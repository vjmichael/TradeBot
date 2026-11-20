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
    import socket
    original=socket.socket.connect
    def local(sock,address):
        assert sock.family==socket.AF_UNIX,'network forbidden'
        return original(sock,address)
    with patch('socket.socket.connect',local):
        init_db(config['db_url']);bot=object.__new__(FreqtradeBot);bot.config=config;bot.state=State.STOPPED;bot.trading_mode=TradingMode.SPOT;bot.exchange=Book(config);bot.strategy=GatePaper(config);bot.wallets=Wallets(config,bot.exchange)
        bot.exchange.mid=100.
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

def run(mode):
    assert os.geteuid()==0 and shutil.which('setpriv');os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='maj-native-reserve-',dir='/run') as tmp:
        r=Path(tmp);r.chmod(0o755)
        def directory(name,uid=0,mode=0o700,gid=None):
            p=r/name;p.mkdir();os.chown(p,uid,uid if gid is None else gid);p.chmod(mode);return p
        for n,u,m in [('witness',AUTH,0o755),('socket',AUTH,0o755),('credentials',AUTH,0o700),('native-keys',NATIVE,0o700),('desk',DESK,0o700),('export',0,0o700),('legacy',0,0o700),('records',0,0o755)]:directory(n,u,m)
        directory('inbox',mode=0o750,gid=NATIVE)
        directory('entry',uid=DESK,mode=0o755)
        directory('fence',mode=0o755)
        lock=r/'fence'/'entry.lock';lock.touch();os.chown(lock,0,TRANSFER);lock.chmod(0o640)
        directory('terminal',uid=NATIVE,mode=0o750,gid=TRANSFER)
        directory('recovery-proof',uid=NATIVE,mode=0o750,gid=65530)
        directory('recovery-human',uid=AUTH,mode=0o750,gid=65530)
        relay,rebind,native=[os.urandom(32) for _ in range(3)]
        for n,s in [('relay',relay),('rebind',rebind),('native',native)]:
            p=r/(n+'.pub');p.write_bytes(Ed25519PrivateKey.from_private_bytes(s).public_key().public_bytes(Encoding.Raw,PublicFormat.Raw));p.chmod(0o644)
        key=r/'native-keys'/'native-ed25519.key';key.write_bytes(native);os.chown(key,NATIVE,NATIVE)
        now=int(time.time());(r/'clock').write_text(str(now));(r/'clock').chmod(0o644)
        env={'PATH':'/usr/bin:/bin','PYTHONPATH':':'.join(map(str,[ROOT,ROOT/'scripts',ROOT/'engine_strategies',ROOT/'tests'])),'PYTHONDONTWRITEBYTECODE':'1','HOME':'/nonexistent','OPENBLAS_NUM_THREADS':'1','DISPLAY':os.environ.get('DISPLAY','')}
        prefix=f'from qualify_independent_entry import *;r=Path({str(r)!r});os.umask(0o077);'
        def cmd(uid,code):return ['setpriv',f'--reuid={uid}',f'--regid={uid}',f'--groups={TRANSFER},65530' if uid in (DESK,NATIVE) else '--groups=65530',sys.executable,'-B','-c',prefix+code]
        def sub(uid,code):
            p=subprocess.run(cmd(uid,code),env=env,capture_output=True,text=True,timeout=30);assert p.returncode==0,p.stderr;return p.stdout.strip()
        result=sub(AUTH,'s=AuthorityStore(r/"witness",authority_uid=AUTH,desk_uid=DESK);c=sqlite3.connect(":memory:");c.execute("CREATE TABLE fin_events(seq INTEGER,kind TEXT,digest TEXT)");c.execute("BEGIN");s.initialize(c);print(enroll(r/"credentials",PASSWORD,authority_uid=AUTH,desk_uid=DESK).hex())')
        (r/'human.pub').write_bytes(bytes.fromhex(result));(r/'human.pub').chmod(0o644)
        clock,store,tx,bindings,public,human,verify=context(r)
        with tx() as db:
            db.execute('CREATE TABLE desk(id INTEGER PRIMARY KEY,paused INTEGER,human_pause INTEGER,virtual_cap_cents INTEGER)');db.execute('INSERT INTO desk VALUES(1,1,1,15000)')
            db.execute('CREATE TABLE instances(id TEXT PRIMARY KEY,pair TEXT,virtual_cents INTEGER,mode TEXT,strategy_family TEXT,state TEXT)');db.execute('INSERT INTO instances VALUES(?,?,10000,\'PAPER\',\'numerical-baseline\',\'RUN\')',(I,'BTC/USD'))
            db.execute('PRAGMA user_version=4');gate_init(db);initialize(db);drafts_init(db);__import__('financial_goals').initialize(db);db.execute('CREATE TABLE secrets(slot TEXT,ciphertext BLOB)');db.execute('INSERT INTO secrets VALUES(?,?)',('test',b'synthetic-vault-canary'))
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
        observe();print('PASS real independently signed wallet',flush=True)
        setup="from risk_gate import MarketEvidence,Proposal\nwith tx() as db:db.execute('UPDATE desk SET paused=0,human_pause=0')\nfrom goal_control import entry_guard\ng=Gate(tx,clock=clock,account_projection=verify,entry_guard=entry_guard(None,verify))\ng.trusted_policy(Policy(version='policy-1',owner='paper-worker',epoch=1,expires=clock()+300,strategy_revision='revision-1',max_order_cents=6000,max_total_exposure_cents=10000,max_pair_exposure_cents=6000,max_positions=1,max_loss_per_trade_cents=300,daily_loss_limit_cents=1000,weekly_loss_limit_cents=1500,drawdown_limit_cents=2000,market_max_age_sec=60,account_max_age_sec=60,max_spread_bps=25,max_volatility_bps=500,min_depth_cents=5000,cost_bps=100,min_edge_bps=10))\ng.trusted_market(MarketEvidence(receipt='native-market',pair='BTC/USD',observed=clock(),available=clock(),source_digest='a'*64,strategy_revision='revision-1',entry=100*SCALE,stop=90*SCALE,target=120*SCALE,spread_bps=10,volatility_bps=100,depth_cents=10000,min_notional_cents=100,estimated_cost_bps=80,gross_edge_bps=130,continuous=True,operational=True,trigger=True,blackout=False))\nresult=g.admit(Proposal('entry-proposal',I,'BTC/USD','native-market',5000,clock()+60));assert result['state']=='AUTHORIZED',result\nprint(result['intent_id'])\n"
        intent=sub(DESK,common+"exec("+repr(setup)+")");snapshot()
        servercode=common+"from reserve_entry_broker import listener,serve_request;from goal_control import entry_guard;g=Gate(tx,clock=clock,account_projection=verify,entry_guard=entry_guard(None,verify));exec("+repr('with listener(r/"entry",DESK) as server:\n conn,_=server.accept()\n with conn: print(serve_request(conn,tx,g,bindings,store,r/"fence"/"entry.lock",native_uid=NATIVE,desk_uid=DESK,clock=clock),flush=True)')+")"
        server=subprocess.Popen(cmd(DESK,servercode),env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            for _ in range(200):
                if (r/'entry'/'entry.sock').exists():break
                assert server.poll() is None;time.sleep(.02)
            else:raise AssertionError('server unavailable')
            native_prefix=common+producer+'from reserve_entry_worker import native_once;'
            native_call='native_once(p,r/"inbox",store,r/"relay.pub",NATIVE,r/"entry",r/"fence"/"entry.lock",factory=lambda i,p,c,w:driver_context(w))'
            if mode=='warm':
                # Qualification-only test orchestration over stdin. Production
                # warm service has no command endpoint.
                (r/'inbox'/'snapshot.bin').unlink()
                warmcode='''from reserve_persistent_worker import WarmRuntime
from paper_supervisor import custody
m=TerminalMailbox(r/'terminal',NATIVE,TRANSFER)
with WarmRuntime(p,r/'relay.pub',lambda i,p,c,w:driver_context(w)).start() as warm:
 try:
  with custody(warm.work):raise AssertionError('competing lease accepted')
 except BlockingIOError:pass
 try:
  with warm.borrow(I,'BTC/USD',10000,warm.work):raise AssertionError('unleased borrow')
 except ValueError:pass
 print('READY',flush=True)
 token=id(warm.driver)
 for line in sys.stdin:
  action=line.strip()
  if action=='stop':break
  if action=='exit':warm.driver.bot.exchange.mid=120.
  assert id(warm.driver)==token
  result=warm.cycle(r/'inbox',store,NATIVE,m,r/'entry',r/'fence'/'entry.lock')
  print(result,flush=True)
'''
                child=subprocess.Popen(cmd(NATIVE,native_prefix+'exec('+repr(warmcode)+')'),env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                def response(expected,action=None):
                    import select
                    if action:child.stdin.write(action+'\n');child.stdin.flush()
                    ready,_,_=select.select([child.stdout],[],[],20)
                    assert ready,'warm runtime deadline';line=child.stdout.readline().strip()
                    assert line==expected,(line,child.stderr.read() if child.poll() is not None else '')
                try:
                    response('READY');response('outage_no_open_exposure','tick')
                    print('PASS readiness without snapshot, exclusive lifetime lease, no unleased access or entry',flush=True)
                    snapshot();response('submitted','tick');out,err=server.communicate(timeout=10);assert server.returncode==0,err
                    print('PASS warm actual synchronous native entry',flush=True)
                    response('outage_monitoring','tick')
                    print('PASS stale AUTHORIZED snapshot cannot cause a second entry or restart',flush=True)
                    response('outage_exit_requested','exit');response('entry_already_attempted','tick')
                    print('PASS stale AUTHORIZED after closed position is denied by durable local attempt',flush=True)
                    snapshot();response('published','tick')
                    make='m=TerminalMailbox(r/"terminal",NATIVE,TRANSFER);'
                    sub(DESK,common+make+'assert desk_step(tx,m,bindings,public,W,store,desk_uid=DESK,clock=clock)=="accounted";assert desk_step(tx,m,bindings,public,W,store,desk_uid=DESK,clock=clock)=="already_accounted"')
                    snapshot();response('retired','tick');snapshot();response('idle','tick')
                    sub(DESK,common+make+'from reserve_wallet_transport import consume as read_wallet;assert read_wallet(tx,m,bindings,public,W,store,clock=clock)=="wallet_accounted";assert read_wallet(tx,m,bindings,public,W,store,clock=clock)=="wallet_held"')
                    print('PASS independently signed warm wallet delivery and exact replay refusal',flush=True)
                    with tx() as db:
                        from financial_ledger import observed,gate_reserve
                        v=observed(replay(db)[0][I]);assert v['active']==100*SCALE and v['protected']>0 and v['qty']==v['pending']==gate_reserve(db,I)==0
                        assert db.execute("SELECT count(*) FROM gate_events WHERE action='native_return'").fetchone()[0]==1
                    print('PASS one retained driver through entry, protection, terminal import, duplicate refusal and acknowledgment',flush=True)
                    child.stdin.write('stop\n');child.stdin.flush();out,err=child.communicate(timeout=15);assert child.returncode==0,err
                    sub(NATIVE,'from paper_supervisor import custody;exec('+repr('with custody(r/"native-data"/"paper"/I):pass')+')')
                    print('PASS graceful closure releases lifetime lease',flush=True)
                finally:
                    if child.poll() is None:child.kill();child.wait()
                return
            if mode in ('normal','outage'):
                print(sub(NATIVE,native_prefix+f'assert {native_call}=="submitted";print("PASS actual independent Freqtrade entry")'))
                out,err=server.communicate(timeout=10);assert server.returncode==0,err
                with tx() as db:assert __import__('paper_execution').event_detail(db,intent,'native_return') is not None
            elif mode=='review':
                cut='import reserve_no_effect\ndef cut(*a,**kw):os.kill(os.getpid(),9)\nreserve_no_effect.possible=cut'
                child=subprocess.run(cmd(NATIVE,native_prefix+'exec('+repr(cut)+');'+native_call),env=env,capture_output=True,text=True,timeout=30)
                assert child.returncode==-9,child.stderr
                out,err=server.communicate(timeout=10);assert server.returncode!=0
                print('PASS independently authorized attempt interrupted after durable PREPARED, before possible effect',flush=True)
            elif mode=='partial':
                # The dry-run simulator normally fills all-or-none. Inject a known
                # cumulative partial observation into the genuine pending buy;
                # freeze it under native custody, then interrupt before ORM import.
                cut="from paper_execution import NativeDriver\nfrom reserve_cancel_custody import NativeCancellationDriver\nfrom freqtrade.persistence import Trade\nold=NativeDriver.entry\ndef interrupted(self,row,guard):\n self.bot.exchange.mid=101.\n old(self,row,guard)\n t=Trade.get_trades_proxy(is_open=True)[0];o=t.open_orders[0]\n raw=self.bot.exchange._dry_run_open_orders[o.order_id]\n raw.update(filled=.2,remaining=raw['amount']-.2,cost=20.,average=100.,fee={'currency':'USD','cost':.10,'rate':.005})\n self.cancel_before_poll(row)\n raise AssertionError('missing crash')\nNativeDriver.entry=interrupted\ndef die(self,row,raw):os.kill(os.getpid(),9)\nNativeCancellationDriver._apply_frozen=die"
                child=subprocess.run(cmd(NATIVE,native_prefix+'exec('+repr(cut)+');'+native_call),env=env,capture_output=True,text=True,timeout=30)
                assert child.returncode==-9,child.stderr
                out,err=server.communicate(timeout=10);assert server.returncode!=0
                print('PASS actual authorized pending entry, observed partial fill and durable cancellation freeze before SIGKILL',flush=True)
            elif mode=='after':
                # After actual ORM persistence but before acknowledging the desk.
                cut='from paper_execution import NativeDriver\nold=NativeDriver.entry\ndef die(self,row,guard):\n old(self,row,guard);os.kill(os.getpid(),9)\nNativeDriver.entry=die'
                child=subprocess.run(cmd(NATIVE,native_prefix+'exec('+repr(cut)+');'+native_call),env=env,capture_output=True,text=True,timeout=30)
                assert child.returncode==-9,child.stderr
                out,err=server.communicate(timeout=10);assert server.returncode!=0
                from reserve_entry_fence import fence
                with fence(lock,timeout=1),tx() as db:
                    assert db.execute('SELECT state FROM gate_intents WHERE id=?',(intent,)).fetchone()[0]=='UNKNOWN'
                    from paper_execution import event_detail
                    assert event_detail(db,intent,'native_attempt') is not None and event_detail(db,intent,'native_return') is None
                print('PASS native SIGKILL after ORM persistence: exact claim retained as UNKNOWN',flush=True)
            else:
                cut='from qualify_gate_native import BookFixture\nold=BookFixture.create_order\ndef cut(self,**kw):\n print("EFFECT",flush=True)\n while not (r/"fence"/"continue").exists():time.sleep(.02)\n return old(self,**kw)\nBookFixture.create_order=cut'
                child=subprocess.Popen(cmd(NATIVE,native_prefix+'exec('+repr(cut)+');'+native_call),env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                try:
                    import select
                    ready,_,_=select.select([child.stdout],[],[],20);assert ready and child.stdout.readline().strip()=='EFFECT','missing effect cut'
                    server.kill();server.wait(timeout=5)
                    from reserve_entry_fence import fence
                    try:
                        with fence(lock,timeout=.2):raise AssertionError('fence dropped before native exit')
                    except ValueError as e:assert str(e)=='entry_fence_busy'
                    child.kill();child.wait(timeout=5)
                    with fence(lock,timeout=1),tx() as db:
                        assert db.execute('SELECT state FROM gate_intents WHERE id=?',(intent,)).fetchone()[0]=='CLAIMED'
                        from paper_execution import event_detail
                        assert event_detail(db,intent,'native_attempt') is not None and event_detail(db,intent,'native_return') is None
                    print('PASS guardian SIGKILL retains fence until native SIGKILL and durable unresolved claim',flush=True)
                finally:
                    if child.poll() is None:child.kill();child.wait()
            with tx() as db:
                from financial_ledger import gate_reserve
                assert db.execute('SELECT state FROM gate_intents WHERE id=?',(intent,)).fetchone()[0]==('UNKNOWN' if mode in ('after','partial','review') else 'CLAIMED')
                assert gate_reserve(db,I)>0
            # Read persisted native records in a fresh unprivileged process.
            expected=0 if mode in ('kill','review') else 1
            sub(NATIVE,f'intent={intent!r};expected={expected};exec('+repr('with closing(sqlite3.connect(r/"native-data"/"paper"/I/"paper.sqlite")) as db:\n assert db.execute("SELECT COUNT(*) FROM trades WHERE enter_tag=?",("mv:"+intent,)).fetchone()[0]==expected\n assert db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]==expected') +')')
            sub(DESK,common+f'assert not Gate(tx,clock=clock,account_projection=verify).claim({intent!r},"paper-worker",1)["claimed"]')
            print('PASS no repeat authorization; in-flight principal remains reserved',flush=True)
            if mode in ('review','kill'):
                with tx() as db:db.execute('UPDATE desk SET paused=1,human_pause=1')
                snapshot()
                getproof='from reserve_no_effect import produce;print(json.dumps(produce(p,r/"inbox",store,r/"relay.pub",NATIVE,factory=lambda i,p,c,w:driver_context(w))))'
                if mode=='kill':
                    refused=subprocess.run(cmd(NATIVE,common+producer+getproof),env=env,capture_output=True,text=True,timeout=30)
                    assert refused.returncode!=0 and 'effect_possible_or_unproven' in refused.stderr,refused.stderr
                    print('PASS possible effect cannot be certified as non-invocation, even with flat wallet',flush=True)
                else:
                    boxes='proofbox=TerminalMailbox(r/"recovery-proof",NATIVE,65530);approvalbox=TerminalMailbox(r/"recovery-human",AUTH,65530);'
                    sub(NATIVE,common+producer+boxes+'from reserve_no_effect import produce;proofbox.publish(produce(p,r/"inbox",store,r/"relay.pub",NATIVE,factory=lambda i,p,c,w:driver_context(w)))')
                    # Actual GUI and encrypted authority key; automated synthetic credentials.
                    auth='from reserve_no_effect import Review;review=Review(W,r/"credentials",r/"native.pub",r/"human.pub",authority_uid=AUTH,desk_uid=DESK,clock=clock);'
                    bad=auth+boxes+'v=review.begin(proofbox.read());review.confirm("WRONG",PASSWORD)'
                    refused=subprocess.run(cmd(AUTH,common+bad),env=env,capture_output=True,text=True,timeout=10)
                    assert refused.returncode!=0
                    with tx() as db:assert gate_reserve(db,I)>0
                    apply='from reserve_no_effect_worker import desk_once;'
                    call='desk_once(tx,proofbox,approvalbox,bindings,public,human,W,store,clock=clock)'
                    sub(DESK,common+boxes+apply+f'assert {call}=="review_required"')
                    if os.environ.get('DISPLAY'):
                        gui='from reserve_no_effect_worker import review_window;import tkinter as tk;t=tk.Tk();v=review_window(t,review,proofbox.read(),approvalbox.publish);t.update();v["challenge"].set(v["review"]["challenge"]);v["password"].set(PASSWORD);v["confirm"].invoke();t.update();assert approvalbox.read() is not None and "Approval recorded" in v["status"].get();t.destroy()'
                        sub(AUTH,common+boxes+auth+gui)
                        print('PASS actual graphical local review and independent protected mailbox delivery',flush=True)
                    else:
                        sub(AUTH,common+boxes+auth+'v=review.begin(proofbox.read());approvalbox.publish(review.confirm(v["challenge"],PASSWORD))')
                    reviewed=json.loads(sub(DESK,boxes+'print(json.dumps(approvalbox.read()))'))
                    altered=json.loads(json.dumps(reviewed));altered['approval']['body']['proof']='a'*64
                    bad=common+'from reserve_no_effect import consume;'+f'consume(tx,{altered!r},bindings,public,human,W,store=store,clock=clock)'
                    refused=subprocess.run(cmd(DESK,bad),env=env,capture_output=True,text=True,timeout=10);assert refused.returncode!=0
                    with tx() as db:assert gate_reserve(db,I)>0;db.execute('UPDATE desk SET paused=0,human_pause=0')
                    refused=subprocess.run(cmd(DESK,common+boxes+apply+call),env=env,capture_output=True,text=True,timeout=10);assert refused.returncode!=0
                    with tx() as db:assert gate_reserve(db,I)>0;db.execute('UPDATE desk SET paused=1,human_pause=1')
                    sub(DESK,common+boxes+apply+f'assert {call}=="accounted";assert {call}=="already_accounted"')
                    with tx() as db:
                        assert gate_reserve(db,I)==0 and db.execute('SELECT state FROM gate_intents WHERE id=?',(intent,)).fetchone()[0]=='NO_FILL'
                        v=__import__('financial_ledger').observed(replay(db)[0][I]);assert v['active']==100*SCALE and v['protected']==v['qty']==v['pending']==0
                        assert tuple(db.execute('SELECT paused,human_pause FROM desk').fetchone())==(1,1)
                    print('PASS exact independent review, modified approval refusal, duplicate nonmutation, principal preserved and desk paused',flush=True)
            if mode=='kill':
                # Missing native order is not proof of no fill. Even an accepted
                # permit with no observed open exposure cannot release capital.
                (r/'inbox'/'snapshot.bin').unlink()
                make='m=TerminalMailbox(r/"terminal",NATIVE,TRANSFER);'
                sub(NATIVE,common+producer+make+'assert native_step(p,r/"inbox",store,r/"relay.pub",NATIVE,m,factory=lambda i,p,c,w:driver_context(w),protected=True)=="outage_no_open_exposure";assert m.read() is None')
                with tx() as db:assert gate_reserve(db,I)>0
                print('PASS ambiguous absence during outage remains unresolved, no inferred zero release',flush=True)
            if mode in ('outage','partial','after'):
                # Missing relay must not block risk reduction on an accepted
                # existing position. No terminal import until a fresh snapshot.
                (r/'inbox'/'snapshot.bin').unlink()
                make='m=TerminalMailbox(r/"terminal",NATIVE,TRANSFER);'
                code='@contextmanager\ndef exit_factory(i,p,c,w):\n with driver_context(w) as d:\n  d.bot.exchange.mid=120.\n  yield d\n'
                terminal='native_step(p,r/"inbox",store,r/"relay.pub",NATIVE,m,factory=exit_factory,protected=True)'
                sub(NATIVE,common+producer+make+'exec('+repr(code)+');'+f'assert {terminal}=="outage_exit_requested";assert m.read() is None')
                sub(NATIVE,common+producer+make+'exec('+repr(code)+');'+f'assert {terminal}=="outage_no_open_exposure";assert m.read() is None')
                with tx() as db:assert gate_reserve(db,I)>0
                print('PASS no snapshot: actual native reduce-only exit, no unverified desk settlement or release',flush=True)
                snapshot()
                sub(NATIVE,common+producer+make+'exec('+repr(code)+');'+f'assert {terminal}=="published"')
                sub(DESK,common+make+'assert desk_step(tx,m,bindings,public,W,store,desk_uid=DESK,clock=clock)=="accounted";assert desk_step(tx,m,bindings,public,W,store,desk_uid=DESK,clock=clock)=="already_accounted"')
                snapshot();sub(NATIVE,common+producer+make+'exec('+repr(code)+');'+f'assert {terminal}=="retired"')
                with tx() as db:
                    st=__import__('financial_ledger').observed(replay(db)[0][I]);assert st['active']==100*SCALE and st['qty']==st['pending']==gate_reserve(db,I)==0 and st['protected']>0
                    if mode=='partial':assert st['protected']==378000000,st
                    print('PASS actual terminal settlement, replay safety, exact acknowledgement, protected profit units:',st['protected'],flush=True)

        finally:
            if server.poll() is None:server.kill();server.wait()

if __name__=='__main__':run('warm' if '--warm' in sys.argv else 'review' if '--review' in sys.argv else 'outage' if '--outage' in sys.argv else 'partial' if '--partial' in sys.argv else 'after' if '--after' in sys.argv else 'kill' if '--kill' in sys.argv else 'normal')
