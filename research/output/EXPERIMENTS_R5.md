# Research round 5 — a Bitcoin strategy worth paper trading?

_Generated 2026-10-05T03:24:20.539047+00:00_

Pass bar (set in advance): OOS Sharpe above buy-and-hold AND OOS max drawdown no deeper than 60% of buy-and-hold's AND full-history Sharpe above buy-and-hold.

## BTC-USD  (2015-04-05 to 2026-10-05; out-of-sample from 2022-07-17)

- **buy_hold**: full CAGR 66.0% Sharpe 1.1 maxDD -83.4% | OOS CAGR 39.5% Sharpe 0.94 maxDD -53.1% | in market 100.0% · 0.0 changes/yr
- **buy_hold_vt40**: full CAGR 46.3% Sharpe 1.1 maxDD -65.2% | OOS CAGR 32.6% Sharpe 0.91 maxDD -52.5% | in market 100.0% · 26.8 changes/yr · fail
- **sma_100**: full CAGR 65.0% Sharpe 1.25 maxDD -62.4% | OOS CAGR 28.7% Sharpe 0.91 maxDD -38.1% | in market 58.4% · 12.4 changes/yr · fail
- **sma_100_vt40**: full CAGR 44.6% Sharpe 1.28 maxDD -35.4% | OOS CAGR 24.8% Sharpe 0.91 maxDD -30.9% | in market 58.4% · 27.5 changes/yr · fail
- **sma_150**: full CAGR 76.0% Sharpe 1.36 maxDD -61.8% | OOS CAGR 35.0% Sharpe 1.04 maxDD -27.6% | in market 60.5% · 6.7 changes/yr · **PASS**
- **sma_150_vt40**: full CAGR 51.4% Sharpe 1.4 maxDD -33.9% | OOS CAGR 28.9% Sharpe 1.01 maxDD -26.2% | in market 60.5% · 22.9 changes/yr · **PASS**
- **sma_200**: full CAGR 59.2% Sharpe 1.15 maxDD -70.4% | OOS CAGR 34.2% Sharpe 1.0 maxDD -32.9% | in market 60.9% · 6.5 changes/yr · fail
- **sma_200_vt40**: full CAGR 43.9% Sharpe 1.25 maxDD -47.0% | OOS CAGR 30.2% Sharpe 1.02 maxDD -25.4% | in market 60.9% · 22.6 changes/yr · **PASS**
- **cross_50_200**: full CAGR 54.2% Sharpe 1.06 maxDD -69.5% | OOS CAGR 18.1% Sharpe 0.64 maxDD -39.9% | in market 60.7% · 1.8 changes/yr · fail
- **cross_50_200_vt40**: full CAGR 34.5% Sharpe 1.03 maxDD -48.9% | OOS CAGR 13.2% Sharpe 0.56 maxDD -36.3% | in market 60.7% · 18.4 changes/yr · fail
- **mom_blend**: full CAGR 64.4% Sharpe 1.28 maxDD -62.6% | OOS CAGR 28.3% Sharpe 0.94 maxDD -27.9% | in market 83.3% · 45.1 changes/yr · fail
- **mom_blend_vt40**: full CAGR 44.1% Sharpe 1.35 maxDD -41.6% | OOS CAGR 23.7% Sharpe 0.92 maxDD -26.9% | in market 83.3% · 56.6 changes/yr · fail
- **breakout_50_20**: full CAGR 59.1% Sharpe 1.33 maxDD -53.6% | OOS CAGR 16.8% Sharpe 0.68 maxDD -33.8% | in market 39.3% · 6.7 changes/yr · fail
- **breakout_50_20_vt40**: full CAGR 43.4% Sharpe 1.42 maxDD -34.6% | OOS CAGR 14.0% Sharpe 0.65 maxDD -33.3% | in market 39.3% · 17.5 changes/yr · fail

Passing rules: sma_150, sma_150_vt40, sma_200_vt40

Yearly returns (%), buy-and-hold vs sma_200 vs mom_blend:
- 2016: hold 123.8 · sma_200 123.8 · mom_blend 117.3
- 2017: hold 1368.9 · sma_200 1368.9 · mom_blend 1061.6
- 2018: hold -73.6 · sma_200 -59.3 · mom_blend -46.4
- 2019: hold 92.2 · sma_200 56.9 · mom_blend 68.2
- 2020: hold 303.2 · sma_200 179.2 · mom_blend 178.2
- 2021: hold 59.7 · sma_200 -24.1 · mom_blend 56.5
- 2022: hold -64.3 · sma_200 0.0 · mom_blend -32.2
- 2023: hold 155.4 · sma_200 93.0 · mom_blend 75.3
- 2024: hold 121.1 · sma_200 78.4 · mom_blend 72.6
- 2025: hold -6.3 · sma_200 -19.2 · mom_blend 3.3

## ETH-USD  (2018-05-28 to 2026-10-05; out-of-sample from 2023-08-23)

- **buy_hold**: full CAGR 20.5% Sharpe 0.64 maxDD -86.4% | OOS CAGR 17.8% Sharpe 0.57 maxDD -67.6% | in market 100.0% · 0.0 changes/yr
- **buy_hold_vt40**: full CAGR 22.6% Sharpe 0.68 maxDD -64.3% | OOS CAGR 22.1% Sharpe 0.67 maxDD -55.8% | in market 100.0% · 25.2 changes/yr · fail
- **sma_100**: full CAGR 35.0% Sharpe 0.82 maxDD -74.9% | OOS CAGR 31.4% Sharpe 0.84 maxDD -50.6% | in market 52.4% · 14.0 changes/yr · fail
- **sma_100_vt40**: full CAGR 29.4% Sharpe 0.97 maxDD -45.8% | OOS CAGR 24.8% Sharpe 0.89 maxDD -40.5% | in market 52.4% · 26.3 changes/yr · **PASS**
- **sma_150**: full CAGR 38.5% Sharpe 0.86 maxDD -67.5% | OOS CAGR 30.4% Sharpe 0.82 maxDD -44.7% | in market 51.2% · 8.0 changes/yr · fail
- **sma_150_vt40**: full CAGR 34.5% Sharpe 1.1 maxDD -32.0% | OOS CAGR 28.1% Sharpe 0.99 maxDD -32.0% | in market 51.2% · 19.9 changes/yr · **PASS**
- **sma_200**: full CAGR 42.8% Sharpe 0.91 maxDD -71.5% | OOS CAGR 26.4% Sharpe 0.75 maxDD -38.9% | in market 50.8% · 5.4 changes/yr · **PASS**
- **sma_200_vt40**: full CAGR 34.0% Sharpe 1.1 maxDD -37.2% | OOS CAGR 26.0% Sharpe 0.92 maxDD -24.5% | in market 50.8% · 17.1 changes/yr · **PASS**
- **cross_50_200**: full CAGR 27.4% Sharpe 0.71 maxDD -78.1% | OOS CAGR 2.5% Sharpe 0.28 maxDD -61.9% | in market 50.8% · 2.0 changes/yr · fail
- **cross_50_200_vt40**: full CAGR 21.3% Sharpe 0.78 maxDD -45.9% | OOS CAGR 8.9% Sharpe 0.43 maxDD -45.4% | in market 50.8% · 14.2 changes/yr · fail
- **mom_blend**: full CAGR 37.5% Sharpe 0.86 maxDD -60.7% | OOS CAGR 20.2% Sharpe 0.66 maxDD -42.2% | in market 78.0% · 43.8 changes/yr · fail
- **mom_blend_vt40**: full CAGR 27.4% Sharpe 0.98 maxDD -31.5% | OOS CAGR 19.6% Sharpe 0.78 maxDD -28.9% | in market 78.0% · 52.1 changes/yr · **PASS**
- **breakout_50_20**: full CAGR 36.0% Sharpe 0.89 maxDD -65.0% | OOS CAGR 28.4% Sharpe 0.86 maxDD -35.2% | in market 36.1% · 6.6 changes/yr · **PASS**
- **breakout_50_20_vt40**: full CAGR 24.8% Sharpe 0.95 maxDD -39.1% | OOS CAGR 21.4% Sharpe 0.87 maxDD -28.6% | in market 36.1% · 15.4 changes/yr · **PASS**

Passing rules: sma_100_vt40, sma_150_vt40, sma_200, sma_200_vt40, mom_blend_vt40, breakout_50_20, breakout_50_20_vt40

Yearly returns (%), buy-and-hold vs sma_200 vs mom_blend:
- 2019: hold -2.8 · sma_200 5.0 · mom_blend 10.2
- 2020: hold 469.2 · sma_200 136.5 · mom_blend 168.6
- 2021: hold 399.1 · sma_200 287.7 · mom_blend 328.8
- 2022: hold -67.5 · sma_200 -16.7 · mom_blend -29.3
- 2023: hold 90.6 · sma_200 48.5 · mom_blend 29.3
- 2024: hold 46.1 · sma_200 46.9 · mom_blend 17.6
- 2025: hold -11.0 · sma_200 -6.7 · mom_blend 8.3
