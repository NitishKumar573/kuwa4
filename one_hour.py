import json
import logging
import os
import time
from datetime import datetime, timedelta

import pandas as pd
import pyotp
import requests
#from growwapi import GrowwAPI
from SmartApi import SmartConnect
from zoneinfo import ZoneInfo
# CONFIG — fill these in
# Groww API credentials.
# CONFIG — fill these in M50848322
API_KEY = "3LjGsQyt"
CLIENT_ID = "M50848322"
PASSWORD = "8581" 
TOTP_SECRET = "C4P6OKR4CY3QHB6DPTYGWLUIC4"     # Base32 secret from SmartAPI TOTP setup

TELEGRAM_BOT_TOKEN = "8842485648:AAGN8_S0PCv_jjxQMfvRPmdNkpPhbUT1SAQ"
TELEGRAM_CHAT_ID = "926442490"
TELEGRAM_BOT_TOKEN2="8869988041:AAHyS7goXL3TKCJI-g2jNIi_jkMQU6-rcvo"
TELEGRAM_CHAT_ID2 = "7984464288"

DRY_RUN = False       # True = simulate orders only (no real order placed). Set False to go live.
PRODUCT_TYPE = "INTRADAY"   # INTRADAY / DELIVERY / CARRYFORWARD (Angel One naming)
ORDER_VARIETY = "NORMAL"
LOOP_SLEEP_SECONDS = 30      # how often the main loop ticks
STATE_FILE = "bot_state.json"

MARKET_OPEN = "09:00"
MARKET_CLOSE = "21:30"

# ---- FIXED FETCH SCHEDULE ----
# 1-Hour candles are fetched ONLY at these exact clock times (once each), never on every tick.
ONE_HOUR_FETCH_TIMES = ["09:15", "10:15", "11:15", "12:15", "13:15", "14:15"]
ONE_HOUR_FETCH_TIMES2 = ["09:00", "10:00", "11:00", "12:00", "13:00", "14:00","15:00","16:00","17:00","18:00","19:00","20:00","21:00"]


# 10-Minute candles are fetched every 10 minutes EXCEPT at the ":15" mark, because ":15" is
# already handled by the 1-Hour fetch above and would otherwise just re-read the same
# just-closed 1H candle boundary (e.g. 9:25, 9:35, 9:45, 9:55, 10:05, 10:25, 10:35 ... — never 9:15/10:15/...).
TEN_MIN_FETCH_MINUTES = {5, 25, 35, 45, 55}
TEN_MIN_FETCH_MINUTES = {0, 10, 20, 30, 40,50}
smartApi = SmartConnect(api_key=API_KEY)
totp = pyotp.TOTP(TOTP_SECRET).now()

WATCHLIST = [
   
    {
        "trading_symbol": "SENSEX",
        "exchange": "BSE",
        "token":"99919000",
    },
    # Add more symbols here...
]
# LOGGING
WATCHLIST2 = [
    
     {
        "trading_symbol": "ELECDMBL30JUL26FUT",
        "token": "568846",
        "exchange": "MCX",
           # optional, informational only in this template
    },
    # Add more symbols here...
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("ha_bot.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("ha_bot")
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
# TELEGRAM
def send_telegram(message: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
def send_telegram2(message:str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN2}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID2, "text": message}, timeout=10)
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
# STATE PERSISTENCE
def default_symbol_state():
    return {
        "position": None,                     # dict with entry_price, quantity, stoploss_price, entry_time
        "pending_signal": None,                # "BUY" or "SELL" (1H bias confirmed, waiting for 10min trigger)
        "pending_signal_1h_close_time": None,  # ISO timestamp of the 1H candle close that set the bias
        "last_processed_1h_time": None,        # avoid re-evaluating the same 1H candle repeatedly
        "last_processed_10m_time": None,       # avoid re-evaluating the same 10min candle repeatedly
    }


def reset_symbol_state_keep_position(sym_state):
    """Reset every tracked variable EXCEPT 'position'. Called for every symbol right before
    each fixed-time 1H candle fetch (9:15, 10:15, 11:15, 12:15, 1:15, 2:15)."""
    sym_state["position"]=None
    sym_state["pending_signal"] = None
    sym_state["pending_signal_1h_close_time"] = None
    sym_state["last_processed_1h_time"] = None
    sym_state["last_processed_10m_time"] = None


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
            log.info("Loaded existing state from disk (resuming after restart).")
            return data
        except Exception as e:
            log.error(f"Failed to load state file, starting fresh: {e}")
    return {item["trading_symbol"]: default_symbol_state() for item in WATCHLIST}


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
# CANDLE DATA HELPERS
def fetch_candles(smart_api, token, exchange, interval, lookback_minutes):
    """
    interval: "ONE_HOUR" or "TEN_MINUTE" (Angel One interval codes)
    Returns a DataFrame with columns: time, open, high, low, close, volume
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
    for attempt in range(3):
        try:
            resp = smart_api.getCandleData(params)
            if resp.get("status") and resp.get("data"):
                df = pd.DataFrame(
                    resp["data"], columns=["time", "open", "high", "low", "close", "volume"]
                )
                df["time"] = pd.to_datetime(df["time"])
                for col in ["open", "high", "low", "close", "volume"]:
                    df[col] = df[col].astype(float)
                return df
            else:
                log.warning(f"Candle fetch returned no data (attempt {attempt+1}): {resp}")
        except Exception as e:
            log.error(f"Candle fetch error (attempt {attempt+1}): {e}")
            time.sleep(1)
        time.sleep(1)
    return None


def to_heikin_ashi(df):
    """Convert a normal OHLC dataframe to Heikin-Ashi OHLC."""
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


def is_last_candle_completed(df, interval_minutes):
    """
    The LAST row returned may still be the currently-forming (incomplete) candle. A candle is
    only "completed" once its close-time (open-time + interval) has passed.
    Returns True if df.iloc[-2] is a fully completed candle we can safely evaluate,
    and there are at least 2 rows.
    """
    if df is None or len(df) < 2:
        return False
    last_candle_open = df["time"].iloc[-1]
    last_candle_close_time = last_candle_open + timedelta(minutes=interval_minutes)
    now = datetime.now(last_candle_open.tzinfo) if last_candle_open.tzinfo else datetime.now(ZoneInfo("Asia/Kolkata"))
    return now >= last_candle_close_time


def get_last_completed(df):
    """Return the second-to-last row = latest fully closed candle (last row may be forming)."""
    return df.iloc[-2]
def get_ltp(smart_api, symbol_info):
    try:
        key = f"{symbol_info['exchange']}_{symbol_info['trading_symbol']}"
        resp = smart_api.get_ltp(segment=smart_api.SEGMENT_CASH, exchange_trading_symbols=key)
        if resp:
            val = resp.get(key)
            if isinstance(val, dict):
                return float(val.get("ltp"))
            elif val is not None:
                return float(val)
    except Exception as e:
        log.error(f"LTP fetch failed for {symbol_info['trading_symbol']}: {e}")
    return None
#MARKET HOUR

def market_is_open():
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    open_t = now.replace(hour=9, minute=0, second=0, microsecond=0)
    close_t = now.replace(hour=23, minute=30, second=0, microsecond=0)

    return open_t <= now <= close_t and now.weekday() < 5
# 1-HOUR BIAS LOGIC — runs ONLY at the fixed times in ONE_HOUR_FETCH_TIMES
def process_1h_bias(smart_api,symbol_info, sym_state):
    symbol = symbol_info["trading_symbol"]

    df_1h = fetch_candles(smart_api,symbol_info["token"],symbol_info["exchange"], "ONE_HOUR", 60 * 3)
    print(df_1h)
    if not is_last_candle_completed(df_1h, 60):
        log.debug(f"{symbol}: last 1H candle not completed yet, skipping this window.")
        return

    last_1h = get_last_completed(df_1h)
    last_1h_time = str(last_1h["time"])

    # Only evaluate a given completed 1H candle once
    if sym_state["last_processed_1h_time"] == last_1h_time:
        return
    sym_state["last_processed_1h_time"] = last_1h_time

    ha_1h = to_heikin_ashi(df_1h)
    last_ha_1h = get_last_completed(ha_1h)
    ha_color = candle_color(last_ha_1h["ha_open"], last_ha_1h["ha_close"])
    normal_color = candle_color(last_1h["open"], last_1h["close"])

    if sym_state["position"] is None:
        # ---- BUY BIAS CHECK ----
        if ha_color == "GREEN":
            if normal_color == "GREEN":
                sym_state["pending_signal"] = "BUY"
                sym_state["pending_signal_1h_close_time"] = last_1h_time
                log.info(f"{symbol}: 1H BUY bias confirmed (HA green + normal green). Watching 10min frame.")
                send_telegram(f"📈 {symbol}: 1H candle confirmed GREEN (HA + normal). Watching 10-min for entry trigger.")
            else:
                log.info(f"{symbol}: HA green but normal candle red — no bias, waiting for next 1H candle.")
        else:
            log.info(f"{symbol}: 1H HA candle is RED — no buy bias, waiting for next 1H candle.")
    else:
        # ---- SELL BIAS CHECK (already in position) ----
        if ha_color == "RED":
            if normal_color == "RED":
                sym_state["pending_signal"] = "SELL"
                sym_state["pending_signal_1h_close_time"] = last_1h_time
                log.info(f"{symbol}: 1H SELL bias confirmed (HA red + normal red). Watching 10min frame.")
                send_telegram(f"📉 {symbol}: 1H candle confirmed RED (HA + normal). Watching 10-min for exit trigger.")
            else:
                log.info(f"{symbol}: HA red but normal candle green — no sell bias yet.")
        else:
            log.info(f"{symbol}: 1H HA candle still GREEN — holding position, no exit bias.")
            
#10-MINUTE TRIGGER LOGIC — runs ONLY at the fixed minute-marks in TEN_MIN_FETCH_MINUTES

#gjbh
def process_10m_trigger(smart_api, symbol_info, sym_state):
    symbol = symbol_info["trading_symbol"]

    if sym_state["pending_signal"] not in ("BUY", "SELL"):
        return  # nothing to watch for right now

    df_10m = fetch_candles(smart_api,symbol_info["token"],symbol_info["exchange"], "TEN_MINUTE", 10 * 8)
    if not is_last_candle_completed(df_10m, 10):
        return

    last_10m = get_last_completed(df_10m)
    last_10m_time = str(last_10m["time"])

    # Only consider 10-min candles that closed AFTER the 1H candle that set the bias
    bias_time = pd.to_datetime(sym_state["pending_signal_1h_close_time"]) + timedelta(hours=1)
    if last_10m["time"] < bias_time:
        return  # 10min candle is stale, hasn't reached the point after 1H close yet

    if sym_state["last_processed_10m_time"] == last_10m_time:
        return  # already evaluated this 10-min candle

    sym_state["last_processed_10m_time"] = last_10m_time

    ha_10m = to_heikin_ashi(df_10m)
    last_ha_10m = get_last_completed(ha_10m)
    ha_10m_color = candle_color(last_ha_10m["ha_open"], last_ha_10m["ha_close"])
    normal_10m_color = candle_color(last_10m["open"], last_10m["close"])

    if sym_state["pending_signal"] == "BUY":
        
        
        if ha_10m_color == "GREEN" and normal_10m_color == "GREEN":
            
            
                entry_price = last_10m["close"]
                #place_order(groww, symbol_info, "BUY", order_type="MARKET")
                sym_state["position"] = {
                    "entry_price": entry_price,
                    "quantity": symbol_info["quantity"],
                    "stoploss_price":0,
                    "entry_time": last_10m_time,
                }
                #sym_state["pending_signal"] = None
                #sym_state["pending_signal_1h_close_time"] = None
                msg = (
                    f"✅ BUY TRIGGERED: {symbol} @ ~{entry_price} "
                    
                )
                log.info(msg)
                send_telegram(msg)
                send_telegram2(msg)
        else:
            log.info(
                f"{symbol}: 10min candle at {last_10m_time} not both-green "
                f"(HA={ha_10m_color}, normal={normal_10m_color}). "
                f"BUY bias still active — will check the next 10min candle when it closes."
            )

    elif sym_state["pending_signal"] == "SELL":
        if ha_10m_color == "RED" and normal_10m_color == "RED":
            
                exit_price = last_10m["close"]
                #place_order(groww, symbol_info, "SELL", order_type="MARKET")
                msg = f"✅ SELL TRIGGERED: {symbol} @ ~{exit_price} (pattern exit)"
                log.info(msg)
                send_telegram(msg)
                send_telegram2(msg)
                sym_state["position"] = None
                #sym_state["position"] = None
                #sym_state["pending_signal_1h_close_time"] = None
        else:
            log.info(
                f"{symbol}: 10min candle at {last_10m_time} not both-red "
                f"(HA={ha_10m_color}, normal={normal_10m_color}). "
                f"SELL bias still active — will check the next 10min candle when it closes."
            )
# MAIN LOOP
def main():
   
    log.info(f"Starting bot. DRY_RUN={DRY_RUN}")
    if DRY_RUN:
        log.warning("Running in DRY_RUN mode — no real orders will be placed. Set DRY_RUN=False to go live.")

    smart_api = login()
    state = load_state()

    last_1h_marker = None   # HH:MM of the last 1H fetch window we already handled
    last_10m_marker = None  # HH:MM of the last 10-min fetch window we already handled
    send_telegram("🤖 Algo trading bot started (Angel One SmartAPI). Watching: "
           + ", ".join(c["trading_symbol"] for c in WATCHLIST
           ))
    send_telegram2("🤖 Algo trading bot started (Angel One SmartAPI). Watching: "
           + ", ".join(c["trading_symbol"] for c in WATCHLIST
           ))
    c1=False

    while True:
        time.sleep(3)
        
        try:
            if not market_is_open():
                log.info("Market closed. Sleeping 5 minutes.")
                time.sleep(300)
                if(c1==False):
                    c1=True
                    msg="Market is closed "
                continue

            for symbol_info in WATCHLIST:
                if symbol_info["trading_symbol"] not in state:
                    state[symbol_info["trading_symbol"]] = default_symbol_state()

            now = datetime.now(ZoneInfo("Asia/Kolkata"))
            current_hm = now.strftime("%H:%M")
            # ---- Hard stop-loss check runs every tick regardless of the fetch schedule ----
            """for symbol_info in WATCHLIST:
                sym_state = state[symbol_info["trading_symbol"]]
                try:
                    check_stoploss(groww, symbol_info, sym_state)
                except Exception as e:
                    log.error(f"Error checking stoploss for {symbol_info['trading_symbol']}: {e}", exc_info=True)
"""
            # ---- 1-HOUR window: fetch only once, exactly at 9:15, 10:15, 11:15, 12:15, 1:15, 2:15 ----
            if current_hm in ONE_HOUR_FETCH_TIMES and last_1h_marker != current_hm:
                time.sleep(3)
                last_1h_marker = current_hm
                log.info(f"=== 1H fetch window {current_hm}: resetting pending state (position kept) for all symbols ===")
                for symbol_info in WATCHLIST:
                    reset_symbol_state_keep_position(state[symbol_info["trading_symbol"]])

                for symbol_info in WATCHLIST:
                    try:
                        process_1h_bias(smart_api, symbol_info, state[symbol_info["trading_symbol"]])
                    except Exception as e:
                        log.error(f"Error processing 1H bias for {symbol_info['trading_symbol']}: {e}", exc_info=True)
                    time.sleep(1)  # small gap between symbols to respect API rate limits

                save_state(state)

            # ---- 10-MINUTE window: fetch every 10 min, but never at the ":15" mark ----
            elif now.minute in TEN_MIN_FETCH_MINUTES and last_10m_marker != current_hm:
                last_10m_marker = current_hm
                time.sleep(6)
                for symbol_info in WATCHLIST:
                    try:
                        process_10m_trigger(smart_api, symbol_info, state[symbol_info["trading_symbol"]])
                    except Exception as e:
                        log.error(f"Error processing 10min trigger for {symbol_info['trading_symbol']}: {e}", exc_info=True)
                    time.sleep(1)  # small gap between symbols to respect API rate limits

                save_state(state)
            elif now.minute in ONE_HOUR_FETCH_TIMES2 and last_10m_marker != current_hm:
                last_10m_marker = current_hm
                time.sleep(3)
                for symbol_info in WATCHLIST2:
                    try:
                        process_1h_bias(smart_api, symbol_info, state[symbol_info["trading_symbol"]])
                    except Exception as e:
                        log.error(f"Error processing 10min trigger for {symbol_info['trading_symbol']}: {e}", exc_info=True)
                    time.sleep(1)
             elif now.minute in TEN_MIN_FETCH_MINUTES and last_10m_marker != current_hm:
                last_10m_marker = current_hm
                time.sleep(6)
                for symbol_info in WATCHLIST2:
                    try:
                        process_10m_trigger(smart_api, symbol_info, state[symbol_info["trading_symbol"]])
                    except Exception as e:
                        log.error(f"Error processing 10min trigger for {symbol_info['trading_symbol']}: {e}", exc_info=True)
                    time.sleep(1)  

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
