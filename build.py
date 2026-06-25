#!/usr/bin/env python3
# build_phoenix_frontend.py

from pathlib import Path
import textwrap

ROOT = Path("phoenix-frontend")

FILES = {
    ".gitignore": """
        .DS_Store
        node_modules/
        dist/
        .env
        .vscode/
    """,

    "index.html": """
        <!doctype html>
        <html lang="en">
        <head>
          <meta charset="UTF-8" />
          <meta name="viewport" content="width=device-width, initial-scale=1.0" />
          <title>Crypto Monsty — Phoenix Terminal</title>
          <link rel="stylesheet" href="./css/variables.css" />
          <link rel="stylesheet" href="./css/base.css" />
          <link rel="stylesheet" href="./css/layout.css" />
          <link rel="stylesheet" href="./css/components.css" />
          <link rel="stylesheet" href="./css/pages.css" />
          <link rel="stylesheet" href="./css/aichat.css" />
          <link rel="stylesheet" href="./css/beginner.css" />
          <link rel="stylesheet" href="./css/protools.css" />
        </head>
        <body>
          <div id="app">
            <header><h1>Crypto Monsty — Phoenix Terminal v2.0</h1></header>
            <main>
              <p>Scratch build initialized.</p>
              <pre id="debug"></pre>
            </main>
          </div>
          <script type="module" src="./js/main.js"></script>
        </body>
        </html>
    """,

    # CSS
    "css/variables.css": ":root {}\n",
    "css/base.css": """
        :root { color-scheme: dark; }
        * { box-sizing: border-box; }
        body {
          margin: 0;
          font-family: Inter, system-ui, sans-serif;
          background: #0b0f14;
          color: #e6edf3;
        }
    """,
    "css/layout.css": """
        #app { max-width: 1100px; margin: 0 auto; padding: 24px; }
        header { margin-bottom: 16px; }
    """,
    "css/components.css": "pre { background: #111827; padding: 12px; border-radius: 8px; overflow: auto; }\n",
    "css/pages.css": "main { display: grid; gap: 12px; }\n",
    "css/aichat.css": "/* placeholder */\n",
    "css/beginner.css": "/* placeholder */\n",
    "css/protools.css": "/* placeholder */\n",

    # JS entry/core
    "js/main.js": """
        import { initState, applyQuantUpdate } from "./core/state.js";
        import { connectWebSocket, onQuantUpdate } from "./services/websocket.js";

        initState();

        onQuantUpdate((payload) => {
          applyQuantUpdate(payload);

          const debug = document.getElementById("debug");
          if (debug) {
            const last = payload?.points?.[payload.points.length - 1];
            debug.textContent = JSON.stringify(last || {}, null, 2);
          }
        });

        // Replace with your exchange stream URL when ready:
        // connectWebSocket("wss://your-stream-endpoint");
        console.info("[main] Phoenix Terminal booted.");
    """,

    "js/core/config.js": """
        export const APP_CONFIG = {
          appName: "Crypto Monsty",
          version: "1.0.0",
        };
    """,

    "js/core/state.js": """
        const state = {
          quant: {
            points: [],
            latest: null,
            meta: null
          }
        };

        export function initState() {
          console.info("[state] initialized");
        }

        export function applyQuantUpdate(payload) {
          const points = payload?.points ?? [];
          state.quant.points.push(...points);
          if (points.length) state.quant.latest = points[points.length - 1];
          state.quant.meta = payload?.meta ?? state.quant.meta;
        }

        export function getState() {
          return state;
        }
    """,

    "js/core/router.js": """
        export function navigateTo(path) {
          console.info("[router] navigateTo:", path);
        }

        export function initRouter() {
          console.info("[router] initialized");
        }
    """,

    "js/core/events.js": """
        export function initGlobalEvents() {
          console.info("[events] global events initialized");
        }
    """,

    # Services
    "js/services/index.js": "export * from './api.js';\nexport * from './websocket.js';\n",
    "js/services/api.js": """
        export async function fetchPrices() { return []; }
        export async function fetchAnalysis() { return {}; }
        export async function sendChat(message) { return { ok: true, echo: message }; }
    """,

    "js/services/websocket.js": """
        let ws = null;
        let quantWorker = null;
        const quantListeners = new Set();

        export function onQuantUpdate(callback) {
          quantListeners.add(callback);
          return () => quantListeners.delete(callback);
        }

        function emitQuantUpdate(payload) {
          for (const cb of quantListeners) {
            try { cb(payload); } catch (e) { console.error("[websocket] quant listener error:", e); }
          }
        }

        function ensureQuantWorker() {
          if (quantWorker) return quantWorker;

          quantWorker = new Worker(new URL("../workers/quantWorker.js", import.meta.url), { type: "module" });

          quantWorker.onmessage = (event) => {
            const { type, payload } = event.data || {};
            if (type === "quant:update") emitQuantUpdate(payload);
            else if (type === "quant:ready") console.info("[quantWorker] ready", payload);
            else if (type === "quant:reset") console.info("[quantWorker] reset");
          };

          quantWorker.onerror = (err) => {
            console.error("[quantWorker] runtime error:", err);
          };

          quantWorker.postMessage({
            type: "init",
            payload: {
              processNoise: 1e-4,
              measurementNoise: 1e-2,
              vpinWindowSize: 50,
              bucketVolumeTarget: 1000
            }
          });

          return quantWorker;
        }

        function normalizeExchangeTick(raw) {
          return {
            price: Number(raw.price ?? raw.p),
            volume: Number(raw.volume ?? raw.v ?? raw.q ?? 0),
            timestamp: raw.timestamp ?? raw.ts ?? raw.T ?? Date.now(),
            side: String(raw.side ?? raw.S ?? "buy").toLowerCase() === "sell" ? "sell" : "buy"
          };
        }

        export function connectWebSocket(url) {
          if (ws && ws.readyState <= 1) return ws;

          const worker = ensureQuantWorker();
          ws = new WebSocket(url);

          ws.onopen = () => console.info("[websocket] connected:", url);

          ws.onmessage = (event) => {
            try {
              const msg = JSON.parse(event.data);

              if (msg?.type === "tick" && msg.data) {
                worker.postMessage({ type: "ticks", payload: [normalizeExchangeTick(msg.data)] });
                return;
              }

              if (msg?.type === "ticks" && Array.isArray(msg.data)) {
                worker.postMessage({ type: "ticks", payload: msg.data.map(normalizeExchangeTick) });
              }
            } catch (err) {
              console.error("[websocket] parse/process error:", err);
            }
          };

          ws.onerror = (err) => console.error("[websocket] error:", err);
          ws.onclose = () => console.info("[websocket] disconnected");

          return ws;
        }

        export function disconnectWebSocket() {
          if (ws) ws.close();
          ws = null;

          if (quantWorker) quantWorker.terminate();
          quantWorker = null;

          quantListeners.clear();
        }

        export function resetQuantState() {
          if (quantWorker) quantWorker.postMessage({ type: "reset" });
        }
    """,

    # Worker
    "js/workers/quantWorker.js": """
        const DEFAULTS = {
          processNoise: 1e-4,
          measurementNoise: 1e-2,
          vpinWindowSize: 50,
          bucketVolumeTarget: 1000
        };

        const state = {
          initialized: false,
          x: null,
          p: 1,
          q: DEFAULTS.processNoise,
          r: DEFAULTS.measurementNoise,
          cvd: 0,
          windowSize: DEFAULTS.vpinWindowSize,
          bucketVolumeTarget: DEFAULTS.bucketVolumeTarget,
          currentBucket: { buyVol: 0, sellVol: 0, totalVol: 0 },
          bucketImbalances: []
        };

        function toTimestampMs(ts) {
          if (typeof ts === "number") return ts;
          if (ts instanceof Date) return ts.getTime();
          const parsed = Date.parse(ts);
          return Number.isNaN(parsed) ? Date.now() : parsed;
        }

        function kalmanUpdate(z) {
          if (state.x === null) {
            state.x = z;
            state.p = 1;
            return state.x;
          }

          state.p = state.p + state.q;
          const k = state.p / (state.p + state.r);
          state.x = state.x + k * (z - state.x);
          state.p = (1 - k) * state.p;

          return state.x;
        }

        function pushCompletedBucket(buyVol, sellVol, totalVol) {
          if (totalVol <= 0) return;
          const imbalanceRatio = Math.abs(buyVol - sellVol) / totalVol;
          state.bucketImbalances.push(imbalanceRatio);
          if (state.bucketImbalances.length > state.windowSize) state.bucketImbalances.shift();
        }

        function computeVPIN() {
          if (!state.bucketImbalances.length) return 0;
          const sum = state.bucketImbalances.reduce((acc, v) => acc + v, 0);
          return sum / state.bucketImbalances.length;
        }

        function updateVolumeToxicity(side, volume) {
          state.cvd += side === "buy" ? volume : -volume;

          let remaining = volume;
          while (remaining > 0) {
            const room = state.bucketVolumeTarget - state.currentBucket.totalVol;
            const take = Math.min(room, remaining);

            if (side === "buy") state.currentBucket.buyVol += take;
            else state.currentBucket.sellVol += take;

            state.currentBucket.totalVol += take;
            remaining -= take;

            if (state.currentBucket.totalVol >= state.bucketVolumeTarget) {
              pushCompletedBucket(
                state.currentBucket.buyVol,
                state.currentBucket.sellVol,
                state.currentBucket.totalVol
              );
              state.currentBucket.buyVol = 0;
              state.currentBucket.sellVol = 0;
              state.currentBucket.totalVol = 0;
            }
          }

          return { cvd: state.cvd, vpin: computeVPIN() };
        }

        function handleInit(config = {}) {
          state.q = Number.isFinite(config.processNoise) ? Math.max(1e-12, Number(config.processNoise)) : DEFAULTS.processNoise;
          state.r = Number.isFinite(config.measurementNoise) ? Math.max(1e-12, Number(config.measurementNoise)) : DEFAULTS.measurementNoise;
          state.windowSize = Number.isFinite(config.vpinWindowSize) ? Math.max(1, Math.floor(config.vpinWindowSize)) : DEFAULTS.vpinWindowSize;
          state.bucketVolumeTarget = Number.isFinite(config.bucketVolumeTarget) ? Math.max(1, Number(config.bucketVolumeTarget)) : DEFAULTS.bucketVolumeTarget;

          state.initialized = true;
          self.postMessage({
            type: "quant:ready",
            payload: {
              ok: true,
              config: {
                processNoise: state.q,
                measurementNoise: state.r,
                windowSize: state.windowSize,
                bucketVolumeTarget: state.bucketVolumeTarget
              }
            }
          });
        }

        function handleTicks(rawTicks) {
          if (!Array.isArray(rawTicks) || rawTicks.length === 0) return;

          const points = [];
          for (const raw of rawTicks) {
            const price = Number(raw?.price);
            const volume = Math.max(0, Number(raw?.volume ?? 0));
            const side = String(raw?.side ?? "buy").toLowerCase() === "sell" ? "sell" : "buy";
            const timestamp = toTimestampMs(raw?.timestamp);

            if (!Number.isFinite(price)) continue;

            const smoothedPrice = kalmanUpdate(price);
            const tox = updateVolumeToxicity(side, volume);

            points.push({
              t: timestamp,
              rawPrice: price,
              smoothedPrice,
              volume,
              side,
              cvd: tox.cvd,
              vpin: tox.vpin
            });
          }

          if (points.length) {
            self.postMessage({
              type: "quant:update",
              payload: {
                points,
                meta: {
                  windowSize: state.windowSize,
                  bucketVolumeTarget: state.bucketVolumeTarget,
                  processNoise: state.q,
                  measurementNoise: state.r
                }
              }
            });
          }
        }

        function handleReset() {
          state.x = null;
          state.p = 1;
          state.cvd = 0;
          state.currentBucket = { buyVol: 0, sellVol: 0, totalVol: 0 };
          state.bucketImbalances = [];
          self.postMessage({ type: "quant:reset", payload: { ok: true } });
        }

        self.onmessage = (event) => {
          const msg = event?.data ?? {};
          const type = msg.type;

          if (type === "init") return handleInit(msg.payload || {});
          if (!state.initialized) handleInit({});

          if (type === "ticks") return handleTicks(msg.payload);
          if (type === "reset") return handleReset();
        };
    """,
}

# Add all remaining project files as lightweight stubs
STUB_JS_FILES = [
    "js/pages/index.js", "js/pages/dashboard.js", "js/pages/markets.js", "js/pages/analysis.js",
    "js/pages/orderflow.js", "js/pages/whales.js", "js/pages/heatmap.js", "js/pages/signals.js",
    "js/pages/backtest.js", "js/pages/portfolio.js", "js/pages/ai.js", "js/pages/correlation.js",
    "js/pages/compare.js", "js/pages/watchlist.js", "js/pages/news.js", "js/pages/beginner.js",
    "js/pages/protools.js", "js/pages/journal.js", "js/pages/shortcuts.js", "js/pages/aichat.js",
    "js/pages/settings.js", "js/pages/api.js",

    "js/charts/index.js", "js/charts/marketCharts.js", "js/charts/analysisCharts.js",
    "js/charts/orderflowCharts.js", "js/charts/signalCharts.js", "js/charts/portfolioCharts.js",
    "js/charts/aiCharts.js", "js/charts/heatmapCharts.js", "js/charts/miscCharts.js",

    "js/renderers/index.js", "js/renderers/marketRenderers.js", "js/renderers/orderflowRenderers.js",
    "js/renderers/signalRenderers.js", "js/renderers/portfolioRenderers.js", "js/renderers/aiRenderers.js",
    "js/renderers/beginnerRenderers.js", "js/renderers/protoolsRenderers.js", "js/renderers/sharedRenderers.js",

    "js/engines/index.js", "js/engines/trendEngine.js", "js/engines/momentumEngine.js",
    "js/engines/volatilityEngine.js", "js/engines/signalEngine.js", "js/engines/whaleEngine.js",

    "js/indicators/index.js", "js/indicators/ema.js", "js/indicators/rsi.js",
    "js/indicators/macd.js", "js/indicators/bollinger.js", "js/indicators/atr.js",
    "js/indicators/ichimoku.js",

    "js/cache/index.js", "js/cache/marketCache.js", "js/cache/analysisCache.js",

    "js/utils/index.js", "js/utils/formatters.js", "js/utils/modal.js", "js/utils/toast.js",
]

for f in STUB_JS_FILES:
    if f.endswith("/index.js"):
        FILES[f] = "export {};\n"
    else:
        FILES[f] = "export default function noop() { return null; }\n"

def write_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = textwrap.dedent(content).lstrip("\n")
    path.write_text(cleaned, encoding="utf-8")

def main():
    print(f"Creating project at: {ROOT.resolve()}")
    for rel_path, content in FILES.items():
        abs_path = ROOT / rel_path
        write_file(abs_path, content)
        print(f"✔ {rel_path}")
    print("\nDone. Project scaffold generated successfully.")

if __name__ == "__main__":
    main()
