"""Receipt/custody regression using deterministic doubles; native gate uses real data."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack
from strategy_comparison import compare,verify_comparison,MemoryUnavailable
from numerical_features import Plan,STEP,outcome
from numerical_dataset import QUALIFICATION
from training_handoff import canonical

T=1704067200
PAIR='BTC/USD'
def put(path,content):
    path.write_bytes(content);return {'sha256':hashlib.sha256(content).hexdigest(),'bytes':len(content)}
def obj(path,value):return put(path,canonical(value).encode())['sha256']
def records(path,rows):return put(path,''.join(canonical(r)+'\n' for r in rows).encode())

class ComparisonTests(unittest.TestCase):
    def test_receipts_costs_reproduction_and_tamper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dataset=root/'dataset';models=root/'models';snapshot=root/'snapshot'
            for p in (dataset,models,snapshot):p.mkdir(mode=0o700)
            plan=Plan(T,T+360*STEP,T+180*STEP,T+270*STEP,6,2,40,10,5,'provider-retrospective')
            data_sha='a'*64;source_sha='b'*64;cut=plan.end+STEP
            decisions=[T+i*STEP for i in range(272,352)]
            raw=[{'ts':T+i*STEP,'o':str(100+i/10)} for i in range(360)]
            by_time={r['ts']:r for r in raw}
            rows=[{'decision':t,'features':{'%-return_6h':.01,'%-return_24h':.02},
                   'targets':outcome(plan,by_time[t+STEP],by_time[t+7*STEP])} for t in decisions]
            entry=records(dataset/'BTC_USD-test.jsonl',rows)|{'name':'BTC_USD-test.jsonl'}
            manifest={'plan':plan.as_dict(),'snapshot_sha256':source_sha,'snapshot_cutoff':cut,
                'markets':[{'pair':PAIR,'files':{'test':entry},'counts':{'test':80}}]}
            refs={PAIR:{}}
            for cohort in ('numerical_only','no_sessions'):
                stem='BTC_USD-'+cohort;m=models/stem;e=models/(stem+'-test')
                m.mkdir();e.mkdir()
                model={'kind':'maj-vaj-frozen-numerical-model','dataset_sha256':data_sha,'pair':PAIR,'cohort':cohort,
                       'final_test_evaluated':False,'artifacts':{name:put(m/name,b'tree\nsimulated receipt fixture\n') for name in ('regressor.txt','classifier.txt')},**QUALIFICATION}
                md=obj(m/'manifest.json',model)
                predicted=[{'decision':r['decision'],'actual_scenario_return':r['targets']['&-net_return'],
                         'actual_positive':r['targets']['&-net_positive'],'predicted_net_return':.01,
                         'raw_positive_probability':.6,'calibrated_positive_probability':.7} for r in rows]
                evaluation={'kind':'maj-vaj-frozen-test-evaluation','dataset_sha256':data_sha,'model_sha256':md,
                      'pair':PAIR,'cohort':cohort,'evaluation_only':True,'metrics':{'rows':80},'first_decision':decisions[0],
                      'last_decision':decisions[-1],'predictions':records(e/'predictions.jsonl',predicted),**QUALIFICATION}
                refs[PAIR][cohort]={'model_sha256':md,'evaluation_sha256':obj(e/'manifest.json',evaluation)}
            with ExitStack() as stack:
                for name,value in (('verify',manifest),('reproduce',{'reproduced':True}),('restore',{'cutoff':cut}),('checked_rows',raw),('MarketMemory',None)):
                    stack.enter_context(patch('strategy_comparison.'+name,return_value=value))
                args=(snapshot,dataset,data_sha,models,refs)
                result=compare(*args,root/'report',3000000)
                self.assertEqual(result['common_decisions'],80)
                self.assertEqual(result['results']['base']['cash']['portfolio']['final_equity'],30000)
                self.assertEqual(result['results']['base']['numerical_only'],result['results']['base']['no_sessions'])
                self.assertGreater(result['results']['zero']['momentum']['portfolio']['final_equity'],result['results']['double']['momentum']['portfolio']['final_equity'])
                self.assertEqual(verify_comparison(root/'report',result['manifest_sha256'],*args,3000000),result)
                with self.assertRaises(MemoryUnavailable):compare(*args,root/'report',3000000)
                (root/'report/base-cash-equity.jsonl').write_text('tampered')
                with self.assertRaises(MemoryUnavailable):verify_comparison(root/'report',result['manifest_sha256'],*args,3000000)
                (models/'BTC_USD-no_sessions-test/predictions.jsonl').write_text('tampered')
                with self.assertRaises(MemoryUnavailable):compare(*args,root/'bad',3000000)
                self.assertFalse((root/'bad').exists())
