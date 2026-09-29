import {chromium,expect} from '@playwright/test';
import {writeFile} from 'node:fs/promises';
const browser=await chromium.launch({headless:true});
const context=await browser.newContext({viewport:{width:1600,height:1000}});
const page=await context.newPage();
try{
  await page.goto('http://127.0.0.1:7777');
  const entry=page.getByRole('main').locator('a[href="/products/paper/open"]');
  await expect(entry).toBeVisible();
  const openingPromise=context.waitForEvent('page');
  await entry.click();
  const opening=await openingPromise;
  await expect(opening.locator('#product-link')).toBeVisible({timeout:60000});
  const readerPromise=context.waitForEvent('page');
  await opening.locator('#product-link').click();
  const reader=await readerPromise;
  await reader.locator('.text-line').first().waitFor();
  await expect(reader.locator('#model-status')).toContainText('已配置');
  await expect(reader.locator('#model-status')).toContainText('尚未启用');
  await expect(reader.locator('#document-title')).toHaveText('attention.pdf');
  await reader.screenshot({path:'var/verification/browser/04-live-reader.png'});
  await page.goto('http://127.0.0.1:7777/?product_id=paper');
  await expect(page.locator('table').first()).toContainText('masking');
  await page.screenshot({path:'var/verification/browser/05-host-history.png'});
  await writeFile('var/verification/browser/host-result.json',JSON.stringify({passed:true,checks:['host entry','product process launch','new-tab reader','configured but unapproved assistant','host archive visible'],model_calls:0},null,2));
  console.log('Live host launch, reader and archived history verified.');
}finally{await browser.close();}
