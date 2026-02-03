import yfinance as yf
import pandas as pd
import pandas_ta as ta
import requests
import datetime
import json
import os
import io

# ================= CONFIG =================
ML_TRADES_FILE = "mltrades.json"
MIN_SCORE = 8.5

# ================= STOCK LOADER =================
def fetch_live_nifty_stocks():
    url = "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            df = pd.read_csv(io.StringIO(r.content.decode("utf-8")))
            return {f"{row['Symbol']}.NS": row.get("Industry", "Unknown")
                    for _, row in df.iterrows()}
    except:
        pass

    return {
        "HDFCBANK.NS": "Bank",
        "RELIANCE.NS": "Energy",
        "INFY.NS": "IT",
        "SBIN.NS": "Bank"
    }

STOCKS = fetch_live_nifty_stocks()

# ================= JSON HELPERS =================
def load_json(file):
    if not os.path.exists(file):
        return []
    try:
        with open(file, "r") as f:
            return json.load(f)
    except:
        return []

def save_json(file, data):
    with open(file, "w") as f:
        json.dump(data, f, indent=4)

# ================= AUTO TRADE CLOSER =================
def auto_close_trades(trades):
    print("🔄 Checking open ML trades...")

    for trade in trades:
        if trade["status"] != "OPEN":
            continue

        ticker = trade["symbol"] + ".NS"
        try:
            df = yf.download(ticker, period="5d", interval="1d", progress=False)
            if df.empty:
                continue

            last_close = df["Close"].iloc[-1]

            if last_close <= trade["sl"]:
                trade["status"] = "LOSS"
                trade["exit_price"] = round(last_close, 1)
                trade["exit_date"] = str(datetime.date.today())
                print(f"❌ ML LOSS: {trade['symbol']}")

            elif last_close >= trade["target"]:
                trade["status"] = "WIN"
                trade["exit_price"] = round(last_close, 1)
                trade["exit_date"] = str(datetime.date.today())
                print(f"✅ ML WIN: {trade['symbol']}")

        except Exception as e:
            print(f"Error closing {trade['symbol']}: {e}")

    return trades

# ================= ANALYSIS ENGINE =================
def analyze_stock(ticker, sector, nifty_trend):
    try:
        df = yf.download(ticker, period="2y", progress=False)
        if df.empty or len(df) < 300:
            return None

        if isinstance(df.columns, pd.MultiIndex):
            df = df.xs(ticker, level=1, axis=1)

        # Weekly trend
        weekly = df.resample("W").agg({"Close": "last"})
        weekly["EMA50"] = ta.ema(weekly["Close"], 50)
        if weekly["Close"].iloc[-1] < weekly["EMA50"].iloc[-1]:
            return None

        # Indicators
        df["EMA20"] = ta.ema(df["Close"], 20)
        df["EMA200"] = ta.ema(df["Close"], 200)
        df["RSI"] = ta.rsi(df["Close"], 14)
        df["ATR"] = ta.atr(df["High"], df["Low"], df["Close"], 14)
        adx = ta.adx(df["High"], df["Low"], df["Close"], 14)
        df = pd.concat([df, adx], axis=1)

        df["OBV"] = ta.obv(df["Close"], df["Volume"])
        df["OBV_EMA"] = ta.ema(df["OBV"], 20)

        df.dropna(inplace=True)
        curr = df.iloc[-1]
        avg_vol = df["Volume"].rolling(20).mean().iloc[-1]

        if curr["Close"] < curr["EMA200"]:
            return None

        adx_col = [c for c in df.columns if "ADX_14" in c][0]
        if curr[adx_col] < 20:
            return None

        last_10 = df.tail(10)
        green_vol = last_10[last_10["Close"] > last_10["Open"]]["Volume"].sum()
        red_vol = last_10[last_10["Close"] < last_10["Open"]]["Volume"].sum() or 1
        buy_pressure = green_vol / red_vol

        score = 5.0
        if nifty_trend == "BULLISH":
            score += 0.5
        if buy_pressure > 2:
            score += 1.5
        if curr["OBV"] > curr["OBV_EMA"]:
            score += 0.5
        if curr[adx_col] > 25:
            score += 1

        setup = None
        range_high = df.iloc[-11:-1]["High"].max()

        if curr["Close"] > range_high and curr["Volume"] > 1.5 * avg_vol:
            setup = "BREAKOUT"
            score += 2
        elif abs(curr["Close"] - curr["EMA20"]) / curr["Close"] < 0.03:
            setup = "PULLBACK"
            score += 1.5

        if not setup or score < MIN_SCORE:
            return None

        sl = max(curr["Close"] - 2 * curr["ATR"], curr["Close"] * 0.92)
        target = curr["Close"] + (curr["Close"] - sl) * 2

        features = {
            "rsi": float(curr["RSI"]),
            "adx": float(curr[adx_col]),
            "ema20_dist": float((curr["Close"] - curr["EMA20"]) / curr["Close"]),
            "ema200_dist": float((curr["Close"] - curr["EMA200"]) / curr["Close"]),
            "volume_ratio": float(curr["Volume"] / avg_vol),
            "atr_pct": float(curr["ATR"] / curr["Close"]),
            "buy_pressure": float(buy_pressure),
            "market_trend": 1 if nifty_trend == "BULLISH" else 0
        }

        return {
            "symbol": ticker.replace(".NS", ""),
            "entry": round(curr["Close"], 1),
            "sl": round(sl, 1),
            "target": round(target, 1),
            "features": features
        }

    except Exception as e:
        print(f"Error {ticker}: {e}")
        return None

# ================= RUNNER =================
def run_scan():
    print("🧠 ML Silent Engine Started")

    trades = load_json(ML_TRADES_FILE)

    # 1️⃣ Auto-close existing trades
    trades = auto_close_trades(trades)

    nifty_trend = "NEUTRAL"
    try:
        nifty = yf.download("^NSEI", period="1y", progress=False)["Close"]
        nifty_trend = "BULLISH" if nifty.iloc[-1] > ta.ema(nifty, 50).iloc[-1] else "BEARISH"
    except:
        pass

    open_symbols = {t["symbol"] for t in trades if t["status"] == "OPEN"}

    # 2️⃣ Scan for new ML trades
    for ticker, sector in STOCKS.items():
        symbol = ticker.replace(".NS", "")
        if symbol in open_symbols:
            continue

        data = analyze_stock(ticker, sector, nifty_trend)
        if not data:
            continue

        trade = {
            "symbol": data["symbol"],
            "entry": data["entry"],
            "target": data["target"],
            "sl": data["sl"],
            "date": str(datetime.date.today()),
            "status": "OPEN",
            "features": data["features"]
        }

        trades.append(trade)
        print(f"📘 ML trade added: {data['symbol']}")

    save_json(ML_TRADES_FILE, trades)
    print("✅ ML cycle complete")

if __name__ == "__main__":
    run_scan()
