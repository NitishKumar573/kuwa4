import json
import logging
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pyotp
import requests
from SmartApi import SmartConnect

# CONFIGURATION — SENSEX ONLY
API_KEY = "3LjGsQyt"
CLIENT_ID = "M50848322"
PASSWORD = "8581" 
TOTP_SECRET = "C4P6OKR4CY3QHB6DPTYGWLUIC4"

TELEGRAM_BOT_TOKEN = "8842485648:AAGN8_S0PCv_jjxQMfvRPmdNkpPhbUT1SAQ"
TELEGRAM_CHAT_ID = "926442490"
TELEGRAM_BOT_TOKEN2="8869988041:AAHyS7goXL3TKCJI-g2jNIi_jkMQU6-rcvo"
TELEGRAM_CHAT_ID2 = "7984464288"

STATE_FILE = "sensex_state.json"
LOOP_SLEEP_SECONDS = 10

SYMBOL_INFO = {
    "trading_symbol": "SENSEX",
    "exchange": "BSE",
    "token": "99919000",
}

ONE_HOUR_FETCH_TIMES = ["09:15", "10:15", "11:15", "12:15", "13:15", "14:15", "15:15"]
TEN_MIN_FETCH_MINUTES = {5, 25, 35, 45, 55}
LOGIN_TIMES = ["09:00", "12:00", "15:00"]

# LOGGING
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SENSEX] [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("sensex_bot.log"), logging.StreamHandler()],
)
log = logging.getLogger("sensex_bot")

def send_telegram(message: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
    except Exception as e:
        log.error(f"Telegram send error: {e}")
def send_telegram2(message: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN2}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID2, "text": message}, timeout=10)
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
# STATE MANAGEMENT
def default_state():
    return {
        "position": None,
        "pending_signal": None,
        "pending_signal_1h_close_time": None,
        "last_processed_1h_time": None,
        "last_processed_10m_time": None,
    }

def reset_signal_keep_position(state):
    state["pending_signal"] = None
    state["pending_signal_1h_close_time"] = None
    state["last_processed_1h_time"] = None
    state["last_processed_10m_time"] = None

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            log.error(f"Failed loading state: {e}")
    return default_state()

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2, default=str)
    except Exception as e:
        log.error(f"Failed saving state: {e}")

# LOGIN
def login():
    obj = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()
    data = obj.generateSession(CLIENT_ID, PASSWORD, totp)
    if not data.get("status"):
        raise RuntimeError(f"Login failed: {data}")
    log.info("Logged in to SmartAPI for SENSEX.")
    return obj

# CANDLE FETCH WITH RATE LIMIT CONTROL
def fetch_candles(smart_api, token, exchange, interval, lookback_minutes):
    to_date = datetime.now(ZoneInfo("Asia/Kolkata"))
    from_date = to_date - timedelta(minutes=lookback_minutes)
    params = {
        "exchange": exchange,
        "symboltoken": token,
        "interval": interval,
        "fromdate": from_date.strftime("%Y-%m-%d %H:%M"),
        "todate": to_date.strftime("%Y-%m-%d %H:%M"),
    }
    
    time.sleep(1.2)  # Rate limit safety delay

    for attempt in range(1, 4):
        try:
            resp = smart_api.getCandleData(params)
            if resp and resp.get("status") and resp.get("data"):
                df = pd.DataFrame(
                    resp["data"], columns=["time", "open", "high", "low", "close", "volume"]
                )
                df["time"] = pd.to_datetime(df["time"])
                for col in ["open", "high", "low", "close", "volume"]:
                    df[col] = df[col].astype(float)
                return df
            else:
                log.warning(f"No candle data received (Attempt {attempt}): {resp}")
        except Exception as e:
            log.error(f"Fetch candle error (Attempt {attempt}): {e}")
        time.sleep(attempt * 3)
    return None

# HEIKIN ASHI & CANDLE UTILS
def to_heikin_ashi(df):
    ha = df.copy().reset_index(drop=True)
    ha["ha_close"] = (df["open"] + df["high"] + df["low"] + df["close"]) / 4.0
    ha_open = [(df["open"].iloc[0] + df["close"].iloc[0]) / 2.0]
    for i in range(1, len(df)):
        ha_open.append((ha_open[i - 1] + ha["ha_close"].iloc[i - 1]) / 2.0)
    ha["ha_open"] = ha_open
    ha["ha_high"] = ha[["ha_open", "ha_close"]].join(df["high"]).max(axis=1)
    ha["ha_low"] = ha[["ha_open", "ha_close"]].join(df["low"]).min(axis=1)
    return ha

def candle_color(open_p, close_p):
    return "GREEN" if close_p >= open_p else "RED"

def get_latest_completed_candle(df, interval_minutes):
    if df is None or len(df) < 2:
        return None

    last_row = df.iloc[-1]
    candle_time = last_row["time"]

    if candle_time.tzinfo is None:
        candle_time = candle_time.tz_localize(ZoneInfo("Asia/Kolkata"))

    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    candle_close_time = candle_time + timedelta(minutes=interval_minutes)

    if now >= candle_close_time:
        return last_row
    else:
        return df.iloc[-2]

def market_is_open():
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    open_t = now.replace(hour=9, minute=15, second=0, microsecond=0)
    close_t = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return open_t <= now <= close_t and now.weekday() < 5

# STRATEGY LOGIC
def process_1h_bias(smart_api, state):
    df_1h = fetch_candles(smart_api, SYMBOL_INFO["token"], SYMBOL_INFO["exchange"], "ONE_HOUR", 60 * 5)
    if df_1h is None or df_1h.empty:
        msg = f"⚠️ SENSEX: Failed to fetch 1H candles from SmartAPI."
        log.warning(msg)
        send_telegram(msg)
        send_telegram2(msg)
        return

    last_1h = get_latest_completed_candle(df_1h, 60)
    if last_1h is None:
        return

    last_1h_time = str(last_1h["time"])
    if state["last_processed_1h_time"] == last_1h_time:
        return
    state["last_processed_1h_time"] = last_1h_time

    ha_1h = to_heikin_ashi(df_1h)
    last_ha_1h = get_latest_completed_candle(ha_1h, 60)

    ha_c = candle_color(last_ha_1h["ha_open"], last_ha_1h["ha_close"])
    norm_c = candle_color(last_1h["open"], last_1h["close"])

    if state["position"] is None:
        if ha_c == "GREEN" and norm_c == "GREEN":
            state["pending_signal"] = "BUY"
            state["pending_signal_1h_close_time"] = last_1h_time
            msg = f"📈 SENSEX: 1H Candle Confirmed GREEN ({last_1h_time}). Watching 10M for BUY trigger."
            log.info(msg)
            send_telegram(msg)
            send_telegram2(msg)
        else:
            log.info(f"SENSEX: No 1H Buy Bias (HA={ha_c}, Normal={norm_c}).")
    else:
        if ha_c == "RED" and norm_c == "RED":
            state["pending_signal"] = "SELL"
            state["pending_signal_1h_close_time"] = last_1h_time
            msg = f"📉 SENSEX: 1H Candle Confirmed RED ({last_1h_time}). Watching 10M for EXIT trigger."
            log.info(msg)
            send_telegram(msg)
            send_telegram2(msg)

def process_10m_trigger(smart_api, state):
    if state["pending_signal"] not in ("BUY", "SELL"):
        return

    df_10m = fetch_candles(smart_api, SYMBOL_INFO["token"], SYMBOL_INFO["exchange"], "TEN_MINUTE", 10 * 8)
    if df_10m is None or df_10m.empty:
        return

    last_10m = get_latest_completed_candle(df_10m, 10)
    if last_10m is None:
        return

    last_10m_time = str(last_10m["time"])
    if state["last_processed_10m_time"] == last_10m_time:
        return
    state["last_processed_10m_time"] = last_10m_time

    ha_10m = to_heikin_ashi(df_10m)
    last_ha_10m = get_latest_completed_candle(ha_10m, 10)

    ha_c = candle_color(last_ha_10m["ha_open"], last_ha_10m["ha_close"])
    norm_c = candle_color(last_10m["open"], last_10m["close"])

    if state["pending_signal"] == "BUY" and ha_c == "GREEN" and norm_c == "GREEN":
        entry_price = last_10m["close"]
        state["position"] = {"entry_price": entry_price, "entry_time": last_10m_time}
        msg = f"✅ BUY TRIGGERED: SENSEX @ ~{entry_price}"
        log.info(msg)
        send_telegram(msg)
        send_telegram2(msg)
    elif state["pending_signal"] == "SELL" and ha_c == "RED" and norm_c == "RED":
        exit_price = last_10m["close"]
        msg = f"✅ SELL TRIGGERED: SENSEX @ ~{exit_price}"
        log.info(msg)
        send_telegram(msg)
        send_telegram2(msg)
        state["position"] = None

# MAIN LOOP
def main():
    log.info("Starting SENSEX Bot...")
    smart_api = login()
    state = load_state()

    last_1h_marker = None
    last_10m_marker = None
    last_login_marker = None

    send_telegram("🤖 SENSEX Algo Bot Active.")

    while True:
        try:
            if not market_is_open():
                log.info("SENSEX Market closed. Sleeping 5 minutes.")
                time.sleep(300)
                continue

            now = datetime.now(ZoneInfo("Asia/Kolkata"))
            current_hm = now.strftime("%H:%M")

            # Scheduled Relogin
            """if current_hm in LOGIN_TIMES and last_login_marker != current_hm:
                last_login_marker = current_hm
                smart_api = login()"""

            # 1-Hour Schedule Check
            if current_hm in ONE_HOUR_FETCH_TIMES and last_1h_marker != current_hm:
                last_1h_marker = current_hm
                log.info(f"=== Running 1H Check for SENSEX ({current_hm}) ===")
                reset_signal_keep_position(state)
                process_1h_bias(smart_api, state)
                save_state(state)

            # 10-Minute Schedule Check
            elif now.minute in TEN_MIN_FETCH_MINUTES and last_10m_marker != current_hm:
                last_10m_marker = current_hm
                process_10m_trigger(smart_api, state)
                save_state(state)

            time.sleep(LOOP_SLEEP_SECONDS)

        except KeyboardInterrupt:
            log.info("Bot manually stopped.")
            save_state(state)
            break
        except Exception as e:
            log.error(f"SENSEX Bot error: {e}", exc_info=True)
            send_telegram(f"⚠️ SENSEX Bot Error: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()
