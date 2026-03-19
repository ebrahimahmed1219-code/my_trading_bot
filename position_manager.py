import MetaTrader5 as mt5

from mt5_connector import (
    cancel_pending_order,
    close_position,
    get_open_positions,
    get_pending_orders,
    is_success_result,
    modify_stop_loss,
)
from logger import log_event


def move_all_to_break_even(buffer=0.0, symbol=None, tickets=None, reference_entry=None):
    """
    Move SLs around entry for matching open positions.

    buffer behavior:
    - BUY positions: SL = entry - buffer
    - SELL positions: SL = entry + buffer
    """

    positions = list(get_open_positions() or [])
    if symbol is not None:
        positions = [pos for pos in positions if pos.symbol == symbol]
    if tickets:
        ticket_set = set(tickets)
        positions = [pos for pos in positions if pos.ticket in ticket_set]

    if not positions:
        log_event(
            f"move_all_to_break_even called but no matching open positions found. symbol={symbol}, tickets={tickets}"
        )
        return

    for pos in positions:
        old_sl = getattr(pos, "sl", None)
        entry = pos.price_open if reference_entry is None else reference_entry

        if pos.type == mt5.POSITION_TYPE_BUY:
            new_sl = entry - buffer
        elif pos.type == mt5.POSITION_TYPE_SELL:
            new_sl = entry + buffer
        else:
            new_sl = entry

        result = modify_stop_loss(pos.ticket, new_sl)
        if is_success_result(result):
            log_event(
                f"Break-even SL update: ticket={pos.ticket} symbol={pos.symbol} "
                f"type={pos.type} old_sl={old_sl} new_sl={new_sl} buffer={buffer} reference_entry={reference_entry}"
            )
        else:
            log_event(
                f"Break-even SL update failed: ticket={pos.ticket} symbol={pos.symbol} "
                f"type={pos.type} attempted_sl={new_sl} buffer={buffer} reference_entry={reference_entry}"
            )

    if buffer:
        log_event(
            f"Matching positions moved near break-even with buffer={buffer}, symbol={symbol}, tickets={tickets}"
        )
    else:
        log_event(
            f"Matching positions moved to exact break-even, symbol={symbol}, tickets={tickets}, "
            f"reference_entry={reference_entry}"
        )


def close_all_positions():
    """Close every open position and cancel any pending orders."""

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
