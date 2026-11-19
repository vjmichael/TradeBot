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
  if(path.startsWith('/api/economic/statistics/history?'))return response({state:'retained_history',total:1,distinct_records:1,rows:[{observed_at:1779500000,period:'2026-04',title:'Fixture unemployment rate',value:'4.2',unit:'percent'}],next_offset:null});
  if(path.startsWith('/api/economic/extended?'))return response({state:'observed',rows:[{period:'2026-04',title:'Fixture producer prices',value:'150',unit:'index'}],next_offset:null});
  if(path.startsWith('/api/economic/statistics?'))return response({state:'observed',rows:[{period:'2026-04',title:'Fixture unemployment rate',value:'4.2',unit:'percent',evidence_id:'fixture'}],next_offset:null});
  if(path.startsWith('/api/economic/monetary?'))return response({state:'unavailable',rows:[],next_offset:null});
  if(path.startsWith('/api/market-directory?'))return response({state:'unavailable',version:null,observed_at:null,total:0,markets:[],selected:['BTC/USD','ETH/USD','SOL/USD'].map(pair=>({pair,preparation:'baseline_only',directory_status:'unverified',mapping_changed:false})),next_offset:null});
  if(path.startsWith('/api/markets/BTC/readiness'))return response({pair:'BTC/USD',as_of:1760000000,expected:43800,coverage:0,missing:43800,conflicts:0,first_observed:null,last_observed:null,last_recent:null,archive_retention:{assets:0,integrity:{}},latest_book_observed:null,classification_hours:{no_observed_history:43800},ranges:[{start:1600000000,end:1760000000,hours:43800,reason:'no_observed_history',recovery:'independent_anchors_missing',pages:0}],economic_sources:[],next_offset:null});
  if(path==='/api/data-preparation')return response({state:'review_required',training_ready:false,markets:[{pair:'BTC/USD',expected:43800,coverage:0,missing:43800,conflicts:0}],job:f.job||{state:'idle'},streams:[],schedule:f.schedule||{enabled:false,service:'stopped',next_repair:null},recovery:[]});
  if(path==='/api/data-preparation/collect'){f.job={id:'fixture',state:'running',mode:'collect',stage:'continuous_public_data'};return response(f.job);}
  if(path==='/api/data-preparation/cancel'){f.job={id:'fixture',state:'cancelled',mode:'collect',stage:'stopping'};return response(f.job);}
  if(path==='/api/data-schedule'){f.schedule={enabled:body.enabled,service:'stopped',next_repair:body.enabled?1795080000:null};return response(f.schedule);}
  if(path==='/api/data-maintenance/backup'){f.maintenance={copies:1,max_copies:4,latest_backup:{id:'fixture',bytes:10485760,cutoff:1795075000},cleanup_preview:null,last_cleanup:null};f.job={id:'fixture-backup',state:'complete',mode:'backup'};return response(f.job);}
  if(path==='/api/data-maintenance/preview-retention'){f.maintenance.cleanup_preview={id:'0123456789abcdef0123456789abcdef',attempts:2,books:1};f.job={id:'fixture-preview',state:'complete',mode:'preview-retention'};return response(f.job);}
  if(path==='/api/data-maintenance/retire'){if(body.preview_id!==f.maintenance.cleanup_preview.id)return response({detail:'Review cleanup first'},409);f.maintenance.last_cleanup={books:1};f.maintenance.cleanup_preview=null;f.job={id:'fixture-retired',state:'complete',mode:'retire'};return response(f.job);}
  if(path==='/api/market-storage')return response({market_memory_bytes:10485760,free_bytes:10737418240,state:'available',staging_retained:true,maintenance:f.maintenance||{copies:0,max_copies:4,latest_backup:null,cleanup_preview:null,last_cleanup:null}});
  if(path.startsWith('/api/markets/BTC/quality?'))return response({pair:'BTC/USD',rv_hours:0,expected_hours:72,book_samples:0,expected_books:864,bins:[],latest:null});
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
