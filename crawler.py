"""crawler.py — Main crawl loop for intelligence-v2."""

import feedparser
import requests
import time
import logging
import os
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from email.utils import parsedate_to_datetime

from config import (
    SOURCES, DELAY_BETWEEN_CALLS, MAX_ARTICLES_PER_RUN,
    ARTICLE_MAX_AGE_DAYS, LOG_PATH,
)
import storage
from extractor import extract_signals

os.makedirs("data", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


# ── RSS FEED ─────────────────────────────────────────────────────────────────

def fetch_rss(source: dict) -> list[dict]:
    try:
        resp = requests.get(source["url"], headers=HEADERS, timeout=12)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)

        if feed.bozo and not feed.entries:
            log.warning(f"[{source['name']}] Invalid feed")
            return []

        cutoff = datetime.utcnow() - timedelta(days=ARTICLE_MAX_AGE_DAYS)
        articles = []

        for entry in feed.entries:
            url = entry.get("link", "")
            if not url:
                continue

            published = None
            for field in ("published", "updated", "created"):
                raw = entry.get(field)
                if raw:
                    try:
                        published = parsedate_to_datetime(raw).replace(tzinfo=None)
                        break
                    except Exception:
                        try:
                            published = datetime(*entry.get(f"{field}_parsed", [])[:6])
                            break
                        except Exception:
                            pass

            if published and published < cutoff:
                continue

            summary = BeautifulSoup(
                entry.get("summary", "") or entry.get("description", ""),
                "html.parser",
            ).get_text(separator=" ", strip=True)

            articles.append({
                "url":       url,
                "title":     entry.get("title", "").strip(),
                "summary":   summary,
                "published": published.isoformat() if published else "",
                "source_name": source["name"],
                "source_category": source.get("category", "online_news"),
            })

        log.info(f"[{source['name']}] {len(articles)} recent articles")
        return articles

    except Exception as e:
        log.error(f"[{source['name']}] RSS fetch error: {e}")
        return []


# ── FULL TEXT ────────────────────────────────────────────────────────────────

def fetch_article_text(url: str, max_chars: int = 3000) -> str:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10, verify=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        for tag in soup(["script", "style", "nav", "header", "footer",
                         "aside", "form", "iframe", "noscript", "ads"]):
            tag.decompose()

        body = (
            soup.find("article") or
            soup.find(class_=lambda c: c and any(
                x in c.lower() for x in ["article", "content", "body", "testo", "news", "entry"]
            )) or
            soup.find("main") or
            soup.body
        )

        text = (body or soup).get_text(separator=" ", strip=True)
        return " ".join(text.split())[:max_chars]

    except Exception as e:
        log.debug(f"Full text fetch failed for {url}: {e}")
        return ""


# ── NEWSPAPER SCRAPER ────────────────────────────────────────────────────────

NEWSPAPER_CONFIGS = {
    "L'Adige": {
        "rss": "https://www.ladige.it/rss",
        "fallback_sections": [
            "https://www.ladige.it/economia",
            "https://www.ladige.it/cronaca",
        ],
    },
    "Alto Adige": {
        "rss": "https://www.altoadige.it/rss",
        "fallback_sections": [
            "https://www.altoadige.it/economia",
        ],
    },
    "Trentino": {
        "rss": "https://www.giornaletrentino.it/feed",
        "fallback_sections": [
            "https://www.giornaletrentino.it/feed",
        ],
    },
}


def fetch_newspaper(source: dict) -> list[dict]:
    """Try RSS first, then scrape section pages for headlines."""
    name = source["name"]
    cfg  = NEWSPAPER_CONFIGS.get(name, {})
    articles = []

    # Try RSS feed
    if cfg.get("rss"):
        rss_src = {**source, "url": cfg["rss"]}
        articles = fetch_rss(rss_src)
        if articles:
            return articles

    # Fallback: scrape section pages
    for url in cfg.get("fallback_sections", []):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=12)
            soup = BeautifulSoup(resp.text, "lxml")
            for a in soup.find_all("a", href=True)[:40]:
                href = a["href"]
                if not href.startswith("http"):
                    from urllib.parse import urljoin
                    href = urljoin(url, href)
                title = a.get_text(strip=True)
                if len(title) > 20 and not storage.is_processed(href):
                    articles.append({
                        "url": href, "title": title, "summary": "",
                        "published": "", "source_name": name,
                        "source_category": source.get("category", "giornali"),
                    })
            if articles:
                break
        except Exception as e:
            log.error(f"[{name}] Section scrape error: {e}")

    log.info(f"[{name}] {len(articles)} articles from newspaper scraper")
    return articles[:20]


# ── CCIAA SCRAPER ────────────────────────────────────────────────────────────

CCIAA_FEEDS = {
    "CCIAA TN": [
        "https://www.tn.camcom.it/it/comunicati-stampa/rss",
        "https://www.tn.camcom.it/it/notizie/rss",
    ],
    "CCIAA BZ": [
        "https://www.camcom.bz.it/it/notizie/rss",
        "https://www.camcom.bz.it/it/comunicati-stampa/rss",
    ],
}


def fetch_cciaa(source: dict) -> list[dict]:
    name = source["name"]
    articles = []

    for feed_url in CCIAA_FEEDS.get(name, []):
        rss_src = {**source, "url": feed_url, "category": "cciaa"}
        found = fetch_rss(rss_src)
        articles.extend(found)

    # Deduplicate by URL
    seen = set()
    unique = []
    for a in articles:
        if a["url"] not in seen:
            seen.add(a["url"])
            unique.append(a)

    log.info(f"[{name}] {len(unique)} CCIAA articles")
    return unique


# ── MAIN LOOP ────────────────────────────────────────────────────────────────

def run():
    log.info("=" * 60)
    log.info(f"INTELLIGENCE CRAWLER START — {datetime.utcnow().isoformat()}")
    log.info("=" * 60)

    from db import init_db, seed_media
    init_db()
    seed_media()

    # Sync sources table
    for s in SOURCES:
        storage.upsert_source(s["name"], s["type"], s.get("category", "online_news"),
                              s.get("url", ""), s.get("region", ""))

    articles_processed = 0
    signals_total      = 0
    sources_ok         = 0
    sources_err        = 0

    for source in sorted(SOURCES, key=lambda s: s.get("priority", 99)):
        if articles_processed >= MAX_ARTICLES_PER_RUN:
            log.info(f"Article limit ({MAX_ARTICLES_PER_RUN}) reached. Stopping.")
            break

        # Fetch articles based on source type
        src_type = source.get("type", "rss")
        if src_type == "rss":
            articles = fetch_rss(source)
        elif src_type == "newspaper":
            articles = fetch_newspaper(source)
        elif src_type == "cciaa":
            articles = fetch_cciaa(source)
        else:
            articles = fetch_rss(source)

        if not articles:
            sources_err += 1
            continue

        sources_ok += 1
        new_count   = 0

        for article in articles:
            if articles_processed >= MAX_ARTICLES_PER_RUN:
                break

            url   = article["url"]
            title = article["title"]

            if storage.is_processed(url):
                continue

            storage.mark_fetched(url, source["name"], title)
            new_count += 1

            full_text = fetch_article_text(url)
            text_to_analyze = full_text if len(full_text) > 200 else article.get("summary", "")

            if not text_to_analyze:
                storage.mark_processed(url, 0)
                continue

            signals = extract_signals(
                text=text_to_analyze,
                source=source["name"],
                source_category=source.get("category", "online_news"),
                title=title,
                date=article.get("published", ""),
            )

            saved = 0
            for sig in signals:
                company = sig.get("company_name", "").strip()
                stype   = sig.get("signal_type", "")

                if not company or not stype:
                    continue

                if storage.is_duplicate_signal(company, stype, days=7):
                    log.debug(f"Duplicate skipped: {company} / {stype}")
                    continue

                storage.save_signal(
                    company_name    = company,
                    signal_type     = stype,
                    description     = sig.get("description", ""),
                    confidence      = sig.get("confidence", 0.5),
                    source_name     = source["name"],
                    source_category = sig.get("source_category", source.get("category", "online_news")),
                    article_url     = url,
                    article_title   = title,
                    expiry_days     = sig.get("expiry_days", 60),
                    sector          = sig.get("sector", ""),
                    city            = sig.get("city", ""),
                    province        = sig.get("province", "TN"),
                    action          = sig.get("action", ""),
                    urgency         = sig.get("urgency", "media"),
                    ai_strategy     = sig.get("ai_strategy", ""),
                    ai_products     = sig.get("ai_products", ""),
                )
                saved += 1

            storage.mark_processed(url, saved)
            articles_processed += 1
            signals_total      += saved

            if new_count > 0:
                time.sleep(DELAY_BETWEEN_CALLS)

        if new_count > 0:
            log.info(f"[{source['name']}] {new_count} new articles processed")

    db_stats = storage.stats()
    log.info("-" * 60)
    log.info(f"CRAWLER END — {datetime.utcnow().isoformat()}")
    log.info(f"Sources OK: {sources_ok} | KO: {sources_err}")
    log.info(f"Articles this run: {articles_processed} | Signals: {signals_total}")
    log.info(f"DB totals — articles: {db_stats['total_articles']} | "
             f"signals: {db_stats['total_signals']} | 24h: {db_stats['signals_last_24h']}")
    log.info("=" * 60)


if __name__ == "__main__":
    run()
