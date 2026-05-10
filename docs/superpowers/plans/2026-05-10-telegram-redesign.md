# Telegram Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Apply @superpowers:test-driven-development discipline within each task and @superpowers:verification-before-completion before claiming any task green.

**Goal:** Rewrite the Poly Kelly Scraper Telegram report for mobile readability. Add a small reusable `lib/telegram_format` library for future projects. Add a 1-page style guide.

**Architecture:** Six pure-formatter helpers in `lib/telegram_format.py` at the Projetos root, imported by `Poly/polymarket_scraper.py` via the same `sys.path.insert` pattern the scraper already uses for `signal_tracker`. Refactor moves all non-report stdout to `logging.info` (stderr) so the scheduler captures only the rendered report. Other 7 projects untouched in this slice.

**Tech Stack:** Python 3.11+ (whatever the Poly project's `uv run --python 3.11` already pins); pytest for tests; no new runtime deps.

**Spec reference:** [docs/superpowers/specs/2026-05-10-telegram-redesign-design.md](../specs/2026-05-10-telegram-redesign-design.md). When a step says "per spec §X", that's the authoritative source for byte-exact format.

---

## Pre-flight checklist

Before Task 1:

- [ ] Working directory is `D:/OMNP - Quant/Projetos` (the Projetos repo, not Multi Factor)
- [ ] Branch is `feat/phase1-signal-tracking` (current branch; no need to switch)
- [ ] Last commit is `866954d` (`docs: telegram redesign spec — apply spec-reviewer recommendations`) or later
- [ ] `python --version` ≥ 3.10 reachable (the helpers use `tuple[str, str]` syntax)
- [ ] `python -m pytest --version` works
- [ ] No uncommitted edits to `Poly/polymarket_scraper.py`

---

## File structure (locked from spec §4)

```
D:/OMNP - Quant/Projetos/
├── lib/                                    ← T1 creates
│   ├── __init__.py                         ← T1
│   ├── telegram_format.py                  ← T2
│   └── tests/
│       ├── __init__.py                     ← T1
│       └── test_telegram_format.py         ← T2
│
├── Poly/
│   ├── polymarket_scraper.py               ← T3 + T4 modify
│   └── tests/
│       ├── __init__.py                     ← T5 (if missing)
│       ├── test_report_format.py           ← T5
│       └── golden/
│           ├── happy_path.txt              ← T5 scaffolds
│           ├── single_bet.txt              ← T5 scaffolds
│           └── empty.txt                   ← T5 scaffolds
│
├── docs/
│   ├── TELEGRAM_STYLE_GUIDE.md             ← T1
│   └── superpowers/
│       ├── specs/2026-05-10-telegram-redesign-design.md   (already committed)
│       └── plans/2026-05-10-telegram-redesign.md          (this file)
```

Untouched in this slice: `scheduler.py`, the other 7 telegram-emitting projects.

---

## Task 1: Directory bootstrap + style guide doc

**Files:**
- Create: `lib/__init__.py` (empty)
- Create: `lib/tests/__init__.py` (empty)
- Create: `docs/TELEGRAM_STYLE_GUIDE.md`

This task ships the structural scaffolding and the standalone documentation. No Python logic; commits before any tests run.

- [ ] **Step 1.1: Create the `lib/` package**

```bash
cd "/d/OMNP - Quant/Projetos"
mkdir -p lib/tests
python -c "open('lib/__init__.py', 'w').close()"
python -c "open('lib/tests/__init__.py', 'w').close()"
```

(Python `open(...).close()` writes a strictly-empty file across PowerShell, Git Bash, and POSIX shells. PowerShell's `echo > file` writes a UTF-16 BOM + newline — avoid.)

Verify:
```bash
ls lib/ lib/tests/
```
Expected: both directories exist with empty `__init__.py` files.

- [ ] **Step 1.2: Write `docs/TELEGRAM_STYLE_GUIDE.md`**

Create the file with this exact content (per spec §8):

````markdown
# Telegram Reports — Style Guide

These are conventions for messages that land in the quant-reports group
via `scheduler.py`. Mobile-first; group members read on phones in <30s.

## Principles

1. **Lead with the action.** Bet picker? Top 3 bets at the top. Risk
   monitor? The tripped alert, first line. Don't make the reader scroll
   past chrome to find what to actually do.

2. **Plain text + emoji + spacing. No HTML, no Markdown.** `parse_mode`
   is unset on `scheduler._tg_send`, so HTML/Markdown markup appears
   literally. (PolyTraders / HedgePoly send HTML via their own POST —
   that's a separate path and OK there.)

3. **Mobile fonts mangle box-drawing.** Avoid `╔═╗║┌├└─`. The single
   character `│` at the left of a summary panel is fine; multi-line
   tables are not.

4. **Stay under 3,500 chars.** Scheduler truncates at 3,500. If you
   need more, paginate (rare for slice-1-class projects) or link out
   to a richer artifact.

5. **Group stdout-only output cleanly.** Diagnostic noise → `log.info` →
   stderr → `scheduler.log` only. Telegram-bound output → `print` →
   stdout. One `print()` at the end of `main()` composing all rendered
   helpers, ideally.

## Recommended emoji conventions

| Project type        | Header emoji | Status emoji                          |
|---------------------|--------------|---------------------------------------|
| Trade tickets       | 📊 / 🎯       | 🟢 trade · ⚪ no-trade · 🔴 degraded   |
| Risk / vol monitors | 📉 / ⚠        | ✅ ok · ⚠ caution · 🔴 alert            |
| Smart-money signals | 🪙 / 🧠       | (use rank emoji)                      |
| Macro briefs        | 📰 / 🌐       | (none — content is the signal)        |

Pick one and stick with it across runs of the same project.

## Skeleton (composed from `lib.telegram_format`)

```python
from lib.telegram_format import (
    header, stat_line, signal_block, tail_summary,
    summary_panel, footer, truncate_smart,
)

def render(snapshot) -> str:
    parts = [
        header("Polymarket Kelly", snapshot.ts),
        stat_line(("Bankroll", "$1,000"), ("Opps screened", "10")),
        "",
    ]
    for i, opp in enumerate(snapshot.top_3, start=1):
        parts.append(signal_block(
            rank=i,
            headline=f"${opp.size:.2f} · edge {opp.edge:+.1%}",
            title=opp.market_title,
            details=[
                f"Yes @ {opp.price:.1%}  ·  resolves {opp.dte}d",
                f"vol 24h ${opp.vol_24h_k}k  ·  liq ${opp.liq_k}k",
            ],
        ))
        parts.append("")
    if snapshot.tail_count:
        parts.append(tail_summary(
            snapshot.tail_count,
            edge_range=snapshot.tail_edge_range,
            suffix="on polymarket.com",
        ))
        parts.append("")
    parts.append(summary_panel([
        ("Bankroll", "$1,000",   "Open",  f"${snapshot.staked:.2f}"),
        ("Edge avg", f"{snapshot.edge_avg:+.1%}", "EV*", f"+${snapshot.ev:.2f}"),
        ("Exposure", f"{snapshot.exposure:.1%}", "Worst", f"-${snapshot.staked:.2f}"),
    ]))
    parts.append(footer("Not financial advice. *EV is heuristic; verify on Polymarket."))
    return truncate_smart("\n".join(parts), limit=3500)
```

## Anti-patterns

- `╔═══╗` "ASCII art" banners — broken on phones.
- `Settings: bankroll=$X | kelly=Y | max_days=Z` startup echoes —
  debug info, not a report.
- Per-batch progress prints (`Enriched 10/148`, `Fetching page 2…`) —
  `log.info` these.
- 7+ line cards per signal — group reads in seconds.
- Multi-line disclaimers — one line max (`"Not financial advice."`).
- `Confidence: LOW` labels when every result is LOW — drop or rename
  to what it actually measures (edge tier, sample size, etc.).
- Column-aligned tables in plain text — alignment breaks under
  proportional fonts on Telegram mobile.

## When to deviate

- HTML mode is fine if the project sends via its own POST (HedgePoly,
  Global Macro, PolyTraders). Don't migrate those to plain unless
  you're also rewriting them — different scope.
- Genuinely tabular output (e.g. multi-asset ranking grid) →
  screenshot via `sendPhoto` rather than fighting Telegram's
  plain-text width.

## See also

- `lib/telegram_format.py` — the helpers
- `lib/tests/test_telegram_format.py` — golden examples
- `docs/superpowers/specs/2026-05-10-telegram-redesign-design.md` §9 —
  audit notes for current projects
````

- [ ] **Step 1.3: Verify file present, commit**

```bash
cd "/d/OMNP - Quant/Projetos"
ls lib/__init__.py lib/tests/__init__.py docs/TELEGRAM_STYLE_GUIDE.md
git add lib/ docs/TELEGRAM_STYLE_GUIDE.md
git status --short
git commit -m "$(cat <<'EOF'
chore: bootstrap lib/ package + add Telegram style guide

- lib/ + lib/tests/ packages with empty __init__.py markers
- docs/TELEGRAM_STYLE_GUIDE.md (1-page reference)

Per spec §4 + §8.
EOF
)"
```

Expected: clean commit with 3 files.

---

## Task 2: `lib/telegram_format.py` + unit tests

**Files:**
- Create: `lib/tests/test_telegram_format.py`
- Create: `lib/telegram_format.py`

TDD: write all **14** test cases first, run (FAIL with ImportError), implement helpers, run (PASS), commit.

- [ ] **Step 2.1: Write `lib/tests/test_telegram_format.py`** (14 test functions, no parametrization)

```python
"""Unit tests for lib/telegram_format helpers. Pure functions, no IO."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lib.telegram_format import (
    footer,
    header,
    signal_block,
    stat_line,
    summary_panel,
    tail_summary,
    truncate_smart,
)


# ── header ──────────────────────────────────────────────────────────────────
def test_header_format() -> None:
    ts = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)
    got = header("Polymarket Kelly", ts)
    assert got == "📊 Polymarket Kelly  ·  2026-05-09  ·  12:00 UTC"


def test_header_custom_emoji() -> None:
    ts = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)
    got = header("BTC Leverage", ts, emoji="🟢")
    assert got.startswith("🟢 BTC Leverage")


# ── stat_line ───────────────────────────────────────────────────────────────
def test_stat_line_pairs() -> None:
    got = stat_line(("Bankroll", "$1,000"), ("Bets", "3"), ("Staked", "$43"))
    assert got == "Bankroll $1,000  ·  Bets 3  ·  Staked $43"


def test_stat_line_empty_returns_empty() -> None:
    assert stat_line() == ""


# ── signal_block ────────────────────────────────────────────────────────────
def test_signal_block_shape() -> None:
    got = signal_block(
        rank=1,
        headline="$15.31 · edge +6.0%",
        title="Italy wins Eurovision 2026",
        details=[
            "Yes @ 3.1%  ·  resolves 6d",
            "vol 24h $63k  ·  liq $78k",
        ],
    )
    expected = (
        "● #1  $15.31 · edge +6.0%\n"
        "  Italy wins Eurovision 2026\n"
        "  Yes @ 3.1%  ·  resolves 6d\n"
        "  vol 24h $63k  ·  liq $78k"
    )
    assert got == expected


def test_signal_block_no_details() -> None:
    got = signal_block(rank=2, headline="$5 · edge +3%", title="Some market", details=[])
    assert got == "● #2  $5 · edge +3%\n  Some market"


# ── tail_summary ────────────────────────────────────────────────────────────
def test_tail_summary_with_range() -> None:
    got = tail_summary(7, (0.03, 0.05), suffix="on polymarket.com")
    assert got == "+ 7 more (edge 3-5%) on polymarket.com"


def test_tail_summary_zero_returns_empty() -> None:
    assert tail_summary(0, (0.03, 0.05)) == ""


def test_tail_summary_no_range() -> None:
    got = tail_summary(3, None, suffix="on Polymarket")
    assert got == "+ 3 more on Polymarket"


# ── summary_panel ───────────────────────────────────────────────────────────
def test_summary_panel_two_columns() -> None:
    got = summary_panel(
        [
            ("Bankroll", "$1,000", "Open", "$43.06"),
            ("Edge avg", "+5.3%", "EV", "+$8.20"),
            ("Exposure", "4.1%", "Worst", "-$41"),
        ]
    )
    expected = (
        "│ Bankroll $1,000     Open  $43.06\n"
        "│ Edge avg +5.3%      EV    +$8.20\n"
        "│ Exposure 4.1%       Worst -$41"
    )
    assert got == expected


# ── footer ──────────────────────────────────────────────────────────────────
def test_footer_default_disclaimer() -> None:
    assert footer() == "\n— Not financial advice."


def test_footer_custom_text() -> None:
    assert footer("Educational only.") == "\n— Educational only."


# ── truncate_smart ──────────────────────────────────────────────────────────
def test_truncate_smart_under_limit() -> None:
    text = "short content"
    assert truncate_smart(text, limit=100) == text


def test_truncate_smart_at_limit() -> None:
    # exactly limit chars (with trailing newline = 100 chars total)
    text = "a" * 99 + "\n"
    assert truncate_smart(text, limit=100) == text


def test_truncate_smart_cuts_at_last_newline() -> None:
    lines = ["line one", "line two", "line three", "line four"]
    text = "\n".join(lines)
    # Force cut: limit small enough to drop "line four"
    got = truncate_smart(text, limit=len("line one\nline two\nline three") + 2)
    assert got.endswith("…")
    assert "line four" not in got
    assert "line three" in got


def test_truncate_smart_no_newline_in_window() -> None:
    text = "x" * 5000  # single huge line
    got = truncate_smart(text, limit=100)
    assert got.endswith("…")
    assert len(got) <= 100
```

- [ ] **Step 2.2: Run tests — must FAIL**

```bash
cd "/d/OMNP - Quant/Projetos"
python -m pytest lib/tests/test_telegram_format.py -v
```

Expected: ImportError or `ModuleNotFoundError: No module named 'lib.telegram_format'` (since the file doesn't exist yet). That's the red of TDD.

If pytest doesn't pick up the test file because of `pyproject.toml`/`conftest.py` config, manually run:
```bash
python -m pytest lib/tests/ -v --rootdir=.
```

- [ ] **Step 2.3: Implement `lib/telegram_format.py`**

```python
"""Plain-text Telegram message formatters.

All helpers are pure functions returning strings. Compose them at the
call site. No side effects: no print, no file writes, no env lookups.

Conventions:
- Plain text only. No HTML, no Markdown. The scheduler's _tg_send does
  not set parse_mode, so any HTML/Markdown markup would appear literally.
- Middle dot (·, U+00B7) as inline separator.
- Box-drawing vertical (│, U+2502) only as a left margin in summary_panel.
  Multi-line box-drawing tables render as broken rectangles on mobile
  Telegram and must be avoided.

Spec: docs/superpowers/specs/2026-05-10-telegram-redesign-design.md §5.
"""

from __future__ import annotations

from datetime import datetime


def header(title: str, ts: datetime, *, emoji: str = "📊") -> str:
    """Top line of a report.

    >>> header("Polymarket Kelly", datetime(2026, 5, 9, 12, 0, tzinfo=UTC))
    '📊 Polymarket Kelly  ·  2026-05-09  ·  12:00 UTC'
    """
    date_str = ts.strftime("%Y-%m-%d")
    time_str = ts.strftime("%H:%M UTC")
    return f"{emoji} {title}  ·  {date_str}  ·  {time_str}"


def stat_line(*pairs: tuple[str, str]) -> str:
    """Single-line K-V pairs separated by '  ·  '.

    >>> stat_line(("Bankroll", "$1,000"), ("Bets", "3"))
    'Bankroll $1,000  ·  Bets 3'
    >>> stat_line()
    ''
    """
    if not pairs:
        return ""
    return "  ·  ".join(f"{k} {v}" for k, v in pairs)


def signal_block(
    rank: int,
    headline: str,
    title: str,
    *,
    details: list[str],
    bullet: str = "●",
) -> str:
    """Multi-line opportunity card.

    Layout:
        ● #N  HEADLINE
          TITLE
          DETAIL_LINE_1
          DETAIL_LINE_2
          …

    Empty `details` → 2 lines (headline + title only).
    """
    lines = [f"{bullet} #{rank}  {headline}", f"  {title}"]
    lines.extend(f"  {d}" for d in details)
    return "\n".join(lines)


def tail_summary(
    remaining: int,
    edge_range: tuple[float, float] | None,
    *,
    suffix: str = "",
) -> str:
    """One-line summary of un-shown opportunities.

    Returns '' when remaining=0 so callers don't have to guard.

    >>> tail_summary(7, (0.03, 0.05), suffix="on polymarket.com")
    '+ 7 more (edge 3-5%) on polymarket.com'
    >>> tail_summary(0, (0.03, 0.05))
    ''
    """
    if remaining <= 0:
        return ""
    parts = [f"+ {remaining} more"]
    if edge_range is not None:
        lo, hi = edge_range
        parts.append(f"(edge {lo * 100:.0f}-{hi * 100:.0f}%)")
    if suffix:
        parts.append(suffix)
    return " ".join(parts)


def summary_panel(rows: list[tuple[str, str, str, str]]) -> str:
    """Two-column vertical-bar block for end-of-report summary.

    `rows` is a variable-length list of 4-tuples
    `(left_key, left_value, right_key, right_value)`.

    Spacing rule:
    - left_key column padded to max(left_key length) + 1 space.
    - left_value column padded to max(left_value length) + 5 spaces.
    - right_key column padded to max(right_key length) + 1 space.
    - right_value rendered as-is.
    """
    if not rows:
        return ""
    lk_w = max(len(r[0]) for r in rows)
    lv_w = max(len(r[1]) for r in rows)
    rk_w = max(len(r[2]) for r in rows)
    out_lines = []
    for lk, lv, rk, rv in rows:
        line = (
            f"│ {lk:<{lk_w}} {lv:<{lv_w + 4}} {rk:<{rk_w}} {rv}"
        )
        # rstrip trailing spaces only (preserve a trailing single space if
        # right-value happens to be empty — but values are always non-empty
        # in practice).
        out_lines.append(line.rstrip())
    return "\n".join(out_lines)


def footer(text: str = "Not financial advice.") -> str:
    """Last-line disclaimer with a leading blank line and em-dash.

    >>> footer()
    '\\n— Not financial advice.'
    """
    return f"\n— {text}"


def truncate_smart(text: str, limit: int = 3500) -> str:
    """Truncate `text` to fit within `limit` chars without breaking lines.

    If `text` already fits, returns it unchanged. Otherwise:
    - Cut at the last `\\n` boundary that keeps the result ≤ limit-1 chars,
      append '…'.
    - If no `\\n` exists in the window, hard-cut at limit-1 and append '…'.

    Well-formed reports compose under-limit and never trigger truncation.
    """
    if len(text) <= limit:
        return text
    window = text[: limit - 1]  # leave room for the '…'
    cut_at = window.rfind("\n")
    if cut_at == -1:
        return window + "…"
    return text[:cut_at] + "…"
```

- [ ] **Step 2.4: Run tests — must PASS**

```bash
cd "/d/OMNP - Quant/Projetos"
python -m pytest lib/tests/test_telegram_format.py -v
```

Expected: 14 tests pass (some functions have multiple test cases). Read the output to confirm no test was accidentally skipped.

- [ ] **Step 2.5: Optional — type-check the new module**

```bash
python -m mypy lib/telegram_format.py
```

Expected: `Success: no issues found in 1 source file`. Skip if mypy isn't installed; the type hints are simple enough that pytest catches the real bugs.

- [ ] **Step 2.6: Commit**

```bash
cd "/d/OMNP - Quant/Projetos"
git add lib/telegram_format.py lib/tests/test_telegram_format.py
git status --short
git commit -m "$(cat <<'EOF'
feat(lib): telegram_format helpers (header, signal_block, summary_panel, …)

Six pure formatters for plain-text Telegram reports. Domain-agnostic;
caller composes them. All return strings; no side effects.

- header(title, ts, emoji='📊')   → top line
- stat_line(*pairs)               → 'K1 V1  ·  K2 V2  ·  K3 V3'
- signal_block(rank, headline, title, details=[…])
                                  → 3-4 line opportunity card
- tail_summary(n, edge_range, suffix)
                                  → '+ N more (edge X-Y%) suffix'
- summary_panel(rows)             → 2-col │-prefixed block
- footer(text='Not financial advice.')
                                  → '\n— text'
- truncate_smart(text, limit=3500)
                                  → cut at last \n, append '…' if cut

14 unit tests covering each helper's contract + edge cases.

Spec: docs/superpowers/specs/2026-05-10-telegram-redesign-design.md §5.
EOF
)"
```

---

## Task 3: Refactor `polymarket_scraper.py` — stdout/log split

**Files:**
- Modify: `Poly/polymarket_scraper.py`

This task ONLY moves print statements off stdout (per spec §7). The report block stays as-is for now; T4 replaces it. Mid-task the scraper produces a sparser stdout but still terminal-style content (e.g., `=` banners). Don't run live yet.

- [ ] **Step 3.1: Read the current scraper to understand structure**

```bash
cd "/d/OMNP - Quant/Projetos"
wc -l Poly/polymarket_scraper.py
grep -n "^def \|^class \|print(" Poly/polymarket_scraper.py | head -40
```

Note line numbers of major `print(` sites — the box-drawing banner around line 640 (`print_banner`), `print(f"  [1/4] ...")` etc., per-batch enriched progress, the report header / opp loop / portfolio summary / disclaimer / "Saved to ..." line.

- [ ] **Step 3.2: Add `logging.basicConfig` near the top of the file**

Find the existing `log = logging.getLogger(__name__)` line (around line 36) — there's no basicConfig today. Add this block immediately AFTER the existing import statements but BEFORE the `signal_tracker` import block (which starts around line 38):

```python
# Configure stderr-only logging so noise doesn't reach Telegram via the
# scheduler's stdout-capture path (spec §7). When run standalone, stderr
# renders below stdout in the terminal — full diagnostic flow visible.
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
```

Make sure `sys` and `logging` are imported (they already are at the top of the file).

- [ ] **Step 3.3: Drop `print_banner()` entirely + its call site**

Find `def print_banner` (around line 640) and the line that calls `print_banner()` inside `main()` (around line 755).

- Delete the entire `print_banner` function.
- Delete the `print_banner()` call from `main()`.

- [ ] **Step 3.4: Convert non-report `print` calls to `log.info` / `log.debug`**

Per spec §7's grep directive: every `print(` in the scraper is either:
- Part of the rendered report (inside the `for bet in bets:` loop or the `PORTFOLIO RISK SUMMARY` block — leave for T4 to replace) — KEEP for now
- Anything else — convert to `log.info(...)` (or `log.debug(...)` for very chatty per-batch messages like `Enriched 10/148`)

Specific conversions (use `log.info` unless noted as `log.debug`):

| Approximate line | Current | New |
|---|---|---|
| ~756 | `print(f"  Settings: bankroll=...")` (settings echo) | `log.info(...)` (drop the leading whitespace) |
| ~758 | `print()` blank line | drop |
| ~778 | `print("  [1/4] Scanning Polymarket for short-term markets...")` | `log.info("[1/4] Scanning Polymarket for short-term markets...")` |
| ~780 | `print(f"  Found {len(markets)} qualifying markets.\n")` | `log.info(f"Found {len(markets)} qualifying markets.")` |
| ~783 | `print("  No markets found...")` | `log.info("No markets found. Filters likely too tight.")` |
| ~787 | `print("  [2/4] Analyzing edges with Kelly Criterion...")` | `log.info(...)` |
| ~789 | `print(f"  Found {len(opportunities)} opportunities ...")` | `log.info(...)` |
| ~792 | `print("  [3/4] Applying risk management limits...")` | `log.info(...)` |
| ~794 | `print(f"  Approved {len(approved_bets)} bets ...")` | `log.info(...)` |
| ~797 | `print("  [4/4] Generating report...\n")` | `log.info("[4/4] Generating report...")` |
| ~728 | `print(f"  📁 Results saved to {filename}")` | `log.info(f"Results saved to {filename}")` |
| ~805–807 | The 3-line `DISCLAIMER:` block at the end | DELETE entirely (the new footer covers it; T4 inserts the new one) |

Also convert in `markets_scanner.scan` / `_enrich_orderbook` (around line 286 onwards) — every `print("Fetching page X")`, `print("Enriched 10/148...")`, `print("Fetched 300 raw markets. Filtering...")`, `print("148 markets pass filters. ...")`:

- `print("  Fetching page X...")` → `log.info("Fetching page X...")` (or `log.debug` if you want it suppressed by default)
- `print("    Enriched 10/148...")` → `log.debug("Enriched 10/148...")` (these are the chattiest)
- `print("  Fetched 300 raw markets. Filtering...")` → `log.info(...)`
- `print("  148 markets pass filters. Enriching with orderbook data...")` → `log.info(...)`

Use grep to find all of them:
```bash
grep -n "print(" Poly/polymarket_scraper.py
```

Convert each non-report match. Leave the ones inside the `for i, bet in enumerate(approved_bets):` loop and the `PORTFOLIO RISK SUMMARY` block alone — T4 replaces them wholesale.

- [ ] **Step 3.5: Verify the report block is the ONLY remaining `print(...)` cluster**

```bash
grep -n "print(" Poly/polymarket_scraper.py
```

Expected: matches all live inside the report-generation block (around lines 654–691) plus possibly the leading `print(f"\n{'='*80}")` separators. Everything else should be gone or `log.info`/`log.debug`.

- [ ] **Step 3.6: Quick syntax check**

```bash
cd "/d/OMNP - Quant/Projetos"
python -c "import ast; ast.parse(open('Poly/polymarket_scraper.py').read()); print('OK')"
```

Expected: `OK`.

DO NOT run the scraper live yet — its report block is still box-drawn ASCII; T4 fixes that. The mid-task state is "syntax-valid + log-suppressed but visually ugly".

- [ ] **Step 3.7: Commit**

```bash
cd "/d/OMNP - Quant/Projetos"
git add Poly/polymarket_scraper.py
git status --short
git commit -m "$(cat <<'EOF'
refactor(poly): move pre-signal scanning noise off stdout

Per spec §7. Scheduler captures stdout to forward to Telegram; stderr
goes to scheduler.log only on non-zero exit. So:

- Add logging.basicConfig(stream=sys.stderr, level=INFO) — was missing,
  every existing log.info call was silently dropped.
- print_banner() (the ╔══╗ ASCII art) deleted entirely.
- All non-report print() calls converted to log.info / log.debug:
  settings echo, [1/4]…[4/4] stage markers, page-fetch noise,
  per-batch "Enriched N/M" progress, file-save announcement,
  3-line DISCLAIMER block (replaced by 1-line footer in T4).

The report block (TOP OPPORTUNITIES + PORTFOLIO RISK SUMMARY) is
unchanged in this commit — it still produces the old box-drawn ASCII
that T4 replaces with composed lib.telegram_format helpers.

Mid-task state: scraper syntax-valid, stdout is sparser, but report
visually unchanged. Don't ship; T4 follows immediately.
EOF
)"
```

---

## Task 4: Refactor `polymarket_scraper.py` — replace report block

**Files:**
- Modify: `Poly/polymarket_scraper.py`

Replace the existing report-generation code (the big `print(f"\n{'='*80}")`, `print("  TOP OPPORTUNITIES ...")`, `for i, bet in enumerate(approved_bets):` loop, `PORTFOLIO RISK SUMMARY` block, and 3-line disclaimer) with one composed `print(render_report(...))` call.

- [ ] **Step 4.0a: Verify the actual field names on `BetRecommendation` / `Market` / `Outcome`**

The draft `render_report` below assumes specific field names. Before writing it, confirm them against the real dataclasses:

```bash
cd "/d/OMNP - Quant/Projetos"
grep -nE "^@dataclass|^class (Bet|Market|Outcome)" Poly/polymarket_scraper.py
```

Read each dataclass definition. Record the actual names of:
- The market question/title field (draft assumes `bet.market.question`)
- Days-to-resolve (draft: `bet.market.days_to_resolve` — could be `dte`, `days_remaining`, `time_to_resolution`, etc.)
- 24h volume (draft: `bet.market.volume_24h`)
- Liquidity (draft: `bet.market.liquidity`)
- The `YES` market price (draft: `bet.outcome.market_price`)
- Bet size in USD (draft: `bet.bet_size_usd`)
- Edge (draft: `bet.edge`)

If any differ from the draft, **adjust both the `render_report` body in Step 4.1 AND the `_MockMarket` / `_MockOutcome` / `_MockBet` field names in T5 Step 5.2** to match. The two must stay coupled.

- [ ] **Step 4.0b: Verify the portfolio summary dict keys**

```bash
grep -nE "summary\[|portfolio_summary|PortfolioSummary" Poly/polymarket_scraper.py | head -20
```

Find where the summary dict is built today (around the `print_portfolio_summary` function or equivalent). Confirm the keys the draft assumes:
- `summary['total_exposure']`
- `summary['avg_edge']`
- `summary['expected_profit']`
- `summary['worst_case']`
- `summary['exposure_pct']`

If any differ, adjust the `summary_panel(...)` rows in `render_report`. Same coupling rule applies to T5's `_summary` helper.

- [ ] **Step 4.1: Add the import + a new `render_report` function**

Near the top of the file (after the `signal_tracker` / `cost_model` import block around line 38–43), add:

```python
from lib.telegram_format import (
    header,
    stat_line,
    signal_block,
    tail_summary,
    summary_panel,
    footer,
    truncate_smart,
)
```

(The same `sys.path.insert(_MONOREPO_ROOT, …)` that's already at the top of the file makes `lib.*` importable.)

Then add this function near the bottom of the file (right above `def main():`):

```python
def render_report(
    *,
    bankroll: float,
    settings: str,
    n_screened: int,
    approved_bets: list,  # list[BetRecommendation]
    summary: dict,        # PortfolioSummary dict (cf. existing print_portfolio_summary)
    asof: datetime,
) -> str:
    """Render the Telegram-bound bet-picker report.

    Spec §6. Composed from lib.telegram_format helpers; emits plain
    text + emoji + spacing only.
    """
    parts: list[str] = [
        header("Polymarket Kelly", asof),
        stat_line(
            ("Bankroll", f"${bankroll:,.0f}"),
            ("Opps screened", f"{n_screened} ({settings})"),
        ),
    ]

    # Empty-state path.
    if not approved_bets:
        parts.extend([
            "",
            "No opportunities matched filters.",
            "",
            f"{n_screened} markets scanned · 0 passed risk gates",
            "Filters likely too tight or markets too efficient today.",
            footer("Not financial advice."),
        ])
        return truncate_smart("\n".join(parts), limit=3500)

    # Top 3 detail blocks.
    top_n = min(3, len(approved_bets))
    parts.append("")
    for i, bet in enumerate(approved_bets[:top_n], start=1):
        days = max(1, int(round(bet.market.days_to_resolve)))
        vol_k = int(round(bet.market.volume_24h / 1000))
        liq_k = int(round(bet.market.liquidity / 1000))
        title = bet.market.question.strip()
        # Trim long titles to keep one line per
        if len(title) > 70:
            title = title[:67] + "…"
        parts.append(signal_block(
            rank=i,
            headline=f"${bet.bet_size_usd:,.2f} · edge {bet.edge:+.1%}",
            title=title,
            details=[
                f"Yes @ {bet.outcome.market_price:.1%}  ·  resolves {days}d",
                f"vol 24h ${vol_k}k  ·  liq ${liq_k}k",
            ],
        ))
        parts.append("")  # blank line between blocks

    # Tail summary.
    remaining = len(approved_bets) - top_n
    if remaining > 0:
        edges = sorted(b.edge for b in approved_bets[top_n:])
        parts.append(tail_summary(
            remaining,
            edge_range=(edges[0], edges[-1]),
            suffix="on polymarket.com",
        ))
        parts.append("")

    # Portfolio summary panel.
    parts.append(summary_panel([
        ("Bankroll", f"${bankroll:,.0f}",
         "Open", f"${summary['total_exposure']:,.2f}"),
        ("Edge avg", f"{summary['avg_edge']:+.1%}",
         "EV*", f"+${summary['expected_profit']:,.2f}"),
        ("Exposure", f"{summary['exposure_pct']:.1f}%",
         "Worst", f"-${abs(summary['worst_case']):,.2f}"),
    ]))

    parts.append(footer("Not financial advice. *EV is heuristic; verify on Polymarket."))

    return truncate_smart("\n".join(parts), limit=3500)
```

Adjust the field accesses (`bet.market.question`, `bet.market.days_to_resolve`, `bet.market.volume_24h`, `bet.market.liquidity`, `bet.outcome.market_price`, `bet.bet_size_usd`, `bet.edge`) to match the actual `BetRecommendation` / `Market` / `Outcome` classes in `polymarket_scraper.py`. Inspect the dataclasses around lines 90–110 to confirm field names.

The `summary` dict is what `print_portfolio_summary` (or equivalent) builds today — check the existing code for keys like `total_exposure`, `avg_edge`, `expected_profit`, `worst_case`, `exposure_pct`. If keys differ, adjust the `summary_panel` accesses to match.

- [ ] **Step 4.2: Replace the existing report-print block in `main()`**

Locate the report block precisely with anchor greps:

```bash
cd "/d/OMNP - Quant/Projetos"
grep -n "TOP OPPORTUNITIES\|PORTFOLIO RISK SUMMARY" Poly/polymarket_scraper.py
```

Both phrases appear inside `main()` and are unique. **Begin anchor:** the `print(f"\n{'='*80}")` line immediately preceding `TOP OPPORTUNITIES`. **End anchor:** the last line of the portfolio summary block (after T3's pass, this is whatever line follows the last `print(f"...")` for category breakdown — there's nothing else after it in `main()` except the JSON-dump `log.info` from T3 and `return 0`).

Find the section in `main()` that prints the banner + opps + portfolio summary + disclaimer. After T3 deleted the 3-line disclaimer, what remains is the box-drawn report block. It looks like:

```python
print(f"\n{'='*80}")
print(f"  TOP OPPORTUNITIES (Bankroll: ${bankroll:,.2f})")
print(f"{'='*80}\n")
for i, bet in enumerate(approved_bets, start=1):
    print(f"  #{i} | {bet.confidence} confidence | Score: {bet.score:.2f}")
    # ... 7 more print lines per opp ...
print(f"{'='*80}")
print(f"  PORTFOLIO RISK SUMMARY")
# ... summary block ...
```

Replace ALL of it (the banners, the opps loop, the portfolio summary, any remaining disclaimer text — the 3-line disclaimer was already deleted in T3) with ONE composed call:

```python
report = render_report(
    bankroll=args.bankroll,
    settings=f"edge ≥{args.min_edge:.0%}, ≤{args.max_days}d",
    n_screened=len(opportunities),  # or len(markets); pick the count that matches "opps screened" semantics
    approved_bets=approved_bets,
    summary=summary,  # the dict that print_portfolio_summary used
    asof=datetime.now(timezone.utc),
)
print(report)
```

Make sure this is the LAST thing `main()` prints (besides the JSON dump's `log.info` from T3 that announces the file save).

- [ ] **Step 4.3: Verify the only remaining stdout-bound `print` is the composed report**

```bash
cd "/d/OMNP - Quant/Projetos"
grep -n "print(" Poly/polymarket_scraper.py
```

Expected: ONE `print(report)` call inside `main()`. Anything else is a bug.

- [ ] **Step 4.4: Quick syntax + import check**

```bash
cd "/d/OMNP - Quant/Projetos"
python -c "import ast; ast.parse(open('Poly/polymarket_scraper.py').read()); print('parse OK')"
python -c "import sys; sys.path.insert(0, '.'); from lib.telegram_format import header; print('lib OK')"
```

Both should succeed.

- [ ] **Step 4.5: Commit**

```bash
cd "/d/OMNP - Quant/Projetos"
git add Poly/polymarket_scraper.py
git status --short
git commit -m "$(cat <<'EOF'
feat(poly): rewrite Telegram report as composed lib helpers

Per spec §6. Replace the box-drawing banners + 7-line per-opp cards +
3-line disclaimer with a single composed render_report() that uses
lib.telegram_format helpers:
  header → stat_line → 3 signal_blocks → tail_summary → summary_panel
  → footer → truncate_smart(limit=3500).

New report shape:
  📊 Polymarket Kelly  ·  YYYY-MM-DD  ·  HH:MM UTC
  Bankroll $X  ·  N opps screened (edge ≥X%, ≤Yd)

  ● #1  $X.XX · edge +X.X%
    Market title
    Yes @ X.X%  ·  resolves Nd
    vol 24h $Xk  ·  liq $Xk

  [● #2, ● #3 — same shape]

  + N more (edge X-Y%) on polymarket.com

  │ Bankroll $X       Open  $Y
  │ Edge avg +X%      EV*   +$Y
  │ Exposure X%       Worst -$Y

  — Not financial advice. *EV is heuristic; verify on Polymarket.

Length budget ~1,400 chars; never truncated by scheduler at 3,500.
Empty-state path renders a clear "no opps" message + scan stats.
EOF
)"
```

---

## Task 5: Golden-file tests for `render_report`

**Files:**
- Create: `Poly/tests/__init__.py` (if missing)
- Create: `Poly/tests/test_report_format.py`
- Create (scaffolded by first run): `Poly/tests/golden/{happy_path,single_bet,empty}.txt`

- [ ] **Step 5.1: Verify whether `Poly/tests/` exists**

```bash
cd "/d/OMNP - Quant/Projetos"
ls Poly/tests/ 2>&1
```

If it doesn't exist:
```bash
mkdir -p Poly/tests/golden
echo > Poly/tests/__init__.py
```

If it exists but no `__init__.py`, create it.

- [ ] **Step 5.2: Write `Poly/tests/test_report_format.py`**

```python
"""Golden-file regression for polymarket_scraper.render_report.

The first pytest run scaffolds the .txt files and fails with a hint to
inspect them. The second run compares byte-equal against the inspected
goldens. Spec §10.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

# Make Poly importable as if running from the Projetos root.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Poly"))

from polymarket_scraper import render_report  # noqa: E402

GOLDEN = Path(__file__).parent / "golden"


# ── minimal fixture types matching the real BetRecommendation / Market shape ──
@dataclass
class _MockMarket:
    question: str
    days_to_resolve: float
    volume_24h: float
    liquidity: float


@dataclass
class _MockOutcome:
    market_price: float


@dataclass
class _MockBet:
    market: _MockMarket
    outcome: _MockOutcome
    bet_size_usd: float
    edge: float


def _mock_bet(*, q: str, days: float, vol: float, liq: float,
              price: float, size: float, edge: float) -> _MockBet:
    return _MockBet(
        market=_MockMarket(question=q, days_to_resolve=days,
                           volume_24h=vol, liquidity=liq),
        outcome=_MockOutcome(market_price=price),
        bet_size_usd=size,
        edge=edge,
    )


def _summary(total_exposure: float, avg_edge: float,
             expected_profit: float, worst_case: float,
             exposure_pct: float) -> dict:
    return {
        "total_exposure": total_exposure,
        "avg_edge": avg_edge,
        "expected_profit": expected_profit,
        "worst_case": worst_case,
        "exposure_pct": exposure_pct,
    }


_ASOF = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)


def _happy_fixture():
    return dict(
        bankroll=1000.0,
        settings="edge ≥3%, ≤30d",
        n_screened=10,
        approved_bets=[
            _mock_bet(q="Italy wins Eurovision 2026", days=6,
                      vol=63_000, liq=78_000, price=0.031,
                      size=15.31, edge=0.060),
            _mock_bet(q="Bitcoin dips to $60k in May", days=23,
                      vol=79_000, liq=121_000, price=0.026,
                      size=12.84, edge=0.050),
            _mock_bet(q="Aliens confirmed by May 31", days=22,
                      vol=905_000, liq=156_000, price=0.031,
                      size=12.91, edge=0.050),
        ] + [
            _mock_bet(q=f"Tail market #{i}", days=10 + i,
                      vol=50_000, liq=50_000, price=0.04,
                      size=10.0, edge=0.030 + i * 0.002)
            for i in range(7)
        ],
        summary=_summary(total_exposure=41.06, avg_edge=0.053,
                         expected_profit=8.20, worst_case=-41.06,
                         exposure_pct=4.1),
        asof=_ASOF,
    )


def _single_fixture():
    return dict(
        bankroll=1000.0,
        settings="edge ≥3%, ≤30d",
        n_screened=10,
        approved_bets=[
            _mock_bet(q="Italy wins Eurovision 2026", days=6,
                      vol=63_000, liq=78_000, price=0.031,
                      size=15.31, edge=0.060),
        ],
        summary=_summary(total_exposure=15.31, avg_edge=0.060,
                         expected_profit=2.50, worst_case=-15.31,
                         exposure_pct=1.5),
        asof=_ASOF,
    )


def _empty_fixture():
    return dict(
        bankroll=1000.0,
        settings="edge ≥3%, ≤30d",
        n_screened=300,
        approved_bets=[],
        summary=_summary(total_exposure=0.0, avg_edge=0.0,
                         expected_profit=0.0, worst_case=0.0,
                         exposure_pct=0.0),
        asof=_ASOF,
    )


# ── golden tests ────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "fixture_factory,name",
    [
        (_happy_fixture, "happy_path.txt"),
        (_single_fixture, "single_bet.txt"),
        (_empty_fixture, "empty.txt"),
    ],
)
def test_golden_match(fixture_factory, name: str) -> None:
    text = render_report(**fixture_factory())
    path = GOLDEN / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        pytest.fail(f"Golden file {name} did not exist; wrote it. Inspect and rerun.")
    assert text == path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "fixture_factory",
    [_happy_fixture, _single_fixture, _empty_fixture],
)
def test_under_3500_chars(fixture_factory) -> None:
    text = render_report(**fixture_factory())
    assert len(text.encode("utf-8")) < 3500


@pytest.mark.parametrize(
    "fixture_factory",
    [_happy_fixture, _single_fixture, _empty_fixture],
)
def test_no_html_no_markdown(fixture_factory) -> None:
    text = render_report(**fixture_factory())
    assert "<" not in text
    assert "**" not in text
    assert "__" not in text
    # MarkdownV2 special chars that would need escaping if we used MV2:
    # `_`, `*`, `[`, `(`, `~`, `>`, `#`, `+`, `-`, `=`, `|`, `{`, `.`, `!`
    # We don't enforce all of these — only assert the markup-style ones that
    # would actually break under the wrong parse_mode.
```

**Coupling note:** the `_MockMarket` / `_MockOutcome` / `_MockBet` field names above must match exactly what `render_report` (T4) accesses. If T4's Step 4.0a found different names on the real dataclasses (e.g. `dte` instead of `days_to_resolve`), update BOTH `render_report` AND these fixture types in lockstep before running.

**Import side-effect note:** `polymarket_scraper.py` runs `sys.path.insert` and a few small imports at module level. It does NOT make network calls or do disk I/O at import time, so the test's `from polymarket_scraper import render_report` is safe. If T3/T4 introduce module-level work (it shouldn't), guard the heavy code under `if __name__ == "__main__":` first.

- [ ] **Step 5.3: First pytest run — must FAIL with golden-scaffolding messages**

```bash
cd "/d/OMNP - Quant/Projetos"
python -m pytest Poly/tests/test_report_format.py -v
```

Expected: 3 `test_golden_match[...]` tests fail with "Golden file XXX did not exist; wrote it. Inspect and rerun." The other 6 tests (under-3500 + no-markdown) should already PASS because they don't depend on goldens.

- [ ] **Step 5.4: Inspect each golden file**

```bash
cd "/d/OMNP - Quant/Projetos"
cat Poly/tests/golden/happy_path.txt
echo "==="
cat Poly/tests/golden/single_bet.txt
echo "==="
cat Poly/tests/golden/empty.txt
```

Confirm by eye that each file matches §6 of the spec:
- `happy_path.txt` — header, bankroll/screened line, 3 signal blocks, "+ 7 more (edge 3-5%) on polymarket.com", summary panel, footer.
- `single_bet.txt` — header, bankroll line, 1 signal block, NO `+ N more` line, summary panel, footer.
- `empty.txt` — header, "No opportunities matched filters." line, scan stats, footer. No signal blocks.

If anything looks wrong (e.g., the `summary_panel` padding is off, edge format wrong), fix `render_report` in T4 / `lib/telegram_format.py` in T2 first, then DELETE the offending golden file and rerun the tests. Don't manually edit the golden file.

- [ ] **Step 5.5: Second pytest run — must PASS**

```bash
cd "/d/OMNP - Quant/Projetos"
python -m pytest Poly/tests/test_report_format.py -v
```

Expected: all 9 parametrized cases pass.

- [ ] **Step 5.6: Run the full repo's tests to confirm no regression**

```bash
cd "/d/OMNP - Quant/Projetos"
python -m pytest lib/tests Poly/tests -v
```

Expected: 14 (lib) + 9 (Poly) = 23 tests pass.

- [ ] **Step 5.7: Commit**

```bash
cd "/d/OMNP - Quant/Projetos"
git add Poly/tests/__init__.py Poly/tests/test_report_format.py Poly/tests/golden/
git status --short
git commit -m "$(cat <<'EOF'
test(poly): golden-file regression for render_report

3 fixtures × 3 tests:
- happy_path: 10 bets, top 3 + tail of 7
- single_bet: 1 bet, no tail line
- empty:      0 bets, empty-state message

9 parametrized cases total. test_golden_match scaffolds the .txt
files on first run, asserts byte-equal on subsequent runs.
test_under_3500_chars + test_no_html_no_markdown enforce the
spec's §6 length budget + plain-text constraint.

Per spec §10. First-run scaffolding pattern matches slice-1 T13.
EOF
)"
```

---

## Task 6: Live smoke run + final verification

**Files:** none (runtime smoke only)

This task is the spec's acceptance check. We don't run Poly Kelly directly during testing because it makes live network calls to Polymarket — but we do exercise the full scheduler-capture-and-forward path for the real e2e test.

- [ ] **Step 6.1: Trigger one Poly Kelly run via the scheduler's `run_project`**

```bash
cd "/d/OMNP - Quant/Projetos"
python -c "
import sys
sys.path.insert(0, '.')
from scheduler import PROJECTS, run_project

p = next(p for p in PROJECTS if p['name'] == 'Poly Kelly Scraper')
print(f'Triggering: {p[\"name\"]}')
ok = run_project(p, dry_run=False)
print(f'run_project returned: {ok}')
"
```

This:
- Runs the scraper subprocess (live Polymarket fetch).
- Captures stdout to a string.
- Sends to Telegram via `_tg_send` because `capture_to_telegram=True`.
- Returns True on success.

Expected output (one of):
- `[Poly Kelly Scraper] Output forwarded to Telegram (X chars)` where X is roughly 1,200–1,800 chars (was 6,134 before this slice).
- `[Poly Kelly Scraper] Done (exit 0)`.

If it crashes or exits non-zero, debug:
```bash
cd "/d/OMNP - Quant/Projetos/Poly"
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 uv run --no-project --python 3.11 --with requests python polymarket_scraper.py
```

Inspect stderr for diagnostic info; check whether a `BetRecommendation` field name guess was wrong.

**Bounded-retry policy:** if the smoke run fails ≥2 times due to apparent network or Polymarket-side issues (rate limit, 5xx, schema drift unrelated to this slice's edits), do NOT block the slice. Commit T1–T5 as-is, document the smoke deferral in a final commit message, and re-attempt T6 later. The slice's correctness is captured by the unit + golden tests; the live smoke is a confirmation, not a gate. Symptoms that are NOT "infra outside scope" (and DO require a fix): `AttributeError` in `render_report`, missing key in `summary` dict, ruff/mypy errors uncovered at runtime — fix and recommit.

- [ ] **Step 6.2: Verify the message arrived in Telegram**

Open the Telegram channel. Confirm:
- Message present, dated within the last minute.
- Header: `[Poly Kelly Scraper] YYYY-MM-DD HH:MM UTC` (from scheduler) followed by the rendered report.
- 1–3 `● #N` signal blocks (depending on what passed filters today).
- No box-drawing banners, no `Enriched 10/148` progress lines, no `[1/4]` stage markers.
- Single-line footer with `— Not financial advice. *EV is heuristic; verify on Polymarket.`
- Total length comfortably under 3,500 chars (no truncation `…` at the end).

- [ ] **Step 6.3: Check `scheduler.log` for the diagnostic flow**

```bash
cd "/d/OMNP - Quant/Projetos"
tail -30 scheduler.log
```

Expected: `[Poly Kelly Scraper] Output forwarded to Telegram (X chars)` line is the only thing about the run on success. If the scraper had failed, you'd see `Exit ≠ 0` + a stderr snippet — those snippets now contain the `log.info` progress messages that were converted in T3, useful for debugging.

- [ ] **Step 6.4: Acceptance criteria checklist**

Per spec §11. Walk each item:

| # | Criterion | Verify |
|---|---|---|
| 1 | All tests green | `python -m pytest lib/tests Poly/tests` exit 0 |
| 2 | Mypy clean on lib | `python -m mypy lib/telegram_format.py` exit 0 (or skip if mypy not installed) |
| 3 | Live message under 3,500 chars | Step 6.1 output shows `(X chars)` with X < 3500 |
| 4 | No scanning noise in Telegram | Step 6.2 manual inspection |
| 5 | Group can read it on phone in 5s | Subjective; user evaluation |
| 6 | Style guide is clear | User reviews `docs/TELEGRAM_STYLE_GUIDE.md` |
| 7 | JSON dump still emitted | `ls Poly/results_*.json` (or wherever the scraper saves) shows a fresh file |
| 8 | Standalone run shows progress in stderr | `cd Poly && PYTHONUTF8=1 python polymarket_scraper.py 2>&1 \| head -30` shows `[1/4] Scanning...` etc. |

- [ ] **Step 6.5: If any post-smoke fixes are needed, commit them**

If steps 6.1–6.3 surface real bugs (e.g., wrong field name in `render_report`, a `print()` that survived T3's pass), fix and commit:

```bash
git add <files>
git commit -m "fix(poly): post-smoke fix — <one-line reason>"
```

If everything works on the first try, no commit needed for this task.

- [ ] **Step 6.6: (Optional) Tag the slice complete**

```bash
git log --oneline -8  # confirm 5-6 slice commits land cleanly
```

Slice complete. Future redesigns of the other 7 projects can pull from `lib/telegram_format` + the style guide.

---

## Out-of-scope reminder

This slice does NOT modify any of the 7 other Telegram-emitting projects. Audit notes for each are in spec §9 to inform future redesigns. When ready, brainstorm each as its own slice.
