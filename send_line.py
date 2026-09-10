import os
import requests

LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_USER_ID = os.environ["LINE_USER_ID"]

message = """
【盤後駐守｜測試訊息】

LINE 自動推播系統已成功啟動。
之後這裡會自動傳送每日台股盤後資料。
""".strip()

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

response = requests.post(url, headers=headers, json=data)

print("Status:", response.status_code)
print("Response:", response.text)

response.raise_for_status()
