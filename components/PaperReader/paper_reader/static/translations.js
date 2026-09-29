const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="paper-token"]').content;
const session = new URLSearchParams(location.search).get('session');
let preview = null, task = null, selected = '', timer = null, busy = false, signature = '';
let pdfDocument=null, pdfIdentity='', referenceSerial=0, referenceKey='';
const states = {pending:'待翻译',running:'正在请求',completed:'已生成',uncertain:'结果不确定',incomplete:'输出不完整',failed:'请求失败',queued:'等待下一段',paused:'已暂停'};
const errors = {translation_no_text:'当前论文没有可供译读的提取文本。',model_key_not_configured:'请先在 Reader 中配置可用模型；已有译文仍可阅读。',translation_plan_changed:'论文版本、模型设置或进度已变化，请刷新后确认新的调用范围。',translation_retry_confirmation_required:'请明确勾选重试未完成段落，结果不确定的请求可能重复计费。',translation_already_running:'该版本正在译读，请查看进度。',platform_configuration_unavailable:'平台模型配置暂不可用，已有译文仍可阅读。',formula_changed:'公式占位符未完整保留，请核对原文。',incomplete_output:'模型输出为空或被截断。',request_result_unknown:'请求结果不确定，未自动重试。',server_interrupted:'服务中断，未自动重发。',translation_interrupted:'执行中断，未自动重试。'};
Object.assign(errors,{provider_http_400:'提供商拒绝参数，请检查当前模型设置。',provider_http_401:'提供商拒绝密钥。',provider_http_429:'提供商速率或额度限制。',thinking_level_not_supported:'当前模型不支持此推理设置，请选择模型默认。',model_finish_max_tokens:'达到输出上限，已保留部分译文。',model_finish_length:'达到输出上限，已保留部分译文。',model_finish_safety:'提供商安全策略终止输出。'});
async function api(path, method='GET', data) {const r=await fetch(path,{method,headers:{'x-paper-token':token,'Content-Type':'application/json'},body:data?JSON.stringify(data):undefined});const body=await r.json();if(!r.ok)throw Error(errors[body.detail]||body.detail||'请求未完成');return body;}
function note(error) {$('notice').textContent=error?.message||String(error||'');}
function text(tag,value,cls){const e=document.createElement(tag);e.textContent=value;if(cls)e.className=cls;return e;}
function planView() {const p=preview?.plan;const active=task&&['running','queued'].includes(task.state);$('start').disabled=busy||!p||!p.remaining||['running','queued'].includes(p.state)||!!active;
 $('start').textContent=p&&!p.remaining?'本版已生成':p&&p.remaining<p.total?'继续未完成段落':'开始全文译读';$('stop').hidden=!active;$('retry-label').hidden=!p?.retry_count;
 $('plan-info').textContent=p?`模型：${p.model}；本版共 ${p.total} 段。本次最多 ${p.remaining} 次请求，每次输出上限 ${p.max_output_tokens} token，合计最多预留 ${p.output_bound} token。${p.retry_count?'其中 '+p.retry_count+' 段需要重新请求。':''}`:(errors[preview?.error]||preview?.error||'暂无可启动的译读方案。');
 const pages=preview?.pages||p?.pages||task?.pages||[];$('coverage').replaceChildren();for(const p of pages){if(p.issues?.length)$('coverage').append(text('li',`第 ${p.page} 页：`+p.issues.map(x=>({'empty_or_image_page_no_ocr':'文字很少或图像页，未进行 OCR','unmapped_glyphs':'存在无法映射字符','formula_layout_not_certified':'公式二维结构未核验','table_cells_not_structurally_verified':'表格结构未核验','column_order_heuristic_review_required':'分栏阅读顺序需核对','rotated_text_omitted_from_linear_reading':'部分旋转文字未纳入'}[x]||'页面解析存在限制')).join('；')));}
 if(!$('coverage').children.length)$('coverage').append(text('li','解析未报告上述问题，仍不代表图表和公式已经全部正确识别。'));
}
async function loadPreview() {preview=await api('/api/translations/preview?session='+encodeURIComponent(session));$('title').textContent=preview.name+' · 中文通读';$('tasks').replaceChildren();for(const item of preview.tasks){const config=JSON.parse(item.config);const option=text('option',config.model+' · '+item.created+' · '+(states[item.state]||item.state));option.value=item.id;$('tasks').append(option);}if(!preview.tasks.length){const o=text('option','尚无译读');o.value='';$('tasks').append(o);}
 if(!selected||!preview.tasks.some(x=>x.id===selected))selected=preview.tasks[0]?.id||'';$('tasks').value=selected;$('retry').checked=false;planView();pageChoices();renderReference();}
function render() {if(!task)return;pageChoices();
 $('progress').textContent=`${states[task.state]||task.state} · 已生成 ${task.completed}/${task.total} 段 · 累计请求预留 ${task.reserved_calls} 次${task.stop_requested&&['queued','running'].includes(task.state)?'；停止已请求，当前请求结束后保留结果':''}`;$('bar').max=task.total||1;$('bar').value=task.completed;
 $('version-note').textContent=(preview.parse_sha256!==task.parse_sha256?'这是旧解析版本的译文，原文和译文按当时版本保留。':'译文绑定当前解析版本。')+(preview.plan&&preview.plan.id!==task.id?' 当前设置对应另一个译读版本，开始按钮会创建或继续当前设置的版本。':'');
 $('export').hidden=false;$('export').href='/api/translations/'+task.id+'/export';renderPage();planView();}
function renderPage(){renderReference();if(!task)return;const p=Number($('pages').value);const current=task.segments.filter(s=>s.page===p);const next=JSON.stringify([task.id,p,current]);if(next===signature)return;signature=next;$('segments').replaceChildren();
 $('original').href='/api/documents/'+task.document+'/pdf#page='+p;
 for(const s of current){const pair=document.createElement('article');pair.className='pair';const original=document.createElement('div'),translation=document.createElement('div');original.append(text('h3',`提取文本（未核验）· 段 ${s.seq+1}`),text('div',s.source,'text'));translation.append(text('h3','中文（未核验）· '+(states[s.state]||s.state)),text('div',s.translated||'尚无译文','text'));if(s.error)translation.append(text('p',errors[s.error]||'本段未完成：'+s.error,'segment-state'));pair.append(original,translation);$('segments').append(pair);}}
async function loadTask(){clearTimeout(timer);if(!selected){task=null;planView();pageChoices();renderReference();return;}const id=selected;const result=await api('/api/translations/'+id);if(id!==selected)return;task=result;render();if(['queued','running'].includes(task.state))timer=setTimeout(()=>loadTask().catch(e=>{note(e);$('refresh').disabled=false;}),1500);else await loadPreview();}
$('start').onclick=async()=>{if(!preview?.plan||busy)return;busy=true;planView();note('');try{const result=await api('/api/translations','POST',{session,token:preview.plan.token,retry:$('retry').checked});selected=result.id;await loadPreview();await loadTask();}catch(e){note(e);await loadPreview().catch(()=>{});}finally{busy=false;planView();}};
$('stop').onclick=async()=>{try{await api('/api/translations/'+task.id+'/stop','POST',{});await loadTask();}catch(e){note(e);}};
$('refresh').onclick=async()=>{try{note('');await loadPreview();await loadTask();}catch(e){note(e);}};
$('tasks').onchange=()=>{selected=$('tasks').value;signature='';loadTask().catch(note);};$('pages').onchange=renderPage;

function pageChoices(){
 const previous=Number($('pages').value)||1;
 const pages=preview?.pages?.map(p=>p.page)||[];
 const values=[...new Set([...pages,...(task?.segments.map(s=>s.page)||[])])].sort((a,b)=>a-b);
 $('pages').replaceChildren();for(const page of values){const o=text('option','第 '+page+' 页');o.value=String(page);$('pages').append(o);}if(values.includes(previous))$('pages').value=String(previous);
}
async function renderReference(){
 const documentId=preview?.document;if(!documentId)return;
 const page=Number($('pages').value)||1;
 $('original').href='/api/documents/'+documentId+'/pdf#page='+page;
 const pageInfo=preview.pages?.find(p=>p.page===page);
 const labels={unmapped_glyphs:'无法映射字符',formula_layout_not_certified:'公式结构未核验',table_cells_not_structurally_verified:'表格结构未核验',column_order_heuristic_review_required:'分栏顺序需核对',empty_or_image_page_no_ocr:'图像页或文字不足，未做 OCR',rotated_text_omitted_from_linear_reading:'存在未纳入的旋转文字'};
 const issues=(pageInfo?.issues||[]).map(x=>labels[x]||'解析存在限制');
 $('quality-note').textContent=(issues.length?'本页：'+issues.join('；')+'。':'本页提取结果未经阅读质量核验。')+' 图、公式和表格以原 PDF 为准。下方文字可能错序或缺失；译文生成完成不表示解析正确。';
 if(!$('pdf-reference').open)return;
 const nextKey=JSON.stringify([documentId,page,$('reference-zoom').value,$('reference-scroll').clientWidth]);if(nextKey===referenceKey)return;referenceKey=nextKey;const serial=++referenceSerial;
 $('reference-status').textContent='正在读取原 PDF 页…';
 try{
  if(pdfIdentity!==documentId){
   const pdfjs=await import('./vendor/pdf.mjs');pdfjs.GlobalWorkerOptions.workerSrc='/static/vendor/pdf.worker.mjs';
   const loaded=await pdfjs.getDocument({url:'/api/documents/'+documentId+'/pdf',isEvalSupported:false}).promise;
   if(serial!==referenceSerial){loaded.destroy();return;}if(pdfDocument)pdfDocument.destroy();pdfDocument=loaded;pdfIdentity=documentId;
  }
  const original=await pdfDocument.getPage(page);if(serial!==referenceSerial)return;
  const scale=(Math.max(280,$('reference-scroll').clientWidth)/original.getViewport({scale:1}).width)*Number($('reference-zoom').value);
  const view=original.getViewport({scale}), density=Math.min(devicePixelRatio||1,2), canvas=document.createElement('canvas');
  canvas.width=Math.round(view.width*density);canvas.height=Math.round(view.height*density);canvas.style.width=view.width+'px';canvas.style.height=view.height+'px';canvas.setAttribute('aria-label','原 PDF 第 '+page+' 页');
  await original.render({canvasContext:canvas.getContext('2d'),viewport:view,transform:[density,0,0,density,0,0]}).promise;
  if(serial!==referenceSerial)return;$('reference-page').replaceChildren(canvas);$('reference-status').textContent='原 PDF 第 '+page+' 页 · 可横向滚动查看放大内容';
 }catch(error){if(serial===referenceSerial){referenceKey='';$('reference-page').replaceChildren();$('reference-status').textContent='原页显示失败，请使用“打开原 PDF 对照”。';}}
}
$('reference-zoom').onchange=renderReference;
$('pdf-reference').ontoggle=()=>{if($('pdf-reference').open)renderReference();};
let resizeTimer;window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(renderReference,200);});

if(!session)note('请从 Reader 选择论文后打开中文通读。');else loadPreview().then(loadTask).catch(note);
