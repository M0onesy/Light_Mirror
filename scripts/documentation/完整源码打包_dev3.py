"""Compatibility entry; reproducible implementation is included in project scripts."""
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))
from scripts.create_full_source import *  # Re-export inventory helpers for owner audit.

if __name__ == "__main__":
    main()
