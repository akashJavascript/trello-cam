# The 3D print queue (2026-11-09)

The shop page's **Prints** tab (`/shop#prints`), next to Shafts, Parts and Plates. The mockup the user approved:
https://claude.ai/artifact/GS5EDfgP53ogQzmSjSxvqf. No slicing and nothing is sent to the printers (the user: maybe
later). Code: `service/autocam_service/prints.py`, state in `state/prints.json` and `state/print_files/`.

## Getting prints on the queue

- **One part, from the Onshape panel.** A part whose Onshape material is one of `[prints] materials` (PLA, PETG,
  TPU 95A, PLA-CF, PA-CF, ASA) opens a print form instead of the plate form:
  - **Material**: starts at Onshape's, and can be changed even when Onshape says otherwise. Nothing is written back.
  - **Quantity**: starts at the assembly's count.
  - **Color**: `[prints] colors` (starting with Any), or **Other…** to type one in.
  - **Infill %** and **Walls**: optional.
  - **Rush**, **Note** and **Assign**.
  - **Send** puts it on the queue.
  - Once it's on the queue, the panel says so, and a waiting print can be taken off. The same part can't go on
    twice while it's waiting or printing.
- **With an assembly.** The Assembly tab lists printed parts under **Prints**, unticked to start. Ticked ones go
  out with Send, with the assembly's counts and Rush.
  - Printed means a print material; for the common parts library's parts, also a name like "3d printed standoff".
  - A part already waiting or printing (same document, part and configuration) is refused, not sent twice.
- **A file, on the shop page.** **Add a print** takes a STEP, STL or 3MF (up to `[prints] max_file_mb`), a name
  (from the file), quantity, material, infill, walls, color, Rush and a note.

## On the Prints tab

- **Sections:**
  - **Yours**: ones you started or were assigned;
  - **Rush**;
  - **Waiting**;
  - **Printing**;
  - **Done today**, with Undo on the phone that finished it.
- **Who does it:** anyone can take a print or assign it to anyone (the names from the Slack sign-in, like
  shafts).
- **Start printing:** pick a printer (`[prints] printers`) and type the print time in hours and minutes. Both are
  optional. The card then shows "Ivy · done ~4:40 PM", worked out from the time it started.
- **On a printing card:**
  - **Failed** sends it back to Waiting, marked "Reprint · failed 1×" (the count goes up each time).
  - **Done** finishes it.
  - There are no check-ins (the user).
- **Download** gives the model file:
  - an upload comes back as it was added;
  - a part from Onshape is exported as a **STEP** the first time someone downloads it, then kept. Onshape's
    translation takes a few calls and a few seconds; if it isn't ready yet, the page says to try again.
  - Prints nobody downloads cost no Onshape calls.
- **Clean-up:** done prints, and their files, are dropped after `[prints] keep_days` (14).

## Config

```toml
[prints]
enabled = true
printers = ["Bartholomew", "Bilbo Baggins", "Darcy", "Ivy", "Oven", "Tandoori"]
materials = ["PLA", "PETG", "TPU 95A", "PLA-CF", "PA-CF", "ASA"]
colors = ["Any", "Black", "Orange", "White", "Gray"]
keep_days = 14
max_file_mb = 50
```

Onshape's material name picks the longest of these it contains, ignoring case ("3D Printed PLA" is PLA). If
none match, its first word is tried ("TPU" is TPU 95A), and after that the first material on the list.
