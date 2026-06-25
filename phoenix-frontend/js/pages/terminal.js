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
