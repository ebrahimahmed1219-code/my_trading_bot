import re

from config import SYMBOL_DEFAULT


def parse_trade_signal(message_text):
    """Extract trade information from a raw Telegram message."""

    # Make parsing more robust to case / emojis / extra spaces
    text = (message_text or "").upper()

    symbol_match = re.search(r"\b(BUY|SELL)\s+([A-Z]+)\b", text)
    sl_match = re.search(r"\bSL\s+(\d+\.?\d*)", text)
    tp_matches = re.findall(r"\bTP\s+(\d+\.?\d*)", text)

    if not symbol_match or not sl_match:
        return None

    side = symbol_match.group(1).upper()
    symbol = symbol_match.group(2) if symbol_match.group(2) else SYMBOL_DEFAULT

    stop_loss = float(sl_match.group(1))
    take_profits = [float(tp) for tp in tp_matches]

    return {
        "symbol": symbol,
        "side": side,
        "stop_loss": stop_loss,
        "take_profits": take_profits,
    }


def parse_quick_direction_signal(message_text):
    """
    Parse short standalone direction-only messages, for example:
    - Sell gold
    - Sell gold again
    - Buy gold
    - Buy gold again
    """

    normalized = re.sub(r"[^a-z0-9\s]", " ", (message_text or "").lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return None

    words = normalized.split()
    if len(words) > 3:
        return None

    if "buy" in words:
        side = "BUY"
    elif "sell" in words:
        side = "SELL"
    else:
        return None

    if "gold" not in words and "xauusd" not in words:
        return None

    return {
        "symbol": SYMBOL_DEFAULT,
        "side": side,
    }
