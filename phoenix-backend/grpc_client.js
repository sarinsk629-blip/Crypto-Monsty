import path from "path";
import { fileURLToPath } from "url";
import grpc from "@grpc/grpc-js";
import protoLoader from "@grpc/proto-loader";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const PROTO_PATH = path.resolve(__dirname, "../phoenix-proto/engine.proto");
const BRIDGE_GRPC_TARGET = process.env.BRIDGE_GRPC_TARGET || "localhost:50051";

const packageDef = protoLoader.loadSync(PROTO_PATH, {
  keepCase: true,
  longs: String,
  enums: Number,
  defaults: true,
  oneofs: true
});

const proto = grpc.loadPackageDefinition(packageDef);
const EngineBridge = proto.phoenix.engine.EngineBridge;

function sideToEnum(side) {
  return String(side).toLowerCase() === "sell" ? 2 : 1; // SIDE_SELL : SIDE_BUY
}

function algoToEnum(algo) {
  const a = String(algo).toLowerCase();
  if (a === "twap") return 1;
  if (a === "vwap") return 2;
  if (a === "iceberg") return 3;
  return 1;
}

export class PhoenixGrpcClient {
  constructor({ onSignal } = {}) {
    this.onSignal = onSignal || (() => {});
    this.client = new EngineBridge(
      BRIDGE_GRPC_TARGET,
      grpc.credentials.createInsecure(),
      {
        "grpc.keepalive_time_ms": 20000,
        "grpc.keepalive_timeout_ms": 5000,
        "grpc.max_receive_message_length": 20 * 1024 * 1024,
        "grpc.max_send_message_length": 20 * 1024 * 1024
      }
    );
    this.signalStream = null;
  }

  health() {
    return new Promise((resolve, reject) => {
      this.client.Health({}, (err, resp) => {
        if (err) return reject(err);
        resolve(resp);
      });
    });
  }

  ingestTicks(ticks = []) {
    const payload = {
      ticks: ticks.map((t) => ({
        exchange: t.exchange || "backend",
        symbol: String(t.symbol || "").toUpperCase(),
        price: Number(t.price || 0),
        volume: Number(t.volume || 0),
        side: sideToEnum(t.side || "buy"),
        event_ts: String(t.ts || Date.now()),
        recv_ts: String(t.recvTs || Date.now()),
        latency_ms: Number(t.latencyMs || 0),
        tags: t.tags || {}
      }))
    };

    return new Promise((resolve, reject) => {
      this.client.IngestTicks(payload, (err, resp) => {
        if (err) return reject(err);
        resolve(resp);
      });
    });
  }

  publishSignals(signals = []) {
    const payload = {
      signals: signals.map((s) => ({
        symbol: String(s.symbol || "").toUpperCase(),
        score: Number(s.score || 0),
        confidence: Number(s.confidence || 0),
        direction: s.direction || "neutral",
        ts: String(s.ts || Date.now()),
        diagnostics: s.diagnostics || {},
        labels: s.labels || [],
        severity: Number(s.severity || 1)
      }))
    };

    return new Promise((resolve, reject) => {
      this.client.PublishSignals(payload, (err, resp) => {
        if (err) return reject(err);
        resolve(resp);
      });
    });
  }

  executeOrder(req = {}) {
    const payload = {
      order_id: req.order_id || "",
      symbol: String(req.symbol || "BTCUSDT").toUpperCase(),
      side: sideToEnum(req.side || "buy"),
      qty: Number(req.qty || 0),
      algo: algoToEnum(req.algo || "twap"),
      duration_sec: Number(req.duration_sec || 60),
      slices: Number(req.slices || 12),
      display_qty: Number(req.display_qty || 0),
      min_clip: Number(req.min_clip || 0),
      max_clip: Number(req.max_clip || 0),
      exchange_weights: req.exchange_weights || {},
      mark_price: Number(req.mark_price || 0),
      slippage_bps: Number(req.slippage_bps || 3),
      ts: String(Date.now())
    };

    return new Promise((resolve, reject) => {
      this.client.ExecuteOrder(payload, (err, resp) => {
        if (err) return reject(err);
        resolve(resp);
      });
    });
  }

  startSignalStream() {
    if (this.signalStream) {
      try { this.signalStream.cancel(); } catch {}
      this.signalStream = null;
    }

    this.signalStream = this.client.StreamSignals({});
    this.signalStream.on("data", (msg) => {
      try { this.onSignal(msg); } catch {}
    });
    this.signalStream.on("error", (err) => {
      console.error("[grpc-client] signal stream error:", err.message || err);
      setTimeout(() => this.startSignalStream(), 1500);
    });
    this.signalStream.on("end", () => {
      console.warn("[grpc-client] signal stream ended; reconnecting...");
      setTimeout(() => this.startSignalStream(), 1500);
    });
  }

  close() {
    try {
      if (this.signalStream) this.signalStream.cancel();
    } catch {}
  }
}

export default PhoenixGrpcClient;
