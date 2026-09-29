import test from 'node:test';
import assert from 'node:assert/strict';
import {clampWidth, readLocal, writeLocal, scrollSnapshot, restoreScroll, composer, splitPane} from '../wiki_app/static/workspace.mjs';

test('width clamps stored desktop values after shrinking and expanding', () => {
  assert.equal(clampWidth(900, 800), 440);
  assert.equal(clampWidth(100, 1600), 320);
  assert.equal(clampWidth('corrupt', 1280), 420);
});
test('storage corruption and quota errors are recoverable and explicit', () => {
  globalThis.localStorage = {getItem: () => '{', setItem: () => {throw Error('quota');}};
  assert.equal(readLocal('draft', null), null); assert.equal(writeLocal('draft', {}), false);
});
test('polling preserves a reader above the bottom and exposes new content', () => {
  const box = {scrollTop: 200, clientHeight: 400, scrollHeight: 1500};
  const before = scrollSnapshot(box), button = {hidden: true}; box.scrollHeight = 1800;
  restoreScroll(box, before, true, button); assert.equal(box.scrollTop, 200); assert.equal(button.hidden, false);
  box.scrollTop = 1400; const bottom = scrollSnapshot(box); box.scrollHeight = 2000;
  restoreScroll(box, bottom, true, button); assert.equal(box.scrollTop, 2000); assert.equal(button.hidden, true);
});
function target() {const events = {};return {events, addEventListener: (name, handler) => events[name] = handler, style: {}, focus(){}};}
test('composer sends Enter, preserves Shift+Enter and IME', () => {
  globalThis.window = target(); globalThis.innerHeight = 800;
  const text = {...target(), scrollHeight: 900}, expand = target(); let sends = 0;
  const form = {classList: {contains: () => false}, requestSubmit: () => sends++};
  composer(text, form, expand);
  for (const flags of [{shiftKey: true}, {ctrlKey: true, isComposing: true}, {metaKey: true, keyCode: 229}]) text.events.keydown({key:'Enter',preventDefault(){},...flags});
  assert.equal(sends, 0); text.events.keydown({key:'Enter',preventDefault(){}}); assert.equal(sends, 1);
  assert.equal(text.style.height, '176px');
});
test('separator keyboard respects bounds, reset and resize preserves preference', () => {
  globalThis.window = target(); const values = new Map();globalThis.localStorage={getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v)};
  const handle = {...target(),attrs:{},setAttribute(k,v){this.attrs[k]=v;},getAttribute(k){return this.attrs[k];}};
  let width, available=1000;const split=splitPane({handle,panel:{id:'assistant'},key:'width',available:()=>available,apply:v=>width=v});
  handle.events.keydown({key:'ArrowLeft',preventDefault(){}});assert.equal(width,436);
  available=700;split.update();assert.equal(width,340);available=1500;split.update();assert.equal(width,436);
  handle.events.keydown({key:'End',preventDefault(){}});assert.equal(width,1140);
  split.reset();assert.equal(width,420);assert.equal(handle.attrs['aria-orientation'],'vertical');
});

test('composer still expands to half-window and returns to normal height', () => {
  globalThis.window=target();globalThis.innerHeight=800;
  const text={...target(),scrollHeight:900},expand={...target(),setAttribute(){}};
  let expanded=false;
  const form={classList:{contains:()=>expanded,toggle:()=>expanded=!expanded},requestSubmit(){}};
  composer(text,form,expand);assert.equal(text.style.height,'176px');
  expand.events.click();assert.equal(text.style.height,'400px');
  expand.events.click();assert.equal(text.style.height,'176px');
});
