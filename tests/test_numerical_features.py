import unittest
from datetime import datetime,timezone
from numerical_features import Plan,STEP,LOOKBACK,FEATURES,technical,outcome,session,split,model_columns
from numerical_dataset import iter_rows,macro_features
from numerical_features import MACRO

T=1704067200
def plan(mode='provider-retrospective'):
    return Plan(T,T+360*STEP,T+180*STEP,T+270*STEP,6,2,40,10,5,mode).validate()

def observations():
    return [{'ts':T+i*STEP,'available_at':T+(i+1)*STEP,'contributing_available_at':T+(i+1)*STEP,'o':str(100+i*.01),
             'h':str(101+i*.01),'l':str(99+i*.01),'c':str(100+i*.01),
             'v':str(10+i%5),'trades':10+i%9,'digest':'a'*64,'source_ids':['b'*64]} for i in range(360)]

def macro():return {s:[] for s in MACRO}

class NumericalTests(unittest.TestCase):
    def test_feature_warmup_and_future_independence(self):
        rows=observations();decision=T+LOOKBACK*STEP
        a=technical(rows[:LOOKBACK],decision,'local-observed')
        self.assertIsNotNone(a)
        rows[100]['c']='999999'
        self.assertEqual(a,technical(rows[:LOOKBACK],decision,'local-observed'))
        rows[0]['contributing_available_at']=decision+1
        self.assertIsNone(technical(rows[:LOOKBACK],decision,'local-observed'))
        self.assertEqual(a,technical(rows[:LOOKBACK],decision,'provider-retrospective'))
        rows[2]['ts']+=STEP
        with self.assertRaises(ValueError):technical(rows[:LOOKBACK],decision,'provider-retrospective')
    def test_explicit_costs_and_targets(self):
        p=plan();same={'o':'100'}
        self.assertLess(outcome(p,same,same)['&-net_return'],0)
        self.assertEqual(outcome(p,same,same)['&-net_positive'],0)
        self.assertEqual(outcome(p,same,{'o':'120'})['&-net_positive'],1)
    def test_purged_shared_splits_and_delayed_execution(self):
        p=plan();out=list(iter_rows(observations(),macro(),p,'BTC/USD'))
        for fold,row in out:
            self.assertEqual(split(p,row['decision'],row['label_available']),fold)
            self.assertEqual(row['entry_at'],row['decision']+STEP)
            self.assertEqual(row['exit_at'],row['entry_at']+6*STEP)
            self.assertEqual(set(row['features']),set(FEATURES))
        self.assertGreater(sum(f=='train' for f,_ in out),48)
        self.assertGreater(sum(f=='test' for f,_ in out),24)
        self.assertIsNone(split(p,p.train_end-STEP,p.train_end))
        self.assertIsNone(split(p,p.train_end+STEP,p.train_end+10*STEP))
        self.assertEqual(split(p,p.train_end+2*STEP,p.train_end+12*STEP),'validation')
        self.assertFalse(any('session' in x or 'weekday' in x or 'utc_hour' in x or 'weekend' in x for x in model_columns(False,False)))
    def test_vintage_causality_and_staleness(self):
        data=macro();data['UNRATE']=[{'available_after':T,'retrieved_at':T+10*STEP,'value':4.0},
                                   {'available_after':T+20*STEP,'retrieved_at':T+30*STEP,'value':99.0}]
        a,_=macro_features(data,T+STEP,'local-observed')
        b,_=macro_features(data,T+STEP,'provider-retrospective')
        self.assertIsNone(a['%-macro_UNRATE']);self.assertEqual(b['%-macro_UNRATE'],4)
        a,_=macro_features(data,T+15*STEP,'local-observed');self.assertEqual(a['%-macro_UNRATE'],4)
        a,_=macro_features(data,T+100*86400,'provider-retrospective');self.assertIsNone(a['%-macro_UNRATE'])
        self.assertEqual(a['%-macro_UNRATE_missing'],1)
    def test_session_dst_and_weekend(self):
        def ts(s):return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp())
        self.assertEqual(session(ts('2025-01-06T13:00:00'))['%-session_new_york'],0)
        self.assertEqual(session(ts('2025-07-07T13:00:00'))['%-session_new_york'],1)
        self.assertEqual(session(ts('2025-07-06T13:00:00'))['%-weekend'],1)
        self.assertEqual(session(ts('2025-07-06T13:00:00'))['%-session_new_york'],0)
    def test_local_late_label_is_purged(self):
        p=plan('local-observed');rows=observations()
        rows[172]['contributing_available_at']=p.train_end+STEP
        out=list(iter_rows(rows,macro(),p,'BTC/USD'))
        self.assertFalse(any(f=='train' and (r['exit_at']==rows[172]['ts'] or r['entry_at']==rows[172]['ts']) for f,r in out))
