"""INVENTO AI shelf scanner (light on CPU).

Stage 1  find objects on the shelf        -> YOLOv8 (loaded only when you scan, freed after 5 idle minutes)
Stage 2  decide which product each one is, in this order of trust:
           a) BARCODE   - a readable EAN-13/UPC-A on the pack is matched to the product's barcode (exact, never wrong)
           b) best.pt   - your own trained classes (exact product names)
           c) COLOUR    - only a very strong, unambiguous match against the product image
Anything else is shown as "unidentified" - the app never puts a guessed product name on it.
"""
import os

# Keep the math libraries from grabbing every CPU core (must be set before torch/numpy load).
for _k in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_k, "2")
os.environ.setdefault("YOLO_AUTOINSTALL", "False")
os.environ.setdefault("YOLO_VERBOSE", "False")

import gc
import difflib
import threading
from datetime import datetime

import cv2
import numpy as np

from ai.barcode import decode_barcodes, normalize as ean_normalize

cv2.setNumThreads(1)

MAX_SIDE = 960          # photos are shrunk to this before analysis (phone photos are 4000px!)
YOLO_SIZE = 640
CONF = 0.20
MIN_BOX = 24            # ignore boxes smaller than this (pixels)
MATCH_MIN = 0.80        # colour-match score needed to call something a known product (strict: wrong names are worse than "unknown")
MATCH_MARGIN = 0.10     # best match must beat the runner-up by this much
SUGGEST_MIN = 0.70      # below this we do not even mention a look-alike
UNLOAD_AFTER = 300      # seconds of idle time before the model is freed from memory

# What a generic YOLO label usually means for each store category (soft hint only).
LABEL_HINTS = {
    "beverages": {"bottle", "cup", "wine glass"}, "dairy": {"bottle", "cup", "bowl"},
    "personal care": {"bottle", "toothbrush", "hair drier"}, "produce": {"apple", "orange", "banana", "broccoli", "carrot"},
    "snacks": {"donut", "cake", "sandwich", "pizza", "hot dog", "bowl"},
}


FOOD_LABELS = set().union(*LABEL_HINTS.values())


def descriptor(bgr):
    """Colour fingerprint of a product: which saturated colours it has + how dark/white it is."""
    h, w = bgr.shape[:2]
    if h < 4 or w < 4:
        return None
    c = bgr[int(h * .1):int(h * .9) or h, int(w * .1):int(w * .9) or w]
    c = cv2.resize(c, (48, 48), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(c, cv2.COLOR_BGR2HSV)
    sat, val = hsv[..., 1], hsv[..., 2]
    mask = ((sat > 70) & (val > 50)).astype("uint8") * 255
    hist = cv2.calcHist([hsv], [0, 1], mask, [18, 4], [0, 180, 70, 256]).flatten()
    hist = hist / max(hist.sum(), 1.0)
    colour_share = float((mask > 0).mean())
    return {"hist": hist, "colour": colour_share, "dark": float((val < 60).mean()),
            "light": float(((sat < 40) & (val > 190)).mean())}


def similarity(a, b):
    tone = 1 - (2 * abs(a["dark"] - b["dark"]) + abs(a["light"] - b["light"])) / 3
    share = 1 - abs(a["colour"] - b["colour"])
    if a["colour"] < .08 or b["colour"] < .08:
        if a["colour"] < .08 and b["colour"] < .08:
            # Both nearly colourless (blank shelf, wall, plain white/black packs): tone alone is too weak to even suggest
            # a product, so these are always left as "not recognised" for the user to decide.
            return min(SUGGEST_MIN - 0.01, 0.7 * tone + 0.3 * share)
        inter = 0.0                                   # one has colour, the other does not: not the same product
    else:
        inter = float(np.minimum(a["hist"], b["hist"]).sum())
    return 0.5 * inter + 0.35 * tone + 0.15 * share


class ProductDetector:
    def __init__(self, model_path=None):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.weights = model_path or os.path.join(root, "best.pt")
        self.model = None
        self.custom = False
        self.model_label = "AI engine not loaded yet"
        self.load_error = None
        self._lock = threading.RLock()
        self._timer = None
        self._refs = {}

    # ---------- model lifecycle ----------
    def _load(self):
        if self.model is not None:
            return True
        try:
            import torch
            torch.set_num_threads(min(2, os.cpu_count() or 1))
            from ultralytics import YOLO
            try:
                from ultralytics import settings
                settings.update({"sync": False})
            except Exception:
                pass
            if os.path.exists(self.weights):
                self.model, self.custom = YOLO(self.weights), True
                self.model_label = "YOLOv8 custom model (best.pt)"
            else:
                self.model, self.custom = YOLO("yolov8n.pt"), False
                self.model_label = "YOLOv8n + product-image matching"
            self.load_error = None
            return True
        except Exception as e:
            self.load_error = str(e)
            return False

    def _schedule_unload(self):
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(UNLOAD_AFTER, self._unload)
        self._timer.daemon = True
        self._timer.start()

    def _unload(self):
        with self._lock:
            self.model = None
            gc.collect()

    # ---------- helpers ----------
    def _ref_descriptor(self, path):
        try:
            key = (path, os.path.getmtime(path))
        except OSError:
            return None
        if key not in self._refs:
            img = cv2.imread(path)
            self._refs[key] = descriptor(img) if img is not None else None
        return self._refs[key]

    @staticmethod
    def _shrink(img):
        h, w = img.shape[:2]
        scale = MAX_SIDE / max(h, w)
        return cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else img

    def identify(self, crop, label, refs):
        """Return (best_ref, best_score, runner_up_score) for one object."""
        d = descriptor(crop)
        if d is None or not refs:
            return None, 0.0, 0.0
        scored = []
        for r in refs:
            s = similarity(d, r["desc"])
            hints = LABEL_HINTS.get((r["category"] or "").lower())
            if hints is not None and not self.custom and label in FOOD_LABELS and label not in hints:
                s *= 0.80                     # e.g. YOLO says "apple" but this product is a beverage: unlikely
            scored.append((s, r))
        scored.sort(key=lambda x: -x[0])
        return scored[0][1], scored[0][0], (scored[1][0] if len(scored) > 1 else 0.0)

    @staticmethod
    def _label_to_product(label, catalog):
        names = {p["name"].lower(): p for p in catalog}
        hit = names.get(label.lower()) or next((names[m] for m in difflib.get_close_matches(label.lower(), list(names), 1, 0.8)), None)
        return hit

    # ---------- main entry ----------
    def detect(self, input_path, output_dir="static/uploads", catalog=None):
        """catalog: [{id, name, category, reference_path}]. Returns a result dict (never fake data)."""
        catalog = catalog or []
        if not os.path.exists(input_path):
            return {"success": False, "error": "Image file not found."}
        img = cv2.imread(input_path)
        if img is None:
            return {"success": False, "error": "Could not read that image."}
        img = self._shrink(img)
        refs = []
        for p in catalog:
            d = self._ref_descriptor(p["reference_path"]) if p.get("reference_path") else None
            if d is not None:
                refs.append({**p, "desc": d})

        with self._lock:
            if not self._load():
                return {"success": False, "error": "The AI engine could not start (" + (self.load_error or "unknown") +
                        "). Install it with: pip install ultralytics. The first run needs internet once to fetch yolov8n.pt, "
                        "or put your trained best.pt in the project folder."}
            try:
                res = self.model.predict(img, imgsz=YOLO_SIZE, conf=CONF, iou=0.5, max_det=80, device="cpu", verbose=False)[0]
                names = res.names
                found = []
                for b in res.boxes:
                    x1, y1, x2, y2 = [int(v) for v in b.xyxy[0].tolist()]
                    if min(x2 - x1, y2 - y1) < MIN_BOX:
                        continue
                    found.append((x1, y1, x2, y2, names[int(b.cls[0])], float(b.conf[0])))
            except Exception as e:
                return {"success": False, "error": f"Detection failed: {e}"}
            finally:
                self._schedule_unload()

        try:
            barcodes = decode_barcodes(img) if any(p.get("barcode") for p in catalog) else []
        except Exception:
            barcodes = []                      # barcode reading is a bonus; never let it break a scan
        return self._build_result(img, found, refs, catalog, output_dir, barcodes)

    def _build_result(self, img, found, refs, catalog, output_dir, barcodes=()):
        os.makedirs(output_dir, exist_ok=True)
        annotated = img.copy()
        scale = max(img.shape[1] / 900.0, 0.7)
        matched, unmatched, detections = {}, {}, []
        by_code = {ean_normalize(p["barcode"]) or p["barcode"]: p for p in catalog if p.get("barcode")}
        coded = [(by_code[c], box) for c, box in barcodes if c in by_code]
        unknown_codes = sorted({c for c, _ in barcodes if c not in by_code})
        used = set()                                    # barcodes already attached to an object box

        def inside(box, outer):
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]

        objects = [(x1, y1, x2, y2, label, conf, None) for (x1, y1, x2, y2, label, conf) in found]
        for i, (x1, y1, x2, y2, label, conf, _) in enumerate(objects):
            for j, (prod, box) in enumerate(coded):
                if j not in used and inside(box, (x1, y1, x2, y2)):
                    objects[i] = (x1, y1, x2, y2, label, conf, prod)
                    used.add(j)
                    break
        for j, (prod, box) in enumerate(coded):         # barcodes YOLO found no object for: still count the product
            if j not in used:
                pad = 6
                objects.append((max(box[0] - pad, 0), max(box[1] - pad, 0), box[2] + pad, box[3] + pad, "barcode", 1.0, prod))

        for (x1, y1, x2, y2, label, conf, by_barcode) in objects:
            product, score, suggestion, method = None, 0.0, None, "colour"
            if by_barcode is not None:
                product, score, method = by_barcode, 1.0, "barcode"
            elif self.custom:
                product = self._label_to_product(label, catalog) if conf >= 0.45 else None    # weak guesses stay unidentified
                score = conf
                method = "model"
            else:
                best, s1, s2 = self.identify(img[y1:y2, x1:x2], label, refs)
                score = s1
                if best is not None and s1 >= MATCH_MIN and (s1 - s2) >= MATCH_MARGIN:
                    product = best
                elif best is not None and s1 >= SUGGEST_MIN:
                    suggestion = best["name"]
            if product:
                m = matched.setdefault(product["name"], {"id": product["id"], "count": 0, "method": method})
                m["count"] += 1
                if method == "barcode":
                    m["method"] = "barcode"
                text, colour = f"{product['name']} ({'barcode' if method == 'barcode' else str(int(score * 100)) + '%'})", (77, 128, 33)
            else:
                u = unmatched.setdefault(label, {"count": 0, "suggestion": suggestion})
                u["count"] += 1
                u["suggestion"] = u["suggestion"] or suggestion
                text, colour = f"{label} (unidentified)", (0, 150, 235)     # never print a guessed product name on the photo
            detections.append({"label": product["name"] if product else label, "matched": bool(product), "score": round(score, 2), "method": method})
            cv2.rectangle(annotated, (x1, y1), (x2, y2), colour, max(2, int(2 * scale)))
            (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, 1)
            ty = max(y1, th + 6)
            cv2.rectangle(annotated, (x1, ty - th - 6), (x1 + tw + 6, ty), colour, -1)
            cv2.putText(annotated, text, (x1 + 3, ty - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, (255, 255, 255), 1, cv2.LINE_AA)

        name = f"detected_scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        cv2.imwrite(os.path.join(output_dir, name), annotated, [cv2.IMWRITE_JPEG_QUALITY, 85])
        note = None
        if not self.custom and not coded:
            note = ("No readable barcode was found, so only very clear colour matches are named; everything else is marked unidentified. "
                    "For exact names take a close, sharp photo with the barcodes facing the camera, or train best.pt (see TRAIN_MODEL.md).")
        if unknown_codes:
            note = (note + " " if note else "") + "Barcode(s) not in your catalog: " + ", ".join(unknown_codes) + " (add them under Products)."
        return {"success": True, "annotated_filename": name, "model_used": self.model_label, "detections": detections,
                "matched": matched, "unmatched": unmatched, "total_count": len(objects), "unknown_barcodes": unknown_codes,
                "matched_total": sum(v["count"] for v in matched.values()), "note": note}
