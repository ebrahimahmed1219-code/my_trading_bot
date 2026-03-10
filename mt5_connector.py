import MetaTrader5 as mt5
from logger import log_event


def initialize_mt5():
    """Initialize MT5 connection"""
    if not mt5.initialize():
        log_event("MT5 initialization failed")
        return False
    log_event("MT5 initialized successfully")
    return True


def get_account_balance():
    account = mt5.account_info()
    if account:
        return account.balance
    return 0


def get_symbol_price(symbol):
    tick = mt5.symbol_info_tick(symbol)
    if tick:
        return tick.ask
    return None


def open_position(symbol, side, lot, sl, tp=None):
    """Open MT5 trade"""
    price = get_symbol_price(symbol)
    normalized_side = str(side).upper()
    order_type = mt5.ORDER_TYPE_BUY if normalized_side == "BUY" else mt5.ORDER_TYPE_SELL

    # MT5 rejects None as a tp/sl value - use 0.0 to mean "no level set"
    tp_value = float(tp) if tp else 0.0
    sl_value = float(sl) if sl else 0.0

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot,
        "type": order_type,
        "price": price,
        "sl": sl_value,
        "tp": tp_value,
        "deviation": 20,
        "magic": 123456,
        "comment": "telegram_copy_trade",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    if result is None:
        err = mt5.last_error()
        log_event(f"Order failed (None result) for {symbol} {normalized_side}: last_error={err}")
        return None
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        log_event(f"Order failed for {symbol} {normalized_side}: {result}")
    else:
        log_event(f"Order executed {symbol} {normalized_side}")
    return result


def modify_position_targets(ticket, new_sl=None, new_tp=None, comment="update_targets"):
    """Modify SL/TP for an existing position ticket."""
    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        log_event(f"modify_position_targets: no position found for ticket {ticket}")
        return None

    pos = positions[0]
    sl_value = pos.sl if new_sl is None else float(new_sl)
    tp_value = pos.tp if new_tp is None else float(new_tp)

    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "symbol": pos.symbol,
        "sl": sl_value,
        "tp": tp_value,
        "magic": 123456,
        "comment": comment,
    }

    result = mt5.order_send(request)
    log_event(f"Modified targets for {ticket}: sl={sl_value}, tp={tp_value}, result={result}")
    return result


def modify_stop_loss(ticket, new_sl):
    return modify_position_targets(ticket, new_sl=new_sl, new_tp=None, comment="move_to_break_even")


def close_position(ticket):
    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        return
    position = positions[0]
    order_type = mt5.ORDER_TYPE_SELL if position.type == 0 else mt5.ORDER_TYPE_BUY
    price = get_symbol_price(position.symbol)
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "position": ticket,
        "symbol": position.symbol,
        "volume": position.volume,
        "type": order_type,
        "price": price,
        "deviation": 20,
        "magic": 123456,
    }
    mt5.order_send(request)


def get_open_positions():
    return mt5.positions_get()
