# SPDX-License-Identifier: MIT
"""Public Playwright capture and session-bound, recoverable local transfer."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit

from sites_downloads import DownloadError, limits
from sites_url_scrub import scrub_url

SCRATCH_ROOT=Path('/run/metnos-browser-downloads')
SCRATCH_BYTES=128*1024*1024


def verified_scratch(root=SCRATCH_ROOT):
    root=Path(root)
    if root.is_symlink() or not root.is_dir():
        raise DownloadError('download_scratch_quota_missing')
    mount=None
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        left,sep,right=line.partition(' - ')
        fields=left.split()
        if sep and len(fields)>5 and fields[4]==str(root):
            mount=(fields,right.split())
    if mount is None or mount[1][0]!='tmpfs':
        raise DownloadError('download_scratch_quota_missing')
    fs=os.statvfs(root)
    if not 0<fs.f_blocks*fs.f_frsize<=SCRATCH_BYTES:
        raise DownloadError('download_scratch_quota_invalid')
    # Every browser variant uses a child of this one aggregate kernel mount.
    child=root/str(os.geteuid())
    child.mkdir(mode=0o700,exist_ok=True)
    st=child.lstat()
    if child.is_symlink() or st.st_uid!=os.geteuid() or st.st_mode&0o077:
        raise DownloadError('download_scratch_quota_invalid')
    return child


# This records only synchronous event-handler causality. A later timer/promise
# is deliberately outside the witness. No JS selector or remote path grants
# any filesystem authority.
_WITNESS_JS=r"""(() => {
 if(window.__metnosDownloadWitness)return;
 let target=null,active=0,urls=[],submitted=false;
 const enter=(fn,receiver,event,args)=>{
  const own=event?.isTrusted&&event.composedPath().includes(target);
  if(own)active++;
  try{return fn.apply(receiver,args)}finally{if(own)active--}
 };
 const add=EventTarget.prototype.addEventListener;
 const remove=EventTarget.prototype.removeEventListener;
 const wrappers=new WeakMap();
 EventTarget.prototype.addEventListener=function(type,fn,options){
  if((type==='click'||type==='submit')&&typeof fn==='function'){
   let byType=wrappers.get(fn);if(!byType){byType=new Map();wrappers.set(fn,byType)}
   const key=type+':'+Boolean(typeof options==='boolean'?options:options?.capture);
   if(!byType.has(key))byType.set(key,function(event){return enter(fn,this,event,arguments)});
   return add.call(this,type,byType.get(key),options);
  }return add.call(this,type,fn,options);
 };
 EventTarget.prototype.removeEventListener=function(type,fn,options){
  const key=type+':'+Boolean(typeof options==='boolean'?options:options?.capture);
  return remove.call(this,type,wrappers.get(fn)?.get(key)||fn,options);
 };
 const anchorClick=HTMLAnchorElement.prototype.click;
 HTMLAnchorElement.prototype.click=function(){if(active&&this.href)urls.push(this.href);return anchorClick.apply(this,arguments)};
 window.__metnosDownloadWitness={
  arm(el){target=el;urls=[];submitted=false;
   for(let node=el;node;node=node.parentElement){
    const fn=node.onclick;if(typeof fn==='function'&&!fn.__metnosWrapped){
     const wrapped=function(event){return enter(fn,this,event,arguments)};
     wrapped.__metnosWrapped=true;node.onclick=wrapped;
    }
   }
   const form=el.form||el.closest('form');
   if(form)add.call(form,'submit',event=>{if(event.isTrusted)submitted=true},{once:true});
   return {href:el.tagName==='A'?el.href:'',
           action:form?(el.hasAttribute('formaction')?el.formAction:form.action):'',
           method:form?(el.hasAttribute('formmethod')?el.formMethod:form.method||'get').toUpperCase():''};
  },read(){return {urls:[...urls],submitted}},clear(){target=null;urls=[];submitted=false}
 };
})()"""


@dataclass
class Captured:
    path:Path
    metadata:dict
    binding:dict
    state:str='captured'
    active_streams:int=0


class ContextDownloads:
    def __init__(self,context):
        self.context=context;self.records={};self.receipts={};self.active=None
        self.tasks=set();self.closed=False;self.files=0;self.bytes=0

    async def install(self):
        await self.context.add_init_script(_WITNESS_JS)
        self.context.on('page',self._page)
        for page in self.context.pages:self._page(page)

    def _page(self,page):
        page.on('download',self._download)

    def _download(self,download):
        if self.active is not None:
            self.active.append(download)
        else:
            task=asyncio.create_task(self._discard(download));self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def _discard(self,download):
        # cancel() alone leaves a completed file in the browser scratch.
        # Cleanup has its own small bound; it never extends remote authority.
        try:
            async with asyncio.timeout(2):
                await download.cancel()
                await download.delete()
        except Exception:
            pass

    async def acquire(self,page,handle,*,action_id,binding,dispatch,timeout_s=120,download_limits=None):
        started=time.monotonic()
        if action_id in self.receipts:
            return dict(self.receipts[action_id])
        if self.closed or self.active is not None:
            return {'ok':False,'error_class':'download_capture_unavailable'}
        cap=limits(download_limits)
        if self.files>=cap['max_files'] or self.bytes>=cap['max_total_bytes']:
            return {'ok':False,'error_class':'download_budget_exceeded'}
        deadline=started+min(timeout_s,cap['timeout_s'])
        events=[];self.active=events;requests=[]
        try:
            async with asyncio.timeout_at(deadline):
                frame=await handle.owner_frame()
                if frame is None:
                    raise DownloadError('download_origin_unproven')
                await frame.evaluate(_WITNESS_JS)
                expected=await handle.evaluate('el=>window.__metnosDownloadWitness.arm(el)')
            if time.monotonic()>=deadline:
                raise TimeoutError
        except TimeoutError:
            self.active=None
            return {'ok':False,'executed':False,'error_class':'download_timeout'}
        except asyncio.CancelledError:
            self.active=None
            raise
        except Exception:
            self.active=None
            return {'ok':False,'executed':False,'error_class':'download_target_unavailable'}
        def request_seen(request):
            try:
                if request.frame==frame and request.is_navigation_request():
                    requests.append((request.url,request.method))
            except Exception:
                pass
        self.context.on('request',request_seen)
        # Install the receipt before dispatch, even if cancellation interrupts.
        result={'ok':False,'executed':True,'error_class':'download_outcome_unknown'}
        self.receipts[action_id]=result
        try:
            async with asyncio.timeout_at(deadline):
                await dispatch()
                while not events:
                    await asyncio.sleep(0.01)
                download=events[0]
                source_page=download.page
                opener=None if source_page==page else await source_page.opener()
                witness=await frame.evaluate('window.__metnosDownloadWitness.read()')
                url=download.url
                static=(expected['href'] and expected['href']==url)
                post=(expected['action']==url and witness['submitted'] and
                      requests.count((url,expected['method']))==1)
                synchronous=url in witness['urls']
                if not (source_page==page or opener==page) or not (static or post or synchronous):
                    raise DownloadError('download_origin_unproven')
                path=Path(await download.path())
                if len(events)!=1:
                    raise DownloadError('download_origin_ambiguous')
                size=path.stat().st_size
                if size>cap['max_file_bytes'] or self.bytes+size>cap['max_total_bytes']:
                    raise DownloadError('download_size_exceeded')
                sha=hashlib.sha256();prefix=b''
                with path.open('rb') as stream:
                    while data:=stream.read(64*1024):
                        if not prefix:prefix=data[:5]
                        sha.update(data)
                token=secrets.token_hex(24)
                parsed=urlsplit(url)
                scrubbed=scrub_url(url) if parsed.scheme in ('http','https') else parsed.scheme+':'
                metadata={'handle':token,'action_id':action_id,'bytes':size,'sha256':sha.hexdigest(),
                          'media_type':'application/pdf' if prefix==b'%PDF-' else 'application/octet-stream',
                          'url':scrubbed,'state':'captured','captured_at':time.time()}
                metadata['deadline_monotonic']=deadline
                self.records[token]=Captured(path,metadata,dict(binding))
                self.files+=1;self.bytes+=size
                result={'ok':True,'executed':True,'primitive':'download','download':dict(metadata)}
        except TimeoutError:
            result={'ok':False,'executed':True,'error_class':'download_timeout'}
        except DownloadError as exc:
            result={'ok':False,'executed':True,'error_class':str(exc)}
        except asyncio.CancelledError:
            raise
        except Exception:
            result={'ok':False,'executed':True,'error_class':'download_failed'}
        finally:
            self.active=None
            self.context.remove_listener('request',request_seen)
            if not result.get('ok'):
                await asyncio.gather(*(self._discard(item) for item in events),return_exceptions=True)
            try:
                async with asyncio.timeout(.25):
                    await frame.evaluate('window.__metnosDownloadWitness.clear()')
            except Exception:
                pass
            self.receipts[action_id]=result
        return dict(result)

    def begin_stream(self,token):
        record=self.records.get(token)
        # Only the retained local file can retry. Its reservation, deadline
        # and opaque handle stay unchanged; never dispatch another site action.
        if (not record or record.state not in ('captured','transfer_failed','transferred')
                or record.active_streams or self.closed):
            raise DownloadError('download_transfer_invalid')
        if time.monotonic()>=record.metadata['deadline_monotonic']:
            raise DownloadError('download_transfer_timeout')
        # Open before marking streaming: context closure cannot swap this fd.
        stream=record.path.open('rb')
        record.state='streaming';record.active_streams+=1
        return record,stream

    def end_stream(self,record,*,complete=True):
        record.active_streams-=1
        if record.state=='streaming':
            record.state='transferred' if complete else 'transfer_failed'

    def acknowledge(self,token,sha):
        record=self.records.get(token)
        if not record or sha!=record.metadata['sha256'] or record.state not in ('transferred','acknowledged'):
            raise DownloadError('download_ack_invalid')
        if record.state!='acknowledged':
            self.files-=1;self.bytes-=record.metadata['bytes']
        record.state='acknowledged'
        record.path.unlink(missing_ok=True)
        return {'ok':True,'state':'acknowledged','handle':token}

    async def close(self):
        self.closed=True
        self.context.remove_listener('page',self._page)
        for page in self.context.pages:
            page.remove_listener('download',self._download)
        # Context close waits for an active local stream; its fd/bytes remain
        # available until transfer finishes, never certified as published here.
        deadline=time.monotonic()+120
        while any(r.active_streams for r in self.records.values()) and time.monotonic()<deadline:
            await asyncio.sleep(0.02)
        for record in self.records.values():record.path.unlink(missing_ok=True)
        self.records.clear()
        self.files=0;self.bytes=0
        if self.tasks:await asyncio.gather(*self.tasks,return_exceptions=True)
