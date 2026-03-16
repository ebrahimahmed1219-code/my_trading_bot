from threading import Thread
import time

import MetaTrader5 as mt5

from config import FIXED_STOP_LOSS_DISTANCE, SYMBOL_DEFAULT, TOTAL_POSITIONS
from logger import log_event
from mt5_connector import (
    get_account_balance,
    get_symbol_price,
    initialize_mt5,
    modify_position_targets,
    open_position,
)
from position_manager import move_all_to_break_even
from risk_manager import calculate_lot_size


RUNNER_ENABLED = True
RUNNER_SLOT_INDEX = TOTAL_POSITIONS - 1
TP_SLOT_COUNT = TOTAL_POSITIONS - 1
NO_MONEY_RETCODE = getattr(mt5, "TRADE_RETCODE_NO_MONEY", 10019)

PENDING_PRE_SIGNAL = {
    "symbol": None,
    "side": None,
    "tickets": [],
    "created_at": 0.0,
}

SYMBOL_CACHE = {}


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


def _start_break_even_monitor(symbol, side, first_trigger_price, tracked_tickets=None, reference_entry=None, take_profits=None):
    """Move tracked positions to entry +/- 2.5 at TP1, then exact break-even at TP2."""

    def _monitor():
        if mt5.account_info() is None:
            initialize_mt5()

        second_trigger_price = None
        if take_profits and len(take_profits) >= 2:
            second_trigger_price = take_profits[1]
        else:
            second_trigger_price = first_trigger_price

        first_stage_done = False
        log_event(
            f"Break-even monitor started for {symbol} {side}. "
            f"first_trigger={first_trigger_price}, second_trigger={second_trigger_price}"
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
                if not first_stage_done and price >= first_trigger_price:
                    log_event(
                        f"{symbol} ASK {price} reached TP1 trigger {first_trigger_price}. "
                        "Moving all SLs to entry - 2.5."
                    )
                    move_all_to_break_even(2.5, symbol=symbol, tickets=tracked_tickets)
                    first_stage_done = True
                elif first_stage_done and price >= second_trigger_price:
                    log_event(
                        f"{symbol} ASK {price} reached TP2 trigger {second_trigger_price}. "
                        "Moving all SLs to exact break-even."
                    )
                    move_all_to_break_even(0.0, symbol=symbol, tickets=tracked_tickets)
                    break
            else:
                price = tick.bid or tick.last
                if price is None:
                    time.sleep(2)
                    continue
                if not first_stage_done and price <= first_trigger_price:
                    log_event(
                        f"{symbol} BID {price} reached TP1 trigger {first_trigger_price}. "
                        "Moving all SLs to entry + 2.5."
                    )
                    move_all_to_break_even(2.5, symbol=symbol, tickets=tracked_tickets)
                    first_stage_done = True
                elif first_stage_done and price <= second_trigger_price:
                    log_event(
                        f"{symbol} BID {price} reached TP2 trigger {second_trigger_price}. "
                        "Moving all SLs to exact break-even."
                    )
                    move_all_to_break_even(0.0, symbol=symbol, tickets=tracked_tickets)
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


def _prepare_symbol_and_account(requested_symbol, side=None):
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
    price_side = side.upper() if isinstance(side, str) else None
    entry_price = get_symbol_price(resolved_symbol, price_side)
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


def _success_retcodes():
    return {
        mt5.TRADE_RETCODE_DONE,
        mt5.TRADE_RETCODE_PLACED,
        mt5.TRADE_RETCODE_DONE_PARTIAL,
    }


def _compute_next_order_lot(symbol, symbol_info, side, stop_loss, remaining_slots, risk_ratio_override=None):
    """Recalculate the next affordable lot from live account state and remaining slots."""
    if remaining_slots <= 0:
        return 0.0, None, None

    balance = get_account_balance()
    entry_price = get_symbol_price(symbol, side.upper())
    if entry_price is None:
        log_event(f"Cannot get current market price for {symbol} while recalculating lot size.")
        return 0.0, None, None

    risk_total_lot = calculate_lot_size(
        balance,
        entry_price,
        stop_loss,
        symbol,
        risk_ratio_override=risk_ratio_override,
    )
    if risk_total_lot <= 0:
        return 0.0, balance, entry_price

    margin_per_lot = _margin_per_lot(symbol, entry_price, symbol_info, side)
    free_margin = _free_margin_or_balance(balance)

    margin_total_lot = risk_total_lot
    if margin_per_lot > 0 and free_margin > 0:
        margin_total_lot = free_margin / margin_per_lot

    total_lot_cap = min(risk_total_lot, margin_total_lot)
    next_lot = _clamp_volume_to_symbol(total_lot_cap / remaining_slots, symbol_info)
    return next_lot, balance, entry_price


def _execute_dynamic_batch(symbol, side, symbol_info, stop_loss, tp_values, include_runner=False, risk_ratio_override=None):
    """Open a batch while recalculating affordable lot size before every order."""
    opened_any = False
    opened_count = 0
    total_slots = len(tp_values) + (1 if include_runner else 0)
    runner_opened = False
    runner_attempted = not include_runner
    success_codes = _success_retcodes()

    def _attempt(tp):
        nonlocal opened_any, opened_count, runner_opened, runner_attempted
        remaining_slots = total_slots - opened_count
        next_lot, balance, entry_price = _compute_next_order_lot(
            symbol,
            symbol_info,
            side,
            stop_loss,
            remaining_slots,
            risk_ratio_override=risk_ratio_override,
        )
        if next_lot <= 0:
            log_event(
                f"Skipping batch slot for {symbol} {side}: remaining_slots={remaining_slots}, "
                f"balance={balance}, entry_price={entry_price}"
            )
            return False

        target_text = f"TP={tp}" if tp is not None else "runner"
        log_event(
            f"Opening batch slot for {symbol} {side} lot={next_lot:.4f} {target_text} "
            f"SL={stop_loss} remaining_slots={remaining_slots}"
        )
        result = open_position(symbol, side.upper(), next_lot, stop_loss, tp)
        retcode = getattr(result, "retcode", None) if result is not None else None
        if retcode == NO_MONEY_RETCODE:
            reduced_lot = _clamp_volume_to_symbol(next_lot / 2.0, symbol_info)
            if 0 < reduced_lot < next_lot:
                log_event(
                    f"No money for {symbol} {side} at lot={next_lot:.4f}. "
                    f"Retrying once with reduced lot={reduced_lot:.4f}."
                )
                result = open_position(symbol, side.upper(), reduced_lot, stop_loss, tp)
                retcode = getattr(result, "retcode", None) if result is not None else None

        if retcode in success_codes:
            opened_any = True
            opened_count += 1
            if tp is None:
                runner_opened = True
                runner_attempted = True
            return True

        if tp is None:
            runner_attempted = True
        return False

    for tp in tp_values:
        if not _attempt(tp):
            break

    if include_runner and opened_any:
        _attempt(None)

    return opened_any, opened_count, runner_opened, runner_attempted


def execute_pre_signal_trade(quick_signal):
    """Open six positions with fixed 4 USD SL and no TP."""
    global PENDING_PRE_SIGNAL

    requested_symbol = (quick_signal or {}).get("symbol") or SYMBOL_DEFAULT
    side = str((quick_signal or {}).get("side", "")).lower()

    if side not in {"buy", "sell"}:
        log_event(f"Invalid pre-signal side: {quick_signal}")
        return

    prep = _prepare_symbol_and_account(requested_symbol, side)
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

    opened_any, opened_count, _, _ = _execute_dynamic_batch(
        symbol,
        side,
        symbol_info,
        stop_loss,
        [None] * TOTAL_POSITIONS,
        include_runner=False,
    )

    if not opened_any:
        log_event(f"No pre-signal positions opened for {symbol} {side}.")
        return

    if opened_count < TOTAL_POSITIONS:
        log_event(f"Pre-signal batch for {symbol} {side} opened partially: opened={opened_count}/{TOTAL_POSITIONS}")

    time.sleep(1)
    after = mt5.positions_get(symbol=symbol) or []
    new_tickets = [
        p.ticket for p in after if p.type == position_type and p.ticket not in before_tickets
    ]
    if not new_tickets:
        log_event(
            f"Pre-signal positions were opened for {symbol} {side}, but no new tickets were confirmed yet. "
            "Skipping pending pre-signal tracking."
        )
        return

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

    prep = _prepare_symbol_and_account(requested_symbol, side)
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

    runner_should_open = RUNNER_ENABLED and TOTAL_POSITIONS > len(take_profits)
    opened_any, opened_count, runner_opened, runner_attempted = _execute_dynamic_batch(
        symbol,
        side,
        symbol_info,
        stop_loss,
        take_profits,
        include_runner=runner_should_open,
    )

    if not opened_any:
        log_event(f"No positions opened for {symbol} due to risk/margin constraints.")
        return

    if opened_count < len(take_profits) + (1 if runner_should_open else 0):
        log_event(
            f"Trade batch for {symbol} {side} opened partially: opened={opened_count}/"
            f"{len(take_profits) + (1 if runner_should_open else 0)}"
        )
    if runner_should_open and runner_attempted and runner_opened:
        log_event(f"Runner position confirmed open for {symbol}.")
    elif runner_should_open and runner_attempted and not runner_opened:
        log_event(f"Runner position could not be completed for {symbol}.")

    time.sleep(1)
    after = mt5.positions_get(symbol=symbol) or []
    tracked_positions = [
        pos for pos in after if pos.type == position_type and pos.ticket not in before_tickets
    ]
    tracked_positions.sort(key=lambda pos: pos.ticket)
    tracked_positions = tracked_positions[:TOTAL_POSITIONS]
    if not tracked_positions:
        log_event(
            f"No newly tracked positions found after order placement for {symbol} {side}. "
            "Skipping break-even monitor startup."
        )
        return
    tracked_tickets = [pos.ticket for pos in tracked_positions]
    reference_entry = entry_price
    if tracked_positions:
        reference_entry = sum(pos.price_open for pos in tracked_positions) / len(tracked_positions)

    _start_break_even_monitor(symbol, side, first_tp, tracked_tickets, reference_entry, take_profits)







