// Al-Qemma — small, dependency-free interactions. No build step on
// purpose: this has to keep working on a shop PC for years.

document.addEventListener("DOMContentLoaded", () => {
  setupMobileNav();
  setupPriceToggles();
  setupCategoryFieldSwitcher();
});

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

