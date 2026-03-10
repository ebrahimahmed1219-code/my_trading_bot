from telethon import TelegramClient, events

from config import TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_CHANNEL
from database import message_exists, store_message
from logger import log_event
from position_manager import close_all_positions, move_all_to_break_even
from signal_classifier import classify_message
from signal_parser import parse_quick_direction_signal, parse_trade_signal
from trade_engine import (
    apply_signal_to_existing_positions,
    execute_pre_signal_trade,
    execute_trade,
)

# Create Telegram client
client = TelegramClient("session", TELEGRAM_API_ID, TELEGRAM_API_HASH)


# Resolve the invite link (for private channels)
async def get_channel_entity():
    await client.start()
    try:
        entity = await client.get_entity(TELEGRAM_CHANNEL)
        log_event(f"Connected to channel: {entity.title}")
        return entity
    except Exception as e:
        log_event(f"Error resolving channel: {e}")
        return None


# Listener function
async def new_message_listener(event):
    message_id = event.id
    message_text = event.raw_text

    if message_exists(message_id):
        return

    log_event(f"New message received: {message_text}")

    message_type = classify_message(message_text)

    if message_type == "PRE_TRADE":
        quick_signal = parse_quick_direction_signal(message_text)
        if quick_signal:
            execute_pre_signal_trade(quick_signal)

    elif message_type == "NEW_TRADE":
        signal = parse_trade_signal(message_text)
        if signal:
            edited_existing = apply_signal_to_existing_positions(signal)
            if not edited_existing:
                execute_trade(signal)

    elif message_type == "MOVE_SL":
        move_all_to_break_even()

    elif message_type == "CLOSE_ALL":
        close_all_positions()

    store_message(message_id, message_text)


# Start listener
async def start_listener():
    channel = await get_channel_entity()
    if not channel:
        log_event("Cannot start listener without valid channel")
        return

    @client.on(events.NewMessage(chats=channel))
    async def handler(event):
        await new_message_listener(event)

    log_event("Telegram listener started")
    await client.run_until_disconnected()
