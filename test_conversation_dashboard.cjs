// Structural contract for the current live owner dashboard.
// Full interaction and responsive behavior is covered by test_dashboard_live.cjs.
const {JSDOM}=require('jsdom');
const fs=require('node:fs');
const assert=require('node:assert/strict');

const html=fs.readFileSync('dashboard.html','utf8');
const dom=new JSDOM(html,{runScripts:'outside-only'});
const d=dom.window.document;

for (const id of ['login','access','unlock','app','health','pause','resume','takeover',
                  'sent','latency','checked','attention','messages','events','timings',
                  'refresh','export']) {
  assert(d.getElementById(id), `missing #${id}`);
}

assert.match(html,/leadengine-owner-dashboard/);
assert.match(html,/connect-src https:\/\/jequjltbvofmhfnbsfzt\.supabase\.co/);
assert.match(html,/for\(const action of \['pause','resume','takeover'\]\)/);
assert.match(html,/setInterval\(load,15000\)/);
assert.doesNotMatch(html,/leadengine-sdr-demo-v2/);
assert.doesNotMatch(html,/data-continue/);
assert.doesNotMatch(html,/pauseBtn|resetBtn/);

dom.window.close();
console.log('PASS: live dashboard contract, auth UI, controls, live API, polling, no legacy demo state');
