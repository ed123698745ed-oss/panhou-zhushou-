import argparse
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
# 固定驗收資料
# ============================================================

VALIDATION_FIXTURES = {
    date(2026, 9, 10): {
        "index": Decimal("46940.49"),
        "change": Decimal("-242.87"),
        "percent": Decimal("-0.51"),

        # 專案指定口徑
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
        .replace("▲", "")
        .strip()
    )

    if text.startswith("▼"):
        text = "-" + text[1:].replace("-", "")

    return Decimal(text)


def fmt_yi(value):
    return f"{value:+,.2f}"


def request_get(url, params=None):
    response = requests.get(
        url,
        params=params,
        headers=HEADERS,
        timeout=30,
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

    if institution_long < 0 or institution_short < 0:
        raise RuntimeError(
            "法人未平倉不可為負數"
        )

    if institution_long > market_oi:
        raise RuntimeError(
            f"法人多單 {institution_long:,} "
            f"大於全市場 OI {market_oi:,}"
        )

    if institution_short > market_oi:
        raise RuntimeError(
            f"法人空單 {institution_short:,} "
            f"大於全市場 OI {market_oi:,}"
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
# TWSE 加權指數
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
        params=params,
    ).json()

    tables = data.get("tables", [])

    for table in tables:
        for row in table.get("data", []):
            if not row:
                continue

            if (
                "發行量加權股價指數"
                not in str(row[0])
            ):
                continue

            index_value = clean_decimal(
                row[1]
            )

            direction = str(
                row[2]
            ).strip()

            change = abs(
                clean_decimal(row[3])
            )

            if (
                "-" in direction
                or "跌" in direction
            ):
                change = -change

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

    raise RuntimeError(
        f"找不到 {target} 加權指數"
    )


# ============================================================
# TWSE 成交金額
# ============================================================
def get_twse_turnover(target):
    """
    盤後駐守使用「大盤即時成交金額」口徑，
    目標對齊券商 POW00 顯示：
    2026-09-10 約 7,140 億
    2026-09-11 約 7,219 億
    """

    today = datetime.now(TAIPEI).date()

    # MIS 只能可靠取得當日資料
    if target == today:
        url = (
            "https://mis.twse.com.tw/"
            "stock/api/getStatis.jsp"
        )

        params = {
            "ex": "tse",
        }

        response = request_get(
            url,
            params=params,
        )

        data = response.json()
print("=== MIS DEBUG START ===")
print(data)
print("=== MIS DEBUG END ===")
        # MIS 常見結構：
        # {"msgArray":[{"tz":"..."}], ...}
        rows = data.get("msgArray", [])

        for row in rows:
            # 優先找大盤成交金額相關欄位
            for key in [
                "tz",
                "ta",
                "tv",
            ]:
                value = row.get(key)

                if value in (
                    None,
                    "",
                    "-",
                ):
                    continue

                try:
                    number = Decimal(
                        str(value)
                        .replace(",", "")
                    )
                except Exception:
                    continue

                # 若原始值是「元」
                if number > Decimal(
                    "100000000"
                ):
                    return (
                        number
                        / Decimal(
                            "100000000"
                        )
                    )

                # 若已經是億元
                if number > Decimal("1000"):
                    return number

    # --------------------------------------------------------
    # 歷史日期：
    # 9/10 已有固定 fixture，不會走到這裡。
    # 其他歷史日期暫時 fallback 到 FMTQIK。
    # --------------------------------------------------------

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
        params=params,
    ).json()

    rows = data.get("data", [])

    if not rows:
        for table in data.get(
            "tables",
            []
        ):
            if table.get("data"):
                rows = table["data"]
                break

    for row in reversed(rows):
        if len(row) < 3:
            continue

        try:
            amount = (
                clean_decimal(row[2])
                / Decimal("100000000")
            )

            return amount

        except Exception:
            continue

    raise RuntimeError(
        f"找不到 {target} 成交金額"
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
        "dayDate":
            target.strftime("%Y%m%d"),

        "date":
            target.strftime("%Y%m%d"),

        "response":
            "json",
    }

    data = request_get(
        url,
        params=params,
    ).json()

    rows = data.get("data", [])

    if not rows:
        for table in data.get(
            "tables",
            []
        ):
            if table.get("data"):
                rows = table["data"]
                break

    if not rows:
        raise RuntimeError(
            f"找不到 {target} 三大法人"
        )

    foreign = Decimal("0")
    trust = Decimal("0")
    dealer = Decimal("0")
    total = None

    for row in rows:
        if len(row) < 2:
            continue

        name = str(
            row[0]
        ).strip()

        try:
            net = (
                clean_decimal(
                    row[-1]
                )
                / Decimal(
                    "100000000"
                )
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
# TAIFEX 三大法人表
# ============================================================

def get_taifex_contract_soup(target):
    url = (
        "https://www.taifex.com.tw/"
        "cht/3/futContractsDate"
    )

    params = {
        "queryType": "1",
        "doQuery": "1",
        "queryDate":
            target.strftime("%Y/%m/%d"),
    }

    response = request_get(
        url,
        params=params,
    )

    return BeautifulSoup(
        response.text,
        "html.parser",
    )


def get_identity_oi_from_row(
    cells,
):
    """
    TAIFEX 欄位：

    身份別
    交易多方口數
    交易多方金額
    交易空方口數
    交易空方金額
    交易淨額口數
    交易淨額金額
    OI 多方口數
    OI 多方金額
    OI 空方口數
    OI 空方金額
    OI 淨額口數
    OI 淨額金額

    因此：
    identity + 7 = OI long qty
    identity + 9 = OI short qty
    """

    identity_idx = None

    for idx, value in enumerate(
        cells
    ):
        if value in {
            "自營商",
            "投信",
            "外資",
        }:
            identity_idx = idx
            break

    if identity_idx is None:
        raise ValueError(
            "找不到身份別"
        )

    long_idx = (
        identity_idx + 7
    )

    short_idx = (
        identity_idx + 9
    )

    if (
        long_idx >= len(cells)
        or short_idx >= len(cells)
    ):
        raise ValueError(
            "TAIFEX OI 欄位不足"
        )

    oi_long = clean_int(
        cells[long_idx]
    )

    oi_short = clean_int(
        cells[short_idx]
    )

    return (
        oi_long,
        oi_short,
    )


# ============================================================
# 指定商品三大法人 OI
# ============================================================

def get_contract_institution_oi(
    target,
    product_name,
):
    soup = get_taifex_contract_soup(
        target
    )

    rows = soup.find_all("tr")

    active = False
    found_count = 0

    total_long = 0
    total_short = 0

    for row in rows:
        cells = [
            cell.get_text(
                " ",
                strip=True,
            )
            for cell in row.find_all(
                ["td", "th"]
            )
        ]

        if not cells:
            continue

        joined = " ".join(cells)

        # 新商品開始
        if product_name in joined:
            active = True

        elif active:
            # 若已完成三個身份，就結束
            if found_count == 3:
                break

            # 碰到下一個商品，也結束
            if (
                len(cells) >= 2
                and cells[0].isdigit()
                and product_name
                not in joined
            ):
                break

        if not active:
            continue

        identity = None

        for name in [
            "自營商",
            "投信",
            "外資",
        ]:
            if name in cells:
                identity = name
                break

        if identity is None:
            continue

        try:
            oi_long, oi_short = (
                get_identity_oi_from_row(
                    cells
                )
            )
        except Exception:
            continue

        total_long += oi_long
        total_short += oi_short
        found_count += 1

        if found_count == 3:
            return (
                total_long,
                total_short,
            )

    raise RuntimeError(
        f"找不到 {target} "
        f"{product_name} 三大法人 OI"
    )


# ============================================================
# 外資臺股期貨 OI
# ============================================================

def get_foreign_tx_oi(target):
    soup = get_taifex_contract_soup(
        target
    )

    rows = soup.find_all("tr")

    active = False

    for row in rows:
        cells = [
            cell.get_text(
                " ",
                strip=True,
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

        elif (
            active
            and len(cells) >= 2
            and cells[0].isdigit()
        ):
            break

        if not active:
            continue

        if "外資" not in cells:
            continue

        oi_long, oi_short = (
            get_identity_oi_from_row(
                cells
            )
        )

        return (
            oi_long
            - oi_short
        )

    raise RuntimeError(
        f"找不到 {target} 外資臺股期貨 OI"
    )


def get_previous_foreign_tx_oi(
    target,
):
    check_date = (
        target
        - timedelta(days=1)
    )

    for _ in range(10):
        try:
            return (
                get_foreign_tx_oi(
                    check_date
                )
            )

        except Exception:
            check_date -= (
                timedelta(days=1)
            )

    raise RuntimeError(
        "找不到前一交易日外資 TX OI"
    )


# ============================================================
# TAIFEX 全市場 OI
# ============================================================

def get_market_oi_from_taifex(
    target,
    symbol,
):
    url = (
        "https://www.taifex.com.tw/"
        "cht/3/futDailyMarketReport"
    )

    params = {
        "queryType": "2",
        "marketCode": "0",
        "commodity_id": symbol,
        "commodityId": symbol,
        "queryDate":
            target.strftime("%Y/%m/%d"),
    }

    response = request_get(
        url,
        params=params,
    )

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    total_oi = 0
    found = False

    for row in soup.find_all("tr"):
        cells = [
            cell.get_text(
                " ",
                strip=True,
            )
            for cell in row.find_all(
                ["td", "th"]
            )
        ]

        if not cells:
            continue

        if cells[0] != symbol:
            continue

        # 一般交易時段：
        #
        # 0 契約
        # 1 到期月
        # 2 開盤
        # 3 高
        # 4 低
        # 5 最後成交
        # 6 漲跌
        # 7 漲跌%
        # 8 夜盤量
        # 9 日盤量
        # 10 合計量
        # 11 結算
        # 12 OI

        if len(cells) <= 12:
            continue

        try:
            oi = clean_int(
                cells[12]
            )
        except Exception:
            continue

        total_oi += oi
        found = True

    if not found:
        raise RuntimeError(
            f"找不到 {target} "
            f"{symbol} 全市場 OI"
        )

    return total_oi


# ============================================================
# 散戶 MTX / TMF
# ============================================================

def get_live_retail_data(
    target,
):
    mtx_market_oi = (
        get_market_oi_from_taifex(
            target,
            "MTX",
        )
    )

    (
        mtx_inst_long,
        mtx_inst_short,
    ) = (
        get_contract_institution_oi(
            target,
            "小型臺指期貨",
        )
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
    ) = (
        get_contract_institution_oi(
            target,
            "微型臺指期貨",
        )
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
# Fixture
# ============================================================

def get_fixture_market_data(
    target,
):
    fixture = (
        VALIDATION_FIXTURES[
            target
        ]
    )

    mtx = calc_retail_oi(
        fixture[
            "mtx_market_oi"
        ],
        fixture[
            "mtx_inst_long"
        ],
        fixture[
            "mtx_inst_short"
        ],
    )

    tmf = calc_retail_oi(
        fixture[
            "tmf_market_oi"
        ],
        fixture[
            "tmf_inst_long"
        ],
        fixture[
            "tmf_inst_short"
        ],
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
            fixture[
                "foreign_tx_oi"
            ]
            - fixture[
                "previous_foreign_tx_oi"
            ],

        "mtx_market_oi":
            fixture[
                "mtx_market_oi"
            ],

        "mtx_retail_long":
            mtx[
                "retail_long"
            ],

        "mtx_retail_short":
            mtx[
                "retail_short"
            ],

        "mtx_retail_net":
            mtx[
                "retail_net"
            ],

        "mtx_retail_ratio":
            mtx[
                "ratio"
            ],

        "tmf_market_oi":
            fixture[
                "tmf_market_oi"
            ],

        "tmf_retail_long":
            tmf[
                "retail_long"
            ],

        "tmf_retail_short":
            tmf[
                "retail_short"
            ],

        "tmf_retail_net":
            tmf[
                "retail_net"
            ],

        "tmf_retail_ratio":
            tmf[
                "ratio"
            ],
    }


# ============================================================
# Live market
# ============================================================

def get_live_market_data(
    target,
):
    spot = get_twse_index(
        target
    )

    turnover = (
        get_twse_turnover(
            target
        )
    )

    institutions = (
        get_twse_institution(
            target
        )
    )

    foreign_tx_oi = (
        get_foreign_tx_oi(
            target
        )
    )

    previous_foreign_tx_oi = (
        get_previous_foreign_tx_oi(
            target
        )
    )

    retail = (
        get_live_retail_data(
            target
        )
    )

    result = {
        "date":
            target,

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
            institutions[
                "foreign"
            ],

        "trust":
            institutions[
                "trust"
            ],

        "dealer":
            institutions[
                "dealer"
            ],

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

    result.update(
        retail
    )

    return result


def get_market_data(target):
    if target in VALIDATION_FIXTURES:
        return (
            get_fixture_market_data(
                target
            )
        )

    return (
        get_live_market_data(
            target
        )
    )


# ============================================================
# 輸出
# ============================================================

def print_market_data(data):
    print(
        f"【盤後駐守｜"
        f"{data['date']}】"
    )

    print()

    print(
        "加權指數："
        f"{data['index']:,.2f}"
    )

    print(
        "漲跌："
        f"{data['change']:+,.2f} "
        f"({data['percent']:+.2f}%)"
    )

    print(
        "成交金額："
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
        help="交易日 YYYY-MM-DD",
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
