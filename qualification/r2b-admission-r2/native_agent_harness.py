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

try:
    s.expect('login:',timeout=240);s.sendline('root');s.expect('Password:');s.sendline('d2-serial-fixture-714');s.expect(r'root@[^\r\n]+#',timeout=80)
    s.sendline("stty -echo; unset PROMPT_COMMAND; PS1=''; bind 'set enable-bracketed-paste off'; printf '\\nD2_%s\\n' READY")
    s.expect(r'D2_READY\r*\n')
    command('bash /root/d2-setup.sh',timeout=160)
    ensure_empty()
    # The root-only test marker activates no provider egress or execution.
    unit='maj-vaj-r2b-approval.service'
    source='/opt/maj-vaj/r2b-admission-r2/'+unit
    command('install -m0644 '+source+' /etc/systemd/system/'+unit)
    command('cmp '+source+' /etc/systemd/system/'+unit)
    command('groupadd --system maj-vaj-r2b-approval-rpc')
    command('usermod -a -G maj-vaj-r2b-approval-rpc maj-vaj-vault')
    command('systemctl daemon-reload')
    command('systemctl start '+unit,ok=False)
    assert command('systemctl is-active --quiet '+unit,ok=False)[0]!=0
    print('R2B_R2_DEFAULT_REFUSAL_PASS',flush=True)
    command('install -m0644 /dev/null /etc/maj-vaj/r2b-approval-test-enabled')
    command('systemctl start '+unit)
    wait_cmd('systemctl is-active --quiet '+unit+' && test -S /run/maj-vaj-r2b-approval/approval.sock && test -S /run/maj-vaj-r2b-approval/agent.sock',35)
    _,details=command("stat -c '%U %G %a %F' /run/maj-vaj-r2b-approval/{approval,agent}.sock /var/lib/maj-vaj-r2b-approval/ledger.sqlite3")
    (out/'native-broker-installed-permissions.txt').write_text(details+'\n')
    assert read_status()==ensure_empty()
    print('R2B_R2_TWO_PROTECTED_SOCKETS_PASS',flush=True)
    # Direct same-UID process outside real LightDM cannot impersonate GUI.
    spoof=(
       "import socket;"
       "s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);"
       "s.settimeout(5);s.connect('/run/maj-vaj-r2b-approval/agent.sock');"
       "s.sendall(b'{\\\"op\\\":\\\"review_next\\\"}\\\\n');"
       "print(s.recv(1024).decode('ascii'));s.close()"
    )
    rc,resp=command('runuser -u maj-vaj-credential-operator -- /usr/bin/python3 -c '+shlex.quote(spoof),ok=False)
    assert rc==0 and '\"ok\":false' in resp.replace(' ','').lower(),resp
    print('R2B_R2_SAME_UID_OUT_OF_SESSION_REFUSED_PASS',flush=True)

    greeter()
    sid,window=choose_operator()
    assert assert_active('maj-vaj-credential-operator')==sid
    _,diag=command("loginctl show-session "+shlex.quote(sid)+" -p Leader -p Service -p Active -p Type --no-pager; ps ax -o pid,uid,ppid,args | grep -E 'credential_graphical|credential_session_wrapper' | grep -v grep",ok=False)
    (out/'operator-original-session-leader.txt').write_text(diag+'\n')
    shot('01-real-operator-original-gui')
    # Opaque dummy credentials are never stored or used as provider secrets.
    rid=new_owner_request()
    click_review();wait_review()
    shot('02-native-pinned-approval-modal')
    key('Escape');time.sleep(2)
    wait_cmd('test "$(date +%s)" -gt 0',5)
    assert current_state(rid)=='cancelled',('native_decline_not_durable',current_state(rid))
    refusal('maj-vaj-vault',{'op':'consume','request_id':rid,'context':{
      'operation':'provider_test','role':'kraken_read',
      'generation':'a'*32,'policy':'b'*64,'inventory':'','roles':[]}})
    print('R2B_R2_NATIVE_DECLINE_CANCEL_PASS',flush=True)

    rid2=new_owner_request()
    click_review();dialog=wait_review()
    shot('03-native-approve-review')
    # Return alone may be delivered to the previously focused Tk widget.
    # Click the screenshot-observed OK control in the real 1280x800 QEMU
    # LightDM desktop; do not inject broker responses or invoke a callback.
    x('windowraise '+dialog)
    time.sleep(1)
    click(312,425)
    time.sleep(2)
    observed=current_state(rid2)
    if observed!='approved':
        shot('native-approve-click-diagnostic')
        _,trace=command('loginctl show-session '+shlex.quote(sid)+
            ' -p Active -p LockedHint -p Type -p Leader; '+
            'systemctl show maj-vaj-r2b-approval.service -p ActiveState -p Result',ok=False)
        (out/'native-approve-click-diagnostic.txt').write_text(
            'observed_state='+observed+'\n'+trace+'\n')
    assert observed=='approved',('native_yes_not_approved',observed)
    assert owner_consume(rid2)=={'ok':True}
    assert current_state(rid2)=='consumed'
    refusal('maj-vaj-vault',{'op':'consume','request_id':rid2,'context':{
      'operation':'provider_test','role':'kraken_read',
      'generation':'a'*32,'policy':'b'*64,'inventory':'','roles':[]}})
    print('R2B_R2_NATIVE_ONE_USE_APPROVAL_PASS',flush=True)

    rid3=new_owner_request()
    command('systemctl restart '+unit,timeout=30)
    wait_cmd('systemctl is-active --quiet '+unit+' && test -S /run/maj-vaj-r2b-approval/agent.sock',30)
    assert current_state(rid3)=='revoked'
    refusal('maj-vaj-vault',{'op':'consume','request_id':rid3,'context':{
      'operation':'provider_test','role':'kraken_read',
      'generation':'a'*32,'policy':'b'*64,'inventory':'','roles':[]}})
    print('R2B_R2_RESTART_REVOKES_PENDING_PASS',flush=True)

    command('loginctl terminate-session '+shlex.quote(sid))
    wait_cmd('! loginctl show-session '+shlex.quote(sid)+' >/dev/null 2>&1',40)
    greeter()
    shot('04-after-logout')
    assert not session('maj-vaj-credential-operator')
    ensure_empty()
    print('R2B_R2_LOGIN_SESSION_LOGOUT_PASS',flush=True)
    print('R2B_R2_INSTALLED_AGENT_IPC_PASS',flush=True)

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
