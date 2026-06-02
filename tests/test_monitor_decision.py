import importlib.util
from pathlib import Path
import datetime as dt


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "monitor.py"
SKILL = ROOT / "SKILL.md"


def load_monitor():
    spec = importlib.util.spec_from_file_location("monitor", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_vx_spread_is_primary_fragility_gate():
    monitor = load_monitor()

    result = monitor.classify_vx_spread(-2.3)

    assert result["level"] == "orange"
    assert "极度脆弱" in result["label"]
    assert "禁止主动加高 beta" in result["action_hint"]
    assert result["available"] is True


def test_event_override_can_escalate_to_red_without_score_sum():
    monitor = load_monitor()
    modules = {
        "sentiment": {"level": "green", "summary": "情绪未失真"},
        "transmission": {"level": "green", "summary": "信用稳定"},
        "structure": {"level": "green", "summary": "结构健康"},
        "slow_pressure": {"level": "green", "summary": "慢变量可控"},
        "events": {"level": "red", "summary": "信用事故", "override": True},
    }

    action = monitor.map_action_light(modules)

    assert action["light"] == "红"
    assert "事件层 override" in action["reason"]


def test_two_independent_orange_modules_map_to_orange():
    monitor = load_monitor()
    modules = {
        "sentiment": {"level": "orange", "summary": "期限结构压缩"},
        "transmission": {"level": "green", "summary": "信用稳定"},
        "structure": {"level": "orange", "summary": "领导权狭窄"},
        "slow_pressure": {"level": "green", "summary": "慢变量可控"},
        "events": {"level": "green", "summary": "无重大事件", "override": False},
    }

    action = monitor.map_action_light(modules)

    assert action["light"] == "橙"
    assert "两个独立模块" in action["reason"]


def test_unavailable_advanced_metrics_are_reported_not_blocking():
    monitor = load_monitor()

    line = monitor.format_unavailable("SOFR-OIS", "using credit + rates + banks as proxy")

    assert line == "SOFR-OIS exact source not configured; using credit + rates + banks as proxy"


def test_unavailable_module_does_not_escalate_action_light():
    monitor = load_monitor()
    modules = {
        "sentiment": {"level": "yellow", "summary": "VX unavailable", "available": False},
        "transmission": {"level": "green", "summary": "信用稳定", "available": True},
        "structure": {"level": "green", "summary": "结构健康", "available": True},
        "slow_pressure": {"level": "green", "summary": "慢变量可控", "available": True},
        "events": {"level": "green", "summary": "无重大事件", "override": False, "available": True},
    }

    action = monitor.map_action_light(modules)

    assert action["light"] == "绿"
    assert "置信度" in action["reason"]


def test_vx_unavailable_reports_low_confidence_not_risk_escalation():
    monitor = load_monitor()

    result = monitor.classify_vx_spread(None)

    assert result["level"] == "green"
    assert result["available"] is False
    assert result["confidence"] == "low"


def test_clear_backwardation_is_red_but_mild_backwardation_is_orange():
    monitor = load_monitor()

    assert monitor.classify_vx_spread(0.8)["level"] == "orange"
    assert monitor.classify_vx_spread(1.8)["level"] == "red"


def test_event_override_requires_actionable_systemic_language():
    monitor = load_monitor()

    assert monitor.is_override_event("Analysts debate whether old war fears still matter for stocks") is False
    assert monitor.is_override_event("Major bank default triggers liquidity crisis across markets") is True


def test_latest_window_stats_can_use_longer_percentile_window():
    monitor = load_monitor()
    rows = [(dt.date(2026, 1, 1) + dt.timedelta(days=i), float(i)) for i in range(180)]

    stats = monitor.latest_window_stats(rows, percentile_window=120)

    assert stats["percentile_window"] == 120
    assert stats["change_20d"] == 20.0


def test_rate_vol_proxy_uses_rolling_daily_change_volatility_not_single_move():
    monitor = load_monitor()
    calm_2y = {"available": True, "rows": [(dt.date(2026, 1, 1) + dt.timedelta(days=i), 4.0 + i * 0.01) for i in range(80)]}
    volatile_10y_values = [4.0 + i * 0.01 for i in range(60)] + [4.6, 4.2, 4.7, 4.1, 4.8, 4.0, 4.9, 3.9, 5.0, 3.8]
    volatile_10y = {
        "available": True,
        "rows": [(dt.date(2026, 1, 1) + dt.timedelta(days=i), value) for i, value in enumerate(volatile_10y_values)],
    }

    result = monitor.classify_rate_vol_proxy(calm_2y, volatile_10y)

    assert result["level"] == "orange"
    assert "20d vol" in result["summary"]


def test_price_proxy_direction_inverts_treasury_futures_pressure():
    monitor = load_monitor()

    result = monitor.classify_rate_pressure_from_stats(
        "2Y Treasury",
        {"available": True, "latest": 103.0, "change_5d": 0.2, "change_20d": -0.8, "is_proxy": True},
        "ZT=F",
        "2Y Treasury futures proxy",
    )

    assert result["level"] == "yellow"
    assert result["pressure_20d"] == 0.8


def test_structure_bucket_aggregation_does_not_double_count_related_breadth_signals():
    monitor = load_monitor()
    conclusions = [
        {"level": "yellow", "bucket": "breadth", "summary": "SPY vs RSP weak"},
        {"level": "yellow", "bucket": "breadth", "summary": "IWM weak"},
        {"level": "yellow", "bucket": "breadth", "summary": "IYT weak"},
    ]

    result = monitor.aggregate_structure_buckets(conclusions)

    assert result["level"] == "yellow"
    assert "局部" in result["summary"]


def test_structure_bucket_aggregation_requires_distinct_bucket_confirmation_for_orange():
    monitor = load_monitor()
    conclusions = [
        {"level": "yellow", "bucket": "breadth", "summary": "小盘掉队"},
        {"level": "yellow", "bucket": "financial_credit", "summary": "银行掉队"},
        {"level": "yellow", "bucket": "growth_risk", "summary": "半导体掉队"},
    ]

    result = monitor.aggregate_structure_buckets(conclusions)

    assert result["level"] == "orange"
    assert "不同结构风险桶" in result["summary"]


def test_oil_pressure_classifier_uses_price_change_when_available():
    monitor = load_monitor()

    result = monitor.classify_oil_pressure({"available": True, "latest": 92.0, "change_5d": 5.5, "change_20d": 11.0})

    assert result["level"] == "orange"
    assert "20d +11.00" in result["summary"]


def test_slow_pressure_module_uses_oil_price_node_when_available(monkeypatch):
    monitor = load_monitor()

    monkeypatch.setattr(monitor, "rate_proxy_summary", lambda name, series, yahoo_symbol, proxy_label: {"level": "green", "summary": name})
    monkeypatch.setattr(monitor, "fetch_google_news", lambda query, limit=2: [])
    monkeypatch.setattr(
        monitor,
        "fetch_yahoo_stats",
        lambda symbol: {"available": True, "latest": 91.0, "change_5d": 4.0, "change_20d": 12.0, "symbol": symbol}
        if symbol == "BZ=F"
        else {"available": False},
    )

    result = monitor.module_slow_pressure()

    assert result["level"] == "orange"
    assert any("油价压力" in line for line in result["lines"])


def test_skill_is_rooted_at_workspace_and_not_codex_specific():
    text = SKILL.read_text()

    assert "python3 scripts/monitor.py" in text
    assert "Use when Codex needs" not in text
    assert 'name: us-market-risk-monitor' in text


def test_vx_contracts_are_resolved_inside_this_skill():
    monitor = load_monitor()

    front, second = monitor.vx_contract_symbols(monitor.dt.date(2026, 6, 2))

    assert front["symbol"] == "CBOE:VXM2026"
    assert front["expiry"].isoformat() == "2026-06-17"
    assert second["symbol"] == "CBOE:VXN2026"
    assert second["expiry"].isoformat() == "2026-07-15"


def test_tradingview_vx_scanner_response_builds_vx_spread():
    monitor = load_monitor()
    scanner_payload = {
        "data": [
            {"s": "CBOE:VXM2026", "d": [17.9, 0.0, 0.0, "VX Jun 2026"]},
            {"s": "CBOE:VXN2026", "d": [20.3, 0.0, 0.0, "VX Jul 2026"]},
        ]
    }

    result = monitor.parse_vx_scanner_response(
        scanner_payload,
        {"symbol": "CBOE:VXM2026", "expiry": monitor.dt.date(2026, 6, 17)},
        {"symbol": "CBOE:VXN2026", "expiry": monitor.dt.date(2026, 7, 15)},
    )

    assert result["available"] is True
    assert result["vx1_price"] == 17.9
    assert result["vx2_price"] == 20.3
    assert round(result["spread"], 2) == -2.4


def test_monitor_does_not_depend_on_external_vix_skill():
    source = SCRIPT.read_text()
    skill_text = SKILL.read_text()

    assert "vix-term-structure" not in source
    assert "VIX_TERM_SCRIPT" not in source
    assert "vix-term-structure" not in skill_text


def test_fred_csv_url_limits_observation_window():
    monitor = load_monitor()

    url = monitor.fred_csv_url("DGS10", today=monitor.dt.date(2026, 6, 2), lookback_days=180)

    assert url.startswith("https://fred.stlouisfed.org/graph/fredgraph.csv?")
    assert "id=DGS10" in url
    assert "cosd=2025-12-04" in url
    assert "coed=2026-06-02" in url


def test_fred_api_url_uses_official_api_key_and_date_window():
    monitor = load_monitor()

    url = monitor.fred_api_url("BAMLH0A0HYM2", "test-key", today=monitor.dt.date(2026, 6, 2), lookback_days=180)

    assert url.startswith("https://api.stlouisfed.org/fred/series/observations?")
    assert "series_id=BAMLH0A0HYM2" in url
    assert "api_key=test-key" in url
    assert "file_type=json" in url
    assert "observation_start=2025-12-04" in url
    assert "observation_end=2026-06-02" in url


def test_fred_exact_requires_api_key_instead_of_graph_csv_timeout(monkeypatch):
    monitor = load_monitor()
    monkeypatch.delenv("FRED_API_KEY", raising=False)

    try:
        monitor.fetch_fred_series("DGS10")
    except monitor.FredApiKeyMissing as exc:
        assert "FRED_API_KEY" in str(exc)
    else:
        raise AssertionError("fetch_fred_series should require FRED_API_KEY for exact FRED data")
