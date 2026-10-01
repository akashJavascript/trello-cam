import sys
from pathlib import Path

# The plate builder lives with the core tests; the pipeline tests use it too.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
