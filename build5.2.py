#!/usr/bin/env python3
# build5.2.py
#
# Phase 5.2 - Execution & Machine Learning Tier
# Generates:
# 1) phoenix-bridge/execution_router.py
# 2) phoenix-bridge/ml_anomaly_detector.py
# 3) phoenix-frontend/js/widgets/executionAlgoWidget.js
# 4) phoenix-frontend/js/widgets/anomalyRadarWidget.js
#
# Also applies integration upgrades:
# - phoenix-bridge/main.py (router + anomaly endpoints/ws)
# - phoenix-bridge/requirements.txt
# - phoenix-frontend/js/app/appShell.js (widget registration + mount points)
# - phoenix-frontend/institutional-cockpit-v5.html (adds widget slots)
# - phoenix-frontend/css/institutional-cockpit-v5.css (widget styling)
#
# Usage:
#   python3 build5.2.py
#
# Then:
#   cd ~/Crypto-Monsty
#   cp .env.example .env
#   docker compose up -d --build
#   # open phoenix-frontend/institutional-cockpit-v5.html

from pathlib import Path
import textwrap

ROOT = Path(".").resolve()

FILES = {
    # =========================================================
    # BRIDGE: EXECUTION ROUTER
    # =========================================================
    "phoenix-bridge/execution_router.py": r'''
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
    ''',

    # =========================================================
    # BRIDGE: ML ANOMALY DETECTOR
    # =========================================================
    "phoenix-bridge/ml_anomaly_detector.py": r'''
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
    ''',

    # =========================================================
    # BRIDGE MAIN.PY UPGRADE (integrates execution + anomaly)
    # =========================================================
    "phoenix-bridge/main.py": r'''
    import os
    import time
    import asyncio
    from typing import List, Dict, Optional, Any

    import requests
    from fastapi import FastAPI, HTTPException, Header, WebSocket, WebSocketDisconnect
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel, Field

    from execution_router import ExecutionRouter
    from ml_anomaly_detector import MLAnomalyDetector

    BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://localhost:8787")
    BRIDGE_API_KEY = os.getenv("BRIDGE_API_KEY", "dev-bridge-key")

    app = FastAPI(title="Phoenix Bridge V8.3", version="2.0.0")
    app.add_middleware(
      CORSMiddleware,
      allow_origins=["*"],
      allow_credentials=True,
      allow_methods=["*"],
      allow_headers=["*"],
    )

    router = ExecutionRouter()
    anomaly = MLAnomalyDetector(window=800)

    ws_clients: List[WebSocket] = []


    class TickModel(BaseModel):
      exchange: str = "bridge"
      symbol: str
      price: float
      volume: float = 0.0
      side: str = "buy"
      ts: int = Field(default_factory=lambda: int(time.time() * 1000))
      latencyMs: Optional[float] = None
      raw: Optional[Dict[str, Any]] = None


    class SignalModel(BaseModel):
      symbol: str
      score: float
      confidence: Optional[float] = None
      explain: Optional[Dict[str, Any]] = None
      diagnostics: Optional[Dict[str, Any]] = None
      ts: int = Field(default_factory=lambda: int(time.time() * 1000))


    class OrderRecRequest(BaseModel):
      symbol: str
      score: float
      confidence: float = 0.5
      equity: float = 10000.0
      maxLeverage: int = 20
      riskMode: str = "balanced"


    class ExecutionOrderRequest(BaseModel):
      symbol: str
      side: str
      qty: float
      algo: str
      duration_sec: int = 60
      slices: int = 12
      display_qty: Optional[float] = None
      min_clip: Optional[float] = None
      max_clip: Optional[float] = None
      exchange_weights: Optional[Dict[str, float]] = None
      mark_price: Optional[float] = None
      slippage_bps: float = 3.0


    def require_key(x_api_key: Optional[str]):
      if x_api_key != BRIDGE_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid bridge API key")


    async def broadcast(payload: Dict[str, Any]):
      dead = []
      for c in ws_clients:
        try:
          await c.send_json(payload)
        except Exception:
          dead.append(c)
      for d in dead:
        if d in ws_clients:
          ws_clients.remove(d)


    @app.get("/health")
    def health():
      backend_ok = False
      backend_status = None
      try:
        r = requests.get(f"{BACKEND_BASE_URL}/health", timeout=2.0)
        backend_ok = r.ok
        backend_status = r.json() if r.ok else {"status_code": r.status_code}
      except Exception as e:
        backend_status = {"error": str(e)}

      return {
        "ok": True,
        "service": "phoenix-bridge",
        "ts": int(time.time() * 1000),
        "backend": {"ok": backend_ok, "status": backend_status}
      }


    @app.post("/signal")
    def post_signal(payload: SignalModel, x_api_key: Optional[str] = Header(default=None)):
      require_key(x_api_key)
      data = payload.model_dump()
      try:
        r = requests.post(f"{BACKEND_BASE_URL}/api/bridge/signal", json=data, timeout=4.0)
        if not r.ok:
          raise HTTPException(status_code=502, detail=f"Backend rejected signal: {r.text}")
      except HTTPException:
        raise
      except Exception as e:
        raise HTTPException(status_code=502, detail=f"Backend unreachable: {e}")

      asyncio.create_task(broadcast({"type": "signal", "payload": data}))
      return {"ok": True, "forwarded": True, "backend": r.json()}


    @app.post("/ticks")
    def post_ticks(ticks: List[TickModel], x_api_key: Optional[str] = Header(default=None)):
      require_key(x_api_key)
      if not ticks:
        return {"ok": True, "ingested": 0, "anomalies": []}

      payload_ticks = [t.model_dump() for t in ticks]

      # run anomaly detection first
      anomaly_out = anomaly.ingest_ticks(payload_ticks)

      # forward ticks to backend
      try:
        r = requests.post(f"{BACKEND_BASE_URL}/api/bridge/ticks", json={"ticks": payload_ticks}, timeout=6.0)
        if not r.ok:
          raise HTTPException(status_code=502, detail=f"Backend rejected ticks: {r.text}")
      except HTTPException:
        raise
      except Exception as e:
        raise HTTPException(status_code=502, detail=f"Backend unreachable: {e}")

      asyncio.create_task(broadcast({
        "type": "anomaly_batch",
        "count": len(anomaly_out),
        "payload": anomaly_out,
        "ts": int(time.time() * 1000)
      }))

      return {"ok": True, "ingested": len(payload_ticks), "anomalies": anomaly_out, "backend": r.json()}


    @app.get("/anomaly/latest")
    def anomaly_latest(symbol: Optional[str] = None):
      return {"ok": True, "data": anomaly.latest(symbol)}


    @app.post("/orders/recommendation")
    def order_recommendation(req: OrderRecRequest, x_api_key: Optional[str] = Header(default=None)):
      require_key(x_api_key)

      score = max(-1.0, min(1.0, float(req.score)))
      conf = max(0.0, min(1.0, float(req.confidence)))
      abs_edge = abs(score) * conf

      if req.riskMode == "conservative":
        base_risk = 0.005
        lev_cap = min(req.maxLeverage, 5)
      elif req.riskMode == "aggressive":
        base_risk = 0.02
        lev_cap = min(req.maxLeverage, 25)
      else:
        base_risk = 0.01
        lev_cap = min(req.maxLeverage, 12)

      risk_budget = req.equity * base_risk * (0.5 + abs_edge)
      leverage = max(1, int(round(1 + abs_edge * (lev_cap - 1))))
      notional = risk_budget * leverage
      side = "buy" if score > 0 else "sell" if score < 0 else "flat"

      out = {
        "symbol": req.symbol.upper(),
        "side": side,
        "confidence": conf,
        "score": score,
        "suggestedLeverage": leverage,
        "riskBudget": risk_budget,
        "suggestedNotional": notional,
        "ts": int(time.time() * 1000)
      }

      asyncio.create_task(broadcast({"type": "order_recommendation", "payload": out}))
      return {"ok": True, "recommendation": out}


    @app.post("/execution/route")
    def execution_route(req: ExecutionOrderRequest, x_api_key: Optional[str] = Header(default=None)):
      require_key(x_api_key)
      try:
        plan = router.create_schedule(req.model_dump())
      except Exception as e:
        raise HTTPException(status_code=400, detail=f"route_build_error: {e}")

      asyncio.create_task(broadcast({"type": "execution_plan", "payload": plan}))
      return {"ok": True, "plan": plan}


    @app.post("/execution/execute")
    def execution_execute(req: ExecutionOrderRequest, x_api_key: Optional[str] = Header(default=None)):
      require_key(x_api_key)
      try:
        plan = router.create_schedule(req.model_dump())
        result = router.simulate_execute(plan, mark_price=req.mark_price, slippage_bps=req.slippage_bps)
      except Exception as e:
        raise HTTPException(status_code=400, detail=f"execute_error: {e}")

      asyncio.create_task(broadcast({"type": "execution_result", "payload": result}))
      return {"ok": True, "result": result}


    @app.websocket("/ws/signals")
    async def ws_signals(ws: WebSocket):
      await ws.accept()
      ws_clients.append(ws)
      try:
        await ws.send_json({"type": "hello", "service": "phoenix-bridge", "ts": int(time.time() * 1000)})
        while True:
          _ = await ws.receive_text()
      except WebSocketDisconnect:
        pass
      finally:
        if ws in ws_clients:
          ws_clients.remove(ws)
    ''',

    "phoenix-bridge/requirements.txt": r'''
    fastapi==0.112.0
    uvicorn==0.30.3
    requests==2.32.3
    websockets==12.0
    pydantic==2.8.2
    python-dotenv==1.0.1
    scikit-learn==1.5.1
    numpy==1.26.4
    ''',

    # =========================================================
    # FRONTEND WIDGETS
    # =========================================================
    "phoenix-frontend/js/widgets/executionAlgoWidget.js": r'''
    export function registerExecutionAlgoWidget(registry) {
      registry.register("executionAlgo", (target, ctx) => {
        const container = target.querySelector("#execution-algo-box");
        if (!container) return { destroy() {} };

        container.innerHTML = `
          <div class="controls" style="margin-bottom:8px;display:flex;gap:8px;flex-wrap:wrap;">
            <select id="ex-algo">
              <option value="twap">TWAP</option>
              <option value="vwap">VWAP</option>
              <option value="iceberg">Iceberg</option>
            </select>
            <input id="ex-symbol" type="text" value="BTCUSDT" style="width:100px" />
            <input id="ex-side" type="text" value="buy" style="width:80px" />
            <input id="ex-qty" type="number" step="0.0001" value="0.05" style="width:100px" />
            <input id="ex-duration" type="number" value="60" style="width:90px" />
            <input id="ex-slices" type="number" value="12" style="width:80px" />
            <button id="ex-plan">Plan</button>
            <button id="ex-run">Execute</button>
          </div>
          <div class="mono" id="ex-log" style="border:1px solid #28406a;background:#0a1322;border-radius:8px;padding:8px;max-height:240px;overflow:auto;"></div>
        `;

        const q = (id) => container.querySelector(id);
        const logEl = q("#ex-log");

        const log = (line) => {
          const d = document.createElement("div");
          d.textContent = line;
          logEl.appendChild(d);
          logEl.scrollTop = logEl.scrollHeight;
        };

        const payload = () => ({
          symbol: (q("#ex-symbol").value || "BTCUSDT").toUpperCase(),
          side: (q("#ex-side").value || "buy").toLowerCase() === "sell" ? "sell" : "buy",
          qty: Number(q("#ex-qty").value || 0),
          algo: (q("#ex-algo").value || "twap").toLowerCase(),
          duration_sec: Number(q("#ex-duration").value || 60),
          slices: Number(q("#ex-slices").value || 12),
          display_qty: Number(q("#ex-qty").value || 0) * 0.08,
          min_clip: Number(q("#ex-qty").value || 0) * 0.05,
          max_clip: Number(q("#ex-qty").value || 0) * 0.15,
          slippage_bps: 3.0
        });

        async function callBridge(path, body) {
          const r = await fetch(`http://localhost:8899${path}`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "x-api-key": "dev-bridge-key"
            },
            body: JSON.stringify(body)
          });
          const j = await r.json();
          if (!r.ok) throw new Error(j?.detail || j?.error || "Bridge error");
          return j;
        }

        q("#ex-plan").addEventListener("click", async () => {
          try {
            const j = await callBridge("/execution/route", payload());
            log(`[plan] order_id=${j.plan.order_id} slices=${j.plan.slices.length} algo=${j.plan.algo}`);
            ctx.shell.bus.emit("execution:plan", j.plan);
          } catch (e) {
            log(`[plan-error] ${e.message || e}`);
          }
        });

        q("#ex-run").addEventListener("click", async () => {
          try {
            const st = ctx.shell.store.get();
            const mark = Number(st.lastTick?.price || 0) || undefined;
            const j = await callBridge("/execution/execute", { ...payload(), mark_price: mark });
            log(`[exec] order_id=${j.result.order_id} status=${j.result.status} filled=${j.result.filled_qty} avg=${j.result.avg_exec_price}`);
            for (const f of j.result.fills.slice(0, 30)) {
              log(`  [fill] ${f.exchange} qty=${f.qty} px=${f.metadata.exec_price.toFixed(4)} ts=${f.ts}`);
            }
            ctx.shell.bus.emit("execution:result", j.result);
          } catch (e) {
            log(`[exec-error] ${e.message || e}`);
          }
        });

        // listen bridge ws events (proxied via app shell bus)
        const u1 = ctx.shell.bus.on("bridge:execution_plan", (p) => {
          log(`[bridge-plan] ${p.order_id} slices=${p.slices?.length || 0}`);
        });
        const u2 = ctx.shell.bus.on("bridge:execution_result", (r) => {
          log(`[bridge-result] ${r.order_id} filled=${r.filled_qty} avg=${r.avg_exec_price}`);
        });

        return {
          destroy() {
            u1();
            u2();
          }
        };
      });
    }

    export default registerExecutionAlgoWidget;
    ''',

    "phoenix-frontend/js/widgets/anomalyRadarWidget.js": r'''
    export function registerAnomalyRadarWidget(registry) {
      registry.register("anomalyRadar", (target, ctx) => {
        const host = target.querySelector("#anomaly-radar-box");
        if (!host) return { destroy() {} };

        host.innerHTML = `
          <canvas id="anomaly-canvas" width="620" height="260" style="width:100%;height:260px;border:1px solid #2b416c;border-radius:8px;background:#071121;display:block"></canvas>
          <div class="mono" id="anomaly-log" style="margin-top:8px;border:1px solid #28406a;background:#0a1322;border-radius:8px;padding:8px;max-height:130px;overflow:auto;"></div>
        `;

        const canvas = host.querySelector("#anomaly-canvas");
        const c = canvas.getContext("2d");
        const logEl = host.querySelector("#anomaly-log");

        const state = {
          series: [], // recent anomaly scores
          latest: null
        };

        function log(line) {
          const d = document.createElement("div");
          d.textContent = line;
          logEl.appendChild(d);
          logEl.scrollTop = logEl.scrollHeight;
        }

        function drawRadar(score, labels = []) {
          const w = canvas.width, h = canvas.height;
          c.clearRect(0, 0, w, h);
          c.fillStyle = "#061021";
          c.fillRect(0, 0, w, h);

          const cx = 140, cy = h / 2;
          const R = 90;

          // rings
          c.strokeStyle = "#1f3357";
          for (let i = 1; i <= 4; i++) {
            c.beginPath();
            c.arc(cx, cy, (R * i) / 4, 0, Math.PI * 2);
            c.stroke();
          }

          // axes
          const axes = ["Flow", "Latency", "Return", "Spread", "Volume"];
          for (let i = 0; i < axes.length; i++) {
            const a = (Math.PI * 2 * i) / axes.length - Math.PI / 2;
            const x = cx + Math.cos(a) * R;
            const y = cy + Math.sin(a) * R;
            c.strokeStyle = "#2a3f68";
            c.beginPath();
            c.moveTo(cx, cy);
            c.lineTo(x, y);
            c.stroke();
            c.fillStyle = "#89a6d8";
            c.font = "11px monospace";
            c.fillText(axes[i], cx + Math.cos(a) * (R + 8), cy + Math.sin(a) * (R + 8));
          }

          // polygon intensity from score
          const s = Math.max(0, Math.min(1, Number(score || 0)));
          const vals = [
            0.3 + 0.7 * s,
            0.25 + 0.8 * s,
            0.2 + 0.75 * s,
            0.35 + 0.65 * s,
            0.3 + 0.7 * s
          ];

          c.beginPath();
          for (let i = 0; i < vals.length; i++) {
            const a = (Math.PI * 2 * i) / vals.length - Math.PI / 2;
            const rr = R * vals[i];
            const x = cx + Math.cos(a) * rr;
            const y = cy + Math.sin(a) * rr;
            if (i === 0) c.moveTo(x, y); else c.lineTo(x, y);
          }
          c.closePath();

          const col = s > 0.8 ? "rgba(239,68,68,0.65)" : s > 0.5 ? "rgba(245,158,11,0.65)" : "rgba(34,197,94,0.65)";
          c.fillStyle = col;
          c.fill();
          c.strokeStyle = "#dbe9ff";
          c.stroke();

          // right panel trend
          const gx = 300, gy = 30, gw = 300, gh = 190;
          c.strokeStyle = "#1f3357";
          c.strokeRect(gx, gy, gw, gh);

          const arr = state.series;
          if (arr.length > 1) {
            c.beginPath();
            for (let i = 0; i < arr.length; i++) {
              const x = gx + (i / (arr.length - 1)) * gw;
              const y = gy + gh - arr[i] * gh;
              if (i === 0) c.moveTo(x, y); else c.lineTo(x, y);
            }
            c.strokeStyle = "#60a5fa";
            c.stroke();
          }

          c.fillStyle = "#dbe9ff";
          c.font = "12px monospace";
          c.fillText(`Anomaly Score: ${s.toFixed(4)}`, gx, gy - 8);
          c.fillText(`Labels: ${labels.join(", ") || "-"}`, 8, h - 10);
        }

        const onAnomaly = (evt) => {
          // evt expected from bridge bus
          const data = evt?.payload || evt;
          if (!data) return;

          const score = Number(data.anomalyScore || 0);
          state.latest = data;
          state.series.push(Math.max(0, Math.min(1, score)));
          if (state.series.length > 220) state.series.shift();

          drawRadar(score, data.labels || []);
          log(`[${data.symbol}] score=${score.toFixed(4)} severity=${data.severity} labels=${(data.labels || []).join("|")}`);
        };

        const u1 = ctx.shell.bus.on("bridge:anomaly", onAnomaly);

        // initial draw
        drawRadar(0, []);

        return {
          destroy() {
            u1();
          }
        };
      });
    }

    export default registerAnomalyRadarWidget;
    ''',

    # =========================================================
    # FRONTEND INTEGRATION: APPSHELL
    # =========================================================
    "phoenix-frontend/js/app/appShell.js": r'''
    import { io } from "https://cdn.socket.io/4.7.5/socket.io.esm.min.js";
    import EventBus from "./eventBus.js";
    import Store from "./store.js";
    import LayoutEngine from "./layoutEngine.js";
    import WidgetRegistry from "./widgetRegistry.js";
    import registerExecutionAlgoWidget from "../widgets/executionAlgoWidget.js";
    import registerAnomalyRadarWidget from "../widgets/anomalyRadarWidget.js";

    export class AppShell {
      constructor(rootEl) {
        this.rootEl = rootEl;
        this.bus = new EventBus();
        this.store = new Store({
          symbol: "BTCUSDT",
          backendHealth: null,
          bridgeHealth: null,
          consensus: null,
          lastTick: null,
          riskState: null,
          exchangeHealth: null,
          indicators: {}
        });
        this.layout = new LayoutEngine("phoenix_v5_layout");
        this.registry = new WidgetRegistry();
        this.socket = null;
        this.bridgeWS = null;
        this.worker = null;
        this.priceSeries = [];
      }

      init() {
        this._renderSkeleton();
        this._wireStatusBindings();
        this._initSocket();
        this._initBridgeWS();
        this._initWorker();
        this._registerWidgets();
        this._mountWidgets();
        this._initTerminalControls();
        this._initReplayControl();
        this._pollHealth();
      }

      _renderSkeleton() {
        this.rootEl.innerHTML = `
          <header class="app-topbar">
            <div class="app-title">
              <h1>Phoenix Institutional Cockpit V5.2</h1>
              <small>Execution & ML Tier + SOR + Anomaly Radar</small>
            </div>
            <div class="controls">
              <select id="sym-select">
                <option>BTCUSDT</option>
                <option>ETHUSDT</option>
                <option>SOLUSDT</option>
              </select>
              <button id="save-layout">Save Layout</button>
              <button id="load-layout">Load Layout</button>
            </div>
            <div class="status-grid">
              <div class="status-card"><span class="label">Backend</span><span class="value" id="st-backend">--</span></div>
              <div class="status-card"><span class="label">Bridge</span><span class="value" id="st-bridge">--</span></div>
              <div class="status-card"><span class="label">Redis</span><span class="value" id="st-redis">--</span></div>
              <div class="status-card"><span class="label">Postgres</span><span class="value" id="st-pg">--</span></div>
              <div class="status-card"><span class="label">RTT</span><span class="value" id="st-rtt">--</span></div>
            </div>
          </header>

          <section class="dock" id="dock">
            <article class="widget w-graph">
              <div class="head">HFT Graph <span id="graph-meta"></span></div>
              <div class="body"><canvas id="graph-canvas" width="1400" height="380"></canvas></div>
            </article>

            <article class="widget w-feed">
              <div class="head">Feed / Consensus</div>
              <div class="body mono" id="feed-box">Waiting...</div>
            </article>

            <article class="widget w-risk">
              <div class="head">Risk / Positions</div>
              <div class="body mono" id="risk-box">Waiting...</div>
            </article>

            <article class="widget w-terminal">
              <div class="head">Execution Terminal</div>
              <div class="body">
                <div class="term-log mono" id="term-log"></div>
                <input class="term-input" id="term-input" placeholder="/buy BTCUSDT 0.01 10x | /consensus BTCUSDT | /deposit 1000" />
              </div>
            </article>

            <article class="widget w-replay">
              <div class="head">Replay / Backtest</div>
              <div class="body">
                <div class="controls">
                  <button id="btn-backtest">Run 1h Backtest</button>
                  <select id="bt-source">
                    <option value="postgres">postgres</option>
                    <option value="redis">redis</option>
                  </select>
                </div>
                <pre class="mono" id="backtest-box">No results.</pre>
              </div>
            </article>

            <article class="widget w-inspector">
              <div class="head">Inspector</div>
              <div class="body mono" id="inspector-box">Waiting...</div>
            </article>

            <article class="widget w-execalgo">
              <div class="head">Execution Algo (TWAP/VWAP/Iceberg)</div>
              <div class="body" id="execution-algo-box"></div>
            </article>

            <article class="widget w-anomaly">
              <div class="head">Anomaly Radar</div>
              <div class="body" id="anomaly-radar-box"></div>
            </article>
          </section>
        `;

        const symSelect = this.rootEl.querySelector("#sym-select");
        symSelect.addEventListener("change", () => {
          this.store.set({ symbol: symSelect.value });
          this._log(`[ui] symbol -> ${symSelect.value}`);
        });

        this.rootEl.querySelector("#save-layout").addEventListener("click", () => {
          this.layout.save({ symbol: this.store.get().symbol });
          this._log("[layout] saved");
        });

        this.rootEl.querySelector("#load-layout").addEventListener("click", () => {
          const loaded = this.layout.load({ symbol: "BTCUSDT" });
          this.store.set({ symbol: loaded.symbol || "BTCUSDT" });
          symSelect.value = this.store.get().symbol;
          this._log("[layout] loaded");
        });
      }

      _wireStatusBindings() {
        this.store.subscribe((s) => {
          const set = (id, txt, color="#22c55e") => {
            const el = this.rootEl.querySelector(id);
            if (!el) return;
            el.textContent = txt;
            el.style.color = color;
          };

          set("#st-backend", s.backendHealth?.ok ? "ONLINE" : "DOWN", s.backendHealth?.ok ? "#22c55e" : "#ef4444");
          set("#st-bridge", s.bridgeHealth?.ok ? "ONLINE" : "DOWN", s.bridgeHealth?.ok ? "#22c55e" : "#ef4444");

          const redisOk = !!s.backendHealth?.redis?.ok;
          set("#st-redis", redisOk ? "HOT" : "OFFLINE", redisOk ? "#22c55e" : "#f59e0b");

          const pgReady = !!s.backendHealth?.services?.writer;
          set("#st-pg", pgReady ? "READY" : "UNKNOWN", pgReady ? "#22c55e" : "#f59e0b");
        });
      }

      _initSocket() {
        this.socket = io("http://localhost:8787", { transports: ["websocket"] });

        this.socket.on("connect", () => {
          this._log("[socket] connected");
          this.socket.emit("paper:bootstrap");
        });

        this.socket.on("market:tick", (tick) => {
          this.store.set({ lastTick: tick });
          this.priceSeries.push(Number(tick.price || 0));
          if (this.priceSeries.length > 1500) this.priceSeries.shift();

          if (this.worker) this.worker.postMessage({ type: "ticks", payload: [tick] });

          this.bus.emit("tick", tick);
        });

        this.socket.on("consensus:update", (c) => {
          this.store.set({ consensus: c });
          this.bus.emit("consensus", c);
        });

        this.socket.on("paper:state", (state) => {
          this.store.set({ riskState: state });
          this.bus.emit("risk", state);
        });

        this.socket.on("exchange:health", (h) => {
          this.store.set({ exchangeHealth: h });
          this.bus.emit("exchangeHealth", h);
        });

        this.socket.on("paper:orderAck", (ack) => this._log(`[orderAck] ${JSON.stringify(ack)}`));
        this.socket.on("paper:fill", (fill) => this._log(`[fill] ${JSON.stringify(fill)}`));
        this.socket.on("paper:error", (e) => this._log(`[error] ${e.message}`));
      }

      _initBridgeWS() {
        const ws = new WebSocket("ws://localhost:8899/ws/signals");
        this.bridgeWS = ws;

        ws.onopen = () => this._log("[bridge-ws] connected");
        ws.onclose = () => this._log("[bridge-ws] disconnected");
        ws.onerror = () => this._log("[bridge-ws] error");

        ws.onmessage = (ev) => {
          try {
            const msg = JSON.parse(ev.data);
            if (msg.type === "execution_plan") this.bus.emit("bridge:execution_plan", msg.payload);
            if (msg.type === "execution_result") this.bus.emit("bridge:execution_result", msg.payload);

            if (msg.type === "anomaly_batch" && Array.isArray(msg.payload)) {
              for (const item of msg.payload) this.bus.emit("bridge:anomaly", item);
            }

            if (msg.type === "signal") this.bus.emit("bridge:signal", msg.payload);
          } catch {}
        };

        // keep alive
        setInterval(() => {
          try { ws.send("ping"); } catch {}
        }, 5000);
      }

      _initWorker() {
        this.worker = new Worker(new URL("../workers/indicatorWorker.js", import.meta.url), { type: "module" });
        this.worker.onmessage = (e) => {
          const msg = e.data || {};
          if (msg.type !== "indicators:update") return;
          const map = { ...(this.store.get().indicators || {}) };
          for (const x of msg.payload || []) map[x.symbol] = x;
          this.store.set({ indicators: map });
          this.bus.emit("indicators", map);
        };
      }

      _registerWidgets() {
        // core widgets
        this.registry.register("graph", (target, ctx) => {
          const canvas = target.querySelector("#graph-canvas");
          const meta = target.querySelector("#graph-meta");
          const c = canvas.getContext("2d");

          const draw = () => {
            const w = canvas.width, h = canvas.height;
            c.clearRect(0, 0, w, h);
            c.fillStyle = "#071121";
            c.fillRect(0, 0, w, h);

            const arr = ctx.shell.priceSeries.filter((x) => Number.isFinite(x) && x > 0);
            if (arr.length < 2) {
              c.fillStyle = "#8ca3d6";
              c.font = "12px monospace";
              c.fillText("waiting for ticks...", 10, 20);
              return;
            }

            const min = Math.min(...arr), max = Math.max(...arr), span = Math.max(1e-9, max - min);
            c.strokeStyle = "#60a5fa";
            c.lineWidth = 1.2;
            c.beginPath();
            for (let i = 0; i < arr.length; i++) {
              const x = (i / (arr.length - 1)) * (w - 20) + 10;
              const y = h - 20 - ((arr[i] - min) / span) * (h - 40);
              if (i === 0) c.moveTo(x, y); else c.lineTo(x, y);
            }
            c.stroke();

            const st = ctx.shell.store.get();
            const sym = st.symbol;
            const ind = st.indicators?.[sym]?.indicators || {};
            meta.textContent = `${sym} | sma20=${fmt(ind.sma20)} vol20=${fmt(ind.vol20)}`;
          };

          const u1 = ctx.shell.bus.on("tick", draw);
          const u2 = ctx.shell.bus.on("indicators", draw);
          draw();
          return { destroy() { u1(); u2(); } };
        });

        this.registry.register("feed", (target, ctx) => {
          const box = target.querySelector("#feed-box");
          const upd = () => {
            const s = ctx.shell.store.get();
            box.textContent = JSON.stringify({
              symbol: s.symbol,
              consensus: s.consensus,
              lastTick: s.lastTick,
              indicators: s.indicators?.[s.symbol] || null
            }, null, 2);
          };
          const u = ctx.shell.store.subscribe(upd);
          upd();
          return { destroy() { u(); } };
        });

        this.registry.register("risk", (target, ctx) => {
          const box = target.querySelector("#risk-box");
          const upd = () => {
            box.textContent = JSON.stringify(ctx.shell.store.get().riskState || { note: "No state yet" }, null, 2);
          };
          const u = ctx.shell.bus.on("risk", upd);
          upd();
          return { destroy() { u(); } };
        });

        this.registry.register("inspector", (target, ctx) => {
          const box = target.querySelector("#inspector-box");
          const upd = () => {
            const s = ctx.shell.store.get();
            box.textContent = JSON.stringify({
              backendHealth: s.backendHealth,
              bridgeHealth: s.bridgeHealth,
              exchangeHealth: s.exchangeHealth
            }, null, 2);
          };
          const u = ctx.shell.store.subscribe(upd);
          upd();
          return { destroy() { u(); } };
        });

        // external widgets (Phase 5.2)
        registerExecutionAlgoWidget(this.registry);
        registerAnomalyRadarWidget(this.registry);
      }

      _mountWidgets() {
        const dock = this.rootEl.querySelector("#dock");
        this.registry.mount("graph", dock, { shell: this });
        this.registry.mount("feed", dock, { shell: this });
        this.registry.mount("risk", dock, { shell: this });
        this.registry.mount("inspector", dock, { shell: this });
        this.registry.mount("executionAlgo", dock, { shell: this });
        this.registry.mount("anomalyRadar", dock, { shell: this });
      }

      _initTerminalControls() {
        const logEl = this.rootEl.querySelector("#term-log");
        const input = this.rootEl.querySelector("#term-input");

        const append = (txt) => {
          const d = document.createElement("div");
          d.textContent = txt;
          logEl.appendChild(d);
          logEl.scrollTop = logEl.scrollHeight;
        };
        this._log = append;

        input.addEventListener("keydown", (e) => {
          if (e.key !== "Enter") return;
          const raw = input.value.trim();
          if (!raw) return;
          input.value = "";
          append(`> ${raw}`);
          this._handleCommand(raw, append);
        });
      }

      async _handleCommand(raw, log) {
        const p = raw.split(/\s+/);
        const cmd = (p[0] || "").toLowerCase();

        if (cmd === "/buy" || cmd === "/sell") {
          this.socket.emit("paper:order", {
            userId: "demo",
            type: "market",
            side: cmd.slice(1),
            symbol: (p[1] || this.store.get().symbol).toUpperCase(),
            qty: Number(p[2] || 0.001),
            leverage: Number(String(p[3] || "1x").replace(/x/i, "")) || 1,
            marginType: "cross"
          });
          return;
        }

        if (cmd === "/consensus") {
          this.socket.emit("consensus:get", { symbol: (p[1] || this.store.get().symbol).toUpperCase() });
          return;
        }

        if (cmd === "/deposit") {
          this.socket.emit("paper:deposit", { userId: "demo", amount: Number(p[1] || 0) });
          return;
        }

        if (cmd === "/bridge-signal") {
          const symbol = (p[1] || this.store.get().symbol).toUpperCase();
          const score = Number(p[2] || 0);
          try {
            const r = await fetch("http://localhost:8899/signal", {
              method: "POST",
              headers: { "Content-Type": "application/json", "x-api-key": "dev-bridge-key" },
              body: JSON.stringify({
                symbol, score, confidence: Math.min(1, Math.abs(score)),
                explain: { source: "manual-cli" }, diagnostics: { manual: true }
              })
            });
            const j = await r.json();
            log(`[bridge-signal] ${JSON.stringify(j)}`);
          } catch (e) {
            log(`[bridge-signal-error] ${e}`);
          }
          return;
        }

        log("[unknown command]");
      }

      _initReplayControl() {
        const btn = this.rootEl.querySelector("#btn-backtest");
        const src = this.rootEl.querySelector("#bt-source");
        const out = this.rootEl.querySelector("#backtest-box");

        btn.addEventListener("click", async () => {
          const symbol = this.store.get().symbol;
          const now = Date.now();
          const body = {
            symbol,
            fromTs: now - 3600_000,
            toTs: now,
            source: src.value,
            longThreshold: 0.15,
            shortThreshold: -0.15,
            feeBps: 2
          };
          try {
            const r = await fetch("http://localhost:8787/api/replay/backtest", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body)
            });
            const j = await r.json();
            out.textContent = JSON.stringify(j, null, 2);
          } catch (e) {
            out.textContent = JSON.stringify({ ok: false, error: String(e) }, null, 2);
          }
        });
      }

      _pollHealth() {
        const tick = async () => {
          try {
            const b = await fetch("http://localhost:8787/health").then((r) => r.json());
            this.store.set({ backendHealth: b });
          } catch {
            this.store.set({ backendHealth: { ok: false } });
          }

          try {
            const br = await fetch("http://localhost:8899/health").then((r) => r.json());
            this.store.set({ bridgeHealth: br });
          } catch {
            this.store.set({ bridgeHealth: { ok: false } });
          }

          const t0 = performance.now();
          this.socket.emit("latency:ping", { t0: Date.now() }, () => {
            const dt = performance.now() - t0;
            const el = this.rootEl.querySelector("#st-rtt");
            if (el) el.textContent = `${dt.toFixed(2)} ms`;
          });
        };

        tick();
        setInterval(tick, 3000);
      }
    }

    function fmt(v) {
      return v == null || Number.isNaN(Number(v)) ? "-" : Number(v).toFixed(4);
    }

    export default AppShell;
    ''',

    # =========================================================
    # FRONTEND HTML/CSS updates for new widget areas
    # =========================================================
    "phoenix-frontend/institutional-cockpit-v5.html": r'''
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="UTF-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1.0" />
      <title>Phoenix Institutional Cockpit V5.2</title>
      <link rel="stylesheet" href="./css/institutional-cockpit-v5.css" />
    </head>
    <body>
      <div id="app-root"></div>
      <script type="module" src="./js/app/bootstrap.js"></script>
    </body>
    </html>
    ''',

    "phoenix-frontend/css/institutional-cockpit-v5.css": r'''
    :root{
      --bg:#060d17;
      --panel:#0d1728;
      --panel2:#0a1321;
      --line:#22314d;
      --text:#d8e6ff;
      --muted:#8fa4cc;
      --green:#22c55e;
      --red:#ef4444;
      --blue:#60a5fa;
      --amber:#f59e0b;
    }

    *{box-sizing:border-box}
    body{
      margin:0;
      color:var(--text);
      background:radial-gradient(1400px 800px at 20% -10%, #13233f 0%, var(--bg) 45%);
      font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
    }

    #app-root{
      max-width:1800px;
      margin:0 auto;
      padding:14px;
    }

    .app-topbar{
      display:flex;
      justify-content:space-between;
      align-items:center;
      padding:12px;
      border:1px solid var(--line);
      border-radius:12px;
      background:linear-gradient(180deg,#0f1c32,#0a1424);
      margin-bottom:10px;
      gap:10px;
      flex-wrap:wrap;
    }

    .app-title h1{margin:0;font-size:20px}
    .app-title small{color:var(--muted)}

    .status-grid{
      display:grid;
      grid-template-columns:repeat(5,minmax(120px,1fr));
      gap:8px;
      min-width:640px;
    }

    .status-card{
      border:1px solid var(--line);
      border-radius:8px;
      padding:8px;
      background:#0b1424;
    }

    .status-card .label{display:block;color:var(--muted);font-size:11px}
    .status-card .value{font-weight:700;font-size:13px}

    .dock{
      display:grid;
      grid-template-columns:2.2fr 1fr;
      grid-template-areas:
        "graph feed"
        "risk terminal"
        "execalgo anomaly"
        "replay inspector";
      gap:10px;
    }

    .widget{
      border:1px solid var(--line);
      border-radius:12px;
      background:linear-gradient(180deg,var(--panel),var(--panel2));
      min-height:200px;
      box-shadow:0 8px 20px rgba(0,0,0,.25);
      overflow:hidden;
      display:flex;
      flex-direction:column;
    }

    .widget .head{
      padding:8px 10px;
      border-bottom:1px solid var(--line);
      font-size:12px;
      color:var(--blue);
      font-weight:700;
      letter-spacing:.3px;
      display:flex;
      justify-content:space-between;
      align-items:center;
    }

    .widget .body{
      padding:8px;
      flex:1;
      overflow:auto;
    }

    .w-graph{grid-area:graph}
    .w-feed{grid-area:feed}
    .w-risk{grid-area:risk}
    .w-terminal{grid-area:terminal}
    .w-execalgo{grid-area:execalgo}
    .w-anomaly{grid-area:anomaly}
    .w-replay{grid-area:replay}
    .w-inspector{grid-area:inspector}

    #graph-canvas{
      width:100%;
      height:360px;
      border:1px solid #26385d;
      border-radius:8px;
      display:block;
      background:#071121;
    }

    .mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;white-space:pre-wrap}
    .term-log{height:190px;border:1px solid #24365a;background:#0a1322;border-radius:8px;padding:8px;overflow:auto}
    .term-input{width:100%;margin-top:8px;padding:10px;border-radius:8px;border:1px solid #2e446f;background:#0b1528;color:#eaf2ff}

    .controls{display:flex;gap:8px;flex-wrap:wrap}
    button{
      border:1px solid #2d497c;background:linear-gradient(180deg,#1b3260,#152a4e);
      color:#dfe9ff;border-radius:8px;padding:7px 10px;font-weight:600;cursor:pointer
    }
    button:hover{filter:brightness(1.08)}

    select,input[type="text"],input[type="number"]{
      border:1px solid #2d497c;background:#0b1528;color:#dfe9ff;border-radius:8px;padding:7px 9px
    }

    @media (max-width:1300px){
      .dock{
        grid-template-columns:1fr;
        grid-template-areas:
          "graph"
          "feed"
          "risk"
          "terminal"
          "execalgo"
          "anomaly"
          "replay"
          "inspector";
      }
      .status-grid{
        min-width:0;
        grid-template-columns:repeat(2,minmax(120px,1fr));
      }
    }
    ''',
}

def write_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = textwrap.dedent(content).lstrip("\n")
    path.write_text(cleaned, encoding="utf-8")

def main():
    print("Generating Phase 5.2 Execution & Machine Learning Tier...")
    for rel, content in FILES.items():
        write_file(ROOT / rel, content)
        print(f"✔ {rel}")
    print("\nDone.")

if __name__ == "__main__":
    main()
