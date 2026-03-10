import re


def _normalize_text(message_text):
    """Lowercase, remove punctuation, and collapse spaces for robust matching."""
    text = (message_text or "").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _is_short_gold_command(message_text):
    """Match short standalone commands like: buy gold / sell gold again."""
    normalized = _normalize_text(message_text)
    if not normalized:
        return False

    words = normalized.split()
    if len(words) > 3:
        return False

    has_side = "buy" in words or "sell" in words
    has_symbol = "gold" in words or "xauusd" in words
    return has_side and has_symbol


def classify_message(message_text):
    """Classify incoming Telegram message."""

    raw_text = (message_text or "")
    text = raw_text.lower()
    normalized = _normalize_text(raw_text)

    if text.startswith("risky trade"):
        return "NEW_TRADE"

    if _is_short_gold_command(raw_text):
        return "PRE_TRADE"

    # Check CLOSE_ALL before MOVE_SL: a message can contain both
    # "close" and "break even" and close intent must win.
    close_keywords = [
        "close all",
        "close position",
        "close trade",
        "exit all",
        "get out",
        "not good anymore",
        "close everything",
        "cancel this trade",
        "i dont like it",
        "i do not like it",
        "dont like it",
        "do not like it",
    ]
    if any(kw in normalized for kw in close_keywords):
        return "CLOSE_ALL"

    if "break even" in normalized:
        return "MOVE_SL"

    return "IGNORE"
