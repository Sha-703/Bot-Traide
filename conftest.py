"""Configuration pytest : garantit que le paquet `bot` est importable."""

from __future__ import annotations

import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))
