"""
scheduler.py - Multi-Project Quant Report Scheduler
====================================================
Saskatchewan (SK), Canada is always UTC-6 (no daylight saving time).

Schedule:
  Morning:   6:00 AM SK  =  12:00 UTC
  Afternoon: 4:00 PM SK  =  22:00 UTC

Modes:
  python scheduler.py           # persistent daemon — start on boot or login
  python scheduler.py --once    # run all projects once right now
  python scheduler.py --test    # dry-run: print commands without executing

Projects orchestrated:
  1. HedgePoly      — Polymarket calibration alpha (400M-trade historical edge)
  2. Poly2/Kelly    — Gemini AI + Kelly criterion live opportunities
  3. Poly2/Macro1   — Polymarket macro intelligence (Fed, geopolitics, crypto)
  4. Poly2/Macro2   — Polymarket macro deep intelligence
  5. ModelTelegra   — Quant Desk: BTC vol, multi-asset trends, risk signals
  6. Poly           — Kelly position scraper (output forwarded to Telegram)
  7. PolyTraders    — Smart-money copy-trade signals (leaderboard + Kelly)
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


# ── Simple .env loader (stdlib only — no external deps) ───────────────────────
def _load_dotenv(env_path: Path) -> None:
    """Load key=value pairs from a .env file into os.environ (no-op if missing)."""
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())


# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECTS_DIR     = Path(__file__).resolve().parent
HEDGEPOLY_DIR    = PROJECTS_DIR / "HedgePoly" / "prediction-market-analysis"
POLY2_DIR        = PROJECTS_DIR / "Poly2"
MODEL_DIR        = PROJECTS_DIR / "ModelTelegra," / "quant_desk"
POLY_DIR         = PROJECTS_DIR / "Poly"
POLYTRADERS_DIR  = PROJECTS_DIR / "PolyTraders"
LOG_FILE         = PROJECTS_DIR / "scheduler.log"

# Load credentials from .env at project root (never commit .env)
_load_dotenv(PROJECTS_DIR / ".env")

# uv executable path (installed per-user)
UV = str(Path.home() / ".local" / "bin" / "uv.exe")
if not Path(UV).exists():
    UV = "uv"  # fall back to PATH

# ── Telegram credentials — loaded from .env, never hardcoded ──────────────────
TG_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ── Saskatchewan schedule — UTC-6, no DST ─────────────────────────────────────
# 6:00 AM SK = 12:00 UTC  |  4:00 PM SK = 22:00 UTC
SCHEDULE_UTC: list[tuple[int, int]] = [(12, 0), (22, 0)]

# Normal trigger window around the exact scheduled time (minutes)
TRIGGER_WINDOW_MINUTES = 2

# After waking from sleep, catch up any missed run within this many hours
CATCHUP_HOURS = 4

# Minimum gap between two runs of the same slot (minutes), prevents double-fire
COOLDOWN_MINUTES = 30

# ── Project definitions ────────────────────────────────────────────────────────
PROJECTS: list[dict] = [
    {
        "name": "HedgePoly Alpha Report",
        "cwd": HEDGEPOLY_DIR,
        "cmd": [UV, "run", "python", "send_report.py"],
        "timeout": 120,
        "capture_to_telegram": False,  # sends to Telegram natively
    },
    {
        # Poly2 scripts use type hints requiring Python 3.9+ — use uv's managed Python
        # uv installs 'requests' inline without a project venv
        "name": "Poly2 Kelly Bot",
        "cwd": POLY2_DIR,
        "cmd": [UV, "run", "--no-project", "--python", "3.9", "--with", "requests",
                "python", "polymarket_telegram_bot.py"],
        "timeout": 180,
        "capture_to_telegram": False,
    },
    {
        "name": "Poly2 Macro Report 1",
        "cwd": POLY2_DIR,
        "cmd": [UV, "run", "--no-project", "--python", "3.9", "--with", "requests",
                "python", "macro_report1.py"],
        "timeout": 240,
        "capture_to_telegram": False,
    },
    {
        "name": "Poly2 Macro Report 2",
        "cwd": POLY2_DIR,
        "cmd": [UV, "run", "--no-project", "--python", "3.9", "--with", "requests",
                "python", "macro_report2.py"],
        "timeout": 240,
        "capture_to_telegram": False,
    },
    {
        # ModelTelegra real project lives in quant_desk/ subdirectory
        # Needs: yfinance, python-telegram-bot, plotly, kaleido, python-dotenv
        "name": "ModelTelegra Quant Desk",
        "cwd": MODEL_DIR,
        "cmd": [UV, "run", "--no-project", "--python", "3.11",
                "--with", "yfinance,python-telegram-bot,plotly,kaleido,"
                          "pandas,numpy,scipy,aiohttp,python-dotenv",
                "python", "main.py"],
        "timeout": 360,
        "capture_to_telegram": False,
    },
    {
        # Poly scraper uses X|Y union syntax (PEP 604) — needs Python 3.10+
        # No native Telegram — we capture stdout and forward it
        "name": "Poly Kelly Scraper",
        "cwd": POLY_DIR,
        "cmd": [UV, "run", "--no-project", "--python", "3.11", "--with", "requests",
                "python", "polymarket_scraper.py"],
        "timeout": 120,
        "capture_to_telegram": True,
    },
    {
        # PolyTraders: smart-money copy-trade signals via leaderboard + Kelly
        "name": "PolyTraders Smart Money",
        "cwd": POLYTRADERS_DIR,
        "cmd": [UV, "run", "--no-project", "--python", "3.11",
                "--with", "requests,python-dotenv",
                "python", "main.py"],
        "timeout": 180,
        "capture_to_telegram": False,  # sends to Telegram natively
    },
]

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
    ],
)
log = logging.getLogger("scheduler")


# ── Telegram helper (stdlib only, no external deps) ───────────────────────────
def _tg_send(text: str, token: str = TG_TOKEN, chat_id: str = TG_CHAT_ID) -> bool:
    """Send a plain-text message to Telegram using only urllib (stdlib)."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    # Split long messages into 4000-char chunks
    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]
    ok = True
    for chunk in chunks:
        payload = json.dumps({
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": True,
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status != 200:
                    ok = False
        except Exception as exc:
            log.error(f"Telegram send failed: {exc}")
            ok = False
    return ok


# ── Project runner ─────────────────────────────────────────────────────────────
def run_project(project: dict, dry_run: bool = False) -> bool:
    """
    Run a single project as a subprocess.
    If capture_to_telegram=True, forwards stdout to Telegram.
    Returns True on success.
    """
    name = project["name"]
    cwd  = project["cwd"]
    cmd  = project["cmd"]
    timeout = project.get("timeout", 120)
    capture = project.get("capture_to_telegram", False)

    if not Path(cwd).exists():
        log.warning(f"[{name}] Directory not found: {cwd}  SKIP")
        return False

    log.info(f"[{name}] Starting ...")
    if dry_run:
        log.info(f"[{name}] DRY-RUN: {' '.join(str(c) for c in cmd)}  (cwd={cwd})")
        return True

    # Force UTF-8 I/O in subprocesses so emoji in reports don't crash on Windows
    import os
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=capture,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            env=env,
        )

        if result.returncode != 0:
            stderr_snippet = (result.stderr or "")[:500] if capture else "(not captured)"
            log.error(f"[{name}] Exit {result.returncode}: {stderr_snippet}")
            return False

        if capture and result.stdout:
            header = f"<b>Poly Kelly Scraper</b>\n<code>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</code>\n\n"
            _tg_send(header + result.stdout[:3500])
            log.info(f"[{name}] Output forwarded to Telegram ({len(result.stdout)} chars)")

        log.info(f"[{name}] Done (exit 0)")
        return True

    except subprocess.TimeoutExpired:
        log.error(f"[{name}] Timed out after {timeout}s")
        return False
    except FileNotFoundError as exc:
        log.error(f"[{name}] Command not found: {exc}")
        return False
    except Exception as exc:
        log.error(f"[{name}] Unexpected error: {exc}")
        return False


# ── Run all projects ───────────────────────────────────────────────────────────
def run_all(dry_run: bool = False) -> None:
    """Run every project in sequence, logging successes/failures."""
    now_utc = datetime.now(timezone.utc)
    utc_now = now_utc.strftime("%Y-%m-%d %H:%M UTC")
    sk_hour = (now_utc.hour - 6) % 24
    sk_now  = f"{sk_hour:02d}:{now_utc.minute:02d} SK"

    log.info("=" * 60)
    log.info(f"QUANT REPORT RUN -- {utc_now}  ({sk_now})")
    log.info("=" * 60)

    results: dict[str, str] = {}
    for project in PROJECTS:
        ok = run_project(project, dry_run=dry_run)
        results[project["name"]] = "OK" if ok else "FAIL"

    log.info("=" * 60)
    log.info("SUMMARY:")
    for name, status in results.items():
        log.info(f"  {status:<6} {name}")
    log.info("=" * 60)

    # Send a summary notification to Telegram (unless dry-run)
    if not dry_run:
        fails = [n for n, s in results.items() if s == "FAIL"]
        if fails:
            summary = (
                f"Scheduler run at {utc_now}\n"
                f"FAILURES ({len(fails)}/{len(results)}):\n"
                + "\n".join(f"  - {f}" for f in fails)
            )
            _tg_send(summary)


# ── Daemon loop ────────────────────────────────────────────────────────────────
def is_scheduled_now(last_run_times: dict[tuple[int, int], float]) -> tuple[int, int] | None:
    """
    Return the schedule slot to run now, or None.

    Handles two cases:
      1. Normal: current UTC time is within TRIGGER_WINDOW_MINUTES of a slot.
      2. Catch-up: laptop was asleep at the scheduled time and just woke up.
         We fire the missed run if it occurred within the last CATCHUP_HOURS
         and we haven't already run it today.
    """
    now = datetime.now(timezone.utc)
    today = now.date()

    for (h, m) in SCHEDULE_UTC:
        # When was this slot supposed to fire today?
        from datetime import timedelta
        scheduled_today = datetime(
            today.year, today.month, today.day, h, m, tzinfo=timezone.utc
        )

        # Cooldown: ran recently (prevents double-fire even in catch-up)
        last = last_run_times.get((h, m), 0.0)
        if time.time() - last < COOLDOWN_MINUTES * 60:
            continue

        seconds_past = (now - scheduled_today).total_seconds()

        # Case 1: exact trigger window (normal path)
        if abs(seconds_past) <= TRIGGER_WINDOW_MINUTES * 60:
            return (h, m)

        # Case 2: catch-up — slot passed while we were asleep
        # Only catch up if: slot was today, already passed, within CATCHUP_HOURS
        if 0 < seconds_past <= CATCHUP_HOURS * 3600:
            # Haven't run this slot today yet?
            if last == 0.0 or datetime.fromtimestamp(last, tz=timezone.utc).date() < today:
                log.info(
                    f"Catch-up: slot {h:02d}:{m:02d} UTC missed "
                    f"{seconds_past/60:.0f} min ago (laptop was likely asleep)"
                )
                return (h, m)

    return None


def run_daemon() -> None:
    """Persistent loop: check time every 30 seconds and run at scheduled times."""
    last_run_times: dict[tuple[int, int], float] = {}

    log.info("Daemon started -- checking every 30s (catch-up window: 4h)")
    slots_str = "  |  ".join(
        f"{h:02d}:{m:02d} UTC (= {((h-6)%24):02d}:{m:02d} SK)"
        for h, m in SCHEDULE_UTC
    )
    log.info(f"Scheduled slots: {slots_str}")
    log.info("Press Ctrl+C to stop")

    while True:
        slot = is_scheduled_now(last_run_times)
        if slot:
            last_run_times[slot] = time.time()
            run_all()
        time.sleep(30)


# ── Entry point ────────────────────────────────────────────────────────────────
def main() -> None:
    args = sys.argv[1:]
    if "--once" in args:
        run_all(dry_run=False)
    elif "--test" in args:
        run_all(dry_run=True)
    else:
        run_daemon()


if __name__ == "__main__":
    main()
