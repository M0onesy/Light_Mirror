"""MingJing deterministic financial fact-checking kernel."""

from pathlib import Path

__version__ = "1.0.0.dev2"

# When executed from ``mainProj/mingjing`` the delivery modules (bench, docs,
# etc.) sit beside this implementation package.  Expose that directory as a
# namespace path without importing any service-layer code into the kernel.
__path__.append(str(Path(__file__).resolve().parents[1]))
