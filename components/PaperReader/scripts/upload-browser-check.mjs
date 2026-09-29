import {chromium,expect} from '@playwright/test';
import {writeFile} from 'node:fs/promises';
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1600,height:1000}});
try{
 await page.goto('http://127.0.0.1:8603');
 await page.locator('.text-line').first().waitFor();
 await page.locator('#upload').setInputFiles('var/evaluation/bert.pdf');
 await expect(page.locator('#document-title')).toHaveText('bert.pdf',{timeout:20000});
 await page.locator('#zoom').selectOption('1');
 await expect(page.locator('#pdf-page')).toHaveAttribute('data-zoom','1');
 await page.locator('#page-number').fill('3');await page.locator('#page-number').blur();
 await expect(page.locator('#pdf-page')).toHaveAttribute('data-page','3');
 const right=await page.locator('.text-line').evaluateAll(lines=>lines.findIndex(line=>{const b=JSON.parse(line.dataset.box);return b[0]>305&&line.textContent.length>35}));
 if(right<0)throw new Error('No right-column text located');
 const line=page.locator('.text-line').nth(right);await line.scrollIntoViewIfNeeded();
 const b=await line.boundingBox();await page.mouse.move(b.x+3,b.y+b.height/2);await page.mouse.down();await page.mouse.move(b.x+Math.min(b.width-3,150),b.y+b.height/2,{steps:15});await page.mouse.up();
 await expect(page.locator('#selection-bar')).toBeVisible();
 const selected=await page.locator('#question-selection').textContent();
 await page.locator('#selection-note').click();await page.locator('#note-body').fill('浏览器验证：双栏右侧选区。');await page.locator('#save-note').click();
 await expect(page.locator('#note-status')).toHaveText('已保存');await expect(page.locator('.note').first()).toContainText(selected);
 await page.locator('#upload').setInputFiles('var/evaluation/bert.pdf');
 await expect(page.locator('#notes-count')).toHaveText('0');
 const state=await (await page.request.get('http://127.0.0.1:8603/api/state')).json();
 if(state.documents.filter(d=>d.name==='bert.pdf').length!==1)throw new Error('Duplicate import duplicated original');
 await page.screenshot({path:'var/verification/browser/06-upload-double-column.png'});
 await writeFile('var/verification/browser/upload-result.json',JSON.stringify({passed:true,checks:['browser PDF upload','right-column mouse selection accepted by server','note retains selected text','duplicate import keeps one original and separate sessions'],model_calls:0},null,2));
 console.log('Browser upload, double-column selection and duplicate original checks passed.');
}finally{await browser.close();}
