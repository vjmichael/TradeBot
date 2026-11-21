"""Add a strictly fixture-gated synthetic R2B path to the ORIGINAL vault.

This is a deterministic SOURCE-LOCKED overlay script; it changes no
repository baseline, only a disposable rebuilt Debian acceptance tree.
The ordinary enrollment/status/read-only contracts remain unchanged.
No real provider adapter, destination, token read/export or trading
operation is introduced.

The explicit root-owned /etc/maj-vaj/r2b-approval-test-enabled marker is
required for every operation. Removed marker refuses actions even when
the code and systemd service happen to be installed.
"""
from __future__ import annotations
import hashlib
from pathlib import Path
import sys

ORIGINAL={
    'paper_credential_vault.py':'160254361a566ba220ccce38021eae3df2045fe3d9a312cb0e3a8b2f70ef8ab4',
    'deploy/maj-vaj-paper-vault.service':'e6b284fa04f289a459960ea37347353bfa6300702a146b8e11dce9b0d60cd6e6',
}


def replace_exact(src,old,new,name):
    if src.count(old)!=1:
        raise SystemExit('source_lock_anchor_mismatch:'+name)
    return src.replace(old,new)


def patch(directory):
    directory=Path(directory)
    original={}
    for name,digest in ORIGINAL.items():
        b=(directory/name).read_bytes()
        if hashlib.sha256(b).hexdigest()!=digest:
            raise SystemExit('source_lock_mismatch:'+name)
        original[name]=b.decode('utf-8')

    vault=original['paper_credential_vault.py']
    vault=replace_exact(vault,
        "        if op == 'remove' and set(request) == {'op', 'slots'} and type(request['slots']) is list and all(type(s) is str for s in request['slots']):\n            return vault.remove(set(request['slots']))\n",
        "        if op == 'remove' and set(request) == {'op', 'slots'} and type(request['slots']) is list and all(type(s) is str for s in request['slots']):\n            return vault.remove(set(request['slots']))\n"
        "        if op == 'synthetic_review_prepare' and set(request) == {'op'}:\n            return vault.review_prepare()\n"
        "        if op == 'synthetic_review_cancel' and set(request) == {'op'}:\n            return vault.review_cancel()\n"
        "        if op == 'synthetic_review_finish' and set(request) == {'op'}:\n            return vault.review_finish()\n",
        'fixed_qualification_dispatch')
    vault=replace_exact(vault,
        "    commands = ManagedVaultCommands(managed)\n",
        "    from owner_synthetic_bridge import read_owner_receipt_key\n"
        "    # Fail closed at process start if the independent synthetic-only\n"
        "    # receipt credential is missing or mismatched. This is a test-only\n"
        "    # owner unit variant, never a production default.\n"
        "    read_owner_receipt_key()\n"
        "    commands = ManagedVaultCommands(managed)\n",
        'fixed_owner_startup')
    unit=original['deploy/maj-vaj-paper-vault.service']
    unit=replace_exact(unit,
        "SupplementaryGroups=maj-vaj-credential-operator\n",
        "SupplementaryGroups=maj-vaj-credential-operator maj-vaj-r2b-approval-rpc\n",
        'fixed_owner_ipc_group')
    unit=replace_exact(unit,
        "LoadCredential=provider-tag-key:/etc/maj-vaj/credential-keys/provider-tag-key\n",
        "LoadCredential=provider-tag-key:/etc/maj-vaj/credential-keys/provider-tag-key\n"
        "LoadCredential=r2b-worker-signing-key:/etc/maj-vaj/credential-keys/r2b-worker-signing-key\n",
        'fixed_owner_worker_key')

    managed=(directory/'managed_rpc.py').read_text()
    if "from managed_paper_vault import ManagedPaperVault, Refused" not in managed:
        raise SystemExit('source_lock_managed_rpc_not_found')
    managed=replace_exact(managed,
        "from managed_paper_vault import ManagedPaperVault, Refused\n",
        "from managed_paper_vault import ManagedPaperVault, Refused\n"
        "import os,sys,stat\n"
        "# Fixed root-owned qualification modules, never caller paths.\n"
        "sys.path.insert(0,'/opt/maj-vaj/r2b-admission-r2')\n"
        "sys.path.insert(0,'/opt/maj-vaj/r2b-admission-r1')\n"
        "from owner_rpc_extension import FixedReadOnlyCoordinator\n",
        'fixed_owner_coordinator_import')
    managed=replace_exact(managed,
        "        self._clock = clock\n",
        "        self._clock = clock\n"
        "        self._fixed_synthetic = FixedReadOnlyCoordinator(vault,clock=clock)\n",
        'fixed_owner_coordinator_construction')
    previous_comment=(
        "    # There is intentionally NO verification dispatch API. A test action must\n"
        "    # originate from independently proven native operator authority and a\n"
        "    # fixed credential-isolated adapter. Merely setting reviewed=True is not\n"
        "    # sufficient and cannot activate a provider test.\n")
    extra=(
        "    @staticmethod\n"
        "    def _require_offline_fixture():\n"
        "        marker='/etc/maj-vaj/r2b-approval-test-enabled'\n"
        "        st=os.stat(marker,follow_symlinks=False)\n"
        "        if (not stat.S_ISREG(st.st_mode) or st.st_uid!=0 or st.st_nlink!=1\n"
        "                or stat.S_IMODE(st.st_mode)!=0o644):\n"
        "            raise Refused('synthetic_qualification_disabled')\n\n"
        "    def review_prepare(self):\n"
        "        self._require_offline_fixture()\n"
        "        return self._fixed_synthetic.prepare()\n\n"
        "    def review_cancel(self):\n"
        "        self._require_offline_fixture()\n"
        "        return self._fixed_synthetic.cancel()\n\n"
        "    def review_finish(self):\n"
        "        self._require_offline_fixture()\n"
        "        return self._fixed_synthetic.finish()\n"
        "    # No real provider adapter, LIVE, external endpoint or positive\n"
        "    # credential permission verification is exposed here.\n")
    managed=replace_exact(managed,previous_comment,extra,
                          'fixed_owner_no_network_methods')
    gui=(directory/'credential_graphical.py').read_text()
    old="""            approved = show_pending(root, guard)
            notice.set('Single-use PAPER review recorded. No provider action or verification has run.'
                       if approved else 'Review declined or refused. No connection verified.')
        except Exception:
            notice.set('Native review unavailable. No connection verified.')
"""
    new="""            prepared = send({'op':'synthetic_review_prepare'})
            if prepared != {'ok':True,'status':'awaiting_native_review'}:
                raise ValueError('synthetic_review_unavailable')
            try:
                approved = show_pending(root, guard)
                if approved:
                    outcome = send({'op':'synthetic_review_finish'})
                    if outcome != {'ok':True,'status':'stored_unverified'}:
                        raise ValueError('synthetic_denial_not_confirmed')
                    notice.set('Synthetic refusal recorded. Credentials remain unverified; no provider accessed.')
                else:
                    send({'op':'synthetic_review_cancel'})
                    notice.set('Review declined or refused. No connection verified.')
            except BaseException:
                try:send({'op':'synthetic_review_cancel'})
                except Exception:pass
                raise
        except Exception:
            notice.set('Native review unavailable. No connection verified.')
"""
    gui=replace_exact(gui,old,new,'original_gui_synthetic_lifecycle')

    outputs={
        'paper_credential_vault.py':vault,
        'managed_rpc.py':managed,
        'deploy/maj-vaj-paper-vault.service':unit,
        'credential_graphical.py':gui,
    }
    for name,text in outputs.items():
        if name.endswith('.py'):
            compile(text,str(directory/name),'exec')
    for name,text in outputs.items():
        (directory/name).write_text(text)
    print('R2B_R2_OWNER_SYNTHETIC_OVERLAY_LOCK_PASS')


if __name__=='__main__':
    if len(sys.argv)!=2:
        raise SystemExit('pass rebuilt private production tree')
    patch(sys.argv[1])
