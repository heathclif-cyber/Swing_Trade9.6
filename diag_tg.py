import requests
import json
TELEGRAM_BOT_TOKEN = "8728046864:AAEaLD5c1yJRuTjoNKRLbyzkBII2AJKV9hE"
TELEGRAM_CHAT_ID = "982913105"

def send_test():
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    # Test 1: plain
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": "Dashboard Tracker Test"
    }
    r = requests.post(url, json=payload)
    print(f"Plain: {r.status_code} - {r.text}")

    # Test 2: HTML
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": "<b>HTML <i>Test</i></b>",
        "parse_mode": "HTML"
    }
    r = requests.post(url, json=payload)
    print(f"HTML: {r.status_code} - {r.text}")

if __name__ == "__main__":
    send_test()
