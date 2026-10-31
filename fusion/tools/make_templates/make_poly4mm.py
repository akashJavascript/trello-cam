"""poly_4mm from alu_4mm: the same ops and settings, the "4mm 0 flute Poly" tool and its preset feeds.

Exact text replacements with expected counts, so nothing else in the file can change (both tools are in mm and
differ only in flute length, coolant and feeds). Run it again whenever alu_4mm changes (repo root):

    python fusion/tools/make_templates/make_poly4mm.py fusion/templates/alu_4mm.f3dhsm-template \
        fusion/templates/poly_4mm.f3dhsm-template
"""
import sys
src, dst = sys.argv[1], sys.argv[2]
t = open(src, encoding="utf-8").read()
ALU_GUID, POLY_GUID = "e5dd75b2-ea23-4767-b10b-756a9444cc2e", "b4521723-7c35-4121-b376-50f3db7bc3a0"
ALU_PRESET, POLY_PRESET = "01017861-4063-8139-e570-c28889a60bd2", "abdbd17c-4b5c-7809-e16b-ca9fcabc6c5e"
swaps = [
    ("<user-name>alu_4mm</user-name>", "<user-name>poly_4mm</user-name>", 1),
    (f'guid="{ALU_GUID}"', f'guid="{POLY_GUID}"', 6),                       # each op's tool
    (ALU_PRESET, POLY_PRESET, 12),                                            # toolPresetId + preset id
    ("<description>4mm 0 flute Aluminum</description>", "<description>4mm 0 flute Poly</description>", 6),
    ('<coolant mode="disabled"/>', '<coolant mode="air"/>', 6),             # the tool's own default (library)
    ('flute-length="12"', 'flute-length="18"', 6),
    ('cutting-feedrate="1524.002816264" entry-feedrate="1524" exit-feedrate="1524"',
     'cutting-feedrate="2540.001693769" entry-feedrate="2540" exit-feedrate="2540"', 5),
    ('ramp-feedrate="1524"', 'ramp-feedrate="2540"', 5),
    ('transition-feedrate="1524.002816264"', 'transition-feedrate="2540.001693769"', 5),
    ('<parameter key="tool_coolant" value="disabled"/>', '<parameter key="tool_coolant" value="air"/>', 6),
    ('<parameter key="tool_feedCutting" value="1524.0028162639564"/>', '<parameter key="tool_feedCutting" value="2540.0016937688974"/>', 6),
    ('<parameter key="tool_feedEntry" value="1524"/>', '<parameter key="tool_feedEntry" value="2540"/>', 6),
    ('<parameter key="tool_feedExit" value="1524"/>', '<parameter key="tool_feedExit" value="2540"/>', 6),
    ('<parameter key="tool_feedRamp" value="1524"/>', '<parameter key="tool_feedRamp" value="2540"/>', 6),
    ('<parameter key="tool_feedTransition" value="1524.0028162639564"/>', '<parameter key="tool_feedTransition" value="2540.0016937688974"/>', 6),
    # the ops' own copies of the tool's values (the op's coolant stays 'disabled': mist comes from the material)
    ("<parameter expression=\"'4mm 0 flute Aluminum'\" name=\"tool_description\"/>", "<parameter expression=\"'4mm 0 flute Poly'\" name=\"tool_description\"/>", 6),
    ('<parameter expression="12." name="tool_fluteLength"/>', '<parameter expression="18." name="tool_fluteLength"/>', 6),
    ('<parameter expression="1524.00281626395645" name="tool_feedCutting"/>', '<parameter expression="2540.0016937688974" name="tool_feedCutting"/>', 5),
    ('<parameter expression="1524." name="tool_feedEntry"/>', '<parameter expression="2540." name="tool_feedEntry"/>', 5),
    ('<parameter expression="1524." name="tool_feedExit"/>', '<parameter expression="2540." name="tool_feedExit"/>', 5),
    ('<parameter expression="1524." name="tool_feedRamp"/>', '<parameter expression="2540." name="tool_feedRamp"/>', 5),
]
for old, new, n in swaps:
    got = t.count(old)
    assert got == n, f"{old[:70]!r}: {got} found, expected {n}"
    t = t.replace(old, new)
for leftover in (ALU_GUID, ALU_PRESET, "Aluminum", "1524"):
    assert leftover not in t, f"{leftover} still in the file"
open(dst, "w", encoding="utf-8", newline="").write(t)
print("wrote", dst)
