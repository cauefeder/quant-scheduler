"""
send_report.py - One-shot Polymarket alpha report sender.

Generates the alpha report from live Polymarket data + historical calibration
and sends it directly to Telegram without starting the bot framework.

Usage:
    python send_report.py
    python send_report.py --config config.toml
    python send_report.py --preview      (print report, don't send)
"""

import sys
import httpx
from pathlib import Path

from utils import load_config, log
from reporting import build_telegram_report


def send_telegram_message(token: str, chat_id: int, text: str, preview: bool = False) -> bool:
    """Send an HTML-formatted message via Telegram Bot API."""
    if preview:
        return True

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    # Telegram limit is 4096 chars; split cleanly on newlines
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > 4000:
            if current:
                chunks.append(current)
            current = line
        else:
            current += ("\n" if current else "") + line
    if current:
        chunks.append(current)

    with httpx.Client(timeout=15.0) as client:
        for i, chunk in enumerate(chunks, 1):
            try:
                resp = client.post(url, json={
                    "chat_id": chat_id,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                })
                if resp.status_code != 200:
                    log.error(f"Telegram error {resp.status_code}: {resp.text[:300]}")
                    return False
                log.info(f"  Chunk {i}/{len(chunks)} sent.")
            except httpx.RequestError as e:
                log.error(f"Request failed: {e}")
                return False
    return True


def main():
    config_path = "config.toml"
    preview_only = False

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--config" and i + 1 < len(args):
            config_path = args[i + 1]
            i += 2
        elif args[i] == "--preview":
            preview_only = True
            i += 1
        else:
            config_path = args[i]
            i += 1

    log.info(f"Loading config from {config_path}")
    cfg = load_config(config_path)

    if not preview_only:
        if not cfg.bot_token or cfg.bot_token == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
            log.error("No bot token configured. Set [telegram].bot_token in config.toml")
            sys.exit(1)
        if not cfg.allowed_chat_ids:
            log.error("No chat IDs configured. Set allowed_chat_ids in config.toml")
            sys.exit(1)

    log.info("Building report from live Polymarket data + historical calibration...")
    try:
        report = build_telegram_report(cfg)
    except Exception as e:
        log.error(f"Report generation failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    log.info(f"Report generated ({len(report)} chars)")
    print("\n" + "=" * 60)
    print("REPORT PREVIEW")
    print("=" * 60)
    # Strip HTML tags for console display
    import re
    plain = re.sub(r"<[^>]+>", "", report)
    print(plain)
    print("=" * 60 + "\n")

    if preview_only:
        log.info("Preview mode - not sending to Telegram.")
        return

    for chat_id in cfg.allowed_chat_ids:
        log.info(f"Sending to chat {chat_id}...")
        ok = send_telegram_message(cfg.bot_token, chat_id, report)
        if ok:
            log.info(f"  Successfully sent to {chat_id}")
        else:
            log.error(f"  Failed to send to {chat_id}")

    log.info("Done.")


if __name__ == "__main__":
    main()
