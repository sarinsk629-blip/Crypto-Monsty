const state = {
  quant: {
    points: [],
    latest: null,
    meta: null
  }
};

export function initState() {
  console.info("[state] initialized");
}

export function applyQuantUpdate(payload) {
  const points = payload?.points ?? [];
  state.quant.points.push(...points);
  if (points.length) state.quant.latest = points[points.length - 1];
  state.quant.meta = payload?.meta ?? state.quant.meta;
}

export function getState() {
  return state;
}
