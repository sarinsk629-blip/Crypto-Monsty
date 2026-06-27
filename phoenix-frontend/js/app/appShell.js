import { io } from "https://cdn.socket.io/4.7.5/socket.io.esm.min.js";
import EventBus from "./eventBus.js";
import Store from "./store.js";
import LayoutEngine from "./layoutEngine.js";
import WidgetRegistry from "./widgetRegistry.js";
import registerExecutionAlgoWidget from "../widgets/executionAlgoWidget.js";
import registerAnomalyRadarWidget from "../widgets/anomalyRadarWidget.js";

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
    this.bridgeWS = null;
    this.worker = null;
    this.priceSeries = [];
  }

  init() {
    this._renderSkeleton();
    this._wireStatusBindings();
    this._initSocket();
    this._initBridgeWS();
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
          <h1>Phoenix Institutional Cockpit V5.2</h1>
          <small>Execution & ML Tier + SOR + Anomaly Radar</small>
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

        <article class="widget w-execalgo">
          <div class="head">Execution Algo (TWAP/VWAP/Iceberg)</div>
          <div class="body" id="execution-algo-box"></div>
        </article>

        <article class="widget w-anomaly">
          <div class="head">Anomaly Radar</div>
          <div class="body" id="anomaly-radar-box"></div>
        </article>
      </section>
    `;

    const symSelect = this.rootEl.querySelector("#sym-select");
    symSelect.addEventListener("change", () => {
      this.store.set({ symbol: symSelect.value });
      console.log(`[ui] symbol -> ${symSelect.value}`);
    });

    this.rootEl.querySelector("#save-layout").addEventListener("click", () => {
      this.layout.save({ symbol: this.store.get().symbol });
      console.log("[layout] saved");
    });

    this.rootEl.querySelector("#load-layout").addEventListener("click", () => {
      const loaded = this.layout.load({ symbol: "BTCUSDT" });
      this.store.set({ symbol: loaded.symbol || "BTCUSDT" });
      symSelect.value = this.store.get().symbol;
      console.log("[layout] loaded");
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
    this.socket = io("https://phoenix-backend-6h1n.onrender.com", { transports: ["websocket"] });

    this.socket.on("connect", () => {
      console.log("[socket] connected");
      this.socket.emit("paper:bootstrap");
    });

    this.socket.on("market:tick", (tick) => {
      this.store.set({ lastTick: tick });
      this.priceSeries.push(Number(tick.price || 0));
      if (this.priceSeries.length > 1500) this.priceSeries.shift();

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

    this.socket.on("paper:orderAck", (ack) => console.log(`[orderAck] ${JSON.stringify(ack)}`));
    this.socket.on("paper:fill", (fill) => console.log(`[fill] ${JSON.stringify(fill)}`));
    this.socket.on("paper:error", (e) => console.log(`[error] ${e.message}`));
  }

  _initBridgeWS() {
    const ws = new WebSocket("wss://phoenix-bridge.onrender.com/ws/signals");
    this.bridgeWS = ws;

    ws.onopen = () => console.log("[bridge-ws] connected");
    ws.onclose = () => console.log("[bridge-ws] disconnected");
    ws.onerror = () => console.log("[bridge-ws] error");

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "execution_plan") this.bus.emit("bridge:execution_plan", msg.payload);
        if (msg.type === "execution_result") this.bus.emit("bridge:execution_result", msg.payload);

        if (msg.type === "anomaly_batch" && Array.isArray(msg.payload)) {
          for (const item of msg.payload) this.bus.emit("bridge:anomaly", item);
        }

        if (msg.type === "signal") this.bus.emit("bridge:signal", msg.payload);
      } catch {}
    };

    // keep alive
    setInterval(() => {
      try { ws.send("ping"); } catch {}
    }, 5000);
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
    // core widgets
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

      const u1 = ctx.shell.bus.on("tick", draw);
      const u2 = ctx.shell.bus.on("indicators", draw);
      draw();
      return { destroy() { u1(); u2(); } };
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
      const u = ctx.shell.store.subscribe(upd);
      upd();
      return { destroy() { u(); } };
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

    // external widgets (Phase 5.2)
    registerExecutionAlgoWidget(this.registry);
    registerAnomalyRadarWidget(this.registry);
  }

  _mountWidgets() {
    const dock = this.rootEl.querySelector("#dock");
    this.registry.mount("graph", dock, { shell: this });
    this.registry.mount("feed", dock, { shell: this });
    this.registry.mount("risk", dock, { shell: this });
    this.registry.mount("inspector", dock, { shell: this });
    this.registry.mount("executionAlgo", dock, { shell: this });
    this.registry.mount("anomalyRadar", dock, { shell: this });
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
        const r = await fetch("https://phoenix-bridge.onrender.com" + "/signal", {
          method: "POST",
          headers: { "Content-Type": "application/json", "x-api-key": "dev-bridge-key" },
          body: JSON.stringify({
            symbol, score, confidence: Math.min(1, Math.abs(score)),
            explain: { source: "manual-cli" }, diagnostics: { manual: true }
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
        const r = await fetch("https://phoenix-backend-6h1n.onrender.com/api/replay/backtest", {
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
        const b = await fetch("https://phoenix-backend-6h1n.onrender.com/health").then((r) => r.json());
        this.store.set({ backendHealth: b });
      } catch {
        this.store.set({ backendHealth: { ok: false } });
      }

      try {
        const br = await fetch("https://phoenix-bridge.onrender.com" + "/health").then((r) => r.json());
        this.store.set({ bridgeHealth: br });
      } catch {
        this.store.set({ bridgeHealth: { ok: false } });
      }

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


// Force Auto-Ignition
setTimeout(() => { window.cockpit = new AppShell(); console.log('🚀 Cockpit UI Online'); }, 100);
