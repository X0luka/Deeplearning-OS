import test from 'node:test';
import assert from 'node:assert/strict';
import {encodeNoteSource,decodeNoteSource,groupNotes} from '../paper_reader/static/note_groups.mjs';
const source={kind:'html',text:'中文摘录',version:'v1',view:'zh',start_unit:'p1',end_unit:'p1',start_offset:0,end_offset:4};
test('structured source survives the existing immutable evidence field without mixing with user prose',()=>{
  const note={body:'我的理解',explanation:'',selection:source};
  const wire=encodeNoteSource(note);assert.equal(wire.body,'我的理解');assert.equal(wire.selection,null);
  assert.deepEqual(decodeNoteSource(wire),note);
});
test('legacy combined text and malformed envelopes are preserved exactly',()=>{
  for(const note of [{body:'摘录：\n旧摘录\n\n用户文字',explanation:'',selection:null},{body:'正文',explanation:'reader-note-source/v1\nnot JSON',selection:null}]){
    assert.deepEqual(decodeNoteSource(note),note);assert.deepEqual(encodeNoteSource(note),note);
  }
});
test('same source groups interpretations while document, version and source location stay distinct',()=>{
  const note={document:'doc1',selection:source,body:'one'};
  const groups=groupNotes([note,{...note,body:'two'},{...note,document:'doc2'}, {...note,selection:{...source,version:'v2'}},{...note,selection:{...source,start_unit:'p2'}}]);
  assert.equal(groups.length,4);assert.equal(groups[0].notes.length,2);
});
test('PDF evidence and original model explanations keep the existing format',()=>{
  const note={selection:{page:1,bbox:[1,2,3,4],text:'source'},body:'my note',explanation:'saved answer'};
  assert.equal(encodeNoteSource(note),note);assert.equal(decodeNoteSource(note),note);
});
