#!/usr/bin/env python3
"""用證交所 MIS 更新畫面上的最新成交價。

只改 catalog 裡的現價，不重算綠燈紅燈。瀏覽器不能直接連 MIS（沒有 CORS），
所以由 GitHub Actions 抓，網站上的「更新」按鈕再讀這份檔。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
from datetime import datetime, time as dtime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_data as fd
import indicators as ind

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "site" / "data" / "catalog.json"
BATCH = 80
MIS = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else CATALOG
    if not path.exists():
        print("沒有 catalog，略過即時報價")
        return 0
    try:
        changed = update_catalog(path)
    except Exception as exc:
        print(f"即時報價沒有寫入：{exc}")
        return 0
    print("已更新報價" if changed else "報價沒有可寫入的新成交價")
    return 0


def update_catalog(path: Path) -> bool:
    catalog = json.loads(path.read_text(encoding="utf-8"))
    stocks = catalog.get("stocks") or []
    if not stocks:
        return False
    channels = []
    for stock in stocks:
        market = "otc" if stock.get("market") == "上櫃" else "tse"
        channels.append(f"{market}_{stock['code']}.tw")
    channels.extend(["tse_t00.tw", "otc_o00.tw"])
    quotes: dict[str, dict] = {}
    query_time = None
    for _ in range(1):
        for start in range(0, len(channels), BATCH):
            batch = channels[start : start + BATCH]
            payload = fetch_mis(batch)
            if not payload:
                continue
            query_time = payload.get("queryTime") or query_time
            for row in payload.get("msgArray") or []:
                parsed = parse_row(row)
                if parsed:
                    quotes[parsed["code"]] = parsed
            time.sleep(0.25)
        time.sleep(0.6)
    if not quotes:
        print("MIS 沒有回傳成交價")
        return False
    now = datetime.now(fd.TZ).replace(microsecond=0)
    stamp = quote_stamp(query_time, now)
    session = "intraday" if in_session(stamp) else "close"
    updated = 0
    for stock in stocks:
        quote = quotes.get(stock["code"])
        if not quote:
            continue
        previous = quote["previous"]
        price = quote["price"]
        stock["close"] = price
        if previous:
            stock["change_pct"] = round((price / previous - 1.0) * 100.0, 2)
        updated += 1
    apply_index(catalog.get("market") or {}, quotes.get("t00"), "加權")
    apply_index((catalog.get("market") or {}).get("otc") or {}, quotes.get("o00"), "櫃買")
    catalog["quote_at"] = stamp.isoformat(timespec="seconds")
    catalog["quote_session"] = session
    catalog["quote_source"] = "TWSE MIS"
    text = json.dumps(catalog, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    path.write_text(text, encoding="utf-8")
    print(f"成交價 {updated} 檔，時間 {catalog['quote_at']}，{session}")
    return updated > 0


def fetch_mis(channels: list[str]) -> dict | None:
    import urllib.request

    joined = urllib.parse.quote("|".join(channels), safe="|")
    url = f"{MIS}?ex_ch={joined}&json=1&delay=0"
    payload = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": fd.UA,
                    "Accept": "application/json",
                    "Referer": "https://mis.twse.com.tw/stock/index.jsp",
                },
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except Exception:
            time.sleep(1 + attempt)
    time.sleep(0.15)
    if not isinstance(payload, dict):
        return None
    return payload


def parse_row(row: dict) -> dict | None:
    code = str(row.get("c") or "").strip()
    price = pick_price(row)
    if not code or price is None:
        return None
    return {"code": code, "price": price, "previous": ind.parse_number(row.get("y"))}


def pick_price(row: dict) -> float | None:
    last = ind.parse_number(row.get("z"))
    if not last or last <= 0:
        trade = row.get("trade")
        if isinstance(trade, dict):
            last = ind.parse_number(trade.get("z"))
    if last and last > 0:
        return round(last, 2)
    open_ = ind.parse_number(row.get("o"))
    high = ind.parse_number(row.get("h"))
    low = ind.parse_number(row.get("l"))
    if open_ and high and low and open_ == high == low and open_ > 0:
        return round(open_, 2)
    return None


def apply_index(target: dict, quote: dict | None, label: str) -> None:
    if not target or not quote:
        return
    price = quote["price"]
    previous = quote["previous"]
    target["close"] = price
    if previous:
        target["change"] = round(price - previous, 2)
        target["change_pct"] = round((price / previous - 1.0) * 100.0, 2)
    print(f"{label} {price}")


def quote_stamp(query_time: dict | None, now: datetime) -> datetime:
    if not query_time:
        return now
    day = str(query_time.get("sysDate") or "")
    clock = str(query_time.get("sysTime") or "")
    if len(day) == 8 and len(clock) >= 5:
        try:
            return datetime.strptime(day + clock[:8], "%Y%m%d%H:%M:%S").replace(tzinfo=fd.TZ)
        except ValueError:
            try:
                return datetime.strptime(day + clock[:5], "%Y%m%d%H:%M").replace(tzinfo=fd.TZ)
            except ValueError:
                return now
    return now


def in_session(moment: datetime) -> bool:
    if moment.weekday() >= 5:
        return False
    return dtime(9, 0) <= moment.time() < dtime(13, 35)


if __name__ == "__main__":
    raise SystemExit(main())
