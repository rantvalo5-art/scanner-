#!/usr/bin/env python3
"""
Crypto Signal Scanner Bot
Corre en PythonAnywhere cada X minutos
Lee config desde Supabase, calcula indicadores, manda alertas a Telegram
"""

import requests
import json
import time
import math
from datetime import datetime

# ============ CONFIG ============
SUPABASE_URL = "https://ecgdswroygkfckkaguxp.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVjZ2Rzd3JveWdrZmNra2FndXhwIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzM1MTUyNzEsImV4cCI6MjA4OTA5MTI3MX0.N_qJsJWTJaqRHpugzlnRTpoZI84mUoctt3RKmUshIrU"
TG_TOKEN = "8521701026:AAH7tNR4hMf5iR-xS9UgHnQYlrUKnd4Anl8"
TG_CHAT  = "8576536483"

SUPA_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}

BINANCE_BASE = "https://api.binance.com/api/v3"

# ============ MATH ============

def ema(prices, period):
    k = 2 / (period + 1)
    e = prices[0]
    result = []
    for p in prices:
        e = p * k + e * (1 - k)
        result.append(e)
    return result

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    ag = al = 0
    for i in range(1, period + 1):
        d = closes[i] - closes[i-1]
        if d > 0: ag += d
        else: al -= d
    ag /= period
    al /= period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i-1]
        ag = (ag * (period - 1) + max(d, 0)) / period
        al = (al * (period - 1) + max(-d, 0)) / period
    if al == 0:
        return 100
    return 100 - (100 / (1 + ag / al))

def calc_macd(closes):
    if len(closes) < 35:
        return None
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    macd_line = [a - b for a, b in zip(ema12, ema26)]
    signal = ema(macd_line[-20:], 9)
    h = macd_line[-1] - signal[-1]
    ph = macd_line[-2] - signal[-2]
    return {"h": h, "ph": ph, "up": h > 0 and h > ph, "down": h < 0 and h < ph}

def calc_ema_cross(closes):
    if len(closes) < 22:
        return None
    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    return {"bullish": e9[-1] > e21[-1]}

def calc_bb(closes, period=20):
    if len(closes) < period:
        return None
    sl = closes[-period:]
    mid = sum(sl) / period
    std = math.sqrt(sum((x - mid) ** 2 for x in sl) / period)
    upper = mid + 2 * std
    lower = mid - 2 * std
    rng = upper - lower or 1
    pct = (closes[-1] - lower) / rng
    return {"pct": pct}

def calc_obv(vols, closes):
    if len(vols) < 10:
        return None
    obv = 0
    arr = [0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]: obv += vols[i]
        elif closes[i] < closes[i-1]: obv -= vols[i]
        arr.append(obv)
    last = arr[-1]
    prev10 = arr[max(0, len(arr) - 11)]
    trend = "up" if last > prev10 else "down" if last < prev10 else "flat"
    return {"trend": trend}

def calc_spike(vols, closes):
    if len(vols) < 20:
        return None
    avg = sum(vols[-20:]) / 20
    lv = vols[-1]
    ratio = lv / avg if avg > 0 else 0
    vroc = ((lv - vols[-11]) / vols[-11] * 100) if len(vols) >= 11 and vols[-11] > 0 else 0
    up = closes[-1] >= closes[-2]
    return {"ratio": ratio, "vroc": vroc, "up": up}

def analyze(rsi, macd, em, bb, vol):
    score = 0
    if rsi is not None:
        if rsi < 35: score += 1
        elif rsi > 65: score -= 1
    if em:
        if em["bullish"]: score += 1
        else: score -= 1
    if macd:
        if macd["up"]: score += 1
        elif macd["down"]: score -= 1
    if bb:
        if bb["pct"] < 0.2: score += 1
        elif bb["pct"] > 0.8: score -= 1
    if vol:
        if vol["ratio"] > 1.3 and vol.get("up"): score += 1
        elif vol["ratio"] > 1.3 and not vol.get("up"): score -= 1
    return score

def fmt_price(p):
    if p < 0.0001: return f"{p:.7f}"
    if p < 0.01:   return f"{p:.6f}"
    if p < 1:      return f"{p:.4f}"
    if p < 1000:   return f"{p:.2f}"
    return f"{p:,.0f}"

# ============ BINANCE ============

def fetch_klines(symbol, interval="4h", limit=100):
    # 1. Try Binance global
    try:
        url = f"{BINANCE_BASE}/klines"
        params = {"symbol": f"{symbol}USDT", "interval": interval, "limit": limit}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            rows = r.json()
            return [float(row[4]) for row in rows], [float(row[5]) for row in rows]
    except: pass

    # 2. Try Binance US
    try:
        url = f"https://api.binance.us/api/v3/klines"
        params = {"symbol": f"{symbol}USDT", "interval": interval, "limit": limit}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            rows = r.json()
            return [float(row[4]) for row in rows], [float(row[5]) for row in rows]
    except: pass

    # 3. Fallback: Bybit
    # Bybit interval map: 1h=60, 4h=240, 1d=D
    interval_map = {"1h": "60", "4h": "240", "1d": "D"}
    bybit_interval = interval_map.get(interval, "240")
    url = "https://api.bybit.com/v5/market/kline"
    params = {"symbol": f"{symbol}USDT", "interval": bybit_interval, "limit": limit}
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    if data.get("retCode") != 0:
        raise Exception(f"Bybit error: {data.get('retMsg')}")
    # Bybit format: [timestamp, open, high, low, close, volume, turnover]
    # Returns newest first — reverse to oldest first
    rows = list(reversed(data["result"]["list"]))
    closes = [float(row[4]) for row in rows]   # close
    vols   = [float(row[6]) for row in rows]   # turnover in USDT
    return closes, vols



# ============ TELEGRAM ============

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT, "text": msg, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=10)
        print(f"  📱 Telegram: {msg[:60]}...")
    except Exception as e:
        print(f"  ❌ Telegram error: {e}")

# ============ SUPABASE ============

def load_state():
    url = f"{SUPABASE_URL}/rest/v1/scanner_state?id=eq.default&select=*"
    r = requests.get(url, headers=SUPA_HEADERS, timeout=10)
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    return data[0]

def save_prev_state(prev_states):
    """Save previous indicator states to Supabase for cross detection"""
    url = f"{SUPABASE_URL}/rest/v1/scanner_state?id=eq.default"
    payload = {"prev_states": json.dumps(prev_states)}
    requests.patch(url, headers=SUPA_HEADERS, json=payload, timeout=10)

def load_prev_state():
    state = load_state()
    if state and state.get("prev_states"):
        try:
            return json.loads(state["prev_states"])
        except:
            return {}
    return {}

# ============ ALERT CONFIG ============

DEFAULT_TRIGGERS = {
    "rsi_low":    {"on": False, "val": 30},
    "rsi_high":   {"on": False, "val": 70},
    "obv_up":     {"on": False},
    "obv_down":   {"on": False},
    "spike_solo": {"on": False, "val": 3.0},
    "spike_green":{"on": False, "val": 3.0},
    "macd_up":    {"on": False},
    "macd_down":  {"on": False},
    "mr":         {"on": False, "val": 35},
    "ob":         {"on": False, "val": 70},
    "strong":     {"on": False, "val": 4},
    "sell":       {"on": False, "val": -4},
}

def is_enabled(coin, trigger_type, global_triggers, coin_triggers):
    """Check if trigger is enabled globally or per coin"""
    g = global_triggers.get(trigger_type, {})
    if isinstance(g, dict) and g.get("on"):
        return True
    elif g is True:
        return True
    c = coin_triggers.get(coin, {}).get(trigger_type, False)
    return bool(c)

def get_val(trigger_type, global_triggers, default):
    g = global_triggers.get(trigger_type, {})
    if isinstance(g, dict):
        return g.get("val", default)
    return default

# ============ COOLDOWN ============

SENT_ALERTS = {}
COOLDOWN_SECS = 300  # 5 minutes default

def can_send(coin, trigger_type):
    key = f"{coin}_{trigger_type}"
    last = SENT_ALERTS.get(key, 0)
    return (time.time() - last) > COOLDOWN_SECS

def mark_sent(coin, trigger_type):
    SENT_ALERTS[f"{coin}_{trigger_type}"] = time.time()

# ============ MAIN CHECK ============

def check_coin(coin, interval, global_triggers, coin_triggers, prev_states):
    print(f"\n  Checking {coin} ({interval})...")
    try:
        closes, vols = fetch_klines(coin, interval, 100)
    except Exception as e:
        print(f"  ❌ Fetch error for {coin}: {e}")
        return {}

    rsi   = calc_rsi(closes)
    macd  = calc_macd(closes)
    em    = calc_ema_cross(closes)
    bb    = calc_bb(closes)
    vol   = calc_spike(vols, closes)
    obv   = calc_obv(vols, closes)
    score = analyze(rsi, macd, em, bb, calc_spike(vols, closes))
    price = closes[-1]
    px    = fmt_price(price)

    obv_trend  = obv["trend"] if obv else "flat"
    prev_obv   = prev_states.get(coin, {}).get("obv", "flat")
    spike_r    = vol["ratio"] if vol else 0
    macd_dir   = "up" if macd and macd["up"] else "down" if macd and macd["down"] else "flat"
    prev_macd  = prev_states.get(coin, {}).get("macd", "flat")

    rsi_str = f"{rsi:.1f}" if rsi else "N/A"
    print(f"    RSI={rsi_str} Score={score} OBV={obv_trend} Spike={spike_r:.1f}x MACD={macd_dir}")

    def chk(t): return is_enabled(coin, t, global_triggers, coin_triggers) and can_send(coin, t)
    def gv(t, d): return get_val(t, global_triggers, d)

    # RSI solo
    if rsi and rsi < gv("rsi_low", 30) and chk("rsi_low"):
        mark_sent(coin, "rsi_low")
        send_telegram(f"📉 <b>RSI BAJO</b>\n<b>{coin}/USDT</b> — ${px}\nRSI: {rsi:.1f} (oversold ✅)")

    if rsi and rsi > gv("rsi_high", 70) and chk("rsi_high"):
        mark_sent(coin, "rsi_high")
        send_telegram(f"📈 <b>RSI ALTO</b>\n<b>{coin}/USDT</b> — ${px}\nRSI: {rsi:.1f} (overbought ⚠️)")

    # OBV solo (solo cuando cambia)
    if obv_trend == "up" and prev_obv != "up" and chk("obv_up"):
        mark_sent(coin, "obv_up")
        send_telegram(f"↑ <b>OBV ACUMULANDO</b>\n<b>{coin}/USDT</b> — ${px}\nOBV cambió a tendencia alcista\nCompradores entrando silenciosamente")

    if obv_trend == "down" and prev_obv != "down" and chk("obv_down"):
        mark_sent(coin, "obv_down")
        send_telegram(f"↓ <b>OBV DISTRIBUYENDO</b>\n<b>{coin}/USDT</b> — ${px}\nOBV cambió a tendencia bajista\nVendedores saliendo silenciosamente")

    # Volume Spike solo
    if spike_r >= gv("spike_solo", 3.0) and chk("spike_solo"):
        mark_sent(coin, "spike_solo")
        vroc = vol["vroc"] if vol else 0
        send_telegram(f"⚡ <b>VOLUME SPIKE</b>\n<b>{coin}/USDT</b> — ${px}\nSpike: {spike_r:.1f}x promedio\nVROC: {vroc:+.0f}%")

    if spike_r >= gv("spike_green", 3.0) and vol and vol["up"] and chk("spike_green"):
        mark_sent(coin, "spike_green")
        send_telegram(f"⚡ <b>VOLUME SPIKE VERDE</b>\n<b>{coin}/USDT</b> — ${px}\nSpike: {spike_r:.1f}x + vela verde 🔥\nCompra masiva detectada")

    # MACD cross solo (solo cuando cruza)
    if macd_dir == "up" and prev_macd != "up" and chk("macd_up"):
        mark_sent(coin, "macd_up")
        send_telegram(f"📊 <b>MACD CRUCE ALCISTA</b>\n<b>{coin}/USDT</b> — ${px}\nHistograma cruzó de negativo a positivo\nMomentum cambia a alcista ✅")

    if macd_dir == "down" and prev_macd != "down" and chk("macd_down"):
        mark_sent(coin, "macd_down")
        send_telegram(f"📊 <b>MACD CRUCE BAJISTA</b>\n<b>{coin}/USDT</b> — ${px}\nHistograma cruzó de positivo a negativo\nMomentum cambia a bajista ⚠️")

    # Combined
    if rsi and rsi < gv("mr", 35) and obv_trend == "up" and chk("mr"):
        mark_sent(coin, "mr")
        send_telegram(f"🔥 <b>MEAN REVERSION</b>\n<b>{coin}/USDT</b> — ${px}\nRSI: {rsi:.1f} ✅  OBV: acumulando ↑\nPuntaje: {score:+d}")

    if rsi and rsi > gv("ob", 70) and obv_trend == "down" and chk("ob"):
        mark_sent(coin, "ob")
        send_telegram(f"🚨 <b>OVERBOUGHT COMBO</b>\n<b>{coin}/USDT</b> — ${px}\nRSI: {rsi:.1f} ⚠️  OBV: distribuyendo ↓")

    if score >= gv("strong", 4) and chk("strong"):
        mark_sent(coin, "strong")
        rsi_str = f"{rsi:.1f}" if rsi else "—"
        send_telegram(f"💎 <b>STRONG BUY</b>\n<b>{coin}/USDT</b> — ${px}\nPuntaje: <b>{score:+d}/+5</b>\nRSI: {rsi_str}")

    if score <= gv("sell", -4) and chk("sell"):
        mark_sent(coin, "sell")
        send_telegram(f"📉 <b>STRONG SELL</b>\n<b>{coin}/USDT</b> — ${px}\nPuntaje: <b>{score}/-5</b>")

    # Return new state for this coin
    return {"obv": obv_trend, "macd": macd_dir, "rsi": rsi, "score": score}

# ============ RUN ============

def run():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*50}")
    print(f"🤖 Scanner Bot — {now}")
    print(f"{'='*50}")

    # Load state from Supabase
    try:
        state = load_state()
    except Exception as e:
        print(f"❌ Supabase error: {e}")
        return

    if not state:
        print("❌ No state found in Supabase. Run the scanner HTML first to set up.")
        return

    coins = state.get("coins") or []
    if not coins:
        print("⚠️  No coins configured. Add coins in the scanner first.")
        return

    timeframe = state.get("timeframe", "4h")

    # Load alert config — saved by HTML scanner via Supabase
    # global_triggers and coin_triggers are stored as JSON in the DB
    raw_global = state.get("global_triggers") or {}
    raw_coin   = state.get("coin_triggers") or {}

    if isinstance(raw_global, str):
        try: raw_global = json.loads(raw_global)
        except: raw_global = {}
    if isinstance(raw_coin, str):
        try: raw_coin = json.loads(raw_coin)
        except: raw_coin = {}

    # Load previous indicator states (for cross detection)
    prev_states = load_prev_state()

    print(f"📊 Coins: {coins}")
    print(f"⏱  Timeframe: {timeframe}")
    print(f"🔔 Active global triggers: {[k for k,v in raw_global.items() if (v.get('on') if isinstance(v,dict) else v)]}")

    new_states = {}
    for coin in coins:
        new_state = check_coin(coin, timeframe, raw_global, raw_coin, prev_states)
        if new_state:
            new_states[coin] = new_state
        time.sleep(0.5)  # Be nice to Binance API

    # Save new states back to Supabase
    try:
        save_prev_state(new_states)
        print(f"\n✅ States saved to Supabase")
    except Exception as e:
        print(f"⚠️  Could not save states: {e}")

    print(f"\n✅ Done — checked {len(coins)} coins")

if __name__ == "__main__":
    run()
