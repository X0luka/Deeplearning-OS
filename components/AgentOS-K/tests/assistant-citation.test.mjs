import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {chromium,expect} from '@playwright/test';
test('citation URL preserves document anchor; desktop and embedded transport; browser fallback',async()=>{
 const source=await readFile('aos/host/static/assistant.js','utf8');const fn=source.slice(source.indexOf('function openDesktopCitation('),source.indexOf('function answerView('));
 const sent=[];let saved=0;const context={URL,location:{origin:'http://host.test'},config:{parent:''},window:{chrome:{webview:{postMessage:v=>sent.push(JSON.parse(v))}}},saveDraft:()=>saved++,bridge:(type,data)=>sent.push({type,...data})};vm.createContext(context);vm.runInContext(fn,context);
 const evidence={url:'http://reader.test/?document=doc',location:{document:'doc',page:3}};
 assert.equal(context.openDesktopCitation(evidence),true);assert.equal(sent[0].type,'open-citation');assert.match(sent[0].url,/assistant-anchor=/);assert.equal(saved,1);
 context.config.parent='http://reader.test';assert.equal(context.openDesktopCitation(evidence),true);assert.equal(sent[1].type,'open-citation');
 context.config.parent='';context.window={};assert.equal(context.openDesktopCitation(evidence),false);assert.equal(context.openDesktopCitation({url:'javascript:alert(1)'}),false);
 assert.match(source,/if\(evidence&&desktopCitations&&openDesktopCitation\(evidence\)\)/);
});
test('embedded bridge validates frame and origin before forwarding desktop citation',async()=>{
 const js=await readFile('aos/host/static/assistant-bridge.js','utf8');const browser=await chromium.launch();
 try{const page=await browser.newPage();await page.route('**/*',r=>{
  const u=new URL(r.request().url());
  if(u.pathname==='/bridge.js')return r.fulfill({contentType:'text/javascript',body:js});
  if(u.hostname==='assistant.test')return r.fulfill({contentType:'text/html',body:`<script>parent.postMessage({channel:'aos-assistant-v1',type:'ready'},'http://reader.test');addEventListener('message',e=>{if(e.data.type==='desktop-capabilities')window.citations=e.data.citations})</script>`});
  return r.fulfill({contentType:'text/html',body:`<body><div id="root"></div><script>window.__aosDesktopShell=true;window.receipts=[];window.chrome={webview:{postMessage:v=>receipts.push(JSON.parse(v))}}</script><script src="/bridge.js" data-host="http://assistant.test" data-provider="paper" data-container="#root"></script></body>`});
 });await page.goto('http://reader.test');const frame=page.frames().find(f=>f.url().startsWith('http://assistant.test'));assert.ok(frame);
 await expect.poll(()=>frame.evaluate(()=>window.citations)).toBe(true);
 await frame.evaluate(()=>parent.postMessage({channel:'aos-assistant-v1',type:'open-citation',url:'http://reader.test/?document=other'},'http://reader.test'));
 await expect.poll(()=>page.evaluate(()=>receipts.length)).toBe(1);
 await page.evaluate(()=>window.postMessage({channel:'aos-assistant-v1',type:'open-citation',url:'http://reader.test/spoof'},location.origin));
 await frame.evaluate(()=>parent.postMessage({channel:'aos-assistant-v1',type:'open-citation',url:'javascript:alert(1)'},'http://reader.test'));
 await page.waitForTimeout(100);assert.equal(await page.evaluate(()=>receipts.length),1);
 }finally{await browser.close();}
});
