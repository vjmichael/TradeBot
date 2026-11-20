"""Vault-owner only dispatch adapter for existing fixed socket contract.

No arbitrary role, nonce, URL, provider test, receipt or financial command.
No legacy plaintext read or export operation is exposed.
"""
import time
from managed_paper_vault import ManagedPaperVault, Refused
from vault_owner_hooks import ROLES


class ManagedVaultCommands:
    """Drop-in implementation of existing vault.status/enroll/remove methods.

    Unmodified status-only peer responses are allowed. Every modification
    targets exactly one approved role and goes through ManagedPaperVault's
    same SQLite transaction with generation invalidation.
    """
    def __init__(self, vault: ManagedPaperVault, *, clock=time.time):
        if not isinstance(vault, ManagedPaperVault) or not callable(clock):
            raise Refused('managed_vault_required')
        self._managed = vault
        self._clock = clock

    def _now(self):
        n = int(self._clock())
        if not 0 < n < (2**63 - 301)//1000:
            raise Refused('invalid_clock')
        return n

    @staticmethod
    def _role(slots):
        if not isinstance(slots, (set, dict)) or not slots:
            raise Refused('credential_selection_refused')
        exact = set(slots)
        found = [role for role, names in ROLES.items() if exact == set(names)]
        if len(found) != 1:
            raise Refused('credential_selection_refused')
        return found[0]

    def status(self):
        return self._managed.status()

    def enroll(self, values):
        if type(values) is not dict:
            raise Refused('credential_selection_refused')
        return self._managed.enroll(self._role(values), values, self._now())

    def remove(self, slots):
        if type(slots) is not set:
            raise Refused('credential_selection_refused')
        return self._managed.remove(self._role(slots), self._now())

    # There is intentionally NO verification dispatch API. A test action must
    # originate from independently proven native operator authority and a
    # fixed credential-isolated adapter. Merely setting reviewed=True is not
    # sufficient and cannot activate a provider test.
