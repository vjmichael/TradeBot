"""Real Tk/Xvfb, no network; demonstrates UI thread remains responsive during probe."""
import time
import tkinter as tk
import unittest
from offline_diagnostics import OfflineDiagnostics
from offline_health import LocalHealth

class Tests(unittest.TestCase):
    def setUp(self):self.root=tk.Tk();self.root.withdraw()
    def tearDown(self):
        try:self.root.destroy()
        except tk.TclError:pass
    def spin(self,seconds):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            self.root.update();time.sleep(.01)
    def test_local_offline_window_responsive(self):
        def slow():time.sleep(.65);return LocalHealth({'dashboard':'active','vault':'active','backend':'active'})
        ui=OfflineDiagnostics(self.root,slow)
        self.assertEqual(ui.services.get(),'Checking services')
        t=time.monotonic();self.spin(.1)
        self.assertLess(time.monotonic()-t,.3)
        self.assertTrue(ui.inflight)
        self.spin(.8)
        self.assertEqual(ui.services.get(),'Local services running')
        self.assertEqual(ui.network.get(),'Internet status unknown')
        ui.close()
    def test_probe_failure_is_safe(self):
        def fail():raise OSError('SECRET_CANARY')
        ui=OfflineDiagnostics(self.root,fail)
        self.spin(.4)
        self.assertEqual(ui.services.get(),'Checking services')
        self.assertNotIn('SECRET_CANARY',ui.detail.get())
        ui.close()
    def test_refresh_not_parallel(self):
        calls=[]
        def slow():calls.append(1);time.sleep(.3);return LocalHealth({'dashboard':'failed'})
        ui=OfflineDiagnostics(self.root,slow)
        ui.refresh();ui.refresh();self.spin(.6)
        self.assertEqual(len(calls),1)
        self.assertEqual(ui.services.get(),'Services need attention')
        ui.close()
if __name__=='__main__':unittest.main()