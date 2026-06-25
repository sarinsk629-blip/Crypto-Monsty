import { renderTerminalPage } from "../pages/terminal.js";
import renderRiskDashboard from "../pages/riskDashboard.js";

const routes = {
  "/": renderHome,
  "/terminal": renderTerminal,
  "/risk": renderRisk
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
    <h2>Crypto Monsty — Phoenix</h2>
    <p><a href="#/terminal">Open Terminal</a></p>
    <p><a href="#/risk">Open Risk Dashboard</a></p>
    <pre id="debug" style="background:#111827;padding:10px;border-radius:8px;overflow:auto"></pre>
  `;
  main.appendChild(card);
}

function renderTerminal(container = root()) {
  const main = clearMain(container);
  renderTerminalPage(main);
}

function renderRisk(container = root()) {
  const main = clearMain(container);
  renderRiskDashboard(main);
}

export function navigateTo(path) {
  const fn = routes[path] || renderHome;
  fn(root());
}

export function initRouter() {
  const parse = () => (window.location.hash || "#/").replace(/^#/, "") || "/";
  window.addEventListener("hashchange", () => navigateTo(parse()));
  navigateTo(parse());
}
