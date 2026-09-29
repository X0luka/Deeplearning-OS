// Keep code opaque to math rendering, including escaped template backticks.
export function prepareMarkdown(text, renderMath) {
  const code=[];
  text=text.replace(/```[\s\S]*?```|~~~[\s\S]*?~~~|(?<!\\)`(?:\\+`|[^`\n])+`/g, raw=>{
    let value=raw;
    if(!raw.startsWith('```')&&!raw.startsWith('~~~')&&raw.includes('\\`')){
      const inner=raw.slice(1,-1).replace(/\\+`/g,'`');
      const width=Math.max(1,...[...inner.matchAll(/`+/g)].map(m=>m[0].length))+1;
      const fence='`'.repeat(width); value=fence+' '+inner+' '+fence;
    }
    const id=code.push(value)-1;return 'AOSCODEPLACEHOLDER'+id+'END';
  });
  text=text.replace(/\$\$([\s\S]+?)\$\$|(?<![\\$])\$(?![{$])([^$\n]+?)\$(?!\$)/g,
    (_,display,inline)=>renderMath(display||inline,display!==undefined));
  return text.replace(/AOSCODEPLACEHOLDER(\d+)END/g,(_,i)=>code[Number(i)]);
}
