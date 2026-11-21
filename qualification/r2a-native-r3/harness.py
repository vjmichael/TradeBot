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
def x(args,ok=True):
    return command('DISPLAY=:0 XAUTHORITY=/run/lightdm/root/:0 xdotool '+args,ok=ok)[1]
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
    return wait_cmd('DISPLAY=:0 XAUTHORITY=/run/lightdm/root/:0 xdotool search --onlyvisible --name '+shlex.quote(name)+' | head -1 | grep -E "^[0-9]+$"',timeout).splitlines()[-1]
def greeter():
    # Linux comm is limited to 15 characters; use anchored argv and the same UID.
    # A process match is only readiness, never a substitute for actual PAM/session assertions.
    try:
        wait_cmd("pgrep -u lightdm -f '^([^[:space:]]*/)?lightdm-gtk-greeter([[:space:]]|$)' >/dev/null && test -e /run/lightdm/root/:0",75)
    except RuntimeError:
        _, report=command("systemctl show lightdm -p ActiveState -p SubState -p Result; if test -e /run/lightdm/root/:0; then echo display_cookie_present; else echo display_cookie_missing; fi; if pgrep -u lightdm -f '^([^[:space:]]*/)?lightdm-gtk-greeter([[:space:]]|$)' >/dev/null; then echo greeter_process_present; else echo greeter_process_missing; fi",ok=False)
        (out/'greeter-readiness.txt').write_text(report+'\n')
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
    rc,_=command('DISPLAY=:0 XAUTHORITY=/run/lightdm/root/:0 xdotool search --onlyvisible --name '+shlex.quote(title)+' >/dev/null 2>&1',ok=False)
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
    print('PASS installed Debian guest preflight, offline vault and real LightDM',flush=True)
    ensure_empty()

    # 1. Real logind lock event while the actual browser-originated Tk
    # confirmation is open. If a window survives long enough to accept
    # Enter, it must refuse the stale review rather than sign out.
    locked_id,locked_browser,locked_modal=console_modal('01-lock')
    command('loginctl lock-session '+shlex.quote(locked_id))
    time.sleep(3)
    if visible('Maj Vaj.*Open account connections'):
        x('windowfocus '+locked_modal);key('Return')
    wait_cmd("! DISPLAY=:0 XAUTHORITY=/run/lightdm/root/:0 xdotool search --onlyvisible --name 'Maj Vaj.*Open account connections' | grep -q .",30)
    assert not session('maj-vaj-credential-operator')
    ensure_empty()
    shot('02-after-session-lock')
    if session('maj-vaj-console'):
        command('loginctl terminate-session '+shlex.quote(locked_id))
    greeter()
    print('R3_INSTALLED_LOCK_REFUSAL_PASS',flush=True)

    # 2. Switch the real seat back to LightDM while a *new* native
    # confirmation is open, using LightDM's own switching command. No
    # helper, URI or vault mutation is permitted on the positive path.
    switch_id,switch_browser,switch_modal=console_modal('03-switch')
    # The fixture serial shell is not a LightDM session and does not
    # inherit XDG_SEAT_PATH. Query the installed display manager's own
    # seat registry, then supply its real seat path to dm-tool exactly as
    # prescribed for out-of-session invocation (not a mock switch).
    _,seats=command('/usr/bin/dm-tool list-seats',timeout=12)
    assert re.search(r'\\bSeat0\\b',seats), 'installed_LightDM_seat0_missing'
    seat_path='/org/freedesktop/DisplayManager/Seat0'
    switch_env='XDG_SEAT_PATH='+shlex.quote(seat_path)+' DISPLAY=:0 XAUTHORITY=/home/maj-vaj-console/.Xauthority XDG_RUNTIME_DIR=/run/user/$(id -u maj-vaj-console)'
    command('runuser -u maj-vaj-console -- env '+switch_env+' /usr/bin/dm-tool switch-to-greeter',timeout=20)
    greeter()
    assert not session('maj-vaj-credential-operator')
    ensure_empty()
    rc,active=command('loginctl show-session '+shlex.quote(switch_id)+' --property Active --value',ok=False)
    assert rc != 0 or active.strip() == 'no',('seat_did_not_change',rc,active)
    shot('04-seat-switched-to-greeter')
    if session('maj-vaj-console'):
        command('loginctl terminate-session '+shlex.quote(switch_id))
    greeter()
    print('R3_INSTALLED_SEAT_SWITCH_PASS',flush=True)

    # 3. Abrupt real PAM console logout during a third protected prompt.
    logout_id,_,logout_modal=console_modal('05-logout')
    command('loginctl terminate-session '+shlex.quote(logout_id))
    wait_cmd('! loginctl show-session '+shlex.quote(logout_id)+' >/dev/null 2>&1',40)
    greeter()
    assert not visible('Maj Vaj.*Open account connections')
    assert not session('maj-vaj-credential-operator')
    ensure_empty()
    shot('06-after-logout')
    print('R3_INSTALLED_LOGOUT_REFUSAL_PASS',flush=True)

    # 4. A new, distinct PAM credential-operator session opens real Tk.
    # Fill both masked fields and explicit confirmation, but exit using the
    # installed End account session control without saving.
    operator_id,operator_win=choose_operator()
    ensure_empty()
    shot('07-operator-empty')
    fill_pair('r3-unsaved-key','r3-unsaved-secret')
    shot('08-operator-unsaved')
    end_operator(operator_id,operator_win)
    ensure_empty()
    print('R3_INSTALLED_UNSAVED_EXIT_PASS',flush=True)

    # 5. Repeat the interrupted entry with unexpected operator PAM logout:
    # no encryption write or partial pair is permitted.
    interrupted_id,interrupted_win=choose_operator()
    fill_pair('r3-aborted-key','r3-aborted-secret')
    shot('09-abrupt-unsaved-entry')
    command('loginctl terminate-session '+shlex.quote(interrupted_id))
    wait_cmd('! loginctl show-session '+shlex.quote(interrupted_id)+' >/dev/null 2>&1',40)
    greeter()
    ensure_empty()
    print('R3_INSTALLED_UNSAVED_LOGOUT_PASS',flush=True)

    # 6. Valid graphical synthetic paired write. The authoritative
    # encrypted vault must retain both slots over genuine service restart.
    saved_id,saved_win=choose_operator()
    fill_pair('r3-synthetic-kraken-read-key','r3-synthetic-kraken-read-secret')
    click(104,349);time.sleep(2)
    shot('10-credential-saved')
    saved=read_status()
    assert saved['kraken_read_key']==saved['kraken_read_secret']=='stored_unverified',saved
    restart_vault()
    assert read_status()==saved
    print('R3_INSTALLED_PERSISTENCE_RESTART_PASS',flush=True)

    # 7. Local graphical removal, vault restart and clean actual operator
    # logout. Neither PAPER readiness nor provider authentication is granted.
    x('windowfocus '+saved_win)
    click(54,316)
    click(214,349);time.sleep(2)
    shot('11-local-removal')
    ensure_empty()
    restart_vault()
    ensure_empty()
    c="import sys,json,urllib.request;sys.path.insert(0,'/opt/maj-vaj/scripts');from qualify_account_browser import TOKEN;r=json.load(urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8765/api/setup',headers={'Authorization':'Bearer '+TOKEN})));assert not r['connections_verified'] and not r['deployment_ready'];print('PASS status-only PAPER API')"
    print(command('/opt/maj-vaj/venv/bin/python -c '+shlex.quote(c))[1],flush=True)
    end_operator(saved_id,saved_win)
    ensure_empty()
    shot('12-final-greeter')
    print('R3_INSTALLED_GUI_REMOVAL_PASS',flush=True)
    print('R3_NATIVE_INSTALLED_PASS',flush=True)

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
