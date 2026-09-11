import argparse
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo


TAIPEI = ZoneInfo("Asia/Taipei")


# =========================================================
# 2026-09-10 固定驗收資料
# =========================================================
#
# 這份 fixture 只用於固定驗收與 LINE 格式測試。
# 正式每日盤後模式之後再接即時官方來源。
#
# =========================================================

VALIDATION_FIXTURES = {
    date(2026, 9, 10): {
        # 大盤
        "index": Decimal("46940.49"),
        "change": Decimal("-242.87"),
        "percent": Decimal("-0.51"),
        "turnover": Decimal("7139.7"),

        # 三大法人
        "institution_total": Decimal("-503.12"),
        "foreign": Decimal("-383.34"),
        "trust": Decimal("17.86"),
        "dealer": Decimal("-137.64"),

        # 外資台指期
        "foreign_tx_oi": -83918,
        "previous_foreign_tx_oi": -81066,

        # 小台 MTX
        "mtx_market_oi": 36259,
        "mtx_inst_long": 5283,
        "mtx_inst_short": 7361,

        # 微台 TMF
        "tmf_market_oi": 97498,
        "tmf_inst_long": 20854,
        "tmf_inst_short": 55646,
    }
}


# =========================================================
# 共用
# =========================================================

def parse_date(value):
    if isinstance(value, date):
        return value

    text = str(value).strip()

    try:
        return datetime.strptime(
            text,
            "%Y-%m-%d"
        ).date()

    except ValueError as exc:
        raise ValueError(
            f"日期格式錯誤：{value}，請使用 YYYY-MM-DD"
        ) from exc


def fmt_yi(value):
    return f"{value:+,.2f}"


def calc_retail_oi(
    market_oi,
    institution_long,
    institution_short,
):
    """
    散戶多單 = 全市場 OI - 三大法人多單
    散戶空單 = 全市場 OI - 三大法人空單

    散戶淨未平倉 = 散戶多單 - 散戶空單

    散戶多空比 =
        散戶淨未平倉 / 全市場 OI * 100
    """

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

def get_market_oi_from_taifex(symbol):
    """
    從 TAIFEX 一般交易時段行情表，
    加總指定商品所有到期月份的未沖銷契約量。
    symbol: "MTX" 或 "TMF"
    """

    url = (
        "https://www.taifex.com.tw/cht/3/"
        f"futDailyMarketExcel?commodity_id={symbol}"
    )

    response = requests.get(
        url,
        headers={
            "User-Agent": "Mozilla/5.0"
        },
        timeout=20
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    total_oi = 0
    found = False

    for row in soup.find_all("tr"):

        cells = [
            cell.get_text(" ", strip=True)
            for cell in row.find_all(["td", "th"])
        ]

        if not cells:
            continue

        # 第一欄必須是 MTX / TMF
        if cells[0] != symbol:
            continue

        # 一般交易時段欄位中，
        # 「未沖銷契約量」通常位於倒數幾欄，
        # 這裡從整列找可解析數字並取 OI 欄位。
        #
        # 正式版下一步會再加欄名定位驗證。
        try:
            oi = int(
                cells[12]
                .replace(",", "")
                .strip()
            )
        except Exception:
            continue

        total_oi += oi
        found = True

    if not found:
        raise RuntimeError(
            f"找不到 {symbol} 全市場未沖銷契約量"
        )

    return total_oi

def get_institution_oi_from_taifex(product_name):
    """
    抓 TAIFEX 三大法人指定契約的
    未平倉多單 / 空單合計。

    product_name:
    "小型臺指期貨"
    "微型臺指期貨"
    """

    url = (
        "https://www.taifex.com.tw/cht/3/"
        "futContractsDateExcel"
    )

    response = requests.get(
        url,
        headers={
            "User-Agent": "Mozilla/5.0"
        },
        timeout=20
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    rows = soup.find_all("tr")

    active = False

    institution_long = 0
    institution_short = 0
    count = 0

    for row in rows:

        cells = [
            cell.get_text(" ", strip=True)
            for cell in row.find_all(["td", "th"])
        ]

        if not cells:
            continue

        if product_name in cells:
            active = True

        if active and any(
            name in cells
            for name in ["自營商", "投信", "外資"]
        ):

            numbers = []

            for value in cells:
                try:
                    numbers.append(
                        int(
                            value
                            .replace(",", "")
                            .strip()
                        )
                    )
                except Exception:
                    pass

            if len(numbers) < 12:
                continue

            # 未平倉多單 / 空單
            institution_long += numbers[6]
            institution_short += numbers[8]

            count += 1

            if count == 3:
                return (
                    institution_long,
                    institution_short
                )

    raise RuntimeError(
        f"找不到 {product_name} 三大法人 OI"
    )
# =========================================================
# 固定驗收資料
# =========================================================

def get_fixture_market_data(target):
    fixture = VALIDATION_FIXTURES.get(
        target
    )

    if fixture is None:
        raise RuntimeError(
            f"{target} 尚未建立固定驗收資料"
        )

    # -------------------------------------
    # 外資台指 OI 增減
    # -------------------------------------

    foreign_tx_oi_change = (
        fixture["foreign_tx_oi"]
        - fixture["previous_foreign_tx_oi"]
    )

    # -------------------------------------
    # 小台散戶
    # -------------------------------------

    mtx = calc_retail_oi(
        fixture["mtx_market_oi"],
        fixture["mtx_inst_long"],
        fixture["mtx_inst_short"],
    )

    # -------------------------------------
    # 微台散戶
    # -------------------------------------

    tmf = calc_retail_oi(
        fixture["tmf_market_oi"],
        fixture["tmf_inst_long"],
        fixture["tmf_inst_short"],
    )

    return {
        "date": target,

        # 大盤
        "index":
            fixture["index"],

        "change":
            fixture["change"],

        "percent":
            fixture["percent"],

        "turnover":
            fixture["turnover"],

        # 三大法人
        "institution_total":
            fixture["institution_total"],

        "foreign":
            fixture["foreign"],

        "trust":
            fixture["trust"],

        "dealer":
            fixture["dealer"],

        # 外資台指期
        "foreign_tx_oi":
            fixture["foreign_tx_oi"],

        "previous_foreign_tx_oi":
            fixture[
                "previous_foreign_tx_oi"
            ],

        "foreign_tx_oi_change":
            foreign_tx_oi_change,

        # 小台
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

        # 微台
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
def get_live_retail_data():
    mtx_market_oi = get_market_oi_from_taifex("MTX")

    mtx_inst_long, mtx_inst_short = (
        get_institution_oi_from_taifex(
            "小型臺指期貨"
        )
    )

    tmf_market_oi = get_market_oi_from_taifex("TMF")

    tmf_inst_long, tmf_inst_short = (
        get_institution_oi_from_taifex(
            "微型臺指期貨"
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
        "mtx_market_oi": mtx_market_oi,
        "mtx_retail_long": mtx["retail_long"],
        "mtx_retail_short": mtx["retail_short"],
        "mtx_retail_net": mtx["retail_net"],
        "mtx_retail_ratio": mtx["ratio"],

        "tmf_market_oi": tmf_market_oi,
        "tmf_retail_long": tmf["retail_long"],
        "tmf_retail_short": tmf["retail_short"],
        "tmf_retail_net": tmf["retail_net"],
        "tmf_retail_ratio": tmf["ratio"],
    }

# =========================================================
# 正式資料入口
# =========================================================

def get_market_data(target):
    """
    目前階段：
    先把 2026-09-10 全部數值與公式驗證完整。

    等這版驗收通過後，
    再把小台 / 微台改成 TAIFEX 當日自動抓取。
    """

    if target in VALIDATION_FIXTURES:
        return get_fixture_market_data(
            target
        )

    raise RuntimeError(
        f"{target} 尚未接入正式盤後資料來源。"
        "目前請使用 2026-09-10 做固定驗收。"
    )


# =========================================================
# 輸出
# =========================================================

def print_market_data(data):

    target = data["date"]

    print(
        f"【盤後駐守｜{target}】"
    )

    print("")

    # -------------------------------------
    # 大盤
    # -------------------------------------

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

    # -------------------------------------
    # 三大法人
    # -------------------------------------

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

    # -------------------------------------
    # 外資台指期
    # -------------------------------------

    print(
        f"外資台指淨未平倉："
        f"{data['foreign_tx_oi']:+,} 口"
    )

    print(
        f"外資台指淨未平倉增減："
        f"{data['foreign_tx_oi_change']:+,} 口"
    )

    print("")

    # -------------------------------------
    # 小台 / 微台散戶
    # -------------------------------------

    print(
        f"散戶小台淨未平倉："
        f"{data['mtx_retail_net']:+,} 口"
    )

    print(
        f"散戶微台淨未平倉："
        f"{data['tmf_retail_net']:+,} 口"
    )

    print("")

    print(
        f"小台散戶多空比："
        f"{data['mtx_retail_ratio']:+.2f}%"
    )

    print(
        f"微台散戶多空比："
        f"{data['tmf_retail_ratio']:+.2f}%"
    )

    print("")

    print(
        "================================"
    )

    # -------------------------------------
    # 驗證用細節
    # -------------------------------------

    print(
        f"前一交易日外資台指淨未平倉："
        f"{data['previous_foreign_tx_oi']:+,} 口"
    )

    print("")

    print(
        f"小台全市場 OI："
        f"{data['mtx_market_oi']:,}"
    )

    print(
        f"小台散戶多單："
        f"{data['mtx_retail_long']:,}"
    )

    print(
        f"小台散戶空單："
        f"{data['mtx_retail_short']:,}"
    )

    print("")

    print(
        f"微台全市場 OI："
        f"{data['tmf_market_oi']:,}"
    )

    print(
        f"微台散戶多單："
        f"{data['tmf_retail_long']:,}"
    )

    print(
        f"微台散戶空單："
        f"{data['tmf_retail_short']:,}"
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
        help="交易日 YYYY-MM-DD"
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
