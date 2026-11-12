# INVENTO AI — Smart Inventory Management System Using Computer Vision & AI

> **Department of Computer Science & Engineering**  
> **TKR College of Engineering & Technology**

---

## 📌 Project Overview
**INVENTO AI** is an intelligent, automated inventory management system that integrates **Computer Vision (YOLOv8 + OpenCV)** and a responsive **Flask Web Application** to streamline shelf stock monitoring, automated counting, real-time restock alerts, and business analytics.

---

## 📂 Project Structure

```text
INVENTO_AI/
│
├── app.py                     # Flask application routes, session management, SQLite DB logic
├── requirements.txt           # Python dependencies (Flask, OpenCV, Ultralytics, Pillow)
├── README.md                  # Complete project guide and documentation
├── database.db                # SQLite database (auto-created on startup)
├── best.pt                    # Pre-configured YOLOv8 weights for product detection
├── test_app.py                # Automated unit and integration test suite
│
├── ai/
│   ├── __init__.py            # Package initialization
│   └── detector.py            # AI CV Engine: YOLO detection, bounding boxes, item counting
│
├── templates/
│   ├── base.html              # Layout template with modern top navigation bar
│   ├── login.html             # Secure login screen (admin / admin123)
│   ├── dashboard.html         # Real-time KPIs, alert summaries, recent scans, charts
│   ├── products.html          # Product catalog with search, category filters, and CRUD
│   ├── add_product.html       # Add new product form
│   ├── edit_product.html      # Update existing product details & thresholds
│   ├── scan.html              # AI Shelf Scanner (Upload image, live webcam, sample demo)
│   ├── alerts.html            # Out-of-Stock and Low-Stock alert center with 1-click restock
│   └── reports.html           # Inventory valuation report, restock estimates, CSV export
│
└── static/
    ├── css/
    │   └── style.css          # Clean, modern UI styling (responsive, cards, badges, tables)
    ├── js/
    │   └── script.js          # Interactive scripts, webcam capture, drag-and-drop
    ├── uploads/               # Uploaded shelf images & annotated detection images
    └── samples/               # Ready-to-use sample shelf images for faculty demonstration
```

---

## 🚀 Step-by-Step Execution Guide

### 🟢 STAGE 1 — Verify Python
Open Command Prompt / Terminal and run:
```bash
python --version
```
*Expected: Python 3.10, 3.11, 3.12, or 3.13.*

---

### 🟢 STAGE 2 & 3 — Open Project in VS Code
1. Open **Visual Studio Code**.
2. Go to **File → Open Folder...**
3. Select the `INVENTO_AI` folder.

---

### 🟢 STAGE 4 — Create Virtual Environment
In your VS Code terminal (`Terminal → New Terminal`):
```powershell
python -m venv venv
```
Activate the environment:
- **Windows (Command Prompt / PowerShell):**
  ```powershell
  .\venv\Scripts\activate
  ```
  *(You will see `(venv)` appear in front of your command prompt)*

---

### 🟢 STAGE 5 — Install Required Dependencies
Run:
```powershell
pip install -r requirements.txt
```
This installs:
- `Flask` (Backend web framework)
- `opencv-python` (Image processing and contour fallback detection)
- `ultralytics` (YOLOv8 deep learning model engine)
- `Pillow` (Image utility support)

---

### 🟢 STAGE 6 — Run the Application
Launch the Flask server:
```powershell
python app.py
```
You will see:
```text
============================================================
  🚀 INVENTO AI - Smart Inventory Management System is running!
  🌐 Access the web portal at: http://127.0.0.1:5000
  🔑 Login Credentials: Username: admin | Password: admin123
============================================================
* Running on http://127.0.0.1:5000
```
Open your browser (Chrome/Edge) and visit:
👉 **`http://127.0.0.1:5000`**

Login credentials:
- **Username:** `admin`
- **Password:** `admin123`

---

### 🟢 STAGE 7 — Test the Dashboard
After logging in, explore the **Dashboard**:
- **Total Products:** Total distinct items in the catalog.
- **Total Stock Units:** Sum of all shelf units currently stored.
- **Low Stock Alerts:** Items at or below their reorder threshold.
- **Inventory Valuation:** Total monetary value calculated as $\sum (\text{Quantity} \times \text{Price})$ in ₹.
- **Category Distribution:** Visual progress bars for stock across categories.
- **Recent AI Scans:** Thumbnails of past shelf scans with detected item breakdown.

---

### 🟢 STAGE 8 — Test Product Management (CRUD) & Expiry Dates
1. Navigate to **Products → Add New Product**.
2. Enter product details:
   - **Product Name:** `Milk`
   - **Category:** `Dairy`
   - **Unit Price:** `30`
   - **Initial Quantity:** `20`
   - **Reorder Level:** `5`
   - **Expiry Date:** Select date via calendar picker (e.g. `2026-09-15`)
3. Click **Save Product**.
4. Use the search bar, category dropdown, or **Expiry Status Filter** (`🔴 Expired`, `🟡 Expiring Soon`, `🟢 Fresh / Good`).
5. Click **Edit** to adjust price, stock, or expiry date.
6. Click **Delete** to remove a product with instant confirmation.

---

### 🟢 STAGE 9 — Test Low-Stock & Product Expiry Alerts
1. In `database.db`, default items are seeded with relative dates:
   - **Low Stock:** Soap (Qty: 4, Reorder: 5), Shampoo (Qty: 6, Reorder: 6), Coca Cola (Qty: 8, Reorder: 10).
   - **🔴 Expired:** Milk (Expired 2 days ago) — flagged with a critical red banner.
   - **🟡 Expiring Soon (&le; 7 Days):** Fresh Apple (expires in 3 days), Instant Noodles (expires in 5 days).
2. Go to **Alerts** (`/alerts`):
   - **Expired Products:** Shows items that have passed their shelf life with an immediate **"Dispose Stock"** action button.
   - **Products Expiring Soon:** Shows items approaching expiration with markdown/discount recommendations.
   - **Out of Stock & Low Stock:** Displays replenishment shortfall with **+10 Restock** buttons.

---

### 🟢 STAGE 10, 12 & 13 — AI Shelf Scanner & Automatic Inventory Update
1. Navigate to **AI Scan** (`/scan`).
2. You have three ways to test:
   - **Option A — Upload Image:** Drag and drop any retail shelf photo from your PC.
   - **Option B — Live Webcam:** Click **Start Camera**, point at items, and click **Capture Shelf Photo**.
   - **Option C — Demo Sample:** Click **Demo Sample** and pick `shelf_beverages.jpg` or `shelf_snacks.jpg`.
3. Click **Run AI Detection**.
4. The system executes the computer vision pipeline:
   - Bounding boxes are drawn with class confidence percentages.
   - Output image is displayed side-by-side with detected product counts (e.g. `Coca Cola: 8`, `Soap: 4`, `Shampoo: 6`).
5. Click **Apply to Inventory Database**:
   - Stock quantities in the database are updated immediately.
   - Changes automatically reflect across Products, Dashboard, and Alerts!

---

### 🟢 STAGE 14 — Reports & Analytics
1. Go to **Reports** (`/reports`).
2. Review:
   - **Inventory Valuation Table:** Product-wise quantities, prices, and totals.
   - **Category Valuation Breakdown:** Distribution of capital across product categories.
   - **Restock Recommendations:** Projected replenishment quantities and estimated financial budget.
3. Click **Export to CSV** to download a spreadsheet report (`INVENTO_AI_Inventory_Report.csv`).
4. Click **Print Report** for a clean printer/PDF view.

---

### 🟢 STAGE 15 — System Architecture

```text
                    USER / ADMIN
                         │
                         ▼
                 ┌───────────────┐
                 │   FRONTEND    │
                 │ HTML/CSS/JS   │
                 └───────┬───────┘
                         │
                         ▼
                 ┌───────────────┐
                 │ FLASK BACKEND │
                 └───────┬───────┘
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
     PRODUCT         AI / CV        REPORTS
    MANAGEMENT      DETECTION       ANALYTICS
          │              │
          │              ▼
          │           YOLOv8
          │              │
          └───────┬──────┘
                  ▼
              DATABASE
              (SQLite)
                  │
          ┌───────┴────────┐
          ▼                ▼
       ALERTS          DASHBOARD
```

---

## 🧪 Running Automated Tests
To verify all features programmatically:
```powershell
python test_app.py
```
*Expected: `Ran 6 tests ... OK`*

## What's new in this version
- Redesigned interface (static/css/style.css, static/js/script.js): shelf-label theme, split-screen login, working drag-and-drop and webcam capture.
- Passwords are hashed (old plain-text passwords upgrade on next login).
- Set `INVENTO_SECRET_KEY` for a fixed secret key; set `FLASK_DEBUG=1` only while developing.
- Bad form numbers no longer crash, uploads are validated, CSV export uses the csv module.
- Scan results show which model ran. For real product names, put your trained `best.pt` in the project root.

## Version 3: accounts, product images, cart and billing
- **Sign in with email.** Demo login: `admin@invento.ai` / `admin123`. New staff can create an account at `/register`; their name (not "admin") shows in the top bar and on every bill.
- **Product images.** Upload a JPG/PNG/WEBP when adding or editing a product. Products without an image show a colored letter tile.
- **Billing (`/billing`).** Add products to a cart, change quantities, enter customer name/phone, choose Cash/UPI/Card, then generate the bill. Stock is reduced automatically; expired and out-of-stock items cannot be sold.
- **Bill history (`/bills`).** Every bill has a Bill ID (`INV-YYYYMMDD-0001`), date, time, items bought, units, payment method and total. Open any bill for a printable invoice.

## Version 4: quick billing, GST, discount, images and assistant
- **Quick billing (`/billing`)**: category chips, product cards with images, `ADD` button that turns into a `- 1 +` stepper, live cart without page reloads.
- **GST and discount**: every product has a GST rate (edit it on the product page). Discount (% or Rs) is applied first, then CGST + SGST is added. Bills store subtotal, discount, GST, delivery fee and total.
- **In-store or home delivery**: delivery needs an address; fee is Rs 25 below Rs 199 (change `DELIVERY_FEE` and `FREE_DELIVERY_ABOVE` at the top of `app.py`).
- **Bills (`/bills`)**: Bill ID, date, time, items bought, type, payment, discount, GST and total. Open a bill for a printable GST invoice.
- **Product images**: the 8 sample products ship with illustrations in `static/img/products/`. Upload your own photo on Add/Edit product.
- **Invento Assistant** (round button, bottom right): ask "price of milk", "what is low on stock", "today's sales", "add 2 soap", "show snacks", or a Bill ID. It runs offline from your database (`assistant.py`), no API key needed.
- The database tables are now created on every start, so `flask run` and `python app.py` both work.

## Version 5: lighter on the CPU, payments, 3D look, honest scanner
- **CPU**: the AI engine loads only when you scan, uses max 2 threads, shrinks photos to 960px first, and unloads after 5 idle minutes. The auto-reloader is off. Idle CPU is about 0%.
- **Payments**: Cash (change calculator), UPI (QR code for `INVENTO_UPI_ID` plus transaction ID), Card (Luhn-checked number, expiry, CVV; only brand + last 4 digits are saved). Card checkout is a DEMO; use a gateway such as Razorpay for real money.
- **3D look**: layered shadows, pushable buttons and hover tilt, all pure CSS (no WebGL, no animation loops).
- **AI Scanner**: no more invented counts or junk products. Unknown objects are listed as "Not recognised" for you to assign. See `TRAIN_MODEL.md` to train `best.pt` on Google Colab.
- Set your UPI ID with the environment variable `INVENTO_UPI_ID` (default `invento.store@upi`).
