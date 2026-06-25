#!/usr/bin/env python3
# build5_1.py
#
# Phase 5.1 Generator
# - Docker Compose infra (Redis + PostgreSQL + backend + pgAdmin + python bridge)
# - Python V8.3 bridge (FastAPI + REST + WebSocket)
# - Frontend pro architecture scaffold (AppShell, WidgetRegistry, EventBus, Store, LayoutEngine, indicator worker)
#
# Usage:
#   python3 build5_1.py
#
# Then:
#   docker compose up -d --build
#
# Frontend:
#   Open phoenix-frontend/institutional-cockpit-v5.html in browser

from pathlib import Path
import textwrap

ROOT = Path(".").resolve()

FILES = {
    # =========================================================
    # ROOT INFRA FILES
    # =========================================================
    ".env.example": r"""
    # ---------- Core ----------
    NODE_ENV=development
    BACKEND_PORT=8787
    BRIDGE_PORT=8899

    # ---------- Redis ----------
    REDIS_HOST=redis
    REDIS_PORT=6379
    REDIS_URL=redis://redis:6379

    # ---------- PostgreSQL ----------
    POSTGRES_DB=phoenix
    POSTGRES_USER=phoenix
    POSTGRES_PASSWORD=phoenix
    POSTGRES_HOST=postgres
    POSTGRES_PORT=5432
    DATABASE_URL=postgresql://phoenix:phoenix@postgres:5432/phoenix

    # ---------- pgAdmin ----------
    PGADMIN_DEFAULT_EMAIL=admin@phoenix.local
    PGADMIN_DEFAULT_PASSWORD=admin

    # ---------- Bridge->Backend ----------
    BACKEND_BASE_URL=http://backend:8787
    BACKEND_WS_URL=http://backend:8787
    BRIDGE_API_KEY=dev-bridge-key
    """,

    "docker-compose.yml": r"""
    version: "3.9"

    services:
      redis:
        image: redis:7-alpine
        container_name: phoenix-redis
        command: ["redis-server", "--appendonly", "yes"]
        ports:
          - "6379:6379"
        volumes:
          - redis_data:/data
        restart: unless-stopped
        healthcheck:
          test: ["CMD", "redis-cli", "ping"]
          interval: 10s
          timeout: 3s
          retries: 10
          start_period: 10s

      postgres:
        image: postgres:16-alpine
        container_name: phoenix-postgres
        environment:
          POSTGRES_DB: ${POSTGRES_DB:-phoenix}
          POSTGRES_USER: ${POSTGRES_USER:-phoenix}
          POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-phoenix}
        ports:
          - "5432:5432"
        volumes:
          - postgres_data:/var/lib/postgresql/data
        restart: unless-stopped
        healthcheck:
          test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-phoenix} -d ${POSTGRES_DB:-phoenix}"]
          interval: 10s
          timeout: 3s
          retries: 10
          start_period: 20s

      backend:
        build:
          context: ./phoenix-backend
          dockerfile: Dockerfile
        container_name: phoenix-backend
        env_file:
          - ./.env
        environment:
          NODE_ENV: ${NODE_ENV:-development}
          PORT: ${BACKEND_PORT:-8787}
          REDIS_URL: ${REDIS_URL:-redis://redis:6379}
          DATABASE_URL: ${DATABASE_URL:-postgresql://phoenix:phoenix@postgres:5432/phoenix}
        ports:
          - "${BACKEND_PORT:-8787}:8787"
        depends_on:
          redis:
            condition: service_healthy
          postgres:
            condition: service_healthy
        restart: unless-stopped
        healthcheck:
          test: ["CMD", "wget", "-qO-", "http://localhost:8787/health"]
          interval: 15s
          timeout: 5s
          retries: 8
          start_period: 20s

      bridge:
        build:
          context: ./phoenix-bridge
          dockerfile: Dockerfile
        container_name: phoenix-bridge
        env_file:
          - ./.env
        environment:
          BRIDGE_PORT: ${BRIDGE_PORT:-8899}
          BACKEND_BASE_URL: ${BACKEND_BASE_URL:-http://backend:8787}
          BACKEND_WS_URL: ${BACKEND_WS_URL:-http://backend:8787}
          BRIDGE_API_KEY: ${BRIDGE_API_KEY:-dev-bridge-key}
        ports:
          - "${BRIDGE_PORT:-8899}:8899"
        depends_on:
          backend:
            condition: service_healthy
        restart: unless-stopped
        healthcheck:
          test: ["CMD", "python", "-c", "import urllib.request; print(urllib.request.urlopen('http://localhost:8899/health').status)"]
          interval: 15s
          timeout: 5s
          retries: 8
          start_period: 20s

      pgadmin:
        image: dpage/pgadmin4:8
        container_name: phoenix-pgadmin
        environment:
          PGADMIN_DEFAULT_EMAIL: ${PGADMIN_DEFAULT_EMAIL:-admin@phoenix.local}
          PGADMIN_DEFAULT_PASSWORD: ${PGADMIN_DEFAULT_PASSWORD:-admin}
        ports:
          - "5050:80"
        depends_on:
          postgres:
            condition: service_healthy
        restart: unless-stopped

    volumes:
      redis_data:
      postgres_data:
    """,

    # =========================================================
    # BACKEND DOCKER + PACKAGE + SERVER PATCH (bridge ingest endpoints)
    # =========================================================
    "phoenix-backend/Dockerfile": r"""
    FROM node:20-alpine

    WORKDIR /app
    COPY package.json package-lock.json* ./
    RUN npm install --no-audit --no-fund

    COPY . .
    EXPOSE 8787
    CMD ["npm", "start"]
    """,

    "phoenix-backend/package.json": r"""
    {
      "name": "phoenix-backend",
      "version": "1.0.0",
      "description": "Phoenix backend with hot cache, async storage, bridge ingestion",
      "main": "server.js",
      "type": "module",
      "scripts": {
        "start": "node server.js",
        "dev": "node server.js"
      },
      "dependencies": {
        "cors": "^2.8.5",
        "express": "^4.19.2",
        "pg": "^8.12.0",
        "redis": "^4.7.0",
        "socket.io": "^4.8.1"
      }
    }
    """,

    "phoenix-backend/server.js": r"""
    import express from "express";
    import http from "http";
    import cors from "cors";
    import { Server } from "socket.io";

    // Existing local modules expected from previous phases:
    import ExchangeMultiplexer from "./exchangeMultiplexer.js";
    import InstitutionalOrderBook from "./institutionalOrderBook.js";
    import evaluateDeepConsensus from "./evaluate_deep_consensus.js";
    import { PaperTradeEngine } from "./paperTradeEngine.js";
    import { RollingStats, VPINCalculator, CrossExchangeDelta, ExecutionGuardrails } from "./strategyInfra.js";
    import RedisHotCache from "./cache/redisHotCache.js";
    import AsyncPgWriter from "./storage/asyncPgWriter.js";
    import ReplayEngine from "./backtest/replayEngine.js";

    const PORT = Number(process.env.PORT || 8787);

    const app = express();
    app.use(cors());
    app.use(express.json({ limit: "10mb" }));

    const server = http.createServer(app);
    const io = new Server(server, {
      cors: { origin: "*", methods: ["GET", "POST"] }
    });

    const symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"];
    const multiplexer = new ExchangeMultiplexer({ io, symbols });
    const orderbook = new InstitutionalOrderBook({ io });
    const paper = new PaperTradeEngine({});
    const replay = new ReplayEngine();

    const cache = new RedisHotCache({});
    const writer = new AsyncPgWriter({});
    const guard = new ExecutionGuardrails({ maxMicroLagMs: 500 });
    const xdelta = new CrossExchangeDelta({ leader: "binance", lagger: "bybit" });

    const wallStats = new Map();
    const vpinMap = new Map();
    const fallback = { ticks: new Map(), consensus: new Map() };

    for (const s of symbols) {
      wallStats.set(s, new RollingStats(25000));
      vpinMap.set(s, new VPINCalculator({ bucketVolume: 250, windowBuckets: 60 }));
    }

    function localPushTick(sym, tick) {
      if (!fallback.ticks.has(sym)) fallback.ticks.set(sym, []);
      const arr = fallback.ticks.get(sym);
      arr.push(tick);
      if (arr.length > 15000) arr.shift();
    }

    // ---------- Bridge ingress ----------
    app.post("/api/bridge/signal", async (req, res) => {
      try {
        const payload = req.body || {};
        const symbol = String(payload.symbol || "BTCUSDT").toUpperCase();
        const score = Number(payload.score || 0);
        const direction = score > 0.08 ? "bullish" : score < -0.08 ? "bearish" : "neutral";

        const out = {
          symbol,
          direction,
          score,
          confidence: Number(payload.confidence ?? Math.min(1, Math.abs(score))),
          explain: payload.explain || { source: "python-bridge" },
          diagnostics: payload.diagnostics || {},
          ts: Number(payload.ts || Date.now()),
          source: "bridge"
        };

        fallback.consensus.set(symbol, out);
        io.emit("consensus:update", out);

        await cache.setConsensus(symbol, out, 90).catch(() => {});
        writer.enqueueConsensus(out);

        res.json({ ok: true, accepted: out });
      } catch (e) {
        res.status(500).json({ ok: false, error: e?.message || String(e) });
      }
    });

    app.post("/api/bridge/ticks", async (req, res) => {
      try {
        const body = req.body || {};
        const ticks = Array.isArray(body.ticks) ? body.ticks : [];
        let n = 0;

        for (const t of ticks) {
          const tick = {
            exchange: String(t.exchange || "bridge"),
            symbol: String(t.symbol || "BTCUSDT").toUpperCase(),
            price: Number(t.price || 0),
            volume: Number(t.volume || 0),
            side: String(t.side || "buy").toLowerCase() === "sell" ? "sell" : "buy",
            ts: Number(t.ts || Date.now()),
            recvTs: Date.now(),
            latencyMs: t.latencyMs == null ? null : Number(t.latencyMs),
            raw: t.raw || t
          };
          if (!tick.symbol || !Number.isFinite(tick.price) || tick.price <= 0) continue;

          localPushTick(tick.symbol, tick);
          orderbook.ingestTrade(tick);
          vpinMap.get(tick.symbol)?.ingest({ volume: tick.volume, side: tick.side, ts: tick.ts });
          xdelta.ingest(tick);

          io.emit("market:tick", tick);
          await cache.setLatestTick(tick.symbol, tick).catch(() => {});
          await cache.pushRecentTick(tick.symbol, tick).catch(() => {});
          writer.enqueueTick(tick);
          n++;
        }

        res.json({ ok: true, ingested: n });
      } catch (e) {
        res.status(500).json({ ok: false, error: e?.message || String(e) });
      }
    });

    // ---------- Health ----------
    app.get("/health", async (_req, res) => {
      const redis = await cache.health().catch(() => ({ ok: false }));
      res.json({
        ok: true,
        ts: Date.now(),
        redis,
        services: {
          writer: true,
          multiplexer: true,
          orderbook: true
        }
      });
    });

    // ---------- Replay/backtest ----------
    app.post("/api/replay/backtest", async (req, res) => {
      try {
        const {
          symbol = "BTCUSDT",
          fromTs = Date.now() - 3600_000,
          toTs = Date.now(),
          source = "postgres",
          longThreshold = 0.15,
          shortThreshold = -0.15,
          feeBps = 2
        } = req.body || {};

        const sym = String(symbol).toUpperCase();
        let ticks = [];

        if (source === "redis") {
          ticks = await cache.getRecentTicks(sym, 200000).catch(() => []);
          ticks = ticks.filter((t) => Number(t.ts || 0) >= Number(fromTs) && Number(t.ts || 0) <= Number(toTs));
        } else {
          ticks = await writer.queryTicks({ symbol: sym, fromTs, toTs, limit: 300000 }).catch(() => []);
        }

        const c = (await cache.getConsensus(sym).catch(() => null)) || fallback.consensus.get(sym) || null;
        const consensusSeries = c ? ticks.map((t) => ({ ts: Number(t.ts), score: Number(c.score || 0) })) : [];

        const result = replay.run({
          ticks,
          consensusSeries,
          longThreshold: Number(longThreshold),
          shortThreshold: Number(shortThreshold),
          feeBps: Number(feeBps)
        });

        res.json({ ok: true, symbol: sym, source, fromTs, toTs, ...result });
      } catch (e) {
        res.status(500).json({ ok: false, error: e?.message || String(e) });
      }
    });

    // ---------- Socket ----------
    io.on("connection", (socket) => {
      socket.emit("server:hello", {
        id: socket.id,
        ts: Date.now(),
        channels: ["market:tick", "consensus:update", "paper:state", "paper:orderAck", "paper:fill", "paper:liquidation"]
      });

      socket.on("latency:ping", (_payload, ack) => {
        if (typeof ack === "function") ack({ ts: Date.now() });
      });

      socket.on("paper:bootstrap", () => {
        socket.emit("paper:state", paper.getState("demo"));
      });

      socket.on("paper:deposit", ({ userId = "demo", amount = 0 } = {}) => {
        paper.deposit(userId, Number(amount || 0));
        socket.emit("paper:state", paper.getState(userId));
      });

      socket.on("paper:order", (order = {}) => {
        try {
          const sym = String(order.symbol || "BTCUSDT").toUpperCase();
          const cons = fallback.consensus.get(sym) || null;
          const slipBps = guard.dynamicSlippageBps(
            Number(cons?.diagnostics?.vpin || 0),
            Number(cons?.diagnostics?.crossExchangeDelta || 0)
          );

          const result = paper.submitOrder({ ...order, slippageBps: slipBps, timestamp: Date.now() });
          socket.emit("paper:orderAck", { ...result.ack, slippageBps: slipBps });

          for (const fill of result.fills) {
            socket.emit("paper:fill", fill);
            writer.enqueueFill({ ...fill, userId: order.userId || "demo", side: order.side || "buy" });
          }

          if (result.liquidation) socket.emit("paper:liquidation", result.liquidation);
          socket.emit("paper:state", paper.getState(order.userId || "demo"));
        } catch (err) {
          socket.emit("paper:error", { message: err.message });
        }
      });

      socket.on("consensus:get", async ({ symbol = "BTCUSDT" } = {}) => {
        const sym = String(symbol).toUpperCase();
        const c = (await cache.getConsensus(sym).catch(() => null)) || fallback.consensus.get(sym) || null;
        socket.emit("consensus:update", c);
      });
    });

    // ---------- Synthetic orderbook fallback ----------
    setInterval(() => {
      const now = Date.now();
      for (const sym of symbols) {
        const last = (fallback.ticks.get(sym) || []).slice(-1)[0];
        const mid = Number(last?.price || (sym === "BTCUSDT" ? 65000 : sym === "ETHUSDT" ? 3500 : 150));
        const bids = [], asks = [];
        for (let i = 0; i < 70; i++) {
          bids.push([Number((mid * (1 - (i + 1) * 0.00035)).toFixed(4)), Number((Math.random() * 5 + 0.1).toFixed(4))]);
          asks.push([Number((mid * (1 + (i + 1) * 0.00035)).toFixed(4)), Number((Math.random() * 5 + 0.1).toFixed(4))]);
        }
        orderbook.updateSnapshot(sym, { bids, asks, ts: now });
        wallStats.get(sym)?.push(Math.max(bids[2]?.[1] || 0, asks[2]?.[1] || 0));
      }
    }, 220);

    // ---------- Consensus scheduler ----------
    setInterval(async () => {
      for (const sym of symbols) {
        const micro = orderbook.getMetrics(sym);
        if (!micro) continue;

        const ticks = fallback.ticks.get(sym) || [];
        const technical = ticks.length >= 2
          ? Math.tanh(((ticks[ticks.length - 1].price - ticks[ticks.length - 2].price) / Math.max(1e-9, ticks[ticks.length - 2].price)) * 100)
          : 0;

        const vpin = vpinMap.get(sym)?.value() ?? 0;
        const deltas = xdelta.snapshot(sym);
        const z = wallStats.get(sym)?.zscore(Math.max(Math.abs(micro.bai || 0), Math.abs(micro.obi || 0))) ?? 0;

        let c = evaluateDeepConsensus({
          technical,
          micro: { ...micro, vpin, crossExchangeDelta: deltas.delta },
          riskEvents: orderbook.snapshot().micro.recentEvents.filter((e) => e.symbol === sym),
          macroSignal: { score: 0, regime: "normal" }
        });

        const microLagMs = guard.estimateMicroLagMs(ticks);
        c = guard.apply({ consensus: c, technical, microLagMs, obi: Number(micro.obi || 0), vpin });

        const out = {
          ...c,
          symbol: sym,
          diagnostics: {
            zscoreAdaptive: z,
            vpin,
            crossExchangeDelta: deltas.delta,
            leaderPx: deltas.leaderPx,
            laggerPx: deltas.laggerPx,
            microLagMs
          },
          ts: Date.now()
        };

        fallback.consensus.set(sym, out);
        io.emit("consensus:update", out);

        await cache.setConsensus(sym, out, 60).catch(() => {});
        writer.enqueueConsensus(out);
      }
    }, 250);

    // ---------- Exchange multiplexer tick flow ----------
    multiplexer.on("market:tick", async (tick) => {
      const sym = String(tick.symbol || "").toUpperCase();
      if (!sym) return;

      localPushTick(sym, tick);
      orderbook.ingestTrade(tick);
      vpinMap.get(sym)?.ingest({ volume: tick.volume, side: tick.side, ts: tick.ts });
      xdelta.ingest(tick);

      io.emit("market:tick", tick);

      await cache.setLatestTick(sym, tick).catch(() => {});
      await cache.pushRecentTick(sym, tick).catch(() => {});
      writer.enqueueTick(tick);
    });

    multiplexer.on("exchange:health", (h) => io.emit("exchange:health", h));

    // ---------- Boot ----------
    async function boot() {
      try {
        await cache.connect();
      } catch (e) {
        console.warn("[boot] redis unavailable:", e?.message || e);
      }

      try {
        await writer.init();
        writer.start();
      } catch (e) {
        console.warn("[boot] postgres unavailable:", e?.message || e);
      }

      multiplexer.start();

      server.listen(PORT, () => {
        console.log(`[phoenix-backend] listening on http://localhost:${PORT}`);
      });
    }

    boot().catch((e) => {
      console.error("[boot] fatal", e);
      process.exit(1);
    });

    process.on("SIGINT", async () => {
      try { await cache.disconnect(); } catch {}
      try { await writer.stop(); } catch {}
      process.exit(0);
    });
    """,

    # =========================================================
    # PYTHON BRIDGE SERVICE
    # =========================================================
    "phoenix-bridge/requirements.txt": r"""
    fastapi==0.112.0
    uvicorn==0.30.3
    requests==2.32.3
    websockets==12.0
    pydantic==2.8.2
    python-dotenv==1.0.1
    """,

    "phoenix-bridge/Dockerfile": r"""
    FROM python:3.11-slim

    WORKDIR /app
    COPY requirements.txt .
    RUN pip install --no-cache-dir -r requirements.txt

    COPY . .
    EXPOSE 8899
    CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8899"]
    """,

    "phoenix-bridge/main.py": r"""
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
    """,

    # =========================================================
    # FRONTEND APP ARCHITECTURE (V5)
    # =========================================================
    "phoenix-frontend/institutional-cockpit-v5.html": r"""
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="UTF-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1.0" />
      <title>Phoenix Institutional Cockpit V5</title>
      <link rel="stylesheet" href="./css/institutional-cockpit-v5.css" />
    </head>
    <body>
      <div id="app-root"></div>
      <script type="module" src="./js/app/bootstrap.js"></script>
    </body>
    </html>
    """,

    "phoenix-frontend/css/institutional-cockpit-v5.css": r"""
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
      max-width:1700px;
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
          "replay"
          "inspector";
      }
      .status-grid{
        min-width:0;
        grid-template-columns:repeat(2,minmax(120px,1fr));
      }
    }
    """,

    "phoenix-frontend/js/app/eventBus.js": r"""
    export class EventBus {
      constructor() {
        this.map = new Map();
      }

      on(event, handler) {
        if (!this.map.has(event)) this.map.set(event, new Set());
        this.map.get(event).add(handler);
        return () => this.off(event, handler);
      }

      off(event, handler) {
        if (!this.map.has(event)) return;
        this.map.get(event).delete(handler);
      }

      emit(event, payload) {
        const set = this.map.get(event);
        if (!set) return;
        for (const h of set) {
          try { h(payload); } catch (e) { console.error("[EventBus]", e); }
        }
      }
    }

    export default EventBus;
    """,

    "phoenix-frontend/js/app/store.js": r"""
    export class Store {
      constructor(initial = {}) {
        this.state = initial;
        this.listeners = new Set();
      }

      get() { return this.state; }

      set(patch) {
        this.state = { ...this.state, ...patch };
        for (const l of this.listeners) {
          try { l(this.state); } catch (e) { console.error("[Store]", e); }
        }
      }

      subscribe(cb) {
        this.listeners.add(cb);
        return () => this.listeners.delete(cb);
      }
    }

    export default Store;
    """,

    "phoenix-frontend/js/app/layoutEngine.js": r"""
    /**
     * LayoutEngine
     * - Saves/restores widget ordering + selected symbol
     * - localStorage + IndexedDB fallback (simple localStorage implementation here)
     */
    export class LayoutEngine {
      constructor(key = "phoenix_v5_layout") {
        this.key = key;
      }

      save(layout) {
        try {
          localStorage.setItem(this.key, JSON.stringify(layout));
        } catch (e) {
          console.warn("[LayoutEngine] save failed", e);
        }
      }

      load(defaultLayout) {
        try {
          const raw = localStorage.getItem(this.key);
          if (!raw) return defaultLayout;
          const parsed = JSON.parse(raw);
          return { ...defaultLayout, ...parsed };
        } catch {
          return defaultLayout;
        }
      }
    }

    export default LayoutEngine;
    """,

    "phoenix-frontend/js/app/widgetRegistry.js": r"""
    export class WidgetRegistry {
      constructor() {
        this.factories = new Map();
        this.instances = new Map();
      }

      register(name, factory) {
        this.factories.set(name, factory);
      }

      mount(name, target, ctx) {
        const fn = this.factories.get(name);
        if (!fn) throw new Error(`Widget not found: ${name}`);
        const instance = fn(target, ctx);
        this.instances.set(name, instance);
        return instance;
      }

      unmount(name) {
        const inst = this.instances.get(name);
        if (inst?.destroy) {
          try { inst.destroy(); } catch {}
        }
        this.instances.delete(name);
      }

      unmountAll() {
        for (const name of Array.from(this.instances.keys())) {
          this.unmount(name);
        }
      }
    }

    export default WidgetRegistry;
    """,

    "phoenix-frontend/js/workers/indicatorWorker.js": r"""
    // indicatorWorker.js
    // Workerized heavy computation for multi-symbol indicators

    const state = {
      symbols: new Map(), // symbol -> arrays
    };

    function ensure(symbol) {
      if (!state.symbols.has(symbol)) {
        state.symbols.set(symbol, {
          prices: [],
          volumes: [],
          returns: []
        });
      }
      return state.symbols.get(symbol);
    }

    function sma(arr, n) {
      if (arr.length < n) return null;
      let s = 0;
      for (let i = arr.length - n; i < arr.length; i++) s += arr[i];
      return s / n;
    }

    function std(arr, n) {
      if (arr.length < n) return null;
      const m = sma(arr, n);
      let v = 0;
      for (let i = arr.length - n; i < arr.length; i++) v += (arr[i] - m) ** 2;
      return Math.sqrt(v / n);
    }

    function ema(prev, x, alpha) {
      if (prev == null) return x;
      return alpha * x + (1 - alpha) * prev;
    }

    self.onmessage = (event) => {
      const msg = event.data || {};
      if (msg.type !== "ticks") return;

      const ticks = Array.isArray(msg.payload) ? msg.payload : [];
      const out = [];

      for (const t of ticks) {
        const symbol = String(t.symbol || "").toUpperCase();
        const price = Number(t.price || 0);
        const vol = Number(t.volume || 0);
        if (!symbol || !Number.isFinite(price) || price <= 0) continue;

        const s = ensure(symbol);
        const prev = s.prices.length ? s.prices[s.prices.length - 1] : price;
        const ret = (price - prev) / Math.max(1e-9, prev);

        s.prices.push(price);
        s.volumes.push(vol);
        s.returns.push(ret);

        if (s.prices.length > 10000) s.prices.shift();
        if (s.volumes.length > 10000) s.volumes.shift();
        if (s.returns.length > 10000) s.returns.shift();

        // sample metrics
        const sma20 = sma(s.prices, 20);
        const sma50 = sma(s.prices, 50);
        const vol20 = std(s.returns, 20);
        const bbStd = std(s.prices, 20);
        const mid = sma20;
        const bbUp = mid != null && bbStd != null ? mid + 2 * bbStd : null;
        const bbDn = mid != null && bbStd != null ? mid - 2 * bbStd : null;

        out.push({
          symbol,
          ts: Number(t.ts || Date.now()),
          indicators: {
            sma20, sma50, vol20, bbUp, bbDn
          }
        });
      }

      if (out.length) {
        self.postMessage({ type: "indicators:update", payload: out });
      }
    };
    """,

    "phoenix-frontend/js/app/appShell.js": r"""
    import { io } from "https://cdn.socket.io/4.7.5/socket.io.esm.min.js";
    import EventBus from "./eventBus.js";
    import Store from "./store.js";
    import LayoutEngine from "./layoutEngine.js";
    import WidgetRegistry from "./widgetRegistry.js";

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
        this.worker = null;
        this.priceSeries = [];
      }

      init() {
        this._renderSkeleton();
        this._wireStatusBindings();
        this._initSocket();
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
              <h1>Phoenix Institutional Cockpit V5.1</h1>
              <small>Dockerized infra + bridge + modular frontend architecture</small>
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

          // feed worker
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

          const unsub1 = ctx.shell.bus.on("tick", draw);
          const unsub2 = ctx.shell.bus.on("indicators", draw);
          draw();

          return { destroy() { unsub1(); unsub2(); } };
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
          const u1 = ctx.shell.store.subscribe(upd);
          upd();
          return { destroy() { u1(); } };
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
      }

      _mountWidgets() {
        const dock = this.rootEl.querySelector("#dock");
        this.registry.mount("graph", dock, { shell: this });
        this.registry.mount("feed", dock, { shell: this });
        this.registry.mount("risk", dock, { shell: this });
        this.registry.mount("inspector", dock, { shell: this });
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
              headers: {
                "Content-Type": "application/json",
                "x-api-key": "dev-bridge-key"
              },
              body: JSON.stringify({
                symbol,
                score,
                confidence: Math.min(1, Math.abs(score)),
                explain: { source: "manual-cli" },
                diagnostics: { manual: true }
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

          // RTT
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
    """,

    "phoenix-frontend/js/app/bootstrap.js": r"""
    import AppShell from "./appShell.js";

    const root = document.getElementById("app-root");
    const shell = new AppShell(root);
    shell.init();

    // expose for debugging
    window.__phoenixShell = shell;
    """,
}

def write_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = textwrap.dedent(content).lstrip("\n")
    path.write_text(cleaned, encoding="utf-8")

def main():
    print("⚡ Generating Phase 5.1 (infra + bridge + frontend architecture)...")
    for rel, content in FILES.items():
        out = ROOT / rel
        write_file(out, content)
        print(f"✅ {rel}")

    print("\nDone.")
    print("Next steps:")
    print("1) Copy .env.example to .env")
    print("   cp .env.example .env")
    print("2) Build and run stack")
    print("   docker compose up -d --build")
    print("3) Open frontend")
    print("   phoenix-frontend/institutional-cockpit-v5.html")
    print("4) Bridge endpoints")
    print("   GET  http://localhost:8899/health")
    print("   POST http://localhost:8899/signal  (x-api-key required)")

if __name__ == "__main__":
    main()
