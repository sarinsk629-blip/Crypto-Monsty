/**
 * goldenLayoutManager.js
 * A lightweight GoldenLayout/GridStack-style manager:
 * - draggable/resizable panels
 * - popout widget into separate window
 * - persisted layout (localStorage)
 *
 * This is framework-agnostic and can be used with AppShell widget containers.
 */

export class GoldenLayoutManager {
  constructor({ root, storageKey = "phoenix_golden_layout_v1" } = {}) {
    if (!root) throw new Error("GoldenLayoutManager requires root element");
    this.root = root;
    this.storageKey = storageKey;
    this.items = new Map(); // id -> element
  }

  init() {
    this.root.style.position = "relative";
    this.root.style.minHeight = "700px";
    this.restore();
  }

  registerPanel({ id, element, x = 0, y = 0, w = 500, h = 300 }) {
    if (!id || !element) throw new Error("registerPanel requires id and element");
    element.dataset.glId = id;
    element.style.position = "absolute";
    element.style.left = `${x}px`;
    element.style.top = `${y}px`;
    element.style.width = `${w}px`;
    element.style.height = `${h}px`;
    element.style.resize = "both";
    element.style.overflow = "auto";
    element.style.border = element.style.border || "1px solid #27406b";
    element.style.borderRadius = element.style.borderRadius || "8px";
    element.style.background = element.style.background || "#0b1528";

    this._makeDraggable(element);
    this._attachPopoutButton(element);

    this.items.set(id, element);
    this.root.appendChild(element);
  }

  _makeDraggable(el) {
    let dragging = false;
    let startX = 0, startY = 0;
    let origL = 0, origT = 0;

    const handle = el.querySelector(".head") || el;
    handle.style.cursor = "move";

    const onDown = (e) => {
      dragging = true;
      startX = e.clientX;
      startY = e.clientY;
      origL = parseInt(el.style.left || "0", 10);
      origT = parseInt(el.style.top || "0", 10);
      e.preventDefault();
    };

    const onMove = (e) => {
      if (!dragging) return;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;
      el.style.left = `${origL + dx}px`;
      el.style.top = `${origT + dy}px`;
    };

    const onUp = () => {
      if (!dragging) return;
      dragging = false;
      this.save();
    };

    handle.addEventListener("mousedown", onDown);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }

  _attachPopoutButton(el) {
    const head = el.querySelector(".head");
    if (!head) return;

    let btn = head.querySelector(".gl-popout-btn");
    if (!btn) {
      btn = document.createElement("button");
      btn.className = "gl-popout-btn";
      btn.textContent = "Popout";
      btn.style.marginLeft = "8px";
      btn.style.fontSize = "11px";
      btn.style.padding = "3px 6px";
      btn.style.border = "1px solid #35558f";
      btn.style.borderRadius = "6px";
      btn.style.background = "#16284b";
      btn.style.color = "#d8e6ff";
      head.appendChild(btn);
    }

    btn.addEventListener("click", () => this.popout(el));
  }

  popout(el) {
    const id = el.dataset.glId || `panel_${Date.now()}`;
    const popup = window.open("", `phoenix_${id}`, "width=900,height=620");
    if (!popup) return;

    // simple clone
    popup.document.write(`
      <!doctype html>
      <html>
      <head>
        <title>Phoenix Popout - ${id}</title>
        <style>
          body{margin:0;background:#060d17;color:#d8e6ff;font-family:Inter,system-ui}
          .wrap{padding:8px}
          .head{font-weight:700;margin-bottom:8px}
          .body{border:1px solid #27406b;border-radius:8px;background:#0b1528;padding:8px;white-space:pre-wrap;font-family:ui-monospace,monospace}
        </style>
      </head>
      <body>
        <div class="wrap">
          <div class="head">${id}</div>
          <div class="body">${(el.querySelector(".body")?.innerText || "No content").replace(/</g, "&lt;")}</div>
        </div>
      </body>
      </html>
    `);
    popup.document.close();
  }

  snapshot() {
    const out = [];
    for (const [id, el] of this.items.entries()) {
      out.push({
        id,
        left: parseInt(el.style.left || "0", 10),
        top: parseInt(el.style.top || "0", 10),
        width: parseInt(el.style.width || "500", 10),
        height: parseInt(el.style.height || "300", 10)
      });
    }
    return out;
  }

  save() {
    try {
      localStorage.setItem(this.storageKey, JSON.stringify(this.snapshot()));
    } catch (e) {
      console.warn("[GoldenLayoutManager] save failed", e);
    }
  }

  restore() {
    try {
      const raw = localStorage.getItem(this.storageKey);
      if (!raw) return;
      const arr = JSON.parse(raw);
      if (!Array.isArray(arr)) return;
      for (const item of arr) {
        const el = this.root.querySelector(`[data-gl-id="${item.id}"]`);
        if (!el) continue;
        el.style.left = `${item.left}px`;
        el.style.top = `${item.top}px`;
        el.style.width = `${item.width}px`;
        el.style.height = `${item.height}px`;
      }
    } catch (e) {
      console.warn("[GoldenLayoutManager] restore failed", e);
    }
  }
}

export default GoldenLayoutManager;
