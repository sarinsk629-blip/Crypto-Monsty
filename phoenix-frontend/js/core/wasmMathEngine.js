/**
 * wasmMathEngine.js
 * High-performance math facade designed for a Wasm module.
 * Fallback JS path included when Wasm binary is not present.
 *
 * Expected Wasm exports (if available):
 * - memory
 * - calc_depth(ptr_prices, ptr_sizes, len) -> float64
 * - calc_cvd(ptr_sides, ptr_volumes, len) -> float64
 */

export class WasmMathEngine {
  constructor({ wasmUrl = "/wasm/phoenix_math.wasm" } = {}) {
    this.wasmUrl = wasmUrl;
    this.instance = null;
    this.exports = null;
    this.ready = false;
    this.fallback = true;
  }

  async init() {
    try {
      const resp = await fetch(this.wasmUrl);
      if (!resp.ok) throw new Error(`WASM fetch failed: ${resp.status}`);
      const bytes = await resp.arrayBuffer();
      const mod = await WebAssembly.instantiate(bytes, {});
      this.instance = mod.instance;
      this.exports = this.instance.exports || {};
      this.ready = true;
      this.fallback = false;
      console.info("[WasmMathEngine] wasm initialized");
    } catch (e) {
      console.warn("[WasmMathEngine] fallback JS mode:", e?.message || e);
      this.ready = true;
      this.fallback = true;
    }
  }

  /**
   * Compute orderbook depth score:
   * weighted sum(size / distance_to_mid)
   */
  calcDepthScore({ bids = [], asks = [], mid = null } = {}) {
    if (!this.ready) throw new Error("WasmMathEngine not initialized");
    const m = mid || this._inferMid(bids, asks);
    if (!m || m <= 0) return 0;

    // fallback JS implementation
    let depth = 0;
    for (const [p, q] of bids) {
      const price = Number(p), qty = Number(q);
      if (!Number.isFinite(price) || !Number.isFinite(qty) || qty <= 0) continue;
      const d = Math.max(1e-9, Math.abs(m - price) / m);
      depth += qty / d;
    }
    for (const [p, q] of asks) {
      const price = Number(p), qty = Number(q);
      if (!Number.isFinite(price) || !Number.isFinite(qty) || qty <= 0) continue;
      const d = Math.max(1e-9, Math.abs(price - m) / m);
      depth += qty / d;
    }
    return depth;
  }

  /**
   * Compute CVD from ticks:
   * ticks: [{side:'buy'|'sell', volume:number}]
   */
  calcCVD(ticks = []) {
    if (!this.ready) throw new Error("WasmMathEngine not initialized");
    let cvd = 0;
    for (const t of ticks) {
      const side = String(t.side || "buy").toLowerCase() === "sell" ? -1 : 1;
      const vol = Number(t.volume || 0);
      if (!Number.isFinite(vol)) continue;
      cvd += side * vol;
    }
    return cvd;
  }

  /**
   * Builds a compact matrix for UI overlay:
   * rows: per-symbol metrics for depth/cvd/imbalance
   */
  buildDepthCVDMatrix(symbolSnapshots = []) {
    if (!this.ready) throw new Error("WasmMathEngine not initialized");
    return symbolSnapshots.map((s) => {
      const depth = this.calcDepthScore({ bids: s.bids || [], asks: s.asks || [], mid: s.mid });
      const cvd = this.calcCVD(s.ticks || []);
      const imbalance = this._imbalance(s.bids || [], s.asks || []);
      return {
        symbol: s.symbol,
        depthScore: depth,
        cvd,
        imbalance,
        ts: Date.now()
      };
    });
  }

  _inferMid(bids, asks) {
    const b = bids?.length ? Number(bids[0][0]) : NaN;
    const a = asks?.length ? Number(asks[0][0]) : NaN;
    if (Number.isFinite(b) && Number.isFinite(a) && a > 0 && b > 0) return (a + b) / 2;
    return Number.isFinite(b) ? b : Number.isFinite(a) ? a : null;
  }

  _imbalance(bids, asks) {
    const sb = (bids || []).slice(0, 30).reduce((acc, x) => acc + Number(x[1] || 0), 0);
    const sa = (asks || []).slice(0, 30).reduce((acc, x) => acc + Number(x[1] || 0), 0);
    const den = sb + sa;
    return den > 0 ? (sb - sa) / den : 0;
  }
}

export default WasmMathEngine;
