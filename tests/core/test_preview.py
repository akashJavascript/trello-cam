"""The preview's framing, design-to-pixel mapping, and where a part's label goes (autocam_core/preview.py)."""

from autocam_core.preview import label_spot, pixels_per_unit, preview_size, preview_view, to_pixels

SHEET = (100.0, 0.0, 124.0, 48.0)          # a 24 x 48 sheet at x=100 in the design


def test_the_framing_matches_what_fusion_was_given():
    assert preview_size(SHEET) == (1000, 1600)
    cx, cy, extent = preview_view(SHEET, (1000, 1600))
    assert (cx, cy) == (112.0, 24.0) and abs(extent - 48 * 1000 / 1600 * 1.08) < 1e-9   # r015: 32.4 in across


def test_design_points_land_where_the_camera_put_them():
    size = preview_size(SHEET)
    assert to_pixels(112.0, 24.0, SHEET, size) == (500.0, 800.0)                       # the centre
    x0, y0 = to_pixels(100.0, 0.0, SHEET, size)                                         # front-left corner
    x1, y1 = to_pixels(124.0, 48.0, SHEET, size)
    assert 0 < x0 < x1 < 1000 and 0 < y1 < y0 < 1600                                    # Y up -> rows down
    assert abs((x1 - x0) / 24 - pixels_per_unit(SHEET, size)) < 1e-9


def square(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def test_a_label_goes_on_the_material_away_from_the_edges():
    x, y, room = label_spot(square(0, 0, 4, 2))
    assert abs(x - 2) < 0.1 and abs(y - 1) < 0.1 and abs(room - 1) < 0.1             # a plain plate: its middle
    # a ring (a 4 x 4 plate with a 3 x 3 hole): never in the hole, in the band of material
    x, y, room = label_spot(square(0, 0, 4, 4), [square(0.5, 0.5, 3.5, 3.5)])
    assert not (0.5 < x < 3.5 and 0.5 < y < 3.5) and 0 < room <= 0.25 + 1e-6
    # a C shape (an L along the left and bottom, 1 in wide): its corner, not the box's middle
    c = [(0, 0), (5, 0), (5, 1), (1, 1), (1, 5), (0, 5)]
    x, y, room = label_spot(c)
    assert x < 1 and y < 1 + 0.5 and room > 0.4


def test_a_part_thats_mostly_cutouts_gets_its_label_in_the_middle():
    ring = (square(0, 0, 4, 4), [square(0.5, 0.5, 3.5, 3.5)])     # a 0.5 wide band: room 0.25 at most
    x, y, room = label_spot(*ring, min_room=0.2)
    assert not (0.5 < x < 3.5 and 0.5 < y < 3.5)                    # a badge that small fits on the band
    x, y, room = label_spot(*ring, min_room=1.0)
    assert abs(x - 2) < 0.1 and abs(y - 2) < 0.1 and abs(room - 2) < 0.1   # it doesn't: the middle, over the hole
    x, y, room = label_spot(square(0, 0, 1, 1), min_room=1.0)       # no cutouts: nothing else to try
    assert abs(x - 0.5) < 0.1 and room < 1.0
