"""
StrategySignalService: Evaluates 6 market signals and recommends PE/CE/AVOID for
the Tuesday directional options strategy (sell 1 OTM weekly, buy 2x bi-weekly).
"""
import logging
import math
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List, NamedTuple, Tuple

logger = logging.getLogger(__name__)

NSE_INDEX_TOKENS = {
    'NIFTY':      256265,
    'BANKNIFTY':  260105,
    'FINNIFTY':   257801,
    'MIDCPNIFTY': 288009,
}

SYMBOL_CONFIG = {
    'NIFTY':     {'kite_key': 'NSE:NIFTY 50',          'fyers_key': 'NSE:NIFTY50-INDEX',   'strike_diff': 50,  'lot_size': 50},
    'BANKNIFTY': {'kite_key': 'NSE:NIFTY BANK',        'fyers_key': 'NSE:NIFTYBANK-INDEX', 'strike_diff': 100, 'lot_size': 25},
    'FINNIFTY':  {'kite_key': 'NSE:NIFTY FIN SERVICE', 'fyers_key': 'NSE:FINNIFTY-INDEX',  'strike_diff': 50,  'lot_size': 40},
    'SENSEX':    {'kite_key': 'BSE:SENSEX',             'fyers_key': 'BSE:SENSEX-INDEX',    'strike_diff': 100, 'lot_size': 10},
}

OTM_DISTANCE = 200


class SignalResult(NamedTuple):
    name: str
    value: Any
    vote: str            # 'PE' | 'CE' | 'NEUTRAL'
    detail: str
    display_value: str
    avoid_override: bool = False


class StrategySignalService:

    def __init__(self, provider):
        self._provider = provider
        self._is_fyers = self._detect_fyers()

    def _detect_fyers(self) -> bool:
        try:
            from trading_app.service.fyers_data_service import FyersDataServiceAdapter
            return isinstance(self._provider, FyersDataServiceAdapter)
        except Exception:
            return False

    def evaluate(self, symbol: str = 'NIFTY') -> Dict[str, Any]:
        symbol = symbol.upper()
        config = SYMBOL_CONFIG.get(symbol, SYMBOL_CONFIG['NIFTY'])
        instrument_key = config['fyers_key'] if self._is_fyers else config['kite_key']

        try:
            spot_price, vix = self._get_spot_and_vix(instrument_key)
        except Exception as e:
            logger.error(f'[strategy-signal] Failed to get spot/vix: {e}')
            return {'success': False, 'error': str(e)}

        strike_diff = config['strike_diff']
        atm = round(spot_price / strike_diff) * strike_diff

        oi_data = self._get_oi_data(symbol)
        candles_day, candles_1m = self._get_candles(symbol)
        global_data = self._get_global_cues()

        s_pcr    = self._eval_pcr(oi_data.get('strikes', []))
        s_oi     = self._eval_oi_buildup(oi_data.get('strikes', []), atm, strike_diff)
        s_gap    = self._eval_gap(candles_day, candles_1m)
        s_ema    = self._eval_ema_trend(candles_1m, spot_price)
        s_vix    = self._eval_india_vix(vix)
        s_global = self._eval_global_cues(global_data)

        pe_score = 0
        ce_score = 0
        avoid = False
        avoid_reason = None
        signals = []

        for sig in [s_pcr, s_oi, s_gap, s_ema, s_vix, s_global]:
            signals.append(sig._asdict())
            if sig.avoid_override:
                avoid = True
                avoid_reason = sig.detail
            elif sig.vote == 'PE':
                pe_score += 1
            elif sig.vote == 'CE':
                ce_score += 1

        if avoid:
            verdict = 'AVOID'
        elif pe_score >= 3 and pe_score > ce_score:
            verdict = 'PE'
        elif ce_score >= 3 and ce_score > pe_score:
            verdict = 'CE'
        else:
            verdict = 'AVOID'
            if not avoid:
                avoid_reason = f'Mixed signals (PE:{pe_score} CE:{ce_score} — need ≥3 clear votes)'

        spread = None
        if verdict in ('PE', 'CE'):
            spread = self._build_spread(symbol, verdict, atm, config)

        return {
            'success':      True,
            'symbol':       symbol,
            'current_price': spot_price,
            'atm':          atm,
            'timestamp':    datetime.now().isoformat(timespec='seconds'),
            'signals':      signals,
            'pe_score':     pe_score,
            'ce_score':     ce_score,
            'verdict':      verdict,
            'avoid':        avoid or verdict == 'AVOID',
            'avoid_reason': avoid_reason,
            'spread':       spread,
        }

    # ── Data fetchers ─────────────────────────────────────────────────────────

    def _get_spot_and_vix(self, instrument_key: str) -> Tuple[float, float]:
        vix_key = 'NSE:INDIA VIX'
        quotes = self._provider.quote([instrument_key, vix_key])
        spot = float((quotes.get(instrument_key) or {}).get('last_price', 0))
        vix  = float((quotes.get(vix_key) or {}).get('last_price', 0))
        if not spot:
            raise ValueError(f'Could not get spot price for {instrument_key}')
        return spot, vix

    def _get_oi_data(self, symbol: str) -> Dict[str, Any]:
        try:
            from trading_app.service.open_interest_service import OpenInterestService
            oi_svc = OpenInterestService(self._provider)
            cached = oi_svc.get_latest_oi_from_db(symbol, max_age_minutes=5)
            if cached:
                return cached
            return oi_svc.get_open_interest_data(symbol)
        except Exception as e:
            logger.warning(f'[strategy-signal] OI fetch failed: {e}')
            return {'strikes': []}

    def _get_candles(self, symbol: str) -> Tuple[List, List]:
        token = NSE_INDEX_TOKENS.get(symbol)
        if not token:
            return [], []

        now = datetime.now()
        from_day = (now - timedelta(days=5)).strftime('%Y-%m-%d')
        to_str   = now.strftime('%Y-%m-%d %H:%M:%S')
        from_1m  = (now - timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S')

        day_candles, min_candles = [], []
        try:
            day_candles = self._provider.historical_data(str(token), from_day, to_str, 'day') or []
        except Exception as e:
            logger.warning(f'[strategy-signal] Day candles failed: {e}')
        try:
            min_candles = self._provider.historical_data(str(token), from_1m, to_str, 'minute') or []
        except Exception as e:
            logger.warning(f'[strategy-signal] Minute candles failed: {e}')

        return day_candles, min_candles

    def _get_global_cues(self) -> List[Dict]:
        try:
            from trading_app.service.global_market_service import GlobalMarketService
            return GlobalMarketService.get_latest_data()
        except Exception as e:
            logger.warning(f'[strategy-signal] Global cues failed: {e}')
            return []

    # ── Signal evaluators ─────────────────────────────────────────────────────

    def _eval_pcr(self, strikes: List[Dict]) -> SignalResult:
        if not strikes:
            return SignalResult('PCR', 0, 'NEUTRAL', 'No OI data available', 'N/A')

        total_pe = sum(s.get('pe_oi', 0) or 0 for s in strikes)
        total_ce = sum(s.get('ce_oi', 0) or 0 for s in strikes)
        pcr = round(total_pe / total_ce, 2) if total_ce > 0 else 0.0

        if pcr > 1.2:
            return SignalResult('PCR', pcr, 'PE', f'PCR {pcr} > 1.2 → bearish bias', str(pcr))
        elif pcr < 0.8 and pcr > 0:
            return SignalResult('PCR', pcr, 'CE', f'PCR {pcr} < 0.8 → bullish bias', str(pcr))
        else:
            return SignalResult('PCR', pcr, 'NEUTRAL', f'PCR {pcr} in neutral zone (0.8–1.2)', str(pcr))

    def _eval_oi_buildup(self, strikes: List[Dict], atm: float, strike_diff: int) -> SignalResult:
        if not strikes:
            return SignalResult('OI Buildup', 0, 'NEUTRAL', 'No OI data available', 'N/A')

        window = 3 * strike_diff
        near = [s for s in strikes if abs((s.get('strike') or 0) - atm) <= window]
        if not near:
            return SignalResult('OI Buildup', 0, 'NEUTRAL', 'No strikes near ATM', 'N/A')

        ce_oi = sum(s.get('ce_oi', 0) or 0 for s in near)
        pe_oi = sum(s.get('pe_oi', 0) or 0 for s in near)

        if ce_oi == 0 and pe_oi == 0:
            return SignalResult('OI Buildup', 0, 'NEUTRAL', 'Zero OI near ATM', 'N/A')

        ratio = round(ce_oi / pe_oi, 2) if pe_oi > 0 else 99.0

        if ce_oi > pe_oi * 1.2:
            return SignalResult('OI Buildup', ratio, 'PE',
                                f'Heavy CE OI near ATM (CE/PE={ratio}) → resistance above', 'CE Heavy')
        elif pe_oi > ce_oi * 1.2:
            return SignalResult('OI Buildup', ratio, 'CE',
                                f'Heavy PE OI near ATM (CE/PE={ratio}) → support below', 'PE Heavy')
        else:
            return SignalResult('OI Buildup', ratio, 'NEUTRAL',
                                f'Balanced OI near ATM (CE/PE={ratio})', 'Balanced')

    def _eval_gap(self, day_candles: List, min_candles: List) -> SignalResult:
        if len(day_candles) < 2 or not min_candles:
            return SignalResult('Gap Direction', 0, 'NEUTRAL', 'Insufficient candle data', 'N/A')

        try:
            prev_close = float(day_candles[-2]['close'])
            today_open = float(min_candles[0]['open'])
            gap = round(today_open - prev_close, 1)

            if gap < -100:
                return SignalResult('Gap Direction', gap, 'PE',
                                    f'Gap-down {abs(gap):.0f} pts from prev close', f'{gap:+.0f} pts')
            elif gap > 100:
                return SignalResult('Gap Direction', gap, 'CE',
                                    f'Gap-up {abs(gap):.0f} pts from prev close', f'+{gap:.0f} pts')
            else:
                return SignalResult('Gap Direction', gap, 'NEUTRAL',
                                    f'Flat open (gap {gap:+.0f} pts)', f'{gap:+.0f} pts')
        except (KeyError, IndexError, TypeError) as e:
            logger.warning(f'[strategy-signal] Gap calc error: {e}')
            return SignalResult('Gap Direction', 0, 'NEUTRAL', 'Gap calculation error', 'N/A')

    def _eval_ema_trend(self, candles_1m: List, spot_price: float) -> SignalResult:
        if len(candles_1m) < 20:
            return SignalResult('EMA Trend', spot_price, 'NEUTRAL', 'Insufficient candles for EMA', 'N/A')

        try:
            from trading_app.filters.ema_rsi_filter import _calc_ema

            closes = [float(c['close']) for c in candles_1m]
            ema9_series  = _calc_ema(closes, 9)
            ema20_series = _calc_ema(closes, 20)

            last_ema9  = next((v for v in reversed(ema9_series)  if not math.isnan(v)), None)
            last_ema20 = next((v for v in reversed(ema20_series) if not math.isnan(v)), None)

            if last_ema9 is None or last_ema20 is None:
                return SignalResult('EMA Trend', spot_price, 'NEUTRAL', 'EMA calc returned NaN', 'N/A')

            last_ema9  = round(last_ema9,  1)
            last_ema20 = round(last_ema20, 1)

            if spot_price < last_ema9 and spot_price < last_ema20:
                return SignalResult('EMA Trend', spot_price, 'PE',
                                    f'Price {spot_price} below EMA9({last_ema9}) & EMA20({last_ema20})',
                                    'Below EMA9+20')
            elif spot_price > last_ema9 and spot_price > last_ema20:
                return SignalResult('EMA Trend', spot_price, 'CE',
                                    f'Price {spot_price} above EMA9({last_ema9}) & EMA20({last_ema20})',
                                    'Above EMA9+20')
            else:
                return SignalResult('EMA Trend', spot_price, 'NEUTRAL',
                                    f'Price between EMA9({last_ema9}) & EMA20({last_ema20})',
                                    'Mixed EMAs')
        except Exception as e:
            logger.warning(f'[strategy-signal] EMA calc error: {e}')
            return SignalResult('EMA Trend', spot_price, 'NEUTRAL', f'EMA error: {e}', 'N/A')

    def _eval_india_vix(self, vix: float) -> SignalResult:
        if vix <= 0:
            return SignalResult('India VIX', vix, 'NEUTRAL', 'VIX data unavailable', 'N/A')

        vix_str = f'{vix:.2f}'
        if vix > 20:
            return SignalResult('India VIX', vix, 'NEUTRAL',
                                f'VIX {vix:.2f} > 20 → AVOID (IV too high for this strategy)',
                                vix_str, avoid_override=True)
        elif vix > 16:
            return SignalResult('India VIX', vix, 'PE',
                                f'VIX {vix:.2f} elevated (>16) → bearish fear', vix_str)
        elif vix < 12:
            return SignalResult('India VIX', vix, 'CE',
                                f'VIX {vix:.2f} low (<12) → complacency, bullish', vix_str)
        else:
            return SignalResult('India VIX', vix, 'NEUTRAL',
                                f'VIX {vix:.2f} in normal range (12–16)', vix_str)

    def _eval_global_cues(self, global_data: List[Dict]) -> SignalResult:
        if not global_data:
            return SignalResult('Global Cues', 0, 'NEUTRAL', 'Global data unavailable', 'N/A')

        try:
            tracked = {'dji_fut', 'gift_nifty'}
            changes, names_used = [], []

            for item in global_data:
                if item.get('id') in tracked:
                    pchange = float(item.get('pChange', 0) or 0)
                    changes.append(pchange)
                    names_used.append(f"{item.get('name', item['id'])} {pchange:+.2f}%")

            if not changes:
                for item in global_data:
                    if item.get('id') == 'dji_fut':
                        pchange = float(item.get('pChange', 0) or 0)
                        changes.append(pchange)
                        names_used.append(f"Dow Futures {pchange:+.2f}%")
                        break

            if not changes:
                return SignalResult('Global Cues', 0, 'NEUTRAL', 'No Dow/Gift Nifty data', 'N/A')

            avg = round(sum(changes) / len(changes), 2)
            detail = ', '.join(names_used)

            if avg < -0.3:
                return SignalResult('Global Cues', avg, 'PE',
                                    f'Negative global cues ({detail})', f'{avg:+.2f}%')
            elif avg > 0.3:
                return SignalResult('Global Cues', avg, 'CE',
                                    f'Positive global cues ({detail})', f'{avg:+.2f}%')
            else:
                return SignalResult('Global Cues', avg, 'NEUTRAL',
                                    f'Flat global cues ({detail})', f'{avg:+.2f}%')
        except Exception as e:
            logger.warning(f'[strategy-signal] Global cues error: {e}')
            return SignalResult('Global Cues', 0, 'NEUTRAL', f'Error: {e}', 'N/A')

    # ── Spread builder ────────────────────────────────────────────────────────

    def _build_spread(self, symbol: str, side: str, atm: float, config: dict) -> Optional[Dict]:
        try:
            lot_size    = config.get('lot_size', 50)
            otm_strike  = int(atm - OTM_DISTANCE) if side == 'PE' else int(atm + OTM_DISTANCE)
            opt_type    = side

            weekly_exp, biweekly_exp = self._select_expiries(symbol)
            if not weekly_exp:
                logger.warning('[strategy-signal] Could not determine expiries')
                return None

            weekly_sym   = self._resolve_option_symbol(symbol, otm_strike, opt_type, weekly_exp)
            biweekly_sym = self._resolve_option_symbol(symbol, otm_strike, opt_type, biweekly_exp)

            sold_ltp, bought_ltp = self._get_leg_ltps(weekly_sym, biweekly_sym)

            net_per_lot = round(sold_ltp - (2 * bought_ltp), 1)
            net_label   = f"Net {'Credit' if net_per_lot >= 0 else 'Debit'} ₹{abs(net_per_lot):.1f}/lot"

            def fmt(d):
                if not d: return ''
                return d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d)

            return {
                'side':             side,
                'otm_strike':       otm_strike,
                'weekly_expiry':    fmt(weekly_exp),
                'biweekly_expiry':  fmt(biweekly_exp),
                'legs': [
                    {
                        'action':       'SELL',
                        'quantity':     1,
                        'expiry_label': 'Weekly',
                        'expiry':       fmt(weekly_exp),
                        'strike':       otm_strike,
                        'option_type':  opt_type,
                        'ltp':          sold_ltp,
                        'tradingsymbol': weekly_sym or '',
                    },
                    {
                        'action':       'BUY',
                        'quantity':     2,
                        'expiry_label': 'Bi-Weekly',
                        'expiry':       fmt(biweekly_exp),
                        'strike':       otm_strike,
                        'option_type':  opt_type,
                        'ltp':          bought_ltp,
                        'tradingsymbol': biweekly_sym or '',
                    },
                ],
                'sold_premium':       sold_ltp,
                'bought_premium_each': bought_ltp,
                'net_per_lot':        net_per_lot,
                'net_label':          net_label,
                'lot_size':           lot_size,
            }
        except Exception as e:
            logger.error(f'[strategy-signal] Spread build failed: {e}', exc_info=True)
            return None

    def _select_expiries(self, symbol: str) -> Tuple[Optional[date], Optional[date]]:
        try:
            from trading_app.service.kite_order_services import KiteOrderService
            kite_svc = KiteOrderService(self._provider)
            instruments = kite_svc.get_nfo_instruments(symbol)
            if not instruments:
                return None, None

            today = date.today()
            expiry_dates = sorted({
                (inst['expiry'].date() if hasattr(inst['expiry'], 'date') else inst['expiry'])
                for inst in instruments
                if inst.get('instrument_type') in ('CE', 'PE') and inst.get('expiry') is not None
                and (inst['expiry'].date() if hasattr(inst['expiry'], 'date') else inst['expiry']) >= today
            })

            if len(expiry_dates) >= 2:
                return expiry_dates[0], expiry_dates[1]
            elif len(expiry_dates) == 1:
                return expiry_dates[0], expiry_dates[0]
            return None, None
        except Exception as e:
            logger.warning(f'[strategy-signal] Expiry selection failed: {e}')
            return None, None

    def _resolve_option_symbol(self, symbol: str, strike: int, opt_type: str,
                               expiry: Optional[date]) -> Optional[str]:
        try:
            from trading_app.service.kite_order_services import KiteOrderService
            kite_svc   = KiteOrderService(self._provider)
            instruments = kite_svc.get_nfo_instruments(symbol)
            if not instruments:
                return None

            for inst in instruments:
                inst_expiry = inst.get('expiry')
                if inst_expiry and hasattr(inst_expiry, 'date'):
                    inst_expiry = inst_expiry.date()
                if (inst.get('strike') == strike
                        and inst.get('instrument_type') == opt_type
                        and inst_expiry == expiry):
                    return inst.get('tradingsymbol')
            return None
        except Exception as e:
            logger.warning(f'[strategy-signal] Symbol resolution failed: {e}')
            return None

    def _get_leg_ltps(self, weekly_sym: Optional[str],
                      biweekly_sym: Optional[str]) -> Tuple[float, float]:
        sold_ltp = 0.0
        bought_ltp = 0.0
        try:
            to_fetch = []
            if weekly_sym:
                to_fetch.append(f'NFO:{weekly_sym}')
            if biweekly_sym and biweekly_sym != weekly_sym:
                to_fetch.append(f'NFO:{biweekly_sym}')
            if not to_fetch:
                return sold_ltp, bought_ltp

            ltp_data = self._provider.ltp(to_fetch)
            if weekly_sym:
                sold_ltp = round(float((ltp_data.get(f'NFO:{weekly_sym}') or {}).get('last_price', 0)), 1)
            if biweekly_sym:
                bought_ltp = round(float((ltp_data.get(f'NFO:{biweekly_sym}') or {}).get('last_price', 0)), 1)
        except Exception as e:
            logger.warning(f'[strategy-signal] LTP fetch failed: {e}')
        return sold_ltp, bought_ltp
