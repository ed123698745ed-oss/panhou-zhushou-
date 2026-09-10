"""Fetch dated closing data for inspection only; never send LINE messages."""
import argparse
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
import re
import time as clock
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
MIS_STATIS_URL = "https://mis.twse.com.tw/stock/api/getStatis.jsp"
TWSE_TURNOVER_URL = "https://www.twse.com.tw/exchangeReport/FMTQIK"
TAIFEX_URL = "https://www.taifex.com.tw/cht/3/futContractsDate"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://mis.twse.com.tw/"}
TAIPEI = ZoneInfo("Asia/Taipei")


def clean_decimal(value):
    try:
        number = Decimal(str(value).replace(",", "").replace("%", "").strip())
    except InvalidOperation as exc:
        raise ValueError(f"無效數字：{value!r}") from exc
    if not number.is_finite():
        raise ValueError(f"無效數字：{value!r}")
    return number


def clean_int(value):
    number = clean_decimal(value)
    if number != number.to_integral_value():
        raise ValueError(f"非整數：{value!r}")
    return int(number)


def clean_float(value):
    return float(clean_decimal(value))


def parse_date(value):
    text = str(value).strip()
    if re.fullmatch(r"\d{8}", text):
        return datetime.strptime(text, "%Y%m%d").date()
    if re.fullmatch(r"\d{7}", text):
        return date(int(text[:3]) + 1911, int(text[3:5]), int(text[5:]))
    parts = re.fullmatch(r"(\d{3,4})[/-](\d{1,2})[/-](\d{1,2})", text)
    if not parts:
        raise ValueError(f"無效日期：{text!r}")
    year, month, day = map(int, parts.groups())
    return date(year + 1911 if year < 1911 else year, month, day)


def expected_date(value=None):
    return value if value is not None else datetime.now(TAIPEI).date()


def require_date(actual, expected, source):
    if actual != expected:
        raise RuntimeError(f"{source} 日期不符：回傳 {actual}，要求 {expected}；停止使用過期資料")


def request(url, params=None, data=None):
    method = "post" if data is not None else "get"
    response = requests.request(
        method, url, params=params, data=data, headers=HEADERS, timeout=20
    )
    response.raise_for_status()
    return response


def parse_mis(data, target):
    if data.get("rtcode") != "0000":
        raise RuntimeError(f"MIS 回傳失敗：{data.get('rtmessage')}")
    rows = [r for r in data.get("msgArray", [])
            if r.get("ex") == "tse" and r.get("c") == "t00"]
    if len(rows) != 1:
        raise RuntimeError("MIS 未回傳唯一的上市加權指數")
    row = rows[0]
    require_date(parse_date(row["d"]), target, "MIS 指數")
    quote_time = time.fromisoformat(row["t"])
    if quote_time < time(13, 30):
        raise RuntimeError(f"MIS 尚未收盤：{row['t']}")
    index, previous = clean_decimal(row["z"]), clean_decimal(row["y"])
    if index <= 0 or previous <= 0:
        raise RuntimeError("MIS 指數或昨收必須大於零")
    change = index - previous
    return index, change, change / previous * 100


def get_twse_index(target=None):
    return parse_mis(request(MIS_URL, {"ex_ch": "tse_t00.tw", "json": 1,
                     "delay": 0, "_": int(clock.time() * 1000)}).json(), expected_date(target))


def parse_mis_turnover(data, target):
    if data.get("rtcode") != "0000":
        raise RuntimeError(f"MIS 市場統計回傳失敗：{data.get('rtmessage')}")
    detail = data.get("detail") or {}
    match = re.fullmatch(r"tse_(\d{8})", str(detail.get("key", "")))
    if not match:
        raise RuntimeError("MIS 市場統計缺少有效日期")
    require_date(parse_date(match.group(1)), target, "MIS 成交金額")
    quote_time = time.fromisoformat(detail.get("%", "00:00:00"))
    if quote_time < time(13, 30):
        raise RuntimeError(f"MIS 市場統計尚未收盤：{detail.get('%')}")
    turnover = clean_int(detail.get("tz"))
    if turnover <= 0:
        raise RuntimeError("MIS 成交金額必須大於零")
    return Decimal(turnover) / 100000000


def get_twse_turnover(target=None):
    target = expected_date(target)
    data = request(MIS_STATIS_URL, {
        "ex": "tse", "delay": 0, "_": int(clock.time() * 1000)
    }).json()
    return parse_mis_turnover(data, target)


def parse_turnover(data, target):
    if data.get("stat") != "OK":
        raise RuntimeError(f"FMTQIK 無當日資料：{data.get('stat')}")
    # The outer date can echo the request; only the actual row proves its date.
    fields = data.get("fields", [])
    required = {"日期", "成交金額", "發行量加權股價指數", "漲跌點數"}
    if not required.issubset(fields):
        raise RuntimeError(f"FMTQIK 欄位變更：{fields}")
    rows = [dict(zip(fields, row)) for row in data.get("data", [])]
    matching = [row for row in rows if parse_date(row["日期"]) == target]
    if len(matching) != 1:
        available = ", ".join(row["日期"] for row in rows)
        raise RuntimeError(f"FMTQIK 找不到唯一的 {target} 資料；回傳日期：{available}")
    row = matching[0]
    turnover = clean_int(row["成交金額"])
    if turnover <= 0:
        raise RuntimeError("FMTQIK 成交金額必須大於零")
    return (Decimal(turnover) / 100000000,
            clean_decimal(row["發行量加權股價指數"]), clean_decimal(row["漲跌點數"]))


def get_turnover_report(target):
    return parse_turnover(request(TWSE_TURNOVER_URL,
                          {"response": "json", "date": target.strftime("%Y%m%d")}).json(), target)


def parse_trading_dates(data):
    if data.get("stat") != "OK" or "日期" not in data.get("fields", []):
        raise RuntimeError("FMTQIK 無法取得交易日")
    date_index = data["fields"].index("日期")
    return sorted({parse_date(row[date_index]) for row in data.get("data", [])})


def get_previous_trading_date(target):
    anchor = target
    for _ in range(3):
        data = request(TWSE_TURNOVER_URL, {
            "response": "json", "date": anchor.strftime("%Y%m%d")
        }).json()
        earlier = [trading_date for trading_date in parse_trading_dates(data)
                   if trading_date < target]
        if earlier:
            return max(earlier)
        anchor = anchor.replace(day=1) - timedelta(days=1)
    raise RuntimeError(f"找不到 {target} 的前一交易日")


def parse_foreign_tx_oi(html, target):
    soup = BeautifulSoup(html, "html.parser")
    # Validate the returned report heading, not a requested date or input field.
    dates = re.findall(r"日期\s*(\d{4}/\d{1,2}/\d{1,2})", soup.get_text(" ", strip=True))
    if len(set(dates)) != 1:
        raise RuntimeError("TAIFEX 找不到唯一的報表日期")
    require_date(parse_date(dates[0]), target, "TAIFEX")
    active_tx = False
    for row in soup.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]
        # A numbered row starts a new product, ending any previous TX block.
        if len(cells) >= 3 and cells[0].isdigit():
            active_tx = cells[1] == "臺股期貨"
        if active_tx and "外資" in cells:
            values = cells[cells.index("外資") + 1:]
            if len(values) != 12:
                raise RuntimeError("TAIFEX 外資欄位數改變")
            numbers = [clean_int(value) for value in values]
            # Keep the verified net-open-interest field; also check long - short.
            net = numbers[-2]
            if numbers[6] - numbers[8] != net:
                raise RuntimeError("TAIFEX 未平倉多方減空方與淨額不符")
            return net
    raise RuntimeError("找不到臺股期貨外資資料")


def get_foreign_tx_oi(target=None):
    target = expected_date(target)
    response = request(TAIFEX_URL, data={
        "queryType": "1",
        "goDay": "",
        "doQuery": "1",
        "dateaddcnt": "",
        "queryDate": target.strftime("%Y/%m/%d"),
    })
    return parse_foreign_tx_oi(response.text, target)


def validate_spot(index, change, report):
    _, report_index, report_change = report
    if index != report_index or change != report_change:
        raise RuntimeError("MIS 與 FMTQIK 同日收盤指數或漲跌不一致")


def describe_oi_change(change):
    if change < 0:
        return f"向淨空方向增加 {abs(change):,} 口"
    if change > 0:
        return f"向淨多方向增加 {change:,} 口"
    return "未變動"


def main():
    parser = argparse.ArgumentParser(description="市場資料核對；不發送 LINE")
    parser.add_argument("--date", type=date.fromisoformat, default=None,
                        help="要求的交易日 YYYY-MM-DD，預設台北今天；MIS/期貨僅支援目前報表")
    target = expected_date(parser.parse_args().date)
    index, change, percent = get_twse_index(target)
    validate_spot(index, change, get_turnover_report(target))
    turnover = get_twse_turnover(target)
    foreign_tx_oi = get_foreign_tx_oi(target)
    previous_date = get_previous_trading_date(target)
    previous_foreign_tx_oi = get_foreign_tx_oi(previous_date)
    foreign_tx_oi_change = foreign_tx_oi - previous_foreign_tx_oi
    print(f"【盤後駐守｜市場資料核對】{target}")
    print(f"加權指數：{index:,.2f}")
    print(f"漲跌點數：{change:+,.2f}")
    print(f"漲跌幅（由昨收計算）：{percent:+.2f}%")
    print(f"加權指數成交金額（MIS）：{turnover:,.1f} 億")
    print(f"外資台指期淨未平倉：{foreign_tx_oi:,} 口")
    print(
        f"較前一交易日（{previous_date}）：{foreign_tx_oi_change:+,} 口"
        f"（{describe_oi_change(foreign_tx_oi_change)}）"
    )
    print(f"前一交易日外資台指期淨未平倉：{previous_foreign_tx_oi:,} 口")
    print(f"日期驗證通過：MIS／FMTQIK／TAIFEX 當日均為 {target}")
    print(f"前一交易日驗證通過：FMTQIK／TAIFEX 均為 {previous_date}")
    print("指數及漲跌交叉核對：MIS 與 FMTQIK 一致")
    print(f"指數來源：{MIS_URL}")
    print(f"成交金額來源：{MIS_STATIS_URL}")
    print(f"期貨來源：{TAIFEX_URL}")


if __name__ == "__main__":
    main()
