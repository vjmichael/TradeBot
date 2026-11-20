import unittest
from unittest.mock import Mock
from fastapi import FastAPI,Header,HTTPException
from fastapi.testclient import TestClient
from health_endpoint import attach_connection_health,StatusSources
from health_backend import Witness
from test_health_backend import Local, ACTIVE, W

class Tests(unittest.TestCase):
    def make(self,local=None,ready=None,clock=lambda:100):
        app=FastAPI()
        def authorize(authorization:str|None=Header(default=None)):
            if authorization!='Bearer SYNTHETIC-SESSION':raise HTTPException(status_code=401,detail='Unauthorized')
        def l():return local if local is not None else StatusSources(ACTIVE,W,'online',100)
        attach_connection_health(app,authorize,l,ready,clock)
        return TestClient(app)
    def fetch(self,c):return c.get('/api/connection-health',headers={'Authorization':'Bearer SYNTHETIC-SESSION'})
    def test_enforces_bearer(self):self.assertEqual(self.make().get('/api/connection-health').status_code,401)
    def test_bad_bearer(self):self.assertEqual(self.make().get('/api/connection-health',headers={'Authorization':'Bearer BAD'}).status_code,401)
    def test_cache_disabled(self):self.assertEqual(self.fetch(self.make()).headers['cache-control'],'no-store')
    def test_real_local_only_is_never_green(self):self.assertNotIn('ready',self.fetch(self.make()).json()['health']['services'].values())
    def test_ready_with_independent_witness(self):
        c=self.make(ready=lambda:W);self.assertEqual(set(self.fetch(c).json()['health']['services'].values()),{'ready'})
    def test_stale_cache_refused(self):
        c=self.make(local=StatusSources(ACTIVE,W,'online',40),ready=lambda:W)
        self.assertEqual(set(self.fetch(c).json()['health']['services'].values()),{'unknown'})
    def test_broken_source_sanitized(self):
        def crash():raise RuntimeError('SECRET_CANARY')
        app=FastAPI();attach_connection_health(app,lambda:None,crash)
        result=TestClient(app).get('/api/connection-health')
        self.assertNotIn('SECRET_CANARY',result.text);self.assertIn('unknown',result.text)
    def test_broken_witness_sanitized(self):
        c=self.make(ready=lambda: (_ for _ in ()).throw(RuntimeError('SECRET_CANARY')))
        self.assertNotIn('SECRET_CANARY',self.fetch(c).text)
    def test_failed_vault_prevents_ready(self):
        c=self.make(local=StatusSources(Local({**ACTIVE.services,'vault':'failed'}),W,'online',100),ready=lambda:W)
        self.assertEqual(self.fetch(c).json()['health']['services']['vault'],'failed')
    def test_route_missing_no_false_online(self):
        c=self.make(local=StatusSources(ACTIVE,W,'no_network_route',100),ready=lambda:W)
        self.assertEqual(self.fetch(c).json()['health']['internet'],'no_network_route')
    def test_stale_witness(self):
        c=self.make(ready=lambda:{**W,'vault':Witness('ready',10)})
        self.assertEqual(self.fetch(c).json()['health']['services']['vault'],'unknown')
    def test_unknown_witness_fields_not_exposed(self):
        c=self.make(ready=lambda:{**W,'SECRET_CANARY':Witness('ready',100)})
        self.assertNotIn('SECRET_CANARY',self.fetch(c).text)
    def test_nonblocking_demand_sampling_when_probe_hangs(self):
        import threading, time
        from health_cache import LocalHealthCache
        started=threading.Event();release=threading.Event()
        def slow():started.set();release.wait(1);return ACTIVE
        cache=LocalHealthCache(slow,clock=lambda:100)
        def source():cache.ensure_fresh();return cache.latest()
        c=self.make(local=StatusSources(ACTIVE,W,'unknown',100))
        app=FastAPI()
        attach_connection_health(app,lambda:None,source,clock=lambda:100)
        try:
            t=time.monotonic()
            first=TestClient(app).get('/api/connection-health')
            self.assertLess(time.monotonic()-t,0.4)
            self.assertTrue(started.wait(0.5))
            self.assertEqual(first.json()['health']['internet'],'unknown')
        finally:release.set()
    def test_no_privileged_operations(self):
        c=self.make();methods=[r.path for r in c.app.routes if r.path=='/api/connection-health'];self.assertEqual(methods,['/api/connection-health'])
        self.assertEqual(c.post('/api/connection-health',headers={'Authorization':'Bearer SYNTHETIC-SESSION'}).status_code,405)

if __name__=='__main__':unittest.main()