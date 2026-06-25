import pg from "pg";
const { Pool } = pg;

/**
 * AsyncPgWriter
 * - non-blocking ingestion queue for ticks, consensus, orders/fills
 * - batch flush interval + max batch size
 * - initializes schema automatically
 */
export class AsyncPgWriter {
  constructor({
    connectionString = process.env.DATABASE_URL || "postgresql://postgres:postgres@127.0.0.1:5432/phoenix",
    flushIntervalMs = 500,
    maxBatchSize = 1000
  } = {}) {
    this.pool = new Pool({ connectionString });
    this.flushIntervalMs = flushIntervalMs;
    this.maxBatchSize = maxBatchSize;

    this.queueTicks = [];
    this.queueConsensus = [];
    this.queueFills = [];
    this._timer = null;
    this._flushing = false;
    this.started = false;
  }

  async init() {
    const sql = `
    CREATE TABLE IF NOT EXISTS ticks (
      id BIGSERIAL PRIMARY KEY,
      ts BIGINT NOT NULL,
      recv_ts BIGINT,
      exchange TEXT NOT NULL,
      symbol TEXT NOT NULL,
      side TEXT NOT NULL,
      price DOUBLE PRECISION NOT NULL,
      volume DOUBLE PRECISION NOT NULL,
      latency_ms DOUBLE PRECISION,
      raw JSONB
    );

    CREATE INDEX IF NOT EXISTS idx_ticks_symbol_ts ON ticks(symbol, ts DESC);
    CREATE INDEX IF NOT EXISTS idx_ticks_exchange_ts ON ticks(exchange, ts DESC);

    CREATE TABLE IF NOT EXISTS consensus (
      id BIGSERIAL PRIMARY KEY,
      ts BIGINT NOT NULL,
      symbol TEXT NOT NULL,
      direction TEXT,
      score DOUBLE PRECISION,
      confidence DOUBLE PRECISION,
      diagnostics JSONB,
      explain JSONB
    );

    CREATE INDEX IF NOT EXISTS idx_consensus_symbol_ts ON consensus(symbol, ts DESC);

    CREATE TABLE IF NOT EXISTS fills (
      id BIGSERIAL PRIMARY KEY,
      ts BIGINT NOT NULL,
      user_id TEXT,
      symbol TEXT,
      side TEXT,
      qty DOUBLE PRECISION,
      price DOUBLE PRECISION,
      payload JSONB
    );

    CREATE INDEX IF NOT EXISTS idx_fills_symbol_ts ON fills(symbol, ts DESC);
    `;
    await this.pool.query(sql);
  }

  start() {
    if (this.started) return;
    this.started = true;
    this._timer = setInterval(() => this.flush().catch((e) => console.error("[AsyncPgWriter] flush error:", e)), this.flushIntervalMs);
  }

  async stop() {
    if (this._timer) clearInterval(this._timer);
    this._timer = null;
    await this.flush();
    await this.pool.end();
    this.started = false;
  }

  enqueueTick(tick) {
    this.queueTicks.push(tick);
    if (this.queueTicks.length >= this.maxBatchSize) this.flush().catch(() => {});
  }

  enqueueConsensus(c) {
    this.queueConsensus.push(c);
    if (this.queueConsensus.length >= this.maxBatchSize) this.flush().catch(() => {});
  }

  enqueueFill(fill) {
    this.queueFills.push(fill);
    if (this.queueFills.length >= this.maxBatchSize) this.flush().catch(() => {});
  }

  async flush() {
    if (this._flushing) return;
    this._flushing = true;

    const ticks = this.queueTicks.splice(0, this.maxBatchSize);
    const consensus = this.queueConsensus.splice(0, this.maxBatchSize);
    const fills = this.queueFills.splice(0, this.maxBatchSize);

    const client = await this.pool.connect();
    try {
      await client.query("BEGIN");

      if (ticks.length) {
        const values = [];
        const params = [];
        let i = 1;
        for (const t of ticks) {
          values.push(`($${i++},$${i++},$${i++},$${i++},$${i++},$${i++},$${i++},$${i++},$${i++})`);
          params.push(
            Number(t.ts || Date.now()),
            Number(t.recvTs || null),
            String(t.exchange || "unknown"),
            String(t.symbol || ""),
            String(t.side || "buy"),
            Number(t.price || 0),
            Number(t.volume || 0),
            t.latencyMs == null ? null : Number(t.latencyMs),
            JSON.stringify(t.raw || {})
          );
        }
        await client.query(
          `INSERT INTO ticks (ts, recv_ts, exchange, symbol, side, price, volume, latency_ms, raw) VALUES ${values.join(",")}`,
          params
        );
      }

      if (consensus.length) {
        const values = [];
        const params = [];
        let i = 1;
        for (const c of consensus) {
          values.push(`($${i++},$${i++},$${i++},$${i++},$${i++},$${i++},$${i++})`);
          params.push(
            Number(c.ts || Date.now()),
            String(c.symbol || ""),
            String(c.direction || "neutral"),
            Number(c.score || 0),
            Number(c.confidence || 0),
            JSON.stringify(c.diagnostics || {}),
            JSON.stringify(c.explain || {})
          );
        }
        await client.query(
          `INSERT INTO consensus (ts, symbol, direction, score, confidence, diagnostics, explain) VALUES ${values.join(",")}`,
          params
        );
      }

      if (fills.length) {
        const values = [];
        const params = [];
        let i = 1;
        for (const f of fills) {
          values.push(`($${i++},$${i++},$${i++},$${i++},$${i++},$${i++},$${i++})`);
          params.push(
            Number(f.ts || Date.now()),
            String(f.userId || "demo"),
            String(f.symbol || ""),
            String(f.side || ""),
            Number(f.qty || 0),
            Number(f.price || 0),
            JSON.stringify(f)
          );
        }
        await client.query(
          `INSERT INTO fills (ts, user_id, symbol, side, qty, price, payload) VALUES ${values.join(",")}`,
          params
        );
      }

      await client.query("COMMIT");
    } catch (e) {
      await client.query("ROLLBACK");
      // put back on front if desired (best effort simple requeue here)
      this.queueTicks.unshift(...ticks);
      this.queueConsensus.unshift(...consensus);
      this.queueFills.unshift(...fills);
      throw e;
    } finally {
      client.release();
      this._flushing = false;
    }
  }

  async queryTicks({ symbol, fromTs, toTs, limit = 5000 }) {
    const sql = `
      SELECT ts, recv_ts AS "recvTs", exchange, symbol, side, price, volume, latency_ms AS "latencyMs", raw
      FROM ticks
      WHERE symbol = $1
        AND ts >= $2
        AND ts <= $3
      ORDER BY ts ASC
      LIMIT $4
    `;
    const res = await this.pool.query(sql, [
      String(symbol).toUpperCase(),
      Number(fromTs || 0),
      Number(toTs || Date.now()),
      Math.max(1, Math.min(500000, Number(limit || 5000)))
    ]);
    return res.rows;
  }
}

export default AsyncPgWriter;
