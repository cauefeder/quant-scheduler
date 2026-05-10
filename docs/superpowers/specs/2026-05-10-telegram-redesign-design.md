---
title: Telegram report redesign — Poly Kelly Scraper + shared style helpers
date: 2026-05-10
status: approved
scope: Poly Kelly Scraper rewrite (primary) + reusable lib/telegram_format helpers + 1-page style guide + audit notes for other 7 projects
---

# Telegram Report Redesign

## 1. Executive summary

The current Poly Kelly Scraper Telegram message (~6,134 chars,
truncated by scheduler at 3,500) is a terminal-style ASCII dump
that renders poorly on mobile. ~30 lines of pre-signal scanning
noise precede the actual bets; box-drawing characters mangle on
phones; the per-opportunity layout buries the action (bet size +
edge) at the bottom of 7-line cards; the portfolio summary +
disclaimer get cut by truncation and never reach the channel.

This slice rewrites the Poly Kelly output for a *bet-picker*
mobile reader: top 3 detailed bets at the top, 1-line tail summary
of remaining opps, compact summary panel at the bottom, single-line
disclaimer footer. All scanning/progress noise moves to `log.info`
(stderr) — visible when running standalone, invisible when run
under scheduler. Length budget ~1,400 chars; never truncated.

The rewrite is implemented in terms of six small, domain-agnostic
helpers in a new `lib/telegram_format.py` shared across projects,
backed by a 1-page `docs/TELEGRAM_STYLE_GUIDE.md`. The other 7
projects keep their existing implementations; audit notes here
record what's good vs. what to address when each is up for redesign.

## 2. Decisions locked

| Axis | Choice |
|---|---|
| Slice scope | Poly Kelly Scraper rewrite + shared lib + 1-page style guide + audit notes for the other 7 projects |
| Lede | Bet picker — top 3 with size + market name first |
| Format | Plain text + emoji + spacing (no HTML, no Markdown V2) |
| Density | Top 3 in detail + 1-line tail summary; portfolio panel at end |
| Architecture | Approach **A** — shared `lib/telegram_format.py` at Projetos root, applied to Poly Kelly only in this slice |

## 3. Scope

### In

1. `lib/telegram_format.py` — six pure helpers (header, stat_line, signal_block, tail_summary, summary_panel, footer, truncate_smart).
2. `lib/__init__.py` (empty marker) + `lib/tests/test_telegram_format.py` (unit + mini-golden).
3. `Poly/polymarket_scraper.py` — refactor stdout/log split + replace report block with composed helpers. JSON dump preserved.
4. `Poly/tests/test_report_format.py` + `Poly/tests/golden/{happy_path,single_bet,empty}.txt`.
5. `docs/TELEGRAM_STYLE_GUIDE.md` — ~70-line markdown reference.
6. This design doc.

### Out (explicitly deferred)

- Edits to the other 7 projects' Telegram messages. Each gets its own brainstorm + spec when scheduled for redesign.
- Edits to `scheduler.py` — already done in commit `cdcc7c0` (dynamic capture header). Truncation cap stays 3,500.
- Strategy / EV calc fixes for the Poly Kelly Scraper (the "+293% EV on 2% market" math is correct but misleading; out of UX scope, separate concern).
- ModelTelegra Quant Desk image-based reports — different design conversation entirely (text + chart caption length conventions).
- Collapsing the 6 separate `_tg_send`-equivalents in HTML projects into a single shared util — worth a future slice but creep here.

## 4. File layout

```
D:/OMNP - Quant/Projetos/
├── lib/                                    ← NEW
│   ├── __init__.py                         ← empty marker
│   ├── telegram_format.py                  ← shared helpers (~80 LOC)
│   └── tests/
│       ├── __init__.py
│       └── test_telegram_format.py
│
├── Poly/
│   ├── polymarket_scraper.py               ← MODIFIED
│   └── tests/
│       ├── __init__.py                     ← may already exist
│       ├── test_report_format.py           ← NEW
│       └── golden/
│           ├── happy_path.txt
│           ├── single_bet.txt
│           └── empty.txt
│
├── docs/
│   ├── TELEGRAM_STYLE_GUIDE.md             ← NEW
│   └── superpowers/specs/
│       └── 2026-05-10-telegram-redesign-design.md     ← this file
│
└── scheduler.py                            ← UNTOUCHED in this slice
```

`Poly/polymarket_scraper.py` already uses `sys.path.insert(0, _MONOREPO_ROOT)` (lines 39–43) for `signal_tracker` and `cost_model` imports. The new `from lib.telegram_format import ...` uses the same path; no new venv plumbing.

## 5. `lib/telegram_format.py` public API

Six small helpers. Domain-agnostic; caller composes them. All return strings; no side effects (no print, no file writes).

```python
from datetime import datetime

def header(title: str, ts: datetime, *, emoji: str = "📊") -> str:
    """Top line.
    header('Polymarket Kelly', datetime(2026,5,9,12,0,tzinfo=UTC))
    → '📊 Polymarket Kelly  ·  2026-05-09  ·  12:00 UTC'
    """

def stat_line(*pairs: tuple[str, str]) -> str:
    """One line of K-V pairs separated by middle-dot.
    stat_line(('Bankroll', '$1,000'), ('Bets', '3'), ('Staked', '$43'))
    → 'Bankroll $1,000  ·  Bets 3  ·  Staked $43'
    Empty pairs → ''.
    """

def signal_block(rank: int, headline: str, title: str,
                 *, details: list[str], bullet: str = "●") -> str:
    """Multi-line opportunity card. 3-4 lines total.
    signal_block(1, '$15.31 · edge +6.0%',
                 'Italy wins Eurovision 2026',
                 details=['Yes @ 3.1%  ·  resolves 6d',
                          'vol 24h $63k  ·  liq $78k'])
    →
    ● #1  $15.31 · edge +6.0%
      Italy wins Eurovision 2026
      Yes @ 3.1%  ·  resolves 6d
      vol 24h $63k  ·  liq $78k
    Empty details → 2 lines (headline + title only).
    """

def tail_summary(remaining: int, edge_range: tuple[float, float] | None,
                 *, suffix: str = "") -> str:
    """One-line summary of un-shown items.
    tail_summary(7, (0.03, 0.05), suffix='on polymarket.com')
    → '+ 7 more (edge 3-5%) on polymarket.com'
    remaining=0 → '' (caller doesn't need to guard).
    """

def summary_panel(rows: list[tuple[str, str, str, str]]) -> str:
    """Two-column vertical-bar block at end of report.

    `rows` is a variable-length list of 4-tuples
    `(left_key, left_value, right_key, right_value)`. Spacing rule:
    - left_key column padded to max(left_key length) + 1 space.
    - left_value column padded to max(left_value length) + 5 spaces.
    - right_key column padded to max(right_key length) + 1 space.
    - right_value rendered as-is.

    summary_panel([('Bankroll', '$1,000', 'Open', '$43.06'),
                   ('Edge avg', '+5.3%', 'EV', '+$8.20'),
                   ('Exposure', '4.1%',  'Worst', '-$41')])
    →
    │ Bankroll $1,000     Open  $43.06
    │ Edge avg +5.3%      EV    +$8.20
    │ Exposure 4.1%       Worst -$41
    """

def footer(text: str = "Not financial advice.") -> str:
    """Last line with light separator.
    footer() → '\\n— Not financial advice.'
    """

def truncate_smart(text: str, limit: int = 3500) -> str:
    """Last-line-of-defense truncation.
    Cut at last full line ≤ limit; append '…' if cut.
    Well-formed reports never trigger.
    """
```

### Conventions

- All helpers return strings. Compose them at the call site.
- No emoji is hardcoded except `header(emoji=…)`'s default. Caller owns the visual identity.
- No domain language (no `bet`, `signal`, `bankroll` in the API). Reusable across the next 7 projects.
- The `│` (U+2502 BOX DRAWINGS LIGHT VERTICAL) in `summary_panel` is a single character per line — verified to render cleanly on iOS / Android / Desktop Telegram.

## 6. Poly Kelly output format

### Happy path (≥1 opportunity, no truncation, ~1,400 chars)

```
📊 Polymarket Kelly  ·  2026-05-09  ·  12:00 UTC
Bankroll $1,000  ·  10 opps screened (edge ≥3%, ≤30d)

● #1  $15.31 · edge +6.0%
  Italy wins Eurovision 2026
  Yes @ 3.1%  ·  resolves 6d
  vol 24h $63k  ·  liq $78k

● #2  $12.84 · edge +5.0%
  Bitcoin dips to $60k in May
  Yes @ 2.6%  ·  resolves 23d
  vol 24h $79k  ·  liq $121k

● #3  $12.91 · edge +5.0%
  Aliens confirmed by May 31
  Yes @ 3.1%  ·  resolves 22d
  vol 24h $905k  ·  liq $156k

+ 7 more (edge 3-5%) on polymarket.com

│ Bankroll  $1,000     Open    $41.06
│ Edge avg  +5.3%      EV*     +$8.20
│ Exposure  4.1%       Worst   -$41

— Not financial advice. *EV is heuristic; verify on Polymarket.
```

### Empty-state path (0 opportunities passed filters)

```
📊 Polymarket Kelly  ·  2026-05-09  ·  12:00 UTC
No opportunities matched filters (edge ≥3%, ≤30d).

300 markets scanned · 0 passed risk gates
Filters likely too tight or markets too efficient today.

— Not financial advice.
```

### Few-opps path (1 or 2 opportunities)

Render the 1 or 2 `signal_block`s. **Omit the `+ N more` line entirely** when remaining = 0. Summary panel still appears.

### Drop / change list (current vs. new)

| Field | Current | New | Why |
|---|---|---|---|
| Banner | `╔══╗ POLYMARKET KELLY ╚══╝` | `📊 Polymarket Kelly` | Mobile fonts mangle box-drawing |
| Per-opp line count | 7 | 4 | Density; reader can scan |
| Headline | `#1 \| LOW confidence \| Score: 116.09` | `#1  $15.31 · edge +6.0%` | Bet size + edge are the bet-pick signals; "LOW confidence" + score are jargon |
| EV display | `+293%` (multiplicative-on-stake) | dropped | "+293%" reads as "triple your money"; misleading. Edge alone tells the story |
| Confidence label | LOW/MED/HIGH (always LOW in practice) | dropped | Edge % already conveys it; explicit label was confusing |
| Spread | `Spread: 0.998` | dropped | Not actionable for a price-taker |
| Full Kelly vs Adj Kelly | both shown | drop full, keep adj as $-amount only | Bet picker doesn't care about full Kelly |
| Pre-signal scanning noise | sent to stdout (≈30 lines) | sent to `log.info` only | Goes to scheduler.log on failure only |
| Stage markers `[1/4]…[4/4]` | sent to stdout | sent to `log.info` only | Same |
| Disclaimer | 3 lines at end | single-line footer | One line is enough |
| Portfolio category breakdown | shown | dropped | Low signal-to-noise on phone |
| File-save announcement | `Results saved to bets_2026-05-09.json` | sent to `log.info` only | JSON file is local-only; not actionable for the channel |

### Numeric formatting rules

- **Dollar amounts**: `$1,000` (commas ≥1k), `$15.31` (cents <100), `$905k` (compact ≥10k in inline contexts).
- **Percent**: `+6.0%` always with sign and one decimal in opp headlines. `5.3%` (no sign) for averages where direction is unambiguous.
- **Days**: `6d`, `23d` — integer days, no decimals (current scraper says `5.6d` which is over-precise for human reading).
- **Counts**: comma-separated when ≥1k.

## 7. stdout vs log split

The scheduler captures **stdout only** for forwarding to Telegram. stderr is captured but discarded on success (only surfaced on non-zero exit, into `scheduler.log`).

### What stays on stdout (becomes the Telegram body)

Only the rendered report from §6: header + stat_line + 1-3 signal_blocks + (tail_summary?) + summary_panel + footer. Composed by one `print(rendered_text)` at the end of `main()`.

### What moves off stdout

**Implementation note**: grep `polymarket_scraper.py` for `print(`. Every match is either part of the new report (one final composed `print(rendered_text)` at the end of `main()`) or becomes `log.info` / `log.debug`. The table below covers the common cases; treat any other `print(...)` call as `log.info` unless it's clearly part of the rendered report.

These all become `log.info(...)` or `log.debug(...)` calls instead of `print(...)`:

| Line type | Today | After |
|---|---|---|
| Box-drawing banner | `╔═════ POLYMARKET KELLY ═════╝` | **dropped entirely** |
| Settings echo | `Settings: bankroll=… kelly=… max_days=…` | `log.info` |
| Stage markers | `[1/4] Scanning Polymarket...` | `log.info` |
| Page-fetch noise | `Fetching page 1...` | `log.info` |
| Per-batch progress | `Enriched 10/148...` `Enriched 20/148...` | `log.debug` (suppressed unless DEBUG enabled) |
| Filter summaries | `148 markets pass filters.` `Found 53 opportunities.` | `log.info` |
| File save | `Results saved to bets_2026-05-09.json` | `log.info` |
| `TOP OPPORTUNITIES` / `PORTFOLIO RISK SUMMARY` ASCII banners | `=` × 80 wrappers | **dropped entirely** (replaced by §6 helpers) |
| 3-line disclaimer block | `DISCLAIMER: This is a research tool...` (3 lines) | **dropped** (replaced by 1-line footer in stdout) |

### Logging setup (one new block near the top of the scraper)

The scraper currently has `log = logging.getLogger(__name__)` at line 36 but **no `logging.basicConfig`** — meaning every existing `log.info(...)` is silently dropped. Adding the basicConfig is a side benefit:

```python
# After imports, before any module-level code:
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
```

Goes to stderr explicitly. When run via scheduler with capture, stderr is consumed by the subprocess — discarded on success, surfaced (truncated) on non-zero exit. When run manually (`python polymarket_scraper.py`), stderr renders inline below stdout in the terminal — full diagnostic flow visible.

### Why not keep some progress visible in `scheduler.log` on success?

1. The new `Bankroll $1,000  ·  10 opps screened (edge ≥3%, ≤30d)` stat line in the Telegram report already confirms the scraper ran. Load-bearing diagnostic.
2. If the scraper genuinely fails (network, schema drift), it exits non-zero — at which point scheduler dumps stderr's last 500 chars to `scheduler.log`. That's when you actually want the progress messages, and they'll be there.

Green-path runs are quiet by design; failures preserve enough breadcrumbs.

## 8. `docs/TELEGRAM_STYLE_GUIDE.md` content

~70-line Markdown reference. Lives at the Projetos root.

### Sections

```
# Telegram Reports — Style Guide

## Principles
1. Lead with the action.
2. Plain text + emoji + spacing. No HTML, no Markdown.
3. Mobile fonts mangle box-drawing.
4. Stay under 3,500 chars.
5. Group stdout-only output cleanly.

## Recommended emoji conventions
| Project type        | Header emoji | Status emoji |
| Trade tickets       | 📊 / 🎯       | 🟢 trade · ⚪ no-trade · 🔴 degraded |
| Risk / vol monitors | 📉 / ⚠        | ✅ ok · ⚠ caution · 🔴 alert |
| Smart-money signals | 🪙 / 🧠       | (use rank emoji) |
| Macro briefs        | 📰 / 🌐       | (none — content is the signal) |

## Skeleton (Python code example using lib.telegram_format)
[~25 LOC composing the helpers]

## Anti-patterns
- ╔═══╗ banners
- "Settings: bankroll=… | kelly=…" startup echoes
- Per-batch progress prints
- 7+ line cards per signal
- Multi-line disclaimers
- "Confidence: LOW" labels when every result is LOW
- Column-aligned tables in plain text

## When to deviate
- HTML mode is fine if the project sends via its own POST.
- Genuinely tabular output → sendPhoto with a rendered image.

## See also
- lib/telegram_format.py
- lib/tests/test_telegram_format.py
- §10 of this design doc (audit notes)
```

## 9. Audit notes — the other 7 projects

Each gets 1–3 lines per the scope decision. The HTML-via-own-POST projects are largely OK because HTML mode gives them formatting power that plain-text-via-stdout doesn't.

### Group A: HTML-via-own-POST (6 projects)

**1. HedgePoly Alpha Report** — `reporting.build_telegram_report` ([reporting.py](../../HedgePoly/prediction-market-analysis/reporting.py))
✅ Looks good. Uses `<b>`, `<code>`. BUY YES/NO + HIGH/MODERATE/LOW conviction tier per opportunity. Action labels are clear. **Action**: spot-check current production output for length under 3,500 chars when many opps fire — otherwise leave as-is.

**2. Global Macro Quant Report** — `quant_helpers.build_telegram_html` ([notebooks/quant_helpers.py](../../HedgePoly/prediction-market-analysis/notebooks/quant_helpers.py))
✅ Looks good. 4-line-per-signal layout, 📈/📉/➡️ direction emoji, `<b>` + `<code>` headers, single-line disclaimer. Compact and scannable. **No action.**

**3. Poly2 Kelly Bot** — `polymarket_telegram_bot.py`
⚠ Skim closer when redesigned. Uses `parse_mode=HTML` via its own `TelegramSender`. Message body not deep-scouted; flag for the next slice. **Action**: defer 1–2 lines of audit until you redesign this project.

**4 & 5. Poly2 Macro Report 1 + 2** — `macro_report1.py`, `macro_report2.py`
⚠ Likely over-long. Both use Gemini-AI-generated structured reports. AI text tends to balloon toward token limits; with HTML overhead and macro coverage of multiple categories, message bodies may exceed 4,096 chars (Telegram's hard ceiling — splits into multiple messages). **Biggest visible issue when you redesign these**: clamp each AI section length explicitly + lead with a 3-bullet TL;DR before the long-form sections.

**6. PolyTraders Smart Money** — `main.py`
✅ Mostly good. `<b>PolyTraders | Smart Money Signals</b>` header, time-period + bankroll callouts, builds report via `lines += [...]` pattern. Same "100 USDC bankroll" framing as Poly Kelly — could share helper logic in a future slice. **Action**: when you redesign Poly Kelly via this slice's helpers, evaluate sharing 2–3 helpers (signal_block, summary_panel) with PolyTraders if its current shape carries dead weight. Likely a 20-minute follow-up; not urgent.

### Group B: Image / multi-channel (1 project)

**7. ModelTelegra Quant Desk** — `ModelTelegra,/quant_desk/main.py`
⏭ Out-of-scope per current scope decision; flag for a separate slice. This project pulls plotly/kaleido for chart rendering and likely sends `sendPhoto` (image) + text caption rather than a pure text message. The plain-text style guide doesn't apply directly. When this project is up for review, it needs a different design conversation: chart-image readability + caption length conventions. **Audit deferred.**

### Cross-cutting observations

| Observation | Affected projects | Action |
|---|---|---|
| All HTML projects have their own `_tg_send`-equivalent | HedgePoly, Global Macro, Poly2 Kelly, Poly2 Macro 1+2, PolyTraders | Could collapse 6 implementations into one shared util (`lib/telegram_send.py` with HTML/plain modes). Defer; scope creep here but worth a future slice. |
| Disclaimer lines vary in length & tone | All 7 + the new Poly Kelly | Style guide §"Anti-patterns" calls out one-line disclaimers; future redesigns adopt naturally. |
| `[Project Name]` prefix from scheduler is now dynamic | All capture-mode projects | Already fixed in commit `cdcc7c0` from the slice-1 wrap-up. |

## 10. Testing strategy

### `lib/tests/test_telegram_format.py` — pure unit tests

| Test | What it asserts |
|---|---|
| `test_header_format` | `header("Polymarket Kelly", datetime(2026,5,9,12,0,tzinfo=UTC))` → exact bytes match |
| `test_stat_line_pairs` | 3 pairs render as `"K1 V1  ·  K2 V2  ·  K3 V3"`; empty input returns `""` |
| `test_signal_block_shape` | Exactly 4 lines, rank renders as `#1`, headline + title + each detail on own line, indented 2 spaces |
| `test_signal_block_no_details` | Empty `details` → 2 lines (headline + title only) |
| `test_tail_summary_with_range` | `tail_summary(7, (0.03, 0.05), suffix='on polymarket.com')` → `"+ 7 more (edge 3-5%) on polymarket.com"` |
| `test_tail_summary_zero_returns_empty` | `tail_summary(0, ...)` → `""` (caller doesn't have to guard) |
| `test_summary_panel_two_columns` | 3 rows × 4 cells render as 3 lines with `│` left margin, key-value alignment within row |
| `test_footer_default_disclaimer` | `footer()` → `"\\n— Not financial advice."` |
| `test_truncate_smart_at_limit` | text exactly at limit → unchanged; over limit → cut at last `\\n`, append `…` |
| `test_truncate_smart_no_newline_in_window` | very long single line over limit → cut at `limit - 1` chars + `…` |

### `Poly/tests/test_report_format.py` — golden-file integration

| Test | Fixture | Assertion |
|---|---|---|
| `test_happy_path_top_3_with_tail` | 10 bets, top 3 pre-known | rendered text byte-equal to `golden/happy_path.txt` |
| `test_few_opps_one_bet_no_tail` | 1 bet | matches `golden/single_bet.txt`; no `+ N more` line |
| `test_empty_state_zero_opps` | 0 bets, 300 scanned | matches `golden/empty.txt` |
| `test_under_3500_chars` | parametrize over the 3 fixtures | `len(text.encode("utf-8")) < 3500` |
| `test_no_html_no_markdown` | parametrize over fixtures | no `<`, `**`, `__`, `[…](…)` in output |

### Workflow (golden-file scaffolding pattern)

The golden tests use this convention (same as slice-1 T13 `test_telegram_format`):

```python
def test_happy_path_top_3_with_tail(...) -> None:
    text = render(fixture)
    path = GOLDEN / "happy_path.txt"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        pytest.fail(f"Golden file {path.name} did not exist; wrote it. Inspect and rerun.")
    assert text == path.read_text(encoding="utf-8")
```

First pytest run: tests fail because golden files don't exist yet, but the scaffolding writes them. Inspect each `.txt` file by eye to confirm it matches §6's intended layout. Second pytest run: tests pass (text == file contents). Only commit once both pass on the second run.

### Out of scope

- No live network in tests (existing scraper integration tests, if any, untouched).
- No tests against scheduler.py's `_tg_send` (already trusted).
- No tests for `logging.basicConfig` (configures a global; brittle to test).

## 11. Acceptance criteria

| # | Criterion | Verification |
|---|---|---|
| 1 | All tests green | `cd "D:/OMNP - Quant/Projetos" && python -m pytest lib/tests Poly/tests` exit 0 |
| 2 | Mypy clean on the new code | `python -m mypy lib/telegram_format.py` exit 0 (basic mypy without --strict; project hasn't standardized) |
| 3 | Live scheduler run produces a Telegram message under 3,500 chars | Manual inspection after next scheduler `--once` (or trigger directly via `run_project`) |
| 4 | Live scheduler run produces a Telegram message with NO scanning noise | Compare lines in scheduler.log vs message body |
| 5 | Group reads it on a phone and finds the top bet within 5 seconds | Subjective — user evaluates |
| 6 | Style guide documents conventions clearly enough that a fresh reader could redesign another project | User reviews TELEGRAM_STYLE_GUIDE.md |
| 7 | `polymarket_scraper.py` still emits its JSON dump file | Inspect output dir after a run |
| 8 | Standalone run shows progress in stderr | Run `python polymarket_scraper.py` outside scheduler |

## 12. Open questions / known unknowns

1. **Style guide location** — `D:/OMNP - Quant/Projetos/docs/TELEGRAM_STYLE_GUIDE.md` is the proposed home. If the user prefers a different convention (e.g., `README.md` section, `STYLE.md` at the root, a wiki), trivial to relocate.

2. **Mypy strictness** — the Projetos repo has no shared mypy config. The lib's own typing is straightforward (no generics, no Protocols); a basic `mypy lib/` invocation suffices. If the user wants `--strict` later, the lib will pass without changes.

3. **`Poly/tests/` already exists?** — if there are existing tests in the Poly project, the new test file slots in alongside them. If not, `tests/__init__.py` is created. Implementer verifies at task time.

## 13. References

- Spec lives at this path within the Projetos repo on `feat/phase1-signal-tracking` (current branch) — to be moved if the user prefers.
- Slice-1 spec for prior style of design doc: `D:/Multi Factor/docs/superpowers/specs/2026-04-28-btc-leverage-slice1-design.md`
- Sample of current Poly Kelly output recorded in §1 above (from a recent `--once` run).
- Scheduler's `capture_to_telegram` path: `D:/OMNP - Quant/Projetos/scheduler.py` lines 270–310 (post-fix at commit `cdcc7c0`).
