import os
import json
import sqlite3
import base64
import csv
import io
import re
import secrets
from datetime import datetime, date, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_file, Response, get_flashed_messages
from werkzeug.security import generate_password_hash, check_password_hash
from PIL import Image, ImageOps
from ai.detector import ProductDetector
from ai.barcode import normalize as ean_normalize, make_internal_ean13, ean13_svg
from seed_foods import FOODS
import assistant

app = Flask(__name__)
app.secret_key = os.environ.get("INVENTO_SECRET_KEY") or secrets.token_hex(32)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.environ.get("INVENTO_DB") or os.path.join(BASE_DIR, "database.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
PRODUCT_IMG_DIR = os.path.join(UPLOAD_FOLDER, "products")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

# Payment settings (UPI QR is generated for this ID; change it to your real UPI ID)
MERCHANT_UPI_ID = os.environ.get("INVENTO_UPI_ID", "invento.store@upi")
MERCHANT_NAME = os.environ.get("INVENTO_STORE_NAME", "INVENTO AI Store")

# Billing settings
DELIVERY_FEE = 25.0            # charged on home-delivery orders below the free-delivery limit
FREE_DELIVERY_ABOVE = 199.0    # item total (after discount, before GST) for free delivery
GST_DEFAULTS = {"Beverages": 12, "Dairy": 0, "Snacks": 12, "Personal Care": 18, "Produce": 0, "Bakery": 18, "Staples": 5,
                "Instant Food": 12, "Spices & Sauces": 12, "Sweets & Chocolates": 18, "Dry Fruits": 12}
SEED_IMAGES = {"Coca Cola": "coca-cola.svg", "Pepsi": "pepsi.svg", "Milk": "milk.svg", "Biscuits": "biscuits.svg",
               "Soap": "soap.svg", "Shampoo": "shampoo.svg", "Fresh Apple": "apple.svg", "Instant Noodles": "noodles.svg"}

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB max

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(PRODUCT_IMG_DIR, exist_ok=True)

# Expiry calculation helper
def get_expiry_info(expiry_date_str):
    if not expiry_date_str:
        return {"status": "none", "label": "No Date Set", "days": None, "badge": "badge-light", "date": ""}
    try:
        exp_date = datetime.strptime(expiry_date_str, "%Y-%m-%d").date()
        today = date.today()
        diff = (exp_date - today).days
        if diff < 0:
            return {"status": "expired", "label": f"Expired ({abs(diff)}d ago)", "days": diff, "badge": "badge-danger", "date": expiry_date_str}
        elif diff <= 7:
            return {"status": "expiring_soon", "label": f"Expires in {diff}d", "days": diff, "badge": "badge-warning", "date": expiry_date_str}
        else:
            return {"status": "good", "label": f"Fresh ({diff}d left)", "days": diff, "badge": "badge-success", "date": expiry_date_str}
    except Exception:
        return {"status": "none", "label": expiry_date_str, "days": None, "badge": "badge-light", "date": expiry_date_str}

# Register custom Jinja filters
app.jinja_env.filters["from_json"] = lambda s: json.loads(s) if s else {}
app.jinja_env.filters["expiry_info"] = get_expiry_info

def to_int(value, default=0, minimum=0):
    try:
        return max(minimum, int(float(value)))
    except (TypeError, ValueError):
        return default

def to_float(value, default=0.0, minimum=0.0):
    try:
        return max(minimum, float(value))
    except (TypeError, ValueError):
        return default

# Initialize AI detector
detector = ProductDetector()

# ----------------- Database Helpers -----------------
def clean_barcode(raw):
    """Barcode typed/scanned by the user. Valid EAN-13/UPC-A are normalised; other codes are kept as typed (letters/digits/-)."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    ean = ean_normalize(raw)
    if ean:
        return ean
    return raw if re.fullmatch(r"[A-Za-z0-9\-]{4,24}", raw) else None     # None = not acceptable

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT DEFAULT 'admin'
        )
    """)
    
    cursor.execute("PRAGMA table_info(users)")
    user_cols = [c[1] for c in cursor.fetchall()]
    if "email" not in user_cols:
        cursor.execute("ALTER TABLE users ADD COLUMN email TEXT")
    if "name" not in user_cols:
        cursor.execute("ALTER TABLE users ADD COLUMN name TEXT")

    # Products table (includes expiry_date)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0,
            reorder_level INTEGER NOT NULL DEFAULT 5,
            price REAL NOT NULL DEFAULT 0.0,
            description TEXT,
            expiry_date TEXT,
            last_updated TEXT
        )
    """)

    # Schema migration: check if expiry_date column exists
    cursor.execute("PRAGMA table_info(products)")
    columns = [col[1] for col in cursor.fetchall()]
    if "expiry_date" not in columns:
        cursor.execute("ALTER TABLE products ADD COLUMN expiry_date TEXT")
    if "image" not in columns:
        cursor.execute("ALTER TABLE products ADD COLUMN image TEXT")
    if "barcode" not in columns:
        cursor.execute("ALTER TABLE products ADD COLUMN barcode TEXT")
    gst_added = "gst_rate" not in columns
    if gst_added:
        cursor.execute("ALTER TABLE products ADD COLUMN gst_rate REAL NOT NULL DEFAULT 5")

    # Billing tables
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_no TEXT UNIQUE NOT NULL,
            customer_name TEXT NOT NULL,
            customer_phone TEXT,
            payment_method TEXT NOT NULL,
            total REAL NOT NULL,
            cashier TEXT,
            created_at TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bill_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_id INTEGER NOT NULL,
            product_id INTEGER,
            name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            unit_price REAL NOT NULL,
            subtotal REAL NOT NULL,
            FOREIGN KEY (bill_id) REFERENCES bills(id)
        )
    """)

    # Scans history table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            original_image TEXT NOT NULL,
            annotated_image TEXT NOT NULL,
            detected_counts TEXT NOT NULL,
            total_items INTEGER NOT NULL
        )
    """)

    def add_columns(table, cols):
        cursor.execute(f"PRAGMA table_info({table})")
        have = {c[1] for c in cursor.fetchall()}
        for col, ddl in cols.items():
            if col not in have:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    add_columns("bills", {"subtotal": "REAL NOT NULL DEFAULT 0", "discount_type": "TEXT DEFAULT 'percent'",
                          "discount_value": "REAL DEFAULT 0", "discount_amount": "REAL DEFAULT 0", "gst_amount": "REAL DEFAULT 0",
                          "delivery_fee": "REAL DEFAULT 0", "order_type": "TEXT DEFAULT 'store'", "address": "TEXT DEFAULT ''",
                          "payment_ref": "TEXT DEFAULT ''"})
    add_columns("bill_items", {"gst_rate": "REAL DEFAULT 0", "gst_amount": "REAL DEFAULT 0", "discount_amount": "REAL DEFAULT 0"})
    cursor.execute("UPDATE bills SET subtotal = total WHERE subtotal = 0")

    cursor.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")

    # Seed admin user if not exists
    cursor.execute("SELECT id FROM users WHERE username = 'admin'")
    admin_row = cursor.fetchone()
    if not admin_row:
        cursor.execute("INSERT INTO users (username, password, role, email, name) VALUES ('admin', ?, 'admin', 'admin@invento.ai', 'Store Admin')", (generate_password_hash('admin123'),))
    else:
        cursor.execute("UPDATE users SET email = COALESCE(email, 'admin@invento.ai'), name = COALESCE(name, 'Store Admin') WHERE id = ?", (admin_row["id"],))

    # Seed initial products if table is empty
    cursor.execute("SELECT COUNT(*) FROM products")
    if cursor.fetchone()[0] == 0:
        today = date.today()
        sample_products = [
            ("Coca Cola", "Beverages", 18, 10, 40.0, "500ml chilled soft drink bottle", (today + timedelta(days=90)).strftime("%Y-%m-%d")),
            ("Pepsi", "Beverages", 12, 10, 38.0, "500ml can", (today + timedelta(days=120)).strftime("%Y-%m-%d")),
            ("Milk", "Dairy", 3, 5, 30.0, "1L fresh cow milk carton", (today - timedelta(days=2)).strftime("%Y-%m-%d")),
            ("Biscuits", "Snacks", 4, 8, 30.0, "Crispy whole wheat biscuit pack", (today + timedelta(days=45)).strftime("%Y-%m-%d")),
            ("Soap", "Personal Care", 2, 5, 35.0, "Gentle skin cleanser bar", (today + timedelta(days=365)).strftime("%Y-%m-%d")),
            ("Shampoo", "Personal Care", 12, 6, 120.0, "200ml anti-dandruff shampoo", (today + timedelta(days=400)).strftime("%Y-%m-%d")),
            ("Fresh Apple", "Produce", 15, 6, 25.0, "Crisp red apple", (today + timedelta(days=3)).strftime("%Y-%m-%d")),
            ("Instant Noodles", "Snacks", 1, 5, 20.0, "Quick spicy noodle pack", (today + timedelta(days=5)).strftime("%Y-%m-%d"))
        ]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for name, cat, qty, reorder, price, desc, exp in sample_products:
            cursor.execute("""
                INSERT INTO products (name, category, quantity, reorder_level, price, description, expiry_date, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (name, cat, qty, reorder, price, desc, exp, now))
    else:
        # Populate expiry dates for existing products if any are missing
        today = date.today()
        exp_presets = {
            "Milk": (today - timedelta(days=2)).strftime("%Y-%m-%d"),
            "Fresh Apple": (today + timedelta(days=3)).strftime("%Y-%m-%d"),
            "Instant Noodles": (today + timedelta(days=5)).strftime("%Y-%m-%d"),
            "Biscuits": (today + timedelta(days=45)).strftime("%Y-%m-%d"),
            "Coca Cola": (today + timedelta(days=90)).strftime("%Y-%m-%d"),
            "Pepsi": (today + timedelta(days=120)).strftime("%Y-%m-%d"),
            "Soap": (today + timedelta(days=365)).strftime("%Y-%m-%d"),
            "Shampoo": (today + timedelta(days=400)).strftime("%Y-%m-%d")
        }
        for name, exp in exp_presets.items():
            cursor.execute("UPDATE products SET expiry_date = ? WHERE name = ? AND (expiry_date IS NULL OR expiry_date = '')", (exp, name))

    if gst_added:
        for cat, rate in GST_DEFAULTS.items():
            cursor.execute("UPDATE products SET gst_rate = ? WHERE category = ?", (rate, cat))
    for pname, fname in SEED_IMAGES.items():
        cursor.execute("UPDATE products SET image = ? WHERE name = ? AND (image IS NULL OR image = '')", ("img/products/" + fname, pname))

    # One-time: add the full food catalogue (never re-added if you delete items later)
    if not cursor.execute("SELECT 1 FROM meta WHERE key = 'foods_seeded_v1'").fetchone():
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for name, cat, price, gst, shelf_days, desc in FOODS:
            if cursor.execute("SELECT 1 FROM products WHERE LOWER(name) = LOWER(?)", (name,)).fetchone():
                continue
            expiry = (date.today() + timedelta(days=shelf_days)).strftime("%Y-%m-%d")
            cursor.execute("""INSERT INTO products (name, category, quantity, reorder_level, price, description, expiry_date, last_updated, gst_rate)
                              VALUES (?, ?, 10, 5, ?, ?, ?, ?, ?)""", (name, cat, price, desc, expiry, now, gst))
        cursor.execute("INSERT INTO meta (key, value) VALUES ('foods_seeded_v1', '1')")

    # Every product gets a barcode (store-internal EAN-13 unless you set the pack's real one)
    for row in cursor.execute("SELECT id FROM products WHERE barcode IS NULL OR barcode = ''").fetchall():
        cursor.execute("UPDATE products SET barcode = ? WHERE id = ?", (make_internal_ean13(row["id"]), row["id"]))
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_products_barcode ON products(barcode) WHERE barcode IS NOT NULL AND barcode != ''")

    conn.commit()
    conn.close()

# ----------------- Auth Decorator -----------------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user" not in session:
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

# ----------------- Routes -----------------
@app.route("/")
def index():
    if "user" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if "user" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        identifier = (request.form.get("email") or request.form.get("username") or "").strip().lower()
        password = request.form.get("password", "").strip()

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE lower(email) = ? OR lower(username) = ?", (identifier, identifier))
        user = cursor.fetchone()
        if user:
            stored = user["password"]
            if stored.startswith(("scrypt:", "pbkdf2:")):
                valid = check_password_hash(stored, password)
            else:  # legacy plain-text password: verify, then upgrade to a hash
                valid = stored == password
                if valid:
                    cursor.execute("UPDATE users SET password = ? WHERE id = ?", (generate_password_hash(password), user["id"]))
                    conn.commit()
            user = user if valid else None
        conn.close()

        if user:
            session["user"] = user["name"] or user["username"]
            session["email"] = user["email"] or user["username"]
            session["role"] = user["role"]
            flash(f"Welcome back, {session['user']}!", "success")
            return redirect(url_for("dashboard"))
        flash("Invalid email or password. Please try again.", "danger")

    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if "user" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()[:60]
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        error = None
        if not name:
            error = "Enter your full name."
        elif not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            error = "Enter a valid email address."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        elif password != confirm:
            error = "Passwords do not match."

        if not error:
            conn = get_db()
            exists = conn.execute("SELECT 1 FROM users WHERE lower(email) = ? OR lower(username) = ?", (email, email)).fetchone()
            if exists:
                error = "An account with this email already exists. Sign in instead."
            else:
                conn.execute("INSERT INTO users (username, password, role, email, name) VALUES (?, ?, 'staff', ?, ?)",
                             (email, generate_password_hash(password), email, name))
                conn.commit()
            conn.close()

        if error:
            flash(error, "danger")
            return render_template("register.html", form_name=name, form_email=email)

        flash("Account created. Sign in with your email and password.", "success")
        return redirect(url_for("login"))

    return render_template("register.html", form_name="", form_email="")

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out successfully.", "info")
    return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    conn = get_db()
    cursor = conn.cursor()

    # Metrics
    cursor.execute("SELECT COUNT(*), COALESCE(SUM(quantity), 0), COALESCE(SUM(quantity * price), 0) FROM products")
    total_products, total_stock, total_valuation = cursor.fetchone()

    # Low Stock Alerts (quantity <= reorder_level)
    cursor.execute("SELECT COUNT(*) FROM products WHERE quantity <= reorder_level")
    low_stock_count = cursor.fetchone()[0]

    # Expiry Dates Metrics
    today_str = date.today().strftime("%Y-%m-%d")
    in_7_days_str = (date.today() + timedelta(days=7)).strftime("%Y-%m-%d")

    cursor.execute("SELECT COUNT(*) FROM products WHERE expiry_date IS NOT NULL AND expiry_date != '' AND expiry_date < ? AND quantity > 0", (today_str,))
    expired_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM products WHERE expiry_date >= ? AND expiry_date <= ? AND quantity > 0", (today_str, in_7_days_str))
    expiring_soon_count = cursor.fetchone()[0]

    # Low stock items list
    cursor.execute("SELECT * FROM products WHERE quantity <= reorder_level ORDER BY quantity ASC LIMIT 5")
    low_stock_items = cursor.fetchall()

    # Expiring items watchlist (expired or expiring in 7 days with quantity > 0)
    cursor.execute("SELECT * FROM products WHERE expiry_date IS NOT NULL AND expiry_date != '' AND expiry_date <= ? AND quantity > 0 ORDER BY expiry_date ASC LIMIT 5", (in_7_days_str,))
    expiring_items = cursor.fetchall()

    # Category breakdown
    cursor.execute("SELECT category, COUNT(*) as count, SUM(quantity) as stock FROM products GROUP BY category")
    categories_data = cursor.fetchall()

    # Recent scans
    cursor.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 4")
    recent_scans = cursor.fetchall()

    cursor.execute("SELECT * FROM bills ORDER BY id DESC LIMIT 5")
    recent_bills = cursor.fetchall()
    today_row = cursor.execute("SELECT COUNT(*), COALESCE(SUM(total), 0) FROM bills WHERE substr(created_at, 1, 10) = ?",
                               (date.today().strftime("%Y-%m-%d"),)).fetchone()

    conn.close()

    return render_template(
        "dashboard.html",
        recent_bills=recent_bills,
        today_bills=today_row[0],
        today_sales=today_row[1],
        total_products=total_products,
        total_stock=total_stock,
        total_valuation=total_valuation,
        low_stock_count=low_stock_count,
        expired_count=expired_count,
        expiring_soon_count=expiring_soon_count,
        low_stock_items=low_stock_items,
        expiring_items=expiring_items,
        categories_data=categories_data,
        recent_scans=recent_scans
    )

@app.route("/products")
@login_required
def products():
    search_query = request.args.get("q", "").strip()
    category_filter = request.args.get("category", "").strip()
    expiry_filter = request.args.get("expiry_status", "").strip()

    conn = get_db()
    cursor = conn.cursor()

    # Fetch unique categories for filter dropdown
    cursor.execute("SELECT DISTINCT category FROM products ORDER BY category")
    categories = [r["category"] for r in cursor.fetchall()]

    query = "SELECT * FROM products WHERE 1=1"
    params = []

    if search_query:
        query += " AND (name LIKE ? OR category LIKE ? OR description LIKE ? OR barcode LIKE ?)"
        term = f"%{search_query}%"
        params.extend([term, term, term, term])

    if category_filter:
        query += " AND category = ?"
        params.append(category_filter)

    today_str = date.today().strftime("%Y-%m-%d")
    in_7_days_str = (date.today() + timedelta(days=7)).strftime("%Y-%m-%d")

    if expiry_filter == "expired":
        query += " AND expiry_date IS NOT NULL AND expiry_date != '' AND expiry_date < ?"
        params.append(today_str)
    elif expiry_filter == "expiring_soon":
        query += " AND expiry_date >= ? AND expiry_date <= ?"
        params.extend([today_str, in_7_days_str])
    elif expiry_filter == "good":
        query += " AND expiry_date > ?"
        params.append(in_7_days_str)

    query += " ORDER BY name ASC"
    cursor.execute(query, params)
    items = cursor.fetchall()
    conn.close()

    return render_template(
        "products.html",
        products=items,
        categories=categories,
        search_query=search_query,
        selected_category=category_filter,
        selected_expiry=expiry_filter
    )

@app.route("/products/add", methods=["GET", "POST"])
@login_required
def add_product():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category = request.form.get("category", "").strip()
        quantity = to_int(request.form.get("quantity"), 0)
        reorder_level = to_int(request.form.get("reorder_level"), 5)
        price = to_float(request.form.get("price"), 0.0)
        description = request.form.get("description", "").strip()
        expiry_date = request.form.get("expiry_date", "").strip()
        barcode = clean_barcode(request.form.get("barcode"))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if not name or not category:
            flash("Product name and category are required!", "danger")
            return redirect(url_for("add_product"))
        if barcode is None:
            flash("Barcode must be 4-24 letters/digits (an EAN-13 or UPC-A code is best).", "danger")
            return redirect(url_for("add_product"))

        conn = get_db()
        cursor = conn.cursor()
        if barcode and cursor.execute("SELECT 1 FROM products WHERE barcode = ?", (barcode,)).fetchone():
            conn.close()
            flash(f"Barcode {barcode} already belongs to another product.", "danger")
            return redirect(url_for("add_product"))
        cursor.execute("""
            INSERT INTO products (name, category, quantity, reorder_level, price, description, expiry_date, last_updated, barcode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (name, category, quantity, reorder_level, price, description, expiry_date, now, barcode or None))
        new_id = cursor.lastrowid
        if not barcode:
            cursor.execute("UPDATE products SET barcode = ? WHERE id = ?", (make_internal_ean13(new_id), new_id))
        cursor.execute("UPDATE products SET gst_rate = ? WHERE id = ?", (min(to_float(request.form.get("gst_rate"), 5.0), 28.0), new_id))
        new_image = save_product_image(request.files.get("image"))
        if new_image:
            cursor.execute("UPDATE products SET image = ? WHERE id = ?", (new_image, new_id))
        conn.commit()
        conn.close()

        flash(f"Product '{name}' added successfully!", "success")
        return redirect(url_for("products"))

    return render_template("add_product.html")

@app.route("/products/edit/<int:product_id>", methods=["GET", "POST"])
@login_required
def edit_product(product_id):
    conn = get_db()
    cursor = conn.cursor()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category = request.form.get("category", "").strip()
        quantity = to_int(request.form.get("quantity"), 0)
        reorder_level = to_int(request.form.get("reorder_level"), 5)
        price = to_float(request.form.get("price"), 0.0)
        description = request.form.get("description", "").strip()
        expiry_date = request.form.get("expiry_date", "").strip()
        barcode = clean_barcode(request.form.get("barcode"))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if barcode is None:
            conn.close()
            flash("Barcode must be 4-24 letters/digits (an EAN-13 or UPC-A code is best).", "danger")
            return redirect(url_for("edit_product", product_id=product_id))
        if barcode and cursor.execute("SELECT 1 FROM products WHERE barcode = ? AND id != ?", (barcode, product_id)).fetchone():
            conn.close()
            flash(f"Barcode {barcode} already belongs to another product.", "danger")
            return redirect(url_for("edit_product", product_id=product_id))
        if not barcode:
            old_bc = cursor.execute("SELECT barcode FROM products WHERE id = ?", (product_id,)).fetchone()
            barcode = (old_bc["barcode"] if old_bc and old_bc["barcode"] else make_internal_ean13(product_id))

        cursor.execute("""
            UPDATE products
            SET name = ?, category = ?, quantity = ?, reorder_level = ?, price = ?, description = ?, expiry_date = ?, last_updated = ?, barcode = ?
            WHERE id = ?
        """, (name, category, quantity, reorder_level, price, description, expiry_date, now, barcode, product_id))
        cursor.execute("UPDATE products SET gst_rate = ? WHERE id = ?", (min(to_float(request.form.get("gst_rate"), 5.0), 28.0), product_id))
        new_image = save_product_image(request.files.get("image"))
        if new_image:
            old = cursor.execute("SELECT image FROM products WHERE id = ?", (product_id,)).fetchone()
            cursor.execute("UPDATE products SET image = ? WHERE id = ?", (new_image, product_id))
            remove_product_image(old["image"] if old else None)
        conn.commit()
        conn.close()

        flash(f"Product '{name}' updated successfully!", "success")
        return redirect(url_for("products"))

    cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
    product = cursor.fetchone()
    conn.close()

    if not product:
        flash("Product not found!", "danger")
        return redirect(url_for("products"))

    return render_template("edit_product.html", product=product)

@app.route("/products/delete/<int:product_id>", methods=["POST"])
@login_required
def delete_product(product_id):
    conn = get_db()
    cursor = conn.cursor()
    old = cursor.execute("SELECT image FROM products WHERE id = ?", (product_id,)).fetchone()
    cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
    remove_product_image(old["image"] if old else None)
    conn.commit()
    conn.close()
    flash("Product deleted successfully.", "info")
    return redirect(url_for("products"))

def reference_image_path(image):
    """File the scanner compares shelf objects against (a product's image)."""
    if not image:
        return None
    if image.startswith("img/"):
        path = os.path.join(BASE_DIR, "static", image)
        if path.endswith(".svg"):
            path = path[:-4] + ".png"
    else:
        path = os.path.join(PRODUCT_IMG_DIR, os.path.basename(image))
    return path if os.path.exists(path) else None

def product_catalog(conn):
    return [{"id": r["id"], "name": r["name"], "category": r["category"], "barcode": ean_normalize(r["barcode"]) or (r["barcode"] or ""),
             "reference_path": reference_image_path(r["image"])}
            for r in conn.execute("SELECT id, name, category, image, barcode FROM products ORDER BY name")]

def normalize_upload(path):
    """Validate an uploaded photo, fix phone rotation and shrink it (keeps scanning fast). Returns False if not an image."""
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((1280, 1280))
            im.save(path, "JPEG", quality=88)
        return True
    except Exception:
        if os.path.exists(path):
            os.remove(path)
        return False

@app.route("/scan", methods=["GET", "POST"])
@login_required
def scan():
    scan_result = None

    if request.method == "POST":
        file = request.files.get("image")
        webcam_data = request.form.get("webcam_image")
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        input_filename = f"scan_{timestamp}.jpg"
        input_path = os.path.join(app.config["UPLOAD_FOLDER"], input_filename)

        image_ready = False

        if file and allowed_file(file.filename):
            file.save(input_path)
            image_ready = True
        elif webcam_data and webcam_data.startswith("data:image"):
            # Decode base64 image from webcam
            try:
                header, encoded = webcam_data.split(",", 1)
                with open(input_path, "wb") as fh:
                    fh.write(base64.b64decode(encoded))
                image_ready = True
            except Exception:
                image_ready = False

        if image_ready and not normalize_upload(input_path):
            image_ready = False
            flash("That file is not a valid image. Upload a PNG, JPG or WEBP photo.", "danger")

        if image_ready:
            conn = get_db()
            catalog = product_catalog(conn)
            conn.close()

            result = detector.detect(input_path, output_dir=app.config["UPLOAD_FOLDER"], catalog=catalog)

            if result.get("success"):
                matched, unmatched = result["matched"], result["unmatched"]
                # What we store and show on the dashboard: matched products, plus unmatched labels marked as such
                detected_counts = {name: v["count"] for name, v in matched.items()}
                for label, v in unmatched.items():
                    detected_counts[f"{label} (unmatched)"] = v["count"]
                total_count = result["total_count"]

                conn = get_db()
                cursor = conn.cursor()
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cursor.execute("""
                    INSERT INTO scans (timestamp, original_image, annotated_image, detected_counts, total_items)
                    VALUES (?, ?, ?, ?, ?)
                """, (now_str, input_filename, result["annotated_filename"], json.dumps(detected_counts), total_count))
                scan_id = cursor.lastrowid
                conn.commit()
                conn.close()

                scan_result = {
                    "scan_id": scan_id, "original_image": input_filename, "annotated_image": result["annotated_filename"],
                    "matched": matched, "unmatched": unmatched, "total_count": total_count,
                    "matched_total": result["matched_total"], "sync_counts": {n: v["count"] for n, v in matched.items()},
                    "unmatched_counts": {l: v["count"] for l, v in unmatched.items()},
                    "model_used": result.get("model_used"), "note": result.get("note"), "timestamp": now_str,
                    "products": [{"id": p["id"], "name": p["name"]} for p in catalog],
                }
                if total_count == 0:
                    flash("The scan finished but no objects were found. Try a closer, brighter photo of the shelf.", "warning")
                else:
                    flash(f"Scan complete: {result['matched_total']} of {total_count} objects matched to your products.", "success")
            else:
                flash(f"Scan could not run: {result.get('error')}", "danger")
        elif not (file or webcam_data):
            flash("Please upload an image or capture a webcam photo to scan.", "warning")

    return render_template("scan.html", scan_result=scan_result)

@app.route("/scan/sync", methods=["POST"])
@login_required
def sync_scan_to_inventory():
    """Apply scan counts to stock. Only known products are changed; nothing is auto-created from a guess."""
    sync_mode = request.form.get("sync_mode", "update")   # 'update' = set stock to the count, 'add' = increase stock
    try:
        matched = json.loads(request.form.get("detected_counts") or "{}")
        unmatched = json.loads(request.form.get("unmatched_counts") or "{}")
    except Exception:
        flash("Invalid detection data format.", "danger")
        return redirect(url_for("scan"))
    if not matched and not unmatched:
        flash("No detection data provided to sync.", "warning")
        return redirect(url_for("scan"))

    conn = get_db()
    cursor = conn.cursor()
    counts, skipped = {}, []          # product id -> count
    for name, count in matched.items():
        row = cursor.execute("SELECT id FROM products WHERE LOWER(name) = LOWER(?)", (name,)).fetchone()
        if row:
            counts[row["id"]] = counts.get(row["id"], 0) + to_int(count)
        else:
            skipped.append(name)
    for key, value in request.form.items():           # unmatched objects the user assigned to a product
        if key.startswith("assign::") and value.isdigit():
            label = key[len("assign::"):]
            if cursor.execute("SELECT 1 FROM products WHERE id = ?", (int(value),)).fetchone():
                counts[int(value)] = counts.get(int(value), 0) + to_int(unmatched.get(label, 0))

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    updated = []
    for pid, count in counts.items():
        row = cursor.execute("SELECT name, quantity FROM products WHERE id = ?", (pid,)).fetchone()
        new_qty = row["quantity"] + count if sync_mode == "add" else count
        cursor.execute("UPDATE products SET quantity = ?, last_updated = ? WHERE id = ?", (new_qty, now_str, pid))
        updated.append(f"{row['name']} ({row['quantity']} → {new_qty})")
    conn.commit()
    conn.close()

    if updated:
        flash(f"Inventory synchronized successfully! Updated {len(updated)} products: {', '.join(updated)}", "success")
    else:
        flash("Nothing was changed. Assign the unmatched objects to products first, or add product images so they can be recognised.", "warning")
    if skipped:
        flash(f"Skipped unknown products (not in your catalog): {', '.join(skipped)}", "warning")
    return redirect(url_for("products"))

@app.route("/alerts")
@login_required
def alerts():
    conn = get_db()
    cursor = conn.cursor()

    # Out of stock
    cursor.execute("SELECT * FROM products WHERE quantity = 0 ORDER BY name ASC")
    out_of_stock = cursor.fetchall()

    # Low stock
    cursor.execute("SELECT * FROM products WHERE quantity > 0 AND quantity <= reorder_level ORDER BY quantity ASC")
    low_stock = cursor.fetchall()

    # Expiry Dates
    today_str = date.today().strftime("%Y-%m-%d")
    in_7_days_str = (date.today() + timedelta(days=7)).strftime("%Y-%m-%d")

    # Expired products (quantity > 0 and expiry_date < today)
    cursor.execute("SELECT * FROM products WHERE expiry_date IS NOT NULL AND expiry_date != '' AND expiry_date < ? AND quantity > 0 ORDER BY expiry_date ASC", (today_str,))
    expired_products = cursor.fetchall()

    # Expiring soon products (quantity > 0 and expiry_date between today and 7 days)
    cursor.execute("SELECT * FROM products WHERE expiry_date >= ? AND expiry_date <= ? AND quantity > 0 ORDER BY expiry_date ASC", (today_str, in_7_days_str))
    expiring_soon_products = cursor.fetchall()

    conn.close()
    return render_template(
        "alerts.html",
        out_of_stock=out_of_stock,
        low_stock=low_stock,
        expired_products=expired_products,
        expiring_soon_products=expiring_soon_products
    )

@app.route("/alerts/restock/<int:product_id>", methods=["POST"])
@login_required
def restock_product(product_id):
    add_qty = to_int(request.form.get("quantity"), 10, minimum=1)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE products SET quantity = quantity + ?, last_updated = ? WHERE id = ?", (add_qty, now_str, product_id))
    conn.commit()
    cursor.execute("SELECT name, quantity FROM products WHERE id = ?", (product_id,))
    p = cursor.fetchone()
    conn.close()

    if not p:
        flash("Product not found.", "danger")
        return redirect(url_for("alerts"))
    flash(f"Restocked '{p['name']}'. New stock quantity: {p['quantity']}.", "success")
    return redirect(url_for("alerts"))

@app.route("/alerts/dispose/<int:product_id>", methods=["POST"])
@login_required
def dispose_product(product_id):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE products SET quantity = 0, last_updated = ? WHERE id = ?", (now_str, product_id))
    conn.commit()
    cursor.execute("SELECT name FROM products WHERE id = ?", (product_id,))
    p = cursor.fetchone()
    conn.close()

    if not p:
        flash("Product not found.", "danger")
        return redirect(url_for("alerts"))
    flash(f"Expired stock for '{p['name']}' has been disposed and cleared from shelf inventory.", "warning")
    return redirect(url_for("alerts"))

@app.route("/reports")
@login_required
def reports():
    conn = get_db()
    cursor = conn.cursor()

    # All products
    cursor.execute("SELECT * FROM products ORDER BY category ASC, name ASC")
    products = cursor.fetchall()

    # Totals
    cursor.execute("SELECT COUNT(*), COALESCE(SUM(quantity), 0), COALESCE(SUM(quantity * price), 0) FROM products")
    total_products, total_stock, total_valuation = cursor.fetchone()

    # Category summaries
    cursor.execute("""
        SELECT category, COUNT(*) as count, SUM(quantity) as stock, SUM(quantity * price) as val
        FROM products
        GROUP BY category
    """)
    category_summary = cursor.fetchall()

    # Restock recommendations
    cursor.execute("""
        SELECT *, (reorder_level * 2 - quantity) as recommended_reorder,
               ((reorder_level * 2 - quantity) * price) as estimated_cost
        FROM products
        WHERE quantity <= reorder_level
    """)
    restock_needs = cursor.fetchall()

    # Expiry Risk Analysis (Expired or expiring within 7 days)
    today_str = date.today().strftime("%Y-%m-%d")
    in_7_days_str = (date.today() + timedelta(days=7)).strftime("%Y-%m-%d")

    cursor.execute("""
        SELECT *, (quantity * price) as risk_val
        FROM products
        WHERE expiry_date IS NOT NULL AND expiry_date != '' AND expiry_date <= ? AND quantity > 0
        ORDER BY expiry_date ASC
    """, (in_7_days_str,))
    expiry_risk_items = cursor.fetchall()

    total_expiry_risk_val = sum(r["risk_val"] for r in expiry_risk_items)

    conn.close()

    return render_template(
        "reports.html",
        products=products,
        total_products=total_products,
        total_stock=total_stock,
        total_valuation=total_valuation,
        category_summary=category_summary,
        restock_needs=restock_needs,
        expiry_risk_items=expiry_risk_items,
        total_expiry_risk_val=total_expiry_risk_val
    )

@app.route("/reports/export-csv")
@login_required
def export_csv():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, category, barcode, quantity, reorder_level, price, (quantity * price) as value, expiry_date, last_updated FROM products")
    rows = cursor.fetchall()
    conn.close()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Product ID", "Product Name", "Category", "Barcode", "Quantity", "Reorder Level", "Price (INR)", "Total Value (INR)", "Expiry Date", "Shelf Status", "Last Updated"])
    for r in rows:
        exp_info = get_expiry_info(r["expiry_date"])
        writer.writerow([r["id"], r["name"], r["category"], f'\t{r["barcode"] or ""}' if r["barcode"] else "", r["quantity"], r["reorder_level"], f'{r["price"]:.2f}', f'{r["value"]:.2f}', r["expiry_date"] or "N/A", exp_info["label"], r["last_updated"]])
    csv_data = buf.getvalue()

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=INVENTO_AI_Inventory_Report.csv"}
    )

# ----------------- Product images -----------------
def save_product_image(file):
    """Validate, resize and store an uploaded product image. Returns the file name or None."""
    if not file or not file.filename:
        return None
    if not allowed_file(file.filename):
        flash("Product image must be a JPG, PNG or WEBP file.", "warning")
        return None
    try:
        img = Image.open(file.stream)
        img.load()
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            background = Image.new("RGB", img.size, "white")
            background.paste(img, mask=img.split()[-1])
            img = background
        else:
            img = img.convert("RGB")
        img.thumbnail((640, 640))
        name = f"product_{secrets.token_hex(8)}.jpg"
        img.save(os.path.join(PRODUCT_IMG_DIR, name), "JPEG", quality=86)
        return name
    except Exception:
        flash("Could not read that image. Try a different file.", "warning")
        return None

def remove_product_image(filename):
    if filename:
        path = os.path.join(PRODUCT_IMG_DIR, os.path.basename(filename))
        if os.path.exists(path):
            os.remove(path)

# ----------------- Template helpers -----------------
@app.context_processor
def inject_globals():
    cart = session.get("cart", {})
    return {"cart_count": sum(cart.values()) if isinstance(cart, dict) else 0}

def _parse_dt(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None

@app.template_filter("fmt_date")
def fmt_date(value):
    d = _parse_dt(value)
    return d.strftime("%d %b %Y") if d else (value or "")

@app.template_filter("fmt_time")
def fmt_time(value):
    d = _parse_dt(value)
    return d.strftime("%I:%M %p") if d else ""

# ----------------- Cart & Billing -----------------
PAYMENT_METHODS = ("Cash", "UPI", "Card")

def luhn_ok(number):
    total, flip = 0, False
    for ch in reversed(number):
        d = int(ch)
        if flip:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
        flip = not flip
    return total % 10 == 0

def card_brand(number):
    if number.startswith("4"):
        return "Visa"
    if re.match(r"^(5[1-5]|222[1-9]|22[3-9]|2[3-6]|27[01]|2720)", number):
        return "Mastercard"
    if number[:2] in ("34", "37"):
        return "Amex"
    if number[:2] in ("60", "65", "81", "82") or number[:3] == "508":
        return "RuPay"
    return "Card"

def validate_payment(method, form, total):
    """Returns (error, reference). Card number and CVV are checked and then discarded: they are never stored or logged.
    This is a DEMO checkout; to take real money connect a payment gateway (e.g. Razorpay) instead."""
    if method == "Cash":
        received = to_float(form.get("cash_received"), 0.0)
        if received and received + 0.001 < total:
            return f"Cash received (₹{received:,.2f}) is less than the total (₹{total:,.2f}).", None
        return None, (f"Received ₹{received:,.2f}, change ₹{received - total:,.2f}" if received else "Cash")
    if method == "UPI":
        txn = re.sub(r"\s", "", form.get("upi_txn_id", ""))
        if not re.fullmatch(r"[A-Za-z0-9]{6,30}", txn):
            return "Enter the UPI transaction ID (UTR) shown after the customer pays (6-30 letters or digits).", None
        vpa = form.get("customer_upi", "").strip()
        return None, f"UPI Txn {txn.upper()}" + (f" from {vpa}" if re.fullmatch(r"[\w.\-]{2,}@[A-Za-z]{2,}", vpa) else "")
    number = re.sub(r"[\s-]", "", form.get("card_number", ""))
    if not (number.isdigit() and 13 <= len(number) <= 19 and luhn_ok(number)):
        return "That card number is not valid. Check the digits and try again.", None
    if len(form.get("card_name", "").strip()) < 2:
        return "Enter the name on the card.", None
    em = re.fullmatch(r"(\d{1,2})\s*/\s*(\d{2})", form.get("card_expiry", "").strip())
    if not em or not 1 <= int(em.group(1)) <= 12:
        return "Enter the card expiry as MM/YY.", None
    today = date.today()
    if (2000 + int(em.group(2)), int(em.group(1))) < (today.year, today.month):
        return "That card has expired.", None
    cvv = form.get("card_cvv", "").strip()
    if not (cvv.isdigit() and len(cvv) == (4 if number[:2] in ("34", "37") else 3)):
        return "Enter the card security code (CVV).", None
    return None, f"{card_brand(number)} •••• {number[-4:]} · Auth {secrets.token_hex(3).upper()}"

def is_expired(expiry_date):
    return bool(expiry_date) and expiry_date < date.today().strftime("%Y-%m-%d")

def build_cart(conn):
    items = []
    for pid, qty in session.get("cart", {}).items():
        p = conn.execute("SELECT * FROM products WHERE id = ?", (int(pid),)).fetchone()
        if p:
            items.append({"product": p, "qty": qty, "price": p["price"], "gst_rate": p["gst_rate"] or 0,
                          "subtotal": round(qty * p["price"], 2)})
    return items

def compute_totals(items, dtype, dvalue, order_type):
    """Discount is applied to the item total first, then GST is charged on the discounted amount."""
    subtotal = round(sum(i["subtotal"] for i in items), 2)
    discount = subtotal * min(dvalue, 100) / 100 if dtype == "percent" else min(dvalue, subtotal)
    discount = round(max(discount, 0), 2)
    gst = 0.0
    for i in items:
        share = round(discount * i["subtotal"] / subtotal, 2) if subtotal else 0.0
        i["disc"] = share
        i["gst"] = round((i["subtotal"] - share) * i["gst_rate"] / 100, 2)
        gst += i["gst"]
    gst = round(gst, 2)
    taxable = round(subtotal - discount, 2)
    fee = DELIVERY_FEE if (order_type == "delivery" and items and taxable < FREE_DELIVERY_ABOVE) else 0.0
    cgst = round(gst / 2, 2)
    return {"subtotal": subtotal, "discount": discount, "gst": gst, "cgst": cgst, "sgst": round(gst - cgst, 2),
            "delivery_fee": fee, "total": round(taxable + gst + fee, 2)}

def cart_context(conn):
    items = build_cart(conn)
    discount = session.get("discount", {"type": "percent", "value": 0})
    order_type = session.get("order_type", "store")
    totals = compute_totals(items, discount["type"], discount["value"], order_type)
    return {"cart_items": items, "totals": totals, "discount": discount, "order_type": order_type,
            "free_delivery_above": FREE_DELIVERY_ABOVE, "delivery_fee_amount": DELIVERY_FEE}

def add_to_cart(conn, product_id, qty=1):
    p = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not p:
        return False, "Product not found."
    if is_expired(p["expiry_date"]):
        return False, f"{p['name']} has expired and cannot be sold."
    if p["quantity"] <= 0:
        return False, f"{p['name']} is out of stock."
    cart = dict(session.get("cart", {}))
    current = cart.get(str(product_id), 0)
    new = min(current + qty, p["quantity"])
    if new == current:
        return False, f"Only {p['quantity']} units of {p['name']} are in stock."
    cart[str(product_id)] = new
    session["cart"] = cart
    note = "" if new == current + qty else f" (only {p['quantity']} in stock)"
    return True, f"Added {new - current} x {p['name']} to your cart{note}."

def cart_response():
    """AJAX requests get the refreshed cart as JSON; normal form posts redirect back to billing."""
    if request.headers.get("X-Requested-With") != "fetch":
        return redirect(url_for("billing", q=request.form.get("q", ""), category=request.form.get("category", "")))
    conn = get_db()
    ctx = cart_context(conn)
    conn.close()
    return jsonify(cart=session.get("cart", {}), count=sum(session.get("cart", {}).values()), total=ctx["totals"]["total"],
                   empty=not ctx["cart_items"], order_type=ctx["order_type"], html=render_template("_cart_dynamic.html", **ctx),
                   messages=[{"category": c, "message": m} for c, m in get_flashed_messages(with_categories=True)])

@app.route("/barcode/<code>.svg")
@login_required
def barcode_image(code):
    svg = ean13_svg(code)
    if not svg:
        return Response("Only valid EAN-13 / UPC-A codes can be drawn.", status=404)
    return Response(svg, mimetype="image/svg+xml")

@app.route("/api/barcode/<code>")
@login_required
def api_barcode(code):
    """Look a product up by its barcode (for scanners / other tools)."""
    conn = get_db()
    row = conn.execute("SELECT id, name, category, price, quantity, expiry_date, barcode FROM products WHERE barcode = ?",
                       (ean_normalize(code) or code,)).fetchone()
    conn.close()
    if not row:
        return jsonify(found=False, barcode=code), 404
    return jsonify(found=True, **dict(row))

@app.route("/billing")
@login_required
def billing():
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    conn = get_db()
    if q:                                      # a barcode scanner "types" the code and presses Enter: add that product
        code = ean_normalize(q) or q
        hit = conn.execute("SELECT id, name FROM products WHERE barcode = ?", (code,)).fetchone() if re.fullmatch(r"[A-Za-z0-9\-]{4,24}", code) else None
        if hit:
            ok, msg = add_to_cart(conn, hit["id"], 1)
            conn.close()
            flash(msg, "success" if ok else "warning")
            return redirect(url_for("billing", category=category))
    categories = [r["category"] for r in conn.execute("SELECT DISTINCT category FROM products ORDER BY category")]
    sql = "SELECT * FROM products WHERE quantity > 0 AND (expiry_date IS NULL OR expiry_date = '' OR expiry_date >= ?)"
    params = [date.today().strftime("%Y-%m-%d")]
    if q:
        sql += " AND (name LIKE ? OR category LIKE ? OR description LIKE ? OR barcode LIKE ?)"
        params += [f"%{q}%"] * 4
    if category:
        sql += " AND category = ?"
        params.append(category)
    items = conn.execute(sql + " ORDER BY name", params).fetchall()
    ctx = cart_context(conn)
    conn.close()
    return render_template("billing.html", products=items, categories=categories, q=q, selected_category=category,
                           in_cart=session.get("cart", {}), merchant_upi=MERCHANT_UPI_ID, merchant_name=MERCHANT_NAME, **ctx)

@app.route("/cart/add/<int:product_id>", methods=["POST"])
@login_required
def cart_add(product_id):
    conn = get_db()
    ok, msg = add_to_cart(conn, product_id, 1)
    conn.close()
    if not ok:
        flash(msg, "warning")
    return cart_response()

@app.route("/cart/update/<int:product_id>", methods=["POST"])
@login_required
def cart_update(product_id):
    qty = to_int(request.form.get("qty"), 1)
    cart = dict(session.get("cart", {}))
    conn = get_db()
    p = conn.execute("SELECT name, quantity FROM products WHERE id = ?", (product_id,)).fetchone()
    conn.close()
    if qty == 0 or not p:
        cart.pop(str(product_id), None)
    else:
        if qty > p["quantity"]:
            flash(f"Only {p['quantity']} units of {p['name']} are in stock.", "warning")
            qty = p["quantity"]
        cart[str(product_id)] = qty
    session["cart"] = cart
    return cart_response()

@app.route("/cart/remove/<int:product_id>", methods=["POST"])
@login_required
def cart_remove(product_id):
    cart = dict(session.get("cart", {}))
    cart.pop(str(product_id), None)
    session["cart"] = cart
    return cart_response()

@app.route("/cart/clear", methods=["POST"])
@login_required
def cart_clear():
    for key in ("cart", "discount", "order_type"):
        session.pop(key, None)
    flash("Cart cleared.", "info")
    return cart_response()

@app.route("/cart/options", methods=["POST"])
@login_required
def cart_options():
    if request.form.get("order_type") in ("store", "delivery"):
        session["order_type"] = request.form["order_type"]
    if "discount_type" in request.form:
        dtype = request.form.get("discount_type")
        dtype = dtype if dtype in ("percent", "flat") else "percent"
        value = to_float(request.form.get("discount_value"), 0.0)
        if dtype == "percent" and value > 100:
            value = 100.0
            flash("A discount cannot be more than 100%.", "warning")
        session["discount"] = {"type": dtype, "value": value}
    return cart_response()

@app.route("/checkout", methods=["POST"])
@login_required
def checkout():
    if not session.get("cart"):
        flash("The cart is empty. Add products before generating a bill.", "warning")
        return redirect(url_for("billing"))

    customer = request.form.get("customer_name", "").strip()[:80] or "Walk-in Customer"
    phone = request.form.get("customer_phone", "").strip()[:20]
    payment = request.form.get("payment_method", "Cash")
    payment = payment if payment in PAYMENT_METHODS else "Cash"
    order_type = session.get("order_type", "store")
    discount = session.get("discount", {"type": "percent", "value": 0})
    address = request.form.get("address", "").strip()[:200]
    if order_type == "delivery" and not address:
        flash("Enter a delivery address for home-delivery orders.", "danger")
        return redirect(url_for("billing"))

    conn = get_db()
    try:
        items = build_cart(conn)
        today = date.today().strftime("%Y-%m-%d")
        if not items:
            flash("The cart is empty. Add products before generating a bill.", "warning")
            return redirect(url_for("billing"))
        for it in items:
            p = it["product"]
            if p["quantity"] < it["qty"] or is_expired(p["expiry_date"]):
                flash(f"{p['name']} is no longer available in the requested quantity. Update the cart and try again.", "danger")
                return redirect(url_for("billing"))
        t = compute_totals(items, discount["type"], discount["value"], order_type)
        pay_error, pay_ref = validate_payment(payment, request.form, t["total"])
        if pay_error:
            flash(pay_error, "danger")
            return redirect(url_for("billing"))

        now = datetime.now()
        stamp = now.strftime("%Y-%m-%d %H:%M:%S")
        count_today = conn.execute("SELECT COUNT(*) FROM bills WHERE substr(created_at, 1, 10) = ?", (now.strftime("%Y-%m-%d"),)).fetchone()[0]
        bill_no = f"INV-{now.strftime('%Y%m%d')}-{count_today + 1:04d}"
        cur = conn.execute(
            """INSERT INTO bills (bill_no, customer_name, customer_phone, payment_method, total, cashier, created_at, subtotal,
                                  discount_type, discount_value, discount_amount, gst_amount, delivery_fee, order_type, address, payment_ref)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (bill_no, customer, phone, payment, t["total"], session.get("user"), stamp, t["subtotal"], discount["type"],
             discount["value"], t["discount"], t["gst"], t["delivery_fee"], order_type, address, pay_ref))
        bill_id = cur.lastrowid
        for it in items:
            p = it["product"]
            conn.execute("""INSERT INTO bill_items (bill_id, product_id, name, quantity, unit_price, subtotal, gst_rate, gst_amount, discount_amount)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                         (bill_id, p["id"], p["name"], it["qty"], it["price"], it["subtotal"], it["gst_rate"], it["gst"], it["disc"]))
            conn.execute("UPDATE products SET quantity = quantity - ?, last_updated = ? WHERE id = ?", (it["qty"], stamp, p["id"]))
        conn.commit()
    except Exception:
        conn.rollback()
        flash("Could not generate the bill. Nothing was charged; please try again.", "danger")
        return redirect(url_for("billing"))
    finally:
        conn.close()

    for key in ("cart", "discount", "order_type"):
        session.pop(key, None)
    flash(f"Bill {bill_no} generated. Stock has been updated.", "success")
    return redirect(url_for("bill_detail", bill_id=bill_id))

@app.route("/bills")
@login_required
def bills():
    q = request.args.get("q", "").strip()
    day = request.args.get("date", "").strip()
    conn = get_db()
    sql = """SELECT b.*,
                    (SELECT group_concat(name || ' x ' || quantity, ', ') FROM bill_items WHERE bill_id = b.id) AS items_text,
                    (SELECT COALESCE(SUM(quantity), 0) FROM bill_items WHERE bill_id = b.id) AS units
             FROM bills b WHERE 1=1"""
    params = []
    if q:
        sql += " AND (b.bill_no LIKE ? OR b.customer_name LIKE ? OR b.customer_phone LIKE ?)"
        params += [f"%{q}%"] * 3
    if day:
        sql += " AND substr(b.created_at, 1, 10) = ?"
        params.append(day)
    rows = conn.execute(sql + " ORDER BY b.id DESC LIMIT 300", params).fetchall()
    today = date.today().strftime("%Y-%m-%d")
    today_row = conn.execute("SELECT COUNT(*), COALESCE(SUM(total), 0) FROM bills WHERE substr(created_at, 1, 10) = ?", (today,)).fetchone()
    all_row = conn.execute("SELECT COUNT(*), COALESCE(SUM(total), 0) FROM bills").fetchone()
    conn.close()
    return render_template("bills.html", bills=rows, q=q, day=day, today_count=today_row[0], today_sales=today_row[1],
                           all_count=all_row[0], all_sales=all_row[1])

@app.route("/bills/<int:bill_id>")
@login_required
def bill_detail(bill_id):
    conn = get_db()
    bill = conn.execute("SELECT * FROM bills WHERE id = ?", (bill_id,)).fetchone()
    if not bill:
        conn.close()
        flash("Bill not found.", "danger")
        return redirect(url_for("bills"))
    items = conn.execute("""SELECT bi.*, p.image, p.category FROM bill_items bi
                            LEFT JOIN products p ON p.id = bi.product_id WHERE bi.bill_id = ?""", (bill_id,)).fetchall()
    conn.close()
    gst = bill["gst_amount"] or 0
    cgst = round(gst / 2, 2)
    return render_template("bill_detail.html", bill=bill, items=items, units=sum(i["quantity"] for i in items),
                           cgst=cgst, sgst=round(gst - cgst, 2))

# ----------------- AI assistant -----------------
@app.route("/api/assistant", methods=["POST"])
@login_required
def api_assistant():
    message = ((request.get_json(silent=True) or {}).get("message") or "").strip()[:300]
    if not message:
        return jsonify(reply="Type a question and I'll look it up.", items=[], links=[], chips=assistant.CHIPS)
    urls = {k: url_for(k) for k in ("billing", "bills", "alerts", "reports", "scan", "dashboard", "products")}
    conn = get_db()
    result = assistant.answer(message, conn, lambda pid, qty: add_to_cart(conn, pid, qty), urls,
                              lambda i: url_for("bill_detail", bill_id=i))
    conn.close()
    result["cart_count"] = sum(session.get("cart", {}).values())
    return jsonify(result)

# Make sure every table exists however the app is started (python app.py, flask run, gunicorn...)
init_db()

if __name__ == "__main__":
    init_db()
    print("\n" + "=" * 60)
    print("  🚀 INVENTO AI - Smart Inventory Management System is running!")
    print("  🌐 Access the web portal at: http://127.0.0.1:5000")
    print("  🔑 Login Credentials: Email: admin@invento.ai | Password: admin123")
    print("=" * 60 + "\n")
    # The auto-reloader re-scans thousands of library files and is a common cause of 80-100% CPU, so it stays off.
    app.run(host="127.0.0.1", port=5000, debug=os.environ.get("FLASK_DEBUG") == "1", use_reloader=False, threaded=True)
