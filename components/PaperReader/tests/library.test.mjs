import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {chromium,expect} from '@playwright/test';

test('file menu pins persistently, trashes/restores current files, retains history, and handles empty library', {timeout:90000}, async()=>{
 const fixture=spawn('.venv/bin/python',['-u','tests/library_fixture.py'],{stdio:['ignore','pipe','pipe']});let log='';fixture.stderr.on('data',v=>log+=v);
 const url=await new Promise((resolve,reject)=>{fixture.stdout.on('data',v=>{const m=String(v).match(/FIXTURE=(http:\/\/[^\s]+)/);if(m)resolve(m[1]);});fixture.once('exit',()=>reject(new Error(log)));});
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1500,height:1000}}), errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>{errors.push('Unexpected native dialog');void d.dismiss();});
  await page.addInitScript(()=>{window.confirm=()=>{throw new Error('Native confirm unavailable');};window.prompt=()=>{throw new Error('Native prompt unavailable');};});
  const dialog=page.getByRole('dialog');
  const saveName=async name=>{await dialog.getByLabel('文件名称').fill(name);await dialog.getByRole('button',{name:'保存名称',exact:true}).click();};
  const confirmDelete=async()=>{await dialog.getByRole('button',{name:'移入回收站',exact:true}).click();};
  for(let i=0;;i++){try{await page.goto(url);break;}catch(e){if(i>20)throw e;await new Promise(r=>setTimeout(r,100));}}
  const rows=page.locator('.library-file');await expect(rows).toHaveCount(2);await page.locator('.text-line').first().waitFor();
  await rows.filter({hasText:'fixture-0.pdf'}).locator('.session').click();await expect(page.locator('#document-title')).toHaveText('fixture-0.pdf');await page.locator('.text-line').first().waitFor();
  await rows.filter({hasText:'fixture-0.pdf'}).locator('.file-more').click();await page.getByRole('menuitem',{name:'重命名',exact:true}).click();await saveName('自定义论文.pdf');
  await expect(rows.filter({hasText:'自定义论文.pdf'})).toHaveCount(1);await expect(page.locator('#document-title')).toHaveText('自定义论文.pdf');
  await page.reload();await page.locator('.text-line').first().waitFor();await expect(page.locator('#document-title')).toHaveText('自定义论文.pdf');
  await rows.filter({hasText:'自定义论文.pdf'}).locator('.file-more').click();await page.getByRole('menuitem',{name:'重命名',exact:true}).click();await dialog.getByRole('button',{name:'取消',exact:true}).click();await expect(rows.filter({hasText:'自定义论文.pdf'})).toHaveCount(1);
  await rows.filter({hasText:'自定义论文.pdf'}).locator('.file-more').click();await page.getByRole('menuitem',{name:'重命名',exact:true}).click();await saveName('fixture-0.pdf');await expect(page.locator('#document-title')).toHaveText('fixture-0.pdf');
  await rows.filter({hasText:'fixture-0.pdf'}).locator('.file-more').click();await page.getByRole('menuitem',{name:'重命名',exact:true}).click();
  await saveName('   ');await expect(dialog.getByRole('alert')).toContainText('名称不能为空');await dialog.getByRole('button',{name:'取消',exact:true}).click();
  await rows.filter({hasText:'fixture-0.pdf'}).locator('.file-more').click();await page.getByRole('menuitem',{name:'删除（移入回收站）'}).click();
  await dialog.getByRole('button',{name:'取消',exact:true}).click();await expect(rows).toHaveCount(2);
  await page.route('**/api/library/*/actions',r=>r.fulfill({status:409,contentType:'application/json',body:JSON.stringify({detail:'library_document_busy'})}));
  await rows.filter({hasText:'fixture-0.pdf'}).locator('.file-more').click();await page.getByRole('menuitem',{name:'删除（移入回收站）'}).click();await confirmDelete();
  await expect(dialog.getByRole('alert')).toContainText('正在处理');await expect(rows).toHaveCount(2);
  await dialog.getByRole('button',{name:'取消',exact:true}).click();await page.unroute('**/api/library/*/actions');
  await page.route('**/api/library/*/actions',r=>r.abort());
  await rows.filter({hasText:'fixture-0.pdf'}).locator('.file-more').click();await page.getByRole('menuitem',{name:'置顶',exact:true}).click();
  await expect(dialog.getByRole('alert')).toContainText('操作未完成');await expect(page.locator('#sessions')).not.toContainText('已置顶');
  await page.unroute('**/api/library/*/actions');await dialog.getByRole('button',{name:'置顶',exact:true}).click();await expect(dialog).not.toBeVisible();
  await rows.filter({hasText:'fixture-0.pdf'}).locator('.file-more').click();await page.getByRole('menuitem',{name:'取消置顶',exact:true}).click();await expect(dialog).not.toBeVisible();
  const first=rows.filter({hasText:'fixture-0.pdf'});const before=await page.locator('#document-title').textContent();
  await first.locator('.file-more').click();const menu=page.getByRole('menu',{name:'文件操作'});await expect(menu).toBeVisible();await page.screenshot({path:"var/maintenance/LIBRARY-20260927/menu-desktop.png"});
  expect(await page.locator('#document-title').textContent()).toBe(before);
  await page.locator('#pdf-scroll').evaluate(el=>el.dispatchEvent(new Event('scroll')));await page.evaluate(()=>window.dispatchEvent(new Event('resize')));await expect(menu).toBeVisible();
  await menu.getByRole('menuitem',{name:'置顶',exact:true}).click();await expect(rows.first()).toContainText('fixture-0.pdf');await expect(rows.first()).toContainText('已置顶');
  await page.reload();await page.locator('.text-line').first().waitFor();await expect(rows.first()).toContainText('已置顶');
  await first.locator('.file-more').click();await expect(menu).toContainText('阅读记录');await page.keyboard.press('Escape');await expect(menu).not.toBeVisible();
  await first.locator('.session').click();await expect(page.locator('#document-title')).toHaveText('fixture-0.pdf');
  await first.locator('.file-more').click();await menu.getByRole('menuitem',{name:'删除（移入回收站）'}).click();await confirmDelete();
  await expect(rows).toHaveCount(1);await expect(page.locator('#document-title')).toHaveText('fixture-1.pdf');
  await page.locator('#library-trash').click();await expect(rows).toHaveCount(1);await expect(rows.first()).toContainText('fixture-0.pdf');
  await rows.first().locator('.file-more').click();await menu.getByRole('menuitem',{name:'恢复到文件库'}).click();await expect(rows).toHaveCount(0);
  await page.locator('#library-trash').click();await expect(rows).toHaveCount(2);await expect(rows.first()).toContainText('已置顶');
  await page.setViewportSize({width:390,height:844});await page.locator('#library-toggle').click();
  await rows.first().locator('.file-more').click();await expect(menu).toBeVisible();await page.screenshot({path:"var/maintenance/LIBRARY-20260927/menu-narrow.png"});const box=await menu.boundingBox();assert.ok(box.x>=0&&box.x+box.width<=390);
  await menu.getByRole('menuitem',{name:'取消置顶'}).click();await expect(page.locator('#sessions')).not.toContainText('已置顶');
  await page.setViewportSize({width:1500,height:1000});
  for(let i=0;i<2;i++) {await rows.first().locator('.file-more').click();await menu.getByRole('menuitem',{name:'删除（移入回收站）'}).click();await confirmDelete();await expect(rows).toHaveCount(1-i);}
  await expect(page.locator('#sessions')).toContainText('文件库暂无文件');await expect(page.locator('#empty')).toBeVisible();
  await page.locator('#library-trash').click();await expect(rows).toHaveCount(2);assert.deepEqual(errors,[]);
 }finally{await browser.close();fixture.kill('SIGTERM');}
});
