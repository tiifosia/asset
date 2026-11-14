const DATA_URL = "./public/data/mdd.json";

const state = {
  benchmarks: [],
  stocks: [],
  searchQuery: "",
  sortBy: "rank",
};

const historyCache = new Map();
let chartInstance = null;
let modalAsset = null;
let modalHistory = null;
let modalInterval = "daily";
let modalBars = "252";
let modalScaleMode = "linear";
let yZoomFactor = 1.0;
let modalRows = [];
let calloutRafId = null;
let yZoomRafId = null;

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
  const decimals = num >= 1000 ? 0 : 2;
  const formatted = num.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  return isIndex ? formatted : `$${formatted}`;
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
            <div class="row-asset-top">
              <span class="row-ticker">${escapeHtml(s.ticker)}</span>
              <span class="row-mcap">${formatMarketCap(s.market_cap)}</span>
            </div>
            <div class="row-name" title="${escapeHtml(s.name)}">${escapeHtml(s.name)}</div>
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

/* ---------- Modal & Candlestick Chart ---------- */

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

function getVisibleRange(chart, totalLen) {
  const dz = chart.getOption()?.dataZoom?.[0];
  if (!dz) return [0, totalLen - 1];
  let s =
    typeof dz.startValue === "number"
      ? dz.startValue
      : Math.round(((dz.start ?? 0) / 100) * (totalLen - 1));
  let e =
    typeof dz.endValue === "number"
      ? dz.endValue
      : Math.round(((dz.end ?? 100) / 100) * (totalLen - 1));
  s = Math.max(0, Math.min(totalLen - 1, Math.floor(s)));
  e = Math.max(s, Math.min(totalLen - 1, Math.ceil(e)));
  return [s, e];
}

function toChartY(price) {
  return modalScaleMode === "log" ? Math.log(price) : price;
}

function fromChartY(val) {
  return modalScaleMode === "log" ? Math.exp(val) : val;
}

function computeYAxisBounds(startIdx, endIdx) {
  if (!modalRows.length) return { min: undefined, max: undefined };
  let visLow = Infinity;
  let visHigh = -Infinity;
  for (let i = startIdx; i <= endIdx; i += 1) {
    const l = toChartY(modalRows[i][3]);
    const h = toChartY(modalRows[i][2]);
    if (l < visLow) visLow = l;
    if (h > visHigh) visHigh = h;
  }
  if (!Number.isFinite(visLow) || !Number.isFinite(visHigh)) {
    return { min: undefined, max: undefined };
  }
  const mid = (visLow + visHigh) / 2;
  const minSpan = modalScaleMode === "log" ? 0.02 : Math.max(Math.abs(mid) * 0.01, 0.01);
  const span = Math.max(visHigh - visLow, minSpan);
  const halfSpan = (span / 2) * 1.36 * yZoomFactor;
  return {
    min: mid - halfSpan,
    max: mid + halfSpan,
  };
}

function applyYAxisBounds() {
  if (!chartInstance || !modalRows.length) return;
  const [startIdx, endIdx] = getVisibleRange(chartInstance, modalRows.length);
  const bounds = computeYAxisBounds(startIdx, endIdx);
  chartInstance.setOption({
    yAxis: {
      min: bounds.min,
      max: bounds.max,
    },
  });
  updateCallouts();
}

function scheduleYAxisUpdate() {
  if (yZoomRafId) {
    cancelAnimationFrame(yZoomRafId);
  }
  yZoomRafId = requestAnimationFrame(() => {
    yZoomRafId = null;
    applyYAxisBounds();
  });
}

function updateCallouts() {
  const peakEl = document.getElementById("chart-peak-callout");
  const troughEl = document.getElementById("chart-trough-callout");

  if (!chartInstance || !modalRows.length || !modalAsset) {
    peakEl.classList.add("hidden");
    troughEl.classList.add("hidden");
    return;
  }

  const [startIdx, endIdx] = getVisibleRange(chartInstance, modalRows.length);
  let peakIdx = startIdx;
  let troughIdx = startIdx;
  let peakPrice = -Infinity;
  let troughPrice = Infinity;

  for (let i = startIdx; i <= endIdx; i += 1) {
    const high = modalRows[i][2];
    const low = modalRows[i][3];
    if (high > peakPrice) {
      peakPrice = high;
      peakIdx = i;
    }
    if (low < troughPrice) {
      troughPrice = low;
      troughIdx = i;
    }
  }

  const latestClose = modalRows[modalRows.length - 1][4];
  const isIndex = modalAsset.ticker.startsWith("^");
  const peakPct = peakPrice > 0 ? ((latestClose - peakPrice) / peakPrice) * 100 : 0;
  const troughPct = troughPrice > 0 ? ((latestClose - troughPrice) / troughPrice) * 100 : 0;

  const peakPx = chartInstance.convertToPixel({ seriesIndex: 0 }, [peakIdx, toChartY(peakPrice)]);
  const troughPx = chartInstance.convertToPixel({ seriesIndex: 0 }, [troughIdx, toChartY(troughPrice)]);

  const stageEl = document.querySelector(".chart-stage");
  const stageWidth = stageEl.clientWidth;
  const stageHeight = stageEl.clientHeight;
  const plotMaxX = stageWidth - 90;

  if (Array.isArray(peakPx) && Number.isFinite(peakPx[0]) && Number.isFinite(peakPx[1])) {
    const peakDate = formatShortDate(modalRows[peakIdx][0]);
    peakEl.innerHTML = `${formatPrice(peakPrice, isIndex)} (${formatSignedPct(peakPct)}, ${peakDate})<br>↓`;
    peakEl.classList.remove("hidden");
    const w = peakEl.offsetWidth;
    const h = peakEl.offsetHeight;
    const left = Math.max(8, Math.min(plotMaxX - w, peakPx[0] - w / 2));
    const top = Math.max(6, peakPx[1] - h - 4);
    peakEl.style.left = `${left}px`;
    peakEl.style.top = `${top}px`;
  } else {
    peakEl.classList.add("hidden");
  }

  if (Array.isArray(troughPx) && Number.isFinite(troughPx[0]) && Number.isFinite(troughPx[1])) {
    const troughDate = formatShortDate(modalRows[troughIdx][0]);
    troughEl.innerHTML = `↑<br>${formatPrice(troughPrice, isIndex)} (${formatSignedPct(troughPct)}, ${troughDate})`;
    troughEl.classList.remove("hidden");
    const w = troughEl.offsetWidth;
    const h = troughEl.offsetHeight;
    const left = Math.max(8, Math.min(plotMaxX - w, troughPx[0] - w / 2));
    const top = Math.min(stageHeight - h - 6, troughPx[1] + 4);
    troughEl.style.left = `${left}px`;
    troughEl.style.top = `${top}px`;
  } else {
    troughEl.classList.add("hidden");
  }
}

function scheduleCalloutsUpdate() {
  if (calloutRafId) {
    cancelAnimationFrame(calloutRafId);
  }
  calloutRafId = requestAnimationFrame(() => {
    calloutRafId = null;
    updateCallouts();
  });
}

function renderModalChart() {
  const canvasEl = document.getElementById("echart-canvas");
  if (!modalHistory || !modalAsset) return;

  if (typeof window.echarts === "undefined") {
    document.getElementById("chart-loading").textContent = "차트 라이브러리(ECharts)를 불러오지 못했습니다.";
    document.getElementById("chart-loading").classList.remove("hidden");
    return;
  }

  const rows = Array.isArray(modalHistory[modalInterval]) ? modalHistory[modalInterval] : [];
  modalRows = rows;
  if (!rows.length) {
    document.getElementById("chart-loading").textContent = "표시할 차트 데이터가 없습니다.";
    document.getElementById("chart-loading").classList.remove("hidden");
    return;
  }

  const dates = rows.map((r) => r[0]);
  const candles = rows.map((r) => [
    toChartY(r[1]),
    toChartY(r[4]),
    toChartY(r[3]),
    toChartY(r[2]),
  ]); // ECharts: [open, close, low, high]
  const closes = rows.map((r) => r[4]);

  const n = rows.length;
  const startIdx = modalBars === "all" ? 0 : Math.max(0, n - Number(modalBars));
  const endIdx = n - 1;
  const latestClose = closes[n - 1];
  const isIndex = modalAsset.ticker.startsWith("^");
  const yBounds = computeYAxisBounds(startIdx, endIdx);

  if (!chartInstance) {
    chartInstance = window.echarts.init(canvasEl);
    chartInstance.on("dataZoom", scheduleYAxisUpdate);
    chartInstance.on("finished", scheduleCalloutsUpdate);
    chartInstance.on("updateAxisPointer", (event) => {
      const idx = event?.axesInfo?.[0]?.value;
      if (typeof idx === "number" && idx >= 0 && idx < modalRows.length) {
        updateOhlcBar(idx);
      }
    });
    canvasEl.addEventListener("mouseleave", () => {
      if (modalRows.length) {
        updateOhlcBar(modalRows.length - 1);
      }
    });
  }

  const option = {
    animation: false,
    backgroundColor: "#111111",
    grid: {
      left: 14,
      right: 86,
      top: 42,
      bottom: 30,
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
              return formatAxisPrice(fromChartY(params.value), isIndex);
            }
            return params.value;
          },
        },
      },
      extraCssText: "display:none;",
      formatter: () => "",
    },
    xAxis: {
      type: "category",
      data: dates,
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
      min: yBounds.min,
      max: yBounds.max,
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: {
        show: true,
        lineStyle: { color: "rgba(51, 51, 51, 0.5)" },
      },
      axisLabel: {
        color: "#8d8d8d",
        fontSize: 11,
        formatter: (value) => formatAxisPrice(fromChartY(value), isIndex),
      },
    },
    dataZoom: [
      {
        type: "inside",
        xAxisIndex: 0,
        startValue: startIdx,
        endValue: endIdx,
        minValueSpan: 12,
      },
    ],
    series: [
      {
        name: modalAsset.ticker,
        type: "candlestick",
        data: candles,
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
          data: [{ yAxis: toChartY(latestClose) }],
          lineStyle: {
            color: "#3182f6",
            type: "dotted",
            width: 1.5,
          },
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

  chartInstance.setOption(option, true);
  chartInstance.resize();
  updateOhlcBar(n - 1);
  scheduleCalloutsUpdate();
}

async function openAssetModal(asset) {
  if (!asset || !asset.history_file) return;
  modalAsset = asset;
  modalInterval = "daily";
  modalBars = "252";
  yZoomFactor = 1.0;

  document.querySelectorAll(".range-btn").forEach((btn) => {
    const active = btn.dataset.interval === "daily" && btn.dataset.bars === "252";
    btn.classList.toggle("active", active);
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

    if (modalAsset !== asset) return; // User switched or closed before fetch finished
    modalHistory = historyData;
    loadingEl.classList.add("hidden");
    renderModalChart();
  } catch (err) {
    loadingEl.textContent = `차트 데이터를 불러오지 못했습니다 (${err.message}).`;
  }
}

function closeAssetModal() {
  const modalEl = document.getElementById("chart-modal");
  modalEl.classList.add("hidden");
  document.body.classList.remove("modal-open");
  modalAsset = null;
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

function bindControls() {
  const searchInput = document.getElementById("search-input");
  searchInput.addEventListener("input", (event) => {
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
    if (event.type === "keydown" && event.key !== "Enter" && event.key !== " ") {
      return;
    }
    const card = event.target.closest(selector);
    if (!card) return;
    if (event.type === "keydown") {
      event.preventDefault();
    }
    const asset = findAssetFromElement(card);
    if (asset) {
      openAssetModal(asset);
    }
  };

  const benchmarksGrid = document.getElementById("benchmarks-grid");
  benchmarksGrid.addEventListener("click", (e) => handleContainerActivate(".benchmark-card", e));
  benchmarksGrid.addEventListener("keydown", (e) => handleContainerActivate(".benchmark-card", e));

  const stocksList = document.getElementById("stocks-list");
  stocksList.addEventListener("click", (e) => handleContainerActivate(".stock-row", e));
  stocksList.addEventListener("keydown", (e) => handleContainerActivate(".stock-row", e));

  document.getElementById("modal-close-btn").addEventListener("click", closeAssetModal);
  document.getElementById("chart-modal").addEventListener("click", (e) => {
    if (e.target.id === "chart-modal") {
      closeAssetModal();
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !document.getElementById("chart-modal").classList.contains("hidden")) {
      closeAssetModal();
    }
  });

  const rangeButtons = document.querySelectorAll(".range-btn");
  rangeButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      rangeButtons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      modalInterval = btn.dataset.interval || "daily";
      modalBars = btn.dataset.bars || "252";
      yZoomFactor = 1.0;
      renderModalChart();
    });
  });

  const scaleButtons = document.querySelectorAll(".scale-btn");
  scaleButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      scaleButtons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      modalScaleMode = btn.dataset.scale === "log" ? "log" : "linear";
      yZoomFactor = 1.0;
      renderModalChart();
    });
  });

  const yAxisZone = document.getElementById("yaxis-zoom-zone");
  yAxisZone.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (!chartInstance || !modalRows.length) return;
      const factor = e.deltaY > 0 ? 1.12 : 0.89;
      yZoomFactor = Math.max(0.15, Math.min(6.0, yZoomFactor * factor));
      scheduleYAxisUpdate();
    },
    { passive: false }
  );

  let yDragActive = false;
  let yDragStartY = 0;
  let yDragStartFactor = 1.0;

  yAxisZone.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    yDragActive = true;
    yDragStartY = e.clientY;
    yDragStartFactor = yZoomFactor;
    e.preventDefault();
  });

  window.addEventListener("mousemove", (e) => {
    if (!yDragActive || !chartInstance || !modalRows.length) return;
    const dy = e.clientY - yDragStartY;
    const nextFactor = yDragStartFactor * Math.exp(dy * 0.008);
    yZoomFactor = Math.max(0.15, Math.min(6.0, nextFactor));
    scheduleYAxisUpdate();
  });

  window.addEventListener("mouseup", () => {
    yDragActive = false;
  });

  yAxisZone.addEventListener("dblclick", () => {
    yZoomFactor = 1.0;
    scheduleYAxisUpdate();
  });

  window.addEventListener("resize", () => {
    if (chartInstance && !document.getElementById("chart-modal").classList.contains("hidden")) {
      chartInstance.resize();
      scheduleCalloutsUpdate();
    }
  });
}

async function init() {
  bindControls();
  try {
    const response = await fetch(DATA_URL, { cache: "no-cache" });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const payload = await response.json();
    state.benchmarks = Array.isArray(payload.benchmarks) ? payload.benchmarks : [];
    state.stocks = Array.isArray(payload.stocks) ? payload.stocks : [];

    document.getElementById("updated-at").textContent = formatUpdatedAt(payload.updated_at);
    renderBenchmarks();
    renderStocks();
  } catch (err) {
    const msg = `데이터를 불러오지 못했습니다 (${escapeHtml(err.message)}). 로컬 파일 직접 열기(file://)가 아닌 웹 서버 또는 GitHub Pages 환경에서 열어주세요.`;
    document.getElementById("updated-at").textContent = "로드 실패";
    document.getElementById("benchmarks-grid").innerHTML = `<div class="error-state">${msg}</div>`;
    document.getElementById("stocks-list").innerHTML = `<div class="error-state">${msg}</div>`;
  }
}

document.addEventListener("DOMContentLoaded", init);
