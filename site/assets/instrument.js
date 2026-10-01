/* Shared Compiler shell behavior. Keep this runtime presentation-only. */
(() => {
  const root = document.documentElement;
  const themes = new Set(["auto", "light", "dark"]);
  const mediaDark = matchMedia("(prefers-color-scheme: dark)");
  const mediaReduced = matchMedia("(prefers-reduced-motion: reduce)");

  function readTheme() {
    try {
      const saved = localStorage.getItem("experiment-theme");
      if (themes.has(saved)) return saved;
      const legacy = localStorage.getItem("experiment-mode");
      if (legacy === "light" || legacy === "dark") return legacy;
    } catch {
      /* A local preference is optional. */
    }
    return "auto";
  }

  function resolvedTheme(theme) {
    return theme === "auto" ? (mediaDark.matches ? "dark" : "light") : theme;
  }

  function syncThemeColor(theme) {
    let meta = document.querySelector('meta[name="theme-color"]');
    if (!meta) {
      meta = document.createElement("meta");
      meta.name = "theme-color";
      document.head.append(meta);
    }
    meta.content = theme === "dark" ? "#00070d" : "#f4f9fd";
  }

  function applyTheme(theme, persist = false) {
    const safeTheme = themes.has(theme) ? theme : "auto";
    const resolved = resolvedTheme(safeTheme);
    root.dataset.themeMode = safeTheme;
    root.dataset.theme = resolved;
    syncThemeColor(resolved);

    document.querySelectorAll("[data-theme-choice]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.themeChoice === safeTheme));
    });
    document.querySelectorAll("[data-theme-label]").forEach((label) => {
      label.textContent = safeTheme[0].toUpperCase() + safeTheme.slice(1);
    });

    if (persist) {
      try { localStorage.setItem("experiment-theme", safeTheme); } catch {
        /* A local preference is optional. */
      }
    }
  }

  // Apply before the page is interactive so the saved theme does not wait for
  // catalog data or detail-page rendering.
  applyTheme(readTheme());

  function setupThemeControls() {
    document.addEventListener("click", (event) => {
      const button = event.target.closest?.("[data-theme-choice]");
      if (!button) return;
      applyTheme(button.dataset.themeChoice, true);
      button.closest("details")?.removeAttribute("open");
    });

    addEventListener("storage", (event) => {
      if (event.key === "experiment-theme" || event.key === null) applyTheme(readTheme());
    });

    mediaDark.addEventListener?.("change", () => {
      if (root.dataset.themeMode === "auto") applyTheme("auto");
    });
  }

  function setupScrollGradient() {
    if (mediaReduced.matches) return;
    let ticking = false;
    const update = () => {
      ticking = false;
      const range = Math.max(1, document.documentElement.scrollHeight - innerHeight);
      const progress = Math.min(1, Math.max(0, scrollY / range));
      root.style.setProperty("--gradient-y", `${-10 + progress * 72}%`);
    };
    addEventListener("scroll", () => {
      if (!ticking) {
        ticking = true;
        requestAnimationFrame(update);
      }
    }, {passive: true});
    update();
  }

  function setupRouteFeedback() {
    if (mediaReduced.matches) return;
    const flash = document.createElement("div");
    flash.className = "route-flash";
    flash.setAttribute("aria-hidden", "true");
    document.body.append(flash);

    document.addEventListener("click", (event) => {
      const link = event.target.closest?.("a[href]");
      if (!link || event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      if (link.target === "_blank" || link.hasAttribute("download")) return;
      const target = new URL(link.href, location.href);

      // Cross-origin pages cannot share this document's View Transition state.
      // Let the browser navigate normally instead of faking a cross-site transition.
      if (target.origin !== location.origin) return;
      if (target.pathname === location.pathname && target.hash) return;

      event.preventDefault();
      document.body.classList.add("route-leaving");
      setTimeout(() => { location.href = target.href; }, 180);
    });
  }

  function start() {
    setupThemeControls();
    setupScrollGradient();
    setupRouteFeedback();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, {once: true});
  } else {
    start();
  }
})();
