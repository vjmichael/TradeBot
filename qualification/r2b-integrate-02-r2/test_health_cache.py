import threading
import unittest
from health_cache import LocalHealthCache
from test_health_backend import ACTIVE

class Tests(unittest.TestCase):
    def test_sample(self):
        c=LocalHealthCache(lambda:ACTIVE,clock=lambda:100)
        self.assertIsNone(c.latest());self.assertTrue(c.sample())
        self.assertEqual(c.latest().local.services['vault'],'active')
        self.assertEqual(c.latest().internet,'unknown')
    def test_no_sample_no_green(self):self.assertIsNone(LocalHealthCache(lambda:ACTIVE).latest())
    def test_exception_clears_old(self):
        calls=[ACTIVE,RuntimeError('SECRET_CANARY')]
        def f():
            a=calls.pop(0)
            if isinstance(a,Exception):raise a
            return a
        c=LocalHealthCache(f)
        self.assertTrue(c.sample());self.assertFalse(c.sample());self.assertIsNone(c.latest())
    def test_sample_does_not_block_status_read(self):
        ready=threading.Event();unblock=threading.Event()
        def slow():ready.set();unblock.wait(timeout=3);return ACTIVE
        c=LocalHealthCache(slow,clock=lambda:100)
        t=threading.Thread(target=c.sample);t.start()
        try:
            self.assertTrue(ready.wait(1));self.assertIsNone(c.latest());self.assertFalse(c.sample())
        finally:
            unblock.set();t.join(timeout=1)
        self.assertFalse(t.is_alive());self.assertIsNotNone(c.latest())
    def test_invalid_source_clears(self):
        c=LocalHealthCache(lambda:'SECRET_CANARY');self.assertFalse(c.sample());self.assertIsNone(c.latest())

if __name__=='__main__':unittest.main()