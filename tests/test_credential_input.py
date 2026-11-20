import unittest
from pydantic import ValidationError
from credential_input import CredentialSetup

CANARY = 'synthetic-canary-not-a-real-key-1203'


class CredentialInputTests(unittest.TestCase):
    def test_secret_wrapped_and_serialization_masked(self):
        model = CredentialSetup(kraken_read_key=CANARY, kraken_read_secret=CANARY)
        for output in (repr(model), str(model), repr(model.model_dump()), model.model_dump_json()):
            self.assertNotIn(CANARY, output)
        self.assertEqual(model.vault_values(), {'kraken_read_key': CANARY, 'kraken_read_secret': CANARY})

    def test_refuse_wallet_and_live_secrets(self):
        for name in ('private_key', 'wallet_private_key', 'seed_phrase', 'mnemonic', 'kraken_trade_key', 'withdrawal_key'):
            with self.assertRaises(ValidationError) as error:
                CredentialSetup.model_validate({name: CANARY})
            self.assertNotIn(CANARY, str(error.exception))

    def test_invalid_value_does_not_show_in_error_string(self):
        for value in ({'nested': CANARY}, CANARY * 300):
            with self.assertRaises(ValidationError) as error:
                CredentialSetup.model_validate({'grok': value})
            self.assertNotIn(CANARY, str(error.exception))

    def test_empty_and_nonstring_rejected(self):
        for value in ('', 123, True, ['abc']):
            with self.assertRaises(ValidationError):
                CredentialSetup.model_validate({'grok': value})

    def test_omitted_fields_not_unwrapped(self):
        self.assertEqual(CredentialSetup(grok=CANARY).vault_values(), {'grok': CANARY})
