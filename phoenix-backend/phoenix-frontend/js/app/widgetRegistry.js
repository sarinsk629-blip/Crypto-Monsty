export class WidgetRegistry {
  constructor() {
    this.factories = new Map();
    this.instances = new Map();
  }

  register(name, factory) {
    this.factories.set(name, factory);
  }

  mount(name, target, ctx) {
    const fn = this.factories.get(name);
    if (!fn) throw new Error(`Widget not found: ${name}`);
    const instance = fn(target, ctx);
    this.instances.set(name, instance);
    return instance;
  }

  unmount(name) {
    const inst = this.instances.get(name);
    if (inst?.destroy) {
      try { inst.destroy(); } catch {}
    }
    this.instances.delete(name);
  }

  unmountAll() {
    for (const name of Array.from(this.instances.keys())) {
      this.unmount(name);
    }
  }
}

export default WidgetRegistry;
