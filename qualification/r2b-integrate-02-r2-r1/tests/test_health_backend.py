import unittest
from dataclasses import dataclass
from health_backend import compose, no_provider_readiness, Witness, REQUIRED

@dataclass(frozen=True)
class Local:
    services: dict
    internet: str='unknown'

ACTIVE=Local({'dashboard':'active','backend':'active','vault':'active'})
W={key:Witness('ready',100) for key in REQUIRED}
class Tests(unittest.TestCase):
    def test_all_ready(self):self.assertTrue(all(v=='ready' for v in compose(ACTIVE,W,'online',100)['services'].values()))
    def test_report_fixed_fields(self): self.assertEqual(set(compose(ACTIVE,W,'online',100)),{'version','internet','services'})
    def test_process_not_ready(self):self.assertNotIn('ready',no_provider_readiness(ACTIVE,100)['services'].values())
    def test_route_not_internet(self):self.assertEqual(no_provider_readiness(ACTIVE,100)['internet'],'unknown')
    def test_route_missing(self):self.assertEqual(no_provider_readiness(Local(ACTIVE.services,'no_network_route'),100)['internet'],'no_network_route')
    def test_broken_vault(self):
        l=Local(dict(ACTIVE.services,vault='failed'));self.assertEqual(compose(l,W,'online',100)['services']['vault'],'failed')
    def test_broken_backend(self):
        l=Local(dict(ACTIVE.services,backend='failed'));self.assertEqual(compose(l,W,'online',100)['services']['dashboard'],'failed')
    def test_inactive_not_ready(self):
        l=Local(dict(ACTIVE.services,vault='inactive'));self.assertEqual(compose(l,W,'online',100)['services']['vault'],'unknown')
    def test_risk_gate_required(self):self.assertEqual(compose(ACTIVE,{k:v for k,v in W.items() if k!='risk_gate'},'online',100)['services']['risk_gate'],'unknown')
    def test_stale_witness(self):self.assertEqual(compose(ACTIVE,W,'online',150)['services']['dashboard'],'unknown')
    def test_future_witness(self):self.assertEqual(compose(ACTIVE,{**W,'vault':Witness('ready',101)},'online',100)['services']['vault'],'unknown')
    def test_single_failed_witness(self):self.assertEqual(compose(ACTIVE,{**W,'reconciliation':Witness('failed',100)},'online',100)['services']['reconciliation'],'failed')
    def test_unknown_network(self):self.assertEqual(compose(ACTIVE,W,'unknown',100)['internet'],'unknown')
    def test_conflicting_evidence(self):self.assertEqual(compose(ACTIVE,W,'conflicting_evidence',100)['internet'],'conflicting_evidence')
    def test_reject_unknown_network(self):
        with self.assertRaises(ValueError):compose(ACTIVE,W,'REAL_SECRET',100)
    def test_reject_untrusted_witness(self):self.assertEqual(compose(ACTIVE,{**W,'vault':'ready'},'online',100)['services']['vault'],'unknown')
    def test_reject_unexpected_unit(self):
        with self.assertRaises(ValueError):compose(Local({**ACTIVE.services,'SECRET':'active'}),W,'online',100)
    def test_unknown_extra_witness_ignored(self):self.assertEqual(compose(ACTIVE,{**W,'EXTRA':Witness('ready',100)},'online',100)['services'],{k:'ready' for k in REQUIRED})

if __name__=='__main__':unittest.main()