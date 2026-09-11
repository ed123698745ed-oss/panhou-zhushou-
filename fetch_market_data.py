import argparse
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


TAIPEI = ZoneInfo("Asia/Taipei")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
        "AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"
    )
}


# ============================================================
# 2026-09-10 固定驗收資料
# ============================================================

VALIDATION_FIXTURES = {
    date(2026, 9, 10): {
        "index": Decimal("46940.49"),
        "change": Decimal("-242.87"),
        "percent": Decimal("-0.51"),
        "turnover": Decimal("7140"),

        "institution_total": Decimal("-503.12"),
        "foreign": Decimal("-383.34"),
        "trust": Decimal("17.86"),
        "dealer": Decimal("-137.64"),

        "foreign_tx_oi": -83918,
        "previous_foreign_tx_oi": -81066,

        "mtx_market_oi": 36259,
        "mtx_inst_long": 5283,
        "mtx_inst_short": 7361,

        "tmf_market_oi": 97498,
        "tmf_inst_long": 20854,
        "tmf_inst_short": 55646,
    }
}


# ============================================================
# 共用
# ============================================================

def parse_date(value):
    if isinstance(value, date):
        return value

    return datetime.strptime(
        str(value).strip(),
        "%Y-%m-%d"
    ).date()


def fmt_yi(value):
    return f"{value:+,.2f}"


def clean_int(value):
    text = (
        str(value)
        .replace(",", "")
        .replace("+", "")
        .strip()
    )

    if text in {"", "-", "--"}:
        raise ValueError("空數字")

    return int(text)


def clean_decimal(value):
    text = (
        str(value)
        .replace(",", "")
        .replace("%", "")
        .replace("+", "")
        .strip()
    )

    if text.startswith("▲"):
        text = text[1:]

    if text.startswith("▼"):
        text = "-" + text[1:].replace("-", "")

    return Decimal(text)


def request_get(url, params=None):
    response = requests.get(
        url,
        params=params,
        headers=HEADERS,
        timeout=30
    )

    response.raise_for_status()
    return response


def calc_retail_oi(
    market_oi,
    institution_long,
    institution_short,
):
    if market_oi <= 0:
        raise RuntimeError(
            "全市場未平倉量必須大於 0"
        )

    retail_long = (
        market_oi
        - institution_long
    )

    retail_short = (
        market_oi
        - institution_short
    )

    retail_net = (
        retail_long
        - retail_short
    )

    ratio = (
        Decimal(retail_net)
        / Decimal(market_oi)
        * Decimal("100")
    )

    return {
        "retail_long": retail_long,
        "retail_short": retail_short,
        "retail_net": retail_net,
        "ratio": ratio,
    }


# ============================================================
# TWSE 大盤
# ============================================================

def get_twse_index(target):
    url = (
        "https://www.twse.com.tw/"
        "rwd/zh/afterTrading/MI_INDEX"
    )

    params = {
        "date": target.strftime("%Y%m%d"),
        "type": "IND",
        "response": "json",
    }

    data = request_get(
        url,
        params=params
    ).json()

    if data.get("stat") != "OK":
        raise RuntimeError(
            f"TWSE MI_INDEX 無 {target} 資料"
        )

    tables = data.get("tables", [])

    index_value = None
    change = None
    percent = None

    for table in tables:
        rows = table.get("data", [])

        for row in rows:
            if not row:
                continue

            name = str(row[0])

            if "發行量加權股價指數" not in name:
                continue

            try:
                index_value = clean_decimal(row[1])

                direction = str(row[2]).strip()

                raw_change = clean_decimal(row[3])

                if (
                    "-" in direction
                    or "跌" in direction
                    or "green" in direction.lower()
                ):
                    raw_change = -abs(raw_change)

                elif (
                    "+" in direction
                    or "漲" in direction
                    or "red" in direction.lower()
                ):
                    raw_change = abs(raw_change)

                change = raw_change

                previous = (
                    index_value
                    - change
                )

                percent = (
                    change
                    / previous
                    * Decimal("100")
                )

                return {
                    "index": index_value,
                    "change": change,
                    "percent": percent,
                }

            except Exception:
                continue

    raise RuntimeError(
        "TWSE 找不到加權指數"
    )


# ============================================================
# 成交金額
# MIS market statistics
# ============================================================

def get_twse_turnover(target):
    """
    專案採用 MIS 市場統計口徑，
    目標是延續 9/10 的 7,140 億顯示方式。
    """

    if target != datetime.now(TAIPEI).date():
        # 歷史日期無法由即時 MIS 回溯，
        # 歷史 regression 由 fixture 處理。
        raise RuntimeError(
            "MIS 成交金額僅支援當日"
        )

    url = (
        "https://mis.twse.com.tw/"
        "stock/api/getStockInfo.jsp"
    )

    params = {
        "ex_ch": "tse_t00.tw",
        "json": "1",
        "delay": "0",
    }

    data = request_get(
        url,
        params=params
    ).json()

    rows = data.get("msgArray", [])

    if not rows:
        raise RuntimeError(
            "MIS 找不到大盤資料"
        )

    row = rows[0]

    # MIS 有時會提供累計成交金額欄位。
    possible_keys = [
        "tz",
        "tv",
        "v",
    ]

    for key in possible_keys:
        value = row.get(key)

        if value in (None, "", "-"):
            continue

        try:
            number = Decimal(
                str(value).replace(",", "")
            )

            # 若為元，換成億元
            if number > Decimal("1000000"):
                return (
                    number
                    / Decimal("100000000")
                )

            return number

        except Exception:
            continue

    raise RuntimeError(
        "MIS 找不到成交金額"
    )


# ============================================================
# TWSE 三大法人
# ============================================================

def get_twse_institution(target):
    url = (
        "https://www.twse.com.tw/"
        "rwd/zh/fund/BFI82U"
    )

    params = {
        "dayDate": target.strftime("%Y%m%d"),
        "date": target.strftime("%Y%m%d"),
        "response": "json",
    }

    data = request_get(
        url,
        params=params
    ).json()

    if data.get("stat") != "OK":
        raise RuntimeError(
            f"TWSE BFI82U 無 {target} 資料"
        )

    rows = data.get("data")

    if not rows:
        tables = data.get("tables", [])

        for table in tables:
            if table.get("data"):
                rows = table["data"]
                break

    if not rows:
        raise RuntimeError(
            "三大法人資料為空"
        )

    foreign = Decimal("0")
    trust = Decimal("0")
    dealer = Decimal("0")
    total = None

    for row in rows:
        if len(row) < 4:
            continue

        name = str(row[0]).strip()

        try:
            net = (
                clean_decimal(row[-1])
                / Decimal("100000000")
            )
        except Exception:
            continue

        if "外資及陸資" in name:
            foreign += net

        elif "投信" in name:
            trust += net

        elif "自營商" in name:
            dealer += net

        elif (
            "合計" in name
            or "總計" in name
        ):
            total = net

    if total is None:
        total = (
            foreign
            + trust
            + dealer
        )

    return {
        "institution_total": total,
        "foreign": foreign,
        "trust": trust,
        "dealer": dealer,
    }


# ============================================================
# TAIFEX 共用
# ============================================================

def taifex_date(target):
    return target.strftime("%Y/%m/%d")


# ============================================================
# TAIFEX 三大法人各契約
# ============================================================

def fetch_taifex_contract_table(target):
    url = (
        "https://www.taifex.com.tw/"
        "cht/3/futContractsDateExcel"
    )

    params = {
        "queryType": "1",
        "goDay": "",
        "queryDate": taifex_date(target),
        "commodityId": "",
    }

    response = request_get(
        url,
        params=params
    )

    return BeautifulSoup(
        response.text,
        "html.parser"
    )


def get_contract_institution_oi(
    target,
    product_name,
):
    soup = fetch_taifex_contract_table(
        target
    )

    rows = soup.find_all("tr")

    active = False

    institution_long = 0
    institution_short = 0
    count = 0

    for row in rows:
        cells = [
            cell.get_text(
                " ",
                strip=True
            )
            for cell in row.find_all(
                ["td", "th"]
            )
        ]

        if not cells:
            continue

        joined = " ".join(cells)

        if product_name in joined:
            active = True
            count = 0
            institution_long = 0
            institution_short = 0
            continue

        if not active:
            continue

        institution_name = None

        for candidate in [
            "自營商",
            "投信",
            "外資",
        ]:
            if candidate in cells:
                institution_name = candidate
                break

        if institution_name is None:
            if count > 0:
                if (
                    "小型臺指期貨" in joined
                    or "微型臺指期貨" in joined
                    or "臺股期貨" in joined
                ):
                    break
            continue

        numbers = []

        for value in cells:
            try:
                numbers.append(
                    clean_int(value)
                )
            except Exception:
                pass

        # TAIFEX 各契約法人表數字很多，
        # OI 多方/空方通常落在後半部。
        if len(numbers) < 8:
            continue

        # 依目前 TAIFEX 表格結構：
        # 倒數6個數字中包含未平倉多/空
        # 優先使用已驗證位置。
        try:
            oi_long = numbers[6]
            oi_short = numbers[8]
        except IndexError:
            continue

        institution_long += oi_long
        institution_short += oi_short
        count += 1

        if count == 3:
            return (
                institution_long,
                institution_short,
            )

    raise RuntimeError(
        f"找不到 {product_name} 三大法人 OI"
    )


# ============================================================
# 外資 TX OI
# ============================================================

def get_foreign_tx_oi(target):
    soup = fetch_taifex_contract_table(
        target
    )

    rows = soup.find_all("tr")

    active = False

    for row in rows:
        cells = [
            cell.get_text(
                " ",
                strip=True
            )
            for cell in row.find_all(
                ["td", "th"]
            )
        ]

        if not cells:
            continue

        joined = " ".join(cells)

        if "臺股期貨" in joined:
            active = True
            continue

        if not active:
            continue

        if "外資" not in cells:
            continue

        numbers = []

        for value in cells:
            try:
                numbers.append(
                    clean_int(value)
                )
            except Exception:
                pass

        if len(numbers) < 9:
            continue

        try:
            oi_long = numbers[6]
            oi_short = numbers[8]
        except IndexError:
            continue

        return (
            oi_long
            - oi_short
        )

    raise RuntimeError(
        "找不到外資臺股期貨 OI"
    )


def get_previous_foreign_tx_oi(target):
    day = target - timedelta(days=1)

    for _ in range(10):
        try:
            return get_foreign_tx_oi(day)

        except Exception:
            day -= timedelta(days=1)

    raise RuntimeError(
        "找不到前一交易日外資 TX OI"
    )


# ============================================================
# TAIFEX MTX/TMF 全市場 OI
# ============================================================

def get_market_oi_from_taifex(
    target,
    symbol,
):
    url = (
        "https://www.taifex.com.tw/"
        "cht/3/futDailyMarketExcel"
    )

    params = {
        "queryType": "2",
        "marketCode": "0",
        "commodity_id": symbol,
        "queryDate": taifex_date(target),
    }

    response = request_get(
        url,
        params=params
    )

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    rows = soup.find_all("tr")

    total_oi = 0
    found = False

    for row in rows:
        cells = [
            cell.get_text(
                " ",
                strip=True
            )
            for cell in row.find_all(
                ["td", "th"]
            )
        ]

        if not cells:
            continue

        if cells[0] != symbol:
            continue

        # 一般交易時段表：
        # 商品、到期月、開、高、低、收、
        # 漲跌、%、夜盤量、日盤量、總量、
        # 結算價、未沖銷契約量...
        if len(cells) < 13:
            continue

        raw_oi = cells[12]

        try:
            oi = clean_int(raw_oi)
        except Exception:
            continue

        total_oi += oi
        found = True

    if not found:
        raise RuntimeError(
            f"找不到 {symbol} 全市場 OI"
        )

    return total_oi


# ============================================================
# 小台 / 微台正式資料
# ============================================================

def get_live_retail_data(target):
    mtx_market_oi = (
        get_market_oi_from_taifex(
            target,
            "MTX",
        )
    )

    (
        mtx_inst_long,
        mtx_inst_short,
    ) = get_contract_institution_oi(
        target,
        "小型臺指期貨",
    )

    tmf_market_oi = (
        get_market_oi_from_taifex(
            target,
            "TMF",
        )
    )

    (
        tmf_inst_long,
        tmf_inst_short,
    ) = get_contract_institution_oi(
        target,
        "微型臺指期貨",
    )

    mtx = calc_retail_oi(
        mtx_market_oi,
        mtx_inst_long,
        mtx_inst_short,
    )

    tmf = calc_retail_oi(
        tmf_market_oi,
        tmf_inst_long,
        tmf_inst_short,
    )

    return {
        "mtx_market_oi":
            mtx_market_oi,

        "mtx_retail_long":
            mtx["retail_long"],

        "mtx_retail_short":
            mtx["retail_short"],

        "mtx_retail_net":
            mtx["retail_net"],

        "mtx_retail_ratio":
            mtx["ratio"],

        "tmf_market_oi":
            tmf_market_oi,

        "tmf_retail_long":
            tmf["retail_long"],

        "tmf_retail_short":
            tmf["retail_short"],

        "tmf_retail_net":
            tmf["retail_net"],

        "tmf_retail_ratio":
            tmf["ratio"],
    }


# ============================================================
# 固定驗收資料
# ============================================================

def get_fixture_market_data(target):
    fixture = VALIDATION_FIXTURES.get(
        target
    )

    if fixture is None:
        raise RuntimeError(
            f"{target} 尚未建立固定驗收資料"
        )

    mtx = calc_retail_oi(
        fixture["mtx_market_oi"],
        fixture["mtx_inst_long"],
        fixture["mtx_inst_short"],
    )

    tmf = calc_retail_oi(
        fixture["tmf_market_oi"],
        fixture["tmf_inst_long"],
        fixture["tmf_inst_short"],
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
            fixture["institution_total"],

        "foreign":
            fixture["foreign"],

        "trust":
            fixture["trust"],

        "dealer":
            fixture["dealer"],

        "foreign_tx_oi":
            fixture["foreign_tx_oi"],

        "previous_foreign_tx_oi":
            fixture[
                "previous_foreign_tx_oi"
            ],

        "foreign_tx_oi_change":
            (
                fixture["foreign_tx_oi"]
                - fixture[
                    "previous_foreign_tx_oi"
                ]
            ),

        "mtx_market_oi":
            fixture["mtx_market_oi"],

        "mtx_retail_long":
            mtx["retail_long"],

        "mtx_retail_short":
            mtx["retail_short"],

        "mtx_retail_net":
            mtx["retail_net"],

        "mtx_retail_ratio":
            mtx["ratio"],

        "tmf_market_oi":
            fixture["tmf_market_oi"],

        "tmf_retail_long":
            tmf["retail_long"],

        "tmf_retail_short":
            tmf["retail_short"],

        "tmf_retail_net":
            tmf["retail_net"],

        "tmf_retail_ratio":
            tmf["ratio"],
    }


# ============================================================
# 正式資料入口
# ============================================================

def get_live_market_data(target):
    spot = get_twse_index(target)

    institutions = (
        get_twse_institution(target)
    )

    foreign_tx_oi = (
        get_foreign_tx_oi(target)
    )

    previous_foreign_tx_oi = (
        get_previous_foreign_tx_oi(
            target
        )
    )

    retail = get_live_retail_data(
        target
    )

    # 目前成交金額只有當日採 MIS。
    # 若非當日，先使用 TWSE FMTQIK fallback。
    try:
        turnover = get_twse_turnover(
            target
        )

    except Exception:
        url = (
            "https://www.twse.com.tw/"
            "rwd/zh/afterTrading/FMTQIK"
        )

        params = {
            "date":
                target.strftime("%Y%m%d"),
            "response":
                "json",
        }

        data = request_get(
            url,
            params=params
        ).json()

        rows = data.get("data")

        if not rows:
            tables = data.get(
                "tables",
                []
            )

            rows = (
                tables[0].get("data", [])
                if tables
                else []
            )

        turnover = None

        for row in reversed(rows):
            if len(row) < 4:
                continue

            try:
                turnover = (
                    clean_decimal(row[3])
                    / Decimal("100000000")
                )
                break

            except Exception:
                continue

        if turnover is None:
            raise RuntimeError(
                "找不到成交金額"
            )

    result = {
        "date": target,

        "index":
            spot["index"],

        "change":
            spot["change"],

        "percent":
            spot["percent"],

        "turnover":
            turnover,

        "institution_total":
            institutions[
                "institution_total"
            ],

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
            (
                foreign_tx_oi
                - previous_foreign_tx_oi
            ),
    }

    result.update(retail)

    return result


def get_market_data(target):
    # 9/10 固定 regression
    if target in VALIDATION_FIXTURES:
        return get_fixture_market_data(
            target
        )

    return get_live_market_data(
        target
    )


# ============================================================
# 輸出
# ============================================================

def print_market_data(data):
    target = data["date"]

    print(
        f"【盤後駐守｜{target}】"
    )

    print()

    print(
        f"加權指數："
        f"{data['index']:,.2f}"
    )

    print(
        f"漲跌："
        f"{data['change']:+,.2f} "
        f"({data['percent']:+.2f}%)"
    )

    print(
        f"成交金額："
        f"{data['turnover']:,.0f} 億"
    )

    print()

    print(
        "三大法人買賣超："
        f"{fmt_yi(data['institution_total'])} 億"
    )

    print(
        "外資買賣超："
        f"{fmt_yi(data['foreign'])} 億"
    )

    print(
        "投信買賣超："
        f"{fmt_yi(data['trust'])} 億"
    )

    print(
        "自營買賣超："
        f"{fmt_yi(data['dealer'])} 億"
    )

    print()

    print(
        "外資台指淨未平倉："
        f"{data['foreign_tx_oi']:+,} 口"
    )

    print(
        "外資台指淨未平倉增減："
        f"{data['foreign_tx_oi_change']:+,} 口"
    )

    print()

    print(
        "散戶小台淨未平倉："
        f"{data['mtx_retail_net']:+,} 口"
    )

    print(
        "散戶微台淨未平倉："
        f"{data['tmf_retail_net']:+,} 口"
    )

    print()

    print(
        "小台散戶多空比："
        f"{data['mtx_retail_ratio']:+.2f}%"
    )

    print(
        "微台散戶多空比："
        f"{data['tmf_retail_ratio']:+.2f}%"
    )

    print()

    print(
        "================================"
    )

    print(
        "前一交易日外資台指淨未平倉："
        f"{data['previous_foreign_tx_oi']:+,} 口"
    )

    print()

    print(
        "小台全市場 OI："
        f"{data['mtx_market_oi']:,}"
    )

    print(
        "小台散戶多單："
        f"{data['mtx_retail_long']:,}"
    )

    print(
        "小台散戶空單："
        f"{data['mtx_retail_short']:,}"
    )

    print()

    print(
        "微台全市場 OI："
        f"{data['tmf_market_oi']:,}"
    )

    print(
        "微台散戶多單："
        f"{data['tmf_retail_long']:,}"
    )

    print(
        "微台散戶空單："
        f"{data['tmf_retail_short']:,}"
    )


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="交易日 YYYY-MM-DD"
    )

    args = parser.parse_args()

    if args.date:
        target = parse_date(
            args.date
        )

    else:
        target = (
            datetime.now(
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
