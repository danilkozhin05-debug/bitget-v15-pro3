# Bitget STRICT V15 PRO — Candle Intelligence + AUTO REAL

Эта версия сохраняет V15 PRO и добавляет/усиливает только систему свечного анализа и автоматического исполнения.

## Реальные данные
- Bitget USDT Futures
- 20 фьючерсов
- 1m / 5m / 15m candles
- ticker обновляется примерно раз в секунду
- свечной поток/REST используется для анализа
- исторические аналоги без look-ahead

Bitget официально предоставляет 1m/5m/15m candles и private endpoint для реального размещения futures orders.

## Candle Intelligence
Учитываются:
- bullish/bearish engulfing
- hammer / shooting star
- тело и тени свечи
- EMA9/21/50/200
- RSI
- MACD
- ATR
- объём
- breakout/pullback/trend/reversal
- согласование 1m/5m/15m
- исторические похожие ситуации

Исторический блок выдаёт статистическую вероятность направления и долю случаев, когда TP был достигнут раньше SL. Это НЕ гарантия следующей свечи.

## AUTO REAL
`START_AUTO_REAL.bat` включает:
- LIVE_REAL=true
- AUTO_REAL=true

При запуске START_AUTO_REAL.bat режим AUTO REAL включается автоматически. Если ключи не записаны в .env, бот попросит ввести API Key, Secret Key и Passphrase в консоли и проверит приватный Bitget API до начала мониторинга. Ключи хранятся только в памяти процесса.

API ключу не нужен Withdraw. Нужны права чтения и торговли.

## Риск
По умолчанию:
- leverage 3x
- risk 1% депозита на сделку
- максимум 1 автоматическая позиция
- isolated margin
- Entry market order отправляется без устаревшего signal-time SL; после подтверждения fill бот рассчитывает SL от фактической цены исполнения и ставит position-level SL на стороне Bitget
- TP1/TP2/TP3 ставятся отдельными exchange-side profit_plan ордерами на Bitget после подтверждения fill
- Python не закрывает TP локально и не дублирует биржевые TP

Реальная торговля может привести к убытку. Ни свечи, ни исторические аналоги, ни Score не дают гарантии прибыли.


## V15 PRO FIX — 40009, real-position confirmation and SL 400 fix

This build fixes the private Bitget REST authentication path:
- GET query parameters are sorted and the exact same query string is both signed and sent.
- The exact JSON body that is signed is the body sent to Bitget.
- Bitget server time is fetched before each authenticated attempt.
- A 40009 signature error stops immediately with a clear diagnostic instead of retrying a bad signature.
- AUTO REAL waits for Bitget to confirm the entry order is filled before the local position manager starts.
- A position is never reported as closed merely because the local signal window expired.
- TP1/TP2/TP3 are real Bitget exchange-side partial profit plans with market execution.
- Python only monitors exchange position-size changes and reports confirmed TP fills; it does not send a duplicate local TP.
- After entry fill, a position-level exchange-side SL is created first using Bitget `/api/v2/mix/order/place-pos-tpsl`. If Bitget rejects the SL, the bot attempts an emergency market close and does not continue to create TP plans.
- The SL distance keeps the original signal risk percentage but is recalculated from the actual average fill price; a final direction check prevents an invalid LONG/SHORT stop from being submitted.
- LONG/SHORT close orders use Bitget hedge-mode side/tradeSide semantics.
- Closing notifications include realized profit/fees found in recent Bitget fills.

### Safe private API test
Run `TEST_BITGET_PRIVATE.bat` before `START_AUTO_REAL.bat`. It checks account and position endpoints and sends **no order**.

### Important
The bot cannot guarantee profit. REAL mode can lose money. Use a Bitget API key with only the permissions required for trading; never enable withdrawals. Bitget documents 40009 as `sign signature error` and requires the signature to be based on the exact timestamp, method, request path, query string and body.


## Adaptive AI / learning layer
- Walk-forward logistic model trained only on prior closed candles.
- Predicts TP-before-SL probability for the current LONG/SHORT setup.
- Combines with the existing historical-analogue model.
- Persists real closed-trade results in `adaptive_stats.json` by side + setup and uses a Bayesian-smoothed win-rate adjustment after at least 5 trades.
- Low-confidence disagreement can block a signal by reducing its score.
- This is statistical learning, not a guarantee or exact next-candle predictor.


## Adaptive AI + Candle Knowledge Engine

This build combines walk-forward adaptive ML, real/paper trade statistics, historical analogue analysis, and a context-aware candlestick knowledge engine. The candle engine recognizes doji, hammer, inverted hammer, shooting star, bullish/bearish engulfing, marubozu, tweezer tops/bottoms, morning/evening star, and three white soldiers/three black crows. It scores patterns only with trend, RSI, volume and closed-candle confirmation; patterns are not standalone guarantees.

SIGNAL/PAPER mode uses live Bitget market data without placing real orders. Adaptive real-trade statistics are only updated from confirmed closed trades.
