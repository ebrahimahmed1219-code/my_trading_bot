import MetaTrader5 as mt5


DEFAULT_REENTRY_RISK_RATIO = 0.40


def _risk_ratio_for_balance(account_balance):
    """Return the configured risk ratio for the current account balance."""
    if account_balance < 101:
        return 0.60
    if account_balance < 200:
        return 0.75
    if account_balance < 500:
        return 0.80
    if account_balance <= 800:
        return 0.80
    return 0.90


def _estimate_current_risk():
    """
    Approximate total monetary risk of all open positions based on their SL and entry.
    Risk is measured in account currency.
    """
    positions = mt5.positions_get()
    if not positions:
        return 0.0

    total_risk = 0.0
    for pos in positions:
        sl = getattr(pos, "sl", None)
        if not sl or sl == 0:
            return float("inf")

        volume = pos.volume
        entry = pos.price_open
        action = mt5.ORDER_TYPE_BUY if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_SELL

        loss_at_sl = mt5.order_calc_profit(action, pos.symbol, volume, entry, sl)
        if loss_at_sl is None:
            return float("inf")

        total_risk += abs(loss_at_sl)

    return total_risk


def calculate_lot_size(account_balance, entry_price, stop_loss_price, symbol="XAUUSD", risk_ratio_override=None):
    """
    Calculate total lot size so that combined open-trade risk plus this new trade
    stays within the configured account-balance tier risk.
    """
    if stop_loss_price == entry_price:
        return 0.0

    risk_ratio = risk_ratio_override if risk_ratio_override is not None else _risk_ratio_for_balance(account_balance)
    max_global_risk = account_balance * risk_ratio

    current_risk = _estimate_current_risk()
    if current_risk == float("inf"):
        return 0.0

    remaining_risk_budget = max_global_risk - current_risk
    if remaining_risk_budget <= 0:
        return 0.0

    action = mt5.ORDER_TYPE_BUY if stop_loss_price < entry_price else mt5.ORDER_TYPE_SELL
    loss_per_lot = mt5.order_calc_profit(action, symbol, 1.0, entry_price, stop_loss_price)
    if loss_per_lot is None or loss_per_lot == 0:
        return 0.0

    lot = remaining_risk_budget / abs(loss_per_lot)

    symbol_info = mt5.symbol_info(symbol)
    margin_per_lot = (symbol_info.margin_initial if symbol_info else None) or account_balance
    max_lot_by_margin = account_balance / margin_per_lot if margin_per_lot > 0 else lot
    lot = min(lot, max_lot_by_margin)

    return round(max(0.0, lot), 2)
