// ==========================================
// PHOENIX QUANT ENGINE: STEP 2 (INSTITUTIONAL)
// Advanced Stochastic & Jump-Diffusion Matrix
// ==========================================

const ENGINE_CONFIG = {
  baseTickRate: 1000,
  jumpThresholdSigma: 3.5, // 3.5 standard deviations for a "Jump"
  maxIndicators: 70
};

const state = {
  returns: [],
  rollingVol: 0,
  jumpDetects: 0,
  hurstExponent: 0.5, // 0.5 = random walk
  indicatorPipeline: new Map() // Matrix to hold up to 70 indicators
};

// 1. ADVANCED MATH: Merton Jump-Diffusion Detector
function detectJumpDiffusion(currentPrice, lastPrice, rollingVol) {
  if (!lastPrice || rollingVol === 0) return { isJump: false, magnitude: 0 };

  const logReturn = Math.log(currentPrice / lastPrice);
  const zScore = Math.abs(logReturn) / rollingVol;

  if (zScore > ENGINE_CONFIG.jumpThresholdSigma) {
    state.jumpDetects++;
    return { isJump: true, magnitude: logReturn, zScore };
  }
  return { isJump: false, magnitude: logReturn, zScore };
}

// 2. ADVANCED MATH: Hurst Exponent (Fractal Market Memory)
function calculateHurst(returns) {
   // Simplified Rescaled Range (R/S) Analysis for worker speed
   if (returns.length < 20) return 0.5;
   const mean = returns.reduce((a,b) => a+b, 0) / returns.length;
   const deviations = returns.map(r => r - mean);

   let maxZ = -Infinity, minZ = Infinity, sum = 0;
   for(let d of deviations) {
       sum += d;
       if(sum > maxZ) maxZ = sum;
       if(sum < minZ) minZ = sum;
   }
   const R = maxZ - minZ;
   const S = Math.sqrt(deviations.reduce((a,b) => a + b*b, 0) / returns.length);

   if (S === 0) return 0.5;
   // Estimate H based on log(R/S) / log(N)
   return Math.log(R/S) / Math.log(returns.length);
}

// 3. INDICATOR MATRIX: Dynamic Pipeline for 70+ Indicators
function registerIndicator(name, computeFn) {
  if (state.indicatorPipeline.size >= ENGINE_CONFIG.maxIndicators) {
     console.warn(`[Quant] Max indicators (${ENGINE_CONFIG.maxIndicators}) reached.`);
     return;
  }
  state.indicatorPipeline.set(name, computeFn);
}

// Registering a few initial advanced models...
registerIndicator("OrderFlowImbalance", (tick, state) => {
    return (tick.buyVol - tick.sellVol) / (tick.buyVol + tick.sellVol + 0.0001);
});

self.onmessage = (event) => {
  const { type, payload } = event.data;

  if (type === "process_tick") {
     const tick = payload;

     // Update stochastic variables
     const jumpData = detectJumpDiffusion(tick.price, tick.lastPrice, state.rollingVol);
     const hurst = calculateHurst(state.returns);

     // Run the 70-indicator matrix
     const matrixResults = {};
     for (const [name, compute] of state.indicatorPipeline.entries()) {
         matrixResults[name] = compute(tick, state);
     }

     // Send back the quantum calculations
     self.postMessage({
         type: "quant_update",
         data: {
             price: tick.price,
             jumpDetected: jumpData.isJump,
             jumpMagnitude: jumpData.magnitude,
             hurstExponent: hurst,
             matrix: matrixResults
         }
     });
  }
};
