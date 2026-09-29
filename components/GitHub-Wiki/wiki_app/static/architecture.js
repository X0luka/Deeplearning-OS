/* Presentation consumes graph contracts; it never parses repository code. */
(() => {
const {version,repo}=window.architectureConfig, base='/api/versions/'+version+'/architecture';
let scope='', selected=null, data=null, cy=null, timer=null, requestId=0;
const el=id=>document.getElementById(id);
const txt=(tag,text)=>{const n=document.createElement(tag);n.textContent=text;return n;};
function button(text,action){const n=txt('button',text);n.type='button';n.className='secondary';n.onclick=action;return n;}
function params(){return new URLSearchParams({scope,view:el('view').value,query:el('search').value,tests:el('tests').checked,external:el('external').checked,types:el('types').checked});}
async function read(url){const r=await fetch(url);if(!r.ok)throw Error(await r.text());return r.json();}
function navigate(path){scope=path;selected=null;el('module-ask').hidden=true;load();}
function breadcrumbs(){el('breadcrumbs').replaceChildren(button('仓库根目录',()=>navigate('')));let path='';for(const part of scope.split('/').filter(Boolean)){path+=(path?'/':'')+part;const target=path;el('breadcrumbs').append(txt('span','/'),button(part,()=>navigate(target)));}}
async function choose(node){
 if(!node.path)return;
 selected=node;
 if(cy){cy.elements().removeClass('focus');cy.getElementById(node.id).closedNeighborhood().addClass('focus');}
 el('detail-title').textContent=node.path;
 el('module-ask').hidden=false;
 const local=params();local.set('node_id',node.id);
 try{
 const result=await read(base+'?'+local);
 if(selected?.id!==node.id)return;
 const detail=el('detail');detail.replaceChildren();
 if(node.kind==='module')detail.append(button('展开此模块',()=>navigate(node.path)));
 if(node.kind==='file')detail.append(link('打开本地源码',source(node.path,1,Math.min(node.lines||1,60))));
 detail.append(txt('p','关联文件 '+result.detail.files+' · 上下游关系 '+result.detail.relations.length));
 for(const declaration of result.detail.declarations)detail.append(txt('p',declaration.name+' · '+declaration.kind+' · L'+declaration.start));
 for(const edge of result.detail.relations){
 const row=txt('div','');row.className='evidence-row';
 row.append(txt('p',edge.source_label+' → '+edge.target_label+' ['+edge.kind+' / '+edge.status+']'));
 for(const proof of edge.evidence)row.append(link(proof.path+':'+proof.start+'–'+proof.end,source(proof.path,proof.start,proof.end)),txt('span',' '));
 detail.append(row);
 }
 }catch(e){showError(e);}
}
function source(path,start,end){return '/versions/'+version+'/source?'+new URLSearchParams({path,start,end})+'#L'+start;}
function link(text,url){const a=txt('a',text);a.href=url;a.target='_blank';a.rel='noopener';return a;}
function draw(){
 if(cy)cy.destroy();
 if(typeof cytoscape==='undefined'){el('graph').textContent='画布加载失败，仍可使用下方节点列表。';return;}
 cy=cytoscape({container:el('graph'),elements:[...data.nodes.map(n=>({data:n})),...data.edges.map(e=>({data:{...e,label:e.kind}}))],
 style:[{selector:'node',style:{label:'data(label)','background-color':'#216b55',color:'#183433','font-size':11,'text-valign':'bottom','text-margin-y':7,'text-wrap':'wrap','text-max-width':150,width:26,height:26}},
 {selector:'node[kind = "module"]',style:{shape:'round-rectangle',width:95,height:40,'background-color':'#b6d6c1'}},
 {selector:'node[kind = "external"], node[kind = "unresolved"]',style:{'background-color':'#d5ab62'}},
 {selector:'edge',style:{width:1.5,'line-color':'#a6bdb2','target-arrow-color':'#a6bdb2','target-arrow-shape':'triangle','curve-style':'bezier'}},
 {selector:'edge[kind = "type"], edge[kind = "dynamic"]',style:{'line-style':'dashed'}},
 {selector:'.focus',style:{'border-width':3,'border-color':'#de9a32','line-color':'#de9a32','target-arrow-color':'#de9a32'}}],
 layout:data.edges.length<data.nodes.length/2?{name:'grid',padding:35,nodeDimensionsIncludeLabels:true}:{name:'breadthfirst',directed:true,padding:35,spacingFactor:1.4}});
 cy.on('tap','node',event=>choose(event.target.data()));
 cy.on('dbltap','node',event=>{const n=event.target.data();if(n.kind==='module')navigate(n.path);});
 cy.on('tap','edge',event=>{
 el('detail-title').textContent='依赖关系证据';el('detail').replaceChildren();
 for(const proof of event.target.data('evidence'))el('detail').append(txt('p',proof.specifier),link(proof.path+':'+proof.start+'–'+proof.end,source(proof.path,proof.start,proof.end)));
 });
}
async function load(){
 const id=++requestId;clearTimeout(timer);
 try{
 const result=await read(base+'?'+params());if(id!==requestId)return;data=result;
 breadcrumbs();
 if(!data.analysis_id){
 el('notice').textContent='架构分析状态：'+data.status+(data.job?.error?' · '+data.job.error:'');
 if(data.job)el('notice').append(link(' 查看任务','/jobs/'+data.job.id));
 if(['queued','running'].includes(data.status))timer=setTimeout(load,1500);
 return;
 }
 el('notice').textContent='图绑定 commit '+data.commit.slice(0,12)+(data.job&&['failed','interrupted'].includes(data.job.state)?' · 最近更新失败，保留已有图':'');
 const c=data.coverage;
 el('coverage').textContent='快照 '+c.files+' 文件 · 解析 '+c.parsed+' · 不支持 '+c.unsupported+' · 排除 '+c.excluded+' · 未解析关系 '+c.unresolved+' · 当前 '+data.counts.nodes+' 节点/'+data.counts.edges+' 关系 · 隐藏或范围外 '+data.counts.hidden_files+' 文件';
 el('issues').textContent=JSON.stringify(c,null,2);el('limit').hidden=!data.too_large;
 el('nodes').replaceChildren();for(const node of data.nodes){const row=txt('div','');row.className='node-row';row.append(button(node.path||node.label,()=>choose(node)));if(node.kind==='module')row.append(button('展开',()=>navigate(node.path)));el('nodes').append(row);}
 el('json').href=base+'/export?format=json';const exportParams=params();exportParams.set('format','mermaid');el('mermaid').href=base+'/export?'+exportParams;
 draw();if(selected&&data.nodes.some(n=>n.id===selected.id))choose(selected);else {selected=null;el('module-ask').hidden=true;el('detail-title').textContent='选择一个节点或关系';el('detail').replaceChildren();}
 }catch(e){showError(e);}
}
el('generate').onclick=async()=>{try{const job=await api(base);location.href='/jobs/'+job.job_id;}catch(e){showError(e);}};
el('updates').onclick=async()=>{
 el('update-result').textContent='正在检查远端 commit…';
 try{const result=await read('/api/repos/'+repo+'/updates');el('update-result').replaceChildren(txt('p',result.pinned?'当前固定 commit，不跟随分支。':result.available?'发现新版本 '+result.commit.slice(0,12):'当前仓库已是最新版本。'));
 if(result.available)el('update-result').append(button('生成该版本 Wiki（使用当前模型设置）',async()=>{try{const job=await api('/api/repos/'+repo+'/build?commit='+result.commit);location.href='/jobs/'+job.job_id;}catch(e){showError(e);}}));
 }catch(e){showError(e);el('update-result').textContent='更新检查未完成，当前版本仍可阅读。';}
};
for(const id of ['view','tests','external','types'])el(id).onchange=load;
let debounce;el('search').oninput=()=>{clearTimeout(debounce);debounce=setTimeout(load,250);};
el('fit').onclick=()=>cy?.fit(undefined,35);
el('png').onclick=()=>{if(!cy||data.too_large)return;const url=cy.png({bg:'#ffffff',full:true,scale:2});const panel=el('png-result');panel.replaceChildren(txt('p','PNG 已生成；若浏览器未自动下载，可使用下载链接或右键保存预览图。'));const a=link('下载 PNG',url);a.removeAttribute('target');a.download='architecture.png';panel.append(a);const preview=document.createElement('img');preview.src=url;preview.alt='当前架构图 PNG 导出预览';preview.style.maxWidth='100%';panel.append(preview);panel.hidden=false;a.click();};
el('module-ask').onsubmit=async e=>{e.preventDefault();if(!selected)return;try{const job=await api('/api/versions/'+version+'/ask',{question:el('question').value,conversation:crypto.randomUUID().replaceAll('-',''),analysis_id:data.analysis_id,node_id:selected.id});location.href='/jobs/'+job.job_id;}catch(error){showError(error);}};
load();
})();
