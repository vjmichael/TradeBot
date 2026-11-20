import unittest
from dataclasses import replace
from connection_status import Observation, Test, network_status, provider_status, ROLES

O = Observation
D = O('reachable',100)
R = O('reachable',100)
P = {'a': O('reachable',100), 'b': O('timeout',100)}

class Tests(unittest.TestCase):
    def test_online(self): self.assertEqual(network_status(100,D,R,P),('server_reachable','online'))
    def test_client_unreachable(self): self.assertEqual(network_status(100,O('timeout',100),R,P),('server_unreachable','unknown'))
    def test_stale_dashboard(self): self.assertEqual(network_status(160,D,R,P),('server_unreachable','unknown'))
    def test_stale_route(self): self.assertEqual(network_status(100,D,O('reachable',40),P),('server_reachable','unknown'))
    def test_no_route(self): self.assertEqual(network_status(100,D,O('no_route',100),{}),('server_reachable','no_network_route'))
    def test_contradiction(self): self.assertEqual(network_status(100,D,O('no_route',100),P),('server_reachable','conflicting_evidence'))
    def test_dns(self): self.assertEqual(network_status(100,D,R,dict(a=O('dns_failure',100),b=O('dns_failure',100)))[1],'dns_problem')
    def test_tls(self): self.assertEqual(network_status(100,D,R,dict(a=O('tls_failure',100),b=O('tls_failure',100)))[1],'tls_problem')
    def test_multiple_timeouts(self): self.assertEqual(network_status(100,D,R,dict(a=O('timeout',100),b=O('timeout',100)))[1],'internet_unreachable')
    def test_single_failure(self): self.assertEqual(network_status(100,D,R,dict(a=O('timeout',100)))[1],'unknown')
    def test_stale_success(self): self.assertEqual(network_status(100,D,R,dict(a=O('reachable',40),b=O('timeout',100)))[1],'unknown')
    def test_future_probe(self): self.assertEqual(network_status(100,D,R,dict(a=O('reachable',101)))[1],'unknown')
    def test_empty(self): self.assertEqual(network_status(100,D,R,{})[1],'unknown')
    def setUp(self): self.t=Test('kraken_read','g1','p1','permissions_verified',100,400)
    def provider(self,t=None,role='kraken_read',enabled=True,generation='g1',policy='p1',now=100): return provider_status(role,enabled,generation,policy,t,now)
    def test_verified(self): self.assertEqual(self.provider(self.t),'permissions_verified')
    def test_disabled(self): self.assertEqual(self.provider(self.t,enabled=False),'disabled')
    def test_not_configured(self): self.assertEqual(self.provider(self.t,generation=None),'not_configured')
    def test_no_test(self): self.assertEqual(self.provider(),'stored_unverified')
    def test_changed_generation(self): self.assertEqual(self.provider(self.t,generation='g2'),'stored_unverified')
    def test_changed_policy(self): self.assertEqual(self.provider(self.t,policy='p2'),'stored_unverified')
    def test_wrong_role(self): self.assertEqual(self.provider(self.t,role='kraken_trade'),'stored_unverified')
    def test_expired(self): self.assertEqual(self.provider(self.t,now=400),'stored_unverified')
    def test_clock_rollback(self): self.assertEqual(self.provider(self.t,now=99),'stored_unverified')
    def test_excess_ttl(self): self.assertEqual(self.provider(replace(self.t,expires=401)),'stored_unverified')
    def test_unknown_status(self): self.assertEqual(self.provider(replace(self.t,result='SECRET_CANARY')),'stored_unverified')
    def test_provider_specific_status(self): self.assertEqual(self.provider(replace(self.t,result='metadata_verified')),'stored_unverified')
    def test_delivery_distinction(self):
        for outcome in ('test_accepted_by_telegram','human_acknowledged','delivery_unknown'):
            t=replace(self.t,role='telegram_desk',result=outcome)
            self.assertEqual(self.provider(t,role='telegram_desk'),outcome)
    def test_all_roles(self):
        for role in ROLES: self.assertEqual(self.provider(role=role),'stored_unverified')
    def test_unknown_role(self):
        with self.assertRaises(ValueError): self.provider(role='unknown')
    def test_failure_not_online(self): self.assertEqual(self.provider(replace(self.t,result='connection_unverified')),'connection_unverified')

if __name__=='__main__': unittest.main()
