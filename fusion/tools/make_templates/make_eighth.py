"""alu_eighth / poly_eighth from alu_4mm: the same ops and settings, a 1/8 in tool from the pinned library.

The 1/8 tools are in inches, so every tool value is rebuilt from the library; each swap's count is checked, so
a changed alu_4mm stops it instead of half-converting. Run it again whenever alu_4mm changes (repo root):

    python fusion/tools/make_templates/make_eighth.py fusion/templates/alu_4mm.f3dhsm-template \
        fusion/tools/5940_Tool_Library.tools "1/8 aluminum" alu_eighth fusion/templates/alu_eighth.f3dhsm-template
    (and "1/8 polycarbonate" poly_eighth ... poly_eighth.f3dhsm-template)
"""
import json, re, sys, zipfile
src, lib, desc, name, dst = sys.argv[1:6]
t = open(src, encoding="utf-8").read()
N = t.count('<template description=')       # ops (6 before [bearing], 2026-11-01)
tool = next(x for x in json.loads(zipfile.ZipFile(lib).read("tools.json"))["data"] if x.get("description") == desc)
g, pp, (pre,) = tool["geometry"], tool["post-process"], tool["start-values"]["presets"]
assert tool["unit"] == "inches" and tool["type"] == "flat end mill"
ALU_GUID, ALU_PRESET = "e5dd75b2-ea23-4767-b10b-756a9444cc2e", "01017861-4063-8139-e570-c28889a60bd2"
MM = 25.4
f9 = lambda v: f"{v:.9f}".rstrip("0").rstrip(".")
num = lambda v: repr(float(v))

def sub(old, new, n, text=None, regex=False):
    global t
    body = t if text is None else text
    got = len(re.findall(old, body)) if regex else body.count(old)
    assert got == n, f"{old[:70]!r}: {got} found, expected {n}"
    body = re.sub(old, new, body) if regex else body.replace(old, new)
    if text is None:
        t = body
    return body

sub("<user-name>alu_4mm</user-name>", f"<user-name>{name}</user-name>", 1)
sub(f'guid="{ALU_GUID}" tool-library-version="37" type="flat end mill" unit="millimeters"',
    f'guid="{tool["guid"]}" tool-library-version="37" type="flat end mill" unit="inches"', N)
sub(ALU_PRESET, pre["guid"], 2 * N)
sub("<description>4mm 0 flute Aluminum</description>", f"<description>{desc}</description>", N)
sub('<nc break-control="0" diameter-offset="1" length-offset="1" live-tool="1" manual-tool-change="0" number="1" turret="0"/>',
    f'<nc break-control="0" diameter-offset="{pp["diameter-offset"]}" length-offset="{pp["length-offset"]}" live-tool="1" '
    f'manual-tool-change="0" number="{pp["number"]}" turret="0"/>', N)
sub('<coolant mode="disabled"/>', f'<coolant mode="{pre["tool-coolant"]}"/>', N)
sub('<body assembly-gauge-length="53" body-length="24" coolant-support="no" diameter="4" flute-length="12" number-of-flutes="1" '
    'overall-length="24" shaft-diameter="4" shoulder-diameter="4" shoulder-length="21" thread-pitch="0" thread-profile-angle="60"/>',
    f'<body assembly-gauge-length="{f9(g["assemblyGaugeLength"])}" body-length="{f9(g["LB"])}" coolant-support="no" '
    f'diameter="{f9(g["DC"])}" flute-length="{f9(g["LCF"])}" number-of-flutes="{g["NOF"]}" overall-length="{f9(g["OAL"])}" '
    f'shaft-diameter="{f9(g["SFDM"])}" shoulder-diameter="{f9(g["shoulder-diameter"])}" '
    f'shoulder-length="{f9(g["shoulder-length"])}" thread-pitch="0" thread-profile-angle="60"/>', N)
# no holder: the library gives the 1/8 tools none, and its assembly gauge length (= the body length) only adds
# up without one
blocks = re.findall(r'\n[ \t]*<holder description="Default Holder" gauge-length="29">.*?</holder>', t, flags=re.S)
assert len(blocks) == N, len(blocks)
for b in blocks:
    t = t.replace(b, "", 1)
# motion: five ops carry the preset's feeds, the drill op zeros
sub('cutting-feedrate="1524.002816264" entry-feedrate="1524" exit-feedrate="1524" feed-mode="per-minute" plunge-feedrate="508" '
    'ramp-angle="2" ramp-feedrate="1524" ramp-spindle-rpm="18000" retract-feedrate="0" spindle-rpm="18000.026176336" '
    'transition-feedrate="1524.002816264"',
    f'cutting-feedrate="{f9(pre["v_f"])}" entry-feedrate="{f9(pre["v_f_leadIn"])}" exit-feedrate="{f9(pre["v_f_leadOut"])}" '
    f'feed-mode="per-minute" plunge-feedrate="{f9(pre["v_f_plunge"])}" ramp-angle="{f9(pre["ramp-angle"])}" '
    f'ramp-feedrate="{f9(pre["v_f_ramp"])}" ramp-spindle-rpm="{f9(pre["n_ramp"])}" retract-feedrate="0" '
    f'spindle-rpm="{f9(pre["n"])}" transition-feedrate="{f9(pre["v_f_transition"])}"', N - 1)
sub('plunge-feedrate="508" ramp-angle="2" ramp-feedrate="0" ramp-spindle-rpm="0" retract-feedrate="0" spindle-rpm="18000.026176336"',
    f'plunge-feedrate="{f9(pre["v_f_plunge"])}" ramp-angle="2" ramp-feedrate="0" ramp-spindle-rpm="0" retract-feedrate="0" '
    f'spindle-rpm="{f9(pre["n"])}"', 1)
for key, old, new in (("tool_coolant", "disabled", pre["tool-coolant"]),
                      ("tool_spindleSpeed", "18000.026176335632", num(pre["n"])),
                      ("tool_feedCutting", "1524.0028162639564", num(pre["v_f"])),
                      ("tool_feedEntry", "1524", num(pre["v_f_leadIn"])),
                      ("tool_feedExit", "1524", num(pre["v_f_leadOut"])),
                      ("tool_feedPlunge", "508", num(pre["v_f_plunge"])),
                      ("tool_feedTransition", "1524.0028162639564", num(pre["v_f_transition"])),
                      ("tool_feedRamp", "1524", num(pre["v_f_ramp"]))):
    sub(f'<parameter key="{key}" value="{old}"/>', f'<parameter key="{key}" value="{new}"/>', N)
# the ops' own copies of the tool's values, in the tool's unit (inches now); the ops' coolant stays disabled
for name_, old, new, n in (("tool_unit", "'millimeters'", "'inches'", N),
                           ("tool_description", "'4mm 0 flute Aluminum'", f"'{desc}'", N),
                           ("tool_diameter", "4.", num(g["DC"]), N),
                           ("tool_fluteLength", "12.", num(g["LCF"]), N),
                           ("tool_bodyLength", "24.", num(g["LB"]), N),
                           ("tool_overallLength", "24.", num(g["OAL"]), N),
                           ("tool_shoulderLength", "21.", num(g["shoulder-length"]), N),
                           ("tool_shoulderDiameter", "4.", num(g["shoulder-diameter"]), N),
                           ("tool_assemblyGaugeLength", "53.", num(g["assemblyGaugeLength"]), N),
                           ("tool_holderGaugeLength", "29.", "0.", N),
                           ("tool_number", "1", str(pp["number"]), N),
                           ("tool_spindleSpeed", "18000.02617633563204", num(pre["n"]), N),
                           # an op's feeds read in mm/min whatever the tool's unit (r026: 96 came out 3.78 in/min)
                           ("tool_feedCutting", "1524.00281626395645", num(pre["v_f"] * MM), N - 1),
                           ("tool_feedEntry", "1524.", num(pre["v_f_leadIn"] * MM), N - 1),
                           ("tool_feedExit", "1524.", num(pre["v_f_leadOut"] * MM), N - 1),
                           ("tool_feedRamp", "1524.", num(pre["v_f_ramp"] * MM), N - 1),
                           ("tool_feedPlunge", "508.", num(pre["v_f_plunge"] * MM), N)):
    # never the library's product fields (vendor, product id, link): in the ops they made Fusion fail an outline,
    # "Failed to write Job File" (r071, r072, r074, 2026-11-03)
    sub(f'<parameter expression="{old}" name="{name_}"/>', f'<parameter expression="{new}" name="{name_}"/>', n)
for leftover in (ALU_GUID, ALU_PRESET, "Aluminum", "1524.0028", "\"508", "508.\"", "unit=\"millimeters\"", "\x27millimeters\x27\" name=\"tool_unit"):
    assert leftover not in t, f"{leftover} still in the file"
open(dst, "w", encoding="utf-8", newline="").write(t)
print("wrote", dst)
