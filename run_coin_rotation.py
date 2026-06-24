# 코인 모멘텀 로테이션 백테스트 — 주식 성공 패러다임을 코인에 적용
# 매월 ~15코인을 모멘텀+저변동 랭크 → 상위K 보유. Donchian(현행) 대비 비교.
import logging,warnings,os; logging.basicConfig(level=logging.ERROR); warnings.filterwarnings("ignore")
from dotenv import load_dotenv; load_dotenv()
from datetime import datetime, timedelta
import numpy as np, pandas as pd
from data.fetcher import DataFetcher
import validation as v

COINS=["BTC-USD","ETH-USD","BNB-USD","SOL-USD","XRP-USD","DOGE-USD","ADA-USD","AVAX-USD",
       "LINK-USD","DOT-USD","LTC-USD","ATOM-USD","UNI-USD","TRX-USD","BCH-USD"]
END=datetime.now().strftime("%Y-%m-%d"); START="2021-01-01"
f=DataFetcher()
closes={}
for c in COINS:
    try:
        d=f.get_ohlcv(c,START,END)
        if d is not None and not d.empty and len(d)>300: closes[c]=d["Close"]
    except Exception: pass
px=pd.DataFrame(closes); pxm=px.resample("ME").last()
print(f"코인 {len(closes)}개, {len(pxm)}개월\n")

def rotation(topk, wt="invvol"):
    syms=list(closes.keys()); dret=px[syms].pct_change()
    rets=[]
    for i in range(13,len(pxm)-1):
        date=pxm.index[i]
        mom=pxm[syms].iloc[i-1]/pxm[syms].iloc[i-12]-1
        absok=(pxm[syms].iloc[i]/pxm[syms].iloc[i-12]-1)>0
        vol=dret.loc[:date].iloc[-60:].std()
        score=mom.rank()-vol.rank()
        cand=score[[s for s in score.index if absok.get(s,False)]].dropna().sort_values(ascending=False)
        sel=cand.index[:topk].tolist()
        if not sel: rets.append(0.0); continue
        if wt=="invvol":
            w=1/vol[sel].replace(0,np.nan); w=(w/w.sum()).fillna(1/len(sel))
        else: w=pd.Series(1/len(sel),index=sel)
        fwd=pxm[sel].iloc[i+1]/pxm[sel].iloc[i]-1
        rets.append(float((fwd*w).sum()))
    r=np.array(rets); eq=np.cumprod(1+r); yrs=len(r)/12
    cagr=(eq[-1]**(1/yrs)-1)*100 if eq[-1]>0 else -100
    peak=np.maximum.accumulate(eq); mdd=((eq-peak)/peak).min()*100
    return {"CAGR%":round(cagr,1),"승률%":round((r>0).mean()*100,1),
            "샤프":round(r.mean()/r.std()*np.sqrt(12),2) if r.std()>0 else 0,
            "MDD%":round(mdd,1),"파산":v.risk_of_ruin(list(r))}

print(f"{'전략':<18}{'CAGR%':>8}{'승률%':>7}{'샤프':>6}{'MDD%':>8}{'파산':>8}")
rows=[]
for k in [3,4,5]:
    for wt in ["invvol","equal"]:
        m=rotation(k,wt); lab=f"top{k}_{wt}"; m["전략"]=lab; rows.append(m)
        print(f"{lab:<18}{m['CAGR%']:>8}{m['승률%']:>7}{m['샤프']:>6}{m['MDD%']:>8}{m['파산']:>8}")
# BTC 매수보유 벤치
btc=pxm["BTC-USD"].pct_change().dropna(); beq=np.cumprod(1+btc.values)
print(f"\nBTC 매수보유 CAGR: {(beq[-1]**(12/len(btc))-1)*100:.1f}%  (Donchian 10코인 현행: +10.7%/샤프0.72/MDD-6.3%)")
pd.DataFrame(rows).sort_values("CAGR%",ascending=False).to_csv("coin_rotation_results.csv",index=False,encoding="utf-8-sig")
print("저장: coin_rotation_results.csv")
