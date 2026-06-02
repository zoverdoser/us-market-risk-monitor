#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import datetime as dt
import email.utils
import io
import json
import math
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


CBOE_QUOTES = "https://cdn.cboe.com/api/global/delayed_quotes/quotes/{symbol}.json"
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FRED_API = "https://api.stlouisfed.org/fred/series/observations"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
TRADINGVIEW_FUTURES_SCAN = "https://scanner.tradingview.com/futures/scan"

CORE_ETFS = ["SPY", "RSP", "QQQ", "QQEW", "IWM", "XLF", "KRE", "SOXX", "SMH", "IYT"]
FRED_SERIES = {
    "HY OAS": "BAMLH0A0HYM2",
    "IG OAS": "BAMLC0A0CM",
    "2Y Treasury": "DGS2",
    "10Y Treasury": "DGS10",
    "10Y real yield": "DFII10",
}
MONTH_CODES = {
    1: "F",
    2: "G",
    3: "H",
    4: "J",
    5: "K",
    6: "M",
    7: "N",
    8: "Q",
    9: "U",
    10: "V",
    11: "X",
    12: "Z",
}


class FredApiKeyMissing(RuntimeError):
    pass


def fetch_text(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def fred_csv_url(series: str, today: dt.date | None = None, lookback_days: int = 240) -> str:
    if today is None:
        today = dt.date.today()
    start = today - dt.timedelta(days=lookback_days)
    query = urllib.parse.urlencode({
        "id": series,
        "cosd": start.isoformat(),
        "coed": today.isoformat(),
    })
    return f"{FRED_CSV}?{query}"


def fred_api_url(series: str, api_key: str, today: dt.date | None = None, lookback_days: int = 240) -> str:
    if today is None:
        today = dt.date.today()
    start = today - dt.timedelta(days=lookback_days)
    query = urllib.parse.urlencode({
        "series_id": series,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": start.isoformat(),
        "observation_end": today.isoformat(),
    })
    return f"{FRED_API}?{query}"


def fetch_json(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict | None = None,
    timeout: int = 15,
) -> dict:
    request_headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, data=body, method=method, headers=request_headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def third_wednesday(year: int, month: int) -> dt.date:
    first = dt.date(year, month, 1)
    return first + dt.timedelta(days=(2 - first.weekday()) % 7 + 14)


def vx_contract_symbols(today: dt.date | None = None) -> tuple[dict, dict]:
    if today is None:
        today = dt.date.today()
    front_expiry = third_wednesday(today.year, today.month)
    if today > front_expiry:
        next_year = today.year + (1 if today.month == 12 else 0)
        next_month = 1 if today.month == 12 else today.month + 1
        front_expiry = third_wednesday(next_year, next_month)
    second_year = front_expiry.year + (1 if front_expiry.month == 12 else 0)
    second_month = 1 if front_expiry.month == 12 else front_expiry.month + 1
    second_expiry = third_wednesday(second_year, second_month)
    front = {"symbol": f"CBOE:VX{MONTH_CODES[front_expiry.month]}{front_expiry.year}", "expiry": front_expiry}
    second = {"symbol": f"CBOE:VX{MONTH_CODES[second_expiry.month]}{second_expiry.year}", "expiry": second_expiry}
    return front, second


def parse_vx_scanner_response(payload: dict, front: dict, second: dict) -> dict:
    rows = {}
    for row in payload.get("data", []):
        values = row.get("d") or []
        rows[row.get("s")] = values[0] if values else None
    vx1 = rows.get(front["symbol"])
    vx2 = rows.get(second["symbol"])
    if vx1 is None or vx2 is None:
        return {
            "available": False,
            "source": "TradingView futures scanner",
            "vx1_symbol": front["symbol"],
            "vx2_symbol": second["symbol"],
            "error": "front or second VX contract missing from scanner response",
        }
    return {
        "available": True,
        "source": "TradingView futures scanner",
        "vx1_symbol": front["symbol"],
        "vx1_expiry": front["expiry"].isoformat(),
        "vx1_price": float(vx1),
        "vx2_symbol": second["symbol"],
        "vx2_expiry": second["expiry"].isoformat(),
        "vx2_price": float(vx2),
        "spread": float(vx1) - float(vx2),
    }


def fetch_current_vx() -> dict:
    front, second = vx_contract_symbols()
    body = json.dumps({
        "symbols": {"tickers": [front["symbol"], second["symbol"]]},
        "columns": ["close", "change", "change_abs", "description", "update_mode"],
    }).encode("utf-8")
    try:
        payload = fetch_json(
            TRADINGVIEW_FUTURES_SCAN,
            method="POST",
            body=body,
            headers={"Content-Type": "application/json"},
            timeout=12,
        )
        return parse_vx_scanner_response(payload, front, second)
    except Exception as exc:
        return {
            "available": False,
            "source": "TradingView futures scanner",
            "vx1_symbol": front["symbol"],
            "vx2_symbol": second["symbol"],
            "error": repr(exc),
        }


def classify_vx_spread(spread: float | None) -> dict:
    if spread is None or math.isnan(spread):
        return {
            "level": "green",
            "label": "VX1!-VX2! unavailable",
            "action_hint": "核心闸门不可用，降低结论置信度",
            "available": False,
            "confidence": "low",
        }
    if spread <= -2.0:
        return {
            "level": "orange",
            "label": "极度脆弱：深度 contango / 乐观过头",
            "action_hint": "禁止主动加高 beta",
            "available": True,
            "confidence": "high",
        }
    if spread <= -1.0:
        return {"level": "orange", "label": "警戒：风险定价偏低", "action_hint": "弱信号减半，新仓提高门槛", "available": True, "confidence": "high"}
    if spread < 0:
        return {"level": "green", "label": "正常 contango", "action_hint": "不追高，但无需额外防守", "available": True, "confidence": "high"}
    if spread <= 1.5:
        return {"level": "orange", "label": "压缩警戒：短端风险抬头", "action_hint": "减高 beta，检查保护", "available": True, "confidence": "high"}
    return {"level": "red", "label": "避险状态：backwardation", "action_hint": "进入防守模式", "available": True, "confidence": "high"}


def level_rank(level: str) -> int:
    return {"green": 0, "yellow": 1, "orange": 2, "red": 3}.get(level, 1)


def light_from_level(level: str) -> str:
    return {"green": "绿", "yellow": "黄", "orange": "橙", "red": "红"}.get(level, "黄")


def format_unavailable(metric: str, proxy: str | None = None) -> str:
    if proxy:
        return f"{metric} exact source not configured; {proxy}"
    return f"{metric} exact source not configured"


def map_action_light(modules: dict[str, dict]) -> dict:
    events = modules.get("events", {})
    if events.get("override") and events.get("level") == "red":
        return action_payload("红", "事件层 override：重大事件直接提升整体风险等级")
    if events.get("override") and level_rank(events.get("level", "green")) >= 2:
        return action_payload("橙", "事件层 override：事件已明显冲击风险偏好")

    risk_modules = {name: item for name, item in modules.items() if item.get("available", True)}
    unavailable = [name for name, item in modules.items() if not item.get("available", True)]
    confidence_note = f"；{', '.join(unavailable)} 数据不可用，结论置信度降低" if unavailable else ""

    reds = [name for name, item in risk_modules.items() if level_rank(item.get("level", "green")) >= 3]
    oranges = [name for name, item in risk_modules.items() if level_rank(item.get("level", "green")) >= 2]
    yellows = [name for name, item in risk_modules.items() if level_rank(item.get("level", "green")) >= 1]

    if len(reds) >= 2 or (reds and len(oranges) >= 2):
        return action_payload("红", "多个独立模块同时红或红色风险获得跨模块确认" + confidence_note)
    if reds:
        return action_payload("橙", f"核心模块红色但尚未形成红灯确认：{', '.join(reds)}" + confidence_note)
    if len(oranges) >= 2:
        return action_payload("橙", "两个独立模块同时恶化" + confidence_note)
    if len(yellows) >= 1:
        return action_payload("黄", "任一核心模块出现脆弱信号，但未形成跨模块确认" + confidence_note)
    return action_payload("绿", "情绪、传导、结构、事件与慢变量均未显示明显恶化" + confidence_note)


def action_payload(light: str, reason: str) -> dict:
    table = {
        "绿": {
            "do": "允许正常配置；观望标的按正常标准评估；新增仓位不需要额外折扣。",
            "avoid": "不追高；不因为绿灯忽略个股基本面和流动性。",
            "high_beta": "可按原计划评估。",
            "watchlist": "正常准入。",
            "core_book": "无需额外防守。",
        },
        "黄": {
            "do": "保留更多现金；新信号减半；观望标的提高准入门槛。",
            "avoid": "停止新增高 beta；不主动追纳指 / 日股。",
            "high_beta": "停止新加，已有仓位看个股质量。",
            "watchlist": "提高赔率与回撤要求。",
            "core_book": "检查组合相关性与流动性。",
        },
        "橙": {
            "do": "防守资产优先；检查沉淀层是否需要对冲思维。",
            "avoid": "停止所有高 beta 加仓；观望标的不纳入。",
            "high_beta": "只减不加，除非低相关且高把握。",
            "watchlist": "暂停纳入。",
            "core_book": "评估对冲、现金和可逆性。",
        },
        "红": {
            "do": "进入防守模式；关注流动性、赎回节奏、可逆性与 QDII 退出代价。",
            "avoid": "新仓暂停；不做高 beta 追涨或流动性差的逆向抄底。",
            "high_beta": "暂停。",
            "watchlist": "暂停。",
            "core_book": "优先降低组合脆弱度。",
        },
    }
    return {"light": light, "reason": reason, **table[light]}


def parse_numeric_csv(text: str, value_col: str = "VALUE") -> list[tuple[dt.date, float]]:
    rows: list[tuple[dt.date, float]] = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        raw = row.get(value_col) or row.get("value") or row.get(reader.fieldnames[-1])
        if raw in (None, "", "."):
            continue
        try:
            value = float(raw)
            date_value = dt.date.fromisoformat(row.get("DATE") or row.get("date") or row[reader.fieldnames[0]])
        except (ValueError, TypeError):
            continue
        rows.append((date_value, value))
    return rows


def parse_fred_observations(payload: dict) -> list[tuple[dt.date, float]]:
    rows: list[tuple[dt.date, float]] = []
    for observation in payload.get("observations", []):
        raw = observation.get("value")
        if raw in (None, "", "."):
            continue
        try:
            rows.append((dt.date.fromisoformat(observation["date"]), float(raw)))
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def latest_window_stats(rows: list[tuple[dt.date, float]], percentile_window: int = 60) -> dict:
    if not rows:
        return {"available": False}
    values = [v for _, v in rows]
    latest = values[-1]
    p_values = values[-percentile_window:]
    rank = sum(1 for v in p_values if v <= latest) / len(p_values) if p_values else None
    return {
        "available": True,
        "date": rows[-1][0].isoformat(),
        "latest": latest,
        "change_5d": latest - values[-6] if len(values) >= 6 else None,
        "change_20d": latest - values[-21] if len(values) >= 21 else None,
        "percentile_60d": rank,
        "percentile_window": len(p_values),
        "rows": rows,
    }


def fetch_fred_series(series: str, lookback_days: int = 240) -> dict:
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        raise FredApiKeyMissing("FRED_API_KEY not configured; exact FRED API data skipped")
    payload = fetch_json(fred_api_url(series, api_key, lookback_days=lookback_days), timeout=12)
    stats = latest_window_stats(parse_fred_observations(payload), percentile_window=240)
    if stats.get("available"):
        stats["source"] = f"FRED API {series}"
    return stats


def classify_credit(name: str, stats: dict) -> dict:
    if not stats.get("available"):
        return {"level": "yellow", "summary": f"{name} unavailable"}
    ch5 = stats.get("change_5d") or 0
    ch20 = stats.get("change_20d") or 0
    pct = stats.get("percentile_60d") or 0
    if ch20 >= 0.4 or (ch5 >= 0.2 and pct >= 0.8):
        level = "red" if name.startswith("HY") else "orange"
    elif ch20 >= 0.2 or ch5 >= 0.1 or pct >= 0.75:
        level = "orange"
    elif ch20 >= 0.08 or ch5 >= 0.05:
        level = "yellow"
    else:
        level = "green"
    return {"level": level, "summary": f"{name} {stats['latest']:.2f}, 5d {fmt_delta(ch5)}, 20d {fmt_delta(ch20)}"}


def fetch_cboe_quote(symbol: str) -> dict:
    try:
        data = json.loads(fetch_text(CBOE_QUOTES.format(symbol=symbol), timeout=8))
        payload = data.get("data", {})
        quote = payload.get("quote") or {}
        value = payload.get("current_price")
        if value is None and isinstance(quote, dict):
            value = quote.get("last") or quote.get("close") or quote.get("current_price")
        return {"available": value is not None, "value": float(value) if value is not None else None}
    except Exception as exc:
        return {"available": False, "error": repr(exc)}


def fetch_yahoo_chart(symbol: str, rng: str = "3mo") -> list[tuple[dt.date, float]]:
    encoded = urllib.parse.quote(symbol)
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{encoded}?interval=1d&range={rng}"
    data = json.loads(fetch_text(url))
    result = data["chart"]["result"][0]
    timestamps = result.get("timestamp") or []
    closes = result["indicators"]["quote"][0].get("close") or []
    rows = []
    for ts, close in zip(timestamps, closes):
        if close is not None:
            rows.append((dt.date.fromtimestamp(ts), float(close)))
    return rows


def fetch_yahoo_stats(symbol: str, rng: str = "3mo") -> dict:
    stats = latest_window_stats(fetch_yahoo_chart(symbol, rng=rng))
    if stats.get("available"):
        stats["source"] = f"Yahoo Finance {symbol}"
        stats["symbol"] = symbol
    return stats


def fred_fallback_reason(exc: Exception) -> str:
    if isinstance(exc, FredApiKeyMissing):
        return "FRED_API_KEY not configured"
    return f"FRED API failed: {type(exc).__name__}"


def fetch_series_with_yahoo_fallback(series: str, yahoo_symbol: str, proxy_label: str) -> dict:
    try:
        return fetch_fred_series(series)
    except Exception as exc:
        stats = fetch_yahoo_stats(yahoo_symbol)
        if stats.get("available"):
            stats["source"] = f"{proxy_label} via Yahoo Finance {yahoo_symbol}; {fred_fallback_reason(exc)}"
            stats["is_proxy"] = True
        return stats


def credit_etf_proxy(name: str, risk_symbol: str, base_symbol: str = "SHY", reason: str = "FRED exact unavailable") -> dict:
    risk = fetch_yahoo_chart(risk_symbol)
    base = fetch_yahoo_chart(base_symbol)
    rels = {days: relative_return(risk, base, days) for days in [5, 20, 60]}
    r20 = rels[20]
    if r20 is None:
        return {"level": "yellow", "summary": f"{name} proxy unavailable"}
    if r20 <= -3:
        level = "orange"
    elif r20 <= -1:
        level = "yellow"
    else:
        level = "green"
    return {
        "level": level,
        "summary": (
            f"{name} {reason}; using {risk_symbol} vs {base_symbol} credit ETF proxy: "
            f"5d {fmt_pct(rels[5])}, 20d {fmt_pct(rels[20])}, 60d {fmt_pct(rels[60])}"
        ),
    }


def rate_proxy_summary(name: str, series: str, yahoo_symbol: str, proxy_label: str) -> dict:
    stats = fetch_series_with_yahoo_fallback(series, yahoo_symbol, proxy_label)
    return classify_rate_pressure_from_stats(name, stats, yahoo_symbol, proxy_label)


def classify_rate_pressure_from_stats(name: str, stats: dict, yahoo_symbol: str, proxy_label: str) -> dict:
    if not stats.get("available"):
        return {"level": "yellow", "summary": f"{name} proxy unavailable"}
    ch20 = stats.get("change_20d") or 0
    source = stats.get("source", proxy_label)
    pressure_20d = ch20
    if stats.get("is_proxy") and yahoo_symbol in {"ZT=F", "IEF", "TIP"}:
        pressure_20d = -ch20
    level = "orange" if pressure_20d >= 0.35 else "yellow" if pressure_20d >= 0.18 else "green"
    if stats.get("is_proxy") and yahoo_symbol in {"ZT=F", "IEF", "TIP"}:
        level = "yellow" if pressure_20d >= 0.5 else "green"
    return {
        "level": level,
        "summary": f"{name}: {stats['latest']:.2f}, 5d {fmt_delta(stats.get('change_5d'))}, 20d {fmt_delta(ch20)} ({source})",
        "stats": stats,
        "pressure_20d": pressure_20d,
    }


def relative_return(a_rows: list[tuple[dt.date, float]], b_rows: list[tuple[dt.date, float]], days: int) -> float | None:
    if len(a_rows) <= days or len(b_rows) <= days:
        return None
    a = a_rows[-1][1] / a_rows[-days - 1][1] - 1
    b = b_rows[-1][1] / b_rows[-days - 1][1] - 1
    return (a - b) * 100


def classify_relative(name: str, rels: dict[int, float | None], negative_is_bad: bool = True) -> dict:
    r20 = rels.get(20)
    r60 = rels.get(60)
    if r20 is None:
        return {"level": "yellow", "summary": f"{name} unavailable"}
    pressure = -r20 if negative_is_bad else r20
    if pressure >= 5 or (pressure >= 3 and r60 is not None and (-r60 if negative_is_bad else r60) >= 6):
        level = "orange"
    elif pressure >= 2:
        level = "yellow"
    else:
        level = "green"
    return {"level": level, "summary": f"{name}: 5d {fmt_pct(rels.get(5))}, 20d {fmt_pct(r20)}, 60d {fmt_pct(r60)}"}


def daily_changes(rows: list[tuple[dt.date, float]]) -> list[float]:
    return [rows[i][1] - rows[i - 1][1] for i in range(1, len(rows))]


def sample_std(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance)


def rolling_change_vol(rows: list[tuple[dt.date, float]], window: int) -> float | None:
    changes = daily_changes(rows)
    if len(changes) < window:
        return None
    return sample_std(changes[-window:])


def aggregate_structure_buckets(conclusions: list[dict]) -> dict:
    risky_buckets = {
        conclusion.get("bucket")
        for conclusion in conclusions
        if conclusion.get("bucket") and level_rank(conclusion.get("level", "green")) >= 1
    }
    orange_buckets = {
        conclusion.get("bucket")
        for conclusion in conclusions
        if conclusion.get("bucket") and level_rank(conclusion.get("level", "green")) >= 2
    }
    if len(orange_buckets) >= 1 or len(risky_buckets) >= 2:
        return {"level": "orange", "summary": "不同结构风险桶同步恶化，指数存在表面强、内部窄风险。"}
    if risky_buckets:
        return {"level": "yellow", "summary": "结构出现局部掉队，尚未形成不同结构风险桶确认。"}
    return {"level": "green", "summary": "ETF 广度代理未显示明显领导权狭窄。"}


def classify_oil_pressure(stats: dict) -> dict:
    if not stats.get("available"):
        return {"level": "green", "summary": "Brent / WTI exact quotes unavailable; using headline proxy only", "available": False}
    ch5 = stats.get("change_5d") or 0
    ch20 = stats.get("change_20d") or 0
    if ch20 >= 10 or ch5 >= 5:
        level = "orange"
    elif ch20 >= 5 or ch5 >= 3:
        level = "yellow"
    else:
        level = "green"
    return {
        "level": level,
        "summary": f"油价压力：latest {stats['latest']:.2f}, 5d {fmt_delta(ch5)}, 20d {fmt_delta(ch20)}",
        "available": True,
    }


def fetch_oil_stats() -> dict:
    for symbol in ["BZ=F", "CL=F"]:
        try:
            stats = fetch_yahoo_stats(symbol)
        except Exception:
            continue
        if stats.get("available"):
            stats["source"] = f"Yahoo Finance oil futures proxy {symbol}"
            return stats
    return {"available": False}


def fmt_delta(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.2f}"


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.1f}%"


def fetch_google_news(query: str, limit: int = 5) -> list[dict]:
    text = fetch_text(GOOGLE_NEWS_RSS.format(query=urllib.parse.quote(query)))
    root = ET.fromstring(text)
    items = []
    for item in root.findall(".//item")[:limit]:
        title = item.findtext("title") or ""
        source = item.findtext("source") or ""
        published = item.findtext("pubDate") or ""
        parsed = email.utils.parsedate_to_datetime(published) if published else None
        items.append({
            "time": parsed.isoformat() if parsed else published,
            "source": source,
            "title": re.sub(r"\s+", " ", title).strip(),
            "type": classify_event_type(title),
            "override": is_override_event(title),
        })
    return items


def classify_event_type(title: str) -> str:
    text = title.lower()
    if any(w in text for w in ["war", "missile", "iran", "israel", "oil", "geopolitical"]):
        return "地缘"
    if any(w in text for w in ["fed", "tariff", "policy", "regulation", "sec", "justice"]):
        return "政策 / 监管"
    if any(w in text for w in ["earnings", "guidance", "nvidia", "apple", "microsoft", "meta", "amazon"]):
        return "龙头财报"
    if any(w in text for w in ["default", "bankruptcy", "credit", "liquidity", "margin call"]):
        return "信用事故"
    return "市场事件"


def is_override_event(title: str) -> bool:
    text = title.lower()
    non_actionable = ["debate", "fear", "fears", "old", "could", "may", "might", "risk of", "warning"]
    if any(w in text for w in non_actionable):
        return False
    systemic_phrases = [
        "liquidity crisis",
        "margin call",
        "triggers liquidity",
        "systemic",
        "defaults on",
        "files bankruptcy",
        "bankruptcy filing",
        "missile strike",
        "war escalates",
        "market crash",
    ]
    return any(w in text for w in systemic_phrases)


def module_sentiment() -> dict:
    lines = []
    vx = fetch_current_vx()
    spread = vx.get("spread") if vx.get("available") else None
    vx_class = classify_vx_spread(spread)
    if vx.get("available"):
        lines.append(
            f"VX1!-VX2!: {spread:.2f}，VX1 {vx['vx1_symbol']}={vx['vx1_price']:.2f}，"
            f"VX2 {vx['vx2_symbol']}={vx['vx2_price']:.2f}；{vx_class['label']}；{vx_class['action_hint']}。"
        )
        lines.append(f"VX 数据源：{vx['source']}。")
    else:
        lines.append(f"VX1!-VX2!: exact unavailable via {vx.get('source')} ({vx.get('error')}); 尝试使用 VIX 期限结构辅助判断。")
    quotes = {sym: fetch_cboe_quote(sym) for sym in ["_VIX9D", "_VIX", "_VIX3M", "_VIX6M"]}
    available = {k: v["value"] for k, v in quotes.items() if v.get("available")}
    if available:
        lines.append("VIX 辅助项：" + ", ".join(f"{k}={v:.2f}" for k, v in available.items()))
        if "_VIX9D" in available and "_VIX" in available and available["_VIX9D"] > available["_VIX"]:
            vx_class = max_level(vx_class, {"level": "orange"})
            lines.append("短端 VIX9D 高于 VIX，短端恐慌抬头。")
        if "_VIX" in available and "_VIX3M" in available and available["_VIX"] > available["_VIX3M"]:
            vx_class = max_level(vx_class, {"level": "red"})
            lines.append("VIX 高于 VIX3M，期限结构进入避险压缩。")
    else:
        lines.append("VIX9D / VIX / VIX3M / VIX6M unavailable from CBOE delayed quotes.")
    return {
        "level": vx_class["level"],
        "summary": vx_class["label"],
        "lines": lines,
        "available": vx_class.get("available", True),
        "confidence": vx_class.get("confidence", "high"),
    }


def max_level(a: dict, b: dict) -> dict:
    return a if level_rank(a.get("level", "green")) >= level_rank(b.get("level", "green")) else {**a, **b}


def module_transmission() -> dict:
    lines = []
    conclusions = []
    for name, proxy in [("HY OAS", "HYG"), ("IG OAS", "LQD")]:
        try:
            stats = fetch_fred_series(FRED_SERIES[name])
            conclusion = classify_credit(name, stats)
        except Exception as exc:
            try:
                conclusion = credit_etf_proxy(name, proxy, reason=fred_fallback_reason(exc))
            except Exception as proxy_exc:
                conclusion = {"level": "yellow", "summary": f"{name} proxy unavailable after FRED {type(exc).__name__}: {proxy_exc!r}"}
        conclusions.append(conclusion)
        lines.append(conclusion["summary"])

    try:
        dgs2 = fetch_series_with_yahoo_fallback("DGS2", "ZT=F", "2Y Treasury futures proxy")
        dgs10 = fetch_series_with_yahoo_fallback("DGS10", "^TNX", "10Y Treasury yield proxy")
        lines.append("MOVE exact source not configured; using Treasury yield volatility proxy")
        rate_level = classify_rate_vol_proxy(dgs2, dgs10)
    except Exception as exc:
        rate_level = {"level": "yellow", "summary": f"MOVE / rates proxy fallback failed: {exc!r}"}
    conclusions.append(rate_level)
    lines.append(rate_level["summary"])
    lines.append("SOFR-OIS exact source not configured; using credit + rates + banks as proxy")

    level = max((c["level"] for c in conclusions), key=level_rank)
    return {"level": level, "summary": "；".join(c["summary"] for c in conclusions), "lines": lines}


def classify_rate_vol_proxy(dgs2: dict, dgs10: dict) -> dict:
    if not dgs2.get("available") or not dgs10.get("available"):
        return {"level": "yellow", "summary": "Treasury yield volatility proxy unavailable"}
    vol2_20 = rolling_change_vol(dgs2.get("rows", []), 20)
    vol10_20 = rolling_change_vol(dgs10.get("rows", []), 20)
    vol2_10 = rolling_change_vol(dgs2.get("rows", []), 10)
    vol10_10 = rolling_change_vol(dgs10.get("rows", []), 10)
    observed = [value for value in [vol2_20, vol10_20, vol2_10, vol10_10] if value is not None]
    if not observed:
        move = max(abs(dgs2.get("change_5d") or 0), abs(dgs10.get("change_5d") or 0))
        summary = f"利率波动代理：2Y 5d {fmt_delta(dgs2.get('change_5d'))}, 10Y 5d {fmt_delta(dgs10.get('change_5d'))}"
    else:
        move = max(observed)
        summary = f"利率波动代理：2Y 20d vol {fmt_delta(vol2_20)}, 10Y 20d vol {fmt_delta(vol10_20)}"
    if move >= 0.35:
        level = "orange"
    elif move >= 0.18:
        level = "yellow"
    else:
        level = "green"
    return {"level": level, "summary": summary}


def module_structure() -> dict:
    prices = {}
    lines = []
    for symbol in CORE_ETFS:
        try:
            prices[symbol] = fetch_yahoo_chart(symbol)
        except Exception:
            prices[symbol] = []
    pairs = [
        ("SPY vs RSP", "RSP", "SPY", True, "concentration"),
        ("QQQ vs QQEW", "QQEW", "QQQ", True, "concentration"),
        ("IWM vs SPY", "IWM", "SPY", True, "breadth"),
        ("XLF vs SPY", "XLF", "SPY", True, "financial_credit"),
        ("SOXX vs QQQ", "SOXX", "QQQ", True, "growth_risk"),
        ("IYT vs SPY", "IYT", "SPY", True, "economic_sensitive"),
    ]
    conclusions = []
    for label, a, b, negative_is_bad, bucket in pairs:
        if not prices.get(a) and label == "QQQ vs QQEW":
            a, b, label = "QQQ", "RSP", "QQQ vs RSP fallback"
            negative_is_bad = False
        rels = {days: relative_return(prices.get(a, []), prices.get(b, []), days) for days in [5, 20, 60]}
        conclusion = classify_relative(label, rels, negative_is_bad=negative_is_bad)
        conclusion["bucket"] = bucket
        conclusions.append(conclusion)
        lines.append(conclusion["summary"])
    aggregate = aggregate_structure_buckets(conclusions)
    level = aggregate["level"]
    summary = aggregate["summary"]
    lines.append("breadth auxiliary metrics unavailable, using ETF breadth proxies instead")
    lines.append(summary)
    return {"level": level, "summary": summary, "lines": lines}


def module_slow_pressure() -> dict:
    lines = []
    conclusions = []
    for name, series, yahoo_symbol, proxy_label in [
        ("2Y Treasury", "DGS2", "ZT=F", "2Y Treasury futures proxy"),
        ("10Y Treasury", "DGS10", "^TNX", "10Y Treasury yield proxy"),
        ("10Y real yield", "DFII10", "TIP", "TIPS ETF real-rate proxy"),
    ]:
        try:
            conclusion = rate_proxy_summary(name, series, yahoo_symbol, proxy_label)
        except Exception as exc:
            conclusion = {"level": "yellow", "summary": f"{name} proxy unavailable after fallback: {exc!r}"}
        conclusions.append(conclusion)
        lines.append(conclusion["summary"])
    oil_stats = fetch_oil_stats()
    oil_conclusion = classify_oil_pressure(oil_stats)
    if oil_conclusion.get("available"):
        lines.append(oil_conclusion["summary"])
        conclusions.append(oil_conclusion)
    else:
        oil_items = []
        for query in ["Brent WTI oil markets", "Iran oil markets"]:
            try:
                oil_items.extend(fetch_google_news(query, limit=2))
            except Exception:
                pass
        if oil_items:
            lines.append("油价：未接入 stock-sdk exact quotes，使用 oil / Iran / Brent / WTI 新闻代理。")
            lines.extend(f"- {item['time']} {item['source']}: {item['title']}" for item in oil_items[:3])
            conclusions.append({"level": "yellow", "summary": "油价 exact unavailable; oil headlines present as proxy", "available": False})
        else:
            lines.append("Brent / WTI unavailable; oil headline fallback unavailable.")
            conclusions.append(oil_conclusion)
    level = max((c["level"] for c in conclusions), key=level_rank)
    return {"level": level, "summary": "；".join(c["summary"] for c in conclusions[:2]), "lines": lines}


def module_events(today: dt.date | None = None) -> dict:
    if today is None:
        today = dt.date.today()
    queries = [
        f"Iran oil {today.isoformat()} markets",
        f"China stocks {today.isoformat()} A-share",
        f"Japan stocks {today.isoformat()}",
        f"Nasdaq {today.isoformat()} stocks",
        f"healthcare stocks {today.isoformat()}",
        f"credit default liquidity {today.isoformat()} markets",
    ]
    items = []
    for query in queries:
        try:
            items.extend(fetch_google_news(query, limit=3))
        except Exception:
            continue
    override = any(item["override"] for item in items)
    level = "red" if override else "yellow" if items else "green"
    lines = [f"{item['time']} | {item['source']} | {item['type']} | override={item['override']} | {item['title']}" for item in items[:10]]
    if not lines:
        lines.append("最近 24 小时事件流 unavailable or no major headlines from Google News RSS.")
    return {"level": level, "summary": "存在 override 事件" if override else "未发现明确 override 事件", "override": override, "lines": lines}


def build_report() -> str:
    modules = {
        "sentiment": module_sentiment(),
        "transmission": module_transmission(),
        "structure": module_structure(),
        "slow_pressure": module_slow_pressure(),
        "events": module_events(),
    }
    action = map_action_light(modules)
    sections = [
        "# 美股见顶 / 崩盘监控报告",
        "",
        "## 1. 总体结论",
        f"- 当前总体灯号：{action['light']}",
        f"- 一句话解释：{action['reason']}",
        "",
        "## 2. 情绪 / 脆弱度层",
        *bullet_lines(modules["sentiment"]["lines"]),
        f"- 本层结论：{light_from_level(modules['sentiment']['level'])}，{modules['sentiment']['summary']}",
        "",
        "## 3. 风险传导层",
        *bullet_lines(modules["transmission"]["lines"]),
        f"- 本层结论：{light_from_level(modules['transmission']['level'])}，{modules['transmission']['summary']}",
        "",
        "## 4. 市场内部结构层",
        *bullet_lines(modules["structure"]["lines"]),
        f"- 本层结论：{light_from_level(modules['structure']['level'])}，{modules['structure']['summary']}",
        "",
        "## 5. 慢变量压力层",
        *bullet_lines(modules["slow_pressure"]["lines"]),
        f"- 本层结论：{light_from_level(modules['slow_pressure']['level'])}，{modules['slow_pressure']['summary']}",
        "",
        "## 6. 事件层",
        *bullet_lines(modules["events"]["lines"]),
        f"- 本层结论：{light_from_level(modules['events']['level'])}，{modules['events']['summary']}",
        "",
        "## 7. 行动映射",
        f"- 当前应该做什么：{action['do']}",
        f"- 当前不该做什么：{action['avoid']}",
        f"- 高 beta：{action['high_beta']}",
        f"- 观望标的：{action['watchlist']}",
        f"- 沉淀层：{action['core_book']}",
    ]
    return "\n".join(sections) + "\n"


def bullet_lines(lines: list[str]) -> list[str]:
    return [line if line.startswith("- ") else f"- {line}" for line in lines]


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a US market top/crash risk monitoring report.")
    parser.add_argument("--json", action="store_true", help="Reserved for future structured output.")
    args = parser.parse_args()
    if args.json:
        print(json.dumps({"report": build_report()}, ensure_ascii=False, indent=2))
    else:
        print(build_report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
