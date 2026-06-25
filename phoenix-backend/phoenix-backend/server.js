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
