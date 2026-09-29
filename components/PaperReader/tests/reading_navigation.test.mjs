import test from 'node:test';
import assert from 'node:assert/strict';
import {chromium} from '@playwright/test';
import {readFile} from 'node:fs/promises';

test('sandbox citations stay in article; external links and inaccessible frames are contained', async () => {
 const browser=await chromium.launch({headless:true});
 try {
  const page=await browser.newPage();
  const module=await readFile(new URL('../paper_reader/static/reading_navigation.mjs',import.meta.url),'utf8');
  await page.route('http://reader.test/**', route => route.fulfill({contentType:route.request().url().endsWith('.mjs')?'text/javascript':'text/html',body:route.request().url().endsWith('.mjs')?module:'<iframe id="reading" sandbox="allow-same-origin" style="height:200px"></iframe>'}));
  await page.goto('http://reader.test/');
  await page.evaluate(async () => {
   const {readingDocument,bindReadingLinks}=await import('/navigation.mjs');
   window.inspect=readingDocument;window.notices=[];window.links=[];
   const frame=document.querySelector('iframe');
   frame.onload=()=>{const doc=readingDocument(frame);if(doc)bindReadingLinks(doc,{notice:t=>window.notices.push(t),openExternal:u=>window.links.push(u)});};
   frame.srcdoc='<a href="#ref17">[17]</a> <a href="#missing">missing</a> <a href="https://example.org/paper">external</a><div style="height:1200px"></div><p id="ref17">reference 17</p>';
  });
  const article=page.frameLocator('iframe');
  await article.getByText('[17]',{exact:true}).click();
  assert.equal(await page.evaluate(()=>document.querySelector('iframe').contentDocument.URL),'about:srcdoc');
  assert.ok(await page.evaluate(()=>document.querySelector('iframe').contentWindow.scrollY)>500);
  await article.getByText('missing',{exact:true}).click();
  assert.deepEqual(await page.evaluate(()=>window.notices),['未找到对应位置']);
  await article.getByText('external',{exact:true}).click();
  assert.deepEqual(await page.evaluate(()=>window.links),['https://example.org/paper']);
  assert.equal(await page.evaluate(()=>document.querySelector('iframe').contentDocument.URL),'about:srcdoc');
  await page.evaluate(()=>{const f=document.querySelector('iframe');f.removeAttribute('srcdoc');f.src='data:text/html,foreign';});
  await page.waitForFunction(()=>document.querySelector('iframe').contentDocument===null);
  assert.equal(await page.evaluate(()=>window.inspect(document.querySelector('iframe'))),null);
  await page.evaluate(()=>document.querySelector('iframe').srcdoc='<p>recovered</p>');
  await article.getByText('recovered',{exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>!!window.inspect(document.querySelector('iframe'))),true);
 } finally {await browser.close();}
});
