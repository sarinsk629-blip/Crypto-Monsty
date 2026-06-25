export function registerAnomalyRadarWidget(registry) {
  registry.register("anomalyRadar", (target, ctx) => {
    const host = target.querySelector("#anomaly-radar-box");
    if (!host) return { destroy() {} };

    host.innerHTML = `
      <canvas id="anomaly-canvas" width="620" height="260" style="width:100%;height:260px;border:1px solid #2b416c;border-radius:8px;background:#071121;display:block"></canvas>
      <div class="mono" id="anomaly-log" style="margin-top:8px;border:1px solid #28406a;background:#0a1322;border-radius:8px;padding:8px;max-height:130px;overflow:auto;"></div>
    `;

    const canvas = host.querySelector("#anomaly-canvas");
    const c = canvas.getContext("2d");
    const logEl = host.querySelector("#anomaly-log");

    const state = {
      series: [], // recent anomaly scores
      latest: null
    };

    function log(line) {
      const d = document.createElement("div");
      d.textContent = line;
      logEl.appendChild(d);
      logEl.scrollTop = logEl.scrollHeight;
    }

    function drawRadar(score, labels = []) {
      const w = canvas.width, h = canvas.height;
      c.clearRect(0, 0, w, h);
      c.fillStyle = "#061021";
      c.fillRect(0, 0, w, h);

      const cx = 140, cy = h / 2;
      const R = 90;

      // rings
      c.strokeStyle = "#1f3357";
      for (let i = 1; i <= 4; i++) {
        c.beginPath();
        c.arc(cx, cy, (R * i) / 4, 0, Math.PI * 2);
        c.stroke();
      }

      // axes
      const axes = ["Flow", "Latency", "Return", "Spread", "Volume"];
      for (let i = 0; i < axes.length; i++) {
        const a = (Math.PI * 2 * i) / axes.length - Math.PI / 2;
        const x = cx + Math.cos(a) * R;
        const y = cy + Math.sin(a) * R;
        c.strokeStyle = "#2a3f68";
        c.beginPath();
        c.moveTo(cx, cy);
        c.lineTo(x, y);
        c.stroke();
        c.fillStyle = "#89a6d8";
        c.font = "11px monospace";
        c.fillText(axes[i], cx + Math.cos(a) * (R + 8), cy + Math.sin(a) * (R + 8));
      }

      // polygon intensity from score
      const s = Math.max(0, Math.min(1, Number(score || 0)));
      const vals = [
        0.3 + 0.7 * s,
        0.25 + 0.8 * s,
        0.2 + 0.75 * s,
        0.35 + 0.65 * s,
        0.3 + 0.7 * s
      ];

      c.beginPath();
      for (let i = 0; i < vals.length; i++) {
        const a = (Math.PI * 2 * i) / vals.length - Math.PI / 2;
        const rr = R * vals[i];
        const x = cx + Math.cos(a) * rr;
        const y = cy + Math.sin(a) * rr;
        if (i === 0) c.moveTo(x, y); else c.lineTo(x, y);
      }
      c.closePath();

      const col = s > 0.8 ? "rgba(239,68,68,0.65)" : s > 0.5 ? "rgba(245,158,11,0.65)" : "rgba(34,197,94,0.65)";
      c.fillStyle = col;
      c.fill();
      c.strokeStyle = "#dbe9ff";
      c.stroke();

      // right panel trend
      const gx = 300, gy = 30, gw = 300, gh = 190;
      c.strokeStyle = "#1f3357";
      c.strokeRect(gx, gy, gw, gh);

      const arr = state.series;
      if (arr.length > 1) {
        c.beginPath();
        for (let i = 0; i < arr.length; i++) {
          const x = gx + (i / (arr.length - 1)) * gw;
          const y = gy + gh - arr[i] * gh;
          if (i === 0) c.moveTo(x, y); else c.lineTo(x, y);
        }
        c.strokeStyle = "#60a5fa";
        c.stroke();
      }

      c.fillStyle = "#dbe9ff";
      c.font = "12px monospace";
      c.fillText(`Anomaly Score: ${s.toFixed(4)}`, gx, gy - 8);
      c.fillText(`Labels: ${labels.join(", ") || "-"}`, 8, h - 10);
    }

    const onAnomaly = (evt) => {
      // evt expected from bridge bus
      const data = evt?.payload || evt;
      if (!data) return;

      const score = Number(data.anomalyScore || 0);
      state.latest = data;
      state.series.push(Math.max(0, Math.min(1, score)));
      if (state.series.length > 220) state.series.shift();

      drawRadar(score, data.labels || []);
      log(`[${data.symbol}] score=${score.toFixed(4)} severity=${data.severity} labels=${(data.labels || []).join("|")}`);
    };

    const u1 = ctx.shell.bus.on("bridge:anomaly", onAnomaly);

    // initial draw
    drawRadar(0, []);

    return {
      destroy() {
        u1();
      }
    };
  });
}

export default registerAnomalyRadarWidget;
