"""How many tabs a contour gets (autocam_core/tabs.py)."""

from autocam_core.tabs import outline_length, tab_count, walls_length
from geombuilder import PlateBuilder

CUTTER = 4 / 25.4            # the 4 mm cutter: one tab per 0.63 in of contour at most


def test_one_per_distance_between_two_and_six():
    assert tab_count(10.0, 2.5, CUTTER) == 4
    assert tab_count(40.0, 2.5, CUTTER) == 6                  # a big outline: no more than 6
    assert tab_count(3.0, 2.5, CUTTER) == 2                   # short: still 2 (spacing by distance gave 0 or 1)


def test_a_short_contour_gets_what_fits():
    assert tab_count(1.0, 2.5, CUTTER) == 1                   # room for one
    assert tab_count(0.5, 2.5, CUTTER) == 0                   # a slug that's little more than chips: none
    assert tab_count(5.0, 2.5, 0.5) == 2 and tab_count(1.9, 2.5, 0.5) == 0


def test_lengths_from_the_wall_areas():
    b = PlateBuilder("p", thickness=0.25)                     # outline: four walls of area 1 = 16 in around
    small = b.cutout(2.0)
    b.hole(0.5)                                                # walls without areas count for nothing here
    g = b.build()
    assert walls_length(g, small) == 2.0
    inner = set(small) | {f.id for f in g.faces if f.kind == "cylinder"}
    assert outline_length(g, inner) == 16.0
