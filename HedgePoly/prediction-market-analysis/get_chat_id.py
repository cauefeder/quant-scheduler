"""
Quick script to get your Telegram chat ID.

Usage:
1. Set your bot token below
2. Start your bot in Telegram (send /start to it)
3. Or add your bot to a group
4. Run this script
5. It will show all recent chat IDs
"""

import asyncio
from telegram import Bot

async def get_chat_ids(token):
    """Get recent chat IDs that have interacted with your bot."""
    bot = Bot(token=token)
    
    try:
        print("Fetching updates...")
        updates = await bot.get_updates()
        
        if not updates:
            print("\n⚠️  No messages found!")
            print("Please:")
            print("1. Start your bot in Telegram (send /start)")
            print("2. Or add the bot to your group and send a message")
            print("3. Run this script again\n")
            return
        
        print(f"\n✓ Found {len(updates)} updates\n")
        print("=" * 60)
        
        seen_chats = set()
        for update in updates:
            if update.message:
                chat = update.message.chat
                chat_id = chat.id
                
                if chat_id not in seen_chats:
                    seen_chats.add(chat_id)
                    
                    chat_type = chat.type
                    chat_name = chat.title or chat.first_name or "Unknown"
                    
                    print(f"Chat ID: {chat_id}")
                    print(f"Type: {chat_type}")
                    print(f"Name: {chat_name}")
                    print("-" * 60)
        
        print("\n💡 Copy the Chat ID you want to use and add it to config.toml")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        print("\nMake sure your bot token is correct!")

if __name__ == "__main__":
    print("Telegram Chat ID Finder")
    print("=" * 60)
    
    token = input("\nEnter your bot token: ").strip()
    
    if not token:
        print("❌ No token provided!")
    else:
        asyncio.run(get_chat_ids(token))
