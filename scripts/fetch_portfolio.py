"""Daily read-only portfolio tracker for Gate.io (API v4) and Toss Securities (Open API)."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

ROOT_DIR = Path(__file__).resolve().parent.parent
PORTFOLIO_PATH = ROOT_DIR / "public" / "data" / "portfolio.json"
KST = timezone(timedelta(hours=9))

GATE_BASE_URL = "https://api.gateio.ws"
GATE_PREFIX = "/api/v4"
TOSS_BASE_URL = "https://openapi.tossinvest.com"
KIS_BASE_URL = os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443").rstrip("/")

EXCHANGE_RATE_URL = "https://open.er-api.com/v6/latest/USD"
UPBIT_USDT_TICKER_URL = "https://api.upbit.com/v1/ticker?markets=KRW-USDT"


def _to_float(val: Any, default: float = 0.0) -> float:
    if val is None or val == "":
        return default
    try:
        num = float(val)
        return num if math.isfinite(num) else default
    except (TypeError, ValueError):
        return default


def _build_proxies(proxy_url: str) -> dict[str, str] | None:
    cleaned = proxy_url.strip()
    if not cleaned:
        return None
    return {"http": cleaned, "https": cleaned}


def _is_retryable_http_error(exc: Exception) -> bool:
    """Retry network errors, 429 rate limits, and 5xx server errors; fail fast on other 4xx."""
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        code = exc.response.status_code
        return code >= 500 or code == 429
    return True


def _run_with_retries(fn: Any, attempts: int = 2, delay: float = 1.2) -> Any:
    """Execute ``fn`` up to ``attempts`` times on transient request errors."""
    last_exc: Exception | None = None
    for idx in range(max(1, attempts)):
        try:
            return fn()
        except requests.RequestException as exc:
            last_exc = exc
            if idx + 1 >= attempts or not _is_retryable_http_error(exc):
                raise
            time.sleep(delay)
    if last_exc is not None:
        raise last_exc


def _call_direct_then_proxy(
    fn: Any,
    proxies: dict[str, str] | None = None,
    direct_attempts: int = 3,
    delay: float = 1.5,
) -> Any:
    """Try direct connection (with retries) before falling back to ``proxies``."""
    last_exc: Exception | None = None
    for idx in range(max(1, direct_attempts)):
        try:
            return fn(None)
        except requests.RequestException as exc:
            last_exc = exc
            if idx + 1 < direct_attempts and _is_retryable_http_error(exc):
                time.sleep(delay)
            else:
                break
    if proxies:
        return fn(proxies)
    if last_exc is not None:
        raise last_exc


def fetch_usdkrw(proxies: dict[str, str] | None = None, fallback: float = 1400.0) -> float:
    """Fetch live USD/KRW exchange rate (direct first, proxy fallback, then yfinance)."""
    for proxy_cfg in ([None, proxies] if proxies else [None]):
        try:
            resp = requests.get(EXCHANGE_RATE_URL, timeout=15, proxies=proxy_cfg)
            resp.raise_for_status()
            rate = _to_float(resp.json().get("rates", {}).get("KRW"), 0.0)
            if rate > 500:
                return round(rate, 2)
        except Exception:
            pass

    try:
        import yfinance as yf

        hist = yf.Ticker("KRW=X").history(period="5d")
        if not hist.empty:
            rate = _to_float(hist["Close"].dropna().iloc[-1], 0.0)
            if rate > 500:
                return round(rate, 2)
    except Exception:
        pass

    return fallback


def fetch_upbit_usdt_krw(proxies: dict[str, str] | None = None, fallback: float = 1400.0) -> float:
    """Fetch live Upbit KRW-USDT trade_price (public API, no API key required)."""
    for proxy_cfg in ([None, proxies] if proxies else [None]):
        try:
            resp = requests.get(
                UPBIT_USDT_TICKER_URL,
                headers={"Accept": "application/json"},
                timeout=15,
                proxies=proxy_cfg,
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list) and data:
                rate = _to_float(data[0].get("trade_price"), 0.0)
                if rate > 500:
                    return round(rate, 2)
        except Exception:
            pass
    return round(fallback, 2)


# ---------------- Gate.io (Read-Only API v4) ---------------- #


def _gate_sign_headers(
    api_key: str,
    api_secret: str,
    method: str,
    full_path: str,
    query_string: str = "",
) -> dict[str, str]:
    timestamp = str(int(time.time()))
    hashed_payload = hashlib.sha512(b"").hexdigest()
    sign_string = f"{method.upper()}\n{full_path}\n{query_string}\n{hashed_payload}\n{timestamp}"
    signature = hmac.new(
        api_secret.encode("utf-8"),
        sign_string.encode("utf-8"),
        hashlib.sha512,
    ).hexdigest()
    return {
        "KEY": api_key,
        "Timestamp": timestamp,
        "SIGN": signature,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _gate_get(
    endpoint: str,
    query_string: str,
    api_key: str,
    api_secret: str,
    proxies: dict[str, str] | None = None,
) -> Any:
    path = f"{GATE_PREFIX}{endpoint}"
    url = f"{GATE_BASE_URL}{path}" + (f"?{query_string}" if query_string else "")

    def _do(proxy_cfg: dict[str, str] | None) -> Any:
        # Recompute Timestamp + HMAC signature per attempt so retries never use a stale signature.
        headers = _gate_sign_headers(api_key, api_secret, "GET", path, query_string)
        resp = requests.get(url, headers=headers, timeout=20, proxies=proxy_cfg)
        resp.raise_for_status()
        return resp.json()

    return _call_direct_then_proxy(_do, proxies=proxies, direct_attempts=3, delay=1.5)


def _fetch_gate_spot_prices(proxies: dict[str, str] | None = None) -> dict[str, float]:
    """Fetch public spot USDT pair last prices for Gate.io currency valuation."""
    prices: dict[str, float] = {
        "USDT": 1.0,
        "USDC": 1.0,
        "FDUSD": 1.0,
        "USD": 1.0,
        "DAI": 1.0,
    }
    def _do(proxy_cfg: dict[str, str] | None) -> list[dict[str, Any]]:
        resp = requests.get(f"{GATE_BASE_URL}{GATE_PREFIX}/spot/tickers", timeout=20, proxies=proxy_cfg)
        resp.raise_for_status()
        return resp.json()

    try:
        rows = _call_direct_then_proxy(_do, proxies=proxies, direct_attempts=3, delay=1.5)
        for row in rows:
            pair = str(row.get("currency_pair", ""))
            if pair.endswith("_USDT"):
                base = pair[:-5]
                last = _to_float(row.get("last"), 0.0)
                if last > 0:
                    prices[base] = last
    except Exception:
        pass
    return prices


def fetch_gateio_readonly(
    api_key: str,
    api_secret: str,
    usdkrw: float,
    proxies: dict[str, str] | None = None,
    prev_gate: dict[str, Any] | None = None,
    prev_gate_holdings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Fetch combined Spot & Futures balances and positions strictly read-only."""
    total_payload = _gate_get("/wallet/total_balance", "currency=USDT", api_key, api_secret, proxies)
    wallet_total_usd = _to_float((total_payload.get("total") or {}).get("amount"), 0.0)
    details = total_payload.get("details") or {}
    spot_usd = _to_float((details.get("spot") or {}).get("amount"), 0.0)
    futures_wallet_usd = _to_float((details.get("futures") or {}).get("amount"), 0.0)
    delivery_wallet_usd = _to_float((details.get("delivery") or {}).get("amount"), 0.0)
    margin_usd = _to_float((details.get("cross_margin") or details.get("margin") or {}).get("amount"), 0.0)
    other_usd = max(0.0, wallet_total_usd - spot_usd - (futures_wallet_usd + delivery_wallet_usd))
    details_breakdown = {
        str(k): round(_to_float((v or {}).get("amount"), 0.0), 2)
        for k, v in details.items()
        if isinstance(v, dict) and _to_float(v.get("amount"), 0.0) > 0
    }

    holdings: list[dict[str, Any]] = []
    spot_prices = _fetch_gate_spot_prices(proxies)
    btc_price_usd = spot_prices.get("BTC") or spot_prices.get("GTBTC") or 0.0
    earn_merged_in_holdings = False

    # 1) Spot balances (GET /api/v4/spot/accounts), merging GTBTC & Earn/금융 into BTC
    try:
        spot_accounts = _gate_get("/spot/accounts", "", api_key, api_secret, proxies)
        btc_qty = 0.0
        btc_val_usd = 0.0
        for item in spot_accounts or []:
            currency = str(item.get("currency", "")).upper()
            avail = _to_float(item.get("available"), 0.0)
            locked = _to_float(item.get("locked"), 0.0)
            qty = avail + locked
            if qty <= 0:
                continue
            price_usd = spot_prices.get(currency, 0.0)
            val_usd = qty * price_usd
            if val_usd < 0.01 and price_usd > 0:
                continue
            if currency in ("BTC", "GTBTC"):
                btc_qty += qty
                btc_val_usd += val_usd
                continue
            holdings.append(
                {
                    "exchange": "Gate.io",
                    "category": "현물",
                    "symbol": currency,
                    "name": f"{currency} (Spot)",
                    "quantity": round(qty, 8),
                    "price_usd": round(price_usd, 6),
                    "value_usd": round(val_usd, 2),
                    "value_krw": round(val_usd * usdkrw),
                    "pnl_usd": None,
                    "pnl_pct": None,
                }
            )
        if other_usd >= 0.01:
            btc_val_usd += other_usd
            if btc_price_usd > 0:
                btc_qty += other_usd / btc_price_usd
            earn_merged_in_holdings = True
        if btc_val_usd >= 0.01 or btc_qty > 0:
            eff_btc_price = btc_price_usd if btc_price_usd > 0 else (btc_val_usd / btc_qty if btc_qty > 0 else 0.0)
            holdings.append(
                {
                    "exchange": "Gate.io",
                    "category": "현물",
                    "symbol": "BTC",
                    "name": "BTC",
                    "quantity": round(btc_qty, 8),
                    "price_usd": round(eff_btc_price, 6),
                    "value_usd": round(btc_val_usd, 2),
                    "value_krw": round(btc_val_usd * usdkrw),
                    "pnl_usd": None,
                    "pnl_pct": None,
                }
            )
    except Exception as exc:
        print(f"[warn] Gate.io spot accounts fetch failed: {exc}")
        if spot_usd > 0 and prev_gate_holdings:
            holdings.extend(h for h in prev_gate_holdings if str(h.get("category") or "").startswith("현물"))
            earn_merged_in_holdings = bool((prev_gate or {}).get("earn_merged_in_holdings", False))

    # 2) USDT Perpetual Futures positions (GET /api/v4/futures/usdt/positions)
    unrealized_pnl_usd = 0.0
    try:
        positions = _gate_get("/futures/usdt/positions", "", api_key, api_secret, proxies)
        for pos in positions or []:
            size = _to_float(pos.get("size"), 0.0)
            if size == 0:
                continue
            contract = str(pos.get("contract", ""))
            side = "LONG" if size > 0 else "SHORT"
            leverage = str(pos.get("leverage") or "-")
            entry_price = _to_float(pos.get("entry_price"), 0.0)
            mark_price = _to_float(pos.get("mark_price"), 0.0)
            upnl = _to_float(pos.get("unrealised_pnl"), 0.0)
            margin = _to_float(pos.get("margin") or pos.get("initial_margin"), 0.0)
            notional = _to_float(pos.get("value"), abs(size) * mark_price)
            unrealized_pnl_usd += upnl
            base_margin = margin if margin > 0 else notional
            pos_equity_usd = max(0.0, base_margin + upnl)
            pnl_pct = (upnl / base_margin * 100.0) if base_margin > 0 else None

            holdings.append(
                {
                    "exchange": "Gate.io",
                    "category": f"선물 {side} {leverage}x",
                    "symbol": contract,
                    "name": f"{contract.replace('_', '/')} Perpetual",
                    "quantity": abs(size),
                    "price_usd": round(mark_price, 6),
                    "entry_price_usd": round(entry_price, 6),
                    "value_usd": round(pos_equity_usd, 2),
                    "value_krw": round(pos_equity_usd * usdkrw),
                    "margin_usd": round(base_margin, 2),
                    "margin_krw": round(base_margin * usdkrw),
                    "notional_usd": round(notional, 2),
                    "pnl_usd": round(upnl, 2),
                    "pnl_krw": round(upnl * usdkrw),
                    "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
                }
            )
    except Exception as exc:
        print(f"[warn] Gate.io futures positions fetch failed: {exc}")
        if (futures_wallet_usd + delivery_wallet_usd) > 0:
            unrealized_pnl_usd = _to_float((prev_gate or {}).get("unrealized_pnl_usd"), 0.0)
            if prev_gate_holdings:
                holdings.extend(h for h in prev_gate_holdings if "선물" in str(h.get("category") or ""))

    net_futures_usd = max(0.0, (futures_wallet_usd + delivery_wallet_usd) + unrealized_pnl_usd)
    net_total_usd = max(0.0, wallet_total_usd + unrealized_pnl_usd)

    # Sort holdings: Futures positions first, then descending by USD value
    holdings.sort(key=lambda h: (0 if "선물" in h["category"] else 1, -_to_float(h.get("value_usd"), 0.0)))

    return {
        "usdt_krw": round(usdkrw, 2),
        "total_usd": round(net_total_usd, 2),
        "total_krw": round(net_total_usd * usdkrw),
        "wallet_total_usd": round(wallet_total_usd, 2),
        "spot_usd": round(spot_usd, 2),
        "spot_krw": round(spot_usd * usdkrw),
        "futures_usd": round(net_futures_usd, 2),
        "futures_krw": round(net_futures_usd * usdkrw),
        "wallet_futures_usd": round(futures_wallet_usd + delivery_wallet_usd, 2),
        "margin_usd": round(margin_usd, 2),
        "other_usd": round(other_usd, 2),
        "other_krw": round(other_usd * usdkrw),
        "earn_merged_in_holdings": earn_merged_in_holdings,
        "details_breakdown": details_breakdown,
        "unrealized_pnl_usd": round(unrealized_pnl_usd, 2),
        "unrealized_pnl_krw": round(unrealized_pnl_usd * usdkrw),
        "holdings": holdings,
    }


# ---------------- Toss Securities (Read-Only Open API) ---------------- #


def _toss_issue_token(
    client_id: str,
    client_secret: str,
    proxies: dict[str, str] | None = None,
) -> str:
    def _do() -> dict[str, Any]:
        resp = requests.post(
            f"{TOSS_BASE_URL}/oauth2/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=20,
            proxies=proxies,
        )
        resp.raise_for_status()
        return resp.json()

    payload = _run_with_retries(_do, attempts=3, delay=2.0)
    token = payload.get("access_token") or (payload.get("result") or {}).get("access_token")
    if not token:
        raise RuntimeError("Toss Open API did not return an access_token")
    return str(token)


def _toss_get(
    path: str,
    token: str,
    account_seq: str | int | None = None,
    proxies: dict[str, str] | None = None,
) -> Any:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if account_seq is not None:
        headers["X-Tossinvest-Account"] = str(account_seq)

    def _do() -> Any:
        resp = requests.get(f"{TOSS_BASE_URL}{path}", headers=headers, timeout=20, proxies=proxies)
        resp.raise_for_status()
        return resp.json()

    return _run_with_retries(_do, attempts=3, delay=2.0)


def _extract_list(payload: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for k in keys:
            val = payload.get(k)
            if isinstance(val, list):
                return [row for row in val if isinstance(row, dict)]
            if isinstance(val, dict):
                nested = _extract_list(val, keys)
                if nested:
                    return nested
    return []


def _extract_buying_power_amount(bp_payload: Any) -> float:
    """Extract cashBuyingPower from /api/v1/buying-power?currency=... response."""
    data = bp_payload.get("result") if isinstance(bp_payload, dict) and isinstance(bp_payload.get("result"), dict) else bp_payload
    if not isinstance(data, dict):
        return 0.0
    return _to_float(
        data.get("cashBuyingPower")
        or data.get("orderableAmount")
        or data.get("buyingPower")
        or data.get("cashBalance")
        or data.get("depositAmount"),
        0.0,
    )


def fetch_toss_readonly(
    client_id: str,
    client_secret: str,
    usdkrw: float,
    proxies: dict[str, str] | None = None,
    prev_toss: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fetch Toss Securities accounts, holdings, and cash buying power strictly read-only."""
    token = _toss_issue_token(client_id, client_secret, proxies)
    acc_payload = _toss_get("/api/v1/accounts", token, None, proxies)
    accounts = _extract_list(acc_payload, ("result", "accounts", "items", "data"))
    if not accounts:
        raise RuntimeError("No Toss Securities accounts returned from /api/v1/accounts")

    stock_krw = 0.0
    stock_usd_direct = 0.0
    cash_krw = 0.0
    cash_usd = 0.0
    krw_bp_ok = False
    usd_bp_ok = False
    cash_fallback = False
    pnl_krw_total = 0.0
    holdings: list[dict[str, Any]] = []

    for acc in accounts:
        seq = acc.get("accountSeq") or acc.get("accountId") or acc.get("seq")
        if seq is None:
            continue

        # 1) Holdings (GET /api/v1/holdings)
        h_payload = _toss_get("/api/v1/holdings", token, seq, proxies)
        result_obj = h_payload.get("result") if isinstance(h_payload, dict) and isinstance(h_payload.get("result"), dict) else h_payload
        items = _extract_list(h_payload, ("items", "holdings", "result", "data"))

        # Check official overview totals in case item-level sum is absent or rounded
        overview_mv = (result_obj.get("marketValue") or {}).get("amount") if isinstance(result_obj, dict) else None
        overview_pnl = (result_obj.get("profitLoss") or {}).get("amount") if isinstance(result_obj, dict) else None

        acc_item_krw = 0.0
        acc_item_usd = 0.0
        acc_item_pnl_krw = 0.0

        for item in items:
            qty = _to_float(
                item.get("quantity")
                or item.get("holdingQuantity")
                or item.get("qty")
                or item.get("balanceQuantity"),
                0.0,
            )
            if qty <= 0:
                continue

            symbol = str(item.get("symbol") or item.get("ticker") or item.get("stockCode") or "-")
            name = str(item.get("name") or item.get("stockName") or item.get("itemName") or symbol)
            market = str(item.get("marketCountry") or item.get("market") or item.get("marketCode") or "").upper()
            currency = str(item.get("currency") or ("USD" if market in {"US", "NAS", "NYS", "AMS", "NASDAQ", "NYSE", "AMEX"} else "KRW")).upper()

            cur_price = _to_float(item.get("lastPrice") or item.get("currentPrice") or item.get("price"), 0.0)
            avg_price = _to_float(
                item.get("averagePurchasePrice")
                or item.get("averagePrice")
                or item.get("avgPrice")
                or item.get("purchasePrice"),
                0.0,
            )

            mv_field = item.get("marketValue")
            if isinstance(mv_field, dict):
                eval_amt = _to_float(mv_field.get("amount") or mv_field.get("amountAfterCost"), qty * cur_price)
            else:
                eval_amt = _to_float(
                    item.get("evaluationAmount") or item.get("evalAmount") or mv_field,
                    qty * cur_price,
                )

            pnl_field = item.get("profitLoss")
            if isinstance(pnl_field, dict):
                pnl_amt = _to_float(
                    pnl_field.get("amount"),
                    (cur_price - avg_price) * qty if cur_price and avg_price else 0.0,
                )
                # Official Toss schema returns decimal ratio (e.g., "0.1077" = +10.77%)
                pnl_rate = _to_float(pnl_field.get("rate"), 0.0) * 100.0
            else:
                pnl_amt = _to_float(
                    item.get("profitAndLoss") or item.get("pnlAmount") or item.get("evaluationProfitLoss"),
                    (cur_price - avg_price) * qty if cur_price and avg_price else 0.0,
                )
                pnl_rate = _to_float(item.get("profitRate") or item.get("pnlRate") or item.get("returnRate"), 0.0)

            if currency == "USD":
                val_usd = eval_amt
                val_krw = eval_amt * usdkrw
                pnl_usd = pnl_amt
                pnl_krw = pnl_amt * usdkrw
                acc_item_usd += val_usd
                category = "미국주식"
            else:
                val_krw = eval_amt
                val_usd = eval_amt / usdkrw if usdkrw > 0 else 0.0
                pnl_krw = pnl_amt
                pnl_usd = pnl_amt / usdkrw if usdkrw > 0 else 0.0
                acc_item_krw += val_krw
                category = "국내주식"

            acc_item_pnl_krw += pnl_krw

            holdings.append(
                {
                    "exchange": "토스증권",
                    "category": category,
                    "symbol": symbol,
                    "name": name,
                    "currency": currency,
                    "quantity": round(qty, 6),
                    "price_native": round(cur_price, 2),
                    "avg_price_native": round(avg_price, 2),
                    "value_krw": round(val_krw),
                    "value_usd": round(val_usd, 2),
                    "pnl_krw": round(pnl_krw),
                    "pnl_usd": round(pnl_usd, 2),
                    "pnl_pct": round(pnl_rate, 2),
                }
            )

        if isinstance(overview_mv, dict):
            ov_krw = _to_float(overview_mv.get("krw"), 0.0)
            ov_usd = _to_float(overview_mv.get("usd"), 0.0)
            stock_krw += ov_krw if ov_krw > 0 else acc_item_krw
            stock_usd_direct += ov_usd if ov_usd > 0 else acc_item_usd
        else:
            stock_krw += acc_item_krw
            stock_usd_direct += acc_item_usd

        if isinstance(overview_pnl, dict):
            ov_p_krw = _to_float(overview_pnl.get("krw"), 0.0)
            ov_p_usd = _to_float(overview_pnl.get("usd"), 0.0)
            pnl_krw_total += ov_p_krw + ov_p_usd * usdkrw
        else:
            pnl_krw_total += acc_item_pnl_krw

        # 2) Buying Power / Cash (GET /api/v1/buying-power?currency=KRW and ?currency=USD)
        try:
            bp_krw_payload = _toss_get("/api/v1/buying-power?currency=KRW", token, seq, proxies)
            cash_krw += round(_extract_buying_power_amount(bp_krw_payload))
            krw_bp_ok = True
        except Exception as exc:
            print(f"[warn] Toss KRW buying-power failed for account {seq}: {exc}")

        try:
            bp_usd_payload = _toss_get("/api/v1/buying-power?currency=USD", token, seq, proxies)
            cash_usd += round(_extract_buying_power_amount(bp_usd_payload), 2)
            usd_bp_ok = True
        except Exception as exc:
            print(f"[warn] Toss USD buying-power failed for account {seq}: {exc}")

    if not krw_bp_ok and not usd_bp_ok and prev_toss:
        cash_krw = round(_to_float(prev_toss.get("cash_krw"), 0.0))
        cash_usd = 0.0
        cash_fallback = cash_krw > 0

    if cash_krw > 0:
        holdings.append(
            {
                "exchange": "토스증권",
                "category": "예수금",
                "symbol": "KRW",
                "name": "원화·외화 예수금 (이전 값 유지)" if cash_fallback else "원화 예수금",
                "currency": "KRW",
                "quantity": cash_krw,
                "price_native": 1.0,
                "value_krw": round(cash_krw),
                "value_usd": round(cash_krw / usdkrw, 2) if usdkrw > 0 else 0.0,
                "pnl_krw": None,
                "pnl_usd": None,
                "pnl_pct": None,
            }
        )
    if cash_usd > 0:
        holdings.append(
            {
                "exchange": "토스증권",
                "category": "예수금",
                "symbol": "USD",
                "name": "외화(USD) 예수금",
                "currency": "USD",
                "quantity": cash_usd,
                "price_native": usdkrw,
                "value_krw": round(cash_usd * usdkrw),
                "value_usd": round(cash_usd, 2),
                "pnl_krw": None,
                "pnl_usd": None,
                "pnl_pct": None,
            }
        )

    holdings.sort(key=lambda h: -_to_float(h.get("value_krw"), 0.0))

    total_stocks_krw = stock_krw + stock_usd_direct * usdkrw
    total_cash_krw = cash_krw + cash_usd * usdkrw
    total_krw = total_stocks_krw + total_cash_krw
    total_usd = total_krw / usdkrw if usdkrw > 0 else 0.0

    return {
        "total_krw": round(total_krw),
        "total_usd": round(total_usd, 2),
        "stocks_krw": round(total_stocks_krw),
        "stocks_usd": round(total_stocks_krw / usdkrw, 2) if usdkrw > 0 else 0.0,
        "cash_krw": round(total_cash_krw),
        "cash_usd": round(total_cash_krw / usdkrw, 2) if usdkrw > 0 else 0.0,
        "pnl_krw": round(pnl_krw_total),
        "pnl_usd": round(pnl_krw_total / usdkrw, 2) if usdkrw > 0 else 0.0,
        "holdings": holdings,
    }


# ---------------- Korea Investment & Securities (ISA Read-Only) ---------------- #


def _parse_kis_account(account_no: str) -> tuple[str, str]:
    """Split KIS account string into 8-digit CANO and 2-digit ACNT_PRDT_CD."""
    cleaned = account_no.strip()
    if "-" in cleaned:
        part1, part2 = cleaned.split("-", 1)
        cano = "".join(ch for ch in part1 if ch.isdigit())[:8]
        acnt_prdt_cd = "".join(ch for ch in part2 if ch.isdigit())[:2] or "01"
    else:
        digits = "".join(ch for ch in cleaned if ch.isdigit())
        if len(digits) >= 10:
            cano = digits[:8]
            acnt_prdt_cd = digits[8:10]
        elif len(digits) == 8:
            cano = digits
            acnt_prdt_cd = "01"
        else:
            cano, acnt_prdt_cd = "", ""
    if len(cano) != 8 or len(acnt_prdt_cd) != 2:
        raise ValueError("KIS_ACCOUNT_NO must be 8-digit CANO + 2-digit product code (e.g. 12345678-01)")
    return cano, acnt_prdt_cd


def _kis_issue_token(
    app_key: str,
    app_secret: str,
    proxies: dict[str, str] | None = None,
) -> str:
    """Issue a KIS Open API OAuth access token via direct connection with proxy fallback."""

    def _do(proxy_cfg: dict[str, str] | None) -> dict[str, Any]:
        resp = requests.post(
            f"{KIS_BASE_URL}/oauth2/tokenP",
            headers={"content-type": "application/json; charset=utf-8"},
            json={
                "grant_type": "client_credentials",
                "appkey": app_key,
                "appsecret": app_secret,
            },
            timeout=20,
            proxies=proxy_cfg,
        )
        if "EGW00133" in resp.text:
            raise RuntimeError("EGW00133_RATE_LIMIT")
        resp.raise_for_status()
        return resp.json()

    try:
        payload = _call_direct_then_proxy(_do, proxies=proxies, direct_attempts=2, delay=2.0)
    except RuntimeError as exc:
        if "EGW00133_RATE_LIMIT" not in str(exc):
            raise
        print("[info] KIS token endpoint hit 1-minute cooldown (EGW00133); waiting 62s before retry...")
        time.sleep(62)
        payload = _call_direct_then_proxy(_do, proxies=proxies, direct_attempts=2, delay=2.0)

    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not token:
        raise RuntimeError("KIS Open API did not return an access_token")
    return str(token)


def fetch_kis_isa_readonly(
    app_key: str,
    app_secret: str,
    account_no: str,
    usdkrw: float,
    proxies: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Fetch Korea Investment & Securities ISA balance and holdings strictly read-only."""
    cano, acnt_prdt_cd = _parse_kis_account(account_no)
    token = _kis_issue_token(app_key, app_secret, proxies)

    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": app_key,
        "appsecret": app_secret,
        "tr_id": "TTTC8434R",
        "custtype": "P",
    }
    params = {
        "CANO": cano,
        "ACNT_PRDT_CD": acnt_prdt_cd,
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "",
        "INQR_DVSN": "02",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "00",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }

    def _do(proxy_cfg: dict[str, str] | None) -> dict[str, Any]:
        resp = requests.get(
            f"{KIS_BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance",
            headers=headers,
            params=params,
            timeout=20,
            proxies=proxy_cfg,
        )
        resp.raise_for_status()
        return resp.json()

    payload = _call_direct_then_proxy(_do, proxies=proxies, direct_attempts=2, delay=1.5)
    if str(payload.get("rt_cd", "0")) != "0":
        raise RuntimeError(f"KIS API ({payload.get('msg_cd')}): {payload.get('msg1')}")

    output1 = payload.get("output1") or []
    output2 = payload.get("output2") or []

    holdings: list[dict[str, Any]] = []
    item_sum_krw = 0.0
    item_pnl_sum_krw = 0.0

    for item in output1:
        if not isinstance(item, dict):
            continue
        qty = _to_float(item.get("hldg_qty") or item.get("ord_psbl_qty"), 0.0)
        if qty <= 0:
            continue

        symbol = str(item.get("pdno") or "-")
        name = str(item.get("prdt_name") or symbol)
        cur_price = _to_float(item.get("prpr"), 0.0)
        avg_price = _to_float(item.get("pchs_avg_pric"), 0.0)
        eval_amt = _to_float(item.get("evlu_amt"), qty * cur_price)
        pnl_amt = _to_float(
            item.get("evlu_pfls_amt"),
            (cur_price - avg_price) * qty if cur_price and avg_price else 0.0,
        )
        pnl_rate = _to_float(item.get("evlu_pfls_rt"), 0.0)

        item_sum_krw += eval_amt
        item_pnl_sum_krw += pnl_amt

        holdings.append(
            {
                "exchange": "한국투자증권 ISA",
                "category": "ISA 국내주식/ETF",
                "symbol": symbol,
                "name": name,
                "currency": "KRW",
                "quantity": round(qty, 6),
                "price_native": round(cur_price, 2),
                "avg_price_native": round(avg_price, 2),
                "value_krw": round(eval_amt),
                "value_usd": round(eval_amt / usdkrw, 2) if usdkrw > 0 else 0.0,
                "pnl_krw": round(pnl_amt),
                "pnl_usd": round(pnl_amt / usdkrw, 2) if usdkrw > 0 else 0.0,
                "pnl_pct": round(pnl_rate, 2),
            }
        )

    out2 = output2[0] if isinstance(output2, list) and output2 and isinstance(output2[0], dict) else {}
    stocks_krw = _to_float(out2.get("scts_evlu_amt"), item_sum_krw)
    if stocks_krw <= 0 and item_sum_krw > 0:
        stocks_krw = item_sum_krw

    tot_evlu_krw = _to_float(out2.get("tot_evlu_amt"), 0.0)
    d2_cash_krw = _to_float(out2.get("prvs_rcdl_excc_amt") or out2.get("dnca_tot_amt"), 0.0)
    if tot_evlu_krw > 0:
        total_krw = tot_evlu_krw
        cash_krw = max(0.0, tot_evlu_krw - stocks_krw)
    else:
        cash_krw = max(0.0, d2_cash_krw)
        total_krw = stocks_krw + cash_krw

    pnl_krw = _to_float(out2.get("evlu_pfls_smtl_amt"), item_pnl_sum_krw)

    if cash_krw > 0:
        holdings.append(
            {
                "exchange": "한국투자증권 ISA",
                "category": "예수금",
                "symbol": "KRW",
                "name": "ISA 원화 예수금",
                "currency": "KRW",
                "quantity": round(cash_krw),
                "price_native": 1.0,
                "value_krw": round(cash_krw),
                "value_usd": round(cash_krw / usdkrw, 2) if usdkrw > 0 else 0.0,
                "pnl_krw": None,
                "pnl_usd": None,
                "pnl_pct": None,
            }
        )

    holdings.sort(key=lambda h: -_to_float(h.get("value_krw"), 0.0))

    return {
        "total_krw": round(total_krw),
        "total_usd": round(total_krw / usdkrw, 2) if usdkrw > 0 else 0.0,
        "stocks_krw": round(stocks_krw),
        "stocks_usd": round(stocks_krw / usdkrw, 2) if usdkrw > 0 else 0.0,
        "cash_krw": round(cash_krw),
        "cash_usd": round(cash_krw / usdkrw, 2) if usdkrw > 0 else 0.0,
        "pnl_krw": round(pnl_krw),
        "pnl_usd": round(pnl_krw / usdkrw, 2) if usdkrw > 0 else 0.0,
        "holdings": holdings,
    }


# ---------------- Portfolio Drawdown & Demo Payload ---------------- #


def _compute_history_metrics(history: list[dict[str, Any]]) -> dict[str, Any]:
    if not history:
        return {
            "ath_krw": 0,
            "ath_usd": 0.0,
            "ath_date": "-",
            "current_drawdown": 0.0,
            "current_dd_abs": 0.0,
            "max_drawdown": 0.0,
            "max_dd_abs": 0.0,
            "mdd_peak_date": "-",
            "mdd_trough_date": "-",
            "daily_change_krw": 0,
            "daily_change_usd": 0.0,
            "daily_change_pct": 0.0,
            "total_return_pct": 0.0,
        }

    peak_krw = -1.0
    peak_usd = -1.0
    peak_date = history[0]["date"]
    mdd = 0.0
    mdd_peak_date = history[0]["date"]
    mdd_trough_date = history[0]["date"]
    ath_krw = 0.0
    ath_usd = 0.0
    ath_date = history[0]["date"]

    for row in history:
        val_krw = _to_float(row.get("total_krw"), 0.0)
        val_usd = _to_float(row.get("total_usd"), 0.0)
        dt = row["date"]
        if val_krw >= ath_krw:
            ath_krw = val_krw
            ath_usd = val_usd
            ath_date = dt
        if val_krw > peak_krw:
            peak_krw = val_krw
            peak_usd = val_usd
            peak_date = dt
        if peak_krw > 0:
            dd = (val_krw - peak_krw) / peak_krw * 100.0
            if dd < mdd:
                mdd = dd
                mdd_peak_date = peak_date
                mdd_trough_date = dt

    latest = history[-1]
    prev = history[-2] if len(history) >= 2 else latest
    first = history[0]

    latest_krw = _to_float(latest.get("total_krw"), 0.0)
    latest_usd = _to_float(latest.get("total_usd"), 0.0)
    prev_krw = _to_float(prev.get("total_krw"), 0.0)
    prev_usd = _to_float(prev.get("total_usd"), 0.0)
    first_krw = _to_float(first.get("total_krw"), 0.0)

    cur_dd = ((latest_krw - ath_krw) / ath_krw * 100.0) if ath_krw > 0 else 0.0
    daily_change_krw = latest_krw - prev_krw
    daily_change_usd = latest_usd - prev_usd
    daily_change_pct = ((daily_change_krw / prev_krw) * 100.0) if prev_krw > 0 and len(history) >= 2 else 0.0
    total_return_pct = ((latest_krw - first_krw) / first_krw * 100.0) if first_krw > 0 else 0.0

    return {
        "ath_krw": round(ath_krw),
        "ath_usd": round(ath_usd, 2),
        "ath_date": ath_date,
        "current_drawdown": round(cur_dd, 2),
        "current_dd_abs": round(abs(cur_dd), 2),
        "max_drawdown": round(mdd, 2),
        "max_dd_abs": round(abs(mdd), 2),
        "mdd_peak_date": mdd_peak_date,
        "mdd_trough_date": mdd_trough_date,
        "daily_change_krw": round(daily_change_krw),
        "daily_change_usd": round(daily_change_usd, 2),
        "daily_change_pct": round(daily_change_pct, 2),
        "total_return_pct": round(total_return_pct, 2),
    }


def _generate_demo_portfolio(usdkrw: float) -> dict[str, Any]:
    """Generate deterministic realistic sample data until read-only API keys are connected."""
    today_kst = datetime.now(KST).date()
    days = 120
    history: list[dict[str, Any]] = []

    gate_usd = 18500.0
    toss_krw = 34000000.0

    for i in range(days):
        d = (today_kst - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        # Smooth realistic wave with growth & pullback
        wave_gate = math.sin(i * 0.14) * 0.018 + math.cos(i * 0.07) * 0.011 + 0.0022
        wave_toss = math.sin(i * 0.11 + 0.8) * 0.012 + math.cos(i * 0.05) * 0.008 + 0.0018
        if 72 <= i <= 88:
            wave_gate -= 0.012
            wave_toss -= 0.007
        gate_usd = max(8000.0, gate_usd * (1.0 + wave_gate))
        toss_krw = max(18000000.0, toss_krw * (1.0 + wave_toss))

        gate_krw = gate_usd * usdkrw
        toss_usd = toss_krw / usdkrw
        gate_spot_usd = gate_usd * 0.58
        gate_futures_usd = gate_usd * 0.42

        history.append(
            {
                "date": d,
                "total_krw": round(gate_krw + toss_krw),
                "total_usd": round(gate_usd + toss_usd, 2),
                "gate_krw": round(gate_krw),
                "gate_usd": round(gate_usd, 2),
                "gate_spot_usd": round(gate_spot_usd, 2),
                "gate_futures_usd": round(gate_futures_usd, 2),
                "toss_krw": round(toss_krw),
                "toss_usd": round(toss_usd, 2),
                "usdkrw": usdkrw,
            }
        )

    latest = history[-1]
    gate_usd_now = latest["gate_usd"]
    toss_krw_now = latest["toss_krw"]
    gate_spot_now = latest["gate_spot_usd"]
    gate_fut_now = latest["gate_futures_usd"]

    btc_val = round(gate_spot_now * 0.54, 2)
    usdt_val = round(gate_spot_now * 0.46, 2)
    fut_margin = round(gate_fut_now, 2)

    nvda_krw = round(toss_krw_now * 0.44)
    tsla_krw = round(toss_krw_now * 0.28)
    cash_krw = round(toss_krw_now - nvda_krw - tsla_krw)

    metrics = _compute_history_metrics(history)
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return {
        "updated_at": now_utc,
        "is_demo": True,
        "usdkrw": usdkrw,
        "providers": {
            "gateio": {
                "status": "not_configured",
                "mode": "read_only",
                "message": "GATE_API_KEY / GATE_API_SECRET 미설정 (샘플 표시 중)",
            },
            "toss": {
                "status": "not_configured",
                "mode": "read_only",
                "message": "TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 미설정 (샘플 표시 중)",
            },
        },
        "summary": {
            "total_krw": latest["total_krw"],
            "total_usd": latest["total_usd"],
            **metrics,
            "gateio": {
                "total_krw": latest["gate_krw"],
                "total_usd": gate_usd_now,
                "spot_usd": gate_spot_now,
                "spot_krw": round(gate_spot_now * usdkrw),
                "futures_usd": gate_fut_now,
                "futures_krw": round(gate_fut_now * usdkrw),
                "unrealized_pnl_usd": 420.5,
                "unrealized_pnl_krw": round(420.5 * usdkrw),
            },
            "toss": {
                "total_krw": toss_krw_now,
                "total_usd": latest["toss_usd"],
                "stocks_krw": nvda_krw + tsla_krw,
                "stocks_usd": round((nvda_krw + tsla_krw) / usdkrw, 2),
                "cash_krw": cash_krw,
                "cash_usd": round(cash_krw / usdkrw, 2),
                "pnl_krw": 4250000,
                "pnl_usd": round(4250000 / usdkrw, 2),
            },
        },
        "holdings": {
            "gateio": [
                {
                    "exchange": "Gate.io",
                    "category": "선물 LONG 5x",
                    "symbol": "BTC_USDT",
                    "name": "BTC/USDT Perpetual (예시)",
                    "quantity": 0.25,
                    "price_usd": 91200.0,
                    "entry_price_usd": 89518.0,
                    "value_usd": fut_margin,
                    "value_krw": round(fut_margin * usdkrw),
                    "pnl_usd": 420.5,
                    "pnl_krw": round(420.5 * usdkrw),
                    "pnl_pct": round(420.5 / max(1.0, fut_margin) * 100.0, 2),
                },
                {
                    "exchange": "Gate.io",
                    "category": "현물",
                    "symbol": "BTC",
                    "name": "BTC (Spot 예시)",
                    "quantity": round(btc_val / 91200.0, 5),
                    "price_usd": 91200.0,
                    "value_usd": btc_val,
                    "value_krw": round(btc_val * usdkrw),
                    "pnl_usd": None,
                    "pnl_pct": None,
                },
                {
                    "exchange": "Gate.io",
                    "category": "현물",
                    "symbol": "USDT",
                    "name": "USDT (Spot 예시)",
                    "quantity": usdt_val,
                    "price_usd": 1.0,
                    "value_usd": usdt_val,
                    "value_krw": round(usdt_val * usdkrw),
                    "pnl_usd": None,
                    "pnl_pct": None,
                },
            ],
            "toss": [
                {
                    "exchange": "토스증권",
                    "category": "미국주식",
                    "symbol": "NVDA",
                    "name": "NVIDIA (예시)",
                    "currency": "USD",
                    "quantity": 100,
                    "price_native": round(nvda_krw / 100 / usdkrw, 2),
                    "value_krw": nvda_krw,
                    "value_usd": round(nvda_krw / usdkrw, 2),
                    "pnl_krw": 3100000,
                    "pnl_usd": round(3100000 / usdkrw, 2),
                    "pnl_pct": 18.4,
                },
                {
                    "exchange": "토스증권",
                    "category": "미국주식",
                    "symbol": "TSLA",
                    "name": "Tesla (예시)",
                    "currency": "USD",
                    "quantity": 35,
                    "price_native": round(tsla_krw / 35 / usdkrw, 2),
                    "value_krw": tsla_krw,
                    "value_usd": round(tsla_krw / usdkrw, 2),
                    "pnl_krw": 1150000,
                    "pnl_usd": round(1150000 / usdkrw, 2),
                    "pnl_pct": 11.2,
                },
                {
                    "exchange": "토스증권",
                    "category": "예수금",
                    "symbol": "KRW",
                    "name": "원화 예수금 (예시)",
                    "currency": "KRW",
                    "quantity": cash_krw,
                    "price_native": 1.0,
                    "value_krw": cash_krw,
                    "value_usd": round(cash_krw / usdkrw, 2),
                    "pnl_krw": None,
                    "pnl_usd": None,
                    "pnl_pct": None,
                },
            ],
        },
        "history": history,
    }


def main() -> None:
    PORTFOLIO_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if PORTFOLIO_PATH.exists():
        try:
            existing = json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    proxies = _build_proxies(os.getenv("PORTFOLIO_PROXY_URL", ""))
    usdkrw = fetch_usdkrw(proxies=proxies, fallback=_to_float(existing.get("usdkrw"), 1400.0))
    usdt_krw = fetch_upbit_usdt_krw(
        proxies=proxies,
        fallback=_to_float(existing.get("usdt_krw"), usdkrw),
    )

    gate_key = os.getenv("GATE_API_KEY", "").strip()
    gate_secret = os.getenv("GATE_API_SECRET", "").strip()
    toss_id = os.getenv("TOSS_CLIENT_ID", "").strip()
    toss_secret = os.getenv("TOSS_CLIENT_SECRET", "").strip()
    kis_key = os.getenv("KIS_APP_KEY", "").strip()
    kis_secret = os.getenv("KIS_APP_SECRET", "").strip()
    kis_account = os.getenv("KIS_ACCOUNT_NO", "").strip()

    has_gate_creds = bool(gate_key and gate_secret)
    has_toss_creds = bool(toss_id and toss_secret)
    has_kis_creds = bool(kis_key and kis_secret and kis_account)

    if not has_gate_creds and not has_toss_creds and not has_kis_creds:
        if existing:
            print("No API keys configured; keeping existing portfolio.json without overwriting.")
            return
        payload = _generate_demo_portfolio(usdkrw)
        PORTFOLIO_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Generated initial demo portfolio snapshot at {PORTFOLIO_PATH}")
        return

    was_demo = bool(existing.get("is_demo", False))
    prev_summary = {} if was_demo else (existing.get("summary") or {})
    prev_holdings = {} if was_demo else (existing.get("holdings") or {})
    history: list[dict[str, Any]] = [] if was_demo else list(existing.get("history") or [])

    providers_status: dict[str, Any] = {}

    # 1) Gate.io Read-Only Fetch (valued in KRW via Upbit KRW-USDT market price)
    if has_gate_creds:
        try:
            gate_data = fetch_gateio_readonly(
                gate_key,
                gate_secret,
                usdt_krw,
                proxies,
                prev_gate=prev_summary.get("gateio"),
                prev_gate_holdings=prev_holdings.get("gateio"),
            )
            providers_status["gateio"] = {
                "status": "connected",
                "mode": "read_only",
                "message": "Gate.io 읽기 전용 API v4 정상 동기화 (업비트 USDT 시세 적용)",
            }
        except Exception as exc:
            print(f"[warn] Gate.io fetch failed: {exc}")
            gate_data = dict(prev_summary.get("gateio") or {
                "total_krw": 0,
                "total_usd": 0.0,
                "spot_usd": 0.0,
                "spot_krw": 0,
                "futures_usd": 0.0,
                "futures_krw": 0,
                "unrealized_pnl_usd": 0.0,
                "unrealized_pnl_krw": 0,
            })
            gate_data["holdings"] = list(prev_holdings.get("gateio") or [])
            providers_status["gateio"] = {
                "status": "error",
                "mode": "read_only",
                "message": f"Gate.io 조회 오류 (이전 값 유지): {exc}",
            }
    else:
        gate_data = {
            "total_krw": 0,
            "total_usd": 0.0,
            "spot_usd": 0.0,
            "spot_krw": 0,
            "futures_usd": 0.0,
            "futures_krw": 0,
            "unrealized_pnl_usd": 0.0,
            "unrealized_pnl_krw": 0,
            "holdings": [],
        }
        providers_status["gateio"] = {
            "status": "not_configured",
            "mode": "read_only",
            "message": "GATE_API_KEY / GATE_API_SECRET 미설정",
        }

    # 2) Toss Securities Read-Only Fetch
    if has_toss_creds:
        try:
            toss_data = fetch_toss_readonly(
                toss_id,
                toss_secret,
                usdkrw,
                proxies,
                prev_toss=prev_summary.get("toss"),
            )
            providers_status["toss"] = {
                "status": "connected",
                "mode": "read_only",
                "message": "토스증권 조회 전용 Open API 정상 동기화",
            }
        except Exception as exc:
            print(f"[warn] Toss fetch failed: {exc}")
            toss_data = dict(prev_summary.get("toss") or {
                "total_krw": 0,
                "total_usd": 0.0,
                "stocks_krw": 0,
                "stocks_usd": 0.0,
                "cash_krw": 0,
                "cash_usd": 0.0,
                "pnl_krw": 0,
                "pnl_usd": 0.0,
            })
            toss_data["holdings"] = list(prev_holdings.get("toss") or [])
            providers_status["toss"] = {
                "status": "error",
                "mode": "read_only",
                "message": f"토스증권 조회 오류 (허용 IP 또는 토큰 확인 필요): {exc}",
            }
    else:
        toss_data = {
            "total_krw": 0,
            "total_usd": 0.0,
            "stocks_krw": 0,
            "stocks_usd": 0.0,
            "cash_krw": 0,
            "cash_usd": 0.0,
            "pnl_krw": 0,
            "pnl_usd": 0.0,
            "holdings": [],
        }
        providers_status["toss"] = {
            "status": "not_configured",
            "mode": "read_only",
            "message": "TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 미설정",
        }

    # 3) Korea Investment & Securities (ISA) Read-Only Fetch
    if has_kis_creds:
        try:
            kis_data = fetch_kis_isa_readonly(kis_key, kis_secret, kis_account, usdkrw, proxies)
            providers_status["kis"] = {
                "status": "connected",
                "mode": "read_only",
                "message": "한국투자증권 중개형 ISA 조회 전용 Open API 정상 동기화",
            }
        except Exception as exc:
            print(f"[warn] KIS ISA fetch failed: {exc}")
            kis_data = dict(prev_summary.get("kis") or {
                "total_krw": 0,
                "total_usd": 0.0,
                "stocks_krw": 0,
                "stocks_usd": 0.0,
                "cash_krw": 0,
                "cash_usd": 0.0,
                "pnl_krw": 0,
                "pnl_usd": 0.0,
            })
            kis_data["holdings"] = list(prev_holdings.get("kis") or [])
            providers_status["kis"] = {
                "status": "error",
                "mode": "read_only",
                "message": f"한국투자증권 ISA 조회 오류 (이전 값 유지): {exc}",
            }
    else:
        kis_data = {
            "total_krw": 0,
            "total_usd": 0.0,
            "stocks_krw": 0,
            "stocks_usd": 0.0,
            "cash_krw": 0,
            "cash_usd": 0.0,
            "pnl_krw": 0,
            "pnl_usd": 0.0,
            "holdings": [],
        }
        providers_status["kis"] = {
            "status": "not_configured",
            "mode": "read_only",
            "message": "KIS_APP_KEY / KIS_APP_SECRET / KIS_ACCOUNT_NO 미설정",
        }

    gate_holdings = gate_data.pop("holdings", [])
    toss_holdings = toss_data.pop("holdings", [])
    kis_holdings = kis_data.pop("holdings", [])

    total_krw = round(
        _to_float(gate_data.get("total_krw"), 0.0)
        + _to_float(toss_data.get("total_krw"), 0.0)
        + _to_float(kis_data.get("total_krw"), 0.0)
    )
    total_usd = round(
        _to_float(gate_data.get("total_usd"), 0.0)
        + _to_float(toss_data.get("total_usd"), 0.0)
        + _to_float(kis_data.get("total_usd"), 0.0),
        2,
    )

    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    today_entry = {
        "date": today_str,
        "total_krw": total_krw,
        "total_usd": total_usd,
        "gate_krw": round(_to_float(gate_data.get("total_krw"), 0.0)),
        "gate_usd": round(_to_float(gate_data.get("total_usd"), 0.0), 2),
        "gate_spot_usd": round(_to_float(gate_data.get("spot_usd"), 0.0), 2),
        "gate_futures_usd": round(_to_float(gate_data.get("futures_usd"), 0.0), 2),
        "toss_krw": round(_to_float(toss_data.get("total_krw"), 0.0)),
        "toss_usd": round(_to_float(toss_data.get("total_usd"), 0.0), 2),
        "kis_krw": round(_to_float(kis_data.get("total_krw"), 0.0)),
        "kis_usd": round(_to_float(kis_data.get("total_usd"), 0.0), 2),
        "usdkrw": usdkrw,
        "usdt_krw": usdt_krw,
    }

    by_date = {
        str(row["date"]): row
        for row in history
        if isinstance(row, dict) and row.get("date") and _to_float(row.get("total_krw"), 0.0) > 0
    }
    if total_krw > 0:
        by_date[today_str] = today_entry
    sorted_history = [by_date[d] for d in sorted(by_date.keys())]

    metrics = _compute_history_metrics(sorted_history)
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    payload = {
        "updated_at": now_utc,
        "is_demo": False,
        "usdkrw": usdkrw,
        "usdt_krw": usdt_krw,
        "providers": providers_status,
        "summary": {
            "total_krw": total_krw,
            "total_usd": total_usd,
            **metrics,
            "gateio": gate_data,
            "toss": toss_data,
            "kis": kis_data,
        },
        "holdings": {
            "gateio": gate_holdings,
            "toss": toss_holdings,
            "kis": kis_holdings,
        },
        "history": sorted_history,
    }

    tmp_path = PORTFOLIO_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(PORTFOLIO_PATH)
    print(f"Saved real portfolio snapshot for {today_str} -> {PORTFOLIO_PATH}")


if __name__ == "__main__":
    main()
