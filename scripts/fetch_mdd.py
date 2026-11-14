"""Daily data pipeline for US Top 100 Market Cap stocks & 3 major benchmarks MDD."""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import yfinance as yf

ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_PATH = ROOT_DIR / "public" / "data" / "mdd.json"

TRADINGVIEW_SCAN_URL = "https://scanner.tradingview.com/america/scan"

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
            if row["market_cap"] > existing["market_cap"]:
                existing["market_cap"] = row["market_cap"]
            if ticker == preferred_ticker:
                existing["ticker"] = preferred_ticker
                if row.get("name"):
                    existing["name"] = row["name"]
                if row.get("sector"):
                    existing["sector"] = row["sector"]

    deduped = sorted(seen_keys.values(), key=lambda x: x["market_cap"], reverse=True)
    return deduped


def fetch_top_candidates_tradingview(limit: int = 160) -> list[dict[str, Any]]:
    """Query TradingView US stock scanner sorted by basic market cap descending."""
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
        "columns": ["name", "description", "market_cap_basic", "close", "sector"],
        "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
        "range": [0, limit],
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Content-Type": "application/json",
    }
    resp = requests.post(TRADINGVIEW_SCAN_URL, json=payload, headers=headers, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    rows: list[dict[str, Any]] = []
    for item in data.get("data", []):
        cols = item.get("d", [])
        if len(cols) < 5:
            continue
        raw_ticker, name, market_cap, _close, sector = cols[:5]
        if not raw_ticker or not market_cap or market_cap <= 0:
            continue
        rows.append(
            {
                "ticker": normalize_yf_ticker(str(raw_ticker)),
                "name": str(name or raw_ticker).strip(),
                "market_cap": float(market_cap),
                "sector": str(sector or "").strip(),
            }
        )
    return deduplicate_candidates(rows)


def fetch_single_market_cap_yf(ticker: str) -> dict[str, Any] | None:
    """Fetch market cap and metadata for a single ticker via yfinance."""
    try:
        tk = yf.Ticker(ticker)
        mcap = getattr(tk.fast_info, "market_cap", None)
        if not mcap or mcap <= 0:
            info = tk.info or {}
            mcap = info.get("marketCap")
            name = info.get("shortName") or info.get("longName") or ticker
            sector = info.get("sector") or ""
        else:
            name = ticker
            sector = ""
            try:
                info = tk.info or {}
                name = info.get("shortName") or info.get("longName") or ticker
                sector = info.get("sector") or ""
            except Exception:
                pass

        if not mcap or mcap <= 0:
            return None
        return {
            "ticker": ticker,
            "name": str(name),
            "market_cap": float(mcap),
            "sector": str(sector),
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


def get_top_100_us_stocks(candidate_buffer: int = 125) -> list[dict[str, Any]]:
    """Discover US stocks sorted by current market cap descending."""
    try:
        candidates = fetch_top_candidates_tradingview(limit=160)
        if len(candidates) >= 100:
            print(f"[Discovery] TradingView scanner returned {len(candidates)} deduplicated candidates.")
            return candidates[:candidate_buffer]
        print(f"[Discovery] TradingView returned only {len(candidates)} rows, falling back to yfinance.")
    except Exception as exc:
        print(f"[Discovery] TradingView scanner failed ({exc}); using yfinance fallback.")

    fallback_candidates = fetch_top_candidates_fallback()
    print(f"[Discovery] Fallback returned {len(fallback_candidates)} candidates.")
    return fallback_candidates[:candidate_buffer]


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


def extract_close_series(df: pd.DataFrame, ticker: str) -> pd.Series | None:
    """Extract a clean Close price series from yf.download output for a given ticker."""
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        level0 = df.columns.get_level_values(0)
        level1 = df.columns.get_level_values(1)
        if "Close" in level0 and ticker in level1:
            s = df["Close"][ticker]
        elif ticker in level0 and "Close" in level1:
            s = df[ticker]["Close"]
        else:
            return None
    else:
        if "Close" not in df.columns:
            return None
        s = df["Close"]

    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    s = s.dropna()
    return s if not s.empty else None


def download_history_batches(tickers: list[str], batch_size: int = 25) -> dict[str, pd.Series]:
    """Download all-time daily close prices in batches with single-ticker retry fallback."""
    histories: dict[str, pd.Series] = {}

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
                s = extract_close_series(df, t)
                if s is not None:
                    histories[t] = s
        except Exception as exc:
            print(f"[History] Batch download error: {exc}")
        time.sleep(0.4)

    missing = [t for t in tickers if t not in histories]
    if missing:
        print(f"[History] Retrying {len(missing)} missing tickers individually...")
        for t in missing:
            for attempt in range(2):
                try:
                    hist = yf.Ticker(t).history(period="max", interval="1d", auto_adjust=False)
                    if not hist.empty and "Close" in hist.columns:
                        s = hist["Close"].dropna()
                        if not s.empty:
                            histories[t] = s
                            break
                except Exception:
                    pass
                time.sleep(0.5 * (attempt + 1))

    return histories


def fetch_all_data() -> dict[str, Any]:
    """Fetch 3 major benchmarks and Top 100 US stocks, compute MDD metrics, and write JSON."""
    benchmark_tickers = [b["ticker"] for b in BENCHMARKS]
    top_candidates = get_top_100_us_stocks(candidate_buffer=125)
    candidate_tickers = [c["ticker"] for c in top_candidates]

    all_tickers = benchmark_tickers + candidate_tickers
    histories = download_history_batches(all_tickers, batch_size=25)

    benchmarks_out: list[dict[str, Any]] = []
    for b in BENCHMARKS:
        s = histories.get(b["ticker"])
        if s is None:
            raise RuntimeError(f"Failed to download benchmark history for {b['ticker']}")
        metrics = compute_drawdown_metrics(s)
        benchmarks_out.append({**b, **metrics})

    stocks_out: list[dict[str, Any]] = []
    for c in top_candidates:
        if len(stocks_out) >= 100:
            break
        s = histories.get(c["ticker"])
        if s is None:
            print(f"[Skip] No price history for {c['ticker']}")
            continue
        try:
            metrics = compute_drawdown_metrics(s)
        except Exception as exc:
            print(f"[Skip] Failed metrics for {c['ticker']}: {exc}")
            continue
        stocks_out.append(
            {
                "rank": len(stocks_out) + 1,
                "ticker": c["ticker"],
                "name": c["name"],
                "sector": c["sector"],
                "market_cap": int(round(c["market_cap"])),
                **metrics,
            }
        )

    if len(stocks_out) < 100:
        print(f"[Warn] Collected {len(stocks_out)} stocks (fewer than 100).")

    payload = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "benchmarks": benchmarks_out,
        "stocks": stocks_out,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[Done] Wrote {len(benchmarks_out)} benchmarks and {len(stocks_out)} stocks to {OUTPUT_PATH}")
    return payload


if __name__ == "__main__":
    fetch_all_data()
