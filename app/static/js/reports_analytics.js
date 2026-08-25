/**
 * Admin-only analytics block embedded in reports.html.
 * Expects window.REPORTS_ANALYTICS_CONFIG = { apiUrl, defaultTimeframe }
 */
(function () {
  "use strict";

  var cfg = window.REPORTS_ANALYTICS_CONFIG || {};
  var root = document.getElementById("reportsAnalytics");
  if (!root) return;

  var privacyKey = "reportsAnalyticsPrivacy";
  var privacyOn = false;
  try { privacyOn = localStorage.getItem(privacyKey) === "1"; } catch (e) {}

  var charts = {
    profit: null,
    purchases: null,
    stockAtRisk: null,
  };

  var moneyFmt = new Intl.NumberFormat("en-EG", {
    maximumFractionDigits: 0,
    minimumFractionDigits: 0,
  });

  function money(n) {
    if (privacyOn) return "••••••";
    var v = Number(n) || 0;
    return moneyFmt.format(v) + " EGP";
  }

  // Reads the app's live theme tokens (style.css :root / [data-theme="dark"])
  // instead of hardcoding colors, so charts match whichever theme is
  // active - including a runtime switch, since this is re-read on redraw.
  function themeColors() {
    var cs = getComputedStyle(document.documentElement);
    function tok(name, fallback) {
      var v = cs.getPropertyValue(name);
      return v && v.trim() ? v.trim() : fallback;
    }
    return {
      text: tok("--text-muted", "#6b7280"),
      grid: tok("--border", "#e5e7eb"),
    };
  }

  function setPrivacy(on) {
    privacyOn = !!on;
    root.classList.toggle("privacy-on", privacyOn);
    var btn = document.getElementById("raPrivacyBtn");
    if (btn) {
      btn.setAttribute("aria-pressed", privacyOn ? "true" : "false");
      btn.querySelector(".ra-privacy-label").textContent = privacyOn
        ? "إظهار الأرقام"
        : "إخفاء الأرقام";
    }
    try { localStorage.setItem(privacyKey, privacyOn ? "1" : "0"); } catch (e) {}
    if (root._lastPayload) applyKpis(root._lastPayload.kpis);
    redrawForTheme();
  }

  function tickCallback(value) {
    if (privacyOn) return "•••";
    if (Math.abs(value) >= 1000) return (value / 1000).toFixed(value % 1000 === 0 ? 0 : 1) + "k";
    return String(value);
  }

  function tooltipLabel(ctx) {
    var label = ctx.dataset.label || "";
    var val = ctx.parsed.y != null ? ctx.parsed.y : ctx.parsed;
    if (privacyOn) return label + ": ••••••";
    return label + ": " + moneyFmt.format(val || 0) + " EGP";
  }

  function baseOptions() {
    var c = themeColors();
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 350, easing: "easeOutQuad" },
      plugins: {
        legend: {
          labels: { color: c.text, boxWidth: 12, usePointStyle: true },
        },
        tooltip: {
          callbacks: { label: tooltipLabel },
        },
      },
      scales: {
        x: {
          ticks: { color: c.text, maxRotation: 0, autoSkip: true, maxTicksLimit: 8 },
          grid: { color: c.grid },
        },
        y: {
          ticks: { color: c.text, callback: tickCallback },
          grid: { color: c.grid },
        },
      },
    };
  }

  function mergeOptions(extra) {
    var base = baseOptions();
    return Object.assign({}, base, extra || {}, {
      plugins: Object.assign({}, base.plugins, (extra && extra.plugins) || {}),
      scales: Object.assign({}, base.scales, (extra && extra.scales) || {}),
    });
  }

  function destroyChart(key) {
    if (charts[key]) {
      charts[key].destroy();
      charts[key] = null;
    }
  }

  function upsertLine(key, canvasId, data) {
    var canvas = document.getElementById(canvasId);
    if (!canvas || typeof Chart === "undefined" || !data) return;
    destroyChart(key);
    charts[key] = new Chart(canvas.getContext("2d"), {
      type: "line",
      data: data,
      options: mergeOptions({
        interaction: { mode: "index", intersect: false },
        elements: { point: { radius: 2, hoverRadius: 5 } },
      }),
    });
  }

  function upsertBar(key, canvasId, data, stacked) {
    var canvas = document.getElementById(canvasId);
    if (!canvas || typeof Chart === "undefined" || !data) return;
    destroyChart(key);
    var base = baseOptions();
    charts[key] = new Chart(canvas.getContext("2d"), {
      type: "bar",
      data: data,
      options: mergeOptions({
        scales: {
          x: Object.assign({}, base.scales.x, { stacked: !!stacked }),
          y: Object.assign({}, base.scales.y, { stacked: !!stacked }),
        },
      }),
    });
  }

  function applyKpis(k) {
    var map = {
      raKpiProfit: k.net_profit,
      raKpiPurchases: k.purchases,
      raKpiAtRisk: k.total_at_risk_value,
    };
    Object.keys(map).forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.textContent = money(map[id]);
    });
    
    // Secondary value for profit: show potential profit if there's outstanding debt
    var secondaryEl = document.getElementById("raKpiProfitSecondary");
    if (secondaryEl) {
      if (k.outstanding_debt > 0.01) {
        secondaryEl.innerHTML = 
          '<div class="potential-value">≈ ' + money(k.potential_net_profit) + '</div>' +
          '<div>بعد تحصيل المديونيات</div>';
      } else {
        secondaryEl.innerHTML = 
          '<div class="potential-value">✓</div>' +
          '<div>تم تحصيل كل المديونيات</div>';
      }
    }
    
    var stagnantHint = document.getElementById("raKpiStagnantHint");
    if (stagnantHint) stagnantHint.textContent = money(k.stagnant_value);
    var damagedHint = document.getElementById("raKpiDamagedHint");
    if (damagedHint) damagedHint.textContent = money(k.damaged_value);
    var chartTotal = document.getElementById("raChartAtRiskTotal");
    if (chartTotal) chartTotal.textContent = money(k.total_at_risk_value);
  }

  function renderCharts(payload) {
    var c = payload.charts || {};
    try {
      upsertLine("profit", "chartProfitRevenue", c.profit_revenue);
      upsertBar("purchases", "chartPurchases", c.purchases_expected, false);
      upsertBar("stockAtRisk", "chartStockAtRisk", c.stock_at_risk, true);
    } catch (err) {
      setStatus(
        "تعذر رسم المخططات (المحتوى النصي ما زال ظاهراً): " +
          (err && err.message ? err.message : err)
      );
    }
  }

  // Re-render existing charts in place with fresh theme colors, without
  // re-fetching data from the server.
  function redrawForTheme() {
    if (!root._lastPayload) return;
    renderCharts(root._lastPayload);
  }

  function setStatus(msg) {
    var el = document.getElementById("raStatus");
    if (!el) return;
    if (!msg) {
      el.classList.remove("is-on");
      el.textContent = "";
      return;
    }
    el.textContent = msg;
    el.classList.add("is-on");
  }

  function setLoading(on) {
    root.classList.toggle("ra-loading", !!on);
  }

  function updateMeta(payload) {
    var el = document.getElementById("raMeta");
    if (!el) return;
    el.textContent =
      "الفترة: " +
      (payload.date_from || "") +
      " → " +
      (payload.date_to || "") +
      " · " +
      (payload.timeframe_label || payload.timeframe || "");
  }

  function setActiveTab(tf) {
    root.querySelectorAll(".ra-tab").forEach(function (btn) {
      btn.classList.toggle("is-active", btn.getAttribute("data-timeframe") === tf);
    });
  }

  function fetchAnalytics(timeframe) {
    var url = (cfg.apiUrl || "/reports/api/analytics") + "?timeframe=" + encodeURIComponent(timeframe);
    setLoading(true);
    setStatus("");
    return fetch(url, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (payload) {
        root._lastPayload = payload;
        applyKpis(payload.kpis || {});
        renderCharts(payload);
        updateMeta(payload);
        setActiveTab(payload.timeframe || timeframe);
      })
      .catch(function (err) {
        setStatus("تعذر تحميل التحليلات: " + (err && err.message ? err.message : err));
      })
      .finally(function () {
        setLoading(false);
      });
  }

  root.querySelectorAll(".ra-tab").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var tf = btn.getAttribute("data-timeframe");
      if (!tf) return;
      fetchAnalytics(tf);
    });
  });

  var privacyBtn = document.getElementById("raPrivacyBtn");
  if (privacyBtn) {
    privacyBtn.addEventListener("click", function () {
      setPrivacy(!privacyOn);
    });
  }

  // إخفاء التحليلات — collapses the whole analytics block (KPIs + charts),
  // purely a display toggle, doesn't stop already-fetched data.
  var analyticsToggle = document.getElementById("toggleAnalytics");
  var analyticsBody = document.getElementById("raBody");
  if (analyticsToggle && analyticsBody) {
    analyticsToggle.addEventListener("click", function () {
      var hidden = analyticsBody.style.display === "none";
      analyticsBody.style.display = hidden ? "" : "none";
      analyticsToggle.textContent = hidden ? "إخفاء التحليلات" : "إظهار التحليلات";
    });
  }

  // Live theme switching: base.html flips [data-theme] on <html> at
  // runtime (via the pywebview theme sync / any in-app toggle). Watch
  // that attribute and just re-color the already-rendered charts -
  // no re-fetch needed.
  var themeObserver = new MutationObserver(function (mutations) {
    for (var i = 0; i < mutations.length; i++) {
      if (mutations[i].attributeName === "data-theme") {
        redrawForTheme();
        break;
      }
    }
  });
  themeObserver.observe(document.documentElement, { attributes: true });

  setPrivacy(privacyOn);
  fetchAnalytics(cfg.defaultTimeframe || "6months");
})();
