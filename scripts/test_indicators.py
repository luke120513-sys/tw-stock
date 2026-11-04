#!/usr/bin/env python3
"""指標與資料合併的單元測試。不連外網。"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_data
import indicators as ind


def original_rsi(closes, n=14):
    gain = loss = 0.0
    for i in range(1, n + 1):
        diff = closes[i] - closes[i - 1]
        gain += max(diff, 0.0)
        loss += max(-diff, 0.0)
    gain /= n
    loss /= n
    for i in range(n + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gain = (gain * (n - 1) + max(diff, 0.0)) / n
        loss = (loss * (n - 1) + max(-diff, 0.0)) / n
    return 100.0 if loss == 0 else 100.0 - 100.0 / (1.0 + gain / loss)


def bars(closes, volume=1000, spread=1.0):
    start = date(2026, 1, 5)
    rows = []
    for index, close in enumerate(closes):
        rows.append(
            {
                "date": (start + timedelta(days=index)).isoformat(),
                "open": close,
                "high": close + spread,
                "low": close - spread,
                "close": close,
                "volume": volume,
            }
        )
    return rows


def test_parse_and_dates():
    assert ind.parse_number("1,234.50") == 1234.5
    assert ind.parse_number("--") is None
    assert ind.parse_number("") is None
    assert ind.parse_number("X") is None
    assert ind.roc_to_iso("115/11/04") == "2026-11-04"
    assert ind.roc_to_iso("1151104") == "2026-11-04"
    assert ind.roc_to_iso("115/13/40") is None


def test_rsi_matches_original_script():
    closes = [100 + (i % 7) - (i % 3) * 2 + i * 0.1 for i in range(40)]
    closes += [closes[-1] - i * 1.7 for i in range(1, 25)]
    assert abs(ind.rsi_last(closes) - original_rsi(closes)) < 1e-9
    series = ind.rsi_series(closes)
    assert series[-1] == ind.rsi_last(closes)
    assert series[14] is not None
    assert series[13] is None


def test_bottom_conditions_and_no_fake_scores():
    rising = ind.enrich_bars(bars([100 + i for i in range(80)], volume=2000))
    quiet = ind.bottom_signals(rising, [100, 200, 300, 400, 500])
    assert quiet["score"] == 0
    assert "RSI超賣" not in quiet["triggered"]

    crushed = []
    for i in range(70):
        price = 100 - i
        crushed.append(
            {
                "date": (date(2026, 1, 5) + timedelta(days=i)).isoformat(),
                "open": price + 1,
                "high": price + 1,
                "low": price - 1,
                "close": price,
                "volume": 5000 if i < 65 else 100,
            }
        )
    # 最後一天量縮且收高、低點不破。
    crushed[-1]["close"] = crushed[-2]["close"] + 0.5
    crushed[-1]["low"] = crushed[-2]["low"]
    crushed[-1]["high"] = crushed[-1]["close"] + 0.5
    enriched = ind.enrich_bars(crushed)
    result = ind.bottom_signals(enriched, [-5000, -4000, -3000, -2000, 1000])
    assert "負乖離過大" in result["triggered"]
    assert "外資由賣轉買" in result["triggered"]
    assert result["score"] == len(result["triggered"])
    assert result["max_score"] == 6
    assert all(item["explain"] for item in result["conditions"])

    empty = ind.bottom_signals([], [])
    assert empty["score"] == 0
    assert empty["conditions"][0]["hit"] is False
    assert empty["conditions"][0]["available"] is False
    assert empty["rsi14"] is None


def test_bollinger_and_bias_edges():
    flat = ind.enrich_bars(bars([10] * 20))
    assert flat[-1]["boll_lower"] == 10
    assert ind.bottom_signals(flat, [1, 1])["conditions"][2]["hit"] is False
    dipped = bars([10] * 19 + [1])
    enriched = ind.enrich_bars(dipped)
    assert enriched[-1]["close"] < enriched[-1]["boll_lower"]
    assert ind.bottom_signals(enriched, [1, 1])["conditions"][2]["hit"] is True

    deep = ind.enrich_bars(bars([100] * 59 + [80]))
    bias = ind.bottom_signals(deep, [1, 1])
    assert bias["conditions"][3]["hit"] is True
    mild = ind.enrich_bars(bars([100] * 59 + [95]))
    assert ind.bottom_signals(mild, [1, 1])["conditions"][3]["hit"] is False


def test_kd_golden_cross_only_when_low_and_crossing():
    rows = bars([50] * 30)
    for row in rows:
        row["high"] = 60
        row["low"] = 40
        row["close"] = 50
    rows[-2]["close"] = 41
    rows[-2]["low"] = 40
    rows[-1]["close"] = 42
    rows[-1]["low"] = 40
    enriched = ind.enrich_bars(rows)
    # 構造一根明確的低檔上穿：直接覆寫最後兩根 KD 再交給條件函式。
    enriched[-2]["k"], enriched[-2]["d"] = 10.0, 12.0
    enriched[-1]["k"], enriched[-1]["d"] = 15.0, 13.0
    hit = ind.bottom_signals(enriched, [1, 1])["conditions"][1]
    assert hit["hit"] is True
    enriched[-1]["k"] = 25
    assert ind.bottom_signals(enriched, [1, 1])["conditions"][1]["hit"] is False
    enriched[-1]["k"], enriched[-1]["d"] = 15.0, 18.0
    assert ind.bottom_signals(enriched, [1, 1])["conditions"][1]["hit"] is False


def test_volume_shrink_needs_price_to_stabilize():
    rows = bars([20] * 20, volume=1000)
    for row in rows[-5:]:
        row["volume"] = 100
    rows[-1]["close"] = 19
    rows[-1]["low"] = 18
    rows[-2]["low"] = 19
    enriched = ind.enrich_bars(rows)
    assert ind.bottom_signals(enriched, [1, 1])["conditions"][4]["hit"] is False
    enriched[-1]["close"] = 21
    enriched[-1]["low"] = 19
    assert ind.bottom_signals(enriched, [1, 1])["conditions"][4]["hit"] is True


def test_foreign_flip_window():
    rows = ind.enrich_bars(bars([30] * 20))
    assert ind.bottom_signals(rows, [-1, -2, 3])["conditions"][5]["hit"] is True
    assert ind.bottom_signals(rows, [-1, -2, -3])["conditions"][5]["hit"] is False
    assert ind.bottom_signals(rows, [1, 2, 3])["conditions"][5]["hit"] is False
    missing = ind.bottom_signals(rows, [None])["conditions"][5]
    assert missing["hit"] is False
    assert missing["available"] is False


def test_alerts_thresholds_are_not_invented():
    rows = bars([100] * 60, volume=1000)
    rows[-1]["close"] = 104
    rows[-1]["low"] = 100
    quiet = ind.build_alerts(rows, 2.5)
    assert quiet == []
    jumped = ind.build_alerts(rows, 3)
    assert jumped[0]["id"] == "change_up"
    dropped = ind.build_alerts(rows, -3.01)
    assert dropped[0]["id"] == "change_down"
    rows[-1]["volume"] = 1500
    volume_hit = [item for item in ind.build_alerts(rows, 0) if item["id"] == "volume"]
    assert volume_hit
    rows[-1]["volume"] = 1499
    assert [item for item in ind.build_alerts(rows, 0) if item["id"] == "volume"] == []
    rows[-1]["low"] = 90
    lows = [item for item in ind.build_alerts(rows, 0) if item["id"] == "low60"]
    assert lows
    short = ind.build_alerts(rows[:10], 10)
    assert all(item["id"] != "low60" for item in short)
    assert all(item["id"] != "volume" for item in short)


def test_month_change_and_history_choice():
    rows = bars([100, 110, 120])
    rows[0]["date"] = "2026-10-01"
    rows[1]["date"] = "2026-10-20"
    rows[2]["date"] = "2026-11-04"
    pct, base = ind.month_change(rows)
    assert base == "2026-10-01"
    assert abs(pct - 20) < 1e-9
    assert ind.month_change(bars([10] * 5)) == (None, None)

    start = date(2026, 1, 1)
    twse = [{"date": (start + timedelta(days=i)).isoformat(), "close": 10 + i} for i in range(70)]
    yahoo = [{"date": (start + timedelta(days=i)).isoformat(), "close": 99} for i in range(200)]
    chosen, source = ind.choose_price_history(twse, yahoo)
    assert source == "TWSE"
    assert chosen[-1]["close"] == 79
    gapped = twse[:30] + [{"date": "2026-04-15", "close": 1}]
    chosen, source = ind.choose_price_history(gapped, yahoo)
    assert source == "Yahoo"
    assert chosen[0]["close"] == 99
    assert ind.choose_price_history([], []) == ([], "")


def test_merge_keeps_last_good_without_inventing():
    previous = {
        "market": {
            "date": "2026-11-04",
            "close": 42948.91,
            "history": [{"date": "2026-11-04", "close": 42948.91}],
            "institutions": {"groups": [{"name": "投信", "net": 10}], "date": "2026-11-04"},
        },
        "stocks": [{"code": "2330", "name": "台積電", "close": 2260, "series": [{"date": "2026-11-04", "close": 2260}]}],
    }
    current = {
        "notes": [],
        "watchlist": ["2330", "2317"],
        "market": {"history": [{"date": "2026-11-04", "close": 42948.91}], "date": "2026-11-04"},
        "stocks": [
            {"code": "2330", "name": "台積電", "series": []},
            {"code": "2317", "name": "鴻海", "series": [{"date": "2026-11-04", "close": 240}]},
        ],
    }
    merged = fetch_data.merge_previous(previous, current)
    assert merged["stocks"][0]["close"] == 2260
    assert merged["stocks"][0]["reused"] is True
    assert merged["stocks"][1]["series"][-1]["close"] == 240
    assert any("沿用" in note for note in merged["notes"])
    assert merged["market"]["institutions"]["groups"][0]["name"] == "投信"

    blank = fetch_data.merge_previous(previous, {"notes": [], "watchlist": ["2330"], "market": {}, "stocks": []})
    assert blank["market"]["close"] == 42948.91
    assert blank["stocks"][0]["code"] == "2330"
    fresh = fetch_data.merge_previous(None, {"notes": [], "stocks": [], "market": None})
    assert fresh["stocks"] == []


def test_security_filter():
    import build_site

    assert build_site.include_security("2603", "長榮")
    assert build_site.include_security("0050", "元大台灣50")
    assert build_site.include_security("00878", "國泰永續高股息")
    assert build_site.include_security("00631L", "元大台灣50正2") is False
    assert build_site.include_security("030001", "某某購") is False


def test_binary_dip_signal():
    def cond(cid, hit):
        return {"id": cid, "name": cid, "hit": hit}

    low = ind.public_bottom({"score": 2, "conditions": [cond("rsi", True), cond("bias", True), cond("kd", False)]})
    assert low["label"] == "不行"
    assert low["can_bottom"] is False
    assert low["cutoff"] == 3
    assert "有一點" not in low["label"] and "很強" not in low["label"]
    assert "最近跌太快" in low["reason"] and "所以不行" in low["reason"]

    high = ind.public_bottom(
        {
            "score": 3,
            "conditions": [cond("rsi", True), cond("bias", True), cond("foreign", True)],
        }
    )
    assert high["label"] == "可以抄底"
    assert high["can_bottom"] is True
    assert high["label"] in {"可以抄底", "不行"}
    assert "大戶（外資）開始買進" in high["reason"]

    none = ind.public_bottom({"score": 0, "conditions": [cond("rsi", False)]})
    assert none["label"] == "不行"
    assert "沒有出現" in none["reason"]
    assert ind.DIP_RULE.startswith("六個條件裡")


def main():
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok {test.__name__}")
    print(f"{len(tests)} tests passed")


if __name__ == "__main__":
    main()
