"""Pure-Python logic shared by the Fusion add-in and the service.

Standard library only: this package is imported inside Fusion's bundled Python.
Fusion 2705.1.15 bundles Python 3.14 (api_probe, 2026-10-01); CI tests this package on 3.14.
"""

# job.json and result.json carry this. The Fusion worker refuses a job built by a
# different version, so a long-lived Fusion process can't run stale code.
CORE_VERSION = "0.22.0"  # 0.22.0: rooms on an offcut from where the program really cuts (cutmap.py), not the parts' boxes; 0.21.0: the stop between parts is a bare G4 that waits for ENTER (WinCNC has no M0); 0.20.6: rooms on an offcut are every empty rectangle among the parts (offcuts.free_rooms), not just the room to their right; 0.20.5: small curvy cutouts cut free beside wide tabs (TabSpec.free_max_in); 0.19.0: small features split off into a small-tool program (ToolingSpec.split, kind "small"); 0.18.0: scribed bend lines (PartSpec.scribe, ScribeSpec, [scribe] op, SCRIBE program); 0.17.0: bent parts (PartSpec.bends, the flat pattern cut); 0.16.0: fit series (PartSpec.fit, FIT lap programs); 0.15.0: [bearing] op, holes.bearing_stock_in; 0.14.0: faced parts (a FACE program per sheet); 0.13.0: pockets ([pocket], [pocketfinish]); 0.12.1: labels in the middle of thin parts; 0.12.0: parts placed by hand; 0.11.0: tab width/height
