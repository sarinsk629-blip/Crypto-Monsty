#!/usr/bin/env python3
# build4_2.py
#
# Phase 4.2 generator:
# 1) Redis hot cache adapter + async PostgreSQL writer queue
# 2) Replay/backtest endpoint
# 3) Structured HTML/CSS institutional cockpit layout
#
# Usage:
#   python3 build4_2.py
#
# Then:
#   cd phoenix-backend && npm install && npm start

from pathlib import Path
import textwrap

ROOT = Path(".").resolve()

FILES = {
    # =========================================================
    # BACKEND PACKAGE UPDATE (adds redis + pg deps)
    # =========================================================
    "phoenix-backend/package.json": r"""
    {
      "name": "phoenix-backend",
      "version": "1.0.0",
      "description": "Phoenix Terminal backend - multiplexer, microstructure, replay/backtest, hot cache",
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

    # =========================================================
    # REDIS HOT CACHE ADAPTER
    # =========================================================
    "phoenix-backend/cache/redisHotCache.js": r"""
    import { createClient } from "redis";

    /**
     * RedisHotCache
     * Hot path store for:
     * - latest tick per symbol
     * - recent ticks ring (list)
     * - latest consensus per symbol
     * - optional TTL keys
     */
    export class RedisHotCache {
      constructor({
        url = process.env.REDIS_URL || "redis://127.0.0.1:6379",
        prefix = "phoenix",
        maxRecentTicks = 5000
      } = {}) {
        this.url = url;
        this.prefix = prefix;
        this.maxRecentTicks = maxRecentTicks;
        this.client = createClient({ url: this.url });
        this.connected = false;

        this.client.on("error", (err) => {
          console.error("[RedisHotCache] error:", err?.message || err);
        });
      }

      async connect() {
        if (this.connected) return;
        await this.client.connect();
        this.connected = true;
        console.log("[RedisHotCache] connected:", this.url);
      }

      async disconnect() {
        if (!this.connected) return;
        await this.client.quit();
        this.connected = false;
      }

      _k(...parts) {
        return `${this.prefix}:${parts.join(":")}`;
      }

      async setLatestTick(symbol, tick) {
        const sym = String(symbol).toUpperCase();
        const key = this._k("tick", "latest", sym);
        await this.client.set(key, JSON.stringify(tick));
      }

      async getLatestTick(symbol) {
        const sym = String(symbol).toUpperCase();
        const key = this._k("tick", "latest", sym);
        const v = await this.client.get(key);
        return v ? JSON.parse(v) : null;
      }

      async pushRecentTick(symbol, tick) {
        const sym = String(symbol).toUpperCase();
        const key = this._k("tick", "recent", sym);
        const pipe = this.client.multi();
        pipe.rPush(key, JSON.stringify(tick));
        pipe.lTrim(key, -this.maxRecentTicks, -1);
        await pipe.exec();
      }

      async getRecentTicks(symbol, limit = 1000) {
        const sym = String(symbol).toUpperCase();
        const key = this._k("tick", "recent", sym);
        const n = Math.max(1, Math.min(this.maxRecentTicks, Number(limit || 1000)));
        const arr = await this.client.lRange(key, -n, -1);
        return arr.map((x) => JSON.parse(x));
      }

      async setConsensus(symbol, consensus, ttlSec = 30) {
        const sym = String(symbol).toUpperCase();
        const key = this._k("consensus", "latest", sym);
        await this.client.set(key, JSON.stringify(consensus), { EX: ttlSec });
      }

      async getConsensus(symbol) {
        const sym = String(symbol).toUpperCase();
        const key = this._k("consensus", "latest", sym);
        const v = await this.client.get(key);
        return v ? JSON.parse(v) : null;
      }

      async setJSON(keyParts, value, ttlSec = null) {
        const key = this._k(...keyParts);
        if (ttlSec) await this.client.set(key, JSON.stringify(value), { EX: ttlSec });
        else await this.client.set(key, JSON.stringify(value));
      }

      async getJSON(keyParts) {
        const key = this._k(...keyParts);
        const v = await this.client.get(key);
        return v ? JSON.parse(v) : null;
      }

      async health() {
        try {
          const pong = await this.client.ping();
          return { ok: pong === "PONG", connected: this.connected, url: this.url };
        } catch (e) {
          return { ok: false, connected: this.connected, error: e?.message || String(e) };
        }
      }
    }

    export default RedisHotCache;
    """,

    # =========================================================
    # ASYNC POSTGRES WRITER QUEUE
    # =========================================================
    "phoenix-backend/storage/asyncPgWriter.js": r"""
    import pg from "pg";
    const { Pool } = pg;

    /**
     * AsyncPgWriter
     * - non-blocking ingestion queue for ticks, consensus, orders/fills
     * - batch flush interval + max batch size
     * - initializes schema automatically
     */
    export class AsyncPgWriter {
      constructor({
        connectionString = process.env.DATABASE_URL || "postgresql://postgres:postgres@127.0.0.1:5432/phoenix",
        flushIntervalMs = 500,
        maxBatchSize = 1000
      } = {}) {
        this.pool = new Pool({ connectionString });
        this.flushIntervalMs = flushIntervalMs;
        this.maxBatchSize = maxBatchSize;

        this.queueTicks = [];
        this.queueConsensus = [];
        this.queueFills = [];
        this._timer = null;
        this._flushing = false;
        this.started = false;
      }

      async init() {
        const sql = `
        CREATE TABLE IF NOT EXISTS ticks (
          id BIGSERIAL PRIMARY KEY,
          ts BIGINT NOT NULL,
          recv_ts BIGINT,
          exchange TEXT NOT NULL,
          symbol TEXT NOT NULL,
          side TEXT NOT NULL,
          price DOUBLE PRECISION NOT NULL,
          volume DOUBLE PRECISION NOT NULL,
          latency_ms DOUBLE PRECISION,
          raw JSONB
        );

        CREATE INDEX IF NOT EXISTS idx_ticks_symbol_ts ON ticks(symbol, ts DESC);
        CREATE INDEX IF NOT EXISTS idx_ticks_exchange_ts ON ticks(exchange, ts DESC);

        CREATE TABLE IF NOT EXISTS consensus (
          id BIGSERIAL PRIMARY KEY,
          ts BIGINT NOT NULL,
          symbol TEXT NOT NULL,
          direction TEXT,
          score DOUBLE PRECISION,
          confidence DOUBLE PRECISION,
          diagnostics JSONB,
          explain JSONB
        );

        CREATE INDEX IF NOT EXISTS idx_consensus_symbol_ts ON consensus(symbol, ts DESC);

        CREATE TABLE IF NOT EXISTS fills (
          id BIGSERIAL PRIMARY KEY,
          ts BIGINT NOT NULL,
          user_id TEXT,
          symbol TEXT,
          side TEXT,
          qty DOUBLE PRECISION,
          price DOUBLE PRECISION,
          payload JSONB
        );

        CREATE INDEX IF NOT EXISTS idx_fills_symbol_ts ON fills(symbol, ts DESC);
        `;
        await this.pool.query(sql);
      }

      start() {
        if (this.started) return;
        this.started = true;
        this._timer = setInterval(() => this.flush().catch((e) => console.error("[AsyncPgWriter] flush error:", e)), this.flushIntervalMs);
      }

      async stop() {
        if (this._timer) clearInterval(this._timer);
        this._timer = null;
        await this.flush();
        await this.pool.end();
        this.started = false;
      }

      enqueueTick(tick) {
        this.queueTicks.push(tick);
        if (this.queueTicks.length >= this.maxBatchSize) this.flush().catch(() => {});
      }

      enqueueConsensus(c) {
        this.queueConsensus.push(c);
        if (this.queueConsensus.length >= this.maxBatchSize) this.flush().catch(() => {});
      }

      enqueueFill(fill) {
        this.queueFills.push(fill);
        if (this.queueFills.length >= this.maxBatchSize) this.flush().catch(() => {});
      }

      async flush() {
        if (this._flushing) return;
        this._flushing = true;

        const ticks = this.queueTicks.splice(0, this.maxBatchSize);
        const consensus = this.queueConsensus.splice(0, this.maxBatchSize);
        const fills = this.queueFills.splice(0, this.maxBatchSize);

        const client = await this.pool.connect();
        try {
          await client.query("BEGIN");

          if (ticks.length) {
            const values = [];
            const params = [];
            let i = 1;
            for (const t of ticks) {
              values.push(`($${i++},$${i++},$${i++},$${i++},$${i++},$${i++},$${i++},$${i++},$${i++})`);
              params.push(
                Number(t.ts || Date.now()),
                Number(t.recvTs || null),
                String(t.exchange || "unknown"),
                String(t.symbol || ""),
                String(t.side || "buy"),
                Number(t.price || 0),
                Number(t.volume || 0),
                t.latencyMs == null ? null : Number(t.latencyMs),
                JSON.stringify(t.raw || {})
              );
            }
            await client.query(
              `INSERT INTO ticks (ts, recv_ts, exchange, symbol, side, price, volume, latency_ms, raw) VALUES ${values.join(",")}`,
              params
            );
          }

          if (consensus.length) {
            const values = [];
            const params = [];
            let i = 1;
            for (const c of consensus) {
              values.push(`($${i++},$${i++},$${i++},$${i++},$${i++},$${i++},$${i++})`);
              params.push(
                Number(c.ts || Date.now()),
                String(c.symbol || ""),
                String(c.direction || "neutral"),
                Number(c.score || 0),
                Number(c.confidence || 0),
                JSON.stringify(c.diagnostics || {}),
                JSON.stringify(c.explain || {})
              );
            }
            await client.query(
              `INSERT INTO consensus (ts, symbol, direction, score, confidence, diagnostics, explain) VALUES ${values.join(",")}`,
              params
            );
          }

          if (fills.length) {
            const values = [];
            const params = [];
            let i = 1;
            for (const f of fills) {
              values.push(`($${i++},$${i++},$${i++},$${i++},$${i++},$${i++},$${i++})`);
              params.push(
                Number(f.ts || Date.now()),
                String(f.userId || "demo"),
                String(f.symbol || ""),
                String(f.side || ""),
                Number(f.qty || 0),
                Number(f.price || 0),
                JSON.stringify(f)
              );
            }
            await client.query(
              `INSERT INTO fills (ts, user_id, symbol, side, qty, price, payload) VALUES ${values.join(",")}`,
              params
            );
          }

          await client.query("COMMIT");
        } catch (e) {
          await client.query("ROLLBACK");
          // put back on front if desired (best effort simple requeue here)
          this.queueTicks.unshift(...ticks);
          this.queueConsensus.unshift(...consensus);
          this.queueFills.unshift(...fills);
          throw e;
        } finally {
          client.release();
          this._flushing = false;
        }
      }

      async queryTicks({ symbol, fromTs, toTs, limit = 5000 }) {
        const sql = `
          SELECT ts, recv_ts AS "recvTs", exchange, symbol, side, price, volume, latency_ms AS "latencyMs", raw
          FROM ticks
          WHERE symbol = $1
            AND ts >= $2
            AND ts <= $3
          ORDER BY ts ASC
          LIMIT $4
        `;
        const res = await this.pool.query(sql, [
          String(symbol).toUpperCase(),
          Number(fromTs || 0),
          Number(toTs || Date.now()),
          Math.max(1, Math.min(500000, Number(limit || 5000)))
        ]);
        return res.rows;
      }
    }

    export default AsyncPgWriter;
    """,

    # =========================================================
    # SIMPLE REPLAY/BACKTEST ENGINE
    # =========================================================
    "phoenix-backend/backtest/replayEngine.js": r"""
    /**
     * ReplayEngine
     * Replays historical ticks with optional speed-up and computes simple strategy stats.
     * Strategy (example):
     * - if score > longThreshold => long
     * - if score < shortThreshold => short
     * - pnl from direction * returns
     */
    export class ReplayEngine {
      constructor() {}

      run({
        ticks = [],
        consensusSeries = [],
        longThreshold = 0.15,
        shortThreshold = -0.15,
        feeBps = 2
      } = {}) {
        if (!Array.isArray(ticks) || ticks.length < 2) {
          return {
            ok: true,
            trades: [],
            equityCurve: [],
            stats: { pnl: 0, maxDrawdown: 0, winRate: 0, tradeCount: 0 }
          };
        }

        // map consensus by nearest ts bucket
        const cByTs = new Map();
        for (const c of (consensusSeries || [])) {
          cByTs.set(Number(c.ts || 0), Number(c.score || 0));
        }

        let position = 0; // -1, 0, +1
        let entry = 0;
        let pnl = 0;
        let peak = 0;
        let maxDrawdown = 0;
        let wins = 0;
        let losses = 0;
        const trades = [];
        const equityCurve = [];

        function nearestScore(ts) {
          // cheap nearest by exact first, else 0
          return cByTs.get(ts) ?? 0;
        }

        for (let i = 1; i < ticks.length; i++) {
          const prev = ticks[i - 1];
          const cur = ticks[i];

          const pxPrev = Number(prev.price);
          const pxCur = Number(cur.price);
          if (!Number.isFinite(pxPrev) || !Number.isFinite(pxCur) || pxPrev <= 0) continue;

          const ret = (pxCur - pxPrev) / pxPrev;
          const score = nearestScore(Number(cur.ts || 0));

          // signal
          let target = 0;
          if (score > longThreshold) target = 1;
          else if (score < shortThreshold) target = -1;

          // rebalance if needed
          if (target !== position) {
            if (position !== 0) {
              const gross = (pxCur - entry) / entry * position;
              const fee = feeBps / 10000;
              const net = gross - fee;
              pnl += net;
              if (net >= 0) wins++;
              else losses++;
              trades.push({
                ts: Number(cur.ts || Date.now()),
                action: "close",
                side: position > 0 ? "long" : "short",
                entry,
                exit: pxCur,
                net
              });
            }

            if (target !== 0) {
              entry = pxCur;
              trades.push({
                ts: Number(cur.ts || Date.now()),
                action: "open",
                side: target > 0 ? "long" : "short",
                entry
              });
            }
            position = target;
          } else if (position !== 0) {
            // mark-to-market
            pnl += ret * position;
          }

          peak = Math.max(peak, pnl);
          maxDrawdown = Math.max(maxDrawdown, peak - pnl);
          equityCurve.push({ ts: Number(cur.ts || Date.now()), pnl });
        }

        const closedTrades = wins + losses;
        const winRate = closedTrades > 0 ? wins / closedTrades : 0;

        return {
          ok: true,
          trades,
          equityCurve,
          stats: {
            pnl,
            maxDrawdown,
            winRate,
            tradeCount: closedTrades
          }
        };
      }
    }

    export default ReplayEngine;
    """,

    # =========================================================
    # SERVER WITH REPLAY/BACKTEST ENDPOINT + CACHE + WRITER
    # =========================================================
    "phoenix-backend/server.js": r"""
    import express from "express";
    import http from "http";
    import cors from "cors";
    import { Server } from "socket.io";

    import ExchangeMultiplexer from "./exchangeMultiplexer.js";
    import InstitutionalOrderBook from "./institutionalOrderBook.js";
    import evaluateDeepConsensus from "./evaluate_deep_consensus.js";
    import { PaperTradeEngine } from "./paperTradeEngine.js";
    import { RollingStats, VPINCalculator, CrossExchangeDelta, ExecutionGuardrails } from "./strategyInfra.js";
    import RedisHotCache from "./cache/redisHotCache.js";
    import AsyncPgWriter from "./storage/asyncPgWriter.js";
    import ReplayEngine from "./backtest/replayEngine.js";

    const PORT = process.env.PORT || 8787;
    const app = express();
    app.use(cors());
    app.use(express.json({ limit: "5mb" }));

    const server = http.createServer(app);
    const io = new Server(server, {
      cors: { origin: "*", methods: ["GET", "POST"] }
    });

    const symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"];

    // Core engines
    const multiplexer = new ExchangeMultiplexer({ io, symbols });
    const ob = new InstitutionalOrderBook({ io });
    const paper = new PaperTradeEngine({});
    const replayEngine = new ReplayEngine();

    // Infra
    const hotCache = new RedisHotCache({});
    const pgWriter = new AsyncPgWriter({});
    const guardrails = new ExecutionGuardrails({ maxMicroLagMs: 500 });
    const xdelta = new CrossExchangeDelta({ leader: "binance", lagger: "bybit" });

    const wallStats = new Map();
    const vpinMap = new Map();
    for (const s of symbols) {
      wallStats.set(s, new RollingStats(20000));
      vpinMap.set(s, new VPINCalculator({ bucketVolume: 250, windowBuckets: 60 }));
    }

    const localFallback = {
      ticks: new Map(),
      consensus: new Map()
    };

    function localPushTick(sym, tick) {
      if (!localFallback.ticks.has(sym)) localFallback.ticks.set(sym, []);
      const arr = localFallback.ticks.get(sym);
      arr.push(tick);
      if (arr.length > 5000) arr.shift();
    }

    function localSetConsensus(sym, c) {
      localFallback.consensus.set(sym, c);
    }

    app.get("/health", async (_req, res) => {
      const redis = await hotCache.health().catch(() => ({ ok: false }));
      res.json({
        ok: true,
        ts: Date.now(),
        redis,
        services: {
          multiplexer: true,
          orderbook: true,
          writer: true
        }
      });
    });

    /**
     * Replay/backtest endpoint
     * POST /api/replay/backtest
     * body:
     * {
     *   symbol: "BTCUSDT",
     *   fromTs: 1710000000000,
     *   toTs:   1710003600000,
     *   source: "postgres" | "redis",
     *   longThreshold: 0.2,
     *   shortThreshold: -0.2,
     *   feeBps: 2
     * }
     */
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

        let ticks = [];
        const sym = String(symbol).toUpperCase();

        if (source === "redis") {
          const rec = await hotCache.getRecentTicks(sym, 100000);
          ticks = rec.filter(t => Number(t.ts || 0) >= Number(fromTs) && Number(t.ts || 0) <= Number(toTs));
        } else {
          ticks = await pgWriter.queryTicks({ symbol: sym, fromTs, toTs, limit: 200000 });
        }

        // consensus reconstruction from cached latest only (for demo) -> zero if absent
        // For richer backtests, persist full consensus timeseries and query from PG.
        const lastC = (await hotCache.getConsensus(sym).catch(() => null)) || localFallback.consensus.get(sym) || null;
        const consensusSeries = lastC ? ticks.map(t => ({ ts: Number(t.ts), score: Number(lastC.score || 0) })) : [];

        const result = replayEngine.run({
          ticks,
          consensusSeries,
          longThreshold: Number(longThreshold),
          shortThreshold: Number(shortThreshold),
          feeBps: Number(feeBps)
        });

        res.json({ ok: true, symbol: sym, fromTs: Number(fromTs), toTs: Number(toTs), source, ...result });
      } catch (e) {
        res.status(500).json({ ok: false, error: e?.message || String(e) });
      }
    });

    io.on("connection", (socket) => {
      socket.emit("server:hello", {
        id: socket.id,
        ts: Date.now(),
        channels: [
          "market:tick",
          "consensus:update",
          "paper:state",
          "paper:orderAck",
          "paper:fill",
          "paper:liquidation",
          "storage:status"
        ]
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
          const cons = localFallback.consensus.get(sym) || null;
          const slipBps = guardrails.dynamicSlippageBps(
            Number(cons?.diagnostics?.vpin || 0),
            Number(cons?.diagnostics?.crossExchangeDelta || 0)
          );

          const result = paper.submitOrder({ ...order, slippageBps: slipBps, timestamp: Date.now() });
          socket.emit("paper:orderAck", { ...result.ack, slippageBps: slipBps });
          for (const fill of result.fills) {
            socket.emit("paper:fill", fill);
            pgWriter.enqueueFill({ ...fill, userId: order.userId || "demo", side: order.side || "buy" });
          }
          if (result.liquidation) socket.emit("paper:liquidation", result.liquidation);
          socket.emit("paper:state", paper.getState(order.userId || "demo"));
        } catch (err) {
          socket.emit("paper:error", { message: err.message });
        }
      });

      socket.on("consensus:get", async ({ symbol = "BTCUSDT" } = {}) => {
        const sym = String(symbol).toUpperCase();
        const cached = (await hotCache.getConsensus(sym).catch(() => null)) || localFallback.consensus.get(sym) || null;
        socket.emit("consensus:update", cached);
      });
    });

    // Optional synthetic orderbook if no real L2 integrated
    setInterval(() => {
      const now = Date.now();
      for (const sym of symbols) {
        const last = (localFallback.ticks.get(sym) || []).slice(-1)[0];
        const mid = Number(last?.price || (sym === "BTCUSDT" ? 65000 : sym === "ETHUSDT" ? 3500 : 150));
        const bids = [], asks = [];
        for (let i = 0; i < 60; i++) {
          bids.push([Number((mid * (1 - (i + 1) * 0.00035)).toFixed(4)), Number((Math.random() * 5 + 0.2).toFixed(4))]);
          asks.push([Number((mid * (1 + (i + 1) * 0.00035)).toFixed(4)), Number((Math.random() * 5 + 0.2).toFixed(4))]);
        }
        ob.updateSnapshot(sym, { bids, asks, ts: now });
        wallStats.get(sym)?.push(Math.max(bids[2]?.[1] || 0, asks[2]?.[1] || 0));
      }
    }, 200);

    // Consensus scheduler
    setInterval(async () => {
      for (const sym of symbols) {
        const micro = ob.getMetrics(sym);
        if (!micro) continue;

        const ticks = localFallback.ticks.get(sym) || [];
        const technical = ticks.length >= 2
          ? Math.tanh(((ticks[ticks.length - 1].price - ticks[ticks.length - 2].price) / Math.max(1e-9, ticks[ticks.length - 2].price)) * 100)
          : 0;

        const vpin = vpinMap.get(sym)?.value() ?? 0;
        const deltas = xdelta.snapshot(sym);
        const z = wallStats.get(sym)?.zscore(Math.max(Math.abs(micro.bai || 0), Math.abs(micro.obi || 0))) ?? 0;

        let c = evaluateDeepConsensus({
          technical,
          micro: { ...micro, vpin, crossExchangeDelta: deltas.delta },
          riskEvents: ob.snapshot().micro.recentEvents.filter(e => e.symbol === sym),
          macroSignal: { score: 0, regime: "normal" }
        });

        const microLagMs = guardrails.estimateMicroLagMs(ticks);
        c = guardrails.apply({ consensus: c, technical, microLagMs, obi: Number(micro.obi || 0), vpin });

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

        localSetConsensus(sym, out);
        io.emit("consensus:update", out);

        // hot cache + async writer
        await hotCache.setConsensus(sym, out, 45).catch(() => {});
        pgWriter.enqueueConsensus(out);
      }
    }, 250);

    // Tick ingestion from multiplexer
    multiplexer.on("market:tick", async (tick) => {
      const sym = String(tick.symbol || "").toUpperCase();
      if (!sym) return;

      localPushTick(sym, tick);
      ob.ingestTrade({ symbol: sym, price: tick.price, volume: tick.volume, side: tick.side, ts: tick.ts });
      vpinMap.get(sym)?.ingest({ volume: tick.volume, side: tick.side, ts: tick.ts });
      xdelta.ingest(tick);

      io.emit("market:tick", tick);

      // hot cache + async writer
      await hotCache.setLatestTick(sym, tick).catch(() => {});
      await hotCache.pushRecentTick(sym, tick).catch(() => {});
      pgWriter.enqueueTick(tick);
    });

    multiplexer.on("exchange:health", (h) => io.emit("exchange:health", h));

    // Boot
    async function boot() {
      try {
        await hotCache.connect();
      } catch (e) {
        console.warn("[boot] Redis unavailable, using local fallback only:", e?.message || e);
      }

      try {
        await pgWriter.init();
        pgWriter.start();
      } catch (e) {
        console.warn("[boot] PostgreSQL unavailable, writer disabled:", e?.message || e);
      }

      multiplexer.start();

      server.listen(PORT, () => {
        console.log(`[phoenix-backend] listening on http://localhost:${PORT}`);
      });
    }

    boot().catch((e) => {
      console.error("[boot] fatal:", e);
      process.exit(1);
    });

    process.on("SIGINT", async () => {
      console.log("Shutting down...");
      try { await hotCache.disconnect(); } catch {}
      try { await pgWriter.stop(); } catch {}
      process.exit(0);
    });
    """,

    # =========================================================
    # FRONTEND: Institutional cockpit HTML
    # =========================================================
    "phoenix-frontend/institutional-cockpit.html": r"""
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="UTF-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1.0" />
      <title>Phoenix Institutional Cockpit</title>
      <link rel="stylesheet" href="./css/institutional-cockpit.css" />
    </head>
    <body>
      <div id="cockpit-root" class="cockpit">
        <header class="topbar">
          <div class="brand">
            <h1>Phoenix Institutional Cockpit</h1>
            <small>Phase 4.2 • Multi-Exchange Microstructure</small>
          </div>
          <div class="status-cluster">
            <div class="status-card"><span>Backend</span><strong id="status-backend">--</strong></div>
            <div class="status-card"><span>Redis</span><strong id="status-redis">--</strong></div>
            <div class="status-card"><span>Postgres</span><strong id="status-pg">--</strong></div>
            <div class="status-card"><span>RTT</span><strong id="status-rtt">--</strong></div>
          </div>
        </header>

        <section class="grid">
          <article class="panel panel-chart">
            <h2>HFT Graph</h2>
            <canvas id="hft-canvas" width="1200" height="340"></canvas>
          </article>

          <article class="panel panel-orderflow">
            <h2>Orderflow / Consensus</h2>
            <pre id="consensus-box">Waiting...</pre>
          </article>

          <article class="panel panel-risk">
            <h2>Risk Matrix</h2>
            <pre id="risk-box">Waiting...</pre>
          </article>

          <article class="panel panel-terminal">
            <h2>Execution Console</h2>
            <div id="term-log" class="term-log"></div>
            <input id="term-input" class="term-input" placeholder="/buy BTCUSDT 0.01 10x | /consensus BTCUSDT | /backtest BTCUSDT 3600" />
          </article>

          <article class="panel panel-replay">
            <h2>Replay / Backtest</h2>
            <div class="replay-row">
              <button id="btn-backtest">Run Backtest (Last 1h BTCUSDT)</button>
            </div>
            <pre id="backtest-box">No results yet.</pre>
          </article>

          <article class="panel panel-health">
            <h2>Exchange Health</h2>
            <pre id="health-box">Waiting...</pre>
          </article>
        </section>
      </div>

      <script type="module" src="./js/pages/institutionalCockpit.js"></script>
    </body>
    </html>
    """,

    # =========================================================
    # FRONTEND: Institutional cockpit CSS
    # =========================================================
    "phoenix-frontend/css/institutional-cockpit.css": r"""
    :root {
      --bg: #060b14;
      --panel: #0e1523;
      --border: #1f2a40;
      --text: #dbe6ff;
      --muted: #93a4c7;
      --ok: #22c55e;
      --warn: #f59e0b;
      --bad: #ef4444;
      --accent: #60a5fa;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      background: radial-gradient(1000px 600px at 20% 0%, #0d1a2f 0%, var(--bg) 45%);
      color: var(--text);
      font-family: Inter, system-ui, Segoe UI, Roboto, sans-serif;
    }

    .cockpit {
      max-width: 1600px;
      margin: 0 auto;
      padding: 16px;
    }

    .topbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 14px;
      padding: 12px;
      border: 1px solid var(--border);
      border-radius: 12px;
      background: linear-gradient(180deg, #111b2f, #0c1422);
    }

    .brand h1 {
      margin: 0;
      font-size: 20px;
      letter-spacing: .2px;
    }

    .brand small {
      color: var(--muted);
    }

    .status-cluster {
      display: grid;
      grid-template-columns: repeat(4, minmax(120px, 1fr));
      gap: 8px;
      min-width: 520px;
    }

    .status-card {
      background: #0a1220;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 8px 10px;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }

    .status-card span {
      color: var(--muted);
      font-size: 12px;
    }

    .status-card strong {
      font-size: 14px;
    }

    .grid {
      display: grid;
      grid-template-columns: 2.2fr 1fr;
      grid-template-areas:
        "chart orderflow"
        "risk terminal"
        "replay health";
      gap: 12px;
    }

    .panel {
      background: linear-gradient(180deg, #0f1a2c, #0a1220);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 10px;
      min-height: 220px;
      box-shadow: 0 8px 24px rgba(0,0,0,.25);
    }

    .panel h2 {
      margin: 2px 0 8px;
      font-size: 14px;
      font-weight: 700;
      color: var(--accent);
      letter-spacing: .3px;
    }

    .panel-chart { grid-area: chart; min-height: 380px; }
    .panel-orderflow { grid-area: orderflow; }
    .panel-risk { grid-area: risk; }
    .panel-terminal { grid-area: terminal; min-height: 300px; }
    .panel-replay { grid-area: replay; }
    .panel-health { grid-area: health; }

    #hft-canvas {
      width: 100%;
      height: 320px;
      border: 1px solid #21304d;
      border-radius: 8px;
      background: #07101d;
      display: block;
    }

    pre {
      margin: 0;
      height: calc(100% - 28px);
      max-height: 290px;
      overflow: auto;
      padding: 8px;
      border-radius: 8px;
      border: 1px solid #243454;
      background: #0a1322;
      color: #d8e4ff;
      font-size: 12px;
      line-height: 1.35;
    }

    .term-log {
      height: 180px;
      overflow: auto;
      border: 1px solid #243454;
      background: #0a1322;
      border-radius: 8px;
      padding: 8px;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 12px;
      margin-bottom: 8px;
      white-space: pre-wrap;
    }

    .term-input {
      width: 100%;
      border: 1px solid #2a3a5b;
      border-radius: 8px;
      background: #0b1526;
      color: #e8efff;
      padding: 10px;
      font-size: 13px;
      outline: none;
    }

    .term-input:focus {
      border-color: #4d73b9;
      box-shadow: 0 0 0 2px rgba(77,115,185,0.25);
    }

    .replay-row {
      display: flex;
      gap: 8px;
      margin-bottom: 8px;
    }

    button {
      appearance: none;
      border: 1px solid #35508a;
      border-radius: 8px;
      background: linear-gradient(180deg, #1a2e55, #152746);
      color: #dce8ff;
      font-weight: 600;
      padding: 8px 10px;
      cursor: pointer;
    }

    button:hover {
      filter: brightness(1.08);
    }

    @media (max-width: 1200px) {
      .grid {
        grid-template-columns: 1fr;
        grid-template-areas:
          "chart"
          "orderflow"
          "risk"
          "terminal"
          "replay"
          "health";
      }

      .status-cluster {
        min-width: 0;
        grid-template-columns: repeat(2, minmax(120px, 1fr));
      }

      .topbar {
        flex-direction: column;
        align-items: flex-start;
      }
    }
    """,

    # =========================================================
    # FRONTEND: Institutional cockpit JS
    # =========================================================
    "phoenix-frontend/js/pages/institutionalCockpit.js": r"""
    import { io } from "https://cdn.socket.io/4.7.5/socket.io.esm.min.js";

    const socket = io("http://localhost:8787", { transports: ["websocket"] });

    const el = {
      backend: document.getElementById("status-backend"),
      redis: document.getElementById("status-redis"),
      pg: document.getElementById("status-pg"),
      rtt: document.getElementById("status-rtt"),
      consensus: document.getElementById("consensus-box"),
      risk: document.getElementById("risk-box"),
      termLog: document.getElementById("term-log"),
      termInput: document.getElementById("term-input"),
      btBtn: document.getElementById("btn-backtest"),
      btBox: document.getElementById("backtest-box"),
      health: document.getElementById("health-box"),
      canvas: document.getElementById("hft-canvas")
    };

    const ctx = el.canvas.getContext("2d");
    const priceSeries = [];
    const maxPoints = 1200;
    let lastConsensus = null;
    let lastRiskState = null;

    function setStatus(node, text, kind = "ok") {
      node.textContent = text;
      node.style.color = kind === "ok" ? "#22c55e" : kind === "warn" ? "#f59e0b" : "#ef4444";
    }

    function log(msg) {
      const line = document.createElement("div");
      line.textContent = msg;
      el.termLog.appendChild(line);
      el.termLog.scrollTop = el.termLog.scrollHeight;
    }

    function drawGraph() {
      const w = el.canvas.width;
      const h = el.canvas.height;
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = "#061021";
      ctx.fillRect(0, 0, w, h);

      if (priceSeries.length < 2) {
        ctx.fillStyle = "#7f9ac9";
        ctx.font = "12px monospace";
        ctx.fillText("Waiting for ticks...", 10, 20);
        return;
      }

      const min = Math.min(...priceSeries);
      const max = Math.max(...priceSeries);
      const span = Math.max(1e-9, max - min);

      ctx.strokeStyle = "#60a5fa";
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      for (let i = 0; i < priceSeries.length; i++) {
        const x = (i / (priceSeries.length - 1)) * (w - 20) + 10;
        const y = h - 20 - ((priceSeries[i] - min) / span) * (h - 40);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();

      // overlay consensus hint
      if (lastConsensus) {
        const score = Number(lastConsensus.score || 0);
        ctx.fillStyle = score >= 0 ? "rgba(34,197,94,0.16)" : "rgba(239,68,68,0.16)";
        ctx.fillRect(0, 0, w, h);
        ctx.fillStyle = "#dbeafe";
        ctx.font = "12px monospace";
        ctx.fillText(`score=${score.toFixed(4)} dir=${lastConsensus.direction} conf=${Number(lastConsensus.confidence || 0).toFixed(3)}`, 10, 16);
      }
    }

    function heartbeatPing() {
      const t0 = performance.now();
      socket.emit("latency:ping", { t0: Date.now() }, () => {
        const dt = performance.now() - t0;
        el.rtt.textContent = `${dt.toFixed(2)} ms`;
      });
    }

    async function refreshHealth() {
      try {
        const r = await fetch("http://localhost:8787/health");
        const j = await r.json();
        setStatus(el.backend, j.ok ? "ONLINE" : "DOWN", j.ok ? "ok" : "bad");

        const redisOk = !!j?.redis?.ok;
        setStatus(el.redis, redisOk ? "HOT" : "OFFLINE", redisOk ? "ok" : "warn");

        // pg status is inferred here as backend writer available in service block
        setStatus(el.pg, j?.services?.writer ? "READY" : "UNKNOWN", j?.services?.writer ? "ok" : "warn");
      } catch {
        setStatus(el.backend, "DOWN", "bad");
        setStatus(el.redis, "UNKNOWN", "warn");
        setStatus(el.pg, "UNKNOWN", "warn");
      }
    }

    socket.on("connect", () => {
      log("[system] connected");
      socket.emit("paper:bootstrap");
      heartbeatPing();
      setInterval(heartbeatPing, 1200);
    });

    socket.on("market:tick", (tick) => {
      const p = Number(tick.price || 0);
      if (Number.isFinite(p) && p > 0) {
        priceSeries.push(p);
        if (priceSeries.length > maxPoints) priceSeries.shift();
        drawGraph();
      }
    });

    socket.on("consensus:update", (c) => {
      if (!c) return;
      lastConsensus = c;
      el.consensus.textContent = JSON.stringify(c, null, 2);
      drawGraph();
    });

    socket.on("paper:state", (s) => {
      lastRiskState = s;
      el.risk.textContent = JSON.stringify(s, null, 2);
    });

    socket.on("exchange:health", (h) => {
      el.health.textContent = JSON.stringify(h, null, 2);
    });

    socket.on("paper:orderAck", (ack) => log(`[orderAck] ${JSON.stringify(ack)}`));
    socket.on("paper:fill", (fill) => log(`[fill] ${JSON.stringify(fill)}`));
    socket.on("paper:error", (e) => log(`[error] ${e.message}`));

    el.termInput.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      const raw = el.termInput.value.trim();
      if (!raw) return;
      el.termInput.value = "";
      log(`> ${raw}`);

      const p = raw.split(/\s+/);
      const cmd = (p[0] || "").toLowerCase();

      if (cmd === "/buy" || cmd === "/sell") {
        socket.emit("paper:order", {
          userId: "demo",
          type: "market",
          side: cmd.slice(1),
          symbol: (p[1] || "BTCUSDT").toUpperCase(),
          qty: Number(p[2] || 0.001),
          leverage: Number(String(p[3] || "1x").replace(/x/i, "")) || 1,
          marginType: "cross"
        });
        return;
      }

      if (cmd === "/consensus") {
        socket.emit("consensus:get", { symbol: (p[1] || "BTCUSDT").toUpperCase() });
        return;
      }

      if (cmd === "/deposit") {
        socket.emit("paper:deposit", { userId: "demo", amount: Number(p[1] || 0) });
        return;
      }

      if (cmd === "/risk") {
        if (lastRiskState) el.risk.textContent = JSON.stringify(lastRiskState, null, 2);
        return;
      }

      log("[system] unknown command");
    });

    el.btBtn.addEventListener("click", async () => {
      try {
        const now = Date.now();
        const oneHour = 3600 * 1000;
        const body = {
          symbol: "BTCUSDT",
          fromTs: now - oneHour,
          toTs: now,
          source: "postgres",
          longThreshold: 0.15,
          shortThreshold: -0.15,
          feeBps: 2
        };
        const r = await fetch("http://localhost:8787/api/replay/backtest", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body)
        });
        const j = await r.json();
        el.btBox.textContent = JSON.stringify(j, null, 2);
      } catch (e) {
        el.btBox.textContent = JSON.stringify({ ok: false, error: String(e) }, null, 2);
      }
    });

    refreshHealth();
    setInterval(refreshHealth, 5000);
    """,
}

def write_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = textwrap.dedent(content).lstrip("\n")
    path.write_text(cleaned, encoding="utf-8")

def main():
    print("🚀 Generating Phase 4.2 files...")
    for rel, content in FILES.items():
        out = ROOT / rel
        write_file(out, content)
        print(f"✅ {rel}")
    print("\nDone.")
    print("Next steps:")
    print("1) cd phoenix-backend")
    print("2) npm install")
    print("3) npm start")
    print("4) Open phoenix-frontend/institutional-cockpit.html in browser")

if __name__ == "__main__":
    main()
