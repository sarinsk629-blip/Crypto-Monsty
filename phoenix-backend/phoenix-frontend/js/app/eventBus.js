export class EventBus {
  constructor() {
    this.map = new Map();
  }

  on(event, handler) {
    if (!this.map.has(event)) this.map.set(event, new Set());
    this.map.get(event).add(handler);
    return () => this.off(event, handler);
  }

  off(event, handler) {
    if (!this.map.has(event)) return;
    this.map.get(event).delete(handler);
  }

  emit(event, payload) {
    const set = this.map.get(event);
    if (!set) return;
    for (const h of set) {
      try { h(payload); } catch (e) { console.error("[EventBus]", e); }
    }
  }
}

export default EventBus;
