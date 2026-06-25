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
