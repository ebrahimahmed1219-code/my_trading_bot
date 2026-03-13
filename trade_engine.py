from threading import Thread
import time

import MetaTrader5 as mt5

from config import FIXED_STOP_LOSS_DISTANCE, SYMBOL_DEFAULT, TOTAL_POSITIONS
from logger import log_event
from mt5_connector import (
    cancel_pending_order,
    get_account_balance,
    get_symbol_price,
    initialize_mt5,
    modify_position_targets,
    open_pending_position,
    open_position,
)
from position_manager import move_all_to_break_even
from risk_manager import DEFAULT_REENTRY_RISK_RATIO, calculate_lot_size


RUNNER_ENABLED = True
RUNNER_SLOT_INDEX = TOTAL_POSITIONS - 1
TP_SLOT_COUNT = TOTAL_POSITIONS - 1
REENTRY_OFFSET = 4.0
REENTRY_STOP_LOSS_DISTANCE = 4.0

PENDING_PRE_SIGNAL = {
    "symbol": None,
    "side": None,
    "tickets": [],
    "created_at": 0.0,
}

SYMBOL_CACHE = {}
REENTRY_CANCEL_DISTANCE = 25.0


def set_runner_enabled(enabled: bool):
    """Enable/disable opening the runner position without TP."""
    global RUNNER_ENABLED
    RUNNER_ENABLED = bool(enabled)
    state = "enabled" if RUNNER_ENABLED else "disabled"
    log_event(f"Runner position has been {state} via UI.")


def _clamp_volume_to_symbol(volume, symbol_info):
    """Clamp volume to symbol limits and align it down to the nearest valid step."""
    if volume <= 0:
        return 0.0

    vol_min = symbol_info.volume_min or 0.0
    vol_max = symbol_info.volume_max or volume
    vol_step = symbol_info.volume_step or 0.01

    volume = min(volume, vol_max)
    if volume < vol_min:
        return 0.0

    if vol_step > 0:
        steps = int((volume - vol_min) / vol_step)
        volume = vol_min + steps * vol_step

    if volume < vol_min:
        return 0.0

    return round(max(0.0, volume), 2)


def _get_position_type_for_side(side):
    return mt5.POSITION_TYPE_BUY if side == "buy" else mt5.POSITION_TYPE_SELL


def _get_side_order_type(side):
    return mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL


def _fixed_stop_loss(entry_price, side):
    """Return SL exactly 4 USD away from entry."""
    return entry_price - FIXED_STOP_LOSS_DISTANCE if side == "buy" else entry_price + FIXED_STOP_LOSS_DISTANCE


def _reentry_entry(reference_entry, side):
    """Return reentry pending price using the requested +/-4 rule."""
    return reference_entry - REENTRY_OFFSET if side == "buy" else reference_entry + REENTRY_OFFSET


def _reentry_stop_loss(entry_price, side):
    """Return SL exactly 4 USD away from the reentry price."""
    return entry_price - REENTRY_STOP_LOSS_DISTANCE if side == "buy" else entry_price + REENTRY_STOP_LOSS_DISTANCE


def _selected_take_profits(take_profits):
    """Use only the first five TP values; the sixth slot is the runner."""
    return list((take_profits or [])[:TP_SLOT_COUNT])


def _resolve_symbol_info(requested_symbol):
    """Resolve broker-specific symbol names such as XAUUSDm or XAUUSD."""
    cached_name = SYMBOL_CACHE.get(requested_symbol.upper())
    if cached_name:
        cached_info = mt5.symbol_info(cached_name)
        if cached_info is not None:
            return cached_info
    exact = mt5.symbol_info(requested_symbol)
    if exact is not None:
        SYMBOL_CACHE[requested_symbol.upper()] = exact.name
        return exact

    try:
        symbols = mt5.symbols_get()
    except Exception:
        symbols = None

    if not symbols:
        return None

    requested_upper = requested_symbol.upper()
    requested_compact = requested_upper.replace(".", "").replace("_", "")

    def _score(name):
        upper = name.upper()
        compact = upper.replace(".", "").replace("_", "")
        if upper == requested_upper:
            return 0
        if compact == requested_compact:
            return 1
        if upper.startswith(requested_upper):
            return 2
        if requested_upper in upper:
            return 3
        if compact.startswith(requested_compact):
            return 4
        if requested_compact in compact:
            return 5
        return 99

    candidates = []
    for symbol in symbols:
        score = _score(symbol.name)
        if score < 99:
            candidates.append((score, len(symbol.name), symbol))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1]))
    resolved = candidates[0][2]
    SYMBOL_CACHE[requested_symbol.upper()] = resolved.name
    log_event(f"Resolved broker symbol {requested_symbol} -> {resolved.name}")
    return resolved


def _reentry_cutoff_tp(take_profits):
    """Reentry stays active only until TP4 is reached."""
    if not take_profits:
        return None
    if len(take_profits) >= 4:
        return take_profits[3]
    return take_profits[-1]


def _start_break_even_monitor(symbol, side, first_trigger_price, tracked_tickets=None, reference_entry=None, take_profits=None):
    """Move tracked positions to exact break-even once TP1 is hit, then arm reentry watcher."""

    def _monitor():
        if mt5.account_info() is None:
            initialize_mt5()

        log_event(
            f"Break-even monitor started for {symbol} {side}. first_trigger={first_trigger_price}"
        )

        while True:
            positions = mt5.positions_get(symbol=symbol)
            if not positions:
                log_event(f"Break-even monitor for {symbol} stopped: no open positions.")
                break

            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                time.sleep(2)
                continue

            if side == "buy":
                price = tick.ask or tick.last
                if price is None:
                    time.sleep(2)
                    continue
                if price >= first_trigger_price:
                    log_event(
                        f"{symbol} ASK {price} reached TP1 trigger {first_trigger_price}. "
                        "Moving all SLs to exact break-even."
                    )
                    move_all_to_break_even(0.0)
                    if tracked_tickets and reference_entry is not None and take_profits:
                        _start_reentry_monitor(symbol, side, tracked_tickets, reference_entry, take_profits)
                    break
            else:
                price = tick.bid or tick.last
                if price is None:
                    time.sleep(2)
                    continue
                if price <= first_trigger_price:
                    log_event(
                        f"{symbol} BID {price} reached TP1 trigger {first_trigger_price}. "
                        "Moving all SLs to exact break-even."
                    )
                    move_all_to_break_even(0.0)
                    if tracked_tickets and reference_entry is not None and take_profits:
                        _start_reentry_monitor(symbol, side, tracked_tickets, reference_entry, take_profits)
                    break

            time.sleep(2)

    Thread(target=_monitor, daemon=True).start()


def _start_reentry_monitor(symbol, side, tracked_tickets, reference_entry, take_profits):
    """Wait for the break-even batch to close unless TP4 is reached first."""

    def _monitor():
        if mt5.account_info() is None:
            initialize_mt5()

        tracked_ticket_set = set(tracked_tickets)
        cutoff_tp = _reentry_cutoff_tp(take_profits)
        log_event(
            f"Reentry monitor armed for {symbol} {side}. "
            f"tracked_tickets={sorted(tracked_ticket_set)}, reference_entry={reference_entry}, cutoff_tp={cutoff_tp}"
        )

        while True:
            positions = mt5.positions_get(symbol=symbol) or []
            open_tickets = {pos.ticket for pos in positions}
            remaining = tracked_ticket_set.intersection(open_tickets)
            if not remaining:
                log_event(
                    f"Tracked break-even batch for {symbol} {side} has closed before TP4. Placing pending reentry orders."
                )
                _place_reentry_orders(symbol, side, reference_entry, take_profits)
                break

            tick = mt5.symbol_info_tick(symbol)
            if tick is not None and cutoff_tp is not None:
                if side == "buy":
                    price = tick.ask or tick.last
                    if price is not None and price >= cutoff_tp:
                        log_event(
                            f"Reentry cancelled for {symbol} {side}: TP4 {cutoff_tp} was reached before break-even close."
                        )
                        break
                else:
                    price = tick.bid or tick.last
                    if price is not None and price <= cutoff_tp:
                        log_event(
                            f"Reentry cancelled for {symbol} {side}: TP4 {cutoff_tp} was reached before break-even close."
                        )
                        break

            time.sleep(2)

    Thread(target=_monitor, daemon=True).start()



def _start_pending_reentry_guard(symbol, side, pending_tickets, reentry_price):
    """Cancel pending reentry orders if price runs 25 USD away from their entry."""

    def _monitor():
        if mt5.account_info() is None:
            initialize_mt5()

        tracked_ticket_set = set(pending_tickets)
        log_event(
            f"Pending reentry guard armed for {symbol} {side}. "
            f"tracked_tickets={sorted(tracked_ticket_set)}, reentry_price={reentry_price}, "
            f"cancel_distance={REENTRY_CANCEL_DISTANCE}"
        )

        while True:
            orders = mt5.orders_get(symbol=symbol) or []
            open_order_tickets = {order.ticket for order in orders}
            remaining = tracked_ticket_set.intersection(open_order_tickets)
            if not remaining:
                log_event(f"Pending reentry guard stopped for {symbol} {side}: no tracked pending orders remain.")
                break

            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                time.sleep(2)
                continue

            price = (tick.ask or tick.last) if side == "buy" else (tick.bid or tick.last)
            if price is None:
                time.sleep(2)
                continue

            if abs(price - reentry_price) >= REENTRY_CANCEL_DISTANCE:
                log_event(
                    f"Cancelling pending reentry orders for {symbol} {side}: current_price={price}, "
                    f"reentry_price={reentry_price}, distance={abs(price - reentry_price):.2f}"
                )
                for ticket in sorted(remaining):
                    cancel_pending_order(ticket)
                break

            time.sleep(2)

    Thread(target=_monitor, daemon=True).start()
def _free_margin_or_balance(balance):
    acc = mt5.account_info()
    if acc is None:
        log_event("MT5 account_info() unavailable while checking margin.")
        return 0.0

    free_margin = getattr(acc, "margin_free", None)
    if free_margin is None:
        return balance
    return max(0.0, free_margin)


def _prepare_symbol_and_account(requested_symbol):
    balance = get_account_balance()
    account_info = mt5.account_info()
    if account_info is None:
        if not initialize_mt5():
            log_event("MT5 account_info() unavailable and MT5 init failed.")
            return None, None, None, None
        account_info = mt5.account_info()
        if account_info is None:
            log_event("MT5 account_info() still unavailable after init.")
            return None, None, None, None

    symbol_info = _resolve_symbol_info(requested_symbol)
    if not symbol_info:
        log_event(f"Symbol info not found for {requested_symbol}.")
        return None, None, None, None

    if not symbol_info.visible:
        mt5.symbol_select(symbol_info.name, True)
        symbol_info = mt5.symbol_info(symbol_info.name)
        if not symbol_info or not symbol_info.visible:
            log_event(f"Symbol {requested_symbol} not visible/selected.")
            return None, None, None, None

    resolved_symbol = symbol_info.name
    entry_price = get_symbol_price(resolved_symbol)
    if entry_price is None:
        log_event(f"Cannot get current market price for {resolved_symbol}.")
        return None, None, None, None

    return balance, entry_price, symbol_info, resolved_symbol


def _margin_per_lot(symbol, entry_price, symbol_info, side):
    action = _get_side_order_type(side)
    margin_for_one = mt5.order_calc_margin(action, symbol, 1.0, entry_price)
    if margin_for_one is not None and margin_for_one > 0:
        return margin_for_one
    return symbol_info.margin_initial or 0.0


def _plan_position_sizing(balance, entry_price, stop_loss, symbol, symbol_info, side, risk_ratio_override=None):
    """Plan one equal lot size for all six positions before sending any order."""
    risk_total_lot = calculate_lot_size(
        balance,
        entry_price,
        stop_loss,
        symbol,
        risk_ratio_override=risk_ratio_override,
    )
    if risk_total_lot <= 0:
        log_event(f"Calculated lot size <= 0 for {symbol}. Aborting trade.")
        return None, None

    margin_per_lot = _margin_per_lot(symbol, entry_price, symbol_info, side)
    free_margin = _free_margin_or_balance(balance)

    margin_total_lot = risk_total_lot
    if margin_per_lot > 0 and free_margin > 0:
        margin_total_lot = free_margin / margin_per_lot

    total_lot_cap = min(risk_total_lot, margin_total_lot)
    per_position_lot = _clamp_volume_to_symbol(total_lot_cap / TOTAL_POSITIONS, symbol_info)

    if per_position_lot <= 0:
        log_event(
            f"Cannot afford minimum lot for all {TOTAL_POSITIONS} positions on {symbol}. "
            f"risk_total_lot={risk_total_lot:.4f}, margin_total_lot={margin_total_lot:.4f}"
        )
        return None, None

    planned_total_lot = per_position_lot * TOTAL_POSITIONS
    log_event(
        f"Planned sizing for {symbol} {side}: risk_total_lot={risk_total_lot:.4f}, "
        f"margin_total_lot={margin_total_lot:.4f}, planned_total_lot={planned_total_lot:.4f}, "
        f"per_position_lot={per_position_lot:.4f}, risk_override={risk_ratio_override}"
    )
    return per_position_lot, planned_total_lot


def _place_reentry_orders(symbol, side, reference_entry, take_profits):
    """Place 6 pending reentry orders using 30% risk and 4 USD SL distance."""
    prep = _prepare_symbol_and_account(symbol)
    if prep[0] is None:
        return
    balance, _, symbol_info, resolved_symbol = prep

    reentry_price = _reentry_entry(reference_entry, side)
    stop_loss = _reentry_stop_loss(reentry_price, side)
    per_position_lot, planned_total_lot = _plan_position_sizing(
        balance,
        reentry_price,
        stop_loss,
        resolved_symbol,
        symbol_info,
        side,
        risk_ratio_override=DEFAULT_REENTRY_RISK_RATIO,
    )
    if per_position_lot is None:
        return

    selected_tps = _selected_take_profits(take_profits)
    log_event(
        f"Placing reentry orders for {resolved_symbol} {side}: reentry_price={reentry_price}, "
        f"sl={stop_loss}, planned_total_lot={planned_total_lot:.4f}, per_position_lot={per_position_lot:.4f}"
    )

    placed_any = False
    pending_tickets = []
    for tp in selected_tps:
        result = open_pending_position(resolved_symbol, side.upper(), per_position_lot, reentry_price, stop_loss, tp)
        if result is not None and getattr(result, "retcode", None) in {
            mt5.TRADE_RETCODE_DONE,
            mt5.TRADE_RETCODE_PLACED,
            mt5.TRADE_RETCODE_DONE_PARTIAL,
        }:
            placed_any = True
            if getattr(result, "order", 0):
                pending_tickets.append(result.order)
        else:
            log_event(f"Pending TP reentry order could not be completed for {resolved_symbol} {side}. Stopping batch.")
            break

    runner_should_open = RUNNER_ENABLED and TOTAL_POSITIONS > len(selected_tps)
    if runner_should_open and placed_any:
        result = open_pending_position(resolved_symbol, side.upper(), per_position_lot, reentry_price, stop_loss, None)
        if result is not None and getattr(result, "retcode", None) in {
            mt5.TRADE_RETCODE_DONE,
            mt5.TRADE_RETCODE_PLACED,
            mt5.TRADE_RETCODE_DONE_PARTIAL,
        }:
            if getattr(result, "order", 0):
                pending_tickets.append(result.order)
            log_event(f"Pending runner reentry order placed for {resolved_symbol} {side}.")
        else:
            log_event(f"Pending runner reentry order failed for {resolved_symbol} {side}.")

    if pending_tickets:
        _start_pending_reentry_guard(resolved_symbol, side, pending_tickets, reentry_price)


def execute_pre_signal_trade(quick_signal):
    """Open six positions with fixed 4 USD SL and no TP."""
    global PENDING_PRE_SIGNAL

    requested_symbol = (quick_signal or {}).get("symbol") or SYMBOL_DEFAULT
    side = str((quick_signal or {}).get("side", "")).lower()

    if side not in {"buy", "sell"}:
        log_event(f"Invalid pre-signal side: {quick_signal}")
        return

    prep = _prepare_symbol_and_account(requested_symbol)
    if prep[0] is None:
        return
    balance, entry_price, symbol_info, symbol = prep

    stop_loss = _fixed_stop_loss(entry_price, side)
    per_position_lot, planned_total_lot = _plan_position_sizing(
        balance, entry_price, stop_loss, symbol, symbol_info, side
    )
    if per_position_lot is None:
        return

    log_event(
        f"Pre-signal open for {symbol} {side}: planned_total_lot={planned_total_lot:.4f}, "
        f"positions={TOTAL_POSITIONS}, per_position_lot={per_position_lot:.4f}, sl={stop_loss}"
    )

    position_type = _get_position_type_for_side(side)
    before = mt5.positions_get(symbol=symbol) or []
    before_tickets = {p.ticket for p in before if p.type == position_type}

    opened_any = False
    for _ in range(TOTAL_POSITIONS):
        result = open_position(symbol, side.upper(), per_position_lot, stop_loss, None)
        if result is not None and getattr(result, "retcode", None) == mt5.TRADE_RETCODE_DONE:
            opened_any = True
        else:
            log_event(f"Pre-signal order could not be completed for {symbol} {side}. Stopping batch.")
            break

    if not opened_any:
        log_event(f"No pre-signal positions opened for {symbol} {side}.")
        return

    time.sleep(1)
    after = mt5.positions_get(symbol=symbol) or []
    new_tickets = [
        p.ticket for p in after if p.type == position_type and p.ticket not in before_tickets
    ]

    PENDING_PRE_SIGNAL = {
        "symbol": symbol,
        "side": side,
        "tickets": new_tickets,
        "created_at": time.time(),
    }
    log_event(f"Stored pending pre-signal batch for {symbol} {side}: tickets={new_tickets}")


def apply_signal_to_existing_positions(signal_data):
    """Apply the main signal to matching pre-opened positions instead of opening duplicates."""
    global PENDING_PRE_SIGNAL
    requested_symbol = signal_data.get("symbol")
    side = str(signal_data.get("side", "")).lower()
    take_profits = _selected_take_profits(signal_data.get("take_profits") or [])

    if not requested_symbol or side not in {"buy", "sell"}:
        return False

    if not take_profits:
        return False

    symbol_info = _resolve_symbol_info(requested_symbol)
    symbol = symbol_info.name if symbol_info is not None else requested_symbol

    positions = mt5.positions_get(symbol=symbol) or []
    position_type = _get_position_type_for_side(side)
    side_positions = [p for p in positions if p.type == position_type]

    pending_matches = (
        PENDING_PRE_SIGNAL.get("symbol") == symbol
        and PENDING_PRE_SIGNAL.get("side") == side
        and PENDING_PRE_SIGNAL.get("tickets")
    )

    if pending_matches:
        pending_tickets = set(PENDING_PRE_SIGNAL["tickets"])
        side_positions = [p for p in side_positions if p.ticket in pending_tickets]

    if not side_positions:
        return False

    side_positions.sort(key=lambda p: p.ticket)
    first_tp = take_profits[0]
    edited_any = False
    tracked_positions = side_positions[:TOTAL_POSITIONS]
    tracked_tickets = [pos.ticket for pos in tracked_positions]
    reference_entry = sum(pos.price_open for pos in tracked_positions) / len(tracked_positions)

    log_event(
        f"Applying main signal to existing positions for {symbol} {side}: "
        f"count={len(side_positions)}, fixed_sl_distance={FIXED_STOP_LOSS_DISTANCE}, tps={take_profits}"
    )

    for idx, pos in enumerate(tracked_positions):
        new_sl = _fixed_stop_loss(pos.price_open, side)
        new_tp = take_profits[idx] if idx < len(take_profits) else 0.0
        if idx == RUNNER_SLOT_INDEX or idx >= TP_SLOT_COUNT:
            new_tp = 0.0

        result = modify_position_targets(
            pos.ticket,
            new_sl=new_sl,
            new_tp=new_tp,
            comment="apply_main_signal",
        )
        if result is not None:
            edited_any = True

    if not edited_any:
        return False

    _start_break_even_monitor(symbol, side, first_tp, tracked_tickets, reference_entry, take_profits)
    PENDING_PRE_SIGNAL = {"symbol": None, "side": None, "tickets": [], "created_at": 0.0}
    return True


def execute_trade(signal_data):
    """Open exactly six positions: five TP trades and one runner, all with fixed 4 USD SL."""
    requested_symbol = signal_data.get("symbol")
    side = str(signal_data.get("side", "")).lower()
    take_profits = _selected_take_profits(signal_data.get("take_profits") or [])

    if not requested_symbol or side not in {"buy", "sell"}:
        log_event(f"Invalid trade signal: {signal_data}")
        return

    if not take_profits:
        log_event(f"No usable take-profit levels provided for {requested_symbol}. Aborting trade.")
        return

    prep = _prepare_symbol_and_account(requested_symbol)
    if prep[0] is None:
        return
    balance, entry_price, symbol_info, symbol = prep

    stop_loss = _fixed_stop_loss(entry_price, side)
    per_position_lot, planned_total_lot = _plan_position_sizing(
        balance, entry_price, stop_loss, symbol, symbol_info, side
    )
    if per_position_lot is None:
        return

    first_tp = take_profits[0]
    log_event(
        f"Executing trade {symbol} {side}: planned_total_lot={planned_total_lot:.4f}, "
        f"positions={TOTAL_POSITIONS}, used_tps={take_profits}, runner_enabled={RUNNER_ENABLED}, "
        f"fixed_sl={stop_loss}, per_position_lot={per_position_lot:.4f}"
    )

    position_type = _get_position_type_for_side(side)
    before = mt5.positions_get(symbol=symbol) or []
    before_tickets = {p.ticket for p in before if p.type == position_type}

    opened_any = False

    for tp in take_profits:
        log_event(
            f"Opening TP position for {symbol} {side} lot={per_position_lot:.4f} TP={tp} SL={stop_loss}"
        )
        result = open_position(symbol, side.upper(), per_position_lot, stop_loss, tp)
        if result is not None and getattr(result, "retcode", None) == mt5.TRADE_RETCODE_DONE:
            opened_any = True
        else:
            log_event(f"TP order could not be completed for {symbol} {side}. Stopping batch.")
            break

    runner_should_open = RUNNER_ENABLED and TOTAL_POSITIONS > len(take_profits)
    if runner_should_open and opened_any:
        log_event(
            f"Opening runner position for {symbol} {side} lot={per_position_lot:.4f} SL={stop_loss}"
        )
        runner_result = open_position(symbol, side.upper(), per_position_lot, stop_loss, None)
        if runner_result is not None:
            retcode = getattr(runner_result, "retcode", None)
            success_retcodes = {
                mt5.TRADE_RETCODE_DONE,
                mt5.TRADE_RETCODE_PLACED,
                mt5.TRADE_RETCODE_DONE_PARTIAL,
            }
            if retcode in success_retcodes:
                opened_any = True
                log_event(f"Runner position confirmed open for {symbol} (retcode={retcode})")
            else:
                log_event(f"Runner position could not be completed for {symbol} (retcode={retcode}).")

    if not opened_any:
        log_event(f"No positions opened for {symbol} due to risk/margin constraints.")
        return

    time.sleep(1)
    after = mt5.positions_get(symbol=symbol) or []
    tracked_positions = [
        pos for pos in after if pos.type == position_type and pos.ticket not in before_tickets
    ]
    tracked_positions.sort(key=lambda pos: pos.ticket)
    tracked_positions = tracked_positions[:TOTAL_POSITIONS]
    tracked_tickets = [pos.ticket for pos in tracked_positions]
    reference_entry = entry_price
    if tracked_positions:
        reference_entry = sum(pos.price_open for pos in tracked_positions) / len(tracked_positions)

    _start_break_even_monitor(symbol, side, first_tp, tracked_tickets, reference_entry, take_profits)







