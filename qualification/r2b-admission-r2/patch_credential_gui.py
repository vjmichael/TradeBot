"""Patch only the frozen root-owned Account Connections Tk source.

The original LightDM wrapper, cookie/session guard, masked credential
fields, vault IPC and buttons remain unchanged. This adds a single
explicit native review button and Alt+R keyboard action. No positive
review is possible without a separately prepared owner request and
the protected broker's live OS attestation.
"""
import hashlib
from pathlib import Path
import sys

def main():
    file=Path(sys.argv[1]) if len(sys.argv)==2 else Path('/opt/maj-vaj/credential_graphical.py')
    original=file.read_bytes()
    if hashlib.sha256(original).hexdigest()!='74cd63e6533c237f5222c151c00548dd2631a95b2b69f147bf8c3871f32a5a9f':
        raise SystemExit('frozen_account_connections_source_mismatch')
    marker=b"    ttk.Button(buttons, text='Refresh', command=refresh).pack(side='left')\n"
    if original.count(marker)!=1:
        raise SystemExit('expected_original_gui_insertion_missing')
    addition=b"""    def review_one_use():
        clear()
        try:
            import sys
            sys.path.insert(0, '/opt/maj-vaj/r2b-admission-r2')
            from native_agent_protocol import show_pending
            approved = show_pending(root, guard)
            notice.set('Single-use PAPER review recorded. No provider action or verification has run.'
                       if approved else 'Review declined or refused. No connection verified.')
        except Exception:
            notice.set('Native review unavailable. No connection verified.')
    ttk.Button(buttons, text='Review pending read-only check',
               command=review_one_use).pack(side='left', padx=(8,0))
    root.bind('<Alt-r>', lambda event: review_one_use())
"""
    changed=original.replace(marker,marker+addition)
    compile(changed.decode('utf-8'),str(file),'exec')
    file.write_bytes(changed)
    print('R2_NATIVE_ORIGINAL_GUI_PATCH_SOURCE_LOCK_PASS')

if __name__=='__main__':
    main()
