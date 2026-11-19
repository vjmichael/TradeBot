"""Calibration and evaluation math; actual FreqAI fit exercised in native gate."""
import unittest
from numerical_models import calibrate,calibrated,scores,MemoryUnavailable
try:
    import numpy as np
    import sklearn
except ImportError:
    np=None

@unittest.skipIf(np is None,'native numerical learner unavailable in foundation')
class ModelMathTests(unittest.TestCase):
    def test_calibration_and_metrics(self):
        p=np.linspace(.05,.95,40);y=(np.arange(40)%3!=0).astype(int)
        cal=calibrate(p,y);q=calibrated(p,cal)
        self.assertTrue(np.all((q>0)&(q<1)))
        metrics=scores(y*.01-.005,y,np.zeros(40),p,q)
        self.assertEqual(sum(x['count'] for x in metrics['reliability']),40)
        self.assertGreaterEqual(metrics['calibrated']['brier'],0)
        self.assertEqual(cal,calibrate(p,y))
    def test_insufficient_calibration_and_invalid_values(self):
        with self.assertRaises(MemoryUnavailable):calibrate(np.full(20,.5),np.zeros(20,dtype=int))
        y=np.arange(20)%2;p=np.full(20,np.nan)
        with self.assertRaises(MemoryUnavailable):calibrate(p,y)
