# CAM templates

Export each template from Fusion's template library and save it here with the file name `config/autocam.toml`
expects:

| File | Material family | Tool (by GUID, see config) |
|---|---|---|
| `alu_4mm.f3dhsm-template` | aluminum (6061, 5052) | `4mm 0 flute Aluminum` |
| `alu_eighth.f3dhsm-template` | aluminum | `1/8 aluminum` (T12) |
| `poly_4mm.f3dhsm-template` | polycarbonate | `4mm 0 flute Poly` |
| `poly_eighth.f3dhsm-template` | polycarbonate | `1/8 polycarbonate` (T12) |

Build every template from the pinned library (`fusion/tools/5940_Tool_Library.tools`). Tool GUIDs change when a
library is re-saved or copied, and the automation rejects any op whose tool GUID isn't the one in config. After
building the templates, run `dump_params` on a part with each template applied and check that the GUIDs match.

## Rules for every template (decisions 7, 12, 22)

- **Op names:**
  - Tag operations by name: `[drill]`, `[bore]`, `[bearing]`, `[pocket]`, `[inner]`, `[outer]`, in that order.
  - Names may contain only letters, digits, spaces, `-` and `_` besides the tag.
- **One tool:**
  - Every op uses the template's single tool. The automation checks the tool GUID.
  - The post can't tell the two T1 4 mm tools apart.
- **Heights:**
  - Every bottom height is **stock bottom with offset 0**. Never negative, never "drill tip through bottom".
  - The automation rejects any program that goes below Z0.
  - Clearance height must be at least **2.0 in above stock top**. The post does not retract with `G53`
    between ops, so clearance height is the travel height over the clamps.
- **Drilling:** use plain drilling (`G81`). Peck cycles (`G73`/`G83`) are rejected until proven on WinCNC.
  Keep the drill retract height above the stock top.
- **Linking:** use full retraction, not minimum retraction or "stay down". The guard rejects any rapid that moves
  sideways below the stock top. Vertical rapids back into a pocket are fine.
- **Compensation:** keep it "in computer". The post's in-control compensation (`G41`/`G42`) is rejected.
