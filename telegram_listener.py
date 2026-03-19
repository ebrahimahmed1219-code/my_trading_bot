import asyncio

from telethon import TelegramClient, events
from telethon.errors.common import TypeNotFoundError

from config import (
    FORWARD_SIGNALS_ENABLED,
    FORWARD_TELEGRAM_CHANNEL,
    TELEGRAM_API_ID,
    TELEGRAM_API_HASH,
    TELEGRAM_CHANNEL,
    TELEGRAM_SESSION_NAME,
)
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

RECONNECT_DELAY_SECONDS = 5

# Create Telegram client
client = TelegramClient(TELEGRAM_SESSION_NAME, TELEGRAM_API_ID, TELEGRAM_API_HASH)
_listener_registered = False
_relay_entity = None


async def get_channel_entity():
    """Resolve the configured Telegram channel entity."""
    try:
        entity = await client.get_entity(TELEGRAM_CHANNEL)
        log_event(f"Connected to channel: {entity.title}")
        return entity
    except Exception as e:
        log_event(f"Error resolving channel: {e}")
        return None


async def relay_signal_message(message_text, message_type):
    """Forward actionable incoming Telegram messages to a configured destination channel."""
    global _relay_entity

    if not FORWARD_SIGNALS_ENABLED or not FORWARD_TELEGRAM_CHANNEL:
        return

    if message_type == "IGNORE":
        return

    if not (message_text or "").strip():
        return

    try:
        if _relay_entity is None:
            _relay_entity = await client.get_entity(FORWARD_TELEGRAM_CHANNEL)
            log_event(f"Relay channel connected: {getattr(_relay_entity, 'title', FORWARD_TELEGRAM_CHANNEL)}")

        await client.send_message(_relay_entity, message_text)
        log_event(f"Forwarded {message_type} message to relay channel")
    except Exception as e:
        log_event(f"Relay forward failed: {e}")


async def new_message_listener(event):
    """Handle incoming Telegram messages."""
    message_id = event.id
    message_text = event.raw_text

    if message_exists(message_id):
        return

    log_event(f"New message received: {message_text}")

    message_type = classify_message(message_text)
    await relay_signal_message(message_text, message_type)

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


async def start_listener():
    """Run the Telegram listener and reconnect automatically on disconnect."""
    global _listener_registered

    while True:
        try:
            await client.start()
            channel = await get_channel_entity()
            if not channel:
                log_event(
                    f"Cannot start listener without valid channel. Retrying in {RECONNECT_DELAY_SECONDS}s."
                )
                await asyncio.sleep(RECONNECT_DELAY_SECONDS)
                continue

            if not _listener_registered:
                @client.on(events.NewMessage(chats=channel))
                async def handler(event):
                    await new_message_listener(event)

                _listener_registered = True
                log_event("Telegram listener handler registered")

            log_event("Telegram listener started")
            await client.run_until_disconnected()
            log_event(
                f"Telegram listener disconnected. Reconnecting in {RECONNECT_DELAY_SECONDS}s."
            )

        except TypeNotFoundError as e:
            log_event(
                "Telegram session decode error. Stop every other bot using this Telegram session, "
                f"delete {TELEGRAM_SESSION_NAME}.session, sign in again, and then restart the bot. "
                f"Details: {e}"
            )
            try:
                await client.disconnect()
            except Exception:
                pass

        except Exception as e:
            log_event(f"Telegram listener error: {e}. Reconnecting in {RECONNECT_DELAY_SECONDS}s.")

        await asyncio.sleep(RECONNECT_DELAY_SECONDS)
