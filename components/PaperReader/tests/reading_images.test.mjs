import test from 'node:test';
import assert from 'node:assert/strict';
import {chromium, expect} from '@playwright/test';
import {readFile} from 'node:fs/promises';

test('sandbox images open above the workspace, zoom, pan, close, restore focus and handle errors', async () => {
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1280,height:850}});
    const errors=[]; page.on('pageerror',e=>errors.push(e.message));
    const module=await readFile(new URL('../paper_reader/static/reading_images.mjs',import.meta.url),'utf8');
    const css=await readFile(new URL('../paper_reader/static/reader.css',import.meta.url),'utf8');
    await page.route('http://reader.test/**',route=>route.fulfill({contentType:route.request().url().endsWith('.mjs')?'text/javascript':'text/html',body:route.request().url().endsWith('.mjs')?module:`<style>${css}</style><iframe id="article" sandbox="allow-same-origin" style="width:600px;height:500px"></iframe>`}));
    await page.goto('http://reader.test/');
    await page.evaluate(async()=>{
      const {createReadingImageViewer}=await import('/images.mjs');window.viewer=createReadingImageViewer();window.navigated=0;
      const canvas=document.createElement('canvas');canvas.width=2400;canvas.height=1600;
      const ctx=canvas.getContext('2d');ctx.fillStyle='white';ctx.fillRect(0,0,2400,1600);ctx.fillStyle='black';ctx.font='90px sans-serif';ctx.fillText('Image detail 123',300,500);
      const f=document.querySelector('iframe');f.onload=()=>{window.viewer.bind(f.contentDocument,'/original.pdf');f.contentDocument.addEventListener('click',e=>{if(e.target.closest('a')){e.preventDefault();window.navigated++;}});};
      f.srcdoc=`<div style="height:700px"></div><figure><a href="https://example.org"><img alt="测试图片" style="width:350px" src="${canvas.toDataURL()}"></a><figcaption>图 1：细节</figcaption></figure><div style="height:900px"></div>`;
    });
    const article=page.frameLocator('iframe'), original=article.locator('img'), dialog=page.locator('dialog'), image=dialog.locator('img');
    await original.scrollIntoViewIfNeeded();
    const scroll=await page.evaluate(()=>document.querySelector('iframe').contentWindow.scrollY);
    await original.click();await expect(dialog).toBeVisible();await expect(dialog.locator('.image-status')).toContainText('2400 × 1600');
    assert.equal(await page.evaluate(()=>window.navigated),0);
    assert.ok((await image.boundingBox()).width>350);
    await dialog.getByText('原始尺寸',{exact:true}).click();
    assert.equal(Math.round((await image.boundingBox()).width),2400);
    const vp=dialog.locator('.image-viewport');await vp.evaluate(el=>el.scrollTo(0,0));
    const b=await vp.boundingBox();await page.mouse.move(b.x+350,b.y+200);await page.mouse.down();await page.mouse.move(b.x+100,b.y+80,{steps:8});await page.mouse.up();
    assert.ok(await vp.evaluate(el=>el.scrollLeft)>200);assert.ok(await vp.evaluate(el=>el.scrollTop)>100);
    await page.mouse.wheel(0,-120);await expect(dialog.locator('output')).toHaveText('115%');
    await page.keyboard.press('Escape');await expect(dialog).not.toBeVisible();
    assert.equal(await page.evaluate(()=>document.querySelector('iframe').contentWindow.scrollY),scroll);
    assert.equal(await original.evaluate(el=>el.ownerDocument.activeElement===el),true);
    await original.press('Enter');await expect(dialog).toBeVisible();await dialog.getByRole('button',{name:'关闭图片'}).click();
    await original.press('Space');await expect(dialog).toBeVisible();await page.mouse.click(1,1);await expect(dialog).not.toBeVisible();
    await page.setViewportSize({width:390,height:844});await original.click();await expect(dialog.locator('.image-status')).toContainText('2400 × 1600');
    assert.ok((await dialog.boundingBox()).width<=390);
    assert.equal(await dialog.evaluate(el=>el.scrollWidth<=el.clientWidth),true);
    await dialog.getByText('适应窗口',{exact:true}).click();
    assert.ok((await image.boundingBox()).width<=390);
    await page.evaluate(()=>window.viewer.close());await expect(dialog).not.toBeVisible();
    await original.evaluate(el=>el.src='data:image/png;base64,broken');await original.click();await expect(dialog.locator('.image-status')).toContainText('图片加载失败');
    await page.keyboard.press('Escape');assert.deepEqual(errors,[]);
  } finally {await browser.close();}
});
