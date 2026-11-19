"""Public REST rollover handling; actual parser with explicit HTTP fixtures."""
import unittest
import httpx
from market_stream import exact_closed, qualification_closed, FormingBoundaryUnavailable

T=1704067200
PAIR='BTC/USD'


def candle(t):return [t,'100','100','100','100','100','2.000000000001',2]


class RolloverTests(unittest.TestCase):
    def fetch(self, responses):
        calls=[];sleep=[];pending=iter(responses)
        def get(pair,interval):
            now,raw=next(pending);calls.append(now)
            with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json={'error':[],'result':{'XXBTZUSD':raw,'last':T}}))) as client:
                return exact_closed(pair,interval,client=client,now=now)
        return get,calls,sleep

    def test_rollover_retries_fresh_response_without_promoting_forming(self):
        old=[candle(T-600),candle(T-300)];new=[*old,candle(T)]
        get,calls,sleep=self.fetch([(T+1,old),(T+3,new)])
        a=qualification_closed(PAIR,5,fetch=get,sleep=sleep.append)
        self.assertEqual(calls,[T+1,T+3]);self.assertEqual(sleep,[2])
        self.assertEqual(a['observed_at'],T+3)
        self.assertEqual([r[0] for r in a['candles']],[(T-600)*1000,(T-300)*1000])
        self.assertEqual(a['candles'][-1][5],'2.000000000001')

    def test_exhaustion_remains_unavailable(self):
        old=[candle(T-600),candle(T-300)]
        get,calls,sleep=self.fetch([(T+i,old) for i in (1,3,7,15)])
        with self.assertRaises(FormingBoundaryUnavailable):qualification_closed(PAIR,5,fetch=get,sleep=sleep.append)
        self.assertEqual(len(calls),4);self.assertEqual(sleep,[2,4,8])

    def test_stale_data_outside_boundary_never_retried(self):
        for now,rows in [(T+31,[candle(T-600),candle(T-300)]),(T+1,[candle(T-900),candle(T-600)])]:
            get,calls,sleep=self.fetch([(now,rows)])
            with self.assertRaises(ValueError) as cm:qualification_closed(PAIR,5,fetch=get,sleep=sleep.append)
            self.assertNotIsInstance(cm.exception,FormingBoundaryUnavailable)
            self.assertEqual(len(calls),1);self.assertEqual(sleep,[])

    def test_malformed_and_future_never_retried(self):
        for rows in [[candle(T-600),candle(T-300)[:3]],[candle(T-300),candle(T+300)],[candle(T-600),candle(T-300),candle(T-300)]]:
            get,calls,sleep=self.fetch([(T+1,rows)])
            with self.assertRaises(ValueError) as cm:qualification_closed(PAIR,5,fetch=get,sleep=sleep.append)
            self.assertNotIsInstance(cm.exception,FormingBoundaryUnavailable)
            self.assertEqual(len(calls),1);self.assertEqual(sleep,[])

    def test_valid_first_response_no_retry(self):
        get,calls,sleep=self.fetch([(T+1,[candle(T-300),candle(T)])])
        self.assertEqual(qualification_closed(PAIR,5,fetch=get,sleep=sleep.append)['candles'][0][0],(T-300)*1000)
        self.assertEqual(len(calls),1);self.assertEqual(sleep,[])
