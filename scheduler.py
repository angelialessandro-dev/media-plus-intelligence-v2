"""scheduler.py — Runs the crawler periodically in the background."""

import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from config import CRAWL_INTERVAL_MINUTES

log = logging.getLogger(__name__)
_scheduler = None


def _crawl_job():
    try:
        from crawler import run
        log.info("Scheduled crawler run starting...")
        run()
    except Exception as e:
        log.error(f"Scheduled crawler error: {e}")


def start_scheduler():
    global _scheduler
    if _scheduler is not None:
        return

    _scheduler = BackgroundScheduler(timezone="Europe/Rome")
    _scheduler.add_job(
        _crawl_job,
        trigger=IntervalTrigger(minutes=CRAWL_INTERVAL_MINUTES),
        id="crawler",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.start()
    log.info(f"Scheduler running — interval: {CRAWL_INTERVAL_MINUTES} min")

    # Run once immediately at startup
    import threading
    t = threading.Thread(target=_crawl_job, daemon=True)
    t.start()


def stop_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
