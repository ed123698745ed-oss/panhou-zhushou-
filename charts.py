"""
盤後圖表產生器

目前支援：
  foreign  外資買賣超（億元）      資料來源：TWSE
  mtx      小台散戶多空比（%）     資料來源：TAIFEX（沿用 fetch_market_data 的計算）

流程：
  1. 從 charts repo 下載歷史 CSV
  2. 補齊到目標日期為止的 WINDOW 個交易日（缺哪些欄位補哪些，每 10 天存檔一次）
  3. 畫圖 → 上傳 charts repo → 推播 LINE

用法：
  python charts.py                              # 全部圖表，抓最近交易日
  python charts.py --chart foreign              # 只做外資那張
  python charts.py --date 2026-09-11            # 指定日期
  python charts.py --dry-run                    # 上傳但不推播
  python charts.py --chart mtx --max-fetch 30   # 本次最多補 30 天（回補分批用）
"""

import argparse, base64, csv, io, math, os, time
from datetime import date as Date
import requests

# ---------------- 設定 ----------------
OWNER, REPO, BRANCH = "ed123698745ed-oss", "panhou-charts", "main"
WINDOW = 80
CSV_PATH = "data/market_data.csv"

FONT_REG = "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/SubsetOTF/TC/NotoSansTC-Regular.otf"
FONT_BOLD = "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/SubsetOTF/TC/NotoSansTC-Bold.otf"

TWSE_FIELDS = ["index_close", "foreign", "foreign_dealer", "trust",
               "dealer_self", "dealer_hedge", "total"]
TAIFEX_FIELDS = ["mtx_market_oi", "mtx_retail_long", "mtx_retail_short", "mtx_retail_ratio",
                 "tmf_market_oi", "tmf_retail_long", "tmf_retail_short", "tmf_retail_ratio"]
FIELDS = ["date"] + TWSE_FIELDS + TAIFEX_FIELDS

CHART_NEEDS = {"foreign": [], "mtx": ["taifex"]}

C_UP, C_DOWN = "#D94B4B", "#2E9E6B"
C_LINE, C_TITLE = "#16181D", "#1E5AA8"
C_GRID, C_TICK, C_ZERO, C_NOTE = "#E9E9E9", "#8C8C8C", "#C2C2C2", "#9A9A9A"

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0"})


def log(*a):
    print(*a, flush=True)


# ---------------- TWSE ----------------
def fetch_month_index(yyyymm01):
    d = S.get("https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK"
              f"?date={yyyymm01}&response=json", timeout=30).json()
    if d.get("stat") != "OK":
        raise RuntimeError(f"FMTQIK {yyyymm01}: {d.get('stat')}")
    out = {}
    for row in d["data"]:
        y, m, dd = row[0].split("/")
        out[f"{int(y)+1911:04d}-{m}-{dd}"] = float(row[4].replace(",", ""))
    return out


def fetch_bfi(date):
    ymd = date.replace("-", "")
    err = None
    for _ in range(3):
        try:
            d = S.get("https://www.twse.com.tw/rwd/zh/fund/BFI82U"
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
            err = e
            time.sleep(4)
    raise RuntimeError(f"BFI82U {date} 失敗: {err}")


def calendar_back_from(end_date, need):
    y, m = int(end_date[:4]), int(end_date[5:7])
    idx = {}
    for _ in range(10):
        idx.update(fetch_month_index(f"{y:04d}{m:02d}01"))
        if len([d for d in idx if d <= end_date]) >= need:
            break
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        time.sleep(0.8)
    return idx


# ---------------- TAIFEX（沿用 fetch_market_data） ----------------
_FMD_PATCHED = False


def _patch_fmd(fmd, gap=1.6):
    """
    期交所會限流（429）。fetch_market_data 每天要打 4 次，
    這裡統一包一層：每次請求前固定間隔，遇 429 退讓重試。
    """
    global _FMD_PATCHED
    if _FMD_PATCHED:
        return
    orig = fmd.request_get

    def wrapped(url, params=None):
        for attempt in range(6):
            time.sleep(gap)
            try:
                return orig(url, params=params)
            except requests.HTTPError as e:
                code = getattr(e.response, "status_code", 0)
                if code == 429:
                    wait = 30 * (attempt + 1)
                    log(f"    [429] 期交所限流，等 {wait}s 重試（第 {attempt+1} 次）")
                    time.sleep(wait)
                    continue
                raise
        raise RuntimeError("期交所連續回 429，本次放棄")

    fmd.request_get = wrapped
    _FMD_PATCHED = True


def fetch_taifex(date):
    """呼叫既有的 get_live_retail_data，取小台／微台散戶資料。"""
    import fetch_market_data as fmd
    _patch_fmd(fmd)
    t = Date(int(date[:4]), int(date[5:7]), int(date[8:10]))
    r = fmd.get_live_retail_data(t)
    return {
        "mtx_market_oi":    int(r["mtx_market_oi"]),
        "mtx_retail_long":  int(r["mtx_retail_long"]),
        "mtx_retail_short": int(r["mtx_retail_short"]),
        "mtx_retail_ratio": round(float(r["mtx_retail_ratio"]), 2),
        "tmf_market_oi":    int(r["tmf_market_oi"]),
        "tmf_retail_long":  int(r["tmf_retail_long"]),
        "tmf_retail_short": int(r["tmf_retail_short"]),
        "tmf_retail_ratio": round(float(r["tmf_retail_ratio"]), 2),
    }


# ---------------- GitHub ----------------
def gh_headers():
    return {"Authorization": f"Bearer {os.environ['CHARTS_TOKEN']}",
            "Accept": "application/vnd.github+json"}


def gh_get_text(path):
    r = S.get(f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{path}?ref={BRANCH}",
              headers=gh_headers(), timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return base64.b64decode(r.json()["content"]).decode("utf-8")


def gh_put(path, data: bytes, message):
    body = {"message": message, "content": base64.b64encode(data).decode(), "branch": BRANCH}
    r = S.get(f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{path}?ref={BRANCH}",
              headers=gh_headers(), timeout=30)
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
    return list(csv.DictReader(io.StringIO(txt))) if txt else []


def save_history(rows_map):
    rows = [rows_map[d] for d in sorted(rows_map)][-(WINDOW * 3):]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({f: r.get(f, "") for f in FIELDS})
    gh_put(CSV_PATH, buf.getvalue().encode("utf-8"),
           f"update market data ({rows[-1]['date']})")
    return len(rows)


def _missing(row, fields):
    return any(str(row.get(f, "")).strip() == "" for f in fields)


def ensure_history(end_date, need_taifex, max_fetch=None):
    rows = {r["date"]: dict(r) for r in load_history()}
    log(f"[歷史] 既有 {len(rows)} 筆")

    idx = calendar_back_from(end_date, WINDOW)
    want = sorted(d for d in idx if d <= end_date)[-WINDOW:]
    if not want:
        raise SystemExit(f"{end_date} 之前找不到交易日")
    if want[-1] != end_date:
        log(f"[注意] {end_date} 非交易日，改用最後交易日 {want[-1]}")

    todo = []
    for d in want:
        r = rows.get(d, {})
        nt = _missing(r, TWSE_FIELDS)
        nf = need_taifex and _missing(r, TAIFEX_FIELDS)
        if nt or nf:
            todo.append((d, nt, nf))

    log(f"[歷史] 視窗 {len(want)} 天，待補 {len(todo)} 天"
        f"（TWSE {sum(1 for t in todo if t[1])}、TAIFEX {sum(1 for t in todo if t[2])}）")

    if max_fetch and len(todo) > max_fetch:
        log(f"[限制] 本次只補前 {max_fetch} 天")
        todo = todo[:max_fetch]

    done = 0
    try:
        for i, (d, nt, nf) in enumerate(todo):
            row = rows.get(d) or {"date": d}
            if nt:
                row["index_close"] = idx[d]
                row.update(fetch_bfi(d))
                time.sleep(1.2)
            if nf:
                row.update(fetch_taifex(d))
            rows[d] = row
            done = i + 1
            log(f"  補 {done}/{len(todo)} {d}")
            if done % 5 == 0 and done < len(todo):
                save_history(rows)
                log(f"  [存檔] 已完成 {done} 天")
    except BaseException as e:
        if done:
            save_history(rows)
            log(f"  [存檔] 中斷前已保住 {done} 天")
        log(f"[中斷] {type(e).__name__}: {e}")
        raise SystemExit("抓取中斷，進度已存檔。再跑一次即可從斷點續補。")

    if todo:
        log(f"[上傳] CSV 共 {save_history(rows)} 筆")

    return [rows[d] for d in want]


# ---------------- 繪圖共用 ----------------
def load_fonts():
    from matplotlib.font_manager import FontProperties
    out = []
    for url, name in ((FONT_REG, "tc-regular.otf"), (FONT_BOLD, "tc-bold.otf")):
        p = os.path.join(os.environ.get("FONT_DIR", "."), name)
        if not os.path.exists(p):
            log(f"[字型] 下載 {name}")
            r = S.get(url, timeout=120)
            r.raise_for_status()
            open(p, "wb").write(r.content)
        out.append(FontProperties(fname=p))
    return out


def nice_step(raw):
    e = math.floor(math.log10(raw)) if raw > 0 else 0
    base = raw / (10 ** e)
    for m in (1, 2, 2.5, 5, 10):
        if base <= m:
            return m * (10 ** e)
    return 10 ** (e + 1)


def nice_axis(lo, hi, n=10, pad=1.35):
    step = nice_step((hi - lo) * pad / n)
    a = math.floor(lo / step) * step - step
    b = a + n * step
    while b < hi:
        a += step
        b += step
    return a, b


def index_axis(idx):
    """左軸：讓指數落在上半部，與永豐一致。"""
    mid = round(min(idx) / 1000) * 1000
    half = math.ceil((max(idx) - mid) * 1.25 / 1000) * 1000
    return mid - half, mid + half


def third_wednesday(y, m):
    cnt = 0
    for day in range(1, 32):
        try:
            dt = Date(y, m, day)
        except ValueError:
            break
        if dt.weekday() == 2:
            cnt += 1
            if cnt == 3:
                return dt.isoformat()
    return None


def settlement_x(dates):
    """結算日（第三個週三，遇假日順延）在 dates 中的 x 位置。"""
    out = []
    for ym in sorted({d[:7] for d in dates}):
        tw = third_wednesday(int(ym[:4]), int(ym[5:7]))
        if tw < dates[0]:          # 結算日早於視窗起點，不畫
            continue
        for i, d in enumerate(dates):
            if d >= tw:
                if d[:7] == ym:
                    out.append(i)
                break
    return out


def _style_axes(ax_i, ax_b, f_reg, dates, n):
    ax_i.set_axisbelow(True)
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


def _footer(fig, dates, n, f_reg, source):
    d = dates[-1]
    fig.text(0.085, 0.038,
             f"資料來源：{source}　·　{d[:4]}/{d[5:7]}/{d[8:10]}　·　"
             f"近 {n} 個交易日（{dates[0][5:7]}/{dates[0][8:10]} 起）",
             fontsize=10.5, color=C_NOTE, va="center", ha="left", fontproperties=f_reg)


def _save(fig):
    import matplotlib.pyplot as plt
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


# ---------------- 圖一：外資買賣超 ----------------
def draw_foreign(rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MultipleLocator

    f_reg, f_bold = load_fonts()
    plt.rcParams["axes.unicode_minus"] = False

    dates = [r["date"] for r in rows]
    idx = [float(r["index_close"]) for r in rows]
    bar = [float(r["foreign"]) for r in rows]
    n = len(rows)

    L_LO, L_HI = index_axis(idx)
    R = math.ceil(max(abs(v) for v in bar) * 1.06 / 500) * 500

    fig = plt.figure(figsize=(12, 8.4), dpi=120, facecolor="white")
    ax_i = fig.add_axes([0.085, 0.125, 0.835, 0.700])
    ax_b = ax_i.twinx()
    ax_i.set_ylim(L_LO, L_HI)
    ax_b.set_ylim(-R, R)

    x = list(range(n))
    ax_i.yaxis.set_major_locator(MultipleLocator((L_HI - L_LO) / 10))
    ax_i.grid(axis="y", color=C_GRID, linewidth=0.9, zorder=0)
    ax_b.axhline(0, color=C_ZERO, linewidth=1.1, zorder=1)
    ax_b.bar(x, bar, width=0.72, zorder=2,
             color=[C_UP if v >= 0 else C_DOWN for v in bar])
    ax_i.plot(x, idx, color=C_LINE, linewidth=1.9, zorder=3,
              solid_joinstyle="round", solid_capstyle="round")

    rt = [-R + i * (2 * R / 10) for i in range(11)]
    ax_b.set_yticks(rt)
    ax_b.set_yticklabels([f"{'+' if v > 0 else ''}{v:,.0f}" for v in rt])
    _style_axes(ax_i, ax_b, f_reg, dates, n)

    last = bar[-1]
    fig.text(0.085, 0.920, "外資買賣超(億元)", fontsize=30, color=C_TITLE,
             va="center", ha="left", fontproperties=f_bold)
    fig.text(0.920, 0.920, f"{last:+,.2f}", fontsize=30,
             color=C_UP if last >= 0 else C_DOWN,
             va="center", ha="right", fontproperties=f_bold)
    fig.add_artist(plt.Line2D([0.085, 0.920], [0.862, 0.862], color="#2B2B2B", linewidth=1.6))
    _footer(fig, dates, n, f_reg, "臺灣證券交易所（TWSE）")
    return _save(fig)


# ---------------- 圖二：小台散戶多空比 ----------------
def draw_mtx_ratio(rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MultipleLocator

    f_reg, f_bold = load_fonts()
    plt.rcParams["axes.unicode_minus"] = False

    dates = [r["date"] for r in rows]
    idx = [float(r["index_close"]) for r in rows]
    bar = [float(r["mtx_retail_ratio"]) for r in rows]
    n = len(rows)

    L_LO, L_HI = index_axis(idx)
    R_LO, R_HI = nice_axis(min(bar), max(bar))

    fig = plt.figure(figsize=(12, 8.4), dpi=120, facecolor="white")
    ax_i = fig.add_axes([0.085, 0.125, 0.835, 0.715])
    ax_b = ax_i.twinx()
    ax_i.set_ylim(L_LO, L_HI)
    ax_b.set_ylim(R_LO, R_HI)

    x = list(range(n))
    ax_i.yaxis.set_major_locator(MultipleLocator((L_HI - L_LO) / 10))
    ax_i.grid(axis="y", color=C_GRID, linewidth=0.9, zorder=0)

    for sx in settlement_x(dates):
        ax_i.axvline(sx, color="#9E9E9E", linewidth=1.0,
                     linestyle=(0, (4, 4)), zorder=1)

    ax_b.axhline(0, color=C_ZERO, linewidth=1.1, zorder=1)
    ax_b.bar(x, bar, width=0.72, zorder=2,
             color=[C_UP if v >= 0 else C_DOWN for v in bar])
    ax_i.plot(x, idx, color=C_LINE, linewidth=1.9, zorder=3,
              solid_joinstyle="round", solid_capstyle="round")

    step = (R_HI - R_LO) / 10
    rt = [R_LO + i * step for i in range(11)]
    ax_b.set_yticks(rt)
    ax_b.set_yticklabels([("0" if abs(v) < 1e-9 else f"{v:+g}%") for v in rt])
    _style_axes(ax_i, ax_b, f_reg, dates, n)

    lg, sh = int(rows[-1]["mtx_retail_long"]), int(rows[-1]["mtx_retail_short"])
    for i, (lab, val, col) in enumerate((("散戶多單", lg, C_UP), ("散戶空單", sh, C_DOWN))):
        bx = 0.040 + i * 0.235
        ax_i.text(bx, 0.945, lab, transform=ax_i.transAxes, fontsize=12.5,
                  color="white", va="center", ha="center", fontproperties=f_bold,
                  bbox=dict(boxstyle="round,pad=0.40", facecolor=col, edgecolor="none"))
        ax_i.text(bx + 0.062, 0.945, f"{val:,}", transform=ax_i.transAxes,
                  fontsize=18, color="#2B2B2B", va="center", ha="left",
                  fontproperties=f_bold)

    last = bar[-1]
    fig.text(0.085, 0.945, "小台散戶多空比", fontsize=30, color=C_TITLE,
             va="center", ha="left", fontproperties=f_bold)
    fig.text(0.920, 0.945, f"{last:+.2f}%", fontsize=30,
             color=C_UP if last >= 0 else C_DOWN,
             va="center", ha="right", fontproperties=f_bold)
    fig.text(0.395, 0.952,
             "散戶多單 = 全市場 OI − 三大法人多單\n"
             "散戶空單 = 全市場 OI − 三大法人空單\n"
             "散戶多空比 =（散戶多單 − 散戶空單）/ 全市場 OI × 100%　·　虛線為結算日",
             fontsize=9.5, color="#7A7A7A", va="center", ha="left",
             linespacing=1.55, fontproperties=f_reg)
    fig.add_artist(plt.Line2D([0.085, 0.920], [0.898, 0.898], color="#2B2B2B", linewidth=1.6))
    _footer(fig, dates, n, f_reg, "臺灣期貨交易所（TAIFEX）、臺灣證券交易所（TWSE）")
    return _save(fig)


DRAWERS = {"foreign": (draw_foreign, "外資買賣超"),
           "mtx": (draw_mtx_ratio, "小台散戶多空比")}


# ---------------- LINE ----------------
def line_push_images(urls):
    msgs = [{"type": "image", "originalContentUrl": u, "previewImageUrl": u} for u in urls]
    r = S.post("https://api.line.me/v2/bot/message/push",
               headers={"Authorization": f"Bearer {os.environ['LINE_CHANNEL_ACCESS_TOKEN']}",
                        "Content-Type": "application/json"},
               json={"to": os.environ["LINE_USER_ID"], "messages": msgs}, timeout=30)
    log(f"[LINE] {r.status_code} {r.text[:200]}")
    if r.status_code != 200:
        raise SystemExit("推播失敗")


# ---------------- 主流程 ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--chart", default="all", help="foreign / mtx / all")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-fetch", type=int, default=None,
                    help="本次最多補幾天（回補分批用）")
    a = ap.parse_args()

    charts = list(DRAWERS) if a.chart == "all" else [c.strip() for c in a.chart.split(",")]
    for c in charts:
        if c not in DRAWERS:
            raise SystemExit(f"未知圖表：{c}")

    end = a.date or time.strftime("%Y-%m-%d", time.localtime(time.time() + 8 * 3600))
    need_taifex = any("taifex" in CHART_NEEDS[c] for c in charts)
    log(f"[開始] 目標 {end}，圖表 {charts}，需要期交所資料：{need_taifex}")

    window = ensure_history(end, need_taifex, a.max_fetch)
    d = window[-1]["date"]

    for c in charts:
        need = TAIFEX_FIELDS if "taifex" in CHART_NEEDS[c] else TWSE_FIELDS
        short = [r["date"] for r in window if _missing(r, need)]
        if short:
            raise SystemExit(
                f"[中止] {c} 還有 {len(short)} 天未補齊"
                f"（{short[0]} ~ {short[-1]}）。CSV 已存檔，再跑一次即可續補。")

    log(f"[資料] {window[0]['date']} ~ {d}，共 {len(window)} 天")

    urls = []
    for c in charts:
        fn, label = DRAWERS[c]
        png = fn(window)
        url = gh_put(f"charts/{c}_{d.replace('-', '')}.png", png, f"{c} chart {d}")
        log(f"[上傳] {label} {url}  ({len(png)//1024} KB)")
        urls.append(url)

    for i in range(10):
        time.sleep(4)
        codes = [S.get(u, timeout=20).status_code for u in urls]
        log(f"  CDN 檢查 {i+1}: {codes}")
        if all(c == 200 for c in codes):
            break
    else:
        raise SystemExit("圖片網址讀不到")

    if a.dry_run:
        log("[dry-run] 跳過 LINE 推播")
    else:
        line_push_images(urls)
    log("[完成]")


if __name__ == "__main__":
    main()
