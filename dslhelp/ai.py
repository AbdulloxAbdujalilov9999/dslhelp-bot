from __future__ import annotations

"""Claude API-backed AI features: BOL/receipt OCR and free-text department routing.

Gated by config.ANTHROPIC_API_KEY — callers must check that before calling into this
module (keeps "is AI enabled" a caller-visible decision, not hidden in here).

Voice-message transcription is NOT implemented here: the spec asked for it "via the
Claude API", but Claude's API has no audio/speech-to-text endpoint, so it can't be
built as literally specified. Per the user's explicit choice, voice messages are left
alone (forwarded as audio attachments, untranscribed) rather than faking a feature
that doesn't exist or silently switching to a different paid provider.

Both public functions return None on any failure (bad JSON, network error, no match)
so callers can treat "no result" uniformly and fall back to the manual flow.
"""

import base64
import json
import logging

from . import config

logger = logging.getLogger("dslhelp.ai")

_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def _parse_json_block(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text.strip())
    except (ValueError, TypeError):
        logger.warning("Claude response wasn't valid JSON: %r", text[:200])
        return None


BOL_OCR_PROMPT = (
    "Bu — BOL (Bill of Lading) yoki chek rasmi. Rasmdan quyidagilarni top va FAQAT "
    "JSON qaytar, boshqa hech narsa yozma: "
    '{"load_number": <matn yoki null>, "address": <matn yoki null>, "amount": <raqam yoki null>}. '
    "Agar maydon topilmasa, null qo'y. Izoh yozma, faqat JSON."
)


async def extract_bol_fields(image_bytes: bytes, media_type: str = "image/jpeg") -> dict | None:
    """Returns {"load_number": str|None, "address": str|None, "amount": float|None}, or None on failure."""
    try:
        client = _get_client()
        b64 = base64.b64encode(image_bytes).decode("ascii")
        response = await client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=300,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                        {"type": "text", "text": BOL_OCR_PROMPT},
                    ],
                }
            ],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
    except Exception:
        logger.exception("BOL OCR call failed")
        return None

    parsed = _parse_json_block(text)
    if parsed is None:
        return None
    return {
        "load_number": parsed.get("load_number") or None,
        "address": parsed.get("address") or None,
        "amount": parsed.get("amount") if isinstance(parsed.get("amount"), (int, float)) else None,
    }


def _routing_options() -> list[tuple[str, str]]:
    """(department, category) pairs the classifier may choose from — intake departments only.

    Maintenance is excluded on purpose: /t has its own duplicate-open-ticket check that
    doesn't cleanly compose with this generic routing path.
    """
    options = []
    for code, dept in config.DEPARTMENTS.items():
        for category in dept.categories:
            options.append((code, category))
    return options


ROUTE_PROMPT_TEMPLATE = (
    "Quyidagi matn logistika kompaniyasidagi driverdan kelgan xabar. Qaysi bo'lim/turga "
    "tegishli ekanini aniqla. Variantlar (bo'lim:tur):\n{options}\n\n"
    'Xabar: "{text}"\n\n'
    "FAQAT JSON qaytar: "
    '{{"department": <bo\'lim kodi yoki null>, "category": <tur matni yoki null>}}. '
    "Agar hech qaysi variant mos kelmasa, ikkisini ham null qil. Izoh yozma."
)


async def classify_department(text: str) -> dict | None:
    """Returns {"department": str, "category": str} if confident, else None."""
    options = _routing_options()
    options_text = "\n".join(f"- {dept}:{cat}" for dept, cat in options)

    try:
        client = _get_client()
        response = await client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=150,
            messages=[{"role": "user", "content": ROUTE_PROMPT_TEMPLATE.format(options=options_text, text=text)}],
        )
        reply_text = "".join(block.text for block in response.content if block.type == "text")
    except Exception:
        logger.exception("Department classification call failed")
        return None

    parsed = _parse_json_block(reply_text)
    if parsed is None:
        return None
    department, category = parsed.get("department"), parsed.get("category")
    if not department or not category:
        return None
    if (department, category) not in options:
        logger.warning("Claude returned an out-of-menu route: %r/%r", department, category)
        return None
    return {"department": department, "category": category}
