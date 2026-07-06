"""db.py — PostgreSQL connection and schema initialisation."""

import os
import psycopg2
import psycopg2.extras
from config import DATABASE_URL


def get_connection():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    conn.autocommit = False
    return conn


def init_db():
    """Create all tables if they don't exist. Safe to call multiple times."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:

            # ── articoli già visti (deduplication) ───────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS articles (
                    url_hash      TEXT PRIMARY KEY,
                    url           TEXT NOT NULL,
                    source        TEXT NOT NULL,
                    title         TEXT,
                    fetched_at    TIMESTAMP NOT NULL DEFAULT NOW(),
                    processed     INTEGER DEFAULT 0,
                    signals_found INTEGER DEFAULT 0
                )
            """)

            # ── segnali rilevati ──────────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    id              SERIAL PRIMARY KEY,
                    company_name    TEXT NOT NULL,
                    signal_type     TEXT NOT NULL,
                    description     TEXT NOT NULL,
                    confidence      REAL NOT NULL DEFAULT 0.5,
                    source_name     TEXT NOT NULL,
                    source_category TEXT DEFAULT 'online_news',
                    signal_nature   TEXT DEFAULT 'informative',
                    article_url     TEXT,
                    article_title   TEXT,
                    detected_at     TIMESTAMP NOT NULL DEFAULT NOW(),
                    published_at    TIMESTAMP,
                    expiry_days     INTEGER DEFAULT 60,
                    sector          TEXT,
                    city            TEXT,
                    province        TEXT,
                    action          TEXT,
                    raw_excerpt     TEXT,
                    urgency         TEXT DEFAULT 'media',
                    ai_strategy     TEXT,
                    ai_products     TEXT
                )
            """)
            # Migration: add signal_nature if missing
            cur.execute("ALTER TABLE signals ADD COLUMN IF NOT EXISTS signal_nature TEXT DEFAULT 'informative'")
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_signals_company
                    ON signals(company_name, detected_at DESC)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_signals_source_cat
                    ON signals(source_category, detected_at DESC)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_signals_detected
                    ON signals(detected_at DESC)
            """)

            # ── fonti monitorate ──────────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sources (
                    id          SERIAL PRIMARY KEY,
                    name        TEXT NOT NULL UNIQUE,
                    type        TEXT NOT NULL,
                    category    TEXT NOT NULL DEFAULT 'online_news',
                    url         TEXT,
                    region      TEXT,
                    active      BOOLEAN DEFAULT true,
                    last_crawl  TIMESTAMP,
                    signal_count INTEGER DEFAULT 0
                )
            """)

            # ── mezzo (TV, Radio, Giornali…) ─────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS media (
                    id               SERIAL PRIMARY KEY,
                    name             TEXT NOT NULL UNIQUE,
                    type             TEXT NOT NULL,
                    category         TEXT NOT NULL,
                    price_per_second REAL,
                    description      TEXT,
                    created_at       TIMESTAMP DEFAULT NOW()
                )
            """)

            # ── listino prezzi ────────────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS media_listino (
                    id          SERIAL PRIMARY KEY,
                    media_name  TEXT NOT NULL,
                    formato     TEXT NOT NULL,
                    specs       TEXT,
                    durata      TEXT,
                    prezzo      TEXT,
                    prezzo_unit TEXT,
                    note        TEXT,
                    created_at  TIMESTAMP DEFAULT NOW()
                )
            """)

            # ── spot/inserzioni rilevate ──────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS media_spots (
                    id               SERIAL PRIMARY KEY,
                    media_name       TEXT NOT NULL,
                    company_name     TEXT NOT NULL,
                    detected_at      TIMESTAMP DEFAULT NOW(),
                    duration_seconds INTEGER DEFAULT 30,
                    signal_id        INTEGER,
                    estimated_cost   REAL,
                    spot_nature      TEXT DEFAULT 'advertising'
                )
            """)
            cur.execute("ALTER TABLE media_spots ADD COLUMN IF NOT EXISTS spot_nature TEXT DEFAULT 'advertising'")
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_spots_media_company
                    ON media_spots(media_name, company_name, detected_at DESC)
            """)

            # ── aziende (entità canoniche) ────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS companies (
                    id              SERIAL PRIMARY KEY,
                    canonical_name  TEXT NOT NULL UNIQUE,
                    normalized_name TEXT NOT NULL,
                    aliases         JSONB DEFAULT '[]',
                    sector          TEXT,
                    city            TEXT,
                    province        TEXT,
                    signal_count    INTEGER DEFAULT 0,
                    created_at      TIMESTAMP DEFAULT NOW(),
                    updated_at      TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_companies_normalized
                    ON companies(normalized_name)
            """)

            # ── coda revisione merge incerti ──────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS company_review (
                    id                  SERIAL PRIMARY KEY,
                    signal_id           INTEGER,
                    company_name_raw    TEXT NOT NULL,
                    suggested_canonical TEXT NOT NULL,
                    confidence          REAL NOT NULL,
                    status              TEXT DEFAULT 'pending',
                    created_at          TIMESTAMP DEFAULT NOW()
                )
            """)

            # Migration: aggiungi company_id e company_canonical a signals
            cur.execute("ALTER TABLE signals ADD COLUMN IF NOT EXISTS company_id INTEGER REFERENCES companies(id)")
            cur.execute("ALTER TABLE signals ADD COLUMN IF NOT EXISTS company_canonical TEXT")


            cur.execute("""
                CREATE TABLE IF NOT EXISTS uploads (
                    id          SERIAL PRIMARY KEY,
                    filename    TEXT NOT NULL,
                    file_type   TEXT,
                    content     TEXT,
                    uploaded_at TIMESTAMP DEFAULT NOW(),
                    processed   BOOLEAN DEFAULT false,
                    signals_found INTEGER DEFAULT 0
                )
            """)

        conn.commit()
    finally:
        conn.close()


def seed_media():
    """Insert default media sources and listino if table is empty."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) as n FROM media")
            if cur.fetchone()["n"] > 0:
                return

            media_rows = [
                ("Trentino TV",      "tv",        "tv",           1.40,  "Canale TV regionale TN"),
                ("Alto Adige TV",    "tv",        "tv",           1.30,  "Canale TV regionale BZ"),
                ("RTTR",             "tv",        "tv",           1.20,  "Radio Televisione Regionale del Trentino"),
                ("TV33",             "tv",        "tv",           1.10,  "Canale TV locale TN/BZ"),
                ("Radio Dolomiti",   "radio",     "radio",        0.35,  "Radio FM Trentino"),
                ("Radio Adige",      "radio",     "radio",        0.28,  "Radio FM TN/BZ"),
                ("L'Adige",          "newspaper", "giornali",     None,  "Quotidiano Trentino"),
                ("Alto Adige",       "newspaper", "giornali",     None,  "Quotidiano Bolzano"),
                ("CCIAA TN",         "cciaa",     "cciaa",        None,  "Camera di Commercio Trento"),
                ("CCIAA BZ",         "cciaa",     "cciaa",        None,  "Camera di Commercio Bolzano"),
            ]
            cur.executemany("""
                INSERT INTO media (name, type, category, price_per_second, description)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (name) DO NOTHING
            """, media_rows)

            listino_rows = [
                # Trentino TV
                ("Trentino TV","Spot 30\"","Durata 30 sec, HD","30\"","€42,00","a passaggio","Prezzo netto"),
                ("Trentino TV","Spot 15\"","Durata 15 sec, HD","15\"","€28,00","a passaggio",""),
                ("Trentino TV","Spot 10\"","Promo breve","10\"","€18,00","a passaggio","Min. 10 passaggi"),
                ("Trentino TV","Sponsorizzazione rubrica","Jingle + logo","—","€1.800","al mese","Esclusiva settore"),
                ("Trentino TV","Telepromozione","Contenuto redazionale","60\"","€120","a passaggio","Max 2/giorno"),
                # Radio Dolomiti
                ("Radio Dolomiti","Spot 30\"","Audio stereo","30\"","€10,50","a passaggio",""),
                ("Radio Dolomiti","Spot 20\"","Audio stereo","20\"","€7,00","a passaggio",""),
                ("Radio Dolomiti","Jingle 10\"","Solo claim","10\"","€4,50","a passaggio","Min. 5 passaggi"),
                ("Radio Dolomiti","Sponsored news","Citazione apertura TG radio","—","€600","al mese","Solo mattino"),
                # L'Adige
                ("L'Adige","Pagina intera","297x420mm, 4 colori","—","€800","a uscita","Prenotazione 5gg"),
                ("L'Adige","Mezza pagina","297x210mm","—","€480","a uscita",""),
                ("L'Adige","Quarto di pagina","148x210mm","—","€280","a uscita",""),
                ("L'Adige","Banner digitale","728x90px, ladige.it","—","€150","a settimana","Traffico certificato"),
                # Alto Adige
                ("Alto Adige","Pagina intera","297x420mm","—","€900","a uscita",""),
                ("Alto Adige","Mezza pagina","297x210mm","—","€520","a uscita",""),
                ("Alto Adige","Banner digitale","altoadige.it","—","€180","a settimana",""),
            ]
            cur.executemany("""
                INSERT INTO media_listino
                    (media_name, formato, specs, durata, prezzo, prezzo_unit, note)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, listino_rows)

        conn.commit()
    finally:
        conn.close()
