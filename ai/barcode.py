"""Barcode helpers: validate, draw (SVG) and read EAN-13 / UPC-A barcodes. No extra dependencies needed."""
import re

_L = ["0001101", "0011001", "0010011", "0111101", "0100011", "0110001", "0101111", "0111011", "0110111", "0001011"]
_G = ["0100111", "0110011", "0011011", "0100001", "0011101", "0111001", "0000101", "0010001", "0001001", "0010111"]
_R = ["1110010", "1100110", "1101100", "1000010", "1011100", "1001110", "1010000", "1000100", "1001000", "1110100"]
_PARITY = ["LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG", "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL"]


def check_digit(first12):
    s = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(first12))
    return str((10 - s % 10) % 10)


def normalize(code):
    """Digits only; a 12-digit UPC-A becomes EAN-13 (leading 0). Returns '' when it is not a valid EAN/UPC."""
    digits = re.sub(r"\D", "", code or "")
    if len(digits) == 12:
        digits = "0" + digits
    if len(digits) == 13 and digits[-1] == check_digit(digits[:12]):
        return digits
    return ""


def make_internal_ean13(seq):
    """Store-internal EAN-13 (GS1 prefix 200 = 'in-store use', can never clash with a real manufacturer's code)."""
    body = "200" + str(int(seq)).zfill(9)
    return body + check_digit(body)


def ean13_bits(code):
    code = normalize(code)
    if not code:
        return None
    parity = _PARITY[int(code[0])]
    bits = "101"
    for d, p in zip(code[1:7], parity):
        bits += (_L if p == "L" else _G)[int(d)]
    bits += "01010"
    for d in code[7:]:
        bits += _R[int(d)]
    return bits + "101"


def ean13_svg(code, bar_w=2, height=70):
    """Printable barcode image (SVG text) for a valid EAN-13/UPC-A code, or None."""
    bits = ean13_bits(code)
    if not bits:
        return None
    code = normalize(code)
    quiet = 10
    width = (len(bits) + 2 * quiet) * bar_w
    out, x = [], quiet * bar_w
    i = 0
    while i < len(bits):
        if bits[i] == "1":
            j = i
            while j < len(bits) and bits[j] == "1":
                j += 1
            out.append(f'<rect x="{(quiet + i) * bar_w}" y="0" width="{(j - i) * bar_w}" height="{height}"/>')
            i = j
        else:
            i += 1
    total_h = height + 16
    txt = (f'<text x="{quiet * bar_w - 4}" y="{total_h - 2}" font-size="13" text-anchor="end" font-family="monospace">{code[0]}</text>'
           f'<text x="{(quiet + 3) * bar_w}" y="{total_h - 2}" font-size="13" font-family="monospace" textLength="{42 * bar_w}">{code[1:7]}</text>'
           f'<text x="{(quiet + 50) * bar_w}" y="{total_h - 2}" font-size="13" font-family="monospace" textLength="{42 * bar_w}">{code[7:]}</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {total_h}" width="{width}" height="{total_h}">'
            f'<rect width="100%" height="100%" fill="#fff"/><g fill="#000">{"".join(out)}</g>{txt}</svg>')


def _runs(bits):
    """'0001101' -> widths of each run of equal bits, e.g. (3, 2, 1, 1)."""
    out, n = [], 1
    for a, b in zip(bits, bits[1:]):
        if a == b:
            n += 1
        else:
            out.append(n)
            n = 1
    out.append(n)
    return tuple(out)


_PAT = {k: [_runs(t) for t in table] for k, table in (("L", _L), ("G", _G), ("R", _R))}


def _best_digit(seg, kinds):
    """seg = 4 measured run lengths. Returns (digit, kind, error) of the closest EAN pattern."""
    total = float(sum(seg)) or 1.0
    norm = [v * 7.0 / total for v in seg]
    best = (None, None, 1e9)
    for kind in kinds:
        for digit, pat in enumerate(_PAT[kind]):
            err = sum((a - b) ** 2 for a, b in zip(norm, pat))
            if err < best[2]:
                best = (digit, kind, err)
    return best


def _decode_runs(runs):
    """runs[0] must be the first dark bar of a start guard. Returns a validated 13-digit code or None."""
    if len(runs) < 59:
        return None
    g = runs[:3]
    if max(g) > 2.2 * min(g):
        return None
    left, kinds = [], ""
    for k in range(6):
        d, kind, err = _best_digit(runs[3 + 4 * k:7 + 4 * k], "LG")
        if err > 1.6:
            return None
        left.append(d)
        kinds += kind
    try:
        first = _PARITY.index(kinds)
    except ValueError:
        return None
    right = []
    for k in range(6):
        d, _, err = _best_digit(runs[32 + 4 * k:36 + 4 * k], "R")
        if err > 1.6:
            return None
        right.append(d)
    return normalize(str(first) + "".join(map(str, left)) + "".join(map(str, right)))


def _scan_gray(gray, step, axis_swapped, found):
    import numpy as np
    h = gray.shape[0]
    for y in range(0, h, step):
        row = gray[y].astype(np.int16)
        lo, hi = int(row.min()), int(row.max())
        if hi - lo < 60:                      # flat row: no bars here
            continue
        dark = row < (lo + hi) // 2
        edges = np.flatnonzero(np.diff(dark.astype(np.int8))) + 1
        if len(edges) < 59:
            continue
        starts = np.concatenate(([0], edges))
        lengths = np.diff(np.concatenate((starts, [len(dark)])))
        is_dark = dark[starts]
        runs = [int(v) for v in lengths]
        for i in range(0, len(runs) - 58):
            if not is_dark[i]:
                continue
            code = _decode_runs(runs[i:i + 59])
            if code:
                x1, x2 = int(starts[i]), int(starts[i + 58] + runs[i + 58])
                found.setdefault(code, []).append((x1, x2, y))


def decode_barcodes(bgr):
    """Find readable EAN-13 / UPC-A barcodes in an image (upright or turned 90 degrees).
    Returns [(code, (x1, y1, x2, y2)), ...]. A barcode must be sharp enough to read; blurry or tiny ones are skipped."""
    import cv2
    import numpy as np
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr
    h, w = gray.shape[:2]
    results = {}
    for scale in (1.0, 2.0):
        if scale == 2.0 and (results or max(h, w) > 1400):
            break
        g = gray if scale == 1.0 else cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        for swapped in (False, True):
            hits = {}
            _scan_gray(g.T if swapped else g, 2 if scale == 1.0 else 3, swapped, hits)
            for code, pts in hits.items():
                xs, ys = [p[0] for p in pts] + [p[1] for p in pts], [p[2] for p in pts]
                a, b = (min(xs), max(xs)), (min(ys) - 6, max(ys) + 6)
                box = (b[0], a[0], b[1], a[1]) if swapped else (a[0], b[0], a[1], b[1])
                box = tuple(int(v / scale) for v in box)
                prev = results.get(code)
                results[code] = box if prev is None else (min(prev[0], box[0]), min(prev[1], box[1]), max(prev[2], box[2]), max(prev[3], box[3]))
    return [(c, (max(b[0], 0), max(b[1], 0), min(b[2], w), min(b[3], h))) for c, b in results.items()]
