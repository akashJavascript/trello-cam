"""Scribed bend lines (core 0.18.0): where a flat pattern's bends are, and the checks both sides run on scribing."""

import copy

import pytest

from autocam_core.schema import SchemaError
from autocam_core.schema_job import load_job
from autocam_core.scribe import bend_centerlines, distance_to_line, off_the_lines, scribe_sections
from autocam_core.sheetcheck import check_scribe_program, sheet_scribe_problems

from test_schema_hotfolder import JOB

BEND = (90.0, 0.05)
T = 0.125


def part13():
    """Part 13's flat (doc 31359564..., read off its STEP 2026-11-01): two flats and the 0.1669 in strip of its
    one bend (R 0.05 in, 1/8 in 5052, k 0.45) between them, as each top face's outer loop."""
    a, b, y0, y1 = -13.7625, -13.9294, -6.375, -1.375
    return {
        1427: [("e596", (a, y0), (a, y1), True), ("a1", (a, y1), (-0.062, y1), True), ("a2", (-0.062, y1), (a, y0), False)],
        1466: [("e596", (a, y1), (a, y0), True), ("z1", (a, y0), (b, y0), True), ("e669", (b, y0), (b, y1), True),
               ("z2", (b, y1), (a, y1), True)],
        1461: [("e669", (b, y1), (b, y0), True), ("b1", (b, y0), (-23.817, y0), True), ("b2", (-23.817, y0), (b, y1), True)],
    }


def strips(*xs, h=4.0):
    """A flat cut into faces by vertical lines at xs (each pair of neighbours shares its edge)."""
    xs = (0.0,) + xs + (10.0,)
    faces = {}
    for i, (x0, x1) in enumerate(zip(xs, xs[1:])):
        left = f"v{i}" if i else "left"
        right = f"v{i + 1}" if i + 2 < len(xs) else "right"
        faces[i] = [(f"b{i}", (x0, 0.0), (x1, 0.0), True), (right, (x1, 0.0), (x1, h), True),
                    (f"t{i}", (x1, h), (x0, h), True), (left, (x0, h), (x0, 0.0), True)]
    return faces


def test_part_13s_bend_line_is_down_the_middle_of_its_strip():
    lines, why = bend_centerlines(part13(), [BEND], T)
    assert why == ""
    ((x0, y0, x1, y1),) = lines
    assert abs(x0 - (-13.84595)) < 1e-9 and abs(x1 - x0) < 1e-9 and {y0, y1} == {-6.375, -1.375}


def test_a_channels_middle_isnt_a_bend():
    lines, why = bend_centerlines(strips(2.0, 2.167, 7.833, 8.0), [BEND, BEND], T)
    assert why == "" and [round(l[0], 4) for l in lines] == [2.0835, 7.9165]


def test_no_guessing_when_the_strips_dont_add_up():
    # a middle flat as narrow as a bend zone: three candidates for two bends
    assert bend_centerlines(strips(2.0, 2.167, 2.3, 2.467), [BEND, BEND], T) == (
        (), "3 bend zones found on its top for its 2 bends")
    assert bend_centerlines(strips(2.0, 2.167), [(90.0, 0.5)], T)[1].startswith("0 bend zones")   # too narrow for R 0.5
    assert bend_centerlines(strips(2.0, 2.167), [(0.0, 0.0)], T) == ((), "its bend table couldn't be read")
    assert bend_centerlines({1: strips(2.0, 2.167)[0]}, [BEND], T) == ((), "its top isn't split along its bends")


def test_scribing_points_must_be_on_a_line():
    line = (1.0, 0.0, 1.0, 4.0)
    assert distance_to_line(1.001, 2.0, line) < 0.0011 and abs(distance_to_line(1.0, 5.0, line) - 1.0) < 1e-9
    assert off_the_lines([(10, 1.0, 2.0, 0.1), (11, 1.01, 2.0, 0.1)], [line]) == [(11, 1.01, 2.0)]


PROGRAM = """[S1]
M11 C8
G90
G20
G53 Z
[scribe bend lines]
S18000
M3
G0 X1. Y2.
Z0.325
G1 Z{z} F20.
G1 Y5. F60.
G0 Z0.725
[outer p01-1]
G0 X0.5 Y0.5
G1 Z0. F20.
G1 X7. F60.
G0 Z0.725
M12 C8
G53 Z
M5
G53 P10
"""


def scribe_job(way="endmill", face=False):
    data = copy.deepcopy(JOB)
    bend = {"name": "Bend D", "angle_deg": 90.0, "direction": "down", "radius_in": 0.05, "order": 1}
    data["parts"][0].update(bends=[bend], scribe=way, face=face)
    data["scribe"] = {"depth_in": 0.012, "chamfer_depth_in": 0.012}
    if way == "chamfer":
        tool = dict(data["tooling"]["tools"][data["tooling"]["default"]])
        tool.update(key="scribe", guid="915d08db-80ad-44e2-8fd5-6b9ed2129f59", number=16, diameter_in=0.23622)
        data["scribe"].update(chamfer=tool, chamfer_tool_json='{"guid": "915d08db-80ad-44e2-8fd5-6b9ed2129f59"}')
    job = load_job(data)
    assert job.validate() == [], job.validate()
    return job


def test_the_sheets_scribing_is_checked_for_depth_and_order():
    job = scribe_job()
    t = job.material.thicknesses_in[0]
    counts = {job.parts[0].part_key: 1}
    good = PROGRAM.format(z=f"{t - 0.012:.4f}")
    assert scribe_sections(good) == [(6, 13)]
    assert sheet_scribe_problems(good, job, t, counts) == []
    deep = PROGRAM.format(z=f"{t - 0.03:.4f}")
    assert "below" in sheet_scribe_problems(deep, job, t, counts)[0]
    shallow = PROGRAM.format(z=f"{t - 0.002:.4f}")
    assert "never gets to" in sheet_scribe_problems(shallow, job, t, counts)[0]
    after = good.replace("[scribe bend lines]", "[x]").replace("[outer p01-1]", "[outer p01-1]\n[scribe bend lines]\nG1 Z0.1 F2.")
    assert any("after cutting an outline" in p for p in sheet_scribe_problems(after, job, t, counts))
    assert "no part on the sheet asks" in sheet_scribe_problems(good, scribe_job(way=""), t, counts)[0]


def test_a_scribe_program_scribes_only_and_only_its_parts():
    job = scribe_job("chamfer")
    t = job.material.thicknesses_in[0]
    key = job.parts[0].part_key
    only = PROGRAM.split("[outer")[0] + "M12 C8\nG53 Z\nM5\nG53 P10\n"
    ok = check_scribe_program(only.format(z=f"{t - 0.012:.4f}").encode(), job, t, {key: 1})
    assert ok.passed, ok.problems()
    deep = check_scribe_program(only.format(z=f"{t - 0.05:.4f}").encode(), job, t, {key: 1})
    assert not deep.passed
    outline = check_scribe_program(PROGRAM.format(z=f"{t - 0.012:.4f}").encode(), job, t, {key: 1})
    assert any("cuts outlines" in p for p in outline.problems())
    assert any("don't ask for it" in p for p in
               check_scribe_program(only.format(z=f"{t - 0.012:.4f}").encode(), job, t, {"p99": 1}).problems())


def refused(data) -> str:
    with pytest.raises(SchemaError) as e:
        load_job(data)
    return str(e.value)


def test_scribing_only_bent_parts_that_ask_and_jobs_that_can():
    data = copy.deepcopy(JOB)
    data["parts"][0]["scribe"] = "endmill"
    assert "only a bent part" in refused(data)
    data["parts"][0].update(bends=[{"name": "B", "angle_deg": 90.0, "direction": "up"}], scribe="chamfer")
    assert "no scribe settings" in refused(data)
    data["scribe"] = {"depth_in": 0.012, "chamfer_depth_in": 0.012}
    assert "no chamfer mill" in refused(data)
    data["parts"][0]["scribe"] = "endmill"
    data["scribe"]["depth_in"] = 0.05
    assert "scribe.depth_in" in refused(data)
    assert scribe_job().parts[0].scribe == "endmill"
