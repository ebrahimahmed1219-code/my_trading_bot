# Configuration file for Telegram Copy Trader

TELEGRAM_API_ID = 39218518
TELEGRAM_API_HASH = "8a211e5ed518571438958ff2bf93f209"
TELEGRAM_CHANNEL = -1003869970651
TELEGRAM_SESSION_NAME = "telegram_session"
FORWARD_SIGNALS_ENABLED = False
FORWARD_TELEGRAM_CHANNEL = ""

# Trade structure settings
TOTAL_POSITIONS = 6
FIXED_STOP_LOSS_DISTANCE = 10.0

# Pre-signal behavior
# If message is a short standalone direction (e.g. "Sell gold"),
# open TOTAL_POSITIONS with SL only using this fixed distance.
PRE_SIGNAL_SL_DISTANCE = FIXED_STOP_LOSS_DISTANCE

# Database
DATABASE_FILE = "trades.db"

# Logging
LOG_FOLDER = "logs"

# Default trading symbol
SYMBOL_DEFAULT = "XAUUSD"
