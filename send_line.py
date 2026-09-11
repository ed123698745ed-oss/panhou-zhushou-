import argparse
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
    args = parser.parse_args()

    if args.date:
        target = parse_date(args.date)
    else:
        target = datetime.now(TAIPEI).date()

    # 任何一項抓不到會直接報錯，不會推播半套或錯誤資料
    data = get_market_data(target)

    print_market_data(data)  # log 保留完整明細

    if args.dry_run:
        print("\n[dry-run] 未推播")
        return

    push_line(format_market_data(data))


if __name__ == "__main__":
    main()
