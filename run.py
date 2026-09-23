#!/usr/bin/env python3
"""Run the reproducible demonstration from a source checkout."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from d2sflow.pilot import main

if __name__ == "__main__":
    main()
