#!/usr/bin/env python3
"""
Crypto Signal Scanner Bot - VERSIÓN 7 ML ENHANCED
===================================================
✅ Basado en scanner_bot_6 (tu versión actual)
✅ Agrega ML (RandomForest) para filtrar falsos positivos
✅ Agrega Multi-Timeframe Confirmation (1H + 4H + 1D)
✅ Agrega Confirmation Candles (persistido en Supabase)
❌ WebSocket REMOVIDO (no funciona en GitHub Actions)
   → Ya tienes fetch_realtime_price() que hace lo mismo via API REST

DEPENDENCIAS NUEVAS (agregar al workflow .yml):
  pip install scikit-learn numpy --break-system-packages

NOTA: Si scikit-learn no está disponible, el bot corre igual
      sin ML (modo degradado seguro).
"""

import requests
import json
import time
import math
from datetime import datetime
from collections import defaultdict

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
COOLDOWN_SECONDS = 1800  # 30 minutos

# Global dict para cooldowns
SENT_ALERTS = {}

# ============ ML - IMPORTACIÓN SEGURA ============
# Si no está instalado, el bot corre igual sin ML

try:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.preprocessing import StandardScaler
    import numpy as np
    ML_AVAILABLE = True
    print("✅ scikit-learn disponible — ML habilitado")
except ImportError:
    ML_AVAILABLE = False
    print("⚠️  scikit-learn no instalado — ML deshabilitado (bot funciona igual)")


class CryptoMLPredictor:
    """
    Predictor ML que filtra señales ruidosas.
    Entrenado con datos sintéticos en bootstrap.
    En el futuro se puede reemplazar con datos reales históricos.
    """

    def __init__(self):
        self.model = None
        self.scaler = None
        self.is_trained = False

    def train(self):
        """Entrena el modelo con datos sintéticos para bootstrap"""
        if not ML_AVAILABLE:
            return False

        print("🤖 Entrenando modelo ML...")

        X_train = []
        y_train = []

        rng = np.random.default_rng(42)  # Seed fijo para reproducibilidad

        for _ in range(1000):
            rsi      = rng.uniform(0, 100)
            macd_h   = rng.uniform(-2, 2)
            ema_bull = rng.choice([0, 1])
            bb_pct   = rng.uniform(0, 1)
            spike_r  = rng.uniform(0.5, 3)
            obv_up   = rng.choice([0, 1])
            momentum = rng.uniform(-0.1, 0.1)
            volatil  = rng.uniform(0, 0.1)

            features = [rsi, macd_h, ema_bull, bb_pct, spike_r, obv_up, momentum, volatil]

            # Label: alcista si RSI bajo + OBV acumulando + momentum positivo
            label = 1 if (rsi < 35 and obv_up == 1 and momentum > 0) else 0

            X_train.append(features)
            y_train.append(label)

        X = np.array(X_train)
        y = np.array(y_train)

        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)

        self.model = RandomForestClassifier(
            n_estimators=100,
            max_depth=10,
            random_state=42
        )
        self.model.fit(X_scaled, y)
        self.is_trained = True
        print("✅ Modelo ML entrenado")
        return True

    def predict(self, closes, vols, rsi, macd, ema_cross, bb, spike, obv):
        """
        Predice probabilidad de movimiento alcista.
        Retorna float entre 0 y 1. Default 0.5 si no hay modelo.
        """
        if not self.is_trained or not ML_AVAILABLE:
            return 0.5

        try:
            momentum = (closes[-1] - closes[-5]) / closes[-5] if len(closes) >= 5 else 0
            volatil  = float(np.std(closes[-20:]) / np.mean(closes[-20:])) if len(closes) >= 20 else 0

            features = [
                rsi if rsi is not None else 50,
                macd["h"] if macd else 0,
                1 if ema_cross and ema_cross["bullish"] else 0,
                bb["pct"] if bb else 0.5,
                spike["ratio"] if spike else 1,
                1 if obv and obv["trend"] == "up" else 0,
                momentum,
                volatil,
            ]

            X = self.scaler.transform([features])
            prob = self.model.predict_proba(X)[0][1]
            return float(prob)
        except Exception as e:
            print(f"  ⚠️ ML predict error: {e}")
            return 0.5


# Instancia global del predictor
ml_predictor = CryptoMLPredictor()


# ============ CONFIRMATION CANDLES (persistido en Supabase) ============
# Usamos Supabase para persistir el estado entre runs de GitHub Actions
# (en memoria sería inútil porque cada run es un proceso nuevo)

def load_confirmation_state():
    """Carga estado de confirmaciones desde Supabase"""
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/confirmation_state?id=eq.default",
            headers=SUPA_HEADERS,
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            if data and "state" in data[0]:
                return data[0]["state"] or {}
    except Exception as e:
        print(f"  ⚠️ No se pudo cargar confirmation_state: {e}")
    return {}


def save_confirmation_state(state_dict):
    """Guarda estado de confirmaciones en Supabase"""
    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/confirmation_state",
            headers=SUPA_HEADERS,
            json={"id": "default", "state": state_dict, "updated_at": datetime.now().isoformat()},
            timeout=10
        )
        if r.status_code not in [200, 201]:
            requests.patch(
                f"{SUPABASE_URL}/rest/v1/confirmation_state?id=eq.default",
                headers=SUPA_HEADERS,
                json={"state": state_dict, "updated_at": datetime.now().isoformat()},
                timeout=10
            )
    except Exception as e:
        print(f"  ⚠️ No se pudo guardar confirmation_state: {e}")


def check_confirmation(conf_state, coin, signal_type, current_signal, required=2):
    """
    Verifica si una señal se confirmó N veces consecutivas.
    Modifica conf_state in-place.

    Args:
        conf_state: dict cargado de Supabase (se modifica)
        coin: "BTC"
        signal_type: "strong", "mr", etc
        current_signal: True/False
        required: cuántas veces consecutivas se necesita (default 2)

    Returns:
        bool: True si confirmado
    """
    key = f"{coin}_{signal_type}"
    now = time.time()

    entry = conf_state.get(key, {"count": 0, "signal": None, "last_check": 0})

    # Si pasó más de 20 minutos desde el último check, resetear
    if now - entry.get("last_check", 0) > 1200:
        entry = {"count": 0, "signal": None, "last_check": 0}

    entry["last_check"] = now

    if current_signal:
        if entry.get("signal") == True:
            entry["count"] = entry.get("count", 0) + 1
        else:
            entry["signal"] = True
            entry["count"] = 1
    else:
        entry["count"] = 0
        entry["signal"] = None

    conf_state[key] = entry
    return entry["count"] >= required


# ============ MATH (igual que v6) ============

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
    if target_str:
        return target_str
    return fmt_price(p)


# ============ MULTI-TIMEFRAME CONFIRMATION ============

def check_multi_tf(coin, default_tf, required_positive=2, min_score=2):
    """
    Verifica alineación en múltiples timeframes.
    
    Args:
        coin: "BTC"
        default_tf: el TF principal del config ("4h", "1h", etc)
        required_positive: cuántos TFs deben tener score >= min_score
        min_score: score mínimo para considerar un TF como "positivo"
    
    Returns:
        (confirmed: bool, scores: dict)
    """
    # Siempre incluye el TF principal + los estándar
    timeframes = list(dict.fromkeys(["1h", "4h", "1d", default_tf]))
    scores = {}

    for tf in timeframes:
        closes, vols = fetch_klines(coin, tf, 100)
        if not closes:
            continue
        rsi   = calc_rsi(closes)
        macd  = calc_macd(closes)
        em    = calc_ema_cross(closes)
        bb    = calc_bb(closes)
        spike = calc_spike(vols, closes)
        scores[tf] = analyze(rsi, macd, em, bb, spike)

    if len(scores) < 2:
        # No hay suficientes datos para confirmar → no bloquear, dejar pasar
        return True, scores

    positive = sum(1 for s in scores.values() if s >= min_score)
    confirmed = positive >= required_positive
    return confirmed, scores


# ============ BINANCE / EXCHANGES ============

def fetch_realtime_price(symbol):
    """Precio en tiempo real desde Binance REST API"""
    try:
        url = f"{BINANCE_BASE}/ticker/price"
        params = {"symbol": f"{symbol}USDT"}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if "price" in data:
                return float(data["price"])
    except:
        pass
    return None

def fetch_klines(symbol, interval="4h", limit=100):
    # 1. Binance global
    try:
        url = f"{BINANCE_BASE}/klines"
        params = {"symbol": f"{symbol}USDT", "interval": interval, "limit": limit + 1}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            rows = r.json()[:-1]
            return [float(row[4]) for row in rows], [float(row[5]) for row in rows]
    except:
        pass

    # 2. OKX — funciona desde GitHub Actions
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
        if "OKX error" not in str(e):
            pass

    # 3. KuCoin
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
    except:
        pass

    # 4. Gate.io
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
    except:
        pass

    return None, None


# ============ SUPABASE ============

def load_sent_alerts():
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
                    print(f"  ✅ {len(cooldowns)} cooldowns cargados")
                    return cooldowns
    except Exception as e:
        print(f"  ⚠️ No se pudo cargar cooldowns: {e}")
    return {}

def save_sent_alerts():
    try:
        now_ts = time.time()
        cleaned = {k: v for k, v in SENT_ALERTS.items() if now_ts - v < COOLDOWN_SECONDS}
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/alert_cooldowns",
            headers=SUPA_HEADERS,
            json={"id": "default", "cooldowns": cleaned, "updated_at": datetime.now().isoformat()},
            timeout=10
        )
        if r.status_code not in [200, 201]:
            requests.patch(
                f"{SUPABASE_URL}/rest/v1/alert_cooldowns?id=eq.default",
                headers=SUPA_HEADERS,
                json={"cooldowns": cleaned, "updated_at": datetime.now().isoformat()},
                timeout=10
            )
    except Exception as e:
        print(f"  ⚠️ No se pudo guardar cooldowns: {e}")

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
    except:
        pass
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
            requests.patch(
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
    except:
        pass


# ============ COOLDOWN ============

def can_send(coin, trigger):
    key = f"{coin}_{trigger}"
    now = time.time()
    if key in SENT_ALERTS:
        if now - SENT_ALERTS[key] < COOLDOWN_SECONDS:
            return False
    return True

def mark_sent(coin, trigger):
    key = f"{coin}_{trigger}"
    SENT_ALERTS[key] = time.time()


# ============ CHECK COIN ============

def check_coin(coin, default_tf, global_triggers, coin_triggers, prev_states,
               conf_state, use_ml=True, use_mtf=True, use_confirmation=True,
               ml_threshold=0.65, mtf_required=2):
    """
    VERSIÓN 7: Análisis completo con ML + Multi-TF + Confirmation Candles.

    Parámetros de configuración:
        use_ml:          Habilitar filtro ML (default True)
        use_mtf:         Habilitar Multi-Timeframe (default True)
        use_confirmation: Habilitar confirmation candles (default True)
        ml_threshold:    Probabilidad mínima ML para pasar (default 0.65)
        mtf_required:    Nº de TFs con score >= 2 para confirmar (default 2)
    """
    print(f"\n🔍 {coin}")

    def gv(key, default):
        gt = global_triggers.get(key, {})
        if isinstance(gt, dict):
            return gt.get("val", default)
        return default

    _tf_cache = {}
    def get_data(tf):
        if tf in _tf_cache:
            return _tf_cache[tf]
        c, v = fetch_klines(coin, tf, 100)
        _tf_cache[tf] = (c, v)
        return c, v

    closes, vols = get_data(default_tf)
    if not closes:
        print(f"  ❌ Sin datos")
        return None

    # Precio en tiempo real
    realtime_price = fetch_realtime_price(coin)
    price = realtime_price if realtime_price else closes[-1]
    print(f"  💰 Precio: ${fmt_price(price)}")

    # Indicadores
    rsi_def  = calc_rsi(closes)
    macd_def = calc_macd(closes)
    ema_def  = calc_ema_cross(closes)
    bb_def   = calc_bb(closes)
    spike_def = calc_spike(vols, closes)
    obv_def  = calc_obv(vols, closes)

    score_def     = analyze(rsi_def, macd_def, ema_def, bb_def, spike_def)
    obv_trend_def = obv_def["trend"] if obv_def else "flat"
    macd_dir_def  = "up" if macd_def and macd_def["up"] else "down" if macd_def and macd_def["down"] else "flat"

    # ── ML PREDICTION ──────────────────────────────────────────────
    ml_prob = 0.5
    ml_info = ""
    if use_ml and ml_predictor.is_trained:
        ml_prob = ml_predictor.predict(closes, vols, rsi_def, macd_def, ema_def, bb_def, spike_def, obv_def)
        trend_label = "🟢 alcista" if ml_prob >= 0.65 else "🟡 neutral" if ml_prob >= 0.50 else "🔴 bajista"
        ml_info = f"\n🤖 ML: {ml_prob:.0%} {trend_label}"
        print(f"  🤖 ML: {ml_prob:.1%} probabilidad alcista")

    # ── MULTI-TIMEFRAME ─────────────────────────────────────────────
    mtf_confirmed = True  # por defecto pasa
    mtf_info = ""
    if use_mtf:
        mtf_confirmed, mtf_scores = check_multi_tf(coin, default_tf, required_positive=mtf_required)
        scores_str = " ".join([f"{tf.upper()}:{s:+d}" for tf, s in mtf_scores.items()])
        mtf_info = f"\n📊 Multi-TF: {scores_str}"
        status = "✅ Alineado" if mtf_confirmed else "❌ No alineado"
        print(f"  📊 Multi-TF: {scores_str} → {status}")

    tf_tag = f"[{default_tf.upper()}]"
    px = fmt_price(price)

    prev = prev_states.get(coin, {})
    prev_obv  = prev.get(f"obv_{default_tf}", "flat")
    prev_macd = prev.get(f"macd_{default_tf}", "flat")

    # ── TRIGGERS ────────────────────────────────────────────────────
    for trigger, enabled in global_triggers.items():
        if not isinstance(enabled, dict):
            enabled = {"on": enabled}

        if not enabled.get("on") and not (coin_triggers.get(coin, {}).get(trigger)):
            continue

        if not can_send(coin, trigger):
            continue

        spike_r = spike_def["ratio"] if spike_def else 0
        current_signal = False
        signal_msg = ""

        # ── Evaluar señal ──
        if trigger == "spike" and spike_r > gv("spike", 1.5):
            current_signal = True
            signal_msg = f"⚡ <b>VOLUME SPIKE</b>\n<b>{coin}/USDT</b> — ${px}\nSpike: {spike_r:.1f}x promedio {tf_tag}"

        elif trigger == "spike_green" and spike_r > gv("spike_green", 2.0) and spike_def and spike_def.get("up"):
            current_signal = True
            signal_msg = f"⚡ <b>SPIKE VERDE</b>\n<b>{coin}/USDT</b> — ${px}\nSpike: {spike_r:.1f}x + vela verde 🔥 {tf_tag}"

        elif trigger == "macd_up" and macd_dir_def == "up" and prev_macd != "up":
            current_signal = True
            signal_msg = f"📊 <b>MACD CRUCE ALCISTA</b>\n<b>{coin}/USDT</b> — ${px}\nHistograma cruzó positivo ✅ {tf_tag}"

        elif trigger == "macd_down" and macd_dir_def == "down" and prev_macd != "down":
            current_signal = True
            signal_msg = f"📊 <b>MACD CRUCE BAJISTA</b>\n<b>{coin}/USDT</b> — ${px}\nHistograma cruzó negativo ⚠️ {tf_tag}"

        elif trigger == "mr" and rsi_def is not None and rsi_def < gv("mr", 35) and obv_trend_def == "up":
            current_signal = True
            signal_msg = f"🔥 <b>MEAN REVERSION</b>\n<b>{coin}/USDT</b> — ${px}\nRSI: {rsi_def:.1f} + OBV↑ Puntaje: {score_def:+d} {tf_tag}"

        elif trigger == "ob" and rsi_def is not None and rsi_def > gv("ob", 70) and obv_trend_def == "down":
            current_signal = True
            signal_msg = f"🚨 <b>OVERBOUGHT COMBO</b>\n<b>{coin}/USDT</b> — ${px}\nRSI: {rsi_def:.1f} + OBV↓ {tf_tag}"

        elif trigger == "strong" and score_def >= gv("strong", 4):
            current_signal = True
            rsi_str = f"{rsi_def:.1f}" if rsi_def else "—"
            signal_msg = f"💎 <b>STRONG BUY</b>\n<b>{coin}/USDT</b> — ${px}\nPuntaje: <b>{score_def:+d}/+5</b> RSI: {rsi_str} {tf_tag}"

        elif trigger == "sell" and score_def <= gv("sell", -4):
            current_signal = True
            signal_msg = f"📉 <b>STRONG SELL</b>\n<b>{coin}/USDT</b> — ${px}\nPuntaje: <b>{score_def}/-5</b> {tf_tag}"

        if not current_signal:
            if use_confirmation:
                # Resetear contador si la señal desapareció
                check_confirmation(conf_state, coin, trigger, False)
            continue

        # ── Filtros nuevos (solo si la señal está activa) ──

        # 1. Confirmation Candles
        if use_confirmation:
            confirmed_candles = check_confirmation(conf_state, coin, trigger, True)
            count = conf_state.get(f"{coin}_{trigger}", {}).get("count", 1)
            if not confirmed_candles:
                print(f"  ⏳ {trigger}: esperando confirmación ({count}/2)")
                continue

        # 2. ML Filter
        if use_ml and ml_predictor.is_trained:
            if ml_prob < ml_threshold:
                print(f"  🚫 {trigger}: ML bloqueó ({ml_prob:.0%} < {ml_threshold:.0%})")
                continue

        # 3. Multi-TF Filter
        if use_mtf and not mtf_confirmed:
            print(f"  🚫 {trigger}: Multi-TF no alineado")
            continue

        # ── Agregar info extra al mensaje ──
        extra = ml_info + mtf_info
        if extra:
            signal_msg += extra

        # ✅ Todo pasó → enviar alerta
        mark_sent(coin, trigger)
        send_telegram(signal_msg)
        print(f"  ✅ {trigger}: alerta enviada")

    # ── Alertas por indicadores individuales (per-coin) ──
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

    # Guardar estados por TF
    new_state = {
        "price": price,
        "score": score_def,
        f"obv_{default_tf}": obv_trend_def,
        f"macd_{default_tf}": macd_dir_def,
        f"rsi_{default_tf}": rsi_def,
    }
    if use_ml and ml_predictor.is_trained:
        new_state["ml_prob"] = round(ml_prob, 3)

    _tf_cache_local = _tf_cache
    for tf, (c, v) in _tf_cache_local.items():
        if c and tf != default_tf:
            new_state[f"rsi_{tf}"]  = calc_rsi(c)
            new_state[f"obv_{tf}"]  = (calc_obv(v, c) or {}).get("trend", "flat")
            m = calc_macd(c)
            new_state[f"macd_{tf}"] = "up" if m and m["up"] else "down" if m and m["down"] else "flat"

    return new_state


# ============ RUN ============

def run():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*55}")
    print(f"🚀 Scanner Bot v7 ML Enhanced — {now}")
    print(f"{'='*55}")

    # ── Entrenar ML ──────────────────────────────────────────────
    if ML_AVAILABLE and not ml_predictor.is_trained:
        ml_predictor.train()

    use_ml   = ml_predictor.is_trained  # False si scikit-learn no está instalado
    use_mtf  = True
    use_conf = True

    print(f"🤖 ML:            {'✅ ON' if use_ml else '❌ OFF (instala scikit-learn)'}")
    print(f"📊 Multi-TF:      {'✅ ON' if use_mtf else '❌ OFF'}")
    print(f"⏳ Confirmation:  {'✅ ON' if use_conf else '❌ OFF'}")

    # ── Cargar estado de Supabase ────────────────────────────────
    try:
        state = load_state()
    except Exception as e:
        print(f"❌ Supabase error: {e}")
        return

    if not state:
        print("❌ Sin estado en Supabase. Abre el HTML primero para configurar.")
        return

    coins = state.get("coins") or []
    if not coins:
        print("⚠️  Sin monedas configuradas.")
        return

    global SENT_ALERTS
    SENT_ALERTS = load_sent_alerts()

    # Limpiar cooldowns vencidos
    now_ts = time.time()
    SENT_ALERTS = {k: v for k, v in SENT_ALERTS.items() if now_ts - v < COOLDOWN_SECONDS}
    print(f"🕐 {len(SENT_ALERTS)} cooldowns activos")

    # Cargar estado de confirmation candles desde Supabase
    conf_state = load_confirmation_state() if use_conf else {}

    timeframe  = state.get("timeframe", "4h")
    raw_global = state.get("global_triggers") or {}
    raw_coin   = state.get("coin_triggers") or {}
    raw_alerts = state.get("alerts") or {}

    for field_name, field in [("raw_global", raw_global), ("raw_coin", raw_coin), ("raw_alerts", raw_alerts)]:
        if isinstance(field, str):
            try:
                locals()[field_name] = json.loads(field)
            except:
                locals()[field_name] = {}

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

    print(f"📊 Monedas: {coins}")
    print(f"⏱  Timeframe: {timeframe}")
    active_triggers = [k for k, v in raw_global.items() if (v.get('on') if isinstance(v, dict) else v)]
    print(f"🔔 Triggers activos: {active_triggers}")

    new_states = {}

    for coin in coins:
        new_state = check_coin(
            coin, timeframe, raw_global, raw_coin, prev_states,
            conf_state,
            use_ml=use_ml,
            use_mtf=use_mtf,
            use_confirmation=use_conf,
            ml_threshold=0.65,
            mtf_required=2,
        )
        if new_state:
            new_states[coin] = new_state

        # ── Alertas de precio ────────────────────────────────────
        if coin in raw_alerts and new_state:
            try:
                current_price = fetch_realtime_price(coin)
                if current_price is None:
                    current_price = new_state.get("price")

                print(f"  💰 {coin} precio actual: ${fmt_price(current_price)}")

                coin_alerts = raw_alerts[coin]
                if isinstance(coin_alerts, dict):
                    coin_alerts = [coin_alerts]

                for alert in coin_alerts:
                    target    = float(alert.get("target", 0))
                    direction = alert.get("dir", "")

                    hit = (direction == "below" and current_price <= target) or \
                          (direction == "above" and current_price >= target)

                    alert_key = f"{direction}_{target}"

                    if hit and can_send(coin, alert_key):
                        mark_sent(coin, alert_key)
                        arrow = "↓" if direction == "below" else "↑"
                        label = "bajó de" if direction == "below" else "subió a"
                        target_str = alert.get("targetStr", None)

                        print(f"  ✅ Alerta de precio: {coin} {arrow} ${fmt_target(target, target_str)}")
                        send_telegram(
                            f"💰 <b>ALERTA DE PRECIO</b>\n"
                            f"<b>{coin}/USDT</b>\n"
                            f"Precio actual: ${fmt_price(current_price)}\n"
                            f"{arrow} {label} ${fmt_target(target, target_str)}"
                        )
            except Exception as e:
                print(f"  ⚠️ Error alerta precio {coin}: {e}")

        time.sleep(1.0)

    # ── Guardar todo en Supabase ──────────────────────────────────
    save_sent_alerts()

    if use_conf:
        save_confirmation_state(conf_state)
        print(f"\n✅ Confirmation state guardado ({len(conf_state)} entradas)")

    try:
        save_prev_state(new_states)
        print(f"✅ Estados guardados en Supabase")
    except Exception as e:
        print(f"⚠️  No se pudo guardar estados: {e}")

    print(f"\n✅ Done — {len(coins)} monedas procesadas")
    print(f"📊 Cooldowns activos: {len(SENT_ALERTS)}")


if __name__ == "__main__":
    run()
