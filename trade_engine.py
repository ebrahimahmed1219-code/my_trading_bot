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

PENDING_PRE_SIGNAL = {
    "symbol": None,
    "side": None,
    "tickets": [],
    "created_at": 0.0,
}


def set_runner_enabled(enabled: bool):
    """Enable/disable opening the runner position without TP."""
    global RUNNER_ENABLED
    RUNNER_ENABLED = bool(enabled)
    state = "enabled" if RUNNER_ENABLED else "disabled"
    log_event(f"Runner position has been {state} via UI.")


def _clamp_volume_to_symbol(volume, symbol_info):
    """Clamp and step-align volume to symbol settings."""
    if volume <= 0:
        return 0.0

    vol_min = symbol_info.volume_min or 0.0
    vol_max = symbol_info.volume_max or volume
    vol_step = symbol_info.volume_step or 0.01

    volume = max(vol_min, min(volume, vol_max))

    if vol_step > 0:
        steps = round((volume - vol_min) / vol_step)
        volume = vol_min + steps * vol_step

    return max(0.0, volume)


def _get_position_type_for_side(side):
    return mt5.POSITION_TYPE_BUY if side == "buy" else mt5.POSITION_TYPE_SELL


def _get_side_order_type(side):
    return mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL


def _fixed_stop_loss(entry_price, side):
    """Return SL exactly 5 USD away from entry."""
    return entry_price - FIXED_STOP_LOSS_DISTANCE if side == "buy" else entry_price + FIXED_STOP_LOSS_DISTANCE


def _selected_take_profits(take_profits):
    """Use only the first five TP values; the sixth slot is the runner."""
    return list((take_profits or [])[:TP_SLOT_COUNT])


def _start_break_even_monitor(symbol, side, first_trigger_price):
    """Move all open positions for the symbol to exact break-even once TP1 is hit."""

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
                    break

            time.sleep(2)

    Thread(target=_monitor, daemon=True).start()


def _compute_safe_lot(desired_lot, symbol_info, margin_per_lot, balance):
    acc = mt5.account_info()
    if acc is None:
        log_event("MT5 account_info() unavailable while placing order.")
        return 0.0

    free_margin = acc.margin_free
    if free_margin is None:
        free_margin = balance

    lot = max(0.0, desired_lot)

    if margin_per_lot > 0 and free_margin > 0:
        lot = min(lot, free_margin / margin_per_lot)

    lot = _clamp_volume_to_symbol(lot, symbol_info)

    if lot <= 0 or lot < (symbol_info.volume_min or 0.0):
        log_event(f"Not enough free margin to open even minimum volume for {symbol_info.name}.")
        return 0.0

    return lot


def _prepare_symbol_and_account(symbol):
    balance = get_account_balance()
    account_info = mt5.account_info()
    if account_info is None:
        if not initialize_mt5():
            log_event("MT5 account_info() unavailable and MT5 init failed.")
            return None, None, None
        account_info = mt5.account_info()
        if account_info is None:
            log_event("MT5 account_info() still unavailable after init.")
            return None, None, None

    entry_price = get_symbol_price(symbol)
    if entry_price is None:
        log_event(f"Cannot get current market price for {symbol}.")
        return None, None, None

    symbol_info = mt5.symbol_info(symbol)
    if not symbol_info:
        log_event(f"Symbol info not found for {symbol}.")
        return None, None, None

    if not symbol_info.visible:
        mt5.symbol_select(symbol, True)
        symbol_info = mt5.symbol_info(symbol)
        if not symbol_info or not symbol_info.visible:
            log_event(f"Symbol {symbol} not visible/selected.")
            return None, None, None

    return balance, entry_price, symbol_info


def _margin_per_lot(symbol, entry_price, symbol_info, side):
    action = _get_side_order_type(side)
    margin_for_one = mt5.order_calc_margin(action, symbol, 1.0, entry_price)
    if margin_for_one is not None and margin_for_one > 0:
        return margin_for_one
    return symbol_info.margin_initial or 0.0


def execute_pre_signal_trade(quick_signal):
    """Open six positions with fixed 5 USD SL and no TP."""
    global PENDING_PRE_SIGNAL

    symbol = (quick_signal or {}).get("symbol") or SYMBOL_DEFAULT
    side = str((quick_signal or {}).get("side", "")).lower()

    if side not in {"buy", "sell"}:
        log_event(f"Invalid pre-signal side: {quick_signal}")
        return

    prep = _prepare_symbol_and_account(symbol)
    if prep[0] is None:
        return
    balance, entry_price, symbol_info = prep

    stop_loss = _fixed_stop_loss(entry_price, side)
    base_lot = calculate_lot_size(balance, entry_price, stop_loss, symbol)
    if base_lot <= 0:
        log_event(f"Pre-signal lot size <= 0 for {symbol}. Aborting.")
        return

    per_position_lot = base_lot / TOTAL_POSITIONS
    margin_per_lot = _margin_per_lot(symbol, entry_price, symbol_info, side)

    log_event(
        f"Pre-signal open for {symbol} {side}: base_lot={base_lot}, "
        f"positions={TOTAL_POSITIONS}, per_position_lot={per_position_lot:.4f}, sl={stop_loss}"
    )

    position_type = _get_position_type_for_side(side)
    before = mt5.positions_get(symbol=symbol) or []
    before_tickets = {p.ticket for p in before if p.type == position_type}

    opened_any = False
    for _ in range(TOTAL_POSITIONS):
        safe_lot = _compute_safe_lot(per_position_lot, symbol_info, margin_per_lot, balance)
        if safe_lot <= 0:
            break

        result = open_position(symbol, side.upper(), safe_lot, stop_loss, None)
        if result is not None:
            opened_any = True

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

    symbol = signal_data.get("symbol")
    side = str(signal_data.get("side", "")).lower()
    take_profits = _selected_take_profits(signal_data.get("take_profits") or [])

    if not symbol or side not in {"buy", "sell"}:
        return False

    if not take_profits:
        return False

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

    log_event(
        f"Applying main signal to existing positions for {symbol} {side}: "
        f"count={len(side_positions)}, fixed_sl_distance={FIXED_STOP_LOSS_DISTANCE}, tps={take_profits}"
    )

    for idx, pos in enumerate(side_positions[:TOTAL_POSITIONS]):
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

    _start_break_even_monitor(symbol, side, first_tp)
    PENDING_PRE_SIGNAL = {"symbol": None, "side": None, "tickets": [], "created_at": 0.0}
    return True


def execute_trade(signal_data):
    """Open exactly six positions: five TP trades and one runner, all with fixed 5 USD SL."""
    symbol = signal_data.get("symbol")
    side = str(signal_data.get("side", "")).lower()
    take_profits = _selected_take_profits(signal_data.get("take_profits") or [])

    if not symbol or side not in {"buy", "sell"}:
        log_event(f"Invalid trade signal: {signal_data}")
        return

    if not take_profits:
        log_event(f"No usable take-profit levels provided for {symbol}. Aborting trade.")
        return

    prep = _prepare_symbol_and_account(symbol)
    if prep[0] is None:
        return
    balance, entry_price, symbol_info = prep

    stop_loss = _fixed_stop_loss(entry_price, side)
    base_lot = calculate_lot_size(balance, entry_price, stop_loss, symbol)
    if base_lot <= 0:
        log_event(f"Calculated lot size <= 0 for {symbol}. Aborting trade.")
        return

    per_position_lot = base_lot / TOTAL_POSITIONS
    margin_per_lot = _margin_per_lot(symbol, entry_price, symbol_info, side)
    first_tp = take_profits[0]

    log_event(
        f"Executing trade {symbol} {side}: total_lot={base_lot}, positions={TOTAL_POSITIONS}, "
        f"used_tps={take_profits}, runner_enabled={RUNNER_ENABLED}, fixed_sl={stop_loss}"
    )

    opened_any = False

    for tp in take_profits:
        safe_lot = _compute_safe_lot(per_position_lot, symbol_info, margin_per_lot, balance)
        if safe_lot <= 0:
            break

        log_event(
            f"Opening TP position for {symbol} {side} lot={safe_lot:.4f} TP={tp} SL={stop_loss}"
        )
        result = open_position(symbol, side.upper(), safe_lot, stop_loss, tp)
        if result is not None:
            opened_any = True

    runner_should_open = RUNNER_ENABLED and TOTAL_POSITIONS > len(take_profits)
    if runner_should_open:
        runner_lot = _compute_safe_lot(per_position_lot, symbol_info, margin_per_lot, balance)
        if runner_lot > 0:
            log_event(
                f"Opening runner position for {symbol} {side} lot={runner_lot:.4f} SL={stop_loss}"
            )
            runner_result = open_position(symbol, side.upper(), runner_lot, stop_loss, None)
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

    if not opened_any:
        log_event(f"No positions opened for {symbol} due to risk/margin constraints.")
        return

    _start_break_even_monitor(symbol, side, first_tp)

