/**
 * AdvancedMicrostructureGraph (frontend placeholder)
 * Visual layers:
 * - BAI/OBI oscillators
 * - Whale delta bars
 * - Phantom-liquidity risk markers
 */
export class AdvancedMicrostructureGraph {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.data = {
      bai: [],
      obi: [],
      whaleDelta: [],
      events: []
    };
  }

  pushSample(sample) {
    this.data.bai.push(sample.bai ?? 0);
    this.data.obi.push(sample.obi ?? 0);
    this.data.whaleDelta.push(sample.whaleDelta ?? 0);
    if (sample.event) this.data.events.push(sample.event);

    const max = 800;
    if (this.data.bai.length > max) this.data.bai.shift();
    if (this.data.obi.length > max) this.data.obi.shift();
    if (this.data.whaleDelta.length > max) this.data.whaleDelta.shift();
    if (this.data.events.length > max) this.data.events.shift();
  }

  render() {
    const ctx = this.ctx;
    const w = this.canvas.width;
    const h = this.canvas.height;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#07111f";
    ctx.fillRect(0, 0, w, h);

    this._line(this.data.bai, "#22c55e", w, h, 0.30);
    this._line(this.data.obi, "#60a5fa", w, h, 0.55);
    this._bars(this.data.whaleDelta, "#f59e0b", w, h, 0.85);

    ctx.fillStyle = "#e2e8f0";
    ctx.font = "12px monospace";
    ctx.fillText("BAI (green), OBI (blue), Whale Delta (amber)", 8, 16);

    // risk markers
    for (const evt of this.data.events.slice(-40)) {
      if (evt.type !== "phantom_liquidity_risk") continue;
      const x = Math.floor(Math.random() * (w - 20)) + 10; // placeholder mapping
      ctx.fillStyle = "#ef4444";
      ctx.fillRect(x, 20, 2, h - 40);
    }
  }

  _line(arr, color, w, h, yBasePct) {
    if (!arr.length) return;
    const ctx = this.ctx;
    const yBase = h * yBasePct;
    const amp = h * 0.12;
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    for (let i = 0; i < arr.length; i++) {
      const x = (i / Math.max(1, arr.length - 1)) * w;
      const y = yBase - Math.max(-1, Math.min(1, arr[i])) * amp;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }

  _bars(arr, color, w, h, yBasePct) {
    if (!arr.length) return;
    const ctx = this.ctx;
    const yBase = h * yBasePct;
    const amp = h * 0.10;
    const n = arr.length;
    const bw = Math.max(1, Math.floor(w / n));
    ctx.fillStyle = color;
    for (let i = 0; i < n; i++) {
      const v = Math.tanh(arr[i] / 100);
      const bh = Math.abs(v) * amp;
      const x = i * bw;
      const y = v >= 0 ? yBase - bh : yBase;
      ctx.fillRect(x, y, Math.max(1, bw - 1), bh);
    }
  }
}

export default AdvancedMicrostructureGraph;
