"""盤後駐守看門狗：檢查今天的文字與圖表是否都已推播，沒推就發 LINE 警示。

起因：2026-11-05 Actions 額度用盡，推播工作根本沒啟動，同一個 job 內的失敗通知也沒發出。
本程式是獨立的 workflow，只讀「已推播記號」，不抓資料、不重推。

判斷：
  1. 今天是否為交易日：證交所 FMTQIK 當月資料有今天 → 交易日（颱風臨時休市不會有資料，不警示）。
  2. panhou-charts 的 data/last_text_push.txt（文字）、data/last_push.txt（圖表）是否等於今天。
任一項不是今天，或無法確認，就推 LINE 警示。
"""
import base64
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import requests

TPE = timezone(timedelta(hours=8))
CHARTS_API = "https://api.github.com/repos/ed123698745ed-oss/panhou-charts/contents/"
MARKERS = {"文字": "data/last_text_push.txt", "圖表": "data/last_push.txt"}
ACTIONS_URL = "https://github.com/ed123698745ed-oss/panhou-zhushou-/actions"


def get_retry(url, **kw):
    last = None
    for k in range(4):
        try:
            r = requests.get(url, timeout=30, **kw)
            if r.status_code < 500 and r.status_code != 429:
                return r
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(5 * 2 ** k)
    raise RuntimeError(f"GET {url} 重試 4 次仍失敗：{last}")


def is_trading_day(day):
    """回傳 True／False；格式異常時拋例外（由呼叫端警示「無法確認」）。"""
    r = get_retry("https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK",
                  params={"date": day.replace("-", ""), "response": "json"})
    d = r.json()  # 非 JSON 會拋例外
    if d.get("stat") != "OK":
        # 當月第一個交易日收盤前沒資料時 stat 會不是 OK
        return False
    roc = f"{int(day[:4]) - 1911}/{day[5:7]}/{day[8:10]}"
    return any(row[0].strip() == roc for row in d.get("data", []))


def read_marker(path):
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("CHARTS_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = get_retry(CHARTS_API + path + "?ref=main", headers=headers)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return base64.b64decode(r.json()["content"]).decode("utf-8").strip()


def push_line(text):
    payload = {"to": os.environ["LINE_USER_ID"], "messages": [{"type": "text", "text": text}]}
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {os.environ['LINE_CHANNEL_ACCESS_TOKEN']}",
                 "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        print(f"LINE status: {resp.status}")


def check(day):
    """回傳要警示的問題清單（空清單＝正常或非交易日）。"""
    try:
        if not is_trading_day(day):
            print(f"{day} 非交易日（或證交所無當日資料），不檢查")
            return []
    except Exception as e:
        return [f"無法確認今天是否為交易日：{e}"]
    problems = []
    for name, path in MARKERS.items():
        try:
            last = read_marker(path)
        except Exception as e:
            problems.append(f"{name}：讀取推播記號失敗（{e}）")
            continue
        print(f"{name} 記號：{last}")
        if last != day:
            problems.append(f"{name}：未推播（記號停在 {last or '無'}）")
    return problems


def main():
    day = os.environ.get("CHECK_DATE") or datetime.now(TPE).date().isoformat()
    dry = os.environ.get("DRY_RUN") == "true"
    problems = check(day)
    if not problems:
        print(f"{day} 檢查正常")
        return
    text = (f"【盤後駐守警示｜{day}】\n" + "\n".join(problems)
            + f"\n請 Emit 檢查 GitHub Actions：\n{ACTIONS_URL}")
    print(text)
    if dry:
        print("[dry-run] 未推播")
    else:
        push_line(text)


if __name__ == "__main__":
    sys.exit(main())
