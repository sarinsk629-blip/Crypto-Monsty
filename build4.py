#!/usr/bin/env python3
# build_phase4_microstructure.py

from pathlib import Path
import textwrap

ROOT = Path(".").resolve()

FILES = {
    "phoenix-backend/advancedMicrostructureTracker.js": r"""
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
    """,

    "phoenix-backend/institutionalOrderBook.js": r"""
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
    """,

    "phoenix-backend/evaluate_deep_consensus.js": r"""
    /**
     * evaluate_deep_consensus
     * Combines:
     * - technical score
     * - microstructure (BAI/OBI + whale flow + phantom-liquidity risk events)
     * - macro sentiment override
     */
    export function evaluate_deep_consensus({
      technical = 0,            // -1..1
      micro = null,             // from AdvancedMicrostructureTracker symbol metrics
      riskEvents = [],          // recent events
      macroSignal = null        // {score:-1..1, regime:"normal|elevated|extreme"}
    } = {}) {
      let score = Number(technical || 0);

      if (micro) {
        const bai = Number(micro.bai || 0);
        const obi = Number(micro.obi || 0);
        const whale = Number(micro.whaleDelta || 0);
        const retail = Number(micro.retailCvd || 0);

        // balanced microstructure contribution
        const microComposite = (0.35 * bai) + (0.35 * obi) + (0.20 * Math.tanh(whale / 100)) + (0.10 * Math.tanh(retail / 100));
        score = 0.6 * score + 0.4 * microComposite;
      }

      // If phantom-liquidity risks seen recently, dampen naive directional confidence
      const spoofCount = riskEvents.filter(e => e.type === "phantom_liquidity_risk").length;
      if (spoofCount > 0) {
        const damp = Math.max(0.25, 1 - spoofCount * 0.15);
        score *= damp;
      }

      // Macro override regime
      if (macroSignal && macroSignal.regime === "extreme") {
        // In extreme macro regime, technicals are secondary
        score = 0.2 * score + 0.8 * Number(macroSignal.score || 0);
      } else if (macroSignal && macroSignal.regime === "elevated") {
        score = 0.5 * score + 0.5 * Number(macroSignal.score || 0);
      }

      // confidence proxy
      const confidence = Math.min(1, Math.abs(score));
      const direction = score > 0.08 ? "bullish" : score < -0.08 ? "bearish" : "neutral";

      return {
        direction,
        score,
        confidence,
        explain: {
          technical,
          microUsed: !!micro,
          spoofEvents: spoofCount,
          macro: macroSignal || null
        },
        ts: Date.now()
      };
    }

    export default evaluate_deep_consensus;
    """,

    "phoenix-backend/newsSentimentEngine.js": r"""
    /**
     * NewsSentimentEngine (Phase 4 upgrade)
     * - Scores RSS/news headlines
     * - Aggressively weights macro catalysts (Fed/CPI/NFP/FOMC)
     * - Emits regime: normal | elevated | extreme
     */
    export class NewsSentimentEngine {
      constructor() {
        this.last = {
          score: 0,
          regime: "normal",
          reasons: [],
          ts: Date.now()
        };

        this.keywords = {
          hawkish: ["rate hike", "higher for longer", "inflation surge", "tightening", "hawkish", "liquidity drain"],
          dovish: ["rate cut", "disinflation", "easing", "dovish", "stimulus", "liquidity injection"],
          highImpact: ["fomc", "fed", "powell", "cpi", "pce", "nfp", "payrolls", "boj", "ecb", "geopolitical", "war", "sanctions"]
        };
      }

      _scoreHeadline(h) {
        const txt = String(h || "").toLowerCase();
        let score = 0;
        const reasons = [];

        for (const k of this.keywords.hawkish) if (txt.includes(k)) { score -= 0.35; reasons.push(`hawkish:${k}`); }
        for (const k of this.keywords.dovish) if (txt.includes(k)) { score += 0.35; reasons.push(`dovish:${k}`); }

        let impactHits = 0;
        for (const k of this.keywords.highImpact) if (txt.includes(k)) impactHits++;

        if (impactHits >= 2) { score *= 1.8; reasons.push("macro:multi-hit"); }
        else if (impactHits === 1) { score *= 1.3; reasons.push("macro:single-hit"); }

        // clip
        score = Math.max(-1, Math.min(1, score));
        return { score, reasons, impactHits };
      }

      evaluateFeed(items = []) {
        // items: [{title, summary, ts}]
        const now = Date.now();
        let agg = 0;
        let wsum = 0;
        const reasons = [];
        let macroBursts = 0;

        for (const it of items.slice(-120)) {
          const title = it.title || "";
          const summary = it.summary || "";
          const combined = `${title} ${summary}`;
          const { score, reasons: r, impactHits } = this._scoreHeadline(combined);

          const ageMin = Math.max(0, (now - Number(it.ts || now)) / 60000);
          const recencyW = Math.exp(-ageMin / 90); // decay over 90 minutes
          const impactW = 1 + impactHits * 0.75;
          const w = recencyW * impactW;

          agg += score * w;
          wsum += w;
          if (impactHits >= 2 && Math.abs(score) > 0.4) macroBursts++;
          reasons.push(...r.slice(0, 2));
        }

        const raw = wsum > 0 ? agg / wsum : 0;
        const abs = Math.abs(raw);

        let regime = "normal";
        if (macroBursts >= 2 || abs >= 0.55) regime = "extreme";
        else if (macroBursts >= 1 || abs >= 0.30) regime = "elevated";

        this.last = {
          score: raw,
          regime,
          reasons: Array.from(new Set(reasons)).slice(0, 12),
          ts: now
        };
        return this.last;
      }

      getLastSignal() {
        return this.last;
      }
    }

    export default NewsSentimentEngine;
    """,

    "phoenix-frontend/js/renderers/advancedMicrostructureGraph.js": r"""
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
    """,
}

def write_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")

def main():
    print("Generating Phase 4 Advanced Microstructure & Whale Tracking...")
    for rel, content in FILES.items():
        write_file(ROOT / rel, content)
        print(f"✔ {rel}")
    print("\nDone.")

if __name__ == "__main__":
    main()
