import asyncio

from database import initialize_database
from mt5_connector import initialize_mt5
from telegram_listener import start_listener
from logger import log_event


def main():
    """
    Main entry point for the Telegram Copy Trader system.

    Responsibilities:
    1. Initialize the SQLite database.
    2. Initialize MetaTrader 5 connection.
    3. Start the Telegram listener.
    """

    # Step 1 — Initialize database
    try:
        initialize_database()
        log_event("Database initialized successfully")
    except Exception as e:
        log_event(f"Database initialization error: {e}")
        return

    # Step 2 — Initialize MetaTrader 5
    try:
        mt5_status = initialize_mt5()
        if not mt5_status:
            log_event("MT5 initialization failed. Exiting program.")
            return
    except Exception as e:
        log_event(f"MT5 connection error: {e}")
        return

    # Step 3 — Start Telegram Listener
    try:
        log_event("Telegram Copy Trader Started")
        asyncio.run(start_listener())
    except KeyboardInterrupt:
        log_event("System stopped by user.")
    except Exception as e:
        log_event(f"Fatal error in main loop: {e}")


if __name__ == "__main__":
    main()








 





















