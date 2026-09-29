(()=>{
'use strict';
const form=document.getElementById('network-form'), fields=document.getElementById('network-fields');
const container=document.getElementById('network-channels'), status=document.getElementById('network-status');
const token=document.querySelector('meta[name="aos-appearance"]').dataset.token;
const names={default:'应用默认',models:'模型 API',paper:'论文下载',wiki:'GitHub 获取'};
const controls={};
for(const [channel,name] of Object.entries(names)){
 const group=document.createElement('div');group.className='llm-item';
 const label=document.createElement('label');label.textContent=name;
 const mode=document.createElement('select');mode.id='network-'+channel;
 const choices=channel==='default'?[['direct','不使用应用代理'],['proxy','显式 HTTP(S) 代理']]:[['inherit','继承应用默认'],['direct','不使用应用代理'],['proxy','单独设置 HTTP(S) 代理']];
 for(const [value,text] of choices){const option=document.createElement('option');option.value=value;option.textContent=text;mode.append(option);}
 label.append(mode);
 const proxyLabel=document.createElement('label');proxyLabel.textContent=name+'代理地址';
 const proxy=document.createElement('input');proxy.type='url';proxy.placeholder='例如 http://127.0.0.1:7897';proxy.autocomplete='off';proxyLabel.append(proxy);
 const effective=document.createElement('p');effective.className='muted';
 const refresh=()=>{proxyLabel.hidden=mode.value!=='proxy';proxy.disabled=mode.value!=='proxy';proxy.required=mode.value==='proxy';};
 mode.onchange=()=>{refresh();status.textContent='有未保存的修改；下方显示的仍是已保存的路径。';};
 proxy.oninput=mode.onchange;
 group.append(label,proxyLabel,effective);container.append(group);
 controls[channel]={mode,proxy,effective,refresh};
}
async function request(method,value){
 const response=await fetch('/api/v1/llm/network',{method,headers:{'Content-Type':'application/json','X-Aos-Token':token},body:value===undefined?undefined:JSON.stringify(value)});
 const data=await response.json();if(!response.ok)throw Error(data.detail||'network_settings_unavailable');return data;
}
function display(data){
 for(const [channel,c] of Object.entries(controls)){
  c.mode.value=data.settings[channel].mode;c.proxy.value=data.settings[channel].proxy;c.refresh();
  const v=data.effective[channel];
  c.effective.textContent=v?'已保存的路径：'+(v.proxy||'不使用应用代理')+'（'+names[v.source]+'）':'';
 }
 fields.disabled=false;
}
function failure(error){status.textContent=error.message==='invalid_proxy_address'?'未保存：请输入有效的 HTTP(S) 代理地址，不含账号、密码或额外路径。':'网络设置未完成，请检查输入或工作台连接。';}
form.onsubmit=async event=>{
 event.preventDefault();fields.disabled=true;
 try{const value={};for(const [name,c] of Object.entries(controls))value[name]={mode:c.mode.value,proxy:c.mode.value==='proxy'?c.proxy.value.trim():''};
 display(await request('PUT',value));status.textContent='网络设置已保存。尚未测试连通性；后续请求使用新配置。';
 }catch(error){fields.disabled=false;failure(error);}
};
request('GET').then(data=>{display(data);status.textContent='已读取网络设置；不会自动更改你的系统网络。';}).catch(failure);
})();
