/**
 * ReplayEngine
 * Replays historical ticks with optional speed-up and computes simple strategy stats.
 * Strategy (example):
 * - if score > longThreshold => long
 * - if score < shortThreshold => short
 * - pnl from direction * returns
 */
export class ReplayEngine {
  constructor() {}

  run({
    ticks = [],
    consensusSeries = [],
    longThreshold = 0.15,
    shortThreshold = -0.15,
    feeBps = 2
  } = {}) {
    if (!Array.isArray(ticks) || ticks.length < 2) {
      return {
        ok: true,
        trades: [],
        equityCurve: [],
        stats: { pnl: 0, maxDrawdown: 0, winRate: 0, tradeCount: 0 }
      };
    }

    // map consensus by nearest ts bucket
    const cByTs = new Map();
    for (const c of (consensusSeries || [])) {
      cByTs.set(Number(c.ts || 0), Number(c.score || 0));
    }

    let position = 0; // -1, 0, +1
    let entry = 0;
    let pnl = 0;
    let peak = 0;
    let maxDrawdown = 0;
    let wins = 0;
    let losses = 0;
    const trades = [];
    const equityCurve = [];

    function nearestScore(ts) {
      // cheap nearest by exact first, else 0
      return cByTs.get(ts) ?? 0;
    }

    for (let i = 1; i < ticks.length; i++) {
      const prev = ticks[i - 1];
      const cur = ticks[i];

      const pxPrev = Number(prev.price);
      const pxCur = Number(cur.price);
      if (!Number.isFinite(pxPrev) || !Number.isFinite(pxCur) || pxPrev <= 0) continue;

      const ret = (pxCur - pxPrev) / pxPrev;
      const score = nearestScore(Number(cur.ts || 0));

      // signal
      let target = 0;
      if (score > longThreshold) target = 1;
      else if (score < shortThreshold) target = -1;

      // rebalance if needed
      if (target !== position) {
        if (position !== 0) {
          const gross = (pxCur - entry) / entry * position;
          const fee = feeBps / 10000;
          const net = gross - fee;
          pnl += net;
          if (net >= 0) wins++;
          else losses++;
          trades.push({
            ts: Number(cur.ts || Date.now()),
            action: "close",
            side: position > 0 ? "long" : "short",
            entry,
            exit: pxCur,
            net
          });
        }

        if (target !== 0) {
          entry = pxCur;
          trades.push({
            ts: Number(cur.ts || Date.now()),
            action: "open",
            side: target > 0 ? "long" : "short",
            entry
          });
        }
        position = target;
      } else if (position !== 0) {
        // mark-to-market
        pnl += ret * position;
      }

      peak = Math.max(peak, pnl);
      maxDrawdown = Math.max(maxDrawdown, peak - pnl);
      equityCurve.push({ ts: Number(cur.ts || Date.now()), pnl });
    }

    const closedTrades = wins + losses;
    const winRate = closedTrades > 0 ? wins / closedTrades : 0;

    return {
      ok: true,
      trades,
      equityCurve,
      stats: {
        pnl,
        maxDrawdown,
        winRate,
        tradeCount: closedTrades
      }
    };
  }
}

export default ReplayEngine;
