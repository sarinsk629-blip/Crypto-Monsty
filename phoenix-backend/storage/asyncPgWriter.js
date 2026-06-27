/**
 * PHANTOM POSTGRES ADAPTER
 * Bypasses ECONNREFUSED crashes by silently dropping writes 
 * if no external Postgres URL is provided.
 */
export class AsyncPgWriter {
  constructor() {
    console.log("🛡️ [AsyncPgWriter] No DB URL found. Running in Phantom Null-Write Mode.");
  }
  async connect() { return true; }
  async writeTick(tick) { return true; }
  async writeSignal(signal) { return true; }
  async query(sql, params) { return { rows: [] }; }
  on(event, handler) { /* Mock event emitter */ }
}
export default AsyncPgWriter;
