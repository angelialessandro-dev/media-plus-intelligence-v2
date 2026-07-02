"""extractor.py — Claude-powered signal extraction for intelligence-v2."""

import json
import logging
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, CLAUDE_MAX_TOKENS, CITIES, SECTORS

log = logging.getLogger(__name__)

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


SIGNAL_PROMPT = """\
Sei un analista commerciale esperto del mercato trentino e altoatesino.
Lavori per un'emittente TV/radio regionale e devi identificare opportunità di vendita pubblicitaria.

Analizza il testo e rispondi SOLO con JSON valido, senza testo aggiuntivo.

TESTO: {text}
FONTE: {source}
DATA: {date}

Se non c'è un segnale commerciale chiaro, rispondi: {{"found": false}}

Se trovi un segnale rispondi:
{{
  "found": true,
  "company_name": "nome esatto dell'azienda",
  "city": "comune dalla lista: {cities}",
  "province": "TN o BZ",
  "sector": "settore dalla lista: {sectors}",
  "signal_type": "nuova_apertura|investimento|assunzioni|apertura_filiale|espansione_territoriale|lancio_prodotto|evento|franchising|raccolta_fondi|campagna_marketing_attiva|partecipazione_fiera|attivita_anomala|notizia_cciaa",
  "urgency": "alta|media|bassa",
  "description": "1-2 frasi sull'opportunità commerciale vista da una TV/radio locale",
  "ai_strategy": "Strategia commerciale: perché contattare adesso, cosa proporre, con quale leva. Max 3 frasi.",
  "ai_products": "Prodotti consigliati: scegli 2-3 tra Spot 30sec prime time, Spot 15sec informativa TG, Sponsorizzazione rubrica tematica, Format intervista, L-Banner programmi, Ticker mattutino, Spot radio drive time, Spot radio morning. Motiva brevemente.",
  "confidence": 0.0,
  "expiry_days": 45
}}

Urgency: alta = segnale immediato (inaugurazione imminente, campagna attiva); media = segnale rilevante (espansione pianificata); bassa = segnale generico.
Confidence: 0.0-1.0 basata su quanto il segnale è concreto e azionabile.
"""


def extract_signals(
    text: str,
    source: str,
    source_category: str = "online_news",
    title: str = "",
    date: str = "",
) -> list[dict]:
    """
    Calls Claude to extract commercial signals from an article.
    Returns list of signal dicts (usually 0 or 1).
    """
    if not text or len(text.strip()) < 50:
        return []

    full_text = (f"TITOLO: {title}\n\n{text}") if title else text

    prompt = SIGNAL_PROMPT.format(
        text=full_text[:3500],
        source=source,
        date=date,
        cities=", ".join(CITIES),
        sectors=", ".join(SECTORS),
    )

    try:
        response = _get_client().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=CLAUDE_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()

        # Strip markdown fences if present
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        data = json.loads(raw)

        if not data.get("found"):
            return []

        signal = {
            "company_name":   data.get("company_name", "").strip(),
            "signal_type":    data.get("signal_type", "nuova_apertura"),
            "description":    data.get("description", ""),
            "confidence":     float(data.get("confidence", 0.5)),
            "source_name":    source,
            "source_category": source_category,
            "expiry_days":    int(data.get("expiry_days", 60)),
            "sector":         data.get("sector", "Altro"),
            "city":           data.get("city", ""),
            "province":       data.get("province", "TN"),
            "action":         data.get("ai_products", ""),
            "urgency":        data.get("urgency", "media"),
            "ai_strategy":    data.get("ai_strategy", ""),
            "ai_products":    data.get("ai_products", ""),
        }

        if not signal["company_name"]:
            return []

        log.info(
            f"  ✓ [{signal['signal_type']}] {signal['company_name']} "
            f"({signal['city']}, {signal['province']}) "
            f"urgency={signal['urgency']} conf={signal['confidence']:.2f}"
        )
        return [signal]

    except json.JSONDecodeError as e:
        log.error(f"JSON decode error from Claude: {e} | raw: {raw[:200]}")
        return []
    except Exception as e:
        log.error(f"Claude API error: {e}")
        return []


def extract_from_upload(content: str, filename: str) -> list[dict]:
    """Extract signals from user-uploaded content (PDF text, image OCR, etc.)."""
    return extract_signals(
        text=content,
        source=f"Upload: {filename}",
        source_category="upload",
        title=filename,
        date="",
    )
