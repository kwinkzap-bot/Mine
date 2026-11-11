"""breeze_connect's history GET goes through one pooled session with a timeout; nothing else moves."""
from trading_app.service import icici_data_service as ids


class _FakeSession:
    def __init__(self):
        self.calls = []
        self.adapters = {}

    def mount(self, prefix, adapter):
        self.adapters[prefix] = adapter

    def get(self, url, *args, **kwargs):
        self.calls.append((url, kwargs))
        return 'pooled'


class _FakeRequests:
    def __init__(self):
        self.calls = []
        self.sessions = []
        self.marker = 'requests-attr'

    def Session(self):
        s = _FakeSession()
        self.sessions.append(s)
        return s

    def get(self, url=None, *args, **kwargs):
        self.calls.append(('get', url, kwargs))
        return 'direct'

    def post(self, url=None, *args, **kwargs):
        self.calls.append(('post', url, kwargs))
        return 'direct'


HIST = 'https://breezeapi.icicidirect.com/api/v2/historicalcharts'


def test_history_get_uses_one_pooled_session_with_a_timeout():
    real = _FakeRequests()
    t = ids._HistoryTransport(real)
    assert t.get(url=HIST, params={'a': 1}, headers={'h': 2}) == 'pooled'
    assert t.get(url=HIST, params={'a': 2}, headers={'h': 2}) == 'pooled'
    assert len(real.sessions) == 1                     # one keep-alive pool, not a connection a call
    s = real.sessions[0]
    assert [kw['timeout'] for _, kw in s.calls] == [ids.HISTORY_TIMEOUT] * 2
    assert s.calls[0][1]['params'] == {'a': 1}
    retry = s.adapters['https://'].max_retries
    assert retry.connect == 2 and retry.read == 0      # a GET that never connected is resent; a slow answer is not
    assert real.calls == []


def test_every_other_call_keeps_the_sdks_own_path():
    real = _FakeRequests()
    t = ids._HistoryTransport(real)
    assert t.get(url='https://api.icicidirect.com/breezeapi/api/v1/customerdetails') == 'direct'
    assert t.post(url='https://api.icicidirect.com/breezeapi/api/v1/order', data='{}') == 'direct'
    assert t.marker == 'requests-attr'
    assert [c[0] for c in real.calls] == ['get', 'post']
    assert 'timeout' not in real.calls[0][2]            # orders and the rest are not given a timeout here
    assert real.sessions == []
