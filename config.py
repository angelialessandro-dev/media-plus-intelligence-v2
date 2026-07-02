import os

# ── AI ────────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL        = "claude-sonnet-4-6"
CLAUDE_MAX_TOKENS   = 1200

# ── DATABASE ──────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "")

# ── CRAWLER ───────────────────────────────────────────────────────────────────
CRAWL_INTERVAL_MINUTES  = int(os.getenv("CRAWL_INTERVAL_MINUTES", "60"))
DELAY_BETWEEN_CALLS     = 2.0
MAX_ARTICLES_PER_RUN    = 60
ARTICLE_MAX_AGE_DAYS    = 7
MIN_CONFIDENCE          = 0.30

# ── GEOGRAPHY ────────────────────────────────────────────────────────────────
REGION_LABEL      = "Trentino-Alto Adige"
TARGET_PROVINCES  = ["TN", "BZ"]
TARGET_CITIES = [
    "Trento","Bolzano","Rovereto","Merano","Bressanone","Riva del Garda",
    "Arco","Pergine Valsugana","Lavis","Mezzocorona","Cles","Borgo Valsugana",
    "Cavalese","Canazei","Levico Terme","Brunico","Vipiteno","Silandro",
    "Egna","Caldaro","Appiano","Laives","Ora","Ortisei","Badia","Dobbiaco",
    "San Candido","Storo","Ledro","Mori","Ala","Avio","Folgaria",
]
SECTORS = [
    "Automotive","Turismo","Casa e Edilizia","Ristorazione","Retail",
    "Tecnologia","Salute e Benessere","Agricoltura","Moda e Abbigliamento",
    "Sport e Outdoor","Finanza e Assicurazioni","Logistica e Trasporti",
    "Artigianato","Istruzione e Formazione","Energia","Industria Manifatturiera",
    "Immobiliare","Comunicazione e Media","Alimentare","Ospitalita",
    "Servizi Professionali","Altro",
]
SIGNAL_TYPES = [
    "nuova_apertura","espansione_territoriale","assunzioni","apertura_filiale",
    "lancio_prodotto","evento","franchising","investimento","raccolta_fondi",
    "campagna_marketing_attiva","partecipazione_fiera","attivita_anomala",
    "notizia_cciaa",
]

# ── SOURCES (Google News RSS + diretti) ───────────────────────────────────────
SOURCES = [
    # Google News TN
    {"name":"GN: apre Trento",     "type":"rss","category":"online_news","region":"TN","priority":1,
     "url":"https://news.google.com/rss/search?q=apre+Trento+azienda&hl=it&gl=IT&ceid=IT:it"},
    {"name":"GN: inaugurazione TN","type":"rss","category":"online_news","region":"TN","priority":1,
     "url":"https://news.google.com/rss/search?q=inaugurazione+Trentino+2026&hl=it&gl=IT&ceid=IT:it"},
    {"name":"GN: investe TN",      "type":"rss","category":"online_news","region":"TN","priority":1,
     "url":"https://news.google.com/rss/search?q=investe+Trentino+azienda&hl=it&gl=IT&ceid=IT:it"},
    {"name":"GN: nuovo locale TN", "type":"rss","category":"online_news","region":"TN","priority":1,
     "url":"https://news.google.com/rss/search?q=nuovo+negozio+OR+locale+Trento&hl=it&gl=IT&ceid=IT:it"},
    {"name":"GN: espande TN",      "type":"rss","category":"online_news","region":"TN","priority":2,
     "url":"https://news.google.com/rss/search?q=espande+OR+espansione+Trentino+azienda&hl=it&gl=IT&ceid=IT:it"},
    {"name":"GN: assume TN",       "type":"rss","category":"online_news","region":"TN","priority":2,
     "url":"https://news.google.com/rss/search?q=assunzioni+Trentino+2026&hl=it&gl=IT&ceid=IT:it"},
    # Google News BZ
    {"name":"GN: apre Bolzano",    "type":"rss","category":"online_news","region":"BZ","priority":1,
     "url":"https://news.google.com/rss/search?q=apre+Bolzano+azienda&hl=it&gl=IT&ceid=IT:it"},
    {"name":"GN: inaugura BZ",     "type":"rss","category":"online_news","region":"BZ","priority":1,
     "url":"https://news.google.com/rss/search?q=inaugurazione+Alto+Adige+2026&hl=it&gl=IT&ceid=IT:it"},
    {"name":"GN: investe BZ",      "type":"rss","category":"online_news","region":"BZ","priority":2,
     "url":"https://news.google.com/rss/search?q=investe+Bolzano+OR+Merano+azienda&hl=it&gl=IT&ceid=IT:it"},
    # Quotidiani (scraping diretto via newspapers.py)
    {"name":"L'Adige",             "type":"newspaper","category":"giornali","region":"TN","priority":1,
     "url":"https://www.ladige.it/"},
    {"name":"Alto Adige",          "type":"newspaper","category":"giornali","region":"BZ","priority":1,
     "url":"https://www.altoadige.it/"},
    {"name":"Trentino",            "type":"newspaper","category":"giornali","region":"TN","priority":2,
     "url":"https://www.giornaletrentino.it/"},
    # CCIAA
    {"name":"CCIAA TN",            "type":"cciaa","category":"cciaa","region":"TN","priority":1,
     "url":"https://www.tn.camcom.it/"},
    {"name":"CCIAA BZ",            "type":"cciaa","category":"cciaa","region":"BZ","priority":1,
     "url":"https://www.bz.camcom.it/"},
]

# Mapping category → display label
CATEGORY_LABELS = {
    "online_news": "Online news",
    "giornali":    "Giornali online/offline",
    "tv":          "Streaming TV",
    "radio":       "Streaming Radio",
    "social":      "Social",
    "cciaa":       "Altre fonti",
    "upload":      "Caricamenti manuali",
}

# ── OUTPUT ────────────────────────────────────────────────────────────────────
LOG_PATH = "data/intelligence.log"
