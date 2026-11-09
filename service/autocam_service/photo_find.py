"""Offcuts from a phone photo (2026-11-09, docs/offcut-photos.md): the piece's corners, the piece straightened, and
boxes over its holes, as suggestions people fix on the shop page.

Worked out on the team's own photos (2026-11-09):
- Corners: two tries, and the one that fits the length typed in is kept (none fits: people place them).
  "Film": bright, smooth film or its red print. "Background": whatever doesn't look like the photo's own border
  (the floor or table around the piece). Bare aluminum on concrete fits neither: people place those.
- Straightening: the four corners and the piece's real size (the sheet's width, the length taped) give a top-down
  view at PX px per inch, with MARGIN_IN of floor kept round it.
- Holes "by look": the photo teaches itself what floor and piece look like (floor: the margin; piece: the rim just
  inside its edges, which AutoCAM never cuts) and each pixel goes to the nearer, by brightness, colour and grain,
  each scaled by how much it varies. Works for film-covered poly on concrete (film is bright and smooth, concrete
  grainy) and bare aluminum on wood (wood is warm, aluminum grey).
- Holes "by outline": bare aluminum on concrete mirrors the floor (same brightness, colour and grain), so only its cut
  edges tell. Long edge chains (concrete grain makes short ones) bound regions; the region on the rim is the metal,
  and only regions most of whose border is a long edge, and solid, are suggested. Fewer boxes, but sure ones: a
  wrong box only wastes metal, a missed hole is what people must look for.
Needs OpenCV and numpy (pip install -e ".[photos]"); without them nothing is suggested and people place it all.
"""

from typing import List, Optional, Sequence, Tuple

try:                                    # optional: the shop page works without it (no suggestions)
    import cv2
    import numpy as np
except ImportError:                     # pragma: no cover - the laptop has it; this is the fallback
    cv2 = None
    np = None

AVAILABLE = cv2 is not None
PX = 40                    # straightened: px per inch
MARGIN_IN = 1.0            # floor kept round the straightened piece (where "floor" is learnt)
BOX_MARGIN_IN = 0.25       # added round each hole: what a photo is out by
MIN_HOLE_IN = 0.6          # a hole smaller than this square isn't suggested (a drilled hole)
GROW_IN = 2.0              # how far a hole's box may reach past what surely looks like floor (a slot's sunlit hook)
SEPARATE = 1.5             # floor and piece this far apart (brightness, colour or grain, in their own spreads):
                           # holes by look. Film on concrete 2.2-4.4, aluminum on wood 5.4, aluminum on concrete
                           # 0.2-0.4 (the team's photos, 2026-11-09)
Box = Tuple[float, float, float, float]      # across0, along0, across1, along1, inches, along from the front end


def decode(data: bytes, longest: int = 2400):
    """A photo's bytes as an image (None if it isn't one), shrunk so its longest side is at most `longest`."""
    if not AVAILABLE:
        return None
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    s = longest / max(img.shape[:2])
    return cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else img


def spread(img, k: int):
    """Local spread of brightness over k x k: grain (concrete is grainy, film and metal smooth)."""
    L = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)[:, :, 0].astype(np.float32)
    m = cv2.blur(L, (k, k))
    return np.sqrt(np.maximum(cv2.blur(L * L, (k, k)) - m * m, 0))


def order(pts) -> List[List[float]]:
    """Four points as front-left, front-right, back-right, back-left, the front at the bottom of the photo."""
    p = np.array(pts, dtype=np.float32).reshape(4, 2)
    s, d = p.sum(1), np.diff(p, axis=1).ravel()
    tl, br, tr, bl = p[np.argmin(s)], p[np.argmax(s)], p[np.argmin(d)], p[np.argmax(d)]
    return [[float(x), float(y)] for x, y in (bl, br, tr, tl)]


def _quad(blob) -> Optional[list]:
    cnts, _ = cv2.findContours(blob, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cnts:
        return None
    hull = cv2.convexHull(max(cnts, key=cv2.contourArea))
    peri = cv2.arcLength(hull, True)
    for eps in np.linspace(0.005, 0.08, 60):
        a = cv2.approxPolyDP(hull, eps * peri, True)
        if len(a) == 4:
            return a.reshape(4, 2).tolist()
    return None


def _largest(mask):
    n, lab, st, _ = cv2.connectedComponentsWithStats(mask)
    if n < 2:
        return None
    return (lab == 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))).astype(np.uint8)


def red_print(img):
    h, s, v = cv2.split(cv2.cvtColor(img, cv2.COLOR_BGR2HSV))
    return ((h < 12) | (h > 165)) & (s > 70) & (v > 70)


def _corners_film(small) -> Optional[list]:
    """Film: bright and smooth, or its red print."""
    v = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)[:, :, 2]
    film = ((spread(small, 5) < 4.0) & (v > 150)) | red_print(small)
    m = cv2.morphologyEx(film.astype(np.uint8) * 255, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    blob = _largest(m)
    return None if blob is None else _quad(blob)


def _corners_background(small) -> Optional[list]:
    """Whatever doesn't look like the photo's own border (the floor or table round the piece)."""
    lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB).astype(np.float32)
    F = np.dstack([cv2.blur(lab[:, :, i], (7, 7)) for i in range(3)] + [spread(small, 9)])
    H, W = F.shape[:2]
    b = max(4, int(0.04 * min(H, W)))
    border = np.zeros((H, W), bool)
    border[:b] = border[-b:] = True
    border[:, :b] = border[:, -b:] = True
    med = np.median(F[border], axis=0)
    mad = np.median(np.abs(F[border] - med), axis=0) * 1.4826 + 1.5
    piece = (np.sqrt((((F - med) / mad) ** 2).sum(-1)) > 4).astype(np.uint8)
    piece = cv2.morphologyEx(piece, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    piece = cv2.morphologyEx(piece, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    blob = _largest(piece)
    return None if blob is None else _quad(blob)


def _aspect(c) -> float:
    """Long side over short side of a quad (front-left, front-right, back-right, back-left)."""
    p = np.array(c, dtype=np.float32)
    across = (np.linalg.norm(p[1] - p[0]) + np.linalg.norm(p[2] - p[3])) / 2
    along = (np.linalg.norm(p[3] - p[0]) + np.linalg.norm(p[2] - p[1])) / 2
    return float(along / max(across, 1e-6))


def _support(edges, c) -> float:
    """How much of the quad's weakest side runs along an edge in the photo (0-1): a piece's sides are edges all along."""
    worst = 1.0
    for i in range(4):
        p, q = np.array(c[i]), np.array(c[(i + 1) % 4])
        t = np.linspace(0.05, 0.95, 80)[:, None]
        pts = np.round(p + (q - p) * t).astype(int)
        ok = (pts[:, 0] >= 0) & (pts[:, 0] < edges.shape[1]) & (pts[:, 1] >= 0) & (pts[:, 1] < edges.shape[0])
        hit = np.zeros(len(pts), bool)
        hit[ok] = edges[pts[ok, 1], pts[ok, 0]] > 0
        worst = min(worst, float(hit.mean()))
    return worst


def _snap(edges, c, band: float):
    """Each side moved onto the straightest, longest run of edge near it (RANSAC on the edge pixels within `band`
    px), corners where those lines meet. A side with nothing to go on stays."""
    ys, xs = np.nonzero(edges)
    pts = np.stack([xs, ys], 1).astype(np.float32)
    rng = np.random.default_rng(0)
    lines = []
    for i in range(4):
        p, q = np.array(c[i], np.float32), np.array(c[(i + 1) % 4], np.float32)
        d = q - p
        L = float(np.linalg.norm(d))
        u, nrm = d / L, np.array([-d[1], d[0]], np.float32) / L
        rel = pts - p
        along, off = rel @ u, rel @ nrm
        near = pts[(np.abs(off) < band) & (along > -0.05 * L) & (along < 1.05 * L)]
        best, best_in = None, 0
        if len(near) >= 30:
            for _ in range(150):
                a, b = near[rng.integers(len(near), size=2)]
                e = b - a
                n = float(np.linalg.norm(e))
                if n < 0.2 * L or abs(float(e @ u)) / n < 0.95:          # roughly along this side
                    continue
                nn = np.array([-e[1], e[0]], np.float32) / n
                inl = np.abs((near - a) @ nn) < 2.0
                if inl.sum() > best_in:
                    best, best_in = inl, int(inl.sum())
        if best is not None and best_in >= 0.25 * L:
            vx, vy, x0, y0 = cv2.fitLine(near[best], cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
            lines.append((np.array([x0, y0]), np.array([vx, vy])))
        else:
            lines.append((p, u))
    out = []
    for i in range(4):                       # corner i is where side i-1 meets side i
        (p1, v1), (p2, v2) = lines[i - 1], lines[i]
        A = np.array([[v1[0], -v2[0]], [v1[1], -v2[1]]], np.float64)
        if abs(np.linalg.det(A)) < 1e-6:
            return c
        t = np.linalg.solve(A, p2 - p1)
        out.append((p1 + v1 * t[0]).tolist())
    return out


def suggest_corners(img, width_in: float, length_in: float) -> Optional[List[List[float]]]:
    """The piece's corners in the photo (front-left, front-right, back-right, back-left; the front is the end at the
    bottom of the photo), or None. Each try's outline is snapped onto the photo's straight edges; one is kept only if
    its shape fits the size (within a third: a photo at an angle squashes the far end) and all four sides run along
    edges, and the best supported wins."""
    if img is None or not AVAILABLE:
        return None
    s = 1000 / max(img.shape[:2])
    small = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    edges = cv2.Canny(cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0), 30, 90)
    near_edge = cv2.dilate(edges, np.ones((5, 5), np.uint8))
    want = length_in / width_in
    band = 0.05 * float(np.hypot(*small.shape[:2]))
    best, best_score = None, 0.5
    for find in (_corners_film, _corners_background):
        try:
            q = find(small)
        except cv2.error:
            q = None
        if q is None:
            continue
        for c in (order(q), order(_snap(edges, order(q), band))):
            area = cv2.contourArea(np.array(c, np.float32))
            if area < 0.08 * small.shape[0] * small.shape[1]:      # a scrap of tape isn't the piece
                continue
            if abs(_aspect(c) - want) / want > 0.34:
                continue
            score = _support(near_edge, c)
            if score > best_score:
                best, best_score = c, score
    return None if best is None else [[round(x / s, 1), round(y / s, 1)] for x, y in best]


def straighten(img, corners: Sequence[Sequence[float]], width_in: float, length_in: float):
    """The piece seen from straight above, PX px per inch, the front at the bottom, MARGIN_IN of floor round it."""
    m = MARGIN_IN * PX
    w, h = width_in * PX, length_in * PX
    dst = np.array([[m, m + h], [m + w, m + h], [m + w, m], [m, m]], dtype=np.float32)
    H = cv2.getPerspectiveTransform(np.array(corners, dtype=np.float32), dst)
    return cv2.warpPerspective(img, H, (int(round(w + 2 * m)), int(round(h + 2 * m))), flags=cv2.INTER_AREA,
                               borderMode=cv2.BORDER_REPLICATE)


def piece_jpeg(flat, quality: int = 82) -> bytes:
    """The straightened piece without its margin, as a JPEG (the shop page draws the boxes over it)."""
    m = int(MARGIN_IN * PX)
    ok, buf = cv2.imencode(".jpg", flat[m:-m, m:-m], [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    return buf.tobytes() if ok else b""


def _ink(flat):
    """Print and marker: the film's red print (and its pink edges), green or blue marker. Never a hole."""
    hh, ss, v = cv2.split(cv2.cvtColor(flat, cv2.COLOR_BGR2HSV))
    a = cv2.cvtColor(flat, cv2.COLOR_BGR2LAB)[:, :, 1].astype(np.int16)
    pink = ((hh < 8) | (hh > 162)) & (ss > 25) & (v > 110)       # true reds only: warm concrete leans orange
    marker = ((a < 120) | ((hh > 100) & (hh < 135))) & (ss > 60)
    # wide enough to cover how far the grain measure (17 px across) spreads from a printed line
    return cv2.dilate(pink.astype(np.uint8), np.ones((15, 15), np.uint8)).astype(bool) | \
        cv2.dilate(marker.astype(np.uint8), np.ones((15, 15), np.uint8)).astype(bool)


def _model(F, sel):
    s = F[sel]
    med = np.median(s, axis=0)
    return med, np.median(np.abs(s - med), axis=0) * 1.4826 + 1.0


def _dist(F, mdl):
    med, mad = mdl
    return (((F - med) / mad) ** 2).sum(axis=-1)


def _bands(flat):
    """Masks: inside the piece, the floor margin, the rim just inside the edges, and each pixel's distance in."""
    m = int(MARGIN_IN * PX)
    H, W = flat.shape[:2]
    yy, xx = np.mgrid[0:H, 0:W]
    inside = (xx >= m) & (xx < W - m) & (yy >= m) & (yy < H - m)
    d_edge = np.minimum.reduce([xx - m, W - m - 1 - xx, yy - m, H - m - 1 - yy])
    floor = (xx < m - 6) | (xx >= W - m + 6) | (yy < m - 6) | (yy >= H - m + 6)
    rim = inside & (d_edge > 0.15 * PX) & (d_edge < 0.6 * PX)
    return inside, floor, rim, d_edge


def _grow_and_box(seeds, reach, inside, d_edge) -> List[Tuple[int, int, int, int]]:
    """Grow every hole at once through `reach`, up to 2 in (a pixel joins whichever hole's floor reaches it first, so
    a thin bit like a slot's hook joins its own slot), and box each: px, in the straightened image. Pieces of one hole
    (split by a sun streak) stay two boxes side by side: joining what touches merged neighbouring holes on film,
    whose print lets each grow up to the next (offcut #4)."""
    n, labels, stats, _ = cv2.connectedComponentsWithStats(seeds)
    keep = [i for i in range(1, n) if stats[i][4] >= (MIN_HOLE_IN * PX) ** 2]
    lab = np.zeros(labels.shape, np.int32)
    for j, i in enumerate(keep, start=1):
        lab[labels == i] = j
    k = np.ones((3, 3), np.uint8)
    for _ in range(int(GROW_IN * PX)):
        grown = cv2.dilate(lab.astype(np.float32), k).astype(np.int32)
        new = (lab == 0) & reach & (grown > 0)
        if not new.any():
            break
        lab[new] = grown[new]
    boxes = []
    for j in range(1, len(keep) + 1):
        ys, xs = np.nonzero(lab == j)
        boxes.append((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
    return boxes


def _holes_by_look(flat, F, inside, floor_sel, rim, d_edge, ink, floor_m, mat_m):
    df, dm = _dist(F, floor_m), _dist(F, mat_m)
    floor = inside & (df < dm) & ~ink
    seeds = cv2.morphologyEx(floor.astype(np.uint8) * 255, cv2.MORPH_OPEN,
                             cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21)))   # bridges under 1/2 in break
    band = inside & (d_edge >= 0.3 * PX)                     # the piece's own edge isn't a hole
    seeds[~band] = 0
    # grow only through what looks more like floor than the piece: "anything not clearly the piece" ran along
    # every scratch on bare aluminum (a scratch's grain spreads round it)
    reach = band & (df < dm) & ~ink
    return _grow_and_box(seeds, reach, inside, d_edge)


def _holes_by_outline(flat, inside, floor_sel, d_edge, ink):
    m = int(MARGIN_IN * PX)
    H, W = flat.shape[:2]
    g = cv2.GaussianBlur(cv2.cvtColor(flat, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    e = cv2.Canny(g, 30, 90)
    ne, el, est, _ = cv2.connectedComponentsWithStats(e, connectivity=8)
    longc = np.zeros(ne, bool)
    longc[1:] = np.maximum(est[1:, 2], est[1:, 3]) >= 1.25 * PX       # cut edges are long; concrete grain short
    edges = (longc[el] * 255).astype(np.uint8)
    far = cv2.distanceTransform((edges == 0).astype(np.uint8), cv2.DIST_L2, 3)
    n, markers = cv2.connectedComponents((far > 0.2 * PX).astype(np.uint8), connectivity=4)
    ws = cv2.watershed(flat.copy(), markers.astype(np.int32))
    strong = cv2.dilate(edges, np.ones((3, 3), np.uint8)) > 0
    # merge neighbours whose shared border is mostly not a long edge (metal next to metal)
    by, bx = np.nonzero(ws == -1)
    pairs = {}
    for (dy, dx), (ey, ex) in (((0, -1), (0, 1)), ((-1, 0), (1, 0))):
        y1, x1 = np.clip(by + dy, 0, H - 1), np.clip(bx + dx, 0, W - 1)
        y2, x2 = np.clip(by + ey, 0, H - 1), np.clip(bx + ex, 0, W - 1)
        a, b = ws[y1, x1], ws[y2, x2]
        ok = (a > 0) & (b > 0) & (a != b)
        for p, q, s in zip(np.minimum(a[ok], b[ok]), np.maximum(a[ok], b[ok]), strong[by[ok], bx[ok]]):
            c = pairs.setdefault((int(p), int(q)), [0, 0])
            c[0] += 1
            c[1] += int(s)
    parent = list(range(n + 1))

    def root(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for (a, b), (cnt, s) in pairs.items():
        if s / cnt < 0.5:
            ra, rb = root(a), root(b)
            if ra != rb:
                parent[rb] = ra
    roots = np.array([root(i) for i in range(n + 1)])
    lab = np.zeros_like(ws)
    v = ws > 0
    lab[v] = roots[ws[v]]
    rim = np.zeros((H, W), bool)
    rim[m + 10:H - m - 10, m + 6:m + 16] = True
    rim[m + 10:H - m - 10, W - m - 16:W - m - 6] = True
    counts = np.bincount(lab[rim & (lab > 0)].ravel(), minlength=n + 1)
    web = set(np.nonzero(counts > 0.15 * max(counts.sum(), 1))[0].tolist())
    out_counts = np.bincount(lab[floor_sel & (lab > 0)].ravel(), minlength=n + 1)
    outside = set(np.nonzero(out_counts > 0)[0].tolist())
    lab_c = cv2.cvtColor(flat, cv2.COLOR_BGR2LAB).astype(np.float32)
    fl = lab_c[floor_sel]
    L_hi = np.percentile(fl[:, 0], 97)
    b_lo, b_hi = np.percentile(fl[:, 2], [2, 98])
    near_edge = cv2.dilate(edges, np.ones((7, 7), np.uint8)) > 0
    boxes = []
    for r in np.unique(lab):
        if r <= 0 or r in web or r in outside:
            continue
        sel = (lab == r) & inside
        area = int(sel.sum())
        if area < (1.0 * PX) ** 2:
            continue
        ys, xs = np.nonzero(sel)
        x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
        if min(x1 - x0, y1 - y0) < 0.375 * PX or ink[sel].mean() > 0.5:
            continue
        e = 0.3 * PX                       # touching an edge: most likely a sliver between a long scratch and the edge
        if x0 < m + e or x1 > W - m - e or y0 < m + e or y1 > H - m - e:     # (sure ones only: people add the rest)
            continue
        med = np.median(lab_c[sel], axis=0)
        if med[0] > L_hi or not b_lo <= med[2] <= b_hi:     # tape, paper: brighter or another colour than any floor
            continue
        cs, _ = cv2.findContours(sel.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        c = max(cs, key=cv2.contourArea)
        pts = c.reshape(-1, 2)
        enclosed = near_edge[pts[:, 1], pts[:, 0]].mean()
        solidity = cv2.contourArea(c) / max(cv2.contourArea(cv2.convexHull(c)), 1)
        if enclosed >= 0.7 and solidity >= 0.7:              # only sure ones
            boxes.append((x0, y0, x1, y1))
    return boxes


def find_holes(flat, width_in: float, length_in: float) -> Tuple[List[Box], str]:
    """Boxes over the holes of a straightened piece (straighten()), in inches across from the left and along from the
    front end, BOX_MARGIN_IN added round each and kept on the piece; and how they were found: "look", "outline" or
    "" (nothing to go on)."""
    if flat is None or not AVAILABLE:
        return [], ""
    lab = cv2.cvtColor(flat, cv2.COLOR_BGR2LAB).astype(np.float32)
    F = np.dstack([cv2.blur(lab[:, :, i], (5, 5)) for i in range(3)] + [spread(flat, 9), spread(flat, 17)])
    inside, floor_sel, rim, d_edge = _bands(flat)
    ink = _ink(flat)
    floor_m, mat_m = _model(F, floor_sel & ~ink), _model(F, rim & ~ink)
    # how different the floor and the piece look: brightness, colour (b*), grain
    sep = max(abs(floor_m[0][i] - mat_m[0][i]) / max(floor_m[1][i], mat_m[1][i]) for i in (0, 2, 3))
    if sep >= SEPARATE:
        px_boxes, how = _holes_by_look(flat, F, inside, floor_sel, rim, d_edge, ink, floor_m, mat_m), "look"
    else:
        px_boxes, how = _holes_by_outline(flat, inside, floor_sel, d_edge, ink), "outline"
    m = MARGIN_IN * PX
    H = flat.shape[0] - 2 * m
    out: List[Box] = []
    for x0, y0, x1, y1 in px_boxes:
        b = ((x0 - m) / PX - BOX_MARGIN_IN, (H - (y1 - m)) / PX - BOX_MARGIN_IN,
             (x1 - m) / PX + BOX_MARGIN_IN, (H - (y0 - m)) / PX + BOX_MARGIN_IN)
        b = (max(0.0, b[0]), max(0.0, b[1]), min(width_in, b[2]), min(length_in, b[3]))
        out.append(tuple(round(v, 2) for v in b))
    # a box inside another adds nothing
    out = [b for b in out if not any(o != b and o[0] <= b[0] + 0.05 and o[1] <= b[1] + 0.05 and b[2] <= o[2] + 0.05
                                     and b[3] <= o[3] + 0.05 for o in out)]
    return sorted(out, key=lambda b: (b[1], b[0])), how
