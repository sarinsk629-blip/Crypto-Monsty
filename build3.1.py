#!/usr/bin/env python3
# build_phase3_1.py
# Upgrades frontend routing + main boot for advanced /terminal auto-mount.

from pathlib import Path
import textwrap

ROOT = Path(".").resolve()

FILES = {
    "phoenix-frontend/js/main.js": r"""
    import { initState, applyQuantUpdate } from "./core/state.js";
    import { connectWebSocket, onQuantUpdate } from "./services/websocket.js";
    import { initRouter } from "./core/router.js";
    import { initGlobalEvents } from "./core/events.js";

    // Optional stream URL; set window.PHOENIX_WS_URL before loading app if needed.
    const WS_URL = window.PHOENIX_WS_URL || "ws://localhost:8787";

    function boot() {
      initState();
      initGlobalEvents();
      initRouter();

      onQuantUpdate((payload) => {
        applyQuantUpdate(payload);
        const debug = document.getElementById("debug");
        if (debug) {
          const last = payload?.points?.[payload.points.length - 1];
          debug.textContent = JSON.stringify(last || {}, null, 2);
        }
      });

      // If your backend emits "market:tick" over Socket.io instead, this can remain disabled.
      // connectWebSocket(WS_URL);

      console.info("[main] Phoenix Terminal booted.");
    }

    boot();
    """,

    "phoenix-frontend/js/core/router.js": r"""
    import { renderTerminalPage } from "../pages/terminal.js";

    const routes = {
      "/": renderHome,
      "/terminal": renderTerminal
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
        <h2>Crypto Monsty — Phoenix Terminal</h2>
        <p>High-performance quant terminal initialized.</p>
        <p><a href="#/terminal">Open /terminal</a></p>
        <pre id="debug" style="background:#111827;padding:10px;border-radius:8px;overflow:auto"></pre>
      `;
      main.appendChild(card);
    }

    function renderTerminal(container = root()) {
      const main = clearMain(container);
      renderTerminalPage(main);
    }

    export function navigateTo(path) {
      const renderer = routes[path] || renderHome;
      renderer(root());
    }

    export function initRouter() {
      function parseHash() {
        const hash = window.location.hash || "#/";
        const path = hash.replace(/^#/, "") || "/";
        return path;
      }

      window.addEventListener("hashchange", () => {
        navigateTo(parseHash());
      });

      const initial = parseHash();
      navigateTo(initial);
    }
    """,

    "phoenix-frontend/js/core/events.js": r"""
    export function initGlobalEvents() {
      window.addEventListener("keydown", (e) => {
        // Quick jump to terminal: Ctrl/Cmd + K
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
          e.preventDefault();
          window.location.hash = "/terminal";
        }
      });

      console.info("[events] global events initialized");
    }
    """,

    "phoenix-frontend/js/pages/terminal.js": r"""
    import { io } from "https://cdn.socket.io/4.7.5/socket.io.esm.min.js";
    import { OrderbookWebGLRenderer } from "../renderers/orderbookWebGL.js";

    export function renderTerminalPage(container = document.body) {
      const root = document.createElement("section");
      root.id = "terminal-page";
      root.innerHTML = `
        <div style="display:grid;gap:10px;max-width:1100px">
          <h2>Phoenix Command Terminal</h2>
          <canvas id="ob-canvas" width="1000" height="260" style="width:100%;height:260px;border-radius:8px;border:1px solid #334155"></canvas>
          <div id="terminal-log" style="height:300px;overflow:auto;background:#0f172a;color:#e2e8f0;padding:10px;border-radius:8px;font-family:monospace"></div>
          <input id="terminal-input" placeholder="/buy BTCUSDT 0.01 10x | /limit buy BTCUSDT 64000 0.01 5x | /analyze orderflow" style="padding:10px;border-radius:8px;border:1px solid #334155;background:#111827;color:#e2e8f0" />
        </div>
      `;
      container.appendChild(root);

      const canvas = root.querySelector("#ob-canvas");
      const renderer = new OrderbookWebGLRenderer(canvas);
      renderer.start();

      const logEl = root.querySelector("#terminal-log");
      const inputEl = root.querySelector("#terminal-input");
      const socket = io("http://localhost:8787", { transports: ["websocket"] });

      function log(line) {
        const div = document.createElement("div");
        div.textContent = line;
        logEl.appendChild(div);
        logEl.scrollTop = logEl.scrollHeight;
      }

      const localBook = { bids: [], asks: [], cvd: [] };
      let cvdValue = 0;

      socket.on("connect", () => {
        log("[system] connected");
        socket.emit("paper:bootstrap");
        socket.emit("market:subscribe", { symbols: ["BTCUSDT", "ETHUSDT"] });
      });

      socket.on("market:tick", (tick) => {
        // lightweight synthetic depth update from ticks (placeholder)
        const p = Number(tick.price);
        const q = Number(tick.volume || 0);
        if (tick.side === "buy") {
          cvdValue += q;
          localBook.bids.unshift([p, q]);
          if (localBook.bids.length > 120) localBook.bids.pop();
        } else {
          cvdValue -= q;
          localBook.asks.unshift([p, q]);
          if (localBook.asks.length > 120) localBook.asks.pop();
        }
        localBook.cvd.push(cvdValue);
        if (localBook.cvd.length > 400) localBook.cvd.shift();

        renderer.setData(localBook);
      });

      socket.on("paper:orderAck", (ack) => log(`[orderAck] ${JSON.stringify(ack)}`));
      socket.on("paper:fill", (fill) => log(`[fill] ${JSON.stringify(fill)}`));
      socket.on("paper:state", (s) => log(`[state] equity=${s.equity ?? "n/a"} wallet=${s.walletBalance ?? "n/a"}`));
      socket.on("paper:liquidation", (evt) => log(`[LIQUIDATION] ${JSON.stringify(evt)}`));
      socket.on("ai:response", (r) => log(`[ai] ${r.insight || JSON.stringify(r)}`));
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

    function parseCommand(raw) {
      const txt = String(raw || "").trim();
      const parts = txt.split(/\s+/);
      const command = (parts[0] || "").toLowerCase();

      if (command === "/buy" || command === "/sell") {
        return {
          kind: "trade",
          type: "market",
          side: command.slice(1),
          symbol: (parts[1] || "BTCUSDT").toUpperCase(),
          qty: Number(parts[2] || 0.001),
          leverage: Number(String(parts[3] || "1x").replace(/x/i, "")) || 1
        };
      }

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

      if (command === "/analyze") {
        return { kind: "analyze", topic: (parts[1] || "orderflow").toLowerCase() };
      }

      if (command === "/deposit") {
        return { kind: "deposit", amount: Number(parts[1] || 0) };
      }

      return { kind: "ai", text: txt };
    }

    function routeCommand(cmd, socket, log) {
      if (cmd.kind === "trade") {
        socket.emit("paper:order", {
          userId: "demo",
          symbol: cmd.symbol,
          side: cmd.side,
          qty: cmd.qty,
          leverage: cmd.leverage,
          type: cmd.type,
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
        socket.emit("ai:command", { command: "/analyze", context: { topic: cmd.topic } });
        log(`[analyze] ${cmd.topic}`);
        return;
      }

      socket.emit("ai:command", { command: cmd.text, context: { source: "terminal-cli" } });
    }
    """,
}

def write_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")

def main():
    print("⚡ Applying Phase 3.1 frontend upgrade...")
    for rel, content in FILES.items():
        p = ROOT / rel
        write_file(p, content)
        print(f"✅ {rel}")
    print("\nDone. Open your app with hash route: #/terminal")

if __name__ == "__main__":
    main()
