// Intentionally synthetic data. This demo has no API, credentials or WeChat connection.
const chats = [
  {id:'team',name:'产品协作小组',kind:'group',icon:'协',desc:'6 位成员 · 产品与设计',preview:'周五评审，记得整理待办',messages:[
    ['09:10','小夏','text','这周先整理用户反馈，再确定第一版的功能范围。'],
    ['09:18','小林','text','收到，我把反馈按读取、搜索、导出三个场景分类。'],
    ['10:02','阿青','text','图片预览也很重要，找方案的时候可以直接看附件。'],
    ['10:20','小夏','text','本周的项目计划已经整理好了，大家可以先看一下。'],
    ['10:21','小夏','image','项目计划.svg'],
    ['10:24','小林','text','周五 15:00 做方案评审。我周四前整理好需求清单。'],
    ['10:26','阿青','text','我负责更新设计稿，周四下午发到群里，评审时一起确认。']]},
  {id:'lin',name:'小林',kind:'private',icon:'林',desc:'单聊 · 项目协作',preview:'需求清单已经整理好了',messages:[
    ['09:00','小林','text','昨天讨论的需求清单已经整理好了。'],
    ['09:05','我','text','谢谢！下午我看一下，确认后回复你。'],
    ['09:08','小林','text','好的，重点看一下导出范围和图片预览。']]},
  {id:'reading',name:'周末读书会',kind:'group',icon:'书',desc:'8 位成员 · 兴趣交流',preview:'周六一起分享读书笔记',messages:[
    ['08:30','小雨','text','这周读到一段很有意思的话，周六一起分享吧。'],
    ['08:36','阿舟','text','可以，我整理三条读书笔记。'],
    ['08:40','小雨','text','那就周六下午两点，还是老地方。']]},
  {id:'helper',name:'文件传输助手',kind:'private',icon:'↗',desc:'个人会话 · 示例',preview:'记录一个临时想法',messages:[['08:00','我','text','临时想法：把散落在聊天里的待办整理到一起。']]}
];
let selected=chats[0], kind='all', limit=4;
const drafts = {};
const $ = id => document.getElementById(id);
const text = (tag, value, cls) => {const el=document.createElement(tag);el.textContent=value;if(cls)el.className=cls;return el;};
function renderChats(){
  const q=$('contacts').value.trim().toLowerCase();
  const visible=chats.filter(c=>(kind==='all'||c.kind===kind)&&c.name.toLowerCase().includes(q));
  $('chat-count').textContent=`${visible.length} 个示例`;
  $('chats').replaceChildren();
  visible.forEach(c=>{const b=text('button','','chat'+(selected.id===c.id?' active':''));b.setAttribute('aria-pressed',String(selected.id===c.id));b.append(text('span',c.icon,'avatar'));const detail=document.createElement('div');detail.append(text('strong',c.name),text('small',c.preview));b.append(detail);b.onclick=()=>{selected=c;limit=4;$('query').value='';$('type').value='all';$('draft').value=drafts[c.id]||'';$('draft-status').textContent='草稿仅保留在当前页面，不会发送到微信。';renderChats();renderMessages();};$('chats').append(b);});
  if(!visible.length)$('chats').append(text('p','没有匹配的会话','muted'));
}
function filtered(){const q=$('query').value.trim().toLowerCase();return selected.messages.filter(m=>(m[1]+' '+m[3]).toLowerCase().includes(q)&&($('type').value==='all'||m[2]===$('type').value));}
function renderMessages(){
  $('title').textContent=selected.name;$('subtitle').textContent=selected.desc;
  const all=filtered();$('messages').replaceChildren();
  all.slice(-limit).forEach(m=>{const row=text('article','','message');row.append(text('span',m[1].slice(-1),'avatar'));const body=document.createElement('div');const meta=text('div',m[1],'message-meta');meta.append(text('time',m[0]));body.append(meta);
    if(m[2]==='image'){const b=text('button','','picture');b.setAttribute('aria-label','查看项目计划图片');const img=document.createElement('img');img.src='assets/board.svg';img.alt='模拟附件：秋日计划设计评审';b.append(img);b.onclick=()=>$('preview').showModal();body.append(b);}else body.append(text('div',m[3],'bubble'));
    row.append(body);$('messages').append(row);});
  $('more').hidden=all.length<=limit;$('empty').hidden=all.length>0;
}
document.querySelectorAll('[data-kind]').forEach(b=>b.onclick=()=>{kind=b.dataset.kind;document.querySelectorAll('[data-kind]').forEach(x=>x.classList.toggle('active',x===b));renderChats();});
$('contacts').oninput=renderChats;$('query').oninput=()=>{limit=4;renderMessages();};$('type').onchange=()=>{limit=4;renderMessages();};$('more').onclick=()=>{limit+=4;renderMessages();};
$('save').onclick=()=>{drafts[selected.id]=$('draft').value;$('draft-status').textContent=drafts[selected.id].trim()?'已保存当前会话草稿，仅在本页保留；未发送到微信。':'草稿已清空。';};
$('export').onclick=()=>{const blob=new Blob([JSON.stringify({demo:true,chat:selected.name,messages:filtered().map(m=>({time:m[0],sender:m[1],type:m[2],content:m[3]}))},null,2)],{type:'application/json;charset=utf-8'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=`示例-${selected.name}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
function agent(mode){
  $('prompt').textContent=mode==='todo'?'“整理产品协作小组最近的待办，列出负责人和时间。”':'“根据产品协作小组的讨论，帮我起草一条回复。”';
  $('answer').replaceChildren();
  if(mode==='todo'){const ul=document.createElement('ul');['小林：整理需求清单，周四前完成。','阿青：更新设计稿，周四下午发群。','全员：周五 15:00 参加方案评审。'].forEach(s=>ul.append(text('li',s)));$('answer').append(ul);}else $('answer').append(text('p','收到，周五 15:00 我会参加评审。需求清单和设计稿发出后，我会提前查看，并整理需要确认的问题。'));
}
document.querySelectorAll('[data-agent]').forEach(b=>b.onclick=()=>{document.querySelectorAll('[data-agent]').forEach(x=>x.classList.toggle('active',x===b));agent(b.dataset.agent);});
renderChats();renderMessages();agent('todo');
