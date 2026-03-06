import tkinter as tk
from tkinter import scrolledtext
from threading import Thread
import asyncio

from config import SYMBOL_DEFAULT, MAX_ACCOUNT_RISK, TOTAL_POSITIONS
from telegram_listener import start_listener  # async Telegram listener
from mt5_connector import get_account_balance, initialize_mt5
from trade_engine import set_runner_enabled
from logger import log_event


class CopyTraderUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Telegram Copy Trader")
        self.root.geometry("750x520")

        # Account Info
        self.balance_label = tk.Label(root, text="Account Balance: $0.00", font=("Arial", 12))
        self.balance_label.pack(pady=5)

        self.refresh_balance_button = tk.Button(root, text="Refresh Balance", command=self.refresh_balance)
        self.refresh_balance_button.pack(pady=5)

        # Risk Settings
        self.risk_label = tk.Label(
            root,
            text=f"Risk: {MAX_ACCOUNT_RISK*100:.1f}% per {TOTAL_POSITIONS} trades",
            font=("Arial", 10),
        )
        self.risk_label.pack(pady=5)

        # Symbol (display / manual override placeholder)
        symbol_frame = tk.Frame(root)
        symbol_frame.pack(pady=5)
        tk.Label(symbol_frame, text="Symbol:", font=("Arial", 10)).pack(side=tk.LEFT, padx=(0, 5))
        self.symbol_var = tk.StringVar(value=SYMBOL_DEFAULT)
        self.symbol_entry = tk.Entry(symbol_frame, textvariable=self.symbol_var, width=10, font=("Arial", 12))
        self.symbol_entry.pack(side=tk.LEFT)

        # Runner (no TP) toggle
        runner_frame = tk.Frame(root)
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
        self.status_label = tk.Label(root, text="Status: Stopped", fg="red", font=("Arial", 10, "bold"))
        self.status_label.pack(pady=5)

        # Start/Stop Buttons
        controls_frame = tk.Frame(root)
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
        self.log_box = scrolledtext.ScrolledText(root, width=90, height=20, state="disabled")
        self.log_box.pack(pady=10)

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

    def refresh_balance(self):
        try:
            balance = get_account_balance()
        except Exception as e:
            self.append_log(f"Error fetching balance: {e}")
            return
        self.balance_label.config(text=f"Account Balance: ${balance:.2f}")

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
            # Ensure task is cancelled/cleaned up on loop stop
            if not task.done():
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
            # Reflect stopped state in UI
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
                # Stop the event loop safely from the main thread
                self.loop.call_soon_threadsafe(self.loop.stop)
            except Exception as e:
                self.append_log(f"Error stopping listener loop: {e}")

        self.append_log("Stopping listener...")
        self.status_label.config(text="Status: Stopping...", fg="orange")

    def update_ui(self):
        # Periodic lightweight updates
        self.refresh_balance()
        if self.running:
            self.status_label.config(text="Status: Running", fg="green")
        self.root.after(5000, self.update_ui)  # refresh every 5s


if __name__ == "__main__":
    root = tk.Tk()
    app = CopyTraderUI(root)
    root.mainloop()