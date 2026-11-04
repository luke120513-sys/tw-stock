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
    universe = [item for item in quotes if include_security(item["code"], item["name"])]
    universe.sort(key=lambda item: item["code"])
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
        "coverage": "上市普通股，以及 0050、0056、00878 這類ETF。不含上櫃、權證、槓桿和反向ETF。",
        "count": len(summaries),
        "defaults": load_defaults(),
        "notes": _unique(notes),
        "sources_used": [source["id"] for source in fd.SOURCES if source["id"] in used or source["id"] == "yahoo"],
        "market": public_market(market),
        "stocks": summaries,
    }
    if "yahoo" not in catalog["sources_used"]:
        catalog["sources_used"].append("yahoo")
    write_json(CATALOG, catalog)
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
        rows = fd.fetch_yahoo(f"{item['code']}.TW", "1y", offline, pause=0.0)
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
    latest = enriched[-1]
    return {
        "code": item["code"],
        "name": item["name"],
        "date": latest.get("date"),
        "close": ind._round(latest.get("close"), 2),
        "change": ind._round(change, 2),
        "change_pct": ind._round(change_pct, 2),
        "can_bottom": view["can_bottom"],
        "label": view["label"],
        "reason": view["reason"],
        "reasons": view["reasons"],
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
        "close": payload.get("close"),
        "change_pct": payload.get("change_pct"),
        "can_bottom": bool(payload.get("can_bottom")),
        "label": payload.get("label") or ("可以抄底" if payload.get("can_bottom") else "不行"),
        "reason": payload.get("reason") or "",
        "score": payload.get("score"),
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
