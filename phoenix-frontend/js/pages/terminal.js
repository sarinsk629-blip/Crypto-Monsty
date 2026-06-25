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
