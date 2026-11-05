#!/usr/bin/env python3
"""抓取證交所公開資料，寫入 site/data/latest.json。

只寫實際取得的數字。休市、尚未公布或連線失敗時，沿用檔案裡上一筆成功的資料。
用法：python scripts/fetch_data.py
"""

from __future__ import annotations

import hashlib
import http.client
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

import indicators as ind

ROOT = Path(__file__).resolve().parents[1]
WATCHLIST = ROOT / "watchlist.json"
OUT = ROOT / "site" / "data" / "latest.json"
CACHE = ROOT / ".cache" / "http"
TZ = ZoneInfo("Asia/Taipei")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
SLEEP = 0.7
PRICE_MONTHS = 8
INDEX_MONTHS = 4
FOREIGN_DAYS = 20

SOURCES = [
    {
        "id": "fmtqik",
        "name": "加權指數與成交金額（FMTQIK）",
        "url": "https://www.twse.com.tw/zh/trading/historical/fmtqik.html",
    },
    {
        "id": "bfi82u",
        "name": "三大法人買賣金額（BFI82U）",
        "url": "https://www.twse.com.tw/zh/trading/foreign/bfi82u.html",
    },
    {
        "id": "stock_day",
        "name": "個股日成交資訊（STOCK_DAY）",
        "url": "https://www.twse.com.tw/zh/trading/historical/stock-day.html",
    },
    {
        "id": "t86",
        "name": "三大法人買賣超日報（T86）",
        "url": "https://www.twse.com.tw/zh/trading/foreign/t86.html",
    },
    {
        "id": "bwibbu",
        "name": "本益比、殖利率及股價淨值比（BWIBBU）",
        "url": "https://www.twse.com.tw/zh/trading/historical/bwibbu.html",
    },
    {
        "id": "openapi",
        "name": "證交所 OpenAPI",
        "url": "https://openapi.twse.com.tw/",
    },
    {
        "id": "yahoo",
        "name": "Yahoo Finance 圖表",
        "url": "https://finance.yahoo.com/",
    },
]


def main() -> int:
    offline = "--offline" in sys.argv
    now = datetime.now(TZ).replace(microsecond=0)
    watch = load_watchlist()
    previous = load_previous()
    notes: list[str] = []
    used: set[str] = set()

    print(f"開始抓資料 {now.isoformat()}，觀察清單 {len(watch)} 檔")
    index_rows, index_source = fetch_index(now.date(), offline, used, notes)
    trade_date = index_rows[-1]["date"] if index_rows else None

    institutions = None
    if trade_date:
        institutions = fetch_institutions(trade_date, offline, used, notes)
        if institutions is None:
            for row in reversed(index_rows[:-1]):
                institutions = fetch_institutions(row["date"], offline, used, notes)
                if institutions:
                    notes.append(f"三大法人最新公布日是 {institutions['date']}，與指數收盤日不同。")
                    break

    valuations = fetch_valuations(offline, used, notes)
    foreign_by_date: dict[str, dict] = {}
    stocks = []
    for item in watch:
        code = item["code"]
        print(f"個股 {code} {item['name']}")
        rows, source = fetch_stock_history(code, now.date(), offline, used, notes)
        official_name = item["name"]
        if source == "TWSE":
            used.add("stock_day")
        elif source.startswith("Yahoo"):
            used.add("yahoo")
        stocks.append(
            {
                "code": code,
                "config_name": item["name"],
                "name": official_name,
                "rows": rows,
                "source": source,
            }
        )

    if not trade_date:
        for stock in stocks:
            if stock["rows"]:
                trade_date = stock["rows"][-1]["date"]
                break

    foreign_dates = trading_dates(stocks, trade_date)[-FOREIGN_DAYS:]
    missed_foreign = []
    for day in foreign_dates:
        print(f"外資買賣超 {day}")
        payload = fetch_t86(day, offline, used)
        if payload:
            foreign_by_date[day] = payload
            used.add("t86")
        else:
            missed_foreign.append(day)
    if missed_foreign:
        if len(missed_foreign) == len(foreign_dates):
            notes.append("這次沒有取得 T86 外資買賣超。")
        else:
            notes.append(f"有 {len(missed_foreign)} 個交易日沒有取得 T86 外資買賣超。")

    built = []
    for stock in stocks:
        built.append(build_stock(stock, foreign_by_date, valuations))

    market = build_market(index_rows, index_source, institutions)
    payload = {
        "schema": 1,
        "generated_at": now.isoformat(timespec="seconds"),
        "timezone": "Asia/Taipei",
        "trade_date": market.get("date") if market else trade_date,
        "stale": False,
        "notes": _unique(notes),
        "rules": ind.rules_payload(),
        "sources": [source for source in SOURCES if source["id"] in used or source["id"] in {"fmtqik", "bfi82u", "stock_day", "t86", "bwibbu"}],
        "sources_used": [source["id"] for source in SOURCES if source["id"] in used],
        "watchlist": [item["code"] for item in watch],
        "market": market,
        "stocks": built,
    }
    payload = merge_previous(previous, payload)
    if not payload.get("market") and not payload.get("stocks"):
        print("沒有任何可用資料，也沒有上次成功的檔案。", file=sys.stderr)
        return 1
    payload["notes"] = _unique(payload.get("notes") or [])
    payload["stale"] = any("沿用" in note for note in payload["notes"])
    has_market = bool((payload.get("market") or {}).get("history"))
    has_stock = any(stock.get("series") for stock in payload.get("stocks") or [])
    if not has_market and not has_stock:
        print("沒有任何可用資料，也沒有上次成功的檔案。", file=sys.stderr)
        return 1
    write_json(payload)
    print_summary(payload)
    return 0


def load_watchlist() -> list[dict]:
    data = json.loads(WATCHLIST.read_text(encoding="utf-8"))
    seen = set()
    stocks = []
    for item in data.get("stocks") or []:
        code = str(item.get("code", "")).strip().upper()
        if not code or code in seen:
            continue
        if not all(ch.isalnum() for ch in code) or not 4 <= len(code) <= 6:
            raise SystemExit(f"watchlist.json 的代號不正確：{code}")
        seen.add(code)
        name = str(item.get("name") or code).strip()
        stocks.append({"code": code, "name": name})
    if not stocks:
        raise SystemExit("watchlist.json 裡沒有股票")
    return stocks


def load_previous() -> dict | None:
    if not OUT.exists():
        return None
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print("上次的 latest.json 無法解析，這次不沿用。")
        return None


def merge_previous(previous: dict | None, current: dict) -> dict:
    if not previous:
        return current
    notes = list(current.get("notes") or [])
    prev_market = previous.get("market") or {}
    market = current.get("market") or {}
    if not market.get("history"):
        if prev_market.get("history"):
            current["market"] = prev_market
            notes.append("這次沒有取得加權指數，沿用上次成功的大盤資料。")
        market = current.get("market") or {}
    elif not (market.get("institutions") or {}).get("groups"):
        old_groups = (prev_market.get("institutions") or {}).get("groups")
        if old_groups:
            market["institutions"] = prev_market["institutions"]
            notes.append("這次沒有取得三大法人買賣超，沿用上次的法人資料。")

    prev_stocks = {item["code"]: item for item in previous.get("stocks") or [] if item.get("code")}
    merged = []
    for stock in current.get("stocks") or []:
        if stock.get("series"):
            merged.append(stock)
            continue
        old = prev_stocks.get(stock["code"])
        if old and old.get("series"):
            reused = dict(old)
            reused["reused"] = True
            merged.append(reused)
            notes.append(f"{stock['code']} {reused.get('name', '')} 這次沒有取得日 K，沿用上次資料。".strip())
        else:
            merged.append(stock)
            notes.append(f"{stock['code']} 這次沒有取得日 K。")
    have = {item["code"] for item in merged}
    order = current.get("watchlist") or []
    for code in order:
        if code in have:
            continue
        old = prev_stocks.get(code)
        if old and old.get("series"):
            reused = dict(old)
            reused["reused"] = True
            merged.append(reused)
            notes.append(f"{code} {reused.get('name', '')} 這次沒有更新，沿用上次資料。".strip())
    rank = {code: index for index, code in enumerate(order)}
    merged.sort(key=lambda item: rank.get(item["code"], 999))
    current["stocks"] = merged
    current["notes"] = notes
    return current


def build_market(rows: list[dict], source: str, institutions: dict | None) -> dict | None:
    if not rows:
        return None
    latest = rows[-1]
    history = []
    for row in rows:
        history.append(
            {
                "date": row["date"],
                "close": ind._round(row.get("close"), 2),
                "change": ind._round(row.get("change"), 2),
                "change_pct": ind._round(row.get("change_pct"), 2),
                "turnover": row.get("turnover"),
            }
        )
    source_name = {
        "TWSE": "臺灣證券交易所 FMTQIK",
        "Yahoo": "Yahoo Finance ^TWII",
    }.get(source, source)
    return {
        "date": latest["date"],
        "close": ind._round(latest.get("close"), 2),
        "change": ind._round(latest.get("change"), 2),
        "change_pct": ind._round(latest.get("change_pct"), 2),
        "turnover": latest.get("turnover"),
        "source": source_name,
        "history": history,
        "institutions": institutions,
    }


def build_stock(stock: dict, foreign_by_date: dict, valuations: dict) -> dict:
    code = stock["code"]
    rows = ind.enrich_bars(stock["rows"])
    foreign_series = []
    for day in sorted(foreign_by_date):
        found = foreign_by_date[day].get(code)
        if not found:
            continue
        shares = found["net_shares"]
        foreign_series.append(
            {
                "date": day,
                "net_shares": shares,
                "net_lots": round(shares / 1000.0, 3),
            }
        )
        if found.get("name"):
            stock["name"] = found["name"]
    signal_nets = [item["net_shares"] for item in foreign_series[-ind.FOREIGN_SIGNAL_DAYS :]]
    bottom = ind.bottom_signals(rows, signal_nets) if rows else ind.bottom_signals([], signal_nets)
    change, change_pct = ind.price_change(rows) if rows else (None, None)
    month_pct, month_date = ind.month_change(rows) if rows else (None, None)
    alerts = ind.build_alerts(rows, change_pct) if rows else []
    valuation = valuations.get(code)
    note = ind.history_note(rows)
    volume = rows[-1].get("volume") if rows else None
    latest = rows[-1] if rows else {}
    source_label = {
        "TWSE": "臺灣證券交易所 STOCK_DAY",
        "Yahoo": f"Yahoo Finance {code}.TW",
    }.get(stock["source"], stock["source"] or None)
    return {
        "code": code,
        "name": stock["name"] or code,
        "date": latest.get("date"),
        "close": ind._round(latest.get("close"), 2),
        "change": ind._round(change, 2),
        "change_pct": ind._round(change_pct, 2),
        "volume_shares": int(round(volume)) if volume is not None else None,
        "volume_lots": round(volume / 1000.0, 3) if volume is not None else None,
        "month_change_pct": ind._round(month_pct, 2),
        "month_base_date": month_date,
        "alerts": alerts,
        "note": note,
        "valuation": valuation,
        "bottom": bottom,
        "foreign": foreign_series,
        "series": [ind.round_bar(row) for row in rows],
        "price_source": source_label,
        "bars": len(rows),
    }


def fetch_index(today: date, offline: bool, used: set[str], notes: list[str]) -> tuple[list[dict], str]:
    rows: list[dict] = []
    for year, month in month_range(today, INDEX_MONTHS):
        print(f"加權指數 {year}-{month:02d}")
        part = fetch_fmtqik(year, month, offline)
        rows.extend(part)
    rows = with_index_change(ind._sorted_unique(rows))
    if len(rows) >= 20 and not ind.has_large_gap(rows):
        used.add("fmtqik")
        return rows, "TWSE"
    print("FMTQIK 不足，改試 OpenAPI 與 Yahoo")
    open_rows = fetch_openapi_fmtqik(offline)
    if open_rows:
        used.add("openapi")
        rows = with_index_change(ind._sorted_unique(rows + open_rows))
    yahoo = fetch_yahoo_index(offline)
    chosen, source = ind.choose_price_history(rows, yahoo, min_bars=20)
    chosen = with_index_change(chosen)
    if source == "Yahoo":
        used.add("yahoo")
        notes.append("加權指數走勢這次改用 Yahoo Finance ^TWII。")
        official = rows[-1] if rows else None
        if official and chosen:
            # 收盤數字以證交所有公布的那一天為準，走勢其餘日子沿用 Yahoo。
            by_date = {row["date"]: row for row in chosen}
            by_date[official["date"]] = {**by_date.get(official["date"], {}), **official}
            chosen = with_index_change(ind._sorted_unique(list(by_date.values())))
    elif source == "TWSE":
        used.add("fmtqik")
    if not chosen:
        notes.append("這次沒有取得加權指數。")
    return chosen, source


def fetch_fmtqik(year: int, month: int, offline: bool) -> list[dict]:
    url = f"https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date={year}{month:02d}01&response=json"
    payload = get_json(url, offline)
    if not payload or payload.get("stat") != "OK":
        return []
    rows = []
    for raw in payload.get("data") or []:
        if not raw or "/" not in str(raw[0]):
            continue
        day = ind.roc_to_iso(raw[0])
        close = ind.parse_number(raw[4] if len(raw) > 4 else None)
        if not day or close is None:
            continue
        rows.append(
            {
                "date": day,
                "close": close,
                "change": ind.parse_number(raw[5] if len(raw) > 5 else None),
                "turnover": _int_or_none(ind.parse_number(raw[2] if len(raw) > 2 else None)),
                "volume": _int_or_none(ind.parse_number(raw[1] if len(raw) > 1 else None)),
            }
        )
    return rows


def fetch_openapi_fmtqik(offline: bool) -> list[dict]:
    payload = get_json("https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK", offline, pause=0.3)
    if not isinstance(payload, list):
        return []
    rows = []
    for raw in payload:
        day = ind.roc_to_iso(raw.get("Date"))
        close = ind.parse_number(raw.get("TAIEX"))
        if not day or close is None:
            continue
        rows.append(
            {
                "date": day,
                "close": close,
                "change": ind.parse_number(raw.get("Change")),
                "turnover": _int_or_none(ind.parse_number(raw.get("TradeValue"))),
                "volume": _int_or_none(ind.parse_number(raw.get("TradeVolume"))),
            }
        )
    return rows


def fetch_yahoo_index(offline: bool) -> list[dict]:
    bars = fetch_yahoo("^TWII", "1y", offline)
    for bar in bars:
        bar["turnover"] = None
    return bars


def with_index_change(rows: list[dict]) -> list[dict]:
    previous = None
    output = []
    for row in rows:
        item = dict(row)
        close = item.get("close")
        change = item.get("change")
        if change is None and previous is not None and close is not None:
            change = close - previous
            item["change"] = change
        if change is not None and close is not None and close != change:
            base = close - change
            item["change_pct"] = (change / base * 100.0) if base else None
        else:
            item["change_pct"] = None
        if close is not None:
            previous = close
        output.append(item)
    return output


def fetch_institutions(day: str, offline: bool, used: set[str], notes: list[str]) -> dict | None:
    compact = ind.iso_compact(day)
    url = f"https://www.twse.com.tw/rwd/zh/fund/BFI82U?response=json&dayDate={compact}"
    payload = get_json(url, offline)
    if not payload or payload.get("stat") != "OK" or not payload.get("data"):
        return None
    parsed = []
    for raw in payload["data"]:
        if len(raw) < 4:
            continue
        parsed.append(
            {
                "name": str(raw[0]).strip(),
                "buy": _int_or_none(ind.parse_number(raw[1])),
                "sell": _int_or_none(ind.parse_number(raw[2])),
                "net": _int_or_none(ind.parse_number(raw[3])),
            }
        )
    if not parsed:
        return None
    used.add("bfi82u")
    by_name = {row["name"]: row for row in parsed}

    def total(names: list[str], label: str) -> dict | None:
        found = [by_name[name] for name in names if name in by_name and by_name[name]["net"] is not None]
        if not found:
            return None
        return {
            "name": label,
            "buy": _sum(row["buy"] for row in found),
            "sell": _sum(row["sell"] for row in found),
            "net": _sum(row["net"] for row in found),
        }

    groups = [
        total(["外資及陸資(不含外資自營商)", "外資自營商"], "外資及陸資"),
        total(["投信"], "投信"),
        total(["自營商(自行買賣)", "自營商(避險)"], "自營商"),
    ]
    groups = [group for group in groups if group]
    official_total = by_name.get("合計")
    return {
        "date": ind.roc_to_iso(payload.get("date")) or day,
        "title": payload.get("title"),
        "source": "臺灣證券交易所 BFI82U",
        "groups": groups,
        "rows": parsed,
        "total_net": official_total["net"] if official_total else _sum(group["net"] for group in groups),
    }


def fetch_stock_history(code: str, today: date, offline: bool, used: set[str], notes: list[str]) -> tuple[list[dict], str]:
    rows = []
    failed = False
    for year, month in month_range(today, PRICE_MONTHS):
        part = fetch_stock_month(code, year, month, offline)
        if part is None:
            failed = True
            continue
        rows.extend(part)
    rows = ind._sorted_unique(rows)
    ok = len(rows) >= 60 and not ind.has_large_gap(rows)
    if ok:
        return rows, "TWSE"
    yahoo = fetch_yahoo(f"{code}.TW", "1y", offline)
    chosen, source = ind.choose_price_history(rows, yahoo, min_bars=60)
    if source == "Yahoo":
        notes.append(f"{code} 的日 K 改用 Yahoo Finance（證交所月份資料不足或中間有缺口）。")
    elif failed and source == "TWSE":
        notes.append(f"{code} 有部分月份沒有從證交所取得。")
    if not chosen:
        notes.append(f"{code} 沒有取得日 K。")
    return chosen, source


def fetch_stock_month(code: str, year: int, month: int, offline: bool) -> list[dict] | None:
    url = (
        "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"
        f"?date={year}{month:02d}01&stockNo={code}&response=json"
    )
    payload = get_json(url, offline)
    if payload is None:
        return None
    if payload.get("stat") != "OK":
        return []
    rows = []
    for raw in payload.get("data") or []:
        if len(raw) < 7:
            continue
        day = ind.roc_to_iso(raw[0])
        close = ind.parse_number(raw[6])
        if not day or close is None:
            continue
        rows.append(
            {
                "date": day,
                "volume": ind.parse_number(raw[1]),
                "open": ind.parse_number(raw[3]),
                "high": ind.parse_number(raw[4]),
                "low": ind.parse_number(raw[5]),
                "close": close,
            }
        )
    return rows


def fetch_t86(day: str, offline: bool, used: set[str]) -> dict:
    url = (
        "https://www.twse.com.tw/rwd/zh/fund/T86"
        f"?date={ind.iso_compact(day)}&selectType=ALLBUT0999&response=json"
    )
    payload = get_json(url, offline)
    if not payload or payload.get("stat") != "OK":
        return {}
    fields = payload.get("fields") or []
    index = next(
        (i for i, name in enumerate(fields) if str(name).startswith("外陸資買賣超股數") and "不含" in str(name)),
        None,
    )
    if index is None:
        return {}
    found = {}
    for raw in payload.get("data") or []:
        if len(raw) <= index:
            continue
        code = str(raw[0]).strip()
        shares = ind.parse_number(raw[index])
        if not code or shares is None:
            continue
        found[code] = {"name": str(raw[1]).strip(), "net_shares": int(round(shares))}
    if found:
        used.add("t86")
    return found


def fetch_valuations(offline: bool, used: set[str], notes: list[str]) -> dict:
    url = "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_ALL?response=json"
    payload = get_json(url, offline)
    parsed = _parse_bwibbu(payload) if isinstance(payload, dict) else {}
    if parsed:
        used.add("bwibbu")
        return parsed
    print("BWIBBU 改試 OpenAPI")
    backup = get_json("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL", offline, pause=0.3)
    parsed = _parse_openapi_bwibbu(backup)
    if parsed:
        used.add("openapi")
        used.add("bwibbu")
        return parsed
    notes.append("這次沒有取得本益比、殖利率、股價淨值比。")
    return {}


def _parse_bwibbu(payload: dict | None) -> dict:
    if not payload or payload.get("stat") != "OK":
        return {}
    fields = payload.get("fields") or []
    index = {name: i for i, name in enumerate(fields)}
    try:
        code_i = index["股票代號"]
        name_i = index["股票名稱"]
        pe_i = index["本益比"]
        yield_i = index["殖利率(%)"]
        pb_i = index["股價淨值比"]
    except KeyError:
        return {}
    day = _valuation_day(payload)
    output = {}
    for raw in payload.get("data") or []:
        if len(raw) <= max(code_i, name_i, pe_i, yield_i, pb_i):
            continue
        code = str(raw[code_i]).strip()
        output[code] = {
            "date": day,
            "name": str(raw[name_i]).strip(),
            "pe": ind.parse_number(raw[pe_i]),
            "dividend_yield": ind.parse_number(raw[yield_i]),
            "pb": ind.parse_number(raw[pb_i]),
            "source": "臺灣證券交易所 BWIBBU",
        }
    return output


def _parse_openapi_bwibbu(payload) -> dict:
    if not isinstance(payload, list):
        return {}
    output = {}
    for raw in payload:
        code = str(raw.get("Code") or "").strip()
        if not code:
            continue
        output[code] = {
            "date": ind.roc_to_iso(raw.get("Date")),
            "name": str(raw.get("Name") or "").strip(),
            "pe": ind.parse_number(raw.get("PEratio")),
            "dividend_yield": ind.parse_number(raw.get("DividendYield")),
            "pb": ind.parse_number(raw.get("PBratio")),
            "source": "臺灣證券交易所 OpenAPI BWIBBU_ALL",
        }
    return output


def _valuation_day(payload: dict) -> str | None:
    title = str(payload.get("title") or "")
    matched = re_search_roc(title)
    if matched:
        return matched
    return ind.roc_to_iso(payload.get("date")) if payload.get("date") and "/" in str(payload.get("date")) else _compact_ad(payload.get("date"))


def re_search_roc(title: str) -> str | None:
    import re

    matched = re.search(r"(\d{2,3})/(\d{1,2})/(\d{1,2})", title)
    if not matched:
        return None
    return ind.roc_to_iso("/".join(matched.groups()))


def _compact_ad(value) -> str | None:
    text = str(value or "")
    if len(text) == 8 and text.isdigit() and text.startswith("20"):
        return ind._ymd(int(text[:4]), int(text[4:6]), int(text[6:8]))
    return ind.roc_to_iso(text)


def fetch_yahoo(symbol: str, range_: str, offline: bool, pause: float = 0.3) -> list[dict]:
    quoted = urllib.parse.quote(symbol)
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{quoted}?interval=1d&range={range_}&includeAdjustedClose=false"
    )
    payload = get_json(url, offline, pause=pause)
    result = (((payload or {}).get("chart") or {}).get("result") or [None])[0]
    if not result:
        return []
    stamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    rows = []
    for i, stamp in enumerate(stamps):
        close = _at(quote.get("close"), i)
        if close is None:
            continue
        volume = _at(quote.get("volume"), i)
        if volume == 0:
            continue
        day = datetime.fromtimestamp(stamp, TZ).date().isoformat()
        rows.append(
            {
                "date": day,
                "open": _at(quote.get("open"), i),
                "high": _at(quote.get("high"), i),
                "low": _at(quote.get("low"), i),
                "close": close,
                "volume": volume,
            }
        )
    return rows


def trading_dates(stocks: list[dict], trade_date: str | None) -> list[str]:
    dates = set()
    for stock in stocks:
        for row in stock["rows"]:
            dates.add(row["date"])
    if trade_date:
        dates.add(trade_date)
    return sorted(dates)


def month_range(today: date, count: int) -> list[tuple[int, int]]:
    year, month = today.year, today.month
    pairs = []
    for _ in range(count):
        pairs.append((year, month))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(pairs))


def get_json(url: str, offline: bool, pause: float = SLEEP, retries: int = 4):
    cached = cache_file(url)
    if offline:
        if not cached.exists():
            print(f"離線模式沒有快取 {url}")
            return None
        return json.loads(cached.read_text(encoding="utf-8"))
    last = "未知錯誤"
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=45) as response:
                text = response.read().decode("utf-8", "replace")
            if text.lstrip().startswith("<") or "查詢頻繁" in text or "too many" in text.lower():
                last = "回應不是 JSON 或要求放慢"
                time.sleep(2.5 * (attempt + 1))
                continue
            data = json.loads(text)
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(text, encoding="utf-8")
            time.sleep(pause)
            return data
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, http.client.HTTPException) as exc:
            last = str(exc)
            time.sleep(1.5 * (attempt + 1))
    print(f"連線失敗 {url} ({last})")
    return None


def cache_file(url: str) -> Path:
    digest = hashlib.sha256(url.encode()).hexdigest()[:24]
    return CACHE / f"{digest}.json"


def write_json(payload: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    temporary = OUT.with_suffix(".json.tmp")
    temporary.write_text(text + "\n", encoding="utf-8")
    temporary.replace(OUT)
    print(f"已寫入 {OUT}")


def print_summary(payload: dict) -> None:
    market = payload.get("market") or {}
    print(
        f"收盤日 {payload.get('trade_date')} 加權 {market.get('close')} "
        f"漲跌 {market.get('change')} ({market.get('change_pct')}%)"
    )
    for stock in payload.get("stocks") or []:
        bottom = stock.get("bottom") or {}
        print(
            f"{stock['code']} {stock.get('name')} 收盤 {stock.get('close')} "
            f"漲跌% {stock.get('change_pct')} 抄底 {bottom.get('score')}/6 "
            f"觸發 {'、'.join(bottom.get('triggered') or []) or '無'}"
        )
    for note in payload.get("notes") or []:
        print(f"備註 {note}")


def _at(values, index):
    if not values or index >= len(values):
        return None
    return ind.parse_number(values[index])


def _int_or_none(value: float | None) -> int | None:
    if value is None:
        return None
    return int(round(value))


def _sum(values) -> int | None:
    numbers = [value for value in values if value is not None]
    if not numbers:
        return None
    return int(sum(numbers))


def _unique(notes: list[str]) -> list[str]:
    seen = set()
    output = []
    for note in notes:
        if note and note not in seen:
            seen.add(note)
            output.append(note)
    return output


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except json.JSONDecodeError as exc:
        print(f"JSON 編碼失敗：{exc}", file=sys.stderr)
        raise SystemExit(1)
