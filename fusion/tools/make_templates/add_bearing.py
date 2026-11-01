"""Adds the [bearing] op to a template: a copy of its [bore] op with a finishing pass, right after [bore].

Bearing holes (holes.bearing_sizes_in) are press fits: the finishing pass takes the last bit of the wall off in one
light lap, so the size is the cutter's and not the roughing helix's. How tight they come out is tuned without
Fusion: holes.bearing_stock_in in config is the radial stock this op leaves (the automation sets it on each sheet).
Run it on alu_4mm (it refuses a template that already has a [bearing] op), then make the other templates from
alu_4mm as usual (repo root):

    python fusion/tools/make_templates/add_bearing.py fusion/templates/alu_4mm.f3dhsm-template
"""
import sys

FINISH_STEPOVER = "0.005in"        # radial, the finishing lap's cut

path = sys.argv[1]
t = open(path, encoding="utf-8").read()
assert "[bearing]" not in t, "the template has a [bearing] op already"
start = t.index('<template description="[bore] holes"')
end = t.index("</template>", start) + len("</template>")
bore = t[start:end]


def sub(text, old, new, n=1):
    got = text.count(old)
    assert got == n, f"{old[:70]!r}: {got} found, expected {n}"
    return text.replace(old, new)


op = sub(bore, '<template description="[bore] holes"', '<template description="[bearing] holes"')
op = sub(op, '<parameter expression="false" name="doFinishingPasses"/>',
         '<parameter expression="true" name="doFinishingPasses"/>')
op = sub(op, '<parameter expression="tool_diameter * 0.1" name="finishingStepover"/>',
         f'<parameter expression="{FINISH_STEPOVER}" name="finishingStepover"/>')
gap = t[end:t.index("<template ", end)]                  # the text between two ops, kept the same
t = t[:end] + gap + op + t[end:]
assert t.count('<template description="[bearing] holes"') == 1
open(path, "w", encoding="utf-8", newline="").write(t)
print("added [bearing] to", path)
