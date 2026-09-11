import os
import subprocess
import requests


LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_USER_ID = os.environ["LINE_USER_ID"]


def get_market_message():
    env = os.environ.copy()

    result = subprocess.run(
        ["python", "fetch_market_data.py"],
        capture_output=True,
        text=True,
        env=env,
        check=True
    )

    output = result.stdout

    marker = "【盤後駐守"

    start = output.find(marker)

    if start == -1:
        raise RuntimeError(
            f"找不到盤後駐守訊息區塊。\n完整輸出：\n{output}"
        )

    message = output[start:].strip()

    # 移除最後用來顯示在 GitHub log 的分隔線
    lines = message.splitlines()

    while lines and set(lines[-1].strip()) <= {"="}:
        lines.pop()

    message = "\n".join(lines).strip()

    if not message:
        raise RuntimeError("LINE 訊息內容為空")

    return message


def send_line(message):
    url = "https://api.line.me/v2/bot/message/push"

    headers = {
        "Authorization": f"Bearer {LINE_TOKEN}",
        "Content-Type": "application/json",
    }

    data = {
        "to": LINE_USER_ID,
        "messages": [
            {
                "type": "text",
                "text": message
            }
        ]
    }

    response = requests.post(
        url,
        headers=headers,
        json=data,
        timeout=20
    )

    print("LINE Status:", response.status_code)
    print("LINE Response:", response.text)

    response.raise_for_status()


def main():
    message = get_market_message()

    print("準備傳送 LINE：")
    print("------------------------------")
    print(message)
    print("------------------------------")

    send_line(message)

    print("LINE 推播成功")


if __name__ == "__main__":
    main()
