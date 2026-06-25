/**
 * LayoutEngine
 * - Saves/restores widget ordering + selected symbol
 * - localStorage + IndexedDB fallback (simple localStorage implementation here)
 */
export class LayoutEngine {
  constructor(key = "phoenix_v5_layout") {
    this.key = key;
  }

  save(layout) {
    try {
      localStorage.setItem(this.key, JSON.stringify(layout));
    } catch (e) {
      console.warn("[LayoutEngine] save failed", e);
    }
  }

  load(defaultLayout) {
    try {
      const raw = localStorage.getItem(this.key);
      if (!raw) return defaultLayout;
      const parsed = JSON.parse(raw);
      return { ...defaultLayout, ...parsed };
    } catch {
      return defaultLayout;
    }
  }
}

export default LayoutEngine;
