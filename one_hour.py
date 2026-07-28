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

# CONFIG — fill these in
API_KEY = "3LjGsQyt"
CLIENT_ID = "M50848322"
PASSWORD = "8581" 
TOTP_SECRET = "C4P6OKR4CY3QHB6DPTYGWLUIC4"     # Base32 secret from SmartAPI TOTP setup

TELEGRAM_BOT_TOKEN = "8842485648:AAGN8_S0PCv_jjxQMfvRPmdNkpPhbUT1SAQ"
TELEGRAM_CHAT_ID = "926442490"
TELEGRAM_BOT_TOKEN2 = "8869988041:AAHyS7goXL3TKCJI-g2jNIi_jkMQU6-rcvo"
TELEGRAM_CHAT_ID2 = "7984464288"

DRY_RUN = False       # True = simulate orders only (no real order placed). Set False to go live.
PRODUCT_TYPE = "INTRADAY"   # INTRADAY / DELIVERY / CARRYFORWARD
ORDER_VARIETY = "NORMAL"
LOOP_SLEEP_SECONDS = 10      # How often the main loop ticks
STATE_FILE = "bot_state.json"

ONE_HOUR_FETCH_TIMES = ["09:15", "10:15", "11:15", "12:15", "13:15", "14:15", "15:15"]
ONE_HOUR_FETCH_TIMES2 = ["09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00", "17:00", "18:00", "19:00", "20:00", "21:00", "22:00", "23:00"]

TEN_MIN_FETCH_MINUTES = {5, 25, 35, 45, 55}
TEN_MIN_FETCH_MINUTES2 = {0, 10, 20, 30, 40, 50}
LOGIN_TIMES = ["09:00", "09:20", "12:00", "15:00", "18:00", "21:00"]

WATCHLIST = [
    {
        "trading_symbol": "SENSEX",
        "exchange": "BSE",
        "token": "99919000",
    }
]

WATCHLIST2 = [
    {
        "trading_symbol": "ELECDMBL30JUL26FUT",
        "token": "568846",
        "exchange": "MCX",
    }
]

# LOGGING
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("ha_bot.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("ha_bot")

# TELEGRAM HELPERS
def send_telegram(message: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
    except Exception as e:
        log.error(f"Telegram send failed: {e}")

def send_telegram2(message: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN2}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID2, "text": message}, timeout=10)
    except Exception as e:
        log.error(f"Telegram send failed: {e}")

# STATE PERSISTENCE
def default_symbol_state():
    return {
        "position": None,                      
        "pending_signal": None,                
        "pending_signal_1h_close_time": None,  
        "last_processed_1h_time": None,        
        "last_processed_10m_time": None,        
    }

def reset_symbol_state_keep_position(sym_state):
    sym_state["pending_signal"] = None
    sym_state["pending_signal_1h_close_time"] = None
    sym_state["last_processed_1h_time"] = None
    sym_state["last_processed_10m_time"] = None

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
            log.info("Loaded existing state from disk.")
            return data
        except Exception as e:
            log.error(f"Failed to load state file: {e}")
    
    all_symbols = [item["trading_symbol"] for item in WATCHLIST + WATCHLIST2]
    return {symbol: default_symbol_state() for symbol in all_symbols}

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2, default=str)
    except Exception as e:
        log.error(f"Failed to save state: {e}")

# ANGEL ONE LOGIN
def login():
    obj = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()
    data = obj.generateSession(CLIENT_ID, PASSWORD, totp)
    if not data.get("status"):
        raise RuntimeError(f"Login failed: {data}")
    log.info("Logged in to Angel One SmartAPI.")
    return obj

# CANDLE DATA HELPERS WITH RATE LIMITING
def fetch_candles(smart_api, token, exchange, interval, lookback_minutes):
    """
    Fetches candle data with explicit rate limit delays and retries.
    """
    to_date = datetime.now(ZoneInfo("Asia/Kolkata"))
    from_date = to_date - timedelta(minutes=lookback_minutes)
    params = {
        "exchange": exchange,
        "symboltoken": token,
        "interval": interval,
        "fromdate": from_date.strftime("%Y-%m-%d %H:%M"),
        "todate": to_date.strftime("%Y-%m-%d %H:%M"),
    }
    
    # Rate limit buffer: pause 1.2 seconds before every request
    time.sleep(1.2)

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
                log.warning(f"Candle fetch no data (attempt {attempt}): {resp}")
        except Exception as e:
            log.error(f"Candle fetch error (attempt {attempt}): {e}")
        
        # Exponential backoff on retry
        time.sleep(attempt * 3)
    return None

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

def candle_color(open_price, close_price):
    return "GREEN" if close_price >= open_price else "RED"

def get_latest_completed_candle(df, interval_minutes):
    """
    Dynamically gets the latest COMPLETED candle.
    If the last row's close time <= current time, it uses df.iloc[-1].
    Otherwise, it drops the active forming candle and uses df.iloc[-2].
    """
    if df is None or len(df) < 2:
        return None

    last_row = df.iloc[-1]
    candle_open_time = last_row["time"]
    
    # Ensure timezone safety
    if candle_open_time.tzinfo is None:
        candle_open_time = candle_open_time.tz_localize(ZoneInfo("Asia/Kolkata"))

    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    candle_close_time = candle_open_time + timedelta(minutes=interval_minutes)

    # If the last row in df has already closed, use it!
    if now >= candle_close_time:
        return last_row
    else:
        # Last candle is still forming, return previous completed candle
        return df.iloc[-2]

def market_is_open():
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    open_t = now.replace(hour=9, minute=0, second=0, microsecond=0)
    close_t = now.replace(hour=23, minute=30, second=0, microsecond=0)
    return open_t <= now <= close_t and now.weekday() < 5

# STRATEGY LOGIC
def process_1h_bias(smart_api, symbol_info, sym_state):
    symbol = symbol_info["trading_symbol"]

    df_1h = fetch_candles(smart_api, symbol_info["token"], symbol_info["exchange"], "ONE_HOUR", 60 * 4)
    if df_1h is None:
        log.warning(f"Could not retrieve 1H candle data for {symbol}")
        return

    last_1h = get_latest_completed_candle(df_1h, 60)
    if last_1h is None:
        return

    last_1h_time = str(last_1h["time"])

    if sym_state["last_processed_1h_time"] == last_1h_time:
        return
    sym_state["last_processed_1h_time"] = last_1h_time

    ha_1h = to_heikin_ashi(df_1h)
    last_ha_1h = get_latest_completed_candle(ha_1h, 60)

    ha_color = candle_color(last_ha_1h["ha_open"], last_ha_1h["ha_close"])
    normal_color = candle_color(last_1h["open"], last_1h["close"])

    if sym_state["position"] is None:
        if ha_color == "GREEN" and normal_color == "GREEN":
            sym_state["pending_signal"] = "BUY"
            sym_state["pending_signal_1h_close_time"] = last_1h_time
            msg = f"📈 {symbol}: 1H candle confirmed GREEN ({last_1h_time}). Watching 10-min for BUY trigger."
            log.info(msg)
            send_telegram(msg)
            send_telegram2(msg)
        else:
            log.info(f"{symbol}: No 1H Buy bias (HA={ha_color}, Normal={normal_color}).")
    else:
        if ha_color == "RED" and normal_color == "RED":
            sym_state["pending_signal"] = "SELL"
            sym_state["pending_signal_1h_close_time"] = last_1h_time
            msg = f"📉 {symbol}: 1H candle confirmed RED ({last_1h_time}). Watching 10-min for EXIT trigger."
            log.info(msg)
            send_telegram(msg)
            send_telegram2(msg)

def process_10m_trigger(smart_api, symbol_info, sym_state):
    symbol = symbol_info["trading_symbol"]

    if sym_state["pending_signal"] not in ("BUY", "SELL"):
        return

    df_10m = fetch_candles(smart_api, symbol_info["token"], symbol_info["exchange"], "TEN_MINUTE", 10 * 8)
    if df_10m is None:
        return

    last_10m = get_latest_completed_candle(df_10m, 10)
    if last_10m is None:
        return

    last_10m_time = str(last_10m["time"])

    if sym_state["last_processed_10m_time"] == last_10m_time:
        return

    sym_state["last_processed_10m_time"] = last_10m_time

    ha_10m = to_heikin_ashi(df_10m)
    last_ha_10m = get_latest_completed_candle(ha_10m, 10)

    ha_10m_color = candle_color(last_ha_10m["ha_open"], last_ha_10m["ha_close"])
    normal_10m_color = candle_color(last_10m["open"], last_10m["close"])

    if sym_state["pending_signal"] == "BUY":
        if ha_10m_color == "GREEN" and normal_10m_color == "GREEN":
            entry_price = last_10m["close"]
            sym_state["position"] = {
                "entry_price": entry_price,
                "stoploss_price": 0,
                "entry_time": last_10m_time,
            }
            msg = f"✅ BUY TRIGGERED: {symbol} @ ~{entry_price}"
            log.info(msg)
            send_telegram(msg)
            send_telegram2(msg)
    elif sym_state["pending_signal"] == "SELL":
        if ha_10m_color == "RED" and normal_10m_color == "RED":
            exit_price = last_10m["close"]
            msg = f"✅ SELL TRIGGERED: {symbol} @ ~{exit_price}"
            log.info(msg)
            send_telegram(msg)
            send_telegram2(msg)
            sym_state["position"] = None

# MAIN LOOP
def main():
    log.info(f"Starting bot. DRY_RUN={DRY_RUN}")
    smart_api = login()
    state = load_state()

    last_1h_marker = None
    last_10m_marker = None
    last_1h_marker2 = None
    last_10m_marker2 = None
    last_login_marker = None

    send_telegram2("🤖 Algo trading bot started (Angel One SmartAPI).")

    while True:
        try:
            if not market_is_open():
                log.info("Market closed. Sleeping 5 minutes.")
                time.sleep(300)
                continue

            # Ensure all watchlist items exist in state
            for item in WATCHLIST + WATCHLIST2:
                if item["trading_symbol"] not in state:
                    state[item["trading_symbol"]] = default_symbol_state()

            now = datetime.now(ZoneInfo("Asia/Kolkata"))
            current_hm = now.strftime("%H:%M")

            # ---- Re-login at scheduled intervals ----
            if current_hm in LOGIN_TIMES and last_login_marker != current_hm:
                last_login_marker = current_hm
                smart_api = login()

            # ---- 1-HOUR window for WATCHLIST (SENSEX) ----
            if current_hm in ONE_HOUR_FETCH_TIMES and last_1h_marker != current_hm:
                last_1h_marker = current_hm
                log.info(f"=== 1H fetch window {current_hm} for WATCHLIST ===")
                for symbol_info in WATCHLIST:
                    reset_symbol_state_keep_position(state[symbol_info["trading_symbol"]])
                    try:
                        process_1h_bias(smart_api, symbol_info, state[symbol_info["trading_symbol"]])
                    except Exception as e:
                        log.error(f"Error processing 1H bias for {symbol_info['trading_symbol']}: {e}", exc_info=True)
                save_state(state)

            # ---- 10-MINUTE window for WATCHLIST (SENSEX) ----
            elif now.minute in TEN_MIN_FETCH_MINUTES and last_10m_marker != current_hm:
                last_10m_marker = current_hm
                for symbol_info in WATCHLIST:
                    try:
                        process_10m_trigger(smart_api, symbol_info, state[symbol_info["trading_symbol"]])
                    except Exception as e:
                        log.error(f"Error processing 10min trigger for {symbol_info['trading_symbol']}: {e}", exc_info=True)
                save_state(state)

            # ---- 1-HOUR window for WATCHLIST2 (MCX Futures) ----
            elif current_hm in ONE_HOUR_FETCH_TIMES2 and last_1h_marker2 != current_hm:
                last_1h_marker2 = current_hm
                log.info(f"=== 1H fetch window {current_hm} for WATCHLIST2 ===")
                for symbol_info in WATCHLIST2:
                    reset_symbol_state_keep_position(state[symbol_info["trading_symbol"]])
                    try:
                        process_1h_bias(smart_api, symbol_info, state[symbol_info["trading_symbol"]])
                    except Exception as e:
                        log.error(f"Error processing 1H bias for {symbol_info['trading_symbol']}: {e}", exc_info=True)
                save_state(state)

            # ---- 10-MINUTE window for WATCHLIST2 (MCX Futures) ----
            elif now.minute in TEN_MIN_FETCH_MINUTES2 and last_10m_marker2 != current_hm:
                last_10m_marker2 = current_hm
                for symbol_info in WATCHLIST2:
                    try:
                        process_10m_trigger(smart_api, symbol_info, state[symbol_info["trading_symbol"]])
                    except Exception as e:
                        log.error(f"Error processing 10min trigger for {symbol_info['trading_symbol']}: {e}", exc_info=True)
                save_state(state)

            time.sleep(LOOP_SLEEP_SECONDS)

        except KeyboardInterrupt:
            log.info("Bot stopped manually.")
            save_state(state)
            break
        except Exception as e:
            log.error(f"Main loop error: {e}", exc_info=True)
            send_telegram(f"⚠️ Bot main loop error: {e}")
            send_telegram2(f"⚠️ Bot main loop error: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()
