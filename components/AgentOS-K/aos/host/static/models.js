(()=>{'use strict';
const $=id=>document.getElementById(id), token=document.querySelector('meta[name="aos-appearance"]').dataset.token;
const errors={key_required:'请输入 API Key。',catalog_http_401:'API Key 无效或没有访问权限，请检查后重新保存。',catalog_http_403:'服务商拒绝访问，请检查账户权限。',catalog_http_404:'此地址未提供模型目录，请检查 Base URL，或手动填写模型 ID。',catalog_unavailable:'无法连接模型目录，请检查地址、网络或代理。',model_output_limit_exceeded:'输出上限超过所选模型的官方限制。',unsupported_tools:'所选模型已标记不支持工具调用；设置未被自动关闭，请换用兼容模型或核对能力标记。',unsupported_thinking:'所选模型已标记不支持思考设置；设置未被自动关闭，请核对模型能力。',unsupported_pdf_input:'所选模型未确认支持原始 PDF，请使用提取文本或选择支持的模型。',unsupported_web_search:'所选模型未确认支持原生联网。',platform_configuration_unavailable:'AgentOS 配置暂不可用，请检查工作台是否运行。',model_not_configured:'请先在 AgentOS 选择模型。',connection_disabled:'此 API 连接已停用。',model_key_not_configured:'此 API 连接尚未配置密钥。',key_required_for_new_endpoint:'更换 API 地址时，请重新填写对应服务的密钥。'};const explain=x=>errors[x]||x;
Object.assign(errors,{connection_in_use:'此连接正在被默认模型或产品设置使用。请先在“默认模型与回答设置”中更换或解除绑定，再删除。',catalog_timeout:'连接服务商超时；当前网络可能需要在“高级连接设置”填写代理。',catalog_proxy_error:'代理连接失败，请确认代理程序已运行且地址正确。',catalog_connect_error:'无法建立连接，请检查 API 地址、网络与代理。'});
let state,models=[];
const msg=t=>{$('model-notice').textContent=t;};
async function api(path,method='GET',value){const r=await fetch('/api/v1/llm'+path,{method,headers:{'Content-Type':'application/json','X-Aos-Token':token},body:value===undefined?undefined:JSON.stringify(value)});const data=await r.json();if(!r.ok)throw Error(data.detail||'操作失败');return data;}
const safe=f=>async e=>{try{await f(e);}catch(e){msg('未完成：'+explain(e.message));}};
async function connectionAction(button,path,method,progress,success){
const notice=$('connection-notice');button.disabled=true;notice.textContent=progress;
try{await api(path,method,method==='POST'?{}:undefined);notice.textContent=success;
try{await load();}catch(e){notice.textContent=success+' 列表刷新失败，请刷新页面查看。';}
}catch(e){notice.textContent='操作未完成：'+explain(e.message);}finally{button.disabled=false;}
}
const el=(tag,text)=>{const n=document.createElement(tag);n.textContent=text;return n;};
function option(s,v,t){const o=el('option',t);o.value=v;s.append(o);}
function options(){const select=$('selection'),old=select.value,q=$('model-search').value.toLowerCase();select.replaceChildren();option(select,'', $('scope').value==='global'?'请选择默认模型':'继承上级模型');for(const m of models){if(m.label.toLowerCase().includes(q)||m.value===old)option(select,m.value,m.label);}select.value=old;}

function controls(){
 const id=$('scope').value,parts=id.split(':'),effective={...state.defaults};
 if(parts.length>1)Object.assign(effective,state.overrides[parts[0]]||{});
 let selected=effective.selection;if($('selection').value)selected=JSON.parse($('selection').value);
 const c=state.connections.find(c=>c.id===selected?.connection),cap=c?.models.find(m=>m.id===selected?.model)?.capabilities||{};
 const tool=$('tools').value===''?effective.tools:$('tools').value==='true';
 const think=$('thinking').value||effective.thinking;
 const names={default:'模型默认',off:'关闭',auto:'开启'};
 let text='本范围设置：工具调用 '+(tool?'开启':'关闭')+' · 思考 '+names[think]+'。';
 if((tool&&cap.tools==null)||(think!=='default'&&cap.thinking==null))text+=' 目录未声明能力，将按用户设置请求；失败会明确报告，不自动关闭。';
 if((tool&&cap.tools===false)||(think!=='default'&&cap.thinking===false))text+=' 所选模型标记不支持该能力，请选择兼容模型；不会自动改动开关。';
 text+=' 产品或会话中已保存的覆盖仍优先；保存后下次提问生效。';
 $('effective-controls').textContent=text;
}
function limits(){
const scopeId=$('scope').value;let selected=state.defaults.selection;
if(scopeId.includes(':'))selected=state.overrides[scopeId.split(':')[0]]?.selection||selected;
if($('selection').value)selected=JSON.parse($('selection').value);
const c=state.connections.find(c=>c.id===selected?.connection),m=c?.models.find(m=>m.id===selected?.model),v=m?.limits||{};
const format=n=>n?Number(n).toLocaleString()+' tokens':'未公布';
$('model-limits').replaceChildren(el('span',(v.context_tokens?'上下文 ':'输入上限 ')+format(v.context_tokens||v.max_input_tokens)+' · 最大输出 '+format(v.max_output_tokens)+(v.serving_model?' · 实际服务 '+v.serving_model:'')));
if(v.source?.startsWith('https://')){const a=el('a',' 官方规格');a.href=v.source;a.target='_blank';a.rel='noopener';a.title='核实于 '+v.verified_at;$('model-limits').append(a);}
const note=$('provider-api-note');note.replaceChildren();note.hidden=!(c?.protocol==='chat'&&v.source==='https://api-docs.deepseek.com/quick_start/pricing/'&&v.serving_model);
if(!note.hidden){
 note.append(el('span','DeepSeek 官方说明：输入与输出合计受上下文长度限制；tokens 不等于字数。上方显示的是服务商最大值，下方“最大输出”是你可调整的单次设置，达到上限可能截断回答。思考模式的额度还需容纳思考过程，设置过小可能在正文完成前耗尽；提高上限不要求模型用满，也不会关闭思考。并发额度按账户共享，超限可能返回 429。'));
 for(const [label,url] of [[' 参数说明','https://api-docs.deepseek.com/api/create-chat-completion/'],[' 思考说明','https://api-docs.deepseek.com/guides/thinking_mode/'],[' 并发说明','https://api-docs.deepseek.com/quick_start/rate_limit/']]){const a=el('a',label);a.href=url;a.target='_blank';a.rel='noopener';note.append(a);}
 note.append(el('span',' · 核对于 2026-09-29，以官方最新文档为准。'));
}
if(v.max_output_tokens)$('output').max=v.max_output_tokens;else $('output').removeAttribute('max');controls();
}
function scope(){const id=$('scope').value,v=id==='global'?state.defaults:(state.overrides[id]||{});$('custom-label').hidden=id!=='global';$('inherit').hidden=id==='global';$('custom').value=state.defaults.custom;options();$('selection').value=v.selection?JSON.stringify(v.selection):'';$('output').value=v.max_output_tokens??'';$('thinking').value=v.thinking??'';$('temperature').value=v.temperature??'';for(const k of ['tools','web_search'])$(k).value=v[k]===undefined?'':String(v[k]);$('document_input').value=v.document_input??'';$('scope-status').textContent=id==='global'?'新任务使用更新后的设置；进行中的任务保持原配置。':Object.keys(v).length?'已设置部分覆盖；留空项继承上级。':'当前全部继承上级设置。';limits();}
async function load(){const previous=$('scope').value;state=await api('');$('connections').replaceChildren();$('catalog').replaceChildren();$('manual-connection').replaceChildren();models=[];
$('connection-empty').hidden=state.connections.length>0;if(!state.connections.length)$('connection-editor').open=true;$('connection-form').elements.key.required=!$('connection-form').elements.id.value;
for(const c of state.connections){option($('manual-connection'),c.id,c.name);const box=el('div','');box.className='llm-item';box.append(el('strong',c.name+' · '+(c.enabled?'启用':'停用')),el('div',c.base_url),el('div','模型代理：'+(c.proxy||'继承“网络设置”中的模型 API 路径')),el('div',(c.configured?'密钥已配置':'未配置密钥')+' · '+c.models.length+' 个模型 · '+(c.error||c.refreshed_at||'尚未刷新')));const row=el('div','');row.className='row';for(const [label,fn] of [['编辑',()=>{const f=$('connection-form');for(const k of ['id','name','protocol','base_url','proxy'])f.elements[k].value=c[k]||'';f.elements.enabled.checked=c.enabled;f.elements.key.value='';f.elements.key.required=false;$('connection-advanced').open=c.protocol!=='chat'||!!c.proxy;$('connection-editor').open=true;}],['获取模型',async()=>{msg('正在获取模型目录…');await api('/connections/'+c.id+'/refresh','POST',{});await load();msg('模型目录已更新，未调用生成接口。');}],['清除密钥',async e=>{if(confirm('清除此连接的密钥？'))await connectionAction(e.currentTarget,'/connections/'+c.id+'/clear-key','POST','正在清除密钥…','已清除“'+c.name+'”的 API 密钥。连接配置仍保留，可点击“编辑”输入新密钥。');}],['删除',async e=>{if(confirm('删除连接 '+c.name+'？已绑定连接需先更换。'))await connectionAction(e.currentTarget,'/connections/'+c.id,'DELETE','正在删除连接…','已删除 API 连接“'+c.name+'”。');}]]){const b=el('button',label);b.type='button';b.onclick=safe(fn);row.append(b);}box.append(row);$('connections').append(box);
for(const m of c.models){if(c.enabled&&m.capabilities.text!==false)models.push({value:JSON.stringify({connection:c.id,model:m.id}),label:c.name+' / '+m.id});const n=el('div',c.name+' / '+m.id+' · '+(m.availability==='verified'?'已验证':'调用未验证'));n.className='llm-item';const b=el('button','查看 / 确认能力');b.onclick=()=>{$('manual-connection').value=c.id;$('manual-id').value=m.id;for(const k in m.capabilities)$('cap-'+k).value=m.capabilities[k]===null?'':String(m.capabilities[k]);$('manual-form').parentElement.open=true;$('manual-form').scrollIntoView({block:'center'});};n.append(b);$('catalog').append(n);}}
const s=$('scope');s.replaceChildren();option(s,'global','全局默认');for(const [p,ts] of Object.entries(state.tasks)){option(s,p,state.products[p]+' · 全产品');for(const [t,label] of Object.entries(ts))option(s,p+':'+t,state.products[p]+' · '+label);}s.value=[...s.options].some(x=>x.value===previous)?previous:'global';scope();}
$('connection-form').onsubmit=safe(async e=>{e.preventDefault();const f=e.target,v=Object.fromEntries(new FormData(f));v.enabled=f.elements.enabled.checked;if(!v.id)delete v.id;await api('/connections','POST',v);f.reset();f.elements.id.value='';await load();msg('连接已保存。可点击“获取模型”。');});
$('new-connection').onclick=()=>{$('connection-form').reset();$('connection-form').elements.id.value='';$('connection-form').elements.key.required=true;};
$('settings-form').onsubmit=safe(async e=>{e.preventDefault();const global=$('scope').value==='global',v={};if($('selection').value)v.selection=JSON.parse($('selection').value);else if(global)v.selection=null;if(global)v.custom=$('custom').value;if($('output').value)v.max_output_tokens=Number($('output').value);if($('thinking').value)v.thinking=$('thinking').value;if($('temperature').value)v.temperature=Number($('temperature').value);for(const k of ['tools','web_search'])if($(k).value)v[k]=$(k).value==='true';if($('document_input').value)v.document_input=$('document_input').value;await api('/settings/'+encodeURIComponent($('scope').value),'PUT',v);await load();msg('设置已保存，将应用于下一次新任务。');});
$('inherit').onclick=safe(async()=>{await api('/settings/'+encodeURIComponent($('scope').value),'PUT',{});await load();msg('已恢复继承。');});
for(const [k,label] of Object.entries({text:'文本生成',tools:'工具调用',pdf:'原始 PDF',search:'原生搜索',thinking:'思考设置',stream:'流式输出',json:'JSON 模式',temperature:'温度'})){const l=el('label',label),s=el('select','');s.id='cap-'+k;option(s,'','未知 / 未确认');option(s,'true','支持');option(s,'false','不支持');l.append(s);$('capabilities').append(l);}
$('manual-form').onsubmit=safe(async e=>{e.preventDefault();const caps={};for(const s of $('capabilities').querySelectorAll('select'))caps[s.id.slice(4)]=s.value===''?null:s.value==='true';await api('/connections/'+$('manual-connection').value+'/models','POST',{id:$('manual-id').value,capabilities:caps});await load();msg('模型信息已保存；没有发起生成测试。');});
$('tools').onchange=controls;$('thinking').onchange=controls;$('selection').onchange=limits;$('scope').onchange=scope;$('model-search').oninput=options;safe(load)();
})();
