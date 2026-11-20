import unittest
from unittest.mock import patch, Mock
from managed_rpc import ManagedVaultCommands, Refused
from vault_owner_hooks import ROLES

class Tests(unittest.TestCase):
    def setUp(self):
        with patch('managed_rpc.ManagedPaperVault', object):
            self.v=Mock()
            self.rpc=ManagedVaultCommands(self.v,clock=lambda:101)
    def test_read_only_status(self):
        self.v.status.return_value={'ok':True}
        self.assertEqual(self.rpc.status(),{'ok':True})
    def test_kraken_read_paired(self):
        vals={s:'synthetic' for s in ROLES['kraken_read']}
        self.rpc.enroll(vals)
        self.v.enroll.assert_called_once_with('kraken_read',vals,101)
    def test_telegram_roles(self):
        for r in ('telegram_desk','telegram_recovery'):
            self.rpc.enroll({ROLES[r][0]:'synthetic'})
        self.assertEqual(self.v.enroll.call_count,2)
    def test_grok_stored_unverified(self):
        self.rpc.enroll({'grok':'synthetic'})
        self.v.enroll.assert_called_once_with('grok',{'grok':'synthetic'},101)
    def test_remove_pair(self):
        self.rpc.remove(set(ROLES['kraken_read']))
        self.v.remove.assert_called_once_with('kraken_read',101)
    def test_partial_pair_refused(self):
        with self.assertRaises(Refused):self.rpc.enroll({'kraken_read_key':'synthetic'})
        self.v.enroll.assert_not_called()
    def test_multi_role_refused(self):
        with self.assertRaises(Refused):self.rpc.enroll({'telegram_desk':'x','grok':'y'})
    def test_unknown_role(self):
        with self.assertRaises(Refused):self.rpc.enroll({'kraken_trade_key':'synthetic'})
    def test_no_test_authority(self):
        for name in ('test_connection','verify','finish','send','get_secrets','read_credentials'):
            self.assertFalse(hasattr(self.rpc,name))
    def test_remove_array_refused(self):
        with self.assertRaises(Refused):self.rpc.remove(['grok'])
    def test_remove_empty(self):
        with self.assertRaises(Refused):self.rpc.remove(set())
    def test_remove_multi_role(self):
        with self.assertRaises(Refused):self.rpc.remove({'telegram_desk','grok'})
    def test_clock_zero(self):
        self.rpc._clock=lambda:0
        with self.assertRaises(Refused):self.rpc.enroll({'grok':'synthetic'})
    def test_clock_exhausted(self):
        self.rpc._clock=lambda:2**63
        with self.assertRaises(Refused):self.rpc.enroll({'grok':'synthetic'})
    def test_invalid_input(self):
        with self.assertRaises(Refused):self.rpc.enroll('grok')
        self.v.enroll.assert_not_called()

if __name__ == '__main__':unittest.main()
