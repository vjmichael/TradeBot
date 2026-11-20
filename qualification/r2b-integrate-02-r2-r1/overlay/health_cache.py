"""Lightweight observer handoff for existing supervised work cycle.

No service/daemon, loop or network probing is started here. The existing
scheduler can run sample() off the request/UI path; latest() is nonblocking.
"""
import threading
import time
from health_endpoint import StatusSources

class LocalHealthCache:
    def __init__(self,probe,clock=time.monotonic):
        if not callable(probe) or not callable(clock): raise ValueError('invalid health collector')
        self._probe,self._clock=probe,clock
        self._lock=threading.Lock()
        self._sample=None
        self._sampling=False
        self._requested_at=None

    def sample(self):
        """Call from the existing scheduler's worker, not a dashboard request."""
        with self._lock:
            if self._sampling:return False
            self._sampling=True
        try:
            local=self._probe()
            if not hasattr(local,'services') or type(local.services) is not dict:
                raise ValueError('health probe unavailable')
            record=StatusSources(local,{},'unknown',self._clock())
            with self._lock:self._sample=record
            return True
        except Exception:
            with self._lock:self._sample=None
            return False
        finally:
            with self._lock:self._sampling=False

    def ensure_fresh(self, min_interval=15):
        """Demand-triggered, single bounded worker; no periodic daemon.
        Only the local authorized status route/supervisor may invoke it.
        """
        if type(min_interval) not in (int,float) or min_interval<1:
            raise ValueError('invalid sampling budget')
        with self._lock:
            now=self._clock()
            if self._sampling or (self._requested_at is not None and now-self._requested_at < min_interval):
                return False
            self._requested_at=now
            # Claim pending before the worker is scheduled to reject races.
            self._sampling=True
        def worker():
            try:
                try:
                    local=self._probe()
                    if not hasattr(local,'services') or type(local.services) is not dict:
                        raise ValueError('health probe unavailable')
                    record=StatusSources(local,{},'unknown',self._clock())
                except Exception:
                    record=None
                with self._lock:self._sample=record
            finally:
                with self._lock:self._sampling=False
        threading.Thread(target=worker,daemon=True,name='maj-vaj-nonsecret-health').start()
        return True

    def latest(self):
        with self._lock:return self._sample