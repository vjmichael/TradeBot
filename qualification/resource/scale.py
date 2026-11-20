"""Offline historical SQLite scale fixture. Run with PYTHONPATH pointing at a verified Maj Vaj source tree.
No real market data, credentials, network operations, or order authority."""
import tempfile,time,json,resource,os
from pathlib import Path
from market_memory import MarketMemory,sha256_file,session_profile
N=43800; start=1577836800; end=start+N*3600; now=end+3600
with tempfile.TemporaryDirectory(prefix='resource-probe-') as tmp:
 root=Path(tmp); data=root/'data'; data.mkdir(mode=0o700); m=MarketMemory(data)
 for pair,filename in [('BTC/USD','XBTUSD_60.csv'),('ETH/USD','ETHUSD_60.csv'),('SOL/USD','SOLUSD_60.csv')]:
  p=root/filename
  with p.open('w') as f:
   for i in range(N): f.write(f'{start+i*3600},100,103,99,{100+i%3},2,4\n')
  t=time.monotonic(); r=m.import_archive(p,pair,60,sha256_file(p),fixture=True,now=now); assert r['state']=='complete' and r['cursor']==N
  ins=time.monotonic()-t; t=time.monotonic(); x=m.query(pair,60,start,end,now,fixture=True); assert x['coverage']==N and x['conflicts']==0; profile=session_profile(x); assert sum(b['samples'] for b in profile['bins'])==N
  dur=time.monotonic()-t; del x,profile
  print(json.dumps({'pair':pair,'synthetic_rows':N,'import_s':round(ins,3),'query_profile_s':round(dur,3),'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'data_allocated_bytes':sum(p.stat().st_blocks*512 for p in data.rglob('*') if p.is_file())}),flush=True)
 assert m.query('BTC/USD',60,start,end,now)['coverage']==0
