import tkinter as tk
from tkinter import scrolledtext, ttk
from threading import Thread
import asyncio

from config import FORWARD_SIGNALS_ENABLED, FORWARD_TELEGRAM_CHANNEL, SYMBOL_DEFAULT, TOTAL_POSITIONS
from telegram_listener import start_listener  # async Telegram listener
from mt5_connector import get_account_balance, initialize_mt5
from risk_manager import _risk_ratio_for_balance
from trade_engine import set_runner_enabled
from logger import log_event


class CopyTraderUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Telegram Copy Trader")
        self.root.geometry("820x600")

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True)

        self.main_page = ttk.Frame(self.notebook)
        self.relay_page = ttk.Frame(self.notebook)
        self.notebook.add(self.main_page, text="Trading")
        self.notebook.add(self.relay_page, text="Relay")

        # Account Info
        self.balance_label = tk.Label(self.main_page, text="Account Balance: $0.00", font=("Arial", 12))
        self.balance_label.pack(pady=5)

        self.refresh_balance_button = tk.Button(self.main_page, text="Refresh Balance", command=self.refresh_balance)
        self.refresh_balance_button.pack(pady=5)

        # Risk Settings
        self.risk_label = tk.Label(
            self.main_page,
            text=self._risk_text(),
            font=("Arial", 10),
        )
        self.risk_label.pack(pady=5)

        # Symbol (display / manual override placeholder)
        symbol_frame = tk.Frame(self.main_page)
        symbol_frame.pack(pady=5)
        tk.Label(symbol_frame, text="Symbol:", font=("Arial", 10)).pack(side=tk.LEFT, padx=(0, 5))
        self.symbol_var = tk.StringVar(value=SYMBOL_DEFAULT)
        self.symbol_entry = tk.Entry(symbol_frame, textvariable=self.symbol_var, width=10, font=("Arial", 12))
        self.symbol_entry.pack(side=tk.LEFT)

        # Runner (no TP) toggle
        runner_frame = tk.Frame(self.main_page)
        runner_frame.pack(pady=2)
        self.runner_enabled_var = tk.BooleanVar(value=True)
        self.runner_check = tk.Checkbutton(
            runner_frame,
            text="Enable runner (no TP)",
            variable=self.runner_enabled_var,
            command=self.on_runner_toggle,
        )
        self.runner_check.pack(side=tk.LEFT)

        # Listener status
        self.status_label = tk.Label(self.main_page, text="Status: Stopped", fg="red", font=("Arial", 10, "bold"))
        self.status_label.pack(pady=5)

        # Start/Stop Buttons
        controls_frame = tk.Frame(self.main_page)
        controls_frame.pack(pady=5)

        self.start_button = tk.Button(
            controls_frame,
            text="Start Listener",
            bg="green",
            fg="white",
            width=15,
            command=self.start_listener_thread,
        )
        self.start_button.pack(side=tk.LEFT, padx=5)

        self.stop_button = tk.Button(
            controls_frame,
            text="Stop Listener",
            bg="red",
            fg="white",
            width=15,
            command=self.stop_listener,
            state=tk.DISABLED,
        )
        self.stop_button.pack(side=tk.LEFT, padx=5)

        self.clear_log_button = tk.Button(
            controls_frame,
            text="Clear Logs",
            width=10,
            command=self.clear_logs,
        )
        self.clear_log_button.pack(side=tk.LEFT, padx=5)

        # Logs
        self.log_box = scrolledtext.ScrolledText(self.main_page, width=96, height=20, state="disabled")
        self.log_box.pack(pady=10)

        # Relay Page
        relay_title = tk.Label(self.relay_page, text="Signal Relay", font=("Arial", 14, "bold"))
        relay_title.pack(pady=(20, 10))

        relay_text = (
            "Forward actionable incoming Telegram messages to your own channel as soon as they arrive.\n"
            "Configure the destination in config.py using FORWARD_SIGNALS_ENABLED and FORWARD_TELEGRAM_CHANNEL."
        )
        self.relay_info_label = tk.Label(
            self.relay_page,
            text=relay_text,
            font=("Arial", 10),
            justify="left",
            wraplength=700,
        )
        self.relay_info_label.pack(pady=5, padx=20, anchor="w")

        self.relay_status_label = tk.Label(self.relay_page, text="", font=("Arial", 11))
        self.relay_status_label.pack(pady=10, padx=20, anchor="w")

        self.relay_target_label = tk.Label(self.relay_page, text="", font=("Arial", 11))
        self.relay_target_label.pack(pady=5, padx=20, anchor="w")

        self.listener_thread = None
        self.loop = None
        self.running = False

        # Periodic UI updates
        self.root.after(1000, self.update_ui)

        # Initialize MT5 connection for balance/trading
        try:
            if not initialize_mt5():
                self.append_log("MT5 initialization failed. Check terminal and account settings.")
                self.status_label.config(text="Status: MT5 init failed", fg="red")
            else:
                self.append_log("MT5 initialized successfully.")
        except Exception as e:
            self.append_log(f"Error during MT5 initialization: {e}")

        # Ensure runner flag in engine matches UI default
        set_runner_enabled(self.runner_enabled_var.get())

    def _risk_text(self, balance=None):
        if balance is None:
            return (
                f"Risk tiers with {TOTAL_POSITIONS} trades: "
                "<101=50%, 101-<200=75%, 200-800=80%, >800=90%."
            )

        risk_percent = int(_risk_ratio_for_balance(balance) * 100)
        return f"Risk: {risk_percent}% across {TOTAL_POSITIONS} trades (balance used: ${balance:.2f})"

    def refresh_relay_status(self):
        status_text = "Relay Status: Enabled" if FORWARD_SIGNALS_ENABLED else "Relay Status: Disabled"
        target_text = (
            f"Relay Target: {FORWARD_TELEGRAM_CHANNEL}"
            if FORWARD_TELEGRAM_CHANNEL
            else "Relay Target: not configured"
        )
        self.relay_status_label.config(text=status_text, fg="green" if FORWARD_SIGNALS_ENABLED else "red")
        self.relay_target_label.config(text=target_text)

    def refresh_balance(self):
        try:
            balance = get_account_balance()
        except Exception as e:
            self.append_log(f"Error fetching balance: {e}")
            return
        self.balance_label.config(text=f"Account Balance: ${balance:.2f}")
        self.risk_label.config(text=self._risk_text(balance))

    def _append_log_to_widget(self, message):
        self.log_box.config(state="normal")
        self.log_box.insert(tk.END, message + "\n")
        self.log_box.yview(tk.END)
        self.log_box.config(state="disabled")

    def append_log(self, message):
        # Ensure UI updates happen on the main Tk thread
        self.root.after(0, self._append_log_to_widget, message)
        log_event(message)  # also log to file

    # Backwards-compatible alias
    def log(self, message):
        self.append_log(message)

    def clear_logs(self):
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", tk.END)
        self.log_box.config(state="disabled")

    def on_runner_toggle(self):
        enabled = self.runner_enabled_var.get()
        set_runner_enabled(enabled)
        state = "ON" if enabled else "OFF"
        self.append_log(f"Runner (no TP) toggle set to {state}.")

    def start_listener_thread(self):
        if self.listener_thread and self.listener_thread.is_alive():
            self.append_log("Listener already running.")
            return

        self.running = True
        self.status_label.config(text="Status: Starting...", fg="orange")
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)

        self.listener_thread = Thread(target=self.run_async_listener, daemon=True)
        self.listener_thread.start()
        self.append_log("Listener thread started.")

    def run_async_listener(self):
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

            async def runner():
                try:
                    await start_listener()
                except asyncio.CancelledError:
                    pass

            task = self.loop.create_task(runner())
            self.loop.run_forever()
            if task.done():
                try:
                    exc = task.exception()
                except asyncio.CancelledError:
                    exc = None
                if exc is not None:
                    self.append_log(f"Listener task stopped with error: {exc}")
            else:
                task.cancel()
                try:
                    self.loop.run_until_complete(task)
                except asyncio.CancelledError:
                    pass
        except Exception as e:
            self.append_log(f"Listener error: {e}")
        finally:
            if self.loop is not None:
                try:
                    self.loop.run_until_complete(self.loop.shutdown_asyncgens())
                except Exception:
                    pass
                self.loop.close()
                self.loop = None
            self.running = False
            self.root.after(0, self._on_listener_stopped)

    def _on_listener_stopped(self):
        self.status_label.config(text="Status: Stopped", fg="red")
        self.start_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)

    def stop_listener(self):
        if not self.listener_thread or not self.listener_thread.is_alive():
            self.append_log("Listener is not running.")
            return

        if self.loop is not None:
            try:
                self.loop.call_soon_threadsafe(self.loop.stop)
            except Exception as e:
                self.append_log(f"Error stopping listener loop: {e}")

        self.append_log("Stopping listener...")
        self.status_label.config(text="Status: Stopping...", fg="orange")

    def update_ui(self):
        self.refresh_balance()
        self.refresh_relay_status()
        if self.running:
            self.status_label.config(text="Status: Running", fg="green")
        self.root.after(5000, self.update_ui)


if __name__ == "__main__":
    root = tk.Tk()
    app = CopyTraderUI(root)
    root.mainloop()

