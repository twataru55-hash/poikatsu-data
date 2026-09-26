import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("POIKATSU_NOW", "2026-10-05T12:00:00+09:00")
