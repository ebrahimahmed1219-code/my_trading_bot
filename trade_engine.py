### trade_engine.py
from mt5_connector import open_position, get_account_balance, get_symbol_price, initialize_mt5
from risk_manager import calculate_lot_size
from logger import log_event
from position_manager import move_all_to_break_even
from threading import Thread
import time
import MetaTrader5 as mt5


# Global toggle for opening an extra "runner" position without TP
RUNNER_ENABLED = True


def set_runner_enabled(enabled: bool):
    """Enable/disable opening the extra runner position without TP."""
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

    # Clamp to allowed min/max
    volume = max(vol_min, min(volume, vol_max))

    # Align to step
    if vol_step > 0:
        steps = round((volume - vol_min) / vol_step)
        volume = vol_min + steps * vol_step

    return max(0.0, volume)


def _start_break_even_monitor(symbol, side, trigger_price):
    """
    Start a background monitor that moves all open positions'
    stop losses to break-even once price reaches the first TP level.
    """

    def _monitor():
        # Ensure MT5 is initialized and usable in this thread
        if mt5.account_info() is None:
            initialize_mt5()

        log_event(
            f"Break-even monitor started for {symbol} {side} at trigger {trigger_price}"
        )
        while True:
            # Stop if there are no more open positions for this symbol
            positions = mt5.positions_get(symbol=symbol)
            if not positions:
                log_event(
                    f"Break-even monitor for {symbol} stopped: no open positions."
                )
                break

            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                time.sleep(2)
                continue

            # For buys, TP/SL are hit on ASK; for sells, on BID.
            if side == "buy":
                price = tick.ask or tick.last
                if price is None:
                    time.sleep(2)
                    continue
                if price >= trigger_price:
                    log_event(
                        f"{symbol} ASK {price} reached first TP {trigger_price}. "
                        "Moving all SLs to break-even."
                    )
                    move_all_to_break_even()
                    break

            else:  # sell
                price = tick.bid or tick.last
                if price is None:
                    time.sleep(2)
                    continue
                if price <= trigger_price:
                    log_event(
                        f"{symbol} BID {price} reached first TP {trigger_price}. "
                        "Moving all SLs to break-even."
                    )
                    move_all_to_break_even()
                    break

            time.sleep(2)

    Thread(target=_monitor, daemon=True).start()


def execute_trade(signal_data):
    """
    Execute parsed trade signal from Telegram.
    - Calculates precise lot size based on account balance/equity and stop-loss.
    - Splits total risk across all TPs plus a runner.
    - Uses MT5 margin and symbol constraints to avoid 'No money' / invalid volume errors.
    """
    symbol = signal_data.get("symbol")
    side = str(signal_data.get("side", "")).lower()
    stop_loss = signal_data.get("stop_loss")
    take_profits = signal_data.get("take_profits") or []

    if not symbol or side not in {"buy", "sell"}:
        log_event(f"Invalid trade signal: {signal_data}")
        return

    if not isinstance(take_profits, (list, tuple)) or len(take_profits) == 0:
        log_event(f"No take-profit levels provided for {symbol}. Aborting trade.")
        return

    # Ensure MT5 is initialized and account info is available
    balance = get_account_balance()
    account_info = mt5.account_info()
    if account_info is None:
        # Try to initialize MT5 once more
        if not initialize_mt5():
            log_event("MT5 account_info() unavailable and MT5 init failed. Aborting trade.")
            return
        account_info = mt5.account_info()
        if account_info is None:
            log_event("MT5 account_info() still unavailable after init. Aborting trade.")
            return

    # Current market price
    entry_price = get_symbol_price(symbol)

    # Symbol info & tradability
    symbol_info = mt5.symbol_info(symbol)
    if not symbol_info:
        log_event(f"Symbol info not found for {symbol}. Aborting trade.")
        return

    if not symbol_info.visible:
        mt5.symbol_select(symbol, True)
        symbol_info = mt5.symbol_info(symbol)
        if not symbol_info or not symbol_info.visible:
            log_event(f"Symbol {symbol} not visible/selected. Aborting trade.")
            return

    # Determine MT5 order action
    if side == "buy":
        action = mt5.ORDER_TYPE_BUY
    else:
        action = mt5.ORDER_TYPE_SELL

    # Margin per 1 lot (or best-effort fallback)
    margin_per_lot = 0.0
    margin_for_one = mt5.order_calc_margin(action, symbol, 1.0, entry_price)
    if margin_for_one is not None and margin_for_one > 0:
        margin_per_lot = margin_for_one
    elif symbol_info.margin_initial:
        margin_per_lot = symbol_info.margin_initial

    # Base lot size using risk manager (total risk across all positions)
    base_lot = calculate_lot_size(balance, entry_price, stop_loss, symbol)
    if base_lot <= 0:
        log_event(f"Calculated lot size <= 0 for {symbol}. Aborting trade.")
        return

    # Decide how many positions to open: one per TP, plus optional runner
    runner_enabled = RUNNER_ENABLED
    log_event(
        f"execute_trade: runner_enabled={runner_enabled} for {symbol} {side}, "
        f"TPs={take_profits}"
    )
    positions_count = len(take_profits) + (1 if runner_enabled else 0)
    if positions_count <= 0:
        log_event(f"No positions to open for {symbol} (no TPs and runner disabled).")
        return
    per_position_lot = base_lot / positions_count

    if len(take_profits) < 3:
        if runner_enabled:
            extra = "TP positions plus one runner."
        else:
            extra = "TP positions only (runner disabled)."
        log_event(
            f"{symbol} signal has only {len(take_profits)} TP levels. Will still open {extra}"
        )

    log_event(
        f"Executing trade {symbol} {side} with total lot {base_lot}, "
        f"{positions_count} positions (~{per_position_lot:.4f} each), "
        f"runner_enabled={runner_enabled}"
    )

    opened_any = False

    # Use the FIRST TP line as the break-even trigger:
    # - For BUY: lowest TP
    # - For SELL: highest TP
    if side == "buy":
        first_tp = min(take_profits)
    else:
        first_tp = max(take_profits)

    log_event(
        f"First TP used for break-even on {symbol} {side}: entry={entry_price}, "
        f"first_tp={first_tp}, all_tps={take_profits}"
    )

    # Helper to compute safe lot for current free margin and symbol constraints
    def _compute_safe_lot(desired_lot):
        nonlocal margin_per_lot

        # Refresh account margin before each order
        acc = mt5.account_info()
        if acc is None:
            log_event("MT5 account_info() unavailable while placing order.")
            return 0.0

        free_margin = acc.margin_free
        if free_margin is None:
            free_margin = balance

        lot = max(0.0, desired_lot)

        if margin_per_lot > 0 and free_margin > 0:
            max_lot_by_margin = free_margin / margin_per_lot
            lot = min(lot, max_lot_by_margin)

        lot = _clamp_volume_to_symbol(lot, symbol_info)

        if lot <= 0 or lot < (symbol_info.volume_min or 0.0):
            log_event(
                f"Not enough free margin to open even minimum volume for {symbol}."
            )
            return 0.0

        return lot

    # Open one position per TP
    for tp in take_profits:
        safe_lot = _compute_safe_lot(per_position_lot)
        if safe_lot <= 0:
            break

        log_event(
            f"Opening TP position for {symbol} {side} "
            f"lot={safe_lot:.4f} TP={tp} SL={stop_loss}"
        )
        # MT5 connector expects side as BUY/SELL
        open_position(symbol, side.upper(), safe_lot, stop_loss, tp)
        opened_any = True

    # Open final runner position without TP (if enabled and margin still allows)
    if runner_enabled:
        runner_lot = _compute_safe_lot(per_position_lot)
        if runner_lot > 0:
            log_event(
                f"Opening runner position for {symbol} {side} "
                f"lot={runner_lot:.4f} SL={stop_loss}"
            )
            runner_result = open_position(symbol, side.upper(), runner_lot, stop_loss, None)
            if runner_result is not None and getattr(runner_result, "retcode", None) == mt5.TRADE_RETCODE_DONE:
                opened_any = True

    if not opened_any:
        log_event(f"No positions opened for {symbol} due to risk/margin constraints.")
    else:
        # Start background monitor to move SL to break-even when first TP is hit
        _start_break_even_monitor(symbol, side, first_tp)