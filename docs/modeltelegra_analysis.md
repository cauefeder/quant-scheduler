# ModelTelegra signal analysis

Generated 2026-06-28, after fixing three resolver bugs that had kept
all 171 ModelTelegra signals unresolved for 64 days. Same shape as
`metrics_analysis.md`, focused on the trend-direction + straddle
signals across 10 asset/type combinations.

## TL;DR

Of 10 distinct `(signal_type, ticker)` segments, **6 are profitable and
4 are losing**. The losses are concentrated in:

1. **BTC straddle (-$18,102 over 39 bets, 23% WR)** — the model
   systematically overpays for vol on BTC. Either IV estimates are too
   low, the 24h horizon is too short for the predicted vol to
   materialise, or both. **Pause and rework the straddle thesis.**
2. **Precious metals + oil trend (-$24 combined)** — CL=F shows 70% WR
   but negative P&L (asymmetric losing bet sizing), GC=F and SI=F are
   close-to-random win rates with negative expectancy. **Drop these
   tickers from the trend signal universe.**

The crypto/equity trend signals (SOL, BNB, ETH, SPY, QQQ, BTC) all
carry positive aggregate P&L over the measured window. **Keep these.**

## What the resolver was missing

Three bugs in `signal_tracker.resolve_modeltelegra_signals` kept this
analysis dark for 64 days:

1. **Hardcoded ticker** — `_fetch_btc_price_at` only ever queried
   `BTC-USD`, so gold (GC=F) and silver (SI=F) signals were being
   resolved against BTC's price. Wrong asset, wrong outcome.
2. **Wrong timestamp targeting** — fetched all hourly bars on the
   target *date* and returned the last close, ignoring the actual hour.
   Up to 9 hours of lookahead noise on every signal.
3. **Straddle cost unit mismatch** — ModelTelegra logs `straddle_cost`
   as a *dollar premium* ($1,734.80 on a $60k BTC), but the resolver
   treated it as a *fractional move* (0.04 = 4%). The comparison
   `move=0.02 > breakeven=1734.80` was always false, so every straddle
   was scored as a full premium loss. This is what created the
   spectacular $50k "loss" seen in the raw DB.

All three fixed in commits `<this-slice>`. Re-resolved straddle
outcomes after the unit fix.

## Per-segment results

| signal_type | ticker | n_resolved | win_rate | total_pnl | avg/bet | per-bet Sharpe | verdict |
|---|---|---|---|---|---|---|---|
| trend_direction | **SOL-USD** | 12 | **75.0%** | **+$12.38** | +$1.03 | +0.24 | **keep** |
| trend_direction | BNB-USD | 14 | 50.0% | +$6.94 | +$0.50 | +0.13 | keep |
| trend_direction | **ETH-USD** | 14 | 57.1% | +$6.14 | +$0.44 | +0.10 | keep |
| trend_direction | **SPY** | 19 | 47.4% | +$4.89 | +$0.26 | **+0.28** | keep |
| trend_direction | QQQ | 21 | 33.3% | +$3.91 | +$0.19 | +0.09 | borderline (low WR) |
| trend_direction | BTC-USD | 14 | 57.1% | +$1.71 | +$0.12 | +0.04 | modest |
| trend_direction | CL=F | 10 | 70.0% | −$2.50 | −$0.25 | −0.04 | **drop** (good WR, bad sizing) |
| trend_direction | GC=F | 13 | 46.2% | −$9.93 | −$0.76 | −0.26 | **drop** |
| trend_direction | SI=F | 15 | 40.0% | −$11.91 | −$0.79 | −0.17 | **drop** |
| straddle | BTC-USD | 39 | 23.1% | **−$18,102** | −$464 | **−0.64** | **pause + rework** |

Total realised P&L across all 171 ModelTelegra signals: **−$18,090.35**.
Excluding straddle: **+$11.63** over 132 trend_direction signals (+$0.09
per bet — a thin but real edge). Excluding straddle + the three losing
tickers (CL/GC/SI): **+$35.95** over 94 trend signals (+$0.38 per bet —
respectable).

## What works (and why)

### Trend direction on crypto + equity indices
- **SOL-USD at 75% win rate** is the standout. Small sample (12 bets)
  so confidence intervals are wide, but the strength of the signal
  warrants keeping it active and accumulating more data.
- **SPY at +0.28 per-bet Sharpe** is the best risk-adjusted segment.
  47% WR sounds modest but the wins are larger than the losses on
  average — the model is right about *direction* most-often-enough,
  and right about *magnitude* when it's right.
- The crypto trend signals (BNB/ETH/BTC) cluster around 50–57% WR with
  small positive expected P&L per bet. Consistent with a real but
  modest momentum signal on assets where 24h momentum persists.

### The 1H timeframe specifically
- Every resolved trend_direction signal is on the 1H timeframe in the
  current data — ModelTelegra is not yet logging 1D / 1W signals. When
  it does (per the `_TREND_HORIZONS_HOURS` table the resolver supports),
  re-running this analysis will tell us whether the longer horizons add
  alpha.

## What doesn't work (and why)

### BTC straddle
- **0% raw WR when the bug was present, 23% WR after the fix.** Even
  with the corrected math, 23% is well below the breakeven you need to
  pay for a 24h ATM straddle on BTC. Average straddle premium implied
  ~2.5–3% move; realised median move was below that for the window.
- **−$18k cumulative over 39 bets at avg kelly_bet ≈ $1,285** —
  the bet sizing is large, the model is wrong about vol direction often,
  and a few outsized losses dominate.
- **Recommendation:** disable straddle signals until either (a) the
  model's IV estimation is recalibrated against realised vol, or (b)
  the horizon extends past 24h (most vol moves take 2–5 days to
  materialise on BTC).

### Gold (GC=F), Silver (SI=F)
- 40–46% WR + negative P&L on 13–15 bets each. The asset class moves
  on different drivers (macro, geopolitics, real rates) than the
  short-horizon momentum signal the model is built for. The model is
  effectively guessing.
- **Recommendation:** drop these tickers from the trend signal
  universe.

### Oil (CL=F)
- **70% WR but −$2.50 P&L** — the dangerous pattern. The model is
  *right about direction* most of the time, but the few losses are
  >2x the average win. Likely cause: when oil moves against the
  signal it moves hard (geopolitical shock, OPEC surprise), and the
  wins are small mean-reverting moves.
- **Recommendation:** drop, or rework with a stop-loss layer that
  caps the bad-tail size.

## Where to improve (in priority order)

1. **Pause the straddle signals from the live Telegram channel.**
   Same `enabled: False` pattern in scheduler that we applied to poly
   and poly2. ModelTelegra is a single scheduler entry, so segment-
   level pausing means a feature flag inside the model code (not just
   scheduler config). Tracked as a follow-up.

2. **Drop CL=F / GC=F / SI=F from the trend signal universe** in
   `D:/OMNP - Quant/Projetos/ModelTelegra,/model2_trend.py` or
   wherever the universe list lives. One line change, big P&L cleanup.

3. **Expand the 1H trend universe to include more crypto + tech
   equity tickers.** SOL/BNB/ETH/BTC and SPY/QQQ all work; consider
   adding NVDA, TSLA, AVAX, MATIC.

4. **Validate the 24h horizon.** Re-run the resolver with
   `_TREND_HORIZONS_HOURS["1H"] = 48` (or 72) to see whether longer
   horizons hold the edge. If so, modify the live signals to use the
   longer horizon.

5. **Per-ticker calibration tracking.** Once we have ≥30 resolved
   signals per ticker, log the model's predicted edge vs the realised
   P&L — same Brier-score idea as recommended in
   `metrics_analysis.md`.

6. **Backfill condition_id-based Polymarket resolution.** The 366
   NO_MATCH markers from E2 are unrelated but still on the open
   improvement list.

## Caveats

- **64-day window** — too short to draw strong conclusions on any
  segment with <30 resolved signals. SOL and CL=F results are
  directional but the confidence intervals are wide.
- **Per-bet Sharpe is not annualised** — useful for ranking segments
  against each other, not as an absolute risk-adjusted return claim.
- **No transaction costs in the realised P&L** — futures (GC=F/SI=F/CL=F)
  carry ~$1–2 round-trip; crypto on yfinance is effectively spot
  pricing without slippage modeling. Real P&L would be a few dollars
  lower per segment.
- **The 24h resolution horizon for trend signals is short.** Real
  trading rarely closes positions in exactly 24h. The realised P&L
  here is an idealised "what if we entered at the signal price and
  exited at the 24h close" — not a real execution model.
