import { EventEmitter } from "events";

/**
 * ExchangeMultiplexer
 * - Connects to Binance + Bybit + Hyperliquid public streams
 * - Normalizes all incoming trade ticks into a standard Phoenix Tick Object
 * - Emits "tick" events and optionally broadcasts over Socket.io
 *
 * Phoenix Tick Object:
 * {
 *   exchange: "binance" | "bybit" | "hyperliquid",
 *   symbol: "BTCUSDT",
 *   price: number,
 *   volume: number,
 *   side: "buy" | "sell",
 *   ts: number,            // event timestamp in ms
 *   recvTs: number,        // local receive timestamp in ms
 *   latencyMs: number|null,
 *   raw: object
 * }
 */
export class ExchangeMultiplexer extends EventEmitter {
  constructor({
    io = null,
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    reconnectMs = 3000,
    heartbeatMs = 15000
  } = {}) {
    super();
    this.io = io;
    this.symbols = symbols;
    this.reconnectMs = reconnectMs;
    this.heartbeatMs = heartbeatMs;
    this.sockets = new Map();
    this.latency = {
      binance: { lastMessageTs: null, stale: false },
      bybit: { lastMessageTs: null, stale: false },
      hyperliquid: { lastMessageTs: null, stale: false }
    };
    this._hbTimer = null;
  }

  start() {
    this._connectBinance();
    this._connectBybit();
    this._connectHyperliquid();
    this._startHeartbeat();
  }

  stop() {
    for (const [, ws] of this.sockets.entries()) {
      try { ws.close(); } catch {}
    }
    this.sockets.clear();
    if (this._hbTimer) clearInterval(this._hbTimer);
    this._hbTimer = null;
  }

  setSocketIO(io) {
    this.io = io;
  }

  _broadcast(type, payload) {
    this.emit(type, payload);
    if (this.io) this.io.emit(type, payload);
  }

  _startHeartbeat() {
    if (this._hbTimer) clearInterval(this._hbTimer);
    this._hbTimer = setInterval(() => {
      const now = Date.now();
      for (const [ex, v] of Object.entries(this.latency)) {
        const stale = !v.lastMessageTs || (now - v.lastMessageTs > this.heartbeatMs * 2);
        v.stale = stale;
      }
      this._broadcast("exchange:health", {
        ts: now,
        exchanges: this.latency
      });
    }, this.heartbeatMs);
  }

  _connectBinance() {
    // Example stream: aggTrade
    const streams = this.symbols.map(s => `${s.toLowerCase()}@aggTrade`).join("/");
    const url = `wss://stream.binance.com:9443/stream?streams=${streams}`;
    this._connectWS("binance", url, null, (msg) => {
      const data = msg?.data || msg;
      if (!data || data.e !== "aggTrade") return null;
      return this._normalizeBinance(data);
    });
  }

  _connectBybit() {
    const url = "wss://stream.bybit.com/v5/public/linear";
    const subscribeMsg = {
      op: "subscribe",
      args: this.symbols.map(s => `publicTrade.${s}`)
    };
    this._connectWS("bybit", url, subscribeMsg, (msg) => {
      if (!msg?.topic?.startsWith("publicTrade.")) return null;
      const trades = Array.isArray(msg.data) ? msg.data : [];
      if (!trades.length) return null;
      // emit all trades
      return trades.map(t => this._normalizeBybit(t)).filter(Boolean);
    });
  }

  _connectHyperliquid() {
    // Hyperliquid public websocket
    const url = "wss://api.hyperliquid.xyz/ws";
    // NOTE: Hyperliquid symbols may differ from USDT contracts naming.
    const subscribe = this.symbols.map((s) => ({
      method: "subscribe",
      subscription: { type: "trades", coin: this._toHyperliquidCoin(s) }
    }));

    this._connectWS("hyperliquid", url, subscribe, (msg) => {
      // Typical trade payload: channel "trades", data [{px,sz,side,time,coin}, ...]
      if (msg?.channel !== "trades") return null;
      const arr = Array.isArray(msg?.data) ? msg.data : [];
      if (!arr.length) return null;
      return arr.map(t => this._normalizeHyperliquid(t)).filter(Boolean);
    });
  }

  _connectWS(exchange, url, subscribeMsg, parser) {
    let WebSocketImpl = globalThis.WebSocket;
    if (!WebSocketImpl) {
      // Node runtime without global WebSocket, require ws dynamically
      // (kept optional to avoid hard dependency for script generation)
      // eslint-disable-next-line no-eval
      const req = eval("require");
      WebSocketImpl = req("ws");
    }

    const openConnection = () => {
      const ws = new WebSocketImpl(url);
      this.sockets.set(exchange, ws);

      ws.onopen = () => {
        if (subscribeMsg) {
          if (Array.isArray(subscribeMsg)) {
            for (const m of subscribeMsg) ws.send(JSON.stringify(m));
          } else {
            ws.send(JSON.stringify(subscribeMsg));
          }
        }
        this._broadcast("exchange:status", { exchange, status: "connected", ts: Date.now() });
      };

      ws.onmessage = (evt) => {
        const recvTs = Date.now();
        this.latency[exchange].lastMessageTs = recvTs;
        this.latency[exchange].stale = false;

        let msg = null;
        try {
          msg = JSON.parse(evt.data);
        } catch {
          return;
        }

        const out = parser(msg);
        if (!out) return;

        if (Array.isArray(out)) {
          for (const tick of out) this._broadcastTick(tick);
        } else {
          this._broadcastTick(out);
        }
      };

      ws.onerror = (err) => {
        this._broadcast("exchange:error", { exchange, error: String(err?.message || err), ts: Date.now() });
      };

      ws.onclose = () => {
        this._broadcast("exchange:status", { exchange, status: "disconnected", ts: Date.now() });
        setTimeout(openConnection, this.reconnectMs);
      };
    };

    openConnection();
  }

  _broadcastTick(tick) {
    if (!tick) return;
    this._broadcast("market:tick", tick);
    this._broadcast("market:tick:normalized", tick);
  }

  _normalizeBinance(d) {
    // aggTrade:
    // p price, q qty, T trade time, m true if buyer is market maker
    const ts = Number(d.T || Date.now());
    const recvTs = Date.now();
    return {
      exchange: "binance",
      symbol: String(d.s || "").toUpperCase(),
      price: Number(d.p),
      volume: Number(d.q),
      side: d.m ? "sell" : "buy", // maker buyer => taker sell
      ts,
      recvTs,
      latencyMs: Number.isFinite(ts) ? Math.max(0, recvTs - ts) : null,
      raw: d
    };
  }

  _normalizeBybit(t) {
    // v5 trade fields commonly: s symbol, p price, v size, T time, S side(Buy/Sell)
    const ts = Number(t.T || t.ts || Date.now());
    const recvTs = Date.now();
    return {
      exchange: "bybit",
      symbol: String(t.s || "").toUpperCase(),
      price: Number(t.p),
      volume: Number(t.v || t.q || 0),
      side: String(t.S || t.side || "Buy").toLowerCase() === "sell" ? "sell" : "buy",
      ts,
      recvTs,
      latencyMs: Number.isFinite(ts) ? Math.max(0, recvTs - ts) : null,
      raw: t
    };
  }

  _normalizeHyperliquid(t) {
    // typical fields: coin, px, sz, side, time
    const ts = Number(t.time || Date.now());
    const recvTs = Date.now();
    const symbol = this._fromHyperliquidCoin(String(t.coin || ""));
    return {
      exchange: "hyperliquid",
      symbol,
      price: Number(t.px),
      volume: Number(t.sz),
      side: String(t.side || "buy").toLowerCase() === "sell" ? "sell" : "buy",
      ts,
      recvTs,
      latencyMs: Number.isFinite(ts) ? Math.max(0, recvTs - ts) : null,
      raw: t
    };
  }

  _toHyperliquidCoin(symbol) {
    // BTCUSDT -> BTC (heuristic)
    return symbol.replace(/USDT$/i, "").toUpperCase();
  }

  _fromHyperliquidCoin(coin) {
    // BTC -> BTCUSDT (heuristic)
    return `${coin.toUpperCase()}USDT`;
  }
}

export default ExchangeMultiplexer;
