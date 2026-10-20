"""Kite WebSocket quote feed: tick cache, staleness and the REST fallback."""
import time

from trading_app.service import kite_ticker_feed as kf


class FakeTicker:
    MODE_QUOTE = 'quote'

    def __init__(self, api_key, token):
        self.subs, self.modes, self.connected = [], [], False

    def connect(self, threaded=False):
        self.connected = True

    def close(self):
        self.connected = False

    def subscribe(self, toks):
        self.subs.append(list(toks))

    def set_mode(self, mode, toks):
        self.modes.append((mode, list(toks)))


def _feed():
    return kf.KiteTickerFeed('k', 't', ticker_factory=FakeTicker)


def _tick(tok, ltp, hi=None, lo=None):
    return {'instrument_token': tok, 'last_price': ltp, 'ohlc': {'high': hi, 'low': lo}}


def test_tick_is_readable_with_session_range():
    f = _feed()
    f.ensure([11])
    f._on_ticks(None, [_tick(11, 101.5, 120, 90)])
    assert f.get(11) == {'last_price': 101.5, 'high': 120, 'low': 90}


def test_unticked_or_stale_token_is_none():
    f = _feed()
    f.ensure([11])
    assert f.get(11) is None
    f._on_ticks(None, [_tick(11, 100)])
    f._ticks[11]['ts'] = time.monotonic() - 60
    assert f.get(11) is None


def test_subscribes_once_when_connected_and_resubscribes_on_reconnect():
    f = _feed()
    f.ensure([11])                      # starts the socket, not yet connected
    ws = f._ticker
    f._on_connect(ws)
    assert ws.subs == [[11]] and ws.modes == [('quote', [11])]
    f.ensure([11, 12])                  # only the new token goes out
    assert ws.subs[-1] == [12]
    f._on_connect(ws)                   # reconnect: everything again
    assert ws.subs[-1] == [11, 12]


def test_disabled_flag_returns_none(monkeypatch):
    monkeypatch.delenv('KITE_WS_QUOTES', raising=False)

    class K: api_key, access_token = 'k', 'tok-off'
    assert kf.ws_quote(K(), 11) is None
    assert 'tok-off' not in kf._feeds   # not even a socket


def test_enabled_without_tick_falls_back_and_never_raises(monkeypatch):
    monkeypatch.setenv('KITE_WS_QUOTES', 'true')
    monkeypatch.setattr(kf.KiteTickerFeed, '_make_ticker',
                        lambda self: FakeTicker('k', 't'))
    kf._feeds.clear()

    class K: api_key, access_token = 'k', 'tok-on'
    assert kf.ws_quote(K(), 11) is None           # subscribed, no tick yet → REST
    feed = kf._feeds['tok-on']
    feed._on_ticks(None, [_tick(11, 77, 80, 70)])
    assert kf.ws_quote(K(), 11)['last_price'] == 77
    kf._feeds.clear()


def test_no_credentials_means_no_feed(monkeypatch):
    monkeypatch.setenv('KITE_WS_QUOTES', 'true')

    class K: api_key, access_token = None, None
    assert kf.ws_quote(K(), 11) is None


def test_new_login_replaces_old_socket(monkeypatch):
    kf._feeds.clear()
    stopped = []
    monkeypatch.setattr(kf.KiteTickerFeed, 'stop', lambda self: stopped.append(self))

    class A: api_key, access_token = 'k', 'a'
    class B: api_key, access_token = 'k', 'b'
    fa = kf.get_feed(A())
    kf.get_feed(B())
    assert stopped == [fa] and list(kf._feeds) == ['b']
    kf._feeds.clear()
