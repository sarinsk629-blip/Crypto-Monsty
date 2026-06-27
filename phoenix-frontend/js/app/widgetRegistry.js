export class WidgetRegistry {
    constructor() { 
        this.widgets = new Map(); 
        console.log("🛡️ WidgetRegistry Online"); 
    }
    register(name, widget) { this.widgets.set(name, widget); }
    get(name) { return this.widgets.get(name); }
}
export default WidgetRegistry;