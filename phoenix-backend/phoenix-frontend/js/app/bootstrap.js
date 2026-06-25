import AppShell from "./appShell.js";

const root = document.getElementById("app-root");
const shell = new AppShell(root);
shell.init();

// expose for debugging
window.__phoenixShell = shell;
