import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const template=fs.readFileSync('wiki_app/templates/base.html','utf8');
const code=template.slice(template.indexOf('async function api('),template.indexOf('\nfunction showError'));
const response=(status,text)=>new Response(text,{status});
function setup(replies){
 const calls=[];
 const ctx=vm.createContext({Response,DOMParser:class{parseFromString(){return {querySelector:()=>({content:'new-token'})}}},fetch:async(path,options)=>{calls.push({path,options});const reply=replies.shift();if(reply instanceof Error)throw reply;return reply;}});
 vm.runInContext("let token='old-token';"+code,ctx);
 return {calls,run:()=>vm.runInContext("api('/api/repos',{url:'https://github.com/huggingface/transformers'})",ctx)};
}
test('expired page refreshes once then preserves request body and uses current token',async()=>{
 const t=setup([response(403,'write_token_required'),response(200,'page'),response(201,'{"repo_id":"ok"}')]);
 assert.equal((await t.run()).repo_id,'ok');assert.equal(t.calls.length,3);
 assert.equal(t.calls[1].path,'/');assert.equal(t.calls[1].options.cache,'no-store');
 assert.equal(t.calls[2].options.headers['X-Wiki-Token'],'new-token');
 assert.equal(t.calls[0].options.body,t.calls[2].options.body);
});
test('repeated auth rejection stops after one retry',async()=>{
 const t=setup([response(403,'write_token_required'),response(200,'page'),response(403,'write_token_required')]);
 await assert.rejects(t.run(),/页面授权已过期/);assert.equal(t.calls.length,3);
});
test('origin rejection, provider failure and unknown network outcome are not replayed',async()=>{
 for(const r of [response(403,'origin_rejected'),response(503,'unavailable'),Error('network')]){
  const t=setup([r]);await assert.rejects(t.run());assert.equal(t.calls.length,1);
 }
});
test('refresh failure does not resend the write',async()=>{
 const t=setup([response(403,'write_token_required'),response(503,'unavailable')]);
 await assert.rejects(t.run(),/授权更新失败/);assert.equal(t.calls.length,2);
});
