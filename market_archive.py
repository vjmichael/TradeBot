"""Kraken's published 2026-09-30 split OHLCVT archive. Fixed public sources only.

Selective HTTPS ranges verify ZIP member CRC, not the complete publisher SHA.
Staged full parts additionally verify every publisher digest and concatenated SHA.
No archive extraction outside the private catalog, no arbitrary URL/ZIP paths.
"""
import bisect
import hashlib
import io
import json
import os
import re
import shutil
import time
from contextlib import contextmanager
import zipfile
from pathlib import Path

import httpx
from engine import SYMBOLS
from market_memory import MarketMemory, MAX_CSV_BYTES, sha256_file, identity

BASE = 'https://assets.kraken.com/marketing/institutions/'
RELEASE = '2026-09-30'
PARTS = tuple(f'Kraken_OHLCVT_Full.zip.part{i:02d}' for i in range(5))
HASHES = (
    '12ce809a0cc12c421f9bb871434f71eca8b3c258cb194efae258318a683b9491',
    '7086e1915e79b5157fae05978ca9603da7f918a091652b5fdedda7d7a53d03ee',
    '880fcf84e177e50ca946f3f5cb6d57c721a4950853ca1bf6bdde526896013cc1',
    '3fa1fdfbb94c5a3cc861068a11b5c309502a8ac8da0f4c6f091c5a2968451157',
    'db2c8b80c92a2e09b3f71f464b0b55574bd56c645d2560169dbbe4887b9c00eb',
)
FULL_SHA = 'bc273e9027470b07d790a7fa4df9bb43f0dfc527c20c6ecae48402e123e7b1c7'
CHUNK = 1024 * 1024
MAX_PART = 3 * 1024**3
MAX_RANGE = 128 * 1024**2
MAX_ENTRIES = 100000
RELEASE_END = 1790812800  # 2026-10-01 00 UTC, exclusive
RESERVE = 256 * 1024**2


class ArchiveError(RuntimeError):
    pass


def _size(s):
    if not s or not re.fullmatch(r'[0-9]{1,12}', s) or not 0 < int(s) <= MAX_PART:
        raise ArchiveError('invalid archive size')
    return int(s)


def _space(path, amount):
    if shutil.disk_usage(path).free < amount + RESERVE:
        raise ArchiveError('insufficient free storage for archive operation')


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


class SplitReader(io.RawIOBase):
    """Seekable concatenated file without allocating a second multi-GB ZIP."""
    def __init__(self, lengths, read_at):
        self.ends=[]
        total=0
        for size in lengths:
            if not 0 < size <= MAX_PART: raise ArchiveError('invalid part length')
            total += size; self.ends.append(total)
        self.pos=0; self.read_at=read_at; self.closer=None
    def close(self):
        try:
            if self.closer is not None:self.closer();self.closer=None
        finally:super().close()
    def seekable(self): return True
    def readable(self): return True
    def tell(self): return self.pos
    def seek(self, offset, whence=0):
        pos = offset if whence==0 else self.pos+offset if whence==1 else self.ends[-1]+offset if whence==2 else -1
        if pos<0: raise OSError('invalid seek')
        self.pos=pos; return pos
    def read(self, size=-1):
        remaining=max(0,self.ends[-1]-self.pos)
        if size<0: size=remaining
        if min(size,remaining)>MAX_RANGE: raise ArchiveError('oversized ZIP read')
        result=bytearray()
        until=min(self.pos+size,self.ends[-1])
        while self.pos<until:
            index=bisect.bisect_right(self.ends,self.pos)
            start=0 if index==0 else self.ends[index-1]
            n=min(until-self.pos,self.ends[index]-self.pos,CHUNK)
            data=self.read_at(index,self.pos-start,n)
            if len(data)!=n: raise ArchiveError('short archive read')
            result.extend(data); self.pos+=n
        return bytes(result)


class RemoteParts:
    """Fixed-origin range transport. Bounded total transfer, immutable ETags when supplied."""
    def __init__(self, client, *, budget=MAX_RANGE):
        if type(budget) is not int or not CHUNK <= budget <= MAX_RANGE:
            raise ValueError('invalid range budget')
        self.client=client; self.budget=budget; self.received=0; self.lengths=[]; self.etags=[]; self.cached=None
        for name in PARTS:
            r=client.head(BASE+name,headers={'Accept-Encoding':'identity'})
            if r.status_code!=200: raise ArchiveError('archive HEAD unavailable')
            self.lengths.append(_size(r.headers.get('Content-Length')))
            self.etags.append(r.headers.get('ETag'))
    def read_at(self,index,offset,n):
        out=bytearray()
        while n:
            begin=offset//CHUNK*CHUNK; end=min(begin+CHUNK,self.lengths[index])-1
            key=(index,begin)
            if self.cached is None or self.cached[0]!=key:
                length=end-begin+1
                if self.received+length>self.budget: raise ArchiveError('range budget exhausted')
                headers={'Range':f'bytes={begin}-{end}','Accept-Encoding':'identity'}
                etag=self.etags[index]
                if etag and not etag.startswith('W/'): headers['If-Match']=etag
                with self.client.stream('GET',BASE+PARTS[index],headers=headers) as r:
                    if r.status_code!=206 or r.headers.get('Content-Range')!=f'bytes {begin}-{end}/{self.lengths[index]}' or r.headers.get('Content-Encoding','identity')!='identity':
                        raise ArchiveError('server did not honor exact archive range')
                    if etag and r.headers.get('ETag')!=etag: raise ArchiveError('archive version changed')
                    data=bytearray()
                    for block in r.iter_raw(chunk_size=CHUNK):
                        self.received+=len(block)
                        if len(data)+len(block)>length or self.received>self.budget: raise ArchiveError('oversized archive range')
                        data.extend(block)
                    if len(data)!=length: raise ArchiveError('truncated archive range')
                self.cached=(key,bytes(data))
            data=self.cached[1]; take=min(n,len(data)-(offset-begin))
            if take<=0: raise ArchiveError('invalid range offset')
            out.extend(data[offset-begin:offset-begin+take]);offset+=take;n-=take
        return bytes(out)
    def reader(self): return SplitReader(self.lengths,self.read_at)


def download_full_parts(directory, client):
    """Verified full-release download, resumes partial parts on explicit retry.

    Never start this ~multi-GB operation automatically; use a deliberate developer job.
    """
    root=Path(directory);root.mkdir(mode=0o700,parents=True,exist_ok=True)
    if root.is_symlink() or (os.name=='posix' and root.stat().st_mode & 0o077): raise ArchiveError('private archive directory required')
    for name,expected in zip(PARTS,HASHES):
        target=root/name; partial=root/(name+'.partial')
        if target.is_symlink() or partial.is_symlink(): raise ArchiveError('unsafe archive staging path')
        if target.exists():
            if sha256_file(target)!=expected: raise ArchiveError('verified part changed')
            continue
        meta=client.head(BASE+name,headers={'Accept-Encoding':'identity'})
        if meta.status_code!=200: raise ArchiveError('archive HEAD unavailable')
        total=_size(meta.headers.get('Content-Length')); offset=partial.stat().st_size if partial.exists() else 0
        if offset>total: raise ArchiveError('oversized partial archive')
        _space(root,total-offset)
        if offset<total:
            headers={'Accept-Encoding':'identity'}
            if offset: headers['Range']=f'bytes={offset}-'
            etag=meta.headers.get('ETag')
            if etag and not etag.startswith('W/'): headers['If-Match']=etag
            with client.stream('GET',BASE+name,headers=headers) as r:
                if r.headers.get('Content-Encoding','identity')!='identity' or (etag and r.headers.get('ETag')!=etag): raise ArchiveError('archive changed or encoded')
                if offset and r.status_code==200: offset=0; _space(root,total)
                elif offset and (r.status_code!=206 or r.headers.get('Content-Range')!=f'bytes {offset}-{total-1}/{total}'): raise ArchiveError('invalid resume range')
                elif not offset and r.status_code!=200: raise ArchiveError('archive download unavailable')
                with partial.open('ab' if offset else 'wb') as dst:
                    os.chmod(partial,0o600)
                    try:
                        for block in r.iter_raw(chunk_size=CHUNK):
                            if offset+len(block)>total: raise ArchiveError('oversized archive body')
                            dst.write(block);offset+=len(block)
                    finally: dst.flush();os.fsync(dst.fileno())
            if offset!=total: raise ArchiveError('incomplete part; retry resumes')
        if sha256_file(partial)!=expected:
            partial.unlink();raise ArchiveError('publisher part SHA-256 mismatch; corrupt partial removed')
        os.replace(partial,target);_sync_dir(root)
    return root


def verified_reader(directory):
    paths=[Path(directory)/name for name in PARTS];lengths=[];full=hashlib.sha256();handles=[];stats=[]
    def cleanup():
        for handle in handles:handle.close()
    try:
        for path,expected in zip(paths,HASHES):
            if path.is_symlink() or not path.is_file(): raise ArchiveError('missing or unsafe part')
            f=os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW),'rb');handles.append(f)
            stat=os.fstat(f.fileno());size=stat.st_size
            if not 0<size<=MAX_PART: raise ArchiveError('invalid part size')
            part=hashlib.sha256()
            for block in iter(lambda:f.read(CHUNK),b''):part.update(block);full.update(block)
            after=os.fstat(f.fileno())
            if (stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns) or part.hexdigest()!=expected:raise ArchiveError('publisher part SHA-256 mismatch or changed')
            lengths.append(size);stats.append((stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns))
        if full.hexdigest()!=FULL_SHA:raise ArchiveError('publisher full SHA-256 mismatch')
        def read(index,offset,n):
            f=handles[index];stat=os.fstat(f.fileno())
            if (stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)!=stats[index]:raise ArchiveError('verified part changed during read')
            f.seek(offset);return f.read(n)
        reader=SplitReader(lengths,read);reader.closer=cleanup;return reader
    except BaseException:
        cleanup();raise


def _import_members(memory, reader, *, intervals=(60,), verified=False, fixture=False, now=None):
    """Only allowlisted members; ZIP CRC verified by reading to EOF; import cursor durable.

    Record the validation mode explicitly, never imply full SHA from selective bytes.
    """
    intervals=tuple(intervals)
    if not intervals or len(intervals)>7 or len(set(intervals))!=len(intervals): raise ValueError('invalid intervals')
    for interval in intervals: identity('BTC/USD',interval)
    mode='publisher_full_sha256' if verified else 'https_range_member_crc32'
    if fixture: mode='fixture'
    root=memory.root/'archive-intake';root.mkdir(mode=0o700,exist_ok=True)
    if root.is_symlink() or (os.name=='posix' and root.stat().st_mode & 0o077): raise ArchiveError('unsafe extraction directory')
    results=[]
    with zipfile.ZipFile(reader) as z:
        infos=z.infolist()
        if len(infos)>MAX_ENTRIES: raise ArchiveError('archive directory exceeds limit')
        selected=[]
        for pair,(base,alias) in SYMBOLS.items():
            for interval in intervals:
                valid={f'{code}_{interval}.csv' for code in (base,alias,pair.replace('/',''))}
                matches=[i for i in infos if i.filename.rsplit('/',1)[-1] in valid and not i.is_dir()]
                if len(matches)!=1: raise ArchiveError('missing or ambiguous requested archive member')
                i=matches[0]
                if i.flag_bits&1 or i.file_size<=0 or i.file_size>MAX_CSV_BYTES: raise ArchiveError('invalid archive member')
                selected.append((pair,interval,i))
        for pair,interval,i in selected:
            # Flatten only the prevalidated basename. Never extractall.
            name=i.filename.rsplit('/',1)[-1];target=root/name;partial=root/(name+'.partial')
            if target.is_symlink() or partial.is_symlink():raise ArchiveError('unsafe member path')
            _space(root,i.file_size*2)
            partial.unlink(missing_ok=True);h=hashlib.sha256();count=0
            try:
                with z.open(i) as src,partial.open('xb') as dst:
                    os.chmod(partial,0o600)
                    for block in iter(lambda:src.read(CHUNK),b''):
                        count+=len(block)
                        if count>i.file_size: raise ArchiveError('member exceeded advertised size')
                        h.update(block);dst.write(block)
                    dst.flush();os.fsync(dst.fileno())
                if count!=i.file_size: raise ArchiveError('incomplete member')
                os.replace(partial,target);_sync_dir(root)
                # Reject a replacement release rather than falsely attributing it.
                # Inspect exact last line before publishing any source.
                if not fixture:
                    with target.open('rb') as check:
                        check.seek(max(0,count-1024)); tail=check.read().splitlines()
                    if not tail or int(tail[-1].split(b',',1)[0])+interval*60>RELEASE_END:
                        raise ArchiveError('archive exceeds pinned release cutoff')
                provenance={'release':RELEASE,'member':i.filename,'validation':mode,'source_urls':[BASE+n for n in PARTS] if not fixture else [],'publisher_full_sha256':FULL_SHA if verified and not fixture else None,'csv_sha256':h.hexdigest(),'crc32':f'{i.CRC:08x}','bytes':count,'pair':pair,'interval':interval}
                receipt=memory.import_archive(target,pair,interval,h.hexdigest(),fixture=fixture,now=now,provenance=provenance)
                if receipt['state']!='complete':raise ArchiveError('member import incomplete')
                results.append(provenance|{'source_id':receipt['id'],'rows':receipt['cursor'],'end':receipt['last_ts']})
            finally: partial.unlink(missing_ok=True);target.unlink(missing_ok=True)
    return results


@contextmanager
def exclusive_job(root, name):
    """Linux custody: at most one archive or stream writer per catalog/job."""
    import fcntl
    path=Path(root)/('.'+name+'.lock')
    fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ArchiveError('job already running') from None
        yield
    finally:os.close(fd)


def import_members(memory, reader, **kwargs):
    with exclusive_job(memory.root,'archive-import'):
        return _import_members(memory,reader,**kwargs)


def public_client():
    return httpx.Client(timeout=httpx.Timeout(45,connect=15),follow_redirects=False,trust_env=False)
