"""Source-root package shim.

The implementation package lives in ``mingjing/mingjing`` so the repository
can also contain top-level delivery assets.  Extending the package path keeps
``python -m mingjing.cli`` and ``python -m mingjing.bench.evaluate`` working
from either ``mainProj`` or ``mainProj/mingjing``.
"""

from pathlib import Path

__path__.append(str(Path(__file__).resolve().parent / "mingjing"))

