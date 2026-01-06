# Project Structure Documentation

## Directory Layout

```
Mine/
├── src/                          # Source code package
│   ├── __init__.py
│   └── trading_app/              # Main application package
│       ├── __init__.py
│       ├── app/                  # Flask web application
│       │   ├── __init__.py       # App factory (create_app)
│       │   ├── config.py         # Configuration management
│       │   ├── extensions.py     # Flask extensions (limiter, csrf, cors)
│       │   ├── routes/           # API and page routes
│       │   │   ├── __init__.py
│       │   │   ├── api.py        # REST API endpoints
│       │   │   ├── auth.py       # Authentication routes
│       │   │   └── pages.py      # HTML page routes
│       │   └── utils/            # Utility modules
│       │       ├── __init__.py
│       │       ├── cache.py      # Caching utilities
│       │       ├── helpers.py    # Helper functions
│       │       └── logger.py     # Logging configuration
│       ├── filters/              # Signal filtering module
│       │   ├── __init__.py       # Exports CPRFilterService
│       │   └── cpr_filter.py     # CPR/PDH/PDL filter logic
│       ├── service/              # Business logic services
│       │   ├── __init__.py       # Exports all services
│       │   ├── cpr_service.py    # CPR/PDH/PDL calculations
│       │   ├── kite_service.py   # Zerodha broker integration
│       │   ├── options_chart_service.py  # Options charting
│       │   ├── whatsapp_service.py       # WhatsApp notifications
│       │   └── multi_strike_service.py   # Multi-strike analysis
│       └── strategy/             # Trading strategies
│           ├── __init__.py
│           └── Live/             # Live trading strategies
│               ├── __init__.py
│               ├── HighLowLiveSignal.py  # Main live trading engine
│               └── HighLowSignal.py      # Signal detection logic
│
├── tests/                        # Test suite
│   ├── __init__.py
│   └── test_cpr_filter.py       # CPR filter tests
│
├── scripts/                      # Utility and entry point scripts
│   ├── run.py                   # Flask app runner + live monitoring
│   ├── strategy_backtest.py     # Backtesting utility
│   └── setup_access_token.py    # Zerodha token setup
│
├── static/                       # Static web assets
│   ├── css/                     # Stylesheets
│   │   ├── main.css
│   │   ├── cpr_filter.css
│   │   ├── strategy.css
│   │   ├── multi_strike.css
│   │   ├── options_chart.css
│   │   ├── historical.css
│   │   └── notifications.css
│   └── js/                      # JavaScript files
│       ├── app.js
│       ├── constants.js
│       ├── index.js
│       ├── cpr_filter.js
│       ├── strategy.js
│       ├── multi_strike.js
│       ├── notifications.js
│       ├── options_chart_app.js
│       ├── historical.js
│       ├── cpr_filter_scheduler.js
│       └── services/
│           ├── notification.service.js
│           ├── options_chart.service.js
│           ├── strategy.service.js
│           └── whatsapp.service.js
│
├── templates/                    # Jinja2 HTML templates
│   ├── base.html               # Base template
│   ├── index.html              # Home page
│   ├── cpr_filter.html         # CPR filter page
│   ├── strategy.html           # Strategy backtest page
│   ├── multi_strike.html       # Multi-strike page
│   ├── options_chart.html      # Options chart page
│   └── historical.html         # Historical data page
│
├── main.py                      # Primary entry point (recommended)
├── run.py                       # Backward compatibility entry point
├── requirements.txt             # Python dependencies
├── setup.sh / setup.bat         # Setup scripts
├── .env                         # Environment variables (git ignored)
├── .gitignore                   # Git ignore rules
└── CLEANUP_SUMMARY.md          # Cleanup documentation
```

## Running the Application

### Method 1: Recommended (Primary Entry Point)
```bash
python main.py
```

### Method 2: Direct Script
```bash
python scripts/run.py
```

### Method 3: Legacy Compatibility
```bash
python run.py
```

All three methods do the same thing.

## Import Conventions

### Internal imports within src/trading_app:
```python
from trading_app.app import create_app
from trading_app.service.cpr_service import CPRService
from trading_app.filters import CPRFilterService
from trading_app.strategy.Live.HighLowLiveSignal import HighLowLiveSignal
```

### External script imports (tests, scripts):
```python
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from trading_app.filters import CPRFilterService
from trading_app.app import create_app
```

## Module Descriptions

### trading_app.app
Flask web application factory and configuration. Manages HTTP routes, authentication, and web UI.

### trading_app.service
Business logic services:
- **kite_service**: Zerodha KiteConnect integration
- **cpr_service**: CPR/PDH/PDL indicator calculations  
- **options_chart_service**: Options chain charting
- **whatsapp_service**: WhatsApp notification delivery
- **multi_strike_service**: Multi-strike options analysis

### trading_app.filters
Signal filtering module for CPR-based entry signals:
- **cpr_filter**: CPR filter service with stock filtering logic

### trading_app.strategy
Trading strategy implementations:
- **Live**: Live trading strategies with real-time signal detection
  - **HighLowLiveSignal**: Main live trading engine (938 lines)
  - **HighLowSignal**: Signal detection logic (414 lines)

## Environment Setup

1. **Create virtual environment:**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # macOS/Linux
   # or
   .venv\Scripts\activate     # Windows
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment:**
   ```bash
   cp .env.example .env
   # Edit .env with your Zerodha API credentials
   ```

4. **Run application:**
   ```bash
   python main.py
   ```

## Key Features

- **Live Signal Monitoring**: Real-time PDH/PDL crossing detection
- **CPR Filtering**: Find stocks meeting specific criteria
- **Backtesting**: Test strategies on historical data
- **Multi-strike Analysis**: Options chain analysis
- **Notifications**: WhatsApp alerts for signals
- **Web Dashboard**: Interactive UI for monitoring

## Testing

Run unit tests:
```bash
python -m pytest tests/
# or
python tests/test_cpr_filter.py
```

## Performance Notes

- Signal checks: Every 5 minutes (9:20-15:25 IST)
- Initialization timeout: 30 seconds max
- Non-blocking setup: Background thread for data init
- API rate limit: 0.05s delay per request
- Thread pool: 4 workers for parallel processing
