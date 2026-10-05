# Research round 4 — win bigger, lose smaller?

_Generated 2026-10-05T03:10:58.769537+00:00_

## EXP A Stops on the position book (per trade, after 20 bps round trip)
- **no_stop**: n 1422 · win 55.8% · avg trade 1.77% · avg win 8.23% · avg loss -6.4% · worst -33.7% · win/loss 1.29 · t 6.32 | OOS avg trade 2.65% avg loss -5.97% (n 570)
- **atr2_stop**: n 1422 · win 35.9% · avg trade 0.64% · avg win 9.68% · avg loss -4.41% · worst -27.0% · win/loss 2.19 · t 2.63 | OOS avg trade 1.19% avg loss -4.36% (n 570)
- **atr3_stop**: n 1422 · win 44.4% · avg trade 0.91% · avg win 8.95% · avg loss -5.51% · worst -33.7% · win/loss 1.62 · t 3.46 | OOS avg trade 1.46% avg loss -5.5% (n 570)
- **trail10_stop**: n 1422 · win 50.6% · avg trade 1.12% · avg win 8.24% · avg loss -6.18% · worst -21.3% · win/loss 1.33 · t 4.24 | OOS avg trade 1.75% avg loss -5.95% (n 570)
- **trail15_stop**: n 1422 · win 54.9% · avg trade 1.53% · avg win 8.18% · avg loss -6.58% · worst -21.3% · win/loss 1.24 · t 5.52 | OOS avg trade 2.24% avg loss -6.33% (n 570)

## EXP B Multi-asset trend following (2007+)
- **trend_long_flat_12m**: full CAGR 3.7% Sharpe 0.76 maxDD -14.4% | OOS CAGR 3.4% Sharpe 0.62 maxDD -14.4% | years {'2008': 0.4, '2018': -3.9, '2020': -3.5, '2022': -2.9}
- **trend_long_flat_blend**: full CAGR 2.5% Sharpe 0.65 maxDD -11.8% | OOS CAGR 2.3% Sharpe 0.56 maxDD -11.8% | years {'2008': 4.3, '2018': -1.6, '2020': -2.4, '2022': 0.2}
- **trend_long_short_blend**: full CAGR 1.6% Sharpe 0.31 maxDD -15.6% | OOS CAGR 0.6% Sharpe 0.14 maxDD -15.6% | years {'2008': 12.6, '2018': -1.2, '2020': -8.0, '2022': 5.0}
- **spy_buy_hold**: full CAGR 10.9% Sharpe 0.63 maxDD -55.2% | OOS CAGR 15.9% Sharpe 0.85 maxDD -33.7% | years {'2008': -36.8, '2018': -4.6, '2020': 18.3, '2022': -18.2}
- **equal_weight_all_assets**: full CAGR 7.0% Sharpe 0.65 maxDD -34.7% | OOS CAGR 9.7% Sharpe 0.86 maxDD -23.4%

## EXP C Stock selection variants (same universe)
- **momentum**: full CAGR 27.1% Sharpe 1.04 maxDD -31.2% | OOS CAGR 36.6% Sharpe 1.49 maxDD -26.7%
- **momentum_ex_high_vol**: full CAGR 12.7% Sharpe 0.74 maxDD -30.3% | OOS CAGR 14.6% Sharpe 1.02 maxDD -16.7%
- **smooth_momentum**: full CAGR 16.0% Sharpe 0.78 maxDD -29.9% | OOS CAGR 16.8% Sharpe 0.98 maxDD -19.7%
- **low_vol**: full CAGR 6.8% Sharpe 0.54 maxDD -28.4% | OOS CAGR 8.3% Sharpe 0.78 maxDD -10.5%
- **equal_weight_same_universe**: full CAGR 14.7% Sharpe 0.81 maxDD -35.5% | OOS CAGR 18.2% Sharpe 1.28 maxDD -17.5%

## EXP D Equity momentum + trend, combined
- **equity_momentum_voltarget15**: full CAGR 16.3% Sharpe 1.06 maxDD -19.1% | OOS CAGR 26.4% Sharpe 1.59 maxDD -16.0%
- **trend_same_period**: full CAGR 2.3% Sharpe 0.59 maxDD -11.8% | OOS CAGR 4.6% Sharpe 1.24 maxDD -3.4%
- **combo_50_50**: full CAGR 9.3% Sharpe 1.05 maxDD -13.4% | OOS CAGR 15.3% Sharpe 1.64 maxDD -9.5%
- correlation_daily: 0.51
- trend_variant_used: trend_long_flat_blend
- **spy_same_period**: full CAGR 14.5% Sharpe 0.81 maxDD -33.7% | OOS CAGR 21.2% Sharpe 1.34 maxDD -18.8%

## EXP F Conditions-score sizing on the momentum book
- **constant_100pct**: full CAGR 27.1% Sharpe 1.04 maxDD -31.2% | OOS CAGR 36.6% Sharpe 1.49 maxDD -26.7%
- **conditions_score_sizing**: full CAGR 15.6% Sharpe 1.04 maxDD -16.6% | OOS CAGR 25.7% Sharpe 1.49 maxDD -15.3%
- **constant_at_same_average_size**: full CAGR 18.3% Sharpe 1.04 maxDD -21.4% | OOS CAGR 23.9% Sharpe 1.49 maxDD -18.5%
- **vol_target_15pct**: full CAGR 16.3% Sharpe 1.06 maxDD -19.1% | OOS CAGR 26.4% Sharpe 1.59 maxDD -16.0%
- average_exposure_pct: 66.7
- days_by_exposure_pct: {'0': 197, '25': 290, '50': 277, '75': 717, '100': 718}
- credit_check_available: True
- spy_next_month_by_score: {'0': {'avg_pct': 1.89, 'days': 62}, '1': {'avg_pct': 3.6, 'days': 196}, '2': {'avg_pct': 0.82, 'days': 143}, '3': {'avg_pct': 0.49, 'days': 402}, '4': {'avg_pct': 1.12, 'days': 790}, '5': {'avg_pct': 1.33, 'days': 788}}

## EXP E Risk dial on the combination (up to 2x, 5%/yr financing)
- **vol_target_10pct**: full CAGR 10.7% Sharpe 1.02 maxDD -12.1% | OOS CAGR 16.7% Sharpe 1.53 maxDD -9.8%
- **vol_target_15pct**: full CAGR 12.9% Sharpe 0.88 maxDD -18.1% | OOS CAGR 22.2% Sharpe 1.4 maxDD -14.7%
- **vol_target_20pct**: full CAGR 13.3% Sharpe 0.82 maxDD -21.8% | OOS CAGR 24.8% Sharpe 1.34 maxDD -18.7%
- **vol_target_25pct**: full CAGR 13.2% Sharpe 0.8 maxDD -24.1% | OOS CAGR 25.4% Sharpe 1.36 maxDD -18.8%