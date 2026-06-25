import os
import time
import asyncio
from typing import List, Dict, Optional, Any

import requests
from fastapi import FastAPI, HTTPException, Header, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://localhost:8787")
BRIDGE_API_KEY = os.getenv("BRIDGE_API_KEY", "dev-bridge-key")
BRIDGE_PORT = int(os.getenv("BRIDGE_PORT", "8899"))

app = FastAPI(title="Phoenix Bridge V8.3", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

clients: List[WebSocket] = []

def require_key(x_api_key: Optional[str]):
  if x_api_key != BRIDGE_API_KEY:
    raise HTTPException(status_code=401, detail="Invalid bridge API key")

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
    "backend": {
      "ok": backend_ok,
      "status": backend_status
    }
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

  # fan-out to WS clients
  asyncio.create_task(_broadcast_json({
    "type": "signal",
    "payload": data
  }))

  return {"ok": True, "forwarded": True, "backend": r.json()}

@app.post("/ticks")
def post_ticks(ticks: List[TickModel], x_api_key: Optional[str] = Header(default=None)):
  require_key(x_api_key)
  if not ticks:
    return {"ok": True, "ingested": 0}

  body = {"ticks": [t.model_dump() for t in ticks]}
  try:
    r = requests.post(f"{BACKEND_BASE_URL}/api/bridge/ticks", json=body, timeout=6.0)
    if not r.ok:
      raise HTTPException(status_code=502, detail=f"Backend rejected ticks: {r.text}")
  except HTTPException:
    raise
  except Exception as e:
    raise HTTPException(status_code=502, detail=f"Backend unreachable: {e}")

  asyncio.create_task(_broadcast_json({
    "type": "ticks",
    "count": len(ticks),
    "ts": int(time.time() * 1000)
  }))
  return {"ok": True, "forwarded": True, "backend": r.json()}

@app.post("/orders/recommendation")
def order_recommendation(req: OrderRecRequest, x_api_key: Optional[str] = Header(default=None)):
  require_key(x_api_key)

  # Simple risk-aware position sizing matrix
  # score/confidence in [0..1], leverage constrained by maxLeverage and riskMode
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

  # Position notional
  risk_budget = req.equity * base_risk * (0.5 + abs_edge)   # adaptive
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

  asyncio.create_task(_broadcast_json({
    "type": "order_recommendation",
    "payload": out
  }))

  return {"ok": True, "recommendation": out}

@app.websocket("/ws/signals")
async def ws_signals(ws: WebSocket):
  await ws.accept()
  clients.append(ws)
  try:
    await ws.send_json({"type": "hello", "service": "phoenix-bridge", "ts": int(time.time() * 1000)})
    while True:
      # Keep-alive receive loop (frontend/bot may send pings)
      _ = await ws.receive_text()
  except WebSocketDisconnect:
    pass
  finally:
    if ws in clients:
      clients.remove(ws)

async def _broadcast_json(payload: Dict[str, Any]):
  dead = []
  for c in clients:
    try:
      await c.send_json(payload)
    except Exception:
      dead.append(c)
  for d in dead:
    if d in clients:
      clients.remove(d)
