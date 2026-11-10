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
- Maybes (2026-11-09): what nearly made it (a hole just under the size by look, an outline nearly sure, a patch nearly
  busy enough) comes back separately; the page shows them dotted, and one tap keeps one.
- Tidy (2026-11-09, tuned on the table photo): the finders hand back each hole's pixels, not just a box; two finds
  of one hole (pixels overlapping, or a piece lying along it) are one; a white patch brighter than the floor is a
  label or a sticker, and a small busy bit along the cuts between holes is chips; neither is a hole.
- Stepped boxes: each hole, grown by BOX_MARGIN_IN, is covered by up to MAX_STEPS boxes stepped along it (down the
  piece or across it, whichever hugs it closer), a step only where it frees STEP_SAVE_SQIN or more: the metal beside a
  curved or slanted hole comes back, and the card and the nesting still only know boxes. A hole's boxes are a group.
- A tap on a hole (grow_at): by look where the photo has it, else the hole grown from the tapped spot, less keenly
  each step, kept from just before it floods the metal round it; stepped like the rest.
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
TEXTURE_MIN = 0.12         # a patch this busy (share of it on short edges) and busier than the floor: film and chips
MAYBE_MIN_IN = 0.3         # a maybe at least this square (smaller is a drilled hole or a speck)
MAX_MAYBES = 15
MAX_STEPS = 4              # boxes per hole at most
STEP_SAVE_SQIN = 4.0       # a hole gets another step only where it frees this much (a 2 x 2 in room; a card line each)
STEP_IN = 0.25             # steps start and end on a quarter inch
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


class _Region:
    """A hole found: its box in the straightened image (px) and which pixels in that box are the hole."""
    __slots__ = ("x0", "y0", "x1", "y1", "mask")

    def __init__(self, x0: int, y0: int, mask):
        self.x0, self.y0 = int(x0), int(y0)
        self.y1, self.x1 = self.y0 + mask.shape[0], self.x0 + mask.shape[1]
        self.mask = mask

    @classmethod
    def of(cls, sel) -> "_Region":
        ys, xs = np.nonzero(sel)
        x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
        return cls(x0, y0, sel[y0:y1, x0:x1].copy())

    @property
    def box(self) -> Tuple[int, int, int, int]:
        return (self.x0, self.y0, self.x1, self.y1)

    def union(self, o: "_Region") -> "_Region":
        x0, y0, x1, y1 = min(self.x0, o.x0), min(self.y0, o.y0), max(self.x1, o.x1), max(self.y1, o.y1)
        m = np.zeros((y1 - y0, x1 - x0), bool)
        for r in (self, o):
            m[r.y0 - y0:r.y1 - y0, r.x0 - x0:r.x1 - x0] |= r.mask
        return _Region(x0, y0, m)


def _overlap_share(a, b) -> float:
    """How much of the smaller of two boxes the other covers."""
    w, h = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    return w * h / max(min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])), 1)


def _grow_and_box(seeds, reach, inside, d_edge, maybes: Optional[list] = None) -> List[_Region]:
    """Grow every hole at once through `reach`, up to 2 in (a pixel joins whichever hole's floor reaches it first, so
    a thin bit like a slot's hook joins its own slot), and box each: px, in the straightened image. Pieces of one hole
    (split by a sun streak) stay two boxes side by side: joining what touches merged neighbouring holes on film,
    whose print lets each grow up to the next (offcut #4). Ones under MIN_HOLE_IN square but solid go to `maybes`."""
    n, labels, stats, _ = cv2.connectedComponentsWithStats(seeds)
    small = (MAYBE_MIN_IN * PX) ** 2
    sure = [i for i in range(1, n) if stats[i][4] >= (MIN_HOLE_IN * PX) ** 2]
    weak = [i for i in range(1, n) if maybes is not None and small <= stats[i][4] < (MIN_HOLE_IN * PX) ** 2
            and stats[i][4] >= 0.5 * stats[i][2] * stats[i][3]]
    keep = sure + weak
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
    regions = []
    for j in range(1, len(keep) + 1):
        r = _Region.of(lab == j)
        if j <= len(sure):
            regions.append(r)
        else:
            maybes.append(r.box)
    return regions


def _holes_by_look(flat, F, inside, floor_sel, rim, d_edge, ink, floor_m, mat_m, maybes: Optional[list] = None):
    df, dm = _dist(F, floor_m), _dist(F, mat_m)
    floor = inside & (df < dm) & ~ink
    seeds = cv2.morphologyEx(floor.astype(np.uint8) * 255, cv2.MORPH_OPEN,
                             cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21)))   # bridges under 1/2 in break
    band = inside & (d_edge >= 0.3 * PX)                     # the piece's own edge isn't a hole
    seeds[~band] = 0
    # grow only through what looks more like floor than the piece: "anything not clearly the piece" ran along
    # every scratch on bare aluminum (a scratch's grain spreads round it)
    reach = band & (df < dm) & ~ink
    if maybes is not None:
        # small holes (a bolt hole) are opened away above: a finer look, for maybes only
        fine = cv2.morphologyEx(floor.astype(np.uint8), cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
        fine[~band | (seeds > 0)] = 0
        n, lab, st, _ = cv2.connectedComponentsWithStats(fine)
        for i in range(1, n):
            x, y, w, h, a = (int(v) for v in st[i])
            if (MAYBE_MIN_IN * PX) ** 2 <= a < (MIN_HOLE_IN * PX) ** 2 and a >= 0.5 * w * h and max(w, h) <= 2.5 * min(w, h):
                maybes.append((x, y, x + w, y + h))
    return _grow_and_box(seeds, reach, inside, d_edge, maybes)


def _outline_regions(flat, inside, floor_sel, ink, canny=(30, 90), long_in=1.25):
    """The piece cut into regions by its long edge chains (watershed from what's 0.2 in clear of them, neighbours
    merged where their shared border is mostly not a long edge); which of them is the web (on the rim) and which reach
    the floor round the piece. Returns (labels, web, outside, edges)."""
    m = int(MARGIN_IN * PX)
    H, W = flat.shape[:2]
    g = cv2.GaussianBlur(cv2.cvtColor(flat, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    e = cv2.Canny(g, *canny)
    ne, el, est, _ = cv2.connectedComponentsWithStats(e, connectivity=8)
    longc = np.zeros(ne, bool)
    longc[1:] = np.maximum(est[1:, 2], est[1:, 3]) >= long_in * PX    # cut edges are long; concrete grain short
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
    return lab, web, outside, edges


def _holes_by_outline(flat, inside, floor_sel, d_edge, ink, maybes: Optional[list] = None):
    m = int(MARGIN_IN * PX)
    H, W = flat.shape[:2]
    lab, web, outside, edges = _outline_regions(flat, inside, floor_sel, ink)
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
            boxes.append(_Region.of(sel))
        elif maybes is not None and enclosed >= 0.45 and solidity >= 0.5:
            maybes.append((x0, y0, x1, y1))
    return boxes


def _holes_by_texture(flat, inside, floor_sel, d_edge, ink, bright_max: float = 255.0, keen: float = 1.0, dens=None):
    """Holes still full of crumpled film and chips (2026-11-09, aluminum on the table): they look like the metal in
    colour, but they're busy (creases, chips) where bare metal and wood are smooth. Busy = many short edges in a
    3/8 in patch, clearly more than the floor's own. Strokes (scratches, a hole's own outline, stamped lettering) are
    opened away."""
    dens = _busy(flat) if dens is None else dens
    floor_busy = float(np.percentile(dens[floor_sel], 90))
    # not near the edges (chips and burrs along them; AutoCAM's own parts are never that close) and not paper (a label)
    L = cv2.cvtColor(flat, cv2.COLOR_BGR2LAB)[:, :, 0]
    busy = (dens > keen * max(TEXTURE_MIN, 1.5 * floor_busy)) & inside & (d_edge >= 0.5 * PX) & ~ink & (L < bright_max)
    busy = cv2.morphologyEx(busy.astype(np.uint8), cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
    # one hole's crinkles come in patches: join them (gaps under ~3/8 in; the metal between two holes is wider and smooth)
    busy = cv2.morphologyEx(busy, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
    n, lab, st, _ = cv2.connectedComponentsWithStats(busy)
    out = []
    for i in range(1, n):
        x, y, w, h, a = (int(v) for v in st[i])
        if a < (MIN_HOLE_IN * PX) ** 2 or min(w, h) < 0.375 * PX:
            continue
        out.append(_Region(x, y, lab[y:y + h, x:x + w] == i))
    return out


def _busy(flat):
    """How busy each spot is: the share of a 3/8 in patch on edges."""
    g = cv2.GaussianBlur(cv2.cvtColor(flat, cv2.COLOR_BGR2GRAY), (3, 3), 0)
    return cv2.blur((cv2.Canny(g, 40, 120) > 0).astype(np.float32), (17, 17))


def _covered_px(t, boxes) -> float:
    t = t.box if isinstance(t, _Region) else t
    ta = max((t[2] - t[0]) * (t[3] - t[1]), 1)
    return sum(max(0, min(t[2], b[2]) - max(t[0], b[0])) * max(0, min(t[3], b[3]) - max(t[1], b[1]))
               for b in (x.box if isinstance(x, _Region) else x for x in boxes)) / ta


class _Look:
    """What find_holes and grow_at both work from: the piece's bands, ink, and the floor's and the piece's looks."""

    def __init__(self, flat):
        lab = cv2.cvtColor(flat, cv2.COLOR_BGR2LAB).astype(np.float32)
        self.F = np.dstack([cv2.blur(lab[:, :, i], (5, 5)) for i in range(3)] + [spread(flat, 9), spread(flat, 17)])
        self.inside, self.floor_sel, self.rim, self.d_edge = _bands(flat)
        self.ink = _ink(flat)
        self.floor_m, self.mat_m = _model(self.F, self.floor_sel & ~self.ink), _model(self.F, self.rim & ~self.ink)
        # how different the floor and the piece look: brightness, colour (b*), grain
        self.sep = max(abs(self.floor_m[0][i] - self.mat_m[0][i]) / max(self.floor_m[1][i], self.mat_m[1][i])
                       for i in (0, 2, 3))
        self.paper = max(float(self.floor_m[0][0]), float(self.mat_m[0][0])) + 40   # far brighter than both


def _to_inches(px_boxes, flat, width_in: float, length_in: float) -> List[Box]:
    m = MARGIN_IN * PX
    H = flat.shape[0] - 2 * m
    out: List[Box] = []
    for x0, y0, x1, y1 in px_boxes:
        b = ((x0 - m) / PX - BOX_MARGIN_IN, (H - (y1 - m)) / PX - BOX_MARGIN_IN,
             (x1 - m) / PX + BOX_MARGIN_IN, (H - (y0 - m)) / PX + BOX_MARGIN_IN)
        b = (max(0.0, b[0]), max(0.0, b[1]), min(width_in, b[2]), min(length_in, b[3]))
        out.append(tuple(round(v, 2) for v in b))
    return out


def _drop_inside(boxes: List[Box]) -> List[Box]:
    """A box inside another adds nothing."""
    return [b for b in boxes if not any(o != b and o[0] <= b[0] + 0.05 and o[1] <= b[1] + 0.05 and b[2] <= o[2] + 0.05
                                        and b[3] <= o[3] + 0.05 for o in boxes)]


def _px_to_in(r, flat, width_in: float, length_in: float) -> Box:
    """A box in the straightened image's px as inches on the piece (across, along from the front), kept on it."""
    m = MARGIN_IN * PX
    H = flat.shape[0] - 2 * m
    b = ((r[0] - m) / PX, (H - (r[3] - m)) / PX, (r[2] - m) / PX, (H - (r[1] - m)) / PX)
    return tuple(round(v, 2) for v in (max(0.0, b[0]), max(0.0, b[1]), min(width_in, b[2]), min(length_in, b[3])))


def _labels(regions: List[_Region], flat, floor_sel) -> List[_Region]:
    """Drop labels and stickers (white paper). On a coloured floor (the plywood table: yellow) a patch brighter than
    the floor and of another colour isn't the floor: the table's own light wood is nearly as bright as its label (177
    against 188-200), but yellow where the label is a bluish white, so colour tells. On a colourless floor (concrete) only far brighter than any of it, and smooth like paper:
    sunlit concrete in a hole is colourless and bright too (offcut #4)."""
    lab = cv2.cvtColor(flat, cv2.COLOR_BGR2LAB).astype(np.float32)
    grain = spread(flat, 9)
    fl = lab[floor_sel]
    floor_L, floor_L98 = float(np.median(fl[:, 0])), float(np.percentile(fl[:, 0], 98))
    floor_a, floor_b = float(np.median(fl[:, 1])), float(np.median(fl[:, 2]))
    floor_chroma = float(np.hypot(floor_a - 128, floor_b - 128))
    floor_grain = float(np.percentile(grain[floor_sel], 25))
    out = []
    for r in regions:
        sel = (slice(r.y0, r.y1), slice(r.x0, r.x1))
        L, a, b = (float(v) for v in np.median(lab[sel][r.mask], axis=0))
        if floor_chroma >= 8:                    # brighter than the floor and another colour (the label: bluish white)
            label = L > floor_L + 5 and float(np.hypot(a - floor_a, b - floor_b)) > max(8.0, floor_chroma)
        else:
            label = L > floor_L98 + 25 and float(np.median(grain[sel][r.mask])) < floor_grain
        if not label:
            out.append(r)
    return out


def _scraps(busy: List[_Region], holes: List[_Region], shape) -> List[_Region]:
    """Drop small busy bits (under 1.5 sq in) lying almost all within 1/2 in of holes: chips and creases along a cut,
    between two holes (the table photo), whose holes' boxes cover them anyway."""
    if not holes:
        return busy
    reach = int(0.5 * PX)
    allm = np.zeros(shape[:2], np.uint8)
    for r in holes:
        allm[r.y0:r.y1, r.x0:r.x1] |= r.mask.astype(np.uint8)
    near = cv2.dilate(allm, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * reach + 1, 2 * reach + 1))) > 0
    return [t for t in busy if t.mask.sum() >= 1.5 * PX * PX
            or (near[t.y0:t.y1, t.x0:t.x1] & t.mask).sum() < 0.8 * t.mask.sum()]


def _cover_area(rects, grid: int = 4) -> int:
    """The area (px, to a 4 px grid) that a set of boxes covers, overlaps counted once."""
    if not rects:
        return 0
    x0, y0 = min(r[0] for r in rects), min(r[1] for r in rects)
    x1, y1 = max(r[2] for r in rects), max(r[3] for r in rects)
    m = np.zeros(((y1 - y0) // grid + 2, (x1 - x0) // grid + 2), bool)
    for r in rects:
        m[(r[1] - y0) // grid:(r[3] - y0 + grid - 1) // grid, (r[0] - x0) // grid:(r[2] - x0 + grid - 1) // grid] = True
    return int(m.sum()) * grid * grid


def _same_hole(a: _Region, b: _Region, steps) -> bool:
    """Two finds of one hole: their pixels overlap (found by look and by busyness), or boxing them together costs no
    more metal than boxing them apart, give or take 1 sq in (a piece cut off a hole by a print line or a streak), or
    the smaller lies mostly inside the bigger's boxes (a fragment inside a hole that's already boxed). Not boxes overlapping: a tall L hole's box holds half the sheet's holes (that snowballed into
    one, the table photo); nor lying near: thin neighbours do (#4's slots), and boxing those together covers the metal
    between them."""
    reach = int(2 * BOX_MARGIN_IN * PX)
    if a.x0 > b.x1 + reach or b.x0 > a.x1 + reach or a.y0 > b.y1 + reach or b.y0 > a.y1 + reach:
        return False
    big, small = (a, b) if a.mask.sum() >= b.mask.sum() else (b, a)
    x0, y0, x1, y1 = min(a.x0, b.x0), min(a.y0, b.y0), max(a.x1, b.x1), max(a.y1, b.y1)

    def place(r):
        m = np.zeros((y1 - y0, x1 - x0), bool)
        m[r.y0 - y0:r.y1 - y0, r.x0 - x0:r.x1 - x0] = r.mask
        return m
    ms = place(small)
    n = max(int(ms.sum()), 1)
    if (ms & place(big)).sum() >= 0.2 * n:
        return True
    boxed = np.zeros_like(ms)                    # a fragment inside a hole that's already boxed
    for bx0, by0, bx1, by1 in steps(big):
        boxed[max(0, by0 - y0):max(0, by1 - y0), max(0, bx0 - x0):max(0, bx1 - x0)] = True
    if (ms & boxed).sum() >= 0.7 * n:
        return True
    apart = _cover_area(steps(a) + steps(b))
    # 1 sq in to spare, not a share: a share snowballed (each merge a little bigger, the table photo one hole again)
    return _cover_area(_steps(a.union(b))) <= apart + PX * PX


def _merge(regions: List[_Region]) -> List[_Region]:
    """Finds of one hole made one (_same_hole)."""
    regions = list(regions)
    cache: dict = {}

    def steps(r):
        if id(r) not in cache:
            cache[id(r)] = (r, _steps(r))
        return cache[id(r)][1]
    merged = True
    while merged:
        merged = False
        for i in range(len(regions)):
            for j in range(i + 1, len(regions)):
                if _same_hole(regions[i], regions[j], steps):
                    regions[i] = regions[i].union(regions.pop(j))
                    merged = True
                    break
            if merged:
                break
    return regions


def _steps(r: _Region) -> List[Tuple[int, int, int, int]]:
    """Up to MAX_STEPS boxes (px) covering the hole grown by BOX_MARGIN_IN, stepped down the piece or across it,
    whichever covers less; a step only where it frees STEP_SAVE_SQIN or more."""
    m = int(round(BOX_MARGIN_IN * PX))
    grown = cv2.dilate(np.pad(r.mask, m).astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * m + 1, 2 * m + 1))) > 0
    ox, oy = r.x0 - m, r.y0 - m
    q, penalty = max(1, int(round(STEP_IN * PX))), STEP_SAVE_SQIN * PX * PX
    best = None
    for across in (False, True):                 # False: steps down the piece (rows); True: across it (columns)
        M = grown.T if across else grown
        has = M.any(axis=1)
        lo = np.where(has, np.argmax(M, axis=1), M.shape[1])
        hi = np.where(has, M.shape[1] - np.argmax(M[:, ::-1], axis=1), 0)
        n = (M.shape[0] + q - 1) // q            # cells of a quarter inch
        c_lo = np.array([lo[c * q:(c + 1) * q].min() for c in range(n)])
        c_hi = np.array([hi[c * q:(c + 1) * q].max() for c in range(n)])
        rows = np.array([min((c + 1) * q, M.shape[0]) - c * q for c in range(n)])

        lo_l, hi_l, rows_l = c_lo.tolist(), c_hi.tolist(), rows.tolist()
        INF = float("inf")
        dp = [[INF] * (n + 1) for _ in range(MAX_STEPS + 1)]
        back = [[0] * (n + 1) for _ in range(MAX_STEPS + 1)]
        dp[0][0] = 0
        for b in range(1, n + 1):
            l, h, rs = 10 ** 9, -1, 0            # the step a..b, grown one cell at a time from b down
            for a in range(b - 1, -1, -1):
                l, h, rs = min(l, lo_l[a]), max(h, hi_l[a]), rs + rows_l[a]
                c = 0 if h <= l else (h - l) * rs
                for k in range(1, MAX_STEPS + 1):
                    v = dp[k - 1][a] + c
                    if v < dp[k][b]:
                        dp[k][b], back[k][b] = v, a
        k = min(range(1, MAX_STEPS + 1), key=lambda k: dp[k][n] + penalty * (k - 1))
        total = dp[k][n] + penalty * (k - 1)
        cuts, b = [], n
        for kk in range(k, 0, -1):
            a = back[kk][b]
            cuts.append((a, b))
            b = a
        rects = []
        for a, b in reversed(cuts):
            l, h = int(c_lo[a:b].min()), int(c_hi[a:b].max())
            if h <= l:
                continue
            r0, r1 = a * q, min(b * q, M.shape[0])
            rects.append((r0, l, r1, h))
        if best is None or total < best[0]:
            best = (total, across, rects)
    _, across, rects = best
    out = []
    for r0, l, r1, h in rects:                   # back to the image: rows are y unless the steps went across
        x0, y0, x1, y1 = (r0, l, r1, h) if across else (l, r0, h, r1)
        out.append((ox + x0, oy + y0, ox + x1, oy + y1))
    return out


def find_holes(flat, width_in: float, length_in: float) -> Tuple[List[Box], str]:
    """Boxes over the holes of a straightened piece (straighten()), in inches across from the left and along from the
    front end, BOX_MARGIN_IN added round each and kept on the piece (a hole may get a few stepped boxes); and how they
    were found: "look", "outline" or "" (nothing to go on)."""
    found = find(flat, width_in, length_in)
    return found["boxes"], found["how"]


def find_holes_and_maybes(flat, width_in: float, length_in: float) -> Tuple[List[Box], List[Box], str]:
    """find_holes, and the maybes: what nearly made it (at most MAX_MAYBES, none over a box)."""
    found = find(flat, width_in, length_in)
    return found["boxes"], found["maybes"], found["how"]


def find(flat, width_in: float, length_in: float) -> dict:
    """Everything the page gets: "boxes" (inches), "groups" (which hole each box covers: a hole's stepped boxes share
    one), "maybes" and "how"."""
    if flat is None or not AVAILABLE:
        return {"boxes": [], "groups": [], "maybes": [], "how": ""}
    k = _Look(flat)
    maybes: list = []
    if k.sep >= SEPARATE:
        px_boxes, how = _holes_by_look(flat, k.F, k.inside, k.floor_sel, k.rim, k.d_edge, k.ink, k.floor_m, k.mat_m,
                                       maybes), "look"
    else:
        px_boxes, how = _holes_by_outline(flat, k.inside, k.floor_sel, k.d_edge, k.ink, maybes), "outline"
    # holes still full of film and chips: busy where the piece is smooth (added only where nothing was found). Not on
    # bare aluminum on concrete: there the metal's own scratches and grain read as busy too
    busy: List[_Region] = []
    if how == "look":
        px_boxes = _labels(px_boxes, flat, k.floor_sel)        # a label or a sticker isn't a hole
        dens = _busy(flat)
        for t in _holes_by_texture(flat, k.inside, k.floor_sel, k.d_edge, k.ink, k.paper, dens=dens):
            if _covered_px(t, px_boxes) < 0.5:
                busy.append(t)
        busy = _scraps(_labels(busy, flat, k.floor_sel), px_boxes, flat.shape)
        for t in _holes_by_texture(flat, k.inside, k.floor_sel, k.d_edge, k.ink, k.paper, keen=0.75, dens=dens):
            if _covered_px(t, px_boxes + busy) < 0.5:
                maybes.append(t.box)
    px_boxes = px_boxes + busy
    regions = sorted(_merge(px_boxes), key=lambda r: (-r.y1, r.x0))      # from the front end
    out: List[Box] = []
    groups: List[int] = []
    for g, r in enumerate(regions):
        for p in _steps(r):
            b = _px_to_in(p, flat, width_in, length_in)
            if b[2] - b[0] > 0.05 and b[3] - b[1] > 0.05:
                out.append(b)
                groups.append(g)
    gap, rim = 0.3 + 2 * BOX_MARGIN_IN, 0.5 + BOX_MARGIN_IN
    maybe = [b for b in _drop_inside(_to_inches([t for t in maybes if _covered_px(t, px_boxes) < 0.5], flat,
                                                width_in, length_in))
             if b not in out
             # not a sliver beside a box (the rest of that hole) nor at the piece's edge (peeled film, burrs)
             and not any(b[0] < o[2] + gap and o[0] < b[2] + gap and b[1] < o[3] + gap and o[1] < b[3] + gap for o in out)
             and b[0] >= rim and b[1] >= rim and b[2] <= width_in - rim and b[3] <= length_in - rim]
    maybe = sorted(maybe, key=lambda b: -(b[2] - b[0]) * (b[3] - b[1]))[:MAX_MAYBES]
    return {"boxes": out, "groups": groups, "maybes": sorted(maybe, key=lambda b: (b[1], b[0])), "how": how}


GROW_WINDOW_IN = 20.0      # how far round a tap a hole is looked for
GROW_STEPS = (3, 4, 5, 6.5, 8, 10, 13, 17, 22)    # how unlike the tapped spot "surely not this hole" is, keenest first


def _tap_region(flat, x: int, y: int, k: "_Look", far: float, win: Tuple[int, int, int, int]):
    """The hole round (x, y) at one keenness: a watershed between the tapped spot and everything clearly unlike it (in
    brightness, colour and grain, by the spot's own spread) or off the piece. None if the tap isn't in it."""
    x0, y0, x1, y1 = win
    F = k.F[y0:y1, x0:x1]
    yy, xx = np.mgrid[y0:y1, x0:x1]
    disk = (xx - x) ** 2 + (yy - y) ** 2 <= (0.3 * PX) ** 2
    spot = disk & ~k.ink[y0:y1, x0:x1]
    if spot.sum() < 20:
        spot = disk
    ds = _dist(F, _model(F, spot)) / F.shape[-1]
    markers = np.zeros((y1 - y0, x1 - x0), np.int32)
    markers[(ds > far * far) | (~k.inside | (k.d_edge < 0.15 * PX))[y0:y1, x0:x1]] = 1
    markers[disk] = 2
    ws = cv2.watershed(np.ascontiguousarray(flat[y0:y1, x0:x1]), markers)
    n, lab = cv2.connectedComponents((ws == 2).astype(np.uint8))
    lb = lab[y - y0, x - x0]
    return None if lb == 0 else lab == lb


def grow_at(flat, width_in: float, length_in: float, at: Sequence[float]) -> Optional[List[Box]]:
    """The boxes (stepped, like find's) over the hole someone tapped (`at`: inches across, along from the front), or None when no hole can be
    told there (the tap is on the metal, or the hole runs into everything round it). By look first, where the photo
    has it (the floor-like patch the tap is in); else the hole grown from the tapped spot, less keenly each step, kept
    from just before it floods the metal round it: a hole fills to its cut edges and holds there over a few steps, the
    metal floods at once."""
    if flat is None or not AVAILABLE:
        return None
    m = MARGIN_IN * PX
    H, W = flat.shape[:2]
    x, y = int(round(m + float(at[0]) * PX)), int(round(H - m - float(at[1]) * PX))
    k = _Look(flat)
    if not (0 <= x < W and 0 <= y < H) or not k.inside[y, x] or k.d_edge[y, x] < 0.15 * PX:
        return None
    region = None
    if k.sep >= SEPARATE:
        df, dm = _dist(k.F, k.floor_m), _dist(k.F, k.mat_m)
        floorish = (k.inside & (k.d_edge >= 0.15 * PX) & (df < dm) & ~k.ink).astype(np.uint8)
        # thin floor-like bridges (a reflection across the strip between two holes) break, as for the finder's seeds
        floorish = cv2.morphologyEx(floorish, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)))
        n, lab = cv2.connectedComponents(floorish)
        lb = lab[y, x]
        if lb:
            r = lab == lb
            if r.sum() < 0.5 * k.inside.sum():
                region = r
        if region is None:
            # the tapped spot looks like the piece, and isn't busy like a hole full of film and chips: the piece
            r0 = int(0.3 * PX)
            spot = (slice(max(0, y - r0), y + r0 + 1), slice(max(0, x - r0), x + r0 + 1))
            busy = float(_busy(flat[max(0, y - 3 * r0):y + 3 * r0 + 1, max(0, x - 3 * r0):x + 3 * r0 + 1]).max())
            if float(np.median(dm[spot])) < float(np.median(df[spot])) and busy < 0.75 * TEXTURE_MIN:
                return None
    if region is None:
        hw = int(GROW_WINDOW_IN / 2 * PX)
        win = (max(0, x - hw), max(0, y - hw), min(W, x + hw), min(H, y + hw))
        last, last_area, steady = None, 0, False
        for far in GROW_STEPS:
            r = _tap_region(flat, x, y, k, far, win)
            if r is None:
                continue
            ys, xs = np.nonzero(r)
            floods = xs.min() <= 2 or ys.min() <= 2 or xs.max() >= r.shape[1] - 3 or ys.max() >= r.shape[0] - 3
            if floods:
                break
            area = int(r.sum())
            steady = steady or (last is not None and area <= 1.25 * last_area)
            last, last_area = r, area
        if last is None or not steady:
            return None
        region = np.zeros((H, W), bool)
        region[win[1]:win[3], win[0]:win[2]] = last
    if int(region.sum()) < (0.2 * PX) ** 2:
        return None
    out = [_px_to_in(p, flat, width_in, length_in) for p in _steps(_Region.of(region))]
    return [b for b in out if b[2] - b[0] > 0.05 and b[3] - b[1] > 0.05] or None
