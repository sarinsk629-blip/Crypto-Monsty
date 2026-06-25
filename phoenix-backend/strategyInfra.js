export class RollingStats {
  constructor(maxN = 10000) {
    this.maxN = maxN;
    this.arr = [];
  }
  push(v) {
    const x = Number(v);
    if (!Number.isFinite(x)) return;
    this.arr.push(x);
    if (this.arr.length > this.maxN) this.arr.shift();
  }
  mean() {
    if (!this.arr.length) return 0;
    return this.arr.reduce((a, b) => a + b, 0) / this.arr.length;
  }
  std() {
    const n = this.arr.length;
    if (n < 2) return 1e-9;
    const m = this.mean();
    const varc = this.arr.reduce((a, b) => a + (b - m) ** 2, 0) / (n - 1);
    return Math.sqrt(Math.max(1e-12, varc));
  }
  zscore(v) {
    const s = this.std();
    return (Number(v) - this.mean()) / s;
  }
}

export class VPINCalculator {
  constructor({ bucketVolume = 250, windowBuckets = 50 } = {}) {
    this.bucketVolume = bucketVolume;
    this.windowBuckets = windowBuckets;
    this.cur = { buy: 0, sell: 0, total: 0 };
    this.buckets = []; // toxicity ratio |buy-sell|/total
  }
  ingest({ volume = 0, side = "buy" } = {}) {
    let rem = Math.max(0, Number(volume));
    const s = String(side).toLowerCase() === "sell" ? "sell" : "buy";

    while (rem > 0) {
      const room = this.bucketVolume - this.cur.total;
      const take = Math.min(room, rem);
      this.cur[s] += take;
      this.cur.total += take;
      rem -= take;

      if (this.cur.total >= this.bucketVolume) {
        const tox = Math.abs(this.cur.buy - this.cur.sell) / Math.max(1e-9, this.cur.total);
        this.buckets.push(tox);
        if (this.buckets.length > this.windowBuckets) this.buckets.shift();
        this.cur = { buy: 0, sell: 0, total: 0 };
      }
    }
  }
  value() {
    if (!this.buckets.length) return 0;
    return this.buckets.reduce((a, b) => a + b, 0) / this.buckets.length;
  }
}

export class CrossExchangeDelta {
  constructor({ leader = "binance", lagger = "bybit" } = {}) {
    this.leader = leader;
    this.lagger = lagger;
    this.last = new Map(); // symbol -> {ex->px}
  }

  ingest(tick) {
    const sym = String(tick.symbol || "").toUpperCase();
    if (!sym) return;
    if (!this.last.has(sym)) this.last.set(sym, {});
    const m = this.last.get(sym);
    m[tick.exchange] = Number(tick.price || 0);
  }

  snapshot(symbol) {
    const m = this.last.get(String(symbol).toUpperCase()) || {};
    const leaderPx = Number(m[this.leader] || 0);
    const laggerPx = Number(m[this.lagger] || 0);
    const delta = leaderPx && laggerPx ? (leaderPx - laggerPx) / leaderPx : 0;
    return { leaderPx, laggerPx, delta };
  }
}

export class ExecutionGuardrails {
  constructor({ maxMicroLagMs = 500 } = {}) {
    this.maxMicroLagMs = maxMicroLagMs;
  }

  estimateMicroLagMs(ticks = []) {
    if (!ticks.length) return 9999;
    const last = ticks[ticks.length - 1];
    return Math.max(0, Date.now() - Number(last.recvTs || last.ts || Date.now()));
  }

  dynamicSlippageBps(vpin = 0, crossDelta = 0) {
    // widen when toxicity and cross-exchange dislocation are elevated
    const base = 5; // 5 bps base
    const tox = Math.min(30, Math.abs(vpin) * 100);
    const dis = Math.min(20, Math.abs(crossDelta) * 10000);
    return Math.round(base + tox + dis);
  }

  apply({ consensus, technical, microLagMs, obi = 0, vpin = 0 }) {
    if (!consensus) return null;

    // fallback if stale micro feed
    if (microLagMs > this.maxMicroLagMs) {
      return {
        ...consensus,
        direction: technical > 0.08 ? "bullish" : technical < -0.08 ? "bearish" : "neutral",
        score: technical,
        confidence: Math.min(1, Math.abs(technical)),
        explain: {
          ...(consensus.explain || {}),
          guardrail: "microstructure_stale_fallback",
          microLagMs
        }
      };
    }

    // extra dampening on extreme toxicity
    if (Math.abs(obi) > 0.9 || vpin > 0.75) {
      return {
        ...consensus,
        score: consensus.score * 0.7,
        confidence: consensus.confidence * 0.8,
        explain: {
          ...(consensus.explain || {}),
          guardrail: "toxicity_dampener",
          obi,
          vpin
        }
      };
    }

    return consensus;
  }
}
