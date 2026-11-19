import unittest
from strategy_replay import Costs,replay,signals,aggregate,STEP,metrics
T=1704067200
class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.times=[T+i*STEP for i in range(20)]
        self.prices={t:'100' for t in self.times}
        self.decisions=self.times[:10]
    def test_cash_never_trades(self):
        r=replay(self.prices,self.decisions,dict.fromkeys(self.decisions,False),3,Costs(40,10,5),10000)
        self.assertEqual(r['final'],100);self.assertEqual(r['fees'],0)
        self.assertFalse(r['trades'])
    def test_delayed_entry_nonoverlap_and_cost_reconciliation(self):
        r=replay(self.prices,self.decisions,dict.fromkeys(self.decisions,True),3,Costs(40,10,5),10000)
        self.assertGreater(r['blocked_signals'],0)
        for a,b in zip(r['trades'],r['trades'][1:]):self.assertLessEqual(a['exit_at'],b['entry_at'])
        for t in r['trades']:
            self.assertEqual(t['entry_at'],t['decision']+STEP)
            self.assertEqual(t['exit_at'],t['entry_at']+3*STEP)
        self.assertAlmostEqual(100-r['final'],r['fees']+r['friction'],places=10)
        self.assertGreater(r['fees'],0)
    def test_drawdown_marks_and_buy_hold(self):
        self.prices[self.times[2]]='50'
        take=dict.fromkeys(self.decisions,False);take[self.times[0]]=True
        r=replay(self.prices,self.decisions,take,3,Costs(0,0,0),10000,True)
        self.assertEqual(len(r['trades']),1);self.assertEqual(r['final'],100)
        _,m=aggregate([r,r]);self.assertAlmostEqual(m['max_hourly_liquidation_drawdown'],.5)
        self.assertEqual(m['final_equity'],200)
    def test_deterministic_signals_and_sensitivity(self):
        take=signals('random','BTC/USD',self.decisions,{},{});self.assertEqual(take,signals('random','BTC/USD',self.decisions,{},{}))
        results=[replay(self.prices,self.decisions,take,3,Costs(f,10,5),10000) for f in (0,40,80)]
        self.assertEqual([t['entry_at'] for t in results[0]['trades']],[t['entry_at'] for t in results[-1]['trades']])
        self.assertGreater(results[0]['final'],results[-1]['final'])
    def test_missing_marks_and_mismatched_clock_refused(self):
        take=dict.fromkeys(self.decisions,False)
        a=replay(self.prices,self.decisions,take,3,Costs(0,0,0),10000)
        b=replay(self.prices,self.decisions[1:],{t:False for t in self.decisions[1:]},3,Costs(0,0,0),10000)
        with self.assertRaises(ValueError):aggregate([a,b])
        del self.prices[self.times[3]]
        with self.assertRaises(ValueError):replay(self.prices,self.decisions,take,3,Costs(0,0,0),10000)
    def test_model_signal_ignores_outcome(self):
        p={t:{'predicted_net_return':.01,'calibrated_positive_probability':.6,'actual_scenario_return':-1} for t in self.decisions}
        a=signals('numerical_only','BTC/USD',self.decisions,{}, {'numerical_only':p})
        for r in p.values():r['actual_scenario_return']=1
        self.assertEqual(a,signals('numerical_only','BTC/USD',self.decisions,{}, {'numerical_only':p}))
