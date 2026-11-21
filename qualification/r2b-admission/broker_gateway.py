"""R2B owner/operator AF_UNIX broker gateway, not an installed service.

The gateway never trusts a JSON 'approved' flag. The protected broker,
not an operator request, invokes a fixed native Tk prompt after checking
the accepted socket's SO_PEERCRED and the actual operator PAM/logind
session immediately before AND after user review.

Deployment still requires protected independent broker UID/database,
socket custody, trusted native display agent and unchanged-SHA
real-installed acceptance. This module is an internal callable gateway,
not an open port, browser endpoint, UI-injected authority or worker.
"""
from __future__ import annotations

from approval_ledger import Context, Ledger, Refused
from operator_session import (
    attest_operator_peer, require_vault_owner_peer, check_live_session)

LABELS={
    'kraken_read':'Kraken read-only permissions check',
    'telegram_desk':'Telegram Desk fixed test message',
    'telegram_recovery':'Telegram Recovery fixed test message',
}


def fixed_native_prompt(context):
    """Server-owned fixed wording, never a dynamic endpoint/recipient.

    Grok, Telegram and other tests requiring separately verified opt-in,
    reviewed recipient or provider-specific display are not admitted until
    those contracts are installed. Real migration prompts only exact
    owner-held ciphertext inventory and role labels, not secrets.
    """
    context.validated()
    if context.operation=='provider_test':
        if context.role!='kraken_read':
            raise Refused('provider_specific_review_unavailable')
        message=(
            'Review one Kraken READ-ONLY connection permission check?\n\n'
            'No trading or withdrawal authority will be enabled. '
            'This approval is single-use, tied to the current credential '
            'generation and policy, and expires if the session changes.'
        )
    elif context.operation=='legacy_migration':
        message=(
            'Review importing existing encrypted PAPER-only credentials?\n\n'
            'Roles: '+', '.join(context.roles)+'\n'
            'Ciphertext inventory commitment: '+context.inventory[:16]+'…\n\n'
            'Existing encrypted credential bytes must remain unchanged. '
            'Every migrated credential remains unverified; no trading is enabled.'
        )
    else:
        raise Refused('native_review_unavailable')
    import tkinter as tk
    from tkinter import messagebox
    root=tk.Tk()
    root.withdraw()
    try:
        return messagebox.askokcancel(
            'Maj Vaj — Protected one-use approval',message,parent=root)
    finally:
        root.destroy()


class BrokerGateway:
    """Broker-owned callable entrypoint; not a public or deployed RPC.

    The process running this must be independently protected and must NOT
    allow callers to inject prompt/session callbacks over a socket. Tests
    can inject those functions only to qualify refusal behavior.
    """
    def __init__(self,ledger:Ledger, *, attest=attest_operator_peer,
                 owner=require_vault_owner_peer, prompt=fixed_native_prompt,
                 live=check_live_session):
        if type(ledger) is not Ledger:
            raise Refused('approval_ledger_required')
        self.ledger=ledger
        self._attest=attest
        self._owner=owner
        self._prompt=prompt
        self._live=live

    def prepare_from_owner(self,connection, context:Context, *, ttl_seconds=120):
        self._owner(connection)
        return self.ledger.prepare(context,ttl_seconds=ttl_seconds)

    def review_from_operator(self,connection,request_id):
        """The RPC carries only opaque ID. No 'approved' or target field."""
        self._attest(connection).validated()
        context=self.ledger.preview_for_native(request_id)
        return self.ledger.review_with_native_prompt(request_id,
            witness=lambda:self._attest(connection),
            prompt=lambda:self._prompt(context),
            reattest=lambda:self._attest(connection))

    def consume_from_owner(self,connection,request_id,context:Context):
        self._owner(connection)
        return self.ledger.consume(request_id,context,live_session=self._live)

    def cancel_from_owner(self,connection,request_id):
        self._owner(connection)
        return self.ledger.cancel(request_id)
