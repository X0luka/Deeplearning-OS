import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {chromium, expect} from '@playwright/test';

test('right-click notes preserve current PDF/HTML selection and save through the real API; disclosure persists', {timeout:90000}, async () => {
  const fixture=spawn('.venv/bin/python',['-u','tests/reading_notes_fixture.py'],{stdio:['ignore','pipe','pipe']});
  let logs='';fixture.stderr.on('data',v=>logs+=v);
  const url=await new Promise((resolve,reject)=>{
    fixture.stdout.on('data',v=>{const match=String(v).match(/FIXTURE=(http:\/\/[^\s]+)/);if(match)resolve(match[1]);});
    fixture.once('exit',()=>reject(new Error(logs||'Fixture exited')));
  });
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1500,height:1000}});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    const quote='这是用于验证右键摘录与笔记保存的中文段落。'.repeat(180);
    let task={id:'fixture',state:'completed',done:100,total:100};
    await page.route('**/api/documents/*/reading',route=>route.fulfill({json:{available:true,css:'body{margin:20px}p{font-size:16px;line-height:1.8}',original:'<main><p data-reading-unit="p1">Original paragraph.</p></main>',translated:`<main><p id="excerpt" data-reading-unit="p1">${quote}</p><p id="second" data-reading-unit="p2">第二段选文不能替换之前保存的摘录。</p></main>`}}));
    await page.route('**/api/documents/*/reading-task',route=>route.fulfill({json:{task}}));
    for(let attempt=0;;attempt++) {try{await page.goto(url);break;}catch(e){if(attempt>20)throw e;await new Promise(r=>setTimeout(r,100));}}
    await page.locator('#sessions button').first().click();await page.locator('.text-line').first().waitFor();
    const panel=page.locator('#reading-work'), summary=panel.locator('summary');
    await expect(panel).not.toHaveAttribute('open','');await expect(summary).toContainText('中文通读已就绪');
    await expect(page.locator('#reading-download-html')).not.toBeVisible();
    await summary.click();await expect(page.locator('#reading-download-html')).toBeVisible();
    await page.reload();await page.locator('.text-line').first().waitFor();await expect(panel).toHaveAttribute('open','');
    await summary.click();await expect(panel).not.toHaveAttribute('open','');
    // Mouse selection of actual synthetic PDF text, followed by a right click.
    const line=page.locator('.text-line').first(), b=await line.boundingBox();
    await page.mouse.move(b.x+1,b.y+b.height/2);await page.mouse.down();await page.mouse.move(b.x+b.width-1,b.y+b.height/2,{steps:12});await page.mouse.up();
    const selected=await page.evaluate(()=>getSelection().toString().trim());assert.ok(selected.length>10);
    await line.click({button:'right',position:{x:15,y:5}});
    const menu=page.getByRole('menu',{name:'选段操作'});await expect(menu).toBeVisible();
    await menu.getByRole('menuitem').click();await expect(page.locator('#note-context')).toContainText(selected);
    await page.locator('#note-body').fill('PDF 选段笔记验证');await page.locator('#save-note').click();
    await expect(page.locator('#note-status')).toHaveText('笔记已保存');await expect(page.locator('.note-group').first()).toContainText(selected);
    await page.reload();await page.locator('.text-line').first().waitFor();await page.locator('#notes-tab').click();
    await expect(page.locator('.note').first()).toContainText('PDF 选段笔记验证');
    // HTML selection exceeds the previous 3000-character truncation boundary.
    await page.locator('button[data-reading-mode="zh"]').click();
    const article=page.frameLocator('#html-reading-frame'), excerpt=article.locator('#excerpt');await excerpt.waitFor();
    const select=async locator=>{await locator.evaluate(el=>{const range=el.ownerDocument.createRange();range.selectNodeContents(el);const s=el.ownerDocument.defaultView.getSelection();s.removeAllRanges();s.addRange(range);});};
    await select(excerpt);await excerpt.click({button:'right',position:{x:20,y:10}});await expect(menu).toBeVisible();
    // The menu must retain the original selection even if focus clears the browser range.
    await excerpt.evaluate(el=>el.ownerDocument.defaultView.getSelection().removeAllRanges());
    await menu.getByRole('menuitem').click();await expect(page.locator('#note-body')).toHaveValue('');await expect(page.locator('#note-context')).toHaveText(quote);
    await page.locator('#note-body').fill('中文笔记补充。');
    await page.locator('#save-note').click();await expect(page.locator('#note-status')).toHaveText('笔记已保存');
    await page.reload();await page.locator('.text-line').first().waitFor();await page.locator('#notes-tab').click();
    await expect(page.locator('#notes-list')).toContainText(quote);await expect(page.locator('#notes-list')).toContainText('中文笔记补充。');
    await page.locator('button[data-reading-mode="bilingual"]').click();await excerpt.waitFor();
    await page.locator('#note-body').fill('尚未保存的另一份草稿');
    const second=article.locator('#second');await select(second);await second.click({button:'right',position:{x:10,y:8}});
    await menu.getByRole('menuitem').click();await expect(page.locator('#note-context')).toContainText('第二段选文');await expect(page.locator('#note-body')).toHaveValue('');
    assert.ok((await page.locator('#note-drafts').textContent()).includes('尚未保存的另一份草稿'));
    await select(second);await second.click({button:'right',position:{x:10,y:8}});await page.keyboard.press('Escape');await expect(menu).not.toBeVisible();
    // Adding another interpretation reuses the source and starts with an empty body.
    await page.locator('#note-body').fill('第一种理解');await page.locator('#save-note').click();await expect(page.locator('#note-status')).toHaveText('笔记已保存');
    await expect(page.locator('#save-note')).toHaveText('保存修改');
    await page.locator('#note-add-same').click();await expect(page.locator('#note-body')).toHaveValue('');
    await expect(page.locator('#note-context')).toContainText('第二段选文');
    await page.locator('#note-body').fill('第二种理解');await page.locator('#save-note').click();await expect(page.locator('#note-status')).toHaveText('笔记已保存');
    const group=page.locator('.note-group').filter({hasText:'第二段选文'});await expect(group.locator('.note')).toHaveCount(2);
    await expect(group.locator('blockquote')).toHaveCount(1);
    await page.locator('#note-body').fill('第二种理解的修订');await page.locator('#save-note').click();await expect(page.locator('#note-status')).toHaveText('笔记已保存');
    await expect(group.locator('.note')).toHaveCount(2);await expect(group).toContainText('第一种理解');await expect(group).toContainText('第二种理解的修订');
    await expect(page.locator('#note-save-copy')).toHaveCount(0);await expect(page.locator('#new-note')).toHaveText('写论文笔记');
    await page.locator('#new-note').click();await expect(page.locator('#note-source')).not.toBeVisible();await expect(page.locator('#note-body')).toHaveValue('');
    await page.locator('#note-body').fill('不关联选段的论文笔记');await page.locator('#save-note').click();await expect(page.locator('#note-status')).toHaveText('笔记已保存');
    await page.reload();await page.locator('.text-line').first().waitFor();await page.locator('#notes-tab').click();
    await expect(group.locator('.note')).toHaveCount(2);await expect(page.locator('#notes-list')).toContainText('不关联选段的论文笔记');
    await page.locator('button[data-reading-mode="bilingual"]').click();await second.waitFor();
    // No selection leaves the browser menu intact; oversized selections are rejected, never truncated.
    await second.evaluate(el=>el.ownerDocument.defaultView.getSelection().removeAllRanges());
    const blocked=await second.evaluate(el=>{const e=new MouseEvent('contextmenu',{bubbles:true,cancelable:true});el.dispatchEvent(e);return e.defaultPrevented;});assert.equal(blocked,false);
    await second.evaluate(el=>el.textContent='长'.repeat(12001));await select(second);await second.click({button:'right',position:{x:10,y:8}});
    await expect(page.locator('#notice')).toContainText('选段过长');await expect(menu).not.toBeVisible();await page.keyboard.press('Escape');await page.locator('#notice').click();
    await page.setViewportSize({width:390,height:844});await page.locator('[data-workspace-view="reader"]').click();
    await expect(summary).toBeVisible();await expect(panel).not.toHaveAttribute('open','');
    await second.evaluate(el=>el.textContent='窄屏右键选段');await select(second);await second.click({button:'right',position:{x:10,y:8}});await expect(menu).toBeVisible();
    const box=await menu.boundingBox();assert.ok(box.x>=0&&box.x+box.width<=390);
    await menu.getByRole('menuitem').click();await expect(page.locator('#note-body')).toBeVisible();await expect(page.locator('#note-context')).toContainText('窄屏右键选段');await expect(page.locator('#note-body')).toHaveValue('');
    task={...task,state:'translating',done:25};await page.reload();await page.locator('#document-title').waitFor();
    await page.locator('[data-workspace-view="reader"]').click();await expect(summary).toContainText('25/100');await expect(panel).not.toHaveAttribute('open','');
    task={...task,state:'paused',done:30};await expect(summary).toContainText('处理已暂停');await expect(panel).not.toHaveAttribute('open','');
    await summary.click();await expect(page.locator('#reading-start')).toHaveText('继续中文通读');
    assert.deepEqual(errors,[]);
  } finally {await browser.close();fixture.kill('SIGTERM');}
});
