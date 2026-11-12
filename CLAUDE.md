# INVENTO AI — Project Architecture & Claude Instructions

## Overview
INVENTO AI is an AI-powered smart inventory monitoring and management web platform. It pairs Computer Vision (YOLOv8 + OpenCV) for automated shelf detection with a Flask-based management dashboard.

## Key Technical Specifications
- **Framework**: Flask (Python)
- **Database**: SQLite3 (`database.db`)
- **Computer Vision**: Ultralytics YOLOv8, OpenCV (`cv2`), Pillow (`PIL`)
- **Frontend**: Jinja2 HTML templates, Bootstrap 5 styling, FontAwesome 6, Google Fonts
- **Authentication**: Session-based login (default: `admin` / `admin123`)

## Directory Layout
```text
invento_ai/
├── app.py                     # Main Flask web application, route handlers, DB operations
├── requirements.txt           # Python dependencies (Flask, opencv-python, ultralytics, Pillow)
├── README.md                  # Detailed documentation and user manual
├── CLAUDE.md                  # Project context & instructions for Claude
├── test_app.py                # Automated unit and integration tests
├── ai/
│   ├── __init__.py            # Python package indicator
│   ├── barcode.py             # EAN-13/UPC-A read, validate and draw (no extra dependencies)
│   └── detector.py            # YOLOv8 & OpenCV computer vision shelf detection engine
├── templates/                 # Jinja2 HTML templates
│   ├── base.html              # Core navigation layout and footer
│   ├── login.html             # Login screen
│   ├── dashboard.html         # Metrics, recent scans, expiry alerts, valuation
│   ├── products.html          # Product listing with category filters & CRUD
│   ├── add_product.html       # Add product form (with expiry date)
│   ├── edit_product.html      # Edit product details & reorder thresholds
│   ├── scan.html              # AI shelf scanning interface (file upload or webcam)
│   ├── alerts.html            # Out-of-Stock and expiring products center
│   └── reports.html           # Valuation report, restock estimates, CSV export
└── static/
    ├── css/                   # Custom styling
    ├── js/                    # Client scripts (webcam stream capture, drag-and-drop)
    ├── uploads/               # Processed scan uploads & annotated detection outputs
    └── samples/               # Demo shelf images for testing
```

## Database Schema (SQLite)
1. **users**: `id (INTEGER PK)`, `username (TEXT UNIQUE)`, `password (TEXT)`, `role (TEXT)`
2. **products**: `id (INTEGER PK)`, `name (TEXT)`, `category (TEXT)`, `quantity (INTEGER)`, `reorder_level (INTEGER)`, `price (REAL)`, `description (TEXT)`, `expiry_date (TEXT)`, `last_updated (TEXT)`, `barcode (TEXT, unique)`, `image`, `gst_rate`
3. **scans**: `id (INTEGER PK)`, `timestamp (TEXT)`, `original_image (TEXT)`, `annotated_image (TEXT)`, `detected_counts (TEXT JSON)`, `total_items (INTEGER)`

## Core API & Web Routes
- `GET /`, `/dashboard`: Main dashboard with statistics, KPIs, and alert cards
- `GET, POST /login`: Authentication
- `GET /logout`: Terminate session
- `GET /products`: Product list with filters
- `GET, POST /products/add`: Add product with expiry date
- `GET, POST /products/edit/<id>`: Update product
- `POST /products/delete/<id>`: Delete product
- `GET, POST /scan`: Upload or capture shelf photo and run AI detection
- `POST /scan/sync`: Synchronize AI detected counts back into product inventory
- `GET /alerts`: View out-of-stock and expiring items
- `POST /alerts/restock/<id>`: Quick restock item
- `POST /alerts/dispose/<id>`: Dispose of expired goods
- `GET /reports`: View valuation reports and financial summaries
- `GET /reports/export-csv`: Export full product inventory as CSV

## Barcodes & food catalogue
- Every product has a unique `barcode`. Foods (133 items) are seeded once from `seed_foods.py` with store-internal EAN-13 codes (prefix 200).
- Replace a code with the real pack barcode in Products > Edit. Billing search accepts a scanned barcode + Enter.
- `GET /api/barcode/<code>` looks a product up; `GET /barcode/<code>.svg` draws a printable barcode.
- Scanner order of trust: barcode in photo > trained best.pt > very strong colour match > "unidentified".

## Development Commands
- Install dependencies: `pip install -r requirements.txt`
- Run application: `python app.py` (accessible at `http://127.0.0.1:5000`)
- Run tests: `python -m unittest test_app.py`
