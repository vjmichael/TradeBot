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

    def latest(self):
        with self._lock:return self._sample