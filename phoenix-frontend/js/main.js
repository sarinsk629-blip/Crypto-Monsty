import { initState, applyQuantUpdate } from "./core/state.js";
import { connectWebSocket, onQuantUpdate } from "./services/websocket.js";
import { initRouter } from "./core/router.js";
import { initGlobalEvents } from "./core/events.js";

// Optional stream URL; set window.PHOENIX_WS_URL before loading app if needed.
const WS_URL = window.PHOENIX_WS_URL || "ws://localhost:8787";

function boot() {
  initState();
  initGlobalEvents();
  initRouter();

  onQuantUpdate((payload) => {
    applyQuantUpdate(payload);
    const debug = document.getElementById("debug");
    if (debug) {
      const last = payload?.points?.[payload.points.length - 1];
      debug.textContent = JSON.stringify(last || {}, null, 2);
    }
  });

  // If your backend emits "market:tick" over Socket.io instead, this can remain disabled.
  // connectWebSocket(WS_URL);

  console.info("[main] Phoenix Terminal booted.");
}

boot();
