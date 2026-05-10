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
