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
