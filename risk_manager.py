### risk_manager.py
from config import MAX_ACCOUNT_RISK
import MetaTrader5 as mt5


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
            # If there's no SL, we can't cap risk accurately. Treat as "already at max".
            return float("inf")

        volume = pos.volume
        entry = pos.price_open

        # Use MT5's own profit calculator for exact SL loss.
        if pos.type == mt5.POSITION_TYPE_BUY:
            action = mt5.ORDER_TYPE_BUY
        else:
            action = mt5.ORDER_TYPE_SELL

        loss_at_sl = mt5.order_calc_profit(action, pos.symbol, volume, entry, sl)
        if loss_at_sl is None:
            # If MT5 can't calculate, be conservative.
            return float("inf")

        total_risk += abs(loss_at_sl)

    return total_risk


def calculate_lot_size(account_balance, entry_price, stop_loss_price, symbol="XAUUSD"):
    """
    Calculate lot size so that the TOTAL risk across all open trades plus this one
    does not exceed MAX_ACCOUNT_RISK * account_balance.
    """
    if stop_loss_price == entry_price:
        return 0.0

    # Global risk budget in account currency
    max_global_risk = account_balance * MAX_ACCOUNT_RISK

    # Estimated risk of all currently open positions
    current_risk = _estimate_current_risk()
    if current_risk == float("inf"):
        return 0.0

    remaining_risk_budget = max_global_risk - current_risk
    if remaining_risk_budget <= 0:
        # Already at or above global risk limit
        return 0.0

    # Stop-loss distance in price
    sl_distance = abs(entry_price - stop_loss_price)
    if sl_distance <= 0:
        return 0.0

    # Use MT5 profit calculator: loss for 1.0 lot if SL hits
    action = mt5.ORDER_TYPE_BUY if stop_loss_price < entry_price else mt5.ORDER_TYPE_SELL
    loss_per_lot = mt5.order_calc_profit(action, symbol, 1.0, entry_price, stop_loss_price)
    if loss_per_lot is None or loss_per_lot == 0:
        return 0.0

    loss_per_lot = abs(loss_per_lot)
    lot = remaining_risk_budget / loss_per_lot

    # Margin sanity cap (best-effort)
    symbol_info = mt5.symbol_info(symbol)
    margin_per_lot = (symbol_info.margin_initial if symbol_info else None) or account_balance
    max_lot_by_margin = account_balance / margin_per_lot if margin_per_lot > 0 else lot
    lot = min(lot, max_lot_by_margin)

    # Ensure lot does not exceed margin or become negative
    lot = max(0.0, lot)

    # Round to 2 decimal places for typical FX symbols
    lot = round(lot, 2)

    return lot