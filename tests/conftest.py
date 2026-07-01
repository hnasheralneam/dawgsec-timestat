import os
import sys

# Ensure the repository root (parent of this tests/ directory) is on
# sys.path so `import app`, `import config`, `import db`, etc. resolve
# regardless of how pytest is invoked (`pytest`, `python -m pytest`, or
# from a different working directory). Without this, plain `pytest`
# invocations fail with ModuleNotFoundError because pytest's default
# import mode only adds the test file's own directory (tests/) to
# sys.path, not the project root above it.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
