"""
Polymarket -> Gemini AI -> Telegram Bot Pipeline (100% FREE)
=============================================================
1. Scrapes Polymarket (free, no auth)
2. Filters for short-term liquid markets
3. Sends best candidates to Gemini 2.5 Flash (FREE, no credit card)
4. Gemini returns probability estimates + reasoning
5. Kelly Criterion calculates position sizes
6. Results posted to Telegram group

ALL FREE:
- Polymarket API: free, no auth
- Gemini API: free, 250 req/day (you need ~48)
- Telegram Bot API: free, unlimited

Requirements:
    pip install requests

Setup:
    1. Get Telegram bot token from @BotFather
    2. Get free Gemini API key from aistudio.google.com
    3. Copy .env.example to .env and fill in your keys
    4. Add bot to your Telegram group
    5. Run: python polymarket_telegram_bot.py

Usage:
    python polymarket_telegram_bot.py                       # one-shot scan
    python polymarket_telegram_bot.py --loop --interval 30  # auto every 30 min
    python polymarket_telegram_bot.py --bankroll 5000 --kelly-fraction 0.25
"""

import argparse
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

try:
    import requests
except ImportError:
    print("Missing dependency. Run:  pip install requests")
    sys.exit(1)


# --- Configuration --------------------------------------------------------

def load_env():
    """Load .env file if it exists (Windows UTF-8 safe)."""
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
CLOB_API = "https://clob.polymarket.com"
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models"
TELEGRAM_API = "https://api.telegram.org/bot{token}"


# --- Data Models ----------------------------------------------------------

@dataclass
class MarketOutcome:
    token_id: str
    outcome: str
    market_price: float
    best_bid: float = 0.0
    best_ask: float = 0.0
    spread: float = 0.0

@dataclass
class Market:
    condition_id: str
    question: str
    slug: str
    end_date: Optional[datetime] = None
    days_to_resolution: Optional[float] = None
    volume: float = 0.0
    volume_24h: float = 0.0
    liquidity: float = 0.0
    outcomes: list[MarketOutcome] = field(default_factory=list)
    category: str = ""
    active: bool = True

@dataclass
class AIEstimate:
    condition_id: str
    true_probability: float
    confidence: str
    reasoning: str
    key_factors: list[str]

@dataclass
class KellyBet:
    market: Market
    outcome: MarketOutcome
    ai_estimate: AIEstimate
    edge: float
    kelly_fraction: float
    adj_kelly_fraction: float
    bet_size_usd: float
    expected_value: float
    odds_decimal: float
    score: float


# --- Polymarket API (free, no auth) ---------------------------------------

class PolymarketAPI:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "PolyKellyBot/2.0",
            "Accept": "application/json",
        })

    def _get(self, url, params=None):
        try:
            resp = self.session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            print(f"  [Polymarket API Error] {e}")
            return None

    def get_markets(self, limit=100, offset=0):
        return self._get(f"{GAMMA_API}/markets", {
            "limit": limit, "offset": offset,
            "active": "true", "closed": "false",
            "order": "volume24hr", "ascending": "false",
        }) or []

    def get_orderbook(self, token_id):
        return self._get(f"{CLOB_API}/book", {"token_id": token_id})


# --- Market Scanner -------------------------------------------------------

class MarketScanner:
    def __init__(self, api: PolymarketAPI, max_days=30, min_volume_24h=500,
                 min_liquidity=1000):
        self.api = api
        self.max_days = max_days
        self.min_volume_24h = min_volume_24h
        self.min_liquidity = min_liquidity

    def scan(self, num_pages=3) -> list[Market]:
        raw_markets = []
        for page in range(num_pages):
            print(f"  Fetching page {page + 1}...")
            batch = self.api.get_markets(limit=100, offset=page * 100)
            if not batch:
                break
            raw_markets.extend(batch)
            time.sleep(0.3)

        markets = []
        for raw in raw_markets:
            m = self._parse(raw)
            if m and self._passes_filter(m):
                markets.append(m)

        print(f"  {len(markets)} markets pass filters. Getting orderbooks...")
        for i, market in enumerate(markets[:30]):
            self._enrich_orderbook(market)
            if (i + 1) % 10 == 0:
                time.sleep(0.3)

        return markets

    def _parse(self, raw: dict) -> Optional[Market]:
        try:
            end_date = None
            days_to_res = None
            end_str = raw.get("endDate") or raw.get("end_date_iso")
            if end_str:
                try:
                    end_date = datetime.fromisoformat(
                        end_str.replace("Z", "+00:00")
                    )
                    days_to_res = (
                        end_date - datetime.now(timezone.utc)
                    ).total_seconds() / 86400
                except (ValueError, TypeError):
                    pass

            outcomes = []
            tokens = raw.get("clobTokenIds")
            prices = raw.get("outcomePrices")
            names = raw.get("outcomes")
            if tokens and prices and names:
                try:
                    tids = json.loads(tokens) if isinstance(tokens, str) else tokens
                    prs = json.loads(prices) if isinstance(prices, str) else prices
                    nms = json.loads(names) if isinstance(names, str) else names
                except (json.JSONDecodeError, TypeError):
                    tids, prs, nms = [], [], []
                for tid, pr, nm in zip(tids, prs, nms):
                    try:
                        p = float(pr)
                    except (ValueError, TypeError):
                        p = 0.5
                    outcomes.append(
                        MarketOutcome(
                            token_id=str(tid), outcome=str(nm), market_price=p
                        )
                    )

            return Market(
                condition_id=raw.get("conditionId", ""),
                question=raw.get("question", "Unknown"),
                slug=raw.get("slug", ""),
                end_date=end_date,
                days_to_resolution=days_to_res,
                volume=float(raw.get("volume", 0) or 0),
                volume_24h=float(raw.get("volume24hr", 0) or 0),
                liquidity=float(
                    raw.get("liquidityClob", 0) or raw.get("liquidity", 0) or 0
                ),
                outcomes=outcomes,
                category=(
                    raw.get("groupItemTitle", "")
                    or raw.get("category", "")
                    or ""
                ),
                active=raw.get("active", True),
            )
        except Exception:
            return None

    def _passes_filter(self, m: Market) -> bool:
        if not m.active:
            return False
        if (
            m.days_to_resolution is None
            or m.days_to_resolution < 0
            or m.days_to_resolution > self.max_days
        ):
            return False
        if m.volume_24h < self.min_volume_24h:
            return False
        if len(m.outcomes) < 2:
            return False
        for o in m.outcomes:
            if o.outcome.lower() == "yes":
                if o.market_price < 0.05 or o.market_price > 0.95:
                    return False
        return True

    def _enrich_orderbook(self, market: Market):
        for outcome in market.outcomes:
            if not outcome.token_id:
                continue
            try:
                book = self.api.get_orderbook(outcome.token_id)
                if book:
                    bids = book.get("bids", [])
                    asks = book.get("asks", [])
                    if bids:
                        outcome.best_bid = float(bids[0].get("price", 0))
                    if asks:
                        outcome.best_ask = float(asks[0].get("price", 0))
                    if outcome.best_bid and outcome.best_ask:
                        outcome.spread = outcome.best_ask - outcome.best_bid
                time.sleep(0.1)
            except Exception:
                pass


# --- Gemini AI Estimator (FREE) -------------------------------------------

class GeminiEstimator:
    """
    Uses Google Gemini API (FREE) to estimate true probabilities.

    Free tier limits (Gemini 2.5 Flash):
      - 10 requests/minute
      - 250 requests/day
      - 250,000 tokens/minute
      - No credit card required

    Your bot uses ~48 requests/day (every 30 min) = well within limits.
    """

    SYSTEM_PROMPT = (
        "You are a prediction market analyst. Given markets with current prices, "
        "estimate the TRUE probability of YES outcomes. Consider: recent news, "
        "base rates, time to resolution, and market context. "
        "Respond ONLY with a valid JSON array. No markdown, no backticks, no text outside the JSON. "
        "Each item: {\"id\": string, \"prob\": float 0-1, \"conf\": \"low\"|\"medium\"|\"high\", "
        "\"reason\": string (max 20 words), \"factors\": [string, string] (max 2 items, 8 words each)}"
    )

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self.api_key = api_key
        self.model = model

    def estimate_batch(
        self, markets: list[Market], max_markets: int = 10
    ) -> list[AIEstimate]:
        if not self.api_key:
            print("  [Gemini] No API key set. Using heuristic fallback.")
            return self._fallback_estimates(markets[:max_markets])

        candidates = self._select_candidates(markets, max_markets)
        if not candidates:
            return []

        user_prompt = self._build_prompt(candidates)

        print(f"  [Gemini] Sending {len(candidates)} markets to {self.model}...")
        try:
            url = (
                f"{GEMINI_API}/{self.model}:generateContent"
                f"?key={self.api_key}"
            )

            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": self.SYSTEM_PROMPT + "\n\n" + user_prompt}
                        ]
                    }
                ],
                "generationConfig": {
                    "temperature": 0.3,
                    "maxOutputTokens": 1500,
                    "responseMimeType": "application/json",
                },
            }

            resp = requests.post(url, json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            # Extract text from Gemini response
            text = ""
            try:
                candidates_resp = data.get("candidates", [])
                if candidates_resp:
                    parts = candidates_resp[0].get("content", {}).get("parts", [])
                    for part in parts:
                        if "text" in part:
                            text += part["text"]
            except (KeyError, IndexError):
                pass

            # Log usage
            usage = data.get("usageMetadata", {})
            in_tok = usage.get("promptTokenCount", 0)
            out_tok = usage.get("candidatesTokenCount", 0)
            print(
                f"  [Gemini] Tokens: {in_tok} in + {out_tok} out "
                f"(FREE - model: {self.model})"
            )

            return self._parse_response(text, candidates)

        except requests.RequestException as e:
            print(f"  [Gemini API Error] {e}")
            if hasattr(e, "response") and e.response is not None:
                print(f"  [Gemini] Response: {e.response.text[:300]}")
            return self._fallback_estimates(candidates)
        except Exception as e:
            print(f"  [Gemini Parse Error] {e}")
            traceback.print_exc()
            return self._fallback_estimates(candidates)

    def _select_candidates(
        self, markets: list[Market], max_n: int
    ) -> list[Market]:
        scored = []
        for m in markets:
            yes_price = next(
                (o.market_price for o in m.outcomes if o.outcome.lower() == "yes"),
                None,
            )
            if yes_price is None or yes_price < 0.05 or yes_price > 0.95:
                continue

            uncertainty = 1.0 - abs(yes_price - 0.5) * 2
            volume_score = min(m.volume_24h / 50000, 1.0)
            urgency = 1.0 / max(m.days_to_resolution or 30, 0.5)
            spread_score = max(
                (o.spread for o in m.outcomes if o.outcome.lower() == "yes"),
                default=0,
            )

            score = (
                uncertainty * 30
                + volume_score * 30
                + urgency * 20
                + min(spread_score * 100, 1) * 20
            )
            scored.append((score, m))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored[:max_n]]

    def _build_prompt(self, markets: list[Market]) -> str:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        lines = [f"Date: {today}. Analyze these prediction markets:\n"]

        for m in markets:
            yes_price = next(
                (o.market_price for o in m.outcomes if o.outcome.lower() == "yes"),
                0.5,
            )
            spread = next(
                (o.spread for o in m.outcomes if o.outcome.lower() == "yes"), 0
            )
            days = (
                f"{m.days_to_resolution:.0f}d" if m.days_to_resolution else "?"
            )
            lines.append(
                f'- id:"{m.condition_id[:12]}" '
                f'q:"{m.question}" '
                f"p:{yes_price:.2f} spread:{spread:.3f} "
                f"vol24h:${m.volume_24h:.0f} "
                f"resolves:{days} cat:{m.category[:15]}"
            )

        return "\n".join(lines)

    def _parse_response(
        self, text: str, markets: list[Market]
    ) -> list[AIEstimate]:
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        try:
            items = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("[")
            end = text.rfind("]") + 1
            if start >= 0 and end > start:
                try:
                    items = json.loads(text[start:end])
                except json.JSONDecodeError:
                    print(f"  [Gemini] Failed to parse JSON response")
                    return self._fallback_estimates(markets)
            else:
                print(f"  [Gemini] No JSON array found in response")
                return self._fallback_estimates(markets)

        estimates = []
        for item in items:
            prob = float(item.get("prob", 0.5))
            prob = max(0.02, min(0.98, prob))
            estimates.append(
                AIEstimate(
                    condition_id=item.get("id", ""),
                    true_probability=prob,
                    confidence=item.get("conf", "low"),
                    reasoning=item.get("reason", "No reasoning"),
                    key_factors=item.get("factors", []),
                )
            )

        return estimates

    def _fallback_estimates(self, markets: list[Market]) -> list[AIEstimate]:
        estimates = []
        for m in markets:
            yes_price = next(
                (o.market_price for o in m.outcomes if o.outcome.lower() == "yes"),
                0.5,
            )
            spread = next(
                (o.spread for o in m.outcomes if o.outcome.lower() == "yes"), 0
            )
            adj = min(spread * 0.3, 0.05)
            if m.volume_24h < 5000 and 0.35 < yes_price < 0.65:
                adj += 0.02
            if m.days_to_resolution and m.days_to_resolution < 7:
                adj += 0.01

            estimates.append(
                AIEstimate(
                    condition_id=m.condition_id[:12],
                    true_probability=min(0.95, max(0.05, yes_price + adj)),
                    confidence="low",
                    reasoning="Heuristic estimate (no AI API key set)",
                    key_factors=["spread-based", "volume-based"],
                )
            )
        return estimates


# --- Kelly Criterion ------------------------------------------------------

class KellyCriterion:
    def __init__(
        self,
        bankroll: float,
        kelly_multiplier: float = 0.25,
        max_position_pct: float = 0.10,
        min_edge: float = 0.05,
    ):
        self.bankroll = bankroll
        self.kelly_multiplier = kelly_multiplier
        self.max_position_pct = max_position_pct
        self.min_edge = min_edge

    def calculate(self, market_prob: float, true_prob: float) -> dict:
        if (
            market_prob <= 0
            or market_prob >= 1
            or true_prob <= 0
            or true_prob >= 1
        ):
            return {
                "kelly": 0, "adj_kelly": 0, "bet": 0,
                "edge": 0, "ev": 0, "odds": 0,
            }

        decimal_odds = 1.0 / market_prob
        b = decimal_odds - 1.0
        p, q = true_prob, 1.0 - true_prob
        edge = p - market_prob

        kelly = max(0, min(1, (b * p - q) / b)) if b > 0 else 0
        adj_kelly = min(kelly * self.kelly_multiplier, self.max_position_pct)
        ev = (p * b) - q

        return {
            "kelly": kelly,
            "adj_kelly": adj_kelly,
            "bet": adj_kelly * self.bankroll,
            "edge": edge,
            "ev": ev,
            "odds": decimal_odds,
        }


# --- Risk Manager ---------------------------------------------------------

class RiskManager:
    def __init__(
        self,
        bankroll: float,
        max_single_pct=0.10,
        max_total_pct=0.40,
        max_correlated_pct=0.20,
        max_bets=8,
    ):
        self.bankroll = bankroll
        self.max_single = bankroll * max_single_pct
        self.max_total = bankroll * max_total_pct
        self.max_correlated = bankroll * max_correlated_pct
        self.max_bets = max_bets

    def apply_limits(self, bets: list[KellyBet]) -> list[KellyBet]:
        approved = []
        total_exp = 0.0
        cat_exp: dict[str, float] = {}

        for bet in bets:
            if len(approved) >= self.max_bets:
                break
            bet.bet_size_usd = min(bet.bet_size_usd, self.max_single)
            if total_exp + bet.bet_size_usd > self.max_total:
                remaining = self.max_total - total_exp
                if remaining < 5:
                    continue
                bet.bet_size_usd = remaining

            cat = bet.market.category or "general"
            ce = cat_exp.get(cat, 0)
            if ce + bet.bet_size_usd > self.max_correlated:
                remaining = self.max_correlated - ce
                if remaining < 5:
                    continue
                bet.bet_size_usd = remaining

            bet.adj_kelly_fraction = bet.bet_size_usd / self.bankroll
            approved.append(bet)
            total_exp += bet.bet_size_usd
            cat_exp[cat] = ce + bet.bet_size_usd

        return approved


# --- Telegram Bot ---------------------------------------------------------

class TelegramBot:
    """
    Sends results to Telegram group.

    Setup:
    1. Telegram -> @BotFather -> /newbot -> get token
    2. Add bot to group, send a message
    3. Visit https://api.telegram.org/bot<TOKEN>/getUpdates
    4. Find "chat":{"id":-XXXXXXXXX} -> that is your TELEGRAM_CHAT_ID
    """

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = TELEGRAM_API.format(token=bot_token)

    def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        if not self.bot_token or not self.chat_id:
            print("  [Telegram] No token/chat_id. Printing to console only.")
            return False

        try:
            chunks = self._split_message(text, 4096)
            for chunk in chunks:
                resp = requests.post(
                    f"{self.base_url}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": chunk,
                        "parse_mode": parse_mode,
                        "disable_web_page_preview": True,
                    },
                    timeout=10,
                )
                if resp.status_code != 200:
                    err = resp.text[:200]
                    print(f"  [Telegram Error] {resp.status_code}: {err}")
                    return False
                time.sleep(0.5)

            print(f"  [Telegram] Sent {len(chunks)} msg(s) to {self.chat_id}")
            return True

        except requests.RequestException as e:
            print(f"  [Telegram Error] {e}")
            return False

    def _split_message(self, text: str, max_len: int) -> list[str]:
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

    def format_report(
        self, bets: list[KellyBet], bankroll: float, model: str
    ) -> str:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [
            f"<b>POLYMARKET KELLY SCANNER</b>",
            f"{now}",
            f"Bankroll: <b>${bankroll:,.0f}</b> | AI: {model} (FREE)",
            "",
        ]

        if not bets:
            lines.append("No opportunities found meeting edge criteria.")
            return "\n".join(lines)

        total_exposure = sum(b.bet_size_usd for b in bets)
        total_ev = sum(b.expected_value * b.bet_size_usd for b in bets)

        lines.append(f"<b>{len(bets)} opportunities found</b>")
        lines.append(
            f"Total exposure: ${total_exposure:,.0f} "
            f"({total_exposure / bankroll * 100:.1f}%)"
        )
        lines.append(f"Expected profit: ${total_ev:,.2f}")
        lines.append("")

        for i, bet in enumerate(bets, 1):
            days = bet.market.days_to_resolution
            days_str = f"{days:.0f}d" if days else "?"
            conf_emoji = {
                "high": "G", "medium": "Y", "low": "R"
            }.get(bet.ai_estimate.confidence, "?")

            lines.extend([
                f"---",
                f"<b>#{i}</b> [{conf_emoji}] {bet.ai_estimate.confidence.upper()}",
                f"<b>{bet.market.question[:80]}</b>",
                f"",
                f"  Market: <b>{bet.outcome.market_price:.0%}</b> "
                f"-> Gemini: <b>{bet.ai_estimate.true_probability:.0%}</b>",
                f"  Edge: <b>{bet.edge:.1%}</b> | "
                f"EV: {bet.expected_value:.1%} | "
                f"Odds: {bet.odds_decimal:.2f}x",
                f"  Kelly: {bet.kelly_fraction:.1%} "
                f"-> Adj: {bet.adj_kelly_fraction:.1%}",
                f"  <b>BET: ${bet.bet_size_usd:.0f}</b> "
                f"({bet.adj_kelly_fraction:.1%} of bankroll)",
                f"  Resolves: {days_str} | "
                f"Vol24h: ${bet.market.volume_24h:,.0f}",
                f"  {bet.ai_estimate.reasoning[:80]}",
            ])

            if bet.ai_estimate.key_factors:
                factors = " | ".join(bet.ai_estimate.key_factors[:2])
                lines.append(f"  Factors: {factors}")

            if bet.market.slug:
                lines.append(
                    f'  <a href="https://polymarket.com/event/'
                    f'{bet.market.slug}">View on Polymarket</a>'
                )
            lines.append("")

        lines.extend([
            f"---",
            f"<i>Research tool only. Not financial advice.</i>",
        ])

        return "\n".join(lines)


# --- Main Pipeline --------------------------------------------------------

class Pipeline:
    def __init__(
        self,
        bankroll: float,
        kelly_fraction: float,
        max_days: int,
        min_edge: float,
        max_position: float,
        max_exposure: float,
        max_bets: int,
        pages: int,
        gemini_model: str,
    ):
        self.bankroll = bankroll
        self.min_edge = min_edge
        self.gemini_model = gemini_model

        self.poly_api = PolymarketAPI()
        self.scanner = MarketScanner(self.poly_api, max_days=max_days)
        self.ai = GeminiEstimator(GEMINI_API_KEY, model=gemini_model)
        self.kelly = KellyCriterion(
            bankroll, kelly_fraction, max_position, min_edge
        )
        self.risk_mgr = RiskManager(
            bankroll, max_position, max_exposure, 0.20, max_bets
        )
        self.telegram = TelegramBot(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
        self.pages = pages

    def run(self) -> list[KellyBet]:
        print("\n" + "=" * 55)
        print("  POLYMARKET -> GEMINI (FREE) -> KELLY -> TELEGRAM")
        print("=" * 55)

        # Step 1: Scan Polymarket
        print(f"\n  [1/5] Scanning Polymarket...")
        markets = self.scanner.scan(num_pages=self.pages)
        print(f"  Found {len(markets)} qualifying markets")

        if not markets:
            print("  No markets found.")
            self.telegram.send_message(
                "<b>Polymarket Scanner</b>\n"
                "No qualifying markets found this scan."
            )
            return []

        # Step 2: AI estimates
        print(f"\n  [2/5] Getting Gemini probability estimates...")
        estimates = self.ai.estimate_batch(markets, max_markets=10)
        print(f"  Got {len(estimates)} estimates")

        # Step 3: Kelly sizing
        print(f"\n  [3/5] Calculating Kelly positions...")
        bets = self._match_and_size(markets, estimates)
        print(f"  {len(bets)} bets with edge > {self.min_edge:.0%}")

        # Step 4: Risk management
        print(f"\n  [4/5] Applying risk limits...")
        approved = self.risk_mgr.apply_limits(bets)
        print(f"  {len(approved)} approved after risk limits")

        # Step 5: Send to Telegram
        print(f"\n  [5/5] Sending Telegram notification...")
        message = self.telegram.format_report(
            approved, self.bankroll, self.gemini_model
        )
        sent = self.telegram.send_message(message)
        if not sent:
            # Print to console as fallback
            print(f"\n{message}")

        return approved

    def _match_and_size(
        self, markets: list[Market], estimates: list[AIEstimate]
    ) -> list[KellyBet]:
        est_map = {e.condition_id: e for e in estimates}
        bets = []

        for market in markets:
            cid_short = market.condition_id[:12]
            estimate = est_map.get(cid_short)
            if not estimate:
                continue

            for outcome in market.outcomes:
                if outcome.outcome.lower() != "yes":
                    continue

                market_prob = outcome.market_price
                true_prob = estimate.true_probability
                edge = true_prob - market_prob

                if edge < self.min_edge:
                    continue

                k = self.kelly.calculate(market_prob, true_prob)
                if k["adj_kelly"] <= 0:
                    continue

                score = (
                    edge * 40
                    + k["ev"] * 30
                    + min(market.volume_24h / 50000, 1) * 15
                    + min(market.liquidity / 100000, 1) * 10
                    + (1 / max(market.days_to_resolution or 30, 0.5)) * 5
                )

                bets.append(
                    KellyBet(
                        market=market,
                        outcome=outcome,
                        ai_estimate=estimate,
                        edge=edge,
                        kelly_fraction=k["kelly"],
                        adj_kelly_fraction=k["adj_kelly"],
                        bet_size_usd=k["bet"],
                        expected_value=k["ev"],
                        odds_decimal=k["odds"],
                        score=score,
                    )
                )

        bets.sort(key=lambda b: b.score, reverse=True)
        return bets


# --- Entry Point ----------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Polymarket -> Gemini AI (FREE) -> Telegram Pipeline"
    )
    parser.add_argument(
        "--bankroll", type=float, default=1000.0,
        help="Bankroll in USD (default: 1000)",
    )
    parser.add_argument(
        "--kelly-fraction", type=float, default=0.25,
        help="Kelly fraction 0-1 (default: 0.25 = quarter Kelly)",
    )
    parser.add_argument(
        "--max-days", type=int, default=14,
        help="Max days to resolution (default: 14)",
    )
    parser.add_argument(
        "--min-edge", type=float, default=0.05,
        help="Minimum edge threshold (default: 0.05 = 5%%)",
    )
    parser.add_argument(
        "--max-position", type=float, default=0.10,
        help="Max single position %% (default: 0.10)",
    )
    parser.add_argument(
        "--max-exposure", type=float, default=0.40,
        help="Max total exposure %% (default: 0.40)",
    )
    parser.add_argument(
        "--max-bets", type=int, default=8,
        help="Max concurrent bets (default: 8)",
    )
    parser.add_argument(
        "--pages", type=int, default=2,
        help="Polymarket API pages (default: 2)",
    )
    parser.add_argument(
        "--model", type=str, default=GEMINI_MODEL,
        help="Gemini model (default: gemini-2.5-flash)",
    )
    parser.add_argument(
        "--loop", action="store_true",
        help="Run continuously",
    )
    parser.add_argument(
        "--interval", type=int, default=30,
        help="Minutes between scans in loop mode (default: 30)",
    )
    args = parser.parse_args()

    # Check config
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")
    if not GEMINI_API_KEY:
        missing.append("GEMINI_API_KEY (will use heuristic fallback)")

    if missing:
        print(f"\n  WARNING: Missing env vars: {', '.join(missing)}")
        print(f"  Set them in .env file. See README.md.\n")

    pipeline = Pipeline(
        bankroll=args.bankroll,
        kelly_fraction=args.kelly_fraction,
        max_days=args.max_days,
        min_edge=args.min_edge,
        max_position=args.max_position,
        max_exposure=args.max_exposure,
        max_bets=args.max_bets,
        pages=args.pages,
        gemini_model=args.model,
    )

    if args.loop:
        print(f"\n  Loop mode: scanning every {args.interval} minutes")
        print(f"  Press Ctrl+C to stop.\n")
        run_count = 0
        while True:
            try:
                run_count += 1
                print(f"\n{'='*55}")
                print(
                    f"  SCAN #{run_count} at "
                    f"{datetime.now().strftime('%H:%M:%S')}"
                )
                pipeline.run()
                print(f"  Next scan in {args.interval} minutes...")
                time.sleep(args.interval * 60)
            except KeyboardInterrupt:
                print(f"\n\n  Stopped after {run_count} scans.")
                break
    else:
        pipeline.run()


if __name__ == "__main__":
    main()
