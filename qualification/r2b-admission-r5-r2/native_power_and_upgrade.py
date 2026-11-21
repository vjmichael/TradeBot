"""R2B R5-R2: genuine hard guest power cuts, durable restore and authenticated offline upgrade. No direct production helper invocation for positive handoff.
Use actual X11 keyboard/pointer events on the LightDM-owned display; root serial is fixture control only.
All passwords/tokens below are disposable synthetic values. Never point at a real system."""
import os,sys,time,subprocess,shlex,re,json,socket
from pathlib import Path
import pexpect
vm=Path(sys.argv[1]); out=Path(sys.argv[2]); out.mkdir(exist_ok=True,parents=True)
qmp=str(vm/'qmp.sock')
accel=['-accel','tcg','-cpu','max'] # exact disposable QEMU fixture, not host virtualization
disk=vm/'guest.qcow2'
assert disk.is_file(), 'r5_read_write_guest_overlay_required'
args=accel+['-m','4096','-smp','4','-drive',f'file={disk},format=qcow2,if=virtio','-kernel',str(vm/'vmlinuz'),'-initrd',str(vm/'initrd.img'),'-append','root=/dev/vda rw console=ttyS0 loglevel=3','-nic','none','-display','none','-vga','std','-qmp',f'unix:{qmp},server=on,wait=off','-serial','stdio']+([] if len(sys.argv)==4 and sys.argv[3]=='reboot' else ['-no-reboot'])
s=pexpect.spawn('qemu-system-x86_64',args,encoding='utf-8',timeout=180,maxread=8192)
def command(c,timeout=90,ok=True):
    s.sendline("stty -echo; "+c+"; rc=$?; printf '\\nD2_%s=%s\\n' END \"$rc\"")
    s.expect(r'D2_END=(\d+)\r*\n',timeout=timeout)
    found=int(s.match.group(1)); result=s.before.replace('\r','').strip()
    if ok and found:raise RuntimeError(f'guest command failed ({found}): {result[-800:]}')
    return found,result
def mon(c):
    with socket.socket(socket.AF_UNIX) as sock:
        sock.connect(qmp); f=sock.makefile('rwb',buffering=0);f.readline()
        f.write(b'{"execute":"qmp_capabilities"}\n');f.readline()
        f.write((json.dumps({'execute':'human-monitor-command','arguments':{'command-line':c}})+'\n').encode())
        while True:
            v=json.loads(f.readline())
            if 'return' in v:return v['return']
            if 'error' in v:raise RuntimeError(v['error'])
def shot(name):
    mon('screendump '+str(out/(name+'.ppm')))
    subprocess.run(['convert',str(out/(name+'.ppm')),str(out/(name+'.png'))],check=True)
    (out/(name+'.ppm')).unlink()
ACTIVE_DISPLAY=':0'
def root_x():
    return 'DISPLAY='+ACTIVE_DISPLAY+' XAUTHORITY=/run/lightdm/root/'+ACTIVE_DISPLAY
def x(args,ok=True):
    return command(root_x()+' xdotool '+args,ok=ok)[1]
def key(*keys):x('key --clearmodifiers '+ ' '.join(keys));time.sleep(.3)
def type_text(t):x('type --clearmodifiers --delay 30 -- '+shlex.quote(t))
def click(xpos,ypos):x(f'mousemove {xpos} {ypos} click 1');time.sleep(.4)
def wait_cmd(c,timeout=45):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        rc,result=command(c,timeout=12,ok=False)
        if rc==0:return result
        time.sleep(1)
    raise RuntimeError('timed out waiting for '+c)
def session(user):
    return command("loginctl list-sessions --no-legend --no-pager | awk '$3==\""+user+"\" && $4==\"seat0\" {print $1}'")[1].strip()
def assert_active(user):
    sid=wait_cmd("id=$(loginctl list-sessions --no-legend | awk '$3==\""+user+"\" && $4==\"seat0\" {print $1}'); test -n \"$id\" && test \"$(loginctl show-session \"$id\" -p Active --value)\" = yes && test \"$(loginctl show-session \"$id\" -p Type --value)\" = x11 && test \"$(loginctl show-session \"$id\" -p Remote --value)\" = no && test \"$(loginctl show-session \"$id\" -p LockedHint --value)\" = no && echo \"$id\"",75)
    return sid.strip()
def wait_window(name,timeout=70):
    return wait_cmd(root_x()+' xdotool search --onlyvisible --name '+shlex.quote(name)+' | head -1 | grep -E "^[0-9]+$"',timeout).splitlines()[-1]
def greeter():
    """Find the *active* LightDM greeter display, not a stale X :0 cookie.

    dm-tool switch-to-greeter starts a new X server, often :1, while
    terminating the prior console may retire /run/lightdm/root/:0.
    Read only DISPLAY from the trusted installed greeter process, then
    validate the corresponding root-owned Xauthority cookie and X socket.
    """
    global ACTIVE_DISPLAY
    pid_cmd="pgrep -u lightdm -f '^([^[:space:]]*/)?lightdm-gtk-greeter([[:space:]]|$)' | head -1"
    env_cmd="p=$("+pid_cmd+"); test -n \"$p\" && tr '\\000' '\\n' <\"/proc/$p/environ\" | grep -E '^DISPLAY=:[0-9]+$' | head -1"
    try:
        display_line=wait_cmd(env_cmd,75).splitlines()[-1]
        assert re.fullmatch(r'DISPLAY=:[0-9]+',display_line),display_line
        ACTIVE_DISPLAY=display_line.split('=',1)[1]
        wait_cmd('test -e /run/lightdm/root/'+ACTIVE_DISPLAY+' && '+root_x()+' xdotool getdisplaygeometry >/dev/null',25)
    except (RuntimeError,AssertionError):
        _,state=command("systemctl show lightdm -p ActiveState -p SubState -p Result; "+pid_cmd+"; find /run/lightdm/root -maxdepth 1 -name ':*' -printf '%f\\n' 2>/dev/null",ok=False)
        (out/'greeter-readiness.txt').write_text('active_display='+ACTIVE_DISPLAY+'\n'+state+'\n')
        raise
    time.sleep(3)

def fixture_physical_keys(text):
    # Only these disposable fixture passwords have this deliberately limited alphabet.
    if not re.fullmatch(r'[a-z0-9-]+',text):
        raise ValueError('unsupported fixture key')
    for char in text:
        keyname='minus' if char=='-' else char
        mon('sendkey '+keyname+' 100')
        time.sleep(.18)
def login(user,password):
    greeter()
    # Observed greeter layout, centered username/password fields. Explicitly focus
    # the username; wait for the asynchronous PAM username transition before typing.
    width,height=map(int,x('getdisplaygeometry').split())
    click(width//2+8,height//2-40)
    key('ctrl+a');type_text(user);key('Tab');time.sleep(3)
    key('ctrl+a')
    # Use actual virtual keyboard key events, not synthesized X text, for the password.
    fixture_physical_keys(password);time.sleep(1)
    key('Return')
    try:
        return assert_active(user)
    except RuntimeError:
        _, state=command("passwd -S "+shlex.quote(user)+" | awk '{print \"fixture_password_status=\" $2}'; loginctl list-sessions --no-legend --no-pager; systemctl show lightdm -p ActiveState -p SubState -p Result",ok=False)
        (out/('login-readiness-'+user+'.txt')).write_text(state+'\n')
        raise
def read_status():
    c="import sys,json;sys.path.insert(0,'/opt/maj-vaj');from paper_credential_vault import send,STATUS_SOCKET;print(json.dumps(send({'op':'status'},STATUS_SOCKET)))"
    raw=command("runuser -u maj-vaj -- /opt/maj-vaj/venv/bin/python -I -B -c "+shlex.quote(c))[1]
    return json.loads(raw.splitlines()[-1])['credentials']
def find(text):
    key('ctrl+f');type_text(text);time.sleep(.5);key('Escape')
def browser_consent_visible():
    # Observe a fixed, screenshot-confirmed Chromium 150 external-app button
    # region in the actual 1280x800 disposable QEMU display. No OCR, browser
    # script, direct URI/handler call, or implicit prompt auto-acceptance.
    ppm=out/'browser-consent-probe.ppm'
    mon('screendump '+str(ppm))
    try:
        with ppm.open('rb') as f:
            if f.readline().strip()!=b'P6':
                raise RuntimeError('unexpected_QEMU_pixel_format')
            line=f.readline()
            while line.startswith(b'#'):
                line=f.readline()
            width,height=map(int,line.split())
            if width<810 or height<230 or f.readline().strip()!=b'255':
                raise RuntimeError('unexpected_QEMU_display_geometry')
            f.seek((180*width+550)*3,1)
            pixel=f.read(3)
            if len(pixel)!=3:
                raise RuntimeError('incomplete_QEMU_frame')
            red,green,blue=pixel
            return blue>100 and green>65 and blue-red>50
    finally:
        ppm.unlink(missing_ok=True)

def await_browser_consent(timeout=35):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if browser_consent_visible():
            return
        time.sleep(.6)
    raise RuntimeError('browser_external_app_permission_not_visible')

def visible(title):
    rc,_=command(root_x()+' xdotool search --onlyvisible --name '+shlex.quote(title)+' >/dev/null 2>&1',ok=False)
    return rc == 0

def ensure_empty():
    now=read_status()
    assert all(value == 'not_configured' for value in now.values()),now
    return now

def console_modal(label):
    greeter()
    sid=login('maj-vaj-console','d2-console-fixture-813')
    wait_cmd("pgrep -u maj-vaj-console -f '/usr/lib/chromium/chromium' >/dev/null",90)
    browser=wait_window('Maj Vaj Trade Bot',90)
    x('windowmove '+browser+' 0 0');x('windowsize '+browser+' 1024 768');x('windowfocus '+browser)
    key('F11');time.sleep(2)
    find('Admin access token');key('Tab');type_text('synthetic-r2ar1-browser-bearer-'*2);key('Return');time.sleep(3)
    find('Settings');key('Return');time.sleep(2)
    find('Manage account connections');key('Return');time.sleep(2)
    find('Open secure account sign-in');key('Tab');time.sleep(1)
    shot(label+'-browser-link')
    click(195,383)
    await_browser_consent(35)
    shot(label+'-browser-consent')
    click(650,184);time.sleep(2)
    modal=wait_window('Maj Vaj.*Open account connections',30)
    assert session('maj-vaj-console') == sid
    ensure_empty()
    shot(label+'-native-modal')
    return sid,browser,modal

def choose_operator():
    greeter()
    click(1122,12);time.sleep(.5)
    click(1156,61);time.sleep(.5)
    sid=login('maj-vaj-credential-operator','d2-operator-fixture-916')
    window=wait_window('Maj Vaj.*Account connections',30)
    assert assert_active('maj-vaj-credential-operator') == sid
    x('windowfocus '+window)
    return sid,window

def fill_pair(keyvalue,secretvalue):
    click(220,234);type_text(keyvalue)
    click(220,281);type_text(secretvalue)
    click(54,316)

def end_operator(sid,win):
    x('windowfocus '+win)
    click(622,438)
    wait_cmd('! loginctl show-session '+shlex.quote(sid)+' >/dev/null 2>&1',50)
    greeter()
    assert not session('maj-vaj-credential-operator')

def restart_vault():
    command('systemctl restart maj-vaj-paper-vault.service',timeout=45)
    wait_cmd('test -S /run/maj-vaj-paper-vault-status/status.sock',40)


def probe(principal,payload):
    """Observed kernel peer identity, never a vault credential/approval input."""
    encoded=json.dumps(payload,separators=(',',':'))
    py=(
      "import sys,socket,json;"
      "path='/run/maj-vaj-r2b-approval/approval.sock';"
      "s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);"
      "s.settimeout(3);s.connect(path);"
      "s.sendall(sys.argv[1].encode('ascii')+b'\\n');"
      "s.shutdown(socket.SHUT_WR);"
      "data=s.recv(1024);s.close();"
      "assert len(data)<=1024 and data.endswith(b'\\n');"
      "print('R2B_RPC='+json.dumps(json.loads(data)))"
    )
    rc,raw=command('runuser -u '+shlex.quote(principal)+' -- /usr/bin/python3 -I -B -c '+
          shlex.quote(py)+' '+shlex.quote(encoded),timeout=14,ok=False)
    if rc:return None
    rows=[line.split('R2B_RPC=',1)[1] for line in raw.splitlines() if line.startswith('R2B_RPC=')]
    if len(rows)!=1:raise RuntimeError('malformed_broker_probe_result')
    return json.loads(rows[0])

def refusal(principal,payload):
    outcome=probe(principal,payload)
    assert outcome is None or outcome=={'ok':False,'reason':'approval_refused'},('unexpected_admission',principal,outcome)


def current_state(rid):
    code=(
      "import sqlite3,sys;"
      "db=sqlite3.connect('file:/var/lib/maj-vaj-r2b-approval/ledger.sqlite3?mode=ro',uri=True);"
      "row=db.execute('SELECT state FROM approvals WHERE request_id=?',(sys.argv[1],)).fetchone();"
      "db.close();print('BROKER_STATE='+str(row[0] if row else 'missing'))"
    )
    data=command('runuser -u maj-vaj-approval -- /usr/bin/python3 -I -B -c '+
                 shlex.quote(code)+' '+shlex.quote(rid))[1]
    matches=[x.split('BROKER_STATE=',1)[1] for x in data.splitlines() if x.startswith('BROKER_STATE=')]
    assert len(matches)==1,'broker_state_receipt_missing'
    return matches[0]
def new_owner_request():
    r=probe('maj-vaj-vault',{'op':'prepare','context':{
        'operation':'provider_test','role':'kraken_read',
        'generation':'a'*32,'policy':'b'*64,'inventory':'','roles':[]}})
    assert type(r) is dict and r.get('ok') is True and re.fullmatch('[0-9a-f]{32}',r.get('request_id',''))
    return r['request_id']
def owner_consume(rid):
    return probe('maj-vaj-vault',{'op':'consume','request_id':rid,'context':{
        'operation':'provider_test','role':'kraken_read',
        'generation':'a'*32,'policy':'b'*64,'inventory':'','roles':[]}})
def click_review():
    key('alt+r')
def wait_review():
    try:
        return wait_window('Maj Vaj.*one-use read-only permission review',35)
    except RuntimeError:
        _,diag=command("loginctl list-sessions --no-legend; systemctl show maj-vaj-r2b-approval -p ActiveState -p Result -p MainPID -p ProtectProc; ps ax -o pid,uid,ppid,args | grep -E 'credential_graphical|credential_session_wrapper' | grep -v grep; ls -l /run/maj-vaj-r2b-approval/",ok=False)
        (out/'operator-approval-dialog-diagnostic.txt').write_text(diag+'\n')
        shot('native-agent-dialog-unavailable')
        raise


def approval_state_count(state):
    if state not in ('approved','cancelled','consumed','revoked','pending'):
        raise ValueError('unknown_approval_state')
    py=("import sys,sqlite3;"
        "db=sqlite3.connect('file:/var/lib/maj-vaj-r2b-approval/ledger.sqlite3?mode=ro',uri=True);"
        "n=db.execute('SELECT count(*) FROM approvals WHERE state=?',(sys.argv[1],)).fetchone()[0];"
        "db.close();print('COUNT='+str(n))")
    data=command('runuser -u maj-vaj-approval -- /usr/bin/python3 -I -B -c '+
                 shlex.quote(py)+' '+shlex.quote(state))[1]
    found=[int(x.split('COUNT=',1)[1]) for x in data.splitlines() if x.startswith('COUNT=')]
    assert len(found)==1
    return found[0]
def metadata_tag():
    py=("import sqlite3,hashlib;"
        "db=sqlite3.connect('file:/var/lib/maj-vaj-paper-vault/vault.sqlite3?mode=ro',uri=True);"
        "blob=db.execute('SELECT sealed FROM provider_metadata WHERE id=1').fetchone()[0];"
        "db.close();print('METADATA='+hashlib.sha256(blob).hexdigest())")
    data=command('runuser -u maj-vaj-vault -- /usr/bin/python3 -I -B -c '+shlex.quote(py))[1]
    found=[x.split('METADATA=',1)[1] for x in data.splitlines() if x.startswith('METADATA=')]
    assert len(found)==1 and re.fullmatch('[0-9a-f]{64}',found[0])
    return found[0]


# R4: independent hostile fixture events use the root serial console to
# change installed OS state; only the original GUI can approve a request.
# No injected worker result or successful native-review shortcut is used.
R4_CASE = sys.argv[3] if len(sys.argv) == 4 else None
if R4_CASE not in ('backup_power','rollback_repeat','signed_upgrade','upgrade_power'):
    raise SystemExit('invalid_r5_r2_case')

def counts():
    # One SQLite SELECT is one snapshot. Five sequential cross-process
    # queries can otherwise straddle broker restart and fabricate a
    # contradictory pending+revoked combination from the SAME row.
    states=('pending','approved','consumed','cancelled','revoked')
    py=("import sqlite3,json;"
        "db=sqlite3.connect('file:/var/lib/maj-vaj-r2b-approval/ledger.sqlite3?mode=ro',uri=True);"
        "rows=db.execute('SELECT state,count(*) FROM approvals GROUP BY state').fetchall();"
        "db.close();"
        "print('ATOMIC_COUNTS='+json.dumps(dict(rows),sort_keys=True))")
    raw=command('runuser -u maj-vaj-approval -- /usr/bin/python3 -I -B -c '+shlex.quote(py))[1]
    values=[json.loads(x.split('ATOMIC_COUNTS=',1)[1]) for x in raw.splitlines()
            if x.startswith('ATOMIC_COUNTS=')]
    assert len(values)==1,'broker_atomic_snapshot_missing'
    found=values[0]
    assert type(found) is dict and set(found)<=set(states) and all(
        type(n) is int and n>=0 for n in found.values()),'broker_atomic_snapshot_invalid'
    return {state:found.get(state,0) for state in states}

def native_yes(dialog):
    x('windowraise '+dialog)
    time.sleep(.7)
    click(312,425)

def assert_unverified():
    state=read_status()
    assert state['kraken_read_key']=='stored_unverified'
    assert state['kraken_read_secret']=='stored_unverified'
    return state

def wait_consumed_one():
    wait_cmd('runuser -u maj-vaj-approval -- /usr/bin/python3 -c '+
        shlex.quote("import sqlite3;s=sqlite3.connect('/var/lib/maj-vaj-r2b-approval/ledger.sqlite3');assert s.execute('SELECT count(*) FROM approvals WHERE state=?',('consumed',)).fetchone()[0]==1"),45)

def operator_request(payload):
    code="import sys,json;sys.path.insert(0,'/opt/maj-vaj');from paper_credential_vault import send;print('FIXTURE='+json.dumps(send(json.loads(sys.argv[1]))))"
    raw=command('runuser -u maj-vaj-credential-operator -- /opt/maj-vaj/venv/bin/python -I -B -c '+
                shlex.quote(code)+' '+shlex.quote(json.dumps(payload)))[1]
    values=[json.loads(line.split('FIXTURE=',1)[1]) for line in raw.splitlines() if line.startswith('FIXTURE=')]
    assert len(values)==1, 'fixture_vault_result_missing'
    return values[0]

def broker_ledger_inventory():
    # Read-only native fixture: no request ID, witness, secret or signing key
    # is copied into acceptance artifacts. Distinguish restart generations.
    py=("import sqlite3,json,hashlib;"
        "db=sqlite3.connect('file:/var/lib/maj-vaj-r2b-approval/ledger.sqlite3?mode=ro',uri=True);"
        "rows=db.execute('SELECT state,incarnation,created FROM approvals ORDER BY created,request_id').fetchall();"
        "db.close();"
        "print('LEDGER_EVIDENCE='+json.dumps([{'state':a,'incarnation_fp':hashlib.sha256(b.encode()).hexdigest()[:12],'created_ns':c} for a,b,c in rows]))")
    raw=command('runuser -u maj-vaj-approval -- /usr/bin/python3 -I -B -c '+shlex.quote(py))[1]
    records=[json.loads(line.split('LEDGER_EVIDENCE=',1)[1])
             for line in raw.splitlines() if line.startswith('LEDGER_EVIDENCE=')]
    assert len(records)==1,'broker_restart_evidence_missing'
    return records[0]


import signal
case=R4_CASE
installer='/opt/maj-vaj/r2b-admission-r5/install-offline.sh'
broker='maj-vaj-r2b-approval.service'
worker='maj-vaj-r2b-synthetic-worker.service'
vault='maj-vaj-paper-vault.service'
marker='/etc/maj-vaj/r2b-approval-test-enabled'
pending='/etc/maj-vaj/r2b-r5-transaction-pending'
backup='/etc/maj-vaj/r2b-r5-rollback-snapshot'
receipt='/etc/maj-vaj/r2b-r5-rollback-completion'
upgrade_pending='/etc/maj-vaj/r5-r2-upgrade-pending'
upgrade_receipt='/etc/maj-vaj/r5-r2-release-receipt'
upgrade_backup='/etc/maj-vaj/r5-r2-upgrade-backup'
keypath='/etc/maj-vaj/credential-keys/r2b-worker-signing-key'

def cmd(c,*,ok=True,timeout=95):
    return command(c,ok=ok,timeout=timeout)
def refuses(c):
    rc,data=cmd(c,ok=False)
    assert rc!=0,'untrusted_action_not_refused'
    return data
def absent(p):
    cmd('test ! -e '+shlex.quote(p)+' && test ! -L '+shlex.quote(p))
def stopped():
    for unit in (broker,worker):
        assert cmd('systemctl is-active --quiet '+unit,ok=False)[0]!=0
def source_digest():
    return cmd('sha256sum /opt/maj-vaj/managed_rpc.py /opt/maj-vaj/paper_credential_vault.py /etc/systemd/system/maj-vaj-paper-vault.service')[1]
def fingerprint(p):
    return cmd('sha256sum '+shlex.quote(p))[1].split()[0]
def fixture(name):
    cmd('install -o root -g root -m 0600 /dev/null /etc/maj-vaj/'+name)
def clear(name):
    cmd('rm -f /etc/maj-vaj/'+name)
def signin():
    s.expect('login:',timeout=240)
    s.sendline('root')
    s.expect('Password:',timeout=75)
    s.sendline('d2-serial-fixture-714')
    s.expect(r'root@[^\r\n]+#',timeout=100)
    s.sendline("stty -echo; unset PROMPT_COMMAND; PS1=''; bind 'set enable-bracketed-paste off'; printf '\\nD2_%s\\n' READY")
    s.expect(r'D2_READY\r*\n')
def hard_cut():
    global s
    # SIGKILL the *QEMU system process*, not a guest service/reboot.
    # All three cuts use the SAME writable guest disk overlay.
    assert s.isalive(),'vm_was_not_running'
    os.kill(s.pid,signal.SIGKILL)
    s.expect(pexpect.EOF,timeout=40)
    s.close(force=True)
    time.sleep(1)
    Path(qmp).unlink(missing_ok=True)
    s=pexpect.spawn('qemu-system-x86_64',args,encoding='utf-8',timeout=180,maxread=8192)
    signin()
    # Original guest keeps only lo and immutable accepted fixtures.
    cmd('test "$(ls -1 /sys/class/net)" = lo')
    cmd('test "$(systemd-detect-virt)" = qemu')
    print('R5_R2_GENUINE_QEMU_POWER_LOSS_REBOOT_PASS',flush=True)
def trust():
    pub='/opt/maj-vaj/r5-r2-offline-trust/trusted.pub'
    seal='/opt/maj-vaj/r5-r2-offline-trust/trusted.pub.sha256'
    cmd('install -o root -g root -m 0600 '+pub+' /etc/maj-vaj/r5-r2-trust-root.pub')
    cmd('install -o root -g root -m 0600 '+seal+' /etc/maj-vaj/r5-r2-trust-root.sha256')
    print('R5_R2_INDEPENDENT_OFFLINE_TRUST_PROVISIONED_PASS',flush=True)

try:
    signin()
    cmd('bash /root/d2-setup.sh',timeout=160)
    ensure_empty()
    baseline=source_digest()
    absent(marker);stopped()
    print('R5_R2_NATIVE_BASELINE_PREPARED_PASS',flush=True)

    if case=='backup_power':
        fixture('r2b-r5-fixture-abort-prepare-first')
        refuses('bash '+installer+' install')
        hard_cut()
        cmd('test "$(cat '+pending+')" = R5-PREPARING-NO-MUTATIONS')
        absent(marker);stopped()
        refuses('bash '+installer+' install')
        refuses('bash '+installer+' enable')
        clear('r2b-r5-fixture-abort-prepare-first')
        cmd('bash '+installer+' recover',timeout=100)
        absent(pending);absent(backup)
        assert source_digest()==baseline
        print('R5_R2_INTERRUPTED_BACKUP_ABANDON_PASS',flush=True)
        cmd('bash '+installer+' install',timeout=110)
        cmd('bash '+installer+' verify')
        print('R5_R2_SAFE_REINSTALL_AFTER_BACKUP_CUT_PASS',flush=True)

    elif case=='rollback_repeat':
        # The accepted R5-R1 fixture had "kill -KILL '$'"; R5-R2 uses
        # actual "$$" and independently SIGKILLs the whole QEMU system.
        fixture('r2b-r5-fixture-abort-after-first')
        refuses('bash '+installer+' install')
        hard_cut()
        assert source_digest()!=baseline
        cmd('test "$(cat '+pending+')" = R5-PENDING-ROLLBACK-ONLY')
        absent(marker);stopped()
        clear('r2b-r5-fixture-abort-after-first')
        fixture('r2b-r5-fixture-abort-restore-first')
        refuses('bash '+installer+' recover')
        hard_cut()
        absent(marker);stopped()
        cmd('test -e '+backup+'/manifest.json')
        clear('r2b-r5-fixture-abort-restore-first')
        fixture('r2b-r5-fixture-abort-cleanup-first')
        refuses('bash '+installer+' recover')
        hard_cut()
        cmd('test -e '+receipt)
        absent(marker);stopped()
        clear('r2b-r5-fixture-abort-cleanup-first')
        cmd('bash '+installer+' recover',timeout=100)
        assert source_digest()==baseline,'recovered_source_changed'
        for path in (pending,backup,receipt,keypath,marker):
            absent(path)
        stopped()
        cmd('systemctl is-active --quiet '+vault)
        print('R5_R2_THREE_HARD_POWER_CUTS_ROLLBACK_COMPLETED_PASS',flush=True)
        cmd('bash '+installer+' install',timeout=110)
        cmd('bash '+installer+' verify')
        print('R5_R2_REINSTALL_AFTER_REPEATED_CUT_PASS',flush=True)

    elif case=='signed_upgrade':
        cmd('bash '+installer+' install',timeout=110)
        cmd('bash '+installer+' verify')
        trust()
        before=source_digest()
        signing_key=fingerprint(keypath)
        signature='/opt/maj-vaj/r5-r2-package/signature.ed25519'
        payload='/opt/maj-vaj/r5-r2-package/managed_rpc-v2.py'
        cmd('cp -p '+signature+' /root/r5r2-correct-signature')
        cmd('cp -p '+payload+' /root/r5r2-correct-payload')
        cmd("printf X >>"+signature)
        refuses('bash '+installer+' upgrade-v2')
        absent(upgrade_pending);absent(marker);stopped()
        cmd('install -o root -g root -m 0644 /root/r5r2-correct-signature '+signature)
        cmd("printf '\\n#tampered\\n' >>"+payload)
        refuses('bash '+installer+' upgrade-v2')
        cmd('install -o root -g root -m 0644 /root/r5r2-correct-payload '+payload)
        assert source_digest()==before
        print('R5_R2_TAMPERED_SIGNED_RELEASE_REFUSED_PASS',flush=True)
        # An independently provisioned root cannot be substituted with a
        # foreign key even when both replacement files are root-controlled.
        pub='/etc/maj-vaj/r5-r2-trust-root.pub'
        seal='/etc/maj-vaj/r5-r2-trust-root.sha256'
        cmd('cp -p '+pub+' /root/r5r2-correct-trust')
        cmd('cp -p '+seal+' /root/r5r2-correct-trust-commitment')
        cmd("printf X >>"+pub)
        refuses('bash '+installer+' upgrade-v2')
        cmd('install -o root -g root -m 0600 /root/r5r2-correct-trust '+pub)
        cmd('install -o root -g root -m 0600 /root/r5r2-correct-trust-commitment '+seal)
        print('R5_R2_TRUST_ROOT_TAMPER_REFUSED_PASS',flush=True)
        cmd('bash '+installer+' upgrade-v2',timeout=100)
        cmd('bash '+installer+' verify-v2')
        cmd('bash '+installer+' verify')
        assert source_digest()!=before
        cmd("grep -Fxq 'R5R2_OFFLINE_PACKAGE_VERSION = 2' /opt/maj-vaj/managed_rpc.py")
        assert fingerprint(keypath)==signing_key
        refuses('bash '+installer+' rollback')
        refuses('bash '+installer+' upgrade-v2')
        cmd('bash '+installer+' enable',timeout=100)
        cmd('bash '+installer+' disable',timeout=100)
        cmd('bash '+installer+' verify-v2')
        print('R5_R2_AUTHENTICATED_CROSS_VERSION_LIFECYCLE_PASS',flush=True)

    elif case=='upgrade_power':
        cmd('bash '+installer+' install',timeout=110)
        cmd('bash '+installer+' verify')
        trust()
        before=source_digest()
        signature_key=fingerprint(keypath)
        fixture('r5-r2-fixture-abort-upgrade-after-code')
        refuses('bash '+installer+' upgrade-v2')
        hard_cut()
        cmd('test -f '+upgrade_pending)
        absent(marker);stopped()
        refuses('bash '+installer+' enable')
        clear('r5-r2-fixture-abort-upgrade-after-code')
        fixture('r5-r2-fixture-abort-recover-after-code')
        refuses('bash '+installer+' recover-v2')
        hard_cut()
        clear('r5-r2-fixture-abort-recover-after-code')
        fixture('r5-r2-fixture-abort-recover-cleanup-first')
        refuses('bash '+installer+' recover-v2')
        hard_cut()
        clear('r5-r2-fixture-abort-recover-cleanup-first')
        cmd('bash '+installer+' recover-v2',timeout=100)
        absent(upgrade_pending);absent(upgrade_receipt);absent(upgrade_backup)
        assert source_digest()==before
        assert fingerprint(keypath)==signature_key
        cmd('bash '+installer+' verify')
        print('R5_R2_THREE_HARD_POWER_CUTS_SIGNED_UPGRADE_RECOVERY_PASS',flush=True)
        cmd('bash '+installer+' upgrade-v2',timeout=100)
        cmd('bash '+installer+' verify-v2')
        assert fingerprint(keypath)==signature_key
        print('R5_R2_SIGNED_UPGRADE_REAPPLIED_AFTER_RECOVERY_PASS',flush=True)

    print('R5_R2_NATIVE_CASE_PASS '+case,flush=True)
finally:
    try:
        cmd('systemctl stop maj-vaj-r2b-approval.service maj-vaj-r2b-synthetic-worker.service maj-vaj-paper-vault.service',ok=False,timeout=30)
        s.sendline('poweroff');s.expect(pexpect.EOF,timeout=60)
    except Exception:
        s.close(force=True)
