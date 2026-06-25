import { createClient } from "redis";

/**
 * RedisHotCache
 * Hot path store for:
 * - latest tick per symbol
 * - recent ticks ring (list)
 * - latest consensus per symbol
 * - optional TTL keys
 */
export class RedisHotCache {
  constructor({
    url = process.env.REDIS_URL || "redis://127.0.0.1:6379",
    prefix = "phoenix",
    maxRecentTicks = 5000
  } = {}) {
    this.url = url;
    this.prefix = prefix;
    this.maxRecentTicks = maxRecentTicks;
    this.client = createClient({ url: this.url });
    this.connected = false;

    this.client.on("error", (err) => {
      console.error("[RedisHotCache] error:", err?.message || err);
    });
  }

  async connect() {
    if (this.connected) return;
    await this.client.connect();
    this.connected = true;
    console.log("[RedisHotCache] connected:", this.url);
  }

  async disconnect() {
    if (!this.connected) return;
    await this.client.quit();
    this.connected = false;
  }

  _k(...parts) {
    return `${this.prefix}:${parts.join(":")}`;
  }

  async setLatestTick(symbol, tick) {
    const sym = String(symbol).toUpperCase();
    const key = this._k("tick", "latest", sym);
    await this.client.set(key, JSON.stringify(tick));
  }

  async getLatestTick(symbol) {
    const sym = String(symbol).toUpperCase();
    const key = this._k("tick", "latest", sym);
    const v = await this.client.get(key);
    return v ? JSON.parse(v) : null;
  }

  async pushRecentTick(symbol, tick) {
    const sym = String(symbol).toUpperCase();
    const key = this._k("tick", "recent", sym);
    const pipe = this.client.multi();
    pipe.rPush(key, JSON.stringify(tick));
    pipe.lTrim(key, -this.maxRecentTicks, -1);
    await pipe.exec();
  }

  async getRecentTicks(symbol, limit = 1000) {
    const sym = String(symbol).toUpperCase();
    const key = this._k("tick", "recent", sym);
    const n = Math.max(1, Math.min(this.maxRecentTicks, Number(limit || 1000)));
    const arr = await this.client.lRange(key, -n, -1);
    return arr.map((x) => JSON.parse(x));
  }

  async setConsensus(symbol, consensus, ttlSec = 30) {
    const sym = String(symbol).toUpperCase();
    const key = this._k("consensus", "latest", sym);
    await this.client.set(key, JSON.stringify(consensus), { EX: ttlSec });
  }

  async getConsensus(symbol) {
    const sym = String(symbol).toUpperCase();
    const key = this._k("consensus", "latest", sym);
    const v = await this.client.get(key);
    return v ? JSON.parse(v) : null;
  }

  async setJSON(keyParts, value, ttlSec = null) {
    const key = this._k(...keyParts);
    if (ttlSec) await this.client.set(key, JSON.stringify(value), { EX: ttlSec });
    else await this.client.set(key, JSON.stringify(value));
  }

  async getJSON(keyParts) {
    const key = this._k(...keyParts);
    const v = await this.client.get(key);
    return v ? JSON.parse(v) : null;
  }

  async health() {
    try {
      const pong = await this.client.ping();
      return { ok: pong === "PONG", connected: this.connected, url: this.url };
    } catch (e) {
      return { ok: false, connected: this.connected, error: e?.message || String(e) };
    }
  }
}

export default RedisHotCache;
