"""
盤後圖表：外資買賣超（億元）

流程：
  1. 從 charts repo 下載歷史 CSV（沒有就從零開始）
  2. 補齊到目標日期為止的 WINDOW 個交易日
  3. 畫圖
  4. CSV 與 PNG 上傳 charts repo
  5. 推播到 LINE

用法：
  python charts.py                      # 抓最近一個交易日，畫圖並推播
  python charts.py --date 2026-09-11    # 指定日期
  python charts.py --dry-run            # 上傳但不推播 LINE，只印出網址
"""

import argparse, base64, csv, io, math, os, sys, time
import requests

# ---------------- 設定 ----------------
OWNER   = "ed123698745ed-oss"
REPO    = "panhou-charts"
BRANCH  = "main"
WINDOW  = 80                      # 圖表顯示的交易日數
CSV_PATH = "data/market_data.csv"

FONT_REG  = "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/SubsetOTF/TC/NotoSansTC-Regular.otf"
FONT_BOLD = "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/SubsetOTF/TC/NotoSansTC-Bold.otf"

FIELDS = ["date", "index_close", "foreign", "foreign_dealer", "trust",
          "dealer_self", "dealer_hedge", "total"]

C_UP, C_DOWN = "#D94B4B", "#2E9E6B"
C_LINE, C_TITLE = "#16181D", "#1E5AA8"
C_GRID, C_TICK, C_ZERO, C_NOTE = "#E9E9E9", "#8C8C8C", "#C2C2C2", "#9A9A9A"

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0"})


def log(*a):
    print(*a, flush=True)


# ---------------- TWSE ----------------
def fetch_month_index(yyyymm01):
    """回傳 {date: 收盤指數}，同時也是該月的交易日清單。"""
    d = S.get(f"https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK"
              f"?date={yyyymm01}&response=json", timeout=30).json()
    if d.get("stat") != "OK":
        raise RuntimeError(f"FMTQIK {yyyymm01}: {d.get('stat')}")
    out = {}
    for row in d["data"]:
        y, m, dd = row[0].split("/")
        out[f"{int(y)+1911:04d}-{m}-{dd}"] = float(row[4].replace(",", ""))
    return out


def fetch_bfi(date):
    """三大法人買賣超，單位轉億元。"""
    ymd = date.replace("-", "")
    last = None
    for _ in range(3):
        try:
            d = S.get(f"https://www.twse.com.tw/rwd/zh/fund/BFI82U"
                      f"?dayDate={ymd}&type=day&response=json", timeout=30).json()
            if d.get("stat") != "OK":
                raise RuntimeError(d.get("stat"))
            v = {r[0]: float(r[3].replace(",", "")) for r in d["data"]}
            return {
                "foreign":        round(v["外資及陸資(不含外資自營商)"] / 1e8, 2),
                "foreign_dealer": round(v["外資自營商"] / 1e8, 2),
                "trust":          round(v["投信"] / 1e8, 2),
                "dealer_self":    round(v["自營商(自行買賣)"] / 1e8, 2),
                "dealer_hedge":   round(v["自營商(避險)"] / 1e8, 2),
                "total":          round(v["合計"] / 1e8, 2),
            }
        except Exception as e:
            last = e
            time.sleep(4)
    raise RuntimeError(f"BFI82U {date} 抓取失敗: {last}")


def calendar_back_from(end_date, need):
    """從 end_date 往前收集至少 need 個交易日，回傳 {date: 指數}。"""
    y, m = int(end_date[:4]), int(end_date[5:7])
    idx = {}
    for _ in range(10):                       # 最多回溯 10 個月
        idx.update(fetch_month_index(f"{y:04d}{m:02d}01"))
        days = [d for d in idx if d <= end_date]
        if len(days) >= need:
            break
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        time.sleep(0.8)
    return idx


# ---------------- GitHub ----------------
def gh_headers():
    return {"Authorization": f"Bearer {os.environ['CHARTS_TOKEN']}",
            "Accept": "application/vnd.github+json"}


def gh_get_text(path):
    """讀取 charts repo 的檔案內容，不存在回傳 None。"""
    r = S.get(f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{path}"
              f"?ref={BRANCH}", headers=gh_headers(), timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return base64.b64decode(r.json()["content"]).decode("utf-8")


def gh_put(path, data: bytes, message):
    """上傳（或覆蓋）檔案。"""
    body = {"message": message,
            "content": base64.b64encode(data).decode(),
            "branch": BRANCH}
    r = S.get(f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{path}"
              f"?ref={BRANCH}", headers=gh_headers(), timeout=30)
    if r.status_code == 200:
        body["sha"] = r.json()["sha"]

    r = S.put(f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{path}",
              headers=gh_headers(), json=body, timeout=60)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"上傳 {path} 失敗 {r.status_code}: {r.text[:300]}")
    return f"https://raw.githubusercontent.com/{OWNER}/{REPO}/{BRANCH}/{path}"


# ---------------- 歷史資料 ----------------
def load_history():
    txt = gh_get_text(CSV_PATH)
    if not txt:
        return []
    return list(csv.DictReader(io.StringIO(txt)))


def save_history(rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS)
    w.writeheader()
    w.writerows(rows)
    return gh_put(CSV_PATH, buf.getvalue().encode("utf-8"),
                  f"update market data ({rows[-1]['date']})")


def ensure_history(end_date):
    """補齊到 end_date 為止的 WINDOW 個交易日，回傳排序後的完整清單。"""
    rows = {r["date"]: r for r in load_history()}
    log(f"[歷史] 既有 {len(rows)} 筆")

    idx = calendar_back_from(end_date, WINDOW)
    want = sorted(d for d in idx if d <= end_date)[-WINDOW:]
    if not want:
        raise SystemExit(f"{end_date} 之前找不到交易日")
    if want[-1] != end_date:
        log(f"[注意] {end_date} 非交易日，改用最後交易日 {want[-1]}")

    missing = [d for d in want if d not in rows]
    log(f"[歷史] 需要 {len(want)} 天，待補 {len(missing)} 天")
    for i, d in enumerate(missing):
        rows[d] = {"date": d, "index_close": idx[d], **fetch_bfi(d)}
        if i % 20 == 0 or i == len(missing) - 1:
            log(f"  補 {i+1}/{len(missing)} {d}")
        time.sleep(1.2)

    # 保留比視窗多一些，方便日後延長區間
    keep = sorted(rows)[-(WINDOW * 3):]
    return [rows[d] for d in keep], [rows[d] for d in want]


# ---------------- 畫圖 ----------------
def load_fonts():
    from matplotlib.font_manager import FontProperties
    paths = []
    for url, name in ((FONT_REG, "tc-regular.otf"), (FONT_BOLD, "tc-bold.otf")):
        local = os.environ.get("FONT_DIR", ".") + "/" + name
        if not os.path.exists(local):
            log(f"[字型] 下載 {name}")
            r = S.get(url, timeout=120)
            r.raise_for_status()
            with open(local, "wb") as f:
                f.write(r.content)
        paths.append(local)
    return FontProperties(fname=paths[0]), FontProperties(fname=paths[1])


def draw_foreign(rows) -> bytes:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MultipleLocator

    f_reg, f_bold = load_fonts()
    plt.rcParams["axes.unicode_minus"] = False

    dates = [r["date"] for r in rows]
    idx   = [float(r["index_close"]) for r in rows]
    bar   = [float(r["foreign"]) for r in rows]
    n     = len(rows)

    mid  = round(min(idx) / 1000) * 1000
    half = math.ceil((max(idx) - mid) * 1.25 / 1000) * 1000
    R    = math.ceil(max(abs(v) for v in bar) * 1.06 / 500) * 500

    fig  = plt.figure(figsize=(12, 8.4), dpi=120, facecolor="white")
    ax_i = fig.add_axes([0.085, 0.125, 0.835, 0.700])
    ax_b = ax_i.twinx()
    ax_i.set_ylim(mid - half, mid + half)
    ax_b.set_ylim(-R, R)

    x = list(range(n))
    ax_i.set_axisbelow(True)
    ax_i.yaxis.set_major_locator(MultipleLocator(2 * half / 10))
    ax_i.grid(axis="y", color=C_GRID, linewidth=0.9, zorder=0)
    ax_b.axhline(0, color=C_ZERO, linewidth=1.1, zorder=1)

    ax_b.bar(x, bar, width=0.72, zorder=2,
             color=[C_UP if v >= 0 else C_DOWN for v in bar])
    ax_i.plot(x, idx, color=C_LINE, linewidth=1.9, zorder=3,
              solid_joinstyle="round", solid_capstyle="round")

    rt = [-R + i * (2 * R / 10) for i in range(11)]
    ax_b.set_yticks(rt)
    ax_b.set_yticklabels([f"{'+' if v > 0 else ''}{v:,.0f}" for v in rt])

    for a in (ax_i, ax_b):
        a.tick_params(colors=C_TICK, labelsize=11, length=0)
        for s in a.spines.values():
            s.set_visible(False)
        for lb in a.get_yticklabels():
            lb.set_fontproperties(f_reg)
    ax_i.spines["bottom"].set_visible(True)
    ax_i.spines["bottom"].set_color("#D0D0D0")

    ax_i.set_xlim(-1, n)
    tp = list(range(0, n, 20))
    if tp[-1] != n - 1:
        tp.append(n - 1)
    ax_i.set_xticks(tp)
    ax_i.set_xticklabels([f"{int(dates[i][5:7])}/{int(dates[i][8:10])}" for i in tp],
                         fontsize=12, color="#5A5A5A", fontproperties=f_reg)
    ax_i.set_ylabel("加權指數", fontsize=12.5, color="#5A5A5A",
                    labelpad=12, fontproperties=f_reg)

    last = bar[-1]
    fig.text(0.085, 0.920, "外資買賣超(億元)", fontsize=30, color=C_TITLE,
             va="center", ha="left", fontproperties=f_bold)
    fig.text(0.920, 0.920, f"{last:+,.2f}", fontsize=30,
             color=C_UP if last >= 0 else C_DOWN,
             va="center", ha="right", fontproperties=f_bold)
    fig.add_artist(plt.Line2D([0.085, 0.920], [0.862, 0.862],
                              color="#2B2B2B", linewidth=1.6))

    d = dates[-1]
    fig.text(0.085, 0.038,
             f"資料來源：臺灣證券交易所（TWSE）　·　{d[:4]}/{d[5:7]}/{d[8:10]}　·　"
             f"近 {n} 個交易日（{dates[0][5:7]}/{dates[0][8:10]} 起）",
             fontsize=10.5, color=C_NOTE, va="center", ha="left",
             fontproperties=f_reg)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


# ---------------- LINE ----------------
def line_push_image(url):
    r = S.post("https://api.line.me/v2/bot/message/push",
               headers={"Authorization": f"Bearer {os.environ['LINE_CHANNEL_ACCESS_TOKEN']}",
                        "Content-Type": "application/json"},
               json={"to": os.environ["LINE_USER_ID"],
                     "messages": [{"type": "image",
                                   "originalContentUrl": url,
                                   "previewImageUrl": url}]},
               timeout=30)
    log(f"[LINE] {r.status_code} {r.text[:200]}")
    if r.status_code != 200:
        raise SystemExit("推播失敗")


# ---------------- 主流程 ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD，預設為今天")
    ap.add_argument("--dry-run", action="store_true", help="上傳但不推播 LINE")
    a = ap.parse_args()

    end = a.date or time.strftime("%Y-%m-%d", time.localtime(time.time() + 8 * 3600))
    log(f"[開始] 目標日期 {end}")

    keep, window = ensure_history(end)
    d = window[-1]["date"]
    log(f"[資料] 視窗 {window[0]['date']} ~ {d}，共 {len(window)} 天，"
        f"外資 {window[-1]['foreign']} 億")

    save_history(keep)
    log(f"[上傳] CSV 共 {len(keep)} 筆")

    png = draw_foreign(window)
    url = gh_put(f"charts/foreign_{d.replace('-', '')}.png", png,
                 f"foreign chart {d}")
    log(f"[上傳] {url}  ({len(png)//1024} KB)")

    # 確認 CDN 讀得到再推播
    for i in range(10):
        time.sleep(4)
        h = S.get(url, timeout=20)
        log(f"  CDN 檢查 {i+1}: {h.status_code} {h.headers.get('content-type')}")
        if h.status_code == 200:
            break
    else:
        raise SystemExit("圖片網址讀不到")

    if a.dry_run:
        log("[dry-run] 跳過 LINE 推播")
    else:
        line_push_image(url)
    log("[完成]")


if __name__ == "__main__":
    main()
