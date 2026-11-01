"""face_6mm from alu_4mm's [pocket] op: the one [face] op that skims the tops of parts with the Face label.

The same 2D Adaptive as alu_4mm's [pocket] (its linking, ramps and heights are what the guard has passed), with:
- the "6mm 3 flute Alu" endmill (T2) and its preset feeds from the pinned library (both tools are metric flat
  endmills with the same holder, so only their numbers change);
- radial stock to leave -0.45 x the tool diameter: the cutter's edge runs up to 0.45 D past each part's outline, so
  it covers the whole top, inside corners and thin bits included, while its center stays inside the outline;
- optimal load 0.5 x the tool diameter (a light skim; the library's 1.75 mm is for full-depth clearing);
- top and bottom heights from the stock top: the automation sets the bottom to the stock top less the face depth.

Exact text replacements with expected counts, so a changed alu_4mm stops it instead of half-converting. Run it
again whenever alu_4mm changes (repo root):

    python fusion/tools/make_templates/make_face.py fusion/templates/alu_4mm.f3dhsm-template \
        fusion/tools/5940_Tool_Library.tools fusion/templates/face_6mm.f3dhsm-template
"""
import json, sys, zipfile
src, lib, dst = sys.argv[1:4]
t = open(src, encoding="utf-8").read()
GUID = "4147915e-c136-481d-bcaa-213e9c8e6623"
tool = next(x for x in json.loads(zipfile.ZipFile(lib).read("tools.json"))["data"] if x.get("guid") == GUID)
g, pp, (pre,) = tool["geometry"], tool["post-process"], tool["start-values"]["presets"]
assert tool["unit"] == "millimeters" and tool["type"] == "flat end mill" and tool["description"] == "6mm 3 flute Alu"
ALU_GUID, ALU_PRESET = "e5dd75b2-ea23-4767-b10b-756a9444cc2e", "01017861-4063-8139-e570-c28889a60bd2"
f9 = lambda v: f"{v:.9f}".rstrip("0").rstrip(".")
num = lambda v: repr(float(v))

start = t.index('<template description="[pocket] pockets"')
end = t.index("</template>", start) + len("</template>")
head, op, tail = t[:start], t[start:end], t[t.rindex("</template>") + len("</template>"):]
assert head.count("<template ") == 0, "the [pocket] op isn't alu_4mm's first op any more"


def sub(text, old, new, n=1):
    got = text.count(old)
    assert got == n, f"{old[:70]!r}: {got} found, expected {n}"
    return text.replace(old, new)


head = sub(head, "<user-name>alu_4mm</user-name>", "<user-name>face_6mm</user-name>")
op = sub(op, '<template description="[pocket] pockets"', '<template description="[face] tops"')
# the tool, from the library
op = sub(op, f'guid="{ALU_GUID}"', f'guid="{GUID}"')
op = sub(op, ALU_PRESET, pre["guid"], 2)                                  # toolPresetId + the preset's id
op = sub(op, "<description>4mm 0 flute Aluminum</description>", f"<description>{tool['description']}</description>")
op = sub(op, '<nc break-control="0" diameter-offset="1" length-offset="1" live-tool="1" manual-tool-change="0" number="1" turret="0"/>',
         f'<nc break-control="0" diameter-offset="{pp["diameter-offset"]}" length-offset="{pp["length-offset"]}" live-tool="1" '
         f'manual-tool-change="0" number="{pp["number"]}" turret="0"/>')
op = sub(op, '<coolant mode="disabled"/>', f'<coolant mode="{pre["tool-coolant"]}"/>')
op = sub(op, '<material name="carbide"/>', f'<material name="{tool["BMC"]}"/>')
op = sub(op, '<body assembly-gauge-length="53" body-length="24" coolant-support="no" diameter="4" flute-length="12" number-of-flutes="1" '
             'overall-length="24" shaft-diameter="4" shoulder-diameter="4" shoulder-length="21" thread-pitch="0" thread-profile-angle="60"/>',
         f'<body assembly-gauge-length="{f9(g["assemblyGaugeLength"])}" body-length="{f9(g["LB"])}" coolant-support="no" '
         f'diameter="{f9(g["DC"])}" flute-length="{f9(g["LCF"])}" number-of-flutes="{g["NOF"]}" overall-length="{f9(g["OAL"])}" '
         f'shaft-diameter="{f9(g["SFDM"])}" shoulder-diameter="{f9(g["shoulder-diameter"])}" '
         f'shoulder-length="{f9(g["shoulder-length"])}" thread-pitch="0" thread-profile-angle="60"/>')
op = sub(op, 'cutting-feedrate="1524.002816264" entry-feedrate="1524" exit-feedrate="1524" feed-mode="per-minute" plunge-feedrate="508" '
             'ramp-angle="2" ramp-feedrate="1524" ramp-spindle-rpm="18000" retract-feedrate="0" spindle-rpm="18000.026176336" '
             'transition-feedrate="1524.002816264"',
         f'cutting-feedrate="{f9(pre["v_f"])}" entry-feedrate="{f9(pre["v_f_leadIn"])}" exit-feedrate="{f9(pre["v_f_leadOut"])}" '
         f'feed-mode="per-minute" plunge-feedrate="{f9(pre["v_f_plunge"])}" ramp-angle="{f9(pre["ramp-angle"])}" '
         f'ramp-feedrate="{f9(pre["v_f_ramp"])}" ramp-spindle-rpm="{f9(pre["n_ramp"])}" retract-feedrate="0" '
         f'spindle-rpm="{f9(pre["n"])}" transition-feedrate="{f9(pre["v_f_transition"])}"')
for key, old, new in (("tool_coolant", "disabled", pre["tool-coolant"]),
                      ("tool_spindleSpeed", "18000.026176335632", num(pre["n"])),
                      ("tool_feedCutting", "1524.0028162639564", num(pre["v_f"])),
                      ("tool_feedEntry", "1524", num(pre["v_f_leadIn"])),
                      ("tool_feedExit", "1524", num(pre["v_f_leadOut"])),
                      ("tool_feedPlunge", "508", num(pre["v_f_plunge"])),
                      ("tool_feedTransition", "1524.0028162639564", num(pre["v_f_transition"])),
                      ("tool_feedRamp", "1524", num(pre["v_f_ramp"]))):
    op = sub(op, f'<parameter key="{key}" value="{old}"/>', f'<parameter key="{key}" value="{new}"/>')


def param(text, name, old, new):
    return sub(text, f'<parameter expression="{old}" name="{name}"/>', f'<parameter expression="{new}" name="{name}"/>')


# the op's own copies of the tool's values (metric, so feeds in mm/min as they are); the op's coolant stays disabled
for name, old, new in (("tool_number", "1", str(pp["number"])),
                       ("tool_material", "'carbide'", f"'{tool['BMC']}'"),
                       ("tool_description", "'4mm 0 flute Aluminum'", f"'{tool['description']}'"),
                       ("tool_diameter", "4.", num(g["DC"])),
                       ("tool_fluteLength", "12.", num(g["LCF"])),
                       ("tool_bodyLength", "24.", num(g["LB"])),
                       ("tool_overallLength", "24.", num(g["OAL"])),
                       ("tool_shoulderLength", "21.", num(g["shoulder-length"])),
                       ("tool_shoulderDiameter", "4.", num(g["shoulder-diameter"])),
                       ("tool_numberOfFlutes", "1", str(g["NOF"])),
                       ("tool_spindleSpeed", "18000.02617633563204", num(pre["n"])),
                       ("tool_feedCutting", "1524.00281626395645", num(pre["v_f"])),
                       ("tool_feedEntry", "1524.", num(pre["v_f_leadIn"])),
                       ("tool_feedExit", "1524.", num(pre["v_f_leadOut"])),
                       ("tool_feedRamp", "1524.", num(pre["v_f_ramp"])),
                       ("tool_feedPlunge", "508.", num(pre["v_f_plunge"])),
                       # the skim
                       ("stockToLeave", "0.02in", "-tool_diameter * 0.45"),
                       ("optimalLoad", "tool_diameter * 0.4", "tool_diameter * 0.5"),
                       ("bottomHeight_mode", "'from contour'", "'from stock top'"),
                       ("bottomHeight_offset", "0in", "-0.005in")):
    op = param(op, name, old, new)

out = head + op + tail
for leftover in (ALU_GUID, ALU_PRESET, "Aluminum", "1524", "[pocket]", "'from contour'"):
    assert leftover not in out, f"{leftover} still in the file"
assert out.count("<template ") == 1
open(dst, "w", encoding="utf-8", newline="").write(out)
print("wrote", dst)
