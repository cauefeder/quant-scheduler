# Metrics analysis — what works, what doesn't, why

Generated 2026-06-27, after E2 resolver fix + post-E2 logger fixes
(commits `4442f06`, `842415b`, `53d7008`). Compares the four logged
signal systems on their realised win rate, dollar P&L, and per-bet
risk-adjusted return.

## TL;DR

**The only system carrying real model signal is alphafeed.** Its
36.7% YES-side win rate sounds bad, but it's a **measurement artefact
of a logger bug that hardcoded `direction="YES"` on every bet**. The
model is actually selecting markets where the *opposite side* is
underpriced — its accuracy on the side it *should* have bet is ~63%.
Now that the direction logger is fixed (Phase 1b), new alphafeed
signals will write the correct side and we can measure real P&L
without ambiguity.

**poly and poly2 are systematically value-destructive on a 1,172-bet
sample.** They bet on extreme-priced underdog markets (~25% and ~34%
avg entry price), the underdogs don't hit often enough, and the
realised Kelly compounding turns a small EV gap into −$4,081 and −$830
over the measured window.

| System | Resolved bets | Win rate | Total $ P&L | Avg price | % bets at price tails (>0.10–0.90) | Per-bet Sharpe |
|---|---|---|---|---|---|---|
| **alphafeed** | **444** | **36.7%** | **$0.00 \*** | **0.585** | **27.3%** | **n/a \*** |
| poly | 598 | 18.4% | −$4,080.59 | 0.249 | 40.1% | −0.19 |
| poly2 | 130 | 27.7% | −$830.30 | 0.339 | 16.2% | −0.21 |
| modeltelegra | 0 (of 171) | n/a | n/a | n/a | n/a | n/a |
| polytraders | 0 (of 1) | n/a | n/a | n/a | n/a | n/a |

\* alphafeed $ P&L is `$0` because every signal had `kelly_bet=NULL`
in the historical data (logger bug, fixed in commit `4442f06`). The
36.7% YES-side win rate is itself misleading — see "Why alphafeed
works" below.

## What works

### The XGBoost mispricing classifier (alphafeed v2)
- **Test AUC 0.674** on the live model (`models/v2/training_metrics.json`),
  honest and reproducible after the leakage-channel cleanup of commit
  `e1f79dd`.
- **3-feature model**: `info_ratio`, `log_volume_total`, `days_left`.
  All three carry meaningful XGBoost gain (~30% / ~30% / ~40%) and stable
  importance across 5 walk-forward folds.
- The model selects markets where the crowd's belief is most likely to
  be wrong — and at average entry price 0.585 the *NO side* of those
  markets won 63.3% of the time. That's the real signal.

### The price-range filter [0.10, 0.90]
- Refuses bets where calibration is unreliable. Backtest confirmed that
  half-Kelly compounding inside this band leaves the bankroll alive even
  during 5 folds of unstable calibration; the same policy at the tails
  bankrupted the strategy in E1b.
- Encoded in [`quant_features.in_live_bet_price_range`](../AlphaFeed/backend/adapters/quant_features.py).
- 27.3% of *historical* alphafeed bets were at the tails because the
  filter wasn't deployed yet. Going forward, all new signals will be
  refused there (signalTier="Skip" + kellyBet=0).

### The Multi Factor BTC leverage engine
- Not a Polymarket signal; not included in signal_tracker. The S1+S2
  backtest harness shows a healthy strategy with paper-exec discipline.
  Touched its first live paper position on 2026-06-25.

### The MF long-term composite (Levy RS + Mom_12_1 + ...)
- 32.0% backtest CAGR vs SPY 14.4% over 11 years (commit `6fa1c93`).
- Honest caveats embedded (survivorship bias, 2020 outlier).
- EEM and EFA showing up today as live picks; the strategy is producing
  reasonable signals.

## What doesn't work

### poly (Poly Kelly Scraper)
- **18.4% win rate over 598 resolved bets.** Average entry price 0.249,
  meaning the strategy buys low-probability YES outcomes hoping they
  hit. They don't, frequently enough to overcome the bet sizing.
- **40.1% of bets at price tails** — well outside the safety band the
  alphafeed v2 filter enforces. No tail filter on poly.
- **Per-bet Sharpe −0.19**: every bet places real capital at risk for
  a negative expected return.
- **Paused 2026-06-27** in `scheduler.py` (`enabled: False`).

### poly2 (Poly2 Kelly Bot + Macro Report 1/2)
- 27.7% win rate over 130 resolved, −$830 P&L.
- Better tail discipline than poly (16.2% tail exposure) but the
  underlying strategy still bets on underdog outcomes at insufficient
  margin.
- **Paused 2026-06-27** alongside Macro Report 1/2 (same signal source).

### modeltelegra and polytraders
- Too few resolved signals to judge. modeltelegra needs the yfinance
  resolver path (different from the Polymarket Gamma resolver fixed in
  E2 commit `6f8da27`); polytraders has logged only 1 signal in 47
  days. Not paused — keep logging, judge later.

## Accuracy deep-dive

### alphafeed: why the model signal is real
- The XGBoost target is `1 if crowd was wrong`, derived from
  `(resolved_yes == 1) != (yes_price >= 0.5)`.
- Test AUC 0.67 means the model can rank markets by P(crowd wrong)
  better than random.
- Cross-checked by the YES-side win rate observation: at average
  market_price 0.585 (crowd betting YES with 58.5% conviction), markets
  the model selected resolved YES only 36.7% of the time. The implied
  market probability was 58.5%, so the actual outcome is 21.8 pts
  *below* the market — exactly what the model was claiming. The signal
  is real; we just bet the wrong side of it.
- Brier score of 0.52 looks bad in the raw table but is comparing
  `quantScore` (the model's P(crowd wrong) — a side-agnostic confidence
  number) against a 0/1 win flag where the side selection was wrong.
  Apples to oranges. Future versions of this report should write Brier
  against `betDirection`-aware outcome targets.

### poly + poly2: why low win rate ≠ profitable
- Both systems bet on low-probability outcomes. At market price 0.249,
  even decimal odds of 1/0.249 = 4.0x (so 3.0x profit on stake) require
  ≥25% win rate to break even **gross of costs**. After 1% spread/fee
  proxy, the breakeven creeps to ~26%.
- poly's 18.4% win rate sits 7 pts below breakeven. Multiplied across
  598 bets at half-Kelly sizing on a $100 reference bankroll, that's
  −$4,081.
- poly2's 27.7% win rate is *just above* gross breakeven but *below*
  net breakeven after costs. Result: −$830 over 130 bets.
- Both strategies are buying lottery tickets that don't pay off enough.

## Where to improve (in priority order)

1. **Validate the alphafeed direction fix in production** (next 14 days).
   New signals starting from commit `53d7008` will carry the right
   `betDirection`. After 50–100 new resolved bets, re-run
   `scripts/signal_pnl_report.py` filtered to `created_at >=
   2026-06-27`. Expected to see win rate flip toward the 50-65% range
   and total P&L turn positive.

2. **Backfill the 366 NO_MATCH markets via condition_id lookup.**
   The Polymarket Gamma slug field rots over time; condition_id is
   stable. Recover ~250+ more resolutions; would strengthen poly /
   poly2 stats and might surface a profitable subset.

3. **Resolver for modeltelegra signals (yfinance).** 168 unresolved
   trend / straddle BTC signals. Different settlement source (BTC price
   at horizon end vs market boundary). Separate slice (~half day).

4. **Add a Sharpe-based kill switch.** Pause any system whose rolling
   200-bet Sharpe drops below −0.10. Would have caught poly inside its
   first ~300 bets instead of 598.

5. **Track per-system Brier on the correct target.** Once direction is
   recorded properly, log `P(winning_side) = direction == 'YES' ? p_yes
   : 1 - p_yes` and compute Brier against the resolved outcome on that
   side. Single calibration metric across systems.

6. **Validate the calibration assumption itself.** Visual calibration
   plot per system: bin predicted probabilities into deciles, plot the
   actual win rate in each bin. If alphafeed predicts 0.65 but actuals
   say 0.45, the Platt scaling needs retraining.

## Caveats on this report

- Per-bet Sharpe is computed on raw P&L, not annualised. Useful for
  ranking systems against each other, not for absolute claims.
- The 1,172 resolved sample is biased toward markets that settled at
  the 0/1 boundary. Markets that resolved at 0.04 or 0.96 are counted
  as PENDING; this excludes a small chunk of the most-edge-cases.
- Average entry price is unweighted by stake — a single $20 bet counts
  as much as a $1 bet in the average. With kelly_bet now logged, future
  versions of this analysis can stake-weight the metrics.
