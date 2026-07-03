"""api.py — FastAPI routes for the intelligence-v2 dashboard."""

import base64
import json
import logging
from datetime import datetime
from typing import Optional

import anthropic
from fastapi import FastAPI, HTTPException, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import storage
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, CATEGORY_LABELS

log = logging.getLogger(__name__)

app = FastAPI(title="Media Plus Intelligence API", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve the dashboard HTML at root
try:
    app.mount("/static", StaticFiles(directory="."), name="static")
except Exception:
    pass


# ── PERIOD HELPERS ────────────────────────────────────────────────────────────

PERIOD_HOURS = {
    "24h":     24,
    "48h":     48,
    "7d":      24 * 7,
    "1m":      24 * 30,
    "6m":      24 * 180,
    "1a":      24 * 365,
    "all":     24 * 365 * 10,
}

def period_to_hours(p: str) -> int:
    return PERIOD_HOURS.get(p.lower(), 24)


# ── ROOT ──────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    try:
        return FileResponse("dashboard.html")
    except Exception:
        return {"status": "Media Plus Intelligence API v2"}


@app.get("/health")
def health():
    s = storage.stats()
    return {"status": "ok", "db": s, "timestamp": datetime.utcnow().isoformat()}


# ── SIGNALS ───────────────────────────────────────────────────────────────────

@app.get("/api/signals")
def get_signals(
    period:   str = Query("24h"),
    category: str = Query(None),
    source:   str = Query(None),
    company:  str = Query(None),
    order_by: str = Query("detected_at"),
    limit:    int = Query(100),
    offset:   int = Query(0),
):
    hours = period_to_hours(period)
    signals = storage.get_signals(
        period_hours=hours,
        source_category=category,
        source_name=source,
        company_name=company,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )
    # Serialize datetimes
    for s in signals:
        for k in ("detected_at", "published_at"):
            if s.get(k) and hasattr(s[k], "isoformat"):
                s[k] = s[k].isoformat()
    return {"signals": signals, "count": len(signals), "period": period}


@app.get("/api/signals/counts")
def signal_counts(period: str = Query("24h")):
    hours = period_to_hours(period)
    rows = storage.get_signal_counts_by_source(period_hours=hours)
    return {"counts": rows, "period": period}


@app.get("/api/signals/categories")
def signal_categories(period: str = Query("24h")):
    """Aggregate counts by category for the sidebar, with has_media_card flag."""
    from config import ADVERTISING_CATEGORIES, CATEGORY_LABELS
    hours = period_to_hours(period)
    rows  = storage.get_signal_counts_by_source(period_hours=hours)

    by_cat: dict = {}
    for r in rows:
        cat   = r["source_category"]
        label = CATEGORY_LABELS.get(cat, cat)
        if label not in by_cat:
            by_cat[label] = {
                "count":         0,
                "sources":       {},
                "category_key":  cat,
                "has_media_card": cat in ADVERTISING_CATEGORIES,
            }
        by_cat[label]["count"] += r["n"]
        by_cat[label]["sources"][r["source_name"]] = r["n"]

    total = sum(v["count"] for v in by_cat.values())
    return {"total": total, "categories": by_cat, "period": period}


# ── TOP MOVERS ────────────────────────────────────────────────────────────────

@app.get("/api/top-movers")
def top_movers(period: str = Query("24h"), limit: int = Query(50)):
    hours = period_to_hours(period)
    movers = storage.get_top_movers(period_hours=hours, limit=limit)
    return {"movers": movers, "period": period, "count": len(movers)}


# ── COMPANY DETAIL ────────────────────────────────────────────────────────────

@app.get("/api/company/{company_name}")
def company_detail(company_name: str, period: str = Query("1a")):
    hours   = period_to_hours(period)
    signals = storage.get_company_signals(company_name, period_hours=hours)

    # Serialize datetimes
    for s in signals:
        for k in ("detected_at", "published_at"):
            if s.get(k) and hasattr(s[k], "isoformat"):
                s[k] = s[k].isoformat()

    # Build spend series (from media_spots if available, otherwise 0)
    by_category: dict = {}
    for s in signals:
        cat = s.get("source_category", "online_news")
        by_category.setdefault(cat, []).append(s)

    return {
        "company_name": company_name,
        "period":       period,
        "total_signals": len(signals),
        "signals":      signals,
        "by_category":  {k: len(v) for k, v in by_category.items()},
    }


# ── SOURCES ───────────────────────────────────────────────────────────────────

@app.get("/api/sources")
def list_sources():
    sources = storage.get_sources()
    for s in sources:
        for k in ("last_crawl",):
            if s.get(k) and hasattr(s[k], "isoformat"):
                s[k] = s[k].isoformat()
    return {"sources": sources}


# ── MEDIA ─────────────────────────────────────────────────────────────────────

@app.get("/api/media")
def list_media():
    from db import get_connection
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM media ORDER BY type, name")
            media = [dict(r) for r in cur.fetchall()]
            for m in media:
                if m.get("created_at"):
                    m["created_at"] = m["created_at"].isoformat()
        return {"media": media}
    finally:
        conn.close()


@app.get("/api/media/{media_name}")
def media_detail(media_name: str, period: str = Query("1a")):
    hours = period_to_hours(period)

    from db import get_connection
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM media WHERE name = %s", (media_name,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, f"Media '{media_name}' not found")
            media_info = dict(row)
            if media_info.get("created_at"):
                media_info["created_at"] = media_info["created_at"].isoformat()
    finally:
        conn.close()

    stats = storage.get_media_stats(media_name, period_hours=hours)

    # Also pull signals mentioning this media source
    signals = storage.get_signals(period_hours=hours, source_name=media_name, limit=50)
    for s in signals:
        for k in ("detected_at", "published_at"):
            if s.get(k) and hasattr(s[k], "isoformat"):
                s[k] = s[k].isoformat()

    return {
        "media":   media_info,
        "stats":   stats,
        "signals": signals,
        "period":  period,
    }


# ── LISTINO ───────────────────────────────────────────────────────────────────

@app.get("/api/media/{media_name}/listino")
def get_listino(media_name: str):
    items = storage.get_media_listino(media_name)
    for item in items:
        if item.get("created_at"):
            item["created_at"] = item["created_at"].isoformat()
    return {"media_name": media_name, "items": items}


class ListinoItem(BaseModel):
    formato:     str
    specs:       str = ""
    durata:      str = ""
    prezzo:      str = ""
    prezzo_unit: str = ""
    note:        str = ""


@app.post("/api/media/{media_name}/listino")
def add_listino(media_name: str, item: ListinoItem):
    new_id = storage.add_listino_item(
        media_name, item.formato, item.specs, item.durata,
        item.prezzo, item.prezzo_unit, item.note,
    )
    return {"ok": True, "id": new_id}


@app.put("/api/media/{media_name}/listino/{item_id}")
def update_listino(media_name: str, item_id: int, item: ListinoItem):
    storage.update_listino_item(
        item_id, item.formato, item.specs, item.durata,
        item.prezzo, item.prezzo_unit, item.note,
    )
    return {"ok": True}


@app.delete("/api/media/{media_name}/listino/{item_id}")
def delete_listino(media_name: str, item_id: int):
    storage.delete_listino_item(item_id)
    return {"ok": True}


# ── UPLOAD ────────────────────────────────────────────────────────────────────

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """
    Accept PDF or image upload, extract text via Claude vision/document API,
    then run signal extraction on the result.
    """
    content_bytes = await file.read()
    filename = file.filename or "upload"
    file_type = file.content_type or "application/octet-stream"

    extracted_text = ""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        if "pdf" in file_type or filename.lower().endswith(".pdf"):
            # Use Claude's document API
            b64 = base64.standard_b64encode(content_bytes).decode()
            msg = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=2000,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "Estrai tutto il testo presente in questo documento. "
                                "Restituisci solo il testo estratto, senza commenti."
                            ),
                        },
                    ],
                }],
            )
            extracted_text = msg.content[0].text

        elif any(file_type.startswith(f"image/{ext}") for ext in ["jpeg", "jpg", "png", "gif", "webp"]):
            # Use Claude vision
            media_map = {
                "image/jpeg": "image/jpeg",
                "image/jpg":  "image/jpeg",
                "image/png":  "image/png",
                "image/gif":  "image/gif",
                "image/webp": "image/webp",
            }
            b64 = base64.standard_b64encode(content_bytes).decode()
            msg = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=2000,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_map.get(file_type, "image/jpeg"),
                                "data": b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "Descrivi tutto il contenuto testuale visibile in questa immagine. "
                                "Includi nomi di aziende, pubblicità, cartelli, testi visibili. "
                                "Restituisci solo il testo estratto."
                            ),
                        },
                    ],
                }],
            )
            extracted_text = msg.content[0].text

        else:
            # Treat as plain text
            extracted_text = content_bytes.decode("utf-8", errors="ignore")

    except Exception as e:
        log.error(f"Upload extraction error: {e}")
        extracted_text = content_bytes.decode("utf-8", errors="ignore")[:5000]

    # Save to DB
    upload_id = storage.save_upload(filename, file_type, extracted_text)

    # Run signal extraction
    from extractor import extract_from_upload
    signals = extract_from_upload(extracted_text, filename)

    saved = 0
    signal_ids = []
    for sig in signals:
        company = sig.get("company_name", "").strip()
        stype   = sig.get("signal_type", "")
        if not company or not stype:
            continue
        sid = storage.save_signal(
            company_name    = company,
            signal_type     = stype,
            description     = sig.get("description", ""),
            confidence      = sig.get("confidence", 0.5),
            source_name     = f"Upload: {filename}",
            source_category = "upload",
            expiry_days     = sig.get("expiry_days", 60),
            sector          = sig.get("sector", ""),
            city            = sig.get("city", ""),
            province        = sig.get("province", "TN"),
            action          = sig.get("action", ""),
            urgency         = sig.get("urgency", "media"),
            ai_strategy     = sig.get("ai_strategy", ""),
            ai_products     = sig.get("ai_products", ""),
        )
        signal_ids.append(sid)
        saved += 1

    storage.mark_upload_processed(upload_id, saved)

    return {
        "ok":          True,
        "upload_id":   upload_id,
        "filename":    filename,
        "signals_found": saved,
        "signal_ids":  signal_ids,
        "extracted_text_preview": extracted_text[:500],
    }


# ── CRAWLER TRIGGER ───────────────────────────────────────────────────────────

@app.post("/api/crawler/run")
def trigger_crawler():
    """Manually trigger a crawler run (async in background)."""
    import threading
    from crawler import run as crawler_run

    def _run():
        try:
            crawler_run()
        except Exception as e:
            log.error(f"Crawler run error: {e}")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return {"ok": True, "message": "Crawler started in background"}


@app.get("/api/crawler/status")
def crawler_status():
    s = storage.stats()
    return {
        "status":   "ok",
        "stats":    s,
        "timestamp": datetime.utcnow().isoformat(),
    }
