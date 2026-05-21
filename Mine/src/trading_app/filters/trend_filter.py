from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


INDEX_SYMBOLS = {'NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'SENSEX', 'NIFTYNXT50'}


def _safe(val: Any, default: float = 0.0) -> float:
    """Return default when val is NaN or Inf — keeps JSON serialisable."""
    try:
        f = float(val)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return default


def _sanitize(obj: Any) -> Any:
    """Recursively replace NaN/Inf floats with None so jsonify never emits NaN."""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    return obj


# ---------------------------------------------------------------------------
# Core indicator calculations
# ---------------------------------------------------------------------------

def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl  = df['high'] - df['low']
    hpc = (df['high'] - df['close'].shift(1)).abs()
    lpc = (df['low']  - df['close'].shift(1)).abs()
    tr  = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def calc_adx(df: pd.DataFrame, period: int = 14) -> Tuple[pd.Series, pd.Series, pd.Series]:
    high, low, close = df['high'], df['low'], df['close']
    hl  = high - low
    hpc = (high - close.shift(1)).abs()
    lpc = (low  - close.shift(1)).abs()
    tr  = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)

    up_move   = high.diff()
    down_move = -low.diff()
    plus_dm  = np.where((up_move > down_move) & (up_move > 0),   up_move,   0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    alpha    = 1.0 / period
    # Strip DatetimeIndex to plain RangeIndex — np.where results are plain arrays,
    # so dividing a DatetimeIndex Series by a RangeIndex Series produces all-NaN.
    atr_s    = pd.Series(tr.values, dtype=float).ewm(alpha=alpha, adjust=False).mean()
    plus_di  = 100 * pd.Series(plus_dm, dtype=float).ewm(alpha=alpha, adjust=False).mean() / atr_s
    minus_di = 100 * pd.Series(minus_dm, dtype=float).ewm(alpha=alpha, adjust=False).mean() / atr_s
    dx  = (plus_di - minus_di).abs() / (plus_di + minus_di).clip(lower=1e-9) * 100
    adx = dx.ewm(alpha=alpha, adjust=False).mean()
    return adx, plus_di, minus_di


def calc_choppiness(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl  = df['high'] - df['low']
    hpc = (df['high'] - df['close'].shift(1)).abs()
    lpc = (df['low']  - df['close'].shift(1)).abs()
    tr  = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)

    sum_tr = tr.rolling(period).sum()
    high_n = df['high'].rolling(period).max()
    low_n  = df['low'].rolling(period).min()
    rng    = (high_n - low_n).clip(lower=1e-9)
    return 100 * np.log10(sum_tr / rng) / np.log10(period)


def calc_norm_slope(closes: np.ndarray, atr_val: float) -> float:
    x     = np.arange(len(closes), dtype=float)
    slope = float(np.polyfit(x, closes, 1)[0])
    return float(np.clip(slope / max(atr_val, 1e-6), -1.0, 1.0))


def calc_candle_consistency(df: pd.DataFrame, lookback: int) -> float:
    tail      = df.iloc[-lookback:]
    bull_frac = float((tail['close'] > tail['open']).sum()) / lookback
    return float(2 * bull_frac - 1.0)


def calc_vix_score(vix_current: float, vix_year_high: float,
                   vix_year_low: float, adx_direction: int) -> float:
    vix_range = max(vix_year_high - vix_year_low, 1.0)
    vix_pct   = (vix_current - vix_year_low) / vix_range
    intensity = float(np.clip((vix_pct - 0.30) / 0.40, 0.0, 1.0))
    return intensity * adx_direction


def calc_pcr_score(pcr: float) -> float:
    return float(np.clip((pcr - 1.0) / 0.5, -1.0, 1.0))


# ---------------------------------------------------------------------------
# Composite regime classifier
# ---------------------------------------------------------------------------

def classify_regime(df: pd.DataFrame, lookback: int = 20,
                    vix_data: Optional[Dict] = None,
                    oi_data: Optional[Dict] = None,
                    symbol: str = '') -> Dict:
    # Drop rows with NaN in OHLC columns before calculations
    df = df.dropna(subset=['open', 'high', 'low', 'close'])

    closes  = df['close'].values
    atr_val = _safe(calc_atr(df).iloc[-1], default=1.0)

    adx_s, plus_di_s, minus_di_s = calc_adx(df)
    adx_v = _safe(adx_s.iloc[-1])
    pdi   = _safe(plus_di_s.iloc[-1])
    mdi   = _safe(minus_di_s.iloc[-1])
    chop  = _safe(calc_choppiness(df).iloc[-1], default=50.0)
    slope = _safe(calc_norm_slope(closes[-lookback:], atr_val))
    consistency = _safe(calc_candle_consistency(df, lookback))

    direction = +1 if pdi > mdi else -1

    adx_norm  = float(np.clip((adx_v - 20) / 30, 0.0, 1.0))
    adx_score = adx_norm * direction

    chop_intensity = float(np.clip((61.8 - chop) / (61.8 - 38.2), 0.0, 1.0))
    chop_score     = chop_intensity * direction

    is_index  = symbol.upper() in INDEX_SYMBOLS
    vix_score = pcr_score = None

    if is_index and vix_data and oi_data:
        vix_score = calc_vix_score(
            vix_data['current'], vix_data['high'], vix_data['low'], direction)
        pcr_score = calc_pcr_score(float(oi_data.get('pcr_oi') or 1.0))
        composite = (
            0.28 * adx_score   +
            0.22 * chop_score  +
            0.15 * slope       +
            0.10 * consistency +
            0.15 * vix_score   +
            0.10 * pcr_score
        )
    elif is_index and vix_data:
        vix_score = calc_vix_score(
            vix_data['current'], vix_data['high'], vix_data['low'], direction)
        composite = (
            0.32 * adx_score   +
            0.25 * chop_score  +
            0.18 * slope       +
            0.10 * consistency +
            0.15 * vix_score
        )
    else:
        composite = (
            0.35 * adx_score   +
            0.30 * chop_score  +
            0.20 * slope       +
            0.15 * consistency
        )

    if adx_v < 20 and chop > 61.8:
        regime = 'SIDEWAYS'
    elif composite > 0.22:
        regime = 'TREND_UP'
    elif composite < -0.22:
        regime = 'TREND_DOWN'
    else:
        regime = 'SIDEWAYS'

    confidence = round(min(abs(composite) / 0.55, 1.0) * 100, 1)

    result: Dict = {
        'regime':          regime,
        'confidence':      confidence,
        'composite':       round(float(composite), 3),
        'adx':             round(adx_v, 1),
        'plus_di':         round(pdi, 1),
        'minus_di':        round(mdi, 1),
        'chop_index':      round(chop, 1),
        'slope':           round(slope, 3),
        'consistency_pct': round((consistency + 1) / 2 * 100, 1),
        'atr':             round(atr_val, 2),
    }
    if vix_score is not None:
        result['vix_score'] = round(vix_score, 3)
    if pcr_score is not None:
        result['pcr_score'] = round(pcr_score, 3)
    return result


# ---------------------------------------------------------------------------
# Support & resistance detection
# ---------------------------------------------------------------------------

def detect_key_levels(df: pd.DataFrame, lookback: int,
                      atr: float, n_levels: int = 3) -> Dict:
    tail  = df.iloc[-(lookback + 4):]
    highs = tail['high'].values
    lows  = tail['low'].values

    res_raw = [highs[i] for i in range(1, len(highs) - 1)
               if highs[i] >= highs[i - 1] and highs[i] >= highs[i + 1]]
    sup_raw = [lows[i]  for i in range(1, len(lows) - 1)
               if lows[i]  <= lows[i - 1]  and lows[i]  <= lows[i + 1]]

    def cluster(levels: List[float]) -> List[float]:
        merged: List[float] = []
        tol = atr * 0.35
        for lvl in sorted(levels):
            if merged and abs(lvl - merged[-1]) < tol:
                merged[-1] = round((merged[-1] + lvl) / 2, 2)
            else:
                merged.append(round(lvl, 2))
        return merged

    price   = float(df['close'].iloc[-1])
    all_res = sorted([r for r in cluster(res_raw) if r > price])
    all_sup = sorted([s for s in cluster(sup_raw) if s < price], reverse=True)

    range_high = all_res[0] if all_res else round(price + atr, 2)
    range_low  = all_sup[0] if all_sup else round(price - atr, 2)

    return {
        'resistance_levels': all_res[:n_levels],
        'support_levels':    all_sup[:n_levels],
        'range_high':        round(range_high, 2),
        'range_low':         round(range_low, 2),
        'range_size':        round(range_high - range_low, 2),
    }


# ---------------------------------------------------------------------------
# Next-day projection
# ---------------------------------------------------------------------------

def project_next_day(close: float, atr: float, regime: str,
                     range_high: float, range_low: float) -> Dict:
    # Multipliers calibrated on NIFTY daily data (3 years, May 2023–May 2026, n=738 days).
    # Targeting 70th-percentile coverage per side — tighter, more actionable range.
    # S/R clamping is intentionally removed: it reduced accuracy by ~50% in backtests.
    #
    # TREND_UP   : up=0.65×, down=0.60×  → 1.25×ATR total  (~412 pts for ATR=330)
    # TREND_DOWN : up=0.70×, down=0.70×  → 1.40×ATR total  (~462 pts)
    # SIDEWAYS   : up=0.70×, down=0.65×  → 1.35×ATR total  (~446 pts)
    if regime == 'TREND_UP':
        proj_high = round(close + atr * 0.65, 2)
        proj_low  = round(close - atr * 0.60, 2)
        direction = 'UP'
    elif regime == 'TREND_DOWN':
        proj_high = round(close + atr * 0.70, 2)
        proj_low  = round(close - atr * 0.70, 2)
        direction = 'DOWN'
    else:  # SIDEWAYS
        proj_high = round(close + atr * 0.70, 2)
        proj_low  = round(close - atr * 0.65, 2)
        direction = 'NEUTRAL'

    return {
        'direction':      direction,
        'proj_high':      proj_high,
        'proj_low':       proj_low,
        'expected_range': round(proj_high - proj_low, 2),
        'atr_multiple':   round((proj_high - proj_low) / max(atr, 1e-6), 2),
    }


# ---------------------------------------------------------------------------
# Service class
# ---------------------------------------------------------------------------

class TrendFilterService:
    def analyse(self, df: pd.DataFrame, lookback: int = 20,
                vix_data: Optional[Dict] = None,
                oi_data: Optional[Dict] = None,
                symbol: str = '') -> Dict:
        df_clean = df.dropna(subset=['open', 'high', 'low', 'close'])
        if len(df_clean) < lookback + 20:
            raise ValueError(f"Need at least {lookback + 20} clean bars, got {len(df_clean)}")
        regime_data = classify_regime(df_clean, lookback,
                                      vix_data=vix_data, oi_data=oi_data, symbol=symbol)
        atr         = regime_data['atr'] or 1.0
        levels      = detect_key_levels(df_clean, lookback, atr)
        close       = _safe(float(df_clean['close'].iloc[-1]))
        next_day    = project_next_day(close, atr, regime_data['regime'],
                                       levels['range_high'], levels['range_low'])
        result = {**regime_data, **levels, 'close': round(close, 2), 'next_day': next_day}
        return _sanitize(result)
