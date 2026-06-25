export function registerExecutionAlgoWidget(registry) {
  registry.register("executionAlgo", (target, ctx) => {
    const container = target.querySelector("#execution-algo-box");
    if (!container) return { destroy() {} };

    container.innerHTML = `
      <div class="controls" style="margin-bottom:8px;display:flex;gap:8px;flex-wrap:wrap;">
        <select id="ex-algo">
          <option value="twap">TWAP</option>
          <option value="vwap">VWAP</option>
          <option value="iceberg">Iceberg</option>
        </select>
        <input id="ex-symbol" type="text" value="BTCUSDT" style="width:100px" />
        <input id="ex-side" type="text" value="buy" style="width:80px" />
        <input id="ex-qty" type="number" step="0.0001" value="0.05" style="width:100px" />
        <input id="ex-duration" type="number" value="60" style="width:90px" />
        <input id="ex-slices" type="number" value="12" style="width:80px" />
        <button id="ex-plan">Plan</button>
        <button id="ex-run">Execute</button>
      </div>
      <div class="mono" id="ex-log" style="border:1px solid #28406a;background:#0a1322;border-radius:8px;padding:8px;max-height:240px;overflow:auto;"></div>
    `;

    const q = (id) => container.querySelector(id);
    const logEl = q("#ex-log");

    const log = (line) => {
      const d = document.createElement("div");
      d.textContent = line;
      logEl.appendChild(d);
      logEl.scrollTop = logEl.scrollHeight;
    };

    const payload = () => ({
      symbol: (q("#ex-symbol").value || "BTCUSDT").toUpperCase(),
      side: (q("#ex-side").value || "buy").toLowerCase() === "sell" ? "sell" : "buy",
      qty: Number(q("#ex-qty").value || 0),
      algo: (q("#ex-algo").value || "twap").toLowerCase(),
      duration_sec: Number(q("#ex-duration").value || 60),
      slices: Number(q("#ex-slices").value || 12),
      display_qty: Number(q("#ex-qty").value || 0) * 0.08,
      min_clip: Number(q("#ex-qty").value || 0) * 0.05,
      max_clip: Number(q("#ex-qty").value || 0) * 0.15,
      slippage_bps: 3.0
    });

    async function callBridge(path, body) {
      const r = await fetch(`http://localhost:8899${path}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-api-key": "dev-bridge-key"
        },
        body: JSON.stringify(body)
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j?.detail || j?.error || "Bridge error");
      return j;
    }

    q("#ex-plan").addEventListener("click", async () => {
      try {
        const j = await callBridge("/execution/route", payload());
        log(`[plan] order_id=${j.plan.order_id} slices=${j.plan.slices.length} algo=${j.plan.algo}`);
        ctx.shell.bus.emit("execution:plan", j.plan);
      } catch (e) {
        log(`[plan-error] ${e.message || e}`);
      }
    });

    q("#ex-run").addEventListener("click", async () => {
      try {
        const st = ctx.shell.store.get();
        const mark = Number(st.lastTick?.price || 0) || undefined;
        const j = await callBridge("/execution/execute", { ...payload(), mark_price: mark });
        log(`[exec] order_id=${j.result.order_id} status=${j.result.status} filled=${j.result.filled_qty} avg=${j.result.avg_exec_price}`);
        for (const f of j.result.fills.slice(0, 30)) {
          log(`  [fill] ${f.exchange} qty=${f.qty} px=${f.metadata.exec_price.toFixed(4)} ts=${f.ts}`);
        }
        ctx.shell.bus.emit("execution:result", j.result);
      } catch (e) {
        log(`[exec-error] ${e.message || e}`);
      }
    });

    // listen bridge ws events (proxied via app shell bus)
    const u1 = ctx.shell.bus.on("bridge:execution_plan", (p) => {
      log(`[bridge-plan] ${p.order_id} slices=${p.slices?.length || 0}`);
    });
    const u2 = ctx.shell.bus.on("bridge:execution_result", (r) => {
      log(`[bridge-result] ${r.order_id} filled=${r.filled_qty} avg=${r.avg_exec_price}`);
    });

    return {
      destroy() {
        u1();
        u2();
      }
    };
  });
}

export default registerExecutionAlgoWidget;
