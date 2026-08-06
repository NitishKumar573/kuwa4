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
import numpy as np

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

#ONE_HOUR_FETCH_TIMES = ["09:15", "10:15", "11:15", "12:15", "13:15", "14:15", "15:15"]
TEN_MIN_FETCH_MINUTES = {3,6,9,12,15,18,21,24,27,30,33,36,39,42,45,48,51,54,57,60}
#LOGIN_TIMES = ["09:00", "12:00", "15:00"]

SYMBOL_INFO = {
    "trading_symbol": "SENSEX",
    "exchange": "BSE",
    "token": "99919000",
}


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
# STATE MANAGEMENT
def default_state():
    return {
        "position": None,
        "pending_signal": None,
        "stoploss":None,
        "signal":None,
        "sell_value":None,
        "sell_value2":None,
        "pending_signal_1h_close_time": None,
        "last_processed_1h_time": None,
        "last_processed_10m_time": None,
    }
def reset_signal_keep_position2(state):
    state["position"] = None
    state["stoploss"] = None
    state["pending_signal"] = None
    state["pending_signal_1h_close_time"] = None
    state["last_processed_1h_time"] = None
    state["last_processed_10m_time"] = None
    state["signal"] = None
    state["sell_value"] = None
    state["sell_value2"] = None
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

def fetch_candles(smart_api, token, exchange, interval, lookback_minutes):
    to_date = datetime.now(ZoneInfo("Asia/Kolkata"))
    #to_date = datetime(2026, 7, 30, 14,00, tzinfo=ZoneInfo("Asia/Kolkata"))
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
def get_second_latest_completed_candle(df, interval_minutes):
    # Need at least 3 candles: forming candle + 1st completed + 2nd completed
    if df is None or len(df) < 3:
        return None

    last_row = df.iloc[-1]
    candle_time = last_row["time"]

    if candle_time.tzinfo is None:
        candle_time = candle_time.tz_localize(ZoneInfo("Asia/Kolkata"))

    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    candle_close_time = candle_time + timedelta(minutes=interval_minutes)

    # If the last candle (iloc[-1]) has completed:
    # - iloc[-1] is the 1st (most recent) completed candle
    # - iloc[-2] is the 2nd completed candle
    if now >= candle_close_time:
        return df.iloc[-2]
    
    # If the last candle (iloc[-1]) is still forming:
    # - iloc[-2] is the 1st (most recent) completed candle
    # - iloc[-3] is the 2nd completed candle
    else:
        return df.iloc[-3]

def market_is_open():
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    open_t = now.replace(hour=9, minute=15, second=0, microsecond=0)
    close_t = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return open_t <= now <= close_t and now.weekday() < 5


def calculate_wma(series, period):
    weights = np.arange(1, period + 1)

    return series.rolling(period).apply(
        lambda prices: np.dot(prices, weights) / weights.sum(),
        raw=True
    )
def get_ltp(smart_api, symbol_info):
    try:
        response = smart_api.ltpData(
            exchange=symbol_info["exchange"],
            tradingsymbol=symbol_info["trading_symbol"],
            symboltoken=symbol_info["token"]
        )

        if response and response.get("status"):
            return float(response["data"]["ltp"])

        log.warning(f"Failed to fetch LTP: {response}")

    except Exception as e:
        log.error(f"LTP fetch failed for {symbol_info['trading_symbol']}: {e}")

    return None
def process_10m_trigger2(smart_api, state):

    df_10m = fetch_candles(smart_api, SYMBOL_INFO["token"], SYMBOL_INFO["exchange"], "THREE_MINUTE", 10 * 8)
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
    df_10m["WMA5"] = calculate_wma(df_10m["close"], 5)
    df_10m["WMA11"] = calculate_wma(df_10m["close"], 11)
    current = df_10m.iloc[-2]
    previous = df_10m.iloc[-3]
    
    ha_1=ha_10m.iloc[-2]
    ha_2=ha_10m.iloc[-3]
    ha_3=ha_10m.iloc[-4]
    ha_1_n_color=candle_color(current["open"],current["close"])
    ha_2_n_color=candle_color(previous["open"],previous["close"])
    ha_1_color=candle_color(ha_1["ha_open"],ha_1["ha_close"])
    ha_2_color=candle_color(ha_2["ha_open"],ha_2["ha_close"])
    ha_3_color=candle_color(ha_3["ha_open"],ha_3["ha_close"])
    

    
    if state["position"] is None:

        if ha_c == "GREEN" and norm_c == "GREEN":
            entry_price = last_10m["close"]
            state["position"] = {"entry_price": entry_price, "entry_time": last_10m_time}
            msg = f" BUY TRIGGERED: SENSEX @ ~{entry_price},When Both candle is green"
            state["stoploss"]=df_10m["low"].iloc[-3]
            state["signal"]="BUY"
            print(state["stoploss"])
            
            log.info(msg)
            send_telegram(msg)
            send_telegram2(msg)
            print("1")
        elif previous["WMA5"] < previous["WMA11"] and current["WMA5"] > current["WMA11"]:
            entry_price = last_10m["close"]
            state["position"] = {"entry_price": entry_price, "entry_time": last_10m_time}
            msg = f" BUY TRIGGERED: SENSEX @ ~{entry_price},by weighting moving average"
            state["stoploss"]=df_10m["low"].iloc[-3]
            state["signal"]="BUY"
        
            print(state["stoploss"])
            log.info(msg)
            send_telegram(msg)
            send_telegram2(msg)
            print("2")
        elif previous["WMA5"] > previous["WMA11"] and current["WMA5"] < current["WMA11"]:
            entry_price = last_10m["close"]
            state["position"] = {"entry_price": entry_price, "entry_time": last_10m_time}
            msg = f"SELL TRIGGERED: SENSEX @ ~{ entry_price},by both candle are red"
            state["signal"]="SELL"
            print(state["stoploss"])
            state["stoploss"]=df_10m["high"].iloc[-3]
            log.info(msg)
            send_telegram(msg)
            send_telegram2(msg)
            
            print("3")
        
        elif ha_c == "RED" and norm_c == "RED":
            entry_price = last_10m["close"]
            state["position"] = {"entry_price": entry_price, "entry_time": last_10m_time}
            state["signal"]="SELL"
            msg = f"SELL TRIGGERED: SENSEX @ ~{ entry_price},by weighted moving average"
            state["stoploss"]=df_10m["high"].iloc[-3]
            print(state["stoploss"])
            log.info(msg)
            send_telegram(msg)
            send_telegram2(msg)
            
            print("4")
    elif state["position"] is not None:
        if ha_1_n_color=="RED" and  ha_1_color=="RED" and  state["signal"]=="BUY":
            if ha_2_color=="GREEN" and ha_2_n_color=="RED" and ha_3_color=="GREEN":
                if ha_3["ha_low"]<ha_2["ha_low"]:
                    msg = ("Profit booked,sell it-->Last candle is red in both n&ha,second last candle is green in ha and red in n,third candle is green in ha,Low of second candle of ha is greater than low of third candle of ha")
                    reset_signal_keep_position2(state)
                    send_telegram(msg)
                    send_telegram2(msg)
        elif ha_2_color=="GREEN" and ha_2_n_color=="RED" and ha_3_color=="GREEN" and state["signal"]=="BUY" and state["sell_value"] is None:
            state["sell_value"]=df_10m["low"].iloc[-3]
        elif state["sell_value"] is not None and state["signal"]=="BUY":
             if df_10m["close"].iloc[-2]<state["sell_value"]:
                  reset_signal_keep_position2(state)
                 
                  msg=(f"Profit booked,sell-->Last candle is lower than {state["sell_value"]} which is third last landle low ")
                  send_telegram(msg)
                  send_telegram2(2)
        
        if ha_1_n_color=="GREEN" and  ha_1_color=="GREEN" and state["signal"]=="SELL":
            if ha_2_color=="RED" and ha_2_n_color=="GREEN" and ha_3_color=="RED":
                if ha_3["ha_high"]>ha_2["ha_high"]:
                    msg = (f"Profit booked,buy it-->Last candle is green in both n&ha,second last candle is red in ha and red in n,third candle is red in ha,high of second candle of ha is lower than high of third candle of ha")
                    reset_signal_keep_position2(state)
                    
                    send_telegram(msg)
                    send_telegram2(msg)
        elif ha_2_color=="RED" and ha_2_n_color=="GREEN" and ha_3_color=="RED" and state["signal"]=="SELL" and state["sell_value2"] is None:
                state["sell_value2"]=df_10m["low"].iloc[-3]
        elif state["sell_value2"] is not None and state["signal"]=="SELL":
            if df_10m["close"].iloc[-2] > state["sell_value"]:
                reset_signal_keep_position2(state)
                msg="Profit booked,buy"
                send_telegram(msg)
                send_telegram2(msg)

    
    
# MAIN LOOP
def main():
    log.info("Starting SENSEX Bot...")
    smart_api = login()
    state = load_state()

    last_1h_marker = None
    last_10m_marker = None
    last_login_marker = None

    send_telegram2("🤖 SENSEX Algo Bot Active.")

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
            """if current_hm in ONE_HOUR_FETCH_TIMES and last_1h_marker != current_hm:
                last_1h_marker = current_hm
                log.info(f"=== Running 1H Check for SENSEX ({current_hm}) ===")
                reset_signal_keep_position(state)
                process_1h_bias(smart_api, state)
                save_state(state)"""

            # 10-Minute Schedule Check
            if now.minute in TEN_MIN_FETCH_MINUTES and last_10m_marker != current_hm:
                last_10m_marker = current_hm
                process_10m_trigger2(smart_api, state)
                save_state(state)
                if state["position"] is not None and state["stoploss"] is not  None:
                    ltp=get_ltp(smart_api,SYMBOL_INFO)
                    if state["signal"]=="BUY":
                        if(ltp < state["stoploss"]):
                           reset_signal_keep_position2(state)
                           state["stoploss"]=None
                           msg="SToploss Hit"
                           send_telegram(msg)
                           send_telegram2(msg)
                    elif state["signal"]=="SELL":
                        if(ltp > state["stoploss"]):
                           print("rj")
                           state["stoploss"]=None
                           reset_signal_keep_position2(state)
                           msg="SToploss Hit"
                           send_telegram(msg)
                           send_telegram2(msg)
            save_state(state)
                    
                
            time.sleep(LOOP_SLEEP_SECONDS)

        except KeyboardInterrupt:
            log.info("Bot manually stopped.")
            save_state(state)
            break
        except Exception as e:
            log.error(f"SENSEX Bot error: {e}", exc_info=True)
            send_telegram(f"⚠️ SENSEX Bot Error: {e}")
            send_telegram2(f"⚠️ SENSEX Bot Error: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()
