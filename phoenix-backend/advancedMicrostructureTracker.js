/**
 * AdvancedMicrostructureTracker
 * - Detects fast-disappearing large depth walls as "phantom-liquidity risk events"
 * - Computes BAI/OBI + whale-vs-retail flow decomposition
 * - Designed for surveillance/risk awareness and signal robustness
 */
export class AdvancedMicrostructureTracker {
  constructor({
    depthPct = 0.01,              // 1% depth band
    spoofMinSizeBTC = 50,         // large wall threshold (BTC-equivalent units as provided)
    spoofMaxLifetimeMs = 3000,    // appears then vanishes quickly
    whaleMinTradeBTC = 25,        // isolated block trade threshold
    maxWallRecords = 4000,
  } = {}) {
    this.depthPct = depthPct;
    this.spoofMinSizeBTC = spoofMinSizeBTC;
    this.spoofMaxLifetimeMs = spoofMaxLifetimeMs;
    this.whaleMinTradeBTC = whaleMinTradeBTC;
    this.maxWallRecords = maxWallRecords;

    // key: symbol|side|priceBucket -> wall object
    this.liveWalls = new Map();

    this.stateBySymbol = new Map(); // symbol -> metrics
    this.events = [];               // surveillance events
  }

  _ensureSymbol(symbol) {
    if (!this.stateBySymbol.has(symbol)) {
      this.stateBySymbol.set(symbol, {
        symbol,
        bai: 0,
        obi: 0,
        retailCvd: 0,
        whaleDelta: 0,
        whaleBuyVol: 0,
        whaleSellVol: 0,
        lastMid: null,
        updatedAt: Date.now()
      });
    }
    return this.stateBySymbol.get(symbol);
  }

  /**
   * Update from full L2 snapshot or aggregated top-N depth arrays
   * bids/asks entries: [price, size]
   */
  ingestOrderBook({ symbol, bids = [], asks = [], ts = Date.now() }) {
    const s = this._ensureSymbol(symbol);

    if (!bids.length || !asks.length) return s;
    const bestBid = Number(bids[0][0]);
    const bestAsk = Number(asks[0][0]);
    if (!Number.isFinite(bestBid) || !Number.isFinite(bestAsk) || bestBid <= 0 || bestAsk <= 0) return s;

    const mid = (bestBid + bestAsk) / 2;
    s.lastMid = mid;

    // depth band for BAI: 1% from mid
    const low = mid * (1 - this.depthPct);
    const high = mid * (1 + this.depthPct);

    let bidDepth = 0, askDepth = 0;
    for (const [p, q] of bids) {
      const price = Number(p), size = Number(q);
      if (!Number.isFinite(price) || !Number.isFinite(size)) continue;
      if (price >= low) bidDepth += Math.max(0, size);
    }
    for (const [p, q] of asks) {
      const price = Number(p), size = Number(q);
      if (!Number.isFinite(price) || !Number.isFinite(size)) continue;
      if (price <= high) askDepth += Math.max(0, size);
    }

    // Bid-Ask Imbalance (BAI)
    const baiDen = bidDepth + askDepth;
    s.bai = baiDen > 0 ? (bidDepth - askDepth) / baiDen : 0;

    // Order Book Imbalance (OBI) from top levels weighted by proximity
    s.obi = this._computeOBI(mid, bids, asks);

    // Spoof-like wall surveillance
    this._scanWalls(symbol, mid, bids, asks, ts);

    s.updatedAt = ts;
    return s;
  }

  _computeOBI(mid, bids, asks) {
    const weighted = (sideRows, side) => {
      let acc = 0;
      for (const [p, q] of sideRows.slice(0, 40)) {
        const price = Number(p), qty = Number(q);
        if (!Number.isFinite(price) || !Number.isFinite(qty) || qty <= 0) continue;
        const dist = Math.abs(price - mid) / mid;
        const w = 1 / (1 + dist * 250); // proximity emphasis
        acc += qty * w * (side === "bid" ? 1 : -1);
      }
      return acc;
    };
    const v = weighted(bids, "bid") + weighted(asks, "ask");
    const norm = Math.max(1e-9, Math.abs(weighted(bids, "bid")) + Math.abs(weighted(asks, "ask")));
    return v / norm;
  }

  /**
   * Ingest executed trades for whale-vs-retail decomposition
   * trade: {symbol, price, volume, side, ts}
   */
  ingestTrade(trade) {
    const { symbol } = trade;
    const s = this._ensureSymbol(symbol);
    const vol = Math.max(0, Number(trade.volume || 0));
    const side = String(trade.side || "buy").toLowerCase() === "sell" ? "sell" : "buy";

    if (vol >= this.whaleMinTradeBTC) {
      // whale block flow
      if (side === "buy") {
        s.whaleDelta += vol;
        s.whaleBuyVol += vol;
      } else {
        s.whaleDelta -= vol;
        s.whaleSellVol += vol;
      }
    } else {
      // retail CVD proxy
      s.retailCvd += side === "buy" ? vol : -vol;
    }

    s.updatedAt = Number(trade.ts || Date.now());
    return s;
  }

  _scanWalls(symbol, mid, bids, asks, ts) {
    const now = Number(ts || Date.now());
    const bandBidMin = mid * (1 - this.depthPct);
    const bandAskMax = mid * (1 + this.depthPct);

    const presentKeys = new Set();

    const processSide = (rows, side) => {
      for (const [p, q] of rows.slice(0, 120)) {
        const price = Number(p), size = Number(q);
        if (!Number.isFinite(price) || !Number.isFinite(size) || size <= 0) continue;

        // only inspect near 1% band
        if (side === "bid" && price < bandBidMin) continue;
        if (side === "ask" && price > bandAskMax) continue;

        if (size < this.spoofMinSizeBTC) continue; // only massive walls

        const bucket = Math.round(price * 10) / 10;
        const key = `${symbol}|${side}|${bucket}`;
        presentKeys.add(key);

        const ex = this.liveWalls.get(key);
        if (!ex) {
          this.liveWalls.set(key, {
            key, symbol, side, price: bucket,
            firstSeen: now, lastSeen: now,
            maxSize: size,
            executedVolumeNearLevel: 0,
            vanished: false
          });
        } else {
          ex.lastSeen = now;
          ex.maxSize = Math.max(ex.maxSize, size);
        }
      }
    };

    processSide(bids, "bid");
    processSide(asks, "ask");

    // Detect vanished large walls
    for (const [key, w] of this.liveWalls.entries()) {
      if (!key.startsWith(symbol + "|")) continue;
      if (presentKeys.has(key)) continue;
      if (w.vanished) continue;

      const life = now - w.firstSeen;
      if (life <= this.spoofMaxLifetimeMs && w.maxSize >= this.spoofMinSizeBTC && w.executedVolumeNearLevel < w.maxSize * 0.05) {
        w.vanished = true;
        this._pushEvent({
          type: "phantom_liquidity_risk",
          label: "Spoof Wall",
          symbol: w.symbol,
          side: w.side,
          price: w.price,
          wallSize: w.maxSize,
          lifetimeMs: life,
          ts: now,
          sentimentImpact: -1 // consumer can invert naive retail weighting
        });
      } else {
        w.vanished = true;
      }
    }

    // memory trim
    if (this.liveWalls.size > this.maxWallRecords) {
      const arr = Array.from(this.liveWalls.entries()).sort((a, b) => a[1].lastSeen - b[1].lastSeen);
      const drop = arr.slice(0, Math.floor(this.maxWallRecords * 0.25));
      for (const [k] of drop) this.liveWalls.delete(k);
    }
  }

  // optional: call when execution occurs near a watched level
  markExecutionNearLevel(symbol, side, price, volume) {
    const bucket = Math.round(Number(price) * 10) / 10;
    const key = `${symbol}|${side}|${bucket}`;
    const w = this.liveWalls.get(key);
    if (w) w.executedVolumeNearLevel += Math.max(0, Number(volume || 0));
  }

  _pushEvent(evt) {
    this.events.push(evt);
    if (this.events.length > 5000) this.events.shift();
  }

  getSymbolMetrics(symbol) {
    return this.stateBySymbol.get(symbol) || null;
  }

  getRecentEvents(limit = 100) {
    return this.events.slice(-limit);
  }

  snapshot() {
    return {
      ts: Date.now(),
      symbols: Array.from(this.stateBySymbol.values()),
      recentEvents: this.getRecentEvents(200)
    };
  }
}

export default AdvancedMicrostructureTracker;
