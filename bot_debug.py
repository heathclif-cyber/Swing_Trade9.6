import requests
import json
TOKEN = "8728046864:AAEaLD5c1yJRuTjoNKRLbyzkBII2AJKV9hE"

def debug_bot():
    print("Testing Bot Token...")
    r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getMe")
    print(f"getMe status: {r.status_code}")
    print(r.json())
    
    print("\nRecent Updates...")
    r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates")
    print(f"getUpdates status: {r.status_code}")
    try:
        data = r.json()
        print(json.dumps(data, indent=2))
        
        # Try to find user Nuruddin / any recent chat
        if data.get("ok") and data.get("result"):
            found_id = data["result"][-1]["message"]["chat"]["id"]
            print(f"\nPotential Chat ID: {found_id}")
    except:
        print("Failed to parsed updates")

if __name__ == "__main__":
    debug_bot()
