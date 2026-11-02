import os
import re
import json
import time
import threading
import webbrowser
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

import pandas as pd
from kiteconnect import KiteConnect

APP_DIR = Path(__file__).resolve().parent
CONFIG_FILE = APP_DIR / "config.json"
SESSION_FILE = APP_DIR / ".kite_session.json"
OUTPUT_DIR = APP_DIR / "output"
INSTRUMENT_CACHE = APP_DIR / "instruments.csv"

EXCHANGES = ["NSE", "BSE", "NFO", "BFO", "CDS", "MCX"]
INTERVALS = ["minute", "3minute", "5minute", "10minute",
             "15minute", "30minute", "60minute", "day"]
# Conservative request windows based on commonly reported Kite historical API limits.
BATCH_DAYS = {
    "minute": 55, "3minute": 90, "5minute": 90, "10minute": 90,
    "15minute": 180, "30minute": 180, "60minute": 360, "day": 1800
}
API_DELAY = 0.45


def safe_name(value):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_")[:100]


def subtract_years(d, years):
    try:
        return d.replace(year=d.year - years)
    except ValueError:
        return d.replace(year=d.year - years, month=2, day=28)


def subtract_months(d, months):
    """Subtract calendar months, clamping to the last valid day of the target month."""
    import calendar
    month_index = d.year * 12 + (d.month - 1) - months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def parse_date(value, label):
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except Exception:
        raise ValueError(f"{label} must use YYYY-MM-DD format.")


def load_json(path):
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_private_json(path, obj):
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


class UniversalKiteDownloader:
    def __init__(self, root):
        self.root = root
        root.title("Kite Connect | Historical Data Downloader")
        root.geometry("940x760")
        root.minsize(820, 650)
        self.busy = False
        self.instruments = pd.DataFrame()
        self.matches = []
        self.selected_instrument = None

        cfg = load_json(CONFIG_FILE)
        self.api_key = tk.StringVar(value=cfg.get("api_key", ""))
        self.api_secret = tk.StringVar(value=cfg.get("api_secret", ""))
        self.exchange = tk.StringVar(value="NSE")
        self.instrument_type = tk.StringVar(value="EQ")
        self.search = tk.StringVar()
        self.interval = tk.StringVar(value="5minute")
        self.duration = tk.StringVar(value="1 year")
        self.start_date = tk.StringVar(value="")
        self.end_date = tk.StringVar(value=date.today().isoformat())
        self.oi = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="Enter credentials, authenticate, then load instruments.")

        self.build_ui()
        self.search.trace_add("write", lambda *_: self.on_filter_changed())
        self.exchange.trace_add("write", lambda *_: self.on_filter_changed())
        self.instrument_type.trace_add("write", lambda *_: self.on_filter_changed())

    def build_ui(self):
        main = ttk.Frame(self.root, padding=14)
        main.pack(fill="both", expand=True)

        ttk.Label(main, text="Kite Connect Historical Data Downloader",
                  font=("Segoe UI", 17, "bold")).pack(anchor="w")
        ttk.Label(main, text="Choose an exchange, instrument, interval and historical period.",
                  font=("Segoe UI", 10)).pack(anchor="w", pady=(2, 12))

        auth = ttk.LabelFrame(main, text="1. Zerodha authentication", padding=10)
        auth.pack(fill="x", pady=(0, 10))
        ttk.Label(auth, text="API key").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(auth, textvariable=self.api_key, width=38).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Label(auth, text="API secret").grid(row=0, column=2, sticky="w", pady=4)
        ttk.Entry(auth, textvariable=self.api_secret, show="•", width=38).grid(row=0, column=3, sticky="ew", padx=8)
        ttk.Button(auth, text="Login / Generate access token", command=self.login).grid(
            row=1, column=0, columnspan=4, sticky="ew", pady=(8, 2))
        auth.columnconfigure(1, weight=1)
        auth.columnconfigure(3, weight=1)

        inst = ttk.LabelFrame(main, text="2. Select instrument", padding=10)
        inst.pack(fill="both", expand=True, pady=(0, 10))
        row = ttk.Frame(inst)
        row.pack(fill="x")
        ttk.Label(row, text="Exchange").pack(side="left")
        ttk.Combobox(row, textvariable=self.exchange, values=EXCHANGES,
                     state="readonly", width=10).pack(side="left", padx=(6, 16))
        ttk.Label(row, text="Type").pack(side="left")
        ttk.Combobox(row, textvariable=self.instrument_type,
                     values=["ALL", "EQ", "FUT", "CE", "PE"],
                     state="readonly", width=10).pack(side="left", padx=6)
        ttk.Button(row, text="Load / Refresh instruments", command=self.load_instruments).pack(side="right")

        ttk.Label(inst, text="Search symbol or company / contract name").pack(anchor="w", pady=(10, 3))
        ttk.Entry(inst, textvariable=self.search).pack(fill="x")
        self.results = tk.Listbox(inst, height=8, exportselection=False)
        self.results.pack(fill="both", expand=True, pady=(6, 4))
        self.results.bind("<<ListboxSelect>>", self.on_select)
        selection_row = ttk.Frame(inst)
        selection_row.pack(fill="x", pady=(4, 0))
        self.selected_label = ttk.Label(
            selection_row, text="No instrument selected",
            font=("Segoe UI", 9, "bold"), foreground="#9b3b32"
        )
        self.selected_label.pack(side="left", anchor="w")
        ttk.Button(selection_row, text="Clear selection",
                   command=self.clear_selection).pack(side="right")

        opts = ttk.LabelFrame(main, text="3. Historical data settings", padding=10)
        opts.pack(fill="x", pady=(0, 10))
        ttk.Label(opts, text="Interval").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Combobox(opts, textvariable=self.interval, values=INTERVALS,
                     state="readonly", width=14).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(opts, text="Duration").grid(row=0, column=2, sticky="w", pady=4)
        ttk.Combobox(opts, textvariable=self.duration,
                     values=["1 month", "3 months", "6 months", "1 year", "2 years",
                             "3 years", "5 years", "Custom dates"],
                     state="readonly", width=16).grid(row=0, column=3, sticky="w", padx=8)
        ttk.Label(opts, text="Start (YYYY-MM-DD)").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(opts, textvariable=self.start_date, width=17).grid(row=1, column=1, sticky="w", padx=8)
        ttk.Label(opts, text="End (YYYY-MM-DD)").grid(row=1, column=2, sticky="w", pady=4)
        ttk.Entry(opts, textvariable=self.end_date, width=17).grid(row=1, column=3, sticky="w", padx=8)
        ttk.Checkbutton(opts, text="Include open interest when available (F&O)",
                        variable=self.oi).grid(row=2, column=0, columnspan=4, sticky="w", pady=(5, 0))
        ttk.Label(opts, text="For preset durations, start/end are calculated automatically. For Custom dates, enter both dates.",
                  foreground="#555").grid(row=3, column=0, columnspan=4, sticky="w", pady=(5, 0))

        self.download_btn = ttk.Button(main, text="4. DOWNLOAD HISTORICAL DATA",
                                       command=self.download)
        self.download_btn.pack(fill="x", ipady=9)
        self.progress = ttk.Progressbar(main, mode="indeterminate")
        self.progress.pack(fill="x", pady=(8, 2))
        ttk.Label(main, textvariable=self.status, foreground="#245b35").pack(anchor="w", pady=5)

        log_frame = ttk.LabelFrame(main, text="Activity log", padding=6)
        log_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_frame, height=8, wrap="word", state="disabled")
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def log(self, msg):
        stamp = datetime.now().strftime("%H:%M:%S")
        def add():
            self.log_text.configure(state="normal")
            self.log_text.insert("end", f"[{stamp}] {msg}\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.root.after(0, add)

    def set_busy(self, busy, message=None):
        self.busy = busy
        def update():
            self.download_btn.configure(state="disabled" if busy else "normal")
            if busy:
                self.progress.start(12)
            else:
                self.progress.stop()
            if message:
                self.status.set(message)
        self.root.after(0, update)

    def clear_selection(self):
        self.selected_instrument = None
        self.results.selection_clear(0, "end")
        self.selected_label.configure(
            text="No instrument selected — choose a row from the current results",
            foreground="#9b3b32"
        )

    def on_filter_changed(self):
        # A selection must never survive a change to exchange, type, or search.
        self.clear_selection()
        self.filter_instruments()

    def save_credentials(self):
        key, secret = self.api_key.get().strip(), self.api_secret.get().strip()
        if not key or not secret:
            raise ValueError("Enter both API key and API secret.")
        save_private_json(CONFIG_FILE, {"api_key": key, "api_secret": secret})
        return key, secret

    def login(self):
        try:
            key, secret = self.save_credentials()
            kite = KiteConnect(api_key=key, timeout=30)
            webbrowser.open(kite.login_url())
            self.log("Opened Zerodha login. Complete login, then paste the fresh request_token.")
            token = simpledialog.askstring(
                "Request token",
                "Paste ONLY the fresh request_token value from the redirected URL (not the full URL):",
                parent=self.root
            )
            if not token or not token.strip():
                self.log("Authentication cancelled.")
                return
            self.set_busy(True, "Exchanging request token...")
            threading.Thread(target=self._exchange, args=(key, secret, token.strip()), daemon=True).start()
        except Exception as exc:
            messagebox.showerror("Login error", str(exc))

    def _exchange(self, key, secret, request_token):
        try:
            kite = KiteConnect(api_key=key, timeout=30)
            data = kite.generate_session(request_token, api_secret=secret)
            access = data["access_token"]
            kite.set_access_token(access)
            kite.profile()
            save_private_json(SESSION_FILE, {
                "api_key": key, "access_token": access,
                "generated_at": datetime.now().isoformat(timespec="seconds")
            })
            self.log("Access token generated, saved locally and validated.")
            self.set_busy(False, "Authenticated. Load instruments to choose a contract.")
            self.root.after(0, lambda: messagebox.showinfo("Authentication successful",
                "Access token is ready. Click Load / Refresh instruments."))
        except Exception as exc:
            self.log(f"Authentication failed: {exc}")
            self.set_busy(False, "Authentication failed. Check connection, credentials and fresh request token.")
            self.root.after(0, lambda e=str(exc): messagebox.showerror("Authentication failed", e))

    def get_kite(self):
        cfg = load_json(CONFIG_FILE)
        session = load_json(SESSION_FILE)
        if not cfg.get("api_key") or not session.get("access_token"):
            raise ValueError("Authenticate first using Login / Generate access token.")
        if cfg["api_key"] != session.get("api_key"):
            raise ValueError("Saved token belongs to a different API key. Log in again.")
        kite = KiteConnect(api_key=cfg["api_key"], timeout=30)
        kite.set_access_token(session["access_token"])
        kite.profile()
        return kite

    def load_instruments(self):
        if self.busy:
            return
        self.set_busy(True, "Downloading instrument master...")
        threading.Thread(target=self._load_instruments_worker, daemon=True).start()

    def _load_instruments_worker(self):
        try:
            kite = self.get_kite()
            rows = kite.instruments()
            df = pd.DataFrame(rows)
            if df.empty:
                raise RuntimeError("Instrument master returned no rows.")
            df.to_csv(INSTRUMENT_CACHE, index=False)
            self.instruments = df
            self.log(f"Downloaded {len(df):,} instruments and saved local instrument master.")
            self.root.after(0, self.filter_instruments)
            self.set_busy(False, "Instrument master ready. Search and select an exact contract.")
        except Exception as exc:
            self.log(f"Instrument download failed: {exc}")
            self.set_busy(False, "Could not load instruments.")
            self.root.after(0, lambda e=str(exc): messagebox.showerror("Instrument error", e))

    def filter_instruments(self):
        if self.instruments.empty:
            return
        df = self.instruments
        df = df[df["exchange"] == self.exchange.get()]
        typ = self.instrument_type.get()
        if typ != "ALL":
            df = df[df["instrument_type"] == typ]
        q = self.search.get().strip().upper()
        if q:
            sym = df["tradingsymbol"].fillna("").astype(str).str.upper()
            name = df["name"].fillna("").astype(str).str.upper()
            df = df[sym.str.contains(q, regex=False) | name.str.contains(q, regex=False)]
        df = df.head(1500)
        self.matches = df.to_dict("records")
        self.results.delete(0, "end")
        for item in self.matches:
            expiry = item.get("expiry")
            expiry_text = ""
            if pd.notna(expiry) and str(expiry).strip() not in ("", "NaT"):
                expiry_text = f" | expiry {str(expiry)[:10]}"
            label = (f"{item.get('tradingsymbol', '')} | {item.get('name', '')} | "
                     f"{item.get('instrument_type', '')}{expiry_text} | token {item.get('instrument_token')}")
            self.results.insert("end", label)
        self.results.selection_clear(0, "end")
        if len(self.matches) == 0:
            self.selected_instrument = None
            self.selected_label.configure(
                text="No matching instrument — refine your search",
                foreground="#9b3b32"
            )
        self.log(f"Showing {len(self.matches)} matching instruments.")

    def on_select(self, _event=None):
        selected = self.results.curselection()
        if not selected:
            return
        self.selected_instrument = self.matches[selected[0]]
        x = self.selected_instrument
        self.selected_label.configure(
            text=f"Selected: {x.get('exchange')}:{x.get('tradingsymbol')} | "
                 f"{x.get('instrument_type')} | token {x.get('instrument_token')}",
            foreground="#176b3a"
        )

    def get_date_range(self):
        today = date.today()
        duration = self.duration.get()
        if duration == "Custom dates":
            start = parse_date(self.start_date.get(), "Start date")
            end = parse_date(self.end_date.get(), "End date")
        else:
            month_periods = {
                "1 month": 1, "3 months": 3, "6 months": 6
            }
            year_periods = {
                "1 year": 1, "2 years": 2, "3 years": 3, "5 years": 5
            }
            if duration in month_periods:
                start = subtract_months(today, month_periods[duration])
            elif duration in year_periods:
                start = subtract_years(today, year_periods[duration])
            else:
                raise ValueError("Unsupported duration selected.")
            end = today
        if start > end:
            raise ValueError("Start date must be on or before end date.")
        if end > today:
            raise ValueError("End date cannot be in the future.")
        return start, end

    def download(self):
        if self.busy:
            return
        if self.selected_instrument is None:
            messagebox.showerror("Select an instrument", "Load instruments and select one contract first.")
            return
        try:
            start, end = self.get_date_range()
            session = load_json(SESSION_FILE)
            cfg = load_json(CONFIG_FILE)
            if not session.get("access_token") or session.get("api_key") != cfg.get("api_key"):
                raise ValueError("No valid saved access token. Authenticate first.")
            selected = dict(self.selected_instrument)
            interval = self.interval.get()
            include_oi = bool(self.oi.get()) and selected.get("instrument_type") in ("FUT", "CE", "PE")
            self.set_busy(True, "Downloading historical candles...")
            threading.Thread(target=self._download_worker,
                args=(selected, interval, start, end, include_oi), daemon=True).start()
        except Exception as exc:
            messagebox.showerror("Cannot download", str(exc))

    def _download_worker(self, instrument, interval, start, end, include_oi):
        try:
            kite = self.get_kite()
            token = int(instrument["instrument_token"])
            exchange = str(instrument["exchange"])
            symbol = str(instrument["tradingsymbol"])
            kind = str(instrument["instrument_type"])
            start_dt = datetime.combine(start, dt_time.min)
            end_dt = datetime.combine(end, dt_time(23, 59, 59))
            batch_days = BATCH_DAYS[interval]
            self.log(f"Fetching {exchange}:{symbol} ({kind}), {interval}, {start} to {end}.")
            chunks, cursor, batch = [], start_dt, 0
            batch_report = []

            while cursor <= end_dt:
                chunk_end = min(cursor + timedelta(days=batch_days) - timedelta(seconds=1), end_dt)
                batch += 1
                self.log(f"Batch {batch}: {cursor.date()} to {chunk_end.date()}")
                rows = None
                for attempt in range(1, 5):
                    try:
                        rows = kite.historical_data(
                            instrument_token=token,
                            from_date=cursor,
                            to_date=chunk_end,
                            interval=interval,
                            continuous=False,
                            oi=include_oi
                        )
                        break
                    except Exception as exc:
                        msg = str(exc).lower()
                        retryable = any(t in msg for t in (
                            "429", "too many", "rate", "timeout", "connection",
                            "502", "503", "504", "temporarily"
                        ))
                        if not retryable or attempt == 4:
                            raise
                        wait = min(2 ** attempt, 16)
                        self.log(f"Temporary error; retrying in {wait}s: {exc}")
                        time.sleep(wait)
                if rows is None:
                    raise RuntimeError("No response received for a date batch.")
                # Empty batch may mean no trading/contract activity; record it and continue.
                if rows:
                    chunks.extend(rows)
                    batch_report.append({
                        "batch": batch,
                        "from": cursor.isoformat(sep=" "),
                        "to": chunk_end.isoformat(sep=" "),
                        "returned_candles": len(rows),
                        "status": "data_returned"
                    })
                    self.log(f"Received {len(rows):,} candles.")
                else:
                    batch_report.append({
                        "batch": batch,
                        "from": cursor.isoformat(sep=" "),
                        "to": chunk_end.isoformat(sep=" "),
                        "returned_candles": 0,
                        "status": "empty_response_check_instrument_activity_holidays"
                    })
                    self.log("No candles in this batch. Recorded as a warning for review.")
                cursor = chunk_end + timedelta(seconds=1)
                time.sleep(API_DELAY)

            if not chunks:
                raise RuntimeError("No historical candles were returned for the selected instrument and period.")

            df = pd.DataFrame(chunks)
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            if df["date"].isna().any():
                raise RuntimeError("Invalid timestamps returned by API.")
            before = len(df)
            df = df.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
            duplicate_count = before - len(df)

            required = ["open", "high", "low", "close", "volume"]
            for col in required:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            if df[required].isna().any().any():
                raise RuntimeError("Missing or non-numeric OHLCV fields detected.")
            invalid = (
                (df["high"] < df[["open", "close", "low"]].max(axis=1)) |
                (df["low"] > df[["open", "close", "high"]].min(axis=1)) |
                (df["volume"] < 0)
            )
            if invalid.any():
                raise RuntimeError(f"Found {int(invalid.sum())} invalid OHLCV rows.")

            if "oi" in df.columns and not include_oi:
                df = df.drop(columns=["oi"])

            OUTPUT_DIR.mkdir(exist_ok=True)
            filename = (f"{safe_name(exchange)}_{safe_name(symbol)}_{safe_name(kind)}_"
                        f"{interval}_{start.isoformat()}_to_{end.isoformat()}.csv")
            final_path = OUTPUT_DIR / filename
            tmp_path = OUTPUT_DIR / (filename + ".part")
            df.to_csv(tmp_path, index=False)
            os.replace(tmp_path, final_path)

            manifest = {
                "downloaded_at_local": datetime.now().isoformat(timespec="seconds"),
                "exchange": exchange,
                "tradingsymbol": symbol,
                "instrument_type": kind,
                "instrument_token_used": token,
                "expiry": str(instrument.get("expiry", "")),
                "strike": str(instrument.get("strike", "")),
                "interval": interval,
                "requested_start_date": start.isoformat(),
                "requested_end_date": end.isoformat(),
                "include_open_interest": include_oi,
                "batch_size_calendar_days": batch_days,
                "batches": batch_report,
                "raw_rows_received": before,
                "duplicate_timestamps_removed": duplicate_count,
                "unique_rows_saved": len(df),
                "first_candle_timestamp": str(df["date"].iloc[0]),
                "last_candle_timestamp": str(df["date"].iloc[-1]),
                "empty_batches_require_review": any(x["returned_candles"] == 0 for x in batch_report),
                "validation": "basic OHLCV and timestamp checks passed; candle completeness not independently proven"
            }
            manifest_path = final_path.with_suffix(".manifest.json")
            manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

            self.log(f"Saved {len(df):,} unique candles; removed {duplicate_count} duplicates.")
            self.log(f"Manifest saved: {manifest_path.name}")
            self.log(f"Output: {final_path}")
            self.set_busy(False, "Download finished. Review the activity log and output CSV.")
            self.root.after(0, lambda: messagebox.showinfo(
                "Download finished",
                f"{exchange}:{symbol}\nInterval: {interval}\nPeriod: {start} to {end}\n"
                f"Candles: {len(df):,}\nDuplicates removed: {duplicate_count}\n\n"
                f"Saved to:\n{final_path}\n\n"
                "Note: empty batches can occur for holidays, inactive contracts or periods without trades. "
                "The app does not claim every expected market candle exists."
            ))
        except Exception as exc:
            self.log(f"DOWNLOAD FAILED: {exc}")
            self.set_busy(False, "Download failed. See activity log.")
            self.root.after(0, lambda e=str(exc): messagebox.showerror("Download failed", e))


if __name__ == "__main__":
    root = tk.Tk()
    app = UniversalKiteDownloader(root)
    root.mainloop()
