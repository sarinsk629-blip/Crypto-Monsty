import express from "express";
import http from "http";
import cors from "cors";
import { Server } from "socket.io";
import { PaperTradeEngine } from "./paperTradeEngine.js";

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

// ---------------------------
// Market data aggregation state
// ---------------------------
const marketState = {
  books: new Map(), // symbol -> { bids: [[p,qty]], asks: [[p,qty]], ts }
  trades: new Map(), // symbol -> recent trades
  candles: new Map(), // symbol -> ohlcv placeholder
  latest: new Map(), // symbol -> latest tick
};

const engine = new PaperTradeEngine({
  maintenanceMarginRate: 0.005, // 0.5%
  takerFeeRate: 0.0004,
  makerFeeRate: 0.0002
});

// ------------------------------------
// Exchange WS scaffolding (placeholder)
// ------------------------------------
function connectExchangeStreams() {
  // In production, connect to Binance/Bybit/OKX WS feeds here.
  // This scaffolding simulates high-frequency ticks.
  const symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"];
  const base = { BTCUSDT: 65000, ETHUSDT: 3500, SOLUSDT: 150 };

  setInterval(() => {
    for (const sym of symbols) {
      const noise = (Math.random() - 0.5) * (sym === "BTCUSDT" ? 25 : sym === "ETHUSDT" ? 2 : 0.25);
      base[sym] = Math.max(0.01, base[sym] + noise);

      const side = Math.random() > 0.5 ? "buy" : "sell";
      const volume = Number((Math.random() * 5 + 0.01).toFixed(4));
      const tick = {
        symbol: sym,
        price: Number(base[sym].toFixed(sym === "BTCUSDT" ? 2 : 4)),
        volume,
        side,
        timestamp: Date.now()
      };

      marketState.latest.set(sym, tick);

      if (!marketState.trades.has(sym)) marketState.trades.set(sym, []);
      const arr = marketState.trades.get(sym);
      arr.push(tick);
      if (arr.length > 500) arr.shift();

      io.emit("market:tick", tick);
    }
  }, 120); // ~8.3Hz simulation per symbol
}

// ---------------------------
// Socket.io API
// ---------------------------
io.on("connection", (socket) => {
  console.log("[io] client connected:", socket.id);

  socket.emit("server:hello", {
    id: socket.id,
    ts: Date.now(),
    channels: [
      "market:tick",
      "paper:state",
      "paper:orderAck",
      "paper:fill",
      "paper:liquidation",
      "ai:response"
    ]
  });

  socket.on("market:subscribe", (payload = {}) => {
    const { symbols = ["BTCUSDT"] } = payload;
    socket.join(symbols.map((s) => `symbol:${s}`));
    socket.emit("market:subscribed", { symbols, ts: Date.now() });
  });

  socket.on("paper:bootstrap", () => {
    socket.emit("paper:state", engine.getState());
  });

  socket.on("paper:deposit", ({ userId = "demo", amount = 0 } = {}) => {
    engine.deposit(userId, Number(amount || 0));
    socket.emit("paper:state", engine.getState(userId));
  });

  socket.on("paper:order", (order = {}) => {
    try {
      const enriched = { ...order, timestamp: Date.now() };
      const result = engine.submitOrder(enriched);

      socket.emit("paper:orderAck", result.ack);
      for (const fill of result.fills) socket.emit("paper:fill", fill);
      if (result.liquidation) socket.emit("paper:liquidation", result.liquidation);
      socket.emit("paper:state", engine.getState(order.userId || "demo"));
    } catch (err) {
      socket.emit("paper:error", { message: err.message });
    }
  });

  socket.on("paper:mark", ({ symbol, markPrice } = {}) => {
    engine.updateMarkPrice(symbol, Number(markPrice));
    const liqEvents = engine.checkLiquidations();
    for (const e of liqEvents) io.emit("paper:liquidation", e);
    socket.emit("paper:state", engine.getState());
  });

  socket.on("ai:command", (payload = {}) => {
    // Placeholder AI responder / command execution result
    const { command = "", context = {} } = payload;
    const response = {
      command,
      context,
      ts: Date.now(),
      insight: "Command received by backend. Integrate model/provider in Phase 4."
    };
    socket.emit("ai:response", response);
  });

  socket.on("disconnect", () => {
    console.log("[io] client disconnected:", socket.id);
  });
});

connectExchangeStreams();

server.listen(PORT, () => {
  console.log(`[phoenix-backend] listening on http://localhost:${PORT}`);
});
