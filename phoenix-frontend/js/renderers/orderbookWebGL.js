/**
 * orderbookWebGL.js
 * High-performance renderer placeholder for:
 *  - Deep order book liquidity heatmap
 *  - Cumulative Volume Delta (CVD) overlays
 *
 * This file is intentionally framework-agnostic.
 * Integrate with your chart/layout engine in Phase 4.
 */

export class OrderbookWebGLRenderer {
  constructor(canvas) {
    if (!canvas) throw new Error("OrderbookWebGLRenderer requires a canvas element");
    this.canvas = canvas;
    this.gl = canvas.getContext("webgl2", { antialias: false, alpha: true });
    this.lastFrameTs = 0;
    this.book = { bids: [], asks: [] };
    this.cvd = [];
    this.running = false;

    if (!this.gl) {
      console.warn("[orderbookWebGL] WebGL2 not supported, fallback required.");
      this.fallback2D = canvas.getContext("2d");
    }
  }

  setData({ bids = [], asks = [], cvd = [] } = {}) {
    this.book = { bids, asks };
    this.cvd = cvd;
  }

  start() {
    if (this.running) return;
    this.running = true;
    requestAnimationFrame(this._renderLoop);
  }

  stop() {
    this.running = false;
  }

  resize(width, height) {
    this.canvas.width = width;
    this.canvas.height = height;
    if (this.gl) this.gl.viewport(0, 0, width, height);
  }

  _renderLoop = (ts) => {
    if (!this.running) return;
    const dt = ts - this.lastFrameTs;
    this.lastFrameTs = ts;

    this.render(dt);
    requestAnimationFrame(this._renderLoop);
  };

  render(_dt) {
    if (this.gl) {
      // Placeholder clear; plug in shaders + VBOs in Phase 4
      this.gl.clearColor(0.04, 0.07, 0.12, 1.0);
      this.gl.clear(this.gl.COLOR_BUFFER_BIT);
      return;
    }

    // 2D fallback simple bars
    if (!this.fallback2D) return;
    const ctx = this.fallback2D;
    const { width, height } = this.canvas;
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#0b1220";
    ctx.fillRect(0, 0, width, height);

    // Draw simple order imbalance bars placeholder
    const bidDepth = this.book.bids.reduce((a, [, q]) => a + Number(q || 0), 0);
    const askDepth = this.book.asks.reduce((a, [, q]) => a + Number(q || 0), 0);
    const total = bidDepth + askDepth || 1;

    const bidW = (bidDepth / total) * width;
    ctx.fillStyle = "rgba(34,197,94,0.65)";
    ctx.fillRect(0, height - 24, bidW, 24);

    ctx.fillStyle = "rgba(239,68,68,0.65)";
    ctx.fillRect(bidW, height - 24, width - bidW, 24);

    ctx.fillStyle = "#e2e8f0";
    ctx.font = "12px monospace";
    ctx.fillText(`BID DEPTH: ${bidDepth.toFixed(2)} | ASK DEPTH: ${askDepth.toFixed(2)}`, 8, 16);
  }
}
