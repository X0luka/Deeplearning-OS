export function captureHtmlNote(content, version = null) {
  const selected = content?.defaultView?.getSelection();
  if (!selected || selected.isCollapsed || !selected.rangeCount) return null;
  const range = selected.getRangeAt(0), main = content.querySelector('main');
  if (!main?.contains(range.startContainer) || !main.contains(range.endContainer)) return null;
  const text = selected.toString().trim();
  if (!text) return null;
  if (text.length > 12000) throw new Error('选段过长，请分段记笔记。');
  const point = (node, offset) => {
    const el = node.nodeType === 1 ? node : node.parentElement;
    const unit = el.closest('[data-reading-unit]') || main;
    const prefix = content.createRange(); prefix.selectNodeContents(unit); prefix.setEnd(node, offset);
    return {unit:unit.getAttribute('data-reading-unit') || 'main', offset:prefix.toString().length};
  };
  const start = point(range.startContainer, range.startOffset), end = point(range.endContainer, range.endOffset);
  return {kind:'html',text,version,start_unit:start.unit,start_offset:start.offset,end_unit:end.unit,end_offset:end.offset,view:content.documentElement.dataset.readingView || 'zh'};
}

export function noteGroupKey(note) {
  const s = note.selection;
  if (!s) return 'free';
  if (s.kind === 'html') return JSON.stringify([note.document || '',s.version,s.view,s.start_unit,s.start_offset,s.end_unit,s.end_offset,s.text]);
  return JSON.stringify([note.document || '',note.parse_sha256,s.page,s.bbox,s.text]);
}

export function groupNotes(notes) {
  const groups = new Map();
  for (const note of notes) {
    const key = noteGroupKey(note);
    if (!groups.has(key)) groups.set(key, {selection:note.selection, notes:[]});
    groups.get(key).notes.push(note);
  }
  return [...groups.values()];
}

// Versioned source envelope uses the existing immutable evidence field, so deployed
// readers can save structured HTML sources without restarting active translation jobs.
const sourcePrefix = 'reader-note-source/v1\n';
export function encodeNoteSource(note) {
  if (note.selection?.kind !== 'html') return note;
  const explanation = sourcePrefix + JSON.stringify({source:note.selection, explanation:note.explanation || ''});
  if (explanation.length > 30000) throw new Error('摘录和解释过长，请分段保存。');
  return {...note, selection:null, explanation};
}
export function decodeNoteSource(note) {
  if (note.selection || !note.explanation?.startsWith(sourcePrefix)) return note;
  try {
    const value = JSON.parse(note.explanation.slice(sourcePrefix.length)), s=value.source;
    if (s?.kind !== 'html' || typeof s.text !== 'string' || !s.text.length || s.text.length>12000
        || typeof value.explanation !== 'string' || !['zh','bilingual'].includes(s.view)
        || !['start_unit','end_unit'].every(k=>typeof s[k]==='string')
        || !['start_offset','end_offset'].every(k=>Number.isSafeInteger(s[k]) && s[k]>=0)) return note;
    return {...note,selection:s,explanation:value.explanation};
  } catch { return note; }
}
