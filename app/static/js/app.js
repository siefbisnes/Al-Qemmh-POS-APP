// Al-Qemma — small, dependency-free interactions. No build step on
// purpose: this has to keep working on a shop PC for years.

document.addEventListener("DOMContentLoaded", () => {
  setupMobileNav();
  setupPriceToggles();
  setupCategoryFieldSwitcher();
  setupPersistentNavigation();
});

function setupPersistentNavigation() {
  document.addEventListener("click", async (event) => {
    const link = event.target.closest("a[href]");
    if (!link || event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (link.target && link.target !== "_self") return;

    const url = new URL(link.href, window.location.href);
    if (url.origin !== window.location.origin || url.hash) return;
    if (url.pathname === window.location.pathname && url.search === window.location.search) return;

    event.preventDefault();
    try {
      const response = await fetch(url.href, { headers: { "X-Requested-With": "fetch" } });
      if (!response.ok) throw new Error("Navigation failed");
      const html = await response.text();
      const doc = new DOMParser().parseFromString(html, "text/html");
      const nextMain = doc.querySelector(".main");
      if (!nextMain) throw new Error("Page has no main content");
      const main = document.querySelector(".main");
      main.replaceChildren(...nextMain.childNodes);
      document.title = doc.title;
      history.pushState({}, "", url.href);
      window.dispatchEvent(new CustomEvent("app:content-replaced"));
      executePageScripts(doc);
    } catch (error) {
      window.location.assign(url.href);
    }
  });

  window.addEventListener("popstate", () => window.location.reload());
}

function executePageScripts(doc) {
  const sources = [...doc.querySelectorAll("script[src]")]
    .filter((source) => !source.src.endsWith("/js/app.js"));
  const inlineScripts = [...doc.querySelectorAll("script[data-page-script]")];

  function runInlineScripts() {
    inlineScripts.forEach((source) => {
      const script = document.createElement("script");
      script.textContent = source.textContent;
      document.body.appendChild(script);
      script.remove();
    });
  }

  function loadNext(index) {
    if (index >= sources.length) {
      runInlineScripts();
      return;
    }
    const script = document.createElement("script");
    script.src = new URL(sources[index].src, window.location.href).href;
    script.onload = () => loadNext(index + 1);
    script.onerror = () => loadNext(index + 1);
    document.body.appendChild(script);
  }

  loadNext(0);
}

function setupMobileNav() {
  const toggle = document.getElementById("mobileNavToggle");
  const sidebar = document.getElementById("sidebar");
  if (!toggle || !sidebar) return;

  toggle.addEventListener("click", () => {
    sidebar.classList.toggle("is-open");
  });

  document.addEventListener("click", (event) => {
    if (!sidebar.classList.contains("is-open")) return;
    if (event.target === toggle || sidebar.contains(event.target)) return;
    sidebar.classList.remove("is-open");
  });
}

// Prices are hidden everywhere until the user explicitly asks to see them.
function setupPriceToggles() {
  document.querySelectorAll("[data-price-toggle]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const targetId = btn.getAttribute("data-price-toggle");
      const target = document.getElementById(targetId);
      if (!target) return;
      const isHidden = target.classList.contains("hidden-price");
      target.classList.toggle("hidden-price");
      btn.textContent = isHidden ? "Hide Price" : "Show Price";
    });
  });
}

// Product add/edit form: only show the spec fields that belong to the
// currently selected category. Each category's fields are rendered once,
// in a <div data-category-fields="ID">, and we just show/hide.
function setupCategoryFieldSwitcher() {
  const select = document.getElementById("categorySelect");
  if (!select) return;

  const groups = document.querySelectorAll("[data-category-fields]");

  function applyVisibility() {
    const selected = select.value;
    groups.forEach((g) => {
      g.style.display = g.getAttribute("data-category-fields") === selected ? "" : "none";
    });
  }

  select.addEventListener("change", applyVisibility);
  applyVisibility();
}

