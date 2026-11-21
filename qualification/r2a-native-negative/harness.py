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
try:
    s.expect('login:',timeout=240);s.sendline('root');s.expect('Password:');s.sendline('d2-serial-fixture-714');s.expect(r'root@[^\r\n]+#',timeout=80)
    s.sendline("stty -echo; unset PROMPT_COMMAND; PS1=''; bind 'set enable-bracketed-paste off'; printf '\\nD2_%s\\n' READY")
    s.expect(r'D2_READY\r*\n')
    command('bash /root/d2-setup.sh',timeout=160)
    print('PASS installed Debian guest preflight, isolated display and vault',flush=True)
    greeter();shot('01-greeter')
    console_id=login('maj-vaj-console','d2-console-fixture-813')
    print('PASS real PAM console login',flush=True)
    wait_cmd("pgrep -u maj-vaj-console -f '/usr/lib/chromium/chromium' >/dev/null",90)
    win=wait_window('Maj Vaj Trade Bot',90)
    # Production browser is unmodified. Query/resize only its actual X window, then use real input.
    x('windowmove '+win+' 0 0');x('windowsize '+win+' 1024 768');x('windowfocus '+win)
    key('F11');time.sleep(2);shot('02-browser-sign-in')
    # Find the label, close Chrome Find, advance to its input.
    find('Admin access token');key('Tab');type_text('synthetic-r2ar1-browser-bearer-'*2);key('Return');time.sleep(3)
    shot('03-browser-after-login')
    find('Settings');key('Return');time.sleep(2);shot('04-browser-settings')
    find('Manage account connections');key('Return');time.sleep(2);shot('05-browser-account-help')
    # Chrome Find highlights the FIRST matching paragraph, not the anchor.
    # Tab moves from the paragraph match to the next focusable element (the
    # actual installed href), scrolling it into view. Do not press Return on
    # highlighted explanatory prose as in failed D2 44810905585.
    find('Open secure account sign-in')
    key('Tab');time.sleep(1);shot('06-link-target')
    # The actual 1024x768 Chromium screenshot proves the focused anchor
    # occupies x=52..340 y=359..407; pointer-click its center instead of
    # assuming Return dispatched an external protocol. This is a genuine
    # browser input event, not an invocation of xdg-open or the handler.
    click(195,383)
    # Never click at a fixed time: actual QEMU screenshots demonstrated a
    # delayed Chromium prompt that appeared only AFTER the previous click.
    await_browser_consent(35)
    shot('07-browser-external-app-prompt')
    # Explicit one-time browser consent, no persistent Always allow selection.
    click(650,184)
    time.sleep(2)
    shot('07-after-browser-external-confirmation')
    # Only the genuine Chromium scheme -> desktop handler may open native Tk.
    try:
        modal=wait_window('Maj Vaj.*Open account connections',30)
    except Exception:
        _, titles=command('DISPLAY=:0 XAUTHORITY=/run/lightdm/root/:0 xwininfo -root -tree | sed -n "1,70p"',ok=False)
        (out/'window-diagnostic.txt').write_text(titles+'\n')
        # Read-only source-of-truth registration and post-timeout process
        # observations; no direct invocation of the protocol handler.
        rc, reg=command("runuser -u maj-vaj-console -- env HOME=/home/maj-vaj-console XDG_RUNTIME_DIR=/run/user/$(id -u maj-vaj-console) /usr/bin/xdg-mime query default x-scheme-handler/majvaj-account",ok=False)
        (out/'handler-registration.txt').write_text('registered='+('yes' if rc==0 and reg.strip()=='maj-vaj-account-handoff.desktop' else 'no')+'\n')
        rc,_=command("pgrep -u maj-vaj-console -f 'credential_handoff.py|account_handoff_wrapper.sh' >/dev/null",ok=False)
        (out/'handler-running-after-timeout.txt').write_text('handler_visible='+('yes' if rc==0 else 'no')+'\n')

        raise
    print('PASS installed browser-to-native confirmation reached',flush=True)
    shot('08-native-confirmation')
    assert session('maj-vaj-console') == console_id
    assert not session('maj-vaj-credential-operator')
    initial = read_status()
    assert all(state == 'not_configured' for state in initial.values()), initial
    # Native negative case: Escape cancels the real Tk prompt. It must not
    # call loginctl terminate-session, start operator login or mutate vault.
    x('windowfocus ' + modal)
    key('Escape')
    wait_cmd("! DISPLAY=:0 XAUTHORITY=/run/lightdm/root/:0 xdotool search --onlyvisible --name 'Maj Vaj.*Open account connections' | grep -q .", 20)
    assert assert_active('maj-vaj-console') == console_id
    wait_window('Maj Vaj Trade Bot',20)
    assert not session('maj-vaj-credential-operator')
    assert read_status() == initial
    shot('09-cancel-keeps-browser-session')
    print('PASS installed Tk cancellation retains original PAM session, no independent login and no credentials',flush=True)
    print('R2A_NATIVE_NEGATIVE_CANCEL_PASS',flush=True)
    # Reauthenticate in the still-running browser: activating the account
    # link signs out the dashboard even if the *native* session prompt is
    # cancelled. The second request must originate from an actual browser
    # click and ordinary one-time external-protocol consent, never direct
    # helper or serial invocation.
    x('windowfocus ' + win)
    find('Admin access token');key('Tab');type_text('synthetic-r2ar1-browser-bearer-'*2);key('Return');time.sleep(2)
    find('Settings');key('Return');time.sleep(1)
    find('Manage account connections');key('Return');time.sleep(1)
    find('Open secure account sign-in');key('Tab');time.sleep(.5)
    shot('10-second-browser-link')
    click(195,383)
    await_browser_consent(35)
    shot('11-second-browser-consent')
    click(650,184)
    modal2=wait_window('Maj Vaj.*Open account connections',30)
    assert modal2 and session('maj-vaj-console') == console_id
    shot('12-native-confirmation-before-session-loss')
    # Fixture administrator forcibly removes the *real* console PAM/logind
    # session during an outstanding Tk review. No privileged credential
    # operator is introduced and the GUI prompt must die with its owner.
    command('loginctl terminate-session '+shlex.quote(console_id))
    wait_cmd('! loginctl show-session '+shlex.quote(console_id)+' >/dev/null 2>&1',40)
    greeter()
    wait_cmd("! DISPLAY=:0 XAUTHORITY=/run/lightdm/root/:0 xdotool search --onlyvisible --name 'Maj Vaj.*Open account connections' | grep -q .",20)
    assert not session('maj-vaj-credential-operator')
    assert read_status() == initial
    shot('13-greeter-after-session-loss')
    print('PASS installed modal owner-session revocation; no credential operator or vault mutation',flush=True)
    print('R2A_NATIVE_NEGATIVE_REVOKE_PASS',flush=True)
    # The vault remains authoritative and fail-closed after a genuine
    # systemd service restart; the fresh console PAM session is distinct.
    command('systemctl restart maj-vaj-paper-vault.service',timeout=45)
    wait_cmd('test -S /run/maj-vaj-paper-vault-status/status.sock',40)
    assert read_status() == initial
    recovered = login('maj-vaj-console','d2-console-fixture-813')
    assert recovered and recovered != console_id
    assert_active('maj-vaj-console')
    wait_cmd("pgrep -u maj-vaj-console -f '/usr/lib/chromium/chromium' >/dev/null",90)
    wait_window('Maj Vaj Trade Bot',60)
    assert not session('maj-vaj-credential-operator')
    assert read_status() == initial
    shot('14-fresh-pam-session-after-vault-restart')
    print('PASS fresh PAM login and vault service restart after session loss, status remains not_configured',flush=True)
    print('R2A_NATIVE_NEGATIVE_RECOVERY_PASS',flush=True)
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
