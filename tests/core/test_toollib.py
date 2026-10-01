from pathlib import Path

import pytest

from autocam_core.toollib import ToolLibrary, ToolLibraryError

LIBRARY = Path(__file__).resolve().parents[2] / "fusion" / "tools" / "5940_Tool_Library.tools"

ALU_4MM = "e5dd75b2-ea23-4767-b10b-756a9444cc2e"
POLY_4MM = "b4521723-7c35-4121-b376-50f3db7bc3a0"
ALU_EIGHTH = "dc1f12bd-dbcc-4d53-9ad2-9a65b2ed6fb5"
POLY_EIGHTH = "7a26b9db-1b3f-4695-8c8e-3439cc04258a"
POLY_3MM = "bb712eaf-2330-48b6-a2d3-a41dfb13023d"  # also T1, never to be used


@pytest.fixture(scope="module")
def lib():
    return ToolLibrary.load(LIBRARY)


@pytest.mark.parametrize("guid, number, diameter_in, flute_in", [
    (ALU_4MM, 1, 4 / 25.4, 12 / 25.4),
    (POLY_4MM, 1, 4 / 25.4, 18 / 25.4),
    (ALU_EIGHTH, 12, 0.125, 0.472441),
    (POLY_EIGHTH, 12, 0.125, 0.472441),
])
def test_shop_tools_resolve_by_guid(lib, guid, number, diameter_in, flute_in):
    tool = lib.by_guid(guid)
    assert tool.number == number
    assert tool.diameter_in == pytest.approx(diameter_in)
    assert tool.flute_length_in == pytest.approx(flute_in)
    assert tool.flutes == 1


def test_tool_numbers_do_not_identify_tools(lib):
    dupes = lib.duplicate_numbers()
    assert {1, 12} <= set(dupes)
    assert {t.guid for t in dupes[1]} == {ALU_4MM, POLY_4MM, POLY_3MM}
    assert {t.guid for t in dupes[12]} == {ALU_EIGHTH, POLY_EIGHTH}
    # Same number, same diameter, different cutter: only the GUID tells them apart.
    alu, poly = lib.by_guid(ALU_4MM), lib.by_guid(POLY_4MM)
    assert (alu.number, alu.diameter_in) == (poly.number, poly.diameter_in) and alu.guid != poly.guid


def test_unknown_guid_raises(lib):
    with pytest.raises(KeyError, match="no tool with GUID"):
        lib.by_guid("00000000-0000-0000-0000-000000000000")


def test_unreadable_library_raises(tmp_path):
    bad = tmp_path / "bad.tools"
    bad.write_bytes(b"not a zip")
    with pytest.raises(ToolLibraryError):
        ToolLibrary.load(bad)
