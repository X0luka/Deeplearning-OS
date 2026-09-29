export function createConversationDelete({onDelete,host=document}) {
  const menu=host.createElement('div');menu.className='session-menu';menu.setAttribute('popover','auto');menu.setAttribute('role','menu');
  const action=host.createElement('button');action.textContent='删除会话';action.setAttribute('role','menuitem');menu.append(action);host.body.append(menu);
  const dialog=host.createElement('dialog');dialog.className='session-delete-dialog';dialog.setAttribute('aria-labelledby','delete-session-title');
  const title=host.createElement('h2');title.id='delete-session-title';title.textContent='删除会话';
  const text=host.createElement('p'),status=host.createElement('p');status.setAttribute('role','alert');
  const cancel=host.createElement('button');cancel.textContent='取消';
  const confirm=host.createElement('button');confirm.textContent='删除';
  const footer=host.createElement('div');footer.append(cancel,confirm);dialog.append(title,text,status,footer);host.body.append(dialog);
  let selected,trigger,pending=false;
  const close=()=>{if(!pending){dialog.close();trigger?.focus();}};
  cancel.onclick=close;dialog.addEventListener('cancel',e=>{e.preventDefault();close();});
  action.onclick=()=>{menu.hidePopover();text.textContent=`删除“${selected.title}”？该会话将从列表移除，未发送草稿会清除。论文、仓库和笔记不受影响；历史用量保留用于核对。`;status.textContent='';dialog.showModal();cancel.focus();};
  confirm.onclick=async()=>{if(pending)return;pending=true;confirm.disabled=cancel.disabled=true;status.textContent='正在删除…';try{await onDelete(selected);dialog.close();}catch(e){status.textContent=e.message==='conversation_busy'?'这条会话正在回答，请先停止生成后再删除。':'删除未完成：'+e.message;}finally{pending=false;confirm.disabled=cancel.disabled=false;}};
  return {button(c){const b=host.createElement('button');b.className='session-more';b.textContent='⋮';b.setAttribute('aria-label',c.title+' 的会话操作');b.setAttribute('aria-haspopup','menu');b.onclick=()=>{selected=c;trigger=b;menu.showPopover();const r=b.getBoundingClientRect();menu.style.left=Math.max(8,Math.min(r.right-menu.offsetWidth,host.defaultView.innerWidth-menu.offsetWidth-8))+'px';menu.style.top=Math.max(8,Math.min(r.bottom,host.defaultView.innerHeight-menu.offsetHeight-8))+'px';action.focus({preventScroll:true});};return b;}};
}
