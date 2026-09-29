"""Vercel entrypoint: expose the FastAPI ASGI app.

The Vercel project's Root Directory is ``app`` so this file, the ``app/``
package, the hash-verified ``data/`` snapshots and ``../frontend`` deploy
together. vercel.json pins this file as the only function and routes every path
to it; there is deliberately no public/ directory, because statically served
files would bypass the host and origin checks.
"""

import sys
from pathlib import Path

# The runtime imports this file as ``backend.index``; make the ``app`` package importable.
_BACKEND_DIR = str(Path(__file__).resolve().parent)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from app.main import app  # noqa: E402

__all__ = ["app"]
