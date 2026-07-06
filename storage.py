"""storage.py — Persistence layer for intelligence signals (PostgreSQL)."""

import hashlib
import logging
from datetime import datetime, timedelta
from db import get_connection

log = logging.getLogger(__name__)


# ── HASHING ──────────────────────────────────────────────────────────────────

def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:20]


# ── ARTICLES (deduplication) ─────────────────────────────────────────────────

def is_processed(url: str) -> bool:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT processed FROM articles WHERE url_hash = %s",
                (url_hash(url),)
            )
            row = cur.fetchone()
            return row is not None and row["processed"] == 1
    finally:
        conn.close()


def mark_fetched(url: str, source: str, title: str = ""):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO articles (url_hash, url, source, title, fetched_at, processed)
                VALUES (%s, %s, %s, %s, %s, 0)
                ON CONFLICT (url_hash) DO NOTHING
            """, (url_hash(url), url, source, title, datetime.utcnow()))
        conn.commit()
    finally:
        conn.close()


def mark_processed(url: str, signals_count: int = 0):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE articles SET processed = 1, signals_found = %s
                WHERE url_hash = %s
            """, (signals_count, url_hash(url)))
        conn.commit()
    finally:
        conn.close()


# ── SIGNALS ───────────────────────────────────────────────────────────────────

def is_duplicate_signal(company: str, signal_type: str, days: int = 7) -> bool:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cutoff = datetime.utcnow() - timedelta(days=days)
            cur.execute("""
                SELECT id FROM signals
                WHERE LOWER(company_name) = LOWER(%s)
                  AND signal_type = %s
                  AND detected_at > %s
                LIMIT 1
            """, (company, signal_type, cutoff))
            return cur.fetchone() is not None
    finally:
        conn.close()


def save_signal(
    company_name: str,
    signal_type: str,
    description: str,
    confidence: float,
    source_name: str,
    source_category: str = "online_news",
    article_url: str = "",
    article_title: str = "",
    published_at=None,
    expiry_days: int = 60,
    sector: str = "",
    city: str = "",
    province: str = "",
    action: str = "",
    raw_excerpt: str = "",
    urgency: str = "media",
    ai_strategy: str = "",
    ai_products: str = "",
    signal_nature: str = None,
) -> int:
    from config import ADVERTISING_CATEGORIES
    from entity_resolver import resolve_company

    if signal_nature is None:
        signal_nature = "advertising" if source_category in ADVERTISING_CATEGORIES else "informative"

    conn = get_connection()
    try:
        # Resolve company entity
        company_id, company_canonical = resolve_company(
            name=company_name, sector=sector, city=city, province=province, conn=conn
        )

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO signals (
                    company_name, signal_type, description, confidence,
                    source_name, source_category, signal_nature,
                    article_url, article_title,
                    detected_at, published_at, expiry_days,
                    sector, city, province, action, raw_excerpt,
                    urgency, ai_strategy, ai_products,
                    company_id, company_canonical
                ) VALUES (
                    %s,%s,%s,%s, %s,%s,%s,
                    %s,%s,
                    %s,%s,%s,
                    %s,%s,%s,%s,%s,
                    %s,%s,%s,
                    %s,%s
                ) RETURNING id
            """, (
                company_name, signal_type, description, confidence,
                source_name, source_category, signal_nature,
                article_url, article_title,
                datetime.utcnow(), published_at, expiry_days,
                sector, city, province, action, raw_excerpt,
                urgency, ai_strategy, ai_products,
                company_id, company_canonical,
            ))
            new_id = cur.fetchone()["id"]

            # Update company signal count
            if company_id:
                cur.execute("""
                    UPDATE companies SET signal_count = signal_count + 1, updated_at = NOW()
                    WHERE id = %s
                """, (company_id,))

            cur.execute("""
                UPDATE sources SET signal_count = signal_count + 1, last_crawl = NOW()
                WHERE name = %s
            """, (source_name,))

        conn.commit()
        return new_id
    finally:
        conn.close()


def get_signals(
    period_hours: int = 24,
    source_category: str = None,
    source_name: str = None,
    company_name: str = None,
    limit: int = 200,
    offset: int = 0,
    order_by: str = "detected_at",
) -> list[dict]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            wheres = ["detected_at > %s"]
            params = [datetime.utcnow() - timedelta(hours=period_hours)]

            if source_category:
                wheres.append("source_category = %s")
                params.append(source_category)
            if source_name:
                wheres.append("source_name = %s")
                params.append(source_name)
            if company_name:
                wheres.append("LOWER(company_name) LIKE LOWER(%s)")
                params.append(f"%{company_name}%")

            order_col = {
                "detected_at": "detected_at DESC",
                "confidence":  "confidence DESC, detected_at DESC",
                "company":     "company_name ASC, detected_at DESC",
            }.get(order_by, "detected_at DESC")

            params += [limit, offset]
            cur.execute(f"""
                SELECT * FROM signals
                WHERE {' AND '.join(wheres)}
                ORDER BY {order_col}
                LIMIT %s OFFSET %s
            """, params)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_signal_counts_by_source(period_hours: int = 24) -> dict:
    """Returns {source_name: count} for the sidebar counters."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cutoff = datetime.utcnow() - timedelta(hours=period_hours)
            cur.execute("""
                SELECT source_name, source_category, COUNT(*) as n
                FROM signals
                WHERE detected_at > %s
                GROUP BY source_name, source_category
                ORDER BY n DESC
            """, (cutoff,))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_companies(limit: int = 200) -> list[dict]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.*, COUNT(s.id) as real_signal_count
                FROM companies c
                LEFT JOIN signals s ON s.company_id = c.id
                GROUP BY c.id
                ORDER BY real_signal_count DESC, c.canonical_name
                LIMIT %s
            """, (limit,))
            rows = []
            for r in cur.fetchall():
                d = dict(r)
                if d.get("aliases") and isinstance(d["aliases"], str):
                    d["aliases"] = json.loads(d["aliases"])
                for k in ("created_at", "updated_at"):
                    if d.get(k) and hasattr(d[k], "isoformat"):
                        d[k] = d[k].isoformat()
                rows.append(d)
            return rows
    finally:
        conn.close()


def get_pending_reviews() -> list[dict]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT cr.*, s.company_name, s.description, s.source_name
                FROM company_review cr
                LEFT JOIN signals s ON s.id = cr.signal_id
                WHERE cr.status = 'pending'
                ORDER BY cr.confidence DESC
                LIMIT 50
            """)
            rows = []
            for r in cur.fetchall():
                d = dict(r)
                if d.get("created_at") and hasattr(d["created_at"], "isoformat"):
                    d["created_at"] = d["created_at"].isoformat()
                rows.append(d)
            return rows
    finally:
        conn.close()


def confirm_merge(review_id: int, accept: bool):
    """Confirm or reject a pending company merge suggestion."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM company_review WHERE id = %s
            """, (review_id,))
            review = cur.fetchone()
            if not review:
                return

            status = "confirmed" if accept else "rejected"
            cur.execute("UPDATE company_review SET status = %s WHERE id = %s",
                        (status, review_id))

            if accept:
                # Merge: update the signal to point to the suggested canonical
                cur.execute("""
                    SELECT id FROM companies WHERE canonical_name = %s
                """, (review["suggested_canonical"],))
                target = cur.fetchone()
                if target and review["signal_id"]:
                    from entity_resolver import add_alias
                    add_alias(conn, target["id"], review["company_name_raw"])
                    cur.execute("""
                        UPDATE signals
                        SET company_id = %s, company_canonical = %s
                        WHERE id = %s
                    """, (target["id"], review["suggested_canonical"], review["signal_id"]))

        conn.commit()
    finally:
        conn.close()


def merge_companies(source_id: int, target_id: int):
    """Merge source company into target, moving all signals."""
    import json as _json
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM companies WHERE id = %s", (source_id,))
            source = cur.fetchone()
            if not source:
                return

            # Move all signals
            cur.execute("""
                UPDATE signals SET company_id = %s,
                    company_canonical = (SELECT canonical_name FROM companies WHERE id = %s)
                WHERE company_id = %s
            """, (target_id, target_id, source_id))

            # Add aliases to target
            aliases = source.get("aliases") or []
            if isinstance(aliases, str):
                aliases = _json.loads(aliases)
            aliases.append(source["canonical_name"])
            for alias in aliases:
                from entity_resolver import add_alias
                add_alias(conn, target_id, alias)

            # Delete source company
            cur.execute("DELETE FROM companies WHERE id = %s", (source_id,))

        conn.commit()
    finally:
        conn.close()


import json


def get_top_movers(period_hours: int = 24, limit: int = 50) -> list[dict]:
    """Aggregate signals per canonical company, compare with previous period."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cutoff     = datetime.utcnow() - timedelta(hours=period_hours)
            prev_start = cutoff - timedelta(hours=period_hours)

            cur.execute("""
                SELECT
                    COALESCE(company_canonical, company_name) as company_name,
                    COUNT(*) as n_total,
                    COUNT(*) FILTER (WHERE signal_nature = 'informative') as n_informative,
                    COUNT(*) FILTER (WHERE signal_nature = 'advertising') as n_advertising,
                    MAX(sector) as sector,
                    MAX(city) as city,
                    STRING_AGG(DISTINCT source_name, ' · ' ORDER BY source_name) as sources,
                    AVG(confidence) as avg_confidence
                FROM signals
                WHERE detected_at > %s
                GROUP BY COALESCE(company_canonical, company_name)
            """, (cutoff,))
            current = {r["company_name"]: dict(r) for r in cur.fetchall()}

            cur.execute("""
                SELECT COALESCE(company_canonical, company_name) as company_name,
                       COUNT(*) as n_prev
                FROM signals
                WHERE detected_at > %s AND detected_at <= %s
                GROUP BY COALESCE(company_canonical, company_name)
            """, (prev_start, cutoff))
            prev = {r["company_name"]: r["n_prev"] for r in cur.fetchall()}

            cur.execute("""
                SELECT company_name, COALESCE(SUM(estimated_cost), 0) as spend
                FROM media_spots WHERE detected_at > %s
                GROUP BY company_name
            """, (cutoff,))
            spend_map = {r["company_name"]: float(r["spend"]) for r in cur.fetchall()}

            results = []
            for company, data in current.items():
                n_cur  = data["n_total"]
                n_prev = prev.get(company, 0)
                pct = round(((n_cur - n_prev) / n_prev) * 100) if n_prev > 0 else (100 if n_cur > 0 else 0)
                results.append({
                    "company_name":     company,
                    "sources":          data["sources"] or "",
                    "sector":           data["sector"] or "",
                    "city":             data["city"] or "",
                    "n_signals":        n_cur,
                    "n_informative":    data["n_informative"],
                    "n_advertising":    data["n_advertising"],
                    "n_prev":           n_prev,
                    "pct_change":       pct,
                    "spend_estimated":  spend_map.get(company, 0.0),
                    "avg_confidence":   round(float(data["avg_confidence"] or 0.5), 2),
                })
            results.sort(key=lambda x: (x["pct_change"], x["n_signals"]), reverse=True)
            return results[:limit]
    finally:
        conn.close()


def get_company_signals(company_name: str, period_hours: int = 24*365) -> list[dict]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cutoff = datetime.utcnow() - timedelta(hours=period_hours)
            cur.execute("""
                SELECT * FROM signals
                WHERE LOWER(company_name) LIKE LOWER(%s)
                  AND detected_at > %s
                ORDER BY detected_at DESC
                LIMIT 500
            """, (f"%{company_name}%", cutoff))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ── SOURCES ───────────────────────────────────────────────────────────────────

def upsert_source(name: str, type_: str, category: str, url: str = "", region: str = ""):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO sources (name, type, category, url, region)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (name) DO UPDATE
                    SET type=EXCLUDED.type, category=EXCLUDED.category,
                        url=EXCLUDED.url, last_crawl=NOW()
            """, (name, type_, category, url, region))
        conn.commit()
    finally:
        conn.close()


def get_sources() -> list[dict]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM sources ORDER BY category, name")
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ── MEDIA & LISTINO ───────────────────────────────────────────────────────────

def get_media_stats(media_name: str, period_hours: int = 24) -> dict:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cutoff = datetime.utcnow() - timedelta(hours=period_hours)
            prev   = cutoff - timedelta(hours=period_hours)

            cur.execute("""
                SELECT COUNT(DISTINCT company_name) as advertisers,
                       COUNT(*) as spots,
                       COALESCE(SUM(estimated_cost), 0) as revenue
                FROM media_spots
                WHERE media_name = %s AND detected_at > %s
            """, (media_name, cutoff))
            current = dict(cur.fetchone() or {})

            cur.execute("""
                SELECT COUNT(*) as spots_prev, COALESCE(SUM(estimated_cost),0) as rev_prev
                FROM media_spots
                WHERE media_name = %s AND detected_at > %s AND detected_at <= %s
            """, (media_name, prev, cutoff))
            prev_data = dict(cur.fetchone() or {})

            prev_spots = prev_data.get("spots_prev", 0) or 0
            cur_spots  = current.get("spots", 0) or 0
            delta_pct  = round(((cur_spots - prev_spots) / prev_spots * 100)) if prev_spots > 0 else (100 if cur_spots > 0 else 0)

            cur.execute("""
                SELECT company_name,
                       COUNT(*) as spots,
                       COALESCE(SUM(estimated_cost),0) as spend
                FROM media_spots
                WHERE media_name = %s AND detected_at > %s
                GROUP BY company_name
                ORDER BY spend DESC
                LIMIT 20
            """, (media_name, cutoff))
            advertisers = [dict(r) for r in cur.fetchall()]

            return {
                "advertisers_count": current.get("advertisers", 0),
                "spots":             cur_spots,
                "revenue":           round(float(current.get("revenue", 0)), 2),
                "delta_pct":         delta_pct,
                "advertisers":       advertisers,
            }
    finally:
        conn.close()


def get_media_listino(media_name: str) -> list[dict]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM media_listino WHERE media_name = %s ORDER BY id
            """, (media_name,))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def add_listino_item(media_name: str, formato: str, specs: str, durata: str,
                     prezzo: str, prezzo_unit: str, note: str) -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO media_listino
                    (media_name, formato, specs, durata, prezzo, prezzo_unit, note)
                VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id
            """, (media_name, formato, specs, durata, prezzo, prezzo_unit, note))
            new_id = cur.fetchone()["id"]
        conn.commit()
        return new_id
    finally:
        conn.close()


def update_listino_item(item_id: int, formato: str, specs: str, durata: str,
                        prezzo: str, prezzo_unit: str, note: str):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE media_listino
                SET formato=%s, specs=%s, durata=%s, prezzo=%s, prezzo_unit=%s, note=%s
                WHERE id=%s
            """, (formato, specs, durata, prezzo, prezzo_unit, note, item_id))
        conn.commit()
    finally:
        conn.close()


def delete_listino_item(item_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM media_listino WHERE id=%s", (item_id,))
        conn.commit()
    finally:
        conn.close()


# ── UPLOADS ───────────────────────────────────────────────────────────────────

def save_upload(filename: str, file_type: str, content: str) -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO uploads (filename, file_type, content, uploaded_at)
                VALUES (%s,%s,%s,NOW()) RETURNING id
            """, (filename, file_type, content))
            new_id = cur.fetchone()["id"]
        conn.commit()
        return new_id
    finally:
        conn.close()


def mark_upload_processed(upload_id: int, signals_found: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE uploads SET processed=true, signals_found=%s WHERE id=%s
            """, (signals_found, upload_id))
        conn.commit()
    finally:
        conn.close()


# ── STATS ─────────────────────────────────────────────────────────────────────

def stats() -> dict:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) as n FROM articles")
            total_articles = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) as n FROM signals")
            total_signals = cur.fetchone()["n"]
            cur.execute("""
                SELECT COUNT(*) as n FROM signals
                WHERE detected_at > NOW() - INTERVAL '24 hours'
            """)
            signals_24h = cur.fetchone()["n"]
        return {
            "total_articles": total_articles,
            "total_signals":  total_signals,
            "signals_last_24h": signals_24h,
        }
    finally:
        conn.close()
