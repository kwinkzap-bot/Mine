"""Strategy backtest module - OptionsStrategy class for backtesting options trading strategies."""

import os
import sys
from datetime import datetime, timedelta, date
from typing import List, Dict, Tuple, Optional, Any
import json
import logging
import importlib.util

import pandas as pd
import numpy as np
from kiteconnect import KiteConnect
from kiteconnect.exceptions import KiteException
from dotenv import load_dotenv

from trading_app.app.utils.logger import logger
from trading_app.strategy.Live.HighLowSignal import HighLowSignal
from trading_app.service.options_chart_service import OptionsChartService

load_dotenv()


def _load_options_strategy_class():
    """Dynamically load OptionsStrategy class from scripts/strategy_backtest.py."""
    # Navigate from src/trading_app/strategy/ to scripts/ at root
    current_dir = os.path.dirname(__file__)
    script_path = os.path.abspath(os.path.join(current_dir, '../../../scripts/strategy_backtest.py'))
    
    if not os.path.exists(script_path):
        raise ImportError(f"Could not find strategy_backtest.py at {script_path}")
    
    spec = importlib.util.spec_from_file_location("strategy_backtest_module", script_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load spec from {script_path}")
    
    module = importlib.util.module_from_spec(spec)
    sys.modules["strategy_backtest_module"] = module
    spec.loader.exec_module(module)
    return module.OptionsStrategy


# Lazy load OptionsStrategy class
_OptionsStrategy = None

def OptionsStrategy(*args, **kwargs):
    """Factory function to load and instantiate OptionsStrategy."""
    global _OptionsStrategy
    if _OptionsStrategy is None:
        _OptionsStrategy = _load_options_strategy_class()
    return _OptionsStrategy(*args, **kwargs)


__all__ = ['OptionsStrategy']
