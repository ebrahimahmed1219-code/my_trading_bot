import MetaTrader5 as mt5

from mt5_connector import (
    cancel_pending_order,
    close_position,
    get_open_positions,
    get_pending_orders,
    modify_stop_loss,
)
from logger import log_event


def move_all_to_break_even(buffer=0.0):
    """
    Move all SLs around entry for every open position.

    buffer behavior:
    - BUY positions: SL = entry - buffer
    - SELL positions: SL = entry + buffer
    """

    positions = get_open_positions()

    if not positions:
        log_event("move_all_to_break_even called but no open positions found.")
        return

    for pos in positions:
        old_sl = getattr(pos, "sl", None)
        entry = pos.price_open

        if pos.type == mt5.POSITION_TYPE_BUY:
            new_sl = entry - buffer
        elif pos.type == mt5.POSITION_TYPE_SELL:
            new_sl = entry + buffer
        else:
            new_sl = entry

        modify_stop_loss(pos.ticket, new_sl)
        log_event(
            f"Break-even SL update: ticket={pos.ticket} symbol={pos.symbol} "
            f"type={pos.type} old_sl={old_sl} new_sl={new_sl} buffer={buffer}"
        )

    if buffer:
        log_event(f"All positions moved near break-even with buffer={buffer}")
    else:
        log_event("All positions moved to exact break-even")


def close_all_positions():
    """Close every open position and cancel any pending reentry orders."""

    positions = get_open_positions() or []
    pending_orders = get_pending_orders() or []

    for pos in positions:
        close_position(pos.ticket)

    for order in pending_orders:
        cancel_pending_order(order.ticket)

    if positions or pending_orders:
        log_event(
            f"All positions and pending orders closed: positions={len(positions)}, pending_orders={len(pending_orders)}"
        )
