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
