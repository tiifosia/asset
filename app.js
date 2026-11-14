const DATA_URL = "./public/data/mdd.json";

const state = {
  benchmarks: [],
  stocks: [],
  searchQuery: "",
  sortBy: "rank",
};

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

function formatUpdatedAt(utcString) {
  if (!utcString) return "-";
  const iso = utcString.replace(" UTC", "Z").replace(" ", "T");
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return utcString;
  return date.toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }) + " KST";
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
        <article class="benchmark-card">
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
        <div class="stock-row">
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
