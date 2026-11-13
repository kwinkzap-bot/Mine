"""Monthly RSI Breakout engine — synthetic months, three trading days each."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from trading_app.Backtest import monthly_rsi_breakout_engine as mrb

ANY_RSI = dict(rsi_lo=0, rsi_hi=100, min_turnover_cr=0)


def build(months, start='2018-01'):
    """months: list of 3-day lists of (open, high, low, close). Day 1, 2, 3 are
    the 2nd, 10th and 20th of each month."""
    rows, idx = [], []
    for k, days in enumerate(months):
        per = pd.Period(start, 'M') + k
        for d, (o, h, l, c) in zip((2, 10, 20), days):
            idx.append(pd.Timestamp(per.year, per.month, d))
            rows.append((o, h, l, c, 1e6))
    return pd.DataFrame(rows, index=pd.DatetimeIndex(idx), columns=['open', 'high', 'low', 'close', 'volume'])


def calm(p, step=1.0):
    """A month drifting up from p: low p-1, high p+step, closing at p+step/2."""
    return [(p, p + step / 2, p - 1, p + step / 4), (p + step / 4, p + step, p, p + step / 2),
            (p + step / 2, p + step, p, p + step / 2)]


def uptrend(n, p=100.0):
    out = []
    for _ in range(n):
        out.append(calm(p))
        p += 1
    return out, p


def test_rsi_extremes_and_balance():
    assert mrb.wilder_rsi(np.arange(1, 30, dtype=float))[-1] == 100.0
    alt = np.array([100, 101] * 20, dtype=float)
    assert abs(mrb.wilder_rsi(alt)[-1] - 50) < 5
    assert np.isnan(mrb.wilder_rsi(np.arange(10.0))).all()


def test_rsi_matches_tradingview_formula():
    rng = np.random.default_rng(1)
    c = 100 + np.cumsum(rng.normal(0, 1, 80))
    d = np.diff(c)
    up, dn = np.clip(d, 0, None), np.clip(-d, 0, None)
    au, ad = up[:14].mean(), dn[:14].mean()
    for i in range(14, len(d)):
        au, ad = (au * 13 + up[i]) / 14, (ad * 13 + dn[i]) / 14
    assert mrb.wilder_rsi(c)[-1] == pytest.approx(100 - 100 / (1 + au / ad))


def test_entry_at_the_previous_months_high_and_gap_fills_at_open():
    months, p = uptrend(24)
    signal_high = months[-1][1][1]               # month 24's high
    months.append([(p, signal_high + 0.5, p - 1, p), calm(p)[1], calm(p)[2]])  # trades through it
    months.append(calm(p + 1))
    cands = mrb.candidates('X', build(months), date(2018, 1, 1), date(2030, 1, 1), **ANY_RSI)
    c = next(c for c in cands if c['signal_period'] == '2019-12')
    assert c['entry_date'] == '2020-01-02' and c['entry_price'] == signal_high

    months, p = uptrend(24)
    gap = months[-1][1][1] + 5
    months.append([(gap, gap + 1, gap - 0.5, gap)] * 3)
    months.append(calm(gap))
    cands = mrb.candidates('X', build(months), date(2018, 1, 1), date(2030, 1, 1), **ANY_RSI)
    c = next(c for c in cands if c['signal_period'] == '2019-12')
    assert c['entry_price'] == gap               # gapped over the trigger: filled at the open


def test_no_break_next_month_no_trade_and_the_forming_month_never_signals():
    months, p = uptrend(24)
    months.append([(p - 3, p - 2, p - 4, p - 3)] * 3)  # stays under month 24's high
    cands = mrb.candidates('X', build(months), date(2018, 1, 1), date(2030, 1, 1), **ANY_RSI)
    assert not any(c['signal_period'] == '2019-12' for c in cands)
    assert not any(c['signal_period'] == '2020-01' for c in cands)   # last month is still forming


def test_exit_needs_a_close_below_the_ema_then_its_low_broken():
    months, p = uptrend(30)
    ema_now = mrb.ema(mrb.monthly_bars(build(months))['close'].to_numpy())[-1]
    low = ema_now - 10
    months.append([(p, p, low, low + 2)] * 3)          # closes well under the 20 EMA, low = `low`
    months.append([(low + 3, low + 4, low + 1, low + 3)] * 3)  # holds above that low: still in
    months.append([(low + 3, low + 4, low - 1, low)] * 3)      # breaks it
    cands = mrb.candidates('X', build(months), date(2018, 1, 1), date(2030, 1, 1), **ANY_RSI)
    c = next(c for c in cands if c['signal_period'] == '2019-08')
    # Month 32 (2020-08) closes under the EMA too, so the low to break moved up to its own.
    assert c['exit_reason'] == 'Below 20 EMA, low broken'
    assert c['exit_date'] == '2020-09-02' and c['exit_price'] == pytest.approx(low + 1, abs=0.01)


def test_a_close_back_above_the_ema_disarms_the_exit():
    months, p = uptrend(30)
    ema_now = mrb.ema(mrb.monthly_bars(build(months))['close'].to_numpy())[-1]
    low = ema_now - 2
    months.append([(p, p, low, ema_now - 1)] * 3)       # closes just under the EMA
    months.append([(p, p + 5, p - 1, p + 5)] * 3)       # back above it: disarmed
    months.append([(p + 5, p + 6, low - 5, p + 6)] * 3)  # trades under the old low — not an exit
    cands = mrb.candidates('X', build(months), date(2018, 1, 1), date(2030, 1, 1), **ANY_RSI)
    c = next(c for c in cands if c['signal_period'] == '2019-08')
    assert c['exit_reason'] == 'Open'


def test_rsi_band_and_turnover_filter():
    months, _ = uptrend(30)
    df = build(months)
    assert mrb.candidates('X', df, date(2018, 1, 1), date(2030, 1, 1), rsi_lo=60, rsi_hi=65,
                          min_turnover_cr=0) == []      # a straight line up is RSI 100
    assert mrb.candidates('X', df, date(2018, 1, 1), date(2030, 1, 1), rsi_lo=0, rsi_hi=100,
                          min_turnover_cr=1000) == []   # ~₹1 Cr a day < ₹1,000 Cr


def test_clean_daily_starts_after_an_unadjusted_split():
    months, p = uptrend(10)
    df = build(months)
    split_at = df.index[15]
    df.loc[split_at:, ['open', 'high', 'low', 'close']] /= 5
    cleaned = mrb.clean_daily(df)
    assert cleaned.index[0] == split_at


def _cand(sym, entry, exit_, px=100.0, stop=90.0, reason='Below 20 EMA, low broken', exit_px=110.0,
          month='2020-01', turnover=5.0):
    return {'symbol': sym, 'signal_period': month, 'rsi': 62, 'trigger': px, 'stop': stop,
            'turnover_cr': turnover, 'entry_date': entry, 'entry_price': px,
            'exit_date': exit_, 'exit_price': exit_px, 'exit_reason': reason}


def test_simulate_caps_new_stocks_a_month_and_sizes_on_risk():
    cands = [_cand(f'S{i}', f'2020-02-{i + 3:02d}', '2020-06-01', turnover=10 - i) for i in range(7)]
    r = mrb.simulate(cands, {}, date(2020, 1, 1), date(2020, 12, 31), capital=1_000_000, size_mode='risk',
                     risk_pct=1, max_pos_pct=20, max_new=5, nifty_floor=0, cost_pct=0, brokerage=0)
    assert len(r['trades']) == 5 and r['skipped']['month_cap'] == 2
    # 1 % of ₹10 L over a ₹10 risk = 1,000 shares, under the 20 % cap (2,000).
    assert r['trades'][0]['qty'] == 1000
    assert r['trades'][0]['pnl_rupees'] == 10_000
    assert r['curve'][-1]['equity'] == pytest.approx(1_050_000)


def test_simulate_cash_held_and_nifty_floor():
    cands = [_cand('A', '2020-02-03', '2020-09-01'), _cand('A', '2020-03-03', '2020-09-01', month='2020-02'),
             _cand('B', '2020-02-04', '2020-09-01', month='2020-01'),
             _cand('C', '2020-04-03', '2020-09-01', month='2020-03')]
    r = mrb.simulate(cands, {}, date(2020, 1, 1), date(2020, 12, 31), capital=10_000, size_mode='risk',
                     risk_pct=100, max_pos_pct=100, max_new=5, nifty_floor=40, cost_pct=0, brokerage=0,
                     nifty_rsi={'2020-01': 55, '2020-02': 55, '2020-03': 35})
    assert [t['symbol'] for t in r['trades']] == ['A']   # A takes all the cash
    assert r['skipped'] == {'held': 1, 'month_cap': 0, 'nifty': 1, 'cash': 1, 'slots': 0}


def test_exit_frees_cash_before_a_same_day_entry_and_open_trades_are_marked():
    cands = [_cand('A', '2020-02-03', '2020-05-04'),
             _cand('B', '2020-05-04', '2020-12-31', reason='Open', exit_px=150.0, month='2020-04')]
    r = mrb.simulate(cands, {}, date(2020, 1, 1), date(2020, 12, 31), capital=10_000, size_mode='risk',
                     risk_pct=100, max_pos_pct=100, nifty_floor=0, cost_pct=0, brokerage=0)
    assert [t['symbol'] for t in r['trades']] == ['A', 'B']
    b = r['trades'][1]
    assert b['result'] == 'OPEN' and b['qty'] == 110      # A's ₹11,000 paid for B
    s = mrb.summarise(r, date(2020, 1, 1), date(2020, 12, 31), 2)
    assert s['open_trades'] == 1 and s['closed_trades'] == 1 and s['wins'] == 1



def test_brokerage_is_charged_on_every_buy_and_sell():
    cands = [_cand('A', '2020-02-03', '2020-06-01'),
             _cand('B', '2020-02-04', '2020-12-31', reason='Open', exit_px=120.0)]
    r = mrb.simulate(cands, {}, date(2020, 1, 1), date(2020, 12, 31), capital=1_000_000, size_mode='risk',
                     risk_pct=1, max_pos_pct=20, nifty_floor=0, cost_pct=0, brokerage=20)
    a, b = r['trades']
    assert a['brokerage'] == 40 and a['charges'] == 40
    assert a['pnl_rupees'] == 1000 * 10 - 40
    # B is sized on equity after A's ₹20 buy order: 1 % of ₹9,99,980 over a ₹10 risk = 999 shares.
    assert b['qty'] == 999
    assert b['pnl_rupees'] == 999 * 20 - 40             # a held stock is charged its sell too
    assert r['final_cash'] == pytest.approx(1_000_000 - 100_000 - 20 - 99_900 - 20 + 110_000 - 20)
    s = mrb.summarise(r, date(2020, 1, 1), date(2020, 12, 31), 2)
    assert s['total_brokerage'] == 80


def test_a_buy_must_cover_its_brokerage():
    cands = [_cand('A', '2020-02-03', '2020-06-01')]
    r = mrb.simulate(cands, {}, date(2020, 1, 1), date(2020, 12, 31), capital=10_000, size_mode='risk',
                     risk_pct=100, max_pos_pct=100, nifty_floor=0, cost_pct=0, brokerage=20)
    # 100 shares is all ₹10,000; with the ₹20 order on top the cash cannot pay in full: skipped, not bought smaller.
    assert r['trades'] == [] and r['skipped']['cash'] == 1


def test_route_runs_the_portfolio_from_the_stock_files(monkeypatch):
    """The POST + background task end to end, on fake files: no broker, no create_app()."""
    import time
    from route_app import build_route_app
    from trading_app.app.routes import api as api_mod
    months, p = uptrend(30)
    files = {'AAA': {'df': build(months)}, 'BBB': {'df': build(months)}}
    monkeypatch.setattr(api_mod, 'check_auth', lambda: None)
    monkeypatch.setattr(api_mod, '_mrb_universe', lambda kind: ['AAA', 'BBB', 'NOFILE'])
    monkeypatch.setattr('trading_app.filters.candle_store._load_disk', lambda sym: files.get(sym))
    app = build_route_app()
    body = {'start_date': '2019-01-01', 'end_date': '2020-06-30', 'rsi_lo': 0, 'rsi_hi': 100,
            'min_turnover_cr': 0, 'nifty_floor': 0, 'max_new': 1, 'capital': 100000}
    with app.test_request_context(json=body):
        task_id = api_mod.run_monthly_rsi_breakout_backtest_api.__wrapped__().get_json()['task_id']
    for _ in range(200):
        task = api_mod._mrb_tasks[task_id]
        if task['status'] != 'running':
            break
        time.sleep(0.02)
    assert task['status'] == 'complete', task.get('error')
    s = task['payload']['summary']
    assert s['symbols_scanned'] == 3 and s['symbols_no_data'] == 1
    # Both stocks break out every month; one new stock a month, and each is then held.
    assert s['total_trades'] == 2 and s['skipped_month_cap'] >= 1 and s['open_trades'] == 2
    assert [y['year'] for y in s['by_year']] == [2019, 2020]
    assert s['params']['timeframe'] == 'month'
    with app.test_request_context(json={**body, 'timeframe': 'hour'}):
        resp = api_mod.run_monthly_rsi_breakout_backtest_api.__wrapped__()
    assert resp[1] == 400


def daily_frame(rows, start='2020-01-01'):
    """rows: (open, high, low, close) one per business day."""
    idx = pd.bdate_range(start, periods=len(rows))
    return pd.DataFrame([(*r, 1e6) for r in rows], index=idx, columns=['open', 'high', 'low', 'close', 'volume'])


def test_day_timeframe_enters_the_next_day_above_the_signal_days_high():
    rows = [(100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(40)]       # a steady climb
    gap = rows[-1][1] + 3
    rows.append((gap, gap + 1, gap - 0.5, gap + 0.5))                      # day 41 gaps over day 40's high
    rows.append((gap, gap + 1, gap - 0.5, gap + 0.5))
    df = daily_frame(rows)
    cands = mrb.candidates('X', df, date(2020, 1, 1), date(2030, 1, 1), timeframe='day', **ANY_RSI)
    by_signal = {c['signal_period']: c for c in cands}
    c = by_signal[df.index[39].date().isoformat()]
    assert c['entry_date'] == df.index[40].date().isoformat() and c['entry_price'] == gap
    c = by_signal[df.index[38].date().isoformat()]
    assert c['entry_price'] == rows[38][1]                                  # filled at the level, not the open


def test_day_timeframe_exit_is_a_close_under_the_ema_then_its_low_broken():
    rows = [(100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(40)]
    rows.append((139, 139.5, 110, 111))      # closes far under the 20-day EMA, low 110
    rows.append((112, 113, 111, 112))        # holds above 110 (and re-arms at 111: also under the EMA)
    rows.append((112, 112.5, 109, 110))      # breaks 111
    df = daily_frame(rows)
    cands = mrb.candidates('X', df, date(2020, 1, 1), date(2030, 1, 1), timeframe='day', **ANY_RSI)
    c = next(c for c in cands if c['signal_period'] == df.index[30].date().isoformat())
    assert c['exit_reason'] == 'Below 20 EMA, low broken'
    assert c['exit_date'] == df.index[42].date().isoformat() and c['exit_price'] == 111


def test_week_candles_are_labelled_by_their_monday_and_match_niftys_keys():
    rows = [(100 + i / 5, 101 + i / 5, 99 + i / 5, 100.5 + i / 5) for i in range(200)]
    df = daily_frame(rows, start='2020-01-06')                             # a Monday
    w = mrb.period_bars(df, 'week')
    assert len(w) == 40 and w['first'].iloc[1] == 5
    cands = mrb.candidates('X', df, date(2020, 1, 1), date(2030, 1, 1), timeframe='week', **ANY_RSI)
    assert cands and all(date.fromisoformat(c['signal_period']).weekday() == 0 for c in cands)
    nifty = mrb.nifty_rsi_by_period(df, 'week')
    assert all(c['signal_period'] in nifty for c in cands)


def test_one_lakh_is_never_overspent():
    """₹1 L and nothing added: across a crowd of overlapping breakouts cash never goes below zero,
    and what is held never cost more than the pot had grown to."""
    rng = np.random.default_rng(7)
    cands = []
    for i in range(400):
        d0 = pd.Timestamp('2018-01-01') + pd.Timedelta(days=int(rng.integers(0, 2500)))
        d1 = d0 + pd.Timedelta(days=int(rng.integers(5, 900)))
        px = float(rng.uniform(20, 3000))
        cands.append(_cand(f'S{i % 150}', d0.date().isoformat(), min(d1, pd.Timestamp('2025-12-31')).date().isoformat(),
                           px=px, stop=px * float(rng.uniform(0.6, 0.97)), exit_px=px * float(rng.uniform(0.5, 3)),
                           month=d0.strftime('%Y-%m'), turnover=float(rng.uniform(1, 50))))
    r = mrb.simulate(cands, {}, date(2018, 1, 1), date(2025, 12, 31), capital=100_000, nifty_floor=0, max_positions=0)
    assert r['trades'] and r['min_cash'] >= 0
    assert r['skipped']['cash'] > 0                     # the pot, not the signal list, decides
    for t in r['trades']:
        assert t['invested'] <= 100_000 + sum(x['pnl_rupees'] for x in r['trades']
                                              if x['result'] != 'OPEN' and x['exit_date'] <= t['entry_date']) + 1e-6


def test_close_exit_rule_leaves_at_the_next_candles_open():
    months, p = uptrend(30)
    ema_now = mrb.ema(mrb.monthly_bars(build(months))['close'].to_numpy())[-1]
    months.append([(p, p, ema_now - 5, ema_now - 2)] * 3)      # closes under the EMA
    months.append([(ema_now - 1, ema_now, ema_now - 3, ema_now - 1)] * 3)
    cands = mrb.candidates('X', build(months), date(2018, 1, 1), date(2030, 1, 1), exit_rule='close', **ANY_RSI)
    c = next(c for c in cands if c['signal_period'] == '2019-08')
    assert c['exit_reason'] == 'Closed below EMA'
    assert c['exit_date'] == '2020-08-02' and c['exit_price'] == pytest.approx(ema_now - 1, abs=0.01)


def test_a_shorter_ema_hugs_price_closer():
    months, _ = uptrend(30)
    df = build(months)
    m10, m20 = mrb.period_bars(df, 'month', 10), mrb.period_bars(df, 'month', 20)
    assert (m10['ema'].iloc[-1] > m20['ema'].iloc[-1])           # rising prices: the 10 EMA sits higher
    c10 = mrb.candidates('X', df, date(2018, 1, 1), date(2030, 1, 1), ema_len=10, **ANY_RSI)
    assert c10 and all(c['stop'] > 0 for c in c10)


def test_nifty_trend_filter_blocks_new_buys_only():
    cands = [_cand('A', '2020-02-03', '2020-09-01', month='2020-01'),
             _cand('B', '2020-03-03', '2020-09-01', month='2020-02')]
    r = mrb.simulate(cands, {}, date(2020, 1, 1), date(2020, 12, 31), capital=1_000_000, risk_pct=1,
                     nifty_floor=0, cost_pct=0, brokerage=0, nifty_above_ema={'2020-01': True, '2020-02': False})
    assert [t['symbol'] for t in r['trades']] == ['A'] and r['skipped']['nifty'] == 1


def test_page_defaults_are_the_swept_ones():
    d = mrb.DEFAULTS
    assert (d['universe'], d['rsi_lo'], d['rsi_hi'], d['ema_len'], d['risk_pct'], d['capital']) == ('fno', 65, 75, 10, 0.5, 100_000)


# ── Find Best Params ─────────────────────────────────────────────────────────

def test_sweep_combos_covers_the_whole_grid():
    combos = mrb.sweep_combos()
    assert len(combos) == (len(mrb.SWEEP_GRID['rsi_band']) * len(mrb.SWEEP_GRID['ema_len'])
                           * len(mrb.SWEEP_GRID['exit_rule']) * len(mrb.SWEEP_GRID['risk_pct'])
                           * len(mrb.SWEEP_GRID['max_new']) * len(mrb.SWEEP_GRID['nifty_floor']))
    assert combos[0]['rsi_lo'] < combos[0]['rsi_hi']
    assert len({(c['rsi_lo'], c['rsi_hi'], c['ema_len'], c['exit_rule'], c['risk_pct'],
                 c['max_new'], c['nifty_floor']) for c in combos}) == len(combos)


def test_in_band_slices_a_wide_candidate_list_by_the_raw_rsi():
    months, p = uptrend(30)
    df = build(months)
    lo, hi = mrb.wide_band()
    cands = mrb.candidates('X', df, date(2018, 1, 1), date(2030, 1, 1), rsi_lo=lo, rsi_hi=hi,
                           min_turnover_cr=0, keep_raw=True)
    assert all('_rsi' in c for c in cands)
    narrow = mrb.in_band(cands, 65, 75)
    assert narrow == [c for c in cands if 65 <= c['_rsi'] <= 75]
    assert len(narrow) <= len(cands)


def test_sweep_row_matches_a_direct_simulate_of_its_own_params():
    """A sweep row (no capital nudge, path 0) is exactly simulate()+summarise() of its own combo —
    the whole point of slicing one wide candidate list by band rather than re-scanning per combo."""
    months, p = uptrend(40)
    df = build(months)
    start, end = date(2018, 1, 1), date(2030, 1, 1)
    grid = {'rsi_band': [(60, 65)], 'ema_len': [20], 'exit_rule': ['low_break'],
            'risk_pct': [1.0], 'max_new': [5], 'nifty_floor': [0.0]}
    lo, hi = mrb.wide_band(grid)
    wide = mrb.candidates('X', df, start, end, rsi_lo=lo, rsi_hi=hi, min_turnover_cr=0,
                          ema_len=20, exit_rule='low_break', keep_raw=True)
    cand_sets = {(20, 'low_break'): wide}
    closes = {'X': mrb.month_closes(df)}
    rows = mrb.sweep(cand_sets, closes, start, end, grid=grid, paths=(0.0,))
    assert len(rows) == 1
    direct = mrb.simulate(mrb.in_band(wide, 60, 65), closes, start, end, risk_pct=1.0, max_new=5, nifty_floor=0.0)
    expected = mrb.score(direct, start, end)
    row = rows[0]
    for k in expected:
        assert row[k] == pytest.approx(expected[k], abs=0.01)


def test_board_filters_min_trades_and_keeps_each_ranking_lens():
    rows = [
        {'total_trades': 2, 'cagr_pct': 90.0, 'max_drawdown_pct': 5.0, 'first_half_cagr': 10.0, 'second_half_cagr': 90.0},
        {'total_trades': 20, 'cagr_pct': 15.0, 'max_drawdown_pct': 50.0, 'first_half_cagr': 12.0, 'second_half_cagr': 18.0},
        {'total_trades': 15, 'cagr_pct': 10.0, 'max_drawdown_pct': 5.0, 'first_half_cagr': 9.0, 'second_half_cagr': 11.0},
    ]
    b = mrb.board(rows, min_trades=10, size=2)
    assert len(b) == 2                       # the 2-trade row is dropped by min_trades regardless of its CAGR
    assert b[0]['cagr_pct'] == 15.0           # sorted best CAGR first
    assert all('cagr_dd' in r and 'worse_half_cagr' in r for r in b)


def test_optimise_route_runs_the_sweep_end_to_end(monkeypatch):
    """The optimise POST + status poll, on fake files: no broker, no create_app()."""
    import time
    from route_app import build_route_app
    from trading_app.app.routes import api as api_mod
    months, p = uptrend(30)
    files = {'AAA': {'df': build(months)}, 'BBB': {'df': build(months)}}
    monkeypatch.setattr(api_mod, 'check_auth', lambda: None)
    monkeypatch.setattr(api_mod, '_mrb_universe', lambda kind: ['AAA', 'BBB', 'NOFILE'])
    monkeypatch.setattr('trading_app.filters.candle_store._load_disk', lambda sym: files.get(sym))
    tiny_grid = {'rsi_band': [(0, 100)], 'ema_len': [20], 'exit_rule': ['low_break', 'close'],
                 'risk_pct': [0.5, 1.0], 'max_new': [5], 'nifty_floor': [0.0]}
    monkeypatch.setattr(mrb, 'SWEEP_GRID', tiny_grid)
    monkeypatch.setattr(mrb, 'SWEEP_MIN_TRADES', 0)
    app = build_route_app()
    body = {'start_date': '2019-01-01', 'end_date': '2020-06-30', 'capital': 100000, 'size_mode': 'risk'}
    with app.test_request_context(json=body):
        resp = api_mod.run_monthly_rsi_breakout_optimise.__wrapped__().get_json()
    task_id = resp['task_id']
    for _ in range(400):
        task = api_mod._mrb_tasks[task_id]
        if task['status'] != 'running':
            break
        time.sleep(0.02)
    assert task['status'] == 'complete', task.get('error')
    payload = task['payload']
    assert payload['total_combos_tested'] == 4
    assert payload['symbols_scanned'] == 3 and payload['symbols_no_data'] == 1
    assert payload['best'] is not None
    assert payload['total'] == len(payload['results'])
    with app.test_request_context(json={**body, 'timeframe': 'hour'}):
        resp2 = api_mod.run_monthly_rsi_breakout_optimise.__wrapped__()
    assert resp2[1] == 400


# ── Fixed ₹-per-stock sizing (the default since 2026-11-13, at the user's ask) ──

def test_fixed_size_mode_invests_the_same_amount_in_every_stock():
    cands = [_cand('A', '2020-02-03', '2020-06-01', px=50.0, stop=40.0),
             _cand('B', '2020-02-04', '2020-06-01', px=500.0, stop=400.0)]
    r = mrb.simulate(cands, {}, date(2020, 1, 1), date(2020, 12, 31), capital=1_000_000,
                     size_mode='fixed', fixed_amount=10_000, nifty_floor=0, cost_pct=0, brokerage=0)
    a, b = r['trades']
    assert a['qty'] == 200 and a['invested'] == 10_000          # ₹10,000 / ₹50
    assert b['qty'] == 20 and b['invested'] == 10_000           # ₹10,000 / ₹500 — same ₹, different qty
    assert mrb.DEFAULTS['size_mode'] == 'fixed' and mrb.DEFAULTS['fixed_amount'] == 10_000


def test_fixed_size_mode_is_the_default_and_ignores_risk_pct():
    """risk_pct/max_pos_pct are 'risk'-mode-only: left at defaults, a fixed-mode run must not size on them."""
    cands = [_cand('A', '2020-02-03', '2020-06-01', px=50.0, stop=49.0)]   # a 1-point stop: risk sizing would buy almost nothing
    r = mrb.simulate(cands, {}, date(2020, 1, 1), date(2020, 12, 31), capital=1_000_000,
                     fixed_amount=10_000, nifty_floor=0, cost_pct=0, brokerage=0)
    assert r['trades'][0]['qty'] == 200                          # ₹10,000 / ₹50, not equity * 0.5% / 1


def test_fixed_size_mode_still_skips_a_buy_the_cash_cannot_cover_in_full():
    cands = [_cand('A', '2020-02-03', '2020-06-01', px=100.0)]
    r = mrb.simulate(cands, {}, date(2020, 1, 1), date(2020, 12, 31), capital=5_000,
                     size_mode='fixed', fixed_amount=10_000, nifty_floor=0, cost_pct=0, brokerage=0)
    assert r['trades'] == [] and r['skipped']['cash'] == 1        # ₹5,000 cash cannot pay for a ₹10,000 position


def test_run_route_accepts_size_mode_and_fixed_amount(monkeypatch):
    """The run route's own size_mode/fixed_amount parsing and validation."""
    from trading_app.app.routes import api as api_mod
    months, p = uptrend(30)
    files = {'AAA': {'df': build(months)}}
    monkeypatch.setattr(api_mod, 'check_auth', lambda: None)
    monkeypatch.setattr(api_mod, '_mrb_universe', lambda kind: ['AAA'])
    monkeypatch.setattr('trading_app.filters.candle_store._load_disk', lambda sym: files.get(sym))
    from route_app import build_route_app
    app = build_route_app()
    body = {'start_date': '2019-01-01', 'end_date': '2020-06-30', 'rsi_lo': 0, 'rsi_hi': 100,
            'min_turnover_cr': 0, 'nifty_floor': 0, 'size_mode': 'fixed', 'fixed_amount': 5000}
    with app.test_request_context(json=body):
        resp = api_mod.run_monthly_rsi_breakout_backtest_api.__wrapped__().get_json()
    assert resp['success']
    with app.test_request_context(json={**body, 'size_mode': 'bogus'}):
        resp2 = api_mod.run_monthly_rsi_breakout_backtest_api.__wrapped__()
    assert resp2[1] == 400


# ── Max Amount Needed (added same day, at the user's ask) ────────────────────

def test_max_invested_is_capital_minus_the_deepest_cash_dip():
    """Two ₹10,000 buys at once, then both sold — the pot never needed more than ₹20,000 even
    though capital was ₹1 L, and that gap is what the card should show."""
    cands = [_cand('A', '2020-02-03', '2020-05-01', px=100.0), _cand('B', '2020-02-04', '2020-05-01', px=100.0)]
    r = mrb.simulate(cands, {}, date(2020, 1, 1), date(2020, 12, 31), capital=100_000,
                     size_mode='fixed', fixed_amount=10_000, nifty_floor=0, cost_pct=0, brokerage=0)
    s = mrb.summarise(r, date(2020, 1, 1), date(2020, 12, 31), 2)
    assert r['min_cash'] == pytest.approx(80_000)
    assert s['max_invested'] == pytest.approx(20_000)


def test_max_invested_never_exceeds_capital():
    cands = [_cand(f'S{i}', f'2020-0{i+1}-03', '2020-12-31', px=100.0, reason='Open') for i in range(5)]
    r = mrb.simulate(cands, {}, date(2020, 1, 1), date(2020, 12, 31), capital=100_000,
                     size_mode='fixed', fixed_amount=100_000, nifty_floor=0, cost_pct=0, brokerage=0)
    s = mrb.summarise(r, date(2020, 1, 1), date(2020, 12, 31), 5)
    assert s['max_invested'] <= s['capital']


# ── Compounding (added same day, at the user's ask) ───────────────────────────

def test_compounding_scales_the_fixed_amount_with_realised_balance():
    """A's realised ₹20,000 profit should make B's buy twice the fixed amount —
    compounding reads the closed trade, not the open one."""
    cands = [_cand('A', '2020-02-03', '2020-04-01', px=100.0, exit_px=300.0),   # +₹200/share on a ₹10k buy -> +₹20k
             _cand('B', '2020-05-04', '2020-12-31', px=100.0, reason='Open', exit_px=100.0)]
    r = mrb.simulate(cands, {}, date(2020, 1, 1), date(2020, 12, 31), capital=100_000,
                     size_mode='fixed', fixed_amount=10_000, compounding=True,
                     nifty_floor=0, cost_pct=0, brokerage=0)
    a, b = r['trades']
    assert a['qty'] == 100 and a['invested'] == 10_000          # A buys at the plain ₹10,000 (nothing closed yet)
    # Realised balance when B is bought = 100,000 + (300-100)*100 = 120,000 -> 1.2x the fixed amount.
    assert b['qty'] == 120 and b['invested'] == 12_000


def test_compounding_off_never_changes_the_fixed_amount():
    cands = [_cand('A', '2020-02-03', '2020-04-01', px=100.0, exit_px=300.0),
             _cand('B', '2020-05-04', '2020-12-31', px=100.0, reason='Open', exit_px=100.0)]
    r = mrb.simulate(cands, {}, date(2020, 1, 1), date(2020, 12, 31), capital=100_000,
                     size_mode='fixed', fixed_amount=10_000, compounding=False,
                     nifty_floor=0, cost_pct=0, brokerage=0)
    a, b = r['trades']
    assert a['qty'] == b['qty'] == 100                           # both at the same, un-compounded size


def test_compounding_ignores_an_open_positions_unrealised_gain():
    """B's size must not grow off A's mark-to-market value while A is still held — only a CLOSED
    trade's P&L feeds compounding, exactly like the straddle/option-selling backtests' own rule."""
    cands = [_cand('A', '2020-02-03', '2020-12-31', px=100.0, stop=90.0, reason='Open', exit_px=500.0),
             _cand('B', '2020-03-04', '2020-12-31', px=100.0, reason='Open', exit_px=100.0)]
    r = mrb.simulate(cands, {}, date(2020, 1, 1), date(2020, 12, 31), capital=100_000,
                     size_mode='fixed', fixed_amount=10_000, compounding=True,
                     nifty_floor=0, cost_pct=0, brokerage=0)
    a, b = r['trades']
    assert a['qty'] == 100
    assert b['qty'] == 100                                        # A is still open: no compounding effect yet


def test_compounding_does_not_apply_under_risk_mode():
    """Risk % already scales with mark-to-market equity on its own; compounding=True must not
    double-count that by also scaling the risk-based qty."""
    cands = [_cand('A', '2020-02-03', '2020-04-01', px=100.0, stop=90.0, exit_px=300.0)]
    r1 = mrb.simulate(cands, {}, date(2020, 1, 1), date(2020, 12, 31), capital=1_000_000, size_mode='risk',
                      risk_pct=1, max_pos_pct=100, compounding=True, nifty_floor=0, cost_pct=0, brokerage=0)
    r2 = mrb.simulate(cands, {}, date(2020, 1, 1), date(2020, 12, 31), capital=1_000_000, size_mode='risk',
                      risk_pct=1, max_pos_pct=100, compounding=False, nifty_floor=0, cost_pct=0, brokerage=0)
    assert r1['trades'][0]['qty'] == r2['trades'][0]['qty']


# ── Max concurrent positions + slot-based compounding (added same day) ───────

def test_max_positions_caps_concurrent_holdings_not_monthly_entries():
    """3 stocks all held at once with a cap of 2: the 3rd is skipped for a free slot, even though
    it is well inside the New Stocks a Month cap and the cash is there to pay for it."""
    cands = [_cand('A', '2020-02-03', '2020-12-31', px=100.0, reason='Open'),
             _cand('B', '2020-02-04', '2020-12-31', px=100.0, reason='Open'),
             _cand('C', '2020-02-05', '2020-12-31', px=100.0, reason='Open')]
    r = mrb.simulate(cands, {}, date(2020, 1, 1), date(2020, 12, 31), capital=1_000_000,
                     size_mode='fixed', fixed_amount=10_000, max_new=10, max_positions=2,
                     nifty_floor=0, cost_pct=0, brokerage=0)
    assert [t['symbol'] for t in r['trades']] == ['A', 'B']
    assert r['skipped']['slots'] == 1 and r['skipped']['cash'] == 0


def test_max_positions_zero_is_uncapped():
    cands = [_cand(f'S{i}', f'2020-0{i+1}-03', '2020-12-31', px=100.0, reason='Open') for i in range(5)]
    r = mrb.simulate(cands, {}, date(2020, 1, 1), date(2020, 12, 31), capital=1_000_000,
                     size_mode='fixed', fixed_amount=10_000, max_new=10, max_positions=0,
                     nifty_floor=0, cost_pct=0, brokerage=0)
    assert len(r['trades']) == 5 and r['skipped']['slots'] == 0


def test_compounding_with_a_slot_cap_splits_the_balance_across_the_slots():
    """A closes +₹20,000; with a 2-slot cap the realised balance (₹1,20,000) splits in two, so
    the stock bought into A's freed slot gets ₹60,000 — 'compounding using the other stock' —
    not the plain capital-wide ratio fixed_amount * balance/capital would give."""
    cands = [_cand('A', '2020-02-03', '2020-04-01', px=100.0, exit_px=300.0),
             _cand('B', '2020-02-04', '2020-12-31', px=100.0, reason='Open', exit_px=100.0),
             _cand('C', '2020-05-04', '2020-12-31', px=100.0, reason='Open', exit_px=100.0)]
    r = mrb.simulate(cands, {}, date(2020, 1, 1), date(2020, 12, 31), capital=100_000,
                     size_mode='fixed', fixed_amount=10_000, compounding=True, max_positions=2,
                     max_new=10, nifty_floor=0, cost_pct=0, brokerage=0)
    by_sym = {t['symbol']: t for t in r['trades']}
    assert by_sym['A']['qty'] == 100 and by_sym['A']['invested'] == 10_000     # nothing closed yet
    assert by_sym['B']['qty'] == 100 and by_sym['B']['invested'] == 10_000     # bought same day as A, still 2 free slots
    # By the time C is bought, A has closed (+₹20,000) and B still holds a slot: balance 120,000 / 2 slots = 60,000.
    assert by_sym['C']['qty'] == 600 and by_sym['C']['invested'] == 60_000


def test_compounding_without_a_slot_cap_falls_back_to_the_capital_ratio():
    cands = [_cand('A', '2020-02-03', '2020-04-01', px=100.0, exit_px=300.0),
             _cand('B', '2020-05-04', '2020-12-31', px=100.0, reason='Open', exit_px=100.0)]
    r = mrb.simulate(cands, {}, date(2020, 1, 1), date(2020, 12, 31), capital=100_000,
                     size_mode='fixed', fixed_amount=10_000, compounding=True, max_positions=0,
                     nifty_floor=0, cost_pct=0, brokerage=0)
    b = next(t for t in r['trades'] if t['symbol'] == 'B')
    assert b['qty'] == 120 and b['invested'] == 12_000               # unchanged from before max_positions existed


def test_default_max_positions_is_ten():
    assert mrb.DEFAULTS['max_positions'] == 10
