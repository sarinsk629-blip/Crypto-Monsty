const DEFAULTS = {
  processNoise: 1e-4,
  measurementNoise: 1e-2,
  vpinWindowSize: 50,
  bucketVolumeTarget: 1000
};

const state = {
  initialized: false,
  x: null,
  p: 1,
  q: DEFAULTS.processNoise,
  r: DEFAULTS.measurementNoise,
  cvd: 0,
  windowSize: DEFAULTS.vpinWindowSize,
  bucketVolumeTarget: DEFAULTS.bucketVolumeTarget,
  currentBucket: { buyVol: 0, sellVol: 0, totalVol: 0 },
  bucketImbalances: []
};

function toTimestampMs(ts) {
  if (typeof ts === "number") return ts;
  if (ts instanceof Date) return ts.getTime();
  const parsed = Date.parse(ts);
  return Number.isNaN(parsed) ? Date.now() : parsed;
}

function kalmanUpdate(z) {
  if (state.x === null) {
    state.x = z;
    state.p = 1;
    return state.x;
  }

  state.p = state.p + state.q;
  const k = state.p / (state.p + state.r);
  state.x = state.x + k * (z - state.x);
  state.p = (1 - k) * state.p;

  return state.x;
}

function pushCompletedBucket(buyVol, sellVol, totalVol) {
  if (totalVol <= 0) return;
  const imbalanceRatio = Math.abs(buyVol - sellVol) / totalVol;
  state.bucketImbalances.push(imbalanceRatio);
  if (state.bucketImbalances.length > state.windowSize) state.bucketImbalances.shift();
}

function computeVPIN() {
  if (!state.bucketImbalances.length) return 0;
  const sum = state.bucketImbalances.reduce((acc, v) => acc + v, 0);
  return sum / state.bucketImbalances.length;
}

function updateVolumeToxicity(side, volume) {
  state.cvd += side === "buy" ? volume : -volume;

  let remaining = volume;
  while (remaining > 0) {
    const room = state.bucketVolumeTarget - state.currentBucket.totalVol;
    const take = Math.min(room, remaining);

    if (side === "buy") state.currentBucket.buyVol += take;
    else state.currentBucket.sellVol += take;

    state.currentBucket.totalVol += take;
    remaining -= take;

    if (state.currentBucket.totalVol >= state.bucketVolumeTarget) {
      pushCompletedBucket(
        state.currentBucket.buyVol,
        state.currentBucket.sellVol,
        state.currentBucket.totalVol
      );
      state.currentBucket.buyVol = 0;
      state.currentBucket.sellVol = 0;
      state.currentBucket.totalVol = 0;
    }
  }

  return { cvd: state.cvd, vpin: computeVPIN() };
}

function handleInit(config = {}) {
  state.q = Number.isFinite(config.processNoise) ? Math.max(1e-12, Number(config.processNoise)) : DEFAULTS.processNoise;
  state.r = Number.isFinite(config.measurementNoise) ? Math.max(1e-12, Number(config.measurementNoise)) : DEFAULTS.measurementNoise;
  state.windowSize = Number.isFinite(config.vpinWindowSize) ? Math.max(1, Math.floor(config.vpinWindowSize)) : DEFAULTS.vpinWindowSize;
  state.bucketVolumeTarget = Number.isFinite(config.bucketVolumeTarget) ? Math.max(1, Number(config.bucketVolumeTarget)) : DEFAULTS.bucketVolumeTarget;

  state.initialized = true;
  self.postMessage({
    type: "quant:ready",
    payload: {
      ok: true,
      config: {
        processNoise: state.q,
        measurementNoise: state.r,
        windowSize: state.windowSize,
        bucketVolumeTarget: state.bucketVolumeTarget
      }
    }
  });
}

function handleTicks(rawTicks) {
  if (!Array.isArray(rawTicks) || rawTicks.length === 0) return;

  const points = [];
  for (const raw of rawTicks) {
    const price = Number(raw?.price);
    const volume = Math.max(0, Number(raw?.volume ?? 0));
    const side = String(raw?.side ?? "buy").toLowerCase() === "sell" ? "sell" : "buy";
    const timestamp = toTimestampMs(raw?.timestamp);

    if (!Number.isFinite(price)) continue;

    const smoothedPrice = kalmanUpdate(price);
    const tox = updateVolumeToxicity(side, volume);

    points.push({
      t: timestamp,
      rawPrice: price,
      smoothedPrice,
      volume,
      side,
      cvd: tox.cvd,
      vpin: tox.vpin
    });
  }

  if (points.length) {
    self.postMessage({
      type: "quant:update",
      payload: {
        points,
        meta: {
          windowSize: state.windowSize,
          bucketVolumeTarget: state.bucketVolumeTarget,
          processNoise: state.q,
          measurementNoise: state.r
        }
      }
    });
  }
}

function handleReset() {
  state.x = null;
  state.p = 1;
  state.cvd = 0;
  state.currentBucket = { buyVol: 0, sellVol: 0, totalVol: 0 };
  state.bucketImbalances = [];
  self.postMessage({ type: "quant:reset", payload: { ok: true } });
}

self.onmessage = (event) => {
  const msg = event?.data ?? {};
  const type = msg.type;

  if (type === "init") return handleInit(msg.payload || {});
  if (!state.initialized) handleInit({});

  if (type === "ticks") return handleTicks(msg.payload);
  if (type === "reset") return handleReset();
};
