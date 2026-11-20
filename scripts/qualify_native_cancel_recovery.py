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
    from qualify_gate_native import BookFixture as Book
    from paper_custody import workspace_key
    from reserve_cancel_custody import NativeCancellationDriver
    config=dict(dry_run=True,trading_mode=TradingMode.SPOT,runmode=RunMode.DRY_RUN,stake_currency='USD',dry_run_wallet=100.,initial_state='stopped',strategy='GatePaper',force_entry_enable=False,max_open_trades=1,timeframe='1h',db_url='sqlite:///'+str(work/'paper.sqlite'),exchange=dict(name='kraken',key='',secret='',pair_whitelist=['BTC/USD']),telegram={'enabled':False},api_server={'enabled':False},freqai={'enabled':False})
    with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')):
        init_db(config['db_url']);bot=object.__new__(FreqtradeBot);bot.config=config;bot.state=State.STOPPED;bot.trading_mode=TradingMode.SPOT;bot.exchange=Book(config);bot.strategy=GatePaper(config);bot.wallets=Wallets(config,bot.exchange)
        bot.exchange.mid=101.
        bot.get_valid_enter_price_and_stake=lambda pair,price,stake,*a:(price,stake,1.)
        bot._notify_enter=lambda *a,**kw:None
        bot._notify_enter_cancel=lambda *a,**kw:None
        bot.strategy.order_time_in_force={'entry':'GTC','exit':'GTC'}
        bot.strategy.order_types={'entry':'limit','exit':'market','stoploss':'market','stoploss_on_exchange':False}
        bot._notify_exit=lambda *args,**kwargs:None
        bot.get_valid_price=lambda custom,proposed:proposed
        bot.cancel_stoploss_on_exchange=lambda trade,**kwargs:trade
        bot._exit_reason_cache={}
        def fee_update(trade,order_id,order,**kwargs):
            trade.update_order(order)
            trade.update_fee(order['fee']['cost'],order['fee']['currency'],order['fee']['rate'],order['side'])
            trade.update_trade(trade.select_order_by_order_id(order_id))
            return False
        bot.update_trade_state=fee_update
        try:yield NativeCancellationDriver(bot,work/'paper.sqlite',I,workspace_key(work.parents[1],I)[0],clock=lambda:int((work.parents[2]/'clock').read_text()))
        finally:
            engine=Trade.session.get_bind();Trade.session.remove();engine.dispose()

@contextmanager
def pending_factory(work):
    # Test-only already-approved claim. Exercise actual native entry but not an
    # installed cross-principal entry broker. No network or live credentials.
    from freqtrade.persistence import Trade
    with driver_context(work) as driver:
        row=dict(id='gain-1',instance_id=I,pair='BTC/USD',entry=100*SCALE,stop=90*SCALE,target=120*SCALE,notional=5000,reserved=5100)
        assert driver.entry(row,lambda operation:operation())['observed']
        trade=Trade.get_trades_proxy(is_open=True)[0]
        assert len(trade.open_orders)==1 and not trade.open_orders[0].filled
        yield driver

def claim_fixture(tx, binding, clock):
    from paper_execution import append_event
    # The intent/relay is a disposable fixture; actual ProtectionWorker verifies
    # observed terminal order arithmetic and settles through the real ledger.
    with tx() as db:
        db.execute('INSERT INTO gate_intents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('gain-1','proposal-gain',I,'BTC/USD','evidence-gain','a'*64,'b'*64,'c'*64,'paper-worker',1,5000,5100,100*SCALE,90*SCALE,120*SCALE,clock()+60,'CLAIMED','fixture-claim',None))
        for action,detail in [('native_bound',{'binding':binding}),('native_attempt',{'binding':binding})]:append_event(db,'gain-1',action,detail,clock())

def run(cut_before=False):
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
        env={'PATH':'/usr/bin:/bin','PYTHONPATH':':'.join(map(str,[ROOT,ROOT/'scripts',ROOT/'engine_strategies',ROOT/'tests'])),'PYTHONDONTWRITEBYTECODE':'1','HOME':'/nonexistent','OPENBLAS_NUM_THREADS':'1'}
        prefix=f'from qualify_native_cancel_recovery import *;r=Path({str(r)!r});os.umask(0o077);'
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
        observe();print('PASS genuine flat wallet and independent ledger reconciliation',flush=True)
        read_binding='with driver_context(r/"native-data"/"paper"/I) as d:\n print(json.dumps(d.binding))'
        binding=json.loads(sub(NATIVE,'exec('+repr(read_binding)+')'))
        sub(DESK,common+f'claim_fixture(tx,{binding!r},clock)')
        snapshot();make='m=TerminalMailbox(r/"terminal",NATIVE,TRANSFER);'
        # Crash after native-owned authenticated freeze, before ORM deletion.
        cut="from reserve_cancel_custody import NativeCancellationDriver\ndef crash(self,row,raw):os.kill(os.getpid(),9)\nNativeCancellationDriver._apply_frozen=crash"
        child=subprocess.run(cmd(NATIVE,common+producer+make+'exec('+repr(cut)+');native_step(p,r/"inbox",store,r/"relay.pub",NATIVE,m,factory=lambda i,p,c,w:pending_factory(w),protected=True)'),env=env,capture_output=True,text=True,timeout=30)
        assert child.returncode==-9,child.stderr
        print('PASS actual native pending buy, authenticated cancellation freeze before SIGKILL',flush=True)
        snapshot()
        native_call='native_step(p,r/"inbox",store,r/"relay.pub",NATIVE,m,factory=lambda i,p,c,w:driver_context(w),protected=True)'
        sub(NATIVE,common+producer+make+f'assert {native_call}=="published";assert m.read()["body"]["v"]==3')
        print('PASS independent native process recovers zero-fill and signs flat wallet',flush=True)
        sub(NATIVE,common+producer+make+f'assert {native_call}=="awaiting_commit"')
        sub(DESK,common+make+'assert desk_step(tx,m,bindings,public,W,store,desk_uid=DESK,clock=clock)=="accounted";assert desk_step(tx,m,bindings,public,W,store,desk_uid=DESK,clock=clock)=="already_accounted"')
        with tx() as db:
            assert db.execute('SELECT state FROM gate_intents WHERE id=?',('gain-1',)).fetchone()[0]=='NO_FILL'
            state=replay(db)[0][I];assert state['active']==100*SCALE and state['protected']==0
            from financial_ledger import gate_reserve
            assert gate_reserve(db,I)==0
        snapshot();sub(NATIVE,common+producer+make+f'assert {native_call}=="retired";assert {native_call}=="idle"')
        print('PASS signed zero-fill import, duplicate refusal, exact acknowledgement, unchanged principal',flush=True)

if __name__=='__main__':run()
