import re

from config import SYMBOL_DEFAULT


def parse_trade_signal(message_text):
    """Parse VIP Gold signals such as 'Gold Sell limit @4721.5' with up to 3 TP levels."""
    text = (message_text or "").strip()
    upper = text.upper()

    if "VIP" not in upper:
        return None

    order_match = re.search(
        r"\bGOLD\s+(BUY|SELL)(?:\s+(LIMIT|STOP))?\s*@\s*(\d+(?:\.\d+)?)",
        upper,
    )
    if not order_match:
        order_match = re.search(
            r"\bGOLD\s+(BUY|SELL)(?:\s+(LIMIT|STOP))?\b",
            upper,
        )

    sl_match = re.search(r"\bSL\b[^\d]*(\d+(?:\.\d+)?)", upper)
    tp_matches = re.findall(r"\b(?:TP\d+|FINAL\s+TP)\b[^\d]*(\d+(?:\.\d+)?)", upper)

    if not order_match or not sl_match:
        return None

    side = order_match.group(1).upper()
    order_kind = (order_match.group(2) or "MARKET").upper()
    entry_price = None
    if order_match.lastindex and order_match.lastindex >= 3:
        raw_entry = order_match.group(3)
        if raw_entry:
            entry_price = float(raw_entry)
    stop_loss = float(sl_match.group(1))
    take_profits = [float(tp) for tp in tp_matches[:3]]

    return {
        "symbol": SYMBOL_DEFAULT,
        "side": side,
        "order_kind": order_kind,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profits": take_profits,
    }
