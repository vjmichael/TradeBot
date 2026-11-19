"""Local browser integration, no exchange secrets or external calls. Build assets first."""
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen
from cryptography.fernet import Fernet
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
TOKEN='browser-test-only-access-'*3


def exercise(page, reload_page, errors, external, mode):
    expect(page.get_by_role('heading',name='Maj Vaj Trade Bot',exact=True)).to_be_visible()
    def sign_in():
        page.get_by_label('Admin access token').fill(TOKEN);page.get_by_role('button',name='Sign in',exact=True).click();expect(page.get_by_role('heading',name='Dashboard',exact=True)).to_be_visible()
    page.get_by_label('Admin access token').fill('bad-token');page.get_by_role('button',name='Sign in',exact=True).click();expect(page.get_by_role('alert')).to_contain_text('Authentication failed')
    sign_in()
    expect(page.locator('.brand')).to_have_text('Maj Vaj Trade Bot')
    if mode=='real local API':expect(page).to_have_title('Maj Vaj Trade Bot — Trading desk')
    page.get_by_role('button',name='Settings',exact=True).click()
    for label,value in [('Grok API key','synthetic-grok-test'),('Kraken read-only API key','synthetic-kraken-key'),('Kraken API secret','synthetic-kraken-secret'),('Telegram Desk token','synthetic-desk-token'),('Telegram Recovery token','synthetic-recovery-token')]:page.get_by_label(label).fill(value)
    page.get_by_role('button',name='Save credentials').click();expect(page.get_by_role('status')).to_contain_text('Credentials stored');expect(page.get_by_label('Grok API key')).to_have_value('')
    page.get_by_label('Total virtual capital (USD)').fill('1000');page.get_by_role('button',name='Save paper capital').click();expect(page.get_by_role('status')).to_contain_text('Paper capital saved')
    page.get_by_role('button',name='Continue to add instance').click()
    page.get_by_label('Instance name').fill('Bitcoin research');page.get_by_label('Grok model ID').fill('synthetic-model');page.get_by_role('button',name='Continue').click();page.get_by_label('Virtual allocation (USD)').fill('400');page.get_by_label('Monthly research budget (USD)').fill('10');page.get_by_role('button',name='Continue').click();page.get_by_role('button',name='Register paper instance').click();expect(page.get_by_role('status')).to_contain_text('Instance registered');expect(page.get_by_role('heading',name='Bitcoin research')).to_be_visible()
    page.get_by_role('button',name='+ Add instance').click();page.get_by_label('Instance name').fill('Numerical ETH');page.get_by_label('Market',exact=True).select_option('ETH/USD');page.get_by_label('Strategy family').select_option('numerical-baseline');expect(page.get_by_text('No Grok model or API key required for numerical-only research.')).to_be_visible();page.get_by_role('button',name='Continue').click();page.get_by_label('Virtual allocation (USD)').fill('100');page.get_by_role('button',name='Continue').click();page.get_by_role('button',name='Register paper instance').click();expect(page.get_by_role('heading',name='Numerical ETH')).to_be_visible()
    if mode=='real local API':
        page.get_by_role('button',name='Financial Goals',exact=True).click()
        expect(page.get_by_label('Monthly expenses (USD)')).to_be_visible()
        page.get_by_label('Monthly expenses (USD)').fill('2500')
        page.get_by_label('Other monthly take-home income (USD)').fill('1500')
        page.get_by_label('Desired monthly savings (USD)').fill('500')
        page.get_by_label('Estimated tax rate (%)').fill('20')
        page.get_by_label('Daily goal type').select_option('usd')
        page.get_by_label('Daily target (USD)').fill('100')
        page.get_by_label('Pause new entries after the goal is reached').check()
        page.get_by_role('button',name='Save planning settings').click()
        expect(page.get_by_text('$1,875.00',exact=True)).to_be_visible()
        expect(page.get_by_text('Daily goal entry restriction is enabled.',exact=False)).to_be_visible()
        expect(page.get_by_text('Paper profits are simulated, not household income.',exact=False)).to_be_visible()
        page.get_by_role('button',name='Dashboard',exact=True).click()
    expect(page.get_by_label('Paper trading for Bitcoin research')).to_be_checked();page.get_by_label('Paper trading for Bitcoin research').click();expect(page.get_by_role('heading',name='Real-money trading unavailable')).to_be_visible();page.get_by_role('button',name='Keep paper trading').click();expect(page.get_by_label('Paper trading for Bitcoin research')).to_be_checked()
    page.once('dialog',lambda dialog: dialog.accept());page.get_by_role('button',name='Pause all').click();expect(page.get_by_role('status')).to_contain_text('All instances paused')
    page.get_by_role('button',name='Calendar',exact=True).click();expect(page.get_by_text('Awaiting an authorized calendar source.')).to_be_visible();page.get_by_role('button',name='Load economic statistics').click();expect(page.get_by_text('Official observations, without AI.',exact=False)).to_be_visible();
    if mode=='real local API':expect(page.get_by_text('No observed records.',exact=True)).to_be_visible()
    else:expect(page.get_by_text('Fixture unemployment rate')).to_be_visible()
    page.get_by_role('button',name='Review saved history').click()
    expect(page.get_by_text('Saved revisions only; release-time vintages are not verified.')).to_be_visible()
    if mode=='real local API':expect(page.get_by_text('0 saved revisions · 0 distinct records')).to_be_visible()
    else:expect(page.get_by_text('1 saved revisions · 1 distinct records')).to_be_visible()
    page.get_by_role('button',name='Load producer prices and earnings').click()
    if mode=='real local API':expect(page.get_by_text('No observed records.',exact=True)).to_be_visible()
    else:expect(page.get_by_text('Fixture producer prices')).to_be_visible()
    page.get_by_role('button',name='Load Federal Reserve releases').click();expect(page.get_by_text('Source unavailable or out of date.',exact=False)).to_be_visible();page.get_by_role('button',name='Market Research',exact=True).click();page.get_by_role('button',name='Check preparation').click();expect(page.get_by_text('BTC/USD · 0 / 43800 hourly observations',exact=False)).to_be_visible();expect(page.get_by_text('Training readiness requires review.',exact=False)).to_be_visible();page.get_by_role('button',name='Review BTC/USD history').click();expect(page.get_by_role('heading',name='BTC/USD history review')).to_be_visible();expect(page.get_by_text('No observed history: 43800 hours')).to_be_visible();expect(page.get_by_text('Training and paper trading readiness not established.')).to_be_visible();expect(page.get_by_role('button',name='Stop data collection')).to_be_disabled();page.get_by_role('button',name='Fetch market data').click();expect(page.get_by_role('alert')).to_contain_text('Market data unavailable');page.get_by_role('button',name='Load activity profile').click();expect(page.get_by_text('Awaiting imported or observed market history. No measured activity profile is available.')).to_be_visible();page.get_by_role('button',name='Load market conditions').click();expect(page.get_by_text('No observed order book samples.')).to_be_visible();expect(page.get_by_text('No complete five-minute volatility observations.')).to_be_visible();page.get_by_role('button',name='Check storage').click();expect(page.get_by_text('Historical storage is within its configured limits.')).to_be_visible();expect(page.get_by_text('Market history and records:',exact=False)).to_be_visible();expect(page.get_by_text('Schedule disabled',exact=True)).to_be_visible();page.once('dialog',lambda dialog:dialog.accept());page.get_by_role('button',name='Enable scheduled collection').click();expect(page.get_by_text('Schedule enabled',exact=True)).to_be_visible();expect(page.get_by_text('Data service is not confirmed running.',exact=False)).to_be_visible();expect(page.get_by_role('button',name='Prepare history')).to_be_disabled();expect(page.get_by_role('button',name='Create recovery copy')).to_be_disabled();page.get_by_role('button',name='Disable scheduled collection').click();expect(page.get_by_text('Schedule disabled',exact=True)).to_be_visible();page.get_by_role('button',name='Create recovery copy').click();expect(page.get_by_text('Latest completed recovery copy:',exact=False)).to_be_visible(timeout=20000);page.get_by_role('button',name='Preview old sample cleanup').click();expect(page.get_by_text('old sample receipts',exact=False)).to_be_visible(timeout=20000)
    if mode!='real local API':
        page.once('dialog',lambda dialog:dialog.accept());page.get_by_role('button',name='Clean up reviewed samples').click();expect(page.get_by_text('Last cleanup: 1 old samples removed',exact=False)).to_be_visible()
    else:expect(page.get_by_role('button',name='Clean up reviewed samples')).to_be_disabled()
    page.get_by_role('button',name='Dashboard',exact=True).click()
    if mode!='real local API':
        page.get_by_role('button',name='Market Research',exact=True).click();page.get_by_role('button',name='Start continuous collection').click();expect(page.get_by_text('Keeping data up to date',exact=True)).to_be_visible();expect(page.get_by_role('button',name='Prepare history')).to_be_disabled();page.get_by_role('button',name='Stop data collection').click();expect(page.get_by_text('Stopped — saved progress retained',exact=True)).to_be_visible();page.get_by_role('button',name='Dashboard',exact=True).click()
    if os.environ.get('TRADEBOT_SCREENSHOT'): page.screenshot(path=os.environ['TRADEBOT_SCREENSHOT'],full_page=True)
    page.set_viewport_size({'width':390,'height':844});expect(page.get_by_role('heading',name='Bitcoin research')).to_be_visible();assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    if mode == 'real local API':
        assert page.evaluate('localStorage.length === 0 && sessionStorage.length === 0 && document.cookie === ""')
    reload_page();expect(page.get_by_role('button',name='Sign in',exact=True)).to_be_visible();sign_in();expect(page.get_by_role('heading',name='Bitcoin research')).to_be_visible()
    assert not errors,errors;assert not external,external
    print('PASS: Chromium interaction flow (' + mode + '); React',page.evaluate('React.version'))

def check():
    with tempfile.TemporaryDirectory() as tmp:
        env=os.environ|{'TRADEBOT_DATA_DIR':str(Path(tmp)/'data'),'TRADEBOT_ADMIN_TOKEN':TOKEN,'TRADEBOT_VAULT_KEY':Fernet.generate_key().decode(),'TRADEBOT_VIRTUAL_CAP_CENTS':'0'}
        # Developer harness uses a private, ephemeral loopback server and fixture credentials.
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        command=[sys.executable,'-c',"import os,uvicorn; from pathlib import Path; from tradebot import create_app; from engine import MarketUnavailable; import tradebot; from unittest.mock import Mock; tradebot.candles=Mock(side_effect=MarketUnavailable('fixture_unavailable')); uvicorn.run(create_app(Path(os.environ['TRADEBOT_DATA_DIR']),os.environ['TRADEBOT_ADMIN_TOKEN'],vault_key=os.environ['TRADEBOT_VAULT_KEY'].encode()),host='127.0.0.1',port="+str(port)+",access_log=False)"]
        server=subprocess.Popen(command,env=env,cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            for _ in range(60):
                try:
                    with urlopen(base+'/health',timeout=1): break
                except OSError: time.sleep(.15)
            else: raise RuntimeError('local API did not start')
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH') or ('/usr/bin/chromium' if Path('/usr/bin/chromium').exists() else None),args=['--no-sandbox'])
                page=browser.new_page(viewport={'width':1280,'height':900}); errors=[]; external=[]
                page.on('pageerror',lambda e: errors.append(str(e)))
                page.on('request',lambda r: external.append(r.url) if not r.url.startswith(base+'/') else None)
                page.goto(base)
                assert page.evaluate('React.version') == '18.3.1', 'Release React build not used'
                exercise(page, lambda: page.reload(), errors, external, 'real local API')
                browser.close()
        finally:
            server.terminate()
            try: server.wait(timeout=5)
            except subprocess.TimeoutExpired: server.kill();server.wait()

def offline_check():
    """Explicitly offline UI fixture, not HTTP/browser integration acceptance."""
    fixture = ROOT / 'tests' / 'browser_fixture.js'
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH') or ('/usr/bin/chromium' if Path('/usr/bin/chromium').exists() else None),args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1280,'height':900}); errors=[]; external=[]
        page.on('pageerror',lambda e: errors.append(str(e)))
        page.on('request',lambda r: external.append(r.url))
        # Keep the fixture on a blank page; do not access a blocked network origin.
        def mount():
            page.set_content('<main id="root"></main>')
            page.add_style_tag(path=ROOT/'dashboard/style.css')
            if not page.evaluate('typeof window.__fixture !== "undefined"'):
                page.add_script_tag(path=fixture)
                page.add_script_tag(path=ROOT/'dashboard/vendor/react.js')
                page.add_script_tag(path=ROOT/'dashboard/vendor/react-dom.js')
            # The first entry mounts app.js; subsequent entry simulates a new UI session
            # while preserving only the fixture's server-side state.
            if page.evaluate('typeof Desk !== "undefined"'):
                page.evaluate('ReactDOM.render(React.createElement(Desk),document.getElementById("root"))')
            else:
                page.add_script_tag(path=ROOT/'dashboard/app.js')
        mount()
        exercise(page,mount,errors,external,'offline UI fixture, no network')
        browser.close()

if __name__=='__main__':
    offline_check() if '--offline' in sys.argv else check()
