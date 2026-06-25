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
