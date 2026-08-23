import os, json, time, hmac, base64, hashlib, asyncio, logging, csv, re, shutil, subprocess
from dataclasses import dataclass, field
from pathlib import Path
import unicodedata
from getpass import getpass

import aiohttp
import pandas as pd
import numpy as np
from dotenv import load_dotenv
try:
    from colorama import init as colorama_init, Fore, Back, Style
    colorama_init(autoreset=True)
except ImportError:
    class _NoColor:
        def __getattr__(self, _name):
            return ""
    Fore = Back = Style = _NoColor()
    def colorama_init(*_args, **_kwargs):
        pass

load_dotenv()

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "BNBUSDT", "SUIUSDT", "LINKUSDT", "AVAXUSDT", "ADAUSDT",
    "LTCUSDT", "HYPEUSDT", "PEPEUSDT", "TAOUSDT", "TRXUSDT",
    "NEARUSDT", "ONDOUSDT", "WLDUSDT", "ZECUSDT", "PUMPUSDT",
]

@dataclass
class Config:
    symbol: str = os.getenv("SYMBOL", "BTCUSDT").upper()
    symbols: list = field(default_factory=lambda: [s.strip().upper() for s in os.getenv("SYMBOLS", ",".join(DEFAULT_SYMBOLS)).split(",") if s.strip()])
    product_type: str = os.getenv("PRODUCT_TYPE", "USDT-FUTURES")
    deposit: float = float(os.getenv("DEPOSIT_USDT", "57"))
    leverage: int = int(os.getenv("LEVERAGE", "3"))
    risk_pct: float = float(os.getenv("RISK_PER_TRADE_PCT", "1.0"))
    min_score: int = int(os.getenv("MIN_SIGNAL_SCORE", "78"))
    signal_cooldown_sec: int = int(os.getenv("SIGNAL_COOLDOWN_SEC", "1800"))
    scan_interval_sec: int = int(os.getenv("SCAN_INTERVAL_SEC", "15"))
    ntfy_server: str = os.getenv("NTFY_SERVER", "https://ntfy.sh")
    ntfy_topic: str = os.getenv("NTFY_TOPIC", "p2psignalsp2p")
    live: bool = os.getenv("LIVE_REAL", os.getenv("LIVE_TRADING", "false")).lower() == "true"
    auto_trade: bool = os.getenv("AUTO_REAL", os.getenv("AUTO_TRADING", "false")).lower() == "true"
    max_auto_positions: int = int(os.getenv("MAX_AUTO_POSITIONS", "1"))
    signal_window_default_min: int = int(os.getenv("SIGNAL_WINDOW_DEFAULT_MIN", "3"))
    api_key: str = os.getenv("BITGET_API_KEY", "")
    api_secret: str = os.getenv("BITGET_API_SECRET", "")
    passphrase: str = os.getenv("BITGET_API_PASSPHRASE", "")
    prompt_api_keys: bool = os.getenv("PROMPT_API_KEYS", "true").lower() == "true"
    margin_mode: str = os.getenv("MARGIN_MODE", "isolated")
    margin_coin: str = os.getenv("MARGIN_COIN", "USDT")
    taker_fee_pct: float = float(os.getenv("TAKER_FEE_PCT", "0.06"))
    max_concurrency: int = int(os.getenv("MAX_CONCURRENCY", "6"))
    # Multiple partial take-profits. Percent is price movement from entry.
    # For the small $57 default deposit: 50% / 30% / 20%.
    # TP1 is intended to be the risk-reduction point; after TP1 the
    # position manager can use the remaining position for TP2/TP3.
    tp1_pct: float = float(os.getenv("TP1_PCT", "0.30"))
    tp2_pct: float = float(os.getenv("TP2_PCT", "0.60"))
    tp3_pct: float = float(os.getenv("TP3_PCT", "1.00"))
    tp1_close_pct: float = float(os.getenv("TP1_CLOSE_PCT", "50"))
    tp2_close_pct: float = float(os.getenv("TP2_CLOSE_PCT", "30"))
    tp3_close_pct: float = float(os.getenv("TP3_CLOSE_PCT", "20"))
    tp_trigger_type: str = os.getenv("TP_TRIGGER_TYPE", "mark_price")
    adaptive_model_enabled: bool = os.getenv("ADAPTIVE_MODEL_ENABLED", "true").lower() == "true"
    adaptive_min_samples: int = int(os.getenv("ADAPTIVE_MIN_SAMPLES", "60"))
    adaptive_weight: float = float(os.getenv("ADAPTIVE_MODEL_WEIGHT", "0.40"))
    historical_model_weight: float = float(os.getenv("HISTORICAL_MODEL_WEIGHT", "0.20"))

CFG = Config()
REST = "https://api.bitget.com"

class ColorFormatter(logging.Formatter):
    LEVEL_COLORS = {
        logging.INFO: Fore.CYAN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.CRITICAL: Fore.RED + Style.BRIGHT,
        logging.DEBUG: Fore.LIGHTBLACK_EX,
    }
    def format(self, record):
        return self.LEVEL_COLORS.get(record.levelno, Fore.WHITE) + super().format(record) + Style.RESET_ALL

_handler = logging.StreamHandler()
_handler.setFormatter(ColorFormatter('%(asctime)s | %(levelname)s | %(message)s'))
logging.basicConfig(level=logging.INFO, handlers=[_handler], force=True)


def csv_log(row):
    p = Path("signals.csv")
    exists = p.exists()
    with p.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not exists:
            w.writeheader()
        w.writerow(row)


ADAPTIVE_STATS_PATH = Path("adaptive_stats.json")
HISTORICAL_MODEL_PATH = Path("historical_models.json")

def load_historical_models():
    try:
        if HISTORICAL_MODEL_PATH.exists():
            data=json.loads(HISTORICAL_MODEL_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data,dict) else {}
    except Exception as e:
        logging.warning("historical_models read: %s",e)
    return {}

def historical_model_prediction(symbol, side, current_features):
    models=load_historical_models()
    m=models.get(f"{symbol}|{side}")
    if not m or int(m.get("samples",0)) < 100:
        return None
    try:
        mu=np.asarray(m["mu"],dtype=float); sd=np.asarray(m["sd"],dtype=float)
        w=np.asarray(m["w"],dtype=float)
        x=np.asarray(current_features,dtype=float)
        if len(x)!=len(mu) or len(w)!=len(mu)+1: return None
        sd=np.where(sd<1e-8,1.0,sd)
        z=np.clip(np.array([1.0,*((x-mu)/sd)])@w,-20,20)
        prob=float(1/(1+np.exp(-z)))
        base=float(m.get("base_rate",0.5))
        prob=0.80*prob+0.20*base
        return {"prob":prob,"samples":int(m.get("samples",0)),"base_rate":base,"horizon":int(m.get("horizon",5))}
    except Exception as e:
        logging.warning("historical model prediction: %s",e)
        return None

def load_adaptive_stats():
    try:
        if ADAPTIVE_STATS_PATH.exists():
            data=json.loads(ADAPTIVE_STATS_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data,dict) else {}
    except Exception as e:
        logging.warning("adaptive_stats read: %s",e)
    return {}

def update_adaptive_trade(side, setup, net):
    """Persist real closed-trade outcomes for setup/side adaptation."""
    try:
        stats=load_adaptive_stats(); key=f"{side}|{setup}"
        x=stats.get(key,{"wins":0,"losses":0,"net":0.0,"trades":0})
        x["trades"]=int(x.get("trades",0))+1
        if float(net)>0: x["wins"]=int(x.get("wins",0))+1
        else: x["losses"]=int(x.get("losses",0))+1
        x["net"]=float(x.get("net",0.0))+float(net)
        stats[key]=x
        ADAPTIVE_STATS_PATH.write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding="utf-8")
    except Exception as e:
        logging.warning("adaptive_stats write: %s",e)

def adaptive_trade_adjustment(side, setup):
    """Bayesian-smoothed adjustment from REAL closed trades only."""
    x=load_adaptive_stats().get(f"{side}|{setup}")
    if not x or int(x.get("trades",0))<5: return 0, None
    n=int(x.get("trades",0)); wins=int(x.get("wins",0))
    rate=(wins+1.5)/(n+3.0)  # mild 50% prior
    adj=int(max(-10,min(10,(rate-0.5)*30)))
    return adj, rate


async def notify(text, priority="high"):
    """Send ntfy without ever stopping the market-analysis loop."""
    url = CFG.ntfy_server.rstrip("/") + "/" + CFG.ntfy_topic
    headers = {
        "Title": "Bitget STRICT Signal",
        "Priority": priority,
        "Tags": "chart_with_upwards_trend",
        "Content-Type": "text/plain; charset=utf-8",
    }
    timeout = aiohttp.ClientTimeout(total=8, connect=3, sock_read=4)
    last_error = None
    for attempt in range(1, 4):
        try:
            async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as s:
                async with s.post(url, data=text.encode("utf-8"), headers=headers) as r:
                    body = await r.text()
                    if r.status < 300:
                        logging.info("ntfy: уведомление отправлено (попытка %s)", attempt)
                        return True
                    last_error = f"HTTP {r.status}: {body[:180]}"
        except Exception as e:
            last_error = f"{type(e).__name__}: {str(e) or '<empty>'}"
        if attempt < 3:
            await asyncio.sleep(attempt)
    logging.warning("ntfy недоступен после 3 попыток: %s. Анализ НЕ останавливаем.", last_error)
    return False


class Bitget:
    def __init__(self, cfg):
        self.cfg = cfg
        self._sem = asyncio.Semaphore(cfg.max_concurrency)

    def _sign(self, ts, method, path, body=""):
        msg = f"{ts}{method.upper()}{path}{body}"
        digest = hmac.new(self.cfg.api_secret.encode(), msg.encode(), hashlib.sha256).digest()
        return base64.b64encode(digest).decode()

    async def get(self, session, path, params):
        url = REST + path
        last = None
        for attempt in range(1, 4):
            try:
                async with self._sem:
                    timeout = aiohttp.ClientTimeout(total=10, connect=4, sock_read=7)
                    async with session.get(url, params=params, timeout=timeout) as r:
                        text = await r.text()
                        if r.status != 200:
                            raise RuntimeError(f"HTTP {r.status}: {text[:250]}")
                        data = json.loads(text)
                        if data.get("code") not in (None, "00000", "0", 0):
                            raise RuntimeError(str(data))
                        return data
            except asyncio.TimeoutError:
                last = "TimeoutError"
            except aiohttp.ClientError as e:
                last = f"ClientError: {e}"
            except Exception as e:
                last = str(e)
            if attempt < 3:
                await asyncio.sleep(0.4 * attempt)
        raise RuntimeError(f"Bitget API error: {last}")

    async def candles(self, session, symbol, granularity, limit=200):
        path = "/api/v3/market/candles"
        target = max(200, int(limit))
        batches, end_ms, remaining = [], None, target
        for _ in range(4):
            take = min(100, remaining)
            params = {
                "category": self.cfg.product_type,
                "symbol": symbol,
                "interval": granularity,
                "type": "MARKET",
                "limit": str(take),
            }
            if end_ms is not None:
                params["endTime"] = str(end_ms)
            data = await self.get(session, path, params)
            rows = data.get("data", [])
            if not rows:
                break
            batches.extend(rows)
            if len(rows) < take:
                break
            try:
                oldest = min(int(r[0]) for r in rows)
            except Exception:
                break
            end_ms = oldest - 1
            remaining = target - len(batches)
            if remaining <= 0:
                break
        if not batches:
            raise RuntimeError(f"Bitget вернул 0 свечей для {symbol} {granularity}")
        df = pd.DataFrame(batches)
        if df.shape[1] < 6:
            raise RuntimeError(f"Неожиданный формат свечей: {df.shape[1]} колонок")
        df = df.iloc[:, :6]
        df.columns = ["ts", "open", "high", "low", "close", "volume"]
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["ts"] = pd.to_datetime(pd.to_numeric(df["ts"], errors="coerce"), unit="ms", utc=True)
        df = df.dropna(subset=["ts", "open", "high", "low", "close", "volume"])
        return df.sort_values("ts").drop_duplicates("ts").tail(target).reset_index(drop=True)

    async def tickers(self, session):
        """Fetch all USDT futures tickers in one request for a true 1-second UI refresh."""
        data = await self.get(session, "/api/v2/mix/market/tickers", {"productType": self.cfg.product_type})
        out = {}
        for row in data.get("data", []):
            sym = str(row.get("symbol", "")).upper()
            try:
                if sym and row.get("lastPr") is not None:
                    out[sym] = {
                        "price": float(row.get("lastPr")),
                        "bid": float(row.get("bidPr") or 0),
                        "ask": float(row.get("askPr") or 0),
                        "ts": int(row.get("ts") or 0),
                        "change24h": float(row.get("change24h") or 0),
                        "funding": float(row.get("fundingRate") or 0),
                    }
            except (TypeError, ValueError):
                continue
        if not out:
            raise RuntimeError("Bitget не вернул тикеры")
        return out

    async def _server_timestamp(self, session):
        """Return Bitget server time in milliseconds."""
        async with session.get(REST + "/api/v2/public/time", timeout=aiohttp.ClientTimeout(total=5)) as r:
            text = await r.text()
            if r.status != 200:
                raise RuntimeError(f"Bitget time HTTP {r.status}: {text[:200]}")
            data = json.loads(text)
            raw = data.get("data")
            if isinstance(raw, dict):
                raw = raw.get("serverTime") or raw.get("time")
            if raw is None:
                raw = data.get("serverTime")
            if raw is None:
                raise RuntimeError(f"Bitget time response has no timestamp: {text[:200]}")
            return str(int(raw))

    @staticmethod
    def _query_string(params):
        from urllib.parse import urlencode
        if not params:
            return ""
        # Bitget requires GET query parameters to be sorted by key for the
        # signature. We use this exact string both for signing and for the URL.
        items = [(str(k), "" if v is None else str(v)) for k, v in params.items()]
        items.sort(key=lambda kv: kv[0])
        return urlencode(items)

    async def _private_request(self, session, method, path, params=None, body=None):
        """Authenticated Bitget Classic v2 REST request with an exact signed URL/body."""
        method = method.upper()
        params = params or {}
        body = body or {}

        # Serialize once. The exact bytes/string signed must be the exact body sent.
        body_text = "" if method == "GET" else json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        query = self._query_string(params) if method == "GET" else ""
        request_path = path + (("?" + query) if query else "")

        last = None
        for attempt in range(1, 4):
            try:
                # Re-read server time for every attempt. This avoids both local-clock
                # drift and a stale signature after a retry.
                ts = await self._server_timestamp(session)
                prehash = f"{ts}{method}{request_path}{body_text}"
                digest = hmac.new(self.cfg.api_secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256).digest()
                signature = base64.b64encode(digest).decode("ascii")
                headers = {
                    "ACCESS-KEY": self.cfg.api_key.strip(),
                    "ACCESS-SIGN": signature,
                    "ACCESS-PASSPHRASE": self.cfg.passphrase,
                    "ACCESS-TIMESTAMP": ts,
                    "Content-Type": "application/json",
                    "locale": "en-US",
                }
                url = REST + request_path
                async with self._sem:
                    timeout = aiohttp.ClientTimeout(total=10, connect=4, sock_read=7)
                    async with session.request(method, url, data=body_text if method != "GET" else None, headers=headers, timeout=timeout) as r:
                        text = await r.text()
                        try:
                            data = json.loads(text)
                        except Exception:
                            data = None
                        if r.status != 200:
                            code = str(data.get("code")) if isinstance(data, dict) else "HTTP"
                            msg = data.get("msg") if isinstance(data, dict) else text[:300]
                            if code == "40012":
                                raise RuntimeError(
                                    "Bitget 40012: API key/passphrase are incorrect. "
                                    "The API key, Secret Key and Passphrase must belong to the SAME HMAC API key. "
                                    "If the passphrase is forgotten, recreate the API key in Bitget; it cannot be recovered."
                                )
                            if code == "40036":
                                raise RuntimeError("Bitget 40036: API passphrase is incorrect. Recreate the API key if the passphrase is forgotten.")
                            if code == "40037":
                                raise RuntimeError("Bitget 40037: API key does not exist. Check that the key was not deleted or recreated.")
                            if code == "40038":
                                raise RuntimeError("Bitget 40038: this machine IP is not allowed by the API key whitelist.")
                            raise RuntimeError(f"HTTP {r.status}: code={code} msg={msg}")
                        if not isinstance(data, dict):
                            raise RuntimeError(f"Bitget returned non-JSON response: {text[:300]}")
                        code = str(data.get("code"))
                        if code not in ("00000", "0", "None"):
                            raise RuntimeError(f"Bitget {code}: {data.get("msg")}")
                        return data.get("data")
            except asyncio.TimeoutError:
                last = "TimeoutError"
            except aiohttp.ClientError as e:
                last = f"ClientError: {e}"
            except Exception as e:
                last = str(e)
                # A signature error will not be fixed by retrying the same credentials.
                if "40009" in last or "sign signature error" in last.lower():
                    raise RuntimeError("Bitget 40009: signature error. Check HMAC Secret Key, API key type, server time and that the exact request URL/body is signed.") from e
            if attempt < 3:
                await asyncio.sleep(0.5 * attempt)
        raise RuntimeError(f"Bitget private API error: {last}")

    async def account(self, session, symbol):
        return await self._private_request(session, "GET", "/api/v2/mix/account/account", {
            "symbol": symbol, "productType": self.cfg.product_type, "marginCoin": self.cfg.margin_coin
        })

    async def positions(self, session):
        return await self._private_request(session, "GET", "/api/v2/mix/position/all-position", {
            "productType": self.cfg.product_type, "marginCoin": self.cfg.margin_coin
        })

    async def order_detail(self, session, symbol, order_id):
        return await self._private_request(session, "GET", "/api/v2/mix/order/detail", {
            "symbol": symbol, "orderId": order_id, "productType": self.cfg.product_type
        })

    async def fills(self, session, symbol, order_id):
        return await self._private_request(session, "GET", "/api/v2/mix/order/fills", {
            "symbol": symbol, "orderId": order_id, "productType": self.cfg.product_type, "limit": "100"
        })

    async def recent_realized_pnl(self, session, symbol, start_ms):
        end_ms=int(time.time()*1000)+2000
        data=await self._private_request(session, "GET", "/api/v2/mix/order/fills", {
            "symbol": symbol, "productType": self.cfg.product_type,
            "startTime": str(int(start_ms)), "endTime": str(end_ms), "limit": "100"
        })
        rows=(data or {}).get("fillList", []) if isinstance(data, dict) else []
        profit=0.0; fee=0.0
        for row in rows:
            trade_side=str(row.get("tradeSide","")).lower()
            if trade_side not in ("close","reduce_close_long","reduce_close_short","offset_close_long","offset_close_short"):
                continue
            try: profit += float(row.get("profit") or 0)
            except Exception: pass
            for fd in (row.get("feeDetail") or []):
                try: fee += float(fd.get("totalFee") or 0)
                except Exception: pass
        # Bitget fee values are normally negative; NET = profit + fee.
        return {"profit": profit, "fee": fee, "net": profit + fee}

    async def set_leverage(self, session, symbol, hold_side):
        return await self._private_request(session, "POST", "/api/v2/mix/account/set-leverage", body={
            "symbol": symbol, "productType": self.cfg.product_type, "marginCoin": self.cfg.margin_coin,
            "leverage": str(self.cfg.leverage), "holdSide": hold_side
        })

    async def place_market_order(self, session, symbol, side, size, sl, tp, price_place):
        # Entry order. TP is NOT attached here because V15 uses three partial
        # exchange-side TP plan orders created after the real fill price is known.
        # The exchange-side SL remains attached to the entry order.
        hold_side = "long" if side == "LONG" else "short"
        await self.set_leverage(session, symbol, hold_side)
        fmt = f"{{:.{max(0,int(price_place))}f}}"
        client_oid = "V15PRO" + str(int(time.time() * 1000))
        body = {
            "symbol": symbol,
            "productType": self.cfg.product_type,
            "marginMode": self.cfg.margin_mode,
            "marginCoin": self.cfg.margin_coin,
            "size": str(size),
            "side": "sell" if side == "LONG" else "buy",
            "tradeSide": "open",
            "orderType": "market",
            "clientOid": client_oid,
            "reduceOnly": "NO",
            "presetStopLossPrice": fmt.format(sl),
            "presetStopLossExecutePrice": fmt.format(sl),
        }
        return await self._private_request(session, "POST", "/api/v2/mix/order/place-order", body=body)

    async def place_partial_tp(self, session, symbol, side, size, trigger_price, price_place, level):
        """Place a real exchange-side partial take-profit plan on Bitget.

        Bitget Classic V2 supports profit_plan orders with an explicit size, so
        each TP can live on the exchange independently of this Python process.
        Market execution (executePrice=0) avoids a second limit-order failure.
        """
        if size <= 0:
            raise ValueError(f"TP{level}: size must be > 0")
        fmt = f"{{:.{max(0,int(price_place))}f}}"
        hold_side = "long" if side == "LONG" else "short"
        client_oid = f"V15TP{level}-" + str(int(time.time()*1000))
        body = {
            "marginCoin": self.cfg.margin_coin,
            "productType": self.cfg.product_type,
            "symbol": symbol,
            "planType": "profit_plan",
            "triggerPrice": fmt.format(trigger_price),
            "triggerType": self.cfg.tp_trigger_type,
            "executePrice": "0",
            "holdSide": hold_side,
            "size": str(size),
            "clientOid": client_oid,
        }
        return await self._private_request(session, "POST", "/api/v2/mix/order/place-tpsl-order", body=body)

    async def cancel_plan_orders(self, session, symbol, order_ids):
        ids=[{"orderId":str(x)} for x in order_ids if x]
        if not ids:
            return None
        body={
            "orderIdList": ids,
            "symbol": symbol,
            "productType": self.cfg.product_type,
            "marginCoin": self.cfg.margin_coin,
            "planType": "profit_plan",
        }
        return await self._private_request(session, "POST", "/api/v2/mix/order/cancel-plan-order", body=body)

    async def close_partial_market(self, session, symbol, side, size):
        # Hedge-mode close: reverse side + tradeSide=close.
        if size <= 0:
            return None
        body = {
            "symbol": symbol,
            "productType": self.cfg.product_type,
            "marginMode": self.cfg.margin_mode,
            "marginCoin": self.cfg.margin_coin,
            "size": str(size),
            # Hedge mode: Bitget uses the same directional side when closing:
            # close long = sell/close, close short = buy/close.
            "side": "sell" if side == "LONG" else "buy",
            "tradeSide": "close",
            "orderType": "market",
            "clientOid": "V15PT" + str(int(time.time() * 1000)),
            "reduceOnly": "NO",
        }
        return await self._private_request(session, "POST", "/api/v2/mix/order/place-order", body=body)

    async def contract(self, session, symbol):
        data = await self.get(session, "/api/v2/mix/market/contracts", {
            "productType": self.cfg.product_type,
            "symbol": symbol,
        })
        row = (data.get("data") or [{}])[0]
        if not row:
            raise RuntimeError(f"Bitget не вернул параметры контракта {symbol}")
        return row


def atr(df, period=14):
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([high-low, (high-prev_close).abs(), (low-prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean()


def add_indicators(df):
    d = df.copy()
    for n in [9, 21, 50, 200]:
        d[f"ema{n}"] = d.close.ewm(span=n, adjust=False).mean()
    d["atr"] = atr(d)
    delta = d.close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    d["rsi"] = 100 - (100 / (1 + rs))
    ema12 = d.close.ewm(span=12, adjust=False).mean()
    ema26 = d.close.ewm(span=26, adjust=False).mean()
    d["macd"] = ema12 - ema26
    d["macd_signal"] = d.macd.ewm(span=9, adjust=False).mean()
    d["vol_ma"] = d.volume.rolling(20).mean()
    d["hh20"] = d.high.rolling(20).max().shift(1)
    d["ll20"] = d.low.rolling(20).min().shift(1)
    d["range"] = d.high - d.low
    d["body"] = (d.close-d.open).abs()
    d["body_ratio"] = d["body"] / d["range"].replace(0, np.nan)
    d["upper_wick"] = d.high - d[["open","close"]].max(axis=1)
    d["lower_wick"] = d[["open","close"]].min(axis=1) - d.low
    # Classic candle features; they are filters, not standalone predictions.
    d["bull_engulf"] = (
        (d.close > d.open) & (d.close.shift(1) < d.open.shift(1)) &
        (d.close >= d.open.shift(1)) & (d.open <= d.close.shift(1))
    )
    d["bear_engulf"] = (
        (d.close < d.open) & (d.close.shift(1) > d.open.shift(1)) &
        (d.open >= d.close.shift(1)) & (d.close <= d.open.shift(1))
    )
    d["hammer"] = (d.lower_wick >= d.body * 2) & (d.upper_wick <= d.body * 0.8)
    d["shooting_star"] = (d.upper_wick >= d.body * 2) & (d.lower_wick <= d.body * 0.8)
    # Expanded candlestick knowledge engine: pattern recognition is contextual,
    # never a standalone trade trigger.
    prev_bull = d.close.shift(1) > d.open.shift(1)
    prev_bear = d.close.shift(1) < d.open.shift(1)
    d["doji"] = d.body <= d.range.replace(0, np.nan) * 0.10
    d["marubozu_bull"] = (d.close > d.open) & (d.body_ratio >= 0.85)
    d["marubozu_bear"] = (d.close < d.open) & (d.body_ratio >= 0.85)
    d["inverted_hammer"] = (d.upper_wick >= d.body * 2) & (d.lower_wick <= d.body * 0.8) & (d.close >= d.open)
    d["tweezer_bottom"] = prev_bear & (d.close > d.open) & ((d.low - d.low.shift(1)).abs() <= d.atr * 0.15)
    d["tweezer_top"] = prev_bull & (d.close < d.open) & ((d.high - d.high.shift(1)).abs() <= d.atr * 0.15)
    # Three-candle patterns.
    d["morning_star"] = (
        (d.close.shift(2) < d.open.shift(2)) &
        (d.body.shift(1) <= d.range.shift(1) * 0.35) &
        (d.close > d.open) &
        (d.close >= (d.open.shift(2) + d.close.shift(2)) / 2)
    )
    d["evening_star"] = (
        (d.close.shift(2) > d.open.shift(2)) &
        (d.body.shift(1) <= d.range.shift(1) * 0.35) &
        (d.close < d.open) &
        (d.close <= (d.open.shift(2) + d.close.shift(2)) / 2)
    )
    d["three_white_soldiers"] = (d.close > d.open) & (d.close.shift(1) > d.open.shift(1)) & (d.close.shift(2) > d.open.shift(2)) & (d.close > d.close.shift(1)) & (d.close.shift(1) > d.close.shift(2))
    d["three_black_crows"] = (d.close < d.open) & (d.close.shift(1) < d.open.shift(1)) & (d.close.shift(2) < d.open.shift(2)) & (d.close < d.close.shift(1)) & (d.close.shift(1) < d.close.shift(2))
    return d


CANDLE_WEIGHTS = {
    "bull_engulf": 12, "bear_engulf": -12, "hammer": 8, "shooting_star": -8,
    "inverted_hammer": 6, "tweezer_bottom": 6, "tweezer_top": -6,
    "morning_star": 12, "evening_star": -12, "three_white_soldiers": 10,
    "three_black_crows": -10, "marubozu_bull": 6, "marubozu_bear": -6,
}

def candle_knowledge(df, side):
    """Context-aware candle engine based on classical price-action patterns.
    It scores closed candles and estimates pattern quality from historical outcomes
    inside the same symbol/timeframe, rather than treating a pattern as a guarantee."""
    d=add_indicators(df).dropna().reset_index(drop=True)
    if len(d)<80: return {"score":50,"patterns":[],"historical":None,"confirmed":False}
    x=d.iloc[-1]; patterns=[]
    for name,w in CANDLE_WEIGHTS.items():
        if bool(x.get(name,False)): patterns.append((name,w))
    directional=sum(w for _,w in patterns)
    side_bias=directional if side=="LONG" else -directional
    context=0
    if side=="LONG":
        if x.ema21>x.ema50: context+=10
        if x.close>x.ema21: context+=6
        if x.volume/max(x.vol_ma,1e-12)>=1.2: context+=6
        if 45<=x.rsi<=68: context+=5
    else:
        if x.ema21<x.ema50: context+=10
        if x.close<x.ema21: context+=6
        if x.volume/max(x.vol_ma,1e-12)>=1.2: context+=6
        if 32<=x.rsi<=55: context+=5
    # Historical pattern hit rate: next 5 closed bars reaching 1 ATR in the
    # expected direction before -0.6 ATR. Ambiguous cases are discarded.
    best_rates=[]
    for name,_ in patterns:
        idxs=[i for i in range(20,len(d)-6) if bool(d.iloc[i].get(name,False))]
        wins=losses=0
        for i in idxs[-500:]:
            base=float(d.iloc[i].close); a=float(d.iloc[i].atr)
            if not np.isfinite(a) or a<=0: continue
            tp=base+(a if side=="LONG" else -a); sl=base-(0.6*a if side=="LONG" else -0.6*a)
            outcome=None
            for j in range(i+1,min(i+6,len(d))):
                hi=float(d.iloc[j].high); lo=float(d.iloc[j].low)
                hit_tp=hi>=tp if side=="LONG" else lo<=tp
                hit_sl=lo<=sl if side=="LONG" else hi>=sl
                if hit_tp and hit_sl: outcome=None; break
                if hit_tp: outcome=1; break
                if hit_sl: outcome=0; break
            if outcome==1: wins+=1
            elif outcome==0: losses+=1
        n=wins+losses
        if n>=10: best_rates.append((wins+1.5)/(n+3.0))
    hist=float(np.mean(best_rates)) if best_rates else None
    raw=50+max(-25,min(25,side_bias))+context
    if hist is not None: raw=0.65*raw+0.35*(hist*100)
    # Next-candle confirmation: closed candle direction must agree for strong signals.
    confirmed=(x.close>x.open) if side=="LONG" else (x.close<x.open)
    if patterns and not confirmed: raw-=8
    return {"score":int(max(0,min(100,raw))),"patterns":[n for n,_ in patterns],"historical":hist,"confirmed":confirmed}


def _setup(side, name, score, reasons, x):
    return {"side": side, "setup": name, "score": int(min(100, score)), "reasons": reasons,
            "entry": float(x.close), "atr": float(x.atr), "atr_pct": float(x.atr/x.close*100),
            "rsi": float(x.rsi), "volume_ratio": float(x.volume/x.vol_ma)}


def find_setups(df1, df5, df15):
    a = add_indicators(df1).dropna()
    b = add_indicators(df5).dropna()
    c = add_indicators(df15).dropna()
    if min(len(a), len(b), len(c)) < 150:
        return []
    x, p = a.iloc[-1], a.iloc[-2]
    y, z = b.iloc[-1], c.iloc[-1]
    if x.atr <= 0 or x.close <= 0 or x.vol_ma <= 0:
        return []
    atr_pct = x.atr/x.close*100
    if atr_pct > 3.5:
        return []

    setups = []
    long_trend = z.ema50 > z.ema200 and y.ema50 > y.ema200 and y.close > y.ema50
    short_trend = z.ema50 < z.ema200 and y.ema50 < y.ema200 and y.close < y.ema50
    long_momo = x.ema9 > x.ema21 > x.ema50 and x.macd > x.macd_signal
    short_momo = x.ema9 < x.ema21 < x.ema50 and x.macd < x.macd_signal
    vol = x.volume/x.vol_ma
    candle_bonus = 0
    candle_reasons = []
    if bool(x.bull_engulf): candle_bonus += 6; candle_reasons.append("bullish engulfing")
    if bool(x.hammer): candle_bonus += 4; candle_reasons.append("hammer")
    if bool(x.bear_engulf): candle_bonus -= 6; candle_reasons.append("bearish engulfing")
    if bool(x.shooting_star): candle_bonus -= 4; candle_reasons.append("shooting star")

    # 1) Trend continuation / momentum
    if long_trend and long_momo:
        score = 45
        reasons = ["15m+5m тренд вверх", "1m EMA9>21>50", "MACD вверх"]
        if 52 <= x.rsi <= 68: score += 12; reasons.append(f"RSI {x.rsi:.1f}")
        if vol >= 1.2: score += 12; reasons.append(f"объём {vol:.1f}x")
        if x.close > p.close: score += 8; reasons.append("импульс вверх")
        if x.close > x.ema21: score += 5
        if candle_bonus > 0: score += candle_bonus; reasons += candle_reasons
        setups.append(_setup("LONG", "TREND", score, reasons, x))
    if short_trend and short_momo:
        score = 45
        reasons = ["15m+5m тренд вниз", "1m EMA9<21<50", "MACD вниз"]
        if 32 <= x.rsi <= 48: score += 12; reasons.append(f"RSI {x.rsi:.1f}")
        if vol >= 1.2: score += 12; reasons.append(f"объём {vol:.1f}x")
        if x.close < p.close: score += 8; reasons.append("импульс вниз")
        if x.close < x.ema21: score += 5
        if candle_bonus < 0: score += abs(candle_bonus); reasons += candle_reasons
        setups.append(_setup("SHORT", "TREND", score, reasons, x))

    # 2) Breakout with volume
    if long_trend and x.close > x.hh20 and vol >= 1.35:
        score = 72 + min(10, int((vol-1.35)*8))
        reasons = ["пробой 20-свечного high", f"объём {vol:.1f}x", "старший тренд вверх"]
        if x.body_ratio >= 0.55: score += 8; reasons.append("сильная свеча")
        if bool(x.bull_engulf): score += 5; reasons.append("bullish engulfing")
        setups.append(_setup("LONG", "BREAKOUT", score, reasons, x))
    if short_trend and x.close < x.ll20 and vol >= 1.35:
        score = 72 + min(10, int((vol-1.35)*8))
        reasons = ["пробой 20-свечного low", f"объём {vol:.1f}x", "старший тренд вниз"]
        if x.body_ratio >= 0.55: score += 8; reasons.append("сильная свеча")
        if bool(x.bear_engulf): score += 5; reasons.append("bearish engulfing")
        setups.append(_setup("SHORT", "BREAKOUT", score, reasons, x))

    # 3) Pullback continuation
    near_ema = abs(x.close-x.ema21)/x.close*100 <= 0.35 or abs(x.close-x.ema50)/x.close*100 <= 0.6
    if long_trend and near_ema and x.close > p.close and x.macd >= p.macd:
        score = 68
        reasons = ["откат к EMA21/50", "старший тренд вверх", "возврат импульса"]
        if 48 <= x.rsi <= 62: score += 10; reasons.append(f"RSI {x.rsi:.1f}")
        if vol >= 1.1: score += 8; reasons.append(f"объём {vol:.1f}x")
        setups.append(_setup("LONG", "PULLBACK", score, reasons, x))
    if short_trend and near_ema and x.close < p.close and x.macd <= p.macd:
        score = 68
        reasons = ["откат к EMA21/50", "старший тренд вниз", "возврат импульса"]
        if 38 <= x.rsi <= 52: score += 10; reasons.append(f"RSI {x.rsi:.1f}")
        if vol >= 1.1: score += 8; reasons.append(f"объём {vol:.1f}x")
        setups.append(_setup("SHORT", "PULLBACK", score, reasons, x))

    # 4) Controlled reversal only with momentum confirmation
    if x.rsi <= 30 and x.macd > x.macd_signal and x.macd > p.macd and x.close > p.close:
        score = 70 + min(10, int((30-x.rsi)*1.5))
        setups.append(_setup("LONG", "REVERSAL", score, [f"RSI {x.rsi:.1f}", "MACD разворачивается вверх", "цена растёт"], x))
    if x.rsi >= 70 and x.macd < x.macd_signal and x.macd < p.macd and x.close < p.close:
        score = 70 + min(10, int((x.rsi-70)*1.5))
        setups.append(_setup("SHORT", "REVERSAL", score, [f"RSI {x.rsi:.1f}", "MACD разворачивается вниз", "цена падает"], x))

    for s in setups:
        A = s["atr"]
        if s["side"] == "LONG":
            s["sl"], s["tp"] = s["entry"]-1.4*A, s["entry"]+2.8*A
        else:
            s["sl"], s["tp"] = s["entry"]+1.4*A, s["entry"]-2.8*A
        s["rr"] = 2.0
    return sorted(setups, key=lambda q: q["score"], reverse=True)



def candle_prediction(df, sig, lookback=140, pattern=8):
    """Statistical analogue model without look-ahead."""
    d = add_indicators(df).dropna().reset_index(drop=True)
    if len(d) < 70:
        return {"ok": False, "reason": "мало истории"}
    last = d.iloc[-1]
    atr_now = float(last.atr)
    if not np.isfinite(atr_now) or atr_now <= 0:
        return {"ok": False, "reason": "ATR недоступен"}
    def vec(row):
        close=max(float(row.close),1e-12)
        return np.asarray([(float(row.close)-float(row.ema21))/close, (float(row.close)-float(row.ema50))/close, float(row.rsi)/100.0, float(row.macd-row.macd_signal)/close, float(row.volume/max(row.vol_ma,1e-12)), float(row.body_ratio if np.isfinite(row.body_ratio) else 0.0)], dtype=float)
    scales=np.array([0.006,0.01,0.15,0.004,1.0,0.5])
    candidates=[]
    start=max(pattern,len(d)-lookback); end=len(d)-11; target=len(d)-1
    for i in range(start,end):
        dist=0.0; valid=True
        for k in range(pattern):
            a=vec(d.iloc[i-k]); b=vec(d.iloc[target-k])
            if not (np.isfinite(a).all() and np.isfinite(b).all()): valid=False; break
            dist += float(np.mean(((a-b)/scales)**2))
        if valid: candidates.append((dist,i))
    candidates=sorted(candidates,key=lambda x:x[0])[:30]
    if len(candidates)<8: return {"ok":False,"reason":"недостаточно похожих ситуаций"}
    horizons=[1,3,5,10]; direction={}; tp_before_sl={}
    for h in horizons:
        up=down=wins=total=0
        for _,i in candidates:
            if i+h>=len(d): continue
            base=float(d.iloc[i].close); future=float(d.iloc[i+h].close); move=(future-base)/base
            if move>0: up+=1
            elif move<0: down+=1
            hist_atr=float(d.iloc[i].atr) if np.isfinite(d.iloc[i].atr) else atr_now
            if hist_atr<=0: continue
            tp_move=2.8*hist_atr/base; sl_move=1.4*hist_atr/base; outcome=None
            for j in range(i+1,min(i+h+1,len(d))):
                hi=float(d.iloc[j].high); lo=float(d.iloc[j].low)
                if sig["side"]=="LONG": hit_tp=hi>=base*(1+tp_move); hit_sl=lo<=base*(1-sl_move)
                else: hit_tp=lo<=base*(1-tp_move); hit_sl=hi>=base*(1+sl_move)
                if hit_tp and hit_sl: outcome="ambiguous"; break
                if hit_tp: outcome="win"; break
                if hit_sl: outcome="loss"; break
            if outcome=="win": wins+=1
            if outcome in ("win","loss"): total+=1
        denom=max(1,up+down)
        direction[h]={"up":up/denom,"down":down/denom}; tp_before_sl[h]=wins/max(1,total)
    probs=[direction[h]["up"] if sig["side"]=="LONG" else direction[h]["down"] for h in horizons]
    combined=[0.55*probs[i]+0.45*tp_before_sl[horizons[i]] for i in range(len(horizons))]
    best_i=int(np.argmax(combined)); best_h=horizons[best_i]; window=0
    for h,c in zip(horizons,combined):
        if c>=0.60 and tp_before_sl[h]>=0.55: window=h; break
    if window==0 and combined[best_i]>=0.58: window=best_h
    return {"ok":True,"samples":len(candidates),"best_horizon":best_h,"window_min":window,"direction_probs":{h:(direction[h]["up"] if sig["side"]=="LONG" else direction[h]["down"]) for h in horizons},"tp_before_sl":tp_before_sl,"best_prob":probs[best_i],"best_tp":tp_before_sl[best_h],"combined":combined[best_i]}



def adaptive_ml_prediction(df, sig, horizon=5):
    """Walk-forward logistic model trained only on candles before the current candle.
    Target: whether TP (2.8 ATR) is reached before SL (1.4 ATR) within horizon bars.
    This is a lightweight numpy model so no ML package is required.
    """
    if not CFG.adaptive_model_enabled:
        return {"ok": False, "reason": "adaptive model disabled"}
    d=add_indicators(df).dropna().reset_index(drop=True)
    if len(d) < max(120, CFG.adaptive_min_samples + horizon + 20):
        return {"ok": False, "reason": "мало данных для adaptive model"}
    feature_names=["ema21_dist","ema50_dist","rsi","macd_norm","vol_ratio","body_ratio","atr_pct","ema_slope","ret1","ret3"]
    def feat(row, prev=None, prev3=None):
        close=max(float(row.close),1e-12)
        ema21=float(row.ema21); ema50=float(row.ema50)
        atr=float(row.atr)
        ema_slope=((ema21-float(prev.ema21))/close) if prev is not None else 0.0
        ret1=((close-float(prev.close))/close) if prev is not None else 0.0
        ret3=((close-float(prev3.close))/close) if prev3 is not None else 0.0
        return np.array([
            (close-ema21)/close, (close-ema50)/close, float(row.rsi)/100.0,
            float(row.macd-row.macd_signal)/close, float(row.volume/max(row.vol_ma,1e-12)),
            float(row.body_ratio), atr/close, ema_slope, ret1, ret3], dtype=float)
    # Build walk-forward labels. No row uses candles after the current training point
    # except to determine that row's already-known historical outcome.
    X=[]; y=[]
    end=len(d)-horizon-1
    for i in range(5,end):
        base=float(d.iloc[i].close); atr=float(d.iloc[i].atr)
        if not np.isfinite(base) or base<=0 or not np.isfinite(atr) or atr<=0: continue
        target_tp=base*(1+2.8*atr/base) if sig["side"]=="LONG" else base*(1-2.8*atr/base)
        target_sl=base*(1-1.4*atr/base) if sig["side"]=="LONG" else base*(1+1.4*atr/base)
        label=None
        for j in range(i+1,min(i+horizon+1,len(d))):
            hi=float(d.iloc[j].high); lo=float(d.iloc[j].low)
            hit_tp=(hi>=target_tp) if sig["side"]=="LONG" else (lo<=target_tp)
            hit_sl=(lo<=target_sl) if sig["side"]=="LONG" else (hi>=target_sl)
            if hit_tp and hit_sl: label=None; break
            if hit_tp: label=1; break
            if hit_sl: label=0; break
        if label is None: continue
        f=feat(d.iloc[i],d.iloc[i-1],d.iloc[i-3])
        if np.isfinite(f).all(): X.append(f); y.append(label)
    if len(X)<CFG.adaptive_min_samples or len(set(y))<2:
        return {"ok":False,"reason":f"adaptive samples {len(X)}"}
    X=np.asarray(X,float); y=np.asarray(y,float)
    # Robust standardization using training data only.
    mu=np.mean(X,axis=0); sd=np.std(X,axis=0); sd=np.where(sd<1e-8,1.0,sd)
    Z=(X-mu)/sd
    Z=np.column_stack([np.ones(len(Z)),Z])
    w=np.zeros(Z.shape[1],dtype=float)
    # Deterministic gradient descent; small L2 regularization reduces overfit.
    lr=0.08; reg=0.02
    for _ in range(220):
        z=Z@w; z=np.clip(z,-20,20); p=1/(1+np.exp(-z))
        grad=(Z.T@(p-y))/len(y); grad[1:]+=reg*w[1:]
        w-=lr*grad
    current=feat(d.iloc[-1],d.iloc[-2],d.iloc[-4])
    if not np.isfinite(current).all(): return {"ok":False,"reason":"текущие признаки недоступны"}
    z=np.clip(np.array([1.0,*((current-mu)/sd)])@w,-20,20)
    prob=float(1/(1+np.exp(-z)))
    # Shrink extreme probabilities toward the observed training base rate.
    base_rate=float(np.mean(y)); prob=0.80*prob+0.20*base_rate
    historical=historical_model_prediction(str(sig.get("symbol","")).upper(), str(sig.get("side","")).upper(), current)
    if historical:
        hw=max(0.0,min(0.50,CFG.historical_model_weight))
        prob=(1.0-hw)*prob+hw*historical["prob"]
    return {"ok":True,"samples":len(y),"prob_tp_before_sl":prob,"base_rate":base_rate,"horizon":horizon,"features":feature_names,
            "historical_model":historical}


def enrich_with_prediction(df1, sig):
    pred=candle_prediction(df1,sig); sig=dict(sig); sig["prediction"]=pred
    adaptive=adaptive_ml_prediction(df1,sig)
    sig["adaptive_model"]=adaptive
    candle=candle_knowledge(df1,sig.get("side","LONG"))
    sig["candle_ai"]=candle
    # Candle engine is an additional vote. Strong disagreement caps the signal.
    cscore=int(candle.get("score",50))
    sig["score"]=int(max(0,min(100,sig["score"] + round((cscore-50)*0.22))))
    pats=candle.get("patterns") or []
    if pats:
        sig["reasons"]=list(sig.get("reasons",[]))+[f"свечной AI: {', '.join(pats)} ({cscore}/100)"]
    if candle.get("historical") is not None:
        sig["reasons"].append(f"статистика свечного паттерна: {candle['historical']*100:.0f}%")
    if pats and not candle.get("confirmed",False):
        sig["score"]=min(sig["score"],76)
        sig["reasons"].append("свеча не подтверждена направлением закрытия")
    # Combine the historical analogue estimate with the trained walk-forward model.
    if pred.get("ok"):
        boost=int(max(0,min(8,(pred["combined"]-0.5)*40)))
        sig["score"]=int(min(100,sig["score"]+boost))
        sig["reasons"]=list(sig.get("reasons",[]))+[f"исторические аналоги: {pred['samples']}",f"TP раньше SL ~{pred['best_tp']*100:.0f}% на {pred['best_horizon']}m"]
    trade_adj, trade_rate = adaptive_trade_adjustment(sig.get("side",""), sig.get("setup",""))
    if trade_adj:
        sig["score"]=int(max(0,min(100,sig["score"]+trade_adj)))
        sig["reasons"]=list(sig.get("reasons",[]))+[f"реальная статистика {sig['side']} {sig['setup']}: {trade_rate*100:.0f}% win rate"]
    sig["real_trade_win_rate"]=trade_rate
    if adaptive.get("ok"):
        old=float(pred.get("combined",0.5)) if pred.get("ok") else 0.5
        combined=(1.0-CFG.adaptive_weight)*old + CFG.adaptive_weight*float(adaptive["prob_tp_before_sl"])
        sig["prediction"]["combined"]=combined
        sig["prediction"]["adaptive_prob"]=adaptive["prob_tp_before_sl"]
        sig["prediction"]["adaptive_samples"]=adaptive["samples"]
        # Strong disagreement is a reason to avoid the trade, not to force a signal.
        if combined < 0.55:
            sig["score"]=min(sig["score"],74)
            sig["reasons"]=list(sig.get("reasons",[]))+[f"adaptive model: {adaptive['prob_tp_before_sl']*100:.0f}% — слабое подтверждение"]
        else:
            delta=int(max(-8,min(10,(combined-0.5)*35)))
            sig["score"]=int(max(0,min(100,sig["score"]+delta)))
            sig["reasons"]=list(sig.get("reasons",[]))+[f"adaptive model: {adaptive['prob_tp_before_sl']*100:.0f}% TP-before-SL ({adaptive['samples']} обучающих случаев)"]
    return sig


def position_size(sig, contract):
    risk = CFG.deposit*CFG.risk_pct/100
    dist = abs(sig["entry"]-sig["sl"])
    if dist <= 0: return 0.0, risk
    qty = min(risk/dist, CFG.deposit*CFG.leverage/sig["entry"])
    step = float(contract.get("sizeMultiplier") or 0.0001)
    minimum = float(contract.get("minTradeNum") or step)
    qty = np.floor(qty/step)*step
    return (float(qty), risk) if qty >= minimum else (0.0, risk)


def profit_numbers(sig, qty):
    notional = sig["entry"]*qty
    fee = CFG.taker_fee_pct/100
    if sig["side"] == "LONG":
        gross_profit = max(0, (sig["tp"]-sig["entry"])*qty)
        gross_loss = max(0, (sig["entry"]-sig["sl"])*qty)
    else:
        gross_profit = max(0, (sig["entry"]-sig["tp"])*qty)
        gross_loss = max(0, (sig["sl"]-sig["entry"])*qty)
    fees_tp = notional*fee + sig["tp"]*qty*fee
    fees_sl = notional*fee + sig["sl"]*qty*fee
    return {
        "notional": notional,
        "net_profit": max(0, gross_profit-fees_tp),
        "net_loss": gross_loss+fees_sl,
        "profit_pct": max(0, gross_profit-fees_tp)/CFG.deposit*100 if CFG.deposit else 0,
        "loss_pct": (gross_loss+fees_sl)/CFG.deposit*100 if CFG.deposit else 0,
    }


def message(symbol, sig, qty, risk):
    p=profit_numbers(sig,qty); icon="🟢" if sig["side"]=="LONG" else "🔴"; now=pd.Timestamp.now(tz="UTC"); entry=sig["entry"]
    entry_band=max(sig["atr"]*0.35,entry*0.0025); low_entry,high_entry=entry-entry_band,entry+entry_band
    pred=sig.get("prediction",{}); window=pred.get("window_min",0) if pred.get("ok") else 0
    if pred.get("ok"):
        probs=pred["direction_probs"]; tp=pred["tp_before_sl"]
        pred_block=(f"🧠 ПРОГНОЗ ПОХОЖИХ СВЕЧЕЙ\nОбразцов: {pred['samples']}\n"
            f"1m: {probs[1]*100:.0f}% | 3m: {probs[3]*100:.0f}% | 5m: {probs[5]*100:.0f}% | 10m: {probs[10]*100:.0f}%\n"
            f"🎯 TP раньше SL: {tp[pred['best_horizon']]*100:.0f}% ({pred['best_horizon']}m)\n"
            f"⏳ РАСЧЁТНОЕ ОКНО ВХОДА: {window} мин\n" if window else "⏳ РАСЧЁТНОЕ ОКНО ВХОДА: нет подтверждённого окна\n")
    else:
        pred_block=f"🧠 ПРОГНОЗ: недостаточно похожих исторических ситуаций ({pred.get('reason','неизвестно')})\n"
    return (f"{icon} СИГНАЛ {sig['side']} — {symbol}\n🧩 Сетап: {sig['setup']}\n⭐ Score: {sig['score']}/100\n"
        f"🕐 Время сигнала: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n{pred_block}\n"
        f"💰 Цена входа: {entry:.10g}\n📍 Зона входа: {low_entry:.10g} — {high_entry:.10g}\n🛑 SL: {sig['sl']:.10g}\n🎯 TP: {sig['tp']:.10g}\n📊 RR: 1:{sig['rr']:.1f}\n\n"
        f"💵 Депозит: ${CFG.deposit:.2f}\n⚙️ Плечо: {CFG.leverage}x (isolated)\n📦 Размер позиции: {qty:.10g}\n💼 Номинал: ${p['notional']:.2f}\n"
        f"🎯 При TP: +${p['net_profit']:.2f} (~{p['profit_pct']:.2f}% депозита)\n🛑 При SL: -${p['net_loss']:.2f} (~{p['loss_pct']:.2f}% депозита)\n\n"
        f"📊 RSI {sig['rsi']:.1f} | ATR {sig['atr_pct']:.2f}% | Vol {sig['volume_ratio']:.1f}x\n🔎 Почему: {', '.join(sig['reasons'])}\n\n"
        f"📋 ЧТО ДЕЛАТЬ\n1️⃣ Bitget → Futures → USDT-M → {symbol}.\n2️⃣ Isolated → {CFG.leverage}x.\n"
        f"3️⃣ Проверь цену: вход только в зоне {low_entry:.10g}–{high_entry:.10g}.\n4️⃣ Открой {sig['side']} рассчитанным размером.\n"
        f"5️⃣ СРАЗУ поставь SL {sig['sl']:.10g}.\n6️⃣ СРАЗУ поставь TP {sig['tp']:.10g}.\n7️⃣ Проверь сторону, размер, SL и TP.\n"
        f"8️⃣ Не усредняй убыточную позицию и не увеличивай риск.\n9️⃣ Если окно истекло или условия изменились — НЕ ВХОДИ, жди новый сигнал.\n\n"
        f"📚 Исторические аналоги: {pred.get('samples',0)}\n"
        f"📈 Вероятность по аналогам: {pred.get('best_prob',0)*100:.0f}% | TP раньше SL: {pred.get('best_tp',0)*100:.0f}%\n"
        f"⏳ Окно рассчитано статистической моделью: {window} мин. Это не гарантия прибыли.\n"
        f"⚠️ Вероятности — историческая статистика похожих ситуаций, а не точное предсказание будущей свечи.\nРежим: {'LIVE' if CFG.live else 'SIGNAL ONLY'}")


def ask_deposit():
    default = CFG.deposit
    while True:
        raw = input(f"\nВведите депозит в USDT [$ {default:.2f}] или Enter для {default:.2f}: ").strip().replace(",", ".")
        if not raw: return default
        try:
            value = float(raw)
            if value > 0: return value
        except ValueError: pass
        print("Введите положительное число, например 10 или 250.50")


def ask_threshold():
    default = CFG.min_score
    while True:
        raw = input(f"Введите порог сигнала 50-100 [по умолчанию {default}]: ").strip()
        if not raw: return default
        try:
            value = int(raw)
            if 50 <= value <= 100: return value
        except ValueError: pass
        print("Введите целое число от 50 до 100. Например: 75, 78 или 85.")


def vis_len(text):
    clean = ANSI_RE.sub("", str(text))
    width = 0
    for ch in clean:
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in "WFA" else 1
    return width


def fit(text, width, align="left"):
    s = str(text)
    while vis_len(s) > width:
        s = s[:-1]
    pad = max(0, width-vis_len(s))
    if align == "right": return " "*pad+s
    if align == "center":
        l = pad//2
        return " "*l+s+" "*(pad-l)
    return s+" "*pad


def paint(text, color=Fore.WHITE, bright=False):
    return (Style.BRIGHT if bright else "") + color + str(text) + Style.RESET_ALL


def progress(score, width=8):
    # ASCII-only progress bar: avoids Unicode terminal width differences in Windows CMD.
    n = int(width * max(0, min(100, score)) / 100)
    color = Fore.GREEN if score >= CFG.min_score else (Fore.YELLOW if score >= 60 else Fore.RED)
    return color + "#" * n + Fore.LIGHTBLACK_EX + "-" * (width - n) + Style.RESET_ALL

def format_price(value):
    """Human-readable futures price without scientific notation."""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return "-"
    if x >= 1000:
        return f"{x:,.2f}".replace(",", " ")
    if x >= 1:
        return f"{x:.4f}".rstrip("0").rstrip(".")
    if x >= 0.01:
        return f"{x:.6f}".rstrip("0").rstrip(".")
    if x >= 0.0001:
        return f"{x:.8f}".rstrip("0").rstrip(".")
    # PEPE and similar micro-priced contracts: keep enough decimals, never 3.65e-06.
    return f"{x:.12f}".rstrip("0").rstrip(".")


def status_badge(status, width=12):
    label={"ГОТОВ":"READY","LIVE":"LIVE","API ERROR":"API ERROR"}.get(status,"WAIT")
    label=fit(label,width,"center")
    if status in ("ГОТОВ","LIVE"): return Back.GREEN+Fore.BLACK+Style.BRIGHT+label+Style.RESET_ALL
    if status == "API ERROR": return Back.RED+Fore.WHITE+Style.BRIGHT+label+Style.RESET_ALL
    return Back.YELLOW+Fore.BLACK+Style.BRIGHT+label+Style.RESET_ALL


def print_market_table(rows, top_setups):
    # Premium Windows CMD UI: no box-drawing characters so it renders reliably.
    C = Fore.CYAN
    CB = Fore.CYAN + Style.BRIGHT
    G = Fore.GREEN + Style.BRIGHT
    R = Fore.RED + Style.BRIGHT
    Y = Fore.YELLOW + Style.BRIGHT
    W = Fore.WHITE + Style.BRIGHT
    D = Fore.LIGHTBLACK_EX

    os.system("cls")
    now = pd.Timestamp.now(tz="UTC").strftime("%H:%M:%S UTC")

    width = 118
    print(CB + "╔" + "═"*width + "╗")
    print(CB + "║" + fit(" BITGET V15 PRO  •  PREMIUM MARKET TERMINAL ", width, "center") + "║")
    print(C + "║" + fit(f" LIVE MARKET  |  Updated {now}  |  20 FUTURES  |  Paper Trading ", width) + "║")
    print(C + "╠" + "═"*width + "╣")

    # KPI strip
    ready=sum(1 for r in rows if r.get("status") in ("ГОТОВ","READY"))
    longs=sum(1 for r in rows if r.get("side")=="LONG")
    shorts=sum(1 for r in rows if r.get("side")=="SHORT")
    print(C + "║" + fit(
        f"  SIGNALS  {len(top_setups):02d}    │    READY  {ready:02d}    │    "
        f"LONG  {longs:02d}    │    SHORT  {shorts:02d}    │    "
        f"DEPOSIT  ${CFG.deposit:.2f}    │    LEVERAGE  {CFG.leverage}x",
        width) + "║")
    print(C + "╠" + "═"*width + "╣")

    cols=[("SYMBOL",13),("PRICE",14),("SCORE",7),("SIDE",8),("RSI",7),
          ("VOL",8),("SETUP",24),("DATA",8),("STATUS",12)]
    header="  "+"  ".join(fit(a,b,"center") for a,b in cols)
    print(CB+"║"+fit(header,width)+"║")
    print(C+"╠"+"─"*width+"╣")

    for r in rows:
        score=int(r.get("score",0))
        side=r.get("side","-")
        symbol=fit(r.get("symbol","-"),13)
        price=fit(r.get("price","-"),14,"right")
        score_s=fit(str(score),7,"right")
        rsi=fit(f"{float(r.get('rsi',0)):.1f}",7,"right")
        vol=fit(f"{float(r.get('vol',0)):.1f}x",8,"right")
        setup=fit(r.get("setup","-"),24)
        data=r.get("data","-")
        status=r.get("status","-")

        # Base row remains turquoise.
        line=C+"║  "+symbol+"  "+price+"  "
        # Score accent
        score_col=G if score>=CFG.min_score else (Y if score>=60 else R)
        line+=score_col+score_s+Style.RESET_ALL+C+"  "
        # Direction accent
        side_col=G if side=="LONG" else (R if side=="SHORT" else C)
        line+=side_col+fit(side,8,"center")+Style.RESET_ALL+C+"  "
        # RSI accent only at extremes
        rv=float(r.get("rsi",0) or 0)
        rsi_col=R if rv>=70 or (0<rv<=30) else C
        line+=rsi_col+rsi+Style.RESET_ALL+C+"  "
        # Volume accent
        vv=float(r.get("vol",0) or 0)
        vol_col=G if vv>=1.2 else (Y if vv>=0.9 else C)
        line+=vol_col+vol+Style.RESET_ALL+C+"  "
        line+=CB+setup+Style.RESET_ALL+C+"  "
        # Data / status
        data_col=G if data=="LIVE" else (Y if data=="STALE" else R)
        stat_col=G if status in ("ГОТОВ","READY","LIVE") else (
            R if status=="API ERROR" else Y)
        line+=data_col+fit(data,8,"center")+Style.RESET_ALL+C+"  "
        line+=stat_col+fit(status,12,"center")+Style.RESET_ALL+C
        # Pad visual line with plain spaces to preserve frame width.
        print(line+" "*(width-len(re.sub(r"\x1b\\[[0-9;]*m","",line))+1)+"║")

    print(C+"╠"+"═"*width+"╣")
    print(CB+"║"+fit(
        " TOP SETUPS  •  strongest current opportunities (signal ≠ guaranteed profit) ",
        width)+"║")

    if top_setups:
        for i,x in enumerate(top_setups[:5],1):
            side=x.get("side","-")
            col=G if side=="LONG" else (R if side=="SHORT" else C)
            txt=(f"  {i}. {x.get('symbol','-'):12}  {side:5}  "
                  f"Score {int(x.get('score',0)):3}/100  •  "
                  f"{x.get('setup','-')}  •  Entry {format_price(x.get('entry',0))}")
            print(C+"║"+col+fit(txt,width)+Style.RESET_ALL+"║")
    else:
        print(C+"║"+Y+fit("  No high-confidence setups right now. Waiting for the market...",width)+Style.RESET_ALL+"║")

    print(C+"╠"+"─"*width+"╣")
    print(C+"║"+fit(
        f" PAPER MODE  •  Virtual balance ${CFG.deposit:.2f}  •  Real orders: OFF  •  "
        f"Updated every scan cycle",
        width)+"║")
    print(C+"╚"+"═"*width+"╝")


async def status_heartbeat():
    while True:
        await asyncio.sleep(600)
        await notify(
            f"🟢 Бот работает\nМонеты: {len(CFG.symbols)}\nМониторинг: 1m / 5m / 15m\n"
            f"Порог: {CFG.min_score}/100\nДепозит: ${CFG.deposit:.2f}\nРежим: {'LIVE' if CFG.live else 'SIGNAL ONLY'}",
            "default"
        )


def watch_metrics(df1, df5, df15):
    """Continuous watch score for the table. It is NOT a trade signal by itself."""
    a = add_indicators(df1).dropna()
    b = add_indicators(df5).dropna()
    c = add_indicators(df15).dropna()
    if min(len(a), len(b), len(c)) < 30:
        return {"score": 0, "side": "-", "rsi": 0.0, "vol": 0.0, "setup": "-"}
    x, p = a.iloc[-1], a.iloc[-2]
    y, z = b.iloc[-1], c.iloc[-1]
    close = float(x.close)
    vol = float(x.volume / max(float(x.vol_ma), 1e-12))
    long_points = 0
    short_points = 0
    # Higher-timeframe trend: strongest component.
    if z.ema50 > z.ema200: long_points += 15
    if z.ema50 < z.ema200: short_points += 15
    if y.ema50 > y.ema200 and y.close > y.ema50: long_points += 15
    if y.ema50 < y.ema200 and y.close < y.ema50: short_points += 15
    # 1m momentum / price location.
    if x.ema9 > x.ema21 > x.ema50: long_points += 15
    if x.ema9 < x.ema21 < x.ema50: short_points += 15
    if x.macd > x.macd_signal: long_points += 10
    if x.macd < x.macd_signal: short_points += 10
    if x.close > x.ema21: long_points += 5
    if x.close < x.ema21: short_points += 5
    # RSI contribution is directional, but neutral RSI is not punished.
    if 52 <= x.rsi <= 68: long_points += 12
    elif 32 <= x.rsi <= 48: short_points += 12
    # Real volume.
    if vol >= 1.5:
        long_points += 8; short_points += 8
    elif vol >= 1.0:
        long_points += 4; short_points += 4
    # Current impulse.
    if x.close > p.close: long_points += 5
    elif x.close < p.close: short_points += 5
    side = "LONG" if long_points >= short_points else "SHORT"
    score = int(max(long_points, short_points))
    # A simple label for the watch screen, not a signal.
    if side == "LONG" and x.close > x.hh20 and vol >= 1.2: setup = "BREAKOUT?"
    elif side == "SHORT" and x.close < x.ll20 and vol >= 1.2: setup = "BREAKOUT?"
    elif abs(close-float(x.ema21))/close*100 <= 0.35: setup = "PULLBACK?"
    else: setup = "WATCH"
    return {"score": min(100, score), "side": side, "rsi": float(x.rsi), "vol": vol, "setup": setup}


async def analyze_symbol(bg, session, symbol, cache):
    now = time.monotonic()
    async def get_tf(tf, ttl):
        key=(symbol,tf)
        item=cache.get(key)
        if item and now-item["at"] < ttl:
            return item["df"]
        df=await bg.candles(session,symbol,tf,200)
        cache[key]={"at":now,"df":df}
        return df
    df1=await get_tf("1m", 12)
    df5=await get_tf("5m", 28)
    df15=await get_tf("15m", 58)
    if min(len(df1),len(df5),len(df15)) < 150:
        raise RuntimeError(f"Недостаточно свечей {len(df1)}/{len(df5)}/{len(df15)}")
    d1=df1.iloc[:-1].copy() if len(df1)>1 else df1
    d5=df5.iloc[:-1].copy() if len(df5)>1 else df5
    d15=df15.iloc[:-1].copy() if len(df15)>1 else df15
    setups=find_setups(d1,d5,d15)
    metrics=watch_metrics(d1,d5,d15)
    last_ts=d1.iloc[-1]["ts"]
    age=max(0,(pd.Timestamp.now(tz="UTC")-last_ts).total_seconds())
    best=setups[0] if setups else None
    if best:
        best["symbol"]=symbol
        best=enrich_with_prediction(d1, best)
        pred=best.get("prediction", {})
        if not pred.get("ok") or pred.get("combined", 0) < 0.55:
            best["score"]=min(best["score"], 77)
    return {"symbol":symbol,"price":float(d1.iloc[-1]["close"]),"age":age,"rows":f"{len(df1)}/{len(df5)}/{len(df15)}","setups":setups,"best":best,"metrics":metrics,"candle_ts":last_ts}


async def ensure_real_credentials():
    if not (CFG.live and CFG.auto_trade):
        return
    # In AUTO REAL mode we deliberately allow visible input so the user can
    # verify what was entered. Nothing is written to disk by this function.
    if CFG.prompt_api_keys:
        print("\n=== BITGET REAL API CREDENTIALS ===")
        CFG.api_key = input("Bitget API Key: ").strip()
        CFG.api_secret = getpass("Bitget Secret Key (hidden): ").strip()
        CFG.passphrase = getpass("Bitget Passphrase (hidden): ").strip()
    else:
        if not CFG.api_key: CFG.api_key = input("Bitget API Key: ").strip()
        if not CFG.api_secret: CFG.api_secret = input("Bitget Secret Key: ").strip()
        if not CFG.passphrase: CFG.passphrase = input("Bitget Passphrase: ").strip()
    if not all([CFG.api_key, CFG.api_secret, CFG.passphrase]):
        raise RuntimeError("REAL AUTO mode requires Bitget API Key, Secret Key and Passphrase")
    if CFG.api_key.startswith("-----BEGIN") or "PRIVATE KEY" in CFG.api_secret.upper():
        raise RuntimeError("Похоже, введён RSA private key. Эта сборка ожидает HMAC Secret Key. Создай HMAC API key в Bitget или переключи signing type на RSA.")

async def main():
    CFG.deposit=ask_deposit()
    CFG.min_score=ask_threshold()
    logging.info("STRICT V15 PRO: %d coins | $%.2f | %sx | score>=%s | live=%s",len(CFG.symbols),CFG.deposit,CFG.leverage,CFG.min_score,CFG.live)
    print("\n"+paint("BITGET STRICT V15 PRO — REAL DATA",Fore.GREEN,True))
    print(paint("20 futures • 1m/5m/15m • candle prediction • dynamic entry window • top 3 setups • manual deposit",Fore.CYAN))
    print(paint(f"Deposit ${CFG.deposit:.2f} | Leverage {CFG.leverage}x | Threshold {CFG.min_score}/100 | Scan {CFG.scan_interval_sec}s",Fore.MAGENTA,True))
    mode_text = "AUTO REAL" if (CFG.live and CFG.auto_trade) else ("LIVE SIGNALS" if CFG.live else "SIGNAL ONLY")
    print(paint(f"MODE: {mode_text} | AUTO_TRADING={CFG.auto_trade}", Fore.YELLOW, True))
    await notify(
        f"🤖 STRICT V15 PRO запущен\nМонеты: {len(CFG.symbols)}\nДепозит: ${CFG.deposit:.2f}\nПлечо: {CFG.leverage}x\n"
        f"Порог: {CFG.min_score}/100\nСетапы: TREND / BREAKOUT / PULLBACK / REVERSAL + candle prediction\nРежим: {'AUTO REAL' if (CFG.live and CFG.auto_trade) else ('LIVE SIGNALS' if CFG.live else 'SIGNAL ONLY')}",
        "default"
    )
    asyncio.create_task(status_heartbeat())

    bg=Bitget(CFG)
    cache={}
    last_candles={s:None for s in CFG.symbols}
    last_signal={s:0 for s in CFG.symbols}
    active_signals={}  # symbol -> live signal with dynamic expiry
    auto_ordered=set()
    managed_positions={}

    timeout=aiohttp.ClientTimeout(total=15,connect=5,sock_read=10)
    async with aiohttp.ClientSession(timeout=timeout,trust_env=True) as session:
        # REAL AUTO mode: request credentials interactively and verify the private API
        # before the market table starts. Credentials are kept only in memory.
        if CFG.live and CFG.auto_trade:
            await ensure_real_credentials()
            try:
                check = await bg.account(session, CFG.symbols[0])
                if check is None:
                    raise RuntimeError("Bitget account check returned empty data")
                logging.info("Bitget private API: OK | REAL AUTO trading is ready")
            except Exception as e:
                raise RuntimeError(f"Bitget private API check failed: {e}")
        results=[]
        last_analysis=0.0
        last_tickers=0.0
        tickers={}
        while True:
            now_mono=time.monotonic()
            # Technical analysis runs every scan interval.
            if not results or now_mono-last_analysis >= CFG.scan_interval_sec:
                async def one(symbol):
                    try:
                        return await analyze_symbol(bg,session,symbol,cache)
                    except Exception as e:
                        return {"symbol":symbol,"error":f"{type(e).__name__}: {str(e)}"}
                results=await asyncio.gather(*(one(s) for s in CFG.symbols))
                last_analysis=now_mono

            # One Bitget REST call updates current prices for all futures every second.
            try:
                if now_mono-last_tickers >= 1.0:
                    tickers=await bg.tickers(session)
                    last_tickers=now_mono
            except Exception as e:
                logging.warning("Ticker refresh: %s", e)

            table=[]; top=[]
            for r in results:
                sym=r["symbol"]
                live=tickers.get(sym,{})
                if r.get("error"):
                    table.append({"symbol":sym,"price":format_price(live.get("price")) if live.get("price") else "-","score":0,"side":"-","rsi":0,"vol":0,"setup":"-","window":"-","age":"-","data":"LIVE" if live else "-","status":"API ERROR"})
                    continue
                b=r.get("best")
                metrics=r.get("metrics",{})
                price=float(live.get("price",r["price"]))
                live_b=dict(b) if b else None
                if live_b:
                    # Live score reacts to the real ticker price each second; it does not invent a candle.
                    move=(price-live_b["entry"])/live_b["entry"]*100 if live_b["entry"] else 0
                    signed=move if live_b["side"]=="LONG" else -move
                    live_b["score"]=int(max(0,min(100,live_b["score"]+max(-5,min(5,signed*8)))))
                    status="ГОТОВ" if live_b["score"]>=CFG.min_score else "WAIT"
                    pred=live_b.get("prediction",{})
                    st=active_signals.get(sym)
                    remaining=max(0,int((st["expires"]-time.time())/60)) if st else 0
                    window=f"{remaining}m" if remaining else "-"
                    table.append({"symbol":sym,"price":format_price(price),"score":live_b["score"],"side":live_b["side"],"rsi":live_b["rsi"],"vol":live_b["volume_ratio"],"setup":live_b["setup"],"window":window,"age":f"{r['age']:.0f}s","data":"LIVE" if live else "STALE","status":status})
                    if live_b["score"]>=CFG.min_score: top.append(live_b)
                else:
                    # Show the real indicator state even when there is no complete setup.
                    # This prevents a table full of artificial zeros while the API is live.
                    wside=metrics.get("side","-")
                    wscore=int(metrics.get("score",0))
                    base_price=float(r["price"])
                    move=(price-base_price)/base_price*100 if base_price else 0
                    signed=move if wside=="LONG" else (-move if wside=="SHORT" else 0)
                    wscore=int(max(0,min(100,wscore+max(-5,min(5,signed*8)))))
                    table.append({"symbol":sym,"price":format_price(price),"score":wscore,"side":wside,"rsi":metrics.get("rsi",0),"vol":metrics.get("vol",0),"setup":metrics.get("setup","WATCH"),"window":"-","age":f"{r['age']:.0f}s","data":"LIVE" if live else "STALE","status":"WAIT"})

                # NEW closed 1m candle creates/refreshes a dynamic signal window.
                candle_ts=r.get("candle_ts")
                if candle_ts is not None and (last_candles[sym] is None or candle_ts>last_candles[sym]):
                    last_candles[sym]=candle_ts
                    if b and b["score"]>=CFG.min_score and b.get("prediction",{}).get("window_min",0)>0:
                        try:
                            contract=await bg.contract(session,sym)
                        except Exception as ce:
                            logging.warning("%s | parameters unavailable: %s", sym, ce)
                            contract={"sizeMultiplier":"0.0001","minTradeNum":"0.0001","pricePlace":"4"}
                        qty,risk=position_size(b,contract)
                        pred=b.get("prediction",{})
                        window=max(1,int(pred.get("window_min") or CFG.signal_window_default_min))
                        active_signals[sym]={
                            "signal":dict(b), "qty":qty, "risk":risk,
                            "created":time.time(), "expires":time.time()+window*60,
                            "contract":contract, "last_price":price,
                        }
                        if qty>0 and time.time()-last_signal[sym]>=CFG.signal_cooldown_sec:
                            msg=message(sym,b,qty,risk)
                            await notify(msg,"high")
                            p=profit_numbers(b,qty)
                            csv_log({"time":pd.Timestamp.utcnow().isoformat(),"symbol":sym,"side":b["side"],"setup":b["setup"],"score":b["score"],"entry":b["entry"],"sl":b["sl"],"tp":b["tp"],"qty":qty,"net_profit_tp":p["net_profit"],"net_loss_sl":p["net_loss"],"action":"SIGNAL"})
                            last_signal[sym]=time.time()
                        elif qty<=0:
                            await notify(f"⚠️ {sym}: сигнал {b['side']} {b['score']}/100, но размер позиции ниже минимума для депозита ${CFG.deposit:.2f}.","high")

                # Real position manager: the exchange is the source of truth.
                # TP orders are now placed on Bitget itself. Python only monitors
                # position-size changes and reports confirmed exchange execution;
                # it never sends a second local TP for the same price.
                mp=managed_positions.get(sym)
                if mp and sym in auto_ordered:
                    try:
                        positions_now=await bg.positions(session)
                        wanted_hold = "long" if mp["side"] == "LONG" else "short"
                        pos=next((p for p in (positions_now or [])
                                   if str(p.get("symbol","" )).upper()==sym
                                   and str(p.get("holdSide","" )).lower() in (wanted_hold, "net")
                                   and float(p.get("total") or 0)>0), None)
                        if not pos:
                            pnl=await bg.recent_realized_pnl(session, sym, mp.get("opened_at_ms", int(time.time()*1000)-600000))
                            await notify(
                                f"🔴 POSITION CLOSED — {sym} {mp['side']}\n"
                                f"Bitget confirms position size = 0.\n"
                                f"Причина: exchange-side SL/TP или ручное закрытие.\n"
                                f"Реализованный PnL: ${pnl['profit']:.6f}\n"
                                f"Комиссии: ${pnl['fee']:.6f}\n"
                                f"NET: ${pnl['net']:.6f}",
                                "default")
                            csv_log({"time":pd.Timestamp.utcnow().isoformat(),"symbol":sym,"side":mp["side"],"action":"POSITION_CLOSED","realized_pnl":pnl["profit"],"close_fees":pnl["fee"],"net":pnl["net"]})
                            managed_positions.pop(sym,None); auto_ordered.discard(sym); active_signals.pop(sym,None)
                        else:
                            side=mp["side"]
                            before=float(mp.get("remaining") or 0)
                            remaining=float(pos.get("total") or 0)
                            mp["remaining"]=remaining
                            mp["exchange_entry"]=float(pos.get("openPriceAvg") or pos.get("averageOpenPrice") or mp["entry"] or 0)
                            upnl=float(pos.get("unrealizedPL") or pos.get("unrealizedPnl") or 0)
                            mp["last_upnl"]=upnl

                            # If Bitget reduced the position, one or more exchange-side
                            # TP plans actually fired. Mark levels only after the
                            # position size changed on the exchange.
                            if before > 0 and remaining < before - max(float(mp.get("size_multiplier") or 0), 1e-12):
                                closed_delta=before-remaining
                                unhit=[lvl for lvl in mp["levels"] if not lvl["hit"]]
                                # Match the reduction to the earliest unconfirmed TP.
                                for level in unhit:
                                    expected=float(mp["qty"])*float(level["pct"])/100.0
                                    if level["final"] or closed_delta >= expected*0.80:
                                        level["hit"]=True
                                        closed_delta=max(0.0, closed_delta-expected)
                                        pnl=await bg.recent_realized_pnl(session,sym,mp.get("opened_at_ms",int(time.time()*1000)-600000))
                                        await notify(
                                            f"🎯 TP{level['n']} CONFIRMED BY BITGET — {sym} {side}\n"
                                            f"Target: {level['price']:.10g}\n"
                                            f"Closed on exchange: ~{min(expected,before):.10g}\n"
                                            f"Remaining: {remaining:.10g}\n"
                                            f"Realized PnL: ${pnl['profit']:.6f} | fees: ${pnl['fee']:.6f} | NET: ${pnl['net']:.6f}\n"
                                            f"TP source: Bitget exchange-side plan order",
                                            "high")
                                        csv_log({"time":pd.Timestamp.utcnow().isoformat(),"symbol":sym,"side":side,"action":f"TP{level['n']}_EXCHANGE_CONFIRMED","closed_qty":before-remaining,"realized_pnl":pnl["profit"],"fees":pnl["fee"],"net":pnl["net"]})
                                        if level["final"] or remaining <= 0:
                                            break
                                if remaining <= 0:
                                    managed_positions.pop(sym,None); auto_ordered.discard(sym); active_signals.pop(sym,None)
                    except Exception as me:
                        logging.error("POSITION MONITOR %s: %s",sym,me)
                        await notify(f"⚠️ POSITION MONITOR — {sym}\nBitget position was NOT assumed closed.\nError: {me}","high")

                # Dynamic signal window applies ONLY before an order is opened.
                state=active_signals.get(sym)
                if sym in auto_ordered:
                    continue
                if state:
                    sig=state["signal"]
                    pred=sig.get("prediction",{})
                    current_price=price
                    elapsed=time.time()-state["created"]
                    expired=time.time()>=state["expires"]
                    band=max(sig["atr"]*.35, sig["entry"]*.0025)
                    in_entry_zone=abs(current_price-sig["entry"])<=band
                    # Live score is recalculated from the current ticker, so stale/invalid setups are cancelled.
                    move=(current_price-sig["entry"])/sig["entry"]*100 if sig["entry"] else 0
                    signed=move if sig["side"]=="LONG" else -move
                    current_score=int(max(0,min(100,sig["score"]+max(-8,min(8,signed*8)))))
                    state["last_price"]=current_price
                    state["current_score"]=current_score
                    state["in_zone"]=in_entry_zone
                    if expired or current_score<CFG.min_score-8 or not pred.get("ok"):
                        reason="окно истекло" if expired else ("Score упал" if current_score<CFG.min_score-8 else "историческое подтверждение пропало")
                        if time.time()-last_signal.get(sym,0)>1:
                            await notify(f"❌ SIGNAL CANCELLED — {sym}\n{sig['side']} {sig['setup']}\nПричина: {reason}\nScore: {current_score}/100\nНе входить. Ждать новый сигнал.","default")
                        active_signals.pop(sym,None)
                        auto_ordered.discard(sym)
                    elif CFG.live and CFG.auto_trade and state["qty"]>0 and sym not in auto_ordered and in_entry_zone and current_score>=CFG.min_score:
                        try:
                            positions=await bg.positions(session)
                            active=[str(p.get("symbol","")).upper() for p in (positions or []) if float(p.get("total") or 0)>0]
                            if len(active)<CFG.max_auto_positions and sym not in active:
                                account=await bg.account(session,sym)
                                account=account or {}
                                # Bitget exposes both the wallet's currently available balance
                                # and the maximum amount that can actually be used to open a
                                # position for the selected margin mode.  The old V15 check
                                # rejected valid trades by requiring margin <= available * 0.90
                                # (an arbitrary 10% haircut).  Use the exchange's max-available
                                # field first and reserve only the real opening taker fee plus a
                                # tiny configurable safety buffer.
                                raw_max = (account.get("isolatedMaxAvailable") if CFG.margin_mode == "isolated"
                                           else account.get("crossedMaxAvailable"))
                                max_available = float(raw_max or 0)
                                available = float(account.get("available") or 0)
                                if max_available <= 0:
                                    max_available = available
                                margin_needed=sig["entry"]*state["qty"]/max(CFG.leverage,1)
                                opening_notional=sig["entry"]*state["qty"]
                                opening_fee=opening_notional*(CFG.taker_fee_pct/100)
                                # Only a small fixed cushion remains; do not reserve 10% of
                                # the user's margin because that can reject otherwise valid orders.
                                margin_buffer=max(0.02, margin_needed*0.001)
                                total_required=margin_needed+opening_fee+margin_buffer
                                if max_available > 0 and total_required <= max_available:
                                    result=await bg.place_market_order(session,sym,sig["side"],state["qty"],sig["sl"],sig["tp"],state["contract"].get("pricePlace",4))
                                    oid=result.get("orderId","?") if isinstance(result,dict) else str(result)
                                    if not oid or oid == "?":
                                        raise RuntimeError(f"Bitget did not return a valid orderId: {result}")
                                    # Do not start the local position manager until Bitget
                                    # confirms that the real order is filled.
                                    od=None
                                    for _ in range(8):
                                        await asyncio.sleep(0.4)
                                        od=await bg.order_detail(session,sym,oid)
                                        state_name=str((od or {}).get("state","")).lower()
                                        if state_name in ("filled","partially_filled","canceled"):
                                            break
                                    state_name=str((od or {}).get("state","")).lower()
                                    if state_name not in ("filled","partially_filled"):
                                        raise RuntimeError(f"Bitget order {oid} was not filled: state={state_name or 'unknown'}")
                                    auto_ordered.add(sym)
                                    ep=float((od or {}).get("priceAvg") or current_price)
                                    actual_qty=float((od or {}).get("baseVolume") or state["qty"])
                                    mult=float(state["contract"].get("sizeMultiplier") or 1.0)
                                    levels=[]
                                    close_plan=[(1,CFG.tp1_pct,CFG.tp1_close_pct),(2,CFG.tp2_pct,CFG.tp2_close_pct),(3,CFG.tp3_pct,CFG.tp3_close_pct)]
                                    for n,pct,close_pct in close_plan:
                                        tp_price=ep*(1+pct/100) if sig["side"]=="LONG" else ep*(1-pct/100)
                                        levels.append({"n":n,"price":tp_price,"pct":close_pct,"final":n==3,"hit":False,"exchange_order_id":None,"exchange_client_oid":None})

                                    # IMPORTANT: put all three partial TP orders on Bitget
                                    # immediately after the real fill. The bot refuses to
                                    # treat the position as fully managed until Bitget has
                                    # accepted every TP plan. This prevents a local-only TP
                                    # from disappearing when the PC/internet/Python stops.
                                    rounded=[]
                                    accepted_tp_ids=[]
                                    try:
                                        for i,level in enumerate(levels):
                                            if level["final"]:
                                                tp_size=max(0.0, actual_qty-sum(rounded))
                                            else:
                                                tp_size=actual_qty*level["pct"]/100.0
                                                if mult>0:
                                                    tp_size=(int(tp_size/mult)*mult)
                                            if tp_size <= 0:
                                                raise RuntimeError(f"TP{level['n']} size became zero after contract rounding")
                                            rounded.append(tp_size)
                                            last_tp_error=None
                                            tp_result=None
                                            for tp_attempt in range(1,3):
                                                try:
                                                    tp_result=await bg.place_partial_tp(session,sym,sig["side"],tp_size,level["price"],state["contract"].get("pricePlace",4),level["n"])
                                                    tp_oid=(tp_result or {}).get("orderId") if isinstance(tp_result,dict) else None
                                                    if tp_oid:
                                                        last_tp_error=None
                                                        break
                                                    last_tp_error=f"empty orderId: {tp_result}"
                                                except Exception as te:
                                                    last_tp_error=str(te)
                                                    if tp_attempt<2:
                                                        await asyncio.sleep(0.5)
                                            if last_tp_error is not None:
                                                raise RuntimeError(f"Bitget TP{level['n']} was not accepted after retry: {last_tp_error}")
                                            level["exchange_order_id"]=tp_oid
                                            level["exchange_client_oid"]=(tp_result or {}).get("clientOid") if isinstance(tp_result,dict) else None
                                            level["exchange_size"]=tp_size
                                            accepted_tp_ids.append(tp_oid)
                                    except Exception:
                                        # Never leave a newly opened REAL position with a
                                        # half-created TP ladder. Cancel already accepted TP
                                        # plans, then attempt an emergency market close. The
                                        # exchange-side SL remains the last-resort protection
                                        # if the emergency close itself cannot be sent.
                                        try:
                                            await bg.cancel_plan_orders(session,sym,accepted_tp_ids)
                                        except Exception as ce:
                                            logging.error("TP ROLLBACK CANCEL %s: %s",sym,ce)
                                        try:
                                            await bg.close_partial_market(session,sym,sig["side"],actual_qty)
                                        except Exception as ee:
                                            logging.error("TP ROLLBACK CLOSE %s: %s",sym,ee)
                                        raise

                                    managed_positions[sym]={"side":sig["side"],"setup":sig.get("setup","-"),"entry":ep,"qty":actual_qty,"remaining":actual_qty,"levels":levels,"size_multiplier":mult,"order_id":oid,"opened_at_ms":int(time.time()*1000)}
                                    await notify(
                                        f"🚨 AUTO ORDER OPENED\n\n{sym} {sig['side']}\nScore: {current_score}/100\n"
                                        f"Entry fill: {ep}\nSize filled: {actual_qty}\nLeverage: {CFG.leverage}x\n"
                                        f"SL: {sig['sl']}\n"
                                        f"TP1: {levels[0]['price']:.10g} ({CFG.tp1_close_pct:.0f}%) — Bitget ID {levels[0]['exchange_order_id']}\n"
                                        f"TP2: {levels[1]['price']:.10g} ({CFG.tp2_close_pct:.0f}%) — Bitget ID {levels[1]['exchange_order_id']}\n"
                                        f"TP3: {levels[2]['price']:.10g} ({CFG.tp3_close_pct:.0f}%) — Bitget ID {levels[2]['exchange_order_id']}\n"
                                        f"SL: exchange-side\nEntry Order ID: {oid}\n"
                                        f"TP trigger: {CFG.tp_trigger_type} / market execution\n"
                                        f"Окно входа: {max(0,int((state['expires']-time.time())/60))}m\n"
                                        f"TP исполняются на Bitget даже если Python остановится. Новая сделка автоматически здесь НЕ открывается.",
                                        "high")
                                    csv_log({"time":pd.Timestamp.utcnow().isoformat(),"symbol":sym,"side":sig["side"],"setup":sig["setup"],"score":current_score,"entry":current_price,"sl":sig["sl"],"tp":sig["tp"],"qty":state["qty"],"action":"AUTO_ORDER","order_id":oid})
                                else:
                                    await notify(
                                        f"⚠️ AUTO SKIP {sym}: недостаточно маржи для реального ордера.\n"
                                        f"Available wallet: ${available:.2f}\n"
                                        f"Max available for {CFG.margin_mode}: ${max_available:.2f}\n"
                                        f"Margin: ${margin_needed:.2f}\n"
                                        f"Opening fee (estimate): ${opening_fee:.4f}\n"
                                        f"Safety buffer: ${margin_buffer:.4f}\n"
                                        f"Total required: ${total_required:.2f}","high")
                                    auto_ordered.add(sym)
                        except Exception as oe:
                            logging.error("AUTO ORDER %s: %s",sym,oe)
                            await notify(f"❌ AUTO ORDER FAILED — {sym}\n{sig['side']}\n{oe}\nСделка НЕ подтверждена.","high")
                            auto_ordered.add(sym)

            top.sort(key=lambda s:s["score"],reverse=True)
            print_market_table(table,top[:3])
            await asyncio.sleep(1)


if __name__=="__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nБот остановлен пользователем.")
