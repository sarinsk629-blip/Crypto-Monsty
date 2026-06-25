#!/usr/bin/env python3
# build_phase4_1_integration.py

from pathlib import Path
import textwrap

ROOT = Path(".").resolve()

FILES = {
    # =========================================================
    # Backend: server integration with multiplexer + microstructure
    # + VPIN + adaptive Z-score thresholds + guardrails
    # =========================================================
    "phoenix-backend/server.js": r"""
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
    """,

    # =========================================================
    # Backend infra helpers
    # =========================================================
    "phoenix-backend/strategyInfra.js": r"""
    export class RollingStats {
      constructor(maxN = 10000) {
        this.maxN = maxN;
        this.arr = [];
      }
      push(v) {
        const x = Number(v);
        if (!Number.isFinite(x)) return;
        this.arr.push(x);
        if (this.arr.length > this.maxN) this.arr.shift();
      }
      mean() {
        if (!this.arr.length) return 0;
        return this.arr.reduce((a, b) => a + b, 0) / this.arr.length;
      }
      std() {
        const n = this.arr.length;
        if (n < 2) return 1e-9;
        const m = this.mean();
        const varc = this.arr.reduce((a, b) => a + (b - m) ** 2, 0) / (n - 1);
        return Math.sqrt(Math.max(1e-12, varc));
      }
      zscore(v) {
        const s = this.std();
        return (Number(v) - this.mean()) / s;
      }
    }

    export class VPINCalculator {
      constructor({ bucketVolume = 250, windowBuckets = 50 } = {}) {
        this.bucketVolume = bucketVolume;
        this.windowBuckets = windowBuckets;
        this.cur = { buy: 0, sell: 0, total: 0 };
        this.buckets = []; // toxicity ratio |buy-sell|/total
      }
      ingest({ volume = 0, side = "buy" } = {}) {
        let rem = Math.max(0, Number(volume));
        const s = String(side).toLowerCase() === "sell" ? "sell" : "buy";

        while (rem > 0) {
          const room = this.bucketVolume - this.cur.total;
          const take = Math.min(room, rem);
          this.cur[s] += take;
          this.cur.total += take;
          rem -= take;

          if (this.cur.total >= this.bucketVolume) {
            const tox = Math.abs(this.cur.buy - this.cur.sell) / Math.max(1e-9, this.cur.total);
            this.buckets.push(tox);
            if (this.buckets.length > this.windowBuckets) this.buckets.shift();
            this.cur = { buy: 0, sell: 0, total: 0 };
          }
        }
      }
      value() {
        if (!this.buckets.length) return 0;
        return this.buckets.reduce((a, b) => a + b, 0) / this.buckets.length;
      }
    }

    export class CrossExchangeDelta {
      constructor({ leader = "binance", lagger = "bybit" } = {}) {
        this.leader = leader;
        this.lagger = lagger;
        this.last = new Map(); // symbol -> {ex->px}
      }

      ingest(tick) {
        const sym = String(tick.symbol || "").toUpperCase();
        if (!sym) return;
        if (!this.last.has(sym)) this.last.set(sym, {});
        const m = this.last.get(sym);
        m[tick.exchange] = Number(tick.price || 0);
      }

      snapshot(symbol) {
        const m = this.last.get(String(symbol).toUpperCase()) || {};
        const leaderPx = Number(m[this.leader] || 0);
        const laggerPx = Number(m[this.lagger] || 0);
        const delta = leaderPx && laggerPx ? (leaderPx - laggerPx) / leaderPx : 0;
        return { leaderPx, laggerPx, delta };
      }
    }

    export class ExecutionGuardrails {
      constructor({ maxMicroLagMs = 500 } = {}) {
        this.maxMicroLagMs = maxMicroLagMs;
      }

      estimateMicroLagMs(ticks = []) {
        if (!ticks.length) return 9999;
        const last = ticks[ticks.length - 1];
        return Math.max(0, Date.now() - Number(last.recvTs || last.ts || Date.now()));
      }

      dynamicSlippageBps(vpin = 0, crossDelta = 0) {
        // widen when toxicity and cross-exchange dislocation are elevated
        const base = 5; // 5 bps base
        const tox = Math.min(30, Math.abs(vpin) * 100);
        const dis = Math.min(20, Math.abs(crossDelta) * 10000);
        return Math.round(base + tox + dis);
      }

      apply({ consensus, technical, microLagMs, obi = 0, vpin = 0 }) {
        if (!consensus) return null;

        // fallback if stale micro feed
        if (microLagMs > this.maxMicroLagMs) {
          return {
            ...consensus,
            direction: technical > 0.08 ? "bullish" : technical < -0.08 ? "bearish" : "neutral",
            score: technical,
            confidence: Math.min(1, Math.abs(technical)),
            explain: {
              ...(consensus.explain || {}),
              guardrail: "microstructure_stale_fallback",
              microLagMs
            }
          };
        }

        // extra dampening on extreme toxicity
        if (Math.abs(obi) > 0.9 || vpin > 0.75) {
          return {
            ...consensus,
            score: consensus.score * 0.7,
            confidence: consensus.confidence * 0.8,
            explain: {
              ...(consensus.explain || {}),
              guardrail: "toxicity_dampener",
              obi,
              vpin
            }
          };
        }

        return consensus;
      }
    }
    """,

    # =========================================================
    # Frontend terminal upgrade (/terminal)
    # =========================================================
    "phoenix-frontend/js/pages/terminal.js": r"""
    import { io } from "https://cdn.socket.io/4.7.5/socket.io.esm.min.js";
    import { OrderbookWebGLRenderer } from "../renderers/orderbookWebGL.js";
    import MultiSymbolEngine from "../core/multiSymbolEngine.js";

    export function renderTerminalPage(container = document.body) {
      const root = document.createElement("section");
      root.id = "terminal-page";
      root.innerHTML = `
        <div style="display:grid;gap:10px;max-width:1200px">
          <h2>Phoenix Terminal</h2>
          <canvas id="ob-canvas" width="1100" height="260" style="width:100%;height:260px;border:1px solid #334155;border-radius:8px"></canvas>
          <div id="consensus" style="background:#111827;padding:10px;border-radius:8px;color:#e2e8f0;font-family:monospace"></div>
          <div id="terminal-log" style="height:280px;overflow:auto;background:#0f172a;color:#e2e8f0;padding:10px;border-radius:8px;font-family:monospace"></div>
          <input id="terminal-input" placeholder="/buy BTCUSDT 0.01 10x | /consensus BTCUSDT | /risk" style="padding:10px;border-radius:8px;border:1px solid #334155;background:#111827;color:#e2e8f0" />
        </div>
      `;
      container.appendChild(root);

      const socket = io("http://localhost:8787", { transports: ["websocket"] });
      const logEl = root.querySelector("#terminal-log");
      const consensusEl = root.querySelector("#consensus");
      const inputEl = root.querySelector("#terminal-input");
      const renderer = new OrderbookWebGLRenderer(root.querySelector("#ob-canvas"));
      renderer.start();

      const mse = new MultiSymbolEngine({
        symbols: ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        maxSymbols: 10
      });

      const localBook = { bids: [], asks: [], cvd: [] };
      let cvd = 0;

      function log(line) {
        const d = document.createElement("div");
        d.textContent = line;
        logEl.appendChild(d);
        logEl.scrollTop = logEl.scrollHeight;
      }

      socket.on("connect", () => {
        log("[system] connected");
        socket.emit("paper:bootstrap");
      });

      socket.on("market:tick", (tick) => {
        mse.ingestTick(tick);

        const p = Number(tick.price);
        const q = Number(tick.volume || 0);
        if (tick.side === "buy") {
          cvd += q;
          localBook.bids.unshift([p, q]);
          if (localBook.bids.length > 120) localBook.bids.pop();
        } else {
          cvd -= q;
          localBook.asks.unshift([p, q]);
          if (localBook.asks.length > 120) localBook.asks.pop();
        }
        localBook.cvd.push(cvd);
        if (localBook.cvd.length > 400) localBook.cvd.shift();

        renderer.setData(localBook);
      });

      socket.on("consensus:update", (c) => {
        if (!c) return;
        consensusEl.textContent = JSON.stringify(c, null, 2);
      });

      socket.on("paper:orderAck", (ack) => log(`[orderAck] ${JSON.stringify(ack)}`));
      socket.on("paper:fill", (fill) => log(`[fill] ${JSON.stringify(fill)}`));
      socket.on("paper:state", (state) => log(`[state] ${JSON.stringify(state)}`));
      socket.on("paper:error", (e) => log(`[error] ${e.message}`));

      inputEl.addEventListener("keydown", (e) => {
        if (e.key !== "Enter") return;
        const raw = inputEl.value.trim();
        if (!raw) return;
        inputEl.value = "";
        log(`> ${raw}`);

        const p = raw.split(/\s+/);
        const cmd = p[0]?.toLowerCase();

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

        if (cmd === "/risk") {
          window.location.hash = "/risk";
          return;
        }

        if (cmd === "/deposit") {
          socket.emit("paper:deposit", { userId: "demo", amount: Number(p[1] || 0) });
          return;
        }
      });
    }

    export default renderTerminalPage;
    """,

    # =========================================================
    # Frontend risk page route wiring
    # =========================================================
    "phoenix-frontend/js/core/router.js": r"""
    import { renderTerminalPage } from "../pages/terminal.js";
    import renderRiskDashboard from "../pages/riskDashboard.js";

    const routes = {
      "/": renderHome,
      "/terminal": renderTerminal,
      "/risk": renderRisk
    };

    function root() {
      return document.getElementById("app") || document.body;
    }

    function clearMain(container) {
      let main = container.querySelector("main");
      if (!main) {
        main = document.createElement("main");
        container.appendChild(main);
      }
      main.innerHTML = "";
      return main;
    }

    function renderHome(container = root()) {
      const main = clearMain(container);
      const card = document.createElement("section");
      card.innerHTML = `
        <h2>Crypto Monsty — Phoenix</h2>
        <p><a href="#/terminal">Open Terminal</a></p>
        <p><a href="#/risk">Open Risk Dashboard</a></p>
        <pre id="debug" style="background:#111827;padding:10px;border-radius:8px;overflow:auto"></pre>
      `;
      main.appendChild(card);
    }

    function renderTerminal(container = root()) {
      const main = clearMain(container);
      renderTerminalPage(main);
    }

    function renderRisk(container = root()) {
      const main = clearMain(container);
      renderRiskDashboard(main);
    }

    export function navigateTo(path) {
      const fn = routes[path] || renderHome;
      fn(root());
    }

    export function initRouter() {
      const parse = () => (window.location.hash || "#/").replace(/^#/, "") || "/";
      window.addEventListener("hashchange", () => navigateTo(parse()));
      navigateTo(parse());
    }
    """,
}

def write_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")

def main():
    print("Applying Phase 4.1 integration patch...")
    for rel, content in FILES.items():
        write_file(ROOT / rel, content)
        print(f"✔ {rel}")
    print("\nDone.\nRun backend: cd phoenix-backend && npm install && npm start")

if __name__ == "__main__":
    main()
