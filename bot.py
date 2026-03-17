"""
bot.py — Interactive Telegram command bot for quant reports.

Commands
--------
  /run polytraders   Run PolyTraders smart-money signals
  /run hedgepoly     Run HedgePoly alpha report
  /run poly2         Run Poly2 Kelly + Macro reports
  /run modeltelegra  Run ModelTelegra quant desk
  /run all           Run every project (same as scheduler --once)
  /status            Show last run times
  /help              List commands

Security
--------
  Only responds to TELEGRAM_CHAT_ID — all other senders are silently ignored.

Deploy
------
  python bot.py                          # polling mode (production)
  TELEGRAM_BOT_TOKEN=... python bot.py   # explicit env

  On Render: Background Worker — Start Command: python bot.py
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ── Load .env (stdlib fallback) ───────────────────────────────────────────────
def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

_load_dotenv(Path(__file__).parent / ".env")

# ── Config ────────────────────────────────────────────────────────────────────
TG_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

PROJECTS_DIR    = Path(__file__).resolve().parent
HEDGEPOLY_DIR   = PROJECTS_DIR / "HedgePoly" / "prediction-market-analysis"
POLY2_DIR       = PROJECTS_DIR / "Poly2"
MODEL_DIR       = PROJECTS_DIR / "ModelTelegra," / "quant_desk"
POLY_DIR        = PROJECTS_DIR / "Poly"
POLYTRADERS_DIR = PROJECTS_DIR / "PolyTraders"

UV = str(Path.home() / ".local" / "bin" / "uv")
if not Path(UV).exists():
    UV = "uv"

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("bot")

# ── Project definitions (same as scheduler.py) ────────────────────────────────
RUNNABLE: dict[str, dict] = {
    "polytraders": {
        "label": "PolyTraders Smart Money",
        "cwd": POLYTRADERS_DIR,
        "cmd": [UV, "run", "--no-project", "--python", "3.11",
                "--with", "requests,python-dotenv", "python", "main.py"],
        "timeout": 180,
    },
    "hedgepoly": {
        "label": "HedgePoly Alpha Report",
        "cwd": HEDGEPOLY_DIR,
        "cmd": [UV, "run", "python", "send_report.py"],
        "timeout": 120,
    },
    "poly2": {
        "label": "Poly2 Kelly + Macro",
        "cwd": POLY2_DIR,
        "cmd": [UV, "run", "--no-project", "--python", "3.9",
                "--with", "requests", "python", "polymarket_telegram_bot.py"],
        "timeout": 240,
    },
    "modeltelegra": {
        "label": "ModelTelegra Quant Desk",
        "cwd": MODEL_DIR,
        "cmd": [UV, "run", "--no-project", "--python", "3.11",
                "--with", "yfinance,python-telegram-bot,plotly,kaleido,"
                          "pandas,numpy,scipy,aiohttp,python-dotenv",
                "python", "main.py"],
        "timeout": 360,
    },
}

# Track last run times: project_key → ISO timestamp string
_last_run: dict[str, str] = {}

# ── Telegram helpers ──────────────────────────────────────────────────────────

def _tg_api(method: str, payload: dict, timeout: int = 15) -> dict | None:
    url = f"https://api.telegram.org/bot{TG_TOKEN}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        log.error("Telegram API %s failed: %s", method, exc)
        return None


def _send(text: str, chat_id: str | None = None) -> None:
    cid = chat_id or TG_CHAT_ID
    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        _tg_api("sendMessage", {
            "chat_id": cid,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        })


def _get_updates(offset: int) -> list[dict]:
    # Socket timeout must exceed Telegram's long-poll timeout (30s) by a margin
    result = _tg_api("getUpdates", {"offset": offset, "timeout": 30, "limit": 10}, timeout=35)
    if result and result.get("ok"):
        return result.get("result", [])
    return []

# ── Runner ────────────────────────────────────────────────────────────────────

def _run_project(key: str) -> tuple[bool, str]:
    """Run one project. Returns (success, message)."""
    proj = RUNNABLE[key]
    cwd = Path(proj["cwd"])
    if not cwd.exists():
        return False, f"Directory not found: <code>{cwd}</code>"

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        result = subprocess.run(
            proj["cmd"],
            cwd=str(cwd),
            timeout=proj["timeout"],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            env=env,
        )
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        _last_run[key] = ts
        if result.returncode != 0:
            snippet = (result.stderr or result.stdout or "no output")[:500]
            return False, f"Exit {result.returncode}:\n<code>{snippet}</code>"
        return True, f"Done at {ts}"
    except subprocess.TimeoutExpired:
        return False, f"Timed out after {proj['timeout']}s"
    except FileNotFoundError:
        return False, f"Command not found — is <code>uv</code> installed?"
    except Exception as exc:
        return False, str(exc)

# ── Command handlers ──────────────────────────────────────────────────────────

def _handle_run(args: str, chat_id: str) -> None:
    key = args.strip().lower()

    if key == "all":
        _send("Running all projects — this may take a few minutes…", chat_id)
        results = {}
        for k, proj in RUNNABLE.items():
            _send(f"Starting <b>{proj['label']}</b>…", chat_id)
            ok, msg = _run_project(k)
            results[k] = (ok, msg)
        lines = ["<b>Run complete</b>\n"]
        for k, (ok, msg) in results.items():
            icon = "✅" if ok else "❌"
            lines.append(f"{icon} <b>{RUNNABLE[k]['label']}</b>: {msg}")
        _send("\n".join(lines), chat_id)
        return

    if key not in RUNNABLE:
        keys = ", ".join(f"<code>{k}</code>" for k in RUNNABLE)
        _send(f"Unknown project. Available: {keys}", chat_id)
        return

    label = RUNNABLE[key]["label"]
    _send(f"Running <b>{label}</b>…", chat_id)
    ok, msg = _run_project(key)
    icon = "✅" if ok else "❌"
    _send(f"{icon} <b>{label}</b>: {msg}", chat_id)


def _handle_status(chat_id: str) -> None:
    if not _last_run:
        _send("No projects have been run this session yet.", chat_id)
        return
    lines = ["<b>Last run times this session:</b>\n"]
    for key, ts in _last_run.items():
        lines.append(f"  • <b>{RUNNABLE[key]['label']}</b>: {ts}")
    _send("\n".join(lines), chat_id)


def _handle_help(chat_id: str) -> None:
    available = "\n".join(
        f"  <code>/run {k}</code>  —  {v['label']}"
        for k, v in RUNNABLE.items()
    )
    _send(
        "<b>Quant Bot — Commands</b>\n\n"
        f"{available}\n"
        "  <code>/run all</code>  —  Run every project\n"
        "  <code>/status</code>   —  Last run times\n"
        "  <code>/help</code>     —  This message",
        chat_id,
    )

# ── Message dispatcher ────────────────────────────────────────────────────────

def _dispatch(message: dict) -> None:
    chat_id = str(message.get("chat", {}).get("id", ""))
    text    = message.get("text", "").strip()

    # Security: only respond to the configured chat
    if chat_id != str(TG_CHAT_ID):
        log.warning("Ignored message from unauthorized chat %s", chat_id)
        return

    if not text.startswith("/"):
        return

    # Strip bot username suffix (e.g. /run@MyBot → /run)
    cmd_full = text.lstrip("/").split("@")[0]
    parts    = cmd_full.split(None, 1)
    cmd      = parts[0].lower()
    args     = parts[1] if len(parts) > 1 else ""

    log.info("Command: /%s %s (from chat %s)", cmd, args, chat_id)

    if cmd == "run":
        _handle_run(args, chat_id)
    elif cmd == "status":
        _handle_status(chat_id)
    elif cmd in ("help", "start"):
        _handle_help(chat_id)
    else:
        _send(f"Unknown command <code>/{cmd}</code>. Try /help", chat_id)

# ── Polling loop ──────────────────────────────────────────────────────────────

def main() -> None:
    if not TG_TOKEN or not TG_CHAT_ID:
        log.error("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set.")
        sys.exit(1)

    log.info("Bot started — polling for commands (chat_id=%s)", TG_CHAT_ID)
    _send("Bot online. Type /help to see available commands.")

    offset = 0
    while True:
        try:
            updates = _get_updates(offset)
        except KeyboardInterrupt:
            log.info("Shutting down.")
            break
        except Exception as exc:
            log.error("getUpdates error: %s — retrying in 5s", exc)
            time.sleep(5)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            msg = update.get("message") or update.get("edited_message")
            if msg:
                try:
                    _dispatch(msg)
                except Exception as exc:
                    log.error("Dispatch error: %s", exc)

        # Long polling already waits 30s server-side; no extra sleep needed
        if not updates:
            time.sleep(1)


if __name__ == "__main__":
    main()
