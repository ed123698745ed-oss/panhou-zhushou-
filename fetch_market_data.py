import requests
from bs4 import BeautifulSoup

URL = "https://www.taifex.com.tw/cht/3/futContractsDateExcel"


def clean_number(text):
    return int(text.replace(",", "").strip())


response = requests.get(
    URL,
    timeout=20,
    headers={
        "User-Agent": "Mozilla/5.0"
    }
)

response.raise_for_status()

soup = BeautifulSoup(response.text, "html.parser")

rows = soup.find_all("tr")

found = False

for i, row in enumerate(rows):
    cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]

    # 找到「臺股期貨」這一組
    if "臺股期貨" in cells:
        # 外資通常是這組的第 3 列
        for next_row in rows[i:i + 4]:
            next_cells = [
                cell.get_text(" ", strip=True)
                for cell in next_row.find_all(["td", "th"])
            ]

            if "外資" in next_cells:
                numbers = []

                for value in next_cells:
                    try:
                        numbers.append(clean_number(value))
                    except:
                        pass

                # 最後兩個數字：
                # 倒數第2 = 未平倉多空淨額口數
                # 倒數第1 = 未平倉多空淨額契約金額
                net_oi = numbers[-2]

                print("==============================")
                print("盤後駐守｜期交所測試")
                print("==============================")
                print(f"外資台指期淨未平倉：{net_oi:,} 口")
                print("==============================")

                found = True
                break

    if found:
        break


if not found:
    raise RuntimeError("找不到臺股期貨外資資料")
