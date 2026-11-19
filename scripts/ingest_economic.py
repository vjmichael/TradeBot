"""Developer-only public evidence refresh. No service or trading activation."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from economic_memory import EconomicMemory,SOURCES


def main():
    p=argparse.ArgumentParser();p.add_argument('--data-dir',type=Path,required=True);p.add_argument('source',choices=(*SOURCES,'all','status'));a=p.parse_args();m=EconomicMemory(a.data_dir)
    if a.source=='status':
        print(json.dumps({s:{k:v for k,v in m.view(s,limit=1).items() if k!='rows'} for s in SOURCES}));return
    failed=False
    for s in SOURCES if a.source=='all' else (a.source,):
        try: print(json.dumps({'feed':s,**m.collect(s)}),flush=True)
        except Exception as exc:
            # Do not echo arbitrary upstream content or local paths.
            print(json.dumps({'feed':s,'state':'unavailable','error_type':type(exc).__name__}),flush=True);failed=True
    if failed:raise SystemExit(1)

if __name__=='__main__':main()
