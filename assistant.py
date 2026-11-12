"""Invento Assistant: an offline, rule-based helper that answers questions from the store database.
No API key needed. It understands stock, price, expiry, sales, bills and can add items to the cart."""
import re
import difflib
from datetime import date, timedelta

STOP = {"the", "a", "an", "is", "are", "of", "in", "do", "you", "have", "any", "me", "show", "tell", "what", "whats", "how",
        "much", "many", "to", "for", "and", "please", "price", "cost", "rate", "stock", "available", "availability", "left",
        "cart", "add", "buy", "i", "want", "need", "find", "search", "get", "give", "my", "on", "there", "with", "it",
        "this", "that", "can", "u", "list", "all", "items", "products", "product", "item", "put", "basket", "bill", "about"}

CHIPS = ["Low stock", "Expiring soon", "Today's sales", "Top selling", "Recent bills", "Show snacks"]


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).strip()


def _img(image):
    if not image:
        return ""
    return "/static/" + (image if image.startswith("img/") else "uploads/products/" + image)


def _item(r):
    today = date.today().strftime("%Y-%m-%d")
    exp = r["expiry_date"] or ""
    status = "Expired" if exp and exp < today else ("Out of stock" if r["quantity"] <= 0 else ("Low stock" if r["quantity"] <= r["reorder_level"] else "In stock"))
    return {"id": r["id"], "name": r["name"], "category": r["category"], "price": r["price"], "stock": r["quantity"],
            "status": status, "image": _img(r["image"]), "sellable": r["quantity"] > 0 and not (exp and exp < today)}


def find_products(conn, text, limit=6):
    rows = conn.execute("SELECT * FROM products ORDER BY name").fetchall()
    t = _norm(text)
    tight = t.replace(" ", "")
    hits = [r for r in rows if _norm(r["name"]).replace(" ", "") in tight]
    if hits:
        return hits[:limit]
    words = [w for w in t.split() if w not in STOP and len(w) > 2]
    scored = []
    for r in rows:
        hay = _norm(f"{r['name']} {r['category']} {r['description'] or ''}")
        score = sum(1 for w in words if w in hay or w.rstrip("s") in hay)
        if score:
            scored.append((score, r))
    if scored:
        return [r for _, r in sorted(scored, key=lambda x: -x[0])][:limit]
    names = {_norm(r["name"]): r for r in rows}
    out = []
    for w in words:
        for m in difflib.get_close_matches(w, list(names), n=2, cutoff=0.6):
            if names[m] not in out:
                out.append(names[m])
    return out[:limit]


def _money(v):
    return f"₹{v:,.2f}"


def answer(message, conn, add_to_cart, urls, bill_url):
    m = _norm(message)
    raw = message.lower()
    today = date.today().strftime("%Y-%m-%d")
    soon = (date.today() + timedelta(days=7)).strftime("%Y-%m-%d")
    out = {"reply": "", "items": [], "links": [], "chips": []}

    def has(*keys):
        return any(k in raw for k in keys)

    # Specific bill lookup
    bm = re.search(r"inv-\d{8}-\d{4}", raw)
    if bm:
        b = conn.execute("SELECT * FROM bills WHERE lower(bill_no) = ?", (bm.group(0),)).fetchone()
        if not b:
            out["reply"] = f"I couldn't find a bill with ID **{bm.group(0).upper()}**."
        else:
            items = conn.execute("SELECT name, quantity FROM bill_items WHERE bill_id = ?", (b["id"],)).fetchall()
            out["reply"] = (f"**{b['bill_no']}** for {b['customer_name']} on {b['created_at']}: "
                            + ", ".join(f"{i['name']} x {i['quantity']}" for i in items) + f". Total **{_money(b['total'])}** ({b['payment_method']}).")
            out["links"].append({"label": "Open bill", "url": bill_url(b["id"])})
        return out

    # Add to cart
    if re.search(r"\b(add|buy|put)\b", raw):
        found = find_products(conn, message, 1)
        if found:
            qm = re.search(r"\b(\d{1,3})\b", raw)
            qty = max(1, int(qm.group(1))) if qm else 1
            ok, msg = add_to_cart(found[0]["id"], qty)
            out["reply"] = msg
            out["links"].append({"label": "Go to billing", "url": urls["billing"]})
            return out

    if re.fullmatch(r"(hi|hello|hey|help|hii|namaste)\b.*", m) or has("what can you", "how to use"):
        out["reply"] = ("Hi! I'm the Invento assistant. Ask me things like **price of shampoo**, **do we have milk**, **what is low on stock**, "
                        "**today's sales**, or **add 2 biscuits to cart**.")
        out["chips"] = CHIPS
        return out

    if has("out of stock", "sold out", "finished", "no stock"):
        rows = conn.execute("SELECT * FROM products WHERE quantity <= 0 ORDER BY name").fetchall()
        out["reply"] = f"{len(rows)} product(s) are out of stock." if rows else "Nothing is out of stock right now."
        out["items"] = [_item(r) for r in rows]
        return out

    if has("low stock", "running low", "running out", "restock", "reorder", "low on"):
        rows = conn.execute("SELECT * FROM products WHERE quantity <= reorder_level ORDER BY quantity LIMIT 8").fetchall()
        out["reply"] = f"{len(rows)} product(s) need restocking." if rows else "All products are above their reorder level."
        out["items"] = [_item(r) for r in rows]
        out["links"].append({"label": "Open alerts", "url": urls["alerts"]})
        return out

    if has("expired"):
        rows = conn.execute("SELECT * FROM products WHERE expiry_date != '' AND expiry_date < ? ORDER BY expiry_date", (today,)).fetchall()
        out["reply"] = f"{len(rows)} product(s) have expired. Remove them from the shelf." if rows else "No expired products."
        out["items"] = [_item(r) for r in rows]
        return out

    if has("expir", "shelf life", "going bad"):
        rows = conn.execute("SELECT * FROM products WHERE expiry_date >= ? AND expiry_date <= ? ORDER BY expiry_date", (today, soon)).fetchall()
        out["reply"] = (f"{len(rows)} product(s) expire within 7 days: " + ", ".join(f"{r['name']} ({r['expiry_date']})" for r in rows) + ".") if rows else "Nothing expires in the next 7 days."
        out["items"] = [_item(r) for r in rows]
        return out

    if has("top sell", "best sell", "most sold", "popular", "top product"):
        rows = conn.execute("SELECT name, SUM(quantity) AS qty, SUM(subtotal) AS amt FROM bill_items GROUP BY name ORDER BY qty DESC LIMIT 5").fetchall()
        out["reply"] = ("Top selling products: " + ", ".join(f"**{r['name']}** ({r['qty']} sold)" for r in rows) + ".") if rows else "No sales yet. Generate a bill to see top sellers."
        return out

    if has("sales", "revenue", "earn", "sold today", "collection", "turnover"):
        t = conn.execute("SELECT COUNT(*), COALESCE(SUM(total),0), COALESCE(SUM(gst_amount),0) FROM bills WHERE substr(created_at,1,10) = ?", (today,)).fetchone()
        a = conn.execute("SELECT COUNT(*), COALESCE(SUM(total),0) FROM bills").fetchone()
        out["reply"] = (f"Today: **{_money(t[1])}** from {t[0]} bill(s), including {_money(t[2])} GST. "
                        f"All time: {_money(a[1])} from {a[0]} bill(s).")
        out["links"].append({"label": "View bills", "url": urls["bills"]})
        return out

    if has("recent bill", "last bill", "latest bill", "show bills", "my bills", "bill history"):
        rows = conn.execute("SELECT * FROM bills ORDER BY id DESC LIMIT 5").fetchall()
        out["reply"] = "Latest bills: " + "; ".join(f"{b['bill_no']} ({_money(b['total'])})" for b in rows) + "." if rows else "No bills yet. Open Billing to create the first one."
        out["links"] = [{"label": b["bill_no"], "url": bill_url(b["id"])} for b in rows[:3]] or [{"label": "Start billing", "url": urls["billing"]}]
        return out

    if has("inventory value", "valuation", "stock value", "worth"):
        v = conn.execute("SELECT COALESCE(SUM(quantity*price),0) FROM products").fetchone()[0]
        out["reply"] = f"Total inventory value is **{_money(v)}** (before GST)."
        return out

    if has("how many product", "total product", "how many item"):
        n, units = conn.execute("SELECT COUNT(*), COALESCE(SUM(quantity),0) FROM products").fetchone()
        out["reply"] = f"You have **{n}** products with **{units}** units in stock."
        return out

    if has("gst", "tax"):
        rows = conn.execute("SELECT category, MIN(gst_rate) lo, MAX(gst_rate) hi FROM products GROUP BY category").fetchall()
        out["reply"] = ("GST is added on top of the item price (split into CGST and SGST). Rates by category: "
                        + ", ".join(f"{r['category']} {r['lo']:g}%" + (f"-{r['hi']:g}%" if r['hi'] != r['lo'] else "") for r in rows)
                        + ". You can change a product's rate on its edit page.")
        return out

    if has("discount", "offer", "coupon"):
        out["reply"] = "In the cart, open **Discount**, choose % or ₹, enter the value and press Apply. GST is calculated after the discount."
        out["links"].append({"label": "Open billing", "url": urls["billing"]})
        return out

    pages = {"billing": ("billing", "bill a customer"), "alert": ("alerts", "alerts"), "report": ("reports", "reports"),
             "scan": ("scan", "AI scan"), "dashboard": ("dashboard", "dashboard"), "product": ("products", "products")}
    if has("open", "go to", "take me", "where"):
        for key, (u, label) in pages.items():
            if key in raw:
                out["reply"] = f"Here's the {label} page."
                out["links"].append({"label": f"Open {label}", "url": urls[u]})
                return out

    # Product / category search
    found = find_products(conn, message)
    if found:
        out["items"] = [_item(r) for r in found]
        if has("price", "cost", "how much", "rate"):
            out["reply"] = " ".join(f"**{r['name']}** costs {_money(r['price'])} (+{r['gst_rate']:g}% GST)." for r in found[:3])
        else:
            out["reply"] = f"I found {len(found)} matching product(s)."
        return out

    out["reply"] = "I couldn't find that. Try a product name, a category like **snacks**, or ask about stock, expiry, sales or bills."
    out["chips"] = CHIPS
    return out
