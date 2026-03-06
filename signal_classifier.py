def classify_message(message_text):
    """Classify incoming Telegram message"""

    text = message_text.lower()

    if text.startswith("risky trade"):
        return "NEW_TRADE"

    if "break even" in text:
        return "MOVE_SL"

    if "close" in text:
        return "CLOSE_ALL"

    return "IGNORE"