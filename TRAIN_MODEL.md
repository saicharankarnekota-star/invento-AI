# Making the AI Shelf Scanner accurate for YOUR products

## Why it was wrong before
The default YOLOv8 model only knows 80 everyday objects ("bottle", "cup", "apple"...). It has never seen
your products, so it cannot tell Coca Cola from Pepsi. No code change can fix that: the model has to be
**trained on photos of your products**. This is a one-time job and **it does not run on your laptop**.

## Newest behaviour: barcode first, no guessed names
If a barcode on a pack is readable in the photo, the product is identified exactly from the barcode. Anything the scanner is not
very sure about is shown as **unidentified** (you choose the product) instead of showing a wrong name.
For the best results photograph close, sharp, with barcodes facing the camera. For brand recognition of every pack without
barcodes, train best.pt below.

## What the scanner does now (no training needed)
1. YOLO finds the objects on the shelf (loaded only while you scan; freed after 5 idle minutes).
2. Each object is compared with the **product images** you uploaded (colour matching).
3. Confident matches are counted. Everything else is listed as **Not recognised** so YOU choose what it is.
   Nothing is guessed into your stock, and no junk products are created.

This helps most for products with distinctive colours. Add a clear front photo for every product
(Products > Edit > Product image). Two products with the same colours (e.g. two red cans) will not be told apart.

## Training your own model (best accuracy), about 1 hour, free
Do this on **Google Colab** (free GPU), not on your laptop.

1. **Take photos**: 50-100 photos per product on real shelves (different angles, lighting, crowded and sparse).
2. **Label** them at https://roboflow.com (free). Draw a box around every product.
   **Class names must be exactly your product names** (for example `Coca Cola`, `Pepsi`, `Milk`).
   Export as **YOLOv8**.
3. Open https://colab.research.google.com, choose *Runtime > Change runtime type > T4 GPU*, then run:
   ```
   !pip install ultralytics
   # upload or download your Roboflow export so that data.yaml is in the current folder
   from ultralytics import YOLO
   model = YOLO("yolov8n.pt")
   model.train(data="data.yaml", epochs=50, imgsz=640, batch=16)
   ```
4. Download `runs/detect/train/weights/best.pt` and put it in the project folder (next to `app.py`).
5. Restart the app. The scan page will say **YOLOv8 custom model (best.pt)** and will use your class names directly.

## Keeping your laptop cool
- Start the app with `python app.py` (the auto-reloader is off; it is a common cause of 80-100% CPU).
- Do not set `FLASK_DEBUG=1` for normal use.
- The AI engine only loads when you press "Run AI Detection", uses at most 2 CPU threads, and shrinks photos first.
