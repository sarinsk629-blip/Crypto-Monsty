export class Store {
  constructor(initial = {}) {
    this.state = initial;
    this.listeners = new Set();
  }

  get() { return this.state; }

  set(patch) {
    this.state = { ...this.state, ...patch };
    for (const l of this.listeners) {
      try { l(this.state); } catch (e) { console.error("[Store]", e); }
    }
  }

  subscribe(cb) {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  }
}

export default Store;
