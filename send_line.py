import argparse
import base64
import os
from datetime import datetime

import requests

from fetch_market_data import (
    TAIPEI,
    parse_date,
    get_market_data,
    format_market_data,
    print_market_data,
)

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"

# 已推播記號：存在 panhou-charts（與圖表記號同處），同一交易日只推一次文字。
SENTINEL_API = (
    "https://api.github.com/repos/ed123698745ed-oss/panhou-charts/contents/"
    "data/last_text_push.txt"
)

# 自動執行（未指定 --date）只在台北 14:00～22:59 進行；
# GitHub 排程可能延遲到午夜後，那時「今天」還沒有資料（2026-10-28 02:03 失敗即此原因）。
AUTO_START_HOUR, AUTO_END_HOUR = 14, 23


def _gh_headers():
    token = os.environ.get("CHARTS_TOKEN", "")
    if not token:
        return None
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def read_sentinel():
    """回傳 (最後推播日字串, sha)。讀不到時回傳 (None, None)，不阻擋推播。"""
    headers = _gh_headers()
    if headers is None:
        print("[記號] 未設定 CHARTS_TOKEN，略過重複推播檢查")
        return None, None
    try:
        r = requests.get(SENTINEL_API, headers=headers, timeout=30)
        if r.status_code == 404:
            return None, None
        r.raise_for_status()
        j = r.json()
        return base64.b64decode(j["content"]).decode("utf-8").strip(), j["sha"]
    except Exception as e:  # 記號讀取失敗不影響推播
        print(f"[記號] 讀取失敗，略過重複推播檢查：{e}")
        return None, None


def write_sentinel(day, sha):
    headers = _gh_headers()
    if headers is None:
        return
    body = {
        "message": f"text pushed {day}",
        "content": base64.b64encode(day.encode("utf-8")).decode(),
        "branch": "main",
    }
    if sha:
        body["sha"] = sha
    try:
        r = requests.put(SENTINEL_API, headers=headers, json=body, timeout=30)
        r.raise_for_status()
        print(f"[記號] 已記錄文字推播 {day}")
    except Exception as e:  # 推播已送出，記號失敗只警告
        print(f"[記號][警告] 寫入失敗，下次可能重複推播：{e}")


def push_line(text):
    token = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
    user_id = os.environ["LINE_USER_ID"]

    response = requests.post(
        LINE_PUSH_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "to": user_id,
            "messages": [
                {"type": "text", "text": text[:5000]},  # LINE 單則上限 5000 字
            ],
        },
        timeout=30,
    )

    print("LINE Status:", response.status_code)
    print("LINE Response:", response.text)
    response.raise_for_status()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=None, help="交易日 YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="只印出、不推播")
    parser.add_argument("--force", action="store_true", help="忽略已推播記號，強制重推")
    args = parser.parse_args()

    if args.date:
        target = parse_date(args.date)
    else:
        now = datetime.now(TAIPEI)
        if not (AUTO_START_HOUR <= now.hour < AUTO_END_HOUR):
            print(f"[略過] 台北時間 {now:%Y-%m-%d %H:%M} 不在自動推播時段"
                  f"（{AUTO_START_HOUR}:00～{AUTO_END_HOUR}:00），本次不執行。"
                  "如需補推請手動指定 --date。")
            return
        target = now.date()

    day = target.isoformat()
    sha = None
    if not (args.dry_run or args.force):
        last, sha = read_sentinel()
        if last == day:
            print(f"[略過] {day} 文字已推播過，本次不重複推播")
            return

    # 任何一項抓不到會直接報錯，不會推播半套或錯誤資料
    data = get_market_data(target)

    print_market_data(data)  # log 保留完整明細

    if args.dry_run:
        print("\n[dry-run] 未推播")
        return

    push_line(format_market_data(data))

    if not args.force:
        write_sentinel(day, sha)
    else:
        # 強制重推後也更新記號，避免之後的自動觸發再推一次
        _, sha = read_sentinel()
        write_sentinel(day, sha)


if __name__ == "__main__":
    main()
