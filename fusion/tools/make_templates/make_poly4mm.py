"""poly_4mm from alu_4mm: the same ops and settings, the "4mm 0 flute Poly" tool and its preset feeds.

Exact text replacements with expected counts, so nothing else in the file can change (both tools are in mm and
differ only in coolant, feeds and the product: the team's 4 mm cutters are WCP-1513, 12 mm cut length, the user
2026-11-03; the library's 18 mm was a guess). Run it again whenever alu_4mm changes (repo root):

    python fusion/tools/make_templates/make_poly4mm.py fusion/templates/alu_4mm.f3dhsm-template \
        fusion/templates/poly_4mm.f3dhsm-template
"""
import sys
src, dst = sys.argv[1], sys.argv[2]
t = open(src, encoding="utf-8").read()
N = t.count('<template description=')       # ops (6 before [bearing], 2026-11-01)
ALU_GUID, POLY_GUID = "e5dd75b2-ea23-4767-b10b-756a9444cc2e", "b4521723-7c35-4121-b376-50f3db7bc3a0"
ALU_PRESET, POLY_PRESET = "01017861-4063-8139-e570-c28889a60bd2", "abdbd17c-4b5c-7809-e16b-ca9fcabc6c5e"
swaps = [
    ("<user-name>alu_4mm</user-name>", "<user-name>poly_4mm</user-name>", 1),
    (f'guid="{ALU_GUID}"', f'guid="{POLY_GUID}"', N),                       # each op's tool
    (ALU_PRESET, POLY_PRESET, 2 * N),                                            # toolPresetId + preset id
    ("<description>4mm 0 flute Aluminum</description>", "<description>4mm 0 flute Poly</description>", N),
    ('<coolant mode="disabled"/>', '<coolant mode="air"/>', N),             # the tool's own default (library)
    ('cutting-feedrate="1524.002816264" entry-feedrate="1524" exit-feedrate="1524"',
     'cutting-feedrate="2540.001693769" entry-feedrate="2540" exit-feedrate="2540"', N - 1),
    ('ramp-feedrate="1524"', 'ramp-feedrate="2540"', N - 1),
    ('transition-feedrate="1524.002816264"', 'transition-feedrate="2540.001693769"', N - 1),
    ('<parameter key="tool_coolant" value="disabled"/>', '<parameter key="tool_coolant" value="air"/>', N),
    ('<parameter key="tool_feedCutting" value="1524.0028162639564"/>', '<parameter key="tool_feedCutting" value="2540.0016937688974"/>', N),
    ('<parameter key="tool_feedEntry" value="1524"/>', '<parameter key="tool_feedEntry" value="2540"/>', N),
    ('<parameter key="tool_feedExit" value="1524"/>', '<parameter key="tool_feedExit" value="2540"/>', N),
    ('<parameter key="tool_feedRamp" value="1524"/>', '<parameter key="tool_feedRamp" value="2540"/>', N),
    ('<parameter key="tool_feedTransition" value="1524.0028162639564"/>', '<parameter key="tool_feedTransition" value="2540.0016937688974"/>', N),
    # the ops' own copies of the tool's values (the op's coolant stays 'disabled': mist comes from the material)
    ("<parameter expression=\"'4mm 0 flute Aluminum'\" name=\"tool_description\"/>", "<parameter expression=\"'4mm 0 flute Poly'\" name=\"tool_description\"/>", N),
    ("<parameter expression=\"''\" name=\"tool_vendor\"/>", "<parameter expression=\"'WCP'\" name=\"tool_vendor\"/>", N),
    ("<parameter expression=\"''\" name=\"tool_productId\"/>", "<parameter expression=\"'WCP-1513'\" name=\"tool_productId\"/>", N),
    ("<parameter expression=\"''\" name=\"tool_productLink\"/>",
     "<parameter expression=\"'https://wcproducts.com/products/wcp-1513'\" name=\"tool_productLink\"/>", N),
    ('<parameter expression="1524.00281626395645" name="tool_feedCutting"/>', '<parameter expression="2540.0016937688974" name="tool_feedCutting"/>', N - 1),
    ('<parameter expression="1524." name="tool_feedEntry"/>', '<parameter expression="2540." name="tool_feedEntry"/>', N - 1),
    ('<parameter expression="1524." name="tool_feedExit"/>', '<parameter expression="2540." name="tool_feedExit"/>', N - 1),
    ('<parameter expression="1524." name="tool_feedRamp"/>', '<parameter expression="2540." name="tool_feedRamp"/>', N - 1),
]
for old, new, n in swaps:
    got = t.count(old)
    assert got == n, f"{old[:70]!r}: {got} found, expected {n}"
    t = t.replace(old, new)
for leftover in (ALU_GUID, ALU_PRESET, "Aluminum", "1524"):
    assert leftover not in t, f"{leftover} still in the file"
open(dst, "w", encoding="utf-8", newline="").write(t)
print("wrote", dst)
