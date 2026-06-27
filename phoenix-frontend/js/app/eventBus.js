export class EventBus {
    constructor() { this.listeners = {}; console.log("🛡️ EventBus Instantiated"); }
    on(event, callback) { if (!this.listeners[event]) this.listeners[event] = []; this.listeners[event].push(callback); }
    emit(event, data) { if (this.listeners[event]) this.listeners[event].forEach(cb => cb(data)); }
}
export default EventBus;