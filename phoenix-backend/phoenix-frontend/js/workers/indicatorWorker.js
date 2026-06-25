// indicatorWorker.js
// Workerized heavy computation for multi-symbol indicators

const state = {
  symbols: new Map(), // symbol -> arrays
};

function ensure(symbol) {
  if (!state.symbols.has(symbol)) {
    state.symbols.set(symbol, {
      prices: [],
      volumes: [],
      returns: []
    });
  }
  return state.symbols.get(symbol);
}

function sma(arr, n) {
  if (arr.length < n) return null;
  let s = 0;
  for (let i = arr.length - n; i < arr.length; i++) s += arr[i];
  return s / n;
}

function std(arr, n) {
  if (arr.length < n) return null;
  const m = sma(arr, n);
  let v = 0;
  for (let i = arr.length - n; i < arr.length; i++) v += (arr[i] - m) ** 2;
  return Math.sqrt(v / n);
}

function ema(prev, x, alpha) {
  if (prev == null) return x;
  return alpha * x + (1 - alpha) * prev;
}

self.onmessage = (event) => {
  const msg = event.data || {};
  if (msg.type !== "ticks") return;

  const ticks = Array.isArray(msg.payload) ? msg.payload : [];
  const out = [];

  for (const t of ticks) {
    const symbol = String(t.symbol || "").toUpperCase();
    const price = Number(t.price || 0);
    const vol = Number(t.volume || 0);
    if (!symbol || !Number.isFinite(price) || price <= 0) continue;

    const s = ensure(symbol);
    const prev = s.prices.length ? s.prices[s.prices.length - 1] : price;
    const ret = (price - prev) / Math.max(1e-9, prev);

    s.prices.push(price);
    s.volumes.push(vol);
    s.returns.push(ret);

    if (s.prices.length > 10000) s.prices.shift();
    if (s.volumes.length > 10000) s.volumes.shift();
    if (s.returns.length > 10000) s.returns.shift();

    // sample metrics
    const sma20 = sma(s.prices, 20);
    const sma50 = sma(s.prices, 50);
    const vol20 = std(s.returns, 20);
    const bbStd = std(s.prices, 20);
    const mid = sma20;
    const bbUp = mid != null && bbStd != null ? mid + 2 * bbStd : null;
    const bbDn = mid != null && bbStd != null ? mid - 2 * bbStd : null;

    out.push({
      symbol,
      ts: Number(t.ts || Date.now()),
      indicators: {
        sma20, sma50, vol20, bbUp, bbDn
      }
    });
  }

  if (out.length) {
    self.postMessage({ type: "indicators:update", payload: out });
  }
};
