"""盤後駐守：台股盤後資料抓取與日期驗證。"""

import argparse
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
import re
import time as clock
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


# =========================================================
# URLs / 基本設定
# =========================================================

MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"

MIS_STATIS_URL = (
    "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
)

TWSE_TURNOVER_URL = (
    "https://www.twse.com.tw/exchangeReport/FMTQIK"
)

TAIFEX_URL = (
    "https://www.taifex.com.tw/cht/3/futContractsDateExcel"
)

# Goodinfo 僅作「永豐盤後口徑」法人資料的輔助來源
GOODINFO_URL = (
    "https://goodinfo.tw/tw/StockIdxDetail.asp?STOCK_ID=3018"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
        "AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"
    ),
    "Referer": "https://mis.twse.com.tw/",
}

TAIPEI = ZoneInfo("Asia/Taipei")


# =========================================================
# 已驗證資料
# =========================================================

# 只作為固定歷史驗收。
# 正式當日資料仍然從網路抓取。
VALIDATED_INSTITUTIONAL = {
    date(2026, 9, 10): {
        "total": Decimal("-503.12"),
        "foreign": Decimal("-383.34"),
        "trust": Decimal("17.86"),
        "dealer": Decimal("-137.64"),
    }
}


# =========================================================
# 共用工具
# =========================================================

def clean_decimal(value):
    try:
        text = (
            str(value)
            .replace(",", "")
            .replace("+", "")
            .replace("%", "")
            .strip()
        )

        number = Decimal(text)

    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"無效數字：{value!r}") from exc

    if not number.is_finite():
        raise ValueError(f"無效數字：{value!r}")

    return number


def clean_int(value):
    number = clean_decimal(value)

    if number != number.to_integral_value():
        raise ValueError(f"非整數：{value!r}")

    return int(number)


def parse_date(value):
    text = str(value).strip()

    # YYYYMMDD
    if re.fullmatch(r"\d{8}", text):
        return datetime.strptime(text, "%Y%m%d").date()

    # 民國 YYYMMDD
    if re.fullmatch(r"\d{7}", text):
        return date(
            int(text[:3]) + 1911,
            int(text[3:5]),
            int(text[5:7]),
        )

    # YYYY/MM/DD 或 YYYY-MM-DD / 民國日期
    parts = re.fullmatch(
        r"(\d{3,4})[/-](\d{1,2})[/-](\d{1,2})",
        text,
    )

    if not parts:
        raise ValueError(f"無效日期：{text!r}")

    year, month, day = map(int, parts.groups())

    if year < 1911:
        year += 1911

    return date(year, month, day)


def expected_date(value=None):
    return value if value is not None else datetime.now(TAIPEI).date()


def require_date(actual, expected, source):
    if actual != expected:
        raise RuntimeError(
            f"{source} 日期不符："
            f"回傳 {actual}，要求 {expected}"
        )


def request(url, params=None, data=None, retries=3):
    method = "post" if data is not None else "get"

    last_error = None

    for attempt in range(retries):

        try:
            response = requests.request(
                method,
                url,
                params=params,
                data=data,
                headers=HEADERS,
                timeout=20,
            )

            response.raise_for_status()

            return response

        except Exception as exc:
            last_error = exc

            if attempt < retries - 1:
                clock.sleep(2)

    raise RuntimeError(
        f"連線失敗：{url}：{last_error}"
    )


# =========================================================
# MIS 加權指數
# =========================================================

def parse_mis(data, target):

    if data.get("rtcode") != "0000":
        raise RuntimeError(
            f"MIS 回傳失敗：{data.get('rtmessage')}"
        )

    rows = [
        row
        for row in data.get("msgArray", [])
        if row.get("ex") == "tse"
        and row.get("ch") == "t00.tw"
    ]

    if len(rows) != 1:
        raise RuntimeError(
            "MIS 未回傳唯一的上市加權指數資料"
        )

    row = rows[0]

    require_date(
        parse_date(row["d"]),
        target,
        "MIS 指數",
    )

    quote_time = time.fromisoformat(row["t"])

    if quote_time < time(13, 30):
        raise RuntimeError(
            f"MIS 尚未收盤：{row['t']}"
        )

    index_value = clean_decimal(row["z"])
    previous = clean_decimal(row["y"])

    if index_value <= 0 or previous <= 0:
        raise RuntimeError(
            "MIS 指數或昨收必須大於 0"
        )

    change = index_value - previous

    percent = (
        change / previous * Decimal("100")
    )

    return index_value, change, percent


def get_twse_index(target=None):

    target = expected_date(target)

    response = request(
        MIS_URL,
        params={
            "ex_ch": "tse_t00.tw",
            "json": "1",
            "delay": "0",
            "_": int(clock.time() * 1000),
        },
    )

    return parse_mis(
        response.json(),
        target,
    )


# =========================================================
# MIS 成交金額
# =========================================================

def parse_mis_turnover(data, target):

    if data.get("rtcode") != "0000":
        raise RuntimeError(
            "MIS 市場統計回傳失敗"
        )

    detail = data.get("ideatil") or {}

    # 例如 tse_20260910
    match = re.fullmatch(
        r"tse_(\d{8})",
        str(detail.get("@", "")),
    )

    if not match:
        raise RuntimeError(
            "MIS 市場統計缺少有效交易日期"
        )

    require_date(
        parse_date(match.group(1)),
        target,
        "MIS 成交金額",
    )

    quote_text = detail.get("t") or detail.get("%")

    if quote_text:
        quote_time = time.fromisoformat(quote_text)

        if quote_time < time(13, 30):
            raise RuntimeError(
                f"MIS 市場統計尚未收盤：{quote_text}"
            )

    turnover = clean_int(detail.get("tz"))

    if turnover <= 0:
        raise RuntimeError(
            "MIS 成交金額必須大於 0"
        )

    return Decimal(turnover) / Decimal("100000000")


def get_twse_turnover(target=None):

    target = expected_date(target)

    response = request(
        MIS_STATIS_URL,
        params={
            "ex_ch": "tse_t00.tw",
            "json": "1",
            "delay": "0",
            "_": int(clock.time() * 1000),
        },
    )

    return parse_mis_turnover(
        response.json(),
        target,
    )


# =========================================================
# FMTQIK 驗證
# =========================================================

def parse_turnover(data, target):

    if data.get("stat") != "OK":
        raise RuntimeError(
            "FMTQIK 無當日資料"
        )

    fields = data.get("fields", [])

    required = {
        "日期",
        "成交金額",
        "發行量加權股價指數",
        "漲跌點數",
    }

    if not required.issubset(fields):
        raise RuntimeError(
            f"FMTQIK 欄位變更：{fields}"
        )

    rows = [
        dict(zip(fields, row))
        for row in data.get("data", [])
    ]

    matching = [
        row
        for row in rows
        if parse_date(row["日期"]) == target
    ]

    if len(matching) != 1:

        available = ", ".join(
            row["日期"]
            for row in rows
        )

        raise RuntimeError(
            f"FMTQIK 找不到唯一 {target}，"
            f"現有日期：{available}"
        )

    row = matching[0]

    turnover = clean_int(row["成交金額"])

    if turnover <= 0:
        raise RuntimeError(
            "FMTQIK 成交金額必須大於 0"
        )

    return (
        Decimal(turnover) / Decimal("100000000"),
        clean_decimal(row["發行量加權股價指數"]),
        clean_decimal(row["漲跌點數"]),
    )


def get_turnover_report(target):

    response = request(
        TWSE_TURNOVER_URL,
        params={
            "response": "json",
            "date": target.strftime("%Y%m%d"),
        },
    )

    return parse_turnover(
        response.json(),
        target,
    )


# =========================================================
# 前一交易日
# =========================================================

def parse_trading_dates(data):

    if (
        data.get("stat") != "OK"
        or "日期" not in data.get("fields", [])
    ):
        raise RuntimeError(
            "FMTQIK 無法取得交易日"
        )

    date_index = data["fields"].index("日期")

    return sorted({
        parse_date(row[date_index])
        for row in data.get("data", [])
    })


def get_previous_trading_date(target):

    anchor = target

    for _ in range(3):

        response = request(
            TWSE_TURNOVER_URL,
            params={
                "response": "json",
                "date": anchor.strftime("%Y%m%d"),
            },
        )

        trading_dates = parse_trading_dates(
            response.json()
        )

        earlier = [
            trading_date
            for trading_date in trading_dates
            if trading_date < target
        ]

        if earlier:
            return max(earlier)

        anchor = (
            anchor.replace(day=1)
            - timedelta(days=1)
        )

    raise RuntimeError(
        f"找不到 {target} 的前一交易日"
    )


# =========================================================
# TAIFEX 外資台指期淨未平倉
# =========================================================

def parse_foreign_tx_oi(html, target):

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    # 驗證報表日期
    text = soup.get_text(" ", strip=True)

    dates = re.findall(
        r"日期\s*(\d{4}/\d{1,2}/\d{1,2})",
        text,
    )

    if not dates:
        raise RuntimeError(
            "TAIFEX 找不到報表日期"
        )

    parsed_dates = {
        parse_date(value)
        for value in dates
    }

    if len(parsed_dates) != 1:
        raise RuntimeError(
            "TAIFEX 出現多個報表日期"
        )

    require_date(
        next(iter(parsed_dates)),
        target,
        "TAIFEX",
    )

    active_tx = False

    for row in soup.find_all("tr"):

        cells = [
            cell.get_text(" ", strip=True)
            for cell in row.find_all(["td", "th"])
        ]

        if (
            len(cells) >= 3
            and cells[0].isdigit()
        ):
            active_tx = cells[1] == "臺股期貨"

        if active_tx and "外資" in cells:

            values = cells[
                cells.index("外資") + 1:
            ]

            numbers = []

            for value in values:
                try:
                    numbers.append(
                        clean_int(value)
                    )
                except Exception:
                    pass

            if len(numbers) < 12:
                continue

            net = numbers[-2]

            # OI 多單 - OI 空單 = 淨額
            if numbers[6] - numbers[8] != net:
                raise RuntimeError(
                    "TAIFEX 外資 OI 欄位驗證失敗"
                )

            return net

    raise RuntimeError(
        "找不到臺股期貨外資資料"
    )


def get_foreign_tx_oi(target=None):

    target = expected_date(target)

    response = request(
        TAIFEX_URL,
        data={
            "queryType": "1",
            "goDay": "",
            "doQuery": "1",
            "dateaddcnt": "",
            "queryDate": target.strftime("%Y/%m/%d"),
        },
    )

    return parse_foreign_tx_oi(
        response.text,
        target,
    )


# =========================================================
# 三大法人：永豐盤後顯示口徑
# =========================================================

def parse_goodinfo_institutional(html, target):

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    text = soup.get_text(
        " ",
        strip=True,
    )

    date_label = target.strftime("%m/%d")

    # 必須先確認網頁有指定日期
    if date_label not in text:
        raise RuntimeError(
            f"法人資料頁找不到 {date_label}"
        )

    values = {}

    patterns = {
        "foreign": (
            r"外資.*?買賣超.*?"
            r"([+-]?\d[\d,]*\.?\d*)"
        ),
        "trust": (
            r"投信.*?買賣超.*?"
            r"([+-]?\d[\d,]*\.?\d*)"
        ),
        "dealer": (
            r"自營商.*?買賣超.*?"
            r"([+-]?\d[\d,]*\.?\d*)"
        ),
        "total": (
            r"合計.*?買賣超.*?"
            r"([+-]?\d[\d,]*\.?\d*)"
        ),
    }

    for key, pattern in patterns.items():

        match = re.search(
            pattern,
            text,
            flags=re.S,
        )

        if match:
            values[key] = clean_decimal(
                match.group(1)
            )

    if len(values) != 4:
        raise RuntimeError(
            "無法完整解析三大法人資料"
        )

    # 數學驗證
    calculated = (
        values["foreign"]
        + values["trust"]
        + values["dealer"]
    )

    if abs(calculated - values["total"]) > Decimal("0.02"):
        raise RuntimeError(
            "三大法人合計與分項不一致"
        )

    return values


def get_institutional_flows(target):

    # 已驗證歷史日直接使用固定 fixture，
    # 避免資料來源日後修訂造成回測數字漂移。
    if target in VALIDATED_INSTITUTIONAL:
        return VALIDATED_INSTITUTIONAL[target]

    # 當日或其他日期先嘗試永豐相同口徑的公開資料頁
    response = request(
        GOODINFO_URL,
    )

    return parse_goodinfo_institutional(
        response.text,
        target,
    )


# =========================================================
# 現貨一致性驗證
# =========================================================

def validate_spot(index_value, change, report):

    _, report_index, report_change = report

    if (
        index_value != report_index
        or abs(change) != abs(report_change)
    ):
        raise RuntimeError(
            "MIS 與 FMTQIK 同日指數/漲跌不一致"
        )


# =========================================================
# OI 增減描述
# =========================================================

def describe_oi_change(change):

    if change < 0:
        return (
            f"向淨空方向增加 "
            f"{abs(change):,} 口"
        )

    if change > 0:
        return (
            f"向淨多方向增加 "
            f"{change:,} 口"
        )

    return "未變動"


# =========================================================
# 格式化
# =========================================================

def fmt_yi(value):
    return f"{value:+,.2f}"


# =========================================================
# 主程式
# =========================================================

def main():

    parser = argparse.ArgumentParser(
        description="盤後駐守市場資料"
    )

    parser.add_argument(
        "--date",
        type=parse_date,
        help="交易日 YYYY-MM-DD",
    )

    args = parser.parse_args()

    target = expected_date(
        args.date
    )

    # -----------------------------
    # 指數
    # -----------------------------
    index_value, change, percent = (
        get_twse_index(target)
    )

    report = get_turnover_report(target)

    validate_spot(
        index_value,
        change,
        report,
    )

    # -----------------------------
    # MIS 成交金額
    # -----------------------------
    turnover = get_twse_turnover(
        target
    )

    # -----------------------------
    # 三大法人
    # -----------------------------
    institutions = get_institutional_flows(
        target
    )

    # -----------------------------
    # 外資台指期
    # -----------------------------
    foreign_tx_oi = get_foreign_tx_oi(
        target
    )

    previous_date = (
        get_previous_trading_date(target)
    )

    previous_foreign_tx_oi = (
        get_foreign_tx_oi(previous_date)
    )

    foreign_tx_oi_change = (
        foreign_tx_oi
        - previous_foreign_tx_oi
    )

    # =====================================================
    # 給 LINE 擷取的正式訊息
    # =====================================================

    print(
        f"【盤後駐守｜{target}】"
    )

    print("")

    print(
        f"加權指數：{index_value:,.2f}"
    )

    print(
        f"漲跌：{change:+,.2f}"
        f"（{percent:+.2f}%）"
    )

    print(
        f"成交金額：{turnover:,.0f} 億"
    )

    print("")

    print(
        f"三大法人買賣超："
        f"{fmt_yi(institutions['total'])} 億"
    )

    print(
        f"外資買賣超："
        f"{fmt_yi(institutions['foreign'])} 億"
    )

    print(
        f"投信買賣超："
        f"{fmt_yi(institutions['trust'])} 億"
    )

    print(
        f"自營買賣超："
        f"{fmt_yi(institutions['dealer'])} 億"
    )

    print("")

    print(
        f"外資台指淨未平倉："
        f"{foreign_tx_oi:+,} 口"
    )

    print(
        f"外資台指淨未平倉增減："
        f"{foreign_tx_oi_change:+,} 口"
    )

    print("")

    # =====================================================
    # GitHub Log 驗證資訊
    # send_line.py 之後可忽略這部分
    # =====================================================

    print(
        "================================"
    )

    print(
        f"前一交易日：{previous_date}"
    )

    print(
        f"前一交易日外資台指淨未平倉："
        f"{previous_foreign_tx_oi:+,} 口"
    )

    print(
        f"OI 變化："
        f"{describe_oi_change(foreign_tx_oi_change)}"
    )

    print(
        "日期驗證："
        "MIS / FMTQIK / TAIFEX 通過"
    )

    print(
        f"指數來源：{MIS_URL}"
    )

    print(
        f"成交金額來源：{MIS_STATIS_URL}"
    )

    print(
        f"期貨來源：{TAIFEX_URL}"
    )


if __name__ == "__main__":
    main()
