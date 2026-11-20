'use strict';
// Client-side observer for an ALREADY OPEN dashboard. Does not authenticate,
// store credentials, call providers, or replace local host supervision.
// A new uncached remote dashboard cannot load while its host is unreachable.
const INTERVAL_MS = 15000;
const DEADLINE_MS = 3500;

function classifyFailure(err, nowMs) {
  // Accept only wrapper-classified, fixed codes. Never display exception text.
  const code = err && err.kind;
  if (code === 'not_authorized') return {browserToServer:'unauthorized', receivedAtMs:nowMs};
  if (code === 'server_unhealthy') return {browserToServer:'reachable', receivedAtMs:nowMs, internet:'unknown',services:{dashboard:'failed'}};
  return {browserToServer:'unreachable',receivedAtMs:nowMs};
}

function createObserver({readHealth, onSnapshot, clock=Date.now,
                         schedule=setTimeout, cancel=clearTimeout,
                         intervalMs=INTERVAL_MS, deadlineMs=DEADLINE_MS}) {
  if (typeof readHealth !== 'function' || typeof onSnapshot !== 'function' ||
      !Number.isInteger(intervalMs) || intervalMs < 1000 ||
      !Number.isInteger(deadlineMs) || deadlineMs < 100) throw Error('invalid observer configuration');
  let active=false, epoch=0, pending=null, timer=null, deadline=null;
  function clearAll() {
    if (timer!==null) { cancel(timer);timer=null; }
    if (deadline!==null) { cancel(deadline);deadline=null; }
    if (pending) { pending.abort();pending=null; }
  }
  async function poll(seq) {
    const controller=new AbortController();pending=controller;
    let timedOut=false;
    const timeoutPromise=new Promise((_,reject)=>{
      deadline=schedule(()=>{
        timedOut=true;
        controller.abort();
        reject({kind:'timeout'});
      },deadlineMs);
    });
    try {
      // Race a hard wall deadline even if a buggy readHealth ignores abort.
      const report=await Promise.race([Promise.resolve().then(()=>readHealth(controller.signal)),timeoutPromise]);
      if (!active || seq!==epoch) return;
      // Never trust a remote timestamp as if clocks were synchronized.
      // Health report must have been checked by the server/status-only gateway.
      if (!report || typeof report!=='object' || report.kind!=='ok' ||
          !report.health || typeof report.health!=='object') throw {kind:'invalid_status'};
      const snapshot={...report.health,browserToServer:'reachable',receivedAtMs:clock()};
      onSnapshot(snapshot);
    } catch (err) {
      if (active && seq===epoch) onSnapshot(classifyFailure(timedOut?{kind:'timeout'}:err,clock()));
    } finally {
      if (seq!==epoch || !active) return;
      if (deadline!==null) {cancel(deadline);deadline=null;}
      pending=null;
      timer=schedule(()=>{timer=null;void poll(epoch);},intervalMs);
    }
  }
  return {
    start() {
      if (active) return;
      active=true;epoch++;
      onSnapshot({browserToServer:'unknown',receivedAtMs:clock()});
      void poll(epoch);
    },
    stop() {active=false;epoch++;clearAll();},
    isRunning() {return active;}
  };
}
module.exports={createObserver,classifyFailure,INTERVAL_MS,DEADLINE_MS};