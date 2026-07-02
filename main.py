"""main.py — Entry point for intelligence-v2 FastAPI app."""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB, seed media, start scheduler."""
    log.info("Intelligence-v2 startup...")
    try:
        from db import init_db, seed_media
        init_db()
        seed_media()
        log.info("DB initialised and seeded.")
    except Exception as e:
        log.error(f"DB init error: {e}")

    # Start background scheduler
    try:
        from scheduler import start_scheduler
        start_scheduler()
        log.info("Scheduler started.")
    except Exception as e:
        log.error(f"Scheduler error: {e}")

    yield
    log.info("Intelligence-v2 shutdown.")


# Import the app from api.py and attach lifespan
from api import app
app.router.lifespan_context = lifespan

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
