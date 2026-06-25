import math
import time
import uuid
from dataclasses import dataclass, asdict
from typing import Dict, List, Any, Optional


@dataclass
class SliceFill:
  parent_order_id: str
  slice_id: str
  exchange: str
  symbol: str
  side: str
  qty: float
  limit_price: Optional[float]
  status: str
  ts: int
  algo: str
  metadata: Dict[str, Any]


class ExecutionRouter:
  """
  Smart Order Router (SOR) with TWAP / VWAP / Iceberg scheduling.
  This module creates execution schedules and simulated fills suitable for
  bridge orchestration and frontend monitoring.
  """

  def __init__(self):
    self.supported_exchanges = ["binance", "bybit", "hyperliquid"]
    self.default_exchange_weights = {
      "binance": 0.45,
      "bybit": 0.35,
      "hyperliquid": 0.20
    }
    self.live_orders: Dict[str, Dict[str, Any]] = {}

  def _gen_id(self, prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"

  def _normalize_side(self, side: str) -> str:
    s = str(side or "buy").lower()
    return "sell" if s == "sell" else "buy"

  def _validate(self, req: Dict[str, Any]):
    if "symbol" not in req:
      raise ValueError("symbol is required")
    if "qty" not in req:
      raise ValueError("qty is required")
    if "algo" not in req:
      raise ValueError("algo is required")

    qty = float(req["qty"])
    if qty <= 0:
      raise ValueError("qty must be > 0")

    algo = str(req["algo"]).lower()
    if algo not in {"twap", "vwap", "iceberg"}:
      raise ValueError("algo must be twap|vwap|iceberg")

  def _calc_exchange_split(self, qty: float, exchange_weights: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    w = exchange_weights or self.default_exchange_weights
    # normalize
    s = sum(max(0.0, float(v)) for v in w.values())
    if s <= 0:
      w = self.default_exchange_weights
      s = sum(w.values())

    out: Dict[str, float] = {}
    remaining = qty

    exchanges = [e for e in self.supported_exchanges if e in w]
    for i, ex in enumerate(exchanges):
      part = qty * (float(w[ex]) / s)
      if i < len(exchanges) - 1:
        part = max(0.0, round(part, 8))
        out[ex] = part
        remaining -= part
      else:
        out[ex] = max(0.0, round(remaining, 8))

    return out

  def _twap_schedule(self, req: Dict[str, Any]) -> List[Dict[str, Any]]:
    qty = float(req["qty"])
    duration_sec = int(req.get("duration_sec", 60))
    slices = int(req.get("slices", 12))
    if slices <= 0:
      slices = 1
    interval = max(1, duration_sec // slices)

    per_slice = qty / slices
    side = self._normalize_side(req.get("side", "buy"))
    symbol = str(req["symbol"]).upper()

    exchange_split = self._calc_exchange_split(qty, req.get("exchange_weights"))
    schedule: List[Dict[str, Any]] = []
    ts0 = int(time.time() * 1000)

    for i in range(slices):
      step_qty = per_slice
      # split across exchanges
      for ex, ex_total in exchange_split.items():
        ex_slice_qty = (ex_total / qty) * step_qty if qty > 0 else 0
        schedule.append({
          "slice_index": i,
          "slice_ts": ts0 + i * interval * 1000,
          "exchange": ex,
          "symbol": symbol,
          "side": side,
          "qty": round(ex_slice_qty, 8),
          "algo": "twap",
          "metadata": {"interval_sec": interval}
        })

    return [s for s in schedule if s["qty"] > 0]

  def _vwap_schedule(self, req: Dict[str, Any]) -> List[Dict[str, Any]]:
    qty = float(req["qty"])
    profile = req.get("volume_profile") or [0.08, 0.09, 0.1, 0.11, 0.12, 0.12, 0.11, 0.1, 0.09, 0.08]
    total = sum(profile)
    if total <= 0:
      profile = [1.0]
      total = 1.0
    profile = [p / total for p in profile]

    side = self._normalize_side(req.get("side", "buy"))
    symbol = str(req["symbol"]).upper()
    duration_sec = int(req.get("duration_sec", 60))
    interval = max(1, duration_sec // len(profile))
    exchange_split = self._calc_exchange_split(qty, req.get("exchange_weights"))

    schedule: List[Dict[str, Any]] = []
    ts0 = int(time.time() * 1000)

    for i, p in enumerate(profile):
      step_qty = qty * p
      for ex, ex_total in exchange_split.items():
        ex_slice_qty = (ex_total / qty) * step_qty if qty > 0 else 0
        schedule.append({
          "slice_index": i,
          "slice_ts": ts0 + i * interval * 1000,
          "exchange": ex,
          "symbol": symbol,
          "side": side,
          "qty": round(ex_slice_qty, 8),
          "algo": "vwap",
          "metadata": {"v_profile_weight": p, "interval_sec": interval}
        })

    return [s for s in schedule if s["qty"] > 0]

  def _iceberg_schedule(self, req: Dict[str, Any]) -> List[Dict[str, Any]]:
    qty = float(req["qty"])
    side = self._normalize_side(req.get("side", "buy"))
    symbol = str(req["symbol"]).upper()
    duration_sec = int(req.get("duration_sec", 60))
    display_qty = float(req.get("display_qty", max(0.001, qty * 0.08)))
    randomize = bool(req.get("randomize", True))
    min_clip = float(req.get("min_clip", max(0.0001, display_qty * 0.7)))
    max_clip = float(req.get("max_clip", display_qty * 1.3))

    if min_clip <= 0 or max_clip <= 0 or min_clip > max_clip:
      raise ValueError("invalid iceberg clip bounds")

    exchange_split = self._calc_exchange_split(qty, req.get("exchange_weights"))
    schedule: List[Dict[str, Any]] = []
    ts0 = int(time.time() * 1000)

    # deterministic pseudo-random without random module (stable in restricted env)
    seed = int(ts0 % 9973)

    for ex, ex_qty in exchange_split.items():
      remain = ex_qty
      idx = 0
      while remain > 1e-12:
        # pseudo random clip in [min_clip, max_clip]
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        u = (seed % 10000) / 10000.0
        clip = min_clip + (max_clip - min_clip) * u if randomize else display_qty
        clip = min(clip, remain)
        step_ts = ts0 + int((idx * duration_sec * 1000) / max(1, math.ceil(ex_qty / max(1e-12, display_qty))))

        schedule.append({
          "slice_index": idx,
          "slice_ts": step_ts,
          "exchange": ex,
          "symbol": symbol,
          "side": side,
          "qty": round(clip, 8),
          "algo": "iceberg",
          "metadata": {"display_qty": display_qty, "remaining_after": round(remain - clip, 8)}
        })

        remain -= clip
        idx += 1

    return [s for s in schedule if s["qty"] > 0]

  def create_schedule(self, req: Dict[str, Any]) -> Dict[str, Any]:
    self._validate(req)
    algo = str(req["algo"]).lower()

    if algo == "twap":
      slices = self._twap_schedule(req)
    elif algo == "vwap":
      slices = self._vwap_schedule(req)
    else:
      slices = self._iceberg_schedule(req)

    order_id = self._gen_id("sor")
    plan = {
      "order_id": order_id,
      "created_ts": int(time.time() * 1000),
      "symbol": str(req["symbol"]).upper(),
      "side": self._normalize_side(req.get("side", "buy")),
      "algo": algo,
      "total_qty": float(req["qty"]),
      "slices": slices,
      "status": "scheduled"
    }
    self.live_orders[order_id] = plan
    return plan

  def simulate_execute(self, plan: Dict[str, Any], mark_price: Optional[float] = None, slippage_bps: float = 3.0) -> Dict[str, Any]:
    """
    Simulates fills. In production replace with real exchange adapters.
    """
    fills: List[SliceFill] = []
    ts = int(time.time() * 1000)
    px = float(mark_price) if mark_price and mark_price > 0 else None

    total_filled = 0.0
    for s in plan["slices"]:
      base_px = px if px else 100.0
      side = s["side"]

      # simplistic slippage direction
      slip = (slippage_bps / 10000.0) * base_px
      exec_px = base_px + slip if side == "buy" else base_px - slip

      slice_id = self._gen_id("slice")
      fill = SliceFill(
        parent_order_id=plan["order_id"],
        slice_id=slice_id,
        exchange=s["exchange"],
        symbol=s["symbol"],
        side=side,
        qty=float(s["qty"]),
        limit_price=None,
        status="filled",
        ts=ts,
        algo=plan["algo"],
        metadata={"slice_index": s["slice_index"], "slice_ts": s["slice_ts"], "exec_price": exec_px}
      )
      fills.append(fill)
      total_filled += float(s["qty"])

    avg_px = None
    if fills:
      notional = sum(f.qty * f.metadata["exec_price"] for f in fills)
      avg_px = notional / max(1e-12, total_filled)

    result = {
      "order_id": plan["order_id"],
      "algo": plan["algo"],
      "symbol": plan["symbol"],
      "side": plan["side"],
      "requested_qty": plan["total_qty"],
      "filled_qty": total_filled,
      "avg_exec_price": avg_px,
      "fills": [asdict(f) for f in fills],
      "status": "filled" if total_filled >= plan["total_qty"] * 0.999 else "partial"
    }

    # update live order state
    if plan["order_id"] in self.live_orders:
      self.live_orders[plan["order_id"]]["status"] = result["status"]
      self.live_orders[plan["order_id"]]["last_result"] = result

    return result
