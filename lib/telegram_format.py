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
