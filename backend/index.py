"""Vercel entrypoint: expose the FastAPI ASGI app.

The Vercel project's Root Directory is ``backend`` so this file, ``app/`` and the
hash-verified ``data/`` snapshots deploy together. vercel.json pins this file as
the only function and routes every path to it; there is deliberately no public/
directory, because statically served files would bypass the access gate.
"""

from app.main import app

__all__ = ["app"]
