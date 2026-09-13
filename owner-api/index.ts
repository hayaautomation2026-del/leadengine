const origin = 'https://hayaautomation2026-del.github.io';
const CHECK = '07dfa044-4beb-48ec-82e9-b66dd8b7e909';
const base = Deno.env.get('SUPABASE_URL')! + '/rest/v1/';
const key = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!;
const headers = {apikey:key, Authorization:'Bearer '+key, 'Content-Type':'application/json'};
async function db(path: string, method='GET', body?: unknown) {
  const r = await fetch(base+path, {method, headers:{...headers,Prefer:'return=representation'},body:body===undefined?undefined:JSON.stringify(body)});
  if(!r.ok) throw new Error('Database request failed');
  return r.json();
}
function response(body: unknown, status=200) {
  return new Response(JSON.stringify(body), {status,headers:{'Content-Type':'application/json','Cache-Control':'no-store',
    'Access-Control-Allow-Origin':origin,'Access-Control-Allow-Headers':'authorization,content-type',
    'Access-Control-Allow-Methods':'GET,POST,OPTIONS','Vary':'Origin'}});
}
Deno.serve(async(req: Request)=>{
  if(req.method==='OPTIONS') return response({});
  const caller=req.headers.get('origin');
  if(caller && caller!==origin) return response({error:'Origin not allowed'},403);
  try {
    const token=(req.headers.get('authorization')||'').replace(/^Bearer /,'');
    if(!/^[A-Za-z0-9_-]{40,100}$/.test(token)) return response({error:'Private access link required'},401);
    const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(token)))).map(x=>x.toString(16).padStart(2,'0')).join('');
    const auth=await db('sdr_dashboard_access?token_hash=eq.'+hash+'&revoked=eq.false&expires_at=gt.'+encodeURIComponent(new Date().toISOString())+'&select=token_hash');
    if(auth.length!==1) return response({error:'Access link expired or invalid'},401);
    const rows=await db('sdr_test_conversations?check_id=eq.'+CHECK+'&select=check_id,enabled,owner,status,version,replies_sent,reply_cap,expires_at,last_output_id,last_decision,state');
    const c=rows[0];
    if(!c) return response({error:'Conversation not found'},404);
    if(req.method==='POST') {
      const input=await req.json();
      if(!['pause','resume','takeover'].includes(input.action)) return response({error:'Unknown action'},400);
      if(input.version!==c.version) return response({error:'The conversation changed. Refresh and try again.'},409);
      if(c.status==='sending'||c.status==='processing') return response({error:'A reply is being processed. Try again shortly.'},409);
      if(input.action==='resume'&&(c.status==='stopped'||c.state.status==='stopped'||c.replies_sent>=c.reply_cap||Date.parse(c.expires_at)<=Date.now())) return response({error:'The test is stopped, expired, or has reached its reply limit.'},409);
      const active=input.action==='resume';
      const owner=active?'sdr':'human';
      const state={...c.state,owner,status:active?'active':c.state.status};
      const updated=await db('sdr_test_conversations?check_id=eq.'+CHECK+'&version=eq.'+c.version,'PATCH',{
        enabled:active,owner,status:active?'active':c.status,version:c.version+1,state,updated_at:new Date().toISOString()});
      if(updated.length!==1) return response({error:'Concurrent change; refresh first'},409);
      await db('sdr_response_events','POST',{check_id:CHECK,event:'owner_control',data:{action:input.action}});
      return response({ok:true});
    }
    if(req.method!=='GET') return response({error:'Method not allowed'},405);
    const [events,checks,settings] = await Promise.all([
      db('sdr_response_events?check_id=eq.'+CHECK+'&event=neq.inbox_checked&order=at.desc&limit=150'),
      db('sdr_response_events?check_id=eq.'+CHECK+'&event=eq.inbox_checked&order=at.desc&limit=30'),
      db('sdr_settings?select=sending_enabled,kill_switch&limit=1')]);
    return response({now:new Date().toISOString(),conversation:c,events,checks,settings:settings[0]||{}});
  }catch(e){console.error('Owner API error',e instanceof Error?e.name:'Unknown');return response({error:'Unable to load live data. Please retry.'},500);}
});
