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
