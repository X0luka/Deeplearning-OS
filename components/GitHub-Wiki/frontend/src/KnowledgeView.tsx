import React, {useState,useEffect,useRef} from 'react';
import {ReactFlow,ReactFlowProvider,Background,Controls,Handle,Position,MarkerType,useReactFlow,BaseEdge,EdgeLabelRenderer,type EdgeProps,type NodeProps} from '@xyflow/react';
import ELK, {type ElkNode} from 'elkjs/lib/elk-api';
import {toPng} from 'html-to-image';

type Proof={path:string;start:number;end:number;text:string};
type Claim={text:string;evidence:string[];basis:string};
type Entity={id:string;title:string;responsibility:string;kind:string;domains:string[];evidence:string[]};
type Relation={source:string;target:string;label:string;explanation:string;evidence:string[];basis:string};
type Domain={id:string;title:string;responsibility:string;boundary:string;claims:Claim[];steps:Claim[];gaps:string[]};
type Knowledge={status?:string;knowledge_id:string;commit:string;model:{title:string;summary:Claim;boundary:Claim;nodes:Entity[];relations:Relation[];omissions:string[];questions:string[]};domains:Domain[];evidence:Record<string,Proof>;trace:unknown[]};
const elk = new ELK({workerUrl:'/static/reader/elk-worker.min.js'});
function MapCard({data}:NodeProps){
 return <div className={'knowledge-node '+(data.kind==='external'?'outside':'')}><Handle type="target" position={Position.Left}/><span>{data.kind==='external'?'仓库外服务':data.kind==='library'?'客户端库':'本仓库'}</span><strong>{String(data.title)}</strong><p>{String(data.responsibility)}</p><small>点击查看职责与证据</small><Handle type="source" position={Position.Right}/></div>;
}
const nodeTypes={knowledge:MapCard};
function RoutedRelation({id,data,markerEnd,style}:EdgeProps){
 const route=data as any;
 return <><BaseEdge id={id} path={route.path||''} markerEnd={markerEnd} style={style}/>{route.labelPosition&&<EdgeLabelRenderer><button type="button" className="knowledge-edge-label nodrag nopan" onClick={()=>route.select?.()} style={{transform:'translate(-50%, -50%) translate('+route.labelPosition.x+'px,'+route.labelPosition.y+'px)'}}>{String(route.label)}</button></EdgeLabelRenderer>}</>;
}
const edgeTypes={knowledge:RoutedRelation};
function Diagram({knowledge,onPick,onRelation}:{knowledge:Knowledge;onPick:(n:Entity)=>void;onRelation:(r:Relation)=>void}){
 const [extent,setExtent]=useState({width:1000,height:600}),[canvasHeight,setCanvasHeight]=useState(680);const [nodes,setNodes]=useState<any[]>([]),[edges,setEdges]=useState<any[]>([]),[error,setError]=useState('');
 const flow=useReactFlow(); const canvas=useRef<HTMLDivElement>(null); const internalIds=new Set(knowledge.model.nodes.filter(n=>n.kind==='internal').map(n=>n.id));const vertical=knowledge.model.relations.filter(r=>internalIds.has(r.source)&&internalIds.has(r.target)).length>0;
 useEffect(()=>{let active=true;const m=knowledge.model;
 const depth=(id:string,seen=new Set<string>()):number=>{if(seen.has(id))return 0;const next=new Set(seen).add(id);return Math.max(0,...m.relations.filter(r=>r.target===id&&internalIds.has(r.source)).map(r=>1+depth(r.source,next)))};
 const finalLayer=Math.max(0,...m.nodes.filter(n=>n.kind==='internal').map(n=>depth(n.id)))+1;

 elk.layout({id:'root',layoutOptions:{'elk.algorithm':'layered','elk.direction':vertical?'DOWN':'RIGHT','elk.edgeRouting':'ORTHOGONAL','elk.spacing.nodeNode':'45','elk.partitioning.activate':'true','elk.layered.spacing.nodeNodeBetweenLayers':'110','elk.layered.cycleBreaking.strategy':'GREEDY'},children:m.nodes.map((n,i)=>({id:n.id,width:280,height:190,layoutOptions:{'elk.partitioning.partition':String(vertical?(n.kind==='external'?finalLayer:depth(n.id)):(n.kind==='external'?2:i%2))}})),edges:m.relations.map((r,i)=>({id:'r'+i,sources:[r.source],targets:[r.target],labels:[{text:r.label,width:Math.max(95,r.label.length*14),height:24}]}))}).then((layout:ElkNode)=>{
 if(!active)return;setExtent({width:layout.width||1000,height:layout.height||600});
 setNodes(m.nodes.map(n=>({id:n.id,type:'knowledge',position:{x:layout.children?.find(p=>p.id===n.id)?.x||0,y:layout.children?.find(p=>p.id===n.id)?.y||0},data:{...n}})));
 setEdges(m.relations.map((r,i)=>{const edge=layout.edges?.find(e=>e.id==='r'+i);const section=edge?.sections?.[0];const points=section?[section.startPoint,...(section.bendPoints||[]),section.endPoint]:[];const label=edge?.labels?.[0];return {id:'r'+i,source:r.source,target:r.target,type:'knowledge',markerEnd:{type:MarkerType.ArrowClosed,color:'var(--aos-graph)'},style:{stroke:r.basis==='inferred'?'var(--aos-warning)':'var(--aos-graph)',strokeWidth:1.7,strokeDasharray:r.basis==='inferred'?'5 4':undefined},data:{...r,select:()=>onRelation(r),path:points.map((p,j)=>(j?'L':'M')+p.x+','+p.y).join(' '),labelPosition:label&&{x:(label.x||0)+(label.width||0)/2,y:(label.y||0)+(label.height||0)/2}}}}));

 }).catch(()=>setError('布局未完成，仍可从下方功能列表阅读。'));return()=>{active=false}},[knowledge]);
 useEffect(()=>{let timer:ReturnType<typeof setTimeout>;const fit=()=>{clearTimeout(timer);const width=canvas.current?.clientWidth||1000;setCanvasHeight(Math.max(520,Math.min(2000,extent.height*Math.min(1,(width-60)/extent.width)+70)));timer=setTimeout(()=>flow.fitView({padding:.1,maxZoom:1,minZoom:.1}),100)};const observer=new ResizeObserver(fit);if(canvas.current)observer.observe(canvas.current);fit();return()=>{observer.disconnect();clearTimeout(timer)}},[nodes,flow,extent]);
 return <div className="knowledge-canvas" ref={canvas} style={{height:canvasHeight}}><ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} onNodeClick={(_,n)=>onPick(knowledge.model.nodes.find(x=>x.id===n.id)!)} onEdgeClick={(_,e)=>onRelation(e.data as Relation)} nodesDraggable={false} nodesConnectable={false} minZoom={.4} maxZoom={1.7}><Background color="var(--aos-line)" gap={24}/><Controls showInteractive={false}/></ReactFlow>{error&&<p>{error}</p>}</div>
}
export function KnowledgeView({version,onBack,onDependencies}:{version:string;onBack:()=>void;onDependencies:(knowledge:string,entity:string)=>void}){
 const [value,setValue]=useState<Knowledge|null>(null),[error,setError]=useState(''),[selected,setSelected]=useState<Entity|null>(null),[relation,setRelation]=useState<Relation|null>(null),[topic,setTopic]=useState(''),[question,setQuestion]=useState('解释这个功能如何工作，指出关键实现和证据不足之处。'),[image,setImage]=useState('');
 const chart=useRef<HTMLDivElement>(null),detail=useRef<HTMLElement>(null);
 useEffect(()=>{const controller=new AbortController();fetch('/api/versions/'+version+'/knowledge',{signal:controller.signal}).then(r=>{if(!r.ok)throw Error('知识读取失败');return r.json()}).then(setValue).catch(e=>{if(!controller.signal.aborted)setError(String(e))});return()=>controller.abort()},[version]);
 const source=(key:string)=>{const p=value!.evidence[key];return '/versions/'+version+'/source?'+new URLSearchParams({path:p.path,start:String(p.start),end:String(p.end)})+'#L'+p.start};
 function proofs(keys:string[]){const unique=[...new Set(keys)];const link=(k:string)=><a key={k} href={source(k)} target="_blank" rel="noreferrer">{value!.evidence[k].path}<small>L{value!.evidence[k].start}–{value!.evidence[k].end} ↗</small></a>;return <div className="knowledge-proofs">{unique.slice(0,3).map(link)}{unique.length>3&&<details><summary>另外 {unique.length-3} 处证据</summary><div className="knowledge-proofs">{unique.slice(3).map(link)}</div></details>}</div>}

 function pick(n:Entity){window.dispatchEvent(new CustomEvent('wiki-chat-context',{detail:{context:{knowledge_id:value!.knowledge_id,entity_id:n.id},label:n.title}}));setSelected(n);setRelation(null);setTopic(n.kind==='external'?'':n.domains[0]||'');setTimeout(()=>detail.current?.scrollIntoView({behavior:'smooth',block:'nearest'}),0)}
 async function generate(){try{const j=await window.api('/api/versions/'+version+'/knowledge');location.href='/jobs/'+j.job_id}catch(e){setError(String(e))}}
 function ask(){if(!selected||!value)return;window.dispatchEvent(new CustomEvent('wiki-chat-context',{detail:{context:{knowledge_id:value.knowledge_id,entity_id:selected.id},label:selected.title,question,open:true}}));}
 function download(text:string,name:string,type:string){const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([text],{type}));a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)}
 async function png(){try{if(chart.current)setImage(await toPng(chart.current,{pixelRatio:2,backgroundColor:getComputedStyle(document.documentElement).getPropertyValue('--aos-bg').trim()}))}catch{setError('图片导出失败，请使用 JSON 或 Mermaid。')}}
 function mermaid(){if(!value)return;const ids=new Map(value.model.nodes.map((n,i)=>[n.id,'n'+i]));const clean=(s:string)=>s.replace(/["<>\n\r|]/g,' ');download(['flowchart LR',...value.model.nodes.map(n=>ids.get(n.id)+'["'+clean(n.title)+'"]'),...value.model.relations.map(r=>ids.get(r.source)+' -->|'+clean(r.label)+'| '+ids.get(r.target))].join('\n'),'architecture.mmd','text/plain')}
 const ready=value?.model;const domain=value?.domains.find(d=>d.id===topic);
 return <div className="reader knowledge-reader">
 <div className="knowledge-top"><button className="quiet" onClick={onBack}>← 所有视图</button><a href={'/versions/'+version+'?page=architecture'}>阅读架构正文 ↗</a><span>固定快照 {value?.commit?.slice(0,12)}</span></div>
 {!value?<p>正在读取架构知识…</p>:!ready?<section className="knowledge-empty"><h1>从源码建立项目认知</h1><p>先研究核心功能与边界，再组织架构总览。保留当前 Wiki 和依赖分析，研究完成后切换新知识版本。</p><button onClick={generate}>研究架构与更新正文</button></section>:<>
 <header className="knowledge-heading"><div><span className="section-kicker">架构总览 · 项目、职责与边界</span><h1>{ready.title}</h1><p>{ready.summary.text}</p></div><button className="outline" onClick={generate}>检查知识更新</button></header>
 <div className="knowledge-boundary"><b>本仓库的边界</b><p>{ready.boundary.text}</p><details><summary>查看依据</summary>{proofs(ready.boundary.evidence)}</details></div>
 <div className="knowledge-map-title"><h2>主要部分如何协作</h2><span>实线：源码或文档依据 · 虚线：推断 · 箭头文字说明关系</span></div>
 <div ref={chart}><ReactFlowProvider><Diagram knowledge={value!} onPick={pick} onRelation={r=>{window.dispatchEvent(new CustomEvent('wiki-chat-context',{detail:{context:{selection:('图中关系：'+r.label+'；'+r.explanation).slice(0,3000)},label:'关系：'+r.label}}));setRelation(r);setSelected(null);setTimeout(()=>detail.current?.scrollIntoView({behavior:'smooth',block:'nearest'}),0)}}/></ReactFlowProvider></div>
 <div className="knowledge-toolbar"><span>点击模块深入；点击关系核对依据。滚轮缩放，拖动画布。</span><button className="quiet" onClick={png}>PNG</button><button className="quiet" onClick={mermaid}>Mermaid</button><button className="quiet" onClick={()=>download(JSON.stringify(value,null,2),'knowledge.json','application/json')}>JSON</button></div>
 <div className="knowledge-topics"><h2>按你想理解的功能展开</h2><div>{value!.domains.map(d=><button className={topic===d.id?'active':''} key={d.id} onClick={()=>{const n=ready.nodes.find(n=>n.domains.includes(d.id));if(n){pick(n);setTopic(d.id)}}}>{d.title}</button>)}</div></div>
 <section ref={detail} className="knowledge-detail">{relation?<><span className="section-kicker">关系解释 · {relation.basis==='inferred'?'推断':relation.basis==='documented'?'文档声明':'源码依据'}</span><h2>{ready.nodes.find(n=>n.id===relation.source)?.title} → {ready.nodes.find(n=>n.id===relation.target)?.title}</h2><h3>{relation.label}</h3><p>{relation.explanation}</p>{proofs(relation.evidence)}</>:selected?<><div className="knowledge-detail-title"><div><span className="section-kicker">{selected.kind==='external'?'仓库外边界':'功能解读'}</span><h2>{domain?.title||selected.title}</h2></div><button className="outline" onClick={()=>onDependencies(value!.knowledge_id,selected.id)}>查看证据文件的静态依赖 →</button></div><p>{domain?.responsibility||selected.responsibility}</p>{domain?<><p className="knowledge-note">职责边界：{domain.boundary}</p><div className="knowledge-columns"><div><h3>如何工作</h3>{domain.steps.length?<ol>{domain.steps.map((c,i)=><li key={i}><p>{c.text}</p>{proofs(c.evidence)}</li>)}</ol>:<p>当前证据不足以形成处理步骤，以下为可核对的职责结论。</p>}</div><div><h3>实现依据</h3>{domain.claims.map((c,i)=><details key={i}><summary>{c.text}</summary>{proofs(c.evidence)}</details>)}{domain.gaps.length>0&&<><h3>还不能确定什么</h3>{domain.gaps.map((g,i)=><p className="knowledge-note" key={i}>{g}</p>)}</>}</div></div></>:proofs(selected.evidence)}<div className="knowledge-ask"><label htmlFor="knowledge-question">继续理解这个功能</label><textarea id="knowledge-question" value={question} onChange={e=>setQuestion(e.target.value)}/><button onClick={ask}>结合该功能的源码解读</button></div></>:<><h3>从问题进入，而不是从文件海洋开始</h3><p>总览只保留主要职责与协作。选择上方一个功能，查看处理步骤、关键源码和职责边界。</p></>}</section>
 <details className="knowledge-omissions"><summary>这张图做了哪些取舍？</summary>{ready.omissions.map((t,i)=><p key={i}>{t}</p>)}<p>职责模型是定向研究的结果，不代表逐行验证全部源码。原始依赖可从独立视图查阅。</p></details>
 </>}
 {error&&<p className="reader-error" role="alert">{error}</p>}
 {image&&<div className="export-preview"><button onClick={()=>setImage('')}>关闭预览</button><a href={image} download="architecture.png">保存图片</a><img src={image} alt="架构总览导出预览"/></div>}
 </div>
}
