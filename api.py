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


# ── COMPANIES ─────────────────────────────────────────────────────────────────

@app.post("/api/companies/backfill")
def backfill_companies():
    """Process all existing signals through the entity resolver."""
    from entity_resolver import resolve_company
    from db import get_connection

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, company_name, sector, city, province
                FROM signals
                WHERE company_id IS NULL
                ORDER BY detected_at
            """)
            signals = [dict(r) for r in cur.fetchall()]

        processed = 0
        for s in signals:
            company_id, canonical = resolve_company(
                name=s["company_name"],
                sector=s.get("sector", ""),
                city=s.get("city", ""),
                province=s.get("province", ""),
                signal_id=s["id"],
                conn=conn,
            )
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE signals SET company_id = %s, company_canonical = %s
                    WHERE id = %s
                """, (company_id, canonical, s["id"]))
            conn.commit()
            processed += 1

        return {"ok": True, "processed": processed}
    finally:
        conn.close()


@app.get("/api/companies")
def list_companies(limit: int = Query(200)):
    companies = storage.get_companies(limit=limit)
    return {"companies": companies, "count": len(companies)}


@app.get("/api/companies/review")
def get_review_queue():
    reviews = storage.get_pending_reviews()
    return {"reviews": reviews, "count": len(reviews)}


class ReviewAction(BaseModel):
    accept: bool


@app.post("/api/companies/review/{review_id}")
def handle_review(review_id: int, action: ReviewAction):
    storage.confirm_merge(review_id, action.accept)
    return {"ok": True, "review_id": review_id, "accepted": action.accept}


class MergeRequest(BaseModel):
    target_id: int


@app.post("/api/companies/{company_id}/merge")
def merge_companies(company_id: int, req: MergeRequest):
    """Merge company_id into target_id (company_id disappears)."""
    storage.merge_companies(source_id=company_id, target_id=req.target_id)
    return {"ok": True, "merged": company_id, "into": req.target_id}


# ── CARICAMENTI MANUALI ───────────────────────────────────────────────────────

MANUAL_ANALYZE_PROMPT = """Sei un analista commerciale esperto del mercato trentino e altoatesino.
Analizza l'input fornito dall'utente (testo, descrizione di un'immagine, o contenuto di una URL).
Rispondi SOLO con JSON valido, senza testo aggiuntivo.

INPUT: {input_text}
TIPO INPUT: {input_type}

Estrai le seguenti informazioni e restituisci:
{{
  "company_name": "nome esatto dell'azienda",
  "signal_type": "spot_tv|spot_radio|tabellone|inserzione_giornale|sponsorizzazione|evento|investimento|nuova_apertura|campagna_marketing|altro",
  "signal_nature": "advertising|informative",
  "title": "titolo breve del segnale (max 8 parole)",
  "description": "descrizione del segnale commerciale (2-3 frasi)",
  "urgency": "alta|media|bassa",
  "sector": "settore tra: Automotive, Turismo, Casa e Edilizia, Ristorazione, Retail, Tecnologia, Salute e Benessere, Agricoltura, Moda e Abbigliamento, Sport e Outdoor, Finanza e Assicurazioni, Logistica e Trasporti, Artigianato, Istruzione e Formazione, Energia, Industria Manifatturiera, Immobiliare, Comunicazione e Media, Alimentare, Ospitalita, Servizi Professionali, Altro",
  "city": "comune",
  "province": "TN o BZ",
  "budget_estimated": 0,
  "budget_note": "come hai stimato il budget (es: 'tabellone outdoor 2 settimane ~€800-1200')",
  "ai_strategy": "strategia commerciale consigliata (2-3 frasi)",
  "ai_products": "prodotti pubblicitari consigliati",
  "confidence": 0.0,
  "source_name": "Manuale",
  "source_category": "upload"
}}

Per il budget_estimated: stima in euro basandoti sul tipo di pubblicità:
- Tabellone outdoor (2 sett): €800-2000
- Spot TV locale (30"x10 passaggi): €400-1500
- Spot radio (30"x20 passaggi): €200-600
- Inserzione giornale pagina intera: €800
- Sponsorizzazione evento locale: €500-5000
- Campagna social: €200-2000
Se è un segnale informativo (non pubblicitario), budget_estimated = 0.
"""


class ManualAttachmentIn(BaseModel):
    filename:    str = "allegato"
    media_type:  str = "application/octet-stream"
    data_base64: str = ""


class ManualAnalyzeRequest(BaseModel):
    input_text: str = ""
    input_type: str = "text"  # text | url
    attachments: list[ManualAttachmentIn] = []


@app.post("/api/manual/analyze")
async def manual_analyze(req: ManualAnalyzeRequest):
    """Analyze manual input (testo/link/allegati) and return proposed signal (not saved)."""
    import anthropic as _anthropic
    client = _anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # If URL, fetch content first
    input_text = req.input_text
    if req.input_type == "url" and req.input_text.startswith("http"):
        try:
            import requests
            from bs4 import BeautifulSoup
            r = requests.get(req.input_text, timeout=10,
                             headers={"User-Agent": "Mozilla/5.0"})
            soup = BeautifulSoup(r.text, "lxml")
            for tag in soup(["script", "style", "nav", "header", "footer"]):
                tag.decompose()
            body = soup.find("article") or soup.find("main") or soup.body
            input_text = (body or soup).get_text(separator=" ", strip=True)[:3000]
        except Exception as e:
            log.warning(f"URL fetch error: {e}")

    prompt = MANUAL_ANALYZE_PROMPT.format(
        input_text=input_text[:3000],
        input_type=req.input_type,
    )

    # Build content blocks: immagini → vision, PDF → document, il resto ignorato
    # ai fini dell'analisi (ma comunque salvato come allegato dopo la conferma).
    content = []
    has_media = False
    for att in req.attachments:
        if not att.data_base64:
            continue
        if att.media_type.startswith("image/"):
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": att.media_type, "data": att.data_base64},
            })
            has_media = True
        elif "pdf" in att.media_type or att.filename.lower().endswith(".pdf"):
            content.append({
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": att.data_base64},
            })
            has_media = True

    if has_media:
        content.append({"type": "text", "text": "Analizza il/i file allegato/i. " + prompt})
    else:
        content = prompt

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1200,
            messages=[{"role": "user", "content": content}],
        )
        raw = response.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        data["input_type"] = req.input_type
        data["input_text"] = req.input_text[:500]
        return {"ok": True, "signal": data}
    except Exception as e:
        log.error(f"Manual analyze error: {e}")
        raise HTTPException(500, f"Analisi fallita: {str(e)}")


class ManualConfirmRequest(BaseModel):
    company_name: str
    signal_type: str
    signal_nature: str = "informative"
    title: str = ""
    description: str
    urgency: str = "media"
    sector: str = ""
    city: str = ""
    province: str = "TN"
    budget_estimated: float = 0
    ai_strategy: str = ""
    ai_products: str = ""
    source_name: str = "Manuale"
    source_category: str = "upload"
    confidence: float = 0.9
    input_text: str = ""
    attachments: list[ManualAttachmentIn] = []


@app.post("/api/manual/confirm")
def manual_confirm(req: ManualConfirmRequest):
    """Save a manually confirmed signal to the DB, along with any attachments."""
    import json as _json

    # Save signal
    signal_id = storage.save_signal(
        company_name    = req.company_name,
        signal_type     = req.signal_type,
        description     = req.description,
        confidence      = req.confidence,
        source_name     = req.source_name,
        source_category = req.source_category,
        signal_nature   = req.signal_nature,
        sector          = req.sector,
        city            = req.city,
        province        = req.province,
        urgency         = req.urgency,
        ai_strategy     = req.ai_strategy,
        ai_products     = req.ai_products,
        raw_excerpt     = req.input_text[:500],
        title           = req.title,
    )

    # Save attachments (images/files), if any, linked to the new signal
    for att in req.attachments:
        if not att.data_base64:
            continue
        storage.save_attachment(
            signal_id   = signal_id,
            filename    = att.filename or "allegato",
            media_type  = att.media_type or "application/octet-stream",
            data_base64 = att.data_base64,
        )

    # If advertising and has budget → save to media_spots
    if req.signal_nature == "advertising" and req.budget_estimated > 0:
        from db import get_connection
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO media_spots
                        (media_name, company_name, signal_id, estimated_cost)
                    VALUES (%s, %s, %s, %s)
                """, (req.source_name, req.company_name, signal_id,
                      req.budget_estimated))
            conn.commit()
        finally:
            conn.close()

    return {"ok": True, "signal_id": signal_id}


@app.get("/api/signals/{signal_id}/attachments")
def get_signal_attachments(signal_id: int):
    """Return attachments (images/files) linked to a manually-created signal."""
    atts = storage.get_attachments_for_signal(signal_id)
    for a in atts:
        if a.get("created_at") and hasattr(a["created_at"], "isoformat"):
            a["created_at"] = a["created_at"].isoformat()
    return {"attachments": atts}


@app.get("/api/signals/{signal_id}")
def get_signal(signal_id: int):
    """Fetch a single signal (used to pre-fill the edit form)."""
    s = storage.get_signal_by_id(signal_id)
    if not s:
        raise HTTPException(404, "Segnale non trovato")
    for k in ("detected_at", "published_at"):
        if s.get(k) and hasattr(s[k], "isoformat"):
            s[k] = s[k].isoformat()
    return {"signal": s}


class SignalEditRequest(BaseModel):
    company_name: str
    signal_type: str
    signal_nature: str = "informative"
    title: str = ""
    description: str
    urgency: str = "media"
    sector: str = ""
    city: str = ""
    province: str = "TN"
    budget_estimated: float = 0
    ai_strategy: str = ""
    ai_products: str = ""


@app.put("/api/signals/{signal_id}")
def edit_signal(signal_id: int, req: SignalEditRequest):
    """Update an existing signal (used by the edit ✎ icon on manually-created signals)."""
    ok = storage.update_signal(
        signal_id        = signal_id,
        company_name     = req.company_name,
        signal_type      = req.signal_type,
        signal_nature    = req.signal_nature,
        title            = req.title,
        description      = req.description,
        urgency          = req.urgency,
        sector           = req.sector,
        city             = req.city,
        province         = req.province,
        ai_strategy      = req.ai_strategy,
        ai_products      = req.ai_products,
        budget_estimated = req.budget_estimated,
    )
    if not ok:
        raise HTTPException(404, "Segnale non trovato")
    return {"ok": True}


@app.delete("/api/signals/{signal_id}")
def remove_signal(signal_id: int):
    """Delete a signal (used by the 🗑 icon on manually-created signals)."""
    ok = storage.delete_signal(signal_id)
    if not ok:
        raise HTTPException(404, "Segnale non trovato")
    return {"ok": True}
