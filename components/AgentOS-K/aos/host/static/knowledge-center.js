import {reviewCoverage} from './knowledge-coverage.mjs?v=2';
(() => {
  const root = document.getElementById('knowledge-center');
  if (!root) return;
  const $ = s => root.querySelector(s);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const short = (s,n=90) => s.length > n ? s.slice(0,n)+'…' : s;
  const statusNames = {completed:'已完成',succeeded:'已完成',saved:'已保存',failed:'失败',cancelled:'已取消',interrupted:'已中断',running:'进行中',queued:'排队中'};
  const state = {view: root.dataset.query ? 'list':'map', topic:'', page:1, query:root.dataset.query||'', marked:true, thread:'', selected:null};
  let graph = {topics:[],records:{},pending:[],changed:[]}, sequence=0, detailSequence=0, searchTimer;
  const api = async (url, options={}) => {const response=await fetch(url,{cache:'no-store',...options});if(!response.ok){const e=await response.json().catch(()=>({}));throw Error(e.error?.code||e.detail||'请求失败 '+response.status);}return response.json();};
  const name = id => graph.topics.find(t=>t.id===id)?.title || id;
  const stamp = value => {const d=new Date(value);return Number.isNaN(d.getTime())?value||'':d.toLocaleString();};
  const report = text => {$('#kc-status').textContent=text;};
  function nav(){
    $('#kc-nav').innerHTML='<div class="kc-nav-label">全部积累</div>'+navButton('','全局概览',graph.total||0)+'<div class="kc-nav-label">整理主题 · 可跨类关联</div>'+graph.topics.map(t=>navButton(t.id,t.title,t.count)).join('')+'<div class="kc-nav-label">继续整理</div>'+navButton('pending','新增待核对',graph.pending.length)+navButton('uncertain','保留疑问与复核提示',Object.values(graph.records).filter(r=>r.warning).length);
  }
  function navButton(id,label,count){return '<button data-topic="'+esc(id)+'" aria-pressed="'+(state.topic===id)+'">'+esc(label)+' <small>'+count+'</small></button>';}
  function clearDetail(){state.selected=null;detailSequence++;$('#kc-detail').innerHTML='<p class="muted">选择一条真实记录，查看完整原文、归类依据和同会话上下文。</p>';}
  async function refresh(){
    const seq=++sequence;
    try{
      const [g,health]=await Promise.all([api('/api/v1/data/map'),api('/api/v1/data/status')]);
      if(seq!==sequence)return;
      graph=g;nav();
      const counts=reviewCoverage(g);
      $('#kc-summary').textContent='已接入 '+g.total+' 条 · 已核对 '+counts.reviewed+' · 已归类 '+counts.classified+' · 待归类 '+counts.unclassified+' · 待核对 '+counts.pending;
      $('#kc-coverage').innerHTML='<summary>整理范围与计数说明</summary><p>本轮快照：'+esc(g.generated?stamp(g.generated):'尚未生成')+'。</p><p>'+esc(g.scope||health.scope)+'</p><p>已核对 = 已归类 + 待归类。待核对包括新增、来源变化和未完成归类审阅的记录；已归类只代表讨论方向已组织，不代表历史回答正确或已掌握。</p><p>主分类合计 '+counts.classified+' 条；主题节点显示关联记录数，同一记录可跨类，不能将节点数量直接相加。</p>'+g.topics.map(t=>'<span class="kc-tag">'+esc(t.title)+'：主类 '+(counts.primaryCounts[t.id]||0)+'</span>').join(' ');
      $('#kc-health').innerHTML='<p>归档方式：仅手动更新；新助手直接查询。</p><p>'+esc(health.scope)+'</p>'+health.sources.map(s=>'<p>'+esc(s.product)+' · 最近同步 '+esc(statusNames[s.sync_state]||s.sync_state||'尚无记录')+' · '+esc(stamp(s.observed_at))+'</p>').join('')+'<p>数据库文件大小（不含原件与 WAL）：'+Object.entries(health.bytes).map(([k,v])=>esc(k)+' '+(v/1048576).toFixed(1)+' MiB').join('；')+'</p>';
      await render(seq);
    }catch(e){if(seq===sequence){$('#kc-main').innerHTML='<p class="kc-error">读取失败：'+esc(e.message)+'。可点击刷新重试。</p>';report('读取失败，未修改原记录。');}}
  }
  async function render(seq=++sequence){
    nav();
    root.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('aria-pressed',b.dataset.view===state.view));
    $('#kc-heading').textContent=state.thread?'同会话记录':state.topic==='pending'?'尚未整理的新记录':state.topic==='uncertain'?'保留含糊的信息':state.topic?name(state.topic):state.view==='notes'?'已归档笔记':'全局概览';
    if(state.view==='map'&&!state.topic&&!state.query&&!state.thread){renderGraph();report('虚线是候选主题共享记录的关系，不表示因果或已掌握。新记录不会后台自动生成主题。');return;}
    const p=new URLSearchParams({page:state.page,q:state.query,marked:state.marked,kind:state.view==='notes'?'note':'all',topic:state.topic,thread:state.thread});
    try{
      const data=await api('/api/v1/data/items?'+p);
      if(seq!==sequence)return;
      $('#kc-main').innerHTML='<div class="kc-results">'+(state.thread?'<button class="kc-secondary" data-action="leave-thread">← 返回主题</button>':'')+(state.topic?'<p class="kc-note">'+esc(graph.topics.find(t=>t.id===state.topic)?.description||'保留整理中发现的缺口和疑问。')+' 记录可关联多个主题；点击查看主分类和理由。</p>':'')+(data.items.length?data.items.map(row).join(''):'<p class="empty">没有符合筛选条件的记录。</p>')+'</div><div class="kc-pages"><button data-page="-1" '+(state.page===1?'disabled':'')+'>上一页</button><span class="small muted">'+data.total+' 条 · 第 '+state.page+' 页</span><button data-page="1" '+(state.page*20>=data.total?'disabled':'')+'>下一页</button></div>';
      report('当前 '+data.total+' 条。列表仅加载摘要；点击记录读取正文。');
    }catch(e){if(seq===sequence){$('#kc-main').innerHTML='<p class="kc-error">'+esc(e.message)+'</p>';}}
  }
  function row(r){const meta=graph.records[r.key];return '<button class="kc-row" data-key="'+esc(r.key)+'" aria-pressed="'+(state.selected===r.key)+'"><small>'+esc(r.key)+' · '+esc(r.product)+' · '+esc(stamp(r.updated))+' · '+esc(statusNames[r.status]||r.status)+'</small>'+esc(short(meta?.subject||(r.kind==='note'?'笔记：'+r.preview:r.query)))+'<span class="kc-tags">'+(meta?.topics||[]).map(t=>'<span class="kc-tag">'+esc(name(t))+'</span>').join('')+(r.marked?'<span class="kc-tag">评测／回放标记</span>':'')+(!meta?'<span class="kc-tag">尚未整理</span>':'')+'</span></button>';}
  function renderGraph(){
    const topics=graph.topics.slice(0,10),rows=Math.ceil(topics.length/2),height=Math.max(460,rows*155);
    $('#kc-main').innerHTML='<div class="kc-graph" style="height:'+height+'px"><svg aria-hidden="true"></svg>'+topics.map((t,i)=>'<button class="kc-node" data-topic="'+esc(t.id)+'" title="'+esc(t.description||t.title)+'" style="left:'+(i%2?74:26)+'%;top:'+((Math.floor(i/2)+.5)/rows*100)+'%">'+esc(t.title)+'<span class="kc-node-examples">'+esc(t.examples||'')+'</span><small>'+t.keys.filter(k=>state.marked||!graph.records[k]?.marked).length+' 条关联记录 · 展开 →</small></button>').join('')+(topics.length?'':'<p class="empty">地图尚未生成，原始记录仍可检索。</p>')+'</div><div class="kc-map-note">'+esc(graph.method||'本轮基于真实归档整理')+(reviewCoverage(graph).pending?' · '+reviewCoverage(graph).pending+' 条待核对，未计入已核对。':'')+'</div><div class="kc-examples"><h3>整理中保留的疑问</h3>'+Object.entries(graph.records).filter(([,r])=>r.warning&&(state.marked||!r.marked)).slice(0,4).map(([key,r])=>'<button class="kc-example" data-key="'+esc(key)+'">'+esc(short(r.subject||r.title,48))+'<small>'+esc(short(r.warning,78))+'</small></button>').join('')+'</div>';
    draw();
  }
  function draw(){const el=$('.kc-graph');if(!el)return;const topics=graph.topics.slice(0,10),rows=Math.ceil(topics.length/2),w=el.clientWidth,h=el.clientHeight,svg=el.querySelector('svg');svg.setAttribute('viewBox','0 0 '+w+' '+h);const pos=i=>[(i%2?.74:.26)*w,(Math.floor(i/2)+.5)/rows*h];let html='';topics.forEach((a,i)=>topics.slice(i+1).forEach((b,j)=>{if(a.keys.some(k=>b.keys.includes(k)&&(state.marked||!graph.records[k]?.marked))){const [x,y]=pos(i),[xx,yy]=pos(i+j+1);html+='<path d="M'+x+' '+y+' L'+xx+' '+yy+'"/>';}}));svg.innerHTML=html;}
  async function detail(key){
    const seq=++detailSequence;state.selected=key;$('#kc-detail').innerHTML='<p class="muted">读取原文…</p>';
    try{const r=await api('/api/v1/data/items/'+encodeURIComponent(key));if(seq!==detailSequence)return;state.selected=r.key;const meta=graph.records[r.key];
      let selection=typeof r.selection==='object'?r.selection?.text:r.selection;
      if(r.explanation?.startsWith('reader-note-source/v1\n')){try{selection=JSON.parse(r.explanation.slice(r.explanation.indexOf('\n')+1)).source?.text||selection;}catch{}}
      $('#kc-detail').innerHTML='<span class="kc-tag">'+esc(r.key)+' · '+(r.kind==='note'?'笔记':'问答')+'</span><h2>'+esc(short(r.kind==='note'?r.answer:r.query,70))+'</h2><p class="kc-note">'+esc(r.product)+' · '+esc(statusNames[r.status]||r.status)+' · '+esc(stamp(r.updated))+'</p>'+(meta?.warning?'<div class="kc-warning">'+esc(meta.warning)+'</div>':'')+(reviewCoverage(graph).needsReviewKeys.includes(r.key)?'<div class="kc-warning">来源在地图生成后发生变化，下方整理依据需复核。</div>':'')+'<h3>原文，不作改写</h3><div class="copy">'+esc(r.kind==='note'?r.answer:r.query)+'</div><h3>主分类与关联主题</h3>'+(meta?.topics?.length?meta.topics.map(t=>'<button class="kc-secondary" data-topic="'+esc(t)+'">'+esc(name(t))+'</button>').join(' '):'<p class="kc-note">尚未归类，保留原记录。</p>')+(meta?.rationale?'<h3>整理理由</h3><p>'+esc(meta.rationale)+'</p><p class="kc-note">主分类：'+esc(meta.primary?name(meta.primary):'待归类')+'；此处整理讨论内容，未核验历史结论。</p>':'')+(meta?.evidence?.length?'<h3>归类依据 · 可追溯原文</h3>'+meta.evidence.map(e=>'<div class="kc-proof"><small>'+esc(e.label)+(e.key!==r.key?' · 同会话其他记录':'')+'</small><div>'+esc(e.text)+'</div>'+(e.key!==r.key?'<button class="kc-secondary" data-key="'+esc(e.key)+'">查看该记录</button>':'')+'</div>').join(''):'')+(!meta?.evidence?.length&&meta?.proof?'<h3>归类依据 · '+esc(meta.proof.field)+'</h3><div class="kc-proof">'+esc(meta.proof.text)+'</div>':'')+(selection?'<details><summary>保存的选段原文</summary><div class="copy">'+esc(selection)+'</div></details>':'')+'<details><summary>'+ (r.kind==='note'?'笔记正文':'历史回答全文 · 未经本轮事实核验')+'</summary><div class="copy">'+esc(r.answer||'尚无回答正文')+'</div></details><details><summary>已存引用 '+r.citations.length+' 条</summary><div class="copy">'+esc(JSON.stringify(r.citations,null,2))+'</div></details><div class="kc-links"><button id="kc-thread" class="kc-secondary">同会话记录</button><a href="/data/item/'+encodeURIComponent(r.key)+'" target="_blank" rel="noopener">完整原记录 ↗</a></div><p class="kc-note">'+esc(meta?'归类依据本轮会话与来源复核；人工理解与历史回答的正确性仍须分别判断。':'这条记录尚未进入地图，仍可在记录层检索。')+'</p>';
      $('#kc-thread').onclick=()=>{state.thread=r.thread;state.topic='';state.view='list';state.page=1;state.query='';$('#kc-search').value='';render();};
      root.querySelectorAll('.kc-row').forEach(b=>b.setAttribute('aria-pressed',b.dataset.key===r.key));
    }catch(e){if(seq===detailSequence)$('#kc-detail').innerHTML='<p class="kc-error">'+esc(e.message)+'。原记录可能已删除或不可用。</p>';}
  }
  root.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;if(b.hasAttribute('data-topic')){state.topic=b.dataset.topic;state.thread='';state.page=1;state.view='map';clearDetail();render();}else if(b.dataset.view){state.view=b.dataset.view;state.thread='';state.page=1;if(state.view==='notes'||state.view==='map')state.topic='';clearDetail();render();}else if(b.dataset.key){detail(b.dataset.key);}else if(b.dataset.page){state.page+=Number(b.dataset.page);render();}else if(b.dataset.action==='leave-thread'){state.thread='';state.page=1;render();}});
  $('#kc-search').addEventListener('input',e=>{state.query=e.target.value;state.page=1;clearDetail();clearTimeout(searchTimer);searchTimer=setTimeout(()=>render(),200);});
  $('#kc-marked').addEventListener('change',e=>{state.marked=e.target.checked;state.page=1;clearDetail();render();});
  $('#kc-refresh').onclick=()=>refresh();
  $('#kc-sync').onclick=async()=>{const button=$('#kc-sync');button.disabled=true;button.textContent='更新中…';try{const r=await api('/api/v1/data/sync',{method:'POST',headers:{'x-aos-token':root.dataset.token}});let jobs=[];for(;;){jobs=await Promise.all(r.job_ids.map(id=>api('/api/v1/jobs/'+id)));if(jobs.every(j=>!['queued','running'].includes(j.state)))break;await new Promise(resolve=>setTimeout(resolve,1000));}await refresh();report('本次旧档案更新：'+jobs.map(j=>j.product+' '+(statusNames[j.state]||j.state)+(j.error?'（'+j.error+'）':'')).join('；')+'。任务已结束，无后台定时同步。');}catch(e){report('更新失败：'+e.message+'。没有自动重试，旧记录仍可读取。');}finally{button.disabled=false;button.textContent='更新旧档案';}};
  new ResizeObserver(draw).observe($('#kc-main'));refresh();
})();
