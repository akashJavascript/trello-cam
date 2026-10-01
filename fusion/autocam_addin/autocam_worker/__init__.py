"""The Fusion side of the pipeline: job.json in, nested and CAM'd sheets out.

- pipeline.py: the job flow, pure Python (standard library + autocam_core). Tested offline.
- adapter.py: the interface to Fusion the pipeline uses.
- fx_*.py: the Fusion implementation. UNTESTED IN FUSION until docs/manual-tests.md says otherwise;
  docs/fusion-api-status.md tracks each call.
"""
