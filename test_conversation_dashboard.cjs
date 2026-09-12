// DOM interaction tests. These do not substitute for a full-browser visual check.
const {JSDOM}=require('jsdom');
const fs=require('node:fs');
const assert=require('node:assert/strict');
const html=fs.readFileSync('dashboard.html','utf8');
function load(saved){
 const pending=new Map();let sequence=0;
 const dom=new JSDOM(html,{url:'https://example.test/dashboard/',runScripts:'dangerously',beforeParse(w){
  w.setTimeout=fn=>{pending.set(++sequence,fn);return sequence};
  w.clearTimeout=id=>pending.delete(id);w.confirm=()=>true;
  if(saved)w.localStorage.setItem('leadengine-sdr-demo-v2',saved);
 }});
 const w=dom.window;
 return {w,dom,read:code=>w.eval(code),click:selector=>{const b=w.document.querySelector(selector);assert(b,selector);b.click()},tick(){const entry=pending.entries().next().value;if(entry){pending.delete(entry[0]);entry[1]()}}};
}
let t=load();
t.click('button[type="submit"]');for(let i=0;i<6;i++)t.tick();
assert.equal(t.read('state.stage'),6);
assert.equal(t.w.document.querySelectorAll('[data-continue]').length,2);
t.click('[data-continue="0"]');t.tick();
assert.match(t.w.document.querySelector('.opportunity').textContent,/USD 350/);
t.click('#pauseBtn');const paused=t.read('state.continuing[0].index');t.tick();
assert.equal(t.read('state.continuing[0].index'),paused);
t.click('#pauseBtn');t.tick();
t.click('[data-take="0"]');const taken=t.read('state.continuing[0].index');t.tick();
assert.equal(t.read('state.continuing[0].index'),taken);
t.click('[data-continue="0"]');
const saved=t.w.localStorage.getItem('leadengine-sdr-demo-v2');t.dom.window.close();t=load(saved);
assert.equal(t.read('state.followPaused'),true);t.tick();
assert.equal(t.read('state.continuing[0].index'),taken);
t.click('#pauseBtn');for(let i=0;i<7;i++)t.tick();
assert.match(t.w.document.querySelector('.opportunity').textContent,/No payment or booking confirmed/);
assert.equal(t.read('state.continuing[0].index'),6);
t.click('[data-continue="1"]');for(let i=0;i<4;i++)t.tick();
assert.match(t.w.document.querySelectorAll('.opportunity')[1].textContent,/No reminder has been scheduled/);
assert.equal(t.w.document.querySelector('#ready').textContent,'1');
t.click('#resetBtn');assert.equal(t.read('state.status'),'idle');
assert.equal(t.w.document.querySelector('#setup').hidden,false);
assert.equal(t.w.localStorage.getItem('leadengine-sdr-demo-v2'),null);
t.dom.window.close();
// Older saved progress is retained; malformed new progress cannot crash the page.
t=load(JSON.stringify({version:2,campaign:{offer:'x',audience:'y',location:'z'},stage:6,status:'complete',handled:[],continuing:{0:{index:999,owner:'sdr'}}}));
assert.equal(t.read('Object.keys(state.continuing).length'),0);
assert.equal(t.w.document.querySelectorAll('[data-continue]').length,2);
t.dom.window.close();
assert.match(html,/connect-src 'none'/);
console.log('PASS: automatic flow, both conversation outcomes, pause/resume, takeover/return, saved progress, reset, older storage, offline policy');
