import {readLocal, writeLocal, splitPane, composer, readability, scrollSnapshot, restoreScroll} from './workspace.mjs?v=feedback-a1';
(()=>{
'use strict';
const cfg=window.wikiChatConfig, $=id=>document.getElementById(id);
if(!cfg)return;
const key='wiki-chat-'+cfg.version;
let conversation=readLocal(key,null)||(()=>{try{return localStorage.getItem(key);}catch{return null;}})()||crypto.randomUUID().replaceAll('-','');
writeLocal(key,conversation);
let context={page:cfg.page}, contextLabel=cfg.page?'当前 Wiki 页面':'当前仓库与架构';
let stream=null, activeJob=null, generation=0, selection='', recovery=null, submitting=false, restoring=false;
const errors={provider_http_400:'提供商拒绝参数，请检查当前模型设置',provider_http_401:'API 密钥无效',provider_http_403:'提供商拒绝访问',provider_http_404:'模型或接口不可用',provider_http_429:'提供商速率或额度限制',model_finish_max_tokens:'达到输出上限，回答未完成',model_finish_length:'达到输出上限，回答未完成',model_finish_safety:'提供商安全策略终止输出',model_finish_missing:'提供商未返回正常结束标记',model_invalid_response:'提供商响应格式无效',model_incomplete_output:'模型输出不完整',thinking_level_not_supported:'当前模型不支持此推理设置，请选择模型默认',ReadTimeout:'模型响应超时',TimeoutError:'本轮处理超时',ConnectError:'无法连接提供商'};
const explain=e=>errors[e]||e;
window.addEventListener('llm-settings-saved',()=>restore());
const status=t=>$('chat-status').textContent=t;
function publishContext(){if(window.platformAssistant)window.platformAssistant.context({provider:'github-wiki',id:cfg.version,...context});}
window.addEventListener('platform-assistant-ready',publishContext);
function contextUI(){$('chat-context').textContent=contextLabel;}
contextUI();
function setOpen(open){$('wiki-chat').hidden=!open;document.body.classList.toggle('chat-open',open);$('chat-toggle').setAttribute('aria-expanded',String(open));writeLocal('wiki-chat-open',open);window.dispatchEvent(new Event('resize'));if(open)$('chat-question').focus();}
$('chat-toggle').onclick=()=>setOpen(true);$('chat-close').onclick=()=>setOpen(false);
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('wiki-chat').hidden){setOpen(false);$('chat-toggle').focus();}});
if(readLocal('wiki-chat-open',false))setOpen(true);
function busy(job){activeJob=job;$('chat-send').disabled=!!job||submitting||restoring||!cfg.enabled;$('chat-stop').hidden=!job;}
function append(role,text,html=false){
 const article=document.createElement('article');article.className='chat-message '+role;
 const label=document.createElement('div');label.className='chat-author';label.textContent=role==='user'?'你':'阅读伙伴';
 const body=document.createElement('div');body.className='chat-body';
 if(html){body.innerHTML=text;body.querySelectorAll('a').forEach(a=>{a.target='_blank';a.rel='noopener noreferrer';});}
 else body.textContent=text;
 if(role==='assistant'){const copy=document.createElement('button');copy.type='button';copy.className='chat-copy';copy.textContent='复制回答';copy.onclick=async()=>{try{await navigator.clipboard.writeText(body.innerText);status('回答已复制');}catch{status('复制失败，请选择回答文字后复制。');}};article.append(copy);}
 article.append(label,body);$('chat-messages').append(article);
}
function empty(){const box=document.createElement('div');box.className='chat-empty';const heading=document.createElement('strong');heading.textContent='边读，边把问题聊明白';const text=document.createElement('p');text.textContent='可以解释当前页面，讨论模块为什么这样划分，也可以自由比较和提出新方案。选择图节点或正文后，带着上下文继续聊。';box.append(heading,text);$('chat-messages').append(box);}
async function get(url){const r=await fetch(url,{signal:AbortSignal.timeout(10000)});if(!r.ok)throw Error('读取讨论失败（'+r.status+'）');return r.json();}
async function list(){
 const thread=conversation,epoch=generation;
 const data=await get('/api/versions/'+cfg.version+'/conversations');
 if(thread!==conversation||epoch!==generation)return;
 const select=$('chat-conversations');select.replaceChildren();
 const drafts=readLocal('wiki-draft-index-'+cfg.version,{});
 for(const [id,title] of Object.entries(drafts)){if(!data.items.some(x=>x.id===id))data.items.unshift({id,title:'草稿 · '+title});}
 if(!data.items.some(x=>x.id===conversation))data.items.unshift({id:conversation,title:'新讨论'});
 for(const item of data.items){const option=document.createElement('option');option.value=item.id;option.textContent=item.title.slice(0,35);select.append(option);}
 select.value=conversation;
}

function watch(job,epoch){
 if(stream)stream.close();clearTimeout(recovery);
 stream=new EventSource('/api/chat/jobs/'+job+'/events');
 const connection=stream;
 connection.onmessage=async event=>{
  if(epoch!==generation){connection.close();return;}
  const data=JSON.parse(event.data);
  if(['running','queued'].includes(data.state)){status(data.state==='queued'?'已排队，等待处理…':'正在阅读相关源码并组织回答…');return;}
  connection.close();if(stream===connection)stream=null;busy(null);
  await restore();
 };
 connection.onerror=()=>{if(epoch!==generation)return;status('连接中断，正在查询已提交任务…');connection.close();if(stream===connection)stream=null;clearTimeout(recovery);recovery=setTimeout(()=>{if(epoch===generation)restore();},2500);};
}
let displayedConversation=null;
async function restore(){
 const snapshot=scrollSnapshot($('chat-messages')); if(displayedConversation!==conversation)snapshot.following=true;
 window.dispatchEvent(new CustomEvent('llm-session',{detail:conversation}));
 restoring=true;clearTimeout(recovery);const epoch=++generation;if(stream)stream.close();stream=null;busy(null);
 try{
  const data=await get('/api/versions/'+cfg.version+'/conversations/'+conversation);
  if(epoch!==generation)return;
  if(typeof data.model_enabled==='boolean')cfg.enabled=data.model_enabled;restoring=false;busy(null);
  $('chat-messages').replaceChildren();
  const byId=new Map(data.items.map(x=>[x.source_interaction_id,x]));
  for(const job of data.jobs){
   append('user',job.args.question);
   const item=byId.get(job.result?.interaction_id);
   if(item)append('assistant',item.html,true);
   else if(!['queued','running'].includes(job.state))append('assistant',({cancelled:'已停止回答。',interrupted:'服务重启，回答中断。',failed:'回答未完成：'+explain(job.error||'请求失败')})[job.state]||'记录不可用');
  }
  if(!data.jobs.length)empty();
  const pending=data.jobs.find(j=>['queued','running'].includes(j.state));
  if(pending){busy(pending.id);watch(pending.id,epoch);}
  status(pending?'正在恢复未完成的讨论…':cfg.enabled?data.history_policy:'模型不可用，请展开模型设置查看原因');
  restoreScroll($('chat-messages'),snapshot,true,$('chat-latest'));displayedConversation=conversation;await list();
 }catch(e){if(epoch!==generation)return;restoring=true;status(e.message+'；正在恢复讨论状态，草稿保留');$('chat-send').disabled=true;recovery=setTimeout(()=>{if(epoch===generation)restore();},5000);}
}
$('chat-new').onclick=()=>{if(!saveDraft())return;conversation=crypto.randomUUID().replaceAll('-','');writeLocal(key,conversation);restoreDraft();restore();};
window.addEventListener('wiki-chat-new',()=>{$('chat-new').click();setOpen(true);});
$('chat-conversations').onchange=e=>{if(!saveDraft()){e.target.value=conversation;return;}conversation=e.target.value;writeLocal(key,conversation);restoreDraft();restore();};
$('chat-clear-context').onclick=()=>{context={};contextLabel='整个仓库 · 不带页面或选中对象';contextUI();saveDraft();};
document.addEventListener('selectionchange',()=>{
 const selected=window.getSelection();if(selected&&!$('wiki-chat').contains(selected.anchorNode)&&selected.toString().trim())selection=selected.toString().trim().slice(0,3000);
});
$('chat-selection').onclick=()=>{if(window.platformAssistant){window.platformAssistant.draft('',selection||'');return;}if(!selection){status('先在页面中选中需要讨论的文字。');return;}context.selection=selection;contextLabel='选中文字：'+selection;contextUI();saveDraft();};
window.addEventListener('wiki-chat-context',event=>{
 if(window.platformAssistant){context={page:cfg.page,...event.detail.context};publishContext();setOpen(true);window.platformAssistant.draft(event.detail.question||event.detail.label||'请解释这个对象',JSON.stringify(event.detail.context||{}));return;}
 context={page:cfg.page,...event.detail.context};contextLabel=event.detail.label||'选中的图对象';contextUI();
 if(event.detail.question)$('chat-question').value=[$('chat-question').value,event.detail.question].filter(Boolean).join('\n\n').slice(0,2000);saveDraft();resizeComposer();
 if(event.detail.open)setOpen(true);
});
$('chat-form').onsubmit=async e=>{
 e.preventDefault();if(activeJob||submitting||restoring){status('已有任务正在处理，请等待或停止后发送');return;}
 const question=$('chat-question').value.trim();if(!question)return;if(!cfg.enabled){status('模型不可用，请先在模型设置中配置。');return;}
 const epoch=generation, thread=conversation, sentText=$('chat-question').value;saveDraft();
 const body={question,conversation:thread,...context}, slot='wiki-pending-'+cfg.version+'-'+thread;
 const previous=readLocal(slot,null), encoded=JSON.stringify(body);
 const requestKey=previous?.body===encoded?previous.key:crypto.randomUUID();
 if(!writeLocal(slot,{body:encoded,key:requestKey})){status('无法保存发送标识，草稿保留，请恢复浏览器存储后发送。');return;}
 submitting=true;
 busy('sending');status('提交中…');
 try{
  const response=await fetch('/api/versions/'+cfg.version+'/chat',{method:'POST',signal:AbortSignal.timeout(15000),headers:{'Content-Type':'application/json','X-Wiki-Token':token},body:JSON.stringify({...body,request_key:requestKey})});
  const job=await response.json();if(!response.ok)throw Error(typeof job.detail==='string'?job.detail:'请求参数无效');
  writeLocal(slot,null);
  const saved=readLocal(draftKey(thread),{});if(saved.text===sentText){saved.text='';writeLocal(draftKey(thread),saved);}
  if(epoch!==generation)return;
  if($('chat-question').value===sentText){$('chat-question').value='';saveDraft();resizeComposer();}await restore();
 }catch(error){if(epoch===generation){busy(null);status('未能确认提交：'+explain(error.message)+'；草稿保留，再次发送将复用同一请求标识。');}}finally{submitting=false;busy(activeJob);}
};
$('chat-stop').onclick=async()=>{if(activeJob&&activeJob!=='sending'){try{await api('/api/jobs/'+activeJob+'/cancel');status('正在停止…');}catch(e){status(e.message);}}};

const draftKey=id=>'wiki-draft-v1-'+cfg.version+'-'+id;
let draftFailed=false;
function saveDraft(){
 const text=$('chat-question').value, index=readLocal('wiki-draft-index-'+cfg.version,{});
 if(text)index[conversation]=text.slice(0,30);else delete index[conversation];
 const saved=writeLocal(draftKey(conversation),{text,context,label:contextLabel});
 const ok=writeLocal('wiki-draft-index-'+cfg.version,index)&&saved;draftFailed=!ok&&!!(text||context.selection);
 if(draftFailed)status('草稿无法保存到浏览器，请先复制内容再切换。');return !draftFailed;
}
function restoreDraft(){const value=readLocal(draftKey(conversation),{});$('chat-question').value=value.text||'';context=value.context||{page:cfg.page};contextLabel=value.label||(cfg.page?'当前 Wiki 页面':'当前仓库与架构');contextUI();resizeComposer();}
const resizeComposer=composer($('chat-question'),$('chat-form'),$('chat-expand'));
$('chat-question').addEventListener('input',saveDraft);
$('chat-discard').onclick=()=>{$('chat-question').value='';saveDraft();resizeComposer();};
window.addEventListener('pagehide',saveDraft);window.addEventListener('beforeunload',e=>{if(draftFailed){e.preventDefault();e.returnValue='';}});
const split=splitPane({handle:$('chat-splitter'),panel:$('wiki-chat'),key:'wiki-chat-width',available:()=>innerWidth-10,apply:width=>document.documentElement.style.setProperty('--chat-width',width+'px')});
$('chat-reset').onclick=()=>{split.reset();focusMode(false);};
function focusMode(value){document.body.classList.toggle('chat-focus',value);$('chat-focus').textContent=value?'并排阅读':'专注讨论';$('chat-focus').setAttribute('aria-pressed',String(value));writeLocal('wiki-chat-focus',value);window.dispatchEvent(new Event('resize'));}
$('chat-focus').onclick=()=>focusMode(!document.body.classList.contains('chat-focus'));
focusMode(readLocal('wiki-chat-focus',false));readability($('chat-reading'),'wiki-reading-type');
$('chat-latest').onclick=()=>{$('chat-messages').scrollTop=$('chat-messages').scrollHeight;$('chat-latest').hidden=true;};
$('chat-messages').addEventListener('scroll',()=>{if(scrollSnapshot($('chat-messages')).following)$('chat-latest').hidden=true;});
restoreDraft();
publishContext();
restore();
})();

window.addEventListener('platform-assistant-action',event=>{if(event.detail.type==='locate'){const url=event.detail.evidence.url;if(url?.startsWith(location.origin+'/'))window.open(url,'_blank','noopener,noreferrer');}});

window.addEventListener('wiki-chat-new',()=>{if(window.platformAssistant){setOpen(true);window.platformAssistant.newConversation();}});
