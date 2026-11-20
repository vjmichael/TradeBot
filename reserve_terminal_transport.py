"""Durable one-way signed terminal PAPER settlement, no order execution RPC."""
import os
import fcntl
import stat
import secrets
import time
from contextlib import nullcontext
from pathlib import Path
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from reserve_checkpoint import require
from reserve_ipc import canonical, strict, scope
from financial_ledger import replay, ident, sha
from paper_supervisor import checked_view, released, ProtectionWorker

DOMAIN=b'maj-vaj:PAPER:terminal-settlement:v1\0'
LIMIT=256*1024


def _body(workspace,instance,intent,binding,count,head,view,rounding):
    ident(instance);ident(intent)
    require(type(count) is int and count>0 and all(type(x) is str and len(x)==64 and set(x)<=set('0123456789abcdef') for x in (binding,head)),'terminal_scope')
    checked_view(view,{'id':intent})
    require(view['closed'] and view['position']==0 and all(o['terminal'] and (not o['qty'] or o['fee'] is not None) for o in view['orders']),'terminal_not_settled')
    require(type(rounding) is list and len(rounding)<=256,'terminal_rounding_proof')
    from financial_ledger import rounding_delta
    rounding_delta(rounding)
    fields=('id','side','gross','fee')
    require([{k:o[k] for k in fields} for o in rounding]==
            [{k:o[k] for k in fields} for o in view['orders'] if o['qty']],'terminal_rounding_scope')
    return dict(v=1,workspace=scope(workspace),instance=instance,intent=intent,binding=binding,count=count,head=head,view=view,rounding=rounding)


def verify(public,workspace,packet):
    require(type(packet) is dict and set(packet)=={'body','signature'} and type(public) is bytes and len(public)==32,'terminal_packet')
    b=packet['body'];require(type(b) is dict and set(b)=={'v','workspace','instance','intent','binding','count','head','view','rounding'},'terminal_body')
    require(b==_body(workspace,b['instance'],b['intent'],b['binding'],b['count'],b['head'],b['view'],b['rounding']),'terminal_scope')
    sig=packet['signature'];require(type(sig) is str and len(sig)==128 and set(sig)<=set('0123456789abcdef'),'terminal_signature')
    try:Ed25519PublicKey.from_public_bytes(public).verify(bytes.fromhex(sig),DOMAIN+canonical(b))
    except (InvalidSignature,ValueError) as exc:raise ValueError('terminal_signature') from exc
    return b


def _scope(db,b,binding_for):
    _,_,count,_=replay(db)
    row=db.execute('SELECT digest FROM fin_events WHERE seq=?',(b['count'],)).fetchone()
    require(0<b['count']<=count and row is not None and row[0]==b['head'],'terminal_prefix_changed')
    row=db.execute('SELECT instance_id,state FROM gate_intents WHERE id=?',(b['intent'],)).fetchone()
    require(row is not None and row[0]==b['instance'] and row[1] in ('CLAIMED','UNKNOWN','FILLED_HELD'),'terminal_intent_scope')
    require(binding_for(db,b['instance'])==b['binding'],'terminal_binding_changed')


def produce(producer,directory,store,relay_pin,native_gid,instance,intent,driver_context):
    """Only actual terminal native evidence under independent exclusive custody."""
    from reserve_native_handoff import open_reviewed
    from reserve_native_rebind import verify as verify_rebind
    from paper_custody import workspace,workspace_key,AuthenticatedNativeDriver
    from paper_supervisor import custody
    from reserve_ipc import pinned_key
    import hmac
    require(producer.rebind_dir is not None,'terminal_rebind_required')
    require(len({pinned_key(p) for p in (relay_pin,producer.native_pin,producer.human_pin,producer.rebind_pin)})==4,'terminal_independent_pins')
    with open_reviewed(directory,producer.workspace,store,relay_pin,native_uid=producer.uid,native_gid=native_gid,desk_uid=producer.desk_uid,authority_uid=producer.authority_uid,clock=producer.clock) as (db,_):
        _,_,count,head=replay(db)
        row=db.execute('SELECT instance_id,state FROM gate_intents WHERE id=?',(intent,)).fetchone()
        require(row is not None and row[0]==instance and row[1] in ('CLAIMED','UNKNOWN','FILLED_HELD') and not released(db,intent),'terminal_intent_scope')
        pair=db.execute('SELECT pair,mode FROM instances WHERE id=?',(instance,)).fetchone()
        require(pair is not None and pair[1]=='PAPER','terminal_paper_scope')
        work=workspace(producer.data,instance)
        with custody(work):
            bound=verify_rebind(db,producer.data,producer.rebind_dir,producer.rebind_pin,instance,native_uid=producer.uid,desk_uid=producer.desk_uid,authority_uid=producer.authority_uid)
            key,binding=workspace_key(producer.data,instance)
            with driver_context(work) as driver:
                require(isinstance(driver,AuthenticatedNativeDriver) and driver.instance==instance and driver.path==work/'paper.sqlite' and driver.binding==binding==bound['binding'] and hmac.compare_digest(driver.key,key),'terminal_driver_custody')
                context={'id':intent,'instance_id':instance,'pair':pair[0]}
                view=driver.view(context)
                require(view['closed'] and view['position']==0,'terminal_not_settled')
                proof=driver.rounding_proof(context,view)
                body=_body(producer.workspace,instance,intent,binding,count,head,view,proof)
                packet={'body':body,'signature':Ed25519PrivateKey.from_private_bytes(producer._key()).sign(DOMAIN+canonical(body)).hex()}
        require(len(canonical(packet))<=LIMIT,'terminal_packet_size')
    return packet


def consume(transaction,packet,binding_for,public,workspace,*,store,clock=time.time):
    """Verify and settle once inside the desk transaction, including duplicate checks."""
    b=verify(public,workspace,packet)
    with transaction() as db:
        witness=store.verify(db)
        _scope(db,b,binding_for)
        old=released(db,b['intent'])
        if old:
            require(old['binding']==b['binding'] and old['view']==sha(b['view']),'terminal_release_conflict')
            return {'state':'ACCOUNTED','duplicate':True}
        class Terminal:
            binding=b['binding']
            def view(self,row):return b['view']
            def rounding_proof(self,row,view):return b['rounding']
        # Actual accounting/authorization checks; the restricted adapter has
        # no entry, exit, poll, cancellation or arbitrary method execution.
        result=ProtectionWorker(lambda:nullcontext(db),Terminal(),clock=clock).reconcile(b['intent'])
        require(store.verify(db)==witness,'terminal_witness_changed')
        return result


def acknowledged(db,packet,binding_for,public,workspace):
    b=verify(public,workspace,packet);_scope(db,b,binding_for)
    old=released(db,b['intent'])
    if not old:return False
    require(old['binding']==b['binding'] and old['view']==sha(b['view']),'terminal_release_conflict')
    return True


class TerminalMailbox:
    """Single immutable pending receipt under native ownership; group-readable only.

    Producer may retire it only on exact release in a fresh signed snapshot.
    No desk-written acknowledgement, timeout deletion or replacement.
    """
    def __init__(self,directory,owner,group):
        self.directory=Path(directory);self.owner=owner;self.group=group
        require(owner>0 and group>0,'terminal_mailbox_identity')
    def _directory(self):
        from reserve_ipc import protected_dir
        return protected_dir(self.directory,self.owner,0o750)
    def _read(self,fd):
        require(os.fstat(fd).st_gid==self.group,'terminal_mailbox_group')
        try:f=os.open('pending.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=fd)
        except FileNotFoundError:return None
        with os.fdopen(f,'rb') as stream:
            s=os.fstat(stream.fileno());require(stat.S_ISREG(s.st_mode) and s.st_uid==self.owner and s.st_gid==self.group and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o640 and 0<s.st_size<=LIMIT,'terminal_mailbox_custody')
            raw=stream.read(LIMIT+1);require(len(raw)==s.st_size,'terminal_mailbox_size')
        return strict(raw)
    def read(self):
        with self._directory() as fd:return self._read(fd)
    def publish(self,packet):
        require(os.geteuid()==self.owner,'terminal_mailbox_writer')
        raw=canonical(packet);require(0<len(raw)<=LIMIT,'terminal_mailbox_size')
        with self._directory() as fd:
            fcntl.flock(fd,fcntl.LOCK_EX)
            require(os.fstat(fd).st_gid==self.group,'terminal_mailbox_group')
            require(self._read(fd) is None,'terminal_delivery_pending')
            name='.pending-'+secrets.token_hex(16)
            out=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=fd)
            try:
                with os.fdopen(out,'wb') as stream:
                    stream.write(raw);stream.flush();os.fchown(stream.fileno(),-1,self.group);os.fchmod(stream.fileno(),0o640);os.fsync(stream.fileno())
                os.replace(name,'pending.json',src_dir_fd=fd,dst_dir_fd=fd);os.fsync(fd)
            finally:
                try:os.unlink(name,dir_fd=fd)
                except FileNotFoundError:pass
    def retire(self,producer,directory,store,relay_pin,native_gid):
        from reserve_native_handoff import open_reviewed
        from reserve_native_wallet import rebound_bindings
        from reserve_ipc import pinned_key
        require(os.geteuid()==self.owner==producer.uid and producer.rebind_dir is not None,'terminal_mailbox_writer')
        require(len({pinned_key(p) for p in (relay_pin,producer.native_pin,producer.human_pin,producer.rebind_pin)})==4,'terminal_independent_pins')
        with self._directory() as fd:
            fcntl.flock(fd,fcntl.LOCK_EX)
            packet=self._read(fd)
            if packet is None:return False
            bindings=rebound_bindings(producer.rebind_dir,producer.rebind_pin,native_uid=producer.uid,desk_uid=producer.desk_uid,authority_uid=producer.authority_uid)
            with open_reviewed(directory,producer.workspace,store,relay_pin,native_uid=producer.uid,native_gid=native_gid,desk_uid=producer.desk_uid,authority_uid=producer.authority_uid,clock=producer.clock) as (db,_):
                done=acknowledged(db,packet,bindings,pinned_key(producer.native_pin),producer.workspace)
            if not done:return False
            require(self._read(fd)==packet,'terminal_mailbox_changed')
            os.unlink('pending.json',dir_fd=fd);os.fsync(fd);return True
