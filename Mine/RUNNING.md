# Running the Application

## Main Application (Flask Web Server + Live Monitoring)

```bash
python3 main.py
```

This will:
- Start Flask web server on `http://127.0.0.1:5000`
- Run live signal monitoring in background
- Check signals every 5 minutes (9:20-15:25 IST)

Access the web interface at: **http://localhost:5000**

---

## Utility Scripts

### Setup Zerodha Access Token

```bash
python3 scripts/setup_access_token.py
```

Interactive setup to get and save your Zerodha API credentials.

---

### Run Strategy Backtest

```bash
python3 scripts/strategy_backtest.py
```

Backtests the High-Low strategy on historical data.

---

## Testing

### Run CPR Filter Tests

```bash
python3 -m pytest tests/test_cpr_filter.py -v
```

Or directly:

```bash
python3 tests/test_cpr_filter.py
```

---

## Environment Variables

Create a `.env` file with:

```env
API_KEY=your_zerodha_api_key
ACCESS_TOKEN=your_zerodha_access_token
FLASK_HOST=127.0.0.1
FLASK_PORT=5000
FLASK_ENV=development
```

---

## Project Structure

```
Mine/
├── main.py                          # ✅ Main entry point (run this!)
├── src/trading_app/                 # Application source code
│   ├── app/                         # Flask web app
│   ├── filters/                     # CPR filtering
│   ├── service/                     # Business logic
│   └── strategy/                    # Trading strategies
├── scripts/                         # Utility scripts
│   ├── setup_access_token.py
│   └── strategy_backtest.py
├── tests/                           # Test suite
│   └── test_cpr_filter.py
├── static/                          # Web assets
├── templates/                       # HTML templates
└── requirements.txt
```

---

## Troubleshooting

### "ModuleNotFoundError: No module named 'strategy_backtest'"
This is expected! `strategy_backtest.py` is a script, not a module. Run it directly:
```bash
python3 scripts/strategy_backtest.py
```

### "Address already in use" on port 5000
Another process is using port 5000. Either:
1. Stop that process
2. Change the port: `FLASK_PORT=5001 python3 main.py`

### Missing dependencies
Install all dependencies:
```bash
pip install -r requirements.txt
```

---

## Development

### Code Style (Black)
```bash
black src/
```

### Linting
```bash
flake8 src/ tests/
```

### Type Checking
```bash
mypy src/
```

---

## Features

✅ Live signal monitoring (every 5 minutes)
✅ CPR-based entry signal detection
✅ WhatsApp notifications
✅ Multi-strike options analysis
✅ Strategy backtesting
✅ Web dashboard
✅ API endpoints for data access
