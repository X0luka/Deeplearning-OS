import {mkdir,copyFile} from 'node:fs/promises';
await mkdir('paper_reader/static/vendor',{recursive:true});
for(const name of ['pdf.mjs','pdf.worker.mjs']) await copyFile('node_modules/pdfjs-dist/build/'+name,'paper_reader/static/vendor/'+name);
await copyFile('node_modules/pdfjs-dist/LICENSE','paper_reader/static/vendor/LICENSE');

import {cp} from 'node:fs/promises';
await copyFile('node_modules/marked/lib/marked.esm.js','paper_reader/static/vendor/marked.esm.js');
await copyFile('node_modules/dompurify/dist/purify.es.mjs','paper_reader/static/vendor/purify.es.mjs');
await copyFile('node_modules/katex/dist/katex.mjs','paper_reader/static/vendor/katex.mjs');
await copyFile('node_modules/katex/dist/contrib/auto-render.mjs','paper_reader/static/vendor/auto-render.mjs');
await copyFile('node_modules/katex/dist/katex.min.css','paper_reader/static/vendor/katex.min.css');
await cp('node_modules/katex/dist/fonts','paper_reader/static/vendor/fonts',{recursive:true});

import {readFile,writeFile} from 'node:fs/promises';
const autoPath='paper_reader/static/vendor/auto-render.mjs';
await writeFile(autoPath,(await readFile(autoPath,'utf8')).replace("'../katex.mjs'","'./katex.mjs'"));

// Keep license texts with generated browser distributions.
for (const [pkg, name] of [['katex','LICENSE'],['marked','LICENSE.md'],['dompurify','LICENSE'],['dompurify','LICENSE-MPL']]) {
  await copyFile('node_modules/'+pkg+'/'+name,'paper_reader/static/vendor/'+pkg+'-'+name);
}
