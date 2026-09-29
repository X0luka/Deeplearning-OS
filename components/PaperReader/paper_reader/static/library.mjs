export function createLibrary({container, onOpen, onAction, host=document}) {
  const menu=host.createElement('div'); menu.className='file-menu';menu.setAttribute('popover','manual');menu.setAttribute('role','menu');menu.setAttribute('aria-label','文件操作');host.body.append(menu);
  const trash=host.createElement('button');trash.id='library-trash';trash.className='library-trash';container.after(trash);
  let data={items:[],trash:[]}, current=null, showTrash=false, trigger=null;
  function close(restore=false){if(menu.matches(':popover-open'))menu.hidePopover();trigger?.setAttribute('aria-expanded','false');if(restore)trigger?.focus({preventScroll:true});trigger=null;}
  host.addEventListener('pointerdown',event=>{if(!menu.contains(event.target)&&event.target!==trigger)close();},true);
  host.addEventListener('keydown',event=>{if(menu.matches(':popover-open')&&event.key==='Escape'){event.preventDefault();close(true);}});
  host.addEventListener('scroll',event=>{if(trigger && (event.target===host || event.target.contains?.(trigger)))positionMenu();},true);host.defaultView.addEventListener('resize',positionMenu);
  menu.addEventListener('keydown',event=>{
    const buttons=[...menu.querySelectorAll('button')],i=buttons.indexOf(host.activeElement);
    if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();buttons[event.key==='Home'?0:event.key==='End'?buttons.length-1:(i+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length]?.focus();}
    if(event.key==='Tab')close();
  });
  const dialog=host.createElement('dialog');dialog.className='library-dialog';dialog.setAttribute('aria-labelledby','library-action-title');
  const form=host.createElement('form');
  const heading=host.createElement('h2');heading.id='library-action-title';
  const description=host.createElement('p');
  const label=host.createElement('label');label.textContent='文件名称';
  const input=host.createElement('input');input.id='library-action-name';label.htmlFor=input.id;input.autocomplete='off';
  const status=host.createElement('p');status.setAttribute('role','alert');status.className='library-action-status';
  const buttons=host.createElement('div');buttons.className='library-action-buttons';
  const cancel=host.createElement('button');cancel.type='button';cancel.textContent='取消';
  const submit=host.createElement('button');submit.type='submit';submit.className='primary';
  buttons.append(cancel,submit);form.append(heading,description,label,input,status,buttons);dialog.append(form);host.body.append(dialog);
  let pending=false, selected=null, returnFocus=null;
  function dismiss(){if(!pending){dialog.close();returnFocus?.focus({preventScroll:true});}}
  cancel.onclick=dismiss;dialog.addEventListener('cancel',event=>{event.preventDefault();dismiss();});
  async function perform(){
    if(pending||!selected)return;
    const {action,item}=selected;const name=action==='rename'?input.value.trim():undefined;
    if(action==='rename'&&(!name||Array.from(name).length>180||/[\p{C}/\\]/u.test(name))){status.textContent='名称不能为空，最多180个字符，不能包含斜杠或控制字符。';input.focus();return;}
    if(action==='rename'&&name===item.title){dismiss();return;}
    pending=true;submit.disabled=true;cancel.disabled=true;input.disabled=true;status.textContent='正在保存…';dialog.setAttribute('aria-busy','true');
    try{await onAction(action,item,name);dialog.close();returnFocus?.focus({preventScroll:true});}
    catch(error){status.textContent='操作未完成：'+(error?.message||'请重试');}
    finally{pending=false;submit.disabled=false;cancel.disabled=false;input.disabled=false;dialog.removeAttribute('aria-busy');}
  }
  form.onsubmit=event=>{event.preventDefault();void perform();};
  function requestAction(action,item,button){
    if(dialog.open)return;
    returnFocus=button;selected={action,item};status.textContent='';input.value=item.title;
    const names={rename:'重命名',trash:'移入回收站',pin:'置顶',unpin:'取消置顶',restore:'恢复到文件库'};
    heading.textContent=names[action];submit.textContent=action==='rename'?'保存名称':names[action];
    description.textContent=action==='trash'?`将“${item.title}”移入回收站？原件、译文和笔记都会保留，可在回收站恢复。`:item.title;
    label.hidden=input.hidden=action!=='rename';dialog.showModal();
    if(action==='rename'){input.focus();input.select();}
    else if(action==='trash')cancel.focus();
    else void perform();
  }
  function openMenu(item,button){
    close();trigger=button;button.setAttribute('aria-expanded','true');menu.replaceChildren();
    const action=(label,fn)=>{const b=host.createElement('button');b.type='button';b.setAttribute('role','menuitem');b.textContent=label;b.onclick=()=>{close();fn();};menu.append(b);};
    if(showTrash) action('恢复到文件库',()=>requestAction('restore',item,button));
    else {
      action('重命名',()=>requestAction('rename',item,button));
      action(item.pinned?'取消置顶':'置顶',()=>requestAction(item.pinned?'unpin':'pin',item,button));
      if(item.sessions.length>1){const label=host.createElement('p');label.textContent='阅读记录';label.className='file-menu-label';menu.append(label);
        for(const s of item.sessions)action(`第 ${s.page} 页 · ${s.updated.slice(0,16).replace('T',' ')}`,()=>onOpen(s.id));}
      action('删除（移入回收站）',()=>requestAction('trash',item,button));
    }
    menu.showPopover();positionMenu();menu.querySelector('button')?.focus({preventScroll:true});
  }
  function positionMenu(){
    if(!trigger||!menu.matches(':popover-open'))return;
    const r=trigger.getBoundingClientRect();
    menu.style.left=`${Math.max(8,Math.min(r.right,host.defaultView.innerWidth-menu.offsetWidth-8))}px`;
    menu.style.top=`${Math.max(8,Math.min(r.bottom,host.defaultView.innerHeight-menu.offsetHeight-8))}px`;
  }
  function draw(){
    close();container.replaceChildren();const files=showTrash?data.trash:data.items;
    trash.textContent=showTrash?'← 返回文件库':`回收站${data.trash.length?' · '+data.trash.length:''}`;
    trash.setAttribute('aria-pressed',String(showTrash));
    if(showTrash){const title=host.createElement('p');title.className='file-menu-label';title.textContent='回收站 · 原件、译文与笔记保留';container.append(title);}
    if(!files.length){const empty=host.createElement('p');empty.className='hint';empty.textContent=showTrash?'回收站为空':'文件库暂无文件';container.append(empty);}
    for(const item of files){
      const row=host.createElement('div');row.className='library-file'+(item.document===current&&!showTrash?' active':'');row.dataset.document=item.document;
      const open=host.createElement('button');open.type='button';open.className='session';open.disabled=showTrash;open.title=item.title;
      const title=host.createElement('span');title.className='file-title';title.textContent=item.title;
      const meta=host.createElement('small');meta.textContent=`${item.pinned?'已置顶 · ':''}第 ${item.page} 页 · ${item.updated.slice(0,10)}${item.sessions.length>1?' · '+item.sessions.length+'条记录':''}`;
      open.append(title,meta);open.onclick=()=>onOpen(item.session);
      const more=host.createElement('button');more.type='button';more.className='file-more';more.textContent='⋮';more.setAttribute('aria-label',item.title+' 的更多操作');more.setAttribute('aria-haspopup','menu');more.setAttribute('aria-expanded','false');more.onclick=()=>openMenu(item,more);
      row.append(open,more);container.append(row);
    }
  }
  trash.onclick=()=>{showTrash=!showTrash;draw();};
  return {update(value,document){if(current===document&&JSON.stringify(data)===JSON.stringify(value))return;data=value;current=document;draw();},close};
}
