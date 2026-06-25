import { io } from "https://cdn.socket.io/4.7.5/socket.io.esm.min.js";

const socket = io("http://localhost:8787", { transports: ["websocket"] });

const el = {
  backend: document.getElementById("status-backend"),
  redis: document.getElementById("status-redis"),
  pg: document.getElementById("status-pg"),
  rtt: document.getElementById("status-rtt"),
  consensus: document.getElementById("consensus-box"),
  risk: document.getElementById("risk-box"),
  termLog: document.getElementById("term-log"),
  termInput: document.getElementById("term-input"),
  btBtn: document.getElementById("btn-backtest"),
  btBox: document.getElementById("backtest-box"),
  health: document.getElementById("health-box"),
  canvas: document.getElementById("hft-canvas")
};

const ctx = el.canvas.getContext("2d");
const priceSeries = [];
const maxPoints = 1200;
let lastConsensus = null;
let lastRiskState = null;

function setStatus(node, text, kind = "ok") {
  node.textContent = text;
  node.style.color = kind === "ok" ? "#22c55e" : kind === "warn" ? "#f59e0b" : "#ef4444";
}

function log(msg) {
  const line = document.createElement("div");
  line.textContent = msg;
  el.termLog.appendChild(line);
  el.termLog.scrollTop = el.termLog.scrollHeight;
}

function drawGraph() {
  const w = el.canvas.width;
  const h = el.canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#061021";
  ctx.fillRect(0, 0, w, h);

  if (priceSeries.length < 2) {
    ctx.fillStyle = "#7f9ac9";
    ctx.font = "12px monospace";
    ctx.fillText("Waiting for ticks...", 10, 20);
    return;
  }

  const min = Math.min(...priceSeries);
  const max = Math.max(...priceSeries);
  const span = Math.max(1e-9, max - min);

  ctx.strokeStyle = "#60a5fa";
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  for (let i = 0; i < priceSeries.length; i++) {
    const x = (i / (priceSeries.length - 1)) * (w - 20) + 10;
    const y = h - 20 - ((priceSeries[i] - min) / span) * (h - 40);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.stroke();

  // overlay consensus hint
  if (lastConsensus) {
    const score = Number(lastConsensus.score || 0);
    ctx.fillStyle = score >= 0 ? "rgba(34,197,94,0.16)" : "rgba(239,68,68,0.16)";
    ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = "#dbeafe";
    ctx.font = "12px monospace";
    ctx.fillText(`score=${score.toFixed(4)} dir=${lastConsensus.direction} conf=${Number(lastConsensus.confidence || 0).toFixed(3)}`, 10, 16);
  }
}

function heartbeatPing() {
  const t0 = performance.now();
  socket.emit("latency:ping", { t0: Date.now() }, () => {
    const dt = performance.now() - t0;
    el.rtt.textContent = `${dt.toFixed(2)} ms`;
  });
}

async function refreshHealth() {
  try {
    const r = await fetch("http://localhost:8787/health");
    const j = await r.json();
    setStatus(el.backend, j.ok ? "ONLINE" : "DOWN", j.ok ? "ok" : "bad");

    const redisOk = !!j?.redis?.ok;
    setStatus(el.redis, redisOk ? "HOT" : "OFFLINE", redisOk ? "ok" : "warn");

    // pg status is inferred here as backend writer available in service block
    setStatus(el.pg, j?.services?.writer ? "READY" : "UNKNOWN", j?.services?.writer ? "ok" : "warn");
  } catch {
    setStatus(el.backend, "DOWN", "bad");
    setStatus(el.redis, "UNKNOWN", "warn");
    setStatus(el.pg, "UNKNOWN", "warn");
  }
}

socket.on("connect", () => {
  log("[system] connected");
  socket.emit("paper:bootstrap");
  heartbeatPing();
  setInterval(heartbeatPing, 1200);
});

socket.on("market:tick", (tick) => {
  const p = Number(tick.price || 0);
  if (Number.isFinite(p) && p > 0) {
    priceSeries.push(p);
    if (priceSeries.length > maxPoints) priceSeries.shift();
    drawGraph();
  }
});

socket.on("consensus:update", (c) => {
  if (!c) return;
  lastConsensus = c;
  el.consensus.textContent = JSON.stringify(c, null, 2);
  drawGraph();
});

socket.on("paper:state", (s) => {
  lastRiskState = s;
  el.risk.textContent = JSON.stringify(s, null, 2);
});

socket.on("exchange:health", (h) => {
  el.health.textContent = JSON.stringify(h, null, 2);
});

socket.on("paper:orderAck", (ack) => log(`[orderAck] ${JSON.stringify(ack)}`));
socket.on("paper:fill", (fill) => log(`[fill] ${JSON.stringify(fill)}`));
socket.on("paper:error", (e) => log(`[error] ${e.message}`));

el.termInput.addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  const raw = el.termInput.value.trim();
  if (!raw) return;
  el.termInput.value = "";
  log(`> ${raw}`);

  const p = raw.split(/\s+/);
  const cmd = (p[0] || "").toLowerCase();

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

  if (cmd === "/deposit") {
    socket.emit("paper:deposit", { userId: "demo", amount: Number(p[1] || 0) });
    return;
  }

  if (cmd === "/risk") {
    if (lastRiskState) el.risk.textContent = JSON.stringify(lastRiskState, null, 2);
    return;
  }

  log("[system] unknown command");
});

el.btBtn.addEventListener("click", async () => {
  try {
    const now = Date.now();
    const oneHour = 3600 * 1000;
    const body = {
      symbol: "BTCUSDT",
      fromTs: now - oneHour,
      toTs: now,
      source: "postgres",
      longThreshold: 0.15,
      shortThreshold: -0.15,
      feeBps: 2
    };
    const r = await fetch("http://localhost:8787/api/replay/backtest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    const j = await r.json();
    el.btBox.textContent = JSON.stringify(j, null, 2);
  } catch (e) {
    el.btBox.textContent = JSON.stringify({ ok: false, error: String(e) }, null, 2);
  }
});

refreshHealth();
setInterval(refreshHealth, 5000);
