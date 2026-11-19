"""Developer host-service entry point. Explicit opt-in only; run under service custody."""
import argparse
import json
import signal
import sys
import threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from data_scheduler import Schedule

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory',type=Path);p.add_argument('action',choices=('enable','disable','status','run'));a=p.parse_args();s=Schedule(a.directory)
    try:
        if a.action=='run':
            stop=threading.Event()
            for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
            with s.lease():
                while not stop.is_set():s.tick();stop.wait(2)
        else:print(json.dumps(s.status() if a.action=='status' else s.enable(a.action=='enable'),sort_keys=True))
    finally:s.close()
if __name__=='__main__':main()
