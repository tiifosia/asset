const PORTFOLIO_DATA_URL = "./public/data/portfolio.json";
const MDD_DATA_URL = "./public/data/mdd.json";

const state = {
  activeView: "portfolio", // "portfolio" | "mdd"
  currency: "krw", // "krw" | "usd"
  portfolio: null,
  portfolioUpdatedAt: "",
  mddUpdatedAt: "",
  benchmarks: [],
  stocks: [],
  searchQuery: "",
  sortBy: "rank",
};

const historyCache = new Map();

const MDD_CHART_GRID = { left: 14, right: 86, top: 42, bottom: 30 };
const PORTFOLIO_CHART_GRID = { left: 14, right: 102, top: 44, bottom: 30 };

// Portfolio interactive trend chart state
let pfChartInstance = null;
let pfPieChartInstance = null;
let pfSeriesMode = "total"; // "total" | "gate" | "toss" | "kis"
let pfPieMode = "asset"; // "asset" | "account" | "category"
const PIE_PALETTE = [
  "#C3BDFF",
  "#3182f6",
  "#6366f1",
  "#38bdf8",
  "#a78bfa",
  "#14b8a6",
  "#f59e0b",
  "#f04452",
  "#ec4899",
  "#8d8d8d",
];
let pfInterval = "daily"; // "daily" | "weekly" | "yearly"
let pfScaleMode = "linear"; // "linear" | "log"
let pfRows = [];
let pfPadLeft = 0;
let pfTotalCats = 0;

// MDD modal candlestick chart state
let modalChartInstance = null;
let modalAsset = null;
let modalHistory = null;
let modalInterval = "daily";
let modalScaleMode = "linear";
let modalRows = [];
let modalPadLeft = 0;
let modalTotalCats = 0;

let pfViewport = null;
let modalViewport = null;

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function clampPct(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return 0;
  return Math.max(0, Math.min(100, num));
}

function toChartY(price, scaleMode) {
  const safe = Number(price);
  if (!Number.isFinite(safe)) return 0;
  return scaleMode === "log" ? Math.log(Math.max(safe, 1e-6)) : safe;
}

function fromChartY(val, scaleMode) {
  return scaleMode === "log" ? Math.exp(val) : val;
}

function formatPrice(value, isIndex = false) {
  const num = Number(value);
  if (!Number.isFinite(num)) return "-";
  const formatted = num.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return isIndex ? `${formatted} pt` : `$${formatted}`;
}

function formatAxisPrice(value, isIndex = false) {
  const num = Number(value);
  if (!Number.isFinite(num)) return "-";
  const decimals = Math.abs(num) >= 1000 ? 0 : 2;
  const formatted = num.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  return isIndex ? formatted : `$${formatted}`;
}

function formatMoney(value, currency = state.currency) {
  const num = Number(value);
  if (!Number.isFinite(num)) return "-";
  if (currency === "krw") {
    const abs = Math.round(Math.abs(num)).toLocaleString("en-US");
    return num < 0 ? `-₩${abs}` : `₩${abs}`;
  }
  const abs = Math.abs(num).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return num < 0 ? `-$${abs}` : `$${abs}`;
}

function formatSignedMoney(value, currency = state.currency) {
  const num = Number(value);
  if (!Number.isFinite(num)) return "-";
  if (num > 0) return `+${formatMoney(num, currency)}`;
  return formatMoney(num, currency);
}

function formatPortfolioAxisMoney(value, currency = state.currency) {
  const num = Number(value);
  if (!Number.isFinite(num)) return "-";
  if (currency === "krw") {
    const abs = Math.round(Math.abs(num)).toLocaleString("en-US");
    return num < 0 ? `-${abs}` : `${abs}`;
  }
  const decimals = Math.abs(num) >= 1000 ? 0 : 2;
  const abs = Math.abs(num).toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  return num < 0 ? `-$${abs}` : `$${abs}`;
}

function formatMarketCap(value) {
  const num = Number(value);
  if (!Number.isFinite(num) || num <= 0) return "-";
  if (num >= 1e12) return `$${(num / 1e12).toFixed(2)}T`;
  if (num >= 1e9) return `$${(num / 1e9).toFixed(1)}B`;
  if (num >= 1e6) return `$${(num / 1e6).toFixed(1)}M`;
  return `$${num.toLocaleString("en-US")}`;
}

function formatDrawdownPct(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return "-";
  if (Math.abs(num) < 0.005) return "0.00%";
  return `${num.toFixed(2)}%`;
}

function formatSignedPct(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return "0.00%";
  const prefix = num > 0 ? "+" : "";
  return `${prefix}${num.toFixed(2)}%`;
}

function formatShortDate(dateStr) {
  if (!dateStr || dateStr.length < 10) return dateStr || "";
  return `${dateStr.slice(2, 4)}.${dateStr.slice(5, 7)}.${dateStr.slice(8, 10)}`;
}

function formatUpdatedAt(utcString) {
  if (!utcString) return "-";
  const iso = utcString.replace(" UTC", "Z").replace(" ", "T");
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return utcString;
  return (
    date.toLocaleString("ko-KR", {
      timeZone: "Asia/Seoul",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }) + " KST"
  );
}

function formatQuantity(qty) {
  const num = Number(qty);
  if (!Number.isFinite(num)) return "-";
  if (Number.isInteger(num) || Math.abs(num) >= 1000) {
    return num.toLocaleString("en-US", { maximumFractionDigits: 2 });
  }
  return num.toLocaleString("en-US", { maximumFractionDigits: 6 });
}

function buildGaugeHtml(item, showFloatingTag = true) {
  const curAbs = clampPct(item.current_dd_abs);
  const maxAbs = clampPct(item.max_dd_abs);
  const maxPctLabel = formatDrawdownPct(item.max_drawdown);
  const tooltip = `역대 최대 낙폭(MDD): ${maxPctLabel} (${escapeHtml(item.mdd_peak_date)} ~ ${escapeHtml(item.mdd_trough_date)})`;
  const tagAlignClass = maxAbs >= 75 ? " tag-align-left" : maxAbs <= 15 ? " tag-align-right" : "";

  return `
    <div class="gauge-track" style="--cur-dd: ${curAbs}%; --max-dd: ${maxAbs}%;" title="${tooltip}">
      <div class="gauge-clip">
        <div class="gauge-fill"></div>
      </div>
      <div class="gauge-mdd-marker" aria-label="${tooltip}">
        ${showFloatingTag ? `<span class="gauge-mdd-tag${tagAlignClass}">MDD ${maxPctLabel}</span>` : ""}
      </div>
    </div>
  `;
}

/* ---------- Shared Interactive Chart Viewport Controller ---------- */

function createChartViewportController(cfg) {
  const viewState = {
    xStart: 0,
    xEnd: 0,
    yMin: 0,
    yMax: 1,
    yAutoFit: true,
  };
  let calloutRafId = null;
  let viewRafId = null;
  let panActive = false;
  let panLastX = 0;
  let panLastY = 0;
  let yDragActive = false;
  let yDragStartY = 0;
  let yDragStartMin = 0;
  let yDragStartMax = 1;

  const getVisibleRealRange = () => {
    const n = cfg.getRowsLength();
    if (!n) return [0, 0];
    const padLeft = cfg.getPadLeft();
    const maxRealIdx = n - 1;
    const rawStart = Math.ceil(viewState.xStart - padLeft);
    const rawEnd = Math.floor(viewState.xEnd - padLeft);
    const s = Math.max(0, Math.min(maxRealIdx, rawStart));
    const e = Math.max(
      s,
      Math.min(maxRealIdx, rawEnd >= rawStart ? rawEnd : Math.round(viewState.xEnd - padLeft))
    );
    return [s, e];
  };

  const clampXWindow = (nextStart, nextEnd) => {
    const n = cfg.getRowsLength();
    const padLeft = cfg.getPadLeft();
    const maxCatIdx = Math.max(1, cfg.getTotalCats() - 1);
    if (!n) return { start: 0, end: maxCatIdx };

    const span = Math.max(1, nextEnd - nextStart);
    const minReal = Math.min(2, n);
    const minAllowedEnd = padLeft + minReal - 1;
    const maxAllowedStart = padLeft + n - minReal;

    let start = nextStart;
    let end = nextEnd;

    if (end < minAllowedEnd) {
      end = minAllowedEnd;
      start = end - span;
    }
    if (start > maxAllowedStart) {
      start = maxAllowedStart;
      end = start + span;
    }
    if (start < 0) {
      start = 0;
      end = Math.min(maxCatIdx, span);
    }
    if (end > maxCatIdx) {
      end = maxCatIdx;
      start = Math.max(0, maxCatIdx - span);
    }
    return { start, end };
  };

  const getDataZoomRangePct = () => {
    const maxCatIdx = Math.max(1, cfg.getTotalCats() - 1);
    const start = Math.max(0, Math.min(100, (viewState.xStart / maxCatIdx) * 100));
    const end = Math.max(start, Math.min(100, (viewState.xEnd / maxCatIdx) * 100));
    return { start, end };
  };

  const computeAutoYBounds = (catStart, catEnd) => {
    const n = cfg.getRowsLength();
    if (!n) return { min: 0, max: 1 };
    const padLeft = cfg.getPadLeft();
    const maxRealIdx = n - 1;
    const s = Math.max(0, Math.min(maxRealIdx, Math.floor(catStart - padLeft)));
    const e = Math.max(s, Math.min(maxRealIdx, Math.ceil(catEnd - padLeft)));
    let visLow = Infinity;
    let visHigh = -Infinity;
    for (let i = s; i <= e; i += 1) {
      const [lowY, highY] = cfg.getRowHighLowY(i);
      if (Number.isFinite(lowY) && lowY < visLow) visLow = lowY;
      if (Number.isFinite(highY) && highY > visHigh) visHigh = highY;
    }
    if (!Number.isFinite(visLow) || !Number.isFinite(visHigh)) {
      return { min: 0, max: 1 };
    }
    const mid = (visLow + visHigh) / 2;
    const isLog = cfg.isLogScale();
    const minSpan = isLog ? 0.02 : Math.max(Math.abs(mid) * 0.01, 0.01);
    const span = Math.max(visHigh - visLow, minSpan);
    const halfSpan = (span / 2) * 1.36;
    return {
      min: mid - halfSpan,
      max: mid + halfSpan,
    };
  };

  const updateCallouts = () => {
    const chart = cfg.getChart();
    const n = cfg.getRowsLength();
    if (!chart || !n) {
      cfg.peakEl.classList.add("hidden");
      cfg.troughEl.classList.add("hidden");
      return;
    }

    const [startIdx, endIdx] = getVisibleRealRange();
    let peakIdx = startIdx;
    let troughIdx = startIdx;
    let peakPrice = -Infinity;
    let troughPrice = Infinity;
    let peakAnchor = null;
    let troughAnchor = null;

    for (let i = startIdx; i <= endIdx; i += 1) {
      const info = cfg.getCalloutInfo(i);
      if (info.highPrice > peakPrice) {
        peakPrice = info.highPrice;
        peakIdx = i;
        peakAnchor = info;
      }
      if (info.lowPrice < troughPrice) {
        troughPrice = info.lowPrice;
        troughIdx = i;
        troughAnchor = info;
      }
    }

    if (!peakAnchor || !troughAnchor) {
      cfg.peakEl.classList.add("hidden");
      cfg.troughEl.classList.add("hidden");
      return;
    }

    const refPrice = cfg.getRefPrice();
    const peakPct = peakPrice > 0 ? ((refPrice - peakPrice) / peakPrice) * 100 : 0;
    const troughPct = troughPrice > 0 ? ((refPrice - troughPrice) / troughPrice) * 100 : 0;
    const padLeft = cfg.getPadLeft();

    const peakPx = chart.convertToPixel({ seriesIndex: 0 }, [peakIdx + padLeft, peakAnchor.highY]);
    const troughPx = chart.convertToPixel({ seriesIndex: 0 }, [troughIdx + padLeft, troughAnchor.lowY]);

    const stageWidth = cfg.stageEl.clientWidth;
    const stageHeight = cfg.stageEl.clientHeight;
    const grid = cfg.getGrid();
    const plotMinX = grid.left;
    const plotMaxX = stageWidth - grid.right - 4;

    if (
      Array.isArray(peakPx) &&
      Number.isFinite(peakPx[0]) &&
      Number.isFinite(peakPx[1]) &&
      peakPx[0] >= plotMinX &&
      peakPx[0] <= stageWidth - grid.right &&
      peakPx[1] >= 18 &&
      peakPx[1] <= stageHeight - grid.bottom - 4
    ) {
      const peakDate = formatShortDate(peakAnchor.date);
      cfg.peakEl.innerHTML = `<div class="callout-label">${cfg.formatCalloutPrice(peakPrice)} (${formatSignedPct(peakPct)}, ${peakDate})</div><div class="callout-arrow">↓</div>`;
      cfg.peakEl.classList.remove("hidden");
      const w = cfg.peakEl.offsetWidth;
      const h = cfg.peakEl.offsetHeight;
      const left = Math.max(8, Math.min(plotMaxX - w, peakPx[0] - w / 2));
      const top = Math.max(6, peakPx[1] - h - 2);
      const arrowX = Math.max(6, Math.min(w - 6, peakPx[0] - left));
      cfg.peakEl.style.left = `${left}px`;
      cfg.peakEl.style.top = `${top}px`;
      const arrowEl = cfg.peakEl.querySelector(".callout-arrow");
      if (arrowEl) arrowEl.style.left = `${arrowX}px`;
    } else {
      cfg.peakEl.classList.add("hidden");
    }

    if (
      Array.isArray(troughPx) &&
      Number.isFinite(troughPx[0]) &&
      Number.isFinite(troughPx[1]) &&
      troughPx[0] >= plotMinX &&
      troughPx[0] <= stageWidth - grid.right &&
      troughPx[1] >= 18 &&
      troughPx[1] <= stageHeight - grid.bottom - 4
    ) {
      const troughDate = formatShortDate(troughAnchor.date);
      cfg.troughEl.innerHTML = `<div class="callout-arrow">↑</div><div class="callout-label">${cfg.formatCalloutPrice(troughPrice)} (${formatSignedPct(troughPct)}, ${troughDate})</div>`;
      cfg.troughEl.classList.remove("hidden");
      const w = cfg.troughEl.offsetWidth;
      const h = cfg.troughEl.offsetHeight;
      const left = Math.max(8, Math.min(plotMaxX - w, troughPx[0] - w / 2));
      const top = Math.min(stageHeight - grid.bottom - h - 2, troughPx[1] + 2);
      const arrowX = Math.max(6, Math.min(w - 6, troughPx[0] - left));
      cfg.troughEl.style.left = `${left}px`;
      cfg.troughEl.style.top = `${top}px`;
      const arrowEl = cfg.troughEl.querySelector(".callout-arrow");
      if (arrowEl) arrowEl.style.left = `${arrowX}px`;
    } else {
      cfg.troughEl.classList.add("hidden");
    }
  };

  const scheduleCalloutsUpdate = () => {
    if (calloutRafId) cancelAnimationFrame(calloutRafId);
    calloutRafId = requestAnimationFrame(() => {
      calloutRafId = null;
      updateCallouts();
    });
  };

  const applyViewState = () => {
    const chart = cfg.getChart();
    if (!chart || !cfg.getRowsLength()) return;
    const { start, end } = getDataZoomRangePct();
    chart.setOption({
      dataZoom: [{ start, end }],
      yAxis: { min: viewState.yMin, max: viewState.yMax },
    });
    updateCallouts();
  };

  const scheduleViewUpdate = () => {
    if (viewRafId) cancelAnimationFrame(viewRafId);
    viewRafId = requestAnimationFrame(() => {
      viewRafId = null;
      applyViewState();
    });
  };

  const zoomYByFactor = (factor) => {
    if (!cfg.getChart() || !cfg.getRowsLength()) return;
    viewState.yAutoFit = false;
    const midY = (viewState.yMin + viewState.yMax) / 2;
    const spanY = Math.max(1e-6, viewState.yMax - viewState.yMin);
    const isLog = cfg.isLogScale();
    const minSpanY = isLog ? 0.005 : Math.max(Math.abs(midY) * 0.002, 0.002);
    const maxSpanY = isLog ? 15.0 : Math.max(Math.abs(midY) * 20, 100);
    const nextSpanY = Math.max(minSpanY, Math.min(maxSpanY, spanY * factor));
    viewState.yMin = midY - nextSpanY / 2;
    viewState.yMax = midY + nextSpanY / 2;
    scheduleViewUpdate();
  };

  const resetAutoYFit = () => {
    if (!cfg.getChart() || !cfg.getRowsLength()) return;
    viewState.yAutoFit = true;
    const bounds = computeAutoYBounds(viewState.xStart, viewState.xEnd);
    viewState.yMin = bounds.min;
    viewState.yMax = bounds.max;
    scheduleViewUpdate();
  };

  // Capture-phase mouse & wheel listeners so ECharts ZRender cannot swallow 2D drags
  cfg.canvasEl.addEventListener(
    "mousedown",
    (e) => {
      if (e.button !== 0 || !cfg.getChart() || !cfg.getRowsLength()) return;
      panActive = true;
      panLastX = e.clientX;
      panLastY = e.clientY;
      cfg.canvasEl.classList.add("is-dragging");
      e.preventDefault();
    },
    { capture: true }
  );

  cfg.canvasEl.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      if (!cfg.getChart() || !cfg.getRowsLength()) return;
      const grid = cfg.getGrid();
      const rect = cfg.canvasEl.getBoundingClientRect();
      const plotWidth = Math.max(1, rect.width - grid.left - grid.right);
      const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left - grid.left) / plotWidth));
      const spanX = Math.max(1, viewState.xEnd - viewState.xStart);

      if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
        const deltaBars = (e.deltaX / plotWidth) * spanX;
        const clamped = clampXWindow(viewState.xStart + deltaBars, viewState.xEnd + deltaBars);
        viewState.xStart = clamped.start;
        viewState.xEnd = clamped.end;
      } else {
        const n = cfg.getRowsLength();
        const minSpan = Math.min(Math.max(4, n), cfg.getMinZoomSpan());
        const maxSpan = Math.max(n * 1.8, 30);
        const factor = e.deltaY > 0 ? 1.15 : 0.87;
        const nextSpan = Math.max(minSpan, Math.min(maxSpan, spanX * factor));

        const anchor = viewState.xStart + ratio * spanX;
        const rawStart = anchor - ratio * nextSpan;
        const rawEnd = rawStart + nextSpan;
        const clamped = clampXWindow(rawStart, rawEnd);
        viewState.xStart = clamped.start;
        viewState.xEnd = clamped.end;
      }

      if (viewState.yAutoFit) {
        const bounds = computeAutoYBounds(viewState.xStart, viewState.xEnd);
        viewState.yMin = bounds.min;
        viewState.yMax = bounds.max;
      }
      scheduleViewUpdate();
    },
    { capture: true, passive: false }
  );

  cfg.canvasEl.addEventListener("dblclick", resetAutoYFit);

  cfg.yAxisZoneEl.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      e.stopPropagation();
      zoomYByFactor(e.deltaY > 0 ? 1.12 : 0.89);
    },
    { passive: false }
  );

  cfg.yAxisZoneEl.addEventListener("mousedown", (e) => {
    if (e.button !== 0 || !cfg.getChart() || !cfg.getRowsLength()) return;
    yDragActive = true;
    yDragStartY = e.clientY;
    yDragStartMin = viewState.yMin;
    yDragStartMax = viewState.yMax;
    e.preventDefault();
  });

  cfg.yAxisZoneEl.addEventListener("dblclick", resetAutoYFit);

  window.addEventListener(
    "mousemove",
    (e) => {
      if (panActive && cfg.getChart() && cfg.getRowsLength()) {
        const dx = e.clientX - panLastX;
        const dy = e.clientY - panLastY;
        panLastX = e.clientX;
        panLastY = e.clientY;

        const grid = cfg.getGrid();
        const plotWidth = Math.max(1, cfg.stageEl.clientWidth - grid.left - grid.right);
        const plotHeight = Math.max(1, cfg.stageEl.clientHeight - grid.top - grid.bottom);

        if (dx !== 0) {
          const spanX = Math.max(1, viewState.xEnd - viewState.xStart);
          const deltaBars = -(dx / plotWidth) * spanX;
          const clamped = clampXWindow(viewState.xStart + deltaBars, viewState.xEnd + deltaBars);
          viewState.xStart = clamped.start;
          viewState.xEnd = clamped.end;
        }

        if (dy !== 0) {
          viewState.yAutoFit = false;
          const spanY = Math.max(1e-6, viewState.yMax - viewState.yMin);
          const deltaY = (dy / plotHeight) * spanY;
          viewState.yMin += deltaY;
          viewState.yMax += deltaY;
        }

        scheduleViewUpdate();
        return;
      }

      if (yDragActive && cfg.getChart() && cfg.getRowsLength()) {
        viewState.yAutoFit = false;
        const dy = e.clientY - yDragStartY;
        const midY = (yDragStartMin + yDragStartMax) / 2;
        const startSpanY = Math.max(1e-6, yDragStartMax - yDragStartMin);
        const isLog = cfg.isLogScale();
        const minSpanY = isLog ? 0.005 : Math.max(Math.abs(midY) * 0.002, 0.002);
        const maxSpanY = isLog ? 15.0 : Math.max(Math.abs(midY) * 20, 100);
        const nextSpanY = Math.max(minSpanY, Math.min(maxSpanY, startSpanY * Math.exp(dy * 0.008)));
        viewState.yMin = midY - nextSpanY / 2;
        viewState.yMax = midY + nextSpanY / 2;
        scheduleViewUpdate();
      }
    },
    { capture: true }
  );

  window.addEventListener(
    "mouseup",
    () => {
      if (panActive) {
        panActive = false;
        cfg.canvasEl.classList.remove("is-dragging");
      }
      yDragActive = false;
    },
    { capture: true }
  );

  return {
    viewState,
    initWindow(initStart, initEnd) {
      viewState.xStart = initStart;
      viewState.xEnd = initEnd;
      viewState.yAutoFit = true;
      const bounds = computeAutoYBounds(initStart, initEnd);
      viewState.yMin = bounds.min;
      viewState.yMax = bounds.max;
      return getDataZoomRangePct();
    },
    scheduleCalloutsUpdate,
  };
}

/* ---------- View Switcher (Portfolio Main <-> MDD Tracker) ---------- */

function updateHeaderForView() {
  const isPortfolio = state.activeView === "portfolio";
  document.getElementById("portfolio-view").classList.toggle("hidden", !isPortfolio);
  document.getElementById("mdd-view").classList.toggle("hidden", isPortfolio);

  const eyebrowEl = document.getElementById("header-eyebrow");
  const titleEl = document.getElementById("header-title");
  const subtitleEl = document.getElementById("header-subtitle");
  const currencyGroupEl = document.getElementById("currency-group");
  const legendEl = document.getElementById("header-mdd-legend");
  const footerEl = document.getElementById("site-footer");
  const toggleBtn = document.getElementById("nav-toggle-view-btn");
  const updatedEl = document.getElementById("updated-at");

  eyebrowEl?.classList.toggle("hidden", isPortfolio);
  subtitleEl?.classList.toggle("hidden", isPortfolio);
  currencyGroupEl?.classList.toggle("hidden", !isPortfolio);
  legendEl?.classList.toggle("hidden", isPortfolio);
  footerEl?.classList.toggle("hidden", isPortfolio);

  if (isPortfolio) {
    document.title = "MY ASSET";
    eyebrowEl.textContent = "";
    titleEl.textContent = "MY ASSET";
    subtitleEl.textContent = "";
    toggleBtn.textContent = "MDD check";
    updatedEl.textContent = state.portfolioUpdatedAt ? formatUpdatedAt(state.portfolioUpdatedAt) : "-";
  } else {
    document.title = "ASSET MDD TRACKER";
    eyebrowEl.textContent = "US MARKET DRAWDOWN DASHBOARD";
    titleEl.textContent = "ASSET MDD TRACKER";
    subtitleEl.textContent =
      "미국 3대 주요 지수 및 시가총액 상위 100대 기업의 전고점(ATH) 대비 현재 낙폭과 역대 최대 낙폭(MDD)";
    toggleBtn.textContent = "← MY ASSET";
    updatedEl.textContent = state.mddUpdatedAt ? formatUpdatedAt(state.mddUpdatedAt) : "-";
  }
}

function switchMainView(nextView) {
  state.activeView = nextView === "mdd" ? "mdd" : "portfolio";
  updateHeaderForView();
  if (window.history && window.history.replaceState) {
    const hash = state.activeView === "mdd" ? "#mdd" : "#portfolio";
    if (window.location.hash !== hash) {
      window.history.replaceState(null, "", hash);
    }
  }
  if (state.activeView === "portfolio") {
    requestAnimationFrame(() => {
      if (pfChartInstance) {
        pfChartInstance.resize();
        pfViewport?.scheduleCalloutsUpdate();
      }
      pfPieChartInstance?.resize();
    });
  }
}

/* ---------- Portfolio View Rendering ---------- */

function getPortfolioProviderLabel(statusObj) {
  if (!statusObj || statusObj.status === "not_configured") {
    return { text: "미연결", cls: "is-demo" };
  }
  if (statusObj.status === "connected") {
    return { text: "연결됨", cls: "is-live" };
  }
  return { text: "조회 오류", cls: "is-error" };
}

function getSelectedSeriesVal(row) {
  const cur = state.currency;
  if (pfSeriesMode === "gate") return cur === "krw" ? Number(row.gate_krw || 0) : Number(row.gate_usd || 0);
  if (pfSeriesMode === "toss") return cur === "krw" ? Number(row.toss_krw || 0) : Number(row.toss_usd || 0);
  if (pfSeriesMode === "kis") return cur === "krw" ? Number(row.kis_krw || 0) : Number(row.kis_usd || 0);
  return cur === "krw" ? Number(row.total_krw || 0) : Number(row.total_usd || 0);
}

function getPortfolioIntervalRows(history, interval) {
  if (!Array.isArray(history) || !history.length) return [];
  const baseRows = history.map((row) => ({
    ...row,
    val: getSelectedSeriesVal(row),
    lowVal: getSelectedSeriesVal(row),
    highVal: getSelectedSeriesVal(row),
  }));

  if (interval === "daily") return baseRows;

  const grouped = new Map();
  for (const r of baseRows) {
    let key = r.date;
    if (interval === "weekly") {
      const dt = new Date(`${r.date}T00:00:00Z`);
      const dayNum = dt.getUTCDay() || 7;
      dt.setUTCDate(dt.getUTCDate() + 4 - dayNum);
      const yearStart = new Date(Date.UTC(dt.getUTCFullYear(), 0, 1));
      const weekNo = Math.ceil(((dt - yearStart) / 86400000 + 1) / 7);
      key = `${dt.getUTCFullYear()}-W${String(weekNo).padStart(2, "0")}`;
    } else if (interval === "monthly") {
      key = r.date.slice(0, 7);
    } else if (interval === "yearly") {
      key = r.date.slice(0, 4);
    }

    if (!grouped.has(key)) {
      grouped.set(key, { ...r });
    } else {
      const cur = grouped.get(key);
      cur.date = r.date;
      cur.total_krw = r.total_krw;
      cur.total_usd = r.total_usd;
      cur.gate_krw = r.gate_krw;
      cur.gate_usd = r.gate_usd;
      cur.toss_krw = r.toss_krw;
      cur.toss_usd = r.toss_usd;
      cur.kis_krw = r.kis_krw;
      cur.kis_usd = r.kis_usd;
      cur.usdkrw = r.usdkrw;
      cur.val = r.val;
      cur.lowVal = Math.min(cur.lowVal, r.lowVal);
      cur.highVal = Math.max(cur.highVal, r.highVal);
    }
  }
  return Array.from(grouped.values());
}

function updatePortfolioHoverBar(idx) {
  const barEl = document.getElementById("portfolio-hover-bar");
  if (!pfRows.length || idx < 0 || idx >= pfRows.length) {
    barEl.innerHTML = "";
    return;
  }
  const r = pfRows[idx];
  const cur = state.currency;
  const totalTxt = formatMoney(cur === "krw" ? r.total_krw : r.total_usd, cur);
  const gateTxt = formatMoney(cur === "krw" ? r.gate_krw : r.gate_usd, cur);
  const tossTxt = formatMoney(cur === "krw" ? r.toss_krw : r.toss_usd, cur);
  const kisTxt = formatMoney(cur === "krw" ? r.kis_krw : r.kis_usd, cur);
  const fx = Number(r.usdkrw || 0).toLocaleString("en-US", { maximumFractionDigits: 2 });
  const usdtFx = r.usdt_krw ? Number(r.usdt_krw).toLocaleString("en-US", { maximumFractionDigits: 2 }) : null;

  barEl.innerHTML = `
    <span class="ohlc-date">${escapeHtml(r.date)}</span>
    <span>합계 ${escapeHtml(totalTxt)}</span>
    <span>Gate ${escapeHtml(gateTxt)}</span>
    <span>토스 ${escapeHtml(tossTxt)}</span>
    <span>한투ISA ${escapeHtml(kisTxt)}</span>
    <span>환율 ₩${escapeHtml(fx)}${usdtFx ? ` · 테더 ₩${escapeHtml(usdtFx)}` : ""}</span>
  `;
}

function renderPortfolioStatusBanner() {
  const data = state.portfolio;
  const banner = document.getElementById("portfolio-status-banner");
  const fxEl = document.getElementById("portfolio-fx-note");

  banner?.classList.add("hidden");
  if (!data) return;

  const fxStr = Number(data.usdkrw || 1400).toLocaleString("en-US", { maximumFractionDigits: 2 });
  const usdtVal = data.usdt_krw || data.summary?.gateio?.usdt_krw;
  const usdtPart = usdtVal
    ? ` · 업비트 테더(USDT) ₩${Number(usdtVal).toLocaleString("en-US", { maximumFractionDigits: 2 })}`
    : "";
  fxEl.textContent = `적용 환율 ₩${fxStr} / $1${usdtPart} · 매일 06:30 KST 자동 갱신`;
}

function getPnlColorClass(val) {
  const num = Number(val || 0);
  if (num > 0) return "is-plus";
  if (num < 0) return "is-minus";
  return "is-neutral";
}

function buildAccountSummaryCardHtml({ title, linkUrl = "", totalMain, totalAlt, isTotal = false, stats = [] }) {
  const rowsHtml = stats
    .map(
      ({ label, value, colorClass = "" }) => `
        <div class="pf-stat-row">
          <span class="pf-stat-label">${escapeHtml(label)}</span>
          <span class="pf-stat-val${colorClass ? ` ${colorClass}` : ""}">${escapeHtml(value)}</span>
        </div>
      `
    )
    .join("");

  return `
    <article class="portfolio-card${isTotal ? " portfolio-card-total" : ""}">
      <div>
        <div class="pf-card-head">
          <div class="pf-card-title">${escapeHtml(title)}</div>
          ${linkUrl ? `<a class="pf-card-link" href="${escapeHtml(linkUrl)}" target="_blank" rel="noopener noreferrer">바로 가기</a>` : ""}
        </div>
        <div class="pf-hero-block">
          <div class="pf-big-value tabular">${escapeHtml(totalMain)}</div>
          <div class="pf-sub-value tabular">${escapeHtml(totalAlt)}</div>
        </div>
      </div>

      <div class="pf-stat-list tabular">
        ${rowsHtml}
      </div>
    </article>
  `;
}

function renderPortfolioSummaryCards() {
  const grid = document.getElementById("portfolio-summary-grid");
  const data = state.portfolio;
  if (!data || !data.summary) {
    grid.innerHTML = `<div class="empty-state">자산 요약 데이터가 없습니다.</div>`;
    return;
  }

  const s = data.summary;
  const cur = state.currency;
  const alt = cur === "krw" ? "usd" : "krw";

  const totalMain = formatMoney(cur === "krw" ? s.total_krw : s.total_usd, cur);
  const totalAlt = formatMoney(alt === "krw" ? s.total_krw : s.total_usd, alt);
  const dailyDeltaNum = cur === "krw" ? Number(s.daily_change_krw || 0) : Number(s.daily_change_usd || 0);
  const dailyDelta = formatSignedMoney(dailyDeltaNum, cur);
  const dailyPctNum = Number(s.daily_change_pct || 0);

  const gatePnlNum = cur === "krw" ? Number(s.gateio?.unrealized_pnl_krw || 0) : Number(s.gateio?.unrealized_pnl_usd || 0);
  const tossPnlNum = cur === "krw" ? Number(s.toss?.pnl_krw || 0) : Number(s.toss?.pnl_usd || 0);
  const kisPnlNum = cur === "krw" ? Number(s.kis?.pnl_krw || 0) : Number(s.kis?.pnl_usd || 0);
  const totalPnlNum = gatePnlNum + tossPnlNum + kisPnlNum;

  const gateMain = formatMoney(cur === "krw" ? s.gateio?.total_krw || 0 : s.gateio?.total_usd || 0, cur);
  const gateAlt = formatMoney(alt === "krw" ? s.gateio?.total_krw || 0 : s.gateio?.total_usd || 0, alt);
  const gateSpotRaw = cur === "krw" ? Number(s.gateio?.spot_krw || 0) : Number(s.gateio?.spot_usd || 0);
  const gateOtherRaw = cur === "krw" ? Number(s.gateio?.other_krw || 0) : Number(s.gateio?.other_usd || 0);
  const gateSpotLabel = gateOtherRaw > 0 ? "현물 · 기타(Earn)" : "현물 (Spot)";
  const gateSpotVal = formatMoney(gateSpotRaw + gateOtherRaw, cur);
  const gateFut = formatMoney(cur === "krw" ? s.gateio?.futures_krw || 0 : s.gateio?.futures_usd || 0, cur);

  const toss = s.toss || {};
  const tossMain = formatMoney(cur === "krw" ? toss.total_krw || 0 : toss.total_usd || 0, cur);
  const tossAlt = formatMoney(alt === "krw" ? toss.total_krw || 0 : toss.total_usd || 0, alt);
  const tossStocks = formatMoney(cur === "krw" ? toss.stocks_krw || 0 : toss.stocks_usd || 0, cur);
  const tossCash = formatMoney(cur === "krw" ? toss.cash_krw || 0 : toss.cash_usd || 0, cur);

  const kis = s.kis || {};
  const kisMain = formatMoney(cur === "krw" ? kis.total_krw || 0 : kis.total_usd || 0, cur);
  const kisAlt = formatMoney(alt === "krw" ? kis.total_krw || 0 : kis.total_usd || 0, alt);
  const kisStocks = formatMoney(cur === "krw" ? kis.stocks_krw || 0 : kis.stocks_usd || 0, cur);
  const kisCash = formatMoney(cur === "krw" ? kis.cash_krw || 0 : kis.cash_usd || 0, cur);

  grid.innerHTML = [
    buildAccountSummaryCardHtml({
      title: "TOTAL",
      isTotal: true,
      totalMain,
      totalAlt,
      stats: [
        { label: "총 평가손익", value: formatSignedMoney(totalPnlNum, cur), colorClass: getPnlColorClass(totalPnlNum) },
        { label: "전일 대비", value: dailyDelta, colorClass: getPnlColorClass(dailyDeltaNum) },
        { label: "전일 등락률", value: formatSignedPct(dailyPctNum), colorClass: getPnlColorClass(dailyPctNum) },
      ],
    }),
    buildAccountSummaryCardHtml({
      title: "Gate.io",
      linkUrl: "https://www.gate.io",
      totalMain: gateMain,
      totalAlt: gateAlt,
      stats: [
        { label: "미실현 손익", value: formatSignedMoney(gatePnlNum, cur), colorClass: getPnlColorClass(gatePnlNum) },
        { label: gateSpotLabel, value: gateSpotVal },
        { label: "선물 (Futures)", value: gateFut },
      ],
    }),
    buildAccountSummaryCardHtml({
      title: "토스증권",
      linkUrl: "https://www.tossinvest.com",
      totalMain: tossMain,
      totalAlt: tossAlt,
      stats: [
        { label: "평가 손익", value: formatSignedMoney(tossPnlNum, cur), colorClass: getPnlColorClass(tossPnlNum) },
        { label: "보유 주식", value: tossStocks },
        { label: "예수금 (KRW + USD)", value: tossCash },
      ],
    }),
    buildAccountSummaryCardHtml({
      title: "한국투자증권",
      totalMain: kisMain,
      totalAlt: kisAlt,
      stats: [
        { label: "평가 손익", value: formatSignedMoney(kisPnlNum, cur), colorClass: getPnlColorClass(kisPnlNum) },
        { label: "ISA 보유 종목", value: kisStocks },
        { label: "ISA 예수금 (KRW)", value: kisCash },
      ],
    }),
  ].join("");
}

const HOLDING_LINKS_BY_SYMBOL = {
  NAS100_USDT: "https://www.gate.com/futures/USDT/NAS100_USDT",
  SPX500_USDT: "https://www.gate.com/futures/USDT/SPX500_USDT",
  BTC_USDT: "https://www.gate.com/futures/USDT/BTC_USDT",
  TECL: "https://www.tossinvest.com/stocks/US20081217003/order",
  "0219E0": "https://www.tossinvest.com/stocks/A0219E0/order",
  BOXX: "https://www.tossinvest.com/stocks/AMX0221228001/order",
  COKE: "https://www.tossinvest.com/stocks/US19900326008/order",
  CCEP: "https://www.tossinvest.com/stocks/US20210913002/order",
};

function getHoldingLinkUrl(item) {
  if (!item || (item.exchange !== "Gate.io" && item.exchange !== "토스증권")) return "";
  return HOLDING_LINKS_BY_SYMBOL[String(item.symbol || "")] || "";
}

function renderHoldingsPanel(badgeId, listId, providerStatus, items, emptyMessage, getSubText) {
  const badgeEl = document.getElementById(badgeId);
  const listEl = document.getElementById(listId);
  if (!listEl) return;
  badgeEl?.classList.add("hidden");

  const rows = Array.isArray(items) ? items : [];
  if (!rows.length) {
    listEl.innerHTML = `<div class="empty-state">${escapeHtml(emptyMessage)}</div>`;
    return;
  }

  const cur = state.currency;
  listEl.innerHTML = rows
    .map((item) => {
      const primaryName = getSliceDisplayName(item);
      const valMain = formatMoney(cur === "krw" ? item.value_krw : item.value_usd, cur);
      const valSub = getSubText(item, cur);
      const hasPnl = item.pnl_krw != null || item.pnl_usd != null;
      const pnlColorClass = hasPnl ? getPnlColorClass(cur === "krw" ? item.pnl_krw : item.pnl_usd) : "";
      const metaSub = `${item.symbol !== primaryName ? `${item.symbol} · ` : ""}수량 ${formatQuantity(item.quantity)}`;
      const linkUrl = getHoldingLinkUrl(item);
      const tagOpen = linkUrl
        ? `<a class="holding-row holding-row-link" href="${escapeHtml(linkUrl)}" target="_blank" rel="noopener noreferrer">`
        : `<div class="holding-row">`;
      const tagClose = linkUrl ? `</a>` : `</div>`;
      return `
        ${tagOpen}
          <div class="holding-info">
            <div class="holding-sym" title="${escapeHtml(item.name || item.symbol)}">${escapeHtml(primaryName)}<span class="holding-cat">${escapeHtml(item.category)}</span></div>
            <div class="holding-sub tabular">${escapeHtml(metaSub)}</div>
          </div>
          <div class="holding-val tabular">
            <div class="holding-val-main">${escapeHtml(valMain)}</div>
            <div class="holding-val-sub${pnlColorClass ? ` ${pnlColorClass}` : ""}">${escapeHtml(valSub)}</div>
          </div>
        ${tagClose}
      `;
    })
    .join("");
}

function renderPortfolioHoldings() {
  const data = state.portfolio;
  if (!data) return;

  renderHoldingsPanel(
    "gate-provider-badge",
    "gate-holdings-list",
    data.providers?.gateio,
    data.holdings?.gateio,
    "표시할 Gate.io 보유 자산 또는 포지션이 없습니다.",
    (item, cur) =>
      item.pnl_usd != null
        ? `손익 ${formatSignedMoney(cur === "krw" ? item.pnl_krw : item.pnl_usd, cur)} (${formatSignedPct(item.pnl_pct || 0)})`
        : `단가 ${formatMoney(item.price_usd, "usd")}`
  );

  const stockSubFormatter = (item, cur) =>
    item.pnl_krw != null
      ? `손익 ${formatSignedMoney(cur === "krw" ? item.pnl_krw : item.pnl_usd, cur)} (${formatSignedPct(item.pnl_pct || 0)})`
      : `${item.currency || "KRW"} 현금성 잔고`;

  renderHoldingsPanel(
    "toss-provider-badge",
    "toss-holdings-list",
    data.providers?.toss,
    data.holdings?.toss,
    "표시할 토스증권 보유 종목 또는 예수금이 없습니다.",
    stockSubFormatter
  );

  renderHoldingsPanel(
    "kis-provider-badge",
    "kis-holdings-list",
    data.providers?.kis,
    data.holdings?.kis,
    "표시할 한국투자증권 ISA 보유 종목 또는 예수금이 없습니다.",
    stockSubFormatter
  );
}

function renderPortfolioHistoryLog() {
  const listEl = document.getElementById("portfolio-history-list");
  const history = Array.isArray(state.portfolio?.history) ? state.portfolio.history : [];
  const cur = state.currency;

  if (!history.length) {
    listEl.innerHTML = `<div class="empty-state">누적된 일별 기록이 아직 없습니다.</div>`;
    return;
  }

  const recent = history.slice().reverse();
  listEl.innerHTML = recent
    .map((row, revIdx) => {
      const origIdx = history.length - 1 - revIdx;
      const prev = origIdx > 0 ? history[origIdx - 1] : null;

      const totalVal = cur === "krw" ? Number(row.total_krw || 0) : Number(row.total_usd || 0);
      const gateVal = cur === "krw" ? Number(row.gate_krw || 0) : Number(row.gate_usd || 0);
      const tossVal = cur === "krw" ? Number(row.toss_krw || 0) : Number(row.toss_usd || 0);
      const kisVal = cur === "krw" ? Number(row.kis_krw || 0) : Number(row.kis_usd || 0);

      let deltaHtml = `<span class="history-sub">-</span>`;
      if (prev) {
        const prevVal = cur === "krw" ? Number(prev.total_krw || 0) : Number(prev.total_usd || 0);
        const diff = totalVal - prevVal;
        const pct = prevVal > 0 ? (diff / prevVal) * 100 : 0;
        deltaHtml = `<span class="pf-accent">${escapeHtml(formatSignedMoney(diff, cur))}</span><span class="history-sub">${formatSignedPct(pct)}</span>`;
      }

      const gateSpot = formatMoney(row.gate_spot_usd || 0, "usd");
      const gateFut = formatMoney(row.gate_futures_usd || 0, "usd");
      const fxTxt = `₩${Number(row.usdkrw || 1400).toLocaleString("en-US")}`;
      const usdtTxt = row.usdt_krw ? ` · USDT ₩${Number(row.usdt_krw).toLocaleString("en-US")}` : "";

      return `
        <div class="history-row tabular">
          <div class="history-date">${escapeHtml(row.date)}<span class="history-sub">${escapeHtml(fxTxt + usdtTxt)}</span></div>
          <div class="text-right"><strong>${escapeHtml(formatMoney(totalVal, cur))}</strong></div>
          <div class="text-right">${escapeHtml(formatMoney(gateVal, cur))}<span class="history-sub">현 ${escapeHtml(gateSpot)} / 선 ${escapeHtml(gateFut)}</span></div>
          <div class="text-right">${escapeHtml(formatMoney(tossVal, cur))}</div>
          <div class="text-right">${escapeHtml(formatMoney(kisVal, cur))}</div>
          <div class="text-right">${deltaHtml}</div>
        </div>
      `;
    })
    .join("");
}

function renderPortfolioChart() {
  const canvasEl = document.getElementById("portfolio-echart-canvas");
  const loadingEl = document.getElementById("portfolio-chart-loading");
  const data = state.portfolio;
  if (!data || !Array.isArray(data.history)) return;

  if (typeof window.echarts === "undefined") {
    loadingEl.textContent = "차트 라이브러리(ECharts)를 불러오지 못했습니다.";
    loadingEl.classList.remove("hidden");
    return;
  }

  const rows = getPortfolioIntervalRows(data.history, pfInterval);
  pfRows = rows;
  document.getElementById("portfolio-point-count").textContent = `${rows.length}개 구간`;

  if (!rows.length) {
    loadingEl.textContent = "표시할 자산 추이 데이터가 없습니다.";
    loadingEl.classList.remove("hidden");
    return;
  }
  loadingEl.classList.add("hidden");

  const n = rows.length;
  pfPadLeft = Math.max(80, n);
  const padRight = Math.max(80, n);
  pfTotalCats = pfPadLeft + n + padRight;

  const paddedDates = new Array(pfTotalCats).fill("");
  const paddedValues = new Array(pfTotalCats).fill(null);

  for (let i = 0; i < n; i += 1) {
    const catIdx = pfPadLeft + i;
    paddedDates[catIdx] = rows[i].date;
    paddedValues[catIdx] = toChartY(rows[i].val, pfScaleMode);
  }

  let visibleBars = n;
  if (pfInterval === "daily") visibleBars = Math.min(n, 120);
  else if (pfInterval === "weekly") visibleBars = Math.min(n, 52);
  else if (pfInterval === "monthly") visibleBars = Math.min(n, 36);

  const rightBuffer = Math.max(4, Math.round(visibleBars * 0.1));
  const leftBuffer = pfInterval === "yearly" ? Math.max(2, Math.round(visibleBars * 0.1)) : 0;
  const initStart = pfPadLeft + n - visibleBars - leftBuffer;
  const initEnd = pfPadLeft + n - 1 + rightBuffer;

  const latestVal = rows[n - 1].val;
  const cur = state.currency;

  if (!pfChartInstance) {
    pfChartInstance = window.echarts.init(canvasEl);
    pfChartInstance.on("finished", () => pfViewport?.scheduleCalloutsUpdate());
    pfChartInstance.on("updateAxisPointer", (event) => {
      const catIdx = event?.axesInfo?.[0]?.value;
      if (typeof catIdx === "number") {
        const realIdx = catIdx - pfPadLeft;
        if (realIdx >= 0 && realIdx < pfRows.length) {
          updatePortfolioHoverBar(realIdx);
        }
      }
    });
    canvasEl.addEventListener("mouseleave", () => {
      if (pfRows.length) updatePortfolioHoverBar(pfRows.length - 1);
    });
  }

  const { start: initZoomStart, end: initZoomEnd } = pfViewport.initWindow(initStart, initEnd);
  const vs = pfViewport.viewState;

  const option = {
    animation: false,
    backgroundColor: "#111111",
    grid: {
      left: PORTFOLIO_CHART_GRID.left,
      right: PORTFOLIO_CHART_GRID.right,
      top: PORTFOLIO_CHART_GRID.top,
      bottom: PORTFOLIO_CHART_GRID.bottom,
      containLabel: false,
    },
    tooltip: {
      trigger: "axis",
      axisPointer: {
        type: "cross",
        lineStyle: { color: "#8d8d8d", type: "dashed", width: 1 },
        crossStyle: { color: "#8d8d8d", type: "dashed", width: 1 },
        label: {
          backgroundColor: "#C3BDFF",
          color: "#111111",
          fontSize: 11,
          formatter: (params) => {
            if (params.axisDimension === "y") {
              return formatPortfolioAxisMoney(fromChartY(params.value, pfScaleMode), cur);
            }
            return params.value || "";
          },
        },
      },
      extraCssText: "display:none;",
      formatter: () => "",
    },
    xAxis: {
      type: "category",
      data: paddedDates,
      boundaryGap: false,
      axisLine: { lineStyle: { color: "#333333" } },
      axisTick: { show: false },
      axisLabel: { color: "#8d8d8d", fontSize: 11 },
      splitLine: {
        show: true,
        lineStyle: { color: "rgba(51, 51, 51, 0.35)" },
      },
    },
    yAxis: {
      type: "value",
      position: "right",
      scale: true,
      min: vs.yMin,
      max: vs.yMax,
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: {
        show: true,
        lineStyle: { color: "rgba(51, 51, 51, 0.5)" },
      },
      axisLabel: {
        color: "#8d8d8d",
        fontSize: 11,
        formatter: (value) => formatPortfolioAxisMoney(fromChartY(value, pfScaleMode), cur),
      },
    },
    dataZoom: [
      {
        type: "inside",
        xAxisIndex: 0,
        filterMode: "none",
        start: initZoomStart,
        end: initZoomEnd,
        zoomOnMouseWheel: false,
        moveOnMouseMove: false,
        moveOnMouseWheel: false,
      },
    ],
    series: [
      {
        name: "개인 자산 추이",
        type: "line",
        data: paddedValues,
        showSymbol: n <= 12,
        symbolSize: 6,
        lineStyle: { color: "#C3BDFF", width: 2 },
        itemStyle: { color: "#C3BDFF" },
        areaStyle: {
          color: new window.echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: "rgba(195, 189, 255, 0.24)" },
            { offset: 1, color: "rgba(195, 189, 255, 0.01)" },
          ]),
        },
        markLine: {
          symbol: ["none", "none"],
          silent: true,
          animation: false,
          data: [{ yAxis: toChartY(latestVal, pfScaleMode) }],
          lineStyle: { color: "#3182f6", type: "dotted", width: 1.5 },
          label: {
            show: true,
            position: "end",
            formatter: () => formatPortfolioAxisMoney(latestVal, cur),
            backgroundColor: "#3182f6",
            color: "#ffffff",
            fontSize: 11,
            fontWeight: 700,
            padding: [4, 7],
            borderRadius: 4,
          },
        },
      },
    ],
  };

  pfChartInstance.setOption(option, true);
  pfChartInstance.resize();
  updatePortfolioHoverBar(n - 1);
  pfViewport.scheduleCalloutsUpdate();
}

function getAllocationVal(item, cur) {
  return cur === "krw" ? Number(item?.value_krw || 0) : Number(item?.value_usd || 0);
}

function getSliceDisplayName(item) {
  const sym = String(item?.symbol || "-");
  if (sym === "KRW" || sym === "USD" || /^[0-9][0-9A-Z]{5}$/.test(sym)) {
    return String(item?.name || sym);
  }
  return sym;
}

function buildPortfolioAllocationData() {
  const data = state.portfolio;
  if (!data) return [];
  const cur = state.currency;
  const holdings = data.holdings || {};
  const summary = data.summary || {};

  let items = [];

  if (pfPieMode === "account") {
    items = [
      {
        name: "Gate.io",
        sub: "해외 거래소",
        val: cur === "krw" ? Number(summary.gateio?.total_krw || 0) : Number(summary.gateio?.total_usd || 0),
      },
      {
        name: "토스증권",
        sub: "국내 · 미국 주식",
        val: cur === "krw" ? Number(summary.toss?.total_krw || 0) : Number(summary.toss?.total_usd || 0),
      },
      {
        name: "한국투자증권 ISA",
        sub: "중개형 ISA",
        val: cur === "krw" ? Number(summary.kis?.total_krw || 0) : Number(summary.kis?.total_usd || 0),
      },
    ];
  } else if (pfPieMode === "category") {
    const gateItems = Array.isArray(holdings.gateio) ? holdings.gateio : [];
    const tossItems = Array.isArray(holdings.toss) ? holdings.toss : [];
    const kisItems = Array.isArray(holdings.kis) ? holdings.kis : [];

    const sumBy = (arr, pred) =>
      arr.reduce((acc, row) => (pred(row) ? acc + getAllocationVal(row, cur) : acc), 0);

    const futuresVal = sumBy(gateItems, (r) => String(r.category || "").includes("선물"));
    const usStockVal = sumBy(tossItems, (r) => r.category === "미국주식");
    const krStockVal =
      sumBy(tossItems, (r) => r.category === "국내주식") +
      sumBy(kisItems, (r) => String(r.category || "") !== "예수금");
    const spotEarnVal =
      sumBy(gateItems, (r) => r.category === "현물") +
      (cur === "krw" ? Number(summary.gateio?.other_krw || 0) : Number(summary.gateio?.other_usd || 0));
    const cashVal =
      sumBy(tossItems, (r) => r.category === "예수금") +
      sumBy(kisItems, (r) => r.category === "예수금");

    items = [
      { name: "선물 (Gate.io)", sub: "Perpetual Futures", val: futuresVal },
      { name: "미국주식 (토스증권)", sub: "해외 상장 주식 · ETF", val: usStockVal },
      { name: "국내주식·ETF (토스·한투 ISA)", sub: "국내 상장 ETF · 주식", val: krStockVal },
      { name: "코인 현물·Earn (Gate.io)", sub: "Spot · 금융/Earn", val: spotEarnVal },
      { name: "예수금 (KRW·USD)", sub: "현금성 잔고", val: cashVal },
    ];
  } else {
    const allRows = [
      ...(Array.isArray(holdings.gateio) ? holdings.gateio : []),
      ...(Array.isArray(holdings.toss) ? holdings.toss : []),
      ...(Array.isArray(holdings.kis) ? holdings.kis : []),
    ];
    for (const row of allRows) {
      const val = getAllocationVal(row, cur);
      if (val > 0) {
        items.push({
          name: getSliceDisplayName(row),
          sub: `${row.exchange || ""} · ${row.category || ""}`,
          val,
        });
      }
    }
    const gateOtherVal =
      cur === "krw" ? Number(summary.gateio?.other_krw || 0) : Number(summary.gateio?.other_usd || 0);
    if (gateOtherVal > 0) {
      items.push({
        name: "Gate.io Earn/금융",
        sub: "Gate.io · 기타",
        val: gateOtherVal,
      });
    }

    const totalRaw = items.reduce((acc, it) => acc + it.val, 0);
    if (totalRaw > 0) {
      const major = [];
      let tinySum = 0;
      let tinyCount = 0;
      for (const it of items) {
        if (it.val / totalRaw < 0.003) {
          tinySum += it.val;
          tinyCount += 1;
        } else {
          major.push(it);
        }
      }
      if (tinySum > 0) {
        major.push({
          name: "기타 소액 잔고",
          sub: `${tinyCount}개 소액 항목`,
          val: tinySum,
        });
      }
      items = major;
    }
  }

  return items
    .filter((it) => Number.isFinite(it.val) && it.val > 0)
    .sort((a, b) => b.val - a.val);
}

function renderPortfolioAllocationPie() {
  const pieCanvasEl = document.getElementById("portfolio-pie-canvas");
  const listEl = document.getElementById("portfolio-allocation-list");
  if (!pieCanvasEl || !listEl) return;

  const slices = buildPortfolioAllocationData();
  const cur = state.currency;

  if (!slices.length) {
    if (pfPieChartInstance) pfPieChartInstance.clear();
    listEl.innerHTML = `<div class="empty-state">표시할 자산 비중 데이터가 없습니다.</div>`;
    return;
  }

  const total = slices.reduce((acc, s) => acc + s.val, 0);
  listEl.innerHTML = slices
    .map((item, idx) => {
      const color = PIE_PALETTE[idx % PIE_PALETTE.length];
      const pct = total > 0 ? (item.val / total) * 100 : 0;
      return `
        <div class="allocation-row">
          <div class="allocation-label">
            <span class="allocation-dot" style="background-color: ${color};"></span>
            <div>
              <div class="allocation-name">${escapeHtml(item.name)}</div>
              <div class="allocation-sub">${escapeHtml(item.sub)}</div>
            </div>
          </div>
          <div class="allocation-val tabular">
            <div class="allocation-money">${escapeHtml(formatMoney(item.val, cur))}</div>
            <div class="allocation-pct">${pct.toFixed(1)}%</div>
          </div>
        </div>
      `;
    })
    .join("");

  if (typeof window.echarts === "undefined") return;
  if (!pfPieChartInstance) {
    pfPieChartInstance = window.echarts.init(pieCanvasEl);
  }

  const showOutsideLabels = pieCanvasEl.clientWidth >= 300;
  pfPieChartInstance.setOption(
    {
      animation: true,
      animationDuration: 320,
      backgroundColor: "transparent",
      tooltip: {
        trigger: "item",
        backgroundColor: "#111111",
        borderColor: "#333333",
        textStyle: { color: "#F5F5F5", fontSize: 12 },
        formatter: (params) =>
          `<strong>${escapeHtml(params.name)}</strong><br/>${escapeHtml(formatMoney(params.value, cur))} (${params.percent.toFixed(1)}%)`,
      },
      series: [
        {
          name: "자산별 비중",
          type: "pie",
          radius: ["42%", "74%"],
          center: ["50%", "50%"],
          avoidLabelOverlap: true,
          itemStyle: {
            borderColor: "#111111",
            borderWidth: 2,
          },
          label: {
            show: showOutsideLabels,
            color: "#F5F5F5",
            fontSize: 11,
            lineHeight: 15,
            formatter: "{b}\n{d}%",
          },
          labelLine: {
            show: showOutsideLabels,
            length: 10,
            length2: 8,
            lineStyle: { color: "#8d8d8d" },
          },
          data: slices.map((item, idx) => ({
            name: item.name,
            value: item.val,
            itemStyle: { color: PIE_PALETTE[idx % PIE_PALETTE.length] },
          })),
        },
      ],
    },
    true
  );
  pfPieChartInstance.resize();
}

function renderPortfolioDashboard() {
  renderPortfolioStatusBanner();
  renderPortfolioSummaryCards();
  renderPortfolioHoldings();
  renderPortfolioAllocationPie();
  renderPortfolioHistoryLog();
  renderPortfolioChart();
  renderApiGuideStatus();
}

function renderApiGuideStatus() {
  const container = document.getElementById("api-status-detail");
  const providers = state.portfolio?.providers;
  if (!providers) {
    container.innerHTML = "";
    return;
  }

  const cards = [
    { title: "Gate.io (API v4 · Read-Only)", obj: providers.gateio || {} },
    { title: "토스증권 (Open API · 조회 전용)", obj: providers.toss || {} },
    { title: "한국투자증권 (중개형 ISA · 조회 전용)", obj: providers.kis || {} },
  ];

  container.innerHTML = cards
    .map(({ title, obj }) => {
      const badge = getPortfolioProviderLabel(obj);
      return `
        <div class="guide-status-card">
          <div class="guide-status-title">
            <span>${escapeHtml(title)}</span>
            <span class="provider-pill ${badge.cls}">${escapeHtml(badge.text)}</span>
          </div>
          <div class="guide-status-msg">${escapeHtml(obj.message || "-")}</div>
        </div>
      `;
    })
    .join("");
}

/* ---------- Existing MDD View Rendering ---------- */

function renderBenchmarks() {
  const grid = document.getElementById("benchmarks-grid");
  if (!state.benchmarks.length) {
    grid.innerHTML = `<div class="empty-state">벤치마크 데이터가 없습니다.</div>`;
    return;
  }

  grid.innerHTML = state.benchmarks
    .map((b) => {
      const isIndex = b.ticker.startsWith("^");
      const curClass = Number(b.current_dd_abs) === 0 ? "cur-dd-pct at-ath" : "cur-dd-pct";
      return `
        <article
          class="benchmark-card"
          data-asset-type="benchmark"
          data-asset-key="${escapeHtml(b.id)}"
          tabindex="0"
          role="button"
          aria-label="${escapeHtml(b.name_ko || b.name)} 차트 보기"
        >
          <div class="benchmark-head">
            <div class="benchmark-name">
              <span class="benchmark-title">${escapeHtml(b.name_ko || b.name)}</span>
              <span class="benchmark-ticker">${escapeHtml(b.display_ticker)} (${escapeHtml(b.ticker)})</span>
            </div>
            <div class="benchmark-prices">
              <div class="benchmark-current-price">${formatPrice(b.current_price, isIndex)}</div>
              <div class="benchmark-ath-price">ATH ${formatPrice(b.ath_price, isIndex)} (${escapeHtml(b.ath_date)})</div>
            </div>
          </div>

          <div>
            <div class="benchmark-gauge-area">
              ${buildGaugeHtml(b, true)}
              <div class="gauge-value">
                <span class="${curClass}">${formatDrawdownPct(b.current_drawdown)}</span>
                <span class="max-dd-pct">MDD ${formatDrawdownPct(b.max_drawdown)}</span>
              </div>
            </div>
            <div class="benchmark-scale" aria-hidden="true">
              <span>-100%</span>
              <span>-50%</span>
              <span>0%</span>
            </div>
          </div>
        </article>
      `;
    })
    .join("");
}

function getFilteredAndSortedStocks() {
  const q = state.searchQuery.trim().toLowerCase();
  const filtered = state.stocks.filter((s) => {
    if (!q) return true;
    return (
      s.ticker.toLowerCase().includes(q) ||
      s.name.toLowerCase().includes(q) ||
      (s.sector && s.sector.toLowerCase().includes(q))
    );
  });

  const sorted = [...filtered];
  switch (state.sortBy) {
    case "dd_desc":
      sorted.sort((a, b) => a.current_drawdown - b.current_drawdown || a.rank - b.rank);
      break;
    case "dd_asc":
      sorted.sort((a, b) => b.current_drawdown - a.current_drawdown || a.rank - b.rank);
      break;
    case "mdd_desc":
      sorted.sort((a, b) => a.max_drawdown - b.max_drawdown || a.rank - b.rank);
      break;
    case "rank":
    default:
      sorted.sort((a, b) => a.rank - b.rank);
      break;
  }
  return sorted;
}

function buildStockLogoUrls(stock) {
  const urls = [];
  const logoid = String(stock?.logoid || "").trim();
  const ticker = String(stock?.ticker || "").trim();
  if (logoid) {
    urls.push(`https://s3-symbol-logo.tradingview.com/${encodeURIComponent(logoid)}--big.svg`);
  }
  if (ticker) {
    urls.push(`https://assets.parqet.com/logos/symbol/${encodeURIComponent(ticker)}?format=png`);
    urls.push(`https://financialmodelingprep.com/image-stock/${encodeURIComponent(ticker)}.png`);
    if (ticker.includes("-")) {
      urls.push(`https://assets.parqet.com/logos/symbol/${encodeURIComponent(ticker.replace(/-/g, "."))}?format=png`);
    }
  }
  return urls;
}

function renderStocks() {
  const listEl = document.getElementById("stocks-list");
  const countEl = document.getElementById("stock-count");
  const items = getFilteredAndSortedStocks();

  if (state.searchQuery.trim()) {
    countEl.textContent = `검색 ${items.length} / ${state.stocks.length}개`;
  } else {
    countEl.textContent = `${state.stocks.length}개 종목`;
  }

  if (!items.length) {
    listEl.innerHTML = `<div class="empty-state">검색 조건에 맞는 종목이 없습니다.</div>`;
    return;
  }

  listEl.innerHTML = items
    .map((s) => {
      const curClass = Number(s.current_dd_abs) === 0 ? "cur-dd-pct at-ath" : "cur-dd-pct";
      const logoUrls = buildStockLogoUrls(s);
      const primaryLogo = logoUrls[0] || "";
      const fallbackLogos = logoUrls.slice(1).join("|");
      const monogram = escapeHtml((s.ticker || "").replace(/[^A-Za-z0-9]/g, "").slice(0, 3) || "?");
      const logoHtml = primaryLogo
        ? `<div class="stock-logo-circle" aria-hidden="true">
            <img
              class="stock-logo-img"
              src="${escapeHtml(primaryLogo)}"
              data-fallbacks="${escapeHtml(fallbackLogos)}"
              alt=""
              loading="lazy"
              decoding="async"
            />
            <span class="stock-logo-fallback">${monogram}</span>
          </div>`
        : `<div class="stock-logo-circle is-fallback" aria-hidden="true">
            <span class="stock-logo-fallback">${monogram}</span>
          </div>`;
      return `
        <div
          class="stock-row"
          data-asset-type="stock"
          data-asset-key="${escapeHtml(s.ticker)}"
          tabindex="0"
          role="button"
          aria-label="${escapeHtml(s.ticker)} (${escapeHtml(s.name)}) 차트 보기"
        >
          <div class="row-rank">#${s.rank}</div>
          <div class="row-asset">
            ${logoHtml}
            <div class="row-asset-info">
              <div class="row-asset-top">
                <span class="row-ticker">${escapeHtml(s.ticker)}</span>
                <span class="row-mcap">${formatMarketCap(s.market_cap)}</span>
              </div>
              <div class="row-name" title="${escapeHtml(s.name)}">${escapeHtml(s.name)}</div>
            </div>
          </div>
          <div class="row-price">
            <span class="row-current-price">${formatPrice(s.current_price, false)}</span>
            <span class="row-ath-price">ATH ${formatPrice(s.ath_price, false)}</span>
          </div>
          <div class="row-gauge">
            ${buildGaugeHtml(s, true)}
          </div>
          <div class="row-pct gauge-value">
            <span class="${curClass}">${formatDrawdownPct(s.current_drawdown)}</span>
            <span class="max-dd-pct">MDD ${formatDrawdownPct(s.max_drawdown)}</span>
          </div>
        </div>
      `;
    })
    .join("");
}

/* ---------- MDD Asset Candlestick Modal Chart ---------- */

function updateOhlcBar(index) {
  const barEl = document.getElementById("chart-ohlc-bar");
  if (!modalRows.length || index < 0 || index >= modalRows.length) {
    barEl.innerHTML = "";
    return;
  }
  const [date, open, high, low, close] = modalRows[index];
  const isIndex = Boolean(modalAsset && modalAsset.ticker.startsWith("^"));
  const fmt = (v) => (v == null ? "-" : formatPrice(v, isIndex));

  barEl.innerHTML = `
    <span class="ohlc-date">${escapeHtml(date)}</span>
    <span>시 ${fmt(open)}</span>
    <span>고 ${fmt(high)}</span>
    <span>저 ${fmt(low)}</span>
    <span>종 ${fmt(close)}</span>
  `;
}

function getIntervalRows(historyData, interval) {
  if (!historyData) return [];
  if (interval !== "yearly") {
    return Array.isArray(historyData[interval]) ? historyData[interval] : [];
  }
  if (Array.isArray(historyData.yearly) && historyData.yearly.length) {
    return historyData.yearly;
  }
  const weekly = Array.isArray(historyData.weekly) ? historyData.weekly : [];
  const byYear = new Map();
  for (const row of weekly) {
    const yr = row[0].slice(0, 4);
    if (!byYear.has(yr)) {
      byYear.set(yr, [row[0], row[1], row[2], row[3], row[4]]);
    } else {
      const cur = byYear.get(yr);
      cur[0] = row[0];
      cur[2] = Math.max(cur[2], row[2]);
      cur[3] = Math.min(cur[3], row[3]);
      cur[4] = row[4];
    }
  }
  return Array.from(byYear.values());
}

function renderModalChart() {
  const canvasEl = document.getElementById("echart-canvas");
  if (!modalHistory || !modalAsset) return;

  if (typeof window.echarts === "undefined") {
    document.getElementById("chart-loading").textContent = "차트 라이브러리(ECharts)를 불러오지 못했습니다.";
    document.getElementById("chart-loading").classList.remove("hidden");
    return;
  }

  const rows = getIntervalRows(modalHistory, modalInterval);
  modalRows = rows;
  if (!rows.length) {
    document.getElementById("chart-loading").textContent = "표시할 차트 데이터가 없습니다.";
    document.getElementById("chart-loading").classList.remove("hidden");
    return;
  }

  const n = rows.length;
  modalPadLeft = Math.max(80, n);
  const padRight = Math.max(80, n);
  modalTotalCats = modalPadLeft + n + padRight;

  const paddedDates = new Array(modalTotalCats).fill("");
  const paddedCandles = new Array(modalTotalCats).fill(null).map(() => []);
  for (let i = 0; i < n; i += 1) {
    const r = rows[i];
    const catIdx = modalPadLeft + i;
    paddedDates[catIdx] = r[0];
    paddedCandles[catIdx] = [
      toChartY(r[1], modalScaleMode),
      toChartY(r[4], modalScaleMode),
      toChartY(r[3], modalScaleMode),
      toChartY(r[2], modalScaleMode),
    ];
  }

  let visibleBars = n;
  if (modalInterval === "daily") visibleBars = Math.min(n, 160);
  else if (modalInterval === "weekly") visibleBars = Math.min(n, 120);

  const rightBuffer = Math.max(4, Math.round(visibleBars * 0.1));
  const leftBuffer = modalInterval === "yearly" ? Math.max(2, Math.round(visibleBars * 0.08)) : 0;

  const initStart = modalPadLeft + n - visibleBars - leftBuffer;
  const initEnd = modalPadLeft + n - 1 + rightBuffer;
  const latestClose = rows[n - 1][4];
  const isIndex = modalAsset.ticker.startsWith("^");

  if (!modalChartInstance) {
    modalChartInstance = window.echarts.init(canvasEl);
    modalChartInstance.on("finished", () => modalViewport?.scheduleCalloutsUpdate());
    modalChartInstance.on("updateAxisPointer", (event) => {
      const catIdx = event?.axesInfo?.[0]?.value;
      if (typeof catIdx === "number") {
        const realIdx = catIdx - modalPadLeft;
        if (realIdx >= 0 && realIdx < modalRows.length) {
          updateOhlcBar(realIdx);
        }
      }
    });
    canvasEl.addEventListener("mouseleave", () => {
      if (modalRows.length) updateOhlcBar(modalRows.length - 1);
    });
  }

  const { start: initZoomStart, end: initZoomEnd } = modalViewport.initWindow(initStart, initEnd);
  const vs = modalViewport.viewState;

  const option = {
    animation: false,
    backgroundColor: "#111111",
    grid: {
      left: MDD_CHART_GRID.left,
      right: MDD_CHART_GRID.right,
      top: MDD_CHART_GRID.top,
      bottom: MDD_CHART_GRID.bottom,
      containLabel: false,
    },
    tooltip: {
      trigger: "axis",
      axisPointer: {
        type: "cross",
        lineStyle: { color: "#8d8d8d", type: "dashed", width: 1 },
        crossStyle: { color: "#8d8d8d", type: "dashed", width: 1 },
        label: {
          backgroundColor: "#3182f6",
          color: "#ffffff",
          fontSize: 11,
          formatter: (params) => {
            if (params.axisDimension === "y") {
              return formatAxisPrice(fromChartY(params.value, modalScaleMode), isIndex);
            }
            return params.value || "";
          },
        },
      },
      extraCssText: "display:none;",
      formatter: () => "",
    },
    xAxis: {
      type: "category",
      data: paddedDates,
      boundaryGap: true,
      axisLine: { lineStyle: { color: "#333333" } },
      axisTick: { show: false },
      axisLabel: { color: "#8d8d8d", fontSize: 11 },
      splitLine: {
        show: true,
        lineStyle: { color: "rgba(51, 51, 51, 0.35)" },
      },
    },
    yAxis: {
      type: "value",
      position: "right",
      scale: true,
      min: vs.yMin,
      max: vs.yMax,
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: {
        show: true,
        lineStyle: { color: "rgba(51, 51, 51, 0.5)" },
      },
      axisLabel: {
        color: "#8d8d8d",
        fontSize: 11,
        formatter: (value) => formatAxisPrice(fromChartY(value, modalScaleMode), isIndex),
      },
    },
    dataZoom: [
      {
        type: "inside",
        xAxisIndex: 0,
        filterMode: "none",
        start: initZoomStart,
        end: initZoomEnd,
        zoomOnMouseWheel: false,
        moveOnMouseMove: false,
        moveOnMouseWheel: false,
      },
    ],
    series: [
      {
        name: modalAsset.ticker,
        type: "candlestick",
        data: paddedCandles,
        large: false,
        barWidth: "62%",
        barMinWidth: 2,
        barMaxWidth: 24,
        itemStyle: {
          color: "#f04452",
          color0: "#3182f6",
          borderColor: "#f04452",
          borderColor0: "#3182f6",
        },
        markLine: {
          symbol: ["none", "none"],
          silent: true,
          animation: false,
          data: [{ yAxis: toChartY(latestClose, modalScaleMode) }],
          lineStyle: { color: "#3182f6", type: "dotted", width: 1.5 },
          label: {
            show: true,
            position: "end",
            formatter: () => formatAxisPrice(latestClose, isIndex),
            backgroundColor: "#3182f6",
            color: "#ffffff",
            fontSize: 11,
            fontWeight: 700,
            padding: [4, 7],
            borderRadius: 4,
          },
        },
      },
    ],
  };

  modalChartInstance.setOption(option, true);
  modalChartInstance.resize();
  updateOhlcBar(n - 1);
  modalViewport.scheduleCalloutsUpdate();
}

async function openAssetModal(asset) {
  if (!asset || !asset.history_file) return;
  modalAsset = asset;
  modalInterval = "daily";

  document.querySelectorAll(".range-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.interval === "daily");
  });

  const isIndex = asset.ticker.startsWith("^");
  const titleText = asset.name_ko || asset.name || asset.ticker;
  const tickerText = asset.display_ticker ? `${asset.display_ticker} (${asset.ticker})` : asset.ticker;

  document.getElementById("modal-title").textContent = titleText;
  document.getElementById("modal-ticker").textContent = tickerText;
  document.getElementById("modal-price").textContent = `현재가 ${formatPrice(asset.current_price, isIndex)}`;
  document.getElementById("modal-cur-dd").textContent = `현재 낙폭 ${formatDrawdownPct(asset.current_drawdown)}`;
  document.getElementById("modal-max-dd").textContent = `역대 MDD ${formatDrawdownPct(asset.max_drawdown)}`;

  const modalEl = document.getElementById("chart-modal");
  const loadingEl = document.getElementById("chart-loading");
  document.getElementById("chart-peak-callout").classList.add("hidden");
  document.getElementById("chart-trough-callout").classList.add("hidden");
  loadingEl.textContent = "차트 데이터를 불러오는 중입니다...";
  loadingEl.classList.remove("hidden");

  modalEl.classList.remove("hidden");
  document.body.classList.add("modal-open");

  try {
    let historyData = historyCache.get(asset.history_file);
    if (!historyData) {
      const resp = await fetch(`./public/data/history/${encodeURIComponent(asset.history_file)}`, {
        cache: "no-cache",
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      historyData = await resp.json();
      historyCache.set(asset.history_file, historyData);
    }

    if (modalAsset !== asset) return;
    modalHistory = historyData;
    loadingEl.classList.add("hidden");
    renderModalChart();
  } catch (err) {
    loadingEl.textContent = `차트 데이터를 불러오지 못했습니다 (${err.message}).`;
  }
}

function closeAssetModal() {
  document.getElementById("chart-modal").classList.add("hidden");
  document.body.classList.remove("modal-open");
  modalAsset = null;
}

function openApiGuideModal() {
  renderApiGuideStatus();
  document.getElementById("api-guide-modal").classList.remove("hidden");
  document.body.classList.add("modal-open");
}

function closeApiGuideModal() {
  document.getElementById("api-guide-modal").classList.add("hidden");
  if (document.getElementById("chart-modal").classList.contains("hidden")) {
    document.body.classList.remove("modal-open");
  }
}

function findAssetFromElement(el) {
  if (!el) return null;
  const type = el.dataset.assetType;
  const key = el.dataset.assetKey;
  if (type === "benchmark") {
    return state.benchmarks.find((b) => b.id === key) || null;
  }
  if (type === "stock") {
    return state.stocks.find((s) => s.ticker === key) || null;
  }
  return null;
}

/* ---------- Control Bindings ---------- */

function bindControls() {
  pfViewport = createChartViewportController({
    stageEl: document.getElementById("portfolio-chart-stage"),
    canvasEl: document.getElementById("portfolio-echart-canvas"),
    yAxisZoneEl: document.getElementById("portfolio-yaxis-zoom-zone"),
    peakEl: document.getElementById("portfolio-peak-callout"),
    troughEl: document.getElementById("portfolio-trough-callout"),
    getChart: () => pfChartInstance,
    getTotalCats: () => pfTotalCats,
    getPadLeft: () => pfPadLeft,
    getRowsLength: () => pfRows.length,
    getMinZoomSpan: () => (pfInterval === "yearly" ? 4 : pfInterval === "monthly" ? 6 : 7),
    getGrid: () => PORTFOLIO_CHART_GRID,
    isLogScale: () => pfScaleMode === "log",
    getRowHighLowY: (i) => [
      toChartY(pfRows[i].lowVal, pfScaleMode),
      toChartY(pfRows[i].highVal, pfScaleMode),
    ],
    getCalloutInfo: (i) => {
      const r = pfRows[i];
      return {
        highPrice: r.highVal,
        lowPrice: r.lowVal,
        highY: toChartY(r.val, pfScaleMode),
        lowY: toChartY(r.val, pfScaleMode),
        date: r.date,
      };
    },
    getRefPrice: () => (pfRows.length ? pfRows[pfRows.length - 1].val : 0),
    formatCalloutPrice: (val) => formatMoney(val, state.currency),
  });

  modalViewport = createChartViewportController({
    stageEl: document.getElementById("modal-chart-stage"),
    canvasEl: document.getElementById("echart-canvas"),
    yAxisZoneEl: document.getElementById("yaxis-zoom-zone"),
    peakEl: document.getElementById("chart-peak-callout"),
    troughEl: document.getElementById("chart-trough-callout"),
    getChart: () => modalChartInstance,
    getTotalCats: () => modalTotalCats,
    getPadLeft: () => modalPadLeft,
    getRowsLength: () => modalRows.length,
    getMinZoomSpan: () => (modalInterval === "yearly" ? 4 : 10),
    getGrid: () => MDD_CHART_GRID,
    isLogScale: () => modalScaleMode === "log",
    getRowHighLowY: (i) => [
      toChartY(modalRows[i][3], modalScaleMode),
      toChartY(modalRows[i][2], modalScaleMode),
    ],
    getCalloutInfo: (i) => {
      const r = modalRows[i];
      return {
        highPrice: r[2],
        lowPrice: r[3],
        highY: toChartY(r[2], modalScaleMode),
        lowY: toChartY(r[3], modalScaleMode),
        date: r[0],
      };
    },
    getRefPrice: () => (modalRows.length ? modalRows[modalRows.length - 1][4] : 0),
    formatCalloutPrice: (val) => formatPrice(val, Boolean(modalAsset && modalAsset.ticker.startsWith("^"))),
  });

  // View Switcher
  document.getElementById("nav-toggle-view-btn").addEventListener("click", () => {
    switchMainView(state.activeView === "portfolio" ? "mdd" : "portfolio");
  });

  // Currency Toggle
  document.querySelectorAll(".currency-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".currency-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.currency = btn.dataset.currency === "usd" ? "usd" : "krw";
      renderPortfolioDashboard();
    });
  });

  // Portfolio Chart Filter Buttons
  document.querySelectorAll(".pf-series-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".pf-series-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      pfSeriesMode = btn.dataset.series || "total";
      renderPortfolioChart();
    });
  });

  document.querySelectorAll(".pf-interval-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".pf-interval-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      pfInterval = btn.dataset.pfInterval || "daily";
      renderPortfolioChart();
    });
  });

  document.querySelectorAll(".pf-scale-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".pf-scale-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      pfScaleMode = btn.dataset.pfScale === "log" ? "log" : "linear";
      renderPortfolioChart();
    });
  });

  // Portfolio Allocation Pie Filter Buttons
  document.querySelectorAll(".pf-pie-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".pf-pie-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      pfPieMode = btn.dataset.pieMode || "asset";
      renderPortfolioAllocationPie();
    });
  });

  // API Guide Modal
  document.getElementById("open-api-guide-btn").addEventListener("click", openApiGuideModal);
  document.getElementById("banner-open-guide-btn").addEventListener("click", openApiGuideModal);
  document.getElementById("api-guide-close-btn").addEventListener("click", closeApiGuideModal);
  document.getElementById("api-guide-modal").addEventListener("click", (e) => {
    if (e.target.id === "api-guide-modal") closeApiGuideModal();
  });

  // MDD Controls
  document.getElementById("search-input").addEventListener("input", (event) => {
    state.searchQuery = event.target.value;
    renderStocks();
  });

  const sortButtons = document.querySelectorAll(".sort-btn");
  sortButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      sortButtons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.sortBy = btn.dataset.sort || "rank";
      renderStocks();
    });
  });

  const handleContainerActivate = (selector, event) => {
    if (event.type === "keydown" && event.key !== "Enter" && event.key !== " ") return;
    const card = event.target.closest(selector);
    if (!card) return;
    if (event.type === "keydown") event.preventDefault();
    const asset = findAssetFromElement(card);
    if (asset) openAssetModal(asset);
  };

  const benchmarksGrid = document.getElementById("benchmarks-grid");
  benchmarksGrid.addEventListener("click", (e) => handleContainerActivate(".benchmark-card", e));
  benchmarksGrid.addEventListener("keydown", (e) => handleContainerActivate(".benchmark-card", e));

  const stocksList = document.getElementById("stocks-list");
  stocksList.addEventListener("click", (e) => handleContainerActivate(".stock-row", e));
  stocksList.addEventListener("keydown", (e) => handleContainerActivate(".stock-row", e));
  stocksList.addEventListener(
    "error",
    (e) => {
      const img = e.target;
      if (!img || !img.classList || !img.classList.contains("stock-logo-img")) return;
      const raw = img.dataset.fallbacks || "";
      const queue = raw ? raw.split("|").filter(Boolean) : [];
      if (queue.length > 0) {
        const nextUrl = queue.shift();
        img.dataset.fallbacks = queue.join("|");
        img.src = nextUrl;
      } else {
        img.classList.add("is-hidden");
        img.parentElement?.classList.add("is-fallback");
      }
    },
    true
  );

  document.getElementById("modal-close-btn").addEventListener("click", closeAssetModal);
  document.getElementById("chart-modal").addEventListener("click", (e) => {
    if (e.target.id === "chart-modal") closeAssetModal();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!document.getElementById("chart-modal").classList.contains("hidden")) {
      closeAssetModal();
    } else if (!document.getElementById("api-guide-modal").classList.contains("hidden")) {
      closeApiGuideModal();
    }
  });

  const rangeButtons = document.querySelectorAll(".range-btn");
  rangeButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      rangeButtons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      modalInterval = btn.dataset.interval || "daily";
      renderModalChart();
    });
  });

  const scaleButtons = document.querySelectorAll(".scale-btn");
  scaleButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      scaleButtons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      modalScaleMode = btn.dataset.scale === "log" ? "log" : "linear";
      renderModalChart();
    });
  });

  window.addEventListener("resize", () => {
    if (state.activeView === "portfolio") {
      if (pfChartInstance) {
        pfChartInstance.resize();
        pfViewport.scheduleCalloutsUpdate();
      }
      pfPieChartInstance?.resize();
    }
    if (modalChartInstance && !document.getElementById("chart-modal").classList.contains("hidden")) {
      modalChartInstance.resize();
      modalViewport.scheduleCalloutsUpdate();
    }
  });
}

/* ---------- App Initialization ---------- */

async function init() {
  bindControls();

  if (window.location.hash === "#mdd") {
    state.activeView = "mdd";
  }
  updateHeaderForView();

  const [pfResult, mddResult] = await Promise.allSettled([
    fetch(PORTFOLIO_DATA_URL, { cache: "no-cache" }).then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    }),
    fetch(MDD_DATA_URL, { cache: "no-cache" }).then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    }),
  ]);

  if (pfResult.status === "fulfilled") {
    state.portfolio = pfResult.value;
    state.portfolioUpdatedAt = pfResult.value.updated_at || "";
    renderPortfolioDashboard();
  } else {
    const msg = `개인 자산 데이터를 불러오지 못했습니다 (${escapeHtml(pfResult.reason?.message || "오류")}).`;
    document.getElementById("portfolio-summary-grid").innerHTML = `<div class="error-state">${msg}</div>`;
    document.getElementById("gate-holdings-list").innerHTML = `<div class="error-state">${msg}</div>`;
    document.getElementById("toss-holdings-list").innerHTML = `<div class="error-state">${msg}</div>`;
    document.getElementById("kis-holdings-list").innerHTML = `<div class="error-state">${msg}</div>`;
    document.getElementById("portfolio-history-list").innerHTML = `<div class="error-state">${msg}</div>`;
  }

  if (mddResult.status === "fulfilled") {
    const payload = mddResult.value;
    state.mddUpdatedAt = payload.updated_at || "";
    state.benchmarks = Array.isArray(payload.benchmarks) ? payload.benchmarks : [];
    state.stocks = Array.isArray(payload.stocks) ? payload.stocks : [];
    renderBenchmarks();
    renderStocks();
  } else {
    const msg = `MDD 데이터를 불러오지 못했습니다 (${escapeHtml(mddResult.reason?.message || "오류")}).`;
    document.getElementById("benchmarks-grid").innerHTML = `<div class="error-state">${msg}</div>`;
    document.getElementById("stocks-list").innerHTML = `<div class="error-state">${msg}</div>`;
  }

  updateHeaderForView();
  if (state.activeView === "portfolio" && pfChartInstance) {
    requestAnimationFrame(() => {
      pfChartInstance.resize();
      pfViewport?.scheduleCalloutsUpdate();
    });
  }
}

document.addEventListener("DOMContentLoaded", init);
