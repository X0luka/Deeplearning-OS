import {KnowledgeView} from './KnowledgeView';
import React,{useState,useEffect,useMemo,useRef} from 'react';
import {createRoot} from 'react-dom/client';
import {ReactFlow,ReactFlowProvider,Background,Controls,MiniMap,Handle,Position,MarkerType,useReactFlow,type NodeProps,type Node} from '@xyflow/react';
import ELK from 'elkjs/lib/elk-api';
import {toPng} from 'html-to-image';
import '@xyflow/react/dist/style.css';
import './style.css';

type Proof={path:string;start:number;end:number};
type Module={id:string;path:string;title:string;name:string;kind:string;files:string[];responsibility:string;role:string;evidence:Proof[];declarations:{name:string;path:string;start:number;kind:string}[];incoming:number;outgoing:number;semantic_state:string;matched?:boolean};
type Relation={id:string;source:string;target:string;count:number;evidence:Proof[];types:Record<string,number>};
type Reading={status:string;analysis_id:string;commit:string;nodes:Module[];edges:Relation[];semantic_status:string;summary:string;total_nodes:number;hidden_nodes:number;matched_count:number;coverage:{files:number;parsed:number;unresolved:number}};
declare global{interface Window{architectureConfig:{version:string;repo:string;name:string};api:(url:string,body?:unknown)=>Promise<any>}}
const cfg=window.architectureConfig,base='/api/versions/'+cfg.version;
const roles:Record<string,{title:string;color:string;description:string}>={
 entry:{title:'入口与装配',color:'#315fcb',description:'程序从哪里进入，各部分如何组织'},
 interface:{title:'界面与交互',color:'#8b5cc4',description:'用户看到什么，如何发起操作'},
 application:{title:'业务与协调',color:'#087f83',description:'功能如何组织，状态如何流转'},
 foundation:{title:'基础与数据',color:'#aa6420',description:'提供哪些数据契约与基础能力'},
 support:{title:'开发与支持',color:'#637184',description:'构建、测试和开发辅助'},
 other:{title:'待归类',color:'#637184',description:'证据不足，尚不能确定职责分类'}};
const source=(p:Proof)=>'/versions/'+cfg.version+'/source?'+new URLSearchParams({path:p.path,start:String(p.start),end:String(p.end)})+'#L'+p.start;
const elk=new ELK({workerUrl:'/static/reader/elk-worker.min.js'});
type CardData=Record<string,unknown>&{title:string;subtitle:string;description:string;role:string;count:number;cluster:boolean;unknown:boolean;matched:boolean;select:()=>void;expand:()=>void};
function Card({data,selected}:NodeProps<Node<CardData>>){
 const role=roles[data.role]||roles.other;
 return <div className={'module-card '+(selected?'chosen ':'')+(data.matched?'matched':'')} style={{borderTopColor:role.color}}>
 <Handle type="target" position={Position.Left}/><div className="card-meta"><span style={{color:role.color}}>{data.cluster?'已折叠关联':data.unknown?'静态文件分组':'抽样职责推断'}</span><span>{data.count} 文件</span></div>
 <button className="card-title" onClick={data.select}>{data.title}</button><div className="card-subtitle">{data.subtitle}</div>
 <p>{data.description}</p><button className="card-open" onClick={e=>{e.stopPropagation();data.expand()}}>{data.cluster?'查看组内模块 →':'展开代码与边界 →'}</button>
 <Handle type="source" position={Position.Right}/></div>
}
const nodeTypes={module:Card};
function App(){
 const [entityContext,setEntityContext]=useState({knowledge_id:'',entity_id:''});
 const [mode,setMode]=useState(''),[scope,setScope]=useState(''),[query,setQuery]=useState(''),[focus,setFocus]=useState(''),[direction,setDirection]=useState('both'),[role,setRole]=useState('');
 const [data,setData]=useState<Reading|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[picked,setPicked]=useState<Module|null>(null),[edge,setEdge]=useState<Relation|null>(null);
 const [nodes,setNodes]=useState<Node<CardData>[]>([]),[edges,setEdges]=useState<any[]>([]),[layoutBusy,setLayoutBusy]=useState(false),[question,setQuestion]=useState('解释这个模块的职责与依赖，并指出证据不足之处。');
 const [png,setPng]=useState(''),[showNav,setShowNav]=useState(true),[showDetail,setShowDetail]=useState(false);
 const diagram=useRef<HTMLDivElement>(null),generation=useRef(0);const flow=useReactFlow();
 useEffect(()=>{const ctrl=new AbortController();const t=setTimeout(async()=>{
  setBusy(true);setError('');
  try{const r=await fetch(base+'/reading?'+new URLSearchParams({scope,query,focus,direction,...(entityContext.entity_id?entityContext:{})}),{signal:ctrl.signal});if(!r.ok)throw Error(await r.text());setData(await r.json());}
  catch(e){if(!ctrl.signal.aborted)setError(String(e));}finally{if(!ctrl.signal.aborted)setBusy(false);}
 },query?200:0);return()=>{ctrl.abort();clearTimeout(t)}},[scope,query,focus,direction,entityContext]);
 function select(n:Module){window.dispatchEvent(new CustomEvent('wiki-chat-context',{detail:{context:{analysis_id:data?.analysis_id,node_id:n.id},label:n.title}}));setPicked(n);setEdge(null);setShowDetail(true);}
 function reset(){setEntityContext({knowledge_id:'',entity_id:''});setScope('');setQuery('');setFocus('');setRole('');setPicked(null);setEdge(null);}
 function expand(n:Module){if(n.kind==='module'){setMode('dependencies');setScope(n.path);setRole('');setFocus('');setQuery('');setPicked(null);setEdge(null);}else{select(n)}}
 const projected=useMemo(()=>{
  if(!data)return {items:[] as any[],edges:[] as Relation[],folded:0};
  const selectedRoles=new Set(data.nodes.filter(n=>n.role===role).map(n=>n.id));
  const retained=new Set(selectedRoles);
  if(role)data.edges.forEach(e=>{if(selectedRoles.has(e.source))retained.add(e.target);if(selectedRoles.has(e.target))retained.add(e.source)});
  let available=role?data.nodes.filter(n=>retained.has(n.id)):data.nodes;
  let relationPool=data.edges;
  const matches=available.filter(n=>n.matched);const hub=mode==='dependencies'?(focus||(query&&matches.length===1?matches[0].id:!query&&available.length>8?[...available].sort((a,b)=>b.files.length-a.files.length)[0]?.id:'')):'';
  if(hub){relationPool=data.edges.filter(e=>e.source===hub||e.target===hub);const ids=new Set([hub,...relationPool.flatMap(e=>[e.source,e.target])]);available=available.filter(n=>ids.has(n.id));}
  const mapping=new Map<string,string>();let items:any[]=[];
  if(mode==='overview'&&!scope&&!role&&!query&&!focus){
   for(const r of Object.keys(roles)){const members=available.filter(n=>n.role===r);if(!members.length)continue;
    const id='role:'+r;members.forEach(n=>mapping.set(n.id,id));
    items.push({id,role:r,title:roles[r].title,subtitle:members.length+' 个模块 · 职责分类',responsibility:members.slice(0,4).map(n=>n.title).join(' · '),files:members.flatMap(n=>n.files),cluster:true,members});
   }
  }else if(role){
   const members=available.filter(n=>n.role===role),outside=available.filter(n=>n.role!==role);
   members.slice(0,4).forEach(n=>{mapping.set(n.id,n.id);items.push({...n,subtitle:n.path})});
   for(const [id,list,title] of [['folded',members.slice(4),'更多组内模块'],['boundary-group',outside,'其他职责的关联模块']] as [string,Module[],string][]){
    if(list.length){list.forEach(n=>mapping.set(n.id,id));items.push({id,role:'other',title,subtitle:list.length+' 个模块 · 点击查看',responsibility:list.slice(0,3).map(n=>n.title).join(' · '),files:list.flatMap(n=>n.files),cluster:true,members:list})}
   }
   relationPool=relationPool.filter(e=>selectedRoles.has(e.source)||selectedRoles.has(e.target));
  }else{
   const sorted=[...available].sort((a,b)=>Number(b.id===hub)-Number(a.id===hub)||Number(b.matched)-Number(a.matched)||(b.incoming+b.outgoing)-(a.incoming+a.outgoing)||b.files.length-a.files.length);
   const limit=hub?4:8;const primary=sorted.slice(0,limit),rest=sorted.slice(limit);
   primary.forEach(n=>{mapping.set(n.id,n.id);items.push({...n,matched:n.matched||n.id===hub,subtitle:(role&&n.role!==role?'关联范围 · ':'')+n.path})});
   if(rest.length){rest.forEach(n=>mapping.set(n.id,'folded'));items.push({id:'folded',role:'support',title:'更多关联模块',subtitle:rest.length+' 项已折叠 · 可从左侧定位',responsibility:rest.slice(0,3).map(n=>n.title).join(' · '),files:rest.flatMap(n=>n.files),cluster:true,members:rest});}
  }
  const grouped=new Map<string,Relation>();
  relationPool.forEach(e=>{const a=mapping.get(e.source),b=mapping.get(e.target);if(!a||!b||a===b)return;const id=a+'>'+b;const prev=grouped.get(id);if(prev){prev.count+=e.count;prev.evidence.push(...e.evidence)}else grouped.set(id,{...e,id,source:a,target:b,evidence:[...e.evidence]})});
  return {items,edges:[...grouped.values()],folded:items.find(n=>n.id==='folded')?.members.length||0};
 },[data,mode,scope,role,query,focus]);
 useEffect(()=>{
  if(!mode)return;const current=++generation.current;setLayoutBusy(true);
  const items=projected.items;const isMap=mode==='overview'&&!scope&&!query&&!focus;
  elk.layout({id:'root',layoutOptions:{'elk.algorithm':'layered','elk.direction':'RIGHT','elk.spacing.nodeNode':'45','elk.layered.spacing.nodeNodeBetweenLayers':'105'},children:items.map(n=>({id:n.id,width:280,height:190})),edges:projected.edges.map(e=>({id:e.id,sources:[e.source],targets:[e.target]}))})
  .then(result=>{if(current!==generation.current)return;
   const positioned=items.map((n,i)=>{const p=isMap?{x:(i%3)*345,y:Math.floor(i/3)*245}:result.children?.find(x=>x.id===n.id);return {id:n.id,type:'module',position:{x:p?.x||0,y:p?.y||0},selected:picked?.id===n.id,data:{title:n.title,subtitle:n.subtitle||n.path,description:n.responsibility,role:n.role,count:n.files.length,cluster:!!n.cluster,unknown:n.semantic_state==='unknown',matched:!!n.matched,
    select:()=>{if(n.cluster){setPicked({...n,kind:'cluster',path:'',declarations:[],evidence:[],semantic_state:'inferred',incoming:0,outgoing:0});setEdge(null);setShowDetail(true)}else select(n)},
    expand:()=>{if(n.id.startsWith('role:')){setRole(n.role);setPicked(null)}else if(n.cluster){setShowNav(true);setPicked({...n,kind:'cluster',path:'',declarations:[],evidence:[],semantic_state:'inferred',incoming:0,outgoing:0})}else expand(n)}}} as Node<CardData>});
   setNodes(positioned);setEdges(projected.edges.map(e=>({id:e.id,source:e.source,target:e.target,type:isMap?'default':'smoothstep',label:isMap?undefined:e.count+' 条代码依赖',markerEnd:{type:MarkerType.ArrowClosed,color:'var(--aos-graph)'},style:{stroke:'var(--aos-graph)',strokeWidth:1.6},labelStyle:{fontSize:12,fill:'var(--aos-muted)'},labelBgStyle:{fill:'var(--aos-panel)'},data:e})));
   setLayoutBusy(false);requestAnimationFrame(()=>flow.fitView({padding:.18,minZoom:.4,maxZoom:1,duration:250}));
  }).catch(()=>{if(current===generation.current){setLayoutBusy(false);setError('布局未完成，可先使用左侧模块目录。')}});
 },[projected,mode]);
 useEffect(()=>{const t=setTimeout(()=>flow.fitView({padding:.12,minZoom:.4,maxZoom:1,duration:200}),100);return()=>clearTimeout(t)},[nodes,showDetail,showNav]);
 function saveMermaid(){const ids=new Map(projected.items.map((n,i)=>[n.id,'n'+i]));const clean=(s:string)=>s.replace(/["<>\n\r]/g,' ');const lines=['flowchart LR',...projected.items.map(n=>ids.get(n.id)+'["'+clean(n.title)+'"]'),...projected.edges.map(e=>ids.get(e.source)+' -->|'+e.count+' 条静态依赖| '+ids.get(e.target))];const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([lines.join('\n')],{type:'text/plain'}));a.download='architecture.mmd';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)}
 async function generate(){setError('');try{const j=await window.api(base+(data?.status==='missing'?'/architecture':'/knowledge'));location.href='/jobs/'+j.job_id}catch(e){setError(String(e))}}
 function focusNode(n:Module){setMode('dependencies');setRole('');setFocus(n.id);setQuery('');select(n)}
 function ask(){if(!picked||picked.kind==='cluster'||picked.kind==='boundary')return;window.dispatchEvent(new CustomEvent('wiki-chat-context',{detail:{context:{analysis_id:data?.analysis_id,node_id:picked.id},label:picked.title,question,open:true}}));}
 async function exportPng(){if(!diagram.current)return;try{const url=await toPng(diagram.current,{backgroundColor:getComputedStyle(document.documentElement).getPropertyValue('--aos-bg').trim(),pixelRatio:2,filter:n=>!(n instanceof HTMLElement&&n.classList.contains('react-flow__controls'))});setPng(url)}catch{setError('图片导出未完成，请使用浏览器截图或 JSON 导出。')}}
 function saveJSON(){if(!data)return;const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));a.download='architecture-reading.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)}
 const visibleList=(role?data?.nodes.filter(n=>n.role===role):data?.nodes)||[];
 const relations=picked?data?.edges.filter(e=>e.source===picked.id||e.target===picked.id)||[]:[];
 if(mode==='overview')return <KnowledgeView version={cfg.version} onBack={()=>setMode('')} onDependencies={(knowledge_id,entity_id)=>{setEntityContext({knowledge_id,entity_id});setScope('');setQuery('');setFocus('');setPicked(null);setEdge(null);setMode('dependencies')}}/>;
 return <div className={'reader '+(mode?'reading-active':'')}>
 <div className="reader-heading"><div><a className="backlink" href={'/versions/'+cfg.version}>← 返回 Wiki</a><div className="overline">REPOSITORY ATLAS · 源码阅读地图</div><h1>{cfg.name}</h1><p>理解职责，探索依赖，让每个解释都有据可查。</p></div><div className="version-chip"><span className="live-dot"/>固定源码快照 <code>{data?.commit?.slice(0,12)||'加载中'}</code><small>审查版本 · 产品可用性待你确认</small></div></div>
 {!mode?<div className="view-picker"><div className="section-kicker">选择你想观察的角度</div><div className="view-options">
 <button onClick={()=>setMode('overview')}><span className="view-number">01 / RESPONSIBILITIES</span><h2>架构总览</h2><p>项目由哪些部分组成？<br/>围绕核心功能理解职责、协作与外部边界。</p><strong>理解项目架构 →</strong></button>
 <button onClick={()=>setMode('dependencies')}><span className="view-number">02 / DEPENDENCIES</span><h2>模块依赖</h2><p>谁使用谁，关系从何而来？<br/>聚焦模块上下游，回到真实导入证据。</p><strong>探索关联 →</strong></button></div><div className="reading-note"><b>两种视角，同一份源码依据。</b>总览围绕功能与协作组织源码证据；模块依赖展示静态导入关系。两种视图分别回答不同问题。</div></div>:<>
 <div className="mode-bar"><button className="quiet" onClick={()=>setMode('')}>所有视图</button><button className={mode==='overview'?'tab active':'tab'} onClick={()=>{setMode('overview');setRole('')}}>架构总览</button><button className={mode==='dependencies'?'tab active':'tab'} onClick={()=>{setMode('dependencies');setRole('')}}>模块依赖</button><span className="bar-spacer"/><button className="quiet" onClick={()=>setShowNav(!showNav)}>模块目录</button><button className="quiet" onClick={()=>setShowDetail(!showDetail)}>解释面板</button><button className="quiet" onClick={()=>document.querySelector('.reader')?.requestFullscreen()}>全屏</button></div>
 <section className="orientation"><div><h2>{role?roles[role].title:scope?scope:mode==='overview'?'先理解分工，再进入代码':'一次理解一个模块的直接依赖'}</h2><p>{mode==='overview'?'颜色表示职责分类。点击卡片查看解释，展开后阅读模块及源码。':'箭头 A → B 表示 A 依赖 B。大仓库先聚焦主要模块；在左侧选择模块切换。折叠卡片保留其余邻居，连线只展示当前焦点的直接关系。'}</p></div><button className="outline" onClick={generate}>{data?.semantic_status==='independent'?'更新功能知识':data?.semantic_status==='ready'?'语义说明已生成':'生成职责说明'}</button></section>
 <div className="breadcrumbs"><button onClick={reset}>仓库</button>{entityContext.entity_id&&<span>当前功能的证据文件及直接邻居 · 非完整功能调用图</span>}{scope.split('/').filter(Boolean).map((s,i,a)=><React.Fragment key={i}><span>/</span><button onClick={()=>{setScope(a.slice(0,i+1).join('/'));setFocus('');setQuery('')}}>{s}</button></React.Fragment>)}{role&&<><span>/</span><span>{roles[role].title}</span></>}{focus&&<span className="focus-pill">聚焦中 <button onClick={()=>setFocus('')}>×</button></span>}<span className="bar-spacer"/><span>{busy?'读取中…':'范围 '+(data?.nodes.length||0)+' 个对象 · 画布 '+projected.items.length+' 个节点 / '+projected.edges.length+' 组关系'}</span></div>
 <div className={'workspace '+(!showNav?'no-nav ':'')+(!showDetail?'no-detail':'')}>
 {showNav&&<aside className="module-nav"><h3>模块目录 <small>{visibleList.length}</small></h3><input aria-label="搜索模块" placeholder="搜索名称、职责或路径" value={query} onChange={e=>{setQuery(e.target.value);setFocus('')}}/><p className="micro">匹配对象及上下游始终一起显示</p><div className="nav-items">{visibleList.map(n=><button key={n.id} onClick={()=>focusNode(n)} className={picked?.id===n.id?'nav-item selected':'nav-item'}><span className="role-dot" style={{background:roles[n.role]?.color}}/><div><b>{n.title}</b><small>{n.path}</small></div><span>{n.files.length||'↗'}</span></button>)}</div></aside>}
 <section className="canvas-section"><div className="canvas-toolbar"><span className="legend"><i/>源码依赖 <span className="infer-dot"/>{data?.semantic_status==='independent'?'文件边界分组':'职责为推断'}</span><select aria-label="依赖方向" value={direction} onChange={e=>setDirection(e.target.value)}><option value="both">上下游</option><option value="incoming">谁依赖它</option><option value="outgoing">它依赖谁</option></select><button className="quiet" onClick={()=>{setFocus('');setQuery('');setRole('')}}>清除聚焦</button></div>
 <div className="graph-canvas" ref={diagram}><ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodeClick={(_,n)=>n.data.select()} onEdgeClick={(_,e)=>{const r=e.data as Relation;window.dispatchEvent(new CustomEvent('wiki-chat-context',{detail:{context:{selection:JSON.stringify({source:r.source,target:r.target,kind:'静态导入，非运行顺序',evidence:r.evidence}).slice(0,3000)},label:'选中的静态依赖关系'}}));setEdge(r);setPicked(null);setShowDetail(true)}} nodesDraggable={false} nodesConnectable={false} minZoom={.3} maxZoom={1.6} onlyRenderVisibleElements><Background color="var(--aos-line)" gap={22}/><Controls showInteractive={false}/><MiniMap nodeColor="var(--aos-graph)" pannable zoomable/></ReactFlow>
 {(busy||layoutBusy)&&<div className="canvas-status">正在组织阅读视图…</div>}
 {!busy&&!layoutBusy&&nodes.length===0&&<div className="empty-state"><h3>{data?.status==='missing'?'尚未生成源码分析':'当前条件没有匹配对象'}</h3><p>可以清除搜索或返回上层；这不表示仓库没有依赖。</p><button onClick={data?.status==='missing'?generate:reset}>{data?.status==='missing'?'生成源码分析':'恢复仓库视图'}</button></div>}</div>
 <div className="canvas-footer"><span>拖动画布浏览 · 滚轮缩放 · 左侧可直接定位{projected.folded>0?' · 超出对象已折叠，可从目录逐一聚焦':''}</span><button className="quiet" onClick={exportPng}>导出 PNG</button><button className="quiet" onClick={saveJSON}>JSON</button><button className="quiet" onClick={saveMermaid}>Mermaid</button></div></section>
 {showDetail&&<aside className="detail-panel">{edge?<><div className="section-kicker">关系证据 / FACT</div><h2>为什么相连？</h2><p>{projected.items.find(n=>n.id===edge.source)?.title||data?.nodes.find(n=>n.id===edge.source)?.title||'来源分组'} → {projected.items.find(n=>n.id===edge.target)?.title||data?.nodes.find(n=>n.id===edge.target)?.title||'目标分组'}</p><div className="explain-box">这条关系汇总了 {edge.count} 条静态导入。它证明代码依赖，不证明运行先后。</div><Proofs proofs={edge.evidence}/></>:picked?<><div className="section-kicker">{picked.kind==='boundary'?'范围外对象':picked.kind==='cluster'?'职责分组':'模块解读'}</div><h2>{picked.title}</h2><code className="detail-path">{picked.path}</code><div className="inference-label">{picked.semantic_state==='unknown'?'待确认 · 尚无职责建模':'模型推断 · 需结合源码核对'}</div><p className="responsibility">{picked.responsibility}</p>
 <div className="detail-stats"><span><b>{picked.files.length}</b>文件</span><span><b>{picked.incoming}</b>上游模块</span><span><b>{picked.outgoing}</b>下游模块</span></div>
 {picked.kind==='cluster'?<><h3>分组中的模块</h3>{((picked as any).members||[]).map((n:Module)=><button className="related" key={n.id} onClick={()=>focusNode(n)}>{n.title}<small>{n.path}</small></button>)}</>:<>
 <div className="detail-actions"><button onClick={()=>focusNode(picked)}>聚焦上下游</button>{picked.kind==='module'&&<button className="outline" onClick={()=>expand(picked)}>展开模块</button>}</div>
 <h3>职责依据</h3>{picked.evidence.length?<Proofs proofs={picked.evidence}/>:<p className="micro">当前无职责引用。下方声明与源码可供核对。</p>}
 <h3>直接关联</h3>{relations.length?relations.map(r=>{const incoming=r.target===picked.id,other=data?.nodes.find(n=>n.id===(incoming?r.source:r.target));return <button className="related" key={r.id} onClick={()=>{setEdge(r);setPicked(null)}}><small>{incoming?'依赖本模块':'本模块依赖'} · {r.count} 条导入</small>{other?.title||'范围外对象'}</button>}):<p className="micro">当前范围未发现跨模块依赖；不等于没有内部关系。</p>}
 <h3>声明与源码</h3>{picked.declarations.slice(0,12).map((d,i)=><a className="source-link" key={i} href={source({path:d.path,start:d.start,end:d.start})} target="_blank" rel="noreferrer">{d.name}<small>{d.path}:{d.start}</small></a>)}
 {picked.files.slice(0,6).map(p=><a className="source-link" key={p} href={source({path:p,start:1,end:1})} target="_blank" rel="noreferrer">{p} ↗</a>)}
 {picked.kind!=='boundary'&&<><h3>继续理解这个模块</h3><textarea aria-label="模块问题" value={question} onChange={e=>setQuestion(e.target.value)}/><button onClick={ask}>结合源码解读</button><p className="micro">可连续追问 · 回答保存在侧栏与阅读历史</p></>}</>}</>:<><div className="section-kicker">HOW TO READ</div><h2>这张图怎么读？</h2><ol className="guide"><li><b>看职责</b><p>中文卡片解释作用，原始名称保留代码定位。</p></li><li><b>看关系</b><p>箭头指向被依赖的一方。点击连线查看导入位置。</p></li><li><b>回到证据</b><p>选择模块，在这里查看职责依据、上下游和源码。</p></li></ol><div className="explain-box">职责分类不是部署层级。未支持语言、动态关系和抽样遗漏仍可能存在。</div></>}</aside>}
 </div></>}
 {error&&<div className="reader-error" role="alert">{error}<button onClick={()=>setError('')}>关闭</button></div>}
 {data&&<div className="coverage-strip"><span>快照 {data.coverage?.files} 文件</span><span>可解析 {data.coverage?.parsed}</span><span>未解析关系 {data.coverage?.unresolved}</span><span>当前分析不含测试及不支持语言的文件节点</span><span>静态导入关系不代表运行顺序；功能协作请查看架构总览</span></div>}
 {png&&<div className="export-preview"><button className="quiet" onClick={()=>setPng('')}>关闭预览</button><a download="architecture.png" href={png}>保存 PNG</a><img src={png} alt="架构阅读图导出预览"/></div>}
 </div>
}
function Proofs({proofs}:{proofs:Proof[]}){const [all,setAll]=useState(false);const unique=proofs.filter((p,i,a)=>a.findIndex(q=>q.path===p.path&&q.start===p.start&&q.end===p.end)===i);return <div className="proofs">{unique.slice(0,all?unique.length:6).map((p,i)=><a key={i} href={source(p)} target="_blank" rel="noreferrer"><span>源码证据 ↗</span><b>{p.path}</b><small>L{p.start}–{p.end}</small></a>)}{unique.length>6&&<button className="quiet" onClick={()=>setAll(!all)}>{all?'收起':'查看全部 '+unique.length+' 处证据'}</button>}</div>}
createRoot(document.getElementById('architecture-reader')!).render(<ReactFlowProvider><App/></ReactFlowProvider>);
