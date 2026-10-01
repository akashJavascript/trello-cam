# Manual tests

Everything Fusion-side or machine-side that can't run in CI. Fusion code counts as **untested** until a
section here has been run and its results recorded (date, who, Fusion version, outcome).
Record per-call API results in `docs/fusion-api-status.md`.

## M1.0: parameter dump, API probe, Phase 1 smoke test

_To be written with `fusion/tools/dump_params/` and `fusion/tools/api_probe/` (M1.0)._

## M1.2 to M1.7: restructured pipeline in Fusion

_Added per sub-milestone._

## M1.6: pause air tests (decision 19). Required before any real cut.

Run on the machine with no material, work zero set as usual. Use `fusion/tests/pause_air_test.tap`
(no mist) and the generated `pause_air_test_mist.tap` (M1.6).

- [ ] The machine stops at every pause, and nothing moves until start/continue is pressed.
- [ ] The spindle actually stops at each pause and restarts after resume (4 s spin-up before moving).
- [ ] Mist turns off at each pause and back on after resume (mist file only).
- [ ] The first move after resume comes down from the top, not sideways at cutting depth.
- [ ] Record the key/button that resumes (open question 9): ______  (never Esc: it aborts the job)

## Before the first real cut

- [ ] Open question 4: Z is touched off on the spoilboard next to the sheet (decision 22).
- [ ] Open question 10: no-tab trial on one taped sheet (decision 6).
- [ ] Both air tests above passed.

## M2: add-in hot-folder worker

_Added in M2._

## M5: reboot test on the shop PC

_Added in M5._
