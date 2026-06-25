import { renderTerminalPage } from "../pages/terminal.js";

const routes = {
  "/": renderHome,
  "/terminal": renderTerminal
};

function root() {
  return document.getElementById("app") || document.body;
}

function clearMain(container) {
  let main = container.querySelector("main");
  if (!main) {
    main = document.createElement("main");
    container.appendChild(main);
  }
  main.innerHTML = "";
  return main;
}

function renderHome(container = root()) {
  const main = clearMain(container);
  const card = document.createElement("section");
  card.innerHTML = `
    <h2>Crypto Monsty — Phoenix Terminal</h2>
    <p>High-performance quant terminal initialized.</p>
    <p><a href="#/terminal">Open /terminal</a></p>
    <pre id="debug" style="background:#111827;padding:10px;border-radius:8px;overflow:auto"></pre>
  `;
  main.appendChild(card);
}

function renderTerminal(container = root()) {
  const main = clearMain(container);
  renderTerminalPage(main);
}

export function navigateTo(path) {
  const renderer = routes[path] || renderHome;
  renderer(root());
}

export function initRouter() {
  function parseHash() {
    const hash = window.location.hash || "#/";
    const path = hash.replace(/^#/, "") || "/";
    return path;
  }

  window.addEventListener("hashchange", () => {
    navigateTo(parseHash());
  });

  const initial = parseHash();
  navigateTo(initial);
}
