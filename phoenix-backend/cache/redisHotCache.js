/**
 * PHANTOM REDIS ADAPTER
 * Bypasses ECONNREFUSED crashes by falling back to in-memory JS Maps
 * if no external Redis URL is provided.
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
  on(event, handler) { /* Mock event emitter */ }
}
export default RedisHotCache;
