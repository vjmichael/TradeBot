"""R2B R3: exact repaired source in disposable offline QEMU desktop. No direct production helper invocation for positive handoff.
Use actual X11 keyboard/pointer events on the LightDM-owned display; root serial is fixture control only.
All passwords/tokens below are disposable synthetic values. Never point at a real system."""
import os,sys,time,subprocess,shlex,re,json,socket
from pathlib import Path
import pexpect
vm=Path(sys.argv[1]); out=Path(sys.argv[2]); out.mkdir(exist_ok=True,parents=True)
qmp=str(vm/'qmp.sock')
accel=['-accel','tcg','-cpu','max'] # exact disposable QEMU fixture, not host virtualization
args=accel+['-m','4096','-smp','4','-drive',f'file={vm}/guest.ext4,format=raw,if=virtio','-kernel',str(vm/'vmlinuz'),'-initrd',str(vm/'initrd.img'),'-append','root=/dev/vda rw console=ttyS0 loglevel=3','-nic','none','-display','none','-vga','std','-qmp',f'unix:{qmp},server=on,wait=off','-serial','stdio','-no-reboot']
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


def socket_probe(principal, frame, accepted=False, hold_open=False):
    """Actual installed SO_PEERCRED/DAC test, never enrollment or authorization.

    Return value 19 means the fixed *UI intent* ACK was received. No secret
    or ticket is ever sent, and ACK is never treated as permission to enroll.
    """
    uid=int(command('id -u maj-vaj-console')[1].splitlines()[-1])
    path='/run/user/'+str(uid)+'/maj-vaj-account-handoff-intent.sock'
    code=(
        "import socket,sys\n"
        "path="+repr(path)+"\n"
        "payload=bytes.fromhex("+repr(frame.hex())+")\n"
        "try:\n"
        "    peer=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)\n"
        "    peer.settimeout(4)\n"
        "    peer.connect(path)\n"
        "    peer.sendall(payload)\n"
        "    if not "+repr(hold_open)+": peer.shutdown(socket.SHUT_WR)\n"
        "    try: reply=peer.recv(16)\n"
        "    except (OSError,TimeoutError): reply=b''\n"
        "    peer.close()\n"
        "    sys.exit(19 if reply==b'QUEUED\\n' else 0)\n"
        "except (OSError,TimeoutError): sys.exit(0)\n"
    )
    rc,_=command('runuser -u '+shlex.quote(principal)+' -- /usr/bin/python3 -c '+shlex.quote(code),ok=False)
    assert rc == (19 if accepted else 0),('unexpected_intent_ack_or_probe_error',principal,rc)
    return path

def no_modal():
    return not visible('Maj Vaj.*Open account connections')

try:
    s.expect('login:',timeout=240);s.sendline('root');s.expect('Password:');s.sendline('d2-serial-fixture-714');s.expect(r'root@[^\r\n]+#',timeout=80)
    s.sendline("stty -echo; unset PROMPT_COMMAND; PS1=''; bind 'set enable-bracketed-paste off'; printf '\\nD2_%s\\n' READY")
    s.expect(r'D2_READY\r*\n')
    command('bash /root/d2-setup.sh',timeout=160)
    print('PASS R4 genuine offline Debian 13 protected vault and LightDM fixture',flush=True)
    empty=ensure_empty()

    # Explicitly malformed installed desktop-handler calls are refusals
    # only. No positive direct handler invocation replaces Chromium.
    for uri in ('majvaj-account:open?ticket=stale','majvaj-account:OPEN',
                'majvaj-account:open#replay','majvaj-account:%6Fpen'):
        rc,_=command('runuser -u maj-vaj-console -- /opt/maj-vaj/scripts/account_handoff_wrapper.sh '+shlex.quote(uri),ok=False)
        assert rc != 0,('malformed_desktop_uri_accepted',uri)
    assert no_modal() and not session('maj-vaj-credential-operator')
    assert read_status()==empty
    print('R4_INSTALLED_INVALID_URI_PASS',flush=True)

    # Real browser consent and native Tk confirmation, then explicit
    # cancellation. This is the authoritative positive entry path.
    sid,browser,modal=console_modal('01-r4-initial')
    x('windowfocus '+modal);key('Escape')
    wait_cmd('! '+root_x()+" xdotool search --onlyvisible --name 'Maj Vaj.*Open account connections' | grep -q .",25)
    assert assert_active('maj-vaj-console')==sid
    assert no_modal() and read_status()==empty
    uid=int(command('id -u maj-vaj-console')[1].splitlines()[-1])
    socket_path='/run/user/'+str(uid)+'/maj-vaj-account-handoff-intent.sock'
    wait_cmd('test -S '+shlex.quote(socket_path),25)
    mode=command("stat -c '%u %a %F' "+shlex.quote(socket_path))[1].splitlines()[-1].strip()
    assert mode==str(uid)+' 600 socket',('installed_intent_socket_mode_mismatch',mode)
    print('R4_INSTALLED_BROWSER_BASELINE_PASS',flush=True)

    # Installed IPC: other UID must never receive the UI ACK, including
    # root (which can pass filesystem DAC but fails SO_PEERCRED).
    for other in ('root','maj-vaj-credential-operator','maj-vaj',
                  'maj-vaj-native','maj-vaj-dashboard-auth'):
        socket_probe(other,b'OPEN\n')
    assert no_modal() and session('maj-vaj-console')==sid
    assert not session('maj-vaj-credential-operator')
    assert read_status()==empty
    print('R4_INSTALLED_FOREIGN_PEER_PASS',flush=True)

    # Same UID malformed, oversized, trailing data, NUL and no-EOF
    # frames must not receive QUEUED. The original listener must survive.
    for bad,hold in ((b'',False),(b'OPEN',False),
                     (b'OPEN\nEXTRA',False),(b'OPEN\r\n',False),
                     (b'ENROLL\n',False),(b'OPEN\n'+b'x'*1024,False),
                     (b'OPEN\n',True)):
        socket_probe('maj-vaj-console',bad,hold_open=hold)
    assert no_modal() and session('maj-vaj-console')==sid
    assert read_status()==empty
    print('R4_INSTALLED_MALFORMED_IPC_PASS',flush=True)

    # The valid fixed same-UID frame is intentionally just a UI intent.
    # Verify that it creates the original Tk confirmation but never
    # bypasses it. An explicit Escape must keep the real PAM session.
    time.sleep(2.5)
    socket_probe('maj-vaj-console',b'OPEN\n',accepted=True)
    forged_modal=wait_window('Maj Vaj.*Open account connections',30)
    assert not session('maj-vaj-credential-operator')
    assert read_status()==empty
    shot('02-fixed-intent-still-requires-review')
    x('windowfocus '+forged_modal);key('Escape')
    wait_cmd('! '+root_x()+" xdotool search --onlyvisible --name 'Maj Vaj.*Open account connections' | grep -q .",25)
    assert assert_active('maj-vaj-console')==sid
    assert read_status()==empty
    print('R4_INSTALLED_SAME_UID_REQUIRES_REVIEW_PASS',flush=True)

    # Save an old Xauthority cookie solely in this disposable guest,
    # mode 0600 and owned by the console. Neither it nor its digest is
    # exported, echoed, or included in CI artifacts.
    old_auth='/home/maj-vaj-console/.r4-stale-Xauthority'
    command('install -o maj-vaj-console -g maj-vaj-console -m 0600 /home/maj-vaj-console/.Xauthority '+shlex.quote(old_auth))
    old_session=sid
    command('loginctl terminate-session '+shlex.quote(old_session))
    wait_cmd('! loginctl show-session '+shlex.quote(old_session)+' >/dev/null 2>&1',40)
    greeter()
    wait_cmd('test ! -S '+shlex.quote(socket_path),40)
    socket_probe('maj-vaj-console',b'OPEN\n')  # retired socket cannot ACK
    assert read_status()==empty
    print('R4_INSTALLED_RETIRED_IPC_PASS',flush=True)

    # Log in again for a *genuinely different* PAM/logind session.
    # Old Xauthority from the prior server must not access its X display.
    new_sid=login('maj-vaj-console','d2-console-fixture-813')
    assert new_sid!=old_session
    assert assert_active('maj-vaj-console')==new_sid
    current_display=ACTIVE_DISPLAY
    live='runuser -u maj-vaj-console -- env DISPLAY='+current_display+' XAUTHORITY=/home/maj-vaj-console/.Xauthority /usr/bin/xdotool getdisplaygeometry'
    stale='runuser -u maj-vaj-console -- env DISPLAY='+current_display+' XAUTHORITY='+shlex.quote(old_auth)+' /usr/bin/xdotool getdisplaygeometry'
    rc,_=command(live,ok=False)
    assert rc==0,('live_X_cookie_not_accepted',rc)
    rc,_=command(stale,ok=False)
    assert rc!=0,('old_X_cookie_replay_accepted',rc)
    command('rm -f '+shlex.quote(old_auth))
    wait_cmd('test -S '+shlex.quote(socket_path),40)
    socket_probe('root',b'OPEN\n')
    assert read_status()==empty and not session('maj-vaj-credential-operator')
    print('R4_INSTALLED_STALE_COOKIE_REFUSAL_PASS',flush=True)

    # Installed filesystem and protected vault principal boundaries:
    # no unauthorized UID may read root key material, any console home
    # cookie or the current status RPC credential path.
    for outsider in ('maj-vaj-console','maj-vaj-credential-operator',
                     'maj-vaj-native','maj-vaj-dashboard-auth'):
        for secret_path in ('/etc/maj-vaj/credential-keys/venue-vault-key',
                            '/etc/maj-vaj/credential-keys/provider-tag-key'):
            rc,_=command('runuser -u '+outsider+' -- /usr/bin/test ! -r '+shlex.quote(secret_path),ok=False)
            assert rc==0,('forbidden_secret_readable',outsider,secret_path)
    for outsider in ('maj-vaj-credential-operator','maj-vaj-native',
                     'maj-vaj-dashboard-auth'):
        rc,_=command('runuser -u '+outsider+' -- /usr/bin/test ! -r /home/maj-vaj-console/.Xauthority',ok=False)
        assert rc==0,('forbidden_console_cookie_readable',outsider)
    rc,_=command("runuser -u maj-vaj-console -- /opt/maj-vaj/venv/bin/python -I -B -c "+
        shlex.quote("import sys;sys.path.insert(0,'/opt/maj-vaj');from paper_credential_vault import send;assert send({'op':'status'})['ok']"),ok=False)
    assert rc!=0,('console_principal_can_query_vault_status',rc)
    assert read_status()==empty
    print('R4_INSTALLED_PRINCIPAL_ISOLATION_PASS',flush=True)

    # A fresh *operator* PAM login remains a separate graphical session,
    # even after all denied requests. No credentials are entered.
    command('loginctl terminate-session '+shlex.quote(new_sid))
    wait_cmd('! loginctl show-session '+shlex.quote(new_sid)+' >/dev/null 2>&1',40)
    greeter()
    op_id,op_window=choose_operator()
    assert op_id not in (old_session,new_sid)
    assert assert_active('maj-vaj-credential-operator')==op_id
    assert read_status()==empty
    shot('03-separate-operator-after-denials')
    end_operator(op_id,op_window)
    assert read_status()==empty
    print('R4_INSTALLED_OPERATOR_SEPARATION_PASS',flush=True)
    print('R4_NATIVE_ADVERSARIAL_PASS',flush=True)

finally:
    # Cleanup of synthetic values and service/session boundary even after failure.
    try:shot('final')
    except Exception:pass
    try:
        c="import sys;sys.path.insert(0,'/opt/maj-vaj');from paper_credential_vault import send;assert send({'op':'remove','slots':['kraken_read_key','kraken_read_secret']})['ok']"
        command("runuser -u maj-vaj-credential-operator -- /opt/maj-vaj/venv/bin/python -I -B -c "+shlex.quote(c),ok=False)
        command('systemctl stop lightdm maj-vaj-paper-dashboard.service maj-vaj-paper-dashboard-backend.service maj-vaj-dashboard-backend.socket maj-vaj-paper-vault.service',timeout=45,ok=False)
        s.sendline('poweroff');s.expect(pexpect.EOF,timeout=70)
    except Exception:
        s.close(force=True)
