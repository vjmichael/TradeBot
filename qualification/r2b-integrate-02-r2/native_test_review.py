"""Operator-desktop-only preview guard for one connection test.

A positive review is only UI intent. It is NOT a receipt, network access,
server-side operator authorization, or permission for LIVE trading.
"""
from __future__ import annotations
from dataclasses import dataclass

LABELS = {
    'kraken_read': 'Kraken read-only permissions',
    'telegram_desk': 'Telegram Desk test notification',
    'telegram_recovery': 'Telegram Recovery test notification',
    'grok': 'Optional Grok metadata inspection (no model request)',
}

@dataclass(frozen=True)
class LocalIntent:
    role: str
    session: str
    # No password, token, chat identifier or general target URI.


def _trusted_identity():
    from credential_graphical import current_identity
    return current_identity()


def _events(session):
    from reserve_review_events import SessionEvents
    return SessionEvents(session)


def _ask_native(role):
    import tkinter as tk
    from tkinter import messagebox
    root=tk.Tk();root.withdraw()
    try:
        return messagebox.askokcancel('Maj Vaj — Test connection',
            'Test '+LABELS[role]+'?\n\n'
            'This confirms only the local test request. It does not authorize trading. '
            'Telegram sends one reviewed test message; Grok checks metadata without inference.',
            parent=root)
    finally:root.destroy()


def review(role, *, opted_in=False, identity=_trusted_identity,
           events=_events, ask=_ask_native):
    """Local native UI helper. Never forward LocalIntent as independent proof
    to an untrusted RPC; the vault must revalidate SO_PEERCRED and session.
    """
    if type(role) is not str or role not in LABELS:
        raise ValueError('unsupported_test_role')
    if role=='grok' and opted_in is not True:
        return None
    begin=identity()  # existing strict UID, PAM/seat, display and X-cookie guard
    if type(begin) is not tuple or not begin or type(begin[0]) is not str:
        raise ValueError('operator_session_unavailable')
    with events(begin[0]) as event:
        def check():
            event.check()
            if identity()!=begin:
                raise ValueError('operator_session_changed')
            event.check()
        check()
        accepted=ask(role)
        check()
        if accepted is not True:
            return None
        return LocalIntent(role,begin[0])