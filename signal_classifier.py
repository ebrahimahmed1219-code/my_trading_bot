import re


def _normalize_text(message_text):
    """Lowercase, remove punctuation, and collapse spaces for robust matching."""
    text = (message_text or "").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def classify_message(message_text):
    """Classify incoming Telegram message."""

    raw_text = (message_text or "")
    normalized = _normalize_text(raw_text)

    if "vip" in normalized and "gold" in normalized and ("buy" in normalized or "sell" in normalized):
        return "NEW_TRADE"

    close_keywords = [
        "close",
        "close all",
        "close position",
        "close trade",
        "exit all",
        "get out",
        "not good anymore",
        "close everything",
        "cancel this trade",
        "cancel it",
        "cancel it now",
        "cancel it nowww",
        "i dont like it",
        "i do not like it",
        "dont like it",
        "do not like it",
        "touched be",
        "touched breakeven",
        "touched break even",
        "be hit",
        "breakeven hit",
        "break even hit",
        "breakeven touched",
        "break even touched",
    ]
    if any(kw in normalized for kw in close_keywords):
        return "CLOSE_ALL"

    if "break even" in normalized or "breakeven" in normalized:
        return "FORWARD_ONLY"

    return "IGNORE"
