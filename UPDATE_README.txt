# GitHub Auto Update

Repository: `danilkozhin05-debug/bitget-v15-pro`

Run `UPDATE.bat` to check GitHub and install a newer version.

The updater preserves user/private state such as:
- `.env`
- `config.json`
- `memory_state.json`
- `adaptive_stats.json`
- `candle_stats.json`
- `historical_models.json`
- `trades.csv`
- `paper_trades.csv`
- `historical_candles.csv`

Future releases are controlled by `updates/manifest.json` in the GitHub repository.
