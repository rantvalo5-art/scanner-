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
        """
        Modelo calibrado a los triggers reales del scanner:
          - obv_up:     OBV en tendencia alcista
          - rsi_low:    RSI < umbral (sobreventa)
          - rsi_high:   RSI > umbral (sobrecompra) — señal de fuerza en breakout
          - rsi_rising: RSI subiendo desde niveles bajos (recuperación)
          - spike_solo: Spike de volumen solo (sin exigir vela verde)

        Features del modelo (deben coincidir exactamente con predict()):
          0  rsi            — valor 0-100
          1  obv_up         — 1 si OBV alcista, 0 si no
          2  spike_ratio    — ratio volumen vs promedio 20 velas
          3  momentum_3     — cambio % en últimas 3 velas  (rsi_rising proxy)
          4  momentum_10    — cambio % en últimas 10 velas (tendencia corta)
          5  bb_pct         — posición en Bollinger 0=inferior 1=superior
          6  macd_h         — histograma MACD normalizado
          7  ema_bull       — 1 si EMA9 > EMA21
          8  rsi_prev_diff  — RSI actual - RSI hace 3 velas (subiendo = positivo)
          9  vol_trend      — ratio volumen última vela vs promedio 5 velas
        """
        if not ML_AVAILABLE:
            return False

        print("🤖 Entrenando modelo ML (calibrado a tus triggers)...")

        X_train = []
        y_train = []
        rng = np.random.default_rng(42)

        for _ in range(3000):
            rsi         = rng.uniform(10, 95)
            obv_up      = rng.choice([0, 1])
            spike_ratio = rng.uniform(0.3, 4.0)
            mom3        = rng.uniform(-0.08, 0.08)   # cambio % 3 velas
            mom10       = rng.uniform(-0.12, 0.12)   # cambio % 10 velas
            bb_pct      = rng.uniform(0, 1)
            macd_h      = rng.uniform(-1.5, 1.5)
            ema_bull    = rng.choice([0, 1])
            rsi_diff    = rng.uniform(-15, 15)        # RSI subiendo/bajando
            vol_trend   = rng.uniform(0.3, 3.0)       # volumen relativo

            features = [rsi, obv_up, spike_ratio, mom3, mom10,
                        bb_pct, macd_h, ema_bull, rsi_diff, vol_trend]

            # ── Condiciones mapeadas a cada trigger real ──────────────────

            # obv_up: OBV alcista + precio subiendo = acumulación real
            cond_obv_up     = obv_up == 1 and mom10 > 0.01 and rsi < 70

            # rsi_low: RSI en sobreventa + cualquier señal de giro
            cond_rsi_low    = rsi < 35 and (obv_up == 1 or rsi_diff > 2)

            # rsi_high + breakout: RSI alto con momentum fuerte (no es bajista si hay fuerza)
            cond_rsi_high   = rsi > 65 and mom3 > 0.02 and ema_bull == 1 and spike_ratio > 1.2

            # rsi_rising: RSI subiendo desde zona media/baja con volumen
            cond_rsi_rising = rsi_diff > 5 and rsi < 65 and obv_up == 1

            # spike_solo: spike de volumen alto con precio no cayendo
            cond_spike      = spike_ratio > 2.0 and mom3 > -0.01 and vol_trend > 1.5

            label = 1 if any([cond_obv_up, cond_rsi_low, cond_rsi_high,
                              cond_rsi_rising, cond_spike]) else 0

            X_train.append(features)
            y_train.append(label)

        X = np.array(X_train)
        y = np.array(y_train)

        pct_pos = y.mean() * 100
        print(f"  📊 Dataset: {len(y)} muestras, {pct_pos:.0f}% alcistas")

        if pct_pos < 30 or pct_pos > 70:
            print(f"  ⚠️  Balance fuera de rango ideal (30-70%). Ajustar condiciones.")

        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)

        self.model = RandomForestClassifier(
            n_estimators=150,
            max_depth=8,
            min_samples_leaf=10,
            class_weight='balanced',
            random_state=42
        )
        self.model.fit(X_scaled, y)
        self.is_trained = True

        # Mostrar importancia de features para debug
        feature_names = ["rsi", "obv_up", "spike_ratio", "mom3", "mom10",
                         "bb_pct", "macd_h", "ema_bull", "rsi_diff", "vol_trend"]
        importances = self.model.feature_importances_
        top = sorted(zip(feature_names, importances), key=lambda x: -x[1])[:4]
        top_str = "  ".join([f"{n}:{v:.2f}" for n, v in top])
        print(f"  🎯 Top features: {top_str}")
        print("✅ Modelo ML entrenado")
        return True

    def predict(self, closes, vols, rsi, macd, ema_cross, bb, spike, obv):
        """
        Predice probabilidad alcista usando los mismos 10 features del entrenamiento.
        Retorna float 0-1. Default 0.5 si el modelo no está disponible.
        """
        if not self.is_trained or not ML_AVAILABLE:
            return 0.5

        try:
            # mom3: cambio % en últimas 3 velas
            mom3  = (closes[-1] - closes[-4]) / closes[-4] if len(closes) >= 4 else 0
            # mom10: cambio % en últimas 10 velas
            mom10 = (closes[-1] - closes[-11]) / closes[-11] if len(closes) >= 11 else 0
            # rsi_diff: cuánto subió/bajó el RSI en las últimas 3 velas
            rsi_val      = rsi if rsi is not None else 50
            rsi_prev_val = calc_rsi(closes[:-3]) if len(closes) > 17 else rsi_val
            rsi_diff     = rsi_val - (rsi_prev_val if rsi_prev_val is not None else rsi_val)
            # vol_trend: volumen última vela vs promedio 5 velas
            vol_trend = vols[-1] / (sum(vols[-6:-1]) / 5) if len(vols) >= 6 and sum(vols[-6:-1]) > 0 else 1.0

            features = [
                rsi_val,
                1 if obv and obv["trend"] == "up" else 0,
                spike["ratio"] if spike else 1.0,
                mom3,
                mom10,
                bb["pct"] if bb else 0.5,
                macd["h"] if macd else 0,
                1 if ema_cross and ema_cross["bullish"] else 0,
                rsi_diff,
                vol_trend,
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
    width = ((upper - lower) / mid * 100) if mid > 0 else 0
    # Width de la vela anterior para detectar expansión
    prev_width = None
    if len(closes) >= period + 1:
        sl_prev = closes[-period-1:-1]
        mid_prev = sum(sl_prev) / period
        std_prev = math.sqrt(sum((x - mid_prev) ** 2 for x in sl_prev) / period)
        prev_width = ((4 * std_prev) / mid_prev * 100) if mid_prev > 0 else 0
    # Dirección del precio en la vela actual
    bullish = len(closes) >= 2 and closes[-1] > closes[-2]
    return {"pct": pct, "width": width, "prev_width": prev_width,
            "upper": upper, "lower": lower, "mid": mid, "bullish": bullish}

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
    
    Se adapta al timeframe principal configurado:
    - Si usás 5m → chequea 5m + 15m + 1h  (TFs cercanos, tiene sentido)
    - Si usás 1h → chequea 1h + 4h + 1d   (TFs clásicos)
    - Si usás 4h → chequea 4h + 1d         (TFs macro)
    
    Con 5m es INCORRECTO pedir alineación en 4H y 1D porque son tendencias
    completamente distintas al scalping de 5 minutos.
    
    Returns:
        (confirmed: bool, scores: dict)
    """
    # Mapa de TFs relevantes según el TF principal
    tf_groups = {
        "1m":  ["1m", "5m", "15m"],
        "3m":  ["3m", "15m", "1h"],
        "5m":  ["5m", "15m", "1h"],
        "15m": ["15m", "1h", "4h"],
        "30m": ["30m", "1h", "4h"],
        "1h":  ["1h", "4h", "1d"],
        "2h":  ["2h", "4h", "1d"],
        "4h":  ["4h", "1d"],
        "1d":  ["1d"],
    }

    timeframes = tf_groups.get(default_tf, ["1h", "4h", "1d"])
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
        # Solo hay 1 TF disponible → no bloquear, dejar pasar
        return True, scores

    positive = sum(1 for s in scores.values() if s >= min_score)
    
    # Con solo 2 TFs, basta que 1 sea positivo (más permisivo)
    req = 1 if len(scores) == 2 else required_positive
    confirmed = positive >= req
    return confirmed, scores


# ============ BINANCE / EXCHANGES ============

def fetch_realtime_price(symbol):
    """
    Precio en tiempo real — Binance bloqueado en GitHub Actions (IPs AWS).
    Usa OKX primero ya que funciona bien, luego KuCoin y Gate.io como respaldo.
    """
    # 1. OKX — el más confiable desde GitHub Actions
    try:
        url = "https://www.okx.com/api/v5/market/ticker"
        params = {"instId": f"{symbol}-USDT"}
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, params=params, headers=headers, timeout=8)
        data = r.json()
        if data.get("code") == "0" and data.get("data"):
            price = float(data["data"][0]["last"])
            print(f"    [precio okx: ${price}]")
            return price, "okx"
        else:
            okx_msg = data.get("msg", "sin precio")
            print(f"    [okx error: {okx_msg}]")
    except Exception as e:
        print(f"    [okx excepcion: {e}]")

    # 2. KuCoin
    try:
        url = "https://api.kucoin.com/api/v1/market/orderbook/level1"
        params = {"symbol": f"{symbol}-USDT"}
        r = requests.get(url, params=params, timeout=8)
        data = r.json()
        if data.get("code") == "200000" and data.get("data"):
            price = float(data["data"]["price"])
            print(f"    [precio kucoin: ${price}]")
            return price, "kucoin"
        else:
            kc_msg = data.get("msg", "sin precio")
            print(f"    [kucoin error: {kc_msg}]")
    except Exception as e:
        print(f"    [kucoin excepcion: {e}]")

    # 3. Gate.io
    try:
        url = "https://api.gateio.ws/api/v4/spot/tickers"
        params = {"currency_pair": f"{symbol}_USDT"}
        r = requests.get(url, params=params, timeout=8)
        data = r.json()
        if data and isinstance(data, list) and data[0].get("last"):
            price = float(data[0]["last"])
            print(f"    [precio gate: ${price}]")
            return price, "gate"
        else:
            print(f"    [gate error: respuesta inesperada]")
    except Exception as e:
        print(f"    [gate excepcion: {e}]")

    # 4. Binance — ultimo intento (suele estar bloqueado en GitHub Actions)
    try:
        url = f"{BINANCE_BASE}/ticker/price"
        params = {"symbol": f"{symbol}USDT"}
        r = requests.get(url, params=params, timeout=8)
        if r.status_code == 200:
            data = r.json()
            if "price" in data:
                price = float(data["price"])
                print(f"    [precio binance: ${price}]")
                return price, "binance"
        print(f"    [binance status: {r.status_code}]")
    except Exception as e:
        print(f"    [binance excepcion: {e}]")

    print(f"    ⚠️ TODOS los exchanges fallaron para {symbol} — usando close")
    return None, None

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
        interval_map_kc = {"1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "1hour", "4h": "4hour", "1d": "1day"}
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
        cleaned = {k: v for k, v in SENT_ALERTS.items() if now_ts - _normalize_ts(v) < COOLDOWN_SECONDS}
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

def _normalize_ts(ts):
    """
    El HTML guarda cooldowns con Date.now() de JS = milisegundos.
    Python usa time.time() = segundos.
    Si ts > 1e11 es milisegundos, dividir por 1000.
    Ej: 1742500000000 ms → 1742500000.0 s
    """
    if ts and ts > 1e11:
        return ts / 1000.0
    return float(ts) if ts else 0.0

def can_send(coin, trigger):
    key = f"{coin}_{trigger}"
    now = time.time()
    if key in SENT_ALERTS:
        ts = _normalize_ts(SENT_ALERTS[key])
        if now - ts < COOLDOWN_SECONDS:
            return False
    return True

def mark_sent(coin, trigger):
    key = f"{coin}_{trigger}"
    # Guardamos siempre en segundos desde Python
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

    # Precio en tiempo real con fallback a último close
    realtime_price, rt_src = fetch_realtime_price(coin)
    price = realtime_price if realtime_price else closes[-1]
    src_tag = f" [{rt_src}]" if rt_src else " [close]"
    print(f"  💰 Precio: ${fmt_price(price)}{src_tag}")

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
        # Para TFs cortos (5m, 15m) bajar el min_score a 1 porque el score raramente llega a 2
        short_tfs = ["1m", "3m", "5m", "15m", "30m"]
        min_score_mtf = 1 if default_tf in short_tfs else 2
        mtf_confirmed, mtf_scores = check_multi_tf(coin, default_tf, required_positive=mtf_required, min_score=min_score_mtf)
        scores_str = " ".join([f"{tf.upper()}:{s:+d}" for tf, s in mtf_scores.items()])
        mtf_info = f"\n📊 Multi-TF: {scores_str}"
        status = "✅ Alineado" if mtf_confirmed else "❌ No alineado"
        print(f"  📊 Multi-TF: {scores_str} → {status}")

    tf_tag = f"[{default_tf.upper()}]"
    px = fmt_price(price)

    prev = prev_states.get(coin, {})
    prev_obv  = prev.get(f"obv_{default_tf}", "flat")
    prev_macd = prev.get(f"macd_{default_tf}", "flat")

    # ── RESUMEN DEBUG — valores actuales vs umbrales ─────────────────
    rsi_str_dbg  = f"{rsi_def:.1f}" if rsi_def is not None else "N/A"
    spike_r_dbg  = spike_def["ratio"] if spike_def else 0
    bb_pct_dbg   = f"{bb_def['pct']*100:.1f}%" if bb_def else "N/A"
    bb_w_dbg     = f"{bb_def['width']:.2f}%" if bb_def else "N/A"
    score_str    = f"{score_def:+d}"
    macd_str     = macd_dir_def
    obv_str      = obv_trend_def
    print(f"  📋 RSI:{rsi_str_dbg} Score:{score_str} OBV:{obv_str} MACD:{macd_str} Spike:{spike_r_dbg:.1f}x BB%B:{bb_pct_dbg} BBW:{bb_w_dbg}")

    # ── TRIGGERS ────────────────────────────────────────────────────
    for trigger, enabled in global_triggers.items():
        if not isinstance(enabled, dict):
            enabled = {"on": enabled}

        if not enabled.get("on") and not (coin_triggers.get(coin, {}).get(trigger)):
            continue

        # Debug cooldown restante
        if not can_send(coin, trigger):
            cd_key = f"{coin}_{trigger}"
            if cd_key in SENT_ALERTS:
                remaining = COOLDOWN_SECONDS - (time.time() - _normalize_ts(SENT_ALERTS[cd_key]))
                print(f"  ⏱  {trigger}: cooldown {remaining/60:.0f} min restantes")
            continue

        # ── Leer el TF específico de ESTA alerta global ──────────────────
        # El HTML guarda enabled["tf"] por cada trigger — si no hay, usa default_tf
        trigger_tf = enabled.get("tf", default_tf) if isinstance(enabled, dict) else default_tf
        t_closes, t_vols = get_data(trigger_tf)
        if not t_closes:
            print(f"  ⚠️  {trigger}: sin datos para TF {trigger_tf}")
            continue

        # Calcular indicadores en el TF de esta alerta
        t_rsi   = calc_rsi(t_closes)
        t_macd  = calc_macd(t_closes)
        t_ema   = calc_ema_cross(t_closes)
        t_bb    = calc_bb(t_closes)
        t_spike = calc_spike(t_vols, t_closes)
        t_obv   = calc_obv(t_vols, t_closes)
        t_score = analyze(t_rsi, t_macd, t_ema, t_bb, t_spike)
        t_obv_trend  = t_obv["trend"] if t_obv else "flat"
        t_macd_dir   = "up" if t_macd and t_macd["up"] else "down" if t_macd and t_macd["down"] else "flat"
        t_spike_r    = t_spike["ratio"] if t_spike else 0
        t_tf_tag     = f"[{trigger_tf.upper()}]"

        # Para cruces (MACD, OBV) necesitamos el estado previo de ESTE TF
        t_prev_obv  = prev.get(f"obv_{trigger_tf}", "flat")
        t_prev_macd = prev.get(f"macd_{trigger_tf}", "flat")

        t_rsi_str = f"{t_rsi:.1f}" if t_rsi is not None else "N/A"

        current_signal = False
        signal_msg = ""

        # ── Evaluar señal con debug de por qué NO cumple ──
        if trigger == "spike":
            threshold = gv("spike", 1.5)
            if t_spike_r > threshold:
                current_signal = True
                signal_msg = f"⚡ <b>VOLUME SPIKE</b>\n<b>{coin}/USDT</b> — ${px}\nSpike: {t_spike_r:.1f}x promedio {t_tf_tag}"
            else:
                print(f"  — spike: {t_spike_r:.1f}x < umbral {threshold}x {t_tf_tag}")

        elif trigger == "spike_green":
            threshold = gv("spike_green", 2.0)
            if t_spike_r > threshold and t_spike and t_spike.get("up"):
                current_signal = True
                signal_msg = f"⚡ <b>SPIKE VERDE</b>\n<b>{coin}/USDT</b> — ${px}\nSpike: {t_spike_r:.1f}x + vela verde 🔥 {t_tf_tag}"
            else:
                up_str = "verde" if (t_spike and t_spike.get("up")) else "roja"
                print(f"  — spike_green: {t_spike_r:.1f}x (umbral {threshold}x) vela {up_str} {t_tf_tag}")

        elif trigger == "macd_up":
            if t_macd_dir == "up" and t_prev_macd != "up":
                current_signal = True
                signal_msg = f"📊 <b>MACD CRUCE ALCISTA</b>\n<b>{coin}/USDT</b> — ${px}\nHistograma cruzó positivo ✅ {t_tf_tag}"
            else:
                print(f"  — macd_up: actual={t_macd_dir} prev={t_prev_macd} {t_tf_tag}")

        elif trigger == "macd_down":
            if t_macd_dir == "down" and t_prev_macd != "down":
                current_signal = True
                signal_msg = f"📊 <b>MACD CRUCE BAJISTA</b>\n<b>{coin}/USDT</b> — ${px}\nHistograma cruzó negativo ⚠️ {t_tf_tag}"
            else:
                print(f"  — macd_down: actual={t_macd_dir} prev={t_prev_macd} {t_tf_tag}")

        elif trigger == "mr":
            threshold = gv("mr", 35)
            if t_rsi is not None and t_rsi < threshold and t_obv_trend == "up":
                current_signal = True
                signal_msg = f"🔥 <b>MEAN REVERSION</b>\n<b>{coin}/USDT</b> — ${px}\nRSI: {t_rsi:.1f} + OBV↑ Score: {t_score:+d} {t_tf_tag}"
            else:
                print(f"  — mr: RSI={t_rsi_str} (necesita <{threshold}) OBV={t_obv_trend} (necesita up) {t_tf_tag}")

        elif trigger == "ob":
            threshold = gv("ob", 70)
            if t_rsi is not None and t_rsi > threshold and t_obv_trend == "down":
                current_signal = True
                signal_msg = f"🚨 <b>OVERBOUGHT COMBO</b>\n<b>{coin}/USDT</b> — ${px}\nRSI: {t_rsi:.1f} + OBV↓ {t_tf_tag}"
            else:
                print(f"  — ob: RSI={t_rsi_str} (necesita >{threshold}) OBV={t_obv_trend} (necesita down) {t_tf_tag}")

        elif trigger == "strong":
            threshold = gv("strong", 4)
            if t_score >= threshold:
                current_signal = True
                signal_msg = f"💎 <b>STRONG BUY</b>\n<b>{coin}/USDT</b> — ${px}\nPuntaje: <b>{t_score:+d}/+5</b> RSI: {t_rsi_str} {t_tf_tag}"
            else:
                print(f"  — strong: Score={t_score:+d} (necesita >={threshold}) {t_tf_tag}")

        elif trigger == "sell":
            threshold = gv("sell", -4)
            if t_score <= threshold:
                current_signal = True
                signal_msg = f"📉 <b>STRONG SELL</b>\n<b>{coin}/USDT</b> — ${px}\nPuntaje: <b>{t_score}/-5</b> {t_tf_tag}"
            else:
                print(f"  — sell: Score={t_score:+d} (necesita <={threshold}) {t_tf_tag}")

        elif trigger == "rsi_low":
            threshold = gv("rsi_low", 30)
            if t_rsi is not None and t_rsi < threshold:
                current_signal = True
                signal_msg = f"📉 <b>RSI BAJO</b>\n<b>{coin}/USDT</b> — ${px}\nRSI: {t_rsi:.1f} < {threshold} {t_tf_tag}"
            else:
                print(f"  — rsi_low: RSI={t_rsi_str} (necesita <{threshold}) {t_tf_tag}")

        elif trigger == "rsi_high":
            threshold = gv("rsi_high", 70)
            if t_rsi is not None and t_rsi > threshold:
                current_signal = True
                signal_msg = f"📈 <b>RSI ALTO</b>\n<b>{coin}/USDT</b> — ${px}\nRSI: {t_rsi:.1f} > {threshold} {t_tf_tag}"
            else:
                print(f"  — rsi_high: RSI={t_rsi_str} (necesita >{threshold}) {t_tf_tag}")

        elif trigger == "rsi_rising":
            pts  = float(gv("rsi_rising_pts", 3))
            bars = int(gv("rsi_rising_bars", 3))
            rsi_old = calc_rsi(t_closes[:-bars]) if len(t_closes) > 14 + bars else None
            slope = (t_rsi - rsi_old) if (t_rsi is not None and rsi_old is not None) else None
            if slope is not None and slope >= pts:
                current_signal = True
                signal_msg = f"📈 <b>RSI SUBIENDO</b>\n<b>{coin}/USDT</b> — ${px}\nRSI subió {slope:.1f} pts en {bars} velas {t_tf_tag}"
            else:
                slope_str = f"{slope:.1f}" if slope is not None else "N/A"
                print(f"  — rsi_rising: slope={slope_str} (necesita >={pts} en {bars} velas) {t_tf_tag}")

        elif trigger == "obv_up":
            if t_obv_trend == "up":
                current_signal = True
                signal_msg = f"↑ <b>OBV ACUMULANDO</b>\n<b>{coin}/USDT</b> — ${px}\n{t_tf_tag}"
            else:
                print(f"  — obv_up: OBV={t_obv_trend} (necesita up) {t_tf_tag}")

        elif trigger == "obv_down":
            if t_obv_trend == "down":
                current_signal = True
                signal_msg = f"↓ <b>OBV DISTRIBUYENDO</b>\n<b>{coin}/USDT</b> — ${px}\n{t_tf_tag}"
            else:
                print(f"  — obv_down: OBV={t_obv_trend} (necesita down) {t_tf_tag}")

        # ── BOLLINGER BANDS — triggers globales ──────────────────────────
        elif trigger == "bb_width_low":
            threshold = gv("bb_width_low", 4.0)
            bb_w = t_bb["width"] if t_bb else None
            if bb_w is not None and bb_w < threshold:
                current_signal = True
                signal_msg = (f"🎯 <b>BB SQUEEZE</b>\n<b>{coin}/USDT</b> — ${px}\n"
                              f"BB Width: {bb_w:.2f}% < {threshold}% — breakout inminente {t_tf_tag}")
            else:
                w_str = f"{bb_w:.2f}%" if bb_w is not None else "N/A"
                print(f"  — bb_width_low: Width={w_str} (necesita <{threshold}%) {t_tf_tag}")

        elif trigger == "bb_width_high":
            threshold = gv("bb_width_high", 8.0)
            bb_w = t_bb["width"] if t_bb else None
            if bb_w is not None and bb_w > threshold:
                current_signal = True
                dir_label = "ALCISTA 📈" if (t_bb and t_bb.get("bullish")) else "BAJISTA 📉"
                signal_msg = (f"📊 <b>BB EXPANSIÓN {dir_label}</b>\n<b>{coin}/USDT</b> — ${px}\n"
                              f"BB Width: {bb_w:.2f}% > {threshold}% {t_tf_tag}")
            else:
                w_str = f"{bb_w:.2f}%" if bb_w is not None else "N/A"
                print(f"  — bb_width_high: Width={w_str} (necesita >{threshold}%) {t_tf_tag}")

        elif trigger == "bb_width_expansion":
            threshold = gv("bb_width_expansion", 50.0)
            bb_w     = t_bb["width"]      if t_bb else None
            bb_pw    = t_bb["prev_width"] if t_bb else None
            if bb_w is not None and bb_pw is not None and bb_pw > 0:
                growth = ((bb_w - bb_pw) / bb_pw) * 100
                if growth >= threshold:
                    current_signal = True
                    dir_label = "ALCISTA 📈" if (t_bb and t_bb.get("bullish")) else "BAJISTA 📉"
                    signal_msg = (f"🚀 <b>BB EXPANSIÓN {dir_label}</b>\n<b>{coin}/USDT</b> — ${px}\n"
                                  f"Width: {bb_pw:.2f}% → {bb_w:.2f}% (+{growth:.0f}%) {t_tf_tag}")
                else:
                    print(f"  — bb_width_expansion: +{growth:.0f}% (necesita >={threshold:.0f}%) {t_tf_tag}")
            else:
                print(f"  — bb_width_expansion: sin datos previos suficientes {t_tf_tag}")

        elif trigger == "bb_pct_low":
            threshold = gv("bb_pct_low", 20.0)
            bb_pct = (t_bb["pct"] * 100) if t_bb else None
            if bb_pct is not None and bb_pct < threshold:
                current_signal = True
                signal_msg = (f"📉 <b>BB %B BAJO</b>\n<b>{coin}/USDT</b> — ${px}\n"
                              f"BB %B: {bb_pct:.1f}% < {threshold}% — precio cerca banda inferior {t_tf_tag}")
            else:
                p_str = f"{bb_pct:.1f}%" if bb_pct is not None else "N/A"
                print(f"  — bb_pct_low: %B={p_str} (necesita <{threshold}%) {t_tf_tag}")

        elif trigger == "bb_pct_high":
            threshold = gv("bb_pct_high", 80.0)
            bb_pct = (t_bb["pct"] * 100) if t_bb else None
            if bb_pct is not None and bb_pct > threshold:
                current_signal = True
                signal_msg = (f"📈 <b>BB %B ALTO</b>\n<b>{coin}/USDT</b> — ${px}\n"
                              f"BB %B: {bb_pct:.1f}% > {threshold}% — precio cerca banda superior {t_tf_tag}")
            else:
                p_str = f"{bb_pct:.1f}%" if bb_pct is not None else "N/A"
                print(f"  — bb_pct_high: %B={p_str} (necesita >{threshold}%) {t_tf_tag}")

        if not current_signal:
            if use_confirmation:
                check_confirmation(conf_state, coin, trigger, False)
            continue

        # ── Filtros nuevos (solo si la señal está activa) ──

        # 1. Confirmation Candles
        if use_confirmation:
            confirmed_candles = check_confirmation(conf_state, coin, trigger, True)
            count = conf_state.get(f"{coin}_{trigger}", {}).get("count", 1)
            if not confirmed_candles:
                print(f"  ⏳ {trigger}: esperando confirmación ({count}/2) — señal detectada pero necesita 1 run más")
                continue
            else:
                print(f"  ✅ {trigger}: confirmado ({count}/2)")

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
            cd_key = f"{coin}_{alert_key}"
            if cd_key in SENT_ALERTS:
                remaining = COOLDOWN_SECONDS - (time.time() - _normalize_ts(SENT_ALERTS[cd_key]))
                print(f"    ⏱  {atype}({atf}): cooldown {remaining/60:.0f} min restantes")
            continue

        if atype == "rsi_low" and aval is not None:
            rsi2 = calc_rsi(c2)
            rsi2_str = f"{rsi2:.1f}" if rsi2 is not None else "N/A"
            if rsi2 is not None and rsi2 < float(aval):
                mark_sent(coin, alert_key)
                send_telegram(f"📉 <b>RSI BAJO — {coin}</b>\nRSI ({atf.upper()}): {rsi2:.1f} < {aval} — ${px}")
                print(f"    ✅ rsi_low({atf}): RSI={rsi2_str} < {aval} → ALERTA")
            else:
                print(f"    — rsi_low({atf}): RSI={rsi2_str} (necesita <{aval})")

        elif atype == "rsi_high" and aval is not None:
            rsi2 = calc_rsi(c2)
            rsi2_str = f"{rsi2:.1f}" if rsi2 is not None else "N/A"
            if rsi2 is not None and rsi2 > float(aval):
                mark_sent(coin, alert_key)
                send_telegram(f"📈 <b>RSI ALTO — {coin}</b>\nRSI ({atf.upper()}): {rsi2:.1f} > {aval} — ${px}")
                print(f"    ✅ rsi_high({atf}): RSI={rsi2_str} > {aval} → ALERTA")
            else:
                print(f"    — rsi_high({atf}): RSI={rsi2_str} (necesita >{aval})")

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

        elif atype == "bb_width_low" and aval is not None:
            bb2 = calc_bb(c2)
            if bb2 and bb2["width"] < float(aval):
                mark_sent(coin, alert_key)
                send_telegram(
                    f"🎯 <b>BB SQUEEZE — {coin}</b>\n"
                    f"BB Width ({atf.upper()}): {bb2['width']:.2f}% < {aval}%\n"
                    f"Breakout inminente — ${px}"
                )

        elif atype == "bb_pct_low" and aval is not None:
            bb2 = calc_bb(c2)
            if bb2 and (bb2["pct"] * 100) < float(aval):
                mark_sent(coin, alert_key)
                pct_val = bb2['pct'] * 100
                send_telegram(
                    f"📉 <b>BB %B BAJO — {coin}</b>\n"
                    f"BB %B ({atf.upper()}): {pct_val:.1f}% < {aval}%\n"
                    f"Precio cerca banda inferior — ${px}"
                )

        elif atype == "bb_pct_high" and aval is not None:
            bb2 = calc_bb(c2)
            if bb2 and (bb2["pct"] * 100) > float(aval):
                mark_sent(coin, alert_key)
                pct_val = bb2['pct'] * 100
                send_telegram(
                    f"📈 <b>BB %B ALTO — {coin}</b>\n"
                    f"BB %B ({atf.upper()}): {pct_val:.1f}% > {aval}%\n"
                    f"Precio cerca banda superior — ${px}"
                )

        elif atype == "bb_width_high" and aval is not None:
            bb2 = calc_bb(c2)
            if bb2 and bb2["width"] > float(aval):
                mark_sent(coin, alert_key)
                dir_label = "ALCISTA 📈" if bb2["bullish"] else "BAJISTA 📉"
                dir_emoji  = "📈" if bb2["bullish"] else "📉"
                send_telegram(
                    f"{dir_emoji} <b>BB EXPANSIÓN {dir_label} — {coin}</b>\n"
                    f"BB Width ({atf.upper()}): {bb2['width']:.2f}% > {aval}%\n"
                    f"Precio {'subiendo' if bb2['bullish'] else 'bajando'} — ${px}"
                )

        elif atype == "bb_width_expansion" and aval is not None:
            bb2 = calc_bb(c2)
            if bb2 and bb2["prev_width"] is not None and bb2["prev_width"] > 0:
                growth_pct = ((bb2["width"] - bb2["prev_width"]) / bb2["prev_width"]) * 100
                if growth_pct >= float(aval):
                    mark_sent(coin, alert_key)
                    dir_label = "ALCISTA" if bb2["bullish"] else "BAJISTA"
                    dir_emoji  = "🚀📈" if bb2["bullish"] else "🚀📉"
                    send_telegram(
                        f"{dir_emoji} <b>BB EXPANSIÓN {dir_label} — {coin}</b>\n"
                        f"Width ({atf.upper()}): {bb2['prev_width']:.2f}% → {bb2['width']:.2f}% "
                        f"(+{growth_pct:.0f}%)\n"
                        f"Precio {'subiendo' if bb2['bullish'] else 'bajando'} — ${px}"
                    )

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

    use_ml   = False  # ❌ Deshabilitado — activar cuando confirmes que funciona
    use_mtf  = False  # ❌ Deshabilitado — simplifica el sistema
    use_conf = True   # ✅ Confirmation candles activo

    print(f"🤖 ML:            {'✅ ON' if use_ml else '❌ OFF'}")
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
    SENT_ALERTS = {k: v for k, v in SENT_ALERTS.items() if now_ts - _normalize_ts(v) < COOLDOWN_SECONDS}
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
                current_price, rt_src2 = fetch_realtime_price(coin)
                if current_price is None:
                    current_price = new_state.get("price")
                    rt_src2 = "close"

                print(f"  💰 {coin} precio actual: ${fmt_price(current_price)} [{rt_src2}]")

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
