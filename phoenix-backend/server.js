import express from "express";
import http from "http";
import cors from "cors";
import { Server } from "socket.io";

import ExchangeMultiplexer from "./exchangeMultiplexer.js";
import InstitutionalOrderBook from "./institutionalOrderBook.js";
import evaluateDeepConsensus from "./evaluate_deep_consensus.js";
import NewsSentimentEngine from "./newsSentimentEngine.js";
import { PaperTradeEngine } from "./paperTradeEngine.js";
import {
  RollingStats,
  VPINCalculator,
  CrossExchangeDelta,
  ExecutionGuardrails
} from "./strategyInfra.js";

const PORT = process.env.PORT || 8787;
const app = express();
app.use(cors());
app.use(express.json());

app.get("/health", (_req, res) => {
  res.json({ ok: true, service: "phoenix-backend", ts: Date.now() });
});

const server = http.createServer(app);
const io = new Server(server, {
  cors: { origin: "*", methods: ["GET", "POST"] }
});

// -------------------------
// Engines
// -------------------------
const symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"];
const paper = new PaperTradeEngine({
  maintenanceMarginRate: 0.005,
  takerFeeRate: 0.0004,
  makerFeeRate: 0.0002
});

const multiplexer = new ExchangeMultiplexer({ io, symbols });
const ob = new InstitutionalOrderBook({ io });
const news = new NewsSentimentEngine();

// Adaptive threshold and VPIN infra per symbol
const wallStats = new Map();    // symbol -> RollingStats
const vpinMap = new Map();      // symbol -> VPINCalculator
const xdelta = new CrossExchangeDelta({ leader: "binance", lagger: "bybit" });
const guardrails = new ExecutionGuardrails({ maxMicroLagMs: 500 });

for (const s of symbols) {
  wallStats.set(s, new RollingStats(24 * 60 * 60)); // rolling seconds approx
  vpinMap.set(s, new VPINCalculator({ bucketVolume: 250, windowBuckets: 60 }));
}

// In-memory cache (hot path), can later swap to Redis
const hotCache = {
  ticks: new Map(),        // symbol -> recent ticks
  consensus: new Map(),    // symbol -> last consensus
  latency: { backendRttMs: 0, microLagMs: 0 }
};

function pushTick(symbol, tick) {
  if (!hotCache.ticks.has(symbol)) hotCache.ticks.set(symbol, []);
  const arr = hotCache.ticks.get(symbol);
  arr.push(tick);
  if (arr.length > 5000) arr.shift();
}

// -------------------------
// Multiplexer event flow
// -------------------------
multiplexer.on("market:tick", (tick) => {
  const symbol = String(tick.symbol || "").toUpperCase();
  if (!symbol) return;

  pushTick(symbol, tick);

  // Trade into microstructure + VPIN
  ob.ingestTrade({
    symbol,
    price: tick.price,
    volume: tick.volume,
    side: tick.side,
    ts: tick.ts
  });

  const vpin = vpinMap.get(symbol);
  if (vpin) {
    vpin.ingest({
      volume: Number(tick.volume || 0),
      side: tick.side,
      ts: Number(tick.ts || Date.now())
    });
  }

  // Cross-exchange delta
  xdelta.ingest(tick);

  // Broadcast normalized tick
  io.emit("market:tick", tick);
});

multiplexer.on("exchange:health", (payload) => {
  io.emit("exchange:health", payload);
});

// -------------------------
// Simulated orderbook updater (replace with real L2 feeds)
// -------------------------
setInterval(() => {
  const now = Date.now();
  for (const sym of symbols) {
    const ticks = hotCache.ticks.get(sym) || [];
    const last = ticks.length ? ticks[ticks.length - 1] : null;
    const mid = Number(last?.price || (sym === "BTCUSDT" ? 65000 : sym === "ETHUSDT" ? 3500 : 150));

    const bids = [], asks = [];
    for (let i = 0; i < 80; i++) {
      const pBid = mid * (1 - (i + 1) * 0.0004);
      const pAsk = mid * (1 + (i + 1) * 0.0004);
      const qBid = Math.random() * 6 + 0.2;
      const qAsk = Math.random() * 6 + 0.2;
      bids.push([Number(pBid.toFixed(4)), Number(qBid.toFixed(4))]);
      asks.push([Number(pAsk.toFixed(4)), Number(qAsk.toFixed(4))]);
    }

    // inject occasional large walls to exercise adaptive tracker
    if (Math.random() < 0.02) bids[2][1] = Number((40 + Math.random() * 70).toFixed(3));
    if (Math.random() < 0.02) asks[2][1] = Number((40 + Math.random() * 70).toFixed(3));

    // update rolling wall stats for adaptive threshold
    const largeTop = Math.max(bids[2]?.[1] || 0, asks[2]?.[1] || 0);
    wallStats.get(sym)?.push(largeTop);

    ob.updateSnapshot(sym, { bids, asks, ts: now });
  }
}, 200);

// -------------------------
// Consensus loop
// -------------------------
setInterval(() => {
  const macroSignal = news.getLastSignal();

  for (const sym of symbols) {
    const micro = ob.getMetrics(sym);
    if (!micro) continue;

    // Adaptive z-score threshold derived from rolling wall sizes
    const rs = wallStats.get(sym);
    const z = rs?.zscore(Math.max(Math.abs(micro.bai || 0), Math.abs(micro.obi || 0))) ?? 0;

    const vpin = vpinMap.get(sym)?.value() ?? 0;
    const deltas = xdelta.snapshot(sym);

    // simplistic technical placeholder from recent ticks
    const ticks = hotCache.ticks.get(sym) || [];
    let technical = 0;
    if (ticks.length >= 2) {
      const a = ticks[ticks.length - 1].price;
      const b = ticks[ticks.length - 2].price;
      technical = Math.tanh((a - b) / Math.max(1e-9, b) * 100);
    }

    const riskEvents = ob.snapshot().micro.recentEvents.filter(e => e.symbol === sym);

    let c = evaluateDeepConsensus({
      technical,
      micro: {
        ...micro,
        // include VPIN and cross-exchange spread into micro context
        vpin,
        crossExchangeDelta: deltas.delta
      },
      riskEvents,
      macroSignal
    });

    // guardrails: if lag high, fallback to technical-only/no macro
    const microLagMs = guardrails.estimateMicroLagMs(ticks);
    hotCache.latency.microLagMs = microLagMs;

    c = guardrails.apply({
      consensus: c,
      technical,
      microLagMs,
      obi: Number(micro.obi || 0),
      vpin
    });

    // attach diagnostics
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

    hotCache.consensus.set(sym, out);
    io.emit("consensus:update", out);
  }
}, 250);

// -------------------------
// Socket API
// -------------------------
io.on("connection", (socket) => {
  socket.emit("server:hello", {
    id: socket.id,
    ts: Date.now(),
    channels: [
      "market:tick",
      "exchange:health",
      "orderbook:metrics",
      "flow:metrics",
      "consensus:update",
      "paper:state",
      "paper:orderAck",
      "paper:fill",
      "paper:liquidation"
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
      const cons = hotCache.consensus.get(sym);

      // dynamic slippage guardrail from OBI/VPIN
      const slipBps = guardrails.dynamicSlippageBps(cons?.diagnostics?.vpin ?? 0, cons?.diagnostics?.crossExchangeDelta ?? 0);
      const enriched = { ...order, timestamp: Date.now(), slippageBps: slipBps };
      const result = paper.submitOrder(enriched);

      socket.emit("paper:orderAck", { ...result.ack, slippageBps: slipBps });
      for (const fill of result.fills) socket.emit("paper:fill", fill);
      if (result.liquidation) socket.emit("paper:liquidation", result.liquidation);
      socket.emit("paper:state", paper.getState(order.userId || "demo"));
    } catch (err) {
      socket.emit("paper:error", { message: err.message });
    }
  });

  socket.on("news:ingest", ({ items = [] } = {}) => {
    const sig = news.evaluateFeed(items);
    socket.emit("news:signal", sig);
  });

  socket.on("consensus:get", ({ symbol = "BTCUSDT" } = {}) => {
    socket.emit("consensus:update", hotCache.consensus.get(symbol) || null);
  });
});

multiplexer.start();

server.listen(PORT, () => {
  console.log(`[phoenix-backend] listening on http://localhost:${PORT}`);
});
