// Synthetic offline UI state. This fixture is never served by the application.
window.__fixture={status:{mode:'PAPER',paused:true,human_pause:false,virtual_cap_cents:0,allocated_cents:0,engine_connected:false,live_available:false},instances:[],setup:{vault_available:true,connections_verified:false,deployment_ready:false,credentials:{grok:'not_configured',kraken_read_key:'not_configured',kraken_read_secret:'not_configured',telegram_desk:'not_configured',telegram_recovery:'not_configured'}},calendar:{state:'not_connected',events:[],source:null}};
window.fetch=async (path,request)=>{
  const f=window.__fixture;
  const response=(value,status=200)=>({ok:status>=200&&status<300,status,json:async()=>JSON.parse(JSON.stringify(value))});
  if(request.headers.Authorization!=='Bearer '+'browser-test-only-access-'.repeat(3))return response({detail:'Unauthorized'},401);
  const body=request.body===undefined?{}:JSON.parse(request.body);
  if(path==='/api/status')return response(f.status);
  if(path==='/api/instances'&&request.method==='GET')return response(f.instances);
  if(path==='/api/setup')return response(f.setup);
  if(path==='/api/calendar')return response(f.calendar);
  if(path.startsWith('/api/markets/BTC/session-profile?'))return response({pair:'BTC/USD',coverage:0,expected:2160,missing:2160,conflicts:0,bins:[],state:'awaiting_history'});
  if(path==='/api/setup/credentials'){
    for(const key of Object.keys(body))f.setup.credentials[key]='stored_unverified';
    return response(f.setup);
  }
  if(path==='/api/setup/paper-capital'){
    f.status.virtual_cap_cents=body.virtual_cap_cents;return response(f.status);
  }
  if(path==='/api/instances'&&request.method==='POST'){
    const row={...body,id:'fixture-id-'+f.instances.length,state:'PAUSED'};f.instances.push(row);f.status.allocated_cents+=row.virtual_cents;return response(row,201);
  }
  if(path==='/api/pause'){f.status.human_pause=true;return response(f.status);}
  return response({detail:'Market data unavailable'},503);
};
