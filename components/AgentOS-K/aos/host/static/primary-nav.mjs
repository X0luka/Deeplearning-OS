const shapes={
 home:'M3 10 12 3l9 7M5 9v12h5v-7h4v7h5V9',
 chat:'M4 4h16v12H9l-5 4V4',
 data:'M3 6h7l2 3h9v11H3V6M3 9h18',
 model:'M8 3v3m8-3v3M8 18v3m8-3v3M3 8h3m-3 8h3m12-8h3m-3 8h3M6 6h12v12H6zM10 10h4v4h-4z',
 wiki:'m8 7-5 5 5 5m8-10 5 5-5 5m-3-12-2 14',
 paper:'M5 3h10l4 4v14H5V3m10 0v5h4M8 12h8m-8 4h8',
 app:'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z'
};
export function decorateNavigation(nav){
 for(const link of nav.querySelectorAll('a[data-nav-icon]')){
  const icon=document.createElementNS('http://www.w3.org/2000/svg','svg');icon.setAttribute('viewBox','0 0 24 24');icon.setAttribute('aria-hidden','true');
  const path=document.createElementNS(icon.namespaceURI,'path');path.setAttribute('d',shapes[link.dataset.navIcon]||shapes.app);icon.append(path);link.prepend(icon);
  link.setAttribute('aria-label',link.title||link.textContent);
  const target=new URL(link.href);const current=location.pathname;
  if(target.pathname==='/'?(current==='/'||current.startsWith('/history/')):current===target.pathname||current.startsWith(target.pathname+'/'))link.setAttribute('aria-current','page');
 }
}
export function mountAssistantNavigation(config){
 if(config.parent)return;
 document.body.classList.add('aos-assistant-shell');
 const nav=document.createElement('nav');nav.className='aos-primary-nav';nav.setAttribute('aria-label','主导航');
 const links=[['/','home','主页','返回主菜单'],['/assistant','chat','会话','助手会话'],['/data','data','资料','数据与知识中心'],['/settings/models','model','模型','API 与模型']];
 for(const p of config.providers||[])links.push(['/products/'+encodeURIComponent(p.id)+'/open',p.id==='paper'?'paper':p.id==='github-wiki'?'wiki':'app',p.id==='paper'?'Reader':p.id==='github-wiki'?'Wiki':p.name,'打开 '+p.name]);
 for(const [href,icon,label,title] of links){const a=document.createElement('a');a.href=href;a.dataset.navIcon=icon;a.textContent=label;a.title=title;if(href.startsWith('/products/')){a.target='_blank';a.rel='noopener';}nav.append(a);}
 document.body.prepend(nav);decorateNavigation(nav);
}
const workspace=document.querySelector('.aos-workspace-shell .aos-primary-nav');if(workspace)decorateNavigation(workspace);
