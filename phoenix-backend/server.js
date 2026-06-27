import { createRequire } from 'module';
const require = createRequire(import.meta.url);
globalThis.require = require;
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
