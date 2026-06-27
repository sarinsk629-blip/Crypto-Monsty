export class Store {
    constructor() { this.state = {}; }
    set(key, val) { this.state[key] = val; }
    get(key) { return this.state[key]; }
}
export const store = new Store();
export default store;