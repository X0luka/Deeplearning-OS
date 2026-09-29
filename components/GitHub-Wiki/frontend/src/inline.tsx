import React, {useEffect, useState, useRef} from 'react';
import {createRoot} from 'react-dom/client';
import {ReactFlow, ReactFlowProvider, Background, Controls, MarkerType, BaseEdge,
  EdgeLabelRenderer, useReactFlow, type EdgeProps, type Node, type Edge} from '@xyflow/react';
import ELK, {type ElkNode} from 'elkjs/lib/elk-api';
import '@xyflow/react/dist/style.css';
import './inline.css';

type Proof = {id:string;path:string;start:number;end:number};
type Item = {label:string;basis:string;evidence:string[];explanation?:string;group?:string};
type Graph = {title:string;direction?:'RIGHT'|'DOWN';version:string;proofs:Proof[];
  nodes:(Item & {id:string})[];edges:(Item & {source:string;target:string})[]};
type Route = {path:string;label:string;position?:{x:number;y:number};inspect:()=>void};
const elk = new ELK({workerUrl:'/static/reader/elk-worker.min.js'});
const basisLabels:Record<string,string> = {source:'源码依据',documented:'文档声明',inferred:'推断',reading:'建议阅读顺序'};

function RoutedEdge({id,data,markerEnd,style}:EdgeProps){
  const route = data as unknown as Route;
  return <><BaseEdge id={id} path={route.path} markerEnd={markerEnd} style={style}/>
    {route.position && <EdgeLabelRenderer><button type="button" className="inline-edge-label nodrag nopan"
      onClick={route.inspect} aria-label={'查看关系依据：'+route.label}
      style={{transform:`translate(-50%,-50%) translate(${route.position.x}px,${route.position.y}px)`}}>
      {route.label}</button></EdgeLabelRenderer>}</>;
}
const edgeTypes = {routed:RoutedEdge};
const textWidth = (s:string) => Array.from(s).reduce((n,c)=>n+(c.charCodeAt(0)>255?14:7.5),0);
function dimensions(item:Item){
  const width = Math.min(245,Math.max(155,textWidth(item.label)/2+30));
  const lines = item.label.split('\n').reduce((n,s)=>n+Math.max(1,Math.ceil(textWidth(s)/(width-26))),0);
  return {width,height:Math.max(60,lines*23+24+(item.group?20:0))};
}

function Diagram({graph}:{graph:Graph}){
  const [nodes,setNodes]=useState<Node[]>([]),[edges,setEdges]=useState<Edge[]>([]);
  const [error,setError]=useState(''),[selected,setSelected]=useState<Item|null>(null);
  const [extent,setExtent]=useState({width:700,height:350}),[height,setHeight]=useState(350);
  const canvas=useRef<HTMLDivElement>(null),flow=useReactFlow();
  useEffect(()=>{
    let active=true;
    const direction=graph.direction||(graph.nodes.length<=4?'RIGHT':'DOWN');
    const ordered=[...graph.nodes].sort((a,b)=>(a.group||'').localeCompare(b.group||''));
    elk.layout({id:'root',layoutOptions:{'elk.algorithm':'layered','elk.direction':direction,
      'elk.edgeRouting':'ORTHOGONAL','elk.spacing.nodeNode':'22','elk.layered.spacing.nodeNodeBetweenLayers':'32'},
      children:ordered.map(n=>({id:n.id,...dimensions(n)})),
      edges:graph.edges.map((e,i)=>({id:'e'+i,sources:[e.source],targets:[e.target],
        labels:[{text:e.label,width:Math.max(70,Math.min(320,textWidth(e.label)+12)),height:Math.ceil(textWidth(e.label)/308)*20+4}]}))
    }).then((layout:ElkNode)=>{
      if(!active)return;
      setExtent({width:layout.width||700,height:layout.height||350});
      setNodes(graph.nodes.map(n=>{
        const placed=layout.children!.find(p=>p.id===n.id)!;
        return {id:n.id,position:{x:placed.x||0,y:placed.y||0},data:{label:<>{n.group&&<small className="inline-node-group">{n.group}</small>}<span>{n.label}</span></>},
          ariaLabel:'查看节点依据：'+n.label,sourcePosition:(direction==='RIGHT'?'right':'bottom') as any,
          targetPosition:(direction==='RIGHT'?'left':'top') as any,
          style:{...dimensions(n),display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',
            fontSize:14,padding:12,borderRadius:8,background:'var(--aos-subtle, #fff)',color:'var(--aos-text, #222)',border:'1px solid var(--aos-line, #bbb)'}};
      }));
      setEdges(graph.edges.map((e,i)=>{
        const route=layout.edges?.find(r=>r.id==='e'+i),s=route?.sections?.[0],label=route?.labels?.[0];
        const points=s?[s.startPoint,...(s.bendPoints||[]),s.endPoint]:[];
        return {id:'e'+i,source:e.source,target:e.target,type:'routed',data:{label:e.label,
          inspect:()=>setSelected(e),path:points.map((p,j)=>(j?'L':'M')+p.x+','+p.y).join(' '),
          position:label&&{x:(label.x||0)+(label.width||0)/2,y:(label.y||0)+(label.height||0)/2}},
          markerEnd:{type:MarkerType.ArrowClosed},style:{stroke:'var(--aos-text, #555)',strokeDasharray:['reading','inferred'].includes(e.basis)?'6 4':undefined}};
      }));
    }).catch(()=>{if(active)setError('图形布局暂不可用，下方仍可查看节点和关系。')});
    return()=>{active=false};
  },[graph]);
  useEffect(()=>{
    let timer:ReturnType<typeof setTimeout>;
    const resize=()=>{const width=canvas.current?.clientWidth||700;
      setHeight(Math.max(260,Math.min(740,extent.height*Math.min(1,(width-36)/extent.width)+50)));
      clearTimeout(timer);timer=setTimeout(()=>flow.fitView({padding:.08,maxZoom:1}),80)};
    const observer=new ResizeObserver(resize);if(canvas.current)observer.observe(canvas.current);resize();
    return()=>{observer.disconnect();clearTimeout(timer)};
  },[extent,flow,nodes]);
  return <>
    <div ref={canvas} className="inline-graph-canvas" style={{height}}>
      {nodes.length>0&&<ReactFlow nodes={nodes} edges={edges} edgeTypes={edgeTypes} minZoom={.1} maxZoom={2}
        nodesDraggable={false} nodesConnectable={false} zoomOnScroll={false} preventScrolling={false} aria-label={graph.title}
        onNodeClick={(_,n)=>setSelected(graph.nodes.find(x=>x.id===n.id)||null)}
        onEdgeClick={(_,e)=>setSelected(graph.edges[Number(e.id.slice(1))])}>
        <Background/><Controls showInteractive={false}/></ReactFlow>}
      {error&&<p role="alert">{error}</p>}
    </div>
    {selected&&<aside className="inline-proof-panel" aria-label="选中对象的源码依据">
      <div className="row"><strong>{selected.label}</strong><button type="button" onClick={()=>setSelected(null)}>关闭依据</button></div>
      <p>{selected.explanation||selected.label} · {basisLabels[selected.basis]}</p>
      <ul>{selected.evidence.map(key=>{const p=graph.proofs?.find(x=>x.id===key);return p&&<li key={key}>
        <a href={`/versions/${graph.version}/source?path=${encodeURIComponent(p.path)}&start=${p.start}&end=${p.end}#L${p.start}`} target="_blank" rel="noopener">{p.path}：{p.start}–{p.end}</a></li>})}</ul>
    </aside>}
    <details><summary>查看图中文字与关系</summary>
      <ul>{graph.nodes.map(n=><li key={n.id}><button className="inline-text-item" onClick={()=>setSelected(n)}>{n.label}</button></li>)}</ul>
      <ul>{graph.edges.map((e,i)=><li key={i}><button className="inline-text-item" onClick={()=>setSelected(e)}>
        {graph.nodes.find(n=>n.id===e.source)?.label} → {e.label} → {graph.nodes.find(n=>n.id===e.target)?.label}（{basisLabels[e.basis]}）
      </button></li>)}</ul>
    </details>
  </>;
}
document.querySelectorAll<HTMLElement>('.inline-graph-root').forEach(element=>{
  const data=document.getElementById(element.dataset.graph!);
  if(data)createRoot(element).render(<ReactFlowProvider><Diagram graph={JSON.parse(data.textContent||'{}')}/></ReactFlowProvider>);
});
