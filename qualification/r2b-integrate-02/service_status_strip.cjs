'use strict';
// Pure UI projection. Health observations must be authenticated and produced by
// independent service supervision; never use this component for admission.
const REQUIRED = Object.freeze(['dashboard', 'vault', 'risk_gate', 'reconciliation', 'market_data']);
const MAX_AGE_MS = 60000;
function view(snapshot, nowMs) {
  const checking = {network:'Checking connection', services:'Checking services', state:'unknown', detail:'No current service health evidence.'};
  if (!snapshot || typeof nowMs !== 'number' || !Number.isFinite(nowMs)) return checking;
  if (snapshot.browserToServer === 'unreachable') return {network:'Server unreachable',services:'Service status unknown',state:'offline',detail:'Cannot contact Maj Vaj. The VPS may be unavailable, or the connection may be interrupted.'};
  if (snapshot.browserToServer !== 'reachable') return checking;
  if (!Number.isFinite(snapshot.receivedAtMs) || snapshot.receivedAtMs > nowMs || nowMs - snapshot.receivedAtMs >= MAX_AGE_MS) return {network:'Status unknown',services:'Checking services',state:'unknown',detail:'The latest service report is too old to rely on.'};
  const internet = snapshot.internet;
  const network = internet === 'online' ? 'Online' :
      internet === 'no_network_route' || internet === 'internet_unreachable' ? 'No internet connection' :
      internet === 'dns_problem' ? 'DNS problem' :
      internet === 'tls_problem' ? 'Secure connection problem' : 'Internet status unknown';
  const ready = snapshot.services && typeof snapshot.services === 'object' && REQUIRED.every(k => snapshot.services[k] === 'ready');
  const unavailable = snapshot.services && REQUIRED.some(k => snapshot.services[k] === 'failed');
  const available = internet === 'online' && ready;
  return {
    network,
    services:available ? 'Services available' : unavailable ? 'Services need attention' : 'Checking services',
    state: available ? 'ready' : internet === 'no_network_route' || internet === 'internet_unreachable' ? 'offline' : unavailable ? 'degraded' : 'unknown',
    detail:available ? 'Required Maj Vaj services have recent health confirmation. This is not permission to trade.' :
      'Check Account connections for provider details. This status does not authorize trading or restart services.'
  };
}
function WiFiIcon(React, active) {
  const h=React.createElement;
  const path=active
    ? [h('path',{key:'outer',d:'M2 8.82a15 15 0 0 1 20 0'}),h('path',{key:'middle',d:'M5 12.86a10 10 0 0 1 14 0'}),h('path',{key:'small',d:'M8.5 16.43a5 5 0 0 1 7 0'}),h('path',{key:'dot',d:'M12 20h.01'})]
    : [h('path',{key:'outer',d:'M2 8.82a15 15 0 0 1 10-3.82'}),h('path',{key:'mid',d:'M6 12.2a10 10 0 0 1 6-2'}),h('path',{key:'slash',d:'M3 3l18 18'}),h('path',{key:'dot',d:'M12 20h.01'})];
  return h('svg',{className:'maj-status-wifi',viewBox:'0 0 24 24',width:18,height:18,fill:'none',stroke:'currentColor',strokeWidth:2,strokeLinecap:'round',strokeLinejoin:'round','aria-hidden':'true'},...path);
}
function ServiceStrip({React,snapshot,nowMs,onDetails}) {
  const h=React.createElement;
  const s=view(snapshot,nowMs);
  return h('footer',{className:'maj-service-strip status-'+s.state,role:'status','aria-live':'polite','aria-label':'Maj Vaj connectivity and service health'},
      h('span',{className:'maj-service-summary'},WiFiIcon(React,s.state==='ready'),
        h('span',{className:'maj-network-label'},s.network),
        h('strong',{className:'maj-services-label'},s.services)),
      typeof onDetails==='function' ? h('button',{className:'maj-services-details',type:'button',onClick:onDetails,'aria-label':'View connection and service details'},'Details') : null,
      h('span',{className:'maj-service-hint'},s.detail));
}
module.exports={view,ServiceStrip,REQUIRED,MAX_AGE_MS};