export function initGlobalEvents() {
  window.addEventListener("keydown", (e) => {
    // Quick jump to terminal: Ctrl/Cmd + K
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      window.location.hash = "/terminal";
    }
  });

  console.info("[events] global events initialized");
}
