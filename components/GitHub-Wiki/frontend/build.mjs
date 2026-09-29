import {build} from 'esbuild';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
await mkdir('../wiki_app/static/reader',{recursive:true});
await build({entryPoints:['src/main.tsx'],bundle:true,minify:true,format:'esm',target:'es2022',outfile:'../wiki_app/static/reader/app.js'});
const worker = await readFile('node_modules/elkjs/lib/elk-worker.min.js','utf8');
await writeFile('../wiki_app/static/reader/elk-worker.min.js',worker.replace(/^\/\/[^\n]*[ \t]+$/gm,line=>line.trimEnd()));

await build({entryPoints:['src/inline.tsx'],bundle:true,minify:true,format:'esm',target:'es2022',outfile:'../wiki_app/static/reader/inline.js'});
