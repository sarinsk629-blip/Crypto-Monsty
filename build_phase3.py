#!/usr/bin/env python3
# build_phase3.py

from pathlib import Path
import textwrap

ROOT = Path(".").resolve()

FILES = {
    # =========================
    # Backend package.json
    # =========================
    "phoenix-backend/package.json": r"""
    {
      "name": "phoenix-backend",
      "version": "1.0.0",
      "description": "Phoenix Terminal Phase 3 backend - HFT data aggregator + paper trading engine",
      "main": "server.js",
      "type": "module",
      "scripts": {
        "start": "node server.js",
        "dev": "node server.js"
      },
      "dependencies": {
        "cors": "^2.8.5",
        "express": "^4.19.2",
        "socket.io": "^4.8.1"
      }
    }
    """,

    # =========================
    # Backend server.js
    # =========================
    "phoenix-backend/server.js": r"""
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
    """,

    # =========================
    # Backend paperTradeEngine.js
    # =========================
    "phoenix-backend/paperTradeEngine.js": r"""
    export class PaperTradeEngine {
      constructor({
        maintenanceMarginRate = 0.005,
        takerFeeRate = 0.0004,
        makerFeeRate = 0.0002
      } = {}) {
        this.maintenanceMarginRate = maintenanceMarginRate;
        this.takerFeeRate = takerFeeRate;
        this.makerFeeRate = makerFeeRate;

        this.users = new Map(); // userId -> account
        this.orderBooks = new Map(); // symbol -> { bids, asks }
        this.markPrices = new Map(); // symbol -> mark
      }

      // ---------- account helpers ----------
      _getOrCreateUser(userId = "demo") {
        if (!this.users.has(userId)) {
          this.users.set(userId, {
            userId,
            walletBalance: 10000,
            realizedPnl: 0,
            positions: new Map(), // symbol -> position
            openOrders: new Map(), // orderId -> order
            isolatedMargin: new Map() // symbol -> margin
          });
        }
        return this.users.get(userId);
      }

      deposit(userId, amount) {
        if (amount <= 0) throw new Error("Deposit amount must be positive");
        const u = this._getOrCreateUser(userId);
        u.walletBalance += amount;
        return u.walletBalance;
      }

      // ---------- market marks ----------
      updateMarkPrice(symbol, markPrice) {
        if (!symbol || !Number.isFinite(markPrice) || markPrice <= 0) return;
        this.markPrices.set(symbol, markPrice);
      }

      // ---------- order book ----------
      _book(symbol) {
        if (!this.orderBooks.has(symbol)) {
          this.orderBooks.set(symbol, { bids: [], asks: [] });
        }
        return this.orderBooks.get(symbol);
      }

      _sortBook(book) {
        book.bids.sort((a, b) => b.price - a.price || a.timestamp - b.timestamp); // high->low
        book.asks.sort((a, b) => a.price - b.price || a.timestamp - b.timestamp); // low->high
      }

      _newOrderId() {
        return "ord_" + Math.random().toString(36).slice(2, 10) + "_" + Date.now();
      }

      submitOrder(order) {
        const normalized = this._normalizeOrder(order);
        const user = this._getOrCreateUser(normalized.userId);
        const book = this._book(normalized.symbol);

        const ack = {
          orderId: normalized.id,
          accepted: true,
          order: normalized
        };

        const fills = [];

        if (normalized.type === "market") {
          fills.push(...this._executeMarketOrder(user, book, normalized));
        } else {
          fills.push(...this._executeLimitOrder(user, book, normalized));
        }

        // post-trade checks
        const liquidation = this._maybeLiquidate(user, normalized.symbol);

        return { ack, fills, liquidation };
      }

      _normalizeOrder(o = {}) {
        const type = (o.type || "market").toLowerCase();
        const side = (o.side || "buy").toLowerCase();
        const marginType = (o.marginType || "cross").toLowerCase();

        if (!["market", "limit"].includes(type)) throw new Error("Invalid order type");
        if (!["buy", "sell"].includes(side)) throw new Error("Invalid side");
        if (!["cross", "isolated"].includes(marginType)) throw new Error("Invalid marginType");

        const qty = Number(o.qty);
        const leverage = Math.max(1, Number(o.leverage || 1));
        const price = o.price != null ? Number(o.price) : null;

        if (!Number.isFinite(qty) || qty <= 0) throw new Error("Invalid qty");
        if (type === "limit" && (!Number.isFinite(price) || price <= 0)) throw new Error("Invalid limit price");

        return {
          id: o.id || this._newOrderId(),
          userId: o.userId || "demo",
          symbol: String(o.symbol || "BTCUSDT"),
          type,
          side,
          qty,
          price,
          leverage,
          marginType,
          reduceOnly: !!o.reduceOnly,
          timestamp: o.timestamp || Date.now()
        };
      }

      _executeMarketOrder(user, book, taker) {
        const fills = [];
        const opposing = taker.side === "buy" ? book.asks : book.bids;

        // Try matching against resting orders first
        let remaining = taker.qty;
        while (remaining > 0 && opposing.length > 0) {
          const top = opposing[0];
          const tradeQty = Math.min(remaining, top.remainingQty);
          const tradePrice = top.price;

          top.remainingQty -= tradeQty;
          remaining -= tradeQty;

          fills.push(this._applyFill({
            takerUser: user,
            makerUser: this._getOrCreateUser(top.userId),
            symbol: taker.symbol,
            side: taker.side,
            qty: tradeQty,
            price: tradePrice,
            taker,
            maker: top
          }));

          if (top.remainingQty <= 1e-10) opposing.shift();
        }

        // If still remaining, execute against mark price as synthetic liquidity
        if (remaining > 0) {
          const mark = this.markPrices.get(taker.symbol) || (taker.symbol.includes("BTC") ? 65000 : 1000);
          fills.push(this._applyFill({
            takerUser: user,
            makerUser: null,
            symbol: taker.symbol,
            side: taker.side,
            qty: remaining,
            price: mark,
            taker,
            maker: null
          }));
        }

        return fills;
      }

      _executeLimitOrder(user, book, order) {
        const fills = [];
        const opposing = order.side === "buy" ? book.asks : book.bids;

        let remaining = order.qty;
        while (remaining > 0 && opposing.length > 0) {
          const top = opposing[0];
          const isCrossed =
            order.side === "buy" ? order.price >= top.price : order.price <= top.price;

          if (!isCrossed) break;

          const tradeQty = Math.min(remaining, top.remainingQty);
          const tradePrice = top.price;

          top.remainingQty -= tradeQty;
          remaining -= tradeQty;

          fills.push(this._applyFill({
            takerUser: user,
            makerUser: this._getOrCreateUser(top.userId),
            symbol: order.symbol,
            side: order.side,
            qty: tradeQty,
            price: tradePrice,
            taker: order,
            maker: top
          }));

          if (top.remainingQty <= 1e-10) opposing.shift();
        }

        // Rest remaining as maker order
        if (remaining > 0) {
          const resting = { ...order, remainingQty: remaining };
          const sameSide = order.side === "buy" ? book.bids : book.asks;
          sameSide.push(resting);
          this._sortBook(book);

          const acc = this._getOrCreateUser(order.userId);
          acc.openOrders.set(order.id, resting);
        }

        return fills;
      }

      _applyFill({ takerUser, makerUser, symbol, side, qty, price, taker, maker }) {
        const notional = qty * price;
        const takerFee = notional * this.takerFeeRate;
        const makerFee = makerUser ? notional * this.makerFeeRate : 0;

        this._updatePosition(takerUser, {
          symbol,
          side,
          qty,
          price,
          leverage: taker.leverage,
          marginType: taker.marginType,
          fee: takerFee,
          reduceOnly: taker.reduceOnly
        });

        if (makerUser && maker) {
          const makerSide = maker.side; // maker side from resting order
          this._updatePosition(makerUser, {
            symbol,
            side: makerSide,
            qty,
            price,
            leverage: maker.leverage || 1,
            marginType: maker.marginType || "cross",
            fee: makerFee,
            reduceOnly: !!maker.reduceOnly
          });
        }

        return {
          symbol,
          qty,
          price,
          aggressorSide: side,
          notional,
          ts: Date.now(),
          takerOrderId: taker.id,
          makerOrderId: maker?.id || null
        };
      }

      _updatePosition(user, fill) {
        const {
          symbol, side, qty, price, leverage,
          marginType, fee, reduceOnly
        } = fill;

        const key = symbol;
        const pos = user.positions.get(key) || {
          symbol,
          qty: 0,          // signed: +long / -short
          entryPrice: 0,
          leverage: leverage || 1,
          marginType: marginType || "cross",
          unrealizedPnl: 0,
          maintenanceMargin: 0
        };

        const signedQty = side === "buy" ? qty : -qty;
        const oldQty = pos.qty;
        const newQty = oldQty + signedQty;

        // Reduce/flip logic + realized PnL
        if (oldQty !== 0 && Math.sign(oldQty) !== Math.sign(signedQty)) {
          const closeQty = Math.min(Math.abs(oldQty), Math.abs(signedQty));
          const pnlPerUnit = oldQty > 0 ? (price - pos.entryPrice) : (pos.entryPrice - price);
          const realized = pnlPerUnit * closeQty;
          user.realizedPnl += realized;
          user.walletBalance += realized;
        }

        // Entry price calc for remaining/open direction
        if (newQty === 0) {
          pos.qty = 0;
          pos.entryPrice = 0;
        } else if (oldQty === 0 || Math.sign(oldQty) === Math.sign(newQty)) {
          // weighted average only when increasing same direction
          const prevAbs = Math.abs(oldQty);
          const addAbs = Math.abs(signedQty);
          const total = prevAbs + addAbs;
          pos.entryPrice = total === 0 ? 0 : ((pos.entryPrice * prevAbs) + (price * addAbs)) / total;
          pos.qty = newQty;
        } else {
          // flipped or reduced
          pos.qty = newQty;
          if (Math.sign(oldQty) !== Math.sign(newQty)) pos.entryPrice = price; // flip
        }

        pos.leverage = leverage || pos.leverage || 1;
        pos.marginType = marginType || pos.marginType || "cross";

        // Fees
        user.walletBalance -= fee;

        // Margin bookkeeping (simplified)
        const mark = this.markPrices.get(symbol) || price;
        const notional = Math.abs(pos.qty) * mark;
        pos.maintenanceMargin = notional * this.maintenanceMarginRate;
        pos.unrealizedPnl = pos.qty >= 0
          ? (mark - pos.entryPrice) * Math.abs(pos.qty)
          : (pos.entryPrice - mark) * Math.abs(pos.qty);

        if (pos.marginType === "isolated") {
          if (!user.isolatedMargin.has(symbol)) {
            user.isolatedMargin.set(symbol, notional / Math.max(1, pos.leverage));
          }
        }

        if (reduceOnly && Math.sign(oldQty) === Math.sign(newQty) && Math.abs(newQty) > Math.abs(oldQty)) {
          throw new Error("Reduce-only order attempted to increase position");
        }

        user.positions.set(key, pos);
      }

      _accountEquity(user) {
        let upl = 0;
        let mm = 0;

        for (const pos of user.positions.values()) {
          const mark = this.markPrices.get(pos.symbol) || pos.entryPrice || 0;
          const notional = Math.abs(pos.qty) * mark;
          const upnl = pos.qty >= 0
            ? (mark - pos.entryPrice) * Math.abs(pos.qty)
            : (pos.entryPrice - mark) * Math.abs(pos.qty);

          upl += upnl;
          mm += notional * this.maintenanceMarginRate;
        }

        const equity = user.walletBalance + upl;
        return { equity, unrealizedPnl: upl, maintenanceMargin: mm };
      }

      _maybeLiquidate(user, symbol) {
        const { equity, maintenanceMargin } = this._accountEquity(user);
        if (equity > maintenanceMargin) return null;

        const pos = user.positions.get(symbol);
        if (!pos || pos.qty === 0) return null;

        // Hard liquidation: close position at mark
        const mark = this.markPrices.get(symbol) || pos.entryPrice;
        const closeSide = pos.qty > 0 ? "sell" : "buy";
        const qty = Math.abs(pos.qty);

        // Realize pnl on full close
        const pnl = pos.qty > 0
          ? (mark - pos.entryPrice) * qty
          : (pos.entryPrice - mark) * qty;

        user.walletBalance += pnl;
        user.realizedPnl += pnl;
        user.positions.set(symbol, { ...pos, qty: 0, entryPrice: 0, unrealizedPnl: 0, maintenanceMargin: 0 });

        return {
          userId: user.userId,
          symbol,
          qty,
          markPrice: mark,
          closeSide,
          realizedPnl: pnl,
          reason: "Maintenance margin breach",
          ts: Date.now()
        };
      }

      checkLiquidations() {
        const events = [];
        for (const user of this.users.values()) {
          for (const symbol of user.positions.keys()) {
            const evt = this._maybeLiquidate(user, symbol);
            if (evt) events.push(evt);
          }
        }
        return events;
      }

      getState(userId = null) {
        if (userId) {
          const user = this._getOrCreateUser(userId);
          const summary = this._accountEquity(user);
          return this._serializeUser(user, summary);
        }

        const users = [];
        for (const user of this.users.values()) {
          users.push(this._serializeUser(user, this._accountEquity(user)));
        }

        return {
          ts: Date.now(),
          users,
          marks: Object.fromEntries(this.markPrices.entries())
        };
      }

      _serializeUser(user, summary) {
        const positions = Array.from(user.positions.values()).map((p) => ({ ...p }));
        const openOrders = Array.from(user.openOrders.values()).map((o) => ({ ...o }));

        return {
          userId: user.userId,
          walletBalance: user.walletBalance,
          realizedPnl: user.realizedPnl,
          equity: summary.equity,
          unrealizedPnl: summary.unrealizedPnl,
          maintenanceMargin: summary.maintenanceMargin,
          positions,
          openOrders
        };
      }
    }
    """,

    # =========================
    # Frontend terminal page
    # =========================
    "phoenix-frontend/js/pages/terminal.js": r"""
    import { io } from "https://cdn.socket.io/4.7.5/socket.io.esm.min.js";

    // Simple terminal UI mount
    export function renderTerminalPage(container = document.body) {
      const root = document.createElement("section");
      root.id = "terminal-page";
      root.innerHTML = `
        <div style="display:grid;gap:8px;max-width:900px">
          <h2>Phoenix Terminal CLI</h2>
          <div id="terminal-log" style="height:320px;overflow:auto;background:#0f172a;color:#e2e8f0;padding:10px;border-radius:8px;font-family:monospace"></div>
          <input id="terminal-input" placeholder="Type /buy BTCUSDT 0.01 10x | /analyze orderflow" style="padding:10px;border-radius:8px;border:1px solid #334155;background:#111827;color:#e2e8f0" />
        </div>
      `;
      container.appendChild(root);

      const logEl = root.querySelector("#terminal-log");
      const inputEl = root.querySelector("#terminal-input");
      const socket = io("http://localhost:8787", { transports: ["websocket"] });

      function log(line) {
        const div = document.createElement("div");
        div.textContent = line;
        logEl.appendChild(div);
        logEl.scrollTop = logEl.scrollHeight;
      }

      socket.on("connect", () => {
        log("[system] connected to backend");
        socket.emit("paper:bootstrap");
      });

      socket.on("server:hello", (msg) => log(`[hello] ${JSON.stringify(msg)}`));
      socket.on("market:tick", (tick) => log(`[tick] ${tick.symbol} ${tick.price} ${tick.side} v=${tick.volume}`));
      socket.on("paper:orderAck", (ack) => log(`[orderAck] ${JSON.stringify(ack)}`));
      socket.on("paper:fill", (fill) => log(`[fill] ${JSON.stringify(fill)}`));
      socket.on("paper:state", (state) => log(`[state] ${JSON.stringify(state)}`));
      socket.on("paper:liquidation", (evt) => log(`[LIQUIDATION] ${JSON.stringify(evt)}`));
      socket.on("ai:response", (r) => log(`[ai] ${r.insight}`));
      socket.on("paper:error", (e) => log(`[error] ${e.message}`));

      inputEl.addEventListener("keydown", (e) => {
        if (e.key !== "Enter") return;
        const raw = inputEl.value.trim();
        if (!raw) return;
        inputEl.value = "";
        log(`> ${raw}`);
        const cmd = parseCommand(raw);
        routeCommand(cmd, socket, log);
      });
    }

    export function parseCommand(raw) {
      const txt = String(raw || "").trim();
      const parts = txt.split(/\s+/);
      const command = (parts[0] || "").toLowerCase();

      // /buy BTCUSDT 0.01 10x
      if (command === "/buy" || command === "/sell") {
        const symbol = (parts[1] || "BTCUSDT").toUpperCase();
        const qty = Number(parts[2] || 0.001);
        const levToken = parts[3] || "1x";
        const leverage = Number(String(levToken).replace(/x/i, "")) || 1;
        return {
          kind: "trade",
          side: command.replace("/", ""),
          symbol,
          qty,
          leverage,
          type: "market"
        };
      }

      // /limit buy BTCUSDT 64000 0.01 5x
      if (command === "/limit") {
        return {
          kind: "trade",
          type: "limit",
          side: (parts[1] || "buy").toLowerCase(),
          symbol: (parts[2] || "BTCUSDT").toUpperCase(),
          price: Number(parts[3] || 0),
          qty: Number(parts[4] || 0.001),
          leverage: Number(String(parts[5] || "1x").replace(/x/i, "")) || 1
        };
      }

      // /analyze orderflow
      if (command === "/analyze") {
        return {
          kind: "analyze",
          topic: (parts[1] || "orderflow").toLowerCase()
        };
      }

      // /deposit 5000
      if (command === "/deposit") {
        return { kind: "deposit", amount: Number(parts[1] || 0) };
      }

      return { kind: "ai", text: txt };
    }

    export function routeCommand(cmd, socket, log) {
      if (cmd.kind === "trade") {
        socket.emit("paper:order", {
          userId: "demo",
          symbol: cmd.symbol,
          side: cmd.side,
          qty: cmd.qty,
          leverage: cmd.leverage,
          type: cmd.type || "market",
          price: cmd.price,
          marginType: "cross"
        });
        return;
      }

      if (cmd.kind === "deposit") {
        socket.emit("paper:deposit", { userId: "demo", amount: cmd.amount });
        return;
      }

      if (cmd.kind === "analyze") {
        socket.emit("ai:command", {
          command: "/analyze",
          context: { topic: cmd.topic, source: "terminal-cli" }
        });
        log(`[analyze] requested ${cmd.topic}`);
        return;
      }

      socket.emit("ai:command", { command: cmd.text, context: { source: "terminal-cli" } });
    }
    """,

    # =========================
    # Frontend WebGL renderer placeholder
    # =========================
    "phoenix-frontend/js/renderers/orderbookWebGL.js": r"""
    /**
     * orderbookWebGL.js
     * High-performance renderer placeholder for:
     *  - Deep order book liquidity heatmap
     *  - Cumulative Volume Delta (CVD) overlays
     *
     * This file is intentionally framework-agnostic.
     * Integrate with your chart/layout engine in Phase 4.
     */

    export class OrderbookWebGLRenderer {
      constructor(canvas) {
        if (!canvas) throw new Error("OrderbookWebGLRenderer requires a canvas element");
        this.canvas = canvas;
        this.gl = canvas.getContext("webgl2", { antialias: false, alpha: true });
        this.lastFrameTs = 0;
        this.book = { bids: [], asks: [] };
        this.cvd = [];
        this.running = false;

        if (!this.gl) {
          console.warn("[orderbookWebGL] WebGL2 not supported, fallback required.");
          this.fallback2D = canvas.getContext("2d");
        }
      }

      setData({ bids = [], asks = [], cvd = [] } = {}) {
        this.book = { bids, asks };
        this.cvd = cvd;
      }

      start() {
        if (this.running) return;
        this.running = true;
        requestAnimationFrame(this._renderLoop);
      }

      stop() {
        this.running = false;
      }

      resize(width, height) {
        this.canvas.width = width;
        this.canvas.height = height;
        if (this.gl) this.gl.viewport(0, 0, width, height);
      }

      _renderLoop = (ts) => {
        if (!this.running) return;
        const dt = ts - this.lastFrameTs;
        this.lastFrameTs = ts;

        this.render(dt);
        requestAnimationFrame(this._renderLoop);
      };

      render(_dt) {
        if (this.gl) {
          // Placeholder clear; plug in shaders + VBOs in Phase 4
          this.gl.clearColor(0.04, 0.07, 0.12, 1.0);
          this.gl.clear(this.gl.COLOR_BUFFER_BIT);
          return;
        }

        // 2D fallback simple bars
        if (!this.fallback2D) return;
        const ctx = this.fallback2D;
        const { width, height } = this.canvas;
        ctx.clearRect(0, 0, width, height);
        ctx.fillStyle = "#0b1220";
        ctx.fillRect(0, 0, width, height);

        // Draw simple order imbalance bars placeholder
        const bidDepth = this.book.bids.reduce((a, [, q]) => a + Number(q || 0), 0);
        const askDepth = this.book.asks.reduce((a, [, q]) => a + Number(q || 0), 0);
        const total = bidDepth + askDepth || 1;

        const bidW = (bidDepth / total) * width;
        ctx.fillStyle = "rgba(34,197,94,0.65)";
        ctx.fillRect(0, height - 24, bidW, 24);

        ctx.fillStyle = "rgba(239,68,68,0.65)";
        ctx.fillRect(bidW, height - 24, width - bidW, 24);

        ctx.fillStyle = "#e2e8f0";
        ctx.font = "12px monospace";
        ctx.fillText(`BID DEPTH: ${bidDepth.toFixed(2)} | ASK DEPTH: ${askDepth.toFixed(2)}`, 8, 16);
      }
    }
    """,
}


def write_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = textwrap.dedent(content).lstrip("\n")
    path.write_text(clean, encoding="utf-8")


def main():
    print("🚀 Generating Phase 3 upgrade files...")
    for rel, content in FILES.items():
      p = ROOT / rel
      write_file(p, content)
      print(f"✅ {rel}")
    print("\nDone.")
    print("Next:")
    print("  cd phoenix-backend && npm install && npm start")
    print("  (frontend can connect to http://localhost:8787 via Socket.io)")


if __name__ == "__main__":
    main()
