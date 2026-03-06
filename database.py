import sqlite3
from config import DATABASE_FILE

def initialize_database():
    """Create database table if not exists"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_message_id INTEGER UNIQUE,
        text TEXT,
        timestamp TEXT
    )
    """)

    conn.commit()
    conn.close()

def store_message(message_id, text):
    """Store processed Telegram message"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()

    cursor.execute(
        "INSERT OR IGNORE INTO messages (telegram_message_id, text, timestamp) VALUES (?, ?, datetime('now'))",
        (message_id, text)
    )

    conn.commit()
    conn.close()

def message_exists(message_id):
    """Check if message already processed"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT 1 FROM messages WHERE telegram_message_id=?",
        (message_id,)
    )

    result = cursor.fetchone()
    conn.close()

    return result is not None