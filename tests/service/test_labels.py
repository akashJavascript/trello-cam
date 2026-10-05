"""Part labels drawn on a sheet's preview (labels.py)."""

import builtins
import io

import pytest

from autocam_service import labels

PIL = pytest.importorskip("PIL")


def picture(w=1000, h=1600):
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (w, h), (40, 46, 58)).save(out, format="PNG")
    return out.getvalue()


SPOTS = {"image": [1000, 1600], "sheet": "6061_0p125_r001_S1",
         "labels": [{"n": 1, "name": "P-2014", "copy": "", "x": 300.0, "y": 900.0, "room": 60.0},
                    {"n": 2, "name": "gusset", "copy": "2/3", "x": 700.0, "y": 1200.0, "room": 10.0}]}


def test_badges_and_names_are_drawn_where_the_worker_said():
    from PIL import Image
    out = Image.open(io.BytesIO(labels.draw(picture(), SPOTS)))
    assert out.size == (1000, 1600)
    assert out.getpixel((300 + 20, 900)) == labels.BADGE                  # inside the first badge, beside its number
    assert out.getpixel((700 + 12, 1200)) == labels.BADGE                 # a small part still gets a 16 px badge
    below = [out.getpixel((300 + dx, 900 + 30 + 4 + 10)) for dx in range(-40, 40)]
    assert labels.NAME in below                                           # its name, under the badge
    assert out.getpixel((10, 1590)) == (40, 46, 58)                       # nothing else changed


def test_without_labels_or_pillow_the_picture_goes_as_it_was(monkeypatch):
    png = picture(50, 80)
    assert labels.labelled(png, None) == png and labels.labelled(png, {"labels": []}) == png
    real = builtins.__import__

    def no_pillow(name, *a, **kw):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("no PIL")
        return real(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", no_pillow)
    assert labels.labelled(png, SPOTS) == png
    assert labels.labelled(b"not a png", SPOTS) == b"not a png"


def test_names_of_parts_side_by_side_dont_land_on_each_other():
    from PIL import Image
    close = {"image": [1000, 1600], "labels": [
        {"n": 1, "name": "P-2041", "copy": "", "x": 212.0, "y": 1172.0, "room": 18.0},
        {"n": 2, "name": "P-2020", "copy": "", "x": 262.0, "y": 1168.0, "room": 18.0}]}
    out = Image.open(io.BytesIO(labels.draw(picture(), close)))
    # the first name went under its badge; the second can't go there too (it would cover the first), so it's
    # somewhere else: nothing white is drawn right under the second badge's centre where the first name isn't
    from PIL import ImageDraw
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    font = labels._font(16)
    first = d.textbbox((212, 1172 + 16 + 4), "P-2041", font=font, anchor="mt", stroke_width=3)
    second_under = d.textbbox((262, 1168 + 16 + 4), "P-2020", font=font, anchor="mt", stroke_width=3)
    assert labels._overlap(first, second_under) > 0            # under would have collided
    white_right = [out.getpixel((262 + 16 + 6 + dx, 1168)) for dx in range(5, 60)]
    white_above = [out.getpixel((262 + dx, 1168 - 16 - 10)) for dx in range(-30, 30)]
    assert labels.NAME in white_right + white_above             # so it went above or to the right


def test_the_command_labels_every_sheet_in_a_job_folder(tmp_path, capsys):
    import json
    from autocam_service import cli
    (tmp_path / "6061_0p125_r001_S1.png").write_bytes(picture())
    (tmp_path / "6061_0p125_r001_S1.labels.json").write_text(json.dumps(SPOTS))
    (tmp_path / "6061_0p125_r001_S2.png").write_bytes(picture())           # made before labels existed
    assert cli.main(["preview-labels", str(tmp_path)]) == 1
    said = capsys.readouterr().out
    assert "6061_0p125_r001_S1.png: labelled -> " in said and "S2.labels.json beside it" in said
    drawn = (tmp_path / "6061_0p125_r001_S1.labelled.png").read_bytes()
    assert drawn == labels.draw(picture(), SPOTS)
    assert cli.main(["preview-labels", str(tmp_path)]) == 1                 # its own output isn't a sheet
    assert "labelled.labels.json" not in capsys.readouterr().out
    out = tmp_path / "look.png"
    assert cli.main(["preview-labels", str(tmp_path / "6061_0p125_r001_S1.png"), "--out", str(out)]) == 0
    assert out.read_bytes() == drawn
