"""Pure-Python logic shared by the Fusion add-in and the service.

Standard library only: this package is imported inside Fusion's bundled Python.
Keep the syntax Python 3.9-compatible until dump_params reports Fusion's version.
"""

# job.json and result.json carry this. The Fusion worker refuses a job built by a
# different version, so a long-lived Fusion process can't run stale code.
CORE_VERSION = "0.1.0"
