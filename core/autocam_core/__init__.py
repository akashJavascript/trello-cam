"""Pure-Python logic shared by the Fusion add-in and the service.

Standard library only: this package is imported inside Fusion's bundled Python.
Fusion 2705.1.15 bundles Python 3.14 (api_probe, 2026-10-01); CI tests this package on 3.14.
"""

# job.json and result.json carry this. The Fusion worker refuses a job built by a
# different version, so a long-lived Fusion process can't run stale code.
CORE_VERSION = "0.3.1"   # 0.3.1: offcuts say which way round they were last cut
