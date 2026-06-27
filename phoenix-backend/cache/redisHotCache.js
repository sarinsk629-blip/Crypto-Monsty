/**
 * PHANTOM REDIS ADAPTER (V2)
 * Includes custom domain methods to prevent TypeErrors in server loops.
 */
export class RedisHotCache {
  constructor() {
    this.cache = new Map();
    console.log("🛡️ [RedisHotCache] No Redis URL found. Running in Phantom In-Memory Mode.");
  }
  async connect() { return true; }
  async get(key) { return this.cache.get(key); }
  async set(key, val, ttl) { this.cache.set(key, val); }
  async del(key) { this.cache.delete(key); }

  // Custom Domain Methods expected by server.js
  async setConsensus(sym, val, ttl) { this.cache.set(`consensus:${sym}`, val); }
  async getConsensus(sym) { return this.cache.get(`consensus:${sym}`); }
  async setTick(sym, val) { this.cache.set(`tick:${sym}`, val); }
  async getTick(sym) { return this.cache.get(`tick:${sym}`); }
  async pushSignal(val) { return true; }

  on(event, handler) { /* Mock event emitter */ }
}
export default RedisHotCache;
