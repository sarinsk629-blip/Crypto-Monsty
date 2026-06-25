import AdvancedMicrostructureTracker from "./advancedMicrostructureTracker.js";

/**
 * InstitutionalOrderBook
 * - Holds L2 state
 * - Streams microstructure metrics (BAI/OBI/CVD-whale split)
 * - Surfaces phantom-liquidity risk events
 */
export class InstitutionalOrderBook {
  constructor({ io = null } = {}) {
    this.io = io;
    this.books = new Map(); // symbol -> {bids, asks, ts}
    this.micro = new AdvancedMicrostructureTracker({});
  }

  setSocketIO(io) { this.io = io; }

  updateSnapshot(symbol, { bids = [], asks = [], ts = Date.now() }) {
    const sym = String(symbol).toUpperCase();
    this.books.set(sym, { bids, asks, ts });

    const metrics = this.micro.ingestOrderBook({ symbol: sym, bids, asks, ts });
    this._emit("orderbook:metrics", { symbol: sym, metrics, ts });

    const recent = this.micro.getRecentEvents(10).filter(e => e.symbol === sym);
    for (const evt of recent) {
      if (evt.ts >= ts - 1000) this._emit("orderbook:riskEvent", evt);
    }
  }

  ingestTrade(trade) {
    const t = { ...trade, symbol: String(trade.symbol || "").toUpperCase() };
    const metrics = this.micro.ingestTrade(t);
    this._emit("flow:metrics", { symbol: t.symbol, metrics, ts: t.ts || Date.now() });
  }

  _emit(event, payload) {
    if (this.io) this.io.emit(event, payload);
  }

  getMetrics(symbol) {
    return this.micro.getSymbolMetrics(String(symbol).toUpperCase());
  }

  snapshot() {
    return {
      ts: Date.now(),
      books: Array.from(this.books.entries()).map(([symbol, b]) => ({ symbol, ...b })),
      micro: this.micro.snapshot()
    };
  }
}

export default InstitutionalOrderBook;
