let ws = null;
let quantWorker = null;
const quantListeners = new Set();

export function onQuantUpdate(callback) {
  quantListeners.add(callback);
  return () => quantListeners.delete(callback);
}

function emitQuantUpdate(payload) {
  for (const cb of quantListeners) {
    try { cb(payload); } catch (e) { console.error("[websocket] quant listener error:", e); }
  }
}

function ensureQuantWorker() {
  if (quantWorker) return quantWorker;

  quantWorker = new Worker(new URL("../workers/quantWorker.js", import.meta.url), { type: "module" });

  quantWorker.onmessage = (event) => {
    const { type, payload } = event.data || {};
    if (type === "quant:update") emitQuantUpdate(payload);
    else if (type === "quant:ready") console.info("[quantWorker] ready", payload);
    else if (type === "quant:reset") console.info("[quantWorker] reset");
  };

  quantWorker.onerror = (err) => {
    console.error("[quantWorker] runtime error:", err);
  };

  quantWorker.postMessage({
    type: "init",
    payload: {
      processNoise: 1e-4,
      measurementNoise: 1e-2,
      vpinWindowSize: 50,
      bucketVolumeTarget: 1000
    }
  });

  return quantWorker;
}

function normalizeExchangeTick(raw) {
  return {
    price: Number(raw.price ?? raw.p),
    volume: Number(raw.volume ?? raw.v ?? raw.q ?? 0),
    timestamp: raw.timestamp ?? raw.ts ?? raw.T ?? Date.now(),
    side: String(raw.side ?? raw.S ?? "buy").toLowerCase() === "sell" ? "sell" : "buy"
  };
}

export function connectWebSocket(url) {
  if (ws && ws.readyState <= 1) return ws;

  const worker = ensureQuantWorker();
  ws = new WebSocket(url);

  ws.onopen = () => console.info("[websocket] connected:", url);

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);

      if (msg?.type === "tick" && msg.data) {
        worker.postMessage({ type: "ticks", payload: [normalizeExchangeTick(msg.data)] });
        return;
      }

      if (msg?.type === "ticks" && Array.isArray(msg.data)) {
        worker.postMessage({ type: "ticks", payload: msg.data.map(normalizeExchangeTick) });
      }
    } catch (err) {
      console.error("[websocket] parse/process error:", err);
    }
  };

  ws.onerror = (err) => console.error("[websocket] error:", err);
  ws.onclose = () => console.info("[websocket] disconnected");

  return ws;
}

export function disconnectWebSocket() {
  if (ws) ws.close();
  ws = null;

  if (quantWorker) quantWorker.terminate();
  quantWorker = null;

  quantListeners.clear();
}

export function resetQuantState() {
  if (quantWorker) quantWorker.postMessage({ type: "reset" });
}
