const fs=require('fs');const {chromium}=require('playwright');
(async()=>{const browser=await chromium.launch({headless:true});const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
const now=new Date().toISOString();let fixture={now,conversation:{enabled:true,owner:'sdr',status:'active',version:1,replies_sent:1,reply_cap:5,expires_at:new Date(Date.now()+86400000).toISOString(),state:{history:[{role:'prospect',text:'What do you need from us?'},{role:'sdr_draft',text:'Your logo and business details.'}]},last_decision:{}},events:[],checks:[{at:now}],settings:{sending_enabled:false}};
await page.route('**/functions/v1/leadengine-owner-dashboard',async route=>{if(route.request().method()==='POST'){const d=route.request().postDataJSON();fixture.conversation.enabled=d.action==='resume';fixture.conversation.owner=d.action==='resume'?'sdr':'human';fixture.conversation.version++;await route.fulfill({json:{ok:true}})}else await route.fulfill({json:fixture})});
await page.route('https://hayaautomation2026-del.github.io/leadengine/dashboard/',route=>route.fulfill({contentType:'text/html',body:fs.readFileSync('dashboard.html','utf8')}));
await page.goto('https://hayaautomation2026-del.github.io/leadengine/dashboard/#access=test-access');await page.waitForSelector('#app:not(.hidden)');
if(await page.locator('#sent').innerText()!=='1')throw Error('Live count mismatch');
await page.click('#pause');await page.waitForFunction(()=>document.querySelector('#health').textContent.includes('Paused'));
await page.click('#resume');await page.waitForFunction(()=>document.querySelector('#health').textContent.includes('watching'));
for(const width of [390,1440]){await page.setViewportSize({width,height:900});if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1))throw Error('Horizontal overflow '+width);await page.screenshot({path:'dashboard-'+width+'.png',fullPage:true})}
if(errors.length)throw Error(errors.join('\n'));console.log('DASHBOARD_QA_PASS desktop mobile controls no_script_errors');await browser.close()})().catch(e=>{console.error(e);process.exit(1)});
