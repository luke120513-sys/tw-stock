"""技術指標、抄底分數與異動條件。

抄底六條件的算法對齊原本的 bottom_signal.py：
RSI 用 Wilder 平滑、KD 從 50 起算、布林用 20 日母體標準差。
沒有足夠 K 線或法人資料時，該條件不觸發，也不填假數字。
"""

from __future__ import annotations

import math
import re
from datetime import date, timedelta
from typing import Any

RSI_N = 14
KD_N = 9
BOLL_N = 20
BOLL_K = 2.0
MA_SLOW = 60
BIAS_LIMIT = -10.0
VOL_SHRINK = 0.7
FOREIGN_SIGNAL_DAYS = 5
DIP_CUTOFF = 3
ALERT_CHANGE_PCT = 3.0
ALERT_VOLUME_RATIO = 1.5
ALERT_LOW_DAYS = 60
MONTH_DAYS = 30
GAP_DAYS = 18


def parse_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return None
        return number
    text = str(value).strip().replace(",", "").replace(" ", "")
    if text in {"", "--", "---", "NaN", "null", "None", "N/A", "X"}:
        return None
    text = text.replace("X", "").replace("x", "")
    if text in {"", "+", "-"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def parse_signed(value: Any) -> tuple[float | None, bool]:
    """解析漲跌價差。第二個值代表字串裡有除權息標記 X。"""
    if value is None:
        return None, False
    raw = str(value).strip()
    ex_rights = "X" in raw.upper() or "除" in raw
    return parse_number(raw), ex_rights


def roc_to_iso(value: Any) -> str | None:
    text = str(value or "").strip()
    matched = re.fullmatch(r"(\d{2,3})/(\d{1,2})/(\d{1,2})", text)
    if matched:
        year, month, day = (int(part) for part in matched.groups())
        return _ymd(year + 1911, month, day)
    matched = re.fullmatch(r"(\d{2,3})(\d{2})(\d{2})", text)
    if matched:
        year, month, day = (int(part) for part in matched.groups())
        return _ymd(year + 1911, month, day)
    matched = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if matched:
        year, month, day = (int(part) for part in matched.groups())
        return _ymd(year, month, day)
    return None


def _ymd(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def iso_compact(iso: str) -> str:
    return iso.replace("-", "")


def fmt_num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:,.{digits}f}"


def has_large_gap(rows: list[dict], max_days: int = GAP_DAYS) -> bool:
    dates = []
    for row in rows:
        try:
            dates.append(date.fromisoformat(row["date"]))
        except (KeyError, TypeError, ValueError):
            continue
    for left, right in zip(dates, dates[1:]):
        if (right - left).days > max_days:
            return True
    return False


def choose_price_history(
    twse_rows: list[dict], yahoo_rows: list[dict], min_bars: int = 60
) -> tuple[list[dict], str]:
    """挑一組真實日 K。證交所資料完整時優先，否則改用 Yahoo。不補造缺失的日子。"""
    twse = _sorted_unique(twse_rows)
    yahoo = _sorted_unique(yahoo_rows)
    twse_ok = len(twse) >= min_bars and not has_large_gap(twse)
    yahoo_ok = len(yahoo) >= min_bars and not has_large_gap(yahoo)
    if twse_ok:
        return twse, "TWSE"
    if yahoo_ok:
        return yahoo, "Yahoo"
    if twse and (not yahoo or len(twse) >= len(yahoo)):
        return twse, "TWSE"
    if yahoo:
        return yahoo, "Yahoo"
    return [], ""


def _sorted_unique(rows: list[dict]) -> list[dict]:
    by_date: dict[str, dict] = {}
    for row in rows:
        day = row.get("date")
        if not day or row.get("close") is None:
            continue
        by_date[day] = row
    return [by_date[key] for key in sorted(by_date)]


def rsi_series(closes: list[float], n: int = RSI_N) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= n:
        return out
    gain = loss = 0.0
    for i in range(1, n + 1):
        diff = closes[i] - closes[i - 1]
        gain += max(diff, 0.0)
        loss += max(-diff, 0.0)
    gain /= n
    loss /= n
    out[n] = _rsi_value(gain, loss)
    for i in range(n + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gain = (gain * (n - 1) + max(diff, 0.0)) / n
        loss = (loss * (n - 1) + max(-diff, 0.0)) / n
        out[i] = _rsi_value(gain, loss)
    return out


def rsi_last(closes: list[float], n: int = RSI_N) -> float | None:
    """與 bottom_signal.py 的 rsi() 相同，只回最後一根。"""
    if len(closes) <= n:
        return None
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
    return _rsi_value(gain, loss)


def _rsi_value(gain: float, loss: float) -> float:
    if loss == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + gain / loss)


def kd_series(rows: list[dict], n: int = KD_N) -> tuple[list[float], list[float]]:
    k = d = 50.0
    ks: list[float] = []
    ds: list[float] = []
    for i, row in enumerate(rows):
        window = rows[max(0, i - n + 1) : i + 1]
        highs = [bar["high"] for bar in window if bar.get("high") is not None]
        lows = [bar["low"] for bar in window if bar.get("low") is not None]
        high = max(highs) if highs else row["close"]
        low = min(lows) if lows else row["close"]
        if high == low:
            rsv = 50.0
        else:
            rsv = (row["close"] - low) / (high - low) * 100.0
        k = k * 2.0 / 3.0 + rsv / 3.0
        d = d * 2.0 / 3.0 + k / 3.0
        ks.append(k)
        ds.append(d)
    return ks, ds


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _pstdev(values: list[float], mean: float) -> float:
    return math.sqrt(sum((item - mean) ** 2 for item in values) / len(values))


def enrich_bars(rows: list[dict]) -> list[dict]:
    ordered = _sorted_unique(rows)
    closes = [row["close"] for row in ordered]
    rsis = rsi_series(closes)
    ks, ds = kd_series(ordered) if ordered else ([], [])
    enriched = []
    for i, row in enumerate(ordered):
        item = dict(row)
        item["ma20"] = _window_mean(closes, i, BOLL_N)
        item["ma60"] = _window_mean(closes, i, MA_SLOW)
        if item["ma20"] is None:
            item["boll_mid"] = item["boll_upper"] = item["boll_lower"] = None
        else:
            window = closes[i - BOLL_N + 1 : i + 1]
            mid = item["ma20"]
            sd = _pstdev(window, mid)
            item["boll_mid"] = mid
            item["boll_upper"] = mid + BOLL_K * sd
            item["boll_lower"] = mid - BOLL_K * sd
        item["rsi"] = rsis[i]
        item["k"] = ks[i] if ks else None
        item["d"] = ds[i] if ds else None
        enriched.append(item)
    return enriched


def _window_mean(closes: list[float], index: int, n: int) -> float | None:
    if index + 1 < n:
        return None
    return _mean(closes[index - n + 1 : index + 1])


def month_change(rows: list[dict], calendar_days: int = MONTH_DAYS) -> tuple[float | None, str | None]:
    if len(rows) < 2:
        return None, None
    end = date.fromisoformat(rows[-1]["date"])
    target = end - timedelta(days=calendar_days)
    base = None
    for row in rows:
        if date.fromisoformat(row["date"]) <= target:
            base = row
        else:
            break
    if base is None or not base.get("close"):
        return None, None
    pct = (rows[-1]["close"] / base["close"] - 1.0) * 100.0
    return pct, base["date"]


def price_change(rows: list[dict]) -> tuple[float | None, float | None]:
    if len(rows) < 2 or not rows[-2].get("close"):
        return None, None
    previous = rows[-2]["close"]
    change = rows[-1]["close"] - previous
    pct = change / previous * 100.0 if previous else None
    return change, pct


def build_alerts(rows: list[dict], change_pct: float | None) -> list[dict]:
    alerts: list[dict] = []
    if not rows:
        return alerts
    today = rows[-1]
    if change_pct is not None and abs(change_pct) >= ALERT_CHANGE_PCT:
        direction = "上漲" if change_pct > 0 else "下跌"
        alerts.append(
            {
                "id": "change_up" if change_pct > 0 else "change_down",
                "title": f"{direction}超過 {ALERT_CHANGE_PCT:g}%",
                "detail": f"漲跌幅 {fmt_num(change_pct, 2)}%，絕對值達到 {ALERT_CHANGE_PCT:g}%。",
            }
        )
    if len(rows) >= 21:
        previous = [row["volume"] for row in rows[-21:-1] if row.get("volume")]
        if len(previous) == 20:
            average = _mean(previous)
            volume = today.get("volume") or 0
            if average > 0 and volume >= ALERT_VOLUME_RATIO * average:
                ratio = volume / average
                alerts.append(
                    {
                        "id": "volume",
                        "title": "成交量放大",
                        "detail": (
                            f"成交量 {fmt_num(volume / 1000, 0)} 張，"
                            f"是前 20 個交易日均量 {fmt_num(average / 1000, 0)} 張的 {fmt_num(ratio, 2)} 倍。"
                        ),
                    }
                )
    if len(rows) >= ALERT_LOW_DAYS:
        window = rows[-ALERT_LOW_DAYS:]
        today_low = window[-1].get("low")
        if today_low is None:
            today_low = window[-1].get("close")
        prior_lows = []
        for row in window[:-1]:
            low = row.get("low")
            if low is None:
                low = row.get("close")
            if low is not None:
                prior_lows.append(low)
        if today_low is not None and len(prior_lows) == ALERT_LOW_DAYS - 1:
            prior_min = min(prior_lows)
            if today_low < prior_min:
                alerts.append(
                    {
                        "id": "low60",
                        "title": "60 日新低",
                        "detail": (
                            f"最低價 {fmt_num(today_low, 2)} 低於前 59 個交易日最低價 {fmt_num(prior_min, 2)}。"
                        ),
                    }
                )
    return alerts


def bottom_signals(rows: list[dict], foreign_nets: list[float | None]) -> dict:
    """rows 需已由 enrich_bars 加上均線與 RSI。foreign_nets 由舊到新，單位是股。"""
    conditions = [
        _rsi_condition(rows),
        _kd_condition(rows),
        _boll_condition(rows),
        _bias_condition(rows),
        _volume_condition(rows),
        _foreign_condition(foreign_nets),
    ]
    triggered = [item["name"] for item in conditions if item["hit"]]
    latest = rows[-1] if rows else {}
    return {
        "score": len(triggered),
        "max_score": 6,
        "rsi14": _round(latest.get("rsi"), 1),
        "k": _round(latest.get("k"), 1),
        "d": _round(latest.get("d"), 1),
        "ma20": _round(latest.get("ma20"), 2),
        "ma60": _round(latest.get("ma60"), 2),
        "boll_lower": _round(latest.get("boll_lower"), 2),
        "boll_upper": _round(latest.get("boll_upper"), 2),
        "bias60_pct": _round(_bias_value(rows), 2),
        "conditions": conditions,
        "triggered": triggered,
    }


def _round(value: float | None, digits: int) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _rsi_condition(rows: list[dict]) -> dict:
    name = "RSI超賣"
    if not rows or rows[-1].get("rsi") is None:
        return _unknown(
            "rsi",
            name,
            "K 線不足，無法計算 RSI(14)，這項不觸發。",
            "RSI 至少要 15 個收盤價。資料不夠時不當成超賣，也不加分。",
        )
    rsi = rows[-1]["rsi"]
    hit = rsi < 30
    if hit:
        summary = f"RSI(14) 是 {fmt_num(rsi, 1)}，低於 30。"
    else:
        summary = f"RSI(14) 是 {fmt_num(rsi, 1)}，沒有低於 30。"
    explain = (
        "RSI 把最近 14 個交易日的漲跌力道換成 0 到 100。"
        "低於 30 通常叫超賣，代表短線跌得比較急。這只說明跌得快，不代表已經跌完。"
    )
    return _condition("rsi", name, hit, summary, explain)


def _kd_condition(rows: list[dict]) -> dict:
    name = "KD低檔黃金交叉"
    if len(rows) < 2 or rows[-1].get("k") is None or rows[-2].get("k") is None:
        return _unknown(
            "kd",
            name,
            "K 線不足，無法判斷 KD，這項不觸發。",
            "KD 需要高低價與收盤價。資料不夠時不當成黃金交叉，也不加分。",
        )
    k, d = rows[-1]["k"], rows[-1]["d"]
    prev_k, prev_d = rows[-2]["k"], rows[-2]["d"]
    cross = prev_k <= prev_d and k > d
    hit = k < 20 and cross
    if hit:
        summary = f"K 是 {fmt_num(k, 1)}、D 是 {fmt_num(d, 1)}。K 在 20 以下，而且由下往上穿過 D。"
    elif k >= 20:
        summary = f"K 是 {fmt_num(k, 1)}、D 是 {fmt_num(d, 1)}。K 沒有低於 20。"
    else:
        summary = f"K 是 {fmt_num(k, 1)}、D 是 {fmt_num(d, 1)}。K 在 20 以下，但沒有向上穿越 D。"
    explain = (
        "KD 看收盤在最近 9 日高低區間的位置。K 低於 20 代表靠近區間下緣；"
        "K 從 D 下方穿到上方叫黃金交叉，是短線由弱轉強的跡象。低檔交叉有時只是下跌途中的小反彈。"
    )
    return _condition("kd", name, hit, summary, explain)


def _boll_condition(rows: list[dict]) -> dict:
    name = "跌破布林下緣"
    if not rows or rows[-1].get("boll_lower") is None:
        return _unknown(
            "boll",
            name,
            "未滿 20 根 K 線，無法計算布林通道，這項不觸發。",
            "布林下緣用過去 20 個收盤價。根數不夠時不視為跌破，也不加分。",
        )
    close = rows[-1]["close"]
    lower = rows[-1]["boll_lower"]
    hit = close < lower
    if hit:
        summary = f"收盤 {fmt_num(close, 2)}，低於布林下緣 {fmt_num(lower, 2)}。"
    else:
        summary = f"收盤 {fmt_num(close, 2)}，布林下緣是 {fmt_num(lower, 2)}，還沒有跌破。"
    explain = (
        "布林通道是 20 日均線上下各加減 2 倍標準差。收盤掉到下緣外面，代表比最近的波動還弱。"
        "跌破之後仍可能沿著下緣繼續走低。"
    )
    return _condition("boll", name, hit, summary, explain)


def _bias_value(rows: list[dict]) -> float | None:
    if not rows or rows[-1].get("ma60") in (None, 0):
        return None
    return (rows[-1]["close"] / rows[-1]["ma60"] - 1.0) * 100.0


def _bias_condition(rows: list[dict]) -> dict:
    name = "負乖離過大"
    bias = _bias_value(rows)
    if bias is None:
        return _unknown(
            "bias",
            name,
            "未滿 60 根 K 線，無法計算 60 日乖離，這項不觸發。",
            "乖離要有 60 個收盤價。資料不夠時不視為跌深，也不加分。",
        )
    ma60 = rows[-1]["ma60"]
    hit = bias <= BIAS_LIMIT
    if hit:
        summary = f"收盤比 60 日均線 {fmt_num(ma60, 2)} 低了 {fmt_num(abs(bias), 2)}%（乖離 {fmt_num(bias, 2)}%）。"
    else:
        summary = f"60 日乖離是 {fmt_num(bias, 2)}%，還沒有到 {BIAS_LIMIT:g}%。"
    explain = (
        "60 日乖離率是收盤價離開 60 日均線多遠。小於或等於 -10% 代表價格明顯在中期均線下方。"
        "均線自己也會往下彎，乖離大不保證會立刻拉回。"
    )
    return _condition("bias", name, hit, summary, explain)


def _volume_condition(rows: list[dict]) -> dict:
    name = "量縮止跌"
    if len(rows) < 20 or any(row.get("volume") is None for row in rows[-20:]):
        return _unknown(
            "volume",
            name,
            "成交量不足 20 日，無法判斷量縮，這項不觸發。",
            "這項要比較近 5 日與 20 日均量。資料不夠時不視為止跌，也不加分。",
        )
    volumes = [row["volume"] for row in rows]
    vol5 = _mean(volumes[-5:])
    vol20 = _mean(volumes[-20:])
    shrink = vol20 > 0 and vol5 < vol20 * VOL_SHRINK
    up = rows[-1]["close"] > rows[-2]["close"]
    today_low = rows[-1].get("low")
    prev_low = rows[-2].get("low")
    held = today_low is not None and prev_low is not None and today_low >= prev_low
    stopped = up or held
    hit = shrink and stopped
    vol5_lot = vol5 / 1000.0
    vol20_lot = vol20 / 1000.0
    if hit:
        how = "今日收高" if up else "最低價沒有跌破昨日低點"
        summary = (
            f"近 5 日均量 {fmt_num(vol5_lot, 0)} 張，低於 20 日均量 {fmt_num(vol20_lot, 0)} 張的七成，"
            f"而且{how}。"
        )
    elif not shrink:
        summary = (
            f"近 5 日均量 {fmt_num(vol5_lot, 0)} 張，20 日均量 {fmt_num(vol20_lot, 0)} 張，"
            "5 日均量沒有低於 20 日的七成。"
        )
    else:
        summary = (
            f"近 5 日均量已縮到 {fmt_num(vol5_lot, 0)} 張，但今日收低且最低價跌破昨日低點，還沒有止跌。"
        )
    explain = (
        "近 5 日均量小於 20 日均量的 70%，而且今日收高，或最低價沒有再破昨日低點，視為賣壓可能減輕。"
        "量縮也可能只是沒有人要接，要和其他條件一起看。"
    )
    return _condition("volume", name, hit, summary, explain)


def _foreign_condition(foreign_nets: list[float | None]) -> dict:
    name = "外資由賣轉買"
    values = [value for value in foreign_nets if value is not None]
    recent = values[-FOREIGN_SIGNAL_DAYS:]
    if len(recent) < 2:
        return _unknown(
            "foreign",
            name,
            "近幾個交易日的外資買賣超不足，這項不觸發。",
            "這項看外資（外陸資，不含外資自營商）買賣超股數。至少要兩天資料；缺資料時不當作轉買，也不加分。",
        )
    hit = recent[-1] > 0 and any(value < 0 for value in recent[:-1])
    shown = "、".join(fmt_num(value / 1000.0, 0) for value in recent)
    if hit:
        summary = f"最近外資買賣超（張，由舊到新）{shown}。最新一日轉為淨買入，而且前幾日有淨賣出。"
    elif recent[-1] <= 0:
        summary = f"最近外資買賣超（張，由舊到新）{shown}。最新一日仍不是淨買入。"
    else:
        summary = f"最近外資買賣超（張，由舊到新）{shown}。這幾日沒有出現由賣轉買。"
    explain = (
        "這裡看的是外資（不含外資自營商）的買賣超股數。前面有淨賣、最新一日變成淨買，才算方向翻轉。"
        "單日轉買，隔天仍可能再賣。"
    )
    return _condition("foreign", name, hit, summary, explain)


def _condition(cid: str, name: str, hit: bool, summary: str, explain: str) -> dict:
    return {
        "id": cid,
        "name": name,
        "hit": hit,
        "available": True,
        "summary": summary,
        "explain": explain,
    }


def _unknown(cid: str, name: str, summary: str, explain: str) -> dict:
    return {
        "id": cid,
        "name": name,
        "hit": False,
        "available": False,
        "summary": summary,
        "explain": explain,
    }


def history_note(rows: list[dict]) -> str | None:
    if not rows:
        return "沒有取得日 K。"
    if len(rows) < MA_SLOW:
        return f"目前只有 {len(rows)} 根日 K，60 日均線、乖離和 60 日新低先不計算。"
    if has_large_gap(rows):
        return "日 K 中間有較長空窗，指標只依實際取得的交易日計算。"
    return None


def round_bar(row: dict) -> dict:
    volume = row.get("volume")
    return {
        "date": row["date"],
        "open": _round(row.get("open"), 2),
        "high": _round(row.get("high"), 2),
        "low": _round(row.get("low"), 2),
        "close": _round(row.get("close"), 2),
        "volume": int(round(volume)) if volume is not None else None,
        "ma20": _round(row.get("ma20"), 2),
        "ma60": _round(row.get("ma60"), 2),
        "boll_upper": _round(row.get("boll_upper"), 2),
        "boll_mid": _round(row.get("boll_mid"), 2),
        "boll_lower": _round(row.get("boll_lower"), 2),
        "rsi": _round(row.get("rsi"), 2),
        "k": _round(row.get("k"), 2),
        "d": _round(row.get("d"), 2),
    }


PLAIN_CONDITION = {
    "rsi": "最近跌太快，可能跌過頭",
    "kd": "短線開始轉強",
    "boll": "價格掉到比平常還低的地方",
    "bias": "比近兩個月的平均價便宜很多",
    "volume": "賣的人變少，價格不再往下破",
    "foreign": "大戶（外資）開始買進",
}

DIP_RULE = "六個條件裡，至少三個同時出現，才亮綠燈；不到三個就是紅燈。"


def public_bottom(bottom: dict) -> dict:
    """主畫面只用兩個字面結果：可以抄底，或不行。"""
    score = int(bottom.get("score") or 0)
    can = score >= DIP_CUTOFF
    reasons = []
    for cond in bottom.get("conditions") or []:
        if cond.get("hit"):
            reasons.append(PLAIN_CONDITION.get(cond.get("id"), cond.get("name") or ""))
    reasons = [item for item in reasons if item]
    if can:
        reason = "出現這些情況：" + "、".join(reasons) + "。"
    elif reasons:
        reason = "有這些情況：" + "、".join(reasons) + "。還不到三個，所以不行。"
    else:
        reason = "今天沒有出現抄底的情況，所以不行。"
    return {
        "can_bottom": can,
        "label": "可以抄底" if can else "不行",
        "reason": reason,
        "reasons": reasons,
        "score": score,
        "max_score": 6,
        "cutoff": DIP_CUTOFF,
    }


def rules_payload() -> dict:
    return {
        "dip_cutoff": DIP_CUTOFF,
        "dip_rule": DIP_RULE,
        "change_pct": ALERT_CHANGE_PCT,
        "volume_ratio": ALERT_VOLUME_RATIO,
        "low_days": ALERT_LOW_DAYS,
        "rsi_below": 30,
        "kd_k_below": 20,
        "boll_window": BOLL_N,
        "boll_stdev": BOLL_K,
        "bias60_at_or_below": BIAS_LIMIT,
        "volume_shrink_ratio": VOL_SHRINK,
        "foreign_days": FOREIGN_SIGNAL_DAYS,
    }
