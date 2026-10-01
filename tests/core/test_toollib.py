from pathlib import Path

import pytest

from autocam_core.toollib import ToolLibrary, ToolLibraryError

LIBRARY = Path(__file__).resolve().parents[2] / "fusion" / "tools" / "5940_Tool_Library.tools"

ALU_4MM = "7b77ef53-1ace-4e3b-ac2d-380b01a638bd"
POLY_4MM = "ec14e4ef-aa38-4a96-af26-8885accae4a3"
ALU_EIGHTH = "7e0681ca-7664-4d53-ae3a-7457a77fd00a"
ONSRUD_4MM = "423977fe-8784-4556-8207-c2279ed05597"  # in the library, never to be used


@pytest.fixture(scope="module")
def lib():
    return ToolLibrary.load(LIBRARY)


@pytest.mark.parametrize("guid, number, diameter_in, flute_in", [
    (ALU_4MM, 1, 4 / 25.4, 12 / 25.4),
    (POLY_4MM, 1, 4 / 25.4, 18 / 25.4),
    (ALU_EIGHTH, 12, 0.125, 0.4724409448818898),
])
def test_shop_tools_resolve_by_guid(lib, guid, number, diameter_in, flute_in):
    tool = lib.by_guid(guid)
    assert tool.number == number
    assert tool.diameter_in == pytest.approx(diameter_in)
    assert tool.flute_length_in == pytest.approx(flute_in)
    assert tool.flutes == 1


def test_tool_numbers_do_not_identify_tools(lib):
    dupes = lib.duplicate_numbers()
    assert {1, 2, 3} <= set(dupes)
    assert {t.guid for t in dupes[1]} == {ALU_4MM, POLY_4MM}
    # Another 4 mm endmill exists with a different number: diameter can't identify a tool either.
    onsrud = lib.by_guid(ONSRUD_4MM)
    assert onsrud.diameter_in == pytest.approx(4 / 25.4) and onsrud.number == 2


def test_unknown_guid_raises(lib):
    with pytest.raises(KeyError, match="no tool with GUID"):
        lib.by_guid("00000000-0000-0000-0000-000000000000")


def test_unreadable_library_raises(tmp_path):
    bad = tmp_path / "bad.tools"
    bad.write_bytes(b"not a zip")
    with pytest.raises(ToolLibraryError):
        ToolLibrary.load(bad)
