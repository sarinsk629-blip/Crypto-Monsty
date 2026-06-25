import os
import time
import json
import asyncio
from typing import List, Dict, Any

import grpc
from grpc import aio

# Generated via grpc_tools.protoc from phoenix-proto/engine.proto:
#   python -m grpc_tools.protoc -I../phoenix-proto --python_out=. --grpc_python_out=. ../phoenix-proto/engine.proto
import engine_pb2
import engine_pb2_grpc

import requests

from execution_router import ExecutionRouter
from ml_anomaly_detector import MLAnomalyDetector


BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://localhost:8787")
GRPC_BIND = os.getenv("GRPC_BIND", "0.0.0.0:50051")


def side_enum_to_str(side_val: int) -> str:
  if side_val == engine_pb2.SIDE_SELL:
    return "sell"
  return "buy"


def algo_enum_to_str(algo_val: int) -> str:
  if algo_val == engine_pb2.ALGO_TWAP:
    return "twap"
  if algo_val == engine_pb2.ALGO_VWAP:
    return "vwap"
  if algo_val == engine_pb2.ALGO_ICEBERG:
    return "iceberg"
  return "twap"


def side_str_to_enum(side: str) -> int:
  return engine_pb2.SIDE_SELL if str(side).lower() == "sell" else engine_pb2.SIDE_BUY


def severity_to_enum(sev: str) -> int:
  s = str(sev or "low").lower()
  if s == "critical":
    return engine_pb2.SEVERITY_CRITICAL
  if s == "high":
    return engine_pb2.SEVERITY_HIGH
  if s == "medium":
    return engine_pb2.SEVERITY_MEDIUM
  return engine_pb2.SEVERITY_LOW


class EngineBridgeService(engine_pb2_grpc.EngineBridgeServicer):
  def __init__(self):
    self.router = ExecutionRouter()
    self.anomaly = MLAnomalyDetector(window=1000)
    self.signal_subscribers: List[asyncio.Queue] = []

  async def IngestTicks(self, request: engine_pb2.TickBatch, context):
    ticks = []
    for t in request.ticks:
      ticks.append({
        "exchange": t.exchange,
        "symbol": t.symbol,
        "price": t.price,
        "volume": t.volume,
        "side": side_enum_to_str(t.side),
        "ts": t.event_ts or int(time.time() * 1000),
        "latencyMs": t.latency_ms if t.latency_ms else None,
        "raw": dict(t.tags)
      })

    # anomaly detect
    anomalies = self.anomaly.ingest_ticks(ticks)

    # forward to backend REST ingress
    try:
      r = requests.post(f"{BACKEND_BASE_URL}/api/bridge/ticks", json={"ticks": ticks}, timeout=4.5)
      if not r.ok:
        return engine_pb2.Ack(ok=False, message=f"backend_reject:{r.status_code}", ts=int(time.time() * 1000))
    except Exception as e:
      return engine_pb2.Ack(ok=False, message=f"backend_unreachable:{e}", ts=int(time.time() * 1000))

    # publish anomalies as signals to stream subscribers
    for a in anomalies:
      sig = engine_pb2.Signal(
        symbol=a["symbol"],
        score=float(a["anomalyScore"]),
        confidence=min(1.0, max(0.0, float(a["anomalyScore"]))),
        direction="risk_on" if float(a["anomalyScore"]) < 0.5 else "risk_off",
        ts=int(a["ts"]),
        diagnostics={k: str(v) for k, v in a.get("z", {}).items()},
        labels=list(a.get("labels", [])),
        severity=severity_to_enum(a.get("severity", "low")),
      )
      await self._broadcast_signal(sig)

    return engine_pb2.Ack(ok=True, message=f"ingested:{len(ticks)}", ts=int(time.time() * 1000))

  async def PublishSignals(self, request: engine_pb2.SignalBatch, context):
    accepted = 0
    for s in request.signals:
      payload = {
        "symbol": s.symbol,
        "score": s.score,
        "confidence": s.confidence,
        "direction": s.direction,
        "ts": s.ts or int(time.time() * 1000),
        "diagnostics": dict(s.diagnostics),
        "labels": list(s.labels)
      }
      try:
        r = requests.post(f"{BACKEND_BASE_URL}/api/bridge/signal", json=payload, timeout=3.0)
        if r.ok:
          accepted += 1
      except Exception:
        pass

      # also stream out
      await self._broadcast_signal(s)

    return engine_pb2.Ack(ok=True, message=f"accepted:{accepted}", ts=int(time.time() * 1000))

  async def ExecuteOrder(self, request: engine_pb2.ExecutionOrder, context):
    req = {
      "symbol": request.symbol,
      "side": side_enum_to_str(request.side),
      "qty": request.qty,
      "algo": algo_enum_to_str(request.algo),
      "duration_sec": request.duration_sec,
      "slices": request.slices,
      "display_qty": request.display_qty if request.display_qty > 0 else None,
      "min_clip": request.min_clip if request.min_clip > 0 else None,
      "max_clip": request.max_clip if request.max_clip > 0 else None,
      "exchange_weights": dict(request.exchange_weights),
      "mark_price": request.mark_price if request.mark_price > 0 else None,
      "slippage_bps": request.slippage_bps if request.slippage_bps > 0 else 3.0
    }

    plan = self.router.create_schedule(req)
    result = self.router.simulate_execute(plan, mark_price=req["mark_price"], slippage_bps=req["slippage_bps"])

    fills = []
    for f in result["fills"]:
      fills.append(engine_pb2.ExecutionSlice(
        parent_order_id=f["parent_order_id"],
        slice_id=f["slice_id"],
        exchange=f["exchange"],
        symbol=f["symbol"],
        side=side_str_to_enum(f["side"]),
        qty=float(f["qty"]),
        exec_price=float(f["metadata"]["exec_price"]),
        status=f["status"],
        ts=int(f["ts"]),
        metadata={k: str(v) for k, v in f.get("metadata", {}).items()}
      ))

    return engine_pb2.ExecutionResult(
      order_id=result["order_id"],
      symbol=result["symbol"],
      side=side_str_to_enum(result["side"]),
      algo=request.algo,
      requested_qty=float(result["requested_qty"]),
      filled_qty=float(result["filled_qty"]),
      avg_exec_price=float(result["avg_exec_price"] or 0.0),
      status=result["status"],
      fills=fills,
      ts=int(time.time() * 1000)
    )

  async def StreamSignals(self, request: engine_pb2.HealthRequest, context):
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    self.signal_subscribers.append(q)
    try:
      while True:
        msg = await q.get()
        yield msg
    except asyncio.CancelledError:
      raise
    finally:
      if q in self.signal_subscribers:
        self.signal_subscribers.remove(q)

  async def Health(self, request: engine_pb2.HealthRequest, context):
    details = {
      "backend": BACKEND_BASE_URL,
      "grpc_bind": GRPC_BIND,
      "subscribers": str(len(self.signal_subscribers))
    }
    return engine_pb2.HealthReply(
      ok=True,
      service="phoenix-bridge-grpc",
      ts=int(time.time() * 1000),
      details=details
    )

  async def _broadcast_signal(self, sig: engine_pb2.Signal):
    stale = []
    for q in self.signal_subscribers:
      try:
        if q.full():
          _ = q.get_nowait()
        q.put_nowait(sig)
      except Exception:
        stale.append(q)
    for q in stale:
      if q in self.signal_subscribers:
        self.signal_subscribers.remove(q)


async def serve():
  server = aio.server(options=[
    ("grpc.max_send_message_length", 20 * 1024 * 1024),
    ("grpc.max_receive_message_length", 20 * 1024 * 1024),
    ("grpc.keepalive_time_ms", 20000),
    ("grpc.keepalive_timeout_ms", 5000),
  ])

  engine_pb2_grpc.add_EngineBridgeServicer_to_server(EngineBridgeService(), server)
  server.add_insecure_port(GRPC_BIND)

  await server.start()
  print(f"[grpc] EngineBridge listening on {GRPC_BIND}")
  await server.wait_for_termination()


if __name__ == "__main__":
  asyncio.run(serve())
