import requests
from bs4 import BeautifulSoup


TAIFEX_URL = "https://www.taifex.com.tw/cht/3/futContractsDateExcel"
TWSE_INDEX_URL = "https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX"
TWSE_TURNOVER_URL = "https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK"

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


def clean_int(text):
    return int(
        str(text)
        .replace(",", "")
        .replace("+", "")
        .strip()
    )


def clean_float(text):
    return float(
        str(text)
        .replace(",", "")
        .replace("+", "")
        .replace("%", "")
        .strip()
    )


# =========================
# 加權指數
# =========================
def get_twse_index():

    response = requests.get(
        TWSE_INDEX_URL,
        headers=HEADERS,
        timeout=20
    )

    response.raise_for_status()
    data = response.json()

    target = None

    for row in data:

        name = (
            row.get("指數", "")
            or row.get("指數名稱", "")
            or row.get("Name", "")
        )

        if "發行量加權股價指數" in name:
            target = row
            break

    if target is None:
        raise RuntimeError("找不到加權指數資料")

    index_value = None
    change = None
    change_percent = None

    for key, value in target.items():

        if "收盤" in key:
            try:
                index_value = clean_float(value)
            except:
                pass

        if "漲跌點數" in key:
            try:
                change = clean_float(value)
            except:
                pass

        if "漲跌百分比" in key or "漲跌幅" in key:
            try:
                change_percent = clean_float(value)
            except:
                pass

    if index_value is None:
        raise RuntimeError("無法解析加權指數")

    # TWSE 的漲跌點數欄位可能沒有帶正負號
    # 直接依漲跌幅決定方向
    if change is not None and change_percent is not None:

        if change_percent < 0:
            change = -abs(change)

        elif change_percent > 0:
            change = abs(change)

        else:
            change = 0.0

    return index_value, change, change_percent


# =========================
# 台股成交金額
# =========================
def get_twse_turnover():

    mis_url = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"

    params = {
        "ex_ch": "tse_t00.tw",
        "json": "1",
        "delay": "0"
    }

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://mis.twse.com.tw/"
    }

    response = requests.get(
        mis_url,
        params=params,
        headers=headers,
        timeout=20
    )

    response.raise_for_status()

    data = response.json()

    rows = data.get("msgArray", [])

    if not rows:
        raise RuntimeError("MIS 找不到加權指數資料")

    row = rows[0]

    # tv = 加權指數當日累計成交金額
    turnover = row.get("tv")

    if turnover in [None, "", "-"]:
        raise RuntimeError(
            f"MIS 找不到成交金額，實際資料：{row}"
        )

    turnover_yi = clean_float(turnover)

    return turnover_yi


# =========================
# 外資台指期淨未平倉
# =========================
def get_foreign_tx_oi():

    response = requests.get(
        TAIFEX_URL,
        headers=HEADERS,
        timeout=20
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    rows = soup.find_all("tr")

    for i, row in enumerate(rows):

        cells = [
            cell.get_text(" ", strip=True)
            for cell in row.find_all(["td", "th"])
        ]

        if "臺股期貨" in cells:

            for next_row in rows[i:i + 4]:

                next_cells = [
                    cell.get_text(" ", strip=True)
                    for cell in next_row.find_all(["td", "th"])
                ]

                if "外資" in next_cells:

                    numbers = []

                    for value in next_cells:

                        try:
                            numbers.append(
                                clean_int(value)
                            )

                        except:
                            pass

                    if len(numbers) < 2:
                        continue

                    # 倒數第 2 個數字
                    # = 未平倉多空淨額口數
                    return numbers[-2]

    raise RuntimeError(
        "找不到臺股期貨外資資料"
    )


# =========================
# 主程式
# =========================
def main():

    print("==============================")
    print("盤後駐守｜開始抓取資料")
    print("==============================")

    index_value, change, change_percent = get_twse_index()

    turnover_yi = get_twse_turnover()

    foreign_tx_oi = get_foreign_tx_oi()

    print("")
    print("【盤後駐守｜市場資料】")
    print("")

    print(
        f"加權指數：{index_value:,.2f}"
    )

    if change is not None:

        print(
            f"漲跌點數：{change:+,.2f}"
        )

    if change_percent is not None:

        print(
            f"漲跌幅：{change_percent:+.2f}%"
        )

    print(
        f"成交金額：{turnover_yi:,.0f} 億"
    )

    print(
        f"外資台指期淨未平倉："
        f"{foreign_tx_oi:,} 口"
    )

    print("")
    print("==============================")


if __name__ == "__main__":
    main()
