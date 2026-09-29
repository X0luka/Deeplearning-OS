import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({headless:true});const page=await browser.newPage();
const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.route('**/api/sessions/*',async route=>{if(route.request().method()!=='GET')return route.continue();const response=await route.fetch();const data=await response.json();if(data.jobs){data.jobs=[{id:'mock',state:'completed',request:{question:'离线显示验证'},answer:{markdown:String.raw`## 公式显示验证
行内 \(d_{model}^{-0.5}\) 和 $x^2$。
\[lrate=d_{model}^{-0.5}\min(t^{-0.5},t w^{-1.5})\]
<img src="x" onerror="alert(1)">
`,anchors:[],limitations:[]}}];}await route.fulfill({response,json:data});});
try{await page.goto('http://127.0.0.1:8604');await expect(page.locator('.katex')).toHaveCount(3);await expect(page.locator('[onerror]')).toHaveCount(0);await expect(page.locator('.markdown h2')).toContainText('公式显示');if(errors.length)throw new Error(errors.join('\n'));console.log('Markdown, three math expressions, HTML sanitization passed; zero model calls.');await page.screenshot({path:'var/verification/reader3-browser/math.png'});}finally{await browser.close();}
