/**
 * LatencyMonitor
 * - Tracks frontend <-> backend roundtrip (Socket.io ack timing)
 * - Tracks backend-reported exchange feed latency when available
 * - Uses high-resolution clock (performance.now)
 */
export class LatencyMonitor {
  constructor({
    socket,
    sampleSize = 200,
    pingIntervalMs = 1000
  } = {}) {
    if (!socket) throw new Error("LatencyMonitor requires a socket instance");
    this.socket = socket;
    this.sampleSize = sampleSize;
    this.pingIntervalMs = pingIntervalMs;

    this.samples = {
      rttMs: [],
      exchangeLatencyMs: {
        binance: [],
        bybit: [],
        hyperliquid: []
      }
    };

    this.ui = null;
    this.timer = null;
    this.boundTickListener = this._onTick.bind(this);
  }

  mount(container) {
    const el = document.createElement("div");
    el.style.cssText = "background:#111827;border:1px solid #1f2937;border-radius:8px;padding:10px;color:#e2e8f0;font-family:monospace";
    el.innerHTML = `
      <div style="font-weight:700;margin-bottom:8px">Latency Monitor</div>
      <div id="lm-rtt">RTT: -</div>
      <div id="lm-ex">EX Latency (ms): binance=- bybit=- hyperliquid=-</div>
      <div id="lm-jitter">Jitter: -</div>
    `;
    container.appendChild(el);
    this.ui = {
      root: el,
      rtt: el.querySelector("#lm-rtt"),
      ex: el.querySelector("#lm-ex"),
      jitter: el.querySelector("#lm-jitter")
    };
  }

  start() {
    this.stop();
    this.socket.on("market:tick", this.boundTickListener);

    this.timer = setInterval(() => {
      this._pingBackend();
      this._render();
    }, this.pingIntervalMs);
  }

  stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.socket.off("market:tick", this.boundTickListener);
  }

  _onTick(tick) {
    const ex = tick?.exchange;
    const latency = Number(tick?.latencyMs);
    if (!ex || !Number.isFinite(latency)) return;
    if (!this.samples.exchangeLatencyMs[ex]) this.samples.exchangeLatencyMs[ex] = [];
    this._push(this.samples.exchangeLatencyMs[ex], latency);
  }

  _pingBackend() {
    const t0 = performance.now();
    // Socket.io ack callback for roundtrip timing
    this.socket.emit("latency:ping", { t0: Date.now() }, () => {
      const dt = performance.now() - t0;
      this._push(this.samples.rttMs, dt);
    });

    // If backend has no explicit latency:ping handler, fallback noop heartbeat
    setTimeout(() => {
      if (this.samples.rttMs.length === 0) {
        // pseudo-sample to avoid empty UI in early boot
        this._push(this.samples.rttMs, 0);
      }
    }, 250);
  }

  _push(arr, v) {
    arr.push(v);
    if (arr.length > this.sampleSize) arr.shift();
  }

  _avg(arr) {
    if (!arr.length) return null;
    return arr.reduce((a, b) => a + b, 0) / arr.length;
  }

  _std(arr) {
    if (arr.length < 2) return null;
    const mean = this._avg(arr);
    const varc = arr.reduce((a, b) => a + (b - mean) ** 2, 0) / (arr.length - 1);
    return Math.sqrt(varc);
  }

  _render() {
    if (!this.ui) return;
    const rttAvg = this._avg(this.samples.rttMs);
    const rttJitter = this._std(this.samples.rttMs);

    const b = this._avg(this.samples.exchangeLatencyMs.binance || []);
    const y = this._avg(this.samples.exchangeLatencyMs.bybit || []);
    const h = this._avg(this.samples.exchangeLatencyMs.hyperliquid || []);

    this.ui.rtt.textContent = `RTT: ${rttAvg == null ? "-" : rttAvg.toFixed(3)} ms`;
    this.ui.ex.textContent = `EX Latency (ms): binance=${b == null ? "-" : b.toFixed(3)} bybit=${y == null ? "-" : y.toFixed(3)} hyperliquid=${h == null ? "-" : h.toFixed(3)}`;
    this.ui.jitter.textContent = `Jitter: ${rttJitter == null ? "-" : rttJitter.toFixed(3)} ms`;
  }
}

export default LatencyMonitor;
