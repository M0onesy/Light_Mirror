"""Frozen application entry point for MingJing."""

from pathlib import Path
import sys

from mingjing.import_integrity import assert_application_origin

assert_application_origin(Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)))

from mingjing.launcher import main


if __name__ == "__main__":
    raise SystemExit(main())
