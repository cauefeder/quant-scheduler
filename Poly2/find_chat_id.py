"""Run this to find your Telegram group chat ID."""
import requests
import os

# Load .env
with open(".env", encoding="utf-8") as f:
    for line in f:
        if line.strip() and "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ[k.strip()] = v.strip()

token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
url = f"https://api.telegram.org/bot{token}/getUpdates"
resp = requests.get(url, timeout=10)
data = resp.json()

if data.get("result"):
    seen = set()
    for update in data["result"]:
        msg = update.get("message") or update.get("my_chat_member", {})
        if isinstance(msg, dict):
            chat = msg.get("chat", {})
            cid = chat.get("id")
            if cid and cid not in seen:
                seen.add(cid)
                print(f"  Chat ID:  {cid}")
                print(f"  Type:     {chat.get('type', '?')}")
                print(f"  Title:    {chat.get('title', '(private chat)')}")
                print()
    if not seen:
        print("No chats found in updates.")
else:
    print("No updates found.")
    print()
    print("Make sure you:")
    print("  1. Added the bot to your group")
    print("  2. Sent a message in the group AFTER adding the bot")
    print("  3. Run this script again")
