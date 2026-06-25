import math
import time
from collections import deque
from typing import Dict, Any, List, Optional


class RollingIsolationLikeDetector:
  """
  Lightweight anomaly detector inspired by Isolation-Forest behavior.
  Uses rolling robust z-scores across engineered microstructure features.
  """

  def __init__(self, window: int = 600):
    self.window = max(100, int(window))
    self.features = {
      "ret_abs": deque(maxlen=self.window),
      "vol_zsrc": deque(maxlen=self.window),
      "flow_imb": deque(maxlen=self.window),
      "latency_ms": deque(maxlen=self.window),
      "spread_proxy": deque(maxlen=self.window),
    }

  def _median(self, arr: List[float]) -> float:
    if not arr:
      return 0.0
    s = sorted(arr)
    n = len(s)
    mid = n // 2
    return float(s[mid]) if n % 2 == 1 else (s[mid - 1] + s[mid]) / 2.0

  def _mad(self, arr: List[float], med: float) -> float:
    if not arr:
      return 1e-9
    dev = [abs(x - med) for x in arr]
    m = self._median(dev)
    return m if m > 1e-9 else 1e-9

  def _robust_z(self, x: float, hist: deque) -> float:
    arr = list(hist)
    if len(arr) < 30:
      return 0.0
    med = self._median(arr)
    mad = self._mad(arr, med)
    return 0.6745 * (x - med) / mad

  def score(self, feat: Dict[str, float]) -> Dict[str, float]:
    # robust z on each feature
    z = {}
    for k, x in feat.items():
      hist = self.features[k]
      z[k] = self._robust_z(float(x), hist)

    # combine absolute z into anomaly score in [0,1]
    abs_sum = sum(min(8.0, abs(v)) for v in z.values())
    score = 1.0 - math.exp(-abs_sum / 10.0)

    # update buffers
    for k, x in feat.items():
      self.features[k].append(float(x))

    return {"score": max(0.0, min(1.0, score)), "z": z}


class MLAnomalyDetector:
  """
  Real-time detector for toxic flow / spoof-like anomalies from normalized ticks.
  """

  def __init__(self, window: int = 600):
    self.detectors: Dict[str, RollingIsolationLikeDetector] = {}
    self.last_tick: Dict[str, Dict[str, Any]] = {}
    self.last_output: Dict[str, Dict[str, Any]] = {}
    self.window = window

  def _ensure(self, symbol: str) -> RollingIsolationLikeDetector:
    if symbol not in self.detectors:
      self.detectors[symbol] = RollingIsolationLikeDetector(window=self.window)
    return self.detectors[symbol]

  def ingest_tick(self, tick: Dict[str, Any]) -> Dict[str, Any]:
    symbol = str(tick.get("symbol", "BTCUSDT")).upper()
    price = float(tick.get("price", 0.0))
    volume = float(tick.get("volume", 0.0))
    side = "sell" if str(tick.get("side", "buy")).lower() == "sell" else "buy"
    latency = float(tick.get("latencyMs") or 0.0)
    ts = int(tick.get("ts") or int(time.time() * 1000))

    prev = self.last_tick.get(symbol)
    ret_abs = 0.0
    spread_proxy = 0.0
    if prev and prev.get("price", 0) > 0:
      ret_abs = abs((price - prev["price"]) / max(1e-9, prev["price"]))
      # proxy: return acceleration
      prev_ret = abs((prev["price"] - prev.get("prev_price", prev["price"])) / max(1e-9, prev.get("prev_price", prev["price"])))
      spread_proxy = abs(ret_abs - prev_ret)

    flow_imb = volume if side == "buy" else -volume
    vol_zsrc = math.log(1.0 + max(0.0, volume))

    feat = {
      "ret_abs": ret_abs,
      "vol_zsrc": vol_zsrc,
      "flow_imb": flow_imb,
      "latency_ms": latency,
      "spread_proxy": spread_proxy
    }

    detector = self._ensure(symbol)
    scored = detector.score(feat)
    score = scored["score"]
    z = scored["z"]

    labels = []
    if abs(z["flow_imb"]) > 3.5 and abs(z["vol_zsrc"]) > 2.5:
      labels.append("toxic_orderflow")
    if z["ret_abs"] > 4.0 and z["spread_proxy"] > 3.0:
      labels.append("microstructure_dislocation")
    if z["latency_ms"] > 4.0:
      labels.append("latency_regime_shift")

    severity = "low"
    if score > 0.8:
      severity = "critical"
    elif score > 0.6:
      severity = "high"
    elif score > 0.4:
      severity = "medium"

    out = {
      "symbol": symbol,
      "ts": ts,
      "anomalyScore": round(score, 6),
      "severity": severity,
      "labels": labels,
      "features": feat,
      "z": {k: round(v, 4) for k, v in z.items()},
      "raw": {
        "price": price,
        "volume": volume,
        "side": side,
        "latencyMs": latency
      }
    }

    self.last_output[symbol] = out
    self.last_tick[symbol] = {
      "price": price,
      "prev_price": prev["price"] if prev else price,
      "ts": ts
    }
    return out

  def ingest_ticks(self, ticks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for t in ticks:
      try:
        out.append(self.ingest_tick(t))
      except Exception:
        # skip malformed tick
        continue
    return out

  def latest(self, symbol: Optional[str] = None):
    if symbol:
      return self.last_output.get(str(symbol).upper())
    return self.last_output
