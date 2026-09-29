// @ts-ignore File library actions
import {createLibrary} from './library.mjs?v=actions-20260927';
// @ts-ignore Structured note groups
import {captureHtmlNote, groupNotes, encodeNoteSource, decodeNoteSource} from './note_groups.mjs?v=1';
// @ts-ignore Local reading selection UI
import {createSelectionNoteMenu, readArticleQuote, setupReadingDisclosure} from './reading_notes.mjs?v=1';
// @ts-ignore Local image viewer module
import {createReadingImageViewer} from './reading_images.mjs?v=1';
const readingImages = createReadingImageViewer();
// @ts-ignore Local reading navigation boundary
import {readingDocument, bindReadingLinks} from './reading_navigation.mjs?v=1';
// @ts-ignore -- browser cache version; runtime module is workspace.mjs
import {readLocal, writeLocal, splitPane, composer, readability, scrollSnapshot, restoreScroll} from './workspace.mjs?v=feedback-a1';
// @ts-ignore Local, pinned PDF.js distribution; never loads a remote script.
import * as pdfjs from './vendor/pdf.mjs';
// @ts-ignore pinned local modules
import { marked } from './vendor/marked.esm.js';
// @ts-ignore
import DOMPurify from './vendor/purify.es.mjs';
// @ts-ignore
import katex from './vendor/katex.mjs';
pdfjs.GlobalWorkerOptions.workerSrc = '/static/vendor/pdf.worker.mjs';
const $ = (id: string): any => document.getElementById(id);
const csrf = (document.querySelector('meta[name="paper-token"]') as HTMLMetaElement).content;
let session: any = null, doc: any = null, pdf: any = null, page = 1, zoom = 1, selection: any = null;
let currentAnchor: any = null, jobs: any[] = [], notes: any[] = [], editNote: any = null, noteSelection: any = null, noteExplanation = '';
let composeMode: string | null = null;
let modelCatalog: any[] = [], modelPreferences: any = {};
function markdown(text) { const equations = []; const protectedText = text.replace(/\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|(?<!\\)\$[^\n$]+?\$/g, part => { const key = `PAPERMATH${equations.length}TOKEN`; equations.push(part); return key; }); const e = element('div', undefined, 'markdown'); e.innerHTML = DOMPurify.sanitize(marked.parse(protectedText)); const walker = document.createTreeWalker(e, NodeFilter.SHOW_TEXT); const nodes: Text[] = []; while (walker.nextNode())
    nodes.push(walker.currentNode as Text); for (const node of nodes) {
    if (node.parentElement?.closest('code,pre')) {
        node.data = node.data.replace(/PAPERMATH(\d+)TOKEN/g, (_, i) => equations[Number(i)]);
        continue;
    }
    const matches = [...node.data.matchAll(/PAPERMATH(\d+)TOKEN/g)];
    if (!matches.length)
        continue;
    const fragment = document.createDocumentFragment();
    let start = 0;
    for (const match of matches) {
        fragment.append(document.createTextNode(node.data.slice(start, match.index)));
        const expression = equations[Number(match[1])];
        const display = expression.startsWith('$$') || (expression.charCodeAt(0) === 92 && expression[1] === '[');
        const width = expression.startsWith('$') && !display ? 1 : 2;
        const span = document.createElement('span');
        katex.render(expression.slice(width, -width), span, { displayMode: display, throwOnError: false, trust: false });
        fragment.append(span);
        start = match.index + match[0].length;
    }
    fragment.append(document.createTextNode(node.data.slice(start)));
    node.replaceWith(fragment);
} return e; }
let renderSerial = 0, loadingSerial = 0, pending = false, renderTask = null;
const errorNames: Record<string, string> = { assistant_enable_required: '请先启用当前论文的助手；已用完额度时需要重新确认。', model_key_not_configured: '未配置 DeepSeek API Key。原文阅读和笔记仍可使用。', selection_text_geometry_mismatch: '选区文字与原文定位不一致，请重新选择同一页中的连续段落。', document_version_changed: '解析版本已变化，请重新打开文档；旧引用不会被自动移到新位置。', note_changed_reload: '这条笔记已在其他页面修改，请重新打开后编辑。', model_budget_exhausted: '本次模型额度已用完，未自动重试。', worker_interrupted: '服务中断，问题已保留，未自动重试。', server_restarted_no_retry: '服务重启，问题已保留，未自动重试。', citation_not_in_supplied_evidence: '模型引用未通过校验，本次回答未作为有效结果展示。', model_incomplete_output: '模型输出不完整，未自动重试。' };
function notice(text) { $('notice').textContent = text; $('notice').hidden = false; }
$('notice').onclick = () => { $('notice').hidden = true; };
async function api(path, method = 'GET', body?: any) { const headers: Record<string, string> = { 'x-paper-token': csrf }; if (body && !(body instanceof FormData))
    headers['Content-Type'] = 'application/json'; const r = await fetch(path, { signal:AbortSignal.timeout(method==='GET'?10000:path==='/api/questions'?15000:120000), method, headers, body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined }); const data = await r.json(); if (!r.ok)
    throw new Error(errorNames[data.detail] || String(data.detail || '请求失败')); return data; }
function element<K extends keyof HTMLElementTagNameMap>(tag: K, text?: string, cls?: string): HTMLElementTagNameMap[K] { const e = document.createElement(tag); if (text)
    e.textContent = text; if (cls)
    e.className = cls; return e; }
function safe(fn) { return async (...args) => { try {
    await fn(...args);
}
catch (e) {
    notice(e.message);
} }; }
let libraryUI:any=null;
async function refreshLibrary() {
    const data=await api('/api/state');
    if (!libraryUI) libraryUI=createLibrary({container:$('sessions'),onOpen:safe(async id=>{if(session?.id!==id)await openSession(id);}),onAction:libraryAction});
    libraryUI.update(data.library,doc?.id);return data;
}
async function libraryAction(action:string,item:any,name?:string) {
    if(action==='trash'&&doc?.id===item.document)protectDrafts();
    try { await api('/api/library/'+item.document+'/actions','POST',{action,...(name!==undefined?{name}:{})}); }
    catch(error){ if(error.message==='library_name_invalid')throw new Error('名称无效，请去除特殊控制字符后重试。'); if(error.message==='library_document_busy')throw new Error('这份文件正在处理或回答问题，请在任务完成或暂停后再删除。');throw error; }
    if(action==='rename'&&doc?.id===item.document){doc.name=name;session.title=name;$('document-title').textContent=name;}
    if(action==='trash'&&doc?.id===item.document){writeLocal('paper-session',null);location.reload();return;}
    await refreshLibrary();
    if(action==='trash')notice('已移入回收站，可从文件库下方恢复。');
}
function modelState(model) { $('model-status').textContent = `${model.model} · ${model.configured ? '密钥已配置（实际可用性以请求为准）' : '未配置密钥'} · 剩余 ${model.remaining} / ${model.max_calls} 次`; if (model.reserved_output_tokens !== undefined)
    $('capability-hint').textContent = `已预留输入 ${model.reserved_input_bytes} 字节 / 输出 ${model.reserved_output_tokens} token。失败不自动重试。`; }
async function loadModels() { const data = await api('/api/models'); if (data.managed)
    return; modelCatalog = data.items; modelPreferences = data.settings; $('model-select').replaceChildren(); for (const m of modelCatalog) {
    const o = element('option', m.id + (m.configured ? '' : '（未配置）'));
    o.value = m.id;
    $('model-select').append(o);
} $('model-select').value = modelPreferences.model; $('output-limit').value = modelPreferences.max_output_tokens; $('thinking').value = modelPreferences.thinking; $('input-mode').value = modelPreferences.document_input; $('web-search').checked = modelPreferences.web_search; capabilities(); }
function capabilities() { const c = modelCatalog.find(m => m.id === $('model-select').value); if (!c)
    return; $('web-search').disabled = !c.search; if (!c.search)
    $('web-search').checked = false; $('input-mode').disabled = !c.pdf; if (!c.pdf)
    $('input-mode').value = 'text'; $('thinking').querySelector('[value="off"]').disabled = !c.thinking.includes('off'); if (!c.thinking.includes($('thinking').value))
    $('thinking').value = 'auto'; }
async function saveSettings() { modelPreferences = await api('/api/settings', 'PUT', { model: $('model-select').value, max_output_tokens: Number($('output-limit').value), thinking: $('thinking').value, document_input: $('input-mode').value, web_search: $('web-search').checked }); $('settings-status').textContent = '已保存，下次提问生效'; if (session)
    modelState((await api('/api/sessions/' + session.id)).model); }
async function openSession(id) { protectDrafts(); clearTimeout(pollTimer); $('quick-note').disabled = true; $('save-note').disabled = true; const serial = ++loadingSerial; const data = await api('/api/sessions/' + id); const documentData = await api('/api/documents/' + data.session.document); if (serial !== loadingSerial)
    return; saveDrafts(); renderSerial++; if (renderTask) renderTask.cancel(); pdf = null; session = data.session; const panel = document.getElementById('platform-model-settings'); if (panel)
    panel.dataset.session = session.id; window.dispatchEvent(new CustomEvent('llm-session', { detail: session.id })); resetHtmlReading(); doc = documentData; void loadDocumentReading(); publishAssistantContext(); jobs = data.jobs; notes = data.notes.map(decodeNoteSource); page = session.page; zoom = session.zoom; selection = null; currentAnchor = null; editNote = null; noteSelection = null; noteExplanation = ''; $('note-body').value = ''; $('note-context').textContent = ''; $('selection-bar').hidden = true; $('question-selection').hidden = true; writeLocal('paper-session', id); restoreDrafts(); if (innerWidth <= 1100) libraryOpen(false); $('document-title').textContent = doc.name; $('original').href = '/api/documents/' + doc.id + '/pdf'; $('original').hidden = false; $('full-translation').href='/translations?session='+encodeURIComponent(session.id); $('full-translation').hidden=false;  $('zoom').value = String(zoom); modelState(data.model); renderJobs(); renderNotes(); await refreshLibrary(); try {
    if (doc.state === 'html') { $('original').hidden=true; document.querySelector<HTMLButtonElement>('[data-reading-mode=pdf]').disabled=true; $('empty').hidden=true; $('pdf-page').hidden=true; await setReadingMode('zh'); } else { document.querySelector<HTMLButtonElement>('[data-reading-mode=pdf]').disabled=false;
    const loading = pdfjs.getDocument({ url: '/api/documents/' + doc.id + '/pdf', isEvalSupported: false });
    const opened = await loading.promise;
    if (serial !== loadingSerial) {
        opened.destroy();
        return;
    }
    pdf = opened;
    page = Math.min(page, pdf.numPages);
    $('page-total').textContent = String(pdf.numPages);
    $('page-number').max = String(pdf.numPages);
    $('empty').hidden = true;
    $('pdf-page').hidden = false;
    await renderPage();
    }
}
catch (e) {
    $('empty').hidden = false;
    $('pdf-page').hidden = true;
    notice('PDF预览失败，可点击“打开原件”查看。' + e.message);
} $('quick-note').disabled = false; $('save-note').disabled = false; watchPending();
if(location.hash.startsWith('#assistant-anchor=')){const anchor=JSON.parse(decodeURIComponent(location.hash.slice('#assistant-anchor='.length)));history.replaceState(null,'',location.pathname+location.search);await locate(anchor);}
}
async function renderPage(preserve = false) {
    const area = $('pdf-scroll'), oldHeight = $('pdf-page').clientHeight || 1, oldWidth = $('pdf-page').clientWidth || 1;
    const areaRect = area.getBoundingClientRect(), pageRect = $('pdf-page').getBoundingClientRect();
    const anchorY = (areaRect.top + area.clientHeight / 2 - pageRect.top) / oldHeight;
    const anchorX = (areaRect.left + area.clientWidth / 2 - pageRect.left) / oldWidth;
    if (!pdf)
        return;
    const serial = ++renderSerial;
    if (renderTask) {
        renderTask.cancel();
        try {
            await renderTask.promise;
        }
        catch { }
        renderTask = null;
    }
    const p = await pdf.getPage(page);
    if (serial !== renderSerial)
        return;
    if (fitMode !== 'custom' && area.clientWidth) {
        const base = p.getViewport({scale: 1}); const style = getComputedStyle(area);
        const width = (area.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)) / base.width;
        const height = (area.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom)) / base.height;
        zoom = Math.max(.1, Math.min(3, fitMode === 'page' ? Math.min(width, height) : width));
    }
    let actual = $('zoom').querySelector('[data-actual]'); if (!actual) { actual = element('option'); actual.dataset.actual = 'true'; $('zoom').append(actual); }
    actual.value = String(zoom); actual.textContent = `${Math.round(zoom * 100)}%${fitMode === 'custom' ? '' : fitMode === 'width' ? ' · 适宽' : ' · 整页'}`; actual.selected = true;
    $('fit-width').setAttribute('aria-pressed', String(fitMode === 'width')); $('fit-page').setAttribute('aria-pressed', String(fitMode === 'page'));
    const viewport = p.getViewport({ scale: zoom });
    const canvas = $('pdf-canvas');
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.floor(viewport.width * ratio);
    canvas.height = Math.floor(viewport.height * ratio);
    canvas.style.width = viewport.width + 'px';
    canvas.style.height = viewport.height + 'px';
    $('pdf-page').style.width = viewport.width + 'px';
    $('pdf-page').style.height = viewport.height + 'px';
    $('text-layer').replaceChildren();
    $('highlights').replaceChildren();
    renderTask = p.render({ canvasContext: canvas.getContext('2d'), viewport, transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0] });
    try {
        await renderTask.promise;
    }
    catch (e) {
        if (e.name === 'RenderingCancelledException')
            return;
        throw e;
    }
    if (serial !== renderSerial)
        return;
    renderTask = null;
    const parsed = doc.parsed?.pages.find((v) => v.page === page);
    const blocks = doc.parsed?.blocks.filter((b) => b.page === page) || [];
    const sx = viewport.width / (parsed?.width || viewport.width), sy = viewport.height / (parsed?.height || viewport.height);
    const measure = document.createElement('canvas').getContext('2d');
    let lineIndex = 0;
    if (['paper-content-1','pdf-metadata-1'].includes(doc.parsed?.version)) {
        const text = await p.getTextContent();
        for (const item of text.items) {
            if (!('str' in item) || !item.str.trim()) continue;
            const transform = pdfjs.Util.transform(viewport.transform, item.transform);
            const height = Math.hypot(transform[2], transform[3]);
            const span = element('span', item.str, 'text-line');
            const x=transform[4], y=transform[5]-height, width=item.width*zoom;
            span.dataset.box=JSON.stringify([x/zoom,y/zoom,(x+width)/zoom,(y+height)/zoom]);
            span.style.left=x+'px';span.style.top=y+'px';span.style.fontSize=height+'px';span.style.height=height+'px';
            measure.font=height+'px serif';span.style.fontFamily='serif';
            span.style.transform=`scaleX(${width/Math.max(1,measure.measureText(item.str).width)})`;
            $('text-layer').append(span);$('text-layer').append(document.createTextNode(' '));
        }
    }
    for (const block of ['paper-content-1','pdf-metadata-1'].includes(doc.parsed?.version) ? [] : blocks) {
        for (const line of block.lines) {
            const span = element('span', line.text, 'text-line');
            const [x0, y0, x1, y1] = line.bbox;
            const height = (y1 - y0) * sy;
            span.dataset.index = String(lineIndex++);
            span.dataset.box = JSON.stringify(line.bbox);
            span.style.left = x0 * sx + 'px';
            span.style.top = y0 * sy + 'px';
            span.style.fontFamily = 'serif';
            span.style.fontSize = height + 'px';
            measure.font = `${height}px serif`;
            span.style.transform = `scaleX(${(x1 - x0) * sx / Math.max(1, measure.measureText(line.text).width)})`;
            span.style.height = height + 'px';
            $('text-layer').append(span);
            $('text-layer').append(document.createTextNode('\n'));
        }
    }
    const issues = parsed?.issues || [];
    $('parse-warning').hidden = doc.state === 'ready' && !issues.length;
    $('parse-warning').textContent = doc.state !== 'ready' ? '文本解析未完成。原始PDF仍可阅读，暂不能据此进行可靠问答。' : issues.includes('unmapped_glyphs') ? '本页存在无法识别的字符。公式请对照原件，必要时在问题中补充公式文本。' : issues.includes('empty_or_image_page_no_ocr') ? '本页未提取到足够文字，暂不支持扫描件识别。' : issues.includes('column_order_heuristic_review_required') ? '本页采用双栏阅读顺序；选段后请核对文字。' : '公式或表格结构尚未认证，请结合原文阅读。';
    $('page-number').value = String(page);
    paintAnchor();
    if (preserve) { const nextRect = $('pdf-page').getBoundingClientRect(); area.scrollTop += nextRect.top + anchorY * viewport.height - areaRect.top - area.clientHeight / 2; area.scrollLeft += nextRect.left + anchorX * viewport.width - areaRect.left - area.clientWidth / 2; }
    await api('/api/sessions/' + session.id + '/position', 'PUT', { page, zoom });
    $('pdf-page').dataset.page = String(page);
    $('pdf-page').dataset.zoom = String(zoom);
}
function paintAnchor() { if (!currentAnchor || currentAnchor.page !== page)
    return; const parsed = doc.parsed?.pages.find((p) => p.page === page); if (!parsed)
    return; const sx = $('pdf-page').clientWidth / parsed.width, sy = $('pdf-page').clientHeight / parsed.height; for (const b of currentAnchor.line_boxes || [currentAnchor.bbox]) {
    const h = element('div', undefined, 'highlight');
    h.style.left = b[0] * sx + 'px';
    h.style.top = b[1] * sy + 'px';
    h.style.width = (b[2] - b[0]) * sx + 'px';
    h.style.height = (b[3] - b[1]) * sy + 'px';
    $('highlights').append(h);
} }
async function locate(anchor) {
    if (anchor.document && anchor.document !== doc.id) throw new Error('引用不属于当前文档');
    if (anchor.note) {
        const note = notes.find(n => n.id === anchor.note);
        if (!note) throw new Error('这条笔记已不可用；回答中保留了当时的引用。');
        tab('notes'); setView('assistant', false);
        const target = document.querySelector<HTMLElement>('[data-note-id="'+CSS.escape(anchor.note)+'"]');
        target?.scrollIntoView({block:'center'});
        target?.animate([{outline:'2px solid currentColor'},{outline:'2px solid transparent'}],{duration:2500});
        if (anchor.note_revision && String(note.revision) !== String(anchor.note_revision)) notice('笔记已有新版本，当前显示最新笔记；回答引用的是保存时的版本。');
        return;
    }
    if (anchor.target?.kind === 'html') {
        returnView = {view: document.body.dataset.view, tab: $('notes-panel').hidden ? 'chat' : 'notes'};
        $('return-answer').hidden = false; setView('reader', false);
        await setReadingMode('bilingual');
        if (readingBundle?.revision !== anchor.target.revision) {
            window.open('/reading-content/' + encodeURIComponent(anchor.target.revision) + '#content-' + encodeURIComponent(anchor.target.block), '_blank', 'noopener');
            return;
        }
        const frame = $('html-reading-frame');
        for (let i=0;i<40;i++) {
            const target=frame.contentDocument?.documentElement.dataset.readingView==='bilingual' ? frame.contentDocument.querySelector('[data-content-id="'+CSS.escape(anchor.target.block)+'"]') : null;
            if(target){target.scrollIntoView({block:'center'});target.animate([{outline:'2px solid currentColor'},{outline:'2px solid transparent'}],{duration:2500});return;}
            await new Promise(resolve=>setTimeout(resolve,50));
        }
        throw new Error('原文段落暂未加载，请重试');
    }
    if (readingMode !== 'pdf') await setReadingMode('pdf'); if (anchor.document && anchor.document !== doc.id)
    throw new Error('引用不属于当前文档'); if (anchor.target?.kind !== 'pdf' && anchor.parse_sha256 && anchor.parse_sha256 !== doc.parsed?.parse_sha256)
    throw new Error(errorNames.document_version_changed); returnView = {view: document.body.dataset.view, tab: $('notes-panel').hidden ? 'chat' : 'notes'}; $('return-answer').hidden = false; setView('reader', false); currentAnchor = anchor; page = anchor.page; await renderPage(); const first = $('highlights').firstElementChild; if (first)
    first.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
function clearSelection() { selection = null; composeMode = null; $('discussion-scope').value = 'paper'; $('selection-bar').hidden = true; $('question-selection').hidden = true; window.getSelection()?.removeAllRanges(); saveDrafts(); }
function capturePdfSelection() { const selected = window.getSelection(); if (!selected || selected.isCollapsed || !selected.rangeCount)
    return; const range = selected.getRangeAt(0); if (!$('text-layer').contains(range.startContainer) || !$('text-layer').contains(range.endContainer)) {
    notice('一次请选择同一页中的连续文字。');
    return;
} const spans: HTMLSpanElement[] = Array.from($('text-layer').querySelectorAll('.text-line')); const hits = spans.filter(s => range.intersectsNode(s)); if (!hits.length)
    return; const text = selected.toString().trim(); if (!text || text.length > 12000) {
    notice('选段过长，请缩小到一个段落。');
    return;
} const boxes = hits.map(s => JSON.parse(s.dataset.box)); selection = { page, bbox: [Math.min(...boxes.map(b => b[0])), Math.min(...boxes.map(b => b[1])), Math.max(...boxes.map(b => b[2])), Math.max(...boxes.map(b => b[3]))], text }; $('selection-bar').hidden = false; $('discussion-scope').value = 'selection'; $('selection-label').textContent = `第 ${page} 页 · 已选 ${text.length} 字符`; $('question-selection').hidden = false; $('question-selection').textContent = text; saveDrafts(); return selection; }
$('text-layer').addEventListener('mouseup', event => { if (event.button === 0) setTimeout(capturePdfSelection, 0); });

function renderJobs() { const snapshot = scrollSnapshot($('messages')); if (lastJobSession !== session?.id) snapshot.following = true; const signature = JSON.stringify(jobs); const changed = signature !== lastJobs; if (!changed && lastJobSession === session?.id) return; lastJobs = signature; lastJobSession = session?.id; const expanded = new Set(Array.from($('messages').querySelectorAll('details[open]')).map((e: HTMLElement) => e.dataset.job)); $('messages').replaceChildren(); for (const job of jobs) {
    $('messages').append(element('div', job.request.question, 'question'));
    const wrap = element('article', undefined, 'answer');
    if (job.state !== 'completed') {
        if (job.answer?.markdown)
            wrap.append(markdown(job.answer.markdown));
        wrap.append(element('p', job.state === 'failed' || job.state === 'interrupted' ? `回答未完成：${errorNames[job.error] || job.error || job.state}` : (job.answer?.stage || '正在准备论文内容…'), 'job-status' + (['failed', 'interrupted'].includes(job.state) ? ' failed' : '')));
    }
    else {
        const answer = job.answer;
        if (answer.markdown) {
            wrap.append(answerMarkdown(answer));
            for (const source of answer.external_sources || []) {
                if (!/^https?:\/\//i.test(source.uri || ''))
                    continue;
                const a = element('a', '外部来源：' + (source.title || source.uri));
                a.href = source.uri;
                a.target = '_blank';
                a.rel = 'noopener noreferrer';
                wrap.append(a);
            }
        }
        let anchorIndex = 0;
        for (const c of answer.markdown ? [] : answer.claims || []) {
            const p = element('p');
            p.append(element('span', c.kind === 'paper' ? '原文依据' : c.kind === 'background' ? '补充背景' : '尚不能确认', 'kind'));
            p.append(document.createTextNode(c.text));
            for (const cite of c.citations || []) {
                const anchor = answer.anchors?.[anchorIndex++];
                if (!anchor)
                    continue;
                const b = element('button', anchor.target?.kind === 'html' ? '原文段落' : `第 ${anchor.page} 页`, 'cite');
                b.title = cite.quote || anchor.quote;
                b.onclick = safe(() => locate(anchor));
                p.append(b);
            }
            wrap.append(p);
        }
        if (answer.limitations?.length)
            wrap.append(element('div', answer.limitations.join('\n'), 'limits'));
        const save = element('button', '保存这条解释到笔记', 'answer-save');
        save.onclick = safe(() => startNote({explanation: answer.markdown || (answer.claims || []).map(c => c.text).join('\n\n'), selection: job.request.selection || null}));
        const copy = element('button', '复制回答', 'answer-save');
        copy.onclick = safe(async () => { await navigator.clipboard.writeText(answer.markdown || (answer.claims || []).map(c => c.text).join('\n\n')); notice('回答已复制'); });
        wrap.append(copy);
        wrap.append(save);
    }
    const coverage = job.context?.coverage;
    if (coverage) {
        const details = element('details', undefined, 'coverage'); details.dataset.job = job.id; details.open = expanded.has(job.id); details.append(element('summary', '模型与原文覆盖详情')); details.append(element('small', `${coverage.model || '历史模型'} · ${coverage.input_mode === 'pdf' ? '已附原始PDF＋' : ''}${coverage.all_extracted_text ? '全部已提取文本' : '部分文本'} ${coverage.included_blocks}/${coverage.total_blocks} 块；不代表公式已核实`)); wrap.append(details);
    }
    $('messages').append(wrap);
} pending = jobs.some(j => ['queued', 'running'].includes(j.state)); $('cancel-question').hidden = !pending; $('send').disabled = pending || submitting; $('send').textContent = pending ? '回答中…' : '发送 ↑'; $('followup').textContent = jobs.some(j => j.state === 'completed') ? '连续追问 · 已保留本会话上下文' : '单篇问答 · 中文回答'; restoreScroll($('messages'), snapshot, changed || !$('latest').hidden, $('latest')); }
let pollTimer = null;
function watchPending() { clearTimeout(pollTimer); if (!session || !pending)
    return; const id = session.id; pollTimer = setTimeout(async () => { try {
    const data = await api('/api/sessions/' + id);
    if (session?.id !== id)
        return;
    jobs = data.jobs;
    renderJobs();
    modelState(data.model);
    watchPending();
}
catch (e) {
    if(session?.id!==id)return;notice('状态读取中断，正在重新查询；不会重新提交问题。');watchPending();
} }, 1500); }
async function sendQuestion(text: string, mode?: string) { if (!session) {
    notice('请先打开一篇PDF。');
    return;
} if (pending || submitting) {
    notice('当前问题尚未完成，请等待结果。');
    return;
} if (!text.trim())
    return; const previous = [...jobs].reverse().find(j => j.state === 'completed'); const request: any = { document: doc.id, mode: mode || composeMode || ($('discussion-scope').value === 'selection' && selection ? 'selection' : 'fulltext'), question: text }; if (selection && request.mode !== 'fulltext')
    request.selection = selection; if (previous)
    request.previous = previous.id; const sentSession = session.id, sentText = $('question').value; let body={session:sentSession,parse_sha256:doc.parsed?.parse_sha256,question:request};const slot='paper-pending-'+sentSession, intent=JSON.stringify({...body,question:{...request,previous:undefined}}), previousRequest=readLocal(slot,null);const retry=previousRequest?.intent===intent;const requestKey=retry?previousRequest.key:crypto.randomUUID();if(retry)body=previousRequest.body;if(!writeLocal(slot,{intent,body,key:requestKey})){notice('无法保存发送标识，草稿保留，请恢复浏览器存储后发送。');return;}submitting = true; $('send').disabled = true; saveDrafts(); try {
    await api('/api/questions', 'POST', {...body,request_key:requestKey});writeLocal(slot,null);
    const saved = readLocal(draftKey(sentSession), {}); if (saved.question === sentText) { saved.question = ''; saved.selection = null; saved.scope = 'paper'; saved.mode = null; writeLocal(draftKey(sentSession), saved); }
    if (session?.id !== sentSession) return;
    pending=true;watchPending();
    if ($('question').value === sentText) { $('question').value = ''; clearSelection(); resizeComposer(); }
    const data = await api('/api/sessions/' + sentSession); if (session?.id !== sentSession) return;
    jobs = data.jobs;
    renderJobs();
    watchPending();
}
catch(error){if(session?.id===sentSession)notice('未能确认发送状态：'+error.message+'；未接收的草稿保留，再次发送会复用请求标识。');}
finally {
    submitting = false;
    if (!pending)
        $('send').disabled = false;
} }
function tab(which, reveal = true) { if (reveal && (innerWidth <= 760 || document.body.dataset.view === 'reader')) setView('assistant', false); $('chat-tab').setAttribute('aria-pressed', String(which === 'chat')); $('notes-tab').setAttribute('aria-pressed', String(which === 'notes'));  $('chat-panel').hidden = which !== 'chat'; $('notes-panel').hidden = which !== 'notes'; $('chat-tab').classList.toggle('active', which === 'chat'); $('notes-tab').classList.toggle('active', which === 'notes'); }
function renderNotes() {
    $('notes-list').replaceChildren(); $('notes-count').textContent=String(notes.length);
    if (!notes.length) return;
    $('notes-list').append(element('h3','已保存的笔记'));
    for (const group of groupNotes(notes)) {
        const section=element('section',undefined,'note-group');
        section.append(element('h4',group.selection ? `选段 · ${group.notes.length} 条笔记` : '论文笔记'));
        if (group.selection) {
            section.append(element('blockquote',group.selection.text));
            const add=element('button','为此选段添加笔记','note-group-add');
            add.onclick=safe(()=>startNote({selection:group.selection})); section.append(add);
            if (group.selection.kind !== 'html') {
                const source=group.notes[0], link=element('button',`原文第 ${source.selection.page} 页`);
                link.onclick=safe(()=>locate({...source.selection,document:source.document,parse_sha256:source.parse_sha256})); section.append(link);
            }
        }
        for (const note of group.notes) {
            const item=element('article',undefined,'note'); item.dataset.noteId=note.id;
            item.append(element('small',note.updated.slice(0,16).replace('T',' ')),element('p',note.body||'（未填写个人笔记）'));
            if (note.explanation) {const details=element('details');details.append(element('summary','已保存的解释'),element('p',note.explanation));item.append(details);}
            const edit=element('button','编辑笔记');edit.onclick=safe(()=>startNote({edit:note,selection:note.selection,explanation:note.explanation,body:note.body}));item.append(edit);section.append(item);
        }
        $('notes-list').append(section);
    }
}
$('upload').onchange = safe(async () => { const file = $('upload').files[0]; if (!file)
    return; const form = new FormData(); form.append('file', file); notice('正在导入并解析PDF…'); const result = await api('/api/documents', 'POST', form); $('notice').hidden = true; await openSession(result.session.id); $('upload').value = ''; const task = await api('/api/reading-jobs/document','POST',{session:result.session.id}); watchReadingTask(task.id); });
$('prev').onclick = safe(async () => { if (page > 1) {
    page--;
    clearSelection();
    await renderPage();
} });
$('next').onclick = safe(async () => { if (pdf && page < pdf.numPages) {
    page++;
    clearSelection();
    await renderPage();
} });
$('page-number').onchange = safe(async () => { if (pdf) {
    page = Math.min(pdf.numPages, Math.max(1, Number($('page-number').value) || 1));
    clearSelection();
    await renderPage();
} });
$('zoom').onchange = safe(async () => { fitMode = 'custom'; writeLocal('paper-fit-mode', fitMode); zoom = Number($('zoom').value); await renderPage(true); });
$('clear-selection').onclick = clearSelection;
$('explain').onclick = () => draftPrompt('请结合上下文解释所选段落，说明术语、指代和它在论文中的作用。', 'selection');
$('translate').onclick = () => draftPrompt('请将所选段落准确翻译成中文，结合上下文处理术语和指代，保留必要英文术语。', 'selection');
$('formula').onclick = () => draftPrompt('请解释所选公式的符号、运算关系和文中用途；如果提取不清楚，请指出缺口。', 'formula');
$('selection-note').onclick = safe(() => startNote({selection}));
$('question-form').onsubmit = (event) => { event.preventDefault(); safe(() => sendQuestion($('question').value))(); };
$('chat-tab').onclick = () => tab('chat');
$('notes-tab').onclick = () => tab('notes');
$('new-note').onclick = safe(() => startNote());
$('save-note').onclick = safe(async () => {
    if (!session) throw new Error('请先打开论文');
    const id = session.id, slot = noteSlot, value = noteValue();
    if (!value.body.trim() && !value.explanation) throw new Error('请先写下你的笔记。');
    const payload = encodeNoteSource({session: id, body: value.body, selection: value.selection, explanation: value.explanation, parse_sha256: doc.parsed?.parse_sha256 || null, revision: value.edit?.revision});
    $('save-note').disabled = true;
    try {
        const saved = await api(value.edit ? '/api/notes/' + value.edit.id : '/api/notes', value.edit ? 'PUT' : 'POST', payload);
        const draft = readLocal(draftKey(id), {});
        if (JSON.stringify(draft.notes?.[slot]) === JSON.stringify(value)) { delete draft.notes[slot]; writeLocal(draftKey(id), draft); }
        if (session?.id !== id) return;
        notes = (await api('/api/notes?session=' + id)).items.map(decodeNoteSource); if (session?.id !== id) return;
        renderNotes();
        if (noteSlot === slot && JSON.stringify(noteValue()) === JSON.stringify(value)) { delete noteDrafts[slot]; const current=notes.find(n=>n.id===saved.id); if(current){const key='edit-'+current.id; noteDrafts[key]={edit:current,selection:current.selection,explanation:current.explanation,body:current.body};loadNoteDraft(key);} saveDrafts(); }
        $('note-status').textContent = '笔记已保存';
    } finally { $('save-note').disabled = false; }
});
document.querySelectorAll<HTMLElement>('[data-prompt]').forEach((b) => b.onclick = () => { draftPrompt(b.dataset.prompt); });
safe(async () => { await loadModels(); const state = await refreshLibrary(); const requestedDoc=new URLSearchParams(location.search).get('assistant_document'); const requestedSession=state.sessions.find(s=>s.document===requestedDoc)?.id; const recent = requestedSession || readLocal('paper-session', null) || (() => { try { return localStorage.getItem('paper-session'); } catch { return null; } })(); if (state.sessions.length)
    await openSession(state.sessions.some((s) => s.id === recent) ? recent : state.sessions[0].id); })();
$('quick-note').onclick = () => { $('new-note').click(); tab('notes'); $('note-body').focus(); };
$('model-select').onchange = safe(async () => { capabilities(); await saveSettings(); });
$('save-settings').onclick = safe(saveSettings);
$('cancel-question').onclick = safe(async () => { const current = jobs.find(j => ['queued', 'running'].includes(j.state)); if (current) {
    await api('/api/questions/' + current.id + '/cancel', 'POST', {});
    notice('已请求停止；已提交的请求可能仍产生费用。');
} });
Object.assign(errorNames, { assistant_enable_required: '所选模型未配置，或当前论文额度已用完。请查看模型设置。', model_key_not_configured: '所选提供商密钥未配置。', user_cancelled: '已停止，已收到内容保留。', provider_http_401: '提供商拒绝密钥，请检查服务端配置。', provider_http_403: '提供商拒绝访问，可能涉及账号或地区权限。', provider_http_404: '此账号或端点无法使用所选模型。', provider_http_429: '提供商额度或速率限制，未自动重试。', provider_http_400: '提供商拒绝请求参数；请调整模型设置。', pdf_input_budget_use_text: '原始PDF超过当前内联预算，请切换提取文本输入。', ReadTimeout: '模型响应超时，已收到内容保留，未自动重试。', ConnectError: '无法连接模型提供商，请检查网络或代理。' });
window.addEventListener("llm-settings-saved", safe(async () => { if (session)
    modelState((await api("/api/sessions/" + session.id)).model); }));

// Local workspace state is separate from saved notes and model history.
type NoteDraft = {body?: string; selection?: any; explanation?: string; edit?: any};
let noteSlot = 'new', noteDrafts: Record<string, NoteDraft> = {}, draftFailed = false, submitting = false;
let fitMode = readLocal('paper-fit-mode', 'width'), lastJobs = '', lastJobSession = null;
let returnView = null;
const draftKey = id => 'paper-draft-v1-' + id;
function noteIsDirty(value) { return value.edit ? value.body !== (value.edit.body || '') : !!(value.body || value.selection || value.explanation); }
function noteValue() { return {body: $('note-body').value, selection: noteSelection, explanation: noteExplanation, edit: editNote}; }
function saveDrafts() {
    if (!session) return true;
    const value = noteValue();
    if (noteIsDirty(value)) noteDrafts[noteSlot] = value;
    else delete noteDrafts[noteSlot];
    const hasContent = $('question').value || selection || Object.keys(noteDrafts).length;
    const ok = writeLocal(draftKey(session.id), {question: $('question').value, selection, scope: $('discussion-scope').value, mode: composeMode, parse: doc?.parsed?.parse_sha256, notes: noteDrafts, active: noteSlot});
    draftFailed = !ok && !!hasContent;
    $('draft-status').textContent = ok ? '草稿保存在本机' : '草稿暂不能保存，请先复制内容';
    if (!ok && hasContent) notice('浏览器存储不可用或已满。草稿仍在当前页面，请复制后再切换或关闭。');
    updateDraftList(); return ok || !hasContent;
}
function protectDrafts() { if (!saveDrafts()) throw new Error('草稿保存失败，已保留当前编辑内容。请先复制，再清除草稿后切换。'); }
function updateDraftList() {
    const select = $('note-drafts'); select.replaceChildren();
    const blank = element('option', '选择本机笔记草稿'); blank.value = ''; select.append(blank);
    for (const [key, value] of Object.entries(noteDrafts)) {
        const option = element('option', (value.edit ? '编辑 · ' : '新笔记 · ') + (value.body || value.selection?.text || value.explanation || '未命名').slice(0, 32));
        option.value = key; select.append(option);
    }
    select.value = noteDrafts[noteSlot] ? noteSlot : '';
    const count=Object.keys(noteDrafts).filter(key=>key!==noteSlot).length; $('note-draft-tools').hidden=!count; $('note-draft-count').textContent=String(count);
}
function loadNoteDraft(slot) {
    noteSlot = slot; const value = noteDrafts[slot] || {};
    editNote = value.edit || null; noteSelection = value.selection || null; noteExplanation = value.explanation || '';
    $('note-body').value = value.body || ''; $('note-context').textContent = noteSelection?.text || '';
    $('note-source').hidden=!noteSelection?.text; $('note-explanation').textContent=noteExplanation; $('note-explanation-wrap').hidden=!noteExplanation;
    $('note-add-same').hidden=!(editNote && noteSelection); $('save-note').textContent=editNote?'保存修改':'保存笔记';
    $('note-body').placeholder=noteSelection?'写下你对这段原文的理解…':'写下关于这篇论文的笔记…';
    const current = notes.find(n => n.id === editNote?.id);
    $('note-status').textContent = editNote && (!current || current.revision !== editNote.revision) ? '笔记已有新版本；你的草稿已保留，请核对后再编辑。' : noteDrafts[slot] ? '已恢复本机草稿 · 尚未保存为笔记' : '';
    updateDraftList();
}
function restoreDrafts() {
    const value = readLocal(draftKey(session.id), {});
    $('question').value = value.question || ''; selection = value.selection || null; composeMode = value.mode || null;
    if (value.parse && value.parse !== doc?.parsed?.parse_sha256 && selection) {
        selection = null; composeMode = null; notice('文档解析版本已变化，问题草稿已保留，请重新选择原文。');
    }
    $('discussion-scope').value = selection ? (value.scope || 'selection') : 'paper';
    $('question-selection').hidden = !selection; $('question-selection').textContent = selection?.text || '';
    noteDrafts = value.notes || {}; loadNoteDraft(value.active || 'new'); resizeComposer();
}
function startNote(value: NoteDraft = {}) {
    protectDrafts(); noteSlot = value.edit ? 'edit-' + value.edit.id : crypto.randomUUID();
    if (!noteDrafts[noteSlot]) noteDrafts[noteSlot] = value;
    loadNoteDraft(noteSlot); saveDrafts(); if(!value.edit) $('note-status').textContent='草稿自动保留 · 尚未保存为笔记'; tab('notes'); $('note-body').focus();
}
function draftPrompt(text, mode = null) {
    if ((window as any).platformAssistant) { tab('chat'); (window as any).platformAssistant.draft(text, selection?.text || ''); return; }
    // Append to a user's unfinished question instead of replacing it.
    $('question').value = [$('question').value.trim(), text].filter(Boolean).join('\n\n').slice(0, 4000);
    composeMode = mode; tab('chat'); resizeComposer(); saveDrafts(); $('question').focus();
}
function setView(view, persist = true) {
    document.body.dataset.view = view;
    document.body.dataset.mobile = view === 'reader' ? 'reader' : 'assistant';
    document.querySelectorAll<HTMLElement>('[data-workspace-view]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.workspaceView === view)));
    if (persist) writeLocal('paper-view', view);
    split.update();
}
const split = splitPane({handle: $('workspace-splitter'), panel: $('assistant'), key: 'paper-assistant-width', available: () => document.querySelector('main').clientWidth - (innerWidth > 1100 && !document.body.classList.contains('library-collapsed') ? 190 : 0) - 10, apply: width => document.documentElement.style.setProperty('--assistant-width', width + 'px')});
document.querySelectorAll<HTMLElement>('[data-workspace-view]').forEach(b => b.onclick = () => {
    setView(b.dataset.workspaceView); if (b.dataset.workspaceView === 'assistant') tab('chat');
});
$('view-notes').onclick = () => tab('notes');
$('reset-layout').onclick = () => { split.reset(); setView('split'); };
setView(readLocal('paper-view', 'split'), false);
function libraryOpen(open) {
    if (innerWidth > 1100) { document.body.classList.toggle('library-collapsed', !open); writeLocal('paper-library-collapsed', !open); }
    else { document.body.classList.toggle('library-open', open); $('library-scrim').hidden = !open; }
    $('library-toggle').setAttribute('aria-expanded', String(open)); split.update();
    if (open && innerWidth <= 1100) $('library-close').focus();
}
if (readLocal('paper-library-collapsed', false)) document.body.classList.add('library-collapsed');
$('library-toggle').onclick = () => libraryOpen(innerWidth > 1100 ? document.body.classList.contains('library-collapsed') : !document.body.classList.contains('library-open'));
$('library-close').onclick = $('library-scrim').onclick = () => { libraryOpen(false); $('library-toggle').focus(); };
document.addEventListener('keydown', e => { if (e.key === 'Escape' && document.body.classList.contains('library-open')) $('library-close').click(); });
window.addEventListener('resize', () => { if (innerWidth > 1100) { document.body.classList.remove('library-open'); $('library-scrim').hidden = true; } });
const resizeComposer = composer($('question'), $('question-form'), $('expand-question'));
readability($('reading-options'), 'paper-reading-type');
$('question').addEventListener('input', saveDrafts); $('note-body').addEventListener('input', saveDrafts);
$('discussion-scope').addEventListener('change', saveDrafts);
$('note-drafts').onchange = safe(() => { const slot = $('note-drafts').value; if (slot) { protectDrafts(); loadNoteDraft(slot); saveDrafts(); } });
$('clear-draft').onclick = () => { $('question').value = ''; clearSelection(); saveDrafts(); resizeComposer(); };
$('discard-note-draft').onclick = () => { delete noteDrafts[noteSlot]; loadNoteDraft(crypto.randomUUID()); saveDrafts(); };
$('note-add-same').onclick = safe(() => { if (noteSelection) startNote({selection:structuredClone(noteSelection)}); });
window.addEventListener('pagehide', saveDrafts);
window.addEventListener('beforeunload', e => { if (draftFailed) { e.preventDefault(); e.returnValue = ''; } });
$('latest').onclick = () => { $('messages').scrollTop = $('messages').scrollHeight; $('latest').hidden = true; };
$('messages').addEventListener('scroll', () => { if (scrollSnapshot($('messages')).following) $('latest').hidden = true; });
$('return-answer').onclick = () => { if (returnView) { setView(returnView.view, false); tab(returnView.tab, false); } $('return-answer').hidden = true; };
let fitTimer;
new ResizeObserver(() => { clearTimeout(fitTimer); if (pdf && fitMode !== 'custom' && $('pdf-scroll').clientWidth) fitTimer = setTimeout(safe(() => renderPage(true)), 160); }).observe($('pdf-scroll'));
$('fit-width').onclick = safe(async () => { fitMode = 'width'; writeLocal('paper-fit-mode', fitMode); await renderPage(true); });
$('fit-page').onclick = safe(async () => { fitMode = 'page'; writeLocal('paper-fit-mode', fitMode); await renderPage(true); });
function answerMarkdown(answer) {
    const body = markdown(answer.markdown);
    const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT); const nodes: Text[] = [];
    while (walker.nextNode()) nodes.push(walker.currentNode as Text);
    for (const node of nodes) {
        if (node.parentElement.closest('code,pre,a')) continue;
        const matches = [...node.data.matchAll(/\[\[([^\]\n]+)\]\]|〔来源(\d+)〕/g)];
        if (!matches.length) continue;
        const fragment = document.createDocumentFragment(); let start = 0;
        for (const match of matches) {
            fragment.append(document.createTextNode(node.data.slice(start, match.index)));
            const anchor = match[2] ? answer.anchors?.[Number(match[2]) - 1] : (answer.anchors || []).find(a => a.evidence_id === match[1] || a.id === match[1]);
            const citation = element(anchor ? 'button' : 'span', anchor ? (anchor.target?.kind === 'html' ? '原文段落' : `第 ${anchor.page} 页`) : '引用无法定位', 'cite');
            citation.title = anchor?.quote || match[0]; if (anchor) citation.onclick = safe(() => locate(anchor));
            fragment.append(citation); start = match.index + match[0].length;
        }
        fragment.append(document.createTextNode(node.data.slice(start))); node.replaceWith(fragment);
    }
    return body;
}

Object.assign(errorNames,{provider_http_400:'提供商拒绝参数，请检查当前模型设置',provider_http_401:'API 密钥无效',provider_http_403:'提供商拒绝访问',provider_http_404:'模型或接口不可用',provider_http_429:'提供商速率或额度限制',model_finish_max_tokens:'达到输出上限，回答未完成',model_finish_length:'达到输出上限，回答未完成',model_finish_safety:'提供商安全策略终止输出',model_finish_missing:'提供商未返回正常结束标记',model_invalid_response:'提供商响应格式无效',model_incomplete_output:'模型输出不完整',thinking_level_not_supported:'当前模型不支持此推理设置，请选择模型默认',ReadTimeout:'模型响应超时',TimeoutError:'本轮处理超时',ConnectError:'无法连接提供商'});


const selectionNoteMenu = createSelectionNoteMenu({onNote: value => startNote(value), notice});
selectionNoteMenu.bind($('text-layer'), () => { const current = capturePdfSelection(); return current ? {selection: current} : null; });
function htmlNoteDraft(content) {
    try { const selected = captureHtmlNote(content, doc?.content_version || doc?.parsed?.parse_sha256 || null); return selected ? {selection:selected} : null; }
    catch (error) { notice(error.message); return null; }
}
setupReadingDisclosure($('reading-work'), {read: readLocal, write: writeLocal});

// Reading artifacts extend the existing workspace; PDF state and notes remain owned above.
let savedPdfScroll = {top:0,left:0};
let readingMode = 'pdf', readingBundle: any = null, readingVersion = 0, readingQuote = '', readingNoteSelection:any=null;
const readingPositions = new Map<string, any>();
function resetHtmlReading() {
    selectionNoteMenu.close(); readingNoteSelection=null;
    readingImages.close();
    rememberHtmlPosition(); readingVersion++; readingBundle = null; readingMode = 'pdf'; readingQuote = ''; document.body.dataset.readingMode = 'pdf'; savedPdfScroll = {top:0,left:0};
    $('html-reading-frame').removeAttribute('srcdoc');
    $('html-reading').hidden = true; $('pdf-scroll').hidden = false;
    document.querySelector<HTMLElement>('.reader-toolbar').hidden = false;
    document.querySelectorAll<HTMLElement>('button[data-reading-mode]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.readingMode === 'pdf')));
}
function rememberHtmlPosition() {
    if (!doc || readingMode === 'pdf') return;
    const frame=$('html-reading-frame'), content=frame.contentDocument;
    const nodes=content ? Array.from(content.querySelectorAll('[data-reading-unit]')) as HTMLElement[] : [];
    const anchor=nodes.find(n=>n.getBoundingClientRect().bottom>0);
    readingPositions.set(doc.id,{top:frame.contentWindow?.scrollY||0,unit:anchor?.dataset.readingUnit,offset:anchor?.getBoundingClientRect().top||0});
}
async function setReadingMode(mode: string) {
    selectionNoteMenu.close(); readingNoteSelection=null;
    readingImages.close();
    if (!doc) return;
    rememberHtmlPosition(); if (readingMode === 'pdf') savedPdfScroll = {top:$('pdf-scroll').scrollTop,left:$('pdf-scroll').scrollLeft}; const serial = ++readingVersion, identity = doc.id;
    readingMode = mode; document.body.dataset.readingMode = mode; clearSelection(); $('html-reading-selection').hidden = true;
    document.querySelectorAll<HTMLElement>('button[data-reading-mode]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.readingMode === mode)));
    $('pdf-scroll').hidden = mode !== 'pdf'; $('html-reading').hidden = mode === 'pdf';
    document.querySelector<HTMLElement>('.reader-toolbar').hidden = mode !== 'pdf';
    if (mode === 'pdf') { if(pdf) await renderPage(true); $('pdf-scroll').scrollTop=savedPdfScroll.top; $('pdf-scroll').scrollLeft=savedPdfScroll.left; return; }
    const frame = $('html-reading-frame'); frame.hidden = true; $('html-reading-message').textContent = '正在打开…';
    try {
        if (!readingBundle || !readingBundle.available) { const data = await api('/api/documents/' + identity + '/reading'); if (serial !== readingVersion || doc.id !== identity) return; readingBundle = data; }
        if (!readingBundle.available) {
            const status=await api('/api/documents/'+identity+'/reading-task');
            if(serial!==readingVersion||doc.id!==identity)return;
            if(status.task?.state==='awaiting_review'){
                const draft=await api('/api/reading-jobs/'+status.task.id+'/preview');
                if(serial!==readingVersion||doc.id!==identity)return;
                readingBundle=draft;
            }
        }
        if (!readingBundle.available) { $('html-reading-message').textContent = '请使用上方的“开始中文通读”或“继续中文通读”。生成完成后可查看、下载中文稿。'; const legacy=element('a',' 查看已有译读记录'); legacy.href='/translations?session='+encodeURIComponent(session.id); legacy.target='_blank'; legacy.rel='noopener'; $('html-reading-message').append(legacy); return; }
        const parsed = new DOMParser().parseFromString(DOMPurify.sanitize(readingBundle.translated), 'text/html');
        if (mode === 'bilingual') {
            const original = new DOMParser().parseFromString(DOMPurify.sanitize(readingBundle.original), 'text/html');
            for (const target of Array.from(parsed.querySelectorAll<HTMLElement>('[data-reading-unit]'))) {
                const counterpart = original.querySelector('[data-reading-unit="' + target.dataset.readingUnit + '"]');
                if (!counterpart || counterpart.innerHTML === target.innerHTML || readingBundle.issues?.[target.dataset.readingUnit]?.kind==='original') continue;
                const copy = counterpart.cloneNode(true) as HTMLElement;
                for (const n of [copy, ...Array.from(copy.querySelectorAll<HTMLElement>('[id]'))]) if (n.id) n.id = 'en-' + n.id;
                copy.classList.add('reading-original');
                if (['TD','TH','LI'].includes(target.tagName)) { const block=parsed.createElement('div'); block.className='reading-original'; block.innerHTML=copy.innerHTML; target.prepend(block); } else target.before(copy);
            }
        }
        for (const note of Array.from(parsed.querySelectorAll<HTMLElement>('.ltx_note'))) {note.tabIndex=0;note.setAttribute('aria-label','脚注');}
        // Inert sandboxed document, no scripts or remote images; DOMPurify also removes handlers.
        for (const a of Array.from(parsed.querySelectorAll<HTMLAnchorElement>('a[href]'))) if (!a.getAttribute('href').startsWith('#')) { a.target = '_blank'; a.rel = 'noopener'; }
        const extra=htmlThemeCSS();
        frame.onload = () => {
            if (serial !== readingVersion || doc.id !== identity) return;
            const content = readingDocument(frame);
            if (!content) { frame.hidden=true; $('html-reading-message').textContent='阅读页面已离开原文，请重新选择中文通读。'; return; }
            readingImages.bind(content, $('original').hidden ? null : $('original').href);
            bindReadingLinks(content, {notice, openExternal: url => window.open(url, '_blank', 'noopener,noreferrer')});
            selectionNoteMenu.bind(content, () => htmlNoteDraft(content), frame);
            content.documentElement.dataset.readingView=mode;
            const position=readingPositions.get(identity); const anchor=position?.unit ? frame.contentDocument.querySelector('[data-reading-unit="'+position.unit+'"]') : null; frame.contentWindow.scrollTo(0,anchor ? anchor.getBoundingClientRect().top-position.offset : position?.top||0);
            applyHtmlType(); applyHtmlTheme();
            frame.contentDocument.addEventListener('mouseup', () => {
                try { readingNoteSelection=captureHtmlNote(content,doc?.content_version || doc?.parsed?.parse_sha256 || null); readingQuote=readingNoteSelection?.text || ''; } catch (error) { readingQuote = ''; readingNoteSelection=null; notice(error.message); }
                $('html-reading-selection').hidden = !readingQuote;
            });
        };
        frame.srcdoc = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>' + readingBundle.css.replace(/<\/style/gi, '') + extra + '</style><main>' + parsed.body.innerHTML + '</main></html>';
        frame.hidden = false; $('html-reading-message').textContent = '';
    } catch { if (serial === readingVersion) $('html-reading-message').textContent = '中文通读稿暂时无法打开，请重试。'; }
}
document.querySelectorAll<HTMLElement>('button[data-reading-mode]').forEach(b => b.onclick = safe(() => setReadingMode(b.dataset.readingMode)));
$('html-reading-ask').onclick = () => { if (readingQuote) draftPrompt('请解释以下论文选段：\n\n' + readingQuote); };
$('html-reading-note').onclick = safe(() => { if (readingNoteSelection) startNote({selection:structuredClone(readingNoteSelection)}); });

function applyHtmlType() {
    const body=readingDocument($('html-reading-frame'))?.body;if(!body)return;
    body.style.fontSize=(Number(document.querySelector<HTMLSelectElement>('[data-font-size]').value)||14)+'px';
    body.style.lineHeight=document.querySelector<HTMLSelectElement>('[data-line-height]').value||'1.85';
}
document.querySelectorAll('[data-font-size],[data-line-height]').forEach(n=>n.addEventListener('change',applyHtmlType));

// Same semantic colors as the surrounding product, also when the host changes theme.
function htmlThemeCSS() {
    return `html,body,main,article,section{background:var(--aos-bg)!important;color:var(--aos-text)!important}
    body{margin:0}main{margin:0 auto;padding:24px;max-width:1100px}article{min-width:0}
    a{color:var(--aos-text)}.ltx_authors,.ltx_author,.ltx_personname,.ltx_contact{color:var(--aos-muted)!important}.reading-original{color:var(--aos-muted)!important;background:var(--aos-sidebar)!important;border-left:2px solid var(--aos-line);padding:10px;margin-bottom:8px}
    table,tbody,tr,td,th{background:var(--aos-bg)!important;color:var(--aos-text)!important;border-color:var(--aos-line)!important}
    pre,code,.ltx_listing,.ltx_verbatim,.ltx_algorithm{background:var(--aos-code)!important;color:var(--aos-text)!important}
    pre,.ltx_listing,.ltx_algorithm{overflow:auto}
    .ltx_listing{white-space:normal!important}.ltx_listingline{display:block;white-space:nowrap!important;margin:0;padding:2px 0}
    .ltx_tag_listingline{display:inline-block;min-width:2.5em}
    figure img,figure svg{background:#fff;border-radius:3px}
    .ltx_note{position:relative}.ltx_note_mark{vertical-align:super;font-size:.8em;cursor:help}
    .ltx_note_outer{display:none;position:absolute;z-index:10;left:0;top:1.3em;width:min(28em,70vw);padding:12px;background:var(--aos-panel);color:var(--aos-text);border:1px solid var(--aos-line);border-radius:8px;white-space:normal;font-size:14px}
    .ltx_note:hover .ltx_note_outer,.ltx_note:focus-within .ltx_note_outer{display:block}
    math{color:var(--aos-text)!important}img{max-width:100%;height:auto}
    ::selection{background:var(--aos-selected);color:var(--aos-text)}`;
}
function applyHtmlTheme() {
    const root=$('html-reading-frame').contentDocument?.documentElement;
    if(!root)return;
    const host=getComputedStyle(document.documentElement);
    for(const name of ['bg','panel','sidebar','subtle','text','muted','line','code','selected'])
        root.style.setProperty('--aos-'+name,host.getPropertyValue('--aos-'+name));
    root.style.colorScheme=document.documentElement.dataset.theme==='dark'?'dark':'light';
}
window.addEventListener('aos-theme-change',applyHtmlTheme);
let readingTaskId='', readingTaskTimer:any=null, autoOpenReadingTask='';
const readingStates={queued:'等待处理',fetching:'获取论文',parsing:'解析论文',translating:'翻译正文',validating:'校验结构',completed:'中文通读已就绪',awaiting_review:'PDF 处理稿已生成，等待样稿验收',paused:'处理已暂停',failed:'处理未完成'};
const readingErrors={pdf_parser_file_missing:'解析所需的 PDF 或模型文件缺失，已保留原件和完成进度；请检查解析环境的模型资源后继续。',pdf_memory_unavailable:'当前可用内存不足，解析进度已保存；关闭高占用应用后继续解析',pdf_parse_memory_limit:'解析达到内存保护上限，已保存完成页；可继续解析',pdf_parser_killed:'PDF 解析进程被系统终止，已保存完成页；可继续解析',pdf_parse_timeout:'PDF 解析超时，已保存完成页；可继续解析',pdf_parser_dependency_missing:'增强 PDF 解析组件尚未安装，请在独立解析环境中安装 pdf-reading 依赖后重试；本地 PDF 浏览和笔记仍可使用。',pdf_parser_failed:'PDF 解析未完成，原件及已完成页已保留',pdf_checkpoint_changed:'已保存的解析文件校验不一致，已停止接入',translation_invalid_json:'模型返回格式错误；已完成部分已保存，可继续未完成部分',translation_invalid_schema:'模型返回字段不正确；已完成部分已保存',translation_duplicate_key:'模型返回重复段落编号，已停止接入',translation_truncated:'模型输出被截断；已完成部分已保存',paragraph_coverage_changed:'模型漏掉或增加了段落，已停止接入',full_text_unavailable_upload_pdf:'未找到可访问全文，请上传 PDF',reading_deepseek_not_configured:'请在模型设置中配置 DeepSeek V4 Flash',protected_marker_changed:'受保护区域校验未通过',protected_object_changed:'原文对象校验未通过',reading_processing_failed:'处理失败，原件已保留',paragraph_exceeds_model_request_capacity:'段落超过当前模型输出设置，请调高输出上限后继续',source_image_format_unsupported:'图片格式暂不支持，已停止发布',source_host_unavailable:'暂时无法连接论文网站',source_too_large:'文件超过导入大小限制',interrupted_no_retry:'上次处理被中断',stopped:'已停止'};
async function watchReadingTask(identity:string) {
    clearTimeout(readingTaskTimer);readingTaskId=identity;writeLocal('paper-reading-task',identity);
    try {
        const task=await api('/api/reading-jobs/'+identity);
        if(identity!==readingTaskId)return;
        $('import-progress').hidden=false;
        $('import-progress').textContent=(readingStates[task.state]||task.state)+(task.total?' · '+task.done+'/'+task.total:'')+(task.error?' · '+(readingErrors[task.error]||'请检查网络或模型配置后继续处理'):'');
        if(task.parse_progress&&!task.total) $('import-progress').append(document.createTextNode(' · 已解析 '+task.parse_progress.done+'/'+task.parse_progress.total+' 页'));
        if(task.parse_quality?.source_images) $('import-progress').append(document.createTextNode(' · '+task.parse_quality.source_images+' 处解析内容需核对'));
        if(task.quality?.issues) $('import-progress').append(document.createTextNode(' · '+task.quality.issues+' 处需核对，'+task.quality.original+' 单元保留原文'));
        if(task.state==='failed'&&task.diagnostic?.batch){$('import-progress').append(document.createTextNode(' · 第'+task.diagnostic.batch+'批'+(task.diagnostic.units?.length?'，'+task.diagnostic.units.length+'个单元校验未通过':'')));}
        if(task.state==='awaiting_review'){const link=element('a',' 查看处理稿');link.href='/reading-drafts/'+identity;link.target='_blank';link.rel='noopener';$('import-progress').append(link);}
        const active=['queued','fetching','parsing','translating','validating'].includes(task.state);
        if(task.state==='translating'&&doc?.id===task.document&&!doc.content_ready){doc=await api('/api/documents/'+doc.id);}
        $('import-actions').hidden=!active&&!['failed','paused'].includes(task.state);
        $('import-stop').hidden=!active;$('import-resume').hidden=active;
        if(task.session&&(autoOpenReadingTask===identity||!doc)){autoOpenReadingTask='';await openSession(task.session);}
        if(task.state==='completed'&&doc?.id===task.document){doc=await api('/api/documents/'+doc.id);readingBundle=null;if(readingMode!=='pdf')await setReadingMode(readingMode);}
        if(active)readingTaskTimer=setTimeout(()=>watchReadingTask(identity),1500);
    } catch { $('import-progress').hidden=false;$('import-progress').textContent='暂时无法读取处理进度'; }
}
$('url-import').onsubmit=safe(async(event)=>{
    event.preventDefault();const url=$('paper-url').value.trim();if(!url)return;
    const button=$('url-import').querySelector('button');button.disabled=true;
    try {const task=await api('/api/reading-jobs/url','POST',{url});autoOpenReadingTask=task.id;await watchReadingTask(task.id);

    }finally{button.disabled=false;}
});
$('import-stop').onclick=safe(async()=>{await api('/api/reading-jobs/'+readingTaskId+'/stop','POST',{});await watchReadingTask(readingTaskId);});
$('import-resume').onclick=safe(async()=>{
    if(!confirm('继续未完成部分；上次结果未知的请求可能重复计费。'))return;
    await api('/api/reading-jobs/'+readingTaskId+'/resume','POST',{});await watchReadingTask(readingTaskId);
});
const savedReadingTask=readLocal('paper-reading-task');if(savedReadingTask)watchReadingTask(savedReadingTask);

// Document-scoped task panel: reloads survive refresh and never show another paper's files.
let documentReadingTask:any=null, documentReadingTimer:any=null;
function renderDocumentReading(task:any) {
    documentReadingTask=task; $('reading-work').hidden=!doc;
    const active=task&&['queued','fetching','parsing','translating','validating'].includes(task.state);
    const ready=task&&['completed','awaiting_review'].includes(task.state);
    $('reading-work-title').textContent=ready?'中文稿已生成':'中文通读';
    $('reading-work-summary').textContent=task ? (readingStates[task.state]||task.state)+(active&&task.total?' · '+task.done+'/'+task.total:'')+(task.error?' · 需处理':'') : '尚未生成';
    $('reading-work-status').textContent=task ? (readingStates[task.state]||task.state)+(task.total?' · '+task.done+'/'+task.total+' 单元':'')+(task.error?' · '+(readingErrors[task.error]||'处理未完成，可继续已保存的进度'):'') : '使用 DeepSeek 生成完整中文稿，进度自动保存。';
    if(task?.quality?.issues){
        const q=task.quality;
        if(ready) $('reading-work-title').textContent='处理稿已生成 · 请核对标注';
        $('reading-work-summary').textContent=(ready?'中文稿已生成':'处理中')+' · '+q.issues+' 处需核对';
        $('reading-work-status').textContent='已处理 '+task.done+'/'+task.total+' 单元 · '+q.translated+' 单元已有译文 · '+q.original+' 单元保留原文 · '+q.review+' 处译文需核对';
    }
    if(ready&&task.parse_quality?.source_images){
        $('reading-work-title').textContent='处理稿已生成 · 请核对标注';
        $('reading-work-summary').textContent+=' · '+task.parse_quality.source_images+' 处解析内容需核对';
    }
    const parsing=task?.parse_progress&&!task.total;
    if(parsing){
        const p=task.parse_progress;
        const progress=' · 已解析 '+p.done+'/'+p.total+' 页';
        $('reading-work-summary').textContent+=progress;
        $('reading-work-status').textContent+=progress;
        if(active&&p.current) $('reading-work-status').textContent+=' · 正在处理第 '+p.current.first+'–'+p.current.last+' 页';
    }
    $('reading-start').hidden=ready; $('reading-start').disabled=Boolean(active);
    $('reading-start').textContent=active?'正在处理…':(parsing||task?.diagnostic?.stage==='parsing')?'继续解析':task?'继续中文通读':'开始中文通读';
    $('reading-stop').hidden=!active; $('reading-stop').disabled=Boolean(task?.stop);
    $('reading-stop').textContent=task?.stop?'正在暂停…':'暂停';
    $('reading-refresh').hidden=true;
    $('reading-progress').hidden=!task?.total&&!parsing;
    $('reading-progress').max=(parsing?task.parse_progress.total:task?.total)||1; $('reading-progress').value=(parsing?task.parse_progress.done:task?.done)||0;
    $('reading-files').hidden=!ready; $('reading-preview').hidden=!ready;
    if(ready){
        $('reading-preview').href='/reading-drafts/'+task.id;
        $('reading-preview').textContent=task.state==='awaiting_review'?'查看中文处理稿 ↗':'打开中文通读 ↗';
        $('reading-download-html').href='/api/reading-jobs/'+task.id+'/files/html';
        $('reading-download-markdown').href='/api/reading-jobs/'+task.id+'/files/markdown';
        $('reading-file-note').textContent=task.state==='awaiting_review'?'PDF 可译正文已完成，图片和图中文字保留原样。处理稿的排版与语义仍需核对。':'完整译文已保存，HTML 可离线打开；Markdown ZIP 请先解压。';
        if(task.quality?.issues) $('reading-file-note').textContent='中文稿含 '+task.quality.issues+' 处红色核对提示；未能可靠翻译的单元保留原文。阅读页与下载文件均保留标注。';
        if(task.parse_quality?.source_images) $('reading-file-note').textContent+=' '+task.parse_quality.source_images+' 处解析内容保留为原文图像，请核对红色提示。';
    }
}
async function loadDocumentReading() {
    clearTimeout(documentReadingTimer); if(!doc)return;
    const identity=doc.id; documentReadingTask=null;
    $('reading-work').hidden=false; $('reading-files').hidden=true; $('reading-preview').hidden=true;
    $('reading-start').disabled=true; $('reading-work-status').textContent='正在读取中文通读进度…';
    if (!documentReadingTask) $('reading-work-summary').textContent='正在读取状态…';
    try {
        const result=await api('/api/documents/'+identity+'/reading-task');
        if(doc?.id!==identity)return;
        renderDocumentReading(result.task);
        if(result.task&&['completed','awaiting_review'].includes(result.task.state)&&readingMode!=='pdf'&&!readingBundle?.available)
            await setReadingMode(readingMode);
        if(result.task&&['queued','fetching','parsing','translating','validating'].includes(result.task.state))
            documentReadingTimer=setTimeout(loadDocumentReading,1500);
    } catch {
        if(doc?.id!==identity)return;
        $('reading-work-status').textContent='暂时无法读取处理进度，请刷新状态。';
        $('reading-work-summary').textContent='状态读取失败 · 展开重试';
        $('reading-refresh').hidden=false; $('reading-stop').hidden=true;
    }
}
$('reading-start').onclick=safe(async()=>{
    if(!doc||!session)return;
    const identity=doc.id, sid=session.id, current=documentReadingTask;
    $('reading-start').disabled=true;
    try {
        let task=current;
        if(!task)task=await api('/api/reading-jobs/document','POST',{session:sid});
        if(['failed','paused'].includes(task.state))task=await api('/api/reading-jobs/'+task.id+'/resume','POST',{});
        if(doc?.id===identity){renderDocumentReading(task);await loadDocumentReading();}
    }catch(e){if(doc?.id===identity){renderDocumentReading(current);$('reading-work-status').textContent=e.message;}throw e;}
});
$('reading-stop').onclick=safe(async()=>{
    const task=documentReadingTask;if(!task)return;
    await api('/api/reading-jobs/'+task.id+'/stop','POST',{}); await loadDocumentReading();
});
$('reading-refresh').onclick=safe(loadDocumentReading);
$('reading-preview').onclick=safe(async(event)=>{
    if(['completed','awaiting_review'].includes(documentReadingTask?.state)){event.preventDefault();readingBundle=null;await setReadingMode('zh');}
});
// Historical translations remain accessible from Reading settings.
$('full-translation').textContent='历史译读记录 ↗';

errorNames.paper_content_requires_processing='这篇旧论文尚未完成内容迁移，请先点击中文通读入口处理；问题草稿已保留。';

function publishAssistantContext() { if(doc && (window as any).platformAssistant) (window as any).platformAssistant.context({provider:'paper',id:doc.id,legacy:session?.id,saveNotes:true}); }
window.addEventListener('platform-assistant-ready',publishAssistantContext);
window.addEventListener('platform-assistant-action',safe(async(event:any)=>{
    if(event.detail.type==='locate') await locate(event.detail.evidence.location);
    if(event.detail.type==='save-note') startNote({explanation:event.detail.text});
}));
