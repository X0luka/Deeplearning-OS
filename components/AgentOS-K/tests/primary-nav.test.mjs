import test from 'node:test';
import {spawn} from 'node:child_process';
import {chromium,expect} from '@playwright/test';
test('persistent main navigation, conversation/draft restoration, compact viewport, embedded exclusion',{timeout:60000},async()=>{
 const child=spawn('.venv/bin/python',['tests/assistant_delete_fixture.py']);let log='';child.stderr.on('data',v=>log+=v);
 const url=await new Promise((resolve,reject)=>{child.stdout.on('data',v=>{const m=String(v).match(/FIXTURE=(\S+)/);if(m)resolve(m[1]);});child.on('exit',()=>reject(Error(log)));});
 const browser=await chromium.launch();
 try{const page=await browser.newPage({viewport:{width:1200,height:850}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(url+'/');const nav=page.getByRole('navigation',{name:'主导航',exact:true});await expect(nav.getByRole('link',{name:'返回主菜单'})).toBeVisible();
 await nav.getByRole('link',{name:'助手会话',exact:true}).click();await expect(page.locator('#title')).not.toHaveText('平台助手');const title=await page.locator('#title').textContent();
 await page.locator('#question').fill('navigation draft preserved');await nav.getByRole('link',{name:'返回主菜单'}).click();await expect(page).toHaveURL(url+'/');
 await nav.getByRole('link',{name:'助手会话',exact:true}).click();await expect(page.locator('#title')).toHaveText(title);await expect(page.locator('#question')).toHaveValue('navigation draft preserved');
 for(const label of ['数据与知识中心','API 与模型']){await nav.getByRole('link',{name:label,exact:true}).click();await expect(nav.getByRole('link',{name:label,exact:true})).toHaveAttribute('aria-current','page');await nav.getByRole('link',{name:'返回主菜单'}).click();}
 await page.setViewportSize({width:390,height:844});await nav.getByRole('link',{name:'助手会话',exact:true}).click();await expect(page.locator('#question')).toHaveValue('navigation draft preserved');await page.locator('#messages').evaluate(el=>el.scrollTop=el.scrollHeight);
 const box=await nav.boundingBox();expect(box.x).toBe(0);expect(box.width).toBe(60);expect(box.height).toBe(844);expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.screenshot({path:'var/maintenance/NAV-RAIL-20260929/narrow.png'});
 await page.evaluate(()=>document.body.classList.add('dark'));await page.setViewportSize({width:1200,height:850});await page.screenshot({path:'var/maintenance/NAV-RAIL-20260929/dark.png'});
 await page.route('**/assistant',async route=>{const response=await route.fetch();const html=await response.text();await route.fulfill({response,body:html.replace('"parent": ""','"parent": "http://localhost:8602"')});});
 await page.reload();await expect(page.locator('body')).toHaveClass(/embedded/);await expect(page.locator('.aos-primary-nav')).toHaveCount(0);
 expect(errors).toEqual([]);
 }finally{await browser.close();child.kill('SIGTERM');}
});
