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

    # 收盤指數
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

    # 備援判斷
    if index_value is None:

        for value in target.values():

            try:
                number = clean_float(value)

                if 10000 < number < 100000:
                    index_value = number
                    break

            except:
                pass

    if index_value is None:
        raise RuntimeError("無法解析加權指數")

    return index_value, change, change_percent


# =========================
# 台股成交金額
# =========================
def get_twse_turnover():

    response = requests.get(
        TWSE_TURNOVER_URL,
        headers=HEADERS,
        timeout=20
    )

    response.raise_for_status()

    data = response.json()

    if not data:
        raise RuntimeError("找不到成交金額資料")

    latest = data[-1]

    if "TradeValue" not in latest:
        raise RuntimeError(
            f"找不到 TradeValue，實際欄位：{list(latest.keys())}"
        )

    turnover = clean_int(latest["TradeValue"])

    # 元 → 億元
    turnover_yi = turnover / 100000000

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
