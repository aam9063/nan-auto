"""Deployment launcher: gateway + decision log + dashboard, for containers.

Unlike `dev_server.py`, this binds `0.0.0.0` and takes the port from `PORT`
(the container-port contract of NaN Cloud Spaces, default 3000), wires the
SQLite decision log so the live feed works, and serves the built dashboard
from `web/dist` when the image carries one.
"""

import asyncio
import os

import uvicorn

from nan_auto.gateway.app import create_app
from nan_auto.gateway.log_store import DecisionLogStore


async def main() -> None:
    store = DecisionLogStore(os.environ.get("DECISION_DB_PATH", "dev_decisions.sqlite"))
    await store.open()
    app = create_app(store=store)
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "3000")),
        log_level="info",
    )
    await uvicorn.Server(config).serve()


if __name__ == "__main__":
    asyncio.run(main())
