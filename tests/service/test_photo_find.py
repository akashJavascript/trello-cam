"""Photo offcuts' finding (photo_find.py) on made-up photos: a piece with known holes laid on a floor, seen at an
angle. (Tuned on the team's own photos, which stay out of this public repo.)"""
import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from autocam_service import photo_find as pf  # noqa: E402

PHOTO_CORNERS = [[130, 1010], [780, 1030], [725, 215], [185, 200]]   # front-left, front-right, back-right, back-left


def _ground(kind, shape, rng):
    h, w = shape
    if kind == "concrete":                        # grainy, a little warm
        base = np.array([108, 112, 118], np.float32)
        img = base + rng.normal(0, 13, (h, w, 1)) + rng.normal(0, 4, (h, w, 3))
    else:                                          # wood: light, warm, smooth grain lines along it
        base = np.array([150, 185, 214], np.float32)
        lines = 6 * np.sin(np.arange(w) / 3.0 + rng.normal(0, 0.3, w))[None, :, None]
        img = base + lines + rng.normal(0, 2.5, (h, w, 3))
    return np.clip(img, 0, 255).astype(np.uint8)


def _piece(kind, w, h, rng):
    if kind == "film":                            # white film: bright, smooth
        img = np.array([224, 228, 226], np.float32) + rng.normal(0, 1.5, (h, w, 3))
    else:                                          # bare aluminum: grey, smooth, a few scratches; "dull": it mirrors
        base = [108, 112, 118] if kind == "dull aluminum" else [150, 152, 154]   # the concrete's colour and grain
        img = np.array(base, np.float32) + rng.normal(0, 2.0, (h, w, 3))
        if kind == "dull aluminum":
            img += rng.normal(0, 12, (h, w, 1))
        for _ in range(12):
            y = rng.integers(0, h)
            cv2.line(img, (0, int(y)), (w - 1, int(y + rng.integers(-20, 20))), (175, 177, 175), 1)
    return np.clip(img, 0, 255).astype(np.uint8)


def photo(piece_kind, ground_kind, holes, length_in=30.0, width_in=24.0, seed=3, filled=()):
    """A made-up phone photo: holes are (x0, y0, x1, y1) in inches across and along from the front-left corner; the
    `filled` ones are still full of crumpled clear film and chips (grey like the metal, but busy)."""
    rng = np.random.default_rng(seed)
    k = 20                                        # px per inch of the flat piece before it's seen at an angle
    w, h = int(width_in * k), int(length_in * k)
    flat = _piece(piece_kind, w, h, rng)
    mask = np.full((h, w), 255, np.uint8)
    for x0, y0, x1, y1 in holes:                  # along from the front (bottom of the flat image)
        cv2.rectangle(mask, (int(x0 * k), int(h - y1 * k)), (int(x1 * k), int(h - y0 * k)), 0, -1)
    H = cv2.getPerspectiveTransform(np.float32([[0, h], [w, h], [w, 0], [0, 0]]), np.float32(PHOTO_CORNERS))
    shape = (1200, 900)
    ground = _ground(ground_kind, shape, rng)
    seen = cv2.warpPerspective(flat, H, shape[::-1])
    m = cv2.warpPerspective(mask, H, shape[::-1]) > 127
    out = ground.copy()
    out[m] = seen[m]
    if filled:
        crinkle = np.clip(np.array([150, 152, 154], np.float32) + rng.normal(0, 4, (h, w, 3)), 0, 255).astype(np.uint8)
        for _ in range(30000):                    # creases and chips, as dense as the real ones
            x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
            cv2.line(crinkle, (x, y), (x + int(rng.integers(-6, 7)), y + int(rng.integers(-6, 7))),
                     (int(rng.integers(90, 235)),) * 3, 1)
        fm = np.zeros((h, w), np.uint8)
        for x0, y0, x1, y1 in filled:
            cv2.rectangle(fm, (int(x0 * k), int(h - y1 * k)), (int(x1 * k), int(h - y0 * k)), 255, -1)
        fseen = cv2.warpPerspective(crinkle, H, shape[::-1])
        fmask = cv2.warpPerspective(fm, H, shape[::-1]) > 127
        out[fmask] = fseen[fmask]
    ok, buf = cv2.imencode(".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    return buf.tobytes()


def covers(box, hole, slack=0.6):
    """The box covers the hole, give or take `slack` in."""
    return (box[0] <= hole[0] + slack and box[1] <= hole[1] + slack and box[2] >= hole[2] - slack
            and box[3] >= hole[3] - slack)


HOLES = [(3, 4, 9, 12), (13, 3, 20, 8), (12, 15, 19, 24), (3, 18, 8, 26)]


def test_film_on_concrete_finds_corners_and_every_hole():
    img = pf.decode(photo("film", "concrete", HOLES))
    corners = pf.suggest_corners(img, 24, 30)
    assert corners is not None
    for got, want in zip(corners, PHOTO_CORNERS):
        assert abs(got[0] - want[0]) < 25 and abs(got[1] - want[1]) < 25
    boxes, how = pf.find_holes(pf.straighten(img, corners, 24, 30), 24, 30)
    assert how == "look"
    for hole in HOLES:
        assert any(covers(b, hole) for b in boxes), (hole, boxes)
    assert len(boxes) == len(HOLES)
    for b in boxes:                                # each box is about its hole plus the margin, not a whole row
        assert b[2] - b[0] < 9 and b[3] - b[1] < 10


def test_aluminum_on_wood_is_found_by_look():
    img = pf.decode(photo("aluminum", "wood", HOLES))
    boxes, how = pf.find_holes(pf.straighten(img, PHOTO_CORNERS, 24, 30), 24, 30)
    assert how == "look"
    for hole in HOLES:
        assert any(covers(b, hole) for b in boxes), (hole, boxes)


def test_a_hole_still_full_of_film_and_chips_gets_a_box():
    """Aluminum on the table with one hole still full of crumpled clear film: grey like the metal, but busy."""
    img = pf.decode(photo("aluminum", "wood", HOLES, filled=[HOLES[2]]))
    boxes, how = pf.find_holes(pf.straighten(img, PHOTO_CORNERS, 24, 30), 24, 30)
    assert how == "look"
    for hole in HOLES:
        assert any(covers(b, hole) for b in boxes), (hole, boxes)


def test_aluminum_on_concrete_goes_by_outline_and_boxes_nothing_solid():
    """Bare aluminum mirroring the floor (as bright as the concrete): nothing to tell them apart but the cut edges.
    Whatever it suggests must be a hole (a wrong box is extra work); missing some is expected, people add them."""
    img = pf.decode(photo("dull aluminum", "concrete", HOLES))
    boxes, how = pf.find_holes(pf.straighten(img, PHOTO_CORNERS, 24, 30), 24, 30)
    assert how == "outline"
    for b in boxes:
        assert any(covers(b, hole, slack=1.5) for hole in HOLES), (b, HOLES)


def test_boxes_keep_to_the_piece_and_get_the_margin():
    img = pf.decode(photo("film", "concrete", [(0.5, 0.5, 4, 4)]))
    boxes, _ = pf.find_holes(pf.straighten(img, PHOTO_CORNERS, 24, 30), 24, 30)
    assert boxes
    b = boxes[0]
    assert b[0] >= 0 and b[1] >= 0                 # never off the piece
    assert b[2] >= 4 + pf.BOX_MARGIN_IN - 0.5      # the margin round the hole


def test_no_corners_suggested_when_nothing_fits_the_size():
    img = pf.decode(photo("film", "concrete", HOLES))
    assert pf.suggest_corners(img, 24, 60) is None   # a 30 in piece isn't a 60 in one: better none than wrong


def test_order_puts_the_front_at_the_bottom():
    assert pf.order([[725, 215], [130, 1010], [185, 200], [780, 1030]]) == \
        [[130.0, 1010.0], [780.0, 1030.0], [725.0, 215.0], [185.0, 200.0]]


def test_not_a_photo():
    assert pf.decode(b"not a jpeg") is None


@pytest.mark.parametrize("piece,ground", [("film", "concrete"), ("aluminum", "wood")])
def test_a_tap_on_a_hole_boxes_it_and_a_tap_on_the_piece_doesnt(piece, ground):
    flat = pf.straighten(pf.decode(photo(piece, ground, HOLES)), PHOTO_CORNERS, 24, 30)
    for hole in HOLES:
        b = pf.grow_at(flat, 24, 30, ((hole[0] + hole[2]) / 2, (hole[1] + hole[3]) / 2))
        assert b is not None and covers(b, hole), (hole, b)
    for at in ((11, 13), (21, 26), (2, 28)):           # on the piece, between holes and near its edge
        assert pf.grow_at(flat, 24, 30, at) is None, at
    assert pf.grow_at(flat, 24, 30, (30, 10)) is None  # off the piece


def test_a_tap_on_bare_aluminum_on_concrete_is_answered():
    flat = pf.straighten(pf.decode(photo("dull aluminum", "concrete", HOLES)), PHOTO_CORNERS, 24, 30)
    b = pf.grow_at(flat, 24, 30, (6, 8))               # the hardest case: it may or may not tell; it mustn't fail
    assert b is None or (0 <= b[0] < b[2] <= 24 and 0 <= b[1] < b[3] <= 30)


def test_a_hole_just_under_the_size_comes_back_as_a_maybe():
    small = (21.4, 27.0, 21.85, 27.45)                 # 0.45 in: under MIN_HOLE_IN, not under MAYBE_MIN_IN
    flat = pf.straighten(pf.decode(photo("film", "concrete", HOLES + [small])), PHOTO_CORNERS, 24, 30)
    boxes, maybes, how = pf.find_holes_and_maybes(flat, 24, 30)
    assert (boxes, how) == pf.find_holes(flat, 24, 30)
    assert not any(covers(b, small, 0.3) for b in boxes)
    assert any(covers(m, small, 0.3) for m in maybes), maybes
