import { initState, applyQuantUpdate } from "./core/state.js";
import { connectWebSocket, onQuantUpdate } from "./services/websocket.js";

initState();

onQuantUpdate((payload) => {
  applyQuantUpdate(payload);

  const debug = document.getElementById("debug");
  if (debug) {
    const last = payload?.points?.[payload.points.length - 1];
    debug.textContent = JSON.stringify(last || {}, null, 2);
  }
});

// Replace with your exchange stream URL when ready:
// connectWebSocket("wss://your-stream-endpoint");
console.info("[main] Phoenix Terminal booted.");
