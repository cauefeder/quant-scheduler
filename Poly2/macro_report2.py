"""
Polymarket Macro Intelligence Report
======================================
Scrapes Polymarket for macro/geopolitics/assets markets,
uses Gemini AI (FREE) to write a structured intelligence report,
and sends it to your Telegram group.

Categories covered:
- Macroeconomics (Fed rates, inflation, GDP, recession)
- Geopolitics (wars, elections, diplomacy, sanctions)
- Assets & Crypto (BTC, ETH, stocks, commodities)
- AI & Tech (model releases, regulations, companies)
- Politics (US and global)

Usage:
    python macro_report.py                        # one-shot report
    python macro_report.py --loop --interval 360  # every 6 hours
    python macro_report.py --categories crypto politics

Requirements:
    pip install requests
    Same .env file as the Kelly bot
"""

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from typing import Optional

try:
    import requests
except ImportError:
    print("Run: pip install requests")
    sys.exit(1)


# --- Configuration --------------------------------------------------------

def load_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(
                        key.strip(), value.strip().strip('"').strip("'")
                    )

load_env()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

GAMMA_API = "https://gamma-api.polymarket.com"
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models"
TELEGRAM_API = "https://api.telegram.org/bot{token}"

# --- Category Keywords for Market Classification -------------------------

CATEGORY_KEYWORDS = {
    "macro": {
        "name": "Macroeconomics",
        "emoji": "📊",
        "keywords": [
            "fed", "federal reserve", "interest rate", "rate cut", "rate hike",
            "inflation", "cpi", "pce", "gdp", "recession", "unemployment",
            "jobs", "nonfarm", "payroll", "treasury", "yield", "bond",
            "debt ceiling", "government shutdown", "deficit", "tariff",
            "trade war", "sanctions", "ecb", "bank of japan", "boj",
            "bank of england", "imf", "world bank",
        ],
    },
    "geopolitics": {
        "name": "Geopolitics & Global Affairs",
        "emoji": "🌍",
        "keywords": [
            "war", "ukraine", "russia", "china", "taiwan", "nato",
            "iran", "israel", "gaza", "hamas", "hezbollah", "north korea",
            "missile", "nuclear", "ceasefire", "peace", "invasion",
            "sanctions", "coup", "regime", "diplomacy", "summit",
            "un ", "united nations", "eu ", "european union", "brexit",
            "middle east", "africa", "india", "modi", "xi jinping",
            "putin", "zelensky", "military", "troops", "border",
        ],
    },
    "crypto": {
        "name": "Crypto & Digital Assets",
        "emoji": "₿",
        "keywords": [
            "bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "sol",
            "xrp", "dogecoin", "doge", "defi", "nft", "stablecoin",
            "usdc", "usdt", "binance", "coinbase", "sec crypto",
            "bitcoin etf", "halving", "mining", "blockchain",
            "memecoin", "altcoin", "token",
        ],
    },
    "stocks": {
        "name": "Stocks & Traditional Assets",
        "emoji": "📈",
        "keywords": [
            "s&p", "sp500", "nasdaq", "dow jones", "stock", "equity",
            "earnings", "revenue", "ipo", "market cap", "bull", "bear",
            "oil", "gold", "silver", "commodity", "wti", "brent",
            "apple", "aapl", "nvidia", "nvda", "tesla", "tsla",
            "microsoft", "msft", "amazon", "amzn", "google", "goog",
            "meta", "netflix", "etsy", "spy", "qqq",
        ],
    },
    "ai_tech": {
        "name": "AI & Technology",
        "emoji": "🤖",
        "keywords": [
            "openai", "anthropic", "google ai", "deepmind", "claude",
            "gpt", "gemini", "llama", "ai model", "artificial intelligence",
            "agi", "machine learning", "chatbot", "ai regulation",
            "ai safety", "chips act", "semiconductor", "tsmc",
            "ai act", "compute", "data center",
        ],
    },
    "politics": {
        "name": "US & Global Politics",
        "emoji": "🏛️",
        "keywords": [
            "trump", "biden", "harris", "republican", "democrat",
            "congress", "senate", "house", "election", "poll",
            "impeach", "supreme court", "executive order", "veto",
            "governor", "mayor", "primary", "nominee", "campaign",
            "doge ", "elon musk", "musk", "cabinet", "secretary",
            "fbi", "doj", "cia", "pardon", "indictment",
            "uk election", "france", "macron", "germany", "canada",
            "trudeau", "brazil", "lula", "mexico", "president",
        ],
    },
}


# --- Polymarket Scraper ---------------------------------------------------

class PolymarketScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "PolyMacroReport/1.0",
            "Accept": "application/json",
        })

    def fetch_markets(self, num_pages=5) -> list[dict]:
        all_markets = []
        for page in range(num_pages):
            print(f"  Fetching page {page + 1}...")
            try:
                resp = self.session.get(
                    f"{GAMMA_API}/markets",
                    params={
                        "limit": 100,
                        "offset": page * 100,
                        "active": "true",
                        "closed": "false",
                        "order": "volume24hr",
                        "ascending": "false",
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
                if not data:
                    break
                all_markets.extend(data)
            except requests.RequestException as e:
                print(f"  [API Error] {e}")
                break
            time.sleep(0.3)

        print(f"  Fetched {len(all_markets)} total markets")
        return all_markets

    def classify_and_filter(
        self, raw_markets: list[dict], categories: list[str]
    ) -> dict[str, list[dict]]:
        """Classify markets into categories by keyword matching."""
        classified: dict[str, list[dict]] = {cat: [] for cat in categories}

        for raw in raw_markets:
            question = (raw.get("question", "") or "").lower()
            group_title = (raw.get("groupItemTitle", "") or "").lower()
            slug = (raw.get("slug", "") or "").lower()
            search_text = f"{question} {group_title} {slug}"

            # Basic quality filters
            volume_24h = float(raw.get("volume24hr", 0) or 0)
            if volume_24h < 100:
                continue

            # Parse end date
            end_str = raw.get("endDate") or raw.get("end_date_iso")
            days_left = None
            if end_str:
                try:
                    end_dt = datetime.fromisoformat(
                        end_str.replace("Z", "+00:00")
                    )
                    days_left = (
                        end_dt - datetime.now(timezone.utc)
                    ).total_seconds() / 86400
                    if days_left < 0:
                        continue  # skip expired
                except (ValueError, TypeError):
                    pass

            # Parse YES price
            yes_price = None
            prices_str = raw.get("outcomePrices")
            if prices_str:
                try:
                    prices = (
                        json.loads(prices_str)
                        if isinstance(prices_str, str)
                        else prices_str
                    )
                    if prices:
                        yes_price = float(prices[0])
                except (json.JSONDecodeError, ValueError, TypeError, IndexError):
                    pass

            # Build clean market object
            market_info = {
                "question": raw.get("question", "Unknown"),
                "slug": raw.get("slug", ""),
                "yes_price": yes_price,
                "volume_24h": volume_24h,
                "volume_total": float(raw.get("volume", 0) or 0),
                "liquidity": float(
                    raw.get("liquidityClob", 0) or raw.get("liquidity", 0) or 0
                ),
                "days_left": days_left,
                "category_tag": raw.get("groupItemTitle", ""),
            }

            # Classify into categories
            for cat in categories:
                cat_info = CATEGORY_KEYWORDS.get(cat, {})
                keywords = cat_info.get("keywords", [])
                for kw in keywords:
                    if kw in search_text:
                        classified[cat].append(market_info)
                        break  # avoid duplicates in same category

        # Sort each category by volume and limit
        for cat in classified:
            classified[cat].sort(
                key=lambda m: m["volume_24h"], reverse=True
            )
            classified[cat] = classified[cat][:15]  # top 15 per category

        return classified


# --- Gemini Report Writer -------------------------------------------------

class GeminiReportWriter:
    """Uses Gemini (FREE) to write the intelligence report."""

    SYSTEM_PROMPT = """You are a macro intelligence analyst writing a concise briefing report.
You receive prediction market data from Polymarket showing crowd-estimated probabilities for real-world events.

Write a structured, professional report with these rules:
- Use the market prices as probability signals (e.g., price 0.72 = market thinks 72% likely)
- Highlight the most significant markets and what they signal
- Note any surprising odds or major shifts
- Keep each section to 3-5 bullet points maximum
- Be concise: the entire report should fit in ~2000 characters
- Write in a professional but readable tone
- End with a brief "Key Takeaways" section (2-3 sentences)
- Output ONLY the report text, no markdown code fences
- Use plain text with simple formatting (dashes for bullets)
- Do NOT use markdown bold (**) or headers (#)"""

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self.api_key = api_key
        self.model = model

    def generate_report(
        self, classified_markets: dict[str, list[dict]]
    ) -> str:
        if not self.api_key:
            print("  [Gemini] No API key. Generating basic report.")
            return self._basic_report(classified_markets)

        # Build data summary for Gemini
        prompt = self._build_prompt(classified_markets)

        print(f"  [Gemini] Generating report with {self.model}...")
        try:
            url = (
                f"{GEMINI_API}/{self.model}:generateContent"
                f"?key={self.api_key}"
            )
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": self.SYSTEM_PROMPT + "\n\n" + prompt}
                        ]
                    }
                ],
                "generationConfig": {
                    "temperature": 0.5,
                    "maxOutputTokens": 2500,
                },
            }

            resp = requests.post(url, json=payload, timeout=45)
            resp.raise_for_status()
            data = resp.json()

            text = ""
            try:
                parts = (
                    data.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [])
                )
                for part in parts:
                    if "text" in part:
                        text += part["text"]
            except (KeyError, IndexError):
                pass

            usage = data.get("usageMetadata", {})
            in_tok = usage.get("promptTokenCount", 0)
            out_tok = usage.get("candidatesTokenCount", 0)
            print(
                f"  [Gemini] Tokens: {in_tok} in + {out_tok} out (FREE)"
            )

            if text.strip():
                return text.strip()
            else:
                print("  [Gemini] Empty response, using basic report")
                return self._basic_report(classified_markets)

        except requests.RequestException as e:
            print(f"  [Gemini Error] {e}")
            return self._basic_report(classified_markets)
        except Exception as e:
            print(f"  [Gemini Error] {e}")
            traceback.print_exc()
            return self._basic_report(classified_markets)

    def _build_prompt(self, classified: dict[str, list[dict]]) -> str:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        lines = [
            f"Date: {today}",
            f"Write a Macro Intelligence Report based on these Polymarket prediction market odds:",
            "",
        ]

        for cat, markets in classified.items():
            if not markets:
                continue
            cat_info = CATEGORY_KEYWORDS.get(cat, {})
            cat_name = cat_info.get("name", cat)
            lines.append(f"=== {cat_name} ===")

            for m in markets[:10]:
                price_str = (
                    f"{m['yes_price']:.0%}" if m["yes_price"] is not None else "?"
                )
                days_str = (
                    f"{m['days_left']:.0f}d"
                    if m["days_left"] is not None
                    else "?"
                )
                lines.append(
                    f"- \"{m['question']}\" "
                    f"YES={price_str} "
                    f"vol24h=${m['volume_24h']:,.0f} "
                    f"resolves={days_str}"
                )

            lines.append("")

        return "\n".join(lines)

    def _basic_report(self, classified: dict[str, list[dict]]) -> str:
        """Fallback report without AI."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [
            f"POLYMARKET MACRO REPORT",
            f"{today}",
            "",
        ]

        for cat, markets in classified.items():
            if not markets:
                continue
            cat_info = CATEGORY_KEYWORDS.get(cat, {})
            cat_name = cat_info.get("name", cat)
            emoji = cat_info.get("emoji", "")
            lines.append(f"{emoji} {cat_name}")
            lines.append("")

            for m in markets[:8]:
                price_str = (
                    f"{m['yes_price']:.0%}"
                    if m["yes_price"] is not None
                    else "?"
                )
                days_str = (
                    f"{m['days_left']:.0f}d"
                    if m["days_left"] is not None
                    else "?"
                )
                lines.append(
                    f"  {price_str} - {m['question'][:70]}"
                )
                lines.append(
                    f"       vol=${m['volume_24h']:,.0f} | {days_str}"
                )

            lines.append("")

        if not any(classified.values()):
            lines.append("No markets found matching selected categories.")

        return "\n".join(lines)


# --- Telegram Sender ------------------------------------------------------

class TelegramSender:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = TELEGRAM_API.format(token=bot_token)

    def send(self, text: str) -> bool:
        if not self.bot_token or not self.chat_id:
            print("  [Telegram] No token/chat_id configured.")
            return False

        try:
            chunks = self._split(text, 4096)
            for chunk in chunks:
                resp = requests.post(
                    f"{self.base_url}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": chunk,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                    timeout=10,
                )
                if resp.status_code != 200:
                    # Retry without parse_mode if HTML fails
                    resp2 = requests.post(
                        f"{self.base_url}/sendMessage",
                        json={
                            "chat_id": self.chat_id,
                            "text": chunk,
                            "disable_web_page_preview": True,
                        },
                        timeout=10,
                    )
                    if resp2.status_code != 200:
                        print(
                            f"  [Telegram Error] {resp2.status_code}: "
                            f"{resp2.text[:200]}"
                        )
                        return False
                time.sleep(0.5)

            print(f"  [Telegram] Sent {len(chunks)} message(s)")
            return True

        except requests.RequestException as e:
            print(f"  [Telegram Error] {e}")
            return False

    def _split(self, text: str, max_len: int) -> list[str]:
        if len(text) <= max_len:
            return [text]
        chunks = []
        current = ""
        for line in text.split("\n"):
            if len(current) + len(line) + 1 > max_len:
                if current:
                    chunks.append(current)
                current = line
            else:
                current += ("\n" if current else "") + line
        if current:
            chunks.append(current)
        return chunks


# --- Main Pipeline --------------------------------------------------------

class MacroReportPipeline:
    def __init__(self, categories: list[str], pages: int):
        self.categories = categories
        self.pages = pages
        self.scraper = PolymarketScraper()
        self.writer = GeminiReportWriter(GEMINI_API_KEY, GEMINI_MODEL)
        self.telegram = TelegramSender(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)

    def run(self):
        print("\n" + "=" * 55)
        print("  POLYMARKET MACRO INTELLIGENCE REPORT")
        print("=" * 55)

        # Step 1: Fetch markets
        print(f"\n  [1/4] Fetching Polymarket data...")
        raw = self.scraper.fetch_markets(num_pages=self.pages)

        if not raw:
            print("  No markets found.")
            return

        # Step 2: Classify
        print(f"\n  [2/4] Classifying into categories...")
        classified = self.scraper.classify_and_filter(raw, self.categories)

        total = sum(len(v) for v in classified.values())
        for cat, markets in classified.items():
            cat_name = CATEGORY_KEYWORDS.get(cat, {}).get("name", cat)
            print(f"    {cat_name}: {len(markets)} markets")
        print(f"    Total: {total} classified markets")

        if total == 0:
            print("  No markets matched selected categories.")
            return

        # Step 3: Generate report with Gemini
        print(f"\n  [3/4] Writing report with Gemini AI...")
        report_body = self.writer.generate_report(classified)

        # Step 4: Format and send
        print(f"\n  [4/4] Sending to Telegram...")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # Build header
        header_lines = [
            f"POLYMARKET MACRO INTELLIGENCE",
            f"{now}",
            f"Categories: {', '.join(self.categories)}",
            f"Markets analyzed: {total}",
            "",
        ]

        # Build footer with top movers (highest volume)
        all_markets = []
        for markets in classified.values():
            all_markets.extend(markets)
        all_markets.sort(key=lambda m: m["volume_24h"], reverse=True)

        footer_lines = [
            "",
            "---",
            "TOP VOLUME MARKETS (24h):",
        ]
        for m in all_markets[:5]:
            price_str = (
                f"{m['yes_price']:.0%}"
                if m["yes_price"] is not None
                else "?"
            )
            footer_lines.append(
                f"  ${m['volume_24h']:,.0f} | "
                f"{price_str} YES | "
                f"{m['question'][:55]}"
            )

        footer_lines.extend([
            "",
            "Source: polymarket.com",
            "Data reflects crowd-estimated probabilities.",
        ])

        full_report = (
            "\n".join(header_lines)
            + report_body
            + "\n"
            + "\n".join(footer_lines)
        )

        # Print to console
        print(f"\n{'='*55}")
        print(full_report)
        print(f"{'='*55}")

        # Send to Telegram
        sent = self.telegram.send(full_report)
        if not sent:
            print("\n  Telegram send failed. Report printed above.")

        return full_report


# --- Entry Point ----------------------------------------------------------

ALL_CATEGORIES = list(CATEGORY_KEYWORDS.keys())

def main():
    parser = argparse.ArgumentParser(
        description="Polymarket Macro Intelligence Report"
    )
    parser.add_argument(
        "--categories",
        nargs="+",
        default=ALL_CATEGORIES,
        choices=ALL_CATEGORIES,
        help=f"Categories to include (default: all). "
             f"Options: {', '.join(ALL_CATEGORIES)}",
    )
    parser.add_argument(
        "--pages", type=int, default=5,
        help="Polymarket API pages to scan (default: 5)",
    )
    parser.add_argument(
        "--loop", action="store_true",
        help="Run continuously",
    )
    parser.add_argument(
        "--interval", type=int, default=360,
        help="Minutes between reports in loop mode (default: 360 = 6 hours)",
    )
    args = parser.parse_args()

    # Check config
    if not GEMINI_API_KEY:
        print("  WARNING: GEMINI_API_KEY not set. Will use basic report.")
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("  WARNING: Telegram not configured. Will print to console.")

    pipeline = MacroReportPipeline(
        categories=args.categories, pages=args.pages
    )

    if args.loop:
        print(f"\n  Loop mode: report every {args.interval} minutes")
        print(f"  Press Ctrl+C to stop.\n")
        run_count = 0
        while True:
            try:
                run_count += 1
                print(f"\n{'='*55}")
                print(
                    f"  REPORT #{run_count} at "
                    f"{datetime.now().strftime('%H:%M:%S')}"
                )
                pipeline.run()
                print(f"  Next report in {args.interval} minutes...")
                time.sleep(args.interval * 60)
            except KeyboardInterrupt:
                print(f"\n\n  Stopped after {run_count} reports.")
                break
    else:
        pipeline.run()


if __name__ == "__main__":
    main()
