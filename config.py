# Configuration file for Telegram Copy Trader

TELEGRAM_API_ID = 33485084
TELEGRAM_API_HASH = "25e730e1cb2e6665f22837ef9fff1c06"
TELEGRAM_CHANNEL = "https://t.me/+pLsvUIjzAx81YmU1"

# Trade structure settings
TOTAL_POSITIONS = 6
FIXED_STOP_LOSS_DISTANCE = 5.0

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
