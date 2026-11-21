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


try:
    s.expect('login:',timeout=240);s.sendline('root');s.expect('Password:');s.sendline('d2-serial-fixture-714');s.expect(r'root@[^\r\n]+#',timeout=80)
    s.sendline("stty -echo; unset PROMPT_COMMAND; PS1=''; bind 'set enable-bracketed-paste off'; printf '\\nD2_%s\\n' READY")
    s.expect(r'D2_READY\r*\n')
    command('bash /root/d2-setup.sh',timeout=160)
    print('PASS installed Debian guest with offline, separately enabled vault',flush=True)
    assert not session('maj-vaj-credential-operator')
    ensure_empty()

    keys='/etc/maj-vaj/credential-keys'
    unit='maj-vaj-paper-vault.service'
    command('test "$(stat -c %a '+keys+')" = 700')
    for name in ('venue-vault-key','provider-tag-key'):
        command('test "$(stat -c %u:%g:%a '+keys+'/'+name+')" = 0:0:600')
        for outsider in ('maj-vaj-console','maj-vaj-credential-operator','maj-vaj','maj-vaj-native'):
            rc,_=command('runuser -u '+outsider+' -- /usr/bin/test ! -r '+keys+'/'+name,ok=False)
            assert rc==0,('unauthorized_key_read',name,outsider)
    _,u=command('systemctl show -p LoadCredential --value '+unit)
    assert 'venue-vault-key:' in u and 'provider-tag-key:' in u
    _,isolation=command('systemctl show -p PrivateNetwork -p RestrictAddressFamilies -p User --value '+unit)
    assert 'maj-vaj-vault' in isolation and 'yes' in isolation and 'AF_UNIX' in isolation
    print('R2B_INSTALLED_SYSTEMD_KEY_CUSTODY_PASS',flush=True)

    op_id,win=choose_operator()
    assert assert_active('maj-vaj-credential-operator')==op_id
    fill_pair('r2b-synthetic-read-key','r2b-synthetic-read-secret')
    click(104,349);time.sleep(2)
    status=read_status()
    assert status['kraken_read_key']==status['kraken_read_secret']=='stored_unverified',status
    shot('01-synthetic-encrypted-operator-save')
    print('R2B_INSTALLED_SYNTHETIC_SAVE_PASS',flush=True)

    # The existing installed socket intentionally exposes no provider test,
    # native approval, migration command or worker receipt operation.
    # An *authenticated operator* still may not dispatch these.
    for operation in ('test_connection','approve_provider_test',
                      'review_legacy_migration','submit_provider_receipt'):
        code="import sys;sys.path.insert(0,'/opt/maj-vaj');from paper_credential_vault import send;r=send({'op':"+repr(operation)+",'role':'kraken_read'});assert r=={'ok':False,'reason':'credential_request_refused'}"
        rc,_=command("runuser -u maj-vaj-credential-operator -- /opt/maj-vaj/venv/bin/python -I -B -c "+shlex.quote(code),ok=False)
        assert rc==0,('unapproved_provider_rpc_was_admitted',operation)
    assert read_status()==status
    print('R2B_INSTALLED_UNAPPROVED_PROVIDER_REFUSAL_PASS',flush=True)

    # Genuine post-enrollment service restart retains the encrypted pair.
    restart_vault()
    assert read_status()==status
    print('R2B_INSTALLED_PERSISTENCE_PASS',flush=True)

    # Missing independent tag key must refuse service startup, and must not
    # erase or silently migrate the persisted encrypted vault.
    command('systemctl stop '+unit)
    command('mv '+keys+'/provider-tag-key '+keys+'/.r2b-staged-provider-tag-original')
    rc,_=command('systemctl start '+unit,timeout=30,ok=False)
    assert rc!=0,('missing_provider_tag_key_was_accepted',rc)
    rc,_=command('systemctl is-active --quiet '+unit,ok=False)
    assert rc!=0
    command('mv '+keys+'/.r2b-staged-provider-tag-original '+keys+'/provider-tag-key')
    command('systemctl reset-failed '+unit,ok=False)
    command('systemctl start '+unit)
    wait_cmd('test -S /run/maj-vaj-paper-vault-status/status.sock',40)
    assert read_status()==status
    print('R2B_INSTALLED_MISSING_TAG_KEY_REFUSAL_PASS',flush=True)

    # Replacing only the tag key with a *new synthetic random* value must
    # invalidate the opaque role-generation/key binding and refuse startup.
    command('systemctl stop '+unit)
    command('mv '+keys+'/provider-tag-key '+keys+'/.r2b-staged-provider-tag-original')
    command("python3 -c "+shlex.quote("from pathlib import Path;import os;p=Path('"+keys+"/provider-tag-key');p.write_bytes(os.urandom(32));p.chmod(0o600)"))
    rc,_=command('systemctl start '+unit,timeout=30,ok=False)
    assert rc!=0,('mismatched_provider_tag_key_was_accepted',rc)
    command('systemctl stop '+unit,ok=False)
    command('rm -f '+keys+'/provider-tag-key')
    command('mv '+keys+'/.r2b-staged-provider-tag-original '+keys+'/provider-tag-key')
    command('systemctl reset-failed '+unit,ok=False)
    command('systemctl start '+unit)
    wait_cmd('test -S /run/maj-vaj-paper-vault-status/status.sock',40)
    assert read_status()==status
    print('R2B_INSTALLED_WRONG_TAG_KEY_REFUSAL_PASS',flush=True)

    # A mismatched venue encryption key is separate from the tag key.
    # No automatic reset or adoption of old ciphertext is permitted.
    command('systemctl stop '+unit)
    command('mv '+keys+'/venue-vault-key '+keys+'/.r2b-staged-venue-original')
    command("/opt/maj-vaj/venv/bin/python -c "+shlex.quote("from pathlib import Path;from cryptography.fernet import Fernet;p=Path('"+keys+"/venue-vault-key');p.write_bytes(Fernet.generate_key());p.chmod(0o600)"))
    rc,_=command('systemctl start '+unit,timeout=30,ok=False)
    assert rc!=0,('mismatched_venue_encryption_key_was_accepted',rc)
    command('systemctl stop '+unit,ok=False)
    command('rm -f '+keys+'/venue-vault-key')
    command('mv '+keys+'/.r2b-staged-venue-original '+keys+'/venue-vault-key')
    command('systemctl reset-failed '+unit,ok=False)
    command('systemctl start '+unit)
    wait_cmd('test -S /run/maj-vaj-paper-vault-status/status.sock',40)
    assert read_status()==status
    print('R2B_INSTALLED_WRONG_ENCRYPTION_KEY_REFUSAL_PASS',flush=True)

    # Status is still non-authorizing after every restart.
    c="import sys,json,urllib.request;sys.path.insert(0,'/opt/maj-vaj/scripts');from qualify_account_browser import TOKEN;r=json.load(urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8765/api/setup',headers={'Authorization':'Bearer '+TOKEN})));assert r['credentials']['kraken_read_key']=='stored_unverified' and not r['connections_verified'] and not r['deployment_ready'];print('PASS status-only PAPER API')"
    print(command('/opt/maj-vaj/venv/bin/python -c '+shlex.quote(c))[1],flush=True)
    shot('02-recovered-protected-operator-window')
    end_operator(op_id,win)
    assert read_status()==status
    print('R2B_INSTALLED_PAPER_AUTHORITY_REFUSAL_PASS',flush=True)
    print('R2B_NATIVE_KEY_CUSTODY_PASS',flush=True)

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
