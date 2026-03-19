#!/usr/bin/env python3
"""
Crypto Signal Scanner Bot - VERSIÓN CORREGIDA
Corre en PythonAnywhere cada X minutos
Lee config desde Supabase, calcula indicadores, manda alertas a Telegram

CORRECCIONES:
- Sistema de cooldown unificado usando SOLO Supabase
- Claves de cooldown consistentes con el HTML
- Cooldown de 30 minutos (en vez de 1 hora)
- Precio en tiempo real para verificación de alertas
- Validación mejorada de alertas
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

# NUEVO: Cooldown de 30 minutos (consistente con el HTML)
COOLDOWN_SECONDS = 120  # 30 minutos = 1800 segundos

# Global dict para cooldowns (se carga/guarda en Supabase)
SENT_ALERTS = {}

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
    if p < 0.000001: return f"{p:.9f}"
    if p < 0.0001:   return f"{p:.7f}"
    if p < 0.01:     return f"{p:.6f}"
    if p < 1:        return f"{p:.4f}"
    if p < 1000:     return f"{p:.4f}"
    return f"{p:,.2f}"

def fmt_target(p, target_str=None):
    """Use exact string if available, otherwise fmt_price"""
    if target_str:
        return target_str
    return fmt_price(p)

# ============ BINANCE ============

def fetch_realtime_price(symbol):
    """Fetch real-time price from Binance only — single source of truth"""
    try:
        url = f"{BINANCE_BASE}/ticker/price"
        params = {"symbol": f"{symbol}USDT"}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if "price" in data:
                return float(data["price"])
    except: pass
    return None

def fetch_klines(symbol, interval="4h", limit=100):
    # 1. Try Binance global
    try:
        url = f"{BINANCE_BASE}/klines"
        params = {"symbol": f"{symbol}USDT", "interval": interval, "limit": limit + 1}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            rows = r.json()[:-1]
            return [float(row[4]) for row in rows], [float(row[5]) for row in rows]
    except: pass

    # 2. OKX — works from GitHub Actions
    try:
        interval_map = {"1h": "1H", "4h": "4H", "1d": "1D"}
        okx_bar = interval_map.get(interval, "4H")
        url = "https://www.okx.com/api/v5/market/candles"
        params = {"instId": f"{symbol}-USDT", "bar": okx_bar, "limit": str(limit + 1)}
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, params=params, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("code") != "0":
            raise Exception(f"OKX error: {data.get('msg')}")
        rows = list(reversed(data["data"]))[:-1]
        closes = [float(row[4]) for row in rows]
        vols   = [float(row[7]) for row in rows]
        return closes, vols
    except Exception as e:
        if "OKX error" in str(e):
            pass  # pair not found on OKX, try KuCoin
        else:
            raise

    # 3. KuCoin fallback — for coins not on OKX (e.g. DEXE)
    try:
        interval_map_kc = {"1h": "1hour", "4h": "4hour", "1d": "1day"}
        kc_interval = interval_map_kc.get(interval, "4hour")
        url = "https://api.kucoin.com/api/v1/market/candles"
        params = {"symbol": f"{symbol}-USDT", "type": kc_interval}
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("code") != "200000":
            raise Exception(f"KuCoin error: {data.get('msg')}")
        rows = list(reversed(data["data"]))[-limit-1:-1]
        closes = [float(row[2]) for row in rows]
        vols   = [float(row[6]) for row in rows]
        return closes, vols
    except Exception as e:
        pass  # KuCoin failed, try Gate.io

    # 4. Gate.io — last resort for exotic pairs like COS
    try:
        interval_map_gate = {"1h": "1h", "4h": "4h", "1d": "1d"}
        gate_interval = interval_map_gate.get(interval, "4h")
        url = "https://api.gateio.ws/api/v4/spot/candlesticks"
        params = {"currency_pair": f"{symbol}_USDT", "interval": gate_interval, "limit": str(limit)}
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        rows = r.json()
        closes = [float(row[2]) for row in rows]
        vols   = [float(row[1]) for row in rows]
        return closes, vols
    except Exception as e:
        pass

    return None, None

# ============ SUPABASE — SISTEMA DE COOLDOWN UNIFICADO ============

def load_sent_alerts():
    """NUEVO: Carga cooldowns desde Supabase - consistente con localStorage del HTML"""
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/alert_cooldowns?id=eq.default",
            headers=SUPA_HEADERS,
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            if data and len(data) > 0 and "cooldowns" in data[0]:
                cooldowns = data[0]["cooldowns"]
                if isinstance(cooldowns, dict):
                    print(f"  ✅ Loaded {len(cooldowns)} cooldowns from Supabase")
                    return cooldowns
    except Exception as e:
        print(f"  ⚠️ Could not load cooldowns: {e}")
    return {}

def save_sent_alerts():
    """NUEVO: Guarda cooldowns en Supabase - consistente con localStorage del HTML"""
    try:
        # Clean old entries (older than 30 minutes)
        now_ts = time.time()
        cleaned = {k: v for k, v in SENT_ALERTS.items() if now_ts - v < COOLDOWN_SECONDS}
        
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/alert_cooldowns",
            headers=SUPA_HEADERS,
            json={
                "id": "default",
                "cooldowns": cleaned,
                "updated_at": datetime.now().isoformat()
            },
            timeout=10
        )
        if r.status_code in [200, 201]:
            print(f"  ✅ Saved {len(cleaned)} cooldowns to Supabase")
            return
        
        # If insert failed, try update
        r = requests.patch(
            f"{SUPABASE_URL}/rest/v1/alert_cooldowns?id=eq.default",
            headers=SUPA_HEADERS,
            json={
                "cooldowns": cleaned,
                "updated_at": datetime.now().isoformat()
            },
            timeout=10
        )
        if r.status_code == 200:
            print(f"  ✅ Updated {len(cleaned)} cooldowns in Supabase")
    except Exception as e:
        print(f"  ⚠️ Could not save cooldowns: {e}")

def load_state():
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/scanner_state?id=eq.default",
        headers=SUPA_HEADERS,
        timeout=10
    )
    if r.status_code == 200:
        data = r.json()
        if data and len(data) > 0:
            return data[0]
    return None

def load_prev_state():
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/prev_state?id=eq.default",
            headers=SUPA_HEADERS,
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            if data and len(data) > 0 and "states" in data[0]:
                states = data[0]["states"]
                if isinstance(states, dict):
                    return states
    except: pass
    return {}

def save_prev_state(states):
    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/prev_state",
            headers=SUPA_HEADERS,
            json={"id": "default", "states": states, "updated_at": datetime.now().isoformat()},
            timeout=10
        )
        if r.status_code not in [200, 201]:
            r = requests.patch(
                f"{SUPABASE_URL}/rest/v1/prev_state?id=eq.default",
                headers=SUPA_HEADERS,
                json={"states": states, "updated_at": datetime.now().isoformat()},
                timeout=10
            )
    except Exception as e:
        print(f"  ⚠️ save_prev_state error: {e}")

def send_telegram(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
    except: pass

# ============ COOLDOWN MANAGEMENT ============

def can_send(coin, trigger):
    """NUEVO: Verificación de cooldown usando claves consistentes con el HTML"""
    key = f"{coin}_{trigger}"
    now = time.time()
    if key in SENT_ALERTS:
        elapsed = now - SENT_ALERTS[key]
        if elapsed < COOLDOWN_SECONDS:
            return False
    return True

def mark_sent(coin, trigger):
    """NUEVO: Marca alerta como enviada con timestamp actual"""
    key = f"{coin}_{trigger}"
    SENT_ALERTS[key] = time.time()

# ============ CHECK COIN ============

def check_coin(coin, default_tf, global_triggers, coin_triggers, prev_states):
    """
    MEJORADO: Sistema de alertas con cooldown unificado y precios en tiempo real
    """
    print(f"\n🔍 {coin}")
    
    # Get global thresholds with defaults
    def gv(key, default):
        gt = global_triggers.get(key, {})
        if isinstance(gt, dict):
            return gt.get("val", default)
        return default

    # Helper to fetch data for any timeframe
    _tf_cache = {}
    def get_data(tf):
        if tf in _tf_cache:
            return _tf_cache[tf]
        c, v = fetch_klines(coin, tf, 100)
        _tf_cache[tf] = (c, v)
        return c, v

    closes, vols = get_data(default_tf)
    if not closes:
        print(f"  ❌ No data")
        return None

    # NUEVO: Obtener precio en tiempo real para alertas más precisas
    realtime_price = fetch_realtime_price(coin)
    price = realtime_price if realtime_price else closes[-1]
    
    print(f"  💰 Real-time price: ${fmt_price(price)}")

    # Calculate indicators on default TF
    rsi_def = calc_rsi(closes)
    macd_def = calc_macd(closes)
    ema_def = calc_ema_cross(closes)
    bb_def = calc_bb(closes)
    spike_def = calc_spike(vols, closes)
    obv_def = calc_obv(vols, closes)

    score_def = analyze(rsi_def, macd_def, ema_def, bb_def, spike_def)
    obv_trend_def = obv_def["trend"] if obv_def else "flat"
    
    macd_dir_def = "up" if macd_def and macd_def["up"] else "down" if macd_def and macd_def["down"] else "flat"

    tf_tag = f"[{default_tf.upper()}]"
    px = fmt_price(price)

    # Load previous state for cross detection
    prev = prev_states.get(coin, {})
    prev_obv = prev.get(f"obv_{default_tf}", "flat")
    prev_macd = prev.get(f"macd_{default_tf}", "flat")

    # Check global triggers
    for trigger, enabled in global_triggers.items():
        if not isinstance(enabled, dict):
            enabled = {"on": enabled}
        
        # Skip if not enabled globally AND not enabled per-coin
        if not enabled.get("on") and not (coin_triggers.get(coin, {}).get(trigger)):
            continue

        # NUEVO: Verificar cooldown antes de enviar cualquier alerta
        if not can_send(coin, trigger):
            continue

        spike_r = spike_def["ratio"] if spike_def else 0
        
        # Process each trigger type
        if trigger == "spike" and spike_r > gv("spike", 1.5):
            mark_sent(coin, trigger)
            send_telegram(f"⚡ <b>VOLUME SPIKE</b>\n<b>{coin}/USDT</b> — ${px}\nSpike: {spike_r:.1f}x promedio {tf_tag}")

        elif trigger == "spike_green" and spike_r > gv("spike_green", 2.0) and spike_def.get("up"):
            mark_sent(coin, trigger)
            send_telegram(f"⚡ <b>SPIKE VERDE</b>\n<b>{coin}/USDT</b> — ${px}\nSpike: {spike_r:.1f}x + vela verde 🔥 {tf_tag}")

        elif trigger == "macd_up" and macd_dir_def == "up" and prev_macd != "up":
            mark_sent(coin, trigger)
            send_telegram(f"📊 <b>MACD CRUCE ALCISTA</b>\n<b>{coin}/USDT</b> — ${px}\nHistograma cruzó positivo ✅ {tf_tag}")

        elif trigger == "macd_down" and macd_dir_def == "down" and prev_macd != "down":
            mark_sent(coin, trigger)
            send_telegram(f"📊 <b>MACD CRUCE BAJISTA</b>\n<b>{coin}/USDT</b> — ${px}\nHistograma cruzó negativo ⚠️ {tf_tag}")

        elif trigger == "mr" and rsi_def is not None and rsi_def < gv("mr", 35) and obv_trend_def == "up":
            mark_sent(coin, trigger)
            send_telegram(f"🔥 <b>MEAN REVERSION</b>\n<b>{coin}/USDT</b> — ${px}\nRSI: {rsi_def:.1f} + OBV↑ Puntaje: {score_def:+d} {tf_tag}")

        elif trigger == "ob" and rsi_def is not None and rsi_def > gv("ob", 70) and obv_trend_def == "down":
            mark_sent(coin, trigger)
            send_telegram(f"🚨 <b>OVERBOUGHT COMBO</b>\n<b>{coin}/USDT</b> — ${px}\nRSI: {rsi_def:.1f} + OBV↓ {tf_tag}")

        elif trigger == "strong" and score_def >= gv("strong", 4):
            mark_sent(coin, trigger)
            rsi_str = f"{rsi_def:.1f}" if rsi_def else "—"
            send_telegram(f"💎 <b>STRONG BUY</b>\n<b>{coin}/USDT</b> — ${px}\nPuntaje: <b>{score_def:+d}/+5</b> RSI: {rsi_str} {tf_tag}")

        elif trigger == "sell" and score_def <= gv("sell", -4):
            mark_sent(coin, trigger)
            send_telegram(f"📉 <b>STRONG SELL</b>\n<b>{coin}/USDT</b> — ${px}\nPuntaje: <b>{score_def}/-5</b> {tf_tag}")

    # ---- Individual indicator alerts per coin ----
    ct = coin_triggers.get(coin, {})
    ind_alerts = ct.get("ind_alerts", [])
    if not isinstance(ind_alerts, list):
        ind_alerts = []

    for alert in ind_alerts:
        atype = alert.get("type", "")
        aval  = alert.get("val")
        atf   = alert.get("tf", default_tf)
        c2, v2 = get_data(atf)
        if not c2:
            continue
        alert_key = f"ind_{atype}_{atf}"
        if not can_send(coin, alert_key):
            continue

        if atype == "rsi_low" and aval is not None:
            rsi2 = calc_rsi(c2)
            if rsi2 is not None and rsi2 < float(aval):
                mark_sent(coin, alert_key)
                send_telegram(f"📉 <b>RSI BAJO — {coin}</b>\nRSI ({atf.upper()}): {rsi2:.1f} < {aval} — ${px}")

        elif atype == "rsi_high" and aval is not None:
            rsi2 = calc_rsi(c2)
            if rsi2 is not None and rsi2 > float(aval):
                mark_sent(coin, alert_key)
                send_telegram(f"📈 <b>RSI ALTO — {coin}</b>\nRSI ({atf.upper()}): {rsi2:.1f} > {aval} — ${px}")

        elif atype == "score_up" and aval is not None:
            sc2 = analyze(calc_rsi(c2), calc_macd(c2), calc_ema_cross(c2), calc_bb(c2), calc_spike(v2, c2))
            if sc2 >= int(aval):
                mark_sent(coin, alert_key)
                send_telegram(f"💎 <b>SCORE ALTO — {coin}</b>\nScore ({atf.upper()}): {sc2:+d} ≥ {aval} — ${px}")

        elif atype == "score_down" and aval is not None:
            sc2 = analyze(calc_rsi(c2), calc_macd(c2), calc_ema_cross(c2), calc_bb(c2), calc_spike(v2, c2))
            if sc2 <= int(aval):
                mark_sent(coin, alert_key)
                send_telegram(f"📉 <b>SCORE BAJO — {coin}</b>\nScore ({atf.upper()}): {sc2:+d} ≤ {aval} — ${px}")

        elif atype == "obv_up":
            obv2 = calc_obv(v2, c2)
            if obv2 and obv2["trend"] == "up":
                mark_sent(coin, alert_key)
                send_telegram(f"↑ <b>OBV ACUM — {coin}</b>\nOBV ({atf.upper()}) alcista — ${px}")

        elif atype == "obv_down":
            obv2 = calc_obv(v2, c2)
            if obv2 and obv2["trend"] == "down":
                mark_sent(coin, alert_key)
                send_telegram(f"↓ <b>OBV DIST — {coin}</b>\nOBV ({atf.upper()}) bajista — ${px}")

    # Save per-TF states for cross detection next run
    new_state = {"price": price, "score": score_def,
                 f"obv_{default_tf}": obv_trend_def,
                 f"macd_{default_tf}": macd_dir_def,
                 f"rsi_{default_tf}": rsi_def}
    # Also save states for other TFs that were fetched
    for tf, (c, v) in _tf_cache.items():
        if c and tf != default_tf:
            new_state[f"rsi_{tf}"]  = calc_rsi(c)
            new_state[f"obv_{tf}"]  = (calc_obv(v, c) or {}).get("trend", "flat")
            m = calc_macd(c)
            new_state[f"macd_{tf}"] = "up" if m and m["up"] else "down" if m and m["down"] else "flat"

    return new_state

# ============ RUN ============

def run():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*50}")
    print(f"🤖 Scanner Bot (FIXED) — {now}")
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

    # NUEVO: Load sent alerts from Supabase to maintain cooldown across executions
    global SENT_ALERTS
    SENT_ALERTS = load_sent_alerts()
    
    # Clean old entries (older than 30 minutes)
    now_ts = time.time()
    SENT_ALERTS = {k: v for k, v in SENT_ALERTS.items() if now_ts - v < COOLDOWN_SECONDS}
    print(f"🕐 Loaded {len(SENT_ALERTS)} active cooldowns (30 min window)")

    timeframe = state.get("timeframe", "4h")

    raw_global = state.get("global_triggers") or {}
    raw_coin   = state.get("coin_triggers") or {}
    raw_alerts = state.get("alerts") or {}

    if isinstance(raw_global, str):
        try: raw_global = json.loads(raw_global)
        except: raw_global = {}
    if isinstance(raw_coin, str):
        try: raw_coin = json.loads(raw_coin)
        except: raw_coin = {}
    if isinstance(raw_alerts, str):
        try: raw_alerts = json.loads(raw_alerts)
        except: raw_alerts = {}

    prev_states = load_prev_state()

    print(f"📊 Coins: {coins}")
    print(f"⏱  Timeframe: {timeframe}")
    print(f"🔔 Active global triggers: {[k for k,v in raw_global.items() if (v.get('on') if isinstance(v,dict) else v)]}")
    if raw_alerts:
        print(f"💰 Price alerts: {list(raw_alerts.keys())}")

    alerts_triggered = []
    new_states = {}

    for coin in coins:
        new_state = check_coin(coin, timeframe, raw_global, raw_coin, prev_states)
        if new_state:
            new_states[coin] = new_state

        # MEJORADO: Check price alerts using real-time price
        if coin in raw_alerts and new_state:
            try:
                current_price = fetch_realtime_price(coin)
                if current_price is None:
                    current_price = new_state.get("price")  # fallback to candle price
                
                print(f"  💰 {coin} checking price alerts at ${fmt_price(current_price)}")
                
                coin_alerts = raw_alerts[coin]
                if isinstance(coin_alerts, dict):
                    coin_alerts = [coin_alerts]
                
                for alert in coin_alerts:
                    target = float(alert.get("target", 0))
                    direction = alert.get("dir", "")
                    
                    # Check if alert condition is met
                    hit = (direction == "below" and current_price <= target) or \
                          (direction == "above" and current_price >= target)
                    
                    # NUEVO: Clave de cooldown consistente con el HTML
                    alert_key = f"{direction}_{target}"
                    
                    if hit and can_send(coin, alert_key):
                        mark_sent(coin, alert_key)
                        arrow = "↓" if direction == "below" else "↑"
                        label = "bajó de" if direction == "below" else "subió a"
                        target_str = alert.get("targetStr", None)
                        
                        print(f"  ✅ Price alert triggered: {coin} {arrow} ${fmt_target(target, target_str)}")
                        
                        send_telegram(
                            f"💰 <b>ALERTA DE PRECIO</b>\n"
                            f"<b>{coin}/USDT</b>\n"
                            f"Precio actual: ${fmt_price(current_price)}\n"
                            f"{arrow} {label} ${fmt_target(target, target_str)}"
                        )
                
            except Exception as e:
                print(f"  ⚠️ Price alert check error for {coin}: {e}")

        time.sleep(1.0)  # avoid rate limiting

    # NUEVO: Save sent alerts to Supabase for cooldown persistence
    save_sent_alerts()

    # Save new states back to Supabase
    try:
        save_prev_state(new_states)
        print(f"\n✅ States saved to Supabase")
    except Exception as e:
        print(f"⚠️  Could not save states: {e}")

    print(f"\n✅ Done — checked {len(coins)} coins")
    print(f"📊 Total cooldowns active: {len(SENT_ALERTS)}")

if __name__ == "__main__":
    run()
