import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createAuthenticatedFetch} from '../aos/host/static/assistant-api.mjs';
const rejected=()=>new Response(JSON.stringify({detail:'write_token_required'}),{status:403});
test('stale token retries same body once; concurrent requests share refresh',async()=>{
 let token='old', refreshes=0;const accepted=[];
 const send=createAuthenticatedFetch({getToken:()=>token,refreshToken:async()=>{refreshes++;await new Promise(r=>setTimeout(r,5));token='new';},fetcher:async(u,o)=>{
 if(o.headers['X-AOS-Token']==='old')return rejected();accepted.push(o.body);return new Response('{}');}});
 await Promise.all([send('/turns',{method:'POST',body:'{"request_key":"same"}'}),send('/events')]);
 assert.equal(refreshes,1);assert.deepEqual(accepted,['{"request_key":"same"}',undefined]);
});
test('persistent rejection is bounded and network failures are not replayed',async()=>{
 let calls=0,refreshes=0;
 const send=createAuthenticatedFetch({getToken:()=>'',refreshToken:async()=>{refreshes++;},fetcher:async()=>{calls++;return rejected();}});
 assert.equal((await send('/turns')).status,403);assert.equal(calls,2);assert.equal(refreshes,1);
 calls=0;const network=createAuthenticatedFetch({getToken:()=>'',refreshToken:async()=>{throw Error('must not refresh');},fetcher:async()=>{calls++;throw Error('network');}});
 await assert.rejects(network('/turns'),/network/);assert.equal(calls,1);
});
test('other 403 and cancellation never replay',async()=>{
 let calls=0;const controller=new AbortController();
 const send=createAuthenticatedFetch({getToken:()=>'',refreshToken:async()=>{controller.abort();},fetcher:async()=>{calls++;return rejected();}});
 await assert.rejects(send('/events',{signal:controller.signal}),{name:'AbortError'});assert.equal(calls,1);
 const denied=createAuthenticatedFetch({getToken:()=>'',refreshToken:async()=>{throw Error('must not refresh');},fetcher:async()=>new Response('{"detail":"origin_rejected"}',{status:403})});
 assert.equal((await denied('/turns')).status,403);
});
