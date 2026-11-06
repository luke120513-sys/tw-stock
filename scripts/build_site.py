#!/usr/bin/env python3
"""整理給網站用的清單。

個股日K用 Yahoo Finance（一檔一次，收盤價與證交所相同），
因為證交所沒有一次給全部歷史的接口，逐月抓一千檔會太慢。
大盤、三大法人、外資買賣超、本益比仍用證交所。
找不到或失敗的股票不填假數字，有舊檔就沿用。
"""

from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_data as fd
import indicators as ind

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "site" / "data"
STOCKS = DATA / "stocks"
CATALOG = DATA / "catalog.json"
TZ = fd.TZ
DEFAULTS_FILE = ROOT / "watchlist.json"


def main() -> int:
    offline = "--offline" in sys.argv
    limit = _flag_int("--limit")
    now = datetime.now(TZ).replace(microsecond=0)
    notes: list[str] = []
    used: set[str] = set()
    print(f"開始整理 {now.isoformat()}")

    index_rows, index_source = fd.fetch_index(now.date(), offline, used, notes)
    trade_date = index_rows[-1]["date"] if index_rows else None
    institutions = None
    if trade_date:
        institutions = fd.fetch_institutions(trade_date, offline, used, notes)
        if institutions is None:
            for row in reversed(index_rows[:-1]):
                institutions = fd.fetch_institutions(row["date"], offline, used, notes)
                if institutions:
                    break
    market = fd.build_market(index_rows, index_source, institutions) if index_rows else None

    quotes = load_quotes(offline, used, notes)
    for item in quotes:
        item["market"] = "上市"
    universe = [item for item in quotes if include_security(item["code"], item["name"])]
    otc_quotes = load_otc_quotes(offline, used, notes)
    listed_codes = {item["code"] for item in universe}
    otc_count = 0
    for item in otc_quotes:
        if item["code"] in listed_codes:
            continue
        universe.append(item)
        otc_count += 1
    universe.sort(key=lambda item: item["code"])
    codes_only = _flag_codes()
    if codes_only:
        universe = [item for item in universe if item["code"] in codes_only]
        otc_count = sum(1 for item in universe if item.get("market") == "上櫃")
    print(f"上櫃納入 {otc_count} 檔")
    if limit:
        preferred = set(load_defaults())
        picked = [item for item in universe if item["code"] in preferred]
        for item in universe:
            if item["code"] not in preferred:
                picked.append(item)
            if len(picked) >= limit:
                break
        universe = picked
    print(f"準備抓 {len(universe)} 檔日K")

    valuations = fd.fetch_valuations(offline, used, notes)
    valuations.update(load_otc_valuations(offline, used, notes))
    histories = fetch_histories(universe, offline)
    for item in universe:
        rows = histories.get(item["code"]) or []
        histories[item["code"]] = overlay_official(rows, item)

    foreign_dates = []
    if index_rows:
        foreign_dates = [row["date"] for row in index_rows][-15:]
    elif histories.get("2330"):
        foreign_dates = [row["date"] for row in histories["2330"]][-15:]
    foreign_by_date: dict[str, dict] = {}
    missed_foreign = 0
    for day in foreign_dates:
        print(f"外資買賣超 {day}")
        payload = fd.fetch_t86(day, offline, used)
        if payload:
            foreign_by_date[day] = payload
            used.add("t86")
        else:
            missed_foreign += 1
    if missed_foreign:
        notes.append(f"有 {missed_foreign} 天沒有取得外資買賣超。")
    otc_missed = 0
    for day in foreign_dates[-8:]:
        print(f"上櫃外資買賣超 {day}")
        extra = fetch_otc_foreign(day, offline, used)
        if extra:
            foreign_by_date.setdefault(day, {}).update(extra)
        else:
            otc_missed += 1
    if otc_missed:
        notes.append(f"有 {otc_missed} 天沒有取得上櫃外資買賣超。")
    if market is not None:
        market["otc"] = fetch_otc_index(foreign_dates or [row["date"] for row in index_rows], offline, used, notes)

    STOCKS.mkdir(parents=True, exist_ok=True)
    kept = []
    failed = []
    reused = 0
    for item in universe:
        code = item["code"]
        built = build_stock(item, histories.get(code) or [], foreign_by_date, valuations.get(code))
        if built and built.get("series"):
            write_json(STOCKS / f"{code}.json", built)
            kept.append(code)
        elif (STOCKS / f"{code}.json").exists():
            kept.append(code)
            reused += 1
        else:
            failed.append(code)
    if reused:
        notes.append(f"{reused} 檔這次沒有新的日K，沿用上次的檔案。")
    if failed and len(failed) <= 12:
        notes.append("這次沒有日K：" + "、".join(failed))
    elif failed:
        notes.append(f"有 {len(failed)} 檔沒有取得日K，搜尋清單裡不會出現。")

    summaries = []
    for code in kept:
        try:
            payload = json.loads((STOCKS / f"{code}.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not payload.get("series"):
            continue
        summaries.append(summary_of(payload))

    if not summaries:
        print("沒有任何股票資料。", file=sys.stderr)
        return 1

    if market is None and CATALOG.exists():
        previous = json.loads(CATALOG.read_text(encoding="utf-8"))
        market = previous.get("market")
        notes.append("這次沒有取得大盤，沿用上次的大盤。")

    catalog = {
        "schema": 2,
        "generated_at": now.isoformat(timespec="seconds"),
        "timezone": "Asia/Taipei",
        "trade_date": (market or {}).get("date") or trade_date,
        "cutoff": ind.DIP_CUTOFF,
        "rule": ind.DIP_RULE,
        "coverage": "上市與上櫃的普通股，以及 0050、0056、00878、006201 這類 ETF。不含興櫃、權證、槓桿和反向 ETF。",
        "count": len(summaries),
        "defaults": load_defaults(),
        "notes": _unique(notes),
        "sources_used": [source["id"] for source in fd.SOURCES if source["id"] in used or source["id"] == "yahoo"],
        "market": public_market(market),
        "stocks": summaries,
    }
    if "yahoo" not in catalog["sources_used"]:
        catalog["sources_used"].append("yahoo")
    catalog["quote_at"] = now.isoformat(timespec="seconds")
    catalog["quote_session"] = "intraday" if in_trading_session(now) else "close"
    write_json(CATALOG, catalog)
    if catalog["quote_session"] == "intraday":
        try:
            import refresh_quotes

            refresh_quotes.update_catalog(CATALOG)
        except Exception as exc:
            print(f"盤中報價這次沒寫入：{exc}")
    stale = DATA / "latest.json"
    if stale.exists():
        stale.unlink()
    yes = sum(1 for item in summaries if item["can_bottom"])
    print(f"可搜尋 {len(summaries)} 檔，其中可以抄底 {yes} 檔")
    print(f"門檻：分數 >= {ind.DIP_CUTOFF} 才是可以抄底")
    return 0


def load_quotes(offline: bool, used: set[str], notes: list[str]) -> list[dict]:
    payload = fd.get_json(
        "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
        offline,
        pause=0.3,
    )
    if not isinstance(payload, list):
        notes.append("這次沒有取得上市股票名單。")
        return quotes_from_existing()
    used.add("openapi")
    rows = []
    for raw in payload:
        code = str(raw.get("Code") or "").strip()
        name = str(raw.get("Name") or "").strip()
        if not code or not name:
            continue
        rows.append(
            {
                "code": code,
                "name": name,
                "date": ind.roc_to_iso(raw.get("Date")),
                "open": ind.parse_number(raw.get("OpeningPrice")),
                "high": ind.parse_number(raw.get("HighestPrice")),
                "low": ind.parse_number(raw.get("LowestPrice")),
                "close": ind.parse_number(raw.get("ClosingPrice")),
                "volume": ind.parse_number(raw.get("TradeVolume")),
            }
        )
    return rows


def quotes_from_existing() -> list[dict]:
    if not CATALOG.exists():
        return []
    try:
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return [{"code": item["code"], "name": item["name"]} for item in catalog.get("stocks") or []]


def include_otc(code: str, name: str) -> bool:
    if any(word in name for word in ("購", "售", "牛證", "熊證", "正2", "反1", "槓桿", "反向", "興櫃")):
        return False
    if len(code) == 4 and code.isdigit():
        return True
    if code.isdigit() and code.startswith("00") and 5 <= len(code) <= 6:
        return True
    return False


def load_otc_quotes(offline: bool, used: set[str], notes: list[str]) -> list[dict]:
    payload = fd.get_json(
        "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
        offline,
        pause=0.4,
    )
    if not isinstance(payload, list):
        notes.append("這次沒有取得上櫃股票名單。")
        return []
    used.add("tpex")
    rows = []
    for raw in payload:
        code = str(raw.get("SecuritiesCompanyCode") or "").strip()
        name = str(raw.get("CompanyName") or "").strip()
        if not code or not name or not include_otc(code, name):
            continue
        rows.append(
            {
                "code": code,
                "name": name,
                "market": "上櫃",
                "date": ind.roc_to_iso(raw.get("Date")),
                "open": ind.parse_number(raw.get("Open")),
                "high": ind.parse_number(raw.get("High")),
                "low": ind.parse_number(raw.get("Low")),
                "close": ind.parse_number(raw.get("Close")),
                "volume": ind.parse_number(raw.get("TradingShares")),
            }
        )
    return rows


def load_otc_valuations(offline: bool, used: set[str], notes: list[str]) -> dict:
    payload = fd.get_json(
        "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis",
        offline,
        pause=0.4,
    )
    if not isinstance(payload, list):
        notes.append("這次沒有取得上櫃本益比。")
        return {}
    used.add("tpex")
    output = {}
    for raw in payload:
        code = str(raw.get("SecuritiesCompanyCode") or "").strip()
        if not code:
            continue
        output[code] = {
            "date": ind.roc_to_iso(raw.get("Date")),
            "name": str(raw.get("CompanyName") or "").strip(),
            "pe": ind.parse_number(raw.get("PriceEarningRatio")),
            "dividend_yield": ind.parse_number(raw.get("YieldRatio")),
            "pb": ind.parse_number(raw.get("PriceBookRatio")),
            "source": "證券櫃檯買賣中心",
        }
    return output


def fetch_otc_foreign(day: str, offline: bool, used: set[str]) -> dict:
    year, month, date = day.split("-")
    roc = f"{int(year) - 1911}/{month}/{date}"
    url = (
        "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php"
        f"?l=zh-tw&o=json&se=EW&t=D&d={roc}"
    )
    payload = fd.get_json(url, offline, pause=0.45)
    tables = (payload or {}).get("tables") or []
    if not tables or not tables[0].get("data"):
        return {}
    used.add("tpex")
    found = {}
    for raw in tables[0]["data"]:
        if len(raw) < 5:
            continue
        code = str(raw[0]).strip()
        shares = ind.parse_number(raw[4])
        if not code or shares is None:
            continue
        found[code] = {"name": str(raw[1]).strip(), "net_shares": int(round(shares))}
    return found


def fetch_otc_index(dates: list[str], offline: bool, used: set[str], notes: list[str]) -> dict | None:
    history = []
    for day in dates[-30:]:
        year, month, date = day.split("-")
        url = (
            "https://www.tpex.org.tw/www/zh-tw/afterTrading/indexSummary"
            f"?date={year}/{month}/{date}&response=json"
        )
        payload = fd.get_json(url, offline, pause=0.35)
        point = _otc_index_point(payload, day)
        if point:
            history.append(point)
            used.add("tpex")
    if not history:
        payload = fd.get_json("https://www.tpex.org.tw/openapi/v1/tpex_index", offline, pause=0.3)
        if isinstance(payload, list):
            for raw in payload:
                day = ind.roc_to_iso(raw.get("Date")) or (
                    f"{raw['Date'][:4]}-{raw['Date'][4:6]}-{raw['Date'][6:8]}" if str(raw.get("Date") or "").startswith("20") else None
                )
                close = ind.parse_number(raw.get("Close"))
                change = ind.parse_number(raw.get("Change"))
                if not day or close is None:
                    continue
                base = close - change if change is not None else None
                pct = (change / base * 100.0) if base else None
                history.append(
                    {
                        "date": day,
                        "close": ind._round(close, 2),
                        "change": ind._round(change, 2),
                        "change_pct": ind._round(pct, 2),
                    }
                )
            if history:
                used.add("tpex")
    if not history:
        notes.append("這次沒有取得櫃買指數。")
        return None
    latest = history[-1]
    return {
        "name": "櫃買指數",
        "date": latest["date"],
        "close": latest["close"],
        "change": latest["change"],
        "change_pct": latest["change_pct"],
        "source": "證券櫃檯買賣中心",
        "history": history,
    }


def _otc_index_point(payload: dict | None, day: str) -> dict | None:
    for table in (payload or {}).get("tables") or []:
        for raw in table.get("data") or []:
            if not raw or str(raw[0]).strip() != "櫃買指數":
                continue
            close = ind.parse_number(raw[1] if len(raw) > 1 else None)
            change = ind.parse_number(raw[2] if len(raw) > 2 else None)
            pct = ind.parse_number(raw[3] if len(raw) > 3 else None)
            if close is None:
                return None
            return {
                "date": day,
                "close": ind._round(close, 2),
                "change": ind._round(change, 2),
                "change_pct": ind._round(pct, 2),
            }
    return None


def include_security(code: str, name: str) -> bool:
    if any(word in name for word in ("購", "售", "牛證", "熊證", "正2", "反1", "槓桿", "反向")):
        return False
    if len(code) == 4 and code.isdigit():
        return True
    if code.isdigit() and code.startswith("00") and 5 <= len(code) <= 6:
        return True
    return False


def fetch_histories(universe: list[dict], offline: bool) -> dict[str, list[dict]]:
    limiter = RateLimiter(0.12)
    result: dict[str, list[dict]] = {}
    lock = threading.Lock()
    total = len(universe)
    done = {"n": 0}

    def one(item: dict) -> None:
        if not offline:
            limiter.wait()
        suffix = "TWO" if item.get("market") == "上櫃" else "TW"
        rows = fd.fetch_yahoo(f"{item['code']}.{suffix}", "1y", offline, pause=0.0)
        with lock:
            result[item["code"]] = rows
            done["n"] += 1
            if done["n"] % 100 == 0 or done["n"] == total:
                print(f"日K {done['n']}/{total}")

    if offline or total <= 1:
        for item in universe:
            one(item)
        return result
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(one, universe))
    return result


def overlay_official(rows: list[dict], quote: dict) -> list[dict]:
    day = quote.get("date")
    close = quote.get("close")
    if not day or close is None:
        return ind._sorted_unique(rows)
    bar = {
        "date": day,
        "open": quote.get("open") if quote.get("open") is not None else close,
        "high": quote.get("high") if quote.get("high") is not None else close,
        "low": quote.get("low") if quote.get("low") is not None else close,
        "close": close,
        "volume": quote.get("volume"),
    }
    merged = [row for row in rows if row.get("date") != day]
    merged.append(bar)
    return ind._sorted_unique(merged)


def build_stock(item: dict, rows: list[dict], foreign_by_date: dict, valuation: dict | None) -> dict | None:
    enriched = ind.enrich_bars(rows)
    if len(enriched) < 30:
        return None
    foreign = []
    for day in sorted(foreign_by_date):
        found = foreign_by_date[day].get(item["code"])
        if not found:
            continue
        shares = found["net_shares"]
        foreign.append({"date": day, "net_shares": shares, "net_lots": round(shares / 1000.0, 3)})
        if found.get("name"):
            item["name"] = found["name"]
    signal_nets = [point["net_shares"] for point in foreign[-ind.FOREIGN_SIGNAL_DAYS :]]
    bottom = ind.bottom_signals(enriched, signal_nets)
    view = ind.public_bottom(bottom)
    for cond in bottom["conditions"]:
        cond["plain"] = ind.PLAIN_CONDITION.get(cond["id"], cond["name"])
    change, change_pct = ind.price_change(enriched)
    alerts = ind.build_alerts(enriched, change_pct)
    latest = enriched[-1]
    return {
        "code": item["code"],
        "name": item["name"],
        "market": item.get("market") or "上市",
        "date": latest.get("date"),
        "close": ind._round(latest.get("close"), 2),
        "change": ind._round(change, 2),
        "change_pct": ind._round(change_pct, 2),
        "can_bottom": view["can_bottom"],
        "label": view["label"],
        "reason": view["reason"],
        "reasons": view["reasons"],
        "alerts": [{"id": item["id"], "title": item["title"], "detail": item["detail"]} for item in alerts],
        "score": view["score"],
        "max_score": 6,
        "cutoff": ind.DIP_CUTOFF,
        "bottom": {
            "score": bottom["score"],
            "max_score": 6,
            "cutoff": ind.DIP_CUTOFF,
            "rsi14": bottom["rsi14"],
            "k": bottom["k"],
            "d": bottom["d"],
            "ma20": bottom["ma20"],
            "ma60": bottom["ma60"],
            "boll_lower": bottom["boll_lower"],
            "boll_upper": bottom["boll_upper"],
            "bias60_pct": bottom["bias60_pct"],
            "conditions": bottom["conditions"],
            "triggered": bottom["triggered"],
        },
        "valuation": valuation,
        "foreign": foreign[-15:],
        "series": [ind.round_bar(row) for row in enriched[-140:]],
        "price_source": f"Yahoo Finance {item['code']}.TW",
        "bars": len(enriched),
    }


def summary_of(payload: dict) -> dict:
    return {
        "code": payload["code"],
        "name": payload.get("name") or payload["code"],
        "market": payload.get("market") or "上市",
        "close": payload.get("close"),
        "change_pct": payload.get("change_pct"),
        "can_bottom": bool(payload.get("can_bottom")),
        "label": payload.get("label") or ("可以抄底" if payload.get("can_bottom") else "不行"),
        "reason": payload.get("reason") or "",
        "score": payload.get("score"),
        "alerts": payload.get("alerts") or [],
    }


def public_market(market: dict | None) -> dict | None:
    if not market:
        return None
    groups = (market.get("institutions") or {}).get("groups") or []
    bits = []
    for group in groups:
        net = group.get("net")
        if net is None:
            continue
        name = {"外資及陸資": "外資", "投信": "投信（基金）", "自營商": "券商自己"}.get(group["name"], group["name"])
        bits.append(f"{name}在買" if net > 0 else f"{name}在賣" if net < 0 else f"{name}買賣差不多")
    return {
        "date": market.get("date"),
        "close": market.get("close"),
        "change": market.get("change"),
        "change_pct": market.get("change_pct"),
        "turnover": market.get("turnover"),
        "source": market.get("source"),
        "plain_flow": "、".join(bits),
        "history": (market.get("history") or [])[-70:],
        "institutions": market.get("institutions"),
        "otc": market.get("otc"),
    }


def load_defaults() -> list[str]:
    try:
        data = json.loads(DEFAULTS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ["2330", "2317", "2454", "2382", "2308", "2881"]
    codes = []
    for item in data.get("stocks") or []:
        code = str(item.get("code") or "").strip()
        if code:
            codes.append(code)
    return codes or ["2330", "2317", "2454", "2382", "2308", "2881"]


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _unique(notes: list[str]) -> list[str]:
    seen = set()
    output = []
    for note in notes:
        if note and note not in seen:
            seen.add(note)
            output.append(note)
    return output


def _flag_int(flag: str) -> int | None:
    if flag not in sys.argv:
        return None
    return int(sys.argv[sys.argv.index(flag) + 1])


def in_trading_session(moment: datetime) -> bool:
    from datetime import time as dtime

    if moment.weekday() >= 5:
        return False
    return dtime(9, 0) <= moment.time() < dtime(13, 35)


def _flag_codes() -> set[str] | None:
    if "--codes" not in sys.argv:
        return None
    raw = sys.argv[sys.argv.index("--codes") + 1]
    return {part.strip() for part in raw.split(",") if part.strip()}


class RateLimiter:
    def __init__(self, gap: float) -> None:
        self.gap = gap
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.time()
            if now < self.next_at:
                time.sleep(self.next_at - now)
            self.next_at = time.time() + self.gap


if __name__ == "__main__":
    raise SystemExit(main())
