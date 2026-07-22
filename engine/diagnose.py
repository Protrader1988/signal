"""One-off data-quality diagnostic: inspect suspicious momentum readings."""
import yfinance as yf, pandas as pd, numpy as np
pd.set_option("display.width", 160)
susp = ["MU","INTC","AMD","CAT","GOOGL"]
raw = yf.download(susp, start="2024-01-01", progress=False, auto_adjust=True)
px = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
out=[]
t = px.index[-1]
out.append(f"latest date: {t.date()}")
for tk in susp:
    s = px[tk].dropna()
    p_now = s.loc[:t].iloc[-1]
    p_21 = s.iloc[-22] if len(s)>22 else np.nan
    p_252 = s.iloc[-253] if len(s)>253 else np.nan
    out.append(f"\n{tk}: now={p_now:.2f}  ~1mo_ago={p_21:.2f}  ~12mo_ago={p_252:.2f}  "
               f"12m_mom={(p_21/p_252-1)*100:.1f}%")
    out.append(f"  min close since 2024: {s.min():.4f} on {s.idxmin().date()}  max: {s.max():.2f}")
    # show the 6 lowest closes to spot bad ticks / near-zero prints
    low = s.nsmallest(6)
    out.append("  lowest 6 closes: " + ", ".join(f"{d.date()}={v:.4f}" for d,v in low.items()))
open("site/data/DIAG.txt","w").write("\n".join(out))
print("\n".join(out))
