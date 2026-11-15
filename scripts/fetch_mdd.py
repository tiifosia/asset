"""Daily data pipeline for US benchmarks, Top 100 MDD, and all NYSE+NASDAQ companies."""

from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

try:
    import yfinance as yf
except ImportError:
    yf = None  # Allow local runs without yfinance via Yahoo v8 fallback.

ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_PATH = ROOT_DIR / "public" / "data" / "mdd.json"
COMPANIES_OUTPUT_PATH = ROOT_DIR / "public" / "data" / "companies.json"
HISTORY_DIR = ROOT_DIR / "public" / "data" / "history"

TRADINGVIEW_SCAN_URL = "https://scanner.tradingview.com/america/scan"
YAHOO_CHART_HOSTS = (
    "https://query1.finance.yahoo.com",
    "https://query2.finance.yahoo.com",
)
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}

BENCHMARKS: list[dict[str, str]] = [
    {
        "id": "SP500",
        "ticker": "^GSPC",
        "display_ticker": "S&P 500",
        "name": "S&P 500",
        "name_ko": "S&P 500",
    },
    {
        "id": "NASDAQ100",
        "ticker": "^NDX",
        "display_ticker": "NDX",
        "name": "NASDAQ 100",
        "name_ko": "나스닥 100",
    },
    {
        "id": "BTC",
        "ticker": "BTC-USD",
        "display_ticker": "BTC",
        "name": "Bitcoin",
        "name_ko": "비트코인",
    },
]

# Dual-class ticker groups mapped to a canonical company key and preferred Yahoo ticker.
DUAL_CLASS_GROUPS: dict[str, tuple[str, str]] = {
    "GOOG": ("ALPHABET", "GOOGL"),
    "GOOGL": ("ALPHABET", "GOOGL"),
    "BRK-A": ("BERKSHIRE_HATHAWAY", "BRK-B"),
    "BRK-B": ("BERKSHIRE_HATHAWAY", "BRK-B"),
    "FOX": ("FOX_CORP", "FOXA"),
    "FOXA": ("FOX_CORP", "FOXA"),
    "NWS": ("NEWS_CORP", "NWSA"),
    "NWSA": ("NEWS_CORP", "NWSA"),
    "BF-A": ("BROWN_FORMAN", "BF-B"),
    "BF-B": ("BROWN_FORMAN", "BF-B"),
    "HEI": ("HEICO", "HEI"),
    "HEI-A": ("HEICO", "HEI"),
    "LEN": ("LENNAR", "LEN"),
    "LEN-B": ("LENNAR", "LEN"),
    "UHAL": ("UHAUL", "UHAL"),
    "UHAL-B": ("UHAUL", "UHAL"),
    "Z": ("ZILLOW", "ZG"),
    "ZG": ("ZILLOW", "ZG"),
}

# Comprehensive fallback list of major US companies (used only if TradingView scanner fails).
FALLBACK_US_TICKERS: list[str] = [
    "AAPL", "NVDA", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "BRK-B", "LLY",
    "WMT", "JPM", "V", "MA", "ORCL", "XOM", "UNH", "COST", "NFLX", "PG",
    "HD", "JNJ", "BAC", "ABBV", "CRM", "KO", "CVX", "TMUS", "MRK", "PLTR",
    "AMD", "CSCO", "PEP", "WFC", "IBM", "ACN", "GE", "ADBE", "AXP", "MS",
    "TMO", "ABT", "MCD", "BX", "GS", "NOW", "TXN", "ISRG", "QCOM", "PM",
    "DIS", "CAT", "INTU", "DHR", "AMGN", "RTX", "SPGI", "VZ", "T", "BLK",
    "UBER", "BKNG", "AMAT", "NEE", "SYK", "PFE", "LOW", "C", "HON", "UNP",
    "ANET", "ETN", "SCHW", "DE", "PGR", "CMCSA", "TJX", "COP", "BSX", "KKR",
    "BA", "APP", "LMT", "VRTX", "PANW", "ADP", "GILD", "FI", "MU", "ADI",
    "BMY", "SBUX", "MMC", "KLAC", "LRCX", "CB", "MDT", "NKE", "CRWD", "PLD",
    "SO", "ICE", "INTC", "MO", "SHW", "ELV", "DUK", "CEG", "GEV", "CME",
    "WM", "PH", "APH", "MCO", "CTAS", "AMT", "WELL", "EQIX", "APO", "CI",
    "MCK", "CL", "AON", "REGN", "TT", "TDG", "ZTS", "SNPS", "CDNS", "MSI",
    "ORLY", "AJG", "NOC", "PYPL", "CVS", "DELL", "MAR", "USB", "PNC", "COF",
]

TV_COLUMNS: list[str] = [
    "name",                         # 0: ticker
    "description",                  # 1: company name
    "exchange",                     # 2: NYSE / NASDAQ
    "sector",                       # 3: sector
    "industry",                     # 4: industry
    "logoid",                       # 5: TradingView logo slug
    "market_cap_basic",             # 6: market cap USD
    "close",                        # 7: current price USD
    "change",                       # 8: daily change %
    "volume",                       # 9: trading volume
    "High.All",                     # 10: all-time high
    "price_52_week_high",           # 11: 52w high
    "price_52_week_low",            # 12: 52w low
    "price_earnings_ttm",           # 13: PER
    "price_book_fq",                # 14: PBR
    "price_sales_ratio",            # 15: PSR
    "enterprise_value_ebitda_ttm",  # 16: EV/EBITDA
    "earnings_per_share_basic_ttm", # 17: EPS
    "dividends_yield_current",      # 18: dividend yield %
    "total_revenue_ttm",            # 19: revenue USD
    "net_income_ttm",               # 20: net income USD
    "return_on_equity",             # 21: ROE %
    "debt_to_equity_fq",            # 22: D/E ratio (converted to % below)
    "Perf.W",                       # 23: 1W %
    "Perf.1M",                      # 24: 1M %
    "Perf.3M",                      # 25: 3M %
    "Perf.6M",                      # 26: 6M %
    "Perf.YTD",                     # 27: YTD %
    "Perf.Y",                       # 28: 1Y %
    "RSI",                          # 29: RSI 14
    "SMA50",                        # 30: SMA 50
    "SMA200",                       # 31: SMA 200
    "Recommend.All",                # 32: Technical signal [-1..1]
    "beta_1_year",                  # 33: 1Y Beta
]

_thread_local = threading.local()


def _get_session() -> requests.Session:
    sess = getattr(_thread_local, "session", None)
    if sess is None:
        sess = requests.Session()
        sess.headers.update(REQUEST_HEADERS)
        _thread_local.session = sess
    return sess


def _opt_float(val: Any, digits: int = 2) -> float | None:
    if val is None or isinstance(val, bool):
        return None
    try:
        num = float(val)
    except (TypeError, ValueError):
        return None
    if pd.isna(num) or num == float("inf") or num == float("-inf"):
        return None
    return round(num, digits)


def _opt_int(val: Any) -> int | None:
    if val is None or isinstance(val, bool):
        return None
    try:
        num = float(val)
    except (TypeError, ValueError):
        return None
    if pd.isna(num) or num == float("inf") or num == float("-inf"):
        return None
    return int(round(num))


def normalize_yf_ticker(raw_ticker: str) -> str:
    """Convert exchange/TradingView ticker format (e.g. BRK.B) to Yahoo Finance format (BRK-B)."""
    return raw_ticker.strip().upper().replace(".", "-")


def normalize_company_key(ticker: str, description: str) -> str:
    """Build a normalized company identifier for dual-class deduplication."""
    if ticker in DUAL_CLASS_GROUPS:
        return DUAL_CLASS_GROUPS[ticker][0]
    cleaned = re.sub(r"\b(inc|incorporated|corp|corporation|co|ltd|plc|nv|sa|holdings|group)\b\.?", "", description.lower())
    cleaned = re.sub(r"\bclass\s+[a-z]\b", "", cleaned)
    cleaned = re.sub(r"[^a-z0-9]", "", cleaned)
    return cleaned or ticker


def deduplicate_candidates(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate dual-class shares while preserving preferred ticker and highest market cap."""
    seen_keys: dict[str, dict[str, Any]] = {}
    for row in raw_rows:
        ticker = normalize_yf_ticker(row["ticker"])
        key = normalize_company_key(ticker, row.get("name", ""))
        preferred_ticker = DUAL_CLASS_GROUPS[ticker][1] if ticker in DUAL_CLASS_GROUPS else ticker

        if key not in seen_keys:
            entry = dict(row)
            entry["ticker"] = preferred_ticker
            seen_keys[key] = entry
        else:
            existing = seen_keys[key]
            best_mcap = max(existing["market_cap"], row["market_cap"])
            if ticker == preferred_ticker:
                # Replace metadata with preferred share class while keeping max market cap.
                merged = dict(row)
                merged["ticker"] = preferred_ticker
                merged["market_cap"] = best_mcap
                seen_keys[key] = merged
            else:
                existing["market_cap"] = best_mcap

    return sorted(seen_keys.values(), key=lambda x: x["market_cap"], reverse=True)


def _parse_tv_row(cols: list[Any]) -> dict[str, Any] | None:
    if len(cols) < 10:
        return None
    raw_ticker = cols[0]
    market_cap = cols[6]
    if not raw_ticker or not market_cap or float(market_cap) <= 0:
        return None

    ticker = normalize_yf_ticker(str(raw_ticker))
    de_ratio = _opt_float(cols[22], 4)

    return {
        "ticker": ticker,
        "tv_symbol": str(raw_ticker).strip().upper(),
        "name": str(cols[1] or raw_ticker).strip(),
        "exchange": str(cols[2] or "").strip().upper(),
        "sector": str(cols[3] or "").strip(),
        "industry": str(cols[4] or "").strip(),
        "logoid": str(cols[5] or "").strip(),
        "market_cap": float(market_cap),
        "tv_close": _opt_float(cols[7], 4),
        "change": _opt_float(cols[8], 2),
        "volume": _opt_int(cols[9]),
        "tv_high_all": _opt_float(cols[10], 4),
        "high_52w": _opt_float(cols[11], 2),
        "low_52w": _opt_float(cols[12], 2),
        "per": _opt_float(cols[13], 2),
        "pbr": _opt_float(cols[14], 2),
        "psr": _opt_float(cols[15], 2),
        "ev_ebitda": _opt_float(cols[16], 2),
        "eps": _opt_float(cols[17], 2),
        "div_yield": _opt_float(cols[18], 2),
        "revenue": _opt_int(cols[19]),
        "net_income": _opt_int(cols[20]),
        "roe": _opt_float(cols[21], 2),
        # TradingView debt_to_equity_fq is a ratio (0.765 -> 76.5%).
        "debt_to_equity": round(de_ratio * 100.0, 1) if de_ratio is not None else None,
        "perf_w": _opt_float(cols[23], 2),
        "perf_1m": _opt_float(cols[24], 2),
        "perf_3m": _opt_float(cols[25], 2),
        "perf_6m": _opt_float(cols[26], 2),
        "perf_ytd": _opt_float(cols[27], 2),
        "perf_1y": _opt_float(cols[28], 2),
        "rsi": _opt_float(cols[29], 2),
        "sma50": _opt_float(cols[30], 2),
        "sma200": _opt_float(cols[31], 2),
        "rec_all": _opt_float(cols[32], 3),
        "beta": _opt_float(cols[33], 2),
    }


def fetch_all_candidates_tradingview(page_size: int = 1500) -> list[dict[str, Any]]:
    """Paginate TradingView US stock scanner for all NYSE + NASDAQ primary common stocks."""
    headers = {**REQUEST_HEADERS, "Content-Type": "application/json"}
    rows: list[dict[str, Any]] = []
    offset = 0
    total_count: int | None = None

    while True:
        payload = {
            "filter": [
                {"left": "exchange", "operation": "in_range", "right": ["NYSE", "NASDAQ"]},
                {"left": "type", "operation": "equal", "right": "stock"},
                {"left": "subtype", "operation": "in_range", "right": ["common", ""]},
                {"left": "is_primary", "operation": "equal", "right": True},
                {"left": "market_cap_basic", "operation": "nempty"},
            ],
            "options": {"lang": "en"},
            "markets": ["america"],
            "symbols": {"query": {"types": []}, "tickers": []},
            "columns": TV_COLUMNS,
            "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
            "range": [offset, offset + page_size],
        }
        resp = requests.post(TRADINGVIEW_SCAN_URL, json=payload, headers=headers, timeout=25)
        resp.raise_for_status()
        data = resp.json()
        if total_count is None:
            total_count = int(data.get("totalCount", 0) or 0)
        batch = data.get("data", [])
        if not batch:
            break

        for item in batch:
            parsed = _parse_tv_row(item.get("d", []))
            if parsed is not None:
                rows.append(parsed)

        offset += len(batch)
        if len(batch) < page_size or (total_count and offset >= total_count):
            break

    return deduplicate_candidates(rows)


def fetch_single_market_cap_yf(ticker: str) -> dict[str, Any] | None:
    """Fallback metadata fetch via yfinance when TradingView scanner is unreachable."""
    if yf is None:
        return None
    try:
        tk = yf.Ticker(ticker)
        info = tk.info or {}
        mcap = getattr(tk.fast_info, "market_cap", None) or info.get("marketCap")
        if not mcap or mcap <= 0:
            return None
        return {
            "ticker": ticker,
            "tv_symbol": ticker,
            "name": str(info.get("shortName") or info.get("longName") or ticker),
            "exchange": str(info.get("exchange") or ""),
            "sector": str(info.get("sector") or ""),
            "industry": str(info.get("industry") or ""),
            "logoid": "",
            "market_cap": float(mcap),
        }
    except Exception:
        return None


def fetch_top_candidates_fallback() -> list[dict[str, Any]]:
    """Fallback method fetching market caps via yfinance for built-in large-cap tickers."""
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(fetch_single_market_cap_yf, t): t for t in FALLBACK_US_TICKERS}
        for fut in as_completed(futures):
            res = fut.result()
            if res is not None:
                results.append(res)
    return deduplicate_candidates(results)


def get_all_us_stocks() -> list[dict[str, Any]]:
    """Discover all NYSE+NASDAQ US common stocks sorted by market cap descending."""
    try:
        candidates = fetch_all_candidates_tradingview()
        if len(candidates) >= 100:
            print(f"[Discovery] TradingView scanner returned {len(candidates)} deduplicated NYSE+NASDAQ companies.")
            return candidates
        print(f"[Discovery] TradingView returned only {len(candidates)} rows, falling back to yfinance.")
    except Exception as exc:
        print(f"[Discovery] TradingView scanner failed ({exc}); using yfinance fallback.")

    fallback_candidates = fetch_top_candidates_fallback()
    print(f"[Discovery] Fallback returned {len(fallback_candidates)} candidates.")
    return fallback_candidates


def compute_drawdown_metrics(series: pd.Series) -> dict[str, Any]:
    """Compute ATH, Current Drawdown, and Historical Max Drawdown metrics from daily closes."""
    clean = series.dropna().astype(float)
    clean = clean[clean > 0].sort_index()
    if clean.empty:
        raise ValueError("Empty price series")

    cummax = clean.cummax()
    dd = (clean - cummax) / cummax * 100.0

    current_price = round(float(clean.iloc[-1]), 2)
    ath_price = round(float(cummax.iloc[-1]), 2)
    ath_idx = clean.idxmax()
    ath_date = str(ath_idx.strftime("%Y-%m-%d"))

    raw_current_dd = round(float(dd.iloc[-1]), 2)
    current_drawdown = 0.0 if raw_current_dd >= 0 else raw_current_dd
    current_dd_abs = round(abs(current_drawdown), 2)

    raw_max_dd = round(float(dd.min()), 2)
    max_drawdown = 0.0 if raw_max_dd >= 0 else raw_max_dd
    max_dd_abs = round(max(abs(max_drawdown), current_dd_abs), 2)
    if max_dd_abs > abs(max_drawdown):
        max_drawdown = -max_dd_abs

    trough_idx = dd.idxmin()
    mdd_trough_date = str(trough_idx.strftime("%Y-%m-%d"))
    peak_slice = clean.loc[:trough_idx]
    mdd_peak_date = str(peak_slice.idxmax().strftime("%Y-%m-%d"))

    return {
        "current_price": current_price,
        "ath_price": ath_price,
        "ath_date": ath_date,
        "current_drawdown": current_drawdown,
        "current_dd_abs": current_dd_abs,
        "max_drawdown": max_drawdown,
        "max_dd_abs": max_dd_abs,
        "mdd_peak_date": mdd_peak_date,
        "mdd_trough_date": mdd_trough_date,
        "last_date": str(clean.index[-1].strftime("%Y-%m-%d")),
    }


def extract_ohlc_df(df: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
    """Extract a clean OHLC DataFrame from yf.download output for a given ticker."""
    if df is None or df.empty:
        return None

    ohlc_cols = ["Open", "High", "Low", "Close"]
    extracted: dict[str, pd.Series] = {}

    if isinstance(df.columns, pd.MultiIndex):
        level0 = df.columns.get_level_values(0)
        level1 = df.columns.get_level_values(1)
        for col in ohlc_cols:
            if col in level0 and ticker in level1:
                s = df[col][ticker]
            elif ticker in level0 and col in level1:
                s = df[ticker][col]
            else:
                continue
            if isinstance(s, pd.DataFrame):
                s = s.iloc[:, 0]
            extracted[col] = s
    else:
        for col in ohlc_cols:
            if col in df.columns:
                s = df[col]
                if isinstance(s, pd.DataFrame):
                    s = s.iloc[:, 0]
                extracted[col] = s

    if "Close" not in extracted:
        return None

    out = pd.DataFrame(extracted).copy()
    out = out[out["Close"].notna() & (out["Close"] > 0)].sort_index()
    if out.empty:
        return None

    idx = pd.to_datetime(out.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    out.index = idx

    for col in ["Open", "High", "Low"]:
        if col not in out.columns:
            out[col] = out["Close"]
        else:
            invalid = out[col].isna() | (out[col] <= 0)
            out.loc[invalid, col] = out.loc[invalid, "Close"]

    out["High"] = out[["Open", "High", "Close"]].max(axis=1)
    out["Low"] = out[["Open", "Low", "Close"]].min(axis=1)
    return out[["Open", "High", "Low", "Close"]].astype(float)


def download_single_yahoo_v8(ticker: str, host_idx: int = 0) -> pd.DataFrame | None:
    """Fetch full daily OHLC history directly from Yahoo Finance v8 chart JSON."""
    sess = _get_session()
    for attempt in range(3):
        host = YAHOO_CHART_HOSTS[(host_idx + attempt) % len(YAHOO_CHART_HOSTS)]
        url = f"{host}/v8/finance/chart/{ticker}?period1=0&period2=2000000000&interval=1d"
        try:
            resp = sess.get(url, timeout=14)
            if resp.status_code == 429:
                time.sleep(0.8 * (attempt + 1))
                continue
            if resp.status_code != 200:
                return None
            payload = resp.json()
            results = (payload.get("chart") or {}).get("result") or []
            if not results:
                return None
            res = results[0]
            ts = res.get("timestamp") or []
            quote_list = (res.get("indicators") or {}).get("quote") or []
            if not ts or not quote_list:
                return None
            q = quote_list[0]
            closes = q.get("close") or []
            if not closes or len(closes) != len(ts):
                return None
            df = pd.DataFrame(
                {
                    "Open": q.get("open") or closes,
                    "High": q.get("high") or closes,
                    "Low": q.get("low") or closes,
                    "Close": closes,
                },
                index=pd.to_datetime(ts, unit="s"),
            )
            return extract_ohlc_df(df, ticker)
        except Exception:
            time.sleep(0.35 * (attempt + 1))
    return None


def download_history_batches(tickers: list[str], batch_size: int = 25) -> dict[str, pd.DataFrame]:
    """Download all-time daily OHLC prices for core tickers with Yahoo v8 fallback."""
    histories: dict[str, pd.DataFrame] = {}

    if yf is not None:
        for i in range(0, len(tickers), batch_size):
            batch = tickers[i : i + batch_size]
            print(f"[History] Downloading batch {i // batch_size + 1} ({len(batch)} tickers)...")
            try:
                df = yf.download(
                    tickers=batch,
                    period="max",
                    interval="1d",
                    auto_adjust=False,
                    progress=False,
                    threads=True,
                    group_by="column",
                )
                for t in batch:
                    ohlc = extract_ohlc_df(df, t)
                    if ohlc is not None:
                        histories[t] = ohlc
            except Exception as exc:
                print(f"[History] Batch download error: {exc}")
            time.sleep(0.4)

    missing = [t for t in tickers if t not in histories]
    if missing:
        print(f"[History] Fetching {len(missing)} tickers via Yahoo v8 endpoint...")
        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = {pool.submit(download_single_yahoo_v8, t, idx): t for idx, t in enumerate(missing)}
            for fut in as_completed(futures):
                t = futures[fut]
                ohlc = fut.result()
                if ohlc is not None:
                    histories[t] = ohlc

    return histories


def _serialize_ohlc_rows(df_ohlc: pd.DataFrame) -> list[list[Any]]:
    """Serialize OHLC DataFrame rows to compact [date, open, high, low, close] lists."""
    rows: list[list[Any]] = []
    for dt, row in df_ohlc.iterrows():
        rows.append(
            [
                dt.strftime("%Y-%m-%d"),
                round(float(row["Open"]), 2),
                round(float(row["High"]), 2),
                round(float(row["Low"]), 2),
                round(float(row["Close"]), 2),
            ]
        )
    return rows


def build_ohlc_payload(df_ohlc: pd.DataFrame, max_daily_bars: int = 1260) -> dict[str, list[list[Any]]]:
    """Build compact daily (recent ~5Y), weekly (all-time), and yearly (all-time) OHLC arrays."""
    daily_df = df_ohlc.tail(max_daily_bars)
    weekly_df = (
        df_ohlc.resample("W-FRI")
        .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"})
        .dropna()
    )
    yearly_rows: list[list[Any]] = []
    for _yr, grp in df_ohlc.groupby(df_ohlc.index.year):
        yearly_rows.append(
            [
                grp.index[-1].strftime("%Y-%m-%d"),
                round(float(grp["Open"].iloc[0]), 2),
                round(float(grp["High"].max()), 2),
                round(float(grp["Low"].min()), 2),
                round(float(grp["Close"].iloc[-1]), 2),
            ]
        )
    return {
        "daily": _serialize_ohlc_rows(daily_df),
        "weekly": _serialize_ohlc_rows(weekly_df),
        "yearly": yearly_rows,
    }


def _load_cached_company_mdd() -> dict[str, dict[str, Any]]:
    """Load previously computed historical MDD metrics from companies.json for fast daily updates."""
    if not COMPANIES_OUTPUT_PATH.exists():
        return {}
    try:
        data = json.loads(COMPANIES_OUTPUT_PATH.read_text(encoding="utf-8"))
        cached: dict[str, dict[str, Any]] = {}
        for row in data.get("companies", []):
            t = row.get("ticker")
            if t and row.get("ath_price") is not None and row.get("max_drawdown") is not None:
                cached[t] = row
        print(f"[Cache] Loaded cached historical MDD for {len(cached)} companies.")
        return cached
    except Exception as exc:
        print(f"[Cache] Failed to read existing companies.json ({exc}); rebuilding all MDDs.")
        return {}


def _needs_full_mdd_refresh(candidate: dict[str, Any], cached: dict[str, Any] | None) -> bool:
    """Return True when historical daily prices should be fetched for accurate MDD."""
    if cached is None:
        return True
    if not cached.get("ath_price") or cached.get("max_drawdown") is None:
        return True
    tv_close = candidate.get("tv_close")
    prev_price = cached.get("current_price")
    if tv_close and prev_price and prev_price > 0:
        ratio = tv_close / prev_price
        # Large overnight jump/drop usually signals a stock split requiring clean re-backfill.
        if ratio > 1.8 or ratio < 0.55:
            return True
    return False


def _update_cached_mdd(candidate: dict[str, Any], cached: dict[str, Any], today_str: str) -> dict[str, Any]:
    """Incrementally update cached historical MDD using today's TradingView close price."""
    prev_ath = float(cached["ath_price"])
    prev_mdd_abs = abs(float(cached["max_drawdown"]))
    tv_close = candidate.get("tv_close")
    current_price = round(float(tv_close if tv_close and tv_close > 0 else cached.get("current_price") or prev_ath), 2)

    ath_price = round(max(prev_ath, current_price), 2)
    ath_date = today_str if current_price >= prev_ath else str(cached.get("ath_date") or today_str)

    current_drawdown = 0.0 if ath_price <= 0 else round(min(0.0, (current_price - ath_price) / ath_price * 100.0), 2)
    current_dd_abs = round(abs(current_drawdown), 2)

    if current_dd_abs > prev_mdd_abs:
        max_dd_abs = current_dd_abs
        mdd_trough_date = today_str
        mdd_peak_date = ath_date
    else:
        max_dd_abs = round(prev_mdd_abs, 2)
        mdd_trough_date = str(cached.get("mdd_trough_date") or today_str)
        mdd_peak_date = str(cached.get("mdd_peak_date") or ath_date)

    return {
        "current_price": current_price,
        "ath_price": ath_price,
        "ath_date": ath_date,
        "current_drawdown": current_drawdown,
        "current_dd_abs": current_dd_abs,
        "max_drawdown": round(-max_dd_abs, 2),
        "max_dd_abs": max_dd_abs,
        "mdd_peak_date": mdd_peak_date,
        "mdd_trough_date": mdd_trough_date,
        "last_date": today_str,
    }


def _fallback_tv_mdd(candidate: dict[str, Any], today_str: str) -> dict[str, Any] | None:
    """Fallback MDD approximation from TradingView High.All when Yahoo has no bars (e.g. brand-new IPO)."""
    tv_close = candidate.get("tv_close")
    if not tv_close or tv_close <= 0:
        return None
    tv_high = candidate.get("tv_high_all") or tv_close
    ath_price = round(max(float(tv_high), float(tv_close)), 2)
    current_price = round(float(tv_close), 2)
    current_drawdown = round(min(0.0, (current_price - ath_price) / ath_price * 100.0), 2)
    current_dd_abs = round(abs(current_drawdown), 2)
    return {
        "current_price": current_price,
        "ath_price": ath_price,
        "ath_date": today_str,
        "current_drawdown": current_drawdown,
        "current_dd_abs": current_dd_abs,
        "max_drawdown": current_drawdown,
        "max_dd_abs": current_dd_abs,
        "mdd_peak_date": today_str,
        "mdd_trough_date": today_str,
        "last_date": today_str,
    }


def _compute_mdd_for_ticker(ticker: str, idx: int) -> tuple[str, dict[str, Any] | None]:
    ohlc = download_single_yahoo_v8(ticker, host_idx=idx)
    if ohlc is None:
        return ticker, None
    try:
        return ticker, compute_drawdown_metrics(ohlc["Close"])
    except Exception:
        return ticker, None


def _build_company_record(
    rank: int,
    c: dict[str, Any],
    mdd: dict[str, Any],
    history_file: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "rank": rank,
        "ticker": c["ticker"],
        "name": c["name"],
        "exchange": c.get("exchange", ""),
        "sector": c.get("sector", ""),
        "industry": c.get("industry", ""),
        "logoid": c.get("logoid", ""),
        "market_cap": int(round(c["market_cap"])),
        "current_price": mdd["current_price"],
        "change": c.get("change"),
        "volume": c.get("volume"),
        "ath_price": mdd["ath_price"],
        "ath_date": mdd["ath_date"],
        "current_drawdown": mdd["current_drawdown"],
        "current_dd_abs": mdd["current_dd_abs"],
        "max_drawdown": mdd["max_drawdown"],
        "max_dd_abs": mdd["max_dd_abs"],
        "mdd_peak_date": mdd["mdd_peak_date"],
        "mdd_trough_date": mdd["mdd_trough_date"],
        "high_52w": c.get("high_52w"),
        "low_52w": c.get("low_52w"),
        "per": c.get("per"),
        "pbr": c.get("pbr"),
        "psr": c.get("psr"),
        "ev_ebitda": c.get("ev_ebitda"),
        "eps": c.get("eps"),
        "div_yield": c.get("div_yield"),
        "revenue": c.get("revenue"),
        "net_income": c.get("net_income"),
        "roe": c.get("roe"),
        "debt_to_equity": c.get("debt_to_equity"),
        "perf_w": c.get("perf_w"),
        "perf_1m": c.get("perf_1m"),
        "perf_3m": c.get("perf_3m"),
        "perf_6m": c.get("perf_6m"),
        "perf_ytd": c.get("perf_ytd"),
        "perf_1y": c.get("perf_1y"),
        "rsi": c.get("rsi"),
        "sma50": c.get("sma50"),
        "sma200": c.get("sma200"),
        "rec_all": c.get("rec_all"),
        "beta": c.get("beta"),
    }
    if history_file:
        record["history_file"] = history_file
    return record


def fetch_all_data() -> dict[str, Any]:
    """Fetch benchmarks, Top 100 MDD history, and all NYSE+NASDAQ companies metadata."""
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    benchmark_tickers = [b["ticker"] for b in BENCHMARKS]
    all_candidates = get_all_us_stocks()
    top_candidates = all_candidates[:125]
    candidate_tickers = [c["ticker"] for c in top_candidates]

    histories = download_history_batches(benchmark_tickers + candidate_tickers, batch_size=25)

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    written_files: set[str] = set()

    benchmarks_out: list[dict[str, Any]] = []
    for b in BENCHMARKS:
        ohlc = histories.get(b["ticker"])
        if ohlc is None:
            raise RuntimeError(f"Failed to download benchmark history for {b['ticker']}")
        metrics = compute_drawdown_metrics(ohlc["Close"])
        history_file = f"{b['id']}.json"
        (HISTORY_DIR / history_file).write_text(
            json.dumps(build_ohlc_payload(ohlc), separators=(",", ":")),
            encoding="utf-8",
        )
        written_files.add(history_file)
        benchmarks_out.append({**b, "history_file": history_file, **metrics})

    stocks_out: list[dict[str, Any]] = []
    top100_mdd_by_ticker: dict[str, tuple[dict[str, Any], str]] = {}

    for c in top_candidates:
        if len(stocks_out) >= 100:
            break
        ohlc = histories.get(c["ticker"])
        if ohlc is None:
            print(f"[Skip] No price history for {c['ticker']}")
            continue
        try:
            metrics = compute_drawdown_metrics(ohlc["Close"])
        except Exception as exc:
            print(f"[Skip] Failed metrics for {c['ticker']}: {exc}")
            continue
        history_file = f"{c['ticker']}.json"
        (HISTORY_DIR / history_file).write_text(
            json.dumps(build_ohlc_payload(ohlc), separators=(",", ":")),
            encoding="utf-8",
        )
        written_files.add(history_file)
        top100_mdd_by_ticker[c["ticker"]] = (metrics, history_file)
        stocks_out.append(
            {
                "rank": len(stocks_out) + 1,
                "ticker": c["ticker"],
                "name": c["name"],
                "exchange": c.get("exchange", ""),
                "sector": c["sector"],
                "logoid": c.get("logoid", ""),
                "market_cap": int(round(c["market_cap"])),
                "history_file": history_file,
                **metrics,
            }
        )

    for existing_json in HISTORY_DIR.glob("*.json"):
        if existing_json.name not in written_files:
            existing_json.unlink()

    if len(stocks_out) < 100:
        print(f"[Warn] Collected {len(stocks_out)} Top-100 stocks.")

    updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    mdd_payload = {
        "updated_at": updated_at,
        "benchmarks": benchmarks_out,
        "stocks": stocks_out,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(mdd_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[Done] Wrote {len(benchmarks_out)} benchmarks, {len(stocks_out)} Top-100 stocks, {len(written_files)} history files.")

    # Build full NYSE+NASDAQ dataset with cached + backfilled historical MDD.
    cached_mdd_map = _load_cached_company_mdd()
    to_fetch: list[str] = []
    for c in all_candidates:
        t = c["ticker"]
        if t in top100_mdd_by_ticker:
            continue
        if _needs_full_mdd_refresh(c, cached_mdd_map.get(t)):
            to_fetch.append(t)

    fresh_mdd_map: dict[str, dict[str, Any]] = {}
    if to_fetch:
        print(f"[Companies] Fetching full historical daily prices for {len(to_fetch)} tickers...")
        done_count = 0
        with ThreadPoolExecutor(max_workers=18) as pool:
            futures = {pool.submit(_compute_mdd_for_ticker, t, idx): t for idx, t in enumerate(to_fetch)}
            for fut in as_completed(futures):
                t, mdd_res = fut.result()
                if mdd_res is not None:
                    fresh_mdd_map[t] = mdd_res
                done_count += 1
                if done_count % 300 == 0 or done_count == len(to_fetch):
                    print(f"[Companies] MDD backfill progress: {done_count}/{len(to_fetch)} (computed={len(fresh_mdd_map)})")

    companies_out: list[dict[str, Any]] = []
    sectors_set: set[str] = set()
    for c in all_candidates:
        t = c["ticker"]
        history_file: str | None = None
        if t in top100_mdd_by_ticker:
            mdd_metrics, history_file = top100_mdd_by_ticker[t]
        elif t in fresh_mdd_map:
            mdd_metrics = fresh_mdd_map[t]
        elif t in cached_mdd_map and not _needs_full_mdd_refresh(c, cached_mdd_map[t]):
            mdd_metrics = _update_cached_mdd(c, cached_mdd_map[t], today_str)
        else:
            fallback_mdd = _fallback_tv_mdd(c, today_str)
            if fallback_mdd is None:
                continue
            mdd_metrics = fallback_mdd

        rank = len(companies_out) + 1
        if c.get("sector"):
            sectors_set.add(c["sector"])
        companies_out.append(_build_company_record(rank, c, mdd_metrics, history_file=history_file))

    companies_payload = {
        "updated_at": updated_at,
        "total_count": len(companies_out),
        "exchanges": ["NASDAQ", "NYSE"],
        "sectors": sorted(sectors_set),
        "companies": companies_out,
    }
    COMPANIES_OUTPUT_PATH.write_text(
        json.dumps(companies_payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"[Done] Wrote {len(companies_out)} NYSE+NASDAQ companies to {COMPANIES_OUTPUT_PATH}.")
    return mdd_payload


if __name__ == "__main__":
    fetch_all_data()
