"""Daily data pipeline for Index Valuation (S&P 500, NASDAQ 100), CNN Fear & Greed, and Macro timing."""

from __future__ import annotations

import csv
import html
import io
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_PATH = ROOT_DIR / "public" / "data" / "macro.json"
PE_HISTORY_PATH = ROOT_DIR / "public" / "data" / "pe_history.json"

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
}

CNN_FG_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
CRYPTO_FG_URL = "https://api.alternative.me/fng/?limit=30"
WSJ_PE_URL = "https://www.wsj.com/market-data/stocks/peyields"
TRADINGVIEW_SCAN_URL = "https://scanner.tradingview.com/america/scan"

CNN_SUB_META: list[tuple[str, str, str]] = [
    ("market_momentum_sp500", "S&P 500 모멘텀", "125일 이동평균 대비 주가 위치"),
    ("stock_price_strength", "52주 신고 · 신저 비율", "NYSE 52주 신고가 vs 신저가 비중"),
    ("stock_price_breadth", "상승 · 하락 거래량 폭", "McClellan 거래량 합산 강세 지표"),
    ("put_call_options", "풋 / 콜 옵션 비율", "5일 평균 풋·콜 옵션 거래 비율"),
    ("market_volatility_vix", "시장 변동성 (VIX)", "50일 평균 대비 VIX 변동성 수준"),
    ("junk_bond_demand", "정크본드 스프레드", "투자적격 대비 하이일드 채권 금리차"),
    ("safe_haven_demand", "안전자산 선호도", "최근 20일 주식 vs 국채 초과 수익률"),
]


def _to_float(val: Any, digits: int = 2) -> float | None:
    if val is None or isinstance(val, bool):
        return None
    try:
        num = float(str(val).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    if num != num or num == float("inf") or num == float("-inf"):
        return None
    return round(num, digits)


def _rating_ko(rating: str | None) -> str:
    r = str(rating or "").strip().lower()
    mapping = {
        "extreme fear": "극단적 공포",
        "fear": "공포",
        "neutral": "중립",
        "greed": "탐욕",
        "extreme greed": "극단적 탐욕",
    }
    return mapping.get(r, rating or "-")


def fetch_cnn_fear_greed() -> dict[str, Any] | None:
    """Fetch CNN Fear & Greed Index, 1Y daily history, and all 7 sub-indicators."""
    try:
        resp = requests.get(
            CNN_FG_URL,
            headers={**REQUEST_HEADERS, "Referer": "https://www.cnn.com/markets/fear-and-greed"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"[Warn] CNN Fear & Greed fetch failed: {exc}")
        return None

    fg = data.get("fear_and_greed") or {}
    hist_raw = (data.get("fear_and_greed_historical") or {}).get("data") or []
    daily_map: dict[str, float] = {}
    for pt in hist_raw:
        x_ms = pt.get("x")
        y_val = _to_float(pt.get("y"), 1)
        if x_ms is None or y_val is None:
            continue
        dt_str = datetime.fromtimestamp(float(x_ms) / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
        daily_map[dt_str] = y_val

    history = [[k, daily_map[k]] for k in sorted(daily_map.keys())]

    sub_indicators: list[dict[str, Any]] = []
    for key, title_ko, desc_ko in CNN_SUB_META:
        sub = data.get(key) or {}
        pts = sub.get("data") or []
        last_y = _to_float(pts[-1].get("y"), 2) if pts else None
        rating = str(sub.get("rating") or "").strip().lower()
        sub_indicators.append(
            {
                "id": key,
                "name_ko": title_ko,
                "description": desc_ko,
                "score": _to_float(sub.get("score"), 1),
                "rating": rating,
                "rating_ko": _rating_ko(rating),
                "value": last_y,
            }
        )

    rating_en = str(fg.get("rating") or "").strip().lower()
    return {
        "score": _to_float(fg.get("score"), 1),
        "rating": rating_en,
        "rating_ko": _rating_ko(rating_en),
        "previous_close": _to_float(fg.get("previous_close"), 1),
        "prev_1w": _to_float(fg.get("previous_1_week"), 1),
        "prev_1m": _to_float(fg.get("previous_1_month"), 1),
        "prev_1y": _to_float(fg.get("previous_1_year"), 1),
        "timestamp": fg.get("timestamp") or "",
        "history": history,
        "sub_indicators": sub_indicators,
    }


def fetch_crypto_fear_greed() -> dict[str, Any] | None:
    """Fetch Alternative.me Crypto Fear & Greed Index (current + 30d history)."""
    try:
        resp = requests.get(CRYPTO_FG_URL, headers=REQUEST_HEADERS, timeout=12)
        resp.raise_for_status()
        items = resp.json().get("data") or []
        if not items:
            return None
        latest = items[0]
        rating = str(latest.get("value_classification") or "").strip().lower()
        hist = []
        for row in reversed(items):
            ts = row.get("timestamp")
            val = _to_float(row.get("value"), 0)
            if ts and val is not None:
                dt = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
                hist.append([dt, int(val)])
        return {
            "score": int(float(latest["value"])),
            "rating": rating,
            "rating_ko": _rating_ko(rating),
            "history": hist,
        }
    except Exception as exc:
        print(f"[Warn] Crypto Fear & Greed fetch failed: {exc}")
        return None


def fetch_wsj_pe_yields() -> dict[str, dict[str, Any]]:
    """Fetch Trailing PER, Forward PER, 52W ago PER, and Yield from WSJ Market Data."""
    out: dict[str, dict[str, Any]] = {}
    try:
        resp = requests.get(WSJ_PE_URL, headers=REQUEST_HEADERS, timeout=15)
        resp.raise_for_status()
        text = resp.text
        for m in re.finditer(r'\{"instrumentId"[^}]+\}', text):
            try:
                obj = json.loads(m.group(0))
            except Exception:
                continue
            ticker = str(obj.get("ticker") or "").upper()
            if not ticker:
                continue
            out[ticker] = {
                "name": obj.get("name"),
                "pe": _to_float(obj.get("priceEarningsRatio"), 2),
                "forward_pe": _to_float(obj.get("priceEarningsRatioEstimate"), 2),
                "pe_52w_ago": _to_float(obj.get("priceEarningsRatio52WeekAgo"), 2),
                "div_yield": _to_float(obj.get("yield"), 2),
                "div_yield_52w_ago": _to_float(obj.get("yield52WeekAgo"), 2),
            }
    except Exception as exc:
        print(f"[Warn] WSJ P/Es fetch failed: {exc}")
    return out


def fetch_stockanalysis_etf(symbol: str) -> dict[str, Any]:
    """Fetch ETF Trailing PE, Forward PE, and Dividend Yield from StockAnalysis as cross-check."""
    try:
        resp = requests.get(f"https://stockanalysis.com/etf/{symbol.lower()}/", headers=REQUEST_HEADERS, timeout=12)
        resp.raise_for_status()
        txt = re.sub(r"<[^>]+>", " ", resp.text)
        txt = re.sub(r"\s+", " ", txt)
        pe_m = re.search(r"PE Ratio\s+([0-9.]+)", txt)
        fwd_m = re.search(r"Forward PE\s+([0-9.]+)", txt)
        div_m = re.search(r"Dividend\s+\$[0-9.]+\s+\(([0-9.]+)%\)", txt)
        return {
            "pe": _to_float(pe_m.group(1), 2) if pe_m else None,
            "forward_pe": _to_float(fwd_m.group(1), 2) if fwd_m else None,
            "div_yield": _to_float(div_m.group(1), 2) if div_m else None,
        }
    except Exception as exc:
        print(f"[Warn] StockAnalysis {symbol} fetch failed: {exc}")
        return {}


def fetch_multpl_sp500() -> dict[str, float | None]:
    """Fetch Shiller CAPE, S&P 500 PBR, Trailing PER, Dividend Yield, and Earnings Yield."""
    slugs = {
        "shiller_cape": "shiller-pe",
        "pe": "s-p-500-pe-ratio",
        "pbr": "s-p-500-price-to-book",
        "div_yield": "s-p-500-dividend-yield",
        "earnings_yield": "s-p-500-earnings-yield",
    }
    res: dict[str, float | None] = {}
    for field, slug in slugs.items():
        try:
            r = requests.get(f"https://www.multpl.com/{slug}", headers=REQUEST_HEADERS, timeout=12)
            r.raise_for_status()
            m = re.search(r'id="current"[^>]*>[\s\S]*?([0-9]+\.[0-9]+)', r.text)
            res[field] = _to_float(m.group(1), 2) if m else None
        except Exception as exc:
            print(f"[Warn] Multpl {slug} fetch failed: {exc}")
            res[field] = None
    return res


def fetch_world_pe(slug: str) -> dict[str, Any]:
    """Fetch current P/E vs historical 1Y/5Y mean and standard deviation from WorldPERatio."""
    try:
        r = requests.get(f"https://worldperatio.com/index/{slug}/", headers=REQUEST_HEADERS, timeout=15)
        r.raise_for_status()
        txt = html.unescape(re.sub(r"<[^>]+>", " ", r.text))
        txt = re.sub(r"\s+", " ", txt)
        cur_m = re.search(r"Current P/E\s*\(\s*([0-9.]+)\s*\)", txt)
        m1y = re.search(r"Last 1Y\s+([0-9.]+)\s+([0-9.]+)\s+\[[^\]]+\]\s+([-+]?[0-9.]+)\s*σ\s+([A-Za-z]+)", txt)
        m5y = re.search(r"Last 5Y\s+([0-9.]+)\s+([0-9.]+)\s+\[[^\]]+\]\s+([-+]?[0-9.]+)\s*σ\s+([A-Za-z]+)", txt)
        out: dict[str, Any] = {"current_pe": _to_float(cur_m.group(1), 2) if cur_m else None}
        if m1y:
            out["mean_1y"] = _to_float(m1y.group(1), 2)
            out["z_1y"] = _to_float(m1y.group(3), 2)
            out["band_1y"] = m1y.group(4)
        if m5y:
            out["mean_5y"] = _to_float(m5y.group(1), 2)
            out["z_5y"] = _to_float(m5y.group(3), 2)
            out["band_5y"] = m5y.group(4)

        idx = r.text.find("detailPE_data = ")
        if idx >= 0:
            end_idx = r.text.find("\n", idx)
            line = r.text[idx : end_idx if end_idx > idx else idx + 500000]
            hist: list[list[Any]] = []
            for yr_s, mo_s, dy_s, val_s in re.findall(
                r"\[Date\.UTC\((\d+),\s*(\d+),\s*(\d+)\),\s*([0-9.]+)\]", line
            ):
                yr = int(yr_s)
                if yr < 1990:
                    continue
                dt_str = f"{yr:04d}-{int(mo_s) + 1:02d}-{int(dy_s):02d}"
                val_f = _to_float(val_s, 2)
                if val_f is not None:
                    hist.append([dt_str, val_f])
            out["history"] = hist
        return out
    except Exception as exc:
        print(f"[Warn] WorldPE {slug} fetch failed: {exc}")
        return {}


def update_pe_history(today_str: str, entries: dict[str, tuple[float | None, float | None]]) -> dict[str, list[list[Any]]]:
    """Maintain persistent daily Trailing PER and Forward PER time series."""
    data: dict[str, list[list[Any]]] = {}
    if PE_HISTORY_PATH.exists():
        try:
            loaded = json.loads(PE_HISTORY_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}

    for key, (pe_val, fwd_val) in entries.items():
        if pe_val is None and fwd_val is None:
            continue
        series = data.get(key) if isinstance(data.get(key), list) else []
        by_date: dict[str, list[Any]] = {}
        for row in series:
            if isinstance(row, list) and len(row) >= 3 and row[0]:
                by_date[str(row[0])] = [str(row[0]), row[1], row[2]]
        by_date[today_str] = [today_str, pe_val, fwd_val]
        data[key] = [by_date[d] for d in sorted(by_date.keys())]

    PE_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    PE_HISTORY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def fetch_tv_macro() -> dict[str, dict[str, Any]]:
    """Fetch SPX, NDX, VIX, and DXY price/change/RSI/SMA/returns from TradingView Scanner."""
    cols = ["name", "close", "change", "RSI", "SMA50", "SMA200", "Perf.1M", "Perf.YTD", "Perf.Y"]
    out: dict[str, dict[str, Any]] = {}
    try:
        resp = requests.post(
            TRADINGVIEW_SCAN_URL,
            json={"symbols": {"tickers": ["SP:SPX", "NASDAQ:NDX", "CBOE:VIX", "TVC:DXY"]}, "columns": cols},
            headers={**REQUEST_HEADERS, "Content-Type": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        for item in resp.json().get("data") or []:
            d = item.get("d") or []
            if len(d) < 9:
                continue
            sym = str(item.get("s") or "").upper()
            close = _to_float(d[1], 2)
            sma50 = _to_float(d[4], 2)
            sma200 = _to_float(d[5], 2)
            out[sym] = {
                "close": close,
                "change": _to_float(d[2], 2),
                "rsi": _to_float(d[3], 1),
                "sma50": sma50,
                "sma200": sma200,
                "dist_sma50": round((close - sma50) / sma50 * 100.0, 2) if close and sma50 else None,
                "dist_sma200": round((close - sma200) / sma200 * 100.0, 2) if close and sma200 else None,
                "perf_1m": _to_float(d[6], 2),
                "perf_ytd": _to_float(d[7], 2),
                "perf_1y": _to_float(d[8], 2),
            }
    except Exception as exc:
        print(f"[Warn] TradingView macro fetch failed: {exc}")
    return out


def fetch_treasury_yield_curve() -> dict[str, dict[str, Any]]:
    """Fetch US 10Y and 2Y Treasury yields from official US Dept. of Treasury daily CSV."""
    year = datetime.now(timezone.utc).year
    url = (
        f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
        f"daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv"
    )
    try:
        resp = requests.get(url, headers=REQUEST_HEADERS, timeout=15)
        resp.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(resp.text)))
        if not rows:
            return {}
        # Treasury CSV is newest-first; sort ascending by MM/DD/YYYY date to be certain
        parsed: list[tuple[str, float | None, float | None]] = []
        for row in rows:
            raw_dt = (row.get("Date") or "").strip()
            try:
                iso_dt = datetime.strptime(raw_dt, "%m/%d/%Y").strftime("%Y-%m-%d")
            except ValueError:
                continue
            y10 = _to_float(row.get("10 Yr"), 2)
            y2 = _to_float(row.get("2 Yr"), 2)
            if y10 is not None or y2 is not None:
                parsed.append((iso_dt, y10, y2))
        parsed.sort(key=lambda x: x[0])
        if not parsed:
            return {}
        latest_dt, y10_cur, y2_cur = parsed[-1]
        prev_dt, y10_prev, y2_prev = parsed[-2] if len(parsed) >= 2 else ("", None, None)
        spread_cur = round(y10_cur - y2_cur, 2) if (y10_cur is not None and y2_cur is not None) else None
        spread_prev = round(y10_prev - y2_prev, 2) if (y10_prev is not None and y2_prev is not None) else None
        return {
            "us10y": {
                "value": y10_cur,
                "delta": round(y10_cur - y10_prev, 2) if (y10_cur is not None and y10_prev is not None) else None,
                "date": latest_dt,
            },
            "us2y": {
                "value": y2_cur,
                "delta": round(y2_cur - y2_prev, 2) if (y2_cur is not None and y2_prev is not None) else None,
                "date": latest_dt,
            },
            "spread_10y2y": {
                "value": spread_cur,
                "delta": round(spread_cur - spread_prev, 2) if (spread_cur is not None and spread_prev is not None) else None,
                "date": latest_dt,
            },
        }
    except Exception as exc:
        print(f"[Warn] US Treasury CSV fetch failed: {exc}")
        return {}


def fetch_fred_latest(series_id: str) -> dict[str, Any]:
    """Fetch latest and previous valid daily observation from St. Louis Fed FRED CSV."""
    cosd = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={cosd}"
    text = ""
    try:
        resp = requests.get(url, headers=REQUEST_HEADERS, timeout=10)
        resp.raise_for_status()
        text = resp.text
    except Exception:
        try:
            proc = subprocess.run(
                ["curl", "-sS", "--max-time", "12", url],
                capture_output=True,
                text=True,
                check=True,
            )
            text = proc.stdout
        except Exception as exc:
            print(f"[Warn] FRED {series_id} fetch failed: {exc}")
            return {}

    valid: list[tuple[str, float]] = []
    for line in text.strip().splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 2 or parts[1].strip() in ("", "."):
            continue
        val = _to_float(parts[1], 2)
        if val is not None:
            valid.append((parts[0].strip(), val))
    if not valid:
        return {}
    latest_date, latest_val = valid[-1]
    prev_val = valid[-2][1] if len(valid) >= 2 else None
    delta = round(latest_val - prev_val, 2) if prev_val is not None else None
    return {"value": latest_val, "prev": prev_val, "delta": delta, "date": latest_date}


def fetch_yahoo_price(symbol: str) -> float | None:
    try:
        resp = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d",
            headers=REQUEST_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        meta = (resp.json().get("chart") or {}).get("result", [{}])[0].get("meta") or {}
        return _to_float(meta.get("regularMarketPrice"), 2)
    except Exception:
        return None


def fetch_all_macro() -> dict[str, Any]:
    cnn_fg = fetch_cnn_fear_greed()
    crypto_fg = fetch_crypto_fear_greed()
    wsj = fetch_wsj_pe_yields()
    spy_sa = fetch_stockanalysis_etf("SPY")
    qqq_sa = fetch_stockanalysis_etf("QQQ")
    multpl = fetch_multpl_sp500()
    world_sp = fetch_world_pe("sp-500")
    world_ndx = fetch_world_pe("nasdaq-100")
    tv = fetch_tv_macro()

    treasury = fetch_treasury_yield_curve()
    fred_10y = treasury.get("us10y") or fetch_fred_latest("DGS10")
    fred_2y = treasury.get("us2y") or fetch_fred_latest("DGS2")
    fred_10y2y = treasury.get("spread_10y2y") or fetch_fred_latest("T10Y2Y")
    fred_hy = fetch_fred_latest("BAMLH0A0HYM2")

    us10y_val = fred_10y.get("value") or fetch_yahoo_price("^TNX")
    us2y_val = fred_2y.get("value")
    spread_10y2y = fred_10y2y.get("value")
    if spread_10y2y is None and us10y_val is not None and us2y_val is not None:
        spread_10y2y = round(us10y_val - us2y_val, 2)

    wsj_spx = wsj.get("INX") or {}
    wsj_ndx = wsj.get("RIXF") or {}
    wsj_rut = wsj.get("RUT") or {}
    wsj_dji = wsj.get("DJI") or {}

    spx_pe = wsj_spx.get("pe") or multpl.get("pe") or spy_sa.get("pe")
    spx_fwd_pe = wsj_spx.get("forward_pe") or spy_sa.get("forward_pe")
    spx_div = wsj_spx.get("div_yield") or multpl.get("div_yield") or spy_sa.get("div_yield")
    spx_fwd_ey = round(100.0 / spx_fwd_pe, 2) if spx_fwd_pe else None
    spx_erp_fwd = round(spx_fwd_ey - us10y_val, 2) if (spx_fwd_ey is not None and us10y_val is not None) else None
    spx_ey = multpl.get("earnings_yield") or (round(100.0 / spx_pe, 2) if spx_pe else None)
    spx_erp_trailing = round(spx_ey - us10y_val, 2) if (spx_ey is not None and us10y_val is not None) else None

    ndx_pe = wsj_ndx.get("pe") or qqq_sa.get("pe")
    ndx_fwd_pe = wsj_ndx.get("forward_pe") or qqq_sa.get("forward_pe")
    ndx_div = wsj_ndx.get("div_yield") or qqq_sa.get("div_yield")
    ndx_fwd_ey = round(100.0 / ndx_fwd_pe, 2) if ndx_fwd_pe else None
    ndx_erp_fwd = round(ndx_fwd_ey - us10y_val, 2) if (ndx_fwd_ey is not None and us10y_val is not None) else None

    vix_tv = tv.get("CBOE:VIX") or {}
    vix_close = vix_tv.get("close") or fetch_yahoo_price("^VIX")
    dxy_tv = tv.get("TVC:DXY") or {}
    dxy_close = dxy_tv.get("close") or fetch_yahoo_price("DX-Y.NYB")

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pe_hist = update_pe_history(
        today_str,
        {
            "SP500": (spx_pe, spx_fwd_pe),
            "NASDAQ100": (ndx_pe, ndx_fwd_pe),
        },
    )

    payload: dict[str, Any] = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "sentiment": {
            "cnn": cnn_fg,
            "crypto": crypto_fg,
        },
        "indices": {
            "primary": [
                {
                    "id": "SP500",
                    "name": "S&P 500",
                    "ticker": "SPX",
                    "etf_ref": "SPY",
                    "pe": spx_pe,
                    "forward_pe": spx_fwd_pe,
                    "etf_pe": spy_sa.get("pe"),
                    "etf_forward_pe": spy_sa.get("forward_pe"),
                    "pe_52w_ago": wsj_spx.get("pe_52w_ago"),
                    "shiller_cape": multpl.get("shiller_cape"),
                    "pbr": multpl.get("pbr"),
                    "div_yield": spx_div,
                    "earnings_yield": spx_ey,
                    "forward_earnings_yield": spx_fwd_ey,
                    "erp_forward": spx_erp_fwd,
                    "erp_trailing": spx_erp_trailing,
                    "world_pe": world_sp,
                    "daily_history": pe_hist.get("SP500") or [],
                    "technicals": tv.get("SP:SPX") or {},
                },
                {
                    "id": "NASDAQ100",
                    "name": "NASDAQ 100",
                    "ticker": "NDX",
                    "etf_ref": "QQQ",
                    "pe": ndx_pe,
                    "forward_pe": ndx_fwd_pe,
                    "etf_pe": qqq_sa.get("pe"),
                    "etf_forward_pe": qqq_sa.get("forward_pe"),
                    "pe_52w_ago": wsj_ndx.get("pe_52w_ago"),
                    "div_yield": ndx_div,
                    "forward_earnings_yield": ndx_fwd_ey,
                    "erp_forward": ndx_erp_fwd,
                    "world_pe": world_ndx,
                    "daily_history": pe_hist.get("NASDAQ100") or [],
                    "technicals": tv.get("NASDAQ:NDX") or {},
                },
            ],
        },
        "macro": {
            "vix": {
                "value": vix_close,
                "change": vix_tv.get("change"),
                "sma50": vix_tv.get("sma50"),
                "sma200": vix_tv.get("sma200"),
                "rsi": vix_tv.get("rsi"),
            },
            "us10y": {
                "value": us10y_val,
                "delta": fred_10y.get("delta"),
                "date": fred_10y.get("date") or "",
            },
            "us2y": {
                "value": us2y_val,
                "delta": fred_2y.get("delta"),
                "date": fred_2y.get("date") or "",
            },
            "spread_10y2y": {
                "value": spread_10y2y,
                "delta": fred_10y2y.get("delta"),
                "date": fred_10y2y.get("date") or "",
            },
            "hy_spread": {
                "value": fred_hy.get("value"),
                "delta": fred_hy.get("delta"),
                "date": fred_hy.get("date") or "",
            },
            "dxy": {
                "value": dxy_close,
                "change": dxy_tv.get("change"),
                "dist_sma200": dxy_tv.get("dist_sma200"),
                "perf_ytd": dxy_tv.get("perf_ytd"),
            },
        },
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[Done] Wrote macro & index valuation dataset to {OUTPUT_PATH}.")
    return payload


if __name__ == "__main__":
    fetch_all_macro()
