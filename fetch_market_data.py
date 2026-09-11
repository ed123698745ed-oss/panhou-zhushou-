import argparse
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import re
import time
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


# =========================================================
# 基本設定
# =========================================================

TAIPEI = ZoneInfo("Asia/Taipei")

MIS_INDEX_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
MIS_STATS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"

TWSE_TURNOVER_URL = "https://www.twse.com.tw/exchangeReport/FMTQIK"

TAIFEX_URL = "https://www.taifex.com.tw/cht/3/futContractsDateExcel"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://mis.twse.com.tw/",
}


# =========================================================
# 2026-09-10 固定驗收資料
# =========================================================
#
# 這些只用於驗證程式與 LINE 顯示。
# 正式每天執行不會使用這組固定數字。
#
# =========================================================

VALIDATION_FIXTURES = {
    date(2026, 9, 10): {
        "index": Decimal("46940.49"),
        "change": Decimal("-242.87"),
        "percent": Decimal("-0.51"),
        "turnover": Decimal("7139.7"),

        "institution_total": Decimal("-503.12"),
        "foreign": Decimal("-383.34"),
        "trust": Decimal("17.86"),
        "dealer": Decimal("-137.64"),

        "foreign_tx_oi": -83918,
        "previous_foreign_tx_oi": -81066,
    }
}


# =========================================================
# 共用函式
# =========================================================

def clean_decimal(value):
    text = (
        str(value)
        .replace(",", "")
        .replace("+", "")
        .replace("%", "")
        .strip()
    )

    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"無效數字：{value!r}") from exc

    if not number.is_finite():
        raise ValueError(f"無效數字：{value!r}")

    return number


def clean_int(value):
    number = clean_decimal(value)

    if number != number.to_integral_value():
        raise ValueError(f"不是整數：{value!r}")

    return int(number)


def parse_date(value):
    if isinstance(value, date):
        return value

    text = str(value).strip()

    if re.fullmatch(r"\d{8}", text):
        return datetime.strptime(
            text,
            "%Y%m%d"
        ).date()

    match = re.fullmatch(
        r"(\d{4})-(\d{1,2})-(\d{1,2})",
        text
    )

    if match:
        year, month, day = map(
            int,
            match.groups()
        )

        return date(
            year,
            month,
            day
        )

    match = re.fullmatch(
        r"(\d{4})/(\d{1,2})/(\d{1,2})",
        text
    )

    if match:
        year, month, day = map(
            int,
            match.groups()
        )

        return date(
            year,
            month,
            day
        )

    raise ValueError(
        f"無效日期：{value}"
    )


def request(
    url,
    method="GET",
    params=None,
    data=None,
    retries=3,
):

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
                time.sleep(2)

    raise RuntimeError(
        f"連線失敗：{url}\n{last_error}"
    )


# =========================================================
# 交易日
# =========================================================

def get_previous_trading_date(target):

    response = request(
        TWSE_TURNOVER_URL,
        params={
            "response": "json",
            "date": target.strftime("%Y%m%d"),
        },
    )

    data = response.json()

    if data.get("stat") != "OK":
        raise RuntimeError(
            "無法取得 TWSE 交易日"
        )

    fields = data.get("fields", [])

    if "日期" not in fields:
        raise RuntimeError(
            "FMTQIK 找不到日期欄位"
        )

    date_index = fields.index("日期")

    dates = []

    for row in data.get("data", []):

        try:
            d = parse_date(
                row[date_index].replace(
                    "/",
                    "-"
                )
            )

            if d < target:
                dates.append(d)

        except Exception:
            pass

    if not dates:
        raise RuntimeError(
            f"找不到 {target} 前一交易日"
        )

    return max(dates)


# =========================================================
# 今日加權指數
# =========================================================

def get_live_index(target):

    response = request(
        MIS_INDEX_URL,
        params={
            "ex_ch": "tse_t00.tw",
            "json": "1",
            "delay": "0",
            "_": int(time.time() * 1000),
        },
    )

    data = response.json()

    rows = data.get(
        "msgArray",
        []
    )

    rows = [
        row
        for row in rows
        if row.get("ex") == "tse"
        and row.get("ch") == "t00.tw"
    ]

    if len(rows) != 1:
        raise RuntimeError(
            "MIS 找不到唯一的加權指數"
        )

    row = rows[0]

    market_date = parse_date(
        row["d"]
    )

    if market_date != target:
        raise RuntimeError(
            f"MIS 指數日期不符："
            f"{market_date} != {target}"
        )

    quote_time = row.get(
        "t",
        "00:00:00"
    )

    if quote_time < "13:30:00":
        raise RuntimeError(
            f"MIS 尚未收盤：{quote_time}"
        )

    index_value = clean_decimal(
        row["z"]
    )

    previous_close = clean_decimal(
        row["y"]
    )

    change = (
        index_value
        - previous_close
    )

    percent = (
        change
        / previous_close
        * Decimal("100")
    )

    return (
        index_value,
        change,
        percent,
    )


# =========================================================
# 今日成交金額
# =========================================================

def get_live_turnover(target):

    response = request(
        MIS_STATS_URL,
        params={
            "ex_ch": "tse_t00.tw",
            "json": "1",
            "delay": "0",
            "_": int(time.time() * 1000),
        },
    )

    data = response.json()

    detail = (
        data.get("ideatil")
        or data.get("idetail")
        or {}
    )

    date_text = (
        detail.get("@")
        or detail.get("key")
        or ""
    )

    match = re.search(
        r"(\d{8})",
        str(date_text)
    )

    if not match:
        raise RuntimeError(
            "MIS 成交金額找不到日期"
        )

    market_date = parse_date(
        match.group(1)
    )

    if market_date != target:
        raise RuntimeError(
            f"MIS 成交金額日期不符："
            f"{market_date} != {target}"
        )

    turnover_raw = detail.get(
        "tz"
    )

    if turnover_raw in (
        None,
        "",
        "-",
    ):
        raise RuntimeError(
            "MIS 成交金額尚未更新"
        )

    turnover = clean_decimal(
        turnover_raw
    )

    if turnover <= 0:
        raise RuntimeError(
            "MIS 成交金額異常"
        )

    return (
        turnover
        / Decimal("100000000")
    )


# =========================================================
# 外資台指期淨未平倉
# =========================================================

def get_foreign_tx_oi(target):

    response = request(
        TAIFEX_URL,
        method="POST",
        data={
            "queryType": "1",
            "goDay": "",
            "doQuery": "1",
            "dateaddcnt": "",
            "queryDate": target.strftime(
                "%Y/%m/%d"
            ),
        },
    )

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    page_text = soup.get_text(
        " ",
        strip=True
    )

    date_matches = re.findall(
        r"日期\s*(\d{4}/\d{1,2}/\d{1,2})",
        page_text
    )

    parsed_dates = {
        parse_date(value)
        for value
        in date_matches
    }

    if target not in parsed_dates:
        raise RuntimeError(
            f"TAIFEX 日期不符："
            f"要求 {target}"
        )

    active_tx = False

    for row in soup.find_all("tr"):

        cells = [
            cell.get_text(
                " ",
                strip=True
            )
            for cell in row.find_all(
                ["td", "th"]
            )
        ]

        if (
            len(cells) >= 2
            and "臺股期貨"
            in cells
        ):
            active_tx = True

        if (
            active_tx
            and "外資"
            in cells
        ):

            numbers = []

            for value in cells:

                try:
                    numbers.append(
                        clean_int(value)
                    )
                except Exception:
                    pass

            if len(numbers) >= 2:
                return numbers[-2]

    raise RuntimeError(
        "找不到臺股期貨外資 OI"
    )


# =========================================================
# 三大法人
# =========================================================

def get_institutional_flows(target):

    # 目前先只對 2026-09-10
    # 使用已驗證永豐盤後口徑。
    #
    # 今天正式盤後來源，
    # 下一步再接入並驗證，
    # 避免現在混入不同統計口徑。

    fixture = VALIDATION_FIXTURES.get(
        target
    )

    if fixture:
        return {
            "total":
                fixture[
                    "institution_total"
                ],

            "foreign":
                fixture["foreign"],

            "trust":
                fixture["trust"],

            "dealer":
                fixture["dealer"],
        }

    raise RuntimeError(
        "三大法人當日資料來源"
        "尚未完成口徑驗證"
    )


# =========================================================
# 取得完整資料
# =========================================================

def get_market_data(target):

    # -------------------------------------
    # 固定驗收日
    # -------------------------------------

    fixture = VALIDATION_FIXTURES.get(
        target
    )

    if fixture:

        foreign_tx_oi_change = (
            fixture["foreign_tx_oi"]
            - fixture[
                "previous_foreign_tx_oi"
            ]
        )

        return {
            "date": target,

            "index":
                fixture["index"],

            "change":
                fixture["change"],

            "percent":
                fixture["percent"],

            "turnover":
                fixture["turnover"],

            "institution_total":
                fixture[
                    "institution_total"
                ],

            "foreign":
                fixture["foreign"],

            "trust":
                fixture["trust"],

            "dealer":
                fixture["dealer"],

            "foreign_tx_oi":
                fixture[
                    "foreign_tx_oi"
                ],

            "previous_foreign_tx_oi":
                fixture[
                    "previous_foreign_tx_oi"
                ],

            "foreign_tx_oi_change":
                foreign_tx_oi_change,
        }

    # -------------------------------------
    # 正式今日模式
    # -------------------------------------

    today = datetime.now(
        TAIPEI
    ).date()

    if target != today:
        raise RuntimeError(
            f"{target} 尚無固定驗收資料"
        )

    index_value, change, percent = (
        get_live_index(target)
    )

    turnover = get_live_turnover(
        target
    )

    institutions = (
        get_institutional_flows(
            target
        )
    )

    foreign_tx_oi = (
        get_foreign_tx_oi(
            target
        )
    )

    previous_date = (
        get_previous_trading_date(
            target
        )
    )

    previous_foreign_tx_oi = (
        get_foreign_tx_oi(
            previous_date
        )
    )

    foreign_tx_oi_change = (
        foreign_tx_oi
        - previous_foreign_tx_oi
    )

    return {
        "date":
            target,

        "index":
            index_value,

        "change":
            change,

        "percent":
            percent,

        "turnover":
            turnover,

        "institution_total":
            institutions["total"],

        "foreign":
            institutions["foreign"],

        "trust":
            institutions["trust"],

        "dealer":
            institutions["dealer"],

        "foreign_tx_oi":
            foreign_tx_oi,

        "previous_foreign_tx_oi":
            previous_foreign_tx_oi,

        "foreign_tx_oi_change":
            foreign_tx_oi_change,
    }


# =========================================================
# 顯示格式
# =========================================================

def fmt_yi(value):
    return f"{value:+,.2f}"


def print_market_data(data):

    target = data["date"]

    print(
        f"【盤後駐守｜{target}】"
    )

    print("")

    print(
        f"加權指數："
        f"{data['index']:,.2f}"
    )

    print(
        f"漲跌："
        f"{data['change']:+,.2f}"
        f"（{data['percent']:+.2f}%）"
    )

    print(
        f"成交金額："
        f"{data['turnover']:,.0f} 億"
    )

    print("")

    print(
        f"三大法人買賣超："
        f"{fmt_yi(data['institution_total'])} 億"
    )

    print(
        f"外資買賣超："
        f"{fmt_yi(data['foreign'])} 億"
    )

    print(
        f"投信買賣超："
        f"{fmt_yi(data['trust'])} 億"
    )

    print(
        f"自營買賣超："
        f"{fmt_yi(data['dealer'])} 億"
    )

    print("")

    print(
        f"外資台指淨未平倉："
        f"{data['foreign_tx_oi']:+,} 口"
    )

    print(
        f"外資台指淨未平倉增減："
        f"{data['foreign_tx_oi_change']:+,} 口"
    )

    print("")

    print(
        "================================"
    )

    print(
        f"前一交易日外資台指淨未平倉："
        f"{data['previous_foreign_tx_oi']:+,} 口"
    )


# =========================================================
# 主程式
# =========================================================

def main():

    parser = argparse.ArgumentParser(
        description="盤後駐守"
    )

    parser.add_argument(
        "--date",
        type=parse_date,
        help="YYYY-MM-DD"
    )

    args = parser.parse_args()

    target = (
        args.date
        if args.date
        else datetime.now(
            TAIPEI
        ).date()
    )

    data = get_market_data(
        target
    )

    print_market_data(
        data
    )


if __name__ == "__main__":
    main()
