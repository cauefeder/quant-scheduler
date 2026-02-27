"""
telegram_bot.py - Telegram Bot for Prediction Market Alpha Insights.

Commands:
  /start          - Welcome + overview
  /kelly <p> <q>  - Empirical Kelly sizing (price, estimated probability)
  /calibration <p>- Calibration bias at price level
  /surface <p> <d>- Full surface mispricing (price, days to resolution)
  /orderflow      - Latest maker vs taker stats
  /longshots      - Top longshot bias opportunities
  /report         - Full daily insight report (on demand)
  /help           - Command list

Scheduled: sends alpha report automatically every report_interval_hours.
"""

import json
import sys
import asyncio
import logging
import os
from pathlib import Path
from datetime import datetime

from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from utils import load_config, DataLoader, Config, log
from kelly import EmpiricalKellyEngine
from calibration import CalibrationEngine
from orderflow import OrderFlowEngine
from reporting import build_telegram_report

# -- Globals (initialized on startup) -----------------------------------------
cfg: Config = None
loader: DataLoader = None
kelly_engine: EmpiricalKellyEngine = None
cal_engine: CalibrationEngine = None
flow_engine: OrderFlowEngine = None
_report_task: asyncio.Task = None


def _is_process_alive(pid: int) -> bool:
    """Return True if a process with this PID exists."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _acquire_single_instance_lock(output_dir: str) -> Path:
    """Prevent multiple polling instances for the same bot token on one machine."""
    lock_path = Path(output_dir) / "telegram_bot.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if lock_path.exists():
        try:
            existing_pid = int(lock_path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            existing_pid = -1

        if _is_process_alive(existing_pid):
            log.error(
                "Another telegram_bot.py instance is already running "
                f"(PID {existing_pid}). Stop it before starting a new one."
            )
            sys.exit(1)

    lock_path.write_text(str(os.getpid()), encoding="utf-8")
    return lock_path


# -- Auth Check ----------------------------------------------------------------
def is_authorized(update: Update) -> bool:
    """Check if the user is authorized (if allowlist is configured)."""
    if not cfg.allowed_chat_ids:
        return True
    return update.effective_chat.id in cfg.allowed_chat_ids


# -- Scheduled Report ----------------------------------------------------------

async def _send_report_to_chats(bot, chat_ids: list[int]) -> None:
    """Generate and send the alpha report to all configured chat IDs."""
    try:
        report = await asyncio.to_thread(build_telegram_report, cfg)
    except Exception as e:
        log.error(f"Report generation failed: {e}")
        report = f"<b>Report generation failed</b>\n<code>{e}</code>"

    for chat_id in chat_ids:
        try:
            # Split at 4000 chars to stay under Telegram 4096 limit
            chunks = [report[i:i + 4000] for i in range(0, len(report), 4000)]
            for chunk in chunks:
                await bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
        except Exception as e:
            log.error(f"Failed to send report to chat {chat_id}: {e}")


async def _scheduled_report_loop(bot, chat_ids: list[int], interval_hours: float) -> None:
    """Background asyncio task: send report on startup then every N hours."""
    interval_secs = int(interval_hours * 3600)

    # Brief initial delay so bot is fully ready
    await asyncio.sleep(15)
    log.info("Sending startup alpha report...")
    await _send_report_to_chats(bot, chat_ids)

    while True:
        log.info(f"Next scheduled report in {interval_hours}h")
        await asyncio.sleep(interval_secs)
        log.info("Sending scheduled alpha report...")
        await _send_report_to_chats(bot, chat_ids)


# -- Command Handlers ----------------------------------------------------------

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    msg = (
        "<b>Prediction Market Alpha Engine</b>\n"
        "<b>----------------------------</b>\n\n"
        "Institutional-grade analysis on 400M+ trades.\n\n"
        "<b>Three Methods:</b>\n"
        "1. Empirical Kelly - Monte Carlo position sizing\n"
        "2. Calibration Surface - Price x Time mispricing\n"
        "3. Order Flow - Maker vs Taker profitability\n\n"
        "Type /help for all commands.\n"
        "Scheduled alpha reports sent automatically."
    )
    await update.message.reply_text(msg, parse_mode="HTML")


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    msg = (
        "<b>Available Commands</b>\n"
        "<b>------------------</b>\n\n"
        "<code>/kelly &lt;price&gt; &lt;est_prob&gt;</code>\n"
        "  Empirical Kelly sizing\n"
        "  Example: <code>/kelly 0.15 0.25</code>\n\n"
        "<code>/calibration &lt;price_cents&gt;</code>\n"
        "  Calibration bias at price\n"
        "  Example: <code>/calibration 5</code>\n\n"
        "<code>/surface &lt;price&gt; &lt;days&gt;</code>\n"
        "  Full surface mispricing\n"
        "  Example: <code>/surface 0.10 30</code>\n\n"
        "<code>/orderflow</code>\n"
        "  Maker vs Taker breakdown\n\n"
        "<code>/longshots</code>\n"
        "  Top longshot opportunities\n\n"
        "<code>/report</code>\n"
        "  Full alpha report (on demand)\n"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


async def cmd_kelly(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Run Empirical Kelly for user-specified parameters."""
    if not is_authorized(update):
        return

    args = ctx.args
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: <code>/kelly &lt;entry_price&gt; &lt;estimated_prob&gt;</code>\n"
            "Example: <code>/kelly 0.15 0.25</code>",
            parse_mode="HTML",
        )
        return

    try:
        price = float(args[0])
        prob = float(args[1])
    except ValueError:
        await update.message.reply_text("Price and probability must be numbers.")
        return

    if not (0 < price < 1) or not (0 < prob < 1):
        await update.message.reply_text("Both values must be between 0 and 1.")
        return

    await update.message.reply_text("Running Empirical Kelly analysis...")

    try:
        result = kelly_engine.calculate_empirical_kelly(
            entry_price=price,
            estimated_prob=prob,
            platform="polymarket",
            strategy_label=f"Custom ({price*100:.0f}c, est. {prob*100:.0f}%)",
        )
        await update.message.reply_text(
            f"<pre>{result.summary()}</pre>",
            parse_mode="HTML",
        )
    except Exception as e:
        await update.message.reply_text(f"Analysis failed: {e}")


async def cmd_calibration(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show calibration at a specific price level."""
    if not is_authorized(update):
        return

    args = ctx.args
    if not args:
        await update.message.reply_text(
            "Usage: <code>/calibration &lt;price_cents&gt;</code>\n"
            "Example: <code>/calibration 5</code> for 5c contracts",
            parse_mode="HTML",
        )
        return

    try:
        price_cent = int(args[0])
    except ValueError:
        await update.message.reply_text("Price must be an integer (cents).")
        return

    await update.message.reply_text("Building calibration curve...")

    try:
        cal_1d = cal_engine.build_1d_calibration("polymarket")
        row = cal_1d[cal_1d["price_cent"] == price_cent]
        if row.empty:
            await update.message.reply_text(f"No data for {price_cent}c contracts.")
            return

        r = row.iloc[0]
        mispricing = r["mispricing_pct"]
        if mispricing < -5:
            signal = "OVERPRICED - takers overpay at this level"
        elif mispricing > 5:
            signal = "UNDERPRICED - potential opportunity"
        else:
            signal = "FAIR - within noise margin"

        msg = (
            f"<b>Calibration at {price_cent}c</b>\n"
            f"Implied probability: {r['implied_prob']*100:.1f}%\n"
            f"Empirical win rate:  {r['empirical_prob']*100:.2f}%\n"
            f"Mispricing:          {mispricing:+.2f}pp\n"
            f"Sample size:         {int(r['n_trades']):,} trades\n\n"
            f"{signal}"
        )
        await update.message.reply_text(msg, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"Failed: {e}")


async def cmd_surface(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Lookup specific point on calibration surface."""
    if not is_authorized(update):
        return

    args = ctx.args
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: <code>/surface &lt;price&gt; &lt;days_to_resolution&gt;</code>\n"
            "Example: <code>/surface 0.10 30</code>",
            parse_mode="HTML",
        )
        return

    try:
        price = float(args[0])
        days = float(args[1])
    except ValueError:
        await update.message.reply_text("Invalid numbers.")
        return

    await update.message.reply_text("Building calibration surface...")

    try:
        surface = cal_engine.build_surface("polymarket")
        signal = surface.get_signal(price, days, cfg.signal_threshold)

        msg = (
            f"<b>Surface Lookup</b>\n"
            f"Price: {price*100:.0f}c | Time: {days:.0f} days\n"
            f"Signal: {signal}"
        )
        await update.message.reply_text(msg, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"Failed: {e}")


async def cmd_orderflow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show maker vs taker statistics."""
    if not is_authorized(update):
        return

    await update.message.reply_text("Analyzing order flow...")

    try:
        stats = flow_engine.analyze("polymarket")
        await update.message.reply_text(
            f"<pre>{stats.summary()}</pre>",
            parse_mode="HTML",
        )
    except Exception as e:
        await update.message.reply_text(f"Failed: {e}")


async def cmd_longshots(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Find top longshot bias opportunities."""
    if not is_authorized(update):
        return

    await update.message.reply_text("Scanning for longshot opportunities...")

    try:
        opps = cal_engine.get_longshot_opportunities("polymarket", top_n=5)
        if not opps:
            await update.message.reply_text("No opportunities found with sufficient data.")
            return

        lines = ["<b>Top Underpriced Opportunities</b>\n"]
        for i, b in enumerate(opps, 1):
            lines.append(
                f"<b>{i}. {b.price_bin_center*100:.0f}c</b> | "
                f"{b.time_bin_center:.0f}d remaining\n"
                f"   Implied: {b.implied_prob*100:.1f}% -> "
                f"Actual: {b.empirical_prob*100:.1f}%\n"
                f"   Mispricing: {b.mispricing:+.2f}pp | "
                f"n={b.n_trades:,}"
            )

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"Failed: {e}")


async def cmd_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Send a full alpha report on demand."""
    if not is_authorized(update):
        return

    await update.message.reply_text("Generating alpha report from live Polymarket data...")

    try:
        report = await asyncio.to_thread(build_telegram_report, cfg)
        chunks = [report[i:i + 4000] for i in range(0, len(report), 4000)]
        for chunk in chunks:
            await update.message.reply_text(
                chunk,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
    except Exception as e:
        await update.message.reply_text(f"Report failed: {e}")


# -- Bot Setup -----------------------------------------------------------------

async def post_init(application: Application):
    """Set bot commands and start scheduled report task."""
    global _report_task

    commands = [
        BotCommand("start", "Welcome & overview"),
        BotCommand("help", "List all commands"),
        BotCommand("kelly", "Empirical Kelly sizing"),
        BotCommand("calibration", "Calibration at price"),
        BotCommand("surface", "Surface mispricing lookup"),
        BotCommand("orderflow", "Maker vs Taker stats"),
        BotCommand("longshots", "Top longshot opportunities"),
        BotCommand("report", "Full alpha report (on demand)"),
    ]
    await application.bot.set_my_commands(commands)
    log.info("Bot commands registered.")

    # Start scheduled report loop if chat IDs and interval are configured
    if cfg.allowed_chat_ids and cfg.report_interval_hours > 0:
        _report_task = asyncio.create_task(
            _scheduled_report_loop(
                application.bot,
                cfg.allowed_chat_ids,
                cfg.report_interval_hours,
            )
        )
        log.info(
            f"Scheduled reports every {cfg.report_interval_hours}h, "
            f"first report in ~15s"
        )
    else:
        log.info("No scheduled reports (no chat IDs or interval = 0).")


def main():
    global cfg, loader, kelly_engine, cal_engine, flow_engine

    # Default config path is at project root
    config_path = "config.toml"
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--config" and i < len(sys.argv) - 1:
            config_path = sys.argv[i + 1]

    cfg = load_config(config_path)
    lock_path = _acquire_single_instance_lock(cfg.output_dir)

    if not cfg.bot_token or cfg.bot_token == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        log.error(
            "Telegram bot token not configured. "
            "Set it in config.toml under [telegram].bot_token"
        )
        sys.exit(1)

    # Initialize engines
    log.info("Initializing data loader and analysis engines...")
    loader = DataLoader(cfg)
    kelly_engine = EmpiricalKellyEngine(cfg, loader)
    cal_engine = CalibrationEngine(cfg, loader)
    flow_engine = OrderFlowEngine(cfg, loader)

    # Build application
    app = Application.builder().token(cfg.bot_token).post_init(post_init).build()

    # Register handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("kelly", cmd_kelly))
    app.add_handler(CommandHandler("calibration", cmd_calibration))
    app.add_handler(CommandHandler("surface", cmd_surface))
    app.add_handler(CommandHandler("orderflow", cmd_orderflow))
    app.add_handler(CommandHandler("longshots", cmd_longshots))
    app.add_handler(CommandHandler("report", cmd_report))

    log.info("Telegram bot starting... Press Ctrl+C to stop.")
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        try:
            if lock_path.exists():
                lock_path.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    main()
