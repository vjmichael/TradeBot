const assert=require('node:assert/strict');
const {createObserver,classifyFailure}=require('./offline_connection_client.cjs');
function wait(){return new Promise(r=>setImmediate(r));}
(async()=>{
 let total=0;function check(x){assert.ok(x);total++;}
 check(classifyFailure({kind:'not_authorized'},100).browserToServer==='unauthorized');
 check(classifyFailure({kind:'server_unhealthy'},100).services.dashboard==='failed');
 check(classifyFailure({kind:'network_offline'},100).browserToServer==='unreachable');
 check(!JSON.stringify(classifyFailure({kind:'network_offline',message:'PASSWORD_SECRET'},100)).includes('PASSWORD_SECRET'));
 let snapshots=[],count=0;
 const observe=createObserver({readHealth:async()=>{count++;return {kind:'ok',health:{internet:'online',services:{dashboard:'ready'}}};},onSnapshot:s=>snapshots.push(s),clock:()=>200,schedule:()=>1,cancel:()=>{},intervalMs:1000});
 observe.start(); await wait();
 check(snapshots[0].browserToServer==='unknown');
 check(snapshots[1].browserToServer==='reachable');
 check(snapshots[1].receivedAtMs===200);
 observe.start();check(count===1);observe.stop();check(!observe.isRunning());
 const failure=[];
 const bad=createObserver({readHealth:async()=>{throw {kind:'network_offline',message:'SECRET'};},onSnapshot:s=>failure.push(s),schedule:()=>2,cancel:()=>{},intervalMs:1000});
 bad.start();await wait();check(failure.at(-1).browserToServer==='unreachable');check(!JSON.stringify(failure).includes('SECRET'));bad.stop();
 const unauthorized=[];
 const denied=createObserver({readHealth:async()=>{throw {kind:'not_authorized'};},onSnapshot:s=>unauthorized.push(s),schedule:()=>3,cancel:()=>{},intervalMs:1000});
 denied.start();await wait();check(unauthorized.at(-1).browserToServer==='unauthorized');denied.stop();
 const unhealthy=[];
 const failService=createObserver({readHealth:async()=>{throw {kind:'server_unhealthy'};},onSnapshot:s=>unhealthy.push(s),schedule:()=>4,cancel:()=>{},intervalMs:1000});
 failService.start();await wait();check(unhealthy.at(-1).services.dashboard==='failed');failService.stop();
 let resolveLate;const late=[];
 const slow=createObserver({readHealth:()=>new Promise(r=>{resolveLate=r;}),onSnapshot:s=>late.push(s),schedule:()=>5,cancel:()=>{},intervalMs:1000});
 slow.start();await wait();slow.stop();resolveLate({kind:'ok',health:{internet:'online'}});await wait();check(late.length===1);
 let deadlineFn; const expiring=[];
 const hanging=createObserver({readHealth:()=>new Promise(()=>{}),onSnapshot:s=>expiring.push(s),schedule:fn=>{if(!deadlineFn)deadlineFn=fn;return 7;},cancel:()=>{},intervalMs:1000});
 hanging.start();await wait();deadlineFn();await wait();check(expiring.at(-1).browserToServer==='unreachable');hanging.stop();
 const clockTest=[];
 const unsync=createObserver({readHealth:async()=>({kind:'ok',health:{internet:'online',receivedAtMs:999999}}),onSnapshot:s=>clockTest.push(s),clock:()=>33,schedule:()=>6,cancel:()=>{},intervalMs:1000});
 unsync.start();await wait();check(clockTest.at(-1).receivedAtMs===33);unsync.stop();
 try {createObserver({readHealth:0,onSnapshot:()=>{}});throw Error('not refused');}catch(e){check(e.message==='invalid observer configuration');}
 console.log('PASS '+total+' offline client assertions');
})().catch(e=>{console.error(e);process.exitCode=1;});