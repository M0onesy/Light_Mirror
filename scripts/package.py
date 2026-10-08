from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from mingjing.import_integrity import assert_application_origin
    assert_application_origin(root)
    from scripts.package_delivery import main as package_main
    return package_main()


if __name__ == "__main__":
    raise SystemExit(main())

