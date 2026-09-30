(() => {
  const root = document.documentElement;
  function preferredMode() {
    try {
      const saved = localStorage.getItem("experiment-mode");
      if (saved === "light" || saved === "dark") return saved;
    } catch {}
    return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  function setMode(value, persist = false) {
    const mode = value === "dark" ? "dark" : "light";
    root.dataset.mode = mode;
    document.querySelectorAll("[data-mode-choice]").forEach(button => {
      button.setAttribute("aria-pressed", String(button.dataset.modeChoice === mode));
    });
    if (persist) {
      try { localStorage.setItem("experiment-mode", mode); } catch {}
    }
  }
  setMode(preferredMode());
  document.addEventListener("click", event => {
    const button = event.target.closest?.("[data-mode-choice]");
    if (button) setMode(button.dataset.modeChoice, true);
  });

  if (!matchMedia("(prefers-reduced-motion: reduce)").matches) {
    const flash = document.createElement("div");
    flash.className = "route-flash";
    flash.setAttribute("aria-hidden", "true");
    document.body.append(flash);
    document.addEventListener("click", event => {
      const link = event.target.closest?.("a[href]");
      if (!link || event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      if (link.target === "_blank" || link.hasAttribute("download")) return;
      const target = new URL(link.href, location.href);
      const isResearch = target.hostname === "tyharbin.com";
      const isLocal = target.origin === location.origin;
      if (!isResearch && !isLocal) return;
      if (isLocal && target.pathname === location.pathname && target.hash) return;
      event.preventDefault();
      document.body.classList.add("route-leaving");
      setTimeout(() => { location.href = target.href; }, 220);
    });
  }
})();