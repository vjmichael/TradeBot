"""D2: disposable offline QEMU desktop. No direct production helper invocation for positive handoff.
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
    wait_cmd('pgrep -u lightdm -x lightdm-gtk-greeter >/dev/null && test -e /run/lightdm/root/:0',75)
    time.sleep(3)
def login(user,password):
    greeter()
    key('ctrl+a');type_text(user);key('Tab');time.sleep(.5);type_text(password);key('Return')
def read_status():
    c="import sys,json;sys.path.insert(0,'/opt/maj-vaj');from paper_credential_vault import send,STATUS_SOCKET;print(json.dumps(send({'op':'status'},STATUS_SOCKET)))"
    raw=command("runuser -u maj-vaj -- /opt/maj-vaj/venv/bin/python -I -B -c "+shlex.quote(c))[1]
    return json.loads(raw.splitlines()[-1])['credentials']
def find(text):
    key('ctrl+f');type_text(text);time.sleep(.5);key('Escape')
try:
    s.expect('login:',timeout=240);s.sendline('root');s.expect('Password:');s.sendline('d2-serial-fixture-714');s.expect(r'root@[^\r\n]+#',timeout=80)
    s.sendline("stty -echo; unset PROMPT_COMMAND; PS1=''; bind 'set enable-bracketed-paste off'; printf '\\nD2_%s\\n' READY")
    s.expect(r'D2_READY\r*\n')
    command('bash /root/d2-setup.sh',timeout=160)
    print('PASS installed Debian guest preflight, isolated display and vault',flush=True)
    greeter();shot('01-greeter')
    login('maj-vaj-console','d2-console-fixture-813')
    console_id=assert_active('maj-vaj-console')
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
    find('Open secure account sign-in');key('Return');time.sleep(3);shot('06-protocol-prompt')
    # Actual Chrome external-protocol dialog may require one more explicit activation.
    # No manual xdg-open / helper invocation is allowed on the positive path.
    try:
        modal=wait_window('Maj Vaj.*Open account connections',12)
    except Exception:
        key('Tab','Return');time.sleep(3);shot('07-protocol-response')
        modal=wait_window('Maj Vaj.*Open account connections',20)
    print('PASS browser click invoked installed native handler and real confirmation',flush=True)
    shot('08-native-confirmation')
    assert session('maj-vaj-console')==console_id
    x('windowfocus '+modal);key('Return')
    wait_cmd('! loginctl show-session '+shlex.quote(console_id)+' >/dev/null 2>&1',40)
    greeter();shot('09-return-to-greeter')
    assert not session('maj-vaj-credential-operator')
    print('PASS confirmed sign-out; no automatic credential login',flush=True)
    # Fresh independent login, not credential impersonation or direct vault enrollment.
    login('maj-vaj-credential-operator','d2-operator-fixture-916')
    operator_id=assert_active('maj-vaj-credential-operator')
    assert operator_id!=console_id
    win=wait_window('Maj Vaj.*Account connections',30)
    assert read_status()['kraken_read_key']=='not_configured'
    x('windowfocus '+win);shot('10-credential-window')
    # Exact Tk window top-left is +30+30; query geometry and use real keyboard traversal.
    click(180,190);type_text('d2-synthetic-kraken-read-key');key('Tab');type_text('d2-synthetic-kraken-read-secret');key('Tab','space','Tab','Return');time.sleep(1)
    shot('11-enrollment')
    status=read_status()
    assert status['kraken_read_key']==status['kraken_read_secret']=='stored_unverified',status
    print('PASS independent graphical paired enrollment and status-only confirmation',flush=True)
    # Verify no usable trading authority or provider connection.
    c="import sys,json,urllib.request;sys.path.insert(0,'/opt/maj-vaj/scripts');from qualify_account_browser import TOKEN;r=json.load(urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8765/api/setup',headers={'Authorization':'Bearer '+TOKEN})));assert r['credentials']['kraken_read_key']=='stored_unverified' and not r['connections_verified'] and not r['deployment_ready'];print('PASS status-only PAPER API')"
    print(command('/opt/maj-vaj/venv/bin/python -c '+shlex.quote(c))[1],flush=True)
    print('D2_INTEGRATED_PASS',flush=True)
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
