def classify_message(message_text):
    """Classify incoming Telegram message"""

    text = message_text.lower()

    if text.startswith("risky trade"):
        return "NEW_TRADE"

    # Check CLOSE_ALL before MOVE_SL — a message can contain both
    # "close" and "break even" (e.g. "Close all position ... at break even")
    # and the intent is always to close, not just move SL.
    _CLOSE_ALL_KEYWORDS = [
        "close all",
        "close position",
        "close trade",
        "exit all",
        "get out",
        "not good anymore",
        "close everything",
    ]
    if any(kw in text for kw in _CLOSE_ALL_KEYWORDS):
        return "CLOSE_ALL"

    if "break even" in text:
        return "MOVE_SL"

    return "IGNORE"