import os
import sys
import tempfile
from pathlib import Path

# Isolate test data before any app module reads config
_tmp = tempfile.mkdtemp(prefix="msa_test_")
os.environ.setdefault("MSA_DATA_DIR", _tmp)

sys.path.insert(0, str(Path(__file__).parent))
