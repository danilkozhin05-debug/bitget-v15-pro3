import csv, json, os, time, sys, traceback
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.parse import urlencode
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
BASE = 'https://api.bitget.com/api/v2/mix/market/history-candles'
SYMBOLS = [x.strip().upper() for x in os.getenv('TRAINING_SYMBOLS','BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LINKUSDT,AVAXUSDT,SUIUSDT').split(',') if x.strip()]
# 1m is included because the live adaptive model works on 1m candles.
INTERVALS = ['1m','5m','15m','1H']
DAYS = int(os.getenv('TRAINING_DAYS','30'))
LIMIT = 200

FEATURE_NAMES = ['ema21_dist','ema50_dist','rsi','macd_norm','vol_ratio','body_ratio','atr_pct','ema_slope','ret1','ret3']


def say(msg):
    print(msg, flush=True)


def fetch(sym, gran, start, end):
    p = {'symbol':sym,'granularity':gran,'productType':'USDT-FUTURES',
         'startTime':str(start),'endTime':str(end),'limit':str(LIMIT)}
    req = Request(BASE+'?'+urlencode(p), headers={'User-Agent':'V15-PRO-Trainer/3.0','Accept':'application/json'})
    with urlopen(req, timeout=20) as r:
        obj = json.loads(r.read().decode('utf-8'))
    if obj.get('code') != '00000':
        raise RuntimeError(f"Bitget {obj.get('code')}: {obj.get('msg')}")
    return obj.get('data') or []


def indicators(rows):
    d=pd.DataFrame(rows,columns=['timestamp','open','high','low','close','volume','turnover'])
    for c in ['open','high','low','close','volume']:
        d[c]=pd.to_numeric(d[c],errors='coerce')
    d=d.sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True)
    close=d.close
    d['ema9']=close.ewm(span=9,adjust=False).mean()
    d['ema21']=close.ewm(span=21,adjust=False).mean()
    d['ema50']=close.ewm(span=50,adjust=False).mean()
    delta=close.diff(); gain=delta.clip(lower=0).ewm(alpha=1/14,adjust=False).mean(); loss=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
    rs=gain/loss.replace(0,np.nan); d['rsi']=(100-(100/(1+rs))).fillna(50)
    ema12=close.ewm(span=12,adjust=False).mean(); ema26=close.ewm(span=26,adjust=False).mean()
    d['macd']=ema12-ema26; d['macd_signal']=d.macd.ewm(span=9,adjust=False).mean()
    tr=pd.concat([(d.high-d.low),(d.high-close.shift()).abs(),(d.low-close.shift()).abs()],axis=1).max(axis=1)
    d['atr']=tr.rolling(14).mean()
    d['vol_ma']=d.volume.rolling(20).mean()
    d['body_ratio']=(d.close-d.open).abs()/(d.high-d.low).replace(0,np.nan)
    d['ema_slope']=(d.ema21-d.ema21.shift(1))/close
    d['ret1']=close.pct_change(); d['ret3']=close.pct_change(3)
    return d.dropna().reset_index(drop=True)


def feature(row, prev, prev3):
    close=max(float(row.close),1e-12); atr=float(row.atr)
    return np.array([
        (close-float(row.ema21))/close,
        (close-float(row.ema50))/close,
        float(row.rsi)/100.0,
        float(row.macd-row.macd_signal)/close,
        float(row.volume/max(row.vol_ma,1e-12)),
        float(row.body_ratio),
        atr/close,
        (float(row.ema21)-float(prev.ema21))/close,
        (close-float(prev.close))/close,
        (close-float(prev3.close))/close,
    ],dtype=float)


def train_one(d, side, horizon=5):
    X=[]; y=[]
    end=len(d)-horizon-1
    for i in range(5,max(5,end)):
        base=float(d.iloc[i].close); atr=float(d.iloc[i].atr)
        if not np.isfinite(base) or base<=0 or not np.isfinite(atr) or atr<=0: continue
        tp=base*(1+2.8*atr/base) if side=='LONG' else base*(1-2.8*atr/base)
        sl=base*(1-1.4*atr/base) if side=='LONG' else base*(1+1.4*atr/base)
        label=None
        for j in range(i+1,min(i+horizon+1,len(d))):
            hi=float(d.iloc[j].high); lo=float(d.iloc[j].low)
            hit_tp=(hi>=tp) if side=='LONG' else (lo<=tp)
            hit_sl=(lo<=sl) if side=='LONG' else (hi>=sl)
            if hit_tp and hit_sl: label=None; break
            if hit_tp: label=1; break
            if hit_sl: label=0; break
        if label is None: continue
        f=feature(d.iloc[i],d.iloc[i-1],d.iloc[i-3])
        if np.isfinite(f).all(): X.append(f); y.append(label)
    if len(X)<100 or len(set(y))<2:
        return None
    X=np.asarray(X,float); y=np.asarray(y,float)
    mu=X.mean(axis=0); sd=np.where(X.std(axis=0)<1e-8,1.0,X.std(axis=0))
    Z=(X-mu)/sd; Z=np.column_stack([np.ones(len(Z)),Z])
    w=np.zeros(Z.shape[1],dtype=float); lr=.08; reg=.02
    for _ in range(260):
        z=np.clip(Z@w,-20,20); p=1/(1+np.exp(-z)); grad=(Z.T@(p-y))/len(y); grad[1:]+=reg*w[1:]; w-=lr*grad
    z=np.clip(Z@w,-20,20); p=1/(1+np.exp(-z));
    pred=(p>=.5).astype(float); accuracy=float(np.mean(pred==y))
    return {'feature_names':FEATURE_NAMES,'mu':mu.tolist(),'sd':sd.tolist(),'w':w.tolist(),
            'samples':int(len(y)),'wins':int(y.sum()),'losses':int((1-y).sum()),
            'base_rate':float(y.mean()),'train_accuracy':accuracy,'horizon':horizon,
            'trained_utc':datetime.now(timezone.utc).isoformat()}


def main():
    say('[OK] Historical trainer started.')
    say(f'[INFO] Symbols: {len(SYMBOLS)} | intervals: {INTERVALS} | days: {DAYS}')
    say('[INFO] Public Bitget endpoint: history-candles')
    say('[INFO] No private API keys and no real orders are used.')
    say('[INFO] 1m history is included because the live adaptive model uses 1m candles.')
    say('')
    now=int(time.time()*1000); min_t=now-DAYS*86400000
    rows=[]; stats={'downloaded':{},'errors':[],'started_utc':datetime.now(timezone.utc).isoformat()}
    models={}
    total_jobs=len(SYMBOLS)*len(INTERVALS); job=0
    for sym in SYMBOLS:
        for gran in INTERVALS:
            job+=1; step={'1m':60000,'5m':300000,'15m':900000,'1H':3600000}[gran]
            end=now; d={}
            say(f'[{job:02d}/{total_jobs}] {sym:10} {gran:>3} downloading...')
            pages=0
            try:
                while end>min_t:
                    start=max(min_t,end-LIMIT*step)
                    batch=fetch(sym,gran,start,end); pages+=1
                    if not batch: break
                    for x in batch: d[int(x[0])]=x
                    old=min(int(x[0]) for x in batch)
                    if old>=end-step: break
                    end=old-step
                    say(f'        page {pages}: +{len(batch)} candles | total {len(d)}')
                    time.sleep(0.08)
                vals=sorted(d.values(),key=lambda x:int(x[0])); rows += [[sym,gran,*x] for x in vals]
                stats['downloaded'][f'{sym}_{gran}']=len(vals); say(f'        DONE: {len(vals)} candles')
                # Train only on 1m for compatibility with live adaptive prediction.
                if gran=='1m' and len(vals)>=500:
                    clean=indicators(vals)
                    for side in ('LONG','SHORT'):
                        model=train_one(clean,side)
                        if model:
                            models[f'{sym}|{side}']=model
                            say(f'        TRAIN {side}: {model["samples"]} labelled cases | base {model["base_rate"]*100:.1f}% | acc {model["train_accuracy"]*100:.1f}%')
                        else:
                            say(f'        TRAIN {side}: insufficient labelled cases')
            except Exception as e:
                stats['errors'].append({'symbol':sym,'interval':gran,'error':str(e)}); say(f'        ERROR: {e}')
    out=os.path.join(ROOT,'historical_candles.csv')
    with open(out,'w',newline='',encoding='utf-8') as f:
        w=csv.writer(f); w.writerow(['symbol','interval','timestamp','open','high','low','close','volume','turnover']); w.writerows(rows)
    with open(os.path.join(ROOT,'historical_models.json'),'w',encoding='utf-8') as f: json.dump(models,f,ensure_ascii=False,indent=2)
    stamp=datetime.now(timezone.utc).isoformat()
    for fn in ('candle_stats.json','adaptive_stats.json'):
        p=os.path.join(ROOT,fn)
        try:
            with open(p,encoding='utf-8') as f: old=json.load(f)
        except Exception: old={}
        old.setdefault('_historical_training',{}).update({'last_training_utc':stamp,'days':DAYS,'symbols':SYMBOLS,'intervals':INTERVALS,'candles_downloaded':len(rows),'models_trained':len(models),'errors':len(stats['errors'])})
        with open(p,'w',encoding='utf-8') as f: json.dump(old,f,ensure_ascii=False,indent=2)
    stats['finished_utc']=stamp; stats['candles_total']=len(rows); stats['models_trained']=len(models)
    with open(os.path.join(ROOT,'training_report.json'),'w',encoding='utf-8') as f: json.dump(stats,f,ensure_ascii=False,indent=2)
    say(''); say('='*60); say(f'[DONE] Total candles downloaded: {len(rows)}'); say(f'[DONE] Historical ML models trained: {len(models)}'); say(f'[DONE] Saved: {out}'); say('[DONE] Saved: historical_models.json / training_report.json')
    if stats['errors']: say(f'[WARN] Jobs with errors: {len(stats["errors"])}')
    else: say('[OK] All download jobs completed successfully.')

if __name__=='__main__':
    try: main()
    except Exception: traceback.print_exc(); sys.exit(1)
