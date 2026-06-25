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
