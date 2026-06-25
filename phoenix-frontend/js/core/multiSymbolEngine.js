/**
 * MultiSymbolEngine
 * - Tracks orderbook snapshots, CVD, and a 70-indicator matrix per symbol
 * - Designed to process updates in chunks and avoid long main-thread blocks
 * - Can ingest ticks from Socket.io or worker bridge
 */
export class MultiSymbolEngine {
  constructor({
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    maxSymbols = 10,
    maxTicksPerSymbol = 5000,
    maxBookLevels = 100,
    indicatorCount = 70
  } = {}) {
    this.maxSymbols = maxSymbols;
    this.maxTicksPerSymbol = maxTicksPerSymbol;
    this.maxBookLevels = maxBookLevels;
    this.indicatorCount = indicatorCount;

    this.state = {
      symbols: new Map(),
      updatedAt: Date.now()
    };

    symbols.slice(0, maxSymbols).forEach((s) => this.ensureSymbol(s));

    this.listeners = new Set();
    this.queue = [];
    this.processing = false;
    this.batchSize = 200;
  }

  ensureSymbol(symbol) {
    const sym = String(symbol).toUpperCase();
    if (this.state.symbols.has(sym)) return this.state.symbols.get(sym);
    if (this.state.symbols.size >= this.maxSymbols) return null;

    const node = {
      symbol: sym,
      ticks: [],
      orderbook: { bids: [], asks: [], ts: 0 },
      cvd: 0,
      indicatorMatrix: this._initIndicatorMatrix(),
      metrics: {
        lastPrice: null,
        lastVolume: 0,
        tickCount: 0,
        buyVolume: 0,
        sellVolume: 0,
        vwapNum: 0,
        vwapDen: 0,
        vwap: null
      },
      updatedAt: Date.now()
    };
    this.state.symbols.set(sym, node);
    return node;
  }

  _initIndicatorMatrix() {
    const m = new Float64Array(this.indicatorCount);
    for (let i = 0; i < this.indicatorCount; i++) m[i] = 0;
    return m;
  }

  onUpdate(cb) {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  }

  emit(payload) {
    for (const cb of this.listeners) {
      try { cb(payload); } catch (e) { console.error("[MultiSymbolEngine] listener error", e); }
    }
  }

  ingestTick(tick) {
    this.queue.push(tick);
    if (!this.processing) this._drainQueue();
  }

  ingestTicks(ticks = []) {
    if (!Array.isArray(ticks) || !ticks.length) return;
    this.queue.push(...ticks);
    if (!this.processing) this._drainQueue();
  }

  _schedule(fn) {
    if (typeof requestIdleCallback === "function") {
      requestIdleCallback(fn, { timeout: 8 });
    } else {
      setTimeout(fn, 0);
    }
  }

  _drainQueue() {
    this.processing = true;
    this._schedule(() => {
      const start = performance.now ? performance.now() : Date.now();
      let n = 0;

      while (this.queue.length && n < this.batchSize) {
        const tick = this.queue.shift();
        this._applyTick(tick);
        n++;
      }

      this.state.updatedAt = Date.now();
      this.emit({ type: "multiSymbol:update", updatedAt: this.state.updatedAt, processed: n });

      const elapsed = (performance.now ? performance.now() : Date.now()) - start;
      // dynamic batch tuning
      if (elapsed > 8) this.batchSize = Math.max(50, this.batchSize - 20);
      else this.batchSize = Math.min(500, this.batchSize + 10);

      if (this.queue.length) this._drainQueue();
      else this.processing = false;
    });
  }

  _applyTick(tick) {
    if (!tick || !tick.symbol) return;
    const sym = String(tick.symbol).toUpperCase();
    const s = this.ensureSymbol(sym);
    if (!s) return;

    const price = Number(tick.price);
    const volume = Math.max(0, Number(tick.volume || 0));
    const side = String(tick.side || "buy").toLowerCase() === "sell" ? "sell" : "buy";
    const ts = Number(tick.ts || tick.timestamp || Date.now());

    if (!Number.isFinite(price) || price <= 0) return;

    // ticks ring buffer
    s.ticks.push({ ts, price, volume, side, exchange: tick.exchange || "unknown" });
    if (s.ticks.length > this.maxTicksPerSymbol) s.ticks.shift();

    // CVD
    s.cvd += side === "buy" ? volume : -volume;

    // basic metrics
    s.metrics.lastPrice = price;
    s.metrics.lastVolume = volume;
    s.metrics.tickCount += 1;
    if (side === "buy") s.metrics.buyVolume += volume;
    else s.metrics.sellVolume += volume;
    s.metrics.vwapNum += price * volume;
    s.metrics.vwapDen += volume;
    s.metrics.vwap = s.metrics.vwapDen > 0 ? s.metrics.vwapNum / s.metrics.vwapDen : price;

    // update indicator matrix
    this._updateIndicators(s, price, volume, ts);

    s.updatedAt = Date.now();
  }

  _updateIndicators(s, price, volume, ts) {
    // Placeholder 70-indicator matrix logic:
    // indices: [0..9] trend/momentum, [10..19] vol, [20..29] flow, [30..69] custom ensemble
    const m = s.indicatorMatrix;
    const prev = m[0] || price;

    // cheap EMA chain
    const alphaFast = 0.2;
    const alphaSlow = 0.05;
    m[0] = alphaFast * price + (1 - alphaFast) * m[0];   // fast ema
    m[1] = alphaSlow * price + (1 - alphaSlow) * m[1];   // slow ema
    m[2] = m[0] - m[1];                                  // macd-like delta
    m[3] = price - prev;                                 // tick return proxy
    m[4] = Math.abs(m[3]);                               // abs return
    m[5] = 0.95 * m[5] + 0.05 * (volume || 0);          // smoothed volume
    m[6] = s.cvd;                                        // cvd
    m[7] = s.metrics.vwap || price;                      // vwap
    m[8] = price - m[7];                                 // price-vwap spread
    m[9] = (s.metrics.buyVolume + 1) / (s.metrics.sellVolume + 1); // flow ratio

    // synthetic rolling volatility
    m[10] = 0.9 * m[10] + 0.1 * (m[4] ** 2);
    m[11] = Math.sqrt(Math.max(0, m[10]));

    // fill remaining ensemble slots with deterministic transforms
    for (let i = 12; i < this.indicatorCount; i++) {
      const seed = (i * 2654435761) % 9973;
      const x = (price * (seed % 17 + 1) + volume * (seed % 7 + 1) + ts % 1000) * 1e-6;
      m[i] = 0.97 * m[i] + 0.03 * Math.tanh(x + m[(i - 1) % 12] * 0.01);
    }
  }

  updateOrderbook(symbol, { bids = [], asks = [], ts = Date.now() }) {
    const s = this.ensureSymbol(symbol);
    if (!s) return;
    s.orderbook.bids = bids.slice(0, this.maxBookLevels);
    s.orderbook.asks = asks.slice(0, this.maxBookLevels);
    s.orderbook.ts = ts;
    s.updatedAt = Date.now();
  }

  getSymbolState(symbol) {
    return this.state.symbols.get(String(symbol).toUpperCase()) || null;
  }

  getSnapshot() {
    return {
      updatedAt: this.state.updatedAt,
      symbols: Array.from(this.state.symbols.values()).map((s) => ({
        symbol: s.symbol,
        cvd: s.cvd,
        metrics: s.metrics,
        updatedAt: s.updatedAt,
        indicatorMatrix: Array.from(s.indicatorMatrix)
      }))
    };
  }
}

export default MultiSymbolEngine;
