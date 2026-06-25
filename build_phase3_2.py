#!/usr/bin/env python3
# build_phase3_2_omniscient_matrix.py
#
# Generates Phase 3.2 "Omniscient Matrix" infrastructure:
# 1) phoenix-backend/exchangeMultiplexer.js
# 2) phoenix-frontend/js/core/multiSymbolEngine.js
# 3) phoenix-frontend/js/pages/riskDashboard.js
# 4) phoenix-frontend/js/components/latencyMonitor.js
# 5) phoenix-frontend/js/renderers/hftGraphWebGL.js

from pathlib import Path
import textwrap

ROOT = Path(".").resolve()

FILES = {
    "phoenix-backend/exchangeMultiplexer.js": r"""
    import { EventEmitter } from "events";

    /**
     * ExchangeMultiplexer
     * - Connects to Binance + Bybit + Hyperliquid public streams
     * - Normalizes all incoming trade ticks into a standard Phoenix Tick Object
     * - Emits "tick" events and optionally broadcasts over Socket.io
     *
     * Phoenix Tick Object:
     * {
     *   exchange: "binance" | "bybit" | "hyperliquid",
     *   symbol: "BTCUSDT",
     *   price: number,
     *   volume: number,
     *   side: "buy" | "sell",
     *   ts: number,            // event timestamp in ms
     *   recvTs: number,        // local receive timestamp in ms
     *   latencyMs: number|null,
     *   raw: object
     * }
     */
    export class ExchangeMultiplexer extends EventEmitter {
      constructor({
        io = null,
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        reconnectMs = 3000,
        heartbeatMs = 15000
      } = {}) {
        super();
        this.io = io;
        this.symbols = symbols;
        this.reconnectMs = reconnectMs;
        this.heartbeatMs = heartbeatMs;
        this.sockets = new Map();
        this.latency = {
          binance: { lastMessageTs: null, stale: false },
          bybit: { lastMessageTs: null, stale: false },
          hyperliquid: { lastMessageTs: null, stale: false }
        };
        this._hbTimer = null;
      }

      start() {
        this._connectBinance();
        this._connectBybit();
        this._connectHyperliquid();
        this._startHeartbeat();
      }

      stop() {
        for (const [, ws] of this.sockets.entries()) {
          try { ws.close(); } catch {}
        }
        this.sockets.clear();
        if (this._hbTimer) clearInterval(this._hbTimer);
        this._hbTimer = null;
      }

      setSocketIO(io) {
        this.io = io;
      }

      _broadcast(type, payload) {
        this.emit(type, payload);
        if (this.io) this.io.emit(type, payload);
      }

      _startHeartbeat() {
        if (this._hbTimer) clearInterval(this._hbTimer);
        this._hbTimer = setInterval(() => {
          const now = Date.now();
          for (const [ex, v] of Object.entries(this.latency)) {
            const stale = !v.lastMessageTs || (now - v.lastMessageTs > this.heartbeatMs * 2);
            v.stale = stale;
          }
          this._broadcast("exchange:health", {
            ts: now,
            exchanges: this.latency
          });
        }, this.heartbeatMs);
      }

      _connectBinance() {
        // Example stream: aggTrade
        const streams = this.symbols.map(s => `${s.toLowerCase()}@aggTrade`).join("/");
        const url = `wss://stream.binance.com:9443/stream?streams=${streams}`;
        this._connectWS("binance", url, null, (msg) => {
          const data = msg?.data || msg;
          if (!data || data.e !== "aggTrade") return null;
          return this._normalizeBinance(data);
        });
      }

      _connectBybit() {
        const url = "wss://stream.bybit.com/v5/public/linear";
        const subscribeMsg = {
          op: "subscribe",
          args: this.symbols.map(s => `publicTrade.${s}`)
        };
        this._connectWS("bybit", url, subscribeMsg, (msg) => {
          if (!msg?.topic?.startsWith("publicTrade.")) return null;
          const trades = Array.isArray(msg.data) ? msg.data : [];
          if (!trades.length) return null;
          // emit all trades
          return trades.map(t => this._normalizeBybit(t)).filter(Boolean);
        });
      }

      _connectHyperliquid() {
        // Hyperliquid public websocket
        const url = "wss://api.hyperliquid.xyz/ws";
        // NOTE: Hyperliquid symbols may differ from USDT contracts naming.
        const subscribe = this.symbols.map((s) => ({
          method: "subscribe",
          subscription: { type: "trades", coin: this._toHyperliquidCoin(s) }
        }));

        this._connectWS("hyperliquid", url, subscribe, (msg) => {
          // Typical trade payload: channel "trades", data [{px,sz,side,time,coin}, ...]
          if (msg?.channel !== "trades") return null;
          const arr = Array.isArray(msg?.data) ? msg.data : [];
          if (!arr.length) return null;
          return arr.map(t => this._normalizeHyperliquid(t)).filter(Boolean);
        });
      }

      _connectWS(exchange, url, subscribeMsg, parser) {
        let WebSocketImpl = globalThis.WebSocket;
        if (!WebSocketImpl) {
          // Node runtime without global WebSocket, require ws dynamically
          // (kept optional to avoid hard dependency for script generation)
          // eslint-disable-next-line no-eval
          const req = eval("require");
          WebSocketImpl = req("ws");
        }

        const openConnection = () => {
          const ws = new WebSocketImpl(url);
          this.sockets.set(exchange, ws);

          ws.onopen = () => {
            if (subscribeMsg) {
              if (Array.isArray(subscribeMsg)) {
                for (const m of subscribeMsg) ws.send(JSON.stringify(m));
              } else {
                ws.send(JSON.stringify(subscribeMsg));
              }
            }
            this._broadcast("exchange:status", { exchange, status: "connected", ts: Date.now() });
          };

          ws.onmessage = (evt) => {
            const recvTs = Date.now();
            this.latency[exchange].lastMessageTs = recvTs;
            this.latency[exchange].stale = false;

            let msg = null;
            try {
              msg = JSON.parse(evt.data);
            } catch {
              return;
            }

            const out = parser(msg);
            if (!out) return;

            if (Array.isArray(out)) {
              for (const tick of out) this._broadcastTick(tick);
            } else {
              this._broadcastTick(out);
            }
          };

          ws.onerror = (err) => {
            this._broadcast("exchange:error", { exchange, error: String(err?.message || err), ts: Date.now() });
          };

          ws.onclose = () => {
            this._broadcast("exchange:status", { exchange, status: "disconnected", ts: Date.now() });
            setTimeout(openConnection, this.reconnectMs);
          };
        };

        openConnection();
      }

      _broadcastTick(tick) {
        if (!tick) return;
        this._broadcast("market:tick", tick);
        this._broadcast("market:tick:normalized", tick);
      }

      _normalizeBinance(d) {
        // aggTrade:
        // p price, q qty, T trade time, m true if buyer is market maker
        const ts = Number(d.T || Date.now());
        const recvTs = Date.now();
        return {
          exchange: "binance",
          symbol: String(d.s || "").toUpperCase(),
          price: Number(d.p),
          volume: Number(d.q),
          side: d.m ? "sell" : "buy", // maker buyer => taker sell
          ts,
          recvTs,
          latencyMs: Number.isFinite(ts) ? Math.max(0, recvTs - ts) : null,
          raw: d
        };
      }

      _normalizeBybit(t) {
        // v5 trade fields commonly: s symbol, p price, v size, T time, S side(Buy/Sell)
        const ts = Number(t.T || t.ts || Date.now());
        const recvTs = Date.now();
        return {
          exchange: "bybit",
          symbol: String(t.s || "").toUpperCase(),
          price: Number(t.p),
          volume: Number(t.v || t.q || 0),
          side: String(t.S || t.side || "Buy").toLowerCase() === "sell" ? "sell" : "buy",
          ts,
          recvTs,
          latencyMs: Number.isFinite(ts) ? Math.max(0, recvTs - ts) : null,
          raw: t
        };
      }

      _normalizeHyperliquid(t) {
        // typical fields: coin, px, sz, side, time
        const ts = Number(t.time || Date.now());
        const recvTs = Date.now();
        const symbol = this._fromHyperliquidCoin(String(t.coin || ""));
        return {
          exchange: "hyperliquid",
          symbol,
          price: Number(t.px),
          volume: Number(t.sz),
          side: String(t.side || "buy").toLowerCase() === "sell" ? "sell" : "buy",
          ts,
          recvTs,
          latencyMs: Number.isFinite(ts) ? Math.max(0, recvTs - ts) : null,
          raw: t
        };
      }

      _toHyperliquidCoin(symbol) {
        // BTCUSDT -> BTC (heuristic)
        return symbol.replace(/USDT$/i, "").toUpperCase();
      }

      _fromHyperliquidCoin(coin) {
        // BTC -> BTCUSDT (heuristic)
        return `${coin.toUpperCase()}USDT`;
      }
    }

    export default ExchangeMultiplexer;
    """,

    "phoenix-frontend/js/core/multiSymbolEngine.js": r"""
    /**
     * MultiSymbolEngine
     * - Tracks orderbook snapshots, CVD, and a 70-indicator matrix per symbol
     * - Designed to process updates in chunks and avoid long main-thread blocks
     * - Can ingest ticks from Socket.io or worker bridge
     */
    export class MultiSymbolEngine {
      constructor({
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        maxSymbols = 10,
        maxTicksPerSymbol = 5000,
        maxBookLevels = 100,
        indicatorCount = 70
      } = {}) {
        this.maxSymbols = maxSymbols;
        this.maxTicksPerSymbol = maxTicksPerSymbol;
        this.maxBookLevels = maxBookLevels;
        this.indicatorCount = indicatorCount;

        this.state = {
          symbols: new Map(),
          updatedAt: Date.now()
        };

        symbols.slice(0, maxSymbols).forEach((s) => this.ensureSymbol(s));

        this.listeners = new Set();
        this.queue = [];
        this.processing = false;
        this.batchSize = 200;
      }

      ensureSymbol(symbol) {
        const sym = String(symbol).toUpperCase();
        if (this.state.symbols.has(sym)) return this.state.symbols.get(sym);
        if (this.state.symbols.size >= this.maxSymbols) return null;

        const node = {
          symbol: sym,
          ticks: [],
          orderbook: { bids: [], asks: [], ts: 0 },
          cvd: 0,
          indicatorMatrix: this._initIndicatorMatrix(),
          metrics: {
            lastPrice: null,
            lastVolume: 0,
            tickCount: 0,
            buyVolume: 0,
            sellVolume: 0,
            vwapNum: 0,
            vwapDen: 0,
            vwap: null
          },
          updatedAt: Date.now()
        };
        this.state.symbols.set(sym, node);
        return node;
      }

      _initIndicatorMatrix() {
        const m = new Float64Array(this.indicatorCount);
        for (let i = 0; i < this.indicatorCount; i++) m[i] = 0;
        return m;
      }

      onUpdate(cb) {
        this.listeners.add(cb);
        return () => this.listeners.delete(cb);
      }

      emit(payload) {
        for (const cb of this.listeners) {
          try { cb(payload); } catch (e) { console.error("[MultiSymbolEngine] listener error", e); }
        }
      }

      ingestTick(tick) {
        this.queue.push(tick);
        if (!this.processing) this._drainQueue();
      }

      ingestTicks(ticks = []) {
        if (!Array.isArray(ticks) || !ticks.length) return;
        this.queue.push(...ticks);
        if (!this.processing) this._drainQueue();
      }

      _schedule(fn) {
        if (typeof requestIdleCallback === "function") {
          requestIdleCallback(fn, { timeout: 8 });
        } else {
          setTimeout(fn, 0);
        }
      }

      _drainQueue() {
        this.processing = true;
        this._schedule(() => {
          const start = performance.now ? performance.now() : Date.now();
          let n = 0;

          while (this.queue.length && n < this.batchSize) {
            const tick = this.queue.shift();
            this._applyTick(tick);
            n++;
          }

          this.state.updatedAt = Date.now();
          this.emit({ type: "multiSymbol:update", updatedAt: this.state.updatedAt, processed: n });

          const elapsed = (performance.now ? performance.now() : Date.now()) - start;
          // dynamic batch tuning
          if (elapsed > 8) this.batchSize = Math.max(50, this.batchSize - 20);
          else this.batchSize = Math.min(500, this.batchSize + 10);

          if (this.queue.length) this._drainQueue();
          else this.processing = false;
        });
      }

      _applyTick(tick) {
        if (!tick || !tick.symbol) return;
        const sym = String(tick.symbol).toUpperCase();
        const s = this.ensureSymbol(sym);
        if (!s) return;

        const price = Number(tick.price);
        const volume = Math.max(0, Number(tick.volume || 0));
        const side = String(tick.side || "buy").toLowerCase() === "sell" ? "sell" : "buy";
        const ts = Number(tick.ts || tick.timestamp || Date.now());

        if (!Number.isFinite(price) || price <= 0) return;

        // ticks ring buffer
        s.ticks.push({ ts, price, volume, side, exchange: tick.exchange || "unknown" });
        if (s.ticks.length > this.maxTicksPerSymbol) s.ticks.shift();

        // CVD
        s.cvd += side === "buy" ? volume : -volume;

        // basic metrics
        s.metrics.lastPrice = price;
        s.metrics.lastVolume = volume;
        s.metrics.tickCount += 1;
        if (side === "buy") s.metrics.buyVolume += volume;
        else s.metrics.sellVolume += volume;
        s.metrics.vwapNum += price * volume;
        s.metrics.vwapDen += volume;
        s.metrics.vwap = s.metrics.vwapDen > 0 ? s.metrics.vwapNum / s.metrics.vwapDen : price;

        // update indicator matrix
        this._updateIndicators(s, price, volume, ts);

        s.updatedAt = Date.now();
      }

      _updateIndicators(s, price, volume, ts) {
        // Placeholder 70-indicator matrix logic:
        // indices: [0..9] trend/momentum, [10..19] vol, [20..29] flow, [30..69] custom ensemble
        const m = s.indicatorMatrix;
        const prev = m[0] || price;

        // cheap EMA chain
        const alphaFast = 0.2;
        const alphaSlow = 0.05;
        m[0] = alphaFast * price + (1 - alphaFast) * m[0];   // fast ema
        m[1] = alphaSlow * price + (1 - alphaSlow) * m[1];   // slow ema
        m[2] = m[0] - m[1];                                  // macd-like delta
        m[3] = price - prev;                                 // tick return proxy
        m[4] = Math.abs(m[3]);                               // abs return
        m[5] = 0.95 * m[5] + 0.05 * (volume || 0);          // smoothed volume
        m[6] = s.cvd;                                        // cvd
        m[7] = s.metrics.vwap || price;                      // vwap
        m[8] = price - m[7];                                 // price-vwap spread
        m[9] = (s.metrics.buyVolume + 1) / (s.metrics.sellVolume + 1); // flow ratio

        // synthetic rolling volatility
        m[10] = 0.9 * m[10] + 0.1 * (m[4] ** 2);
        m[11] = Math.sqrt(Math.max(0, m[10]));

        // fill remaining ensemble slots with deterministic transforms
        for (let i = 12; i < this.indicatorCount; i++) {
          const seed = (i * 2654435761) % 9973;
          const x = (price * (seed % 17 + 1) + volume * (seed % 7 + 1) + ts % 1000) * 1e-6;
          m[i] = 0.97 * m[i] + 0.03 * Math.tanh(x + m[(i - 1) % 12] * 0.01);
        }
      }

      updateOrderbook(symbol, { bids = [], asks = [], ts = Date.now() }) {
        const s = this.ensureSymbol(symbol);
        if (!s) return;
        s.orderbook.bids = bids.slice(0, this.maxBookLevels);
        s.orderbook.asks = asks.slice(0, this.maxBookLevels);
        s.orderbook.ts = ts;
        s.updatedAt = Date.now();
      }

      getSymbolState(symbol) {
        return this.state.symbols.get(String(symbol).toUpperCase()) || null;
      }

      getSnapshot() {
        return {
          updatedAt: this.state.updatedAt,
          symbols: Array.from(this.state.symbols.values()).map((s) => ({
            symbol: s.symbol,
            cvd: s.cvd,
            metrics: s.metrics,
            updatedAt: s.updatedAt,
            indicatorMatrix: Array.from(s.indicatorMatrix)
          }))
        };
      }
    }

    export default MultiSymbolEngine;
    """,

    "phoenix-frontend/js/pages/riskDashboard.js": r"""
    import { io } from "https://cdn.socket.io/4.7.5/socket.io.esm.min.js";
    import { LatencyMonitor } from "../components/latencyMonitor.js";

    /**
     * riskDashboard.js
     * Live paper-trading risk UI:
     * - Cross/isolated margin health
     * - Leverage exposure heatmap
     * - Realized/Unrealized PnL
     * - Liquidation distance
     */
    export function renderRiskDashboard(container = document.body) {
      const root = document.createElement("section");
      root.id = "risk-dashboard";
      root.innerHTML = `
        <div style="display:grid;gap:12px;max-width:1200px">
          <h2>Risk Dashboard</h2>
          <div id="risk-summary" style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px"></div>
          <canvas id="risk-heatmap" width="1100" height="220" style="width:100%;height:220px;border:1px solid #334155;border-radius:8px"></canvas>
          <div id="positions" style="background:#0f172a;color:#e2e8f0;padding:10px;border-radius:8px;font-family:monospace;max-height:320px;overflow:auto"></div>
          <div id="latency-host"></div>
        </div>
      `;
      container.appendChild(root);

      const socket = io("http://localhost:8787", { transports: ["websocket"] });
      const summaryEl = root.querySelector("#risk-summary");
      const positionsEl = root.querySelector("#positions");
      const heatmap = root.querySelector("#risk-heatmap");
      const ctx = heatmap.getContext("2d");

      const lm = new LatencyMonitor({ socket, sampleSize: 120 });
      lm.mount(root.querySelector("#latency-host"));
      lm.start();

      let latestState = null;

      function card(title, value, color = "#22c55e") {
        return `
          <div style="background:#111827;border:1px solid #1f2937;padding:10px;border-radius:8px">
            <div style="font-size:12px;color:#94a3b8">${title}</div>
            <div style="font-size:18px;color:${color};font-weight:700">${value}</div>
          </div>
        `;
      }

      function formatNum(n) {
        if (n == null || Number.isNaN(Number(n))) return "-";
        return Number(n).toLocaleString(undefined, { maximumFractionDigits: 4 });
      }

      function renderSummary(user) {
        const wallet = Number(user.walletBalance || 0);
        const equity = Number(user.equity || 0);
        const upnl = Number(user.unrealizedPnl || 0);
        const rpnl = Number(user.realizedPnl || 0);
        const mm = Number(user.maintenanceMargin || 0);

        const marginHealth = mm > 0 ? equity / mm : Infinity;
        const color = marginHealth < 1.2 ? "#ef4444" : marginHealth < 2 ? "#f59e0b" : "#22c55e";

        summaryEl.innerHTML = [
          card("Wallet Balance", formatNum(wallet), "#60a5fa"),
          card("Equity", formatNum(equity), equity >= wallet ? "#22c55e" : "#f59e0b"),
          card("PnL (Unrealized / Realized)", `${formatNum(upnl)} / ${formatNum(rpnl)}`, upnl >= 0 ? "#22c55e" : "#ef4444"),
          card("Margin Health (Equity/MM)", marginHealth === Infinity ? "∞" : formatNum(marginHealth), color),
        ].join("");
      }

      function liquidationDistance(pos, mark) {
        const qty = Number(pos.qty || 0);
        if (!qty) return null;
        const entry = Number(pos.entryPrice || 0);
        if (!entry || !mark) return null;

        // rough estimate: distance from entry to mark with sign-aware interpretation
        const dist = qty > 0 ? (mark - entry) / entry : (entry - mark) / entry;
        return dist * 100;
      }

      function renderPositions(user, marks = {}) {
        const rows = (user.positions || []).map((p) => {
          const mark = Number(marks[p.symbol] || p.entryPrice || 0);
          const liqDist = liquidationDistance(p, mark);
          const lev = Number(p.leverage || 1);
          const marginType = p.marginType || "cross";
          return {
            symbol: p.symbol,
            side: Number(p.qty || 0) >= 0 ? "LONG" : "SHORT",
            qty: Number(p.qty || 0),
            entry: Number(p.entryPrice || 0),
            mark,
            lev,
            marginType,
            upnl: Number(p.unrealizedPnl || 0),
            liqDist
          };
        });

        positionsEl.textContent = rows.length
          ? rows.map((r) =>
              `${r.symbol.padEnd(10)} ${r.side.padEnd(6)} qty=${r.qty.toFixed(4)} lev=${r.lev}x ${r.marginType.padEnd(8)} entry=${r.entry.toFixed(4)} mark=${r.mark.toFixed(4)} uPnL=${r.upnl.toFixed(4)} liqDist=${r.liqDist == null ? "-" : r.liqDist.toFixed(3) + "%"}`
            ).join("\n")
          : "No positions";
      }

      function renderHeatmap(user) {
        const w = heatmap.width;
        const h = heatmap.height;
        ctx.clearRect(0, 0, w, h);
        ctx.fillStyle = "#0b1220";
        ctx.fillRect(0, 0, w, h);

        const positions = user.positions || [];
        if (!positions.length) {
          ctx.fillStyle = "#94a3b8";
          ctx.fillText("No active exposure", 10, 20);
          return;
        }

        const barW = Math.max(24, Math.floor((w - 20) / positions.length));
        let x = 10;
        for (const p of positions) {
          const lev = Math.min(100, Math.max(1, Number(p.leverage || 1)));
          const intensity = Math.min(1, lev / 25);
          const col = Number(p.qty || 0) >= 0
            ? `rgba(34,197,94,${0.25 + intensity * 0.75})`
            : `rgba(239,68,68,${0.25 + intensity * 0.75})`;
          const barH = Math.max(10, (h - 40) * Math.min(1, lev / 50));

          ctx.fillStyle = col;
          ctx.fillRect(x, h - barH - 20, barW - 8, barH);

          ctx.fillStyle = "#e2e8f0";
          ctx.font = "11px monospace";
          ctx.fillText(p.symbol || "", x, h - 6);
          ctx.fillText(`${lev.toFixed(1)}x`, x, h - barH - 24);

          x += barW;
        }
      }

      function pickUserState(statePayload) {
        // backend may return single user object or multi-user container
        if (!statePayload) return null;
        if (statePayload.userId) return { user: statePayload, marks: {} };
        if (Array.isArray(statePayload.users)) {
          const user = statePayload.users.find((u) => u.userId === "demo") || statePayload.users[0] || null;
          return { user, marks: statePayload.marks || {} };
        }
        return null;
      }

      socket.on("connect", () => {
        socket.emit("paper:bootstrap");
      });

      socket.on("paper:state", (payload) => {
        const parsed = pickUserState(payload);
        if (!parsed || !parsed.user) return;
        latestState = parsed;
        renderSummary(parsed.user);
        renderPositions(parsed.user, parsed.marks);
        renderHeatmap(parsed.user);
      });

      socket.on("paper:liquidation", (evt) => {
        const line = document.createElement("div");
        line.style.color = "#ef4444";
        line.textContent = `[LIQ] ${JSON.stringify(evt)}`;
        positionsEl.prepend(line);
      });

      return {
        destroy() {
          lm.stop();
          socket.close();
          root.remove();
        }
      };
    }

    export default renderRiskDashboard;
    """,

    "phoenix-frontend/js/components/latencyMonitor.js": r"""
    /**
     * LatencyMonitor
     * - Tracks frontend <-> backend roundtrip (Socket.io ack timing)
     * - Tracks backend-reported exchange feed latency when available
     * - Uses high-resolution clock (performance.now)
     */
    export class LatencyMonitor {
      constructor({
        socket,
        sampleSize = 200,
        pingIntervalMs = 1000
      } = {}) {
        if (!socket) throw new Error("LatencyMonitor requires a socket instance");
        this.socket = socket;
        this.sampleSize = sampleSize;
        this.pingIntervalMs = pingIntervalMs;

        this.samples = {
          rttMs: [],
          exchangeLatencyMs: {
            binance: [],
            bybit: [],
            hyperliquid: []
          }
        };

        this.ui = null;
        this.timer = null;
        this.boundTickListener = this._onTick.bind(this);
      }

      mount(container) {
        const el = document.createElement("div");
        el.style.cssText = "background:#111827;border:1px solid #1f2937;border-radius:8px;padding:10px;color:#e2e8f0;font-family:monospace";
        el.innerHTML = `
          <div style="font-weight:700;margin-bottom:8px">Latency Monitor</div>
          <div id="lm-rtt">RTT: -</div>
          <div id="lm-ex">EX Latency (ms): binance=- bybit=- hyperliquid=-</div>
          <div id="lm-jitter">Jitter: -</div>
        `;
        container.appendChild(el);
        this.ui = {
          root: el,
          rtt: el.querySelector("#lm-rtt"),
          ex: el.querySelector("#lm-ex"),
          jitter: el.querySelector("#lm-jitter")
        };
      }

      start() {
        this.stop();
        this.socket.on("market:tick", this.boundTickListener);

        this.timer = setInterval(() => {
          this._pingBackend();
          this._render();
        }, this.pingIntervalMs);
      }

      stop() {
        if (this.timer) clearInterval(this.timer);
        this.timer = null;
        this.socket.off("market:tick", this.boundTickListener);
      }

      _onTick(tick) {
        const ex = tick?.exchange;
        const latency = Number(tick?.latencyMs);
        if (!ex || !Number.isFinite(latency)) return;
        if (!this.samples.exchangeLatencyMs[ex]) this.samples.exchangeLatencyMs[ex] = [];
        this._push(this.samples.exchangeLatencyMs[ex], latency);
      }

      _pingBackend() {
        const t0 = performance.now();
        // Socket.io ack callback for roundtrip timing
        this.socket.emit("latency:ping", { t0: Date.now() }, () => {
          const dt = performance.now() - t0;
          this._push(this.samples.rttMs, dt);
        });

        // If backend has no explicit latency:ping handler, fallback noop heartbeat
        setTimeout(() => {
          if (this.samples.rttMs.length === 0) {
            // pseudo-sample to avoid empty UI in early boot
            this._push(this.samples.rttMs, 0);
          }
        }, 250);
      }

      _push(arr, v) {
        arr.push(v);
        if (arr.length > this.sampleSize) arr.shift();
      }

      _avg(arr) {
        if (!arr.length) return null;
        return arr.reduce((a, b) => a + b, 0) / arr.length;
      }

      _std(arr) {
        if (arr.length < 2) return null;
        const mean = this._avg(arr);
        const varc = arr.reduce((a, b) => a + (b - mean) ** 2, 0) / (arr.length - 1);
        return Math.sqrt(varc);
      }

      _render() {
        if (!this.ui) return;
        const rttAvg = this._avg(this.samples.rttMs);
        const rttJitter = this._std(this.samples.rttMs);

        const b = this._avg(this.samples.exchangeLatencyMs.binance || []);
        const y = this._avg(this.samples.exchangeLatencyMs.bybit || []);
        const h = this._avg(this.samples.exchangeLatencyMs.hyperliquid || []);

        this.ui.rtt.textContent = `RTT: ${rttAvg == null ? "-" : rttAvg.toFixed(3)} ms`;
        this.ui.ex.textContent = `EX Latency (ms): binance=${b == null ? "-" : b.toFixed(3)} bybit=${y == null ? "-" : y.toFixed(3)} hyperliquid=${h == null ? "-" : h.toFixed(3)}`;
        this.ui.jitter.textContent = `Jitter: ${rttJitter == null ? "-" : rttJitter.toFixed(3)} ms`;
      }
    }

    export default LatencyMonitor;
    """,

    "phoenix-frontend/js/renderers/hftGraphWebGL.js": r"""
    /**
     * HFTGraphWebGL (placeholder engine)
     * Goal:
     * - Render millions of points at ~60 FPS (candles, spikes, overlays)
     * - Provide architecture for VBO batching, LOD downsampling, and overlays
     *
     * This is a high-performance placeholder with:
     * - WebGL2 init
     * - GPU buffers scaffold
     * - Frame loop with adaptive quality
     * - Fallback 2D mode
     */
    export class HFTGraphWebGL {
      constructor(canvas, { maxPoints = 1_000_000 } = {}) {
        if (!canvas) throw new Error("HFTGraphWebGL requires a canvas");
        this.canvas = canvas;
        this.maxPoints = maxPoints;

        this.gl = canvas.getContext("webgl2", {
          antialias: false,
          alpha: false,
          depth: false,
          stencil: false,
          preserveDrawingBuffer: false,
          powerPreference: "high-performance"
        });

        this.ctx2d = null;
        if (!this.gl) this.ctx2d = canvas.getContext("2d");

        this.running = false;
        this.lastTs = 0;
        this.fps = 0;
        this.frameTimes = [];

        this.data = {
          candles: new Float32Array(0),   // [x,o,h,l,c,vol] repeated
          spikes: new Float32Array(0),    // [x,y,magnitude]
          overlays: new Map()             // name -> Float32Array
        };

        this.quality = {
          lod: 1, // 1 = full, 2 = half, 4 = quarter...
          targetFps: 60
        };

        this.gpu = {
          initialized: false,
          program: null,
          vao: null,
          buffers: new Map()
        };

        if (this.gl) this._initGL();
      }

      _initGL() {
        const gl = this.gl;
        // Minimal shader pair
        const vsSrc = `#version 300 es
        precision highp float;
        layout(location=0) in vec2 aPos;
        void main() {
          gl_Position = vec4(aPos, 0.0, 1.0);
          gl_PointSize = 1.0;
        }`;
        const fsSrc = `#version 300 es
        precision highp float;
        out vec4 fragColor;
        void main() {
          fragColor = vec4(0.10, 0.85, 0.95, 1.0);
        }`;

        const vs = this._compile(gl.VERTEX_SHADER, vsSrc);
        const fs = this._compile(gl.FRAGMENT_SHADER, fsSrc);
        const prog = gl.createProgram();
        gl.attachShader(prog, vs);
        gl.attachShader(prog, fs);
        gl.linkProgram(prog);
        if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
          console.error("[HFTGraphWebGL] Shader link failed:", gl.getProgramInfoLog(prog));
          return;
        }

        const vao = gl.createVertexArray();
        gl.bindVertexArray(vao);

        const pointBuffer = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, pointBuffer);
        gl.bufferData(gl.ARRAY_BUFFER, 8 * 1024 * 1024, gl.DYNAMIC_DRAW); // pre-alloc
        gl.enableVertexAttribArray(0);
        gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);

        this.gpu.program = prog;
        this.gpu.vao = vao;
        this.gpu.buffers.set("points", pointBuffer);
        this.gpu.initialized = true;
      }

      _compile(type, src) {
        const gl = this.gl;
        const sh = gl.createShader(type);
        gl.shaderSource(sh, src);
        gl.compileShader(sh);
        if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
          console.error("[HFTGraphWebGL] Shader compile failed:", gl.getShaderInfoLog(sh));
        }
        return sh;
      }

      resize(width, height, dpr = (window.devicePixelRatio || 1)) {
        const w = Math.max(1, Math.floor(width * dpr));
        const h = Math.max(1, Math.floor(height * dpr));
        this.canvas.width = w;
        this.canvas.height = h;
        this.canvas.style.width = `${width}px`;
        this.canvas.style.height = `${height}px`;
        if (this.gl) this.gl.viewport(0, 0, w, h);
      }

      setCandles(floatArray) {
        this.data.candles = floatArray instanceof Float32Array ? floatArray : new Float32Array(floatArray || []);
      }

      setSpikes(floatArray) {
        this.data.spikes = floatArray instanceof Float32Array ? floatArray : new Float32Array(floatArray || []);
      }

      setOverlay(name, floatArray) {
        this.data.overlays.set(name, floatArray instanceof Float32Array ? floatArray : new Float32Array(floatArray || []));
      }

      start() {
        if (this.running) return;
        this.running = true;
        requestAnimationFrame(this._loop);
      }

      stop() {
        this.running = false;
      }

      _loop = (ts) => {
        if (!this.running) return;

        const dt = this.lastTs ? (ts - this.lastTs) : 16.7;
        this.lastTs = ts;

        this._trackFps(dt);
        this._adaptiveQuality();

        this.render(dt);
        requestAnimationFrame(this._loop);
      };

      _trackFps(dt) {
        const fps = dt > 0 ? 1000 / dt : 0;
        this.frameTimes.push(fps);
        if (this.frameTimes.length > 120) this.frameTimes.shift();
        this.fps = this.frameTimes.reduce((a, b) => a + b, 0) / this.frameTimes.length;
      }

      _adaptiveQuality() {
        // simple adaptive LOD
        if (this.fps < 45) this.quality.lod = Math.min(8, this.quality.lod * 2);
        else if (this.fps > 58) this.quality.lod = Math.max(1, this.quality.lod / 2);
      }

      render(_dt) {
        if (this.gl && this.gpu.initialized) {
          this._renderGL();
        } else if (this.ctx2d) {
          this._render2D();
        }
      }

      _renderGL() {
        const gl = this.gl;
        gl.clearColor(0.02, 0.04, 0.08, 1.0);
        gl.clear(gl.COLOR_BUFFER_BIT);

        const candles = this.data.candles;
        if (!candles.length) return;

        // Convert candle x/close into points for placeholder draw
        const step = this.quality.lod;
        const n = Math.min(this.maxPoints, Math.floor(candles.length / 6));
        const pts = new Float32Array(Math.ceil(n / step) * 2);

        let j = 0;
        for (let i = 0; i < n; i += step) {
          const base = i * 6;
          const x = candles[base + 0]; // expected normalized [-1..1]
          const c = candles[base + 4]; // expected normalized [-1..1]
          pts[j++] = x;
          pts[j++] = c;
        }

        gl.useProgram(this.gpu.program);
        gl.bindVertexArray(this.gpu.vao);

        const buf = this.gpu.buffers.get("points");
        gl.bindBuffer(gl.ARRAY_BUFFER, buf);
        gl.bufferSubData(gl.ARRAY_BUFFER, 0, pts);

        gl.drawArrays(gl.POINTS, 0, pts.length / 2);
      }

      _render2D() {
        const ctx = this.ctx2d;
        const w = this.canvas.width;
        const h = this.canvas.height;
        ctx.clearRect(0, 0, w, h);
        ctx.fillStyle = "#05101f";
        ctx.fillRect(0, 0, w, h);

        const candles = this.data.candles;
        const n = Math.floor(candles.length / 6);
        if (!n) return;

        ctx.strokeStyle = "#33d1ff";
        ctx.lineWidth = 1;
        ctx.beginPath();

        const step = this.quality.lod;
        for (let i = 0; i < n; i += step) {
          const base = i * 6;
          const xn = candles[base + 0];
          const cn = candles[base + 4];
          const x = (xn * 0.5 + 0.5) * w;
          const y = (1 - (cn * 0.5 + 0.5)) * h;
          if (i === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.stroke();

        ctx.fillStyle = "#94a3b8";
        ctx.font = "12px monospace";
        ctx.fillText(`FPS: ${this.fps.toFixed(1)} | LOD: ${this.quality.lod}x`, 8, 16);
      }
    }

    export default HFTGraphWebGL;
    """,
}


def write_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")


def main():
    print("Generating Phase 3.2 Omniscient Matrix files...")
    for rel_path, content in FILES.items():
        out = ROOT / rel_path
        write_file(out, content)
        print(f"✔ {rel_path}")
    print("\nDone. Phase 3.2 scaffold generated successfully.")


if __name__ == "__main__":
    main()
