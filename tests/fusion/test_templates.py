"""The committed templates: alu_4mm is the team's (plus the [bearing] op, add_bearing.py); the others are made
from it by fusion/tools/make_templates/ and must be exactly what those make."""

import re
import subprocess
import sys

import pytest

from autocam_service.config import DEFAULT_CONFIG, REPO_ROOT, load_config

TOOLS = REPO_ROOT / "fusion" / "tools"
TEMPLATES = REPO_ROOT / "fusion" / "templates"
ORDER = ["[pocket] pockets", "[pocketfinish] pocket walls", "[drill] holes", "[bore] holes", "[bearing] holes",
         "[inner] cutouts", "[outer] outline"]


def make(script, *args):
    subprocess.run([sys.executable, str(TOOLS / "make_templates" / script), *map(str, args)], check=True,
                   capture_output=True)


@pytest.mark.parametrize("name, args", [
    ("poly_4mm", ("make_poly4mm.py",)),
    ("alu_eighth", ("make_eighth.py", TOOLS / "5940_Tool_Library.tools", "1/8 aluminum", "alu_eighth")),
    ("poly_eighth", ("make_eighth.py", TOOLS / "5940_Tool_Library.tools", "1/8 polycarbonate", "poly_eighth")),
])
def test_made_templates_are_what_their_generator_makes(tmp_path, name, args):
    out = tmp_path / f"{name}.f3dhsm-template"
    make(args[0], TEMPLATES / "alu_4mm.f3dhsm-template", *args[1:], out)
    assert out.read_bytes() == (TEMPLATES / f"{name}.f3dhsm-template").read_bytes()


def test_every_template_has_the_bearing_op_with_a_finishing_pass_and_its_own_tool():
    cfg = load_config(DEFAULT_CONFIG)
    for key, tpl in cfg.templates.items():
        text = tpl.file.read_text(encoding="utf-8")
        assert re.findall(r'<template description="([^"]*)"', text) == ORDER, key
        bearing = text[text.index('[bearing] holes'):]
        bearing = bearing[:bearing.index("</template>")]
        assert '<parameter expression="true" name="doFinishingPasses"/>' in bearing
        assert '<parameter expression="0.005in" name="finishingStepover"/>' in bearing
        assert re.findall(r'<tool guid="([^"]*)"', bearing) == [cfg.tools[tpl.tool].guid]


def test_add_bearing_refuses_a_template_that_has_one(tmp_path):
    f = tmp_path / "t.f3dhsm-template"
    f.write_bytes((TEMPLATES / "alu_4mm.f3dhsm-template").read_bytes())
    with pytest.raises(subprocess.CalledProcessError):
        make("add_bearing.py", f)
