"""entity_resolver.py — Company name deduplication and entity resolution."""

import json
import logging
import re
from typing import Optional, Tuple

log = logging.getLogger(__name__)

# ── NORMALIZZAZIONE ───────────────────────────────────────────────────────────

# Suffissi legali e parole generiche da rimuovere prima del confronto
STRIP_TOKENS = [
    r'\bs\.?r\.?l\.?\b', r'\bs\.?p\.?a\.?\b', r'\bs\.?n\.?c\.?\b',
    r'\bs\.?a\.?s\.?\b', r'\bcoop(?:erativa)?\b', r'\bconsorzio\b',
    r'\bcooperativa\b', r'\bsocietà\b', r'\bgruppo\b', r'\bfondazione\b',
    r'\bassociazione\b', r'\bcomune\s+di\b', r'\bprovincia\s+di\b',
    r'\bsociale\b', r'\bitaliana?\b', r'\bnazionale\b',
]


def normalize_name(name: str) -> str:
    """Lowercase, rimuove suffissi legali, punteggiatura e spazi ridondanti."""
    n = name.lower().strip()
    for pat in STRIP_TOKENS:
        n = re.sub(pat, ' ', n, flags=re.IGNORECASE)
    n = re.sub(r"['\"\-.,;:!?()&/]", ' ', n)
    n = ' '.join(n.split())
    return n


# ── SIMILARITÀ ────────────────────────────────────────────────────────────────

def levenshtein(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return levenshtein(s2, s1)
    if not s2:
        return len(s1)
    prev = range(len(s2) + 1)
    for c1 in s1:
        curr = [prev[0] + 1]
        for i, c2 in enumerate(s2):
            curr.append(min(prev[i + 1] + 1, curr[i] + 1, prev[i] + (c1 != c2)))
        prev = curr
    return prev[-1]


def name_similarity(a: str, b: str) -> float:
    """Similarità 0.0–1.0 tra due nomi aziendali (dopo normalizzazione)."""
    na, nb = normalize_name(a), normalize_name(b)
    mx = max(len(na), len(nb))
    if mx == 0:
        return 1.0
    # Boost se uno è contenuto nell'altro (es. "Melinda" in "Consorzio Melinda")
    if na in nb or nb in na:
        return max(0.87, 1 - levenshtein(na, nb) / mx)
    return 1 - levenshtein(na, nb) / mx


def find_best_match(name: str, companies: list[dict]) -> Tuple[Optional[dict], float]:
    """Trova la migliore corrispondenza tra le aziende esistenti."""
    best_company, best_score = None, 0.0
    for c in companies:
        score = name_similarity(name, c["canonical_name"])
        aliases = c.get("aliases") or []
        if isinstance(aliases, str):
            aliases = json.loads(aliases)
        for alias in aliases:
            score = max(score, name_similarity(name, alias))
        if score > best_score:
            best_score = score
            best_company = c
    return best_company, best_score


# ── CLAUDE DISAMBIGUATION ─────────────────────────────────────────────────────

def claude_resolve(name: str, candidates: list[str]) -> Optional[str]:
    """Chiede a Claude se il nome corrisponde a uno dei candidati.
    Usato solo per i casi a media confidence (0.65–0.88)."""
    if not candidates:
        return None
    try:
        import anthropic
        from config import ANTHROPIC_API_KEY, CLAUDE_MODEL
        prompt = (
            f'Sei un esperto di aziende del Trentino-Alto Adige.\n\n'
            f'Nome rilevato: "{name}"\n\n'
            f'Nomi già in archivio:\n'
            + "\n".join(f"- {c}" for c in candidates)
            + '\n\nRispondi SOLO con JSON valido:\n'
            '- Se corrispondono: {"match": "nome esatto in archivio", "confidence": 0.95}\n'
            '- Se non corrispondono: {"match": null, "confidence": 0.0}\n\n'
            'Considera abbreviazioni, nomi parziali, varianti tipografiche.'
        )
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=CLAUDE_MODEL, max_tokens=80,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        return data.get("match")
    except Exception as e:
        log.warning(f"claude_resolve error: {e}")
        return None


# ── SOGLIE ────────────────────────────────────────────────────────────────────

HIGH_CONFIDENCE   = 0.88   # merge automatico
MEDIUM_CONFIDENCE = 0.65   # chiede a Claude, poi flagga per revisione


# ── OPERAZIONI DB ─────────────────────────────────────────────────────────────

def get_all_companies(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, canonical_name, normalized_name, aliases, sector, city, province
            FROM companies ORDER BY id
        """)
        return [dict(r) for r in cur.fetchall()]


def create_company(conn, canonical_name: str, sector: str = "",
                   city: str = "", province: str = "") -> int:
    normalized = normalize_name(canonical_name)
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO companies (canonical_name, normalized_name, aliases, sector, city, province)
            VALUES (%s, %s, %s::jsonb, %s, %s, %s)
            ON CONFLICT (canonical_name) DO UPDATE SET updated_at = NOW()
            RETURNING id
        """, (canonical_name, normalized, json.dumps([canonical_name]),
              sector, city, province))
        return cur.fetchone()["id"]


def add_alias(conn, company_id: int, alias: str):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE companies
            SET aliases = CASE
                WHEN aliases @> %s::jsonb THEN aliases
                ELSE aliases || %s::jsonb
            END, updated_at = NOW()
            WHERE id = %s
        """, (json.dumps([alias]), json.dumps([alias]), company_id))


def add_to_review(conn, signal_id: int, raw_name: str,
                  suggested_canonical: str, confidence: float):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO company_review
                (signal_id, company_name_raw, suggested_canonical, confidence)
            VALUES (%s, %s, %s, %s)
        """, (signal_id, raw_name, suggested_canonical, round(confidence, 3)))


# ── RESOLVE PRINCIPALE ────────────────────────────────────────────────────────

def resolve_company(
    name: str,
    sector: str = "",
    city: str = "",
    province: str = "",
    signal_id: int = None,
    conn=None,
) -> Tuple[Optional[int], str]:
    """
    Trova o crea un'entità aziendale canonica.
    Restituisce (company_id, canonical_name).
    """
    if not name or not conn:
        return None, name

    companies = get_all_companies(conn)
    best, score = find_best_match(name, companies)

    log.debug(f"resolve '{name}' → '{best and best['canonical_name']}' score={score:.2f}")

    # ── Alta confidence: merge automatico ────────────────────────────────────
    if best and score >= HIGH_CONFIDENCE:
        cid, canonical = best["id"], best["canonical_name"]
        if name.lower() != canonical.lower():
            add_alias(conn, cid, name)
        log.info(f"AUTO-MERGE '{name}' → '{canonical}' ({score:.2f})")
        return cid, canonical

    # ── Media confidence: chiedi a Claude ────────────────────────────────────
    elif best and score >= MEDIUM_CONFIDENCE:
        candidates = [best["canonical_name"]]
        # Aggiungi altri candidati vicini
        others = sorted(
            [(c, name_similarity(name, c["canonical_name"])) for c in companies
             if c["id"] != best["id"]],
            key=lambda x: -x[1]
        )[:3]
        candidates += [c["canonical_name"] for c, s in others if s >= 0.50]

        resolved = claude_resolve(name, candidates)
        if resolved:
            match = next((c for c in companies if c["canonical_name"] == resolved), None)
            if match:
                add_alias(conn, match["id"], name)
                log.info(f"CLAUDE-MERGE '{name}' → '{resolved}'")
                return match["id"], resolved

        # Claude non ha risolto → crea nuova entità e manda in revisione
        cid = create_company(conn, name, sector, city, province)
        if signal_id:
            add_to_review(conn, signal_id, name, best["canonical_name"], score)
        log.info(f"REVIEW '{name}' ↔ '{best['canonical_name']}' ({score:.2f})")
        return cid, name

    # ── Bassa confidence: nuova entità ───────────────────────────────────────
    else:
        cid = create_company(conn, name, sector, city, province)
        log.info(f"NEW ENTITY '{name}' (id={cid})")
        return cid, name
