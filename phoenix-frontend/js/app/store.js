export class Store {
    constructor() { this.state = {}; console.log("🛡️ Store Instantiated"); }
    set(key, val) { this.state[key] = val; }
    get(key) { return this.state[key]; }
}
export default Store;