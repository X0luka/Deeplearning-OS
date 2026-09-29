import test from 'node:test';
import {spawn} from 'node:child_process';
import {chromium,expect} from '@playwright/test';
test('conversation deletion in actual UI, cancel, errors, persistence and current switch',{timeout:60000},async()=>{
 const child=spawn('.venv/bin/python',['tests/assistant_delete_fixture.py']);let logs='';child.stderr.on('data',v=>logs+=v);
 const url=await new Promise((resolve,reject)=>{child.stdout.on('data',v=>{const m=String(v).match(/FIXTURE=(\S+)/);if(m)resolve(m[1]);});child.on('exit',()=>reject(Error(logs)));});
 const browser=await chromium.launch();
 try{const page=await browser.newPage({viewport:{width:390,height:844}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(url+'/assistant');await expect(page.locator('#title')).not.toHaveText('平台助手');
 await page.locator('#sessions-toggle').click();const rows=page.locator('.session-row');await expect(rows).toHaveCount(2);
 const title=await page.locator('#title').textContent();await page.locator('#question').fill('preserved draft');
 await rows.first().locator('.session-more').click();await page.getByRole('menuitem',{name:'删除会话'}).click();const dialog=page.getByRole('dialog');
 await dialog.getByRole('button',{name:'取消',exact:true}).click();await expect(rows).toHaveCount(2);await expect(page.locator('#question')).toHaveValue('preserved draft');
 await page.route('**/api/v1/assistant/conversations/*',route=>route.request().method()==='DELETE'?route.fulfill({status:409,json:{detail:'conversation_busy'}}):route.continue());
 await rows.first().locator('.session-more').click();await page.getByRole('menuitem',{name:'删除会话'}).click();await dialog.getByRole('button',{name:'删除',exact:true}).click();await expect(dialog.getByRole('alert')).toContainText('请先停止');await expect(rows).toHaveCount(2);await page.unroute('**/api/v1/assistant/conversations/*');
 await dialog.getByRole('button',{name:'删除',exact:true}).click();await expect(dialog).not.toBeVisible();await expect(rows).toHaveCount(1);await expect(page.locator('#title')).not.toHaveText(title);
 await page.reload();await page.locator('#sessions-toggle').click();await expect(rows).toHaveCount(1);
 await rows.first().locator('.session-more').click();await page.getByRole('menuitem',{name:'删除会话'}).click();await dialog.getByRole('button',{name:'删除',exact:true}).click();await expect(dialog).not.toBeVisible();await expect(page.locator('#title')).toHaveText('新对话');await expect(page.locator('#question')).toHaveValue('');
 expect(errors).toEqual([]);
 }finally{await browser.close();child.kill('SIGTERM');}
});
