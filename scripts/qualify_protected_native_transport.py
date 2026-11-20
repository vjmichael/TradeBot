"""Disposable cross-UID PAPER test; real ledger/ORM, automated relay, synthetic book."""
import json, os, shutil, sqlite3, subprocess, sys, tempfile, time
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from financial_ledger import initialize, replay, SCALE, sha
from reserve_checkpoint import AuthorityStore
from reserve_native_migration import stage_pristine, digest
from reserve_native_rebind import issue
from reserve_terminal_transport import consume as terminal_consume, TerminalMailbox
from reserve_terminal_worker import native_step,desk_step
TRANSFER=65531
from reserve_native_wallet import rebound_bindings, projection, publish
from reserve_ipc import pinned_key
from reserve_native_evidence import NativeProducer
from reserve_snapshot import project, open_projected
from reserve_recovery import RecoverableAuthorityService, RecoveryClient
from reserve_protected import commit_packet, derive_event
from reserve_exact_review import ExactReview
from reserve_delivery import human_revalidator
from reserve_enrollment import enroll
from reserve_policy import DAY
from reserve_preview import initialize as drafts_init, save
from risk_gate import initialize as gate_init, Gate, Policy
from paper_provision import provision
AUTH,DESK,NATIVE=65533,65534,65532
I='a'*32; W='installed-paper'; PASSWORD='synthetic-qualification-only-passphrase'
IDS=dict(native_uid=NATIVE,desk_uid=DESK,authority_uid=AUTH)
ROOT=Path(__file__).resolve().parents[1]

def context(r):
    clock=lambda:int((r/'clock').read_text())
    store=AuthorityStore(r/'witness',authority_uid=AUTH,desk_uid=DESK)
    @contextmanager
    def tx():
        with closing(sqlite3.connect(r/'desk'/'desk.sqlite3',isolation_level=None)) as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE');yield db;db.commit()
    bindings=rebound_bindings(r/'records',r/'rebind.pub',**IDS)
    public=pinned_key(r/'native.pub');human=pinned_key(r/'human.pub')
    return clock,store,tx,bindings,public,human,projection(bindings,public,W)

@contextmanager
def driver_context(work):
    from freqtrade.freqtradebot import FreqtradeBot
    from freqtrade.enums import State, TradingMode, RunMode
    from freqtrade.persistence import init_db,Trade
    from freqtrade.wallets import Wallets
    from GatePaper import GatePaper
    from qualify_native_wallet_runtime import Book
    from paper_custody import AuthenticatedNativeDriver,workspace_key
    config=dict(dry_run=True,trading_mode=TradingMode.SPOT,runmode=RunMode.DRY_RUN,stake_currency='USD',dry_run_wallet=100.,initial_state='stopped',strategy='GatePaper',force_entry_enable=False,max_open_trades=1,timeframe='1h',db_url='sqlite:///'+str(work/'paper.sqlite'),exchange=dict(name='kraken',key='',secret='',pair_whitelist=['BTC/USD']),telegram={'enabled':False},api_server={'enabled':False},freqai={'enabled':False})
    with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')):
        init_db(config['db_url']);bot=object.__new__(FreqtradeBot);bot.config=config;bot.state=State.STOPPED;bot.trading_mode=TradingMode.SPOT;bot.exchange=Book(config);bot.strategy=GatePaper(config);bot.wallets=Wallets(config,bot.exchange)
        try:yield AuthenticatedNativeDriver(bot,work/'paper.sqlite',I,workspace_key(work.parents[1],I)[0])
        finally:
            engine=Trade.session.get_bind();Trade.session.remove();engine.dispose()

def simulated_gain(work):
    # Test-only settlement relay: actual native fills and fee journal, not installed broker.
    from datetime import datetime,timezone
    from ccxt import TICK_SIZE
    from freqtrade.enums import TradingMode
    from freqtrade.persistence import Trade,Order
    from paper_supervisor import custody
    with custody(work),driver_context(work) as driver:
        ex=driver.bot.exchange
        raw=ex.create_order(pair='BTC/USD',ordertype='limit',side='buy',amount=.5,rate=100.,leverage=1.)
        t=Trade(exchange='kraken',pair='BTC/USD',base_currency='BTC',stake_currency='USD',open_rate=100.,stake_amount=50.,amount=.5,leverage=1.,is_open=True,trading_mode=TradingMode.SPOT,fee_open=.005,fee_close=.005,amount_precision=.001,price_precision=.01,precision_mode=TICK_SIZE,precision_mode_price=TICK_SIZE,contract_size=1.,open_date=datetime.now(timezone.utc),strategy='GatePaper',enter_tag='mv:gain-1')
        o=Order.parse_from_ccxt_object(raw,'BTC/USD','buy');o.ft_order_tag='mv:gain-1';t.orders.append(o);Trade.session.add(t);Trade.commit();t.update_trade(o);Trade.commit()
        ex.mid=120.;raw=ex.create_order(pair='BTC/USD',ordertype='market',side='sell',amount=.5,rate=120.,leverage=1.)
        o=Order.parse_from_ccxt_object(raw,'BTC/USD','sell');o.ft_order_tag='mv-exit:gain-1:1';t.orders.append(o);Trade.commit();t.update_trade(o);Trade.commit()
        view=driver.view({'id':'gain-1','instance_id':I,'pair':'BTC/USD'})
        assert view['closed'] and view['quote']==10945000000 and all(o['fee'] is not None for o in view['orders'])
        return {'binding':driver.binding}

def claim_fixture(tx, binding, clock):
    from paper_execution import append_event
    # The intent/relay is a disposable fixture; actual ProtectionWorker verifies
    # observed terminal order arithmetic and settles through the real ledger.
    with tx() as db:
        db.execute('INSERT INTO gate_intents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('gain-1','proposal-gain',I,'BTC/USD','evidence-gain','a'*64,'b'*64,'c'*64,'paper-worker',1,5000,5100,100*SCALE,90*SCALE,120*SCALE,clock()+60,'CLAIMED',None,None))
        for action,detail in [('native_bound',{'binding':binding}),('native_attempt',{'fixture':True}),('native_exit_attempt',{'tag':'mv-exit:gain-1:1','qty':SCALE//2})]:append_event(db,'gain-1',action,detail,clock())

def run():
    assert os.geteuid()==0 and shutil.which('setpriv');os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='maj-native-reserve-',dir='/run') as tmp:
        r=Path(tmp);r.chmod(0o755)
        def directory(name,uid=0,mode=0o700,gid=None):
            p=r/name;p.mkdir();os.chown(p,uid,uid if gid is None else gid);p.chmod(mode);return p
        for n,u,m in [('witness',AUTH,0o755),('socket',AUTH,0o755),('credentials',AUTH,0o700),('native-keys',NATIVE,0o700),('desk',DESK,0o700),('export',0,0o700),('legacy',0,0o700),('records',0,0o755)]:directory(n,u,m)
        directory('inbox',mode=0o750,gid=NATIVE)
        directory('terminal',uid=NATIVE,mode=0o750,gid=TRANSFER)
        relay,rebind,native=[os.urandom(32) for _ in range(3)]
        for n,s in [('relay',relay),('rebind',rebind),('native',native)]:
            p=r/(n+'.pub');p.write_bytes(Ed25519PrivateKey.from_private_bytes(s).public_key().public_bytes(Encoding.Raw,PublicFormat.Raw));p.chmod(0o644)
        key=r/'native-keys'/'native-ed25519.key';key.write_bytes(native);os.chown(key,NATIVE,NATIVE)
        now=int(time.time());(r/'clock').write_text(str(now));(r/'clock').chmod(0o644)
        env={'PATH':'/usr/bin:/bin','PYTHONPATH':':'.join(map(str,[ROOT,ROOT/'scripts',ROOT/'engine_strategies'])),'PYTHONDONTWRITEBYTECODE':'1','HOME':'/nonexistent','OPENBLAS_NUM_THREADS':'1'}
        prefix=f'from qualify_protected_native_transport import *;r=Path({str(r)!r});os.umask(0o077);'
        def cmd(uid,code):return ['setpriv',f'--reuid={uid}',f'--regid={uid}',f'--groups={TRANSFER}' if uid in (DESK,NATIVE) else '--clear-groups',sys.executable,'-B','-c',prefix+code]
        def sub(uid,code):
            p=subprocess.run(cmd(uid,code),env=env,capture_output=True,text=True,timeout=30);assert p.returncode==0,p.stderr;return p.stdout.strip()
        result=sub(AUTH,'s=AuthorityStore(r/"witness",authority_uid=AUTH,desk_uid=DESK);c=sqlite3.connect(":memory:");c.execute("CREATE TABLE fin_events(seq INTEGER,kind TEXT,digest TEXT)");c.execute("BEGIN");s.initialize(c);print(enroll(r/"credentials",PASSWORD,authority_uid=AUTH,desk_uid=DESK).hex())')
        (r/'human.pub').write_bytes(bytes.fromhex(result));(r/'human.pub').chmod(0o644)
        clock,store,tx,bindings,public,human,verify=context(r)
        with tx() as db:
            db.execute('CREATE TABLE desk(id INTEGER PRIMARY KEY,paused INTEGER,human_pause INTEGER,virtual_cap_cents INTEGER)');db.execute('INSERT INTO desk VALUES(1,1,1,15000)')
            db.execute('CREATE TABLE instances(id TEXT PRIMARY KEY,pair TEXT,virtual_cents INTEGER,mode TEXT,strategy_family TEXT,state TEXT)');db.execute('INSERT INTO instances VALUES(?,?,10000,\'PAPER\',\'numerical-baseline\',\'RUN\')',(I,'BTC/USD'))
            db.execute('PRAGMA user_version=4');gate_init(db);initialize(db);drafts_init(db);db.execute('CREATE TABLE secrets(slot TEXT,ciphertext BLOB)');db.execute('INSERT INTO secrets VALUES(?,?)',('test',b'synthetic-vault-canary'))
        provision(r/'legacy',I,tx);dbpath=r/'desk'/'desk.sqlite3';os.chown(dbpath,DESK,DESK)
        def export():shutil.copyfile(dbpath,r/'export'/'desk.sqlite3')
        export();shutil.copyfile(r/'legacy'/'paper'/I/'paper.sqlite',r/'export'/'paper.sqlite')
        review={'scope':{'instance':I,'pair':'BTC/USD','principal_cents':10000,'mode':'PAPER'},'native_sha256':digest((r/'export'/'paper.sqlite').read_bytes()),'desk_sha256':digest((r/'export'/'desk.sqlite3').read_bytes())}
        stage_pristine(r/'export',r/'native-data',review,**IDS);issue(r/'export',r/'native-data',r/'records',review,store,rebind,**IDS)
        def snapshot():
            from reserve_live_relay import relay as publish_snapshot
            publish_snapshot(r/'desk',r/'inbox',W,store,relay,desk_uid=DESK,desk_gid=DESK,native_uid=NATIVE,native_gid=NATIVE,root=ROOT,clock=clock)
            with tx() as db:raw=project(db)
            assert b'synthetic-vault-canary' not in raw and b'synthetic-vault-canary' not in (r/'inbox'/'snapshot.bin').read_bytes()
            p=r/'authority-view';p.write_bytes(raw);os.chown(p,0,AUTH);p.chmod(0o640)
        common='clock,store,tx,bindings,public,human,verify=context(r);'
        producer='p=NativeProducer(W,r/"native-data",r/"native-keys",native_uid=NATIVE,desk_uid=DESK,authority_uid=AUTH,native_pin=r/"native.pub",human_pin=r/"human.pub",rebind_dir=r/"records",rebind_pin=r/"rebind.pub",clock=clock);'
        def observe(expected=10000):
            snapshot();packet=json.loads(sub(NATIVE,common+producer+'print(json.dumps(p.observe_from_handoff(r/"inbox",store,r/"relay.pub",NATIVE,I,driver_context)))'))
            assert packet['body']['wallet']['quote']==expected*(SCALE//100)
            code=f'with tx() as db:\n assert verify(db,clock())[0].settled_cash_cents=={expected}'
            sub(DESK,common+f'publish(tx,{{I:{packet!r}}},bindings,public,W,clock=clock);exec({code!r})')
        observe();print('PASS genuine native producer, actual signed ledger reconciliation',flush=True)
        for uid in (DESK,AUTH):
            code='try:\n (r/"native-keys"/"native-ed25519.key").read_bytes()\nexcept PermissionError: pass\nelse: raise AssertionError("private key readable")'
            sub(uid,'exec('+repr(code)+')')
        print('PASS independent private key custody and minimized snapshots',flush=True)
        with tx() as db:db.execute('UPDATE desk SET paused=0,human_pause=0')
        Gate(tx,clock=clock).trusted_policy(Policy(version='policy-1',owner='paper-worker',epoch=1,expires=clock()+4*DAY,strategy_revision='revision-1',max_order_cents=6000,max_total_exposure_cents=10000,max_pair_exposure_cents=6000,max_positions=2,max_loss_per_trade_cents=300,daily_loss_limit_cents=1000,weekly_loss_limit_cents=1500,drawdown_limit_cents=2000,market_max_age_sec=60,account_max_age_sec=60,max_spread_bps=25,max_volatility_bps=500,min_depth_cents=5000,cost_bps=100,min_edge_bps=10))
        save(tx,I,dict(rate_bps=5000,floor_mode='fixed',floor_fixed=0,floor_bps=0,ceiling=125*SCALE,cycle_cap=5*SCALE,lifetime_cap=25*SCALE,anchor=clock()%DAY,start=clock(),end=clock()+3*DAY,expiry=clock()+3*DAY),0,clock=clock)
        observe();snapshot()
        service=subprocess.Popen(cmd(AUTH,common+'import reserve_recovery;reserve_recovery.time.time=clock;RecoverableAuthorityService(store,r/"socket",authority_uid=AUTH,desk_uid=DESK,workspace=W,pin=r/"human.pub",native_pin=r/"native.pub").serve()'),env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            for _ in range(100):
                if (r/'socket'/'authority.sock').exists():break
                assert service.poll() is None;time.sleep(.05)
            else:raise AssertionError('authority not ready')
            adopt=json.loads(sub(NATIVE,common+producer+'print(json.dumps(p.propose_from_handoff(r/"inbox",store,r/"relay.pub",NATIVE,I,"adopt","adopt-1")))'))
            def approve(event,evidence):
                code='@contextmanager\ndef view():\n with closing(open_projected((r/"authority-view").read_bytes())) as db:\n  db.execute("BEGIN");yield db\n'
                return json.loads(sub(AUTH,common+'exec('+repr(code)+');'+f'review=ExactReview(W,r/"credentials",authority_uid=AUTH,desk_uid=DESK,permit_pin=r/"human.pub",native_pin=r/"native.pub",revalidate=human_revalidator(view,store,clock=clock,verify_projection=verify),clock=clock);v=review.begin(store.read(),{event!r},{evidence!r});print(json.dumps(review.confirm(v["challenge"],PASSWORD)))'))
            def commit(packet):return json.loads(sub(DESK,common+f'client=RecoveryClient(r/"socket",authority_uid=AUTH,desk_uid=DESK,workspace=W);print(json.dumps(commit_packet(tx,client,{packet!r},human,public,clock=clock,verify_projection=verify)))'))
            assert commit(approve(adopt['event'],adopt['evidence']))['kind']=='reserve_adopt';print('PASS protected adoption via independent human review and authority IPC',flush=True)
            (r/'clock').write_text(str(now+DAY));observe();snapshot()
            with tx() as db:active=replay(db)[0][I]['reserve']['active'];cycle=(clock()-active['policy']['anchor'])//DAY;receipt='cycle:'+sha([I,active['version'],cycle])
            tick=json.loads(sub(NATIVE,common+producer+f'print(json.dumps(p.propose_from_handoff(r/"inbox",store,r/"relay.pub",NATIVE,I,"tick",{receipt!r})))'))
            assert commit(tick)['release']==0;print('PASS protected zero-surplus tick via native signer and authority IPC',flush=True)
            gain=json.loads(sub(NATIVE,'print(json.dumps(simulated_gain(r/"native-data"/"paper"/I)))'))
            # The synthetic authorization is setup data only, not an execution broker.
            sub(DESK,common+f'claim_fixture(tx,{gain["binding"]!r},clock)')
            snapshot()
            make='m=TerminalMailbox(r/"terminal",NATIVE,TRANSFER);'
            sub(NATIVE,common+producer+make+'assert native_step(p,r/"inbox",store,r/"relay.pub",NATIVE,m,factory=lambda i,p,c,w:driver_context(w))=="published"')
            sub(NATIVE,common+producer+make+'assert native_step(p,r/"inbox",store,r/"relay.pub",NATIVE,m)=="awaiting_commit"')
            def refused(uid,code):
                sub(uid,common+make+'exec('+repr('try:\n '+code+'\nexcept (ValueError,PermissionError): pass\nelse: raise AssertionError("unsafe operation accepted")')+')')
            refused(NATIVE,'m.publish(m.read())')
            refused(DESK,'m.publish(m.read())')
            refused(DESK,'(r/"terminal"/"pending.json").unlink()')
            refused(AUTH,'m.read()')
            refused(DESK,'packet=m.read();packet["signature"]="0"*128;terminal_consume(tx,packet,bindings,public,W,store=store,clock=clock)')
            refused(DESK,'packet=m.read();packet["body"]["rounding"][0]["id"]="other";terminal_consume(tx,packet,bindings,public,W,store=store,clock=clock)')
            print('PASS tampered, out-of-scope and unauthorized mailbox operations refused',flush=True)
            sub(DESK,common+make+'assert desk_step(tx,m,bindings,public,W,store,desk_uid=DESK,clock=clock)=="accounted"')
            sub(DESK,common+make+'assert desk_step(tx,m,bindings,public,W,store,desk_uid=DESK,clock=clock)=="already_accounted"')
            snapshot()
            sub(NATIVE,common+producer+make+'assert native_step(p,r/"inbox",store,r/"relay.pub",NATIVE,m)=="retired";assert m.read() is None;assert native_step(p,r/"inbox",store,r/"relay.pub",NATIVE,m)=="idle"')
            print('PASS signed terminal delivery, replay-safe restart, exact signed snapshot acknowledgement',flush=True)
            (r/'clock').write_text(str(now+2*DAY));observe(10945);snapshot()
            with tx() as db:active=replay(db)[0][I]['reserve']['active'];cycle=(clock()-active['policy']['anchor'])//DAY;receipt='cycle:'+sha([I,active['version'],cycle])
            tick=json.loads(sub(NATIVE,common+producer+f'print(json.dumps(p.propose_from_handoff(r/"inbox",store,r/"relay.pub",NATIVE,I,"tick",{receipt!r})))'))
            assert commit(tick)['release']==472000000
            with tx() as db:
                state=replay(db)[0][I];assert state['active']==10472000000 and state['protected']==473000000
            observe(10945);print('PASS actual native gain, ledger settlement and protected positive release of $4.72',flush=True)
            (r/'clock').write_text(str(now+2*DAY+60));snapshot()
            with tx() as db:event=derive_event(db,store.read(),I,'revoke','revoke-1',clock(),clock(),None)
            assert commit(approve(event,None))['kind']=='reserve_revoke'
            with tx() as db:assert replay(db)[0][I]['reserve']['active'] is None and store.verify(db)==store.read()
            print('PASS stale-wallet revocation and durable ledger/witness agreement',flush=True)
        finally:service.terminate();service.wait(timeout=5)

if __name__=='__main__':run()
