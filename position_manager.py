from mt5_connector import get_open_positions, modify_stop_loss, close_position
from logger import log_event


def move_all_to_break_even():
    """Move all SL to entry price for every open position."""

    positions = get_open_positions()

    if not positions:
        log_event("move_all_to_break_even called but no open positions found.")
        return

    for pos in positions:
        old_sl = getattr(pos, "sl", None)
        new_sl = pos.price_open
        modify_stop_loss(pos.ticket, new_sl)
        log_event(
            f"Break-even SL update: ticket={pos.ticket} symbol={pos.symbol} "
            f"type={pos.type} old_sl={old_sl} new_sl={new_sl}"
        )

    log_event("All positions moved to break even")

def close_all_positions():
    """Close every open position"""

    positions = get_open_positions()

    if not positions:
        return

    for pos in positions:
        close_position(pos.ticket)

    log_event("All positions closed")