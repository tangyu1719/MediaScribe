/**
 * SuperBizAgent Web 前端主应用
 * 安全架构：统一认证管理 + RBAC权限控制 + 路由守卫
 */
(function(){
'use strict';

/** Vue 未挂载时须去掉 v-cloak，否则错误提示也会被 CSS 隐藏成白屏 */
function revealBootError(html){
  var el=document.getElementById('app');
  if(!el)return;
  el.removeAttribute('v-cloak');
  el.innerHTML=html;
}

// ==================== 初始化检查 ====================
if(typeof Vue==='undefined'){
  revealBootError('<div style="padding:32px 20px;max-width:520px;margin:24px auto;font-family:system-ui,Segoe UI,Roboto,sans-serif;line-height:1.55;color:#1e293b;background:#f8fafc;border-radius:12px;border:1px solid #e2e8f0"><h2 style="font-size:18px;margin:0 0 12px">无法加载 Vue（单页应用无法启动）</h2><p style="margin:0 0 10px;font-size:14px;color:#475569">当前页面依赖 CDN 上的 Vue 3。若公司网络或广告拦截导致脚本失败，你会看到未编译的模板占位符或空白。</p><p style="margin:0 0 10px;font-size:13px;color:#64748b"><b>请先确认：</b>已在本机启动后端（<code style="background:#e2e8f0;padding:2px 6px;border-radius:4px">web_rebuild_v2\\start_backend.bat</code> 或 <code style="background:#e2e8f0;padding:2px 6px;border-radius:4px">uvicorn</code> 监听 8000），浏览器访问 <code style="background:#e2e8f0;padding:2px 6px;border-radius:4px">http://127.0.0.1:8000/</code>（不要只用本地文件方式打开 html）。</p><p style="margin:0;font-size:13px;color:#64748b">若仍失败：换网络/代理、放行 <code style="background:#e2e8f0;padding:2px 6px;border-radius:4px">cdn.jsdelivr.net</code>，或将 <code style="background:#e2e8f0;padding:2px 6px;border-radius:4px">vue.global.prod.js</code> 放到 <code style="background:#e2e8f0;padding:2px 6px;border-radius:4px">frontend/vendor/</code> 并改为本地 script 引用。</p></div>');
  return;
}

// 检查认证模块是否加载
if(typeof AuthManager==='undefined'){
  console.error('AuthManager 未加载，认证功能不可用');
  revealBootError('<div style="padding:32px 20px;max-width:520px;margin:24px auto;font-family:system-ui,sans-serif;line-height:1.55;color:#1e293b;background:#f8fafc;border-radius:12px;border:1px solid #e2e8f0"><h2 style="font-size:18px;margin:0 0 12px">系统错误</h2><p style="margin:0;font-size:14px;color:#475569">认证模块加载失败。请确认已启动后端并访问 <code>http://127.0.0.1:8000/</code>。</p><p style="margin:12px 0 0"><a href="/login.html">前往登录</a></p></div>');
  return;
}

function safeJsonParse(raw,fallback){
  if(raw==null||raw==='')return fallback;
  try{
    const v=JSON.parse(String(raw));
    return v==null?fallback:v;
  }catch(_){return fallback}
}

/** 解析 API 响应；若返回 index.html（路由未注册/未重启后端）给出明确错误 */
async function parseApiJson(r){
  const ct=(r.headers.get('content-type')||'').toLowerCase();
  const text=await r.text();
  if(r.status===401){
    let d={detail:'未登录'};
    try{d=JSON.parse(text);}catch(_){}
    throw new Error(d.detail||'未登录，请先登录');
  }
  if(!ct.includes('json')){
    const t=text.trim();
    if(t.startsWith('<!')||t.startsWith('<html')){
      throw new Error('接口返回了 HTML 而非 JSON（多为后端未重启或路由缺失）。请重启 uvicorn 后 Ctrl+F5。');
    }
  }
  try{return JSON.parse(text);}catch(_){
    throw new Error('JSON 解析失败: '+String(text).slice(0,100));
  }
}

function showAuthRequiredMask(show){
  const mask=document.getElementById('auth-required-mask');
  if(!mask)return;
  mask.style.display=show?'flex':'none';
}

let _runtimeFaultShown=false;
function revealRuntimeFault(title,detail){
  if(_runtimeFaultShown)return;
  _runtimeFaultShown=true;
  console.error('[SBA]',title,detail);
  const esc=String(detail||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  revealBootError(
    '<div style="padding:32px 20px;max-width:560px;margin:24px auto;font-family:system-ui,sans-serif;line-height:1.55;color:#1e293b;background:#f8fafc;border-radius:12px;border:1px solid #e2e8f0">'
    +'<h2 style="font-size:18px;margin:0 0 12px">'+String(title||'界面运行异常')+'</h2>'
    +'<p style="margin:0 0 10px;font-size:13px;color:#475569;word-break:break-word">'+esc+'</p>'
    +'<p style="margin:12px 0 0;font-size:13px;color:#64748b">可尝试 <a href="/login.html">重新登录</a> 或 Ctrl+F5 强刷；若仍白屏请打开浏览器控制台查看 [SBA] 日志。</p></div>'
  );
}

// ==================== 任务注册表 UI 辅助（须在 setup 外，供模板稳定引用） ====================
function taskRegistryKindLabel(h){
  const kind=String(h&&h.task_kind||"main").toLowerCase();
  if(kind==="pipeline")return"链接流水线";
  if(kind==="main")return"AI 主任务";
  return kind||"任务";
}
function taskRegistryKindClass(h){
  const kind=String(h&&h.task_kind||"main").toLowerCase();
  return kind==="pipeline"?"kind-pipeline":"kind-main";
}

/** 任务中心字段中文映射（展示：中文（english_key）） */
const TASK_FIELD_LABELS={
  task_id:"任务 ID",session_id:"会话 ID",user_query:"用户目标",rewritten_query:"改写后目标",
  query_summary:"任务摘要",intent:"意图分类",status:"状态",group_seq:"组序号",
  total_duration_ms:"总耗时",total_token_count:"Token 总量",total_steps:"步骤总数",
  completed_steps:"已完成步骤",failed_steps:"失败步骤",sub_plans_count:"子计划数量",
  tool_outputs_count:"工具调用次数",snapshot_fixed_count:"固定层字段数",snapshot_open_count:"开放层字段数",
  started_at:"开始时间",ended_at:"结束时间",created_at:"创建时间",updated_at:"更新时间",
  link:"链接地址",platform:"平台",title:"标题",progress:"进度",log_count:"日志条数",source:"数据来源",
  task_kind:"任务类型",needs_multi_path:"多路径需求",needs_rag:"需要 RAG",async_pipeline_pending:"异步流水线待处理",
  pipeline_task_ids:"关联流水线 ID",result_msg_index:"结果消息索引",result_status:"结果状态",
  objective:"当前目标",current_assessment:"现状评估",progress_percent:"进度百分比",
  next_actions:"下一步动作",risk_flags:"风险标记",context_summary:"上下文摘要",
  tool_result_analysis:"工具结果分析",decision:"决策",confidence:"置信度",stop_reason:"停止原因",
  metadata:"元数据",keywords:"关键词",rewrite_state:"改写状态",rewrite_confidence:"改写置信度",
  tool_io_brief:"工具 IO 摘要",tool_name:"工具名称",tool_args:"工具参数",tool_result:"工具结果",
  react_round:"ReAct 轮次",sub_plan_id:"子计划 ID",input_preview:"输入预览",output_preview:"输出预览",
  brief:"结果摘要",at:"调用时间",step_id:"步骤 ID",step_name:"阶段名称",step_type:"步骤类型",
  duration_ms:"耗时",token_count:"Token",error_code:"错误码",error_message:"错误信息",
  input_payload:"输入载荷",output_payload:"输出载荷",parent_step_id:"父步骤 ID",
  schema_version:"Schema 版本",cost_ms:"耗时",timestamp:"时间戳",value:"值",key:"字段",
  fixed:"固定层",open:"开放层",tool_outputs:"工具链",steps:"步骤",sub_plans:"子计划",
  redis_present:"Redis 热缓存",mysql_synced:"MySQL 已同步",mysql_table:"MySQL 表",
  domain:"领域",module:"模块",doc_type:"文档类型",keyword1:"关键词1",keyword2:"关键词2",
  rewrite_snapshot:"改写快照",query:"查询",task_summary:"任务摘要",query_keywords:"查询关键词",
};
const TASK_STEP_TYPE_LABELS={
  retrieval:"检索",llm_call:"LLM 调用",llm:"LLM",tool_call:"工具调用",tool:"工具",
  api_call:"API 调用",summary:"摘要",reasoning:"推理",intent:"意图识别",rewrite:"问题改写",
  slot:"业务槽位",decompose:"任务分解",enhance:"意图增强",execute_prep:"执行准备",
  react_round:"ReAct 轮",rag_decision:"RAG 判定",plan:"规划",observe:"观察",act:"执行",
  orchestration:"编排",created:"已创建",running:"运行中",completed:"已完成",failed:"已失败",
};
function taskFieldLabel(key){
  const k=String(key||"").trim();
  if(!k)return "—";
  if(TASK_FIELD_LABELS[k])return TASK_FIELD_LABELS[k]+"（"+k+"）";
  const leaf=k.includes(".")?k.split(".").pop():k;
  if(leaf&&TASK_FIELD_LABELS[leaf])return TASK_FIELD_LABELS[leaf]+"（"+k+"）";
  return k;
}
function taskStepTypeLabel(t){
  const k=String(t||"").trim().toLowerCase();
  if(!k)return "—";
  const cn=TASK_STEP_TYPE_LABELS[k];
  return cn?cn+"（"+k+"）":taskFieldLabel(k);
}
function taskFieldDisplayValue(key,val){
  if(val===null||val===undefined||val==="")return "—";
  const k=String(key||"").toLowerCase();
  if(k==="status"||k.endsWith(".status")){
    const s=String(val).toLowerCase();
    const m={
      executing:"执行中",running:"运行中",pending:"待处理",completed:"已完成",failed:"已失败",
      cancelled:"已取消",closed:"已结案",resolved:"已解决",created:"已创建",summarizing:"摘要中",
      planning:"计划中",paused:"暂停中",abnormal:"异常中",ok:"成功",error:"错误",timeout:"超时",
    };
    return(m[s]||val)+"（"+val+"）";
  }
  if(typeof val==="boolean")return val?"是（true）":"否（false）";
  if(typeof val==="object"){try{return JSON.stringify(val,null,2);}catch(_){return String(val);}}
  return String(val);
}

// ==================== Vue 应用初始化 ====================
const{createApp,ref,reactive,nextTick,computed,onMounted,watch}=Vue;

const _sbaApp=createApp({setup(){
// ==================== 认证状态 ====================
const authUser=ref(AuthManager.state.user);
const isAuthenticated=ref(AuthManager.state.isAuthenticated);
const isAdmin=ref(AuthManager.state.isAdmin);

// 订阅认证状态变化
AuthManager.state.subscribe(function(state){
  authUser.value=state.user;
  isAuthenticated.value=state.isAuthenticated;
  isAdmin.value=state.isAdmin;
});

// ==================== 路由与页面 ====================
const page=ref("video");

// 需要登录的页面
const REQUIRES_AUTH_PAGES=['video','orch','chat','tasks','agpz','rag','rss','multimodal','cache','ops','webreplay','profile','settings'];
// 管理员页面
const ADMIN_PAGES=['iag'];

// 页面切换守卫
function guardPageSwitch(toPage){
  // 如果认证状态尚未初始化，尝试从 localStorage 重新读取
  if(!isAuthenticated.value){
    var token = localStorage.getItem('sba_token');
    var userStr = localStorage.getItem('sba_user');
    if(token && userStr){
      const user=safeJsonParse(userStr,null);
      if(user&&user.id){
        AuthManager.state.setAuth(token,user);
        isAuthenticated.value=true;
        isAdmin.value=!!(user.roles&&user.roles.includes('admin'));
        authUser.value=user;
      }
    }
  }
  // 检查是否需要登录
  if(REQUIRES_AUTH_PAGES.includes(toPage)){
    if(!isAuthenticated.value){
      showToastMsg('请先登录');
      showAuthRequiredMask(true);
      return false;
    }
    showAuthRequiredMask(false);
  }
  // 检查管理员权限
  if(ADMIN_PAGES.includes(toPage)){
    if(!isAdmin.value){
      showToastMsg('需要管理员权限');
      return false;
    }
  }
  return true;
}

// 监听页面切换
watch(page,function(newPage){
  if(!guardPageSwitch(newPage)){
    // 如果不允许切换，回到之前的页面
    page.value='video';
  }
});

// ==================== 向后兼容的辅助函数 ====================
function authBearerHeaders(){
  return AuthManager.state.token?{Authorization:'Bearer '+AuthManager.state.token}:{};
}
function authJsonHeaders(){
  return Object.assign({'Content-Type':'application/json'},authBearerHeaders());
}
function fmtApiErr(d,r){
  const det=d&&d.detail;
  if(typeof det==='string')return det;
  if(Array.isArray(det))return det.map(x=>(x&&x.msg)||String(x)).join('; ');
  if(det&&typeof det==='object')return JSON.stringify(det);
  return(r&&r.statusText)||'请求失败';
}
async function ldAuthUser(){
  try{
    // 使用认证模块的 token
    if(!AuthManager.state.token) return;
    const r = await fetch('/api/auth/me',{headers:authBearerHeaders()});
    const d = await r.json().catch(()=>({}));
    if(r.ok && d.user){ 
      authUser.value = d.user;
      // 同步到认证模块
      AuthManager.state.setAuth(AuthManager.state.token, d.user);
    } else if(r.status === 401) {
      // Token 过期，清除认证状态
      AuthManager.logout();
    }
  }catch(e){
    console.error('加载用户信息失败:', e);
  }
}

function doLogout(){
  // 使用认证模块的登出方法
  AuthManager.api.post('/api/auth/logout').finally(function(){
    AuthManager.logout();
  });
}
const authDisplayName=computed(()=>{
  const u=authUser.value;
  if(!u)return'';
  const nick=(u.nickname||'').trim();
  if(nick)return nick;
  return u.username||'';
});
const authAvatarChar=computed(()=>{
  const u=authUser.value;
  if(!u)return'?';
  const raw=(authDisplayName.value||u.username||'?').trim();
  if(!raw)return'?';
  const ch=Array.from(raw)[0]||'?';
  const cp=ch.codePointAt?ch.codePointAt(0):0;
  if(cp>=65&&cp<=90||cp>=97&&cp<=122)return String.fromCodePoint(cp<=90?cp:cp-32);
  return ch;
});
const prof=reactive({
  nickname:'',phone:'',oldPw:'',newPw:'',
  saving:false,msg:'',err:false,pwBusy:false,pwMsg:'',pwErr:false
});
function syncProfFromAuth(){
  const u=authUser.value;
  prof.nickname=(u&&u.nickname)||'';
  prof.phone=(u&&u.phone)||'';
  prof.oldPw='';prof.newPw='';
  prof.msg='';prof.err=false;prof.pwMsg='';prof.pwErr=false;
}
function goPersonalSettings(){
  try{document.querySelectorAll('details.user-dd').forEach(d=>{d.open=false})}catch(_){}
  syncProfFromAuth();
  page.value='profile';
}
async function saveProfile(){
  prof.saving=true;prof.msg='';prof.err=false;
  try{
    const r=await fetch('/api/auth/profile',{method:'PATCH',headers:authJsonHeaders(),body:JSON.stringify({nickname:(prof.nickname||'').trim(),phone:(prof.phone||'').trim()})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(fmtApiErr(d,r));
    if(d.user){
      authUser.value=d.user;
      try{localStorage.setItem('sba_user',JSON.stringify(d.user))}catch(_){}
    }
    prof.msg='已保存';prof.err=false;
  }catch(e){prof.msg=e.message||String(e);prof.err=true}
  finally{prof.saving=false}
}
async function savePassword(){
  prof.pwBusy=true;prof.pwMsg='';prof.pwErr=false;
  try{
    if(!(prof.oldPw&&prof.newPw))throw new Error('请填写当前密码和新密码');
    const r=await fetch('/api/auth/change-password',{method:'POST',headers:authJsonHeaders(),body:JSON.stringify({old_password:prof.oldPw,new_password:prof.newPw})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(fmtApiErr(d,r));
    prof.oldPw='';prof.newPw='';
    prof.pwMsg=d.message||'密码已修改';prof.pwErr=false;
  }catch(e){prof.pwMsg=e.message||String(e);prof.pwErr=true}
  finally{prof.pwBusy=false}
}
const portrait=reactive({
  display_name:'',timezone:'',occupation:'',tech_stack:'',communication_style:'',
  interests_projects:'',notes:'',language_pref:'',
  saving:false,msg:'',err:false
});
function applyPortraitFields(f){
  const o=f&&typeof f==='object'?f:{};
  portrait.display_name=String(o.display_name||'');
  portrait.timezone=String(o.timezone||'');
  portrait.occupation=String(o.occupation||'');
  portrait.tech_stack=String(o.tech_stack||'');
  portrait.communication_style=String(o.communication_style||'');
  portrait.interests_projects=String(o.interests_projects||'');
  portrait.notes=String(o.notes||'');
  portrait.language_pref=String(o.language_pref||'');
}
async function ldUserPortrait(){
  try{
    const r=await fetch('/api/auth/user-portrait',{headers:authBearerHeaders()});
    const d=await r.json().catch(()=>({}));
    if(r.ok&&d.fields)applyPortraitFields(d.fields);
    portrait.msg='';portrait.err=false;
  }catch(_){}
}
async function saveUserPortrait(){
  portrait.saving=true;portrait.msg='';portrait.err=false;
  try{
    const body={
      display_name:String(portrait.display_name||'').trim(),
      timezone:String(portrait.timezone||'').trim(),
      occupation:String(portrait.occupation||'').trim(),
      tech_stack:String(portrait.tech_stack||'').trim(),
      communication_style:String(portrait.communication_style||'').trim(),
      interests_projects:String(portrait.interests_projects||'').trim(),
      notes:String(portrait.notes||'').trim(),
      language_pref:String(portrait.language_pref||'').trim()
    };
    const r=await fetch('/api/auth/user-portrait',{method:'PUT',headers:authJsonHeaders(),body:JSON.stringify(body)});
    const d=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(fmtApiErr(d,r));
    if(d.fields)applyPortraitFields(d.fields);
    portrait.msg='已生成 user.md，对话时将随 agent.md 一并加载';portrait.err=false;
  }catch(e){portrait.msg=e.message||String(e);portrait.err=true}
  finally{portrait.saving=false}
}
function closeUserDd(ev){
  try{
    var t=ev&&ev.target;
    var el=t&&t.closest?t.closest('details.user-dd'):null;
    if(el)el.open=false;
    else document.querySelectorAll('details.user-dd').forEach(d=>{d.open=false});
  }catch(_){}
}
const menuMainBase=[
  {key:"video",label:"链接文档化"},{key:"orch",label:"工具"},{key:"chat",label:"AI 问答"},
  {key:"tasks",label:"任务中心"},
  {key:"agpz",label:"Agent 个性化设置"},
  {key:"rag",label:"RAG 知识库"},{key:"rss",label:"RSS 阅读"},{key:"multimodal",label:"多模态文档"},{key:"cache",label:"Redis 缓存"},
  {key:"ops",label:"OPS 运维"}
];
const menuMain=ref([]);
function updateMenuMain(){
  const a = menuMainBase ? menuMainBase.slice() : [];
  if(isAdmin.value){
    const ix=a.findIndex(m=>m.key==="rag");
    if(ix>=0)a.splice(ix,0,{key:"iag",label:"内部 Agent 配置"});
  }
  menuMain.value = a;
}
watch(isAdmin, updateMenuMain, {immediate: true});
const settingsOpen=ref((()=>{try{return localStorage.getItem("sba_settings_open")==="1"}catch(_){return false}})());
const webreplayOpen=ref((()=>{try{return localStorage.getItem("sba_webreplay_open")==="1"}catch(_){return true}})());
const wr=reactive({
  sec:(()=>{try{return localStorage.getItem("sba_webreplay_sec")||"scripts"}catch(_){return "scripts"}})(),
  scripts:[],
  selId:"",
  selDetail:null,
  loading:false,
  err:"",
  bridge:{extensionId:"",origin:""},
});
const wrMcpSnippet=computed(()=>{
  const id=(wr.bridge.extensionId||"").trim()||"<扩展ID>";
  const origin=(typeof location!=="undefined"&&location.origin)?location.origin:"http://127.0.0.1:8000";
  return [
    "// 在允许 externally_connectable 的页面控制台或本地脚本中：",
    "chrome.runtime.sendMessage('"+id+"', { method: 'list_scripts' }, console.log);",
    "chrome.runtime.sendMessage('"+id+"', { method: 'run_script', params: { name: '脚本名' } }, console.log);",
    "// 本站点 API（需登录）： "+origin+"/api/webreplay/scripts",
  ].join("\n");
});
const rss=reactive({
  feeds:[],
  items:[],
  st:{feed_count:0,item_count:0,unread_count:0,starred_count:0,last_sync:""},
  scheduler:{scheduler_running:false,default_cron:""},
  selFeedId:"",
  selItemId:"",
  newUrl:"",
  filterUnread:false,
  filterStarred:false,
  loading:false,
  busy:false,
  err:"",
});
function rssFmtTime(iso){
  if(!iso)return "";
  try{
    const d=new Date(iso);
    if(Number.isNaN(d.getTime()))return String(iso).slice(0,16);
    const pad=n=>String(n).padStart(2,"0");
    return d.getFullYear()+"-"+pad(d.getMonth()+1)+"-"+pad(d.getDate())+" "+pad(d.getHours())+":"+pad(d.getMinutes());
  }catch(_){return String(iso).slice(0,16)}
}
function rssFeedTitle(feedId){
  const f=(rss.feeds||[]).find(x=>x.id===feedId);
  return f?(f.title||f.url||feedId):"";
}
function rssArticleTitle(){
  if(rss.selFeedId){
    const f=(rss.feeds||[]).find(x=>x.id===rss.selFeedId);
    if(f)return (f.title||"文章列表");
  }
  return "全部文章";
}
async function ldRssFeeds(){
  const r=await fetch("/api/rss/feeds",{headers:authBearerHeaders()});
  const d=await parseApiJson(r);
  if(!r.ok)throw new Error(d.detail||"加载订阅失败");
  rss.feeds=d.feeds||[];
}
async function ldRssItems(){
  const qs=new URLSearchParams();
  if(rss.selFeedId)qs.set("feed_id",rss.selFeedId);
  if(rss.filterUnread)qs.set("unread_only","true");
  if(rss.filterStarred)qs.set("starred_only","true");
  const q=qs.toString();
  const r=await fetch("/api/rss/items"+(q?("?"+q):""),{headers:authBearerHeaders()});
  const d=await parseApiJson(r);
  if(!r.ok)throw new Error(d.detail||"加载文章失败");
  rss.items=d.items||[];
}
async function ldRssStats(){
  const r=await fetch("/api/rss/stats",{headers:authBearerHeaders()});
  const d=await parseApiJson(r);
  if(!r.ok)throw new Error(d.detail||"加载统计失败");
  rss.st=d||{feed_count:0,item_count:0,unread_count:0,starred_count:0,last_sync:""};
}
async function ldRssScheduler(){
  try{
    const r=await fetch("/api/rss/scheduler/status",{headers:authBearerHeaders()});
    const d=await parseApiJson(r);
    if(r.ok)rss.scheduler=d||{};
  }catch(_){}
}
async function ldRssAll(){
  rss.loading=true;rss.err="";
  try{
    await Promise.all([ldRssFeeds(),ldRssStats(),ldRssScheduler()]);
    await ldRssItems();
  }catch(e){rss.err=e.message||String(e)}finally{rss.loading=false}
}
function rssToggleFilter(kind){
  if(kind==="unread"){rss.filterUnread=!rss.filterUnread;if(rss.filterUnread)rss.filterStarred=false;}
  else if(kind==="star"){rss.filterStarred=!rss.filterStarred;if(rss.filterStarred)rss.filterUnread=false;}
  ldRssItems().catch(e=>{rss.err=e.message||String(e)});
}
async function rssToggleRead(it,ev){
  if(ev)ev.stopPropagation();
  if(!it||!it.id||rss.busy)return;
  rss.busy=true;
  try{
    const r=await fetch("/api/rss/items/"+encodeURIComponent(it.id)+"/read",{method:"POST",headers:authJsonHeaders(),body:JSON.stringify({read:!it.read})});
    const d=await parseApiJson(r);
    if(!r.ok)throw new Error(d.detail||"更新已读失败");
    it.read=!!(d.item&&d.item.read);
    await ldRssStats();
  }catch(e){rss.err=e.message||String(e)}finally{rss.busy=false}
}
async function rssToggleStar(it,ev){
  if(ev)ev.stopPropagation();
  if(!it||!it.id||rss.busy)return;
  rss.busy=true;
  try{
    const r=await fetch("/api/rss/items/"+encodeURIComponent(it.id)+"/star",{method:"POST",headers:authJsonHeaders(),body:JSON.stringify({starred:!it.starred})});
    const d=await parseApiJson(r);
    if(!r.ok)throw new Error(d.detail||"更新星标失败");
    it.starred=!!(d.item&&d.item.starred);
    await ldRssStats();
  }catch(e){rss.err=e.message||String(e)}finally{rss.busy=false}
}
async function rssExportOpml(){
  try{
    const r=await fetch("/api/rss/opml/export",{headers:authBearerHeaders()});
    if(!r.ok){const d=await parseApiJson(r);throw new Error(d.detail||"导出失败");}
    const blob=await r.blob();
    const a=document.createElement("a");
    a.href=URL.createObjectURL(blob);
    a.download="sba-rss-subscriptions.opml";
    a.click();
    URL.revokeObjectURL(a.href);
    showToastMsg("OPML 已导出");
  }catch(e){rss.err=e.message||String(e)}
}
async function rssImportOpmlFile(ev){
  const file=ev&&ev.target&&ev.target.files&&ev.target.files[0];
  if(!file)return;
  rss.busy=true;rss.err="";
  try{
    const text=await file.text();
    const r=await fetch("/api/rss/opml/import",{method:"POST",headers:authJsonHeaders(),body:JSON.stringify({content:text})});
    const d=await parseApiJson(r);
    if(!r.ok)throw new Error(d.detail||"导入失败");
    await ldRssAll();
    showToastMsg("OPML 导入：新增 "+(d.added||0)+"，跳过 "+(d.skipped||0));
  }catch(e){rss.err=e.message||String(e)}finally{rss.busy=false;if(ev&&ev.target)ev.target.value=""}
}
function rssTriggerOpmlImport(){const el=document.getElementById("rss-opml-file");if(el)el.click();}
function rssSelectFeed(id){
  rss.selFeedId=id||"";
  rss.selItemId="";
  ldRssItems().catch(e=>{rss.err=e.message||String(e)});
}
function rssSelectItem(id){rss.selItemId=id||"";}
function rssOpenDoc(it,ev){
  if(ev)ev.stopPropagation();
  if(!it)return;
  const url=outputMdPreviewUrl(it.doc_path||it.doc_filename||"");
  if(!url){showToastMsg("暂无 MD 文档");return}
  window.open(url,"_blank","noopener");
}
async function rssPollTaskDoc(taskId,maxMs){
  const limit=Number(maxMs)||300000;
  const t0=Date.now();
  while(Date.now()-t0<limit){
    const r=await fetch("/api/process/status/"+encodeURIComponent(taskId),{headers:authBearerHeaders()});
    const d=await parseApiJson(r);
    if(!r.ok)throw new Error(d.detail||"查询任务失败");
    if(d.status==="completed"&&d.doc_filename)return d;
    if(d.status==="failed")throw new Error(d.error||"沉淀失败");
    await new Promise(rs=>setTimeout(rs,1500));
  }
  return null;
}
async function rssEnqueueDoc(it,ev){
  if(ev)ev.stopPropagation();
  if(!it||!it.id||!it.link||rss.busy)return;
  rss.busy=true;rss.err="";
  it.doc_status="running";
  try{
    const r=await fetch("/api/rss/items/"+encodeURIComponent(it.id)+"/document",{method:"POST",headers:authJsonHeaders(),body:JSON.stringify({})});
    const d=await parseApiJson(r);
    if(!r.ok)throw new Error(d.detail||"提交沉淀失败");
    const taskId=d.task_id||"";
    showToastMsg("已提交链接沉淀，正在抓取全文…");
    const done=taskId?await rssPollTaskDoc(taskId):null;
    await ldRssItems();
    if(done&&done.doc_filename){
      const row=(rss.items||[]).find(x=>x.id===it.id);
      if(row){
        row.doc_filename=done.doc_filename;
        row.doc_path=done.doc_path||row.doc_path;
        row.doc_status="completed";
      }
      showToastMsg("MD 已生成，正在打开阅读页");
      rssOpenDoc({doc_path:done.doc_path,doc_filename:done.doc_filename});
    }else if(taskId){
      showToastMsg("沉淀仍在进行，请到「链接文档化」查看进度");
    }
  }catch(e){
    it.doc_status="";
    rss.err=e.message||String(e);
    showToastMsg(rss.err);
  }finally{rss.busy=false}
}
async function rssAddFeed(){
  const url=(rss.newUrl||"").trim();
  if(!url||rss.busy)return;
  rss.busy=true;rss.err="";
  try{
    const r=await fetch("/api/rss/feeds",{method:"POST",headers:authJsonHeaders(),body:JSON.stringify({url})});
    const d=await parseApiJson(r);
    if(!r.ok)throw new Error(d.detail||"添加失败");
    rss.newUrl="";
    rss.selFeedId=(d.feed&&d.feed.id)||"";
    await ldRssAll();
    showToastMsg("订阅已添加并同步");
  }catch(e){rss.err=e.message||String(e)}finally{rss.busy=false}
}
async function rssDeleteFeed(feedId){
  if(!feedId||rss.busy)return;
  if(!confirm("确定删除该订阅及其文章？"))return;
  rss.busy=true;rss.err="";
  try{
    const r=await fetch("/api/rss/feeds/"+encodeURIComponent(feedId),{method:"DELETE",headers:authBearerHeaders()});
    const d=await parseApiJson(r);
    if(!r.ok)throw new Error(d.detail||"删除失败");
    if(rss.selFeedId===feedId){rss.selFeedId="";rss.selItemId=""}
    await ldRssAll();
    showToastMsg("已删除订阅");
  }catch(e){rss.err=e.message||String(e)}finally{rss.busy=false}
}
async function rssSyncOne(feedId){
  if(!feedId||rss.busy)return;
  rss.busy=true;rss.err="";
  try{
    const r=await fetch("/api/rss/feeds/"+encodeURIComponent(feedId)+"/sync",{method:"POST",headers:authBearerHeaders()});
    const d=await parseApiJson(r);
    if(!r.ok)throw new Error(d.detail||"同步失败");
    await ldRssAll();
    showToastMsg("同步完成");
  }catch(e){rss.err=e.message||String(e)}finally{rss.busy=false}
}
async function rssSyncAll(){
  if(rss.busy)return;
  rss.busy=true;rss.err="";
  try{
    const r=await fetch("/api/rss/sync",{method:"POST",headers:authBearerHeaders()});
    const d=await parseApiJson(r);
    if(!r.ok)throw new Error(d.detail||"同步失败");
    await ldRssAll();
    const fail=d.fail_count||0;
    showToastMsg(fail?("同步完成，"+fail+" 个源失败"):("已同步 "+(d.ok_count||0)+" 个源"));
  }catch(e){rss.err=e.message||String(e)}finally{rss.busy=false}
}
const navCollapsed=ref((()=>{try{return localStorage.getItem("sba_nav_collapsed")==="1"}catch(_){return false}})());
function toggleNav(){
  requestAnimationFrame(()=>{
    navCollapsed.value=!navCollapsed.value;
    nextTick(()=>{try{localStorage.setItem("sba_nav_collapsed",navCollapsed.value?"1":"0")}catch(_){}});
  });
}
const chatSbCollapsed=ref((()=>{try{return localStorage.getItem("sba_chat_sb_collapsed")==="1"}catch(_){return false}})());
function toggleChatSb(){
  requestAnimationFrame(()=>{
    chatSbCollapsed.value=!chatSbCollapsed.value;
    nextTick(()=>{try{localStorage.setItem("sba_chat_sb_collapsed",chatSbCollapsed.value?"1":"0")}catch(_){}});
  });
}
const wfs=ref([]);async function ldWfs(){try{const r=await fetch('/api/workflow/selector');const d=await r.json();wfs.value=d.workflows||[];if(!v.wf&&wfs.value.length)v.wf=wfs.value[0].key}catch(e){}}

/* ══ P1 链接文档化 ══ */
const v=reactive({wf:"",link:"",fs:true,fp:"",html:true,submitting:false,submitPulse:false,pg:0,stage:"就绪",stxt:"就绪",sd:"i",qs:"0",pr:"",sp:false,rd:null,htmlStat:"",htmlMsg:"",subtitle:true,cookies:"",comments:{enabled:false,count:10,sort:"hot"}});
const videoSubTab=ref("single");
const subForm=reactive({profile_url:"",display_name:"",submitting:false,error:""});
const subList=ref([]);
const subSelId=ref("");
const subDigest=reactive({digest_md:"",rag_degraded:false,digest_id:""});
const subProfile=reactive({profile_md:"",profile_md_path:"",busy:false,profile_doc_id:""});
const subViewTab=ref("digest");
const subSelRow=computed(()=>(subList.value||[]).find(s=>s.subscription_id===subSelId.value)||null);
function subFmtTime(iso){
  if(!iso)return"";
  try{
    const d=new Date(iso);
    if(Number.isNaN(d.getTime()))return String(iso).slice(0,16);
    return d.toLocaleString("zh-CN",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"});
  }catch(_){return String(iso).slice(0,16)}
}
async function selectSubscription(id){
  subSelId.value=id;
  await loadSubDigest(id);
}
async function ldSubscriptions(){
  subForm.error="";
  try{
    const r=await fetch("/api/subscriptions",{headers:authBearerHeaders()});
    if(!r.ok){const e=await r.json().catch(()=>({}));subForm.error=(e.detail&&e.detail.message)||e.detail||("HTTP "+r.status);return;}
    const d=await r.json();
    subList.value=d.items||[];
  }catch(e){subForm.error=String(e);}
}
async function addSubscription(){
  subForm.error="";subForm.submitting=true;
  try{
    const r=await fetch("/api/subscriptions",{method:"POST",headers:authJsonHeaders(),body:JSON.stringify({platform:"xiaohongshu",profile_url:subForm.profile_url.trim(),display_name:(subForm.display_name||"").trim()})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok){subForm.error=(d.detail&&d.detail.message)||d.detail||("HTTP "+r.status);return;}
    subForm.profile_url="";subForm.display_name="";
    showToastMsg("订阅已添加，正在首次同步…");
    await ldSubscriptions();
    const sid=(d.subscription_id||"").trim();
    if(sid){
      subSelId.value=sid;
      await syncSubscription(sid);
    }
  }catch(e){subForm.error=String(e);}finally{subForm.submitting=false;}
}
async function syncSubscription(id){
  try{
    showToastMsg("同步已启动…");
    const r=await fetch("/api/subscriptions/"+encodeURIComponent(id)+"/sync",{method:"POST",headers:authBearerHeaders()});
    const d=await r.json();
    if(!r.ok){alert((d.detail&&d.detail.message)||d.detail||"同步失败");return;}
    showToastMsg("同步完成："+(d.status||""));
    await ldSubscriptions();
    if(d.digest_id)await loadSubDigest(id);
  }catch(e){alert(String(e));}
}
async function syncAllSubscriptions(){
  try{
    showToastMsg("全部同步已启动…");
    const r=await fetch("/api/subscriptions/sync-all",{method:"POST",headers:authBearerHeaders()});
    if(!r.ok){alert("同步失败 HTTP "+r.status);return;}
    showToastMsg("全部同步完成");
    await ldSubscriptions();
  }catch(e){alert(String(e));}
}
async function loadSubDigest(subscriptionId){
  try{
    const r=await fetch("/api/subscriptions/digests/latest?subscription_id="+encodeURIComponent(subscriptionId),{headers:authBearerHeaders()});
    if(!r.ok){subDigest.digest_md="";return;}
    const d=await r.json();
    subDigest.digest_md=d.digest_md||"";
    subDigest.rag_degraded=!!d.rag_degraded;
    subDigest.digest_id=d.digest_id||"";
  }catch(e){console.error(e);}
}
async function loadSubProfile(subscriptionId){
  try{
    const r=await fetch("/api/subscriptions/"+encodeURIComponent(subscriptionId)+"/profile/latest",{headers:authBearerHeaders()});
    if(!r.ok){subProfile.profile_md="";subProfile.profile_md_path="";return;}
    const d=await r.json();
    const doc=(d.profile_doc)||{};
    subProfile.profile_md=doc.profile_md||"";
    subProfile.profile_md_path=doc.profile_md_path||"";
    subProfile.profile_doc_id=doc.profile_doc_id||"";
  }catch(e){console.error(e);}
}
async function runCreatorProfile(subscriptionId){
  if(subProfile.busy)return;
  subProfile.busy=true;subViewTab.value="profile";
  try{
    showToastMsg("UP 画像流水线已启动（五阶段）…");
    const r=await fetch("/api/subscriptions/"+encodeURIComponent(subscriptionId)+"/profile/run",{method:"POST",headers:authBearerHeaders()});
    const d=await r.json();
    if(!r.ok){alert((d.detail&&d.detail.message)||d.error||d.detail||"画像失败");return;}
    showToastMsg("画像完成："+(d.status||""));
    await loadSubProfile(subscriptionId);
  }catch(e){alert(String(e));}finally{subProfile.busy=false;}
}
async function pauseSubscription(id){
  await fetch("/api/subscriptions/"+encodeURIComponent(id),{method:"PATCH",headers:authJsonHeaders(),body:JSON.stringify({status:"paused"})});
  await ldSubscriptions();
}
async function resumeSubscription(id){
  await fetch("/api/subscriptions/"+encodeURIComponent(id),{method:"PATCH",headers:authJsonHeaders(),body:JSON.stringify({status:"active"})});
  await ldSubscriptions();
}
async function deleteSubscription(id){
  if(!confirm("确定删除该订阅？"))return;
  await fetch("/api/subscriptions/"+encodeURIComponent(id),{method:"DELETE",headers:authBearerHeaders()});
  await ldSubscriptions();
}
function renderSubDigestMd(md){
  const raw=String(md||"");
  try{
    if(typeof marked!=="undefined"&&typeof DOMPurify!=="undefined")return DOMPurify.sanitize(marked.parse(raw));
    if(typeof marked!=="undefined")return marked.parse(raw);
  }catch(_){}
  return raw.replace(/</g,"&lt;").replace(/\n/g,"<br>");
}
const vec=reactive({ok:false,ver:"-",lat:0,err:""});
const taskQueue=ref([]);
const logFocusId=ref("");
const outDirInp=ref(null);
const toast=reactive({show:false,msg:""});
const uiOverlay=reactive({z:10060});
function bumpModalLayer(){
  uiOverlay.z=Math.min(10120,uiOverlay.z+2);
  const root=document.getElementById("sba-modal-root");
  if(root)root.style.setProperty("--modal-z",String(uiOverlay.z));
}
function closeAllPageOverlays(opts){
  const except=(opts&&opts.except)||"";
  if(except!=="skill")skillImport.show=false;
  if(except!=="kbMeta"){kbImportMeta.show=false;kbImportMeta.busy=false;}
  if(except!=="kbBrowse")kbBrowse.show=false;
  if(except!=="mmBrowse")mmBrowse.show=false;
  if(except!=="modalOut")modalOut.show=false;
  if(except!=="modalArtifact")modalArtifact.show=false;
  if(except!=="hist")showHist.value=false;
  if(except!=="chatExpand")chatExpandOpen.value=false;
  if(except!=="taskHistModal"){c.taskHistModalOpen=false;c.taskHistModalRow=null;c.taskHistModalFromChat=false;}
}
function openPageOverlay(kind,openFn){
  closeAllPageOverlays({except:kind});
  bumpModalLayer();
  if(typeof openFn==="function")openFn();
}
const modalOut=reactive({show:false,path:"",files:[],newAbs:""});
let toastT=null;let procEs=null;let queueTimer=null;let vecTimer=null;
function showToastMsg(msg){toast.msg=msg;toast.show=true;clearTimeout(toastT);toastT=setTimeout(()=>{toast.show=false},2400)}
async function ldVec(){try{const r=await fetch("/api/vector/health");const d=await r.json();vec.ok=!!d.milvus_ok;vec.ver=d.version||"-";vec.lat=d.latency_ms||0;vec.err=d.error||""}catch(e){vec.ok=false;vec.err=String(e)}}
async function fetchJsonSafe(url, opts){
  const r=await fetch(url, opts);
  const text=await r.text();
  let data={};
  if(text){
    try{data=JSON.parse(text)}catch(_){
      const brief=text.replace(/\s+/g," ").slice(0,120);
      throw new Error(r.ok?("响应非 JSON："+brief):("HTTP "+r.status+"："+brief));
    }
  }
  if(!r.ok){
    const detail=data.detail||data.message||data.error||("HTTP "+r.status);
    throw new Error(typeof detail==="string"?detail:JSON.stringify(detail));
  }
  return data;
}
async function pollQueue(){
  try{
    const d=await fetchJsonSafe("/api/process/queue");
    taskQueue.value=sortTaskQueueFifo(d.tasks||[]);
  }catch(e){console.warn("[pollQueue]",e.message||e)}
}
/** 链接分析队列：最近提交/重新执行在最左（queue_seq / updated_at 降序） */
function sortTaskQueueFifo(tasks){
  return (Array.isArray(tasks)?tasks:[]).slice().sort((a,b)=>{
    const sa=Number(a.queue_seq??a.priority??0);
    const sb=Number(b.queue_seq??b.priority??0);
    if(sa!==sb)return sb-sa;
    const ua=String(a.updated_at||a.created_at||"");
    const ub=String(b.updated_at||b.created_at||"");
    return ub.localeCompare(ua);
  });
}
function pendingQueueIndex(taskId){
  const pending=taskQueue.value.filter(x=>(x.status||"")==="pending");
  return pending.findIndex(x=>x.task_id===taskId);
}
function isFirstPendingTask(taskId){return pendingQueueIndex(taskId)===0;}
function isLastPendingTask(taskId){
  const pending=taskQueue.value.filter(x=>(x.status||"")==="pending");
  const i=pendingQueueIndex(taskId);
  return i>=0&&i===pending.length-1;
}
async function moveQueueTask(taskId, direction){
  try{
    await fetch("/api/process/queue/move", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({task_id: taskId, direction})});
    await pollQueue();
  }catch(e){console.error("移动任务失败:", e)}
}
async function cancelQueueTask(taskId){
  if(!confirm("确定要取消此任务吗？"))return;
  try{
    await fetch("/api/process/queue/cancel", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({task_id: taskId})});
    await pollQueue();
  }catch(e){console.error("取消任务失败:", e)}
}
async function cleanupQueueTasks(){
  try{
    const r=await fetch("/api/process/queue/cleanup", {method: "POST"});
    const d=await r.json();
    if(d.removed>0)showToastMsg(`已清理 ${d.removed} 个完成任务`);
    await pollQueue();
  }catch(e){console.error("清理任务失败:", e)}
}
function taskQueueFmtTime(iso){return subFmtTime(iso)}
function taskShowReadBadge(t){return String((t&&t.status)||"").toLowerCase()==="completed"}
function taskIsUnread(t){
  if(!taskShowReadBadge(t))return false;
  const rs=String((t&&t.read_status)||"unread").toLowerCase();
  return rs!=="read";
}
function taskReadLabel(t){return taskIsUnread(t)?"未读":"已读"}
async function markQueueTaskRead(t){
  if(!t||!taskIsUnread(t))return;
  try{
    await fetchJsonSafe("/api/process/queue/read", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({task_id:t.task_id})});
    patchQueueTaskMetrics(t.task_id,{read_status:"read"});
  }catch(e){console.error("标记已读失败:",e);showToastMsg("标记已读失败")}
}
async function deleteQueueTask(taskId){
  if(!taskId)return;
  try{
    await fetchJsonSafe("/api/process/queue/delete", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({task_id:taskId})});
    if(logFocusId.value===taskId){
      logFocusId.value="";
      logs.value=[];
      if(procEs){procEs.close();procEs=null}
    }
    await pollQueue();
    showToastMsg("已移除卡片");
  }catch(e){console.error("移除卡片失败:",e);showToastMsg("移除失败")}
}
function shortLink(s,n){if(!s)return"";const t=String(s);return t.length>n?t.slice(0,n)+"…":t}
function clampTaskText(s,maxLen=80){
  if(!s)return"";
  const t=String(s).trim().replace(/\s+/g," ");
  const cleaned=t.replace(/_/g," ");
  return cleaned.length>maxLen?cleaned.slice(0,maxLen)+"…":cleaned;
}
const HIST_STATUS_LABEL={completed:"已完成",failed:"失败",cancelled:"已取消",running:"运行中",started:"已启动",in_progress:"处理中",pending:"待处理"};
function histStatusLabel(t){const s=(t&&t.status)||"pending";return HIST_STATUS_LABEL[s]||s}
function histPipelineSteps(t){
  if(Array.isArray(t&&t.pipeline_steps)&&t.pipeline_steps.length)return t.pipeline_steps;
  return [];
}
function histFailedStageLabel(t){
  if(!t)return"";
  if(t.failed_stage_label)return t.failed_stage_label;
  const fid=(t.failed_stage||"").trim();
  if(!fid)return"";
  const hit=(histPipelineSteps(t)||[]).find(s=>s.id===fid);
  return hit&&hit.label?hit.label:fid;
}
function histResumeHint(t){
  const label=histFailedStageLabel(t);
  if(!label)return"";
  if(t.status!=="failed"&&t.status!=="cancelled")return"";
  return `将从「${label}」断点恢复，已完成步骤复用缓存参数`;
}
async function copyHistLink(t){
  const link=(t&&t.link)||"";
  if(!link){alert("无链接");return}
  try{
    if(navigator.clipboard&&navigator.clipboard.writeText)await navigator.clipboard.writeText(link);
    else throw new Error("clipboard");
    showToastMsg("链接已复制");
  }catch(_){prompt("复制链接：",link)}
}
function pathBasename(p){if(!p)return"";return String(p).replace(/\\/g,"/").split("/").filter(Boolean).pop()||""}
function isJunkTaskTitle(s){
  const t=String(s||"").trim();
  if(!t||t.length<2)return true;
  if(t.length>120)return true;
  const low=t.toLowerCase();
  if(low.startsWith("http://")||low.startsWith("https://"))return true;
  if(low.includes("xsec_token")||low.includes("xsec_source"))return true;
  if(/^[A-Za-z0-9_-]{24,}$/.test(t))return true;
  return false;
}
function taskContentKind(t){
  if(!t)return"图文";
  const ct=String(t.content_type||"").trim();
  if(ct==="视频"||ct.toLowerCase()==="video")return"视频";
  if(ct==="图文"||ct.toLowerCase()==="graphic")return"图文";
  const rt=String(t.route_type||"").toLowerCase();
  if(rt.includes("video")&&!rt.includes("image"))return"视频";
  if(rt.includes("image")||rt.includes("article")||rt==="xiaohongshu"||rt==="douyin_image")return"图文";
  const link=String(t.link||"").toLowerCase();
  if(link.includes("douyin.com/note"))return"图文";
  if((t.platform||"")==="B站")return"视频";
  return ct||"图文";
}
function taskRouteLabel(t){
  const plat=(t&&t.platform)||detectPlatform(t&&t.link)||"链接";
  return String(plat).trim()+"·"+taskContentKind(t);
}
function taskRouteTagClass(t){
  const plat=String((t&&t.platform)||"").trim();
  if(plat==="小红书")return"q-route-xhs";
  if(plat==="抖音")return"q-route-dy";
  if(plat==="B站")return"q-route-bili";
  return"q-route-default";
}
function taskCardLinkTitle(t){
  if(!t)return"";
  const lt=String(t.link_title||"").trim();
  if(lt&&!isJunkTaskTitle(lt))return clampTaskText(lt,72);
  const dt=String(t.doc_title||"").trim();
  if(dt&&!isJunkTaskTitle(dt))return clampTaskText(dt,72);
  const tt=String(t.title||"").trim();
  if(tt&&!isJunkTaskTitle(tt))return clampTaskText(tt,72);
  return ((t.task_id||t.id||"")+"").slice(0,8);
}
function taskCardHeadTitle(t){return taskCardLinkTitle(t)}
function taskCardDocSubTitle(t){
  if(!t)return"";
  const linkTitle=String(t.link_title||"").trim();
  const docTitle=String(t.doc_title||"").trim();
  if(docTitle&&linkTitle&&docTitle!==linkTitle&&!isJunkTaskTitle(docTitle))
    return clampTaskText("摘要标题："+docTitle,64);
  return "";
}
function taskCardSubTitle(t){return taskCardDocSubTitle(t)}
function taskCardMetricsLine(t){
  if(!t)return"";
  const st=String(t.status||"").toLowerCase();
  if(st!=="completed")return"";
  const dur=Number(t.total_duration_ms||0);
  const tok=Number(t.total_token_count||0);
  const article=Number(t.article_char_count||0);
  const summary=Number(t.summary_char_count||0);
  if(!dur&&!tok)return"";
  const parts=[];
  if(dur>0)parts.push("耗时 "+formatDuration(dur));
  if(tok>0){
    let tokLine="Token "+tok.toLocaleString("zh-CN");
    if(article>0||summary>0)tokLine+="（"+article.toLocaleString("zh-CN")+"+"+summary.toLocaleString("zh-CN")+"）";
    parts.push(tokLine);
  }
  return parts.join(" · ");
}
function patchQueueTaskMetrics(tid,patch){
  if(!tid||!patch||typeof patch!=="object")return;
  const i=taskQueue.value.findIndex(x=>x.task_id===tid);
  if(i<0)return;
  const cur=taskQueue.value[i];
  taskQueue.value.splice(i,1,{...cur,...patch});
}
function taskFeishuHint(t){
  if(!t)return"";
  const st=String(t.feishu_status||"");
  if(!st||st==="completed"||st==="skipped")return"";
  if(st==="async_pending")return"飞书上传中…";
  if(st==="failed"){
    const msg=String(t.feishu_message||"").trim();
    return msg?"飞书失败："+msg:"飞书上传失败";
  }
  return String(t.feishu_message||st);
}
function taskCoverUrl(t){return String((t&&t.cover_url)||"").trim()}
function onTaskCoverError(ev,t){
  if(ev&&ev.target){ev.target.style.display="none"}
}
function taskHtmlPending(t){
  if(!t||taskHtmlReady(t))return false;
  const s=String(t.html_status||"");
  return s==="async_pending"||s==="pending"||s==="running";
}
function histStatusStyle(t){
  const s=t&&t.status||"pending";
  if(s==="completed")return{color:"var(--ok)"};
  if(s==="failed"||s==="cancelled")return{color:"var(--err)"};
  if(s==="running"||s==="started"||s==="in_progress")return{color:"var(--warn)"};
  if(s==="pending")return{color:"var(--warn)"};
  return{color:"var(--t3)"};
}
function histTaskTitle(t){return taskCardLinkTitle(t)}
function histTaskSubTitle(t){return taskCardSubTitle(t)}
function taskHasMd(t){return!!(t&&(t.doc_path||t.doc_filename))}
function taskHasHtml(t){return!!(t&&t.html_path)}
function taskHtmlReady(t){
  if(!t)return false;
  if((t.html_path||"").trim())return true;
  const s=String(t.html_status||"");
  return s==="completed"||s==="ok";
}
function histHasMd(t){return taskHasMd(t)}
function histHasHtml(t){return taskHasHtml(t)}
function outputHttpUrl(path){
  const b=pathBasename(path);
  if(!b)return"";
  // 产物直链走 /output 白名单，禁止附带 sba_token（旧后端会进 Casbin 报 403）
  return "/output/"+encodeURIComponent(b);
}
function outputMdPreviewUrl(path){
  const b=pathBasename(path);
  if(!b)return"";
  const low=b.toLowerCase();
  if(low.endsWith(".md")||low.endsWith(".txt")||low.endsWith(".markdown")||low.endsWith(".mdx"))
    return "/preview/md.html?file="+encodeURIComponent(b);
  return outputHttpUrl(path);
}
function artifactBrowserUrl(path){
  return outputMdPreviewUrl(path)||outputHttpUrl(path);
}
const modalArtifact=reactive({show:false,label:"",items:[]});
async function openLocalOutput(path,action){
  const p=(path||"").trim();
  if(!p){showToastMsg("路径不可用");return}
  try{
    const r=await fetch("/api/output/open-local",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:p,action:action||"file"})});
    let d={};try{d=await r.json()}catch(_){}
    if(!r.ok)throw new Error(typeof d.detail==="string"?d.detail:(d.detail&&JSON.stringify(d.detail))||d.error||r.statusText);
    showToastMsg(action==="folder"||action==="explorer"||action==="reveal"?"已在资源管理器中定位":"已用本机程序打开");
  }catch(e){showToastMsg("打开失败："+(e.message||String(e)))}
}
async function copyArtifactItem(it){
  const p=(it&&it.path||"").trim();
  if(!p)return;
  try{
    await navigator.clipboard.writeText(p);
    showToastMsg((it.label||"路径")+" 路径已复制");
  }catch(_){alert(p)}
}
async function openArtifactModalExplorer(){
  const first=modalArtifact.items&&modalArtifact.items[0];
  if(first&&first.path)await openLocalOutput(first.path,"explorer");
}
async function openTaskArtifactsLocation(t){
  const md=(t.doc_path||t.doc_filename||"").trim();
  const html=(t.html_path||"").trim();
  if(!md&&!html){showToastMsg("尚无 MD/HTML 产物");return}
  const items=[];
  if(md)items.push({label:"MD",path:md,url:artifactBrowserUrl(md)});
  if(html)items.push({label:"HTML",path:html,url:outputHttpUrl(html)});
  modalArtifact.items=items;
  modalArtifact.label="任务产物";
  openPageOverlay("modalArtifact",()=>{modalArtifact.show=true;});
  await openLocalOutput(md||html,"explorer");
}
function showArtifactModal(path,label){
  const p=(path||"").trim();
  modalArtifact.items=[{label:label||"产物",path:p,url:artifactBrowserUrl(p)}];
  modalArtifact.label=label||"产物";
  openPageOverlay("modalArtifact",()=>{modalArtifact.show=true;});
}
async function openTaskMd(t){
  const path=(t.doc_path||t.doc_filename||"").trim();
  if(!path){showToastMsg("MD 尚未生成");return}
  const url=outputMdPreviewUrl(path);
  if(url){window.open(url,"_blank","noopener");return}
  await openLocalOutput(path,"file");
}
async function openTaskHtml(t){
  const path=(t.html_path||"").trim();
  if(!path){showToastMsg("HTML 尚未生成");return}
  const url=outputHttpUrl(path);
  if(url){window.open(url,"_blank");return}
  await openLocalOutput(path,"file");
}
async function openTaskHtmlExplorer(t){
  const path=(t.html_path||"").trim();
  if(!path)return;
  showArtifactModal(path,"HTML");
  await openLocalOutput(path,"explorer");
}
function openHistMd(t){return openTaskMd(t)}
function openHistHtml(t){return openTaskHtml(t)}
function openHistHtmlExplorer(t){return openTaskHtmlExplorer(t)}
function detectPlatform(link){const u=(link||"").toLowerCase();if(u.includes("douyin.com")||u.includes("iesdouyin")||u.includes("tiktok"))return"抖音";if(u.includes("bilibili.com")||u.includes("b23.tv"))return"B站";return"小红书"}
const procActive=new Set(["pending","queued","started","running","downloading","transcribing","generating","extracting","ocr","comments","assembling","consolidating","feishu_upload"]);
const logs=ref([]);
function aLog(ts,lv,msg){logs.value.push({timestamp:ts,level:lv,message:msg});nextTick(()=>{const el=document.getElementById("lb");if(el)el.scrollTop=el.scrollHeight})}
function logRowClass(lv){const x=String(lv||"").toUpperCase();if(x==="ERROR"||x==="ERR")return"err";if(x==="WARN"||x==="WARNING")return"warn";return"info"}
function connectLogEs(tid){
  if(procEs){procEs.close();procEs=null}
  if(!tid)return;
  logs.value=[];
  var token = localStorage.getItem('sba_token');
  procEs=new EventSource("/api/process/logs/"+tid+(token?'?sba_token='+encodeURIComponent(token):''));
  procEs.addEventListener("log",e=>{try{const d=JSON.parse(e.data||"{}");aLog(d.timestamp,d.level,d.message)}catch(ex){aLog("","ERROR","日志解析失败："+(ex.message||String(ex)))}});
  procEs.addEventListener("progress",e=>{
    const d=JSON.parse(e.data);
    v.pg=d.progress||0;v.stage=d.stage||"";
    patchQueueTaskMetrics(logFocusId.value,{
      progress:d.progress,stage:d.stage,status:d.status,
      total_duration_ms:d.total_duration_ms,total_token_count:d.total_token_count,
      article_char_count:d.article_char_count,summary_char_count:d.summary_char_count,
    });
  });
  procEs.addEventListener("complete",e=>{
    const d=JSON.parse(e.data);v.pg=100;v.stage="完成";v.stxt="完成";v.sd="o";v.rd=d.doc_filename;v.htmlStat=d.html_status||"";v.htmlMsg=d.html_message||"";
    patchQueueTaskMetrics(d.task_id||logFocusId.value,{
      status:"completed",progress:100,stage:"完成",read_status:"unread",
      total_duration_ms:d.total_duration_ms,total_token_count:d.total_token_count,
      article_char_count:d.article_char_count,summary_char_count:d.summary_char_count,
    });
    if(procEs){procEs.close();procEs=null}
    showToastMsg("任务已完成");pollQueue();
    if(d.html_status==="async_pending")pollHtmlStatus(d.task_id);
  });
  procEs.addEventListener("error",e=>{
    v.sd="f";
    try{
      const d=JSON.parse(e.data||"{}");
      patchQueueTaskMetrics(logFocusId.value,{
        status:"failed",
      });
    }catch(_){}
    if(procEs){procEs.close();procEs=null}
    pollQueue();
  });
}
function onLogFocusChange(){connectLogEs(logFocusId.value)}
function selectQueueTask(tid){logFocusId.value=tid;connectLogEs(tid)}
async function startProc(){
  const linkTrim=(v.link||"").trim();
  if(!linkTrim){alert("请输入链接");return}
  v.submitting=true;
  try{
    const hd=await fetchJsonSafe("/api/link/url-hash?link="+encodeURIComponent(linkTrim));
    const cleanLink=hd.link||linkTrim;
    const platform=hd.platform||detectPlatform(cleanLink);
    const qd=await fetchJsonSafe("/api/process/queue");
    const sameHash=(qd.tasks||[]).filter(t=>t.url_hash===hd.url_hash);
    const running=sameHash.filter(t=>procActive.has(t.status||""));
    if(running.length){if(!confirm("该链接已有任务在执行中，仍要再提交？")){v.submitting=false;return}}
    const payload={
      platform,
      link:cleanLink,
      user_prompt:v.pr||"",
      comments:v.comments||{enabled:false,count:10,sort:"hot"}
    };
    const d=await fetchJsonSafe("/api/process/start",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    v.submitPulse=true;setTimeout(()=>{v.submitPulse=false},700);
    showToastMsg(d.reused?"同链接已复用原卡片继续处理":"已加入处理队列");
    v.link="";
    logFocusId.value=d.task_id;
    connectLogEs(d.task_id);
    await pollQueue();
  }catch(e){showToastMsg("提交失败："+(e.message||String(e)));aLog("","ERROR",e.message||String(e))}
  finally{v.submitting=false}
}
function clrV(){v.link="";v.pr="";logs.value=[];v.pg=0;v.stage="就绪";v.stxt="就绪";v.sd="i";v.rd=null;v.htmlStat="";v.htmlMsg="";if(procEs){procEs.close();procEs=null}}
let linkPipelinePrefTimer=null;
async function persistLinkPipelinePrefs(){
  clearTimeout(linkPipelinePrefTimer);
  linkPipelinePrefTimer=setTimeout(async()=>{
    try{
      await fetch("/api/settings/link-pipeline-prefs",{
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({
          feishu_sync_enabled:!!v.fs,
          feishu_default_folder_path:v.fp||"",
          longpage_html_enabled:!!v.html,
        }),
      });
    }catch(_){}
  },350);
}
async function ldLinkPipelinePrefs(){
  try{
    const r=await fetch("/api/settings/link-pipeline-prefs");
    if(!r.ok)return;
    const d=await r.json();
    if(d.feishu_sync_enabled!=null)v.fs=!!d.feishu_sync_enabled;
    if(d.feishu_default_folder_path!=null)v.fp=String(d.feishu_default_folder_path||"");
    if(d.longpage_html_enabled!=null)v.html=!!d.longpage_html_enabled;
  }catch(_){}
}
function pollHtmlStatus(taskId){let count=0;const timer=setInterval(async()=>{try{const r=await fetch("/api/process/status/"+taskId);const d=await r.json();v.htmlStat=d.html_status||"";v.htmlMsg=d.html_message||"";if(d.html_status&&d.html_status!=="async_pending"||++count>60){clearInterval(timer)}}catch(e){clearInterval(timer)}},3000)}
async function openOut(){try{const r=await fetch("/api/output/path");const d=await r.json();openPageOverlay("modalOut",()=>{modalOut.path=d.path||"";modalOut.files=d.files||[];modalOut.newAbs=modalOut.path;modalOut.show=true;});}catch(e){showToastMsg("读取输出路径失败")}}
async function copyOutPath(){try{await navigator.clipboard.writeText(modalOut.path);showToastMsg("路径已复制")}catch(_){alert(modalOut.path)}}
async function saveServerOutPath(){const p=(modalOut.newAbs||"").trim();if(!p){showToastMsg("请输入绝对路径");return}try{const r=await fetch("/api/output/config",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:p})});const d=await r.json();if(!r.ok)throw new Error(d.detail||d.error||"保存失败");modalOut.path=d.path;showToastMsg("服务端输出目录已更新")}catch(e){showToastMsg(e.message||String(e))}}
async function configureOutputFolder(){
  if(typeof window.showDirectoryPicker==="function"){
    try{const dir=await window.showDirectoryPicker();showToastMsg("已选择本机文件夹「"+dir.name+"」。产物仍写入服务端输出根；本选择可作本机整理参考。");try{localStorage.setItem("sba_client_out_label",dir.name)}catch(_){ }return}catch(e){if((e&&e.name)==="AbortError")return}
  }
  outDirInp.value&&outDirInp.value.click();
}
function onOutDirNative(e){
  const files=e.target.files;
  if(files&&files.length){
    const name=files[0].webkitRelativePath?files[0].webkitRelativePath.split("/")[0]:files[0].name;
    showToastMsg("已选择本机文件夹「"+name+"」（"+files.length+" 项）。产物仍写入服务端；纯 Web 无法把本机绝对路径交给后端。");
  }
  e.target.value="";
}

/* ══ 历史/队列 ══ */
const showHist=ref(false);
function openHistPanel(){openPageOverlay("hist",()=>{showHist.value=true;ldHist();});}
const ht=reactive({tasks:[]});const hs=reactive({total:0,completed:0,failed:0,pending:0,in_progress:0});let histTimer=null;
const histLogPanel=reactive({
  open:false,loading:false,taskId:"",title:"",source:"",logCount:0,tab:"text",
  textLogs:[],spans:[],errors:[],spanTask:null
});
let histLogEs=null;
function histLogSourceLabel(src){
  const m={memory:"运行中内存",history:"历史 JSON",file:"JSONL 落盘",merged:"多源合并"};
  return m[src]||src||"—";
}
function closeHistLogPanel(){
  histLogPanel.open=false;
  if(histLogEs){histLogEs.close();histLogEs=null}
}
async function ldHist(){
  try{
    const[r1,r2]=await Promise.all([fetch('/api/history'),fetch('/api/history/stats')]);
    const d1=await r1.json();const d2=await r2.json();
    ht.tasks=d1.tasks||[];Object.assign(hs,d2);
    const pending=ht.tasks.filter(t=>taskHtmlPending(t)&&(t.id||"").trim());
    if(pending.length){
      await Promise.all(pending.map(async t=>{
        try{
          const r=await fetch("/api/process/status/"+encodeURIComponent(t.id));
          if(!r.ok)return;
          const d=await r.json();
          if(d.html_path)t.html_path=d.html_path;
          if(d.html_status)t.html_status=d.html_status;
          if(d.html_message)t.html_message=d.html_message;
        }catch(_){}
      }));
    }
  }catch(e){}
}
async function openHistLogs(t){
  const tid=(t&&t.id||"").trim();
  if(!tid){showToastMsg("无任务 ID，无法加载日志");return}
  histLogPanel.open=true;
  histLogPanel.loading=true;
  histLogPanel.taskId=tid;
  histLogPanel.title=histTaskTitle(t)||"";
  histLogPanel.tab="text";
  histLogPanel.textLogs=[];
  histLogPanel.spans=[];
  histLogPanel.errors=[];
  histLogPanel.spanTask=null;
  if(histLogEs){histLogEs.close();histLogEs=null}
  try{
    const token=localStorage.getItem("sba_token");
    const r=await fetch("/api/history/logs/"+encodeURIComponent(tid)+(token?"?sba_token="+encodeURIComponent(token):""));
    const d=await r.json();
    if(!r.ok)throw new Error(typeof d.detail==="string"?d.detail:"无法加载日志");
    histLogPanel.textLogs=d.text_logs||d.logs||[];
    histLogPanel.spans=d.spans||[];
    histLogPanel.errors=d.errors||[];
    histLogPanel.spanTask=d.span_task||null;
    histLogPanel.source=d.source||"merged";
    histLogPanel.logCount=d.log_count||histLogPanel.textLogs.length;
    if((t.status==="running"||t.status==="started"||procActive.has(t.status||""))){
      histLogEs=new EventSource("/api/process/logs/"+tid+(token?"?sba_token="+encodeURIComponent(token):""));
      histLogEs.addEventListener("log",e=>{
        const row=JSON.parse(e.data);
        histLogPanel.textLogs.push({timestamp:row.timestamp,level:row.level,message:row.message});
        histLogPanel.logCount=histLogPanel.textLogs.length;
      });
      histLogEs.addEventListener("complete",()=>{if(histLogEs){histLogEs.close();histLogEs=null}ldHist();openHistLogs(t)});
    }
  }catch(e){
    showToastMsg("日志加载失败："+(e.message||String(e)));
    histLogPanel.textLogs=[{timestamp:"",level:"ERROR",message:e.message||String(e)}];
  }finally{
    histLogPanel.loading=false;
  }
}
async function restartTask(t,action){
  const platform=detectPlatform(t&&t.link)||(t&&t.platform)||"小红书";
  const taskId=((t&&t.task_id)||(t&&t.id)||"").trim();
  const body={link:t.link,platform,action:action||"resume"};
  if(taskId)body.task_id=taskId;
  const r=await fetch("/api/history/restart",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const d=await r.json().catch(()=>({}));
  if(!r.ok){showToastMsg(d.detail||d.error||"重新执行失败");return}
  if(d.task_id){
    logFocusId.value=d.task_id;
    connectLogEs(d.task_id);
  }
  await pollQueue();
  ldHist();
  const from=d.resume_from?` · 从「${histFailedStageLabel(t)||d.resume_from}」恢复`:"";
  const reused=d.reused?"（本卡片）":"";
  showToastMsg(`${action==="resume"?"断点恢复":"重新执行"}已提交${reused}${from}`);
}
async function stopTask(t){await fetch('/api/history/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({link:t.link})});ldHist()}
async function moveTask(t,dir){await fetch('/api/history/move',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({link:t.link,direction:dir})});ldHist()}
async function deleteTask(t){if(!confirm('删除任务：'+t.link))return;await fetch('/api/history/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({link:t.link})});ldHist()}
async function clearCompleted(){if(!confirm('确定清理所有已完成任务？'))return;await fetch('/api/history/clear-completed',{method:'POST'});ldHist()}
async function regenerateHtml(t){
  if(!t.doc_filename){alert('没有MD文件，无法生成HTML');return}
  if(!confirm('确定重新生成HTML？'))return
  try{
    const r=await fetch('/api/history/regenerate-html',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({link:t.link,doc_filename:t.doc_filename})})
    const d=await r.json()
    if(d.ok){showToastMsg('HTML重新生成中...');ldHist()}
    else{alert(d.error||'生成失败')}
  }catch(e){alert('请求失败: '+e.message)}
}

/* ══ P2 编排 API（兼容保留）；工具页用 SKILL ══ */
const o=reactive({nds:[]});async function ldNodes(){try{const r=await fetch('/api/orchestration/nodes');const d=await r.json();o.nds=d.nodes||[]}catch(e){}}

const skills=ref([]);
const skillCmdDraft=reactive({});
const sk=reactive({name:"",description:"",command:"",body_md:""});
const skillImport=reactive({show:false});
function openSkillImport(){openPageOverlay("skill",()=>{skillImport.show=true;});}
function closeSkillImport(){skillImport.show=false;}
const builtinTools=ref([]);
const mcpDiscovered=ref([]);
const mcpByServer=ref({});
const mcpVendors=ref([]);
const orchToolSearch=ref("");
const orchBoardTab=ref("all");
function _orchCat(){return window.SBA_ORCH_CATALOG||null;}
function orchSkillSearchDoc(s){
  const p=skillDescParts(s);
  return [s.name,s.command,skillAliasCn(s),p.zh,p.en,s.preview].filter(Boolean).join(" ");
}
function orchMatchToolItem(fields,q){
  const cat=_orchCat();
  if(!cat||!String(q||"").trim())return true;
  return cat.matchOrchSearch(fields,q);
}
const skillsFiltered=computed(()=>{
  const q=orchToolSearch.value;
  return (skills.value||[]).filter(s=>orchMatchToolItem({name:s.name,aliasCn:skillAliasCn(s),description:orchSkillSearchDoc(s),command:s.command||""},q));
});
const mcpDiscoveredFiltered=computed(()=>{
  const q=orchToolSearch.value;
  return (mcpDiscovered.value||[]).filter((mt,i)=>orchMatchToolItem({name:mt.name,description:mt.description||"",server:mt.server||""},q));
});
const mcpEnabledListFiltered=computed(()=>{
  const q=orchToolSearch.value;
  return mcpEnabledList.value.filter(es=>orchMatchToolItem({name:es.alias,aliasCn:mcpAliasCn(es),description:es.summary||""},q));
});
function orchBoardSkillAlias(s){
  const b=s&&s.board;
  if(b&&b.alias_cn)return b.alias_cn;
  return skillAliasCn(s);
}
function orchBoardCatalogItems(){
  const items=[];
  (skills.value||[]).forEach(s=>{
    const b=s.board||null;
    const tags=b&&Array.isArray(b.tags)?b.tags:[];
    items.push({
      type:"skill",typeLabel:"SKILL",id:s.id,name:s.name,
      aliasCn:orchBoardSkillAlias(s),
      description:orchSkillSearchDoc(s),
      command:s.command||"",
      board:b,
      boardCategory:b&&b.category,
      tags,
      categoryHint:b&&b.summary,
      key:"skill:"+s.id,raw:s,
    });
  });
  mcpEnabledList.value.forEach(es=>{
    items.push({type:"mcp-server",typeLabel:"MCP服务",id:es.alias,name:es.alias,aliasCn:mcpAliasCn(es),description:es.summary||"",key:"mcp-srv:"+es.alias,raw:es});
  });
  (mcpDiscovered.value||[]).forEach((mt,i)=>{
    items.push({type:"mcp",typeLabel:"MCP工具",id:mcpDiscKey(mt,i),name:mt.name||"—",description:mt.description||"",server:mt.server||"",key:mcpDiscKey(mt,i),raw:mt,idx:i});
  });
  return items;
}
const orchBoardTotalCount=computed(()=>orchBoardCatalogItems().length);
const orchBoardFilteredItems=computed(()=>{
  const tab=orchBoardTab.value;
  const q=orchToolSearch.value;
  return orchBoardCatalogItems().filter(it=>{
    if(tab==="skill"&&it.type!=="skill")return false;
    if(tab==="mcp"&&it.type==="skill")return false;
    return orchMatchToolItem(it,q);
  });
});
const orchBoardByCategory=computed(()=>{
  const cat=_orchCat();
  if(!cat)return [];
  const buckets={};
  cat.ORCH_CATEGORIES.forEach(c=>{buckets[c.id]={...c,items:[]};});
  orchBoardFilteredItems.value.forEach(it=>{
    const ids=cat.classifyOrchItem(it);
    const pid=ids[0]||"other";
    (buckets[pid]||buckets.other).items.push(it);
  });
  return cat.ORCH_CATEGORIES.map(c=>({...c,items:(buckets[c.id]&&buckets[c.id].items)||[]})).filter(c=>c.items.length>0);
});
function orchBoardOpenItem(it){
  if(!it)return;
  if(it.type==="skill")selectOrchSkill(it.raw,"rail");
  else if(it.type==="mcp")selectOrchMcpTool(it.raw,it.idx,"rail");
  else if(it.type==="mcp-server")selectOrchMcpServer(it.raw,"rail");
}
const orchToggle=reactive({builtin:{},mcp:{},skill:{}});
function loadOrchToggle(){
  const raw=safeJsonParse(localStorage.getItem('sba_orch_toggle'),{});
  orchToggle.builtin={...(raw.builtin||{})};
  orchToggle.mcp={...(raw.mcp||{})};
  orchToggle.skill={...(raw.skill||{})};
}
function persistOrchToggle(){try{localStorage.setItem('sba_orch_toggle',JSON.stringify({builtin:{...orchToggle.builtin},mcp:{...orchToggle.mcp},skill:{...orchToggle.skill}}))}catch(_){}}
function isOrchOn(cat,key){
  const m=orchToggle[cat];
  return!(m&&m[key]===false);
}
function setOrchOn(cat,key,on){
  if(!orchToggle[cat])orchToggle[cat]={};
  orchToggle[cat][key]=!!on;
  persistOrchToggle();
}
function mcpDiscKey(mt,i){
  return String((mt&&mt.server)||'')+'::'+String((mt&&mt.name)||'')+'::'+i;
}
const PAGE_CRUMB_LABELS={
  video:"链接文档化",orch:"工具",chat:"AI 问答",tasks:"任务中心",agpz:"Agent 个性化设置",
  iag:"内部 Agent 配置",rag:"RAG 知识库",rss:"RSS 阅读",multimodal:"多模态文档",cache:"Redis 缓存",
  ops:"OPS 运维",webreplay:"浏览器自动化",settings:"设置",profile:"个人信息"
};
// 顶栏标签定义（key → 展示元数据）；iag 仅管理员可见
const ALL_TAB_DEFS={
  video:{key:"video",label:"链接文档化"},
  chat:{key:"chat",label:"AI 问答"},
  tasks:{key:"tasks",label:"任务中心"},
  orch:{key:"orch",label:"工具"},
  rag:{key:"rag",label:"RAG 知识库"},
  rss:{key:"rss",label:"RSS 阅读"},
  agpz:{key:"agpz",label:"Agent 设置"},
  iag:{key:"iag",label:"内部配置",adminOnly:true},
  settings:{key:"settings",label:"设置"},
  multimodal:{key:"multimodal",label:"多模态文档"},
  cache:{key:"cache",label:"Redis 缓存"},
  ops:{key:"ops",label:"OPS 运维"},
  webreplay:{key:"webreplay",label:"浏览器自动化"},
  profile:{key:"profile",label:"个人信息"}
};
const openTabs=ref(["video","chat"]);
const appTabs=computed(()=>{
  const keys=openTabs.value||[];
  return keys.map(k=>ALL_TAB_DEFS[k]).filter(def=>{
    if(!def)return false;
    if(def.adminOnly&&!isAdmin.value)return false;
    return true;
  });
});
function canCloseTab(key){
  return (openTabs.value||[]).length>1&&key!==page.value;
}
const uiPrefs=reactive({navDynamicIsland:true});
const navTabCompact=ref(false);
const navTabExpanded=ref(false);
const userAvatarUrl=ref('');
const userAvatarInp=ref(null);
let navIslandTimer=null;
function loadUiPrefs(){
  const o=safeJsonParse(localStorage.getItem('sba_ui_prefs'),{});
  if(o.navDynamicIsland!=null)uiPrefs.navDynamicIsland=!!o.navDynamicIsland;
  const av=localStorage.getItem('sba_user_avatar');
  if(av)userAvatarUrl.value=av;
}
function persistUiPrefs(){
  try{localStorage.setItem('sba_ui_prefs',JSON.stringify({navDynamicIsland:uiPrefs.navDynamicIsland}))}catch(_){}
}
function onUiPrefsChange(){
  persistUiPrefs();
  if(!uiPrefs.navDynamicIsland){navTabCompact.value=false;clearNavIslandTimer();}
  else resetNavIslandTimer();
}
function clearNavIslandTimer(){
  if(navIslandTimer){clearTimeout(navIslandTimer);navIslandTimer=null;}
}
function resetNavIslandTimer(){
  clearNavIslandTimer();
  if(!uiPrefs.navDynamicIsland)return;
  navTabCompact.value=false;
  navIslandTimer=setTimeout(()=>{
    requestAnimationFrame(()=>{navTabCompact.value=true;});
  },3200);
}
function onNavIslandEnter(){
  if(!uiPrefs.navDynamicIsland)return;
  navTabExpanded.value=true;
  clearNavIslandTimer();
}
function onNavIslandLeave(){
  if(!uiPrefs.navDynamicIsland)return;
  navTabExpanded.value=false;
  resetNavIslandTimer();
}
function bindNavIslandScroll(el){
  if(!el||el._navIslandBound)return;
  el._navIslandBound=true;
  let lastY=el.scrollTop||0;
  let scrollRaf=0;
  el.addEventListener('scroll',()=>{
    if(!uiPrefs.navDynamicIsland)return;
    if(scrollRaf)return;
    scrollRaf=requestAnimationFrame(()=>{
      scrollRaf=0;
      const y=el.scrollTop;
      if(y>lastY+8&&y>40){
        navTabCompact.value=true;
        clearNavIslandTimer();
        navIslandTimer=setTimeout(()=>{
          requestAnimationFrame(()=>{navTabCompact.value=true;});
        },3200);
      }else{
        resetNavIslandTimer();
      }
      lastY=y;
    });
  },{passive:true});
}
function pickUserAvatar(){
  try{document.querySelectorAll('details.user-dd').forEach(d=>{d.open=false})}catch(_){}
  if(userAvatarInp.value)userAvatarInp.value.click();
}
function onUserAvatarFile(ev){
  const f=ev.target&&ev.target.files&&ev.target.files[0];
  if(!f)return;
  if(f.size>2*1024*1024){showToastMsg('头像请小于 2MB');ev.target.value='';return;}
  const reader=new FileReader();
  reader.onload=()=>{
    userAvatarUrl.value=reader.result;
    try{localStorage.setItem('sba_user_avatar',reader.result)}catch(_){}
    showToastMsg('头像已更新');
  };
  reader.readAsDataURL(f);
  ev.target.value='';
}
function switchPage(key){
  if(page.value===key)return;
  // 检查权限
  if(REQUIRES_AUTH_PAGES.includes(key)&&!isAuthenticated.value){
    window.location.href='/login.html';
    return;
  }
  if(ADMIN_PAGES.includes(key)&&!isAdmin.value){
    alert('需要管理员权限');
    return;
  }
  closeAllPageOverlays();
  if(!openTabs.value.includes(key)){
    openTabs.value=openTabs.value.concat([key]);
  }
  if(key==="chat"&&c.chatPanelTab!=="config")c.chatPanelTab="room";
  page.value=key;
  // 更新 URL，不刷新页面
  if(history.pushState){
    history.pushState({page:key}, '', '/'+key);
  }
  resetNavIslandTimer();
}
function closeTab(key){
  if(!canCloseTab(key))return;
  openTabs.value=(openTabs.value||[]).filter(k=>k!==key);
}
function closeOtherTabs(){
  openTabs.value=[page.value];
  showToastMsg("已关闭其他标签");
}
watch(isAdmin,(adm)=>{
  if(!adm)openTabs.value=(openTabs.value||[]).filter(k=>k!=="iag");
});
watch(page,k=>{
  const def=ALL_TAB_DEFS[k];
  if(!def)return;
  if(def.adminOnly&&!isAdmin.value)return;
  if(!openTabs.value.includes(k))openTabs.value=openTabs.value.concat([k]);
  closeAllPageOverlays();
  if(k==="webreplay"){
    if(wr.sec==="scripts")ldWrScripts();
    if(wr.sec==="bridge")ldWrBridge();
  }
});
// 右键菜单关闭其他标签
function showTabContextMenu(key,event){
  // 可以在这里实现右键菜单逻辑
  // 暂时简单处理：切换到该标签
  switchPage(key);
}
const ORCH_SEC_CRUMB={
  "orch-sec-board":"看板",
  "orch-sec-tool":"TOOL CALL",
  "orch-sec-mcp":"MCP",
  "orch-sec-skill":"SKILL"
};
// 工具页二级菜单（SKILL 详情时为三级：… / SKILL / 技能名）
function switchOrchSubTab(key){
  if(key==="orch-skill-detail")return;
  if(orchStage.fullscreen&&orchRail.kind==="skill"){
    orchStage.fullscreen=false;
    orchRail.skillDiffMode=false;
    if(orchDetailSecObs){orchDetailSecObs.disconnect();orchDetailSecObs=null}
    orchRail.tabs=_skillRailTabs();
    if(skillFlowPollTimer){clearInterval(skillFlowPollTimer);skillFlowPollTimer=null}
  }
  if(orchTocActive.value===key)return;
  orchTocActive.value=key;
  try{localStorage.setItem("sba_orch_sec",key);}catch(_){}
  nextTick(()=>scrollOrchTo(key));
}
const SKILL_ALIAS_BY_NAME={
  "doc-coauthoring":"文档协作",
  "light-diagram-html-suite":"亮色图解套件",
  "longpage-html-3uds":"长页版式审核",
  "htet-gui-macro-regression-sop":"GUI宏回归",
  "ui-ux-pro-max":"UI/UX 设计",
  "impeccable":"前端审美",
  "arxiv-scholar-search":"论文检索",
  "latex-writer":"LaTeX 写作",
  "image-generation":"图像生成",
  "deskclaw-docx":"Word 文档",
  "thesis-template-guide":"论文模板",
  "complex-dev-harness":"复杂开发",
  "spec-plan-doc-metadata":"需求文档规范"
};
const MCP_ALIAS_BY_NAME={
  "comment-scraper":"评论抓取",
  "lark-mcp":"飞书开放平台",
  "feishu":"飞书",
  "lark":"飞书"
};
function mcpAliasCn(es){
  const alias=String((es&&es.alias)||"").trim();
  if(MCP_ALIAS_BY_NAME[alias])return MCP_ALIAS_BY_NAME[alias];
  return alias.replace(/[-_]/g," ").replace(/\b\w/g,c=>c.toUpperCase()).slice(0,12)||"MCP";
}
function _skillHasCjk(s){return /[\u4e00-\u9fff]/.test(String(s||""));}
function _skillMostlyLatin(s){
  const t=String(s||"").replace(/\s/g,"");
  if(!t)return false;
  return (t.match(/[A-Za-z]/g)||[]).length/t.length>0.55;
}
function splitSkillDesc(desc){
  const raw=String(desc||"").trim();
  if(!raw)return {zh:"",en:""};
  const invoke=raw.search(/\bInvoke when\b/i);
  if(invoke>12){
    return {zh:raw.slice(0,invoke).trim().replace(/[。.]\s*$/,""),en:raw.slice(invoke).trim()};
  }
  const parts=raw.split(/\n\s*\n/).map(p=>p.trim()).filter(Boolean);
  if(parts.length>=2){
    const zh=[],en=[];
    parts.forEach(p=>{
      if(_skillHasCjk(p)&&!_skillMostlyLatin(p))zh.push(p);
      else if(_skillMostlyLatin(p)&&!_skillHasCjk(p))en.push(p);
      else if(_skillHasCjk(p))zh.push(p);
      else en.push(p);
    });
    if(zh.length&&en.length)return {zh:zh.join("\n"),en:en.join("\n")};
  }
  const m=raw.match(/^([\s\S]*?)([。.!?])\s+([A-Z][\s\S]+)$/);
  if(m&&_skillHasCjk(m[1])&&_skillMostlyLatin(m[3])){
    return {zh:(m[1]+m[2]).trim(),en:m[3].trim()};
  }
  if(_skillHasCjk(raw)&&!_skillMostlyLatin(raw))return {zh:raw,en:""};
  if(_skillMostlyLatin(raw))return {zh:"",en:raw};
  return {zh:raw,en:""};
}
function skillDescParts(s){return splitSkillDesc(s&&s.description);}
function skillDisplay(s){return (s&&s.display&&typeof s.display==="object")?s.display:{};}
function skillDescLabelSrc(src){
  const v=String(src||"").toLowerCase();
  if(v==="file")return "文件原文";
  if(v==="ai")return "AI 翻译";
  return "";
}
function skillCardSummary(s){
  const d=skillDisplay(s);
  if(d.card_summary)return d.card_summary;
  const zh=splitSkillDesc(s&&s.description).zh;
  if(zh){return zh.length>72?zh.slice(0,71)+"…":zh;}
  const en=splitSkillDesc(s&&s.description).en||String(s&&s.description||"");
  return en.length>72?en.slice(0,71)+"…":en;
}
function skillCardTags(s){
  const b=s&&s.board;
  if(b&&Array.isArray(b.tags)&&b.tags.length)return b.tags.slice(0,4);
  return [];
}
function applySkillDisplayToRail(d){
  const disp=(d&&d.display)||{};
  let zh=String(disp.desc_zh||"").trim();
  let en=String(disp.desc_en||"").trim();
  let zhLbl=skillDescLabelSrc(disp.desc_zh_source);
  let enLbl=skillDescLabelSrc(disp.desc_en_source);
  if(!zh&&!en){
    const p=splitSkillDesc(d&&d.description);
    zh=p.zh;en=p.en;
    if(zh)zhLbl="文件原文";
    if(en)enLbl="文件原文";
  }
  orchRail.skillDescZh=zh;
  orchRail.skillDescEn=en;
  orchRail.skillDescZhLabel=zhLbl;
  orchRail.skillDescEnLabel=enLbl;
  orchRail.docText=zh||en||(d&&d.description)||"—";
}
function skillAliasCn(s){
  const name=String((s&&s.name)||"").trim();
  if(SKILL_ALIAS_BY_NAME[name])return SKILL_ALIAS_BY_NAME[name];
  const b=s&&s.board;
  if(b&&b.alias_cn)return b.alias_cn;
  const zh=splitSkillDesc(s&&s.description).zh;
  if(zh){
    const lead=zh.match(/^[\u4e00-\u9fffA-Za-z0-9（）()·、]{2,14}/);
    if(lead)return lead[0].length>10?lead[0].slice(0,10):lead[0];
  }
  return name.replace(/[-_]/g," ").slice(0,10)||"技能";
}
const orchTocActive=ref((()=>{try{const s=localStorage.getItem("sba_orch_sec");return ORCH_SEC_CRUMB[s]?s:"orch-sec-tool"}catch(_){return "orch-sec-tool"}})());
const appBreadcrumbs=computed(()=>{
  const p=page.value;
  const crumbs=[{key:p,label:PAGE_CRUMB_LABELS[p]||p,isLast:false}];
  if(p==="orch"){
    const sec=orchTocActive.value||"orch-sec-tool";
    const secLabel=sec==="orch-skill-detail"?"SKILL":(ORCH_SEC_CRUMB[sec]||"TOOL CALL");
    const secKey=sec==="orch-skill-detail"?"orch-sec-skill":sec;
    crumbs.push({key:secKey,label:secLabel,isLast:false});
    if(orchStage.fullscreen&&orchRail.kind==="skill"&&orchRail.open){
      crumbs.push({key:"orch-skill-detail",label:orchRail.title||"详情",isLast:true});
    }else{
      crumbs[crumbs.length-1].isLast=true;
    }
  }else{
    crumbs[0].isLast=true;
  }
  return crumbs;
});
function goAppBreadcrumb(c){
  if(!c||!c.key)return;
  if(PAGE_CRUMB_LABELS[c.key]){
    if(page.value!==c.key)page.value=c.key;
    if(c.key==="orch"){
      const el=document.querySelector(".tools-orch-page");
      if(el)el.scrollIntoView({behavior:"smooth",block:"start"});
    }
    return;
  }
  if(c.key==="orch-skill-detail")return;
  if(ORCH_SEC_CRUMB[c.key]){
    if(orchStage.fullscreen&&orchRail.kind==="skill")switchOrchSubTab(c.key);
    else scrollOrchTo(c.key);
  }
}
let orchSecObs=null;
function setupOrchSectionSpy(){
  if(orchSecObs){orchSecObs.disconnect();orchSecObs=null;}
  if(page.value!=="orch")return;
  const ids=["orch-sec-board","orch-sec-tool","orch-sec-mcp","orch-sec-skill"];
  orchSecObs=new IntersectionObserver((entries)=>{
    const vis=entries.filter(e=>e.isIntersecting).sort((a,b)=>b.intersectionRatio-a.intersectionRatio);
    if(vis[0]&&vis[0].target.id)orchTocActive.value=vis[0].target.id;
  },{root:null,rootMargin:"-12% 0px -62% 0px",threshold:[0,.15,.35]});
  ids.forEach(id=>{const el=document.getElementById(id);if(el)orchSecObs.observe(el);});
}
function scrollOrchTo(id){
  orchTocActive.value=id;
  try{localStorage.setItem("sba_orch_sec",id)}catch(_){}
  const el=document.getElementById(id);
  if(!el)return;
  el.scrollIntoView({behavior:"smooth",block:"start"});
  try{el.focus({preventScroll:true})}catch(_){}
}
const mcpMarketOpen=ref(false);
const mcpConfigEditText=ref("{}");
const mcpConfigAlias=ref("");
const mcpFeishuForm=reactive({appId:"",appSecret:""});
let skillFlowPollTimer=null;
const orchStage=reactive({fullscreen:false});
/** 详情展示模式：HTML 曾传 true，须与 "fullscreen" / "rail" 一并识别 */
function orchModeIsFullscreen(mode){
  return mode==="fullscreen"||mode===true;
}
const orchRail=reactive({
  open:false,kind:"",key:"",tab:"io",title:"",subtitle:"",badgeKind:"builtin",badgeLabel:"",
  docText:"",inText:"",outText:"",skillBody:"",skillBodyHtml:"",skillId:"",skillAliasCn:"",
  skillDescZh:"",skillDescEn:"",skillDescZhLabel:"",skillDescEnLabel:"",
  skillAttachments:[],skillAttachPath:"",
  configKind:"",methods:[],tabs:[{id:"io",label:"说明与 I/O"}],
  flow:{status:"none",mermaid:"",flow:null,error:""},
  versions:[],currentVersion:"",viewVersion:"",skillDiffMode:false,
  diffFrom:"",diffTo:"",diffHunks:[],diffUnified:"",
  zoom:1,zoomOrigin:"center center"
});
const diagramStyleFields=ref({});
async function ldDiagramStyles(){
  try{
    const r=await fetch("/api/settings/workflow-instructions/longpage_diagram_legend_agent");
    const d=await r.json();
    diagramStyleFields.value=d.fields||{};
    ensureDiagramStyleDefaults();
  }catch(_){diagramStyleFields.value={}}
}
function ensureDiagramStyleDefaults(target){
  if(!window.SBA_DIAGRAM_STYLES)return;
  const defs=window.SBA_DIAGRAM_STYLES.defaults();
  const f=target||diagramStyleFields.value||{};
  Object.keys(defs).forEach(k=>{
    if(f[k]==null||f[k]===""){
      f[k]=typeof defs[k]==="object"?JSON.stringify(defs[k],null,2):String(defs[k]);
    }
  });
  if(target)Object.assign(target,f);
  else diagramStyleFields.value=f;
}
function iagDiagramStyleKeys(){return window.SBA_DIAGRAM_STYLES?window.SBA_DIAGRAM_STYLES.DIAGRAM_TYPE_META:[]}
function iagDiagramStyleLabel(k){
  const m=iagDiagramStyleKeys().find(x=>x.key===k);
  return m?m.label:k;
}
function iagDiagramStyleHint(k){
  const m=iagDiagramStyleKeys().find(x=>x.key===k);
  return m?m.hint:"";
}
function resetIagDiagramStyle(key){
  if(!window.SBA_DIAGRAM_STYLES||!iag.fields)return;
  const defs=window.SBA_DIAGRAM_STYLES.defaults();
  iag.fields[key]=typeof defs[key]==="object"?JSON.stringify(defs[key],null,2):String(defs[key]||"");
}
const orchSubTabs=computed(()=>{
  if(orchStage.fullscreen&&orchRail.kind==="skill"&&orchRail.open){
    return [
      {key:"orch-sec-skill",label:"SKILL"},
      {key:"orch-skill-detail",label:orchRail.title||"详情"}
    ];
  }
  return [
    {key:"orch-sec-board",label:"看板"},
    {key:"orch-sec-tool",label:"TOOL CALL"},
    {key:"orch-sec-mcp",label:"MCP"},
    {key:"orch-sec-skill",label:"SKILL"}
  ];
});
const orchDetailTocActive=ref("orch-dsec-desc");
const orchDetailToc=computed(()=>{
  const items=[
    {id:"orch-dsec-desc",label:"说明"},
    {id:"orch-dsec-io",label:"输入输出"},
    {id:"orch-dsec-body",label:"正文"}
  ];
  const atts=orchRail.skillAttachments||[];
  if(atts.length)items.push({id:"orch-dsec-files",label:"附件"});
  items.push({id:"orch-dsec-versions",label:"版本"});
  if(orchRail.skillDiffMode)items.push({id:"orch-dsec-diff",label:"版本对比"});
  return items;
});
const orchSkillAttachActive=computed(()=>{
  const atts=orchRail.skillAttachments||[];
  if(!atts.length)return null;
  const want=orchRail.skillAttachPath;
  if(want)return atts.find(a=>a.path===want)||atts[0];
  return atts[0];
});
function skillAttachKindLabel(kind){
  const m={script:"脚本",config:"配置",data:"数据",doc:"文档",style:"样式",other:"其他"};
  return m[kind]||kind||"文件";
}
function selectSkillAttachment(a){
  if(!a)return;
  orchRail.skillAttachPath=a.path||"";
}
const orchDiffStats=computed(()=>{
  let add=0,del=0;
  (orchRail.diffHunks||[]).forEach(h=>{
    (h.lines||[]).forEach(ln=>{
      if(ln.kind==="add")add++;
      else if(ln.kind==="del")del++;
    });
  });
  return {add,del};
});
const orchDiffDisplay=computed(()=>{
  let oldN=0,newN=0;
  return (orchRail.diffHunks||[]).map(h=>{
    const lines=(h.lines||[]).map(ln=>{
      const kind=ln.kind||"context";
      let raw=String(ln.text||"");
      let sign=" ";
      if(kind==="add"){
        newN++;
        sign="+";
        if(raw.startsWith("+"))raw=raw.slice(1);
      }else if(kind==="del"){
        oldN++;
        sign="−";
        if(raw.startsWith("-"))raw=raw.slice(1);
      }else{
        if(!raw.startsWith("\\")){oldN++;newN++;}
        if(raw.startsWith(" "))raw=raw.slice(1);
        sign="";
      }
      return {
        kind,sign,text:raw,
        oldNo:kind==="add"?null:oldN,
        newNo:kind==="del"?null:newN
      };
    });
    return {header:h.header,lines};
  });
});
let orchDetailSecObs=null;
function scrollOrchDetailTo(id){
  if(!id)return;
  orchDetailTocActive.value=id;
  const root=document.querySelector(".orch-stage-doc-scroll");
  const el=document.getElementById(id);
  if(root&&el){
    const top=el.getBoundingClientRect().top-root.getBoundingClientRect().top+root.scrollTop-10;
    root.scrollTo({top:Math.max(0,top),behavior:"smooth"});
  }else if(el)el.scrollIntoView({behavior:"smooth",block:"start"});
}
function setupOrchDetailSectionSpy(){
  if(orchDetailSecObs){orchDetailSecObs.disconnect();orchDetailSecObs=null;}
  const root=document.querySelector(".orch-stage-doc-scroll");
  if(!root||!orchStage.fullscreen||orchRail.kind!=="skill")return;
  orchDetailSecObs=new IntersectionObserver((entries)=>{
    const vis=entries.filter(e=>e.isIntersecting).sort((a,b)=>b.intersectionRatio-a.intersectionRatio);
    if(vis[0]&&vis[0].target.id)orchDetailTocActive.value=vis[0].target.id;
  },{root,rootMargin:"-6% 0px -52% 0px",threshold:[0,.1,.25]});
  orchDetailToc.value.forEach(item=>{
    const el=document.getElementById(item.id);
    if(el)orchDetailSecObs.observe(el);
  });
}
function clearSkillDiff(){
  orchRail.skillDiffMode=false;
  orchRail.diffFrom="";orchRail.diffTo="";orchRail.diffHunks=[];orchRail.diffUnified="";
  orchRail.viewVersion=orchRail.currentVersion||orchRail.viewVersion;
  nextTick(()=>setupOrchDetailSectionSpy());
}
const orchFlowPan=reactive({dragging:false,sx:0,sy:0,sl:0,st:0,el:null});
function _orchFlowPanDocMove(e){
  if(!orchFlowPan.dragging||!orchFlowPan.el)return;
  const el=orchFlowPan.el;
  el.scrollLeft=orchFlowPan.sl-(e.clientX-orchFlowPan.sx);
  el.scrollTop=orchFlowPan.st-(e.clientY-orchFlowPan.sy);
}
function _orchFlowPanDocEnd(){
  if(orchFlowPan.el)orchFlowPan.el.classList.remove("is-panning");
  orchFlowPan.dragging=false;
  orchFlowPan.el=null;
  document.removeEventListener("mousemove",_orchFlowPanDocMove);
  document.removeEventListener("mouseup",_orchFlowPanDocEnd);
}
function orchFlowPanStart(e){
  if(e.button!==0)return;
  if(e.target.closest("button,a,input,textarea,select,label,summary"))return;
  const el=e.currentTarget;
  orchFlowPan.dragging=true;
  orchFlowPan.el=el;
  orchFlowPan.sx=e.clientX;orchFlowPan.sy=e.clientY;
  orchFlowPan.sl=el.scrollLeft;orchFlowPan.st=el.scrollTop;
  el.classList.add("is-panning");
  document.addEventListener("mousemove",_orchFlowPanDocMove);
  document.addEventListener("mouseup",_orchFlowPanDocEnd);
  e.preventDefault();
}
function orchFlowPanMove(e){_orchFlowPanDocMove(e)}
function orchFlowPanEnd(){_orchFlowPanDocEnd()}
function fitOrchFlowToViewport(){
  nextTick(()=>{
    const vp=document.querySelector(".orch-stage-skill .orch-stage-flow-viewport")||document.querySelector(".orch-flow-viewport--rail");
    if(!vp)return;
    const board=vp.querySelector(".orch-flow-board-pill,.orch-mermaid-render svg,.orch-mermaid-render");
    if(!board){orchRail.zoom=1;return}
    vp.scrollLeft=0;vp.scrollTop=0;
    const pad=20;
    const vw=Math.max(80,vp.clientWidth-pad);
    const vh=Math.max(80,vp.clientHeight-pad);
    const rect=board.getBoundingClientRect();
    const bw=rect.width||board.offsetWidth||1;
    const bh=rect.height||board.offsetHeight||1;
    const scale=Math.min(1.25,Math.max(0.38,Math.min(vw/bw,vh/bh,1)));
    orchRail.zoom=Math.round(scale*100)/100;
    orchRail.zoomOrigin="center center";
  });
}
function resetOrchRailView(){
  orchRail.zoom=1;
  orchRail.zoomOrigin="center center";
  nextTick(()=>{
    const vp=document.querySelector(".orch-flow-viewport--rail")||document.querySelector(".orch-stage-skill .orch-stage-flow-viewport");
    if(vp){vp.scrollLeft=0;vp.scrollTop=0}
    if(orchRail.tab==="flow")fitOrchFlowToViewport();
  });
}
function renderOrchMarkdown(md){
  const raw=String(md||"").trim();
  if(!raw)return "<div class=\"orch-skill-doc\"><p class=\"orch-hint\">（无正文）</p></div>";
  let inner="";
  if(typeof marked!=="undefined"){
    try{
      if(typeof marked.setOptions==="function"){
        marked.setOptions({breaks:true,gfm:true,headerIds:false,mangle:false});
      }
      inner=marked.parse(raw);
      if(typeof DOMPurify!=="undefined")inner=DOMPurify.sanitize(inner);
    }catch(_){inner=""}
  }
  if(!inner){
    inner=raw.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/\n/g,"<br>");
  }
  return "<div class=\"orch-skill-doc\">"+inner+"</div>";
}
function buildFlowDisplay(flow){
  if(!flow||!flow.nodes||!flow.nodes.length)return {sequence:[],branches:{}};
  const nodes=flow.nodes;
  const edges=flow.edges||[];
  const byId={};
  nodes.forEach(n=>{byId[n.id]=n});
  const out={};
  edges.forEach(e=>{
    if(!out[e.from])out[e.from]=[];
    out[e.from].push(e);
  });
  const start=nodes.find(n=>n.type==="start")||nodes[0];
  const sequence=[];
  const branches={};
  const seen=new Set();
  let cur=start;
  let guard=0;
  while(cur&&guard<30){
    guard++;
    if(seen.has(cur.id))break;
    seen.add(cur.id);
    sequence.push(cur);
    const nexts=out[cur.id]||[];
    if(nexts.length>1){
      branches[cur.id]=nexts.map(e=>({
        label:e.label||"",
        node:byId[e.to]||{id:e.to,label:e.to,type:"auto"}
      }));
      break;
    }
    if(!nexts.length)break;
    cur=byId[nexts[0].to];
  }
  nodes.forEach(n=>{
    if(!seen.has(n.id)&&n.type==="done")sequence.push(n);
  });
  return {sequence,branches};
}
const orchFlowDisplay=computed(()=>buildFlowDisplay(orchRail.flow.flow));
function mcpServerKeys(){
  try{
    const o=JSON.parse(mcpJsonText.value||"{}");
    const s=o.servers;
    if(s&&typeof s==="object"&&!Array.isArray(s))return Object.keys(s);
  }catch(_){}
  return [];
}
function mcpServerSummary(alias){
  try{
    const o=JSON.parse(mcpJsonText.value||"{}");
    const blk=(o.servers||{})[alias];
    if(!blk)return "已写入 mcp_servers.json";
    if(blk.url)return String(blk.url);
    const cmd=[blk.command].concat(blk.args||[]).filter(Boolean).join(" ");
    return cmd||"stdio 服务";
  }catch(_){return "—"}
}
const mcpEnabledList=computed(()=>{
  const keys=mcpServerKeys();
  const by=mcpByServer.value||{};
  return keys.map(alias=>{
    const blk=by[alias];
    return {
      alias,
      summary:mcpServerSummary(alias),
      status:blk?{ok:!!blk.ok}:{ok:false},
      toolCount:blk&&blk.tools?blk.tools.length:0
    };
  });
});
function closeOrchRail(){
  orchRail.open=false;orchRail.kind="";orchRail.key="";
  orchStage.fullscreen=false;
  orchRail.skillDiffMode=false;
  orchRail.diffFrom="";orchRail.diffTo="";orchRail.diffHunks=[];orchRail.diffUnified="";
  if(orchTocActive.value==="orch-skill-detail")orchTocActive.value="orch-sec-skill";
  if(skillFlowPollTimer){clearInterval(skillFlowPollTimer);skillFlowPollTimer=null}
  if(orchDetailSecObs){orchDetailSecObs.disconnect();orchDetailSecObs=null}
}
async function openOrchFullscreen(){
  if(!orchRail.open)return;
  orchStage.fullscreen=true;
  if(orchRail.kind==="skill"){
    orchTocActive.value="orch-skill-detail";
    orchRail.tabs=[{id:"flow",label:"流程图"},{id:"io",label:"说明"},{id:"body",label:"正文"}];
    await nextTick();
    setupOrchDetailSectionSpy();
    await renderOrchMermaid();
    fitOrchFlowToViewport();
  }
}
function dockOrchFromFullscreen(){
  if(orchRail.kind==="skill"){
    orchStage.fullscreen=false;
    orchRail.skillDiffMode=false;
    orchTocActive.value="orch-sec-skill";
    if(orchDetailSecObs){orchDetailSecObs.disconnect();orchDetailSecObs=null}
    orchRail.tabs=_skillRailTabs();
    nextTick(()=>{
      scrollOrchTo("orch-sec-skill");
      if(orchRail.flow.mermaid)renderOrchMermaid();
    });
    return;
  }
  orchStage.fullscreen=false;
}
function detectMcpConfigKind(block){
  if(!block||typeof block!=="object")return "";
  const args=block.args||[];
  const j=JSON.stringify(args);
  if(j.indexOf("lark-mcp")>=0||j.indexOf("@larksuiteoapi/lark-mcp")>=0)return "lark-mcp";
  return "";
}
function parseLarkCreds(block){
  const args=(block&&block.args)||[];
  let appId="",appSecret="";
  for(let i=0;i<args.length;i++){
    if(args[i]==="-a"&&args[i+1])appId=String(args[i+1]);
    if(args[i]==="-s"&&args[i+1])appSecret=String(args[i+1]);
  }
  return {appId,appSecret};
}
function buildLarkMcpBlock(appId,appSecret){
  return {
    transport:"stdio",
    command:"npx",
    args:["-y","@larksuiteoapi/lark-mcp","mcp","-a",String(appId||"").trim(),"-s",String(appSecret||"").trim()]
  };
}
function fillOrchRailBuiltin(t){
  orchRail.kind="builtin";orchRail.key="builtin:"+(t&&t.id);orchRail.badgeKind="builtin";orchRail.badgeLabel="内置";
  orchRail.title=(t&&t.name)||"内置工具";orchRail.subtitle=(t&&t.id)||"";
  orchRail.docText=(t&&t.description)||"—";
  orchRail.inText=t&&t.inputs?JSON.stringify(t.inputs,null,2):"—";
  orchRail.outText=(t&&t.outputs)||"—";
  orchRail.tabs=[{id:"io",label:"说明与 I/O"}];orchRail.tab="io";orchRail.methods=[];
  orchRail.versions=[];orchRail.skillBodyHtml="";
}
function selectOrchBuiltin(t,mode){
  fillOrchRailBuiltin(t);
  resetOrchRailView();
  orchRail.open=true;
  orchStage.fullscreen=orchModeIsFullscreen(mode);
}
function mcpToolsForServer(alias){
  return (mcpDiscovered.value||[]).filter(mt=>String(mt.server||"")===String(alias));
}
function mcpMethodRows(tools){
  return (tools||[]).map(mt=>({
    name:mt.name||"—",
    description:mt.description||"",
    inputText:mt.input_schema?JSON.stringify(mt.input_schema,null,2):"（无 parameters schema）",
    outputHint:"由 MCP Server 返回；常见为 JSON 或文本。"
  }));
}
function selectOrchMcpServer(es,mode){
  const alias=es&&es.alias?es.alias:"";
  orchRail.kind="mcp-server";orchRail.key="mcp-srv:"+alias;orchRail.badgeKind="mcp";orchRail.badgeLabel="MCP";
  orchRail.title=alias;orchRail.subtitle=mcpServerSummary(alias);
  const tools=mcpToolsForServer(alias);
  orchRail.methods=mcpMethodRows(tools);
  orchRail.docText="服务别名："+alias+"\n\n"+(tools.length?("已发现 "+tools.length+" 个工具，见「方法」页签。"):"保存配置并「连接并拉取」后显示方法列表。");
  orchRail.inText="各工具 parameters 见方法列表。";
  orchRail.outText="各工具返回值由 MCP 实现决定。";
  orchRail.tabs=[{id:"methods",label:"方法"},{id:"io",label:"概览"},{id:"config",label:"配置"}];
  orchRail.tab=tools.length?"methods":"io";
  resetOrchRailView();
  orchRail.open=true;
  orchStage.fullscreen=orchModeIsFullscreen(mode);
}
function selectOrchMcpTool(mt,i,mode){
  const key=mcpDiscKey(mt,i);
  orchRail.kind="mcp-tool";orchRail.key=key;orchRail.badgeKind="mcp";orchRail.badgeLabel="工具";
  orchRail.title=(mt&&mt.name)||"MCP 工具";orchRail.subtitle=(mt&&mt.server)?"服务："+mt.server:"";
  orchRail.docText=(mt&&mt.description)||"—";
  orchRail.inText=mt&&mt.input_schema?JSON.stringify(mt.input_schema,null,2):"（无 JSON Schema）";
  orchRail.outText="返回值由 MCP Server 实现决定。";
  orchRail.methods=mcpMethodRows([mt]);
  orchRail.tabs=[{id:"io",label:"说明与 I/O"},{id:"methods",label:"Schema"}];
  orchRail.tab="io";
  resetOrchRailView();
  orchRail.open=true;
  orchStage.fullscreen=orchModeIsFullscreen(mode);
}
async function loadSkillVersions(skillId){
  orchRail.versions=[];
  try{
    const r=await fetch("/api/skills/"+encodeURIComponent(skillId)+"/versions");
    const d=await r.json();
    orchRail.versions=d.versions||[];
    orchRail.currentVersion=d.current||"";
    orchRail.viewVersion=orchRail.currentVersion;
  }catch(_){orchRail.versions=[];orchRail.currentVersion=""}
}
async function openSkillDiff(fromVer,toVer){
  if(!orchRail.skillId||!fromVer||!toVer)return;
  if(fromVer===toVer){clearSkillDiff();return}
  orchRail.diffFrom=fromVer;orchRail.diffTo=toVer;
  orchRail.skillDiffMode=true;
  if(orchRail.kind!=="skill"||!orchStage.fullscreen){
    orchRail.tab="diff";
    if(!orchRail.tabs.find(t=>t.id==="diff"))orchRail.tabs.push({id:"diff",label:"版本对比"});
  }
  try{
    const q="from="+encodeURIComponent(fromVer)+"&to="+encodeURIComponent(toVer);
    const r=await fetch("/api/skills/"+encodeURIComponent(orchRail.skillId)+"/diff?"+q);
    const d=await r.json();
    orchRail.diffHunks=d.hunks||[];
    orchRail.diffUnified=d.unified||"";
    if(orchStage.fullscreen&&orchRail.kind==="skill"){
      await nextTick();
      setupOrchDetailSectionSpy();
      scrollOrchDetailTo("orch-dsec-diff");
    }
  }catch(e){
    orchRail.diffUnified="加载失败: "+(e.message||String(e));
    orchRail.diffHunks=[];
  }
}
async function onSkillVersionClick(ver){
  const cur=orchRail.currentVersion||"";
  orchRail.viewVersion=ver||cur;
  if(!ver||ver===cur){clearSkillDiff();return}
  await openSkillDiff(ver,cur);
}
async function _loadSkillRailData(s,sid){
  try{
    const r=await fetch("/api/skills/"+encodeURIComponent(sid));
    const d=await r.json();
    const ver=d.version||s.version||"1.0.0";
    orchRail.currentVersion=ver;
    orchRail.viewVersion=ver;
    applySkillDisplayToRail(d);
    const body=(d.body_md||"").trim()||"（无正文）";
    orchRail.skillBody=body;
    orchRail.skillBodyHtml=renderOrchMarkdown(body);
    const atts=Array.isArray(d.attachments)?d.attachments:[];
    orchRail.skillAttachments=atts;
    orchRail.skillAttachPath=atts.length?(atts[0].path||""):"";
  }catch(e){
    orchRail.skillBody="加载失败: "+(e.message||String(e));
    orchRail.skillBodyHtml="";
    orchRail.skillAttachments=[];
    orchRail.skillAttachPath="";
  }
  await loadSkillVersions(sid);
  if(orchRail.currentVersion)orchRail.viewVersion=orchRail.currentVersion;
}
function _skillRailTabs(){
  const tabs=[{id:"io",label:"说明"},{id:"body",label:"正文"},{id:"flow",label:"流程"}];
  if((orchRail.skillAttachments||[]).length)tabs.push({id:"attach",label:"附件"});
  return tabs;
}
async function selectOrchSkill(s,mode){
  const sid=s&&s.id;
  if(!sid)return;
  orchRail.key="skill:"+sid;
  if(!mode)mode="rail";
  const wantFullscreen=orchModeIsFullscreen(mode);
  orchRail.kind="skill";orchRail.skillId=sid;orchRail.badgeKind="skill";orchRail.badgeLabel="SKILL";
  orchRail.title=(s&&s.name)||"SKILL";orchRail.subtitle=(s&&s.command)?String(s.command):"";
  orchRail.skillAliasCn=skillAliasCn(s);
  orchRail.inText="用户消息 + 会话；/command 挂载本 SKILL。";
  orchRail.outText="由大模型按 SKILL 正文生成；无固定 schema。";
  orchRail.skillDiffMode=false;
  orchRail.docText="加载中…";orchRail.skillBody="加载中…";orchRail.skillBodyHtml="";
  orchRail.flow={status:"pending",mermaid:"",flow:null,error:""};
  resetOrchRailView();
  orchRail.open=true;
  orchStage.fullscreen=wantFullscreen;
  orchTocActive.value=wantFullscreen?"orch-skill-detail":"orch-sec-skill";
  await _loadSkillRailData(s,sid);
  orchRail.tabs=_skillRailTabs();
  orchRail.tab="io";
  pollSkillFlow(sid);
  await nextTick();
  if(wantFullscreen){
    setupOrchDetailSectionSpy();
    await renderOrchMermaid();
    fitOrchFlowToViewport();
  }else if(orchRail.flow.mermaid){
    await renderOrchMermaid();
  }
}
async function pollSkillFlow(skillId){
  if(skillFlowPollTimer){clearInterval(skillFlowPollTimer);skillFlowPollTimer=null}
  async function tick(){
    try{
      const r=await fetch("/api/skills/"+encodeURIComponent(skillId)+"/flow-diagram");
      const d=await r.json();
      const st=d.status||"none";
      orchRail.flow.status=st;
      orchRail.flow.mermaid=d.mermaid||"";
      orchRail.flow.flow=d.flow||null;
      orchRail.flow.error=d.error||"";
      if(st==="done"||st==="error"||st==="none"){
        if(skillFlowPollTimer){clearInterval(skillFlowPollTimer);skillFlowPollTimer=null}
        if(st==="done"&&orchRail.flow.mermaid&&!orchRail.flow.flow)await renderOrchMermaid();
        if(st==="done"&&orchRail.flow.flow)fitOrchFlowToViewport();
      }
    }catch(_){}
  }
  await tick();
  if(orchRail.flow.status==="none"){
    try{await fetch("/api/skills/"+encodeURIComponent(skillId)+"/flow-diagram",{method:"POST"})}catch(_){}
    orchRail.flow.status="pending";
    skillFlowPollTimer=setInterval(tick,2500);
    return;
  }
  if(orchRail.flow.status==="pending")skillFlowPollTimer=setInterval(tick,2500);
  else if(orchRail.flow.flow)fitOrchFlowToViewport();
}
async function refreshSkillFlow(skillId){
  if(!skillId||orchRail.flow.status==="pending")return;
  orchRail.flow={status:"pending",mermaid:"",flow:null,error:""};
  try{
    await fetch("/api/skills/"+encodeURIComponent(skillId)+"/flow-diagram",{method:"POST"});
  }catch(_){}
  pollSkillFlow(skillId);
}
function onOrchRailTabChange(tabId){
  orchRail.tab=tabId;
  if(tabId!=="flow")return;
  nextTick(()=>{
    if(orchRail.flow.flow||orchRail.flow.mermaid){
      fitOrchFlowToViewport();
      if(orchRail.flow.mermaid&&!orchRail.flow.flow)renderOrchMermaid();
    }else if(orchRail.skillId&&(orchRail.flow.status==="none"||orchRail.flow.status==="error")){
      refreshSkillFlow(orchRail.skillId);
    }
  });
}
/** 与历史模板名兼容，避免 tab 点击报「is not a function」 */
const onOrchDetailTabChange=onOrchRailTabChange;
function orchRailTabIsFlow(){return orchRail.tab==="flow"}
async function renderOrchMermaid(){
  await nextTick();
  if(typeof window.mermaid==="undefined"||!orchRail.flow.mermaid)return;
  const ids=["mermaid-"+orchRail.key,"mermaid-stage-"+orchRail.key];
  const el=ids.map(id=>document.getElementById(id)).find(Boolean);
  if(!el)return;
  try{
    if(window.SBA_DIAGRAM_STYLES){
      window.SBA_DIAGRAM_STYLES.applyMermaidInitialize(window.mermaid,diagramStyleFields.value);
    }else{
      window.mermaid.initialize({startOnLoad:false,theme:"base",securityLevel:"strict"});
    }
    const id="mmd-"+String(orchRail.key).replace(/\W/g,"");
    const out=await window.mermaid.render(id,orchRail.flow.mermaid);
    el.innerHTML=out.svg;
    el.classList.add("orch-mermaid-svg-wrap");
    fitOrchFlowToViewport();
  }catch(e){el.textContent="流程图渲染失败: "+(e.message||String(e))}
}
async function ldMcpVendors(){try{const r=await fetch("/api/tools/mcp/vendors");const d=await r.json();mcpVendors.value=d.items||[]}catch(_){mcpVendors.value=[]}}
function insertMcpVendorMerge(merge){
  const m=merge&&typeof merge==="object"?merge:null;
  if(!m||!Object.keys(m).length){showToastMsg("此项无自动合并片段");return false}
  try{
    const o=JSON.parse(mcpJsonText.value||"{}");
    const srv=o.servers&&typeof o.servers==="object"&&!Array.isArray(o.servers)?o.servers:{};
    Object.assign(srv,m);
    mcpJsonText.value=JSON.stringify({servers:srv},null,2);
    return true;
  }catch(_){alert("当前 JSON 非法");return false}
}
async function addMcpFromMarket(v){
  if(!v||!v.merge||!Object.keys(v.merge||{}).length){showToastMsg("此项为文档入口，无 merge 片段");return}
  if(!insertMcpVendorMerge(v.merge))return;
  await saveMcpCfg();
  const alias=v.preset_alias||Object.keys(v.merge)[0];
  showToastMsg("已添加「"+(v.title||alias)+"」→ 请配置密钥后连接");
  mcpMarketOpen.value=false;
  await openMcpServerConfig(alias);
}
async function openMcpServerConfig(alias){
  mcpConfigAlias.value=alias;
  orchRail.kind="mcp-config";orchRail.key="mcp-cfg:"+alias;orchRail.badgeKind="mcp";orchRail.badgeLabel="配置";
  orchRail.title=alias;orchRail.subtitle="mcp_servers.json";
  orchRail.tabs=[{id:"config",label:"配置"},{id:"methods",label:"方法"}];
  orchRail.tab="config";
  orchRail.open=true;
  orchStage.fullscreen=false;
  try{
    const r=await fetch("/api/tools/mcp/server/"+encodeURIComponent(alias));
    const d=await r.json();
    const block=d.block||{};
    mcpConfigEditText.value=JSON.stringify(block,null,2);
    orchRail.configKind=detectMcpConfigKind(block);
    const cred=parseLarkCreds(block);
    mcpFeishuForm.appId=cred.appId;mcpFeishuForm.appSecret=cred.appSecret;
    orchRail.methods=mcpMethodRows(mcpToolsForServer(alias));
  }catch(e){
    mcpConfigEditText.value="{}";orchRail.configKind="";alert(e.message||String(e));
  }
}
async function saveMcpServerConfigFromRail(andSync){
  const alias=String(mcpConfigAlias.value||"").trim();
  if(!alias){alert("无服务别名");return}
  let block;
  try{
    if(orchRail.configKind==="lark-mcp"){
      block=buildLarkMcpBlock(mcpFeishuForm.appId,mcpFeishuForm.appSecret);
      mcpConfigEditText.value=JSON.stringify(block,null,2);
    }else{
      block=JSON.parse(mcpConfigEditText.value||"{}");
    }
  }catch(e){alert("配置 JSON 无效");return}
  try{
    const r=await fetch("/api/tools/mcp/server/"+encodeURIComponent(alias),{
      method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({block})
    });
    const d=await r.json();
    if(!r.ok)throw new Error(typeof d.detail==="string"?d.detail:JSON.stringify(d.detail||d));
    await ldMcpCfg();
    showToastMsg("MCP 配置已保存");
    if(andSync)await mcpSyncPull();
    if(orchRail.kind==="mcp-config")selectOrchMcpServer({alias});
  }catch(e){alert(e.message||String(e))}
}
async function removeMcpServer(alias){
  if(!confirm("从配置中移除 MCP 服务「"+alias+"」？"))return;
  try{
    const r=await fetch("/api/tools/mcp/server/"+encodeURIComponent(alias),{method:"DELETE"});
    const d=await r.json();
    if(!r.ok)throw new Error(typeof d.detail==="string"?d.detail:JSON.stringify(d.detail||d));
    await ldMcpCfg();
    if(orchRail.key==="mcp-srv:"+alias||orchRail.key==="mcp-cfg:"+alias)closeOrchRail();
    showToastMsg("已移除");
  }catch(e){alert(e.message||String(e))}
}
const mcpSyncMsg=ref("");
const mcpJsonText=ref('{\n  "servers": {}\n}');
const mcpPlaceholder='{"servers":{}}';
async function ldBuiltinTools(){try{const r=await fetch("/api/tools/builtin");const d=await r.json();builtinTools.value=d.tools||[]}catch(e){builtinTools.value=[]}}
async function ldMcpCfg(){try{const r=await fetch("/api/tools/mcp/config");const d=await r.json();const s=d.servers&&typeof d.servers==="object"&&!Array.isArray(d.servers)?d.servers:{};mcpJsonText.value=JSON.stringify({servers:s},null,2)}catch(e){mcpJsonText.value='{\n  "servers": {}\n}'}}
async function saveMcpCfg(){
  try{
    const obj=JSON.parse(mcpJsonText.value);
    const servers=obj.servers;
    if(!servers||typeof servers!=="object"||Array.isArray(servers)){alert('JSON 须为 {"servers": { ... }} 结构');return}
    const r=await fetch("/api/tools/mcp/config",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({servers})});
    const d=await r.json();
    if(!r.ok)throw new Error(typeof d.detail==="string"?d.detail:JSON.stringify(d.detail||d));
    showToastMsg("MCP 配置已保存");
  }catch(e){alert(e.message||String(e))}
}
async function mcpSyncPull(){
  mcpSyncMsg.value="连接中…";
  mcpDiscovered.value=[];
  mcpByServer.value={};
  try{
    const r=await fetch("/api/tools/mcp/sync",{method:"POST"});
    const d=await r.json();
    if(!d.ok){mcpSyncMsg.value=d.error||"失败";return}
    mcpDiscovered.value=d.tools||[];
    mcpByServer.value=d.by_server||{};
    mcpSyncMsg.value="已拉取 "+(d.count!=null?d.count:mcpDiscovered.value.length)+" 个 MCP 工具";
  }catch(e){mcpSyncMsg.value=String(e.message||e)}
}
async function ldSkills(){
  try{
    const r=await fetch('/api/skills');
    const d=await r.json();
    skills.value=d.skills||[];
    Object.keys(skillCmdDraft).forEach(k=>{delete skillCmdDraft[k]});
    (skills.value||[]).forEach(s=>{skillCmdDraft[s.id]=String(s.command||'')});
  }catch(e){skills.value=[]}
}
async function saveSkillCommand(s){
  const id=s.id;
  const cmd=String(skillCmdDraft[id]!==undefined?skillCmdDraft[id]:(s.command||'')).trim();
  try{
    const r=await fetch('/api/skills/'+encodeURIComponent(id),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({command:cmd})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail||d));
    showToastMsg('命令映射已保存');
    await ldSkills();
  }catch(e){alert(e.message||String(e))}
}
async function saveAllSkillCommands(){
  const commands={};
  (skills.value||[]).forEach(s=>{
    if(!s||!s.id)return;
    const draft=skillCmdDraft[s.id];
    if(draft!==undefined)commands[s.id]=String(draft).trim();
  });
  try{
    const r=await fetch('/api/skills/commit-commands',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({commands})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail||d));
    const n=d.count!=null?d.count:(d.saved||[]).length;
    showToastMsg('已提交 '+n+' 条命令映射');
    await ldSkills();
  }catch(e){alert(e.message||String(e))}
}
async function retagAllSkillsBoard(){
  if(!confirm('为注册表中全部 SKILL 重新生成能力看板 AI 标签？\n将调用已配置的网关模型，可能需要数十秒。'))return;
  try{
    const r=await fetch('/api/skills/tag-board',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({force:true})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail||d));
    showToastMsg('看板打标完成：'+ (d.tagged||0) +' 个'+(d.skipped?('，跳过 '+d.skipped):''));
    await ldSkills();
  }catch(e){alert(e.message||String(e))}
}
async function importProjectSkillsBatch(){
  if(!confirm('从项目目录批量导入 SKILL？\n· .cursor/skills\n· web_migration/skills_downloaded\n· F:\\AI\\local_skills（递归含 batch-* / garden-skills 等）\n已存在同名将更新正文与附件。'))return;
  try{
    const r=await fetch('/api/skills/import-batch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({upsert:true})});
    const d=await r.json();
    if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail||d));
    const errs=(d.errors||[]).length;
    const bt=d.board_tag||{};
    const tagN=bt.tagged!=null?bt.tagged:0;
    let msg='批量导入 '+ (d.count||0) +' 个';
    if(errs)msg+='，失败 '+errs+' 个';
    if(tagN)msg+='；看板 AI 打标 '+tagN+' 个';
    showToastMsg(msg);
    await ldSkills();
    await saveAllSkillCommands();
  }catch(e){alert(e.message||String(e))}
}
async function importSkillForm(){
  if(!String(sk.name||"").trim()||!String(sk.description||"").trim()){alert("name 与 description 必填");return}
  try{
    const r=await fetch("/api/skills/import",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:String(sk.name).trim(),description:String(sk.description).trim(),body_md:String(sk.body_md||""),command:String(sk.command||"").trim(),source:"form"})});
    const d=await r.json();
    if(!r.ok)throw new Error(typeof d.detail==="string"?d.detail:JSON.stringify(d.detail||d));
    showToastMsg("SKILL 已导入");
    sk.body_md="";sk.command="";
    await ldSkills();
    closeSkillImport();
  }catch(e){alert(e.message||String(e))}
}
async function onSkillFile(e){
  const f=(e.target.files||[])[0];e.target.value="";
  if(!f)return;
  const txt=await f.text();
  try{
    const r=await fetch("/api/skills/import-md",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({markdown:txt})});
    const d=await r.json();
    if(!r.ok)throw new Error(typeof d.detail==="string"?d.detail:JSON.stringify(d.detail||d));
    showToastMsg("已从文件导入");
    await ldSkills();
  }catch(err){alert(err.message||String(err))}
}
function _skillFolderKey(rel){
  const p=String(rel||"").replace(/\\/g,"/");
  const i=p.indexOf("/");
  return i>0?p.slice(0,i):p||"root";
}
function _isSkillMdName(name){
  return /^skill\.md$/i.test(String(name||""));
}
async function onSkillFolder(e){
  const all=Array.from(e.target.files||[]);
  e.target.value="";
  if(!all.length){alert("未选择文件夹");return}
  const groups=new Map();
  for(const f of all){
    const rel=f.webkitRelativePath||f.name;
    const top=_skillFolderKey(rel);
    if(!groups.has(top))groups.set(top,{skillMd:null,files:[]});
    const g=groups.get(top);
    if(_isSkillMdName(f.name)&&rel.endsWith(f.name))g.skillMd=f;
    else g.files.push({file:f,rel});
  }
  const bundles=[];
  for(const [top,g] of groups){
    if(!g.skillMd)continue;
    const markdown=await g.skillMd.text();
    const prefix=(g.skillMd.webkitRelativePath||"").replace(/\\/g,"/");
    const dirPrefix=prefix.includes("/")?prefix.slice(0,prefix.lastIndexOf("/")+1):"";
    const attachments=[];
    for(const {file,rel} of g.files){
      const rpath=String(rel||"").replace(/\\/g,"/");
      if(_isSkillMdName(file.name))continue;
      const apath=dirPrefix&&rpath.startsWith(dirPrefix)?rpath.slice(dirPrefix.length):rpath;
      if(!apath||apath.startsWith(".")||/\.(png|jpe?g|gif|webp|ico|pdf|zip|woff2?)$/i.test(apath))continue;
      try{
        const text=await file.text();
        attachments.push({path:apath,name:file.name,text});
      }catch(_){}
    }
    bundles.push({name:top,markdown,attachments});
  }
  if(!bundles.length){alert("文件夹内未找到 SKILL.md");return}
  try{
    const r=await fetch("/api/skills/import-bundle",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({skills:bundles,upsert:true})});
    const d=await r.json();
    if(!r.ok)throw new Error(typeof d.detail==="string"?d.detail:JSON.stringify(d.detail||d));
    showToastMsg("文件夹导入："+(d.count||bundles.length)+" 个 SKILL");
    await ldSkills();
  }catch(err){alert(err.message||String(err))}
}
async function delSkill(id){
  if(!confirm("删除此 SKILL？"))return;
  await fetch("/api/skills/"+encodeURIComponent(id),{method:"DELETE"});
  await ldSkills();
}

/* ══ P3 AI问答 ══ */
const ORCH_PIPELINE_NODE_DEFS=[
  {id:"simple_intent_gate",label:"简单/复杂任务分流",default:true},
  {id:"intent_recognition",label:"意图识别（LLM）",default:true},
  {id:"query_rewrite",label:"Query 改写（LLM）",default:true},
  {id:"rewrite_confirm",label:"改写确认（HITL）",default:false},
  {id:"slot_fill",label:"业务对齐 / 槽位填充",default:true},
  {id:"task_decompose",label:"任务分解",default:true},
  {id:"intent_enhance",label:"意图增强（检索提示/核验）",default:false},
  {id:"rag_filter_confirm",label:"RAG 过滤确认",default:true},
  {id:"rag_decision",label:"RAG 决策与预取",default:true},
];
function defaultOrchPipelineNodes(){
  const m={};
  ORCH_PIPELINE_NODE_DEFS.forEach(n=>{m[n.id]=!!n.default;});
  return m;
}
function mergeOrchPipelineNodes(src){
  const m=defaultOrchPipelineNodes();
  if(src&&typeof src==="object"){
    ORCH_PIPELINE_NODE_DEFS.forEach(n=>{
      if(src[n.id]!=null)m[n.id]=!!src[n.id];
    });
  }
  return m;
}
const c=reactive({sid:"",mode:"normal",search:"",inp:"",msgs:[],th:"",model:"",agentId:"default",deepThink:false,webSearch:false,ragPrefetch:true,readComments:false,includeRss:false,uploads:[],recording:false,curTask:null,mainTaskHistory:[],taskExpanded:false,mainTaskHistoryOpen:false,taskHistPick:"",taskHistMenuOpen:false,taskHistSearchId:"",taskHistSearchName:"",taskHistSort:"time_desc",taskHistFilterSession:"",taskHistFilterStatus:"",taskHistFilterKind:"all",taskHistLoading:false,taskHistRemoteList:[],taskHistTotal:0,taskHistStats:null,taskHistMysqlInfo:null,taskHistSyncingId:"",taskHistDetailCache:{},taskHistDetailLoading:"",taskHistModalOpen:false,taskHistModalRow:null,taskHistModalFromChat:false,taskStatusMenuOpen:false,chatContextExpanded:false,chatStreaming:false,chatAbort:null,summaryPatches:[],rewriteDraft:"",rewriteCountdown:0,rewriteTimer:null,rewriteConfirmOpen:false,rewriteSnapshot:null,chatHitl:{active:false,kind:"",title:"",message:"",payload:null,traceId:"",taskId:"",threadId:"",phase:"",editText:"",keywordsLines:"",slotDomain:"",slotModule:"",slotNeedsRag:false,ragFilter:{domain:"",module:"",doc_type:"",keyword1:"",keyword2:""},ragVocab:{domain:[],module:[],doc_type:[],keyword1:[],keyword2:[]},termNotes:"",toolOptions:[]},chatHitlResumeMsg:null,platformHealth:null,platformHealthLoading:false,platformHealthOpen:false,memoryMeta:null,chatWarmup:{loading:false,ready:false,warming:false,readCommentsCached:false,toolsTotal:0,elapsedMs:0,phases:{},error:''},chatConnect:{active:false,doneFlash:false},chatPrefs:{showToolIo:false,autoFoldChain:true,showThinkBlocks:true,showTaskRail:false,showFooterOps:true,showCopyExport:true,wideChatArea:true,maxToolRounds:15,toolTimeoutSec:60,maxToolRetry:3,distinctToolFailLimit:3,streamIntervalMs:14,streamIntervalFastMs:5,contextMaxTokens:128000,contextWarnPct:80,orchPipelineNodes:defaultOrchPipelineNodes()},chatPanelTab:"room",sessionMenuId:""});
function switchChatPanel(tab){
  const t=tab==="config"?"config":"room";
  c.chatPanelTab=t;
  if(t==="room")nextTick(()=>{const el=document.querySelector(".chat-msgs");if(el)el.scrollTop=el.scrollHeight});
}
const PARENT_TASK_STATUSES=new Set(["created","summarizing","planning","executing","paused","abnormal","resolved","failed","closed"]);
const PARENT_STATUS_LABELS={
  created:"已创建",summarizing:"摘要中",planning:"计划中",executing:"执行中",
  paused:"暂停中",abnormal:"异常中",resolved:"已解决",failed:"已失败",closed:"已结案",
  started:"执行中",running:"执行中",
};
/** 主任务状态机：当前态 → 允许的目标态（禁止异常→已解决等） */
const PARENT_STATUS_TRANSITIONS={
  abnormal:["paused","closed"],
  resolved:["paused","closed"],
  executing:["paused","abnormal","resolved"],
  paused:["executing","closed","abnormal"],
  planning:["paused","executing","closed"],
  summarizing:["planning","paused","closed"],
  created:["planning","paused","closed"],
  failed:["closed","paused"],
};
function parentStatusTransitions(from){
  const s=normalizeParentTaskStatus(from,"executing");
  return PARENT_STATUS_TRANSITIONS[s]||[];
}
/** 主任务状态：拒绝把子步骤的 done/completed 误当成父任务终态 */
function normalizeParentTaskStatus(st,fallback="executing"){
  const s=String(st||"").toLowerCase();
  if(PARENT_TASK_STATUSES.has(s))return s;
  if(["completed","done","success","ok"].includes(s))return fallback;
  if(["failed","abnormal","error","err"].includes(s))return"abnormal";
  if(["thinking","acting","running","started"].includes(s))return"executing";
  return fallback;
}
function parentStatusLabel(st){const s=normalizeParentTaskStatus(st,"executing");return PARENT_STATUS_LABELS[s]||s||"—"}
function parentStatusClass(st){const s=normalizeParentTaskStatus(st,"executing");if(["resolved","closed"].includes(s))return"ok";if(["failed","abnormal"].includes(s))return"err";if(["paused"].includes(s))return"warn";if(["executing","running","started","summarizing","planning","created"].includes(s))return"run";return""}
function resolveParentTaskStatus(aiMsg){
  if(c.curTask&&aiMsg&&c.curTask.task_id===aiMsg.task_id&&isParentTaskStatusRaw(c.curTask.status))
    return normalizeParentTaskStatus(c.curTask.status,"executing");
  const audit=aiMsg&&aiMsg.task_audit;
  if(audit&&audit.status&&PARENT_TASK_STATUSES.has(String(audit.status).toLowerCase()))
    return String(audit.status).toLowerCase();
  const j=mapResultJudgment(aiMsg&&aiMsg.result_status);
  if(j==="success")return"resolved";
  if(j==="abnormal")return"abnormal";
  if(j==="paused")return"paused";
  if(j==="running"||j==="pending")return c.chatStreaming?"executing":"planning";
  return"executing";
}
function isParentTaskStatusRaw(st){return PARENT_TASK_STATUSES.has(String(st||"").toLowerCase())}
function formatDuration(ms){
  const n=Number(ms);
  if(!n||n<0)return"";
  if(n<1000)return Math.round(n)+"ms";
  return (n/1000).toFixed(n>=10000?0:1)+"s";
}
function looksLikeJsonBlob(t){
  const x=String(t||"").trim();
  if(!x)return false;
  if(x.startsWith("{")||x.startsWith("["))return true;
  return x.includes('"objective"')&&x.includes('"results"');
}
/** 工具参数里的检索词：兼容 string / string[] / 嵌套 tool_args */
function normalizeQueryList(val){
  if(val==null||val==="")return [];
  if(Array.isArray(val))return val.map(x=>String(x||"").trim()).filter(Boolean);
  if(typeof val==="object"){
    const nested=val.search_queries||val.queries||val.keywords;
    if(nested!=null&&nested!==val)return normalizeQueryList(nested);
    return [];
  }
  const s=String(val).trim();
  if(!s)return [];
  if(s.startsWith("[")&&s.endsWith("]")){
    try{
      const parsed=JSON.parse(s);
      if(Array.isArray(parsed))return normalizeQueryList(parsed);
    }catch(_){}
  }
  return s.split(/[;；,\n]/).map(x=>x.trim()).filter(Boolean);
}
function formatQueryListBrief(val,max=5){
  const arr=normalizeQueryList(val);
  if(!arr.length)return "";
  return arr.slice(0,max).join("；");
}
function extractToolNameFromStep(s){
  const jIn=parseStepJson(s&&s.input_text);
  if(jIn&&jIn.tool_name)return String(jIn.tool_name).trim();
  const jOut=parseStepJson(s&&s.output_text);
  if(jOut&&jOut.tool_name)return String(jOut.tool_name).trim();
  const nm=String(s&&s.step_name||"").trim();
  const m=nm.match(/^调用\s+([^\s(（]+)/);
  if(m)return m[1];
  if(nm.startsWith("MCP 工具:"))return nm.replace(/^MCP 工具:\s*/,"").trim();
  if(nm==="联网搜索")return"web_search";
  return nm||"工具";
}
function formatToolPillPrimary(s){
  return"调用 "+extractToolNameFromStep(s);
}
function coerceToolResult(tr){
  if(tr&&typeof tr==="object")return tr;
  if(typeof tr==="string"){
    const t=tr.trim();
    if(t.startsWith("{")||t.startsWith("[")){
      try{const o=JSON.parse(t);if(o&&typeof o==="object")return o;}catch(_){}
    }
  }
  return null;
}
function briefWebSearchDict(tr){
  if(!tr||typeof tr!=="object")return"";
  const res=Array.isArray(tr.results)?tr.results:[];
  const queries=Array.isArray(tr.search_queries)?tr.search_queries:[];
  const qOne=String(tr.query||(queries[0]||"")).trim();
  if(res.length){
    const titles=res.slice(0,2).map(r=>(r&&r.title)||(r&&r.url)||"").filter(Boolean);
    let line=queries.length
      ?("关键词 "+queries.slice(0,3).map(q=>String(q).slice(0,24)).join("、")+"，共 "+res.length+" 条")
      :(qOne?("「"+qOne.slice(0,30)+"」共 "+res.length+" 条"):("共 "+res.length+" 条"));
    if(titles.length)line+="："+titles.join("；");
    return line.slice(0,120);
  }
  const err=String(tr.error||"").trim();
  if(err)return err.slice(0,120);
  if(qOne)return ("「"+qOne.slice(0,40)+"」无检索结果").slice(0,120);
  return"无检索结果";
}
function summarizeToolResultCn(s){
  const st=String(s&&s.status||"").toLowerCase();
  const inFlight=["running","thinking","started","executing"].includes(st);
  const hasOut=!!(String(s&&s.output_text||"").trim());
  if(inFlight){
    const wait=String(s&&s.status_text||"").trim();
    if(wait&&wait!=="已完成"&&wait!=="完成")return wait.slice(0,120);
    return hasOut?"处理中…":"执行中…";
  }
  const rb=String(s&&(s.result_brief||s.description)||"").trim();
  if(rb&&rb!=="已返回"&&!looksLikeJsonBlob(rb))return rb.slice(0,120);
  const j=parseStepJson(s&&s.output_text);
  if(j&&j.tool_call===true){
    const name=j.tool_name||extractToolNameFromStep(s);
    if(j.error)return String(j.error).slice(0,120);
    const tr=coerceToolResult(j.tool_result);
    if(name==="web_search"&&tr){
      const line=briefWebSearchDict(tr);
      if(line)return line;
    }
    if(tr&&typeof tr==="object"){
      if(tr.ok===false&&tr.error)return String(tr.error).slice(0,120);
      if(Array.isArray(tr.results)){
        const line=briefWebSearchDict(tr);
        if(line)return line;
      }
      for(const k of ["message","detail","summary","result_msg"]){
        const v=tr[k];
        if(v&&String(v).trim()&&!looksLikeJsonBlob(String(v)))return String(v).slice(0,120);
      }
    }
    let msg=String(j.result_msg||"").trim();
    if(msg.startsWith(name+":"))msg=msg.slice(name.length+1).trim();
    const msgObj=looksLikeJsonBlob(msg)?coerceToolResult(msg):null;
    if(msgObj&&Array.isArray(msgObj.results)){
      const line=briefWebSearchDict(msgObj);
      if(line)return line;
    }
    if(msg&&!looksLikeJsonBlob(msg))return msg.slice(0,120);
    if(typeof j.tool_result==="string"&&!looksLikeJsonBlob(j.tool_result))return j.tool_result.slice(0,120);
    return"执行完成，详见输入/输出";
  }
  let brief=rb;
  const tn=extractToolNameFromStep(s);
  if(brief.startsWith(tn+":"))brief=brief.slice(tn.length+1).trim();
  if(brief&&!looksLikeJsonBlob(brief))return brief.slice(0,120);
  if(inFlight)return"执行中…";
  if(["done","completed"].includes(st))return"完成";
  return"执行中…";
}
function formatToolPillResult(s){
  return summarizeToolResultCn(s);
}
function formatStepBrief(s){
  if(!s)return"";
  if(s.node_kind==="tool_call"){
    return formatToolPillPrimary(s)+" 结果 "+formatToolPillResult(s);
  }
  const op=s.operation||s.step_name||"步骤";
  const tgt=s.target||"";
  const brief=s.result_brief||s.description||"";
  const links=(s.io_links||[]).filter(Boolean);
  if(links.length)return op+(tgt?" · "+tgt:"")+" — "+brief+" "+links.map(u=>'<a href="'+String(u).replace(/"/g,"")+'" target="_blank" rel="noopener">链接</a>').join(" ");
  if(brief)return op+(tgt?" · "+tgt:"")+" — "+brief;
  if(["done","completed"].includes(String(s.status||"")))return op+(tgt?" · "+tgt:"")+" — 已完成";
  return op+(tgt?" · "+tgt:"")+" — 执行中…";
}
function normalizeCurTask(raw){
  if(!raw||typeof raw!=="object")return null;
  const t={...raw};
  t.steps=Array.isArray(t.steps)?t.steps.filter(Boolean):[];
  return t;
}
function normalizeChatMsg(m){
  const x={...m};
  if(x.role==="assistant"){
    x.thinking=Array.isArray(x.thinking)?x.thinking.filter(Boolean):[];
    x.thinkingExpanded=x.thinkingExpanded!==false;
  }
  return x;
}
const STEP_LANE_ORDER={orchestration:0,prefetch:1,execution:2};
function stepLaneOrder(lane){
  const k=String(lane||"execution").toLowerCase();
  return STEP_LANE_ORDER[k]!=null?STEP_LANE_ORDER[k]:2;
}
function groupStepsBySubPlan(steps){
  const map=new Map();
  (Array.isArray(steps)?steps:[]).filter(Boolean).forEach(s=>{
    if(!s||typeof s!=="object")return;
    const pid=s.sub_plan_id||"_default";
    if(!map.has(pid))map.set(pid,{sub_plan_id:pid,sub_index:s.sub_index||0,step_lane:s.step_lane||"",steps:[]});
    const g=map.get(pid);
    if(!g||!Array.isArray(g.steps))return;
    g.steps.push(s);
    const si=Number(s.sub_index);
    if(Number.isFinite(si)&&si>0)g.sub_index=si;
    if(s.step_lane&&!g.step_lane)g.step_lane=s.step_lane;
  });
  return Array.from(map.values())
    .filter(p=>p&&Array.isArray(p.steps))
    .sort((a,b)=>{
      const oa=stepLaneOrder(a.step_lane||a.steps?.[0]?.step_lane);
      const ob=stepLaneOrder(b.step_lane||b.steps?.[0]?.step_lane);
      if(oa!==ob)return oa-ob;
      return(a.sub_index||0)-(b.sub_index||0);
    });
}
function stepIsSkipped(s){
  if(!s)return false;
  if(s.executed===false)return true;
  const st=String(s.status||"").toLowerCase();
  return st==="skipped"||st==="skip";
}
function pillStatusClass(s){
  if(stepIsSkipped(s))return"skip";
  const st=String(s&&s.status||"").toLowerCase();
  if(["failed","abnormal"].includes(st)||s.success===false)return"fail";
  if(["running","thinking","started","executing"].includes(st))return"run";
  return"ok";
}
function execPillClass(s){
  if(!s)return"orch";
  if(s.node_kind==="tool_call")return["is-tool",pillStatusClass(s)].filter(Boolean).join(" ");
  if(stepIsSkipped(s))return"skip orch";
  const st=pillStatusClass(s);
  return[st,st!=="ok"?"orch":""].filter(Boolean).join(" ");
}
async function loadPlatformHealth(refresh){
  if(c.platformHealthLoading)return;
  c.platformHealthLoading=true;
  try{
    const qs=new URLSearchParams();
    if(refresh)qs.set('refresh','1');
    const m=String(c.model||'').trim();
    if(m)qs.set('chat_model',m);
    const q=qs.toString();
    const url='/api/platform/health'+(q?'?'+q:'');
    const r=await fetch(url,{headers:authBearerHeaders()});
    const d=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(fmtApiErr(d,r));
    c.platformHealth=d&&typeof d==='object'?d:null;
  }catch(e){
    c.platformHealth={
      ready:false,
      items:[],
      summary:{ok:0,warn:0,error:1},
      all_ok:false,
      error:String(e&&e.message||e),
    };
  }finally{
    c.platformHealthLoading=false;
    maybeFinishChatConnect();
  }
}
function goHealthSettings(href){
  const h=String(href||'').trim();
  if(!h)return;
  let path=h.replace(/^\/#/,'').replace(/^#/,'');
  if(path.startsWith('page-'))path=path.slice(5);
  if(path&&typeof guardPageSwitch==='function'&&guardPageSwitch(path)){
    page.value=path;
    history.pushState({page:path},'',path==='video'?'/':'/'+path);
  }else location.href=h;
}
function execSubPlanTitle(plan,epi){
  if(!plan)return`步骤组 #${(epi||0)+1}`;
  const n=plan.sub_index;
  const idx=Number.isFinite(Number(n))&&Number(n)>0?Number(n):(epi||0)+1;
  const steps=Array.isArray(plan.steps)?plan.steps:[];
  const lane=String(plan.step_lane||steps[0]?.step_lane||"").toLowerCase();
  if(lane==="prefetch"){
    const tool=steps.find(s=>s&&s.node_kind==="tool_call");
    return tool?`步骤组 #${idx} · ${String(tool.step_name||"检索预取")}`:`步骤组 #${idx} · 检索预取`;
  }
  const tool=steps.find(s=>s&&s.node_kind==='tool_call');
  const react=steps.find(s=>{
    const ph=String(s&&s.phase||"").toLowerCase();
    return ph==="react_round"||ph==="react_think"||(s&&s.node_kind==="llm_call"&&String(s.step_name||"").includes("推理"));
  });
  if(tool&&react)return`步骤组 #${idx} · ReAct → ${formatToolPillPrimary(tool).replace(/^调用\s+/,"")}`;
  if(tool)return`步骤组 #${idx} · ${formatToolPillPrimary(tool)}`;
  const first=steps[0]||{};
  const hint=String(first.step_name||'').trim();
  if(hint)return`步骤组 #${idx} · ${hint.replace(/^推理分析\s*\/?\s*工具调用规划$/,"ReAct 推理")}`;
  return`步骤组 #${idx}`;
}
const ORCH_IO_PHASES=new Set(["intent","rewrite","slot","decompose","enhance","rag_decision","execute_prep"]);
function stripReactDisplayMarkers(text){
  if(!text)return'';
  return String(text)
    .replace(/^#{1,3}\s*Thought\s*\n?/gim,'')
    .replace(/^#{1,3}\s*Action\s*\n?/gim,'')
    .replace(/^#{1,3}\s*Observation\s*\n?/gim,'')
    .replace(/^#thought\s*\n?/gim,'')
    .trim();
}
function summarizeOrchJsonCn(j,phase){
  if(!j||typeof j!=="object"||Array.isArray(j))return"";
  const ph=String(phase||(j.phase)||"").toLowerCase();
  const lines=[];
  const mode=j.mode||j.intent_type||j.task_action;
  if(mode){
    const modeCn={new_main:"新建主任务",continue_main:"延续主任务",simple:"简单问答",task:"主任务",simple_chat:"简单问答"};
    lines.push("意图模式："+(modeCn[mode]||mode));
  }
  if(j.reason)lines.push("原因："+String(j.reason).slice(0,200));
  if(j.rewritten_query)lines.push("改写后："+String(j.rewritten_query).slice(0,200));
  if(j.query_summary&&!j.rewritten_query)lines.push("摘要："+String(j.query_summary).slice(0,120));
  if(j.domain)lines.push("业务域："+j.domain);
  if(j.operation_type)lines.push("操作："+j.operation_type);
  if(j.needs_rag!=null)lines.push("需知识库："+(j.needs_rag?"是":"否"));
  const arrKeys=[["retrieval_hints","检索提示"],["search_keyword_queries","检索词"],["verification_points","核验"],["sub_tasks","子任务"],["retrieval_terms","检索词"]];
  for(const[key,label]of arrKeys){
    const raw=j[key];
    if(Array.isArray(raw)&&raw.length){
      raw.slice(0,5).forEach((x,i)=>lines.push(label+(i+1)+"："+String(x).slice(0,100)));
      if(raw.length>5)lines.push("…共 "+raw.length+" 条");
    }
  }
  if(j.summary_cn)lines.push(String(j.summary_cn));
  if(j.search_objective)lines.push("检索目标："+String(j.search_objective).slice(0,160));
  if(j.result_brief_cn)lines.push(String(j.result_brief_cn));
  if(!lines.length&&ph)return"";
  return lines.join("\n");
}
function formatOrchThinkDisplay(s){
  if(!s)return"";
  const raw=String(s.think_text||"").trim();
  if(!raw)return"";
  const j=parseStepJson(raw);
  if(j&&typeof j==="object"&&!Array.isArray(j)){
    const cn=summarizeOrchJsonCn(j,s.phase);
    if(cn)return cn;
  }
  if(/^\s*[\{\[]/.test(raw)){
    const cn=summarizeOrchJsonCn(parseStepJson(raw),s.phase);
    if(cn)return cn;
  }
  return stripReactDisplayMarkers(raw);
}
function showOrchestrationThink(s){
  if(!s||!String(s.think_text||"").trim())return false;
  if(c.chatPrefs&&c.chatPrefs.showThinkBlocks===false)return false;
  if(s.node_kind==='tool_call')return false;
  const ph=String(s.phase||'').toLowerCase();
  if(ph==='react_round'||ph==='react_think'){
    return !!String(s.think_text||"").trim();
  }
  if(ph==='tool')return false;
  if(s.think_kind==="node_analysis")return true;
  if(ORCH_IO_PHASES.has(ph))return true;
  return!!s.llm_powered;
}
function hasStepIo(s){
  if(!s)return false;
  if(stepIsSkipped(s))return false;
  if(s.node_kind==="tool_call")return true;
  const ph=String(s.phase||"").toLowerCase();
  if(ORCH_IO_PHASES.has(ph)){
    return !!(String(s.input_text||"").trim()||String(s.output_text||"").trim());
  }
  return false;
}
function formatOrchStepInputDisplay(s){
  if(!s)return"—";
  const j=parseStepJson(s.input_text);
  if(j&&j.summary_cn)return String(j.summary_cn)+"\n\n"+JSON.stringify(j,null,2);
  if(j&&(j.node_kind==="orchestration"||ORCH_IO_PHASES.has(String(s.phase||"").toLowerCase()))){
    const cn=summarizeOrchJsonCn(j,s.phase);
    return(cn?cn+"\n\n":"")+JSON.stringify(j,null,2);
  }
  return formatStepInputDisplay(s);
}
function ragSliceParentName(sl){
  if(!sl||typeof sl!=="object")return"知识库片段";
  return String(sl.parent_document||sl.title||"知识库片段").trim()||"知识库片段";
}
function extractRagSlicesFromStep(s){
  const j=parseStepJson(s&&s.output_text);
  if(j&&Array.isArray(j.rag_slices)&&j.rag_slices.length)return j.rag_slices;
  if(s&&Array.isArray(s.rag_slices)&&s.rag_slices.length)return s.rag_slices;
  return[];
}
function isRagDecisionStep(s){
  return String(s&&s.phase||"").toLowerCase()==="rag_decision";
}
function formatOrchStepOutputDisplay(s){
  if(!s)return"—";
  const j=parseStepJson(s.output_text);
  if(isRagDecisionStep(s)&&j&&Array.isArray(j.rag_slices)&&j.rag_slices.length){
    const cn=summarizeOrchJsonCn(j,s.phase);
    const head=cn?cn+"\n\n":"";
    const lines=j.rag_slices.map(sl=>{
      const rid=sl.ref_id!=null?sl.ref_id:"?";
      const parent=ragSliceParentName(sl).slice(0,120);
      const src=String(sl.source_file||"").slice(0,200);
      const body=String(sl.content||"").trim();
      return `[${rid}] 父文档：${parent}${src?"\n父文档路径："+src:""}\n切片全文：\n${body}`;
    });
    return head+lines.join("\n\n---\n\n");
  }
  if(j&&j.summary_cn)return String(j.summary_cn)+"\n\n"+JSON.stringify(j,null,2);
  if(j&&(j.node_kind==="orchestration"||ORCH_IO_PHASES.has(String(s.phase||"").toLowerCase()))){
    const cn=summarizeOrchJsonCn(j,s.phase);
    return(cn?cn+"\n\n":"")+JSON.stringify(j,null,2);
  }
  return formatStepOutputDisplay(s);
}
function filterExecThinking(thinking){
  return(Array.isArray(thinking)?thinking:[]).filter(s=>{
    if(!s||typeof s!=="object")return false;
    if(s.node_kind==="tool_call")return true;
    if(s.node_kind==="orchestration"||s.step_lane==="orchestration"||s.step_lane==="prefetch")return true;
    if(stepIsSkipped(s))return false;
    const ph=String(s.phase||"").toLowerCase();
    const nm=String(s.step_name||"");
    if(ph==="rag_decision")return true;
    if(ph==="react_round"||ph==="react_think"){
      return !!(String(s.think_text||"").trim()||s.llm_powered);
    }
    if(ph==="reason"||ph==="llm"||ph==="tool")return false;
    if(nm.includes("答案组织")||nm.includes("LLM生成回答"))return false;
    if((nm.includes("推理分析")||nm.includes("推理与行动")||nm.includes("工具调用规划"))&&ph!=="react_round")return false;
    return true;
  });
}
function hasVisibleExecChain(thinking){return filterExecThinking(thinking).length>0}
function execThinkingForMsg(m,msgIndex){
  const raw=Array.isArray(m&&m.thinking)?m.thinking.filter(Boolean):[];
  if(raw.length)return raw;
  if(!m||m.role!=="assistant")return[];
  const tid=String(m.task_id||"").trim();
  if(!tid||!c.curTask||String(c.curTask.task_id||"")!==tid)return[];
  const steps=Array.isArray(c.curTask.steps)?c.curTask.steps.filter(Boolean):[];
  if(!steps.length)return[];
  let lastIdx=-1;
  for(let j=0;j<c.msgs.length;j++){
    const x=c.msgs[j];
    if(x&&x.role==="assistant"&&String(x.task_id||"")===tid)lastIdx=j;
  }
  if(Number(c.curTask.result_msg_index)===msgIndex||lastIdx===msgIndex)return steps;
  return[];
}
function groupExecPlans(thinking){return groupStepsBySubPlan(filterExecThinking(thinking))}
const chatGroupedSubPlans=computed(()=>{
  const steps=(c.curTask&&Array.isArray(c.curTask.steps))?c.curTask.steps:[];
  return groupStepsBySubPlan(steps);
});
function stepSuccessLabel(s){
  if(stepIsSkipped(s))return"跳过";
  if(s&&s.success===false)return"失败";
  if(s&&s.success===true)return"成功";
  const st=String(s&&s.status||"").toLowerCase();
  if(["failed","abnormal"].includes(st))return"失败";
  if(["done","completed","resolved"].includes(st))return"成功";
  if(["running","thinking","started","executing"].includes(st))return"进行中";
  if(s&&s.node_kind!=="tool_call")return"编排";
  return"—";
}
function stepStatusIcoClass(s){
  const lb=stepSuccessLabel(s);
  if(lb==="成功")return"ok";
  if(lb==="失败")return"fail";
  if(lb==="进行中")return"run";
  if(lb==="跳过")return"skip";
  return"neutral";
}
function stepConfidencePct(s){
  if(s&&s.node_kind!=="tool_call")return"—";
  const n=Number(s&&s.confidence);
  if(!Number.isFinite(n)||n<=0)return"—";
  return Math.round(n*100)+"%";
}
function parseStepJson(raw){
  if(raw==null||raw==="")return null;
  if(typeof raw==="object")return raw;
  try{return JSON.parse(String(raw))}catch(_){return null}
}
function isDocStepOutput(s){
  if(!s)return false;
  if(s.io_links&&s.io_links.length)return true;
  const j=parseStepJson(s.output_text);
  if(j&&(j.links||j.path||j.url||j.doc_id||j.file_path))return true;
  const ph=String(s.phase||"").toLowerCase();
  const nm=String(s.step_name||"");
  if(/doc|文档|longpage|feishu|docx|改写|创建/.test(nm+ph))return true;
  return false;
}
function formatStepInputDisplay(s){
  const j=parseStepJson(s&&s.input_text);
  if(s&&s.node_kind==="tool_call"&&j){
    const lines=[];
    if(j.tool_name)lines.push("工具："+j.tool_name);
    const kw=formatQueryListBrief(j.search_queries)||(j.tool_args?formatQueryListBrief(j.tool_args.search_queries):"");
    if(kw)lines.push("检索词："+kw);
    if(j.objective)lines.push("目标："+String(j.objective).slice(0,200));
    lines.push("—— JSON ——");
    lines.push(JSON.stringify(j,null,2));
    return lines.join("\n");
  }
  if(j)return JSON.stringify(j,null,2);
  return(s&&s.input_text)?String(s.input_text):"—";
}
function formatStepOutputDisplay(s){
  if(!s)return"—";
  const j=parseStepJson(s.output_text);
  const ph=String((j&&j.phase)||s.phase||"").toLowerCase();
  if(s&&s.node_kind==="tool_call"&&j&&j.tool_call===true){
    const head=["【摘要】"+summarizeToolResultCn(s),"—— JSON ——"];
    const body=JSON.stringify({
      schema_version:j.schema_version||1,
      tool_call:true,
      tool_name:j.tool_name||(parseStepJson(s.input_text)&&parseStepJson(s.input_text).tool_name)||"",
      tool_args:j.tool_args||(parseStepJson(s.input_text)&&parseStepJson(s.input_text).tool_args)||{},
      tool_result:j.tool_result,
      result_msg:j.result_msg||null,
      error:j.error||null,
      cost_ms:j.cost_ms!=null?j.cost_ms:(s.duration_ms||0),
      phase:j.phase||s.phase||"tool",
    },null,2);
    return head.join("\n")+"\n"+body;
  }
  if(j&&j.tool_call===true){
    const inp=parseStepJson(s.input_text);
    return JSON.stringify({
      schema_version:j.schema_version||1,
      tool_call:true,
      tool_name:j.tool_name||(inp&&inp.tool_name)||"",
      tool_args:j.tool_args||(inp&&inp.tool_args)||{},
      tool_result:j.tool_result,
      result_msg:j.result_msg||null,
      error:j.error||null,
      cost_ms:j.cost_ms!=null?j.cost_ms:(s.duration_ms||0),
      phase:j.phase||s.phase||"tool",
    },null,2);
  }
  if(j&&(j.node_kind==="orchestration"||ORCH_IO_PHASES.has(ph))){
    return JSON.stringify(j,null,2);
  }
  if(j&&j.tool_call===false){
    const out={
      schema_version:j.schema_version||1,
      phase:j.phase||s.phase||"",
      tool_call:false,
      tool_name:"",
      tool_args:{},
      tool_result:j.tool_result!=null?j.tool_result:null,
      error:j.error||null,
      cost_ms:j.cost_ms!=null?j.cost_ms:(s.duration_ms||0),
    };
    if(j.result_msg)out.result_msg=j.result_msg;
    const skip=new Set(["schema_version","phase","tool_call","tool_name","tool_args","tool_result","error","cost_ms","result_msg"]);
    for(const[k,v]of Object.entries(j)){
      if(!skip.has(k)&&v!=null&&v!=="")out[k]=v;
    }
    return JSON.stringify(out,null,2);
  }
  if(isDocStepOutput(s)){
    const links=[...(s.io_links||[])];
    if(j){
      if(Array.isArray(j.links))links.push(...j.links);
      if(j.path)links.push(j.path);
      if(j.url)links.push(j.url);
      if(j.file_path)links.push(j.file_path);
    }
    const uniq=[...new Set(links.filter(Boolean))];
    if(uniq.length)return uniq.join("\n");
  }
  if(j)return JSON.stringify(j,null,2);
  const txt=String(s.output_text||"").trim();
  if(txt)return txt;
  return "—";
}
function formatTaskIdFull(id,fallback){
  const s=String(id||fallback||"").trim();
  return s||"—";
}
function taskKindLabel(taskKind,taskId,subPlanId){
  if(taskKind==="simple")return"简单任务";
  if(taskKind==="main"&&taskId){
    const tid=formatTaskIdFull(taskId);
    const sid=subPlanId?formatTaskIdFull(subPlanId):"";
    return sid?`主任务 ${tid} / 子 ${sid}`:`主任务 ${tid}`;
  }
  if(taskKind==="pending")return"执行过程";
  return"执行过程";
}
function mainTaskCardLabel(){return"主任务"}
function execCardLabel(m){
  if(!m||m.role!=="assistant")return"执行过程";
  const tid=m.task_id||(c.curTask&&c.curTask.task_id);
  const sub=m.execSubPlanId||(c.curTask&&c.curTask.sub_plan_id);
  return taskKindLabel(m.execTaskKind||(c.curTask&&c.curTask.task_kind),tid,sub);
}
function mapResultJudgment(st){
  const s=String(st||"").toLowerCase();
  if(["failed","abnormal","err","error"].includes(s))return"abnormal";
  if(["resolved","completed","closed","ok","success"].includes(s))return"success";
  if(["paused"].includes(s))return"paused";
  if(["running","executing","started","created"].includes(s))return"running";
  return"pending";
}
function resultJudgmentLabel(j){
  const m={success:"已成功",abnormal:"异常中",failed:"异常中",paused:"暂停中",running:"进行中"};
  return m[j]||"—";
}
function resultJudgmentClass(j){
  if(j==="success")return"ok";
  if(j==="abnormal"||j==="failed")return"err";
  if(j==="running")return"run";
  if(j==="paused")return"warn";
  return"";
}
function msgErrLabel(m){
  return resultJudgmentLabel(mapResultJudgment(m&&m.result_status||(m&&m.span&&m.span.status)));
}
function msgErrClass(m){
  return resultJudgmentClass(mapResultJudgment(m&&m.result_status||(m&&m.span&&m.span.status)));
}
function isMainTaskFollowUpQuery(text){
  const t=String(text||"").trim();
  if(!t)return true;
  if(t.length<=3)return true;
  const low=t.toLowerCase();
  const hints=[
    "好了吗","好了没","什么情况","啥情况","怎么回事","进度","结果呢","现在呢",
    "继续当前","链接分析好了","分析好了","搞定了吗","出来了吗","完成了吗",
    "继续","接着","然后","那个任务","上面的任务","要你分析",
  ];
  if(hints.some(h=>t.includes(h)||low.includes(h)))return true;
  if(/^[?？!！。…\s]{0,6}(好|行|嗯|ok)+[?？!！。…\s]*$/i.test(t))return true;
  return false;
}
function findOriginUserQueryBefore(assistIdx){
  for(let j=assistIdx-1;j>=0;j--){
    const m=c.msgs[j];
    if(m&&m.role==="user"){
      const t=String(m.content||"").trim();
      if(t&&!isMainTaskFollowUpQuery(t))return t;
    }
  }
  for(let j=assistIdx-1;j>=0;j--){
    const m=c.msgs[j];
    if(m&&m.role==="user")return String(m.content||"").trim();
  }
  return "";
}
function dedupeMainTaskHistoryList(list){
  const byId=new Map();
  for(const raw of list||[]){
    if(!raw||!raw.task_id)continue;
    const tid=String(raw.task_id).trim();
    if(!tid.startsWith("task_"))continue;
    const prev=byId.get(tid);
    if(!prev){
      byId.set(tid,{...raw,task_id:tid});
      continue;
    }
    const uq=String(raw.user_query||"").trim();
    const patch={...prev,...raw,task_id:tid};
    const prevUq=String(prev.user_query||"").trim();
    if(prevUq&&!isMainTaskFollowUpQuery(prevUq)){
      patch.user_query=prevUq;
      patch.query_summary=String(prev.query_summary||prevUq).slice(0,80);
    }else if(uq&&!isMainTaskFollowUpQuery(uq)){
      patch.user_query=uq;
      patch.query_summary=String(raw.query_summary||uq).slice(0,80);
    }else{
      patch.user_query=prev.user_query;
      patch.query_summary=prev.query_summary;
    }
    byId.set(tid,patch);
  }
  return Array.from(byId.values());
}
function upsertMainTaskHistory(entry){
  if(!entry||!entry.task_id)return;
  const tid=String(entry.task_id).trim();
  const patch={...entry,task_id:tid};
  if(!patch.session_id&&c.sid)patch.session_id=c.sid;
  if(isMainTaskFollowUpQuery(patch.user_query||"")){
    delete patch.user_query;
    delete patch.query_summary;
  }
  const i=(c.mainTaskHistory||[]).findIndex(t=>t.task_id===tid);
  if(i>=0){
    const prev=c.mainTaskHistory[i]||{};
    const merged={...prev,...patch};
    const prevUq=String(prev.user_query||"").trim();
    if(prevUq&&!isMainTaskFollowUpQuery(prevUq)){
      merged.user_query=prevUq;
      if(prev.query_summary)merged.query_summary=prev.query_summary;
    }
    c.mainTaskHistory[i]=merged;
  }else c.mainTaskHistory.push(patch);
  c.mainTaskHistory=dedupeMainTaskHistoryList(c.mainTaskHistory);
}
const TASK_NAME_SEARCH_SYNONYMS={
  "小红书":["xhs","红书","xiaohongshu","薯","笔记"],
  "用户":["账号","作者","id","uid","号","用户id","用户ID"],
  "分析":["调研","画像","研究","报告","拆解"],
  "链接":["url","网址","笔记链接","作品"],
  "评论":["留言","弹幕","反馈"],
  "进度":["好了吗","执行到哪","状态","怎么样了"],
};
function expandTaskNameSearchTokens(q){
  const raw=String(q||"").trim().toLowerCase();
  if(!raw)return[];
  const set=new Set([raw]);
  for(const [key,aliases] of Object.entries(TASK_NAME_SEARCH_SYNONYMS)){
    const keyL=key.toLowerCase();
    const hit=raw.includes(keyL)||aliases.some(a=>raw.includes(String(a).toLowerCase()));
    if(hit){
      set.add(keyL);
      aliases.forEach(a=>set.add(String(a).toLowerCase()));
    }
  }
  return Array.from(set).filter(Boolean);
}
function isChatSessionMainTask(h){
  if(!h||!h.task_id)return false;
  const tid=String(h.task_id).trim();
  if(!tid.startsWith("task_"))return false;
  const kind=String(h.task_kind||"main").toLowerCase();
  if(kind==="pipeline"||kind==="simple")return false;
  const sid=String(c.sid||"").trim();
  if(sid){
    const hs=String(h.session_id||"").trim();
    if(hs&&hs!==sid)return false;
  }
  return true;
}
function filterChatSessionMainHistory(list){
  return dedupeMainTaskHistoryList(Array.isArray(list)?list:[]).filter(isChatSessionMainTask);
}
function taskHistRowMatches(h){
  if(!h)return false;
  const idQ=String(c.taskHistSearchId||"").trim().toLowerCase();
  const nameQ=String(c.taskHistSearchName||"").trim();
  if(idQ&&!String(h.task_id||"").toLowerCase().includes(idQ))return false;
  if(nameQ){
    const hay=(String(h.user_query||"")+" "+String(h.query_summary||"")+" "+String(h.session_id||"")).toLowerCase();
    const tokens=expandTaskNameSearchTokens(nameQ);
    if(!tokens.some(tok=>hay.includes(tok)))return false;
  }
  return true;
}
function sortTaskHistList(rows,sortKey){
  const arr=(Array.isArray(rows)?rows:[]).slice();
  const sk=String(sortKey||"time_desc");
  if(sk==="id_asc")return arr.sort((a,b)=>String(a.task_id||"").localeCompare(String(b.task_id||"")));
  if(sk==="id_desc")return arr.sort((a,b)=>String(b.task_id||"").localeCompare(String(a.task_id||"")));
  if(sk==="name_asc"){
    return arr.sort((a,b)=>String(a.user_query||a.query_summary||"").localeCompare(String(b.user_query||b.query_summary||""),"zh"));
  }
  if(sk==="name_desc"){
    return arr.sort((a,b)=>String(b.user_query||b.query_summary||"").localeCompare(String(a.user_query||a.query_summary||""),"zh"));
  }
  if(sk==="time_asc")return arr.sort((a,b)=>(a._histOrd||0)-(b._histOrd||0));
  return arr.sort((a,b)=>(b._histOrd||0)-(a._histOrd||0));
}
function syncMainTaskResultIndex(aiMsg){
  if(!aiMsg||!aiMsg.task_id)return;
  const idx=c.msgs.indexOf(aiMsg);
  if(idx<0)return;
  const judgment=mapResultJudgment(aiMsg.result_status||(aiMsg.span&&aiMsg.span.status));
  aiMsg.result_status=judgment;
  upsertMainTaskHistory({
    task_id:aiMsg.task_id,
    result_msg_index:idx,
    result_status:judgment,
    status:resolveParentTaskStatus(aiMsg),
    total_token_count:aiMsg.span&&aiMsg.span.total_token_count,
    total_duration_ms:aiMsg.span&&aiMsg.span.total_duration_ms,
  });
  if(c.curTask&&c.curTask.task_id===aiMsg.task_id){
    c.curTask.result_msg_index=idx;
    c.curTask.result_status=judgment;
  }
}
function rebuildMainTaskHistoryFromMsgs(){
  const byId=new Map();
  let lastUser="";
  for(let i=0;i<c.msgs.length;i++){
    const m=c.msgs[i];
    if(!m)continue;
    if(m.role==="user"){
      lastUser=String(m.content||"").trim();
      continue;
    }
    if(m.role!=="assistant")continue;
    if(m.ephemeral||m.task_kind==="simple")continue;
    const tid=String(m.task_id||"").trim();
    if(!tid||!tid.startsWith("task_"))continue;
    const j=mapResultJudgment(m.result_status||(m.span&&m.span.status));
    const st=resolveParentTaskStatus(m);
    let origin=findOriginUserQueryBefore(i);
    if(!origin)origin=isMainTaskFollowUpQuery(lastUser)?"":lastUser;
    const row={
      task_id:tid,
      session_id:c.sid||"",
      user_query:origin,
      query_summary:String(origin||tid).slice(0,80),
      status:st,
      task_kind:"main",
      result_msg_index:i,
      result_status:j,
      total_token_count:m.span&&m.span.total_token_count,
      total_duration_ms:m.span&&m.span.total_duration_ms,
    };
    const prev=byId.get(tid);
    if(!prev){
      byId.set(tid,row);
      continue;
    }
    if(origin&&!isMainTaskFollowUpQuery(origin)&&isMainTaskFollowUpQuery(prev.user_query||"")){
      prev.user_query=origin;
      prev.query_summary=String(origin).slice(0,80);
    }
    prev.status=st;
    prev.result_msg_index=i;
    prev.result_status=j;
    if(m.span&&m.span.total_token_count!=null)prev.total_token_count=m.span.total_token_count;
    if(m.span&&m.span.total_duration_ms!=null)prev.total_duration_ms=m.span.total_duration_ms;
  }
  c.mainTaskHistory=filterChatSessionMainHistory(Array.from(byId.values()));
}
const chatMainTaskHistory=computed(()=>{
  let rows=filterChatSessionMainHistory(c.mainTaskHistory||[]).map((h,i)=>({...h,task_kind:"main",_histOrd:i}));
  const stFilter=String(c.taskHistFilterStatus||"").trim().toLowerCase();
  if(stFilter)rows=rows.filter(h=>String(h.status||"").toLowerCase()===stFilter);
  rows=rows.filter(taskHistRowMatches);
  rows=sortTaskHistList(rows,c.taskHistSort||"time_desc");
  if(String(c.taskHistSort||"time_desc")==="time_desc")rows=rows.slice().reverse();
  return rows;
});
const taskHistDisplayCount=computed(()=>chatMainTaskHistory.value.length);
function refreshChatSessionTaskHistory(){
  if(c.msgs.length)rebuildMainTaskHistoryFromMsgs();
  c.mainTaskHistory=filterChatSessionMainHistory(c.mainTaskHistory||[]);
}
let _taskHistLoadTimer=null;
async function loadTaskRegistry(force){
  /* 任务中心独立页 / 全局查询用；AI 问答页任务历史仅用本会话 mainTaskHistory */
  if(page.value!=="tasks"&&!force)return;
  c.taskHistLoading=true;
  try{
    const qs=new URLSearchParams({
      session_id:c.taskHistFilterSession||"",
      task_id:c.taskHistSearchId||"",
      status:c.taskHistFilterStatus||"",
      task_kind:c.taskHistFilterKind||"all",
      name:c.taskHistSearchName||"",
      sort:c.taskHistSort||"time_desc",
      limit:"200",
    });
    const r=await fetch("/api/tasks/query?"+qs,{headers:authBearerHeaders()});
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||d.error||"查询失败");
    c.taskHistRemoteList=Array.isArray(d.tasks)?d.tasks:[];
    c.taskHistTotal=Number(d.total||c.taskHistRemoteList.length||0);
    c.taskHistStats=d.stats||null;
    c.taskHistMysqlInfo=d.mysql||null;
  }catch(e){
    showToastMsg("任务查询失败："+(e.message||e));
    const fallback=dedupeMainTaskHistoryList(c.mainTaskHistory||[]).map(h=>({...h,task_kind:"main",mysql_synced:false,redis_present:true,mysql_table:"span_tasks"}));
    c.taskHistRemoteList=fallback;
    c.taskHistTotal=fallback.length;
    c.taskHistStats=null;
  }finally{
    c.taskHistLoading=false;
    preloadTaskHistDetails(c.taskHistRemoteList);
  }
}
async function preloadTaskHistDetails(list){
  const rows=Array.isArray(list)?list.slice(0,50):[];
  if(!rows.length)return;
  await Promise.all(rows.map(h=>loadTaskHistDetail(h).catch(()=>null)));
}
function scheduleTaskRegistryReload(){
  if(page.value==="tasks"){
    if(_taskHistLoadTimer)clearTimeout(_taskHistLoadTimer);
    _taskHistLoadTimer=setTimeout(()=>loadTaskRegistry(true),350);
  }
}
function scheduleChatTaskHistReload(){
  if(_taskHistLoadTimer)clearTimeout(_taskHistLoadTimer);
  _taskHistLoadTimer=setTimeout(()=>refreshChatSessionTaskHistory(),120);
}
function setTaskHistKindFilter(kind){
  c.taskHistFilterKind=kind||"all";
  scheduleTaskRegistryReload();
}
function setTaskHistSort(sk){
  c.taskHistSort=sk||"time_desc";
  if(page.value==="tasks")scheduleTaskRegistryReload();
}
async function syncTaskToMysql(h){
  if(!h||!h.task_id)return;
  const tid=String(h.task_id);
  c.taskHistSyncingId=tid;
  try{
    const r=await fetch("/api/tasks/sync-mysql",{
      method:"POST",
      headers:{...authJsonHeaders()},
      body:JSON.stringify({task_id:tid,task_kind:h.task_kind||"main"}),
    });
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||d.error||"同步失败");
    showToastMsg("已同步到 MySQL："+tid);
    await loadTaskRegistry(true);
  }catch(e){
    showToastMsg("同步失败："+(e.message||e));
  }finally{
    c.taskHistSyncingId="";
  }
}
async function openRegistryTask(h){
  if(!h)return;
  const kind=String(h.task_kind||"main").toLowerCase();
  const tid=String(h.task_id||"").trim();
  if(kind==="pipeline"||/^[0-9a-f]{12}$/i.test(tid)){
    switchPage("video");
    await pollQueue();
    if(tid)selectQueueTask(tid);
    return;
  }
  const sid=String(h.session_id||"").trim();
  if(sid&&sid!==c.sid){
    await loadChatSession(sid);
  }
  switchPage("chat");
  setCurrentMainTaskFromHistory(h);
}

function taskHistDetailKey(h){
  if(!h)return"";
  return String(h.task_id||"")+":"+(h.task_kind||"main");
}
function taskHistDetailOf(h){
  const k=taskHistDetailKey(h);
  return k?(c.taskHistDetailCache[k]||null):null;
}
const taskHistModalDetail=computed(()=>{
  const h=c.taskHistModalRow;
  return h?taskHistDetailOf(h):null;
});
const taskHistModalLoading=computed(()=>{
  const h=c.taskHistModalRow;
  if(!h)return false;
  return c.taskHistDetailLoading===taskHistDetailKey(h);
});
async function loadTaskHistDetail(h,force){
  if(!h||!h.task_id)return null;
  const k=taskHistDetailKey(h);
  if(!force&&c.taskHistDetailCache[k])return c.taskHistDetailCache[k];
  c.taskHistDetailLoading=k;
  try{
    const qs=new URLSearchParams({task_id:String(h.task_id),task_kind:String(h.task_kind||"main")});
    const r=await fetch("/api/tasks/detail?"+qs,{headers:authBearerHeaders()});
    const d=await r.json();
    if(!r.ok)throw new Error(typeof d.detail==="string"?d.detail:(d.error||r.statusText));
    c.taskHistDetailCache[k]=d;
    return d;
  }catch(e){
    showToastMsg("任务详情加载失败："+(e.message||e));
    return null;
  }finally{
    if(c.taskHistDetailLoading===k)c.taskHistDetailLoading="";
  }
}
function taskDetailPayloadFromCur(){
  if(!c.curTask||!c.curTask.task_id)return null;
  const h=activeTaskHistoryEntry();
  if(h)return h;
  return{
    task_id:c.curTask.task_id,
    task_kind:c.curTask.task_kind||"main",
    session_id:c.sid,
    user_query:c.curTask.user_query||"",
    query_summary:c.curTask.query_summary||"",
    status:c.curTask.status,
    result_msg_index:c.curTask.result_msg_index,
    result_status:c.curTask.result_status||"pending",
  };
}
async function openTaskDetailFromChat(h){
  const row=h||taskDetailPayloadFromCur();
  if(!row||!row.task_id){showToastMsg("暂无任务详情");return;}
  c.taskHistMenuOpen=false;
  c.taskStatusMenuOpen=false;
  _detachTaskHistMenuCloser();
  _detachTaskStatusMenuCloser();
  c.taskHistModalFromChat=true;
  await openTaskHistModal(row);
}
async function openTaskHistModal(h){
  if(!h||!h.task_id)return;
  c.taskHistModalRow=h;
  openPageOverlay("taskHistModal",()=>{c.taskHistModalOpen=true;});
  if(!taskHistDetailOf(h))await loadTaskHistDetail(h);
}
function closeTaskHistModal(){
  c.taskHistModalOpen=false;
  c.taskHistModalRow=null;
  c.taskHistModalFromChat=false;
}
function closeTaskHistModalBack(){
  closeTaskHistModal();
}
function taskMetaLabel(key){
  return taskFieldLabel(key);
}
function taskHistDetailCounts(h){
  const d=taskHistDetailOf(h);
  if(!d)return null;
  return{
    fixed:(d.snapshot_fixed_rows||[]).length,
    open:(d.snapshot_open_rows||[]).length,
    tools:(d.tool_outputs||[]).length,
    steps:(d.steps||[]).length,
  };
}
function setCurrentMainTaskFromHistory(h){
  if(!h||!h.task_id)return;
  const tid=String(h.task_id).trim();
  const idx=h.result_msg_index;
  let steps=[];
  if(idx!=null&&c.msgs[idx]&&Array.isArray(c.msgs[idx].thinking))steps=c.msgs[idx].thinking.filter(Boolean).slice();
  else{
    for(let i=c.msgs.length-1;i>=0;i--){
      const m=c.msgs[i];
      if(m&&m.role==="assistant"&&String(m.task_id||"")===tid&&Array.isArray(m.thinking)&&m.thinking.length){
        steps=m.thinking.filter(Boolean).slice();
        break;
      }
    }
  }
  c.curTask=normalizeCurTask({
    task_id:tid,
    user_query:h.user_query||"",
    query_summary:h.query_summary||String(h.user_query||"").slice(0,80),
    status:normalizeParentTaskStatus(h.status||"executing","executing"),
    task_kind:h.task_kind||"main",
    sub_plan_id:"",
    steps,
    result_msg_index:idx!=null?idx:null,
    result_status:h.result_status||"pending",
    pipeline_task_ids:Array.isArray(h.pipeline_task_ids)?h.pipeline_task_ids.slice():[],
  });
  c.taskExpanded=false;
  c.taskHistMenuOpen=false;
  scheduleChatPersist();
  showToastMsg("已设为当前主任务："+tid);
}
function activeTaskHistoryEntry(){
  const tid=c.curTask&&c.curTask.task_id;
  if(!tid)return null;
  return(c.mainTaskHistory||[]).find(t=>t.task_id===tid)||null;
}
function jumpToMsgIndex(idx){
  const n=Number(idx);
  if(!Number.isFinite(n)||n<0)return;
  nextTick(()=>{
    const el=document.querySelector('.chat-turn[data-msg-index="'+n+'"]');
    if(el){
      el.scrollIntoView({behavior:"smooth",block:"center"});
      el.classList.add("chat-turn-highlight");
      setTimeout(()=>el.classList.remove("chat-turn-highlight"),2200);
    }
  });
}
function jumpToTaskResult(h){
  if(!h)return;
  if(h.result_msg_index!=null)jumpToMsgIndex(h.result_msg_index);
}
function jumpToCurTaskResult(){
  const h=activeTaskHistoryEntry();
  if(h)jumpToTaskResult(h);
  else if(c.curTask&&c.curTask.result_msg_index!=null)jumpToMsgIndex(c.curTask.result_msg_index);
}
function exportMsgMd(m,i){
  const text=m&&m.content?String(m.content):"";
  if(!text){showToastMsg("无内容可导出");return}
  const blob=new Blob([text],{type:"text/markdown;charset=utf-8"});
  const a=document.createElement("a");
  a.href=URL.createObjectURL(blob);
  a.download="reply-"+(Number(i)+1)+".md";
  a.click();
  URL.revokeObjectURL(a.href);
  showToastMsg("已导出 Markdown");
}
const chatExpandOpen=ref(false);
const cs=ref([]);
const chatModels=ref([{id:"",label:"auto（节点池）"},{id:"hunyuan",label:"混元（须配置 endpoint）"},{id:"gpt-4o",label:"GPT-4o"},{id:"claude-3-5-sonnet",label:"Claude 3.5"}]);
const BUILTIN_CHAT_AGENTS=[{id:"default",label:"这鱼"}];
const customAgents=ref([]);
const dbChatAgents=ref([]);
const chatAgents=computed(()=>{
  const fromDb=(dbChatAgents.value||[]).map(a=>({id:a.id,label:a.label||a.id}));
  const ids=new Set(fromDb.map(x=>x.id));
  const legacy=(customAgents.value||[]).filter(z=>!ids.has(z.id)).map(a=>({id:a.id,label:a.name||a.id}));
  return BUILTIN_CHAT_AGENTS.concat(fromDb).concat(legacy);
});
function ldAgentProfiles(){
  const o=safeJsonParse(localStorage.getItem('sba_agent_profiles_v1'),[]);
  customAgents.value=Array.isArray(o)?o:[];
}
function getAgentProfilePayload(){
  const id=(c.agentId||"").trim();
  if(!id.startsWith("c_"))return undefined;
  const a=(customAgents.value||[]).find(z=>z.id===id);
  if(!a)return undefined;
  return{name:a.name,description:a.description||"",tools_scope:a.tools_scope||"",framework:a.framework||"",boundaries:a.boundaries||""};
}
const chatSessionTitle=computed(()=>{
  if(!c.sid)return"新对话（发送首条消息后自动保存）";
  const s=(cs.value||[]).find(x=>x.id===c.sid);
  return(s&&(s.title||"会话"))||"当前会话";
});
const chatTopKpi=computed(()=>{
  const t=c.curTask;
  if(!t)return"等待任务";
  const lbl=taskKindLabel(t.task_kind,t.task_id,t.sub_plan_id);
  return lbl+" · "+parentStatusLabel(t.status);
});
const chatCurrentSubtask=computed(()=>{const t=c.curTask;if(!t||!t.steps||!t.steps.length)return"（尚无子步骤）";const run=t.steps.filter(s=>(s.status||"")==="running");if(run.length)return run[run.length-1].step_name||"执行中";const last=t.steps[t.steps.length-1];return last?(last.step_name+" · "+(last.status||"")):"—"});
function persistChatPrefs(){try{localStorage.setItem("sba_chat_prefs",JSON.stringify({model:c.model,agentId:c.agentId,deepThink:c.deepThink,webSearch:c.webSearch,ragPrefetch:c.ragPrefetch,readComments:c.readComments,includeRss:c.includeRss,chatPrefs:c.chatPrefs}))}catch(_){}}
function applyChatWarmupStatus(d){
  if(!d||typeof d!=='object')return;
  c.chatWarmup.ready=!!d.ready;
  c.chatWarmup.warming=!!d.warming;
  c.chatWarmup.toolsTotal=Number(d.tools_total||0);
  c.chatWarmup.elapsedMs=Number(d.elapsed_ms||0);
  c.chatWarmup.phases=(d.phases&&typeof d.phases==='object')?{...d.phases}:{};
  c.chatWarmup.error=String(d.error||'');
  const tc=d.tools_cached||{};
  c.chatWarmup.readCommentsCached=!!tc.read_comments;
  if(c.chatWarmup.warming||c.chatWarmup.loading)startWarmupPoll();
  else if(c.chatWarmup.ready)stopWarmupPoll();
  maybeFinishChatConnect();
}
let _chatConnectDoneTimer=null;
function beginChatConnect(){
  if(c.chatWarmup.ready&&!c.chatWarmup.warming&&!c.chatWarmup.loading&&!c.platformHealthLoading&&c.platformHealth&&c.platformHealth.ready)return;
  c.chatConnect.active=true;
  c.chatConnect.doneFlash=false;
  if(_chatConnectDoneTimer){clearTimeout(_chatConnectDoneTimer);_chatConnectDoneTimer=null;}
}
function finishChatConnect(){
  if(!c.chatConnect.active)return;
  c.chatConnect.doneFlash=true;
  if(_chatConnectDoneTimer)clearTimeout(_chatConnectDoneTimer);
  _chatConnectDoneTimer=setTimeout(()=>{
    c.chatConnect.active=false;
    c.chatConnect.doneFlash=false;
    _chatConnectDoneTimer=null;
  },1800);
}
function maybeFinishChatConnect(){
  if(!c.chatConnect.active||c.chatConnect.doneFlash)return;
  const warmOk=!!c.chatWarmup.ready&&!c.chatWarmup.warming&&!c.chatWarmup.loading;
  const healthOk=!c.platformHealthLoading&&(!!(c.platformHealth&&c.platformHealth.ready)||!!c.platformHealth);
  if(warmOk&&healthOk)finishChatConnect();
}
let _warmupPollTimer=null;
function stopWarmupPoll(){
  if(_warmupPollTimer){clearInterval(_warmupPollTimer);_warmupPollTimer=null;}
}
function startWarmupPoll(){
  if(_warmupPollTimer)return;
  _warmupPollTimer=setInterval(async ()=>{
    if(page.value!=='chat'){stopWarmupPoll();return;}
    try{
      const q=new URLSearchParams({
        read_comments:c.readComments?'true':'false',
        include_rag:c.ragPrefetch?'true':'false',
        wait:'false',
        force:'false',
      });
      const r=await fetch('/api/chat/warmup?'+q.toString(),{headers:authBearerHeaders()});
      const d=await r.json().catch(()=>({}));
      applyChatWarmupStatus(d);
      if(!c.chatWarmup.warming&&c.chatWarmup.ready)stopWarmupPoll();
    }catch(_){}
  },700);
}
const CHAT_CONNECT_STEPS=[
  {phase:'langgraph',label:'编排引擎',needsRag:false},
  {phase:'tools_mcp',label:'MCP',needsRag:false},
  {phase:'rag_milvus',label:'RAG',needsRag:true},
  {phase:'rag_embedder',label:'RAG 嵌入',needsRag:true,after:'rag_milvus'},
  {phase:'llm',label:'LLM',needsRag:false,isHealth:true},
];
const chatConnectVisible=computed(()=>!!c.chatConnect.active);
const chatConnectClass=computed(()=>c.chatConnect.doneFlash?'cc-done':'cc-loading');
const chatConnectLabel=computed(()=>{
  if(c.chatConnect.doneFlash)return'连接完成';
  const phases=c.chatWarmup.phases||{};
  const warming=!!(c.chatWarmup.warming||c.chatWarmup.loading);
  if(!c.chatWarmup.ready||warming){
    for(const step of CHAT_CONNECT_STEPS){
      if(step.needsRag&&!c.ragPrefetch)continue;
      if(step.isHealth)continue;
      if(step.after&&phases[step.after]&&!phases[step.after].ok)continue;
      const ph=phases[step.phase];
      if(!ph||ph.ok!==true)return'连接 '+step.label;
    }
    return'连接中';
  }
  if(c.platformHealthLoading||!(c.platformHealth&&c.platformHealth.ready))return'连接 LLM';
  return'连接中';
});
/** 后台/阻塞预热 MCP 工具、LangGraph、可选 RAG（未发问题前即可调用） */
async function requestChatWarmup(opts={}){
  const readComments=!!(opts.readComments!=null?opts.readComments:c.readComments);
  const includeRag=opts.includeRag!==false&&!!c.ragPrefetch;
  const wait=!!opts.wait;
  const force=!!opts.force;
  if(c.chatWarmup.warming&&!wait&&!force)return null;
  c.chatWarmup.loading=true;
  try{
    const q=new URLSearchParams({
      read_comments:readComments?'true':'false',
      include_rag:includeRag?'true':'false',
      force:force?'true':'false',
      wait:wait?'true':'false',
    });
    const r=await fetch('/api/chat/warmup?'+q.toString(),{headers:authBearerHeaders()});
    const d=await r.json().catch(()=>({}));
    applyChatWarmupStatus(d);
    return d;
  }catch(e){
    console.warn('[SBA chat warmup]',e);
    return null;
  }finally{
    c.chatWarmup.loading=false;
  }
}
async function ensureChatWarmupBeforeSend(){
  const needComments=!!c.readComments;
  const maxWaitMs=12000;
  const withCap=(p)=>Promise.race([
    p,
    new Promise((resolve)=>setTimeout(()=>resolve(null),maxWaitMs)),
  ]);
  if(!c.chatWarmup.ready){
    await withCap(requestChatWarmup({wait:true,readComments:needComments,includeRag:!!c.ragPrefetch}));
    return;
  }
  if(needComments&&!c.chatWarmup.readCommentsCached){
    await withCap(requestChatWarmup({wait:true,readComments:true,includeRag:!!c.ragPrefetch}));
  }
}
/** 应用挂载后后台预热，不阻塞首屏（与切到 chat 页逻辑一致） */
function kickoffChatWarmup(){
  requestChatWarmup({wait:false}).catch(()=>{});
}
/** 后台拉取会话上下文，补全任务轨与 memoryMeta（不切换 sid、不阻塞首屏） */
async function prefetchChatSessionContext(sid){
  if(!sid||sid==="temp"||c.chatStreaming)return;
  try{
    const r=await fetch("/api/chat/sessions/"+encodeURIComponent(sid),{headers:authBearerHeaders()});
    if(!r.ok)return;
    const d=await r.json();
    const srvHist=Array.isArray(d.main_task_history)?d.main_task_history:[];
    if(srvHist.length>(c.mainTaskHistory||[]).length){
      c.mainTaskHistory=filterChatSessionMainHistory(srvHist);
    }else if(!srvHist.length&&c.msgs.length){
      rebuildMainTaskHistoryFromMsgs();
    }
    if(d.cur_task&&!c.curTask)c.curTask=normalizeCurTask(d.cur_task);
    if(d.memory_meta&&typeof d.memory_meta==="object")c.memoryMeta=d.memory_meta;
    const serverMsgs=Array.isArray(d.messages)?d.messages:[];
    if(!c.msgs.length&&serverMsgs.length){
      c.msgs=serverMsgs.map(normalizeChatMsg);
      if(!c.mainTaskHistory.length&&c.msgs.length)rebuildMainTaskHistoryFromMsgs();
    }
  }catch(e){
    console.warn("[SBA chat session prefetch]",e);
  }
}
function chatCtxPct(s){
  const est=Number(s&&s.context_tokens_est)||0;
  const maxTok=Number(c.chatPrefs&&c.chatPrefs.contextMaxTokens)||128000;
  if(est<=0)return 0;
  return Math.min(100,Math.round(est/maxTok*100));
}
function chatCtxPctLabel(s){
  return chatCtxPct(s)+'%';
}
const filteredCs=computed(()=>{
  const term=(c.search||"").trim().toLowerCase();
  let list=[...(cs.value||[])];
  list.sort((a,b)=>String(b.updated_at||b.created_at||"").localeCompare(String(a.updated_at||a.created_at||"")));
  if(!term)return list;
  return list.filter(s=>String(s.title||"").toLowerCase().includes(term));
});
let chatSaveTimer=null;
function scheduleChatPersist(){
  clearTimeout(chatSaveTimer);
  chatSaveTimer=setTimeout(()=>persistChatSession(),800);
}
function persistChatLocalCache(){
  try{
    localStorage.setItem("sba_chat_local_v1",JSON.stringify({sid:c.sid,msgs:c.msgs,curTask:c.curTask,mainTaskHistory:c.mainTaskHistory||[],updated_at:new Date().toISOString()}));
  }catch(_){}
}
async function persistChatSession(){
  if(!c.sid||c.sid==="temp")return;
  else if(c.msgs.length)rebuildMainTaskHistoryFromMsgs();
  c.mainTaskHistory=filterChatSessionMainHistory(c.mainTaskHistory||[]);
  const payload={messages:c.msgs,cur_task:c.curTask,main_task_history:c.mainTaskHistory,prefs:{model:c.model,agentId:c.agentId,deepThink:c.deepThink,webSearch:c.webSearch,ragPrefetch:c.ragPrefetch,readComments:c.readComments,includeRss:c.includeRss,chatPrefs:c.chatPrefs}};
  persistChatLocalCache();
  try{
    await fetch("/api/chat/sessions/"+encodeURIComponent(c.sid)+"/state",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    ldCs();
  }catch(_){}
}
async function loadChatSession(sid){
  if(!sid||sid===c.sid)return;
  if(c.chatStreaming){showToastMsg("正在生成回答，请完成或暂停后再切换会话");return}
  c.sid=sid;c.mode="normal";
  try{
    const r=await fetch("/api/chat/sessions/"+encodeURIComponent(sid));
    if(!r.ok){c.msgs=[];c.curTask=null;return}
    const d=await r.json();
    c.msgs=(d.messages||[]).map(normalizeChatMsg);
    c.curTask=normalizeCurTask(d.cur_task);
    c.mainTaskHistory=filterChatSessionMainHistory(Array.isArray(d.main_task_history)?d.main_task_history:[]);
    c.memoryMeta=d.memory_meta&&typeof d.memory_meta==='object'?d.memory_meta:null;
    if(!c.mainTaskHistory.length)c.msgs.length&&rebuildMainTaskHistoryFromMsgs();
    else if(c.msgs.length)rebuildMainTaskHistoryFromMsgs();
    c.taskExpanded=false;
    const p=d.prefs||{};
    if(p.model!=null)c.model=p.model;
    if(p.agentId)c.agentId=p.agentId;
    if(p.deepThink!=null)c.deepThink=!!p.deepThink;
    if(p.webSearch!=null)c.webSearch=!!p.webSearch;
    if(p.ragPrefetch!=null)c.ragPrefetch=!!p.ragPrefetch;
    if(p.readComments!=null)c.readComments=!!p.readComments;
    if(p.includeRss!=null)c.includeRss=!!p.includeRss;
    if(p.chatPrefs)c.chatPrefs={...c.chatPrefs,...p.chatPrefs};
    if(c.chatPrefs.autoFoldChain){
      c.msgs.forEach(m=>{if(m.role==="assistant"&&m.thinking&&m.thinking.length)m.thinkingExpanded=false});
    }
    if(!c.mainTaskHistory.length)c.msgs.length&&rebuildMainTaskHistoryFromMsgs();
    nextTick(()=>{const el=document.querySelector(".chat-msgs");if(el)el.scrollTop=el.scrollHeight});
  }catch(_){c.msgs=[];c.curTask=null}
}
async function ldCs(){try{const r=await fetch('/api/chat/sessions');const d=await r.json();cs.value=d.sessions||[]}catch(e){}}
async function ensureChatSessionForSend(firstMsg){
  if(c.sid&&c.sid!=="temp")return c.sid;
  try{
    const r=await fetch('/api/chat/sessions',{method:'POST',headers:{"Content-Type":"application/json"},body:JSON.stringify({title:(firstMsg||"").slice(0,40)||"新对话"})});
    const d=await r.json();
    c.sid=d.session_id;c.mode='normal';
    await ldCs();
    return c.sid;
  }catch(e){c.sid="default";return c.sid}
}
async function newChatSess(){
  c.sid="";c.msgs=[];c.curTask=null;c.uploads=[];c.mode="normal";c.inp="";c.rewriteDraft="";c.rewriteConfirmOpen=false;clearRewriteCountdown();
  persistChatLocalCache();
}
async function delCs(sid){
  if(!confirm("删除此对话？"))return;
  await fetch('/api/chat/sessions/'+encodeURIComponent(sid),{method:'DELETE'});
  if(c.sid===sid){await newChatSess()}
  ldCs();
}
async function renameCs(s){
  const t=prompt("重命名对话",s.title||"新对话");
  if(t==null||!String(t).trim())return;
  await fetch("/api/chat/sessions/"+encodeURIComponent(s.id),{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({title:String(t).trim()})});
  if(c.sid===s.id)await ldCs();else ldCs();
}
async function closeCs(s){
  await fetch("/api/chat/sessions/"+encodeURIComponent(s.id)+"/close",{method:"POST"});
  if(c.sid===s.id&&c.curTask)c.curTask.status="closed";
  ldCs();
}
function exportCsMd(s){
  window.open("/api/chat/sessions/"+encodeURIComponent(s.id)+"/export-md","_blank");
}
function toggleCsMenu(sid){c.sessionMenuId=c.sessionMenuId===sid?"":sid}
function upImg(e){const files=Array.from(e.target.files||[]);files.forEach(f=>{const r=new FileReader();r.onload=ev=>c.uploads.push({type:'image',name:f.name,data:ev.target.result,preview:ev.target.result});r.readAsDataURL(f)});e.target.value=''}
function upFile(e){const files=Array.from(e.target.files||[]);files.forEach(f=>c.uploads.push({type:'file',name:f.name}));e.target.value=''}
let mediaRecorder=null,audioChunks=[];
function toggleVoice(){if(c.recording){if(mediaRecorder){mediaRecorder.stop()}c.recording=false;return}if(!navigator.mediaDevices){alert('浏览器不支持麦克风')}navigator.mediaDevices.getUserMedia({audio:true}).then(stream=>{mediaRecorder=new MediaRecorder(stream);audioChunks=[];mediaRecorder.ondataavailable=e=>audioChunks.push(e.data);mediaRecorder.onstop=()=>{const blob=new Blob(audioChunks,{type:'audio/webm'});const fd=new FormData();fd.append('audio',blob,'voice.webm');fetch('/api/chat/voice-to-text',{method:'POST',body:fd}).then(r=>r.json()).then(d=>{if(d.text){c.inp=d.text;autoResize()}}).catch(e=>console.error(e))};mediaRecorder.start();c.recording=true}).catch(e=>alert('麦克风权限被拒绝'))}
function autoResize(){}
const SLASH_SUGGEST_LIMIT=12;
const slashOpen=ref(false);
const slashItems=ref([]);
const slashIdx=ref(0);
const slashTotal=ref(0);
let slashTimer=null;
function scoreSlashItem(pref,item){
  const p=String(pref||"").trim().toLowerCase();
  const cmd=String(item&&item.command||"").toLowerCase();
  const name=String(item&&item.name||"").toLowerCase();
  const desc=String(item&&item.description||"").toLowerCase();
  const body=(cmd+" "+name+" "+desc).trim();
  if(!p||p==="/")return 1000;
  const q=p.startsWith("/")?p:p.startsWith("/")?p:"/"+p.replace(/^\//,"");
  const bare=q.replace(/^\//,"");
  if(!bare)return 1000;
  if(cmd===q||cmd===("/"+bare))return 2000;
  if(cmd.startsWith(q)||cmd.startsWith("/"+bare))return 1800-bare.length;
  if(name.startsWith(bare))return 1500;
  const initials=name.split(/[\s\-_/]+/).filter(Boolean).map(w=>w[0]||"").join("");
  if(initials&&initials.startsWith(bare))return 1300;
  if(cmd.includes(bare))return 1100;
  if(name.includes(bare))return 900;
  if(desc.includes(bare))return 700;
  if(body.includes(bare))return 500;
  return 0;
}
async function refreshSlashRaw(){
  const raw=(c.inp||"");
  const line=(raw.split("\n").pop())||"";
  const m=line.match(/(?:^|\s)(\/[^\s]*)$/);
  if(!m){slashOpen.value=false;slashTotal.value=0;return}
  const pref=(m[1]||"/").trim();
  if(!pref.startsWith("/")){slashOpen.value=false;slashTotal.value=0;return}
  try{
    const r=await fetch("/api/chat/slash-suggest?prefix="+encodeURIComponent(pref)+"&limit="+SLASH_SUGGEST_LIMIT);
    const d=await r.json();
    const all=Array.isArray(d.suggestions)?d.suggestions:[];
    slashTotal.value=Number(d.total||all.length||0);
    const ranked=all.slice().sort((a,b)=>scoreSlashItem(pref,b)-scoreSlashItem(pref,a)||String(a.command||"").localeCompare(String(b.command||"")));
    slashItems.value=ranked.slice(0,SLASH_SUGGEST_LIMIT);
    slashIdx.value=0;
    slashOpen.value=true;
  }catch(_){slashOpen.value=false;slashTotal.value=0}
}
function refreshSlash(){clearTimeout(slashTimer);slashTimer=setTimeout(refreshSlashRaw,90)}
function onChatInput(){autoResize();refreshSlash()}
function pickSlash(it){
  const raw=c.inp||"";
  const lines=raw.split("\n");
  const last=lines.length-1;
  const line=lines[last]||"";
  const idx=line.lastIndexOf("/");
  if(idx>=0)lines[last]=line.slice(0,idx)+(it.command||"")+" ";
  c.inp=lines.join("\n");
  slashOpen.value=false;
}
function renderMsg(m){
  if(!m||!m.content)return'';
  const raw=String(m.content||'');
  if(m._answerStreaming){
    let t=raw.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    t=t.replace(/\n/g,'<br>');
    return t+'<span class="stream-cursor" aria-hidden="true">▊</span>';
  }
  if(typeof marked!=='undefined'){
    try{
      let html=marked.parse(raw,{breaks:true,gfm:true});
      if(typeof DOMPurify!=='undefined')html=DOMPurify.sanitize(html);
      return html;
    }catch(_){}
  }
  let t=raw.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  t=t.replace(/```(\w*)\n([\s\S]*?)```/g,'<pre><code>$2</code></pre>');
  t=t.replace(/`([^`]+)`/g,'<code>$1</code>');
  t=t.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
  t=t.replace(/\n/g,'<br>');
  return t;
}
function renderWebSearchPanel(span){
  const s=span||{};
  const payload=s.web_search||s.webSearch||s.search_results||s.searchResults||s.web_result||s.webResult||null;
  if(!payload)return '';
  let data=payload;
  if(typeof data==='string'){
    try{data=JSON.parse(data)}catch(_){data={raw:data}}
  }
  const results=Array.isArray(data.results)?data.results:[];
  const err=data.error||s.web_search_error||s.search_error||'';
  let html='<div class="web-search-panel"><div class="web-search-hd">联网搜索结果</div>';
  if(err)html+='<div class="web-search-err">'+String(err).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')+'</div>';
  if(!results.length)html+='<div class="web-search-empty">暂无可展示结果</div>';
  else html+='<ol class="web-search-list">'+results.map((r,idx)=>{
    const title=String((r&&r.title)||('结果 '+(idx+1)));
    const url=String((r&&r.url)||'');
    const snip=String((r&&r.snippet)||'');
    return '<li class="web-search-item"><a href="'+url.replace(/&/g,'&amp;').replace(/"/g,'&quot;')+'" target="_blank" rel="noopener">'+title.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')+'</a><div class="web-search-url">'+url.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')+'</div><div class="web-search-snippet">'+snip.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')+'</div></li>';
  }).join('')+'</ol>';
  html+='</div>';
  return html;
}
const chatScrollAwayFromBottom=ref(false);
let _chatScrollStateRaf=0;
function _chatMsgsEl(){return document.querySelector('.chat-msgs')}
function updateChatScrollState(){
  const el=_chatMsgsEl();
  if(!el){chatScrollAwayFromBottom.value=false;return}
  const gap=el.scrollHeight-el.scrollTop-el.clientHeight;
  chatScrollAwayFromBottom.value=gap>72;
}
function bindChatMsgsScrollState(el){
  if(!el||el._chatScrollStateBound)return;
  el._chatScrollStateBound=true;
  el.addEventListener('scroll',()=>{
    if(_chatScrollStateRaf)return;
    _chatScrollStateRaf=requestAnimationFrame(()=>{
      _chatScrollStateRaf=0;
      updateChatScrollState();
    });
  });
  updateChatScrollState();
}
function chatScrollBottom(){
  nextTick(()=>{
    const el=_chatMsgsEl();
    if(!el)return;
    el.scrollTop=el.scrollHeight;
    updateChatScrollState();
  });
}
function chatScrollBottomClick(){chatScrollBottom()}
watch(()=>c.msgs.length,()=>nextTick(updateChatScrollState));
watch(()=>c.th,()=>nextTick(updateChatScrollState));
/**
 * 流式展示（仿 Claude text_delta / Cursor / Vercel AI SDK smoothStream）：
 * - 网络 chunk 只写入 pending，不直接整块贴到气泡
 * - 每 tick 用 Intl.Segmenter 吐出 1 个 grapheme；积压越多间隔越短
 * - 流式中 renderMsg 跳过 marked，结束后一次性 Markdown
 */
const _answerStreamState=new WeakMap();
function _streamIntervalMs(backlog){
  const prefs=c.chatPrefs||{};
  const base=Number(prefs.streamIntervalMs)||14;
  const fast=Number(prefs.streamIntervalFastMs)||5;
  const n=Number(backlog)||0;
  if(n>350)return fast;
  if(n>140)return Math.max(fast,base-6);
  if(n>40)return Math.max(fast,base-3);
  return base;
}
function _clearAnswerPlaceholder(aiMsg){
  if(!aiMsg.content||isChatLoadingPlaceholder(aiMsg.content))
    aiMsg.content='';
}
/** 流式占位文案：可被 pipeline_progress / orchestration_node_start 等真实 SSE 覆盖 */
function isChatLoadingPlaceholder(text){
  return /^(正在连接|正在准备|正在分析任务|正在生成回答|正在识别意图|正在延续|正在检索|正在编排)/.test(String(text||''));
}
function setChatProgressText(aiMsg,stage,fallback){
  const st=String(stage||fallback||'').trim();
  if(!st)return;
  if(isChatLoadingPlaceholder(aiMsg.content)||!aiMsg.content)
    aiMsg.content=st.slice(0,48)+'…';
}
function hasActiveMainTaskInHistory(){
  const hist=Array.isArray(c.mainTaskHistory)?c.mainTaskHistory:[];
  return hist.some(h=>{
    if(!h||!h.task_id)return false;
    const st=String(h.status||'').toLowerCase();
    return !['closed','resolved'].includes(st);
  });
}
function shouldKeepCurTaskOnSimpleIntent(){
  if(c.curTask&&String(c.curTask.task_id||'').trim()){
    const st=String(c.curTask.status||'').toLowerCase();
    if(!['closed','resolved'].includes(st))return true;
  }
  if((c.mainTaskHistory||[]).some(t=>t&&String(t.task_id||'').startsWith('task_')&&String(t.task_kind||'main')!=='simple'))
    return true;
  return hasActiveMainTaskInHistory();
}
function _splitGraphemes(text){
  const s=String(text||'');
  if(!s)return [];
  try{
    if(typeof Intl!=='undefined'&&Intl.Segmenter){
      const seg=new Intl.Segmenter('zh',{granularity:'grapheme'});
      return [...seg.segment(s)].map(x=>x.segment);
    }
  }catch(_){}
  return Array.from(s);
}
function _takeOneGrapheme(pending){
  const s=String(pending||'');
  if(!s)return{piece:'',rest:''};
  try{
    if(typeof Intl!=='undefined'&&Intl.Segmenter){
      const seg=new Intl.Segmenter('zh',{granularity:'grapheme'});
      const it=seg.segment(s)[Symbol.iterator]();
      const first=it.next();
      if(first.done)return{piece:'',rest:''};
      const piece=first.value.segment;
      const idx=first.value.index!=null?first.value.index:0;
      return{piece,rest:s.slice(idx+piece.length)};
    }
  }catch(_){}
  return{piece:s[0],rest:s.slice(1)};
}
function _ensureAnswerStreamState(aiMsg){
  if(!_answerStreamState.has(aiMsg)){
    _answerStreamState.set(aiMsg,{pending:'',mode:'token',pumpTimer:0});
  }
  return _answerStreamState.get(aiMsg);
}
function resetAnswerStream(aiMsg){
  const st=_answerStreamState.get(aiMsg);
  if(st&&st.pumpTimer){clearTimeout(st.pumpTimer);st.pumpTimer=0}
  _answerStreamState.delete(aiMsg);
  if(aiMsg){aiMsg._answerStreamBuf='';aiMsg._answerStreamMode='token'}
}
function _pumpAnswerStreamTick(aiMsg){
  const st=_ensureAnswerStreamState(aiMsg);
  st.pumpTimer=0;
  if(!st.pending)return;
  const {piece,rest}=_takeOneGrapheme(st.pending);
  st.pending=rest;
  if(piece){
    _clearAnswerPlaceholder(aiMsg);
    aiMsg._answerStreaming=true;
    aiMsg.content=(aiMsg.content||'')+piece;
    chatScrollBottom();
  }
  if(st.pending){
    st.pumpTimer=setTimeout(()=>_pumpAnswerStreamTick(aiMsg),_streamIntervalMs(st.pending.length));
  }
}
function _scheduleAnswerPump(aiMsg){
  const st=_ensureAnswerStreamState(aiMsg);
  if(st.pumpTimer)return;
  _pumpAnswerStreamTick(aiMsg);
}
function enqueueAnswerStream(aiMsg,chunk,opts){
  if(!chunk)return;
  const st=_ensureAnswerStreamState(aiMsg);
  const mode=(opts&&opts.stream_mode)==='replay'?'replay':'token';
  if(!aiMsg._answerStreamMode)aiMsg._answerStreamMode=mode;
  st.mode=mode==='replay'||aiMsg._answerStreamMode==='replay'?'replay':'token';
  aiMsg._answerStreamMode=st.mode;
  st.pending+=String(chunk);
  aiMsg._answerStreaming=true;
  _scheduleAnswerPump(aiMsg);
}
function flushAnswerStream(aiMsg){
  const st=_answerStreamState.get(aiMsg);
  if(st&&st.pumpTimer){clearTimeout(st.pumpTimer);st.pumpTimer=0}
  if(st&&st.pending){
    _clearAnswerPlaceholder(aiMsg);
    aiMsg.content=(aiMsg.content||'')+st.pending;
    st.pending='';
    chatScrollBottom();
  }
  resetAnswerStream(aiMsg);
  if(aiMsg)aiMsg._answerStreaming=false;
}
function mergeThinkingStep(arr,d){
  if(!Array.isArray(arr))return;
  const st={
    step_id:d.step_id,step_name:d.step_name,status:d.status||'done',
    description:d.description,duration_ms:d.elapsed_ms,objective:d.description||'',
    sub_plan_id:d.sub_plan_id,sub_index:d.sub_index,result_brief:d.result_brief,
    io_links:d.io_links||[],operation:d.operation,target:d.target,
    input_text:d.input_text||'',output_text:d.output_text||'',
    think_text:stripReactDisplayMarkers(d.think_text||''),
    think_kind:d.think_kind||'',
    node_kind:d.node_kind||'sub_task',
    llm_powered:!!d.llm_powered,
    phase:d.phase||'',
    step_lane:d.step_lane||'',
    executed:d.executed!==undefined?d.executed:true,
    success:d.success,confidence:d.confidence,token_count:d.token_count,
    io_expanded:!!(c.chatPrefs&&c.chatPrefs.showToolIo)||(d.node_kind!=="tool_call"&&ORCH_IO_PHASES.has(String(d.phase||"").toLowerCase())&&!!(d.input_text||d.output_text)),
    expanded:!!(c.chatPrefs&&c.chatPrefs.showToolIo),
  };
  const i=arr.findIndex(x=>x.step_id===d.step_id);
  if(i>=0){
    const prevThink=arr[i].think_text||'';
    Object.assign(arr[i],st);
    if(d.think_text)arr[i].think_text=stripReactDisplayMarkers(prevThink+(d.think_text||''));
    else if(prevThink)arr[i].think_text=prevThink;
    const endSt=String(d.status||st.status||"").toLowerCase();
    if(['done','completed','failed'].includes(endSt)&&!(c.chatPrefs&&c.chatPrefs.showToolIo))arr[i].io_expanded=false;
  }else arr.push(st);
  if(c.curTask&&Array.isArray(c.curTask.steps)){
    const ti=c.curTask.steps.findIndex(x=>x&&x.step_id===d.step_id);
    if(ti>=0)Object.assign(c.curTask.steps[ti],{...c.curTask.steps[ti],...st});
    else c.curTask.steps.push({...st});
  }
}
let _thinkDeltaRaf=0;
function scheduleThinkDeltaFlush(){
  if(_thinkDeltaRaf)return;
  _thinkDeltaRaf=requestAnimationFrame(()=>{_thinkDeltaRaf=0;chatScrollBottom()});
}

/** LangGraph HITL：统一状态与 resume 协议（action: confirm|pause|reintent|restart|switch_*） */
const HITL_KIND_LABELS={
  rewrite_confirm:"改写确认",
  slot_confirm:"业务槽位确认",
  rag_confirm:"检索词确认",
  rag_filter_confirm:"RAG 元数据筛选",
  tool_exception:"工具异常处理",
  paused:"编排已暂停",
  unknown:"人工确认",
};
function hitlKindTitle(kind){return HITL_KIND_LABELS[kind]||HITL_KIND_LABELS.unknown}
function clearChatHitl(){
  const h=c.chatHitl;
  h.active=false;h.kind="";h.title="";h.message="";h.payload=null;
  h.traceId="";h.taskId="";h.threadId="";h.phase="";
  h.editText="";h.keywordsLines="";h.slotDomain="";h.slotModule="";h.slotNeedsRag=false;
  h.ragFilter={domain:"",module:"",doc_type:"",keyword1:"",keyword2:""};
  h.ragVocab={domain:[],module:[],doc_type:[],keyword1:[],keyword2:[]};h.termNotes="";
  h.toolOptions=[];
  c.chatHitlResumeMsg=null;
  c.rewriteConfirmOpen=false;
}
function _syncHitlFormFromPayload(kind,payload){
  const p=payload&&typeof payload==="object"?payload:{};
  const snap=p.rewrite_snapshot||{};
  if(kind==="rewrite_confirm"){
    c.chatHitl.editText=String(snap.rewritten_query||p.rewritten_query||"").trim();
    c.rewriteDraft=c.chatHitl.editText;
    c.rewriteSnapshot=snap;
  }
  if(kind==="slot_confirm"){
    const slot=p.slot_snapshot||{};
    c.chatHitl.slotDomain=String(slot.domain||"");
    c.chatHitl.slotModule=String(slot.module||"");
    c.chatHitl.slotNeedsRag=!!slot.needs_rag;
  }
  if(kind==="rag_filter_confirm"){
    const ff=p.filter_form||{};
    c.chatHitl.ragFilter={
      domain:String(ff.domain||""),
      module:String(ff.module||""),
      doc_type:String(ff.doc_type||""),
      keyword1:String(ff.keyword1||""),
      keyword2:String(ff.keyword2||""),
    };
    const voc=p.vocabulary||{};
    c.chatHitl.ragVocab={
      domain:Array.isArray(voc.domain)?voc.domain:[],
      module:Array.isArray(voc.module)?voc.module:[],
      doc_type:Array.isArray(voc.doc_type)?voc.doc_type:[],
      keyword1:Array.isArray(voc.keyword1)?voc.keyword1:[],
      keyword2:Array.isArray(voc.keyword2)?voc.keyword2:[],
    };
    const notes=Array.isArray(p.term_mapping_notes)?p.term_mapping_notes:[];
    c.chatHitl.termNotes=notes.length?("术语映射："+notes.join("；")):"";
  }
  if(kind==="rag_confirm"){
    const kws=Array.isArray(p.keywords)?p.keywords:[];
    const enhKws=(c.curTask&&c.curTask.enhancement_snapshot&&c.curTask.enhancement_snapshot.search_keyword_queries)||[];
    const merged=kws.length?kws:(Array.isArray(enhKws)?enhKws:[]);
    c.chatHitl.keywordsLines=merged.map(x=>String(x||"").trim()).filter(Boolean).join("\n");
  }
  if(kind==="tool_exception"){
    c.chatHitl.toolOptions=Array.isArray(p.options)?p.options:[];
  }
}
function applyChatHitlFromSse(d,aiMsg){
  const kind=String(d.hitl_kind||d.kind||(d.hitl_payload&&d.hitl_payload.kind)||"").trim()||"unknown";
  const inner=d.hitl_payload&&typeof d.hitl_payload==="object"?d.hitl_payload:(d.payload&&typeof d.payload==="object"?d.payload:{});
  const h=c.chatHitl;
  h.active=true;
  h.kind=kind;
  h.title=hitlKindTitle(kind);
  h.message=String(d.message||inner.message||"请确认后继续编排");
  h.payload=inner;
  h.traceId=String(d.trace_id||inner.trace_id||"");
  h.taskId=String(d.task_id||inner.task_id||"");
  h.threadId=String(d.thread_id||d.session_id||c.sid||"");
  h.phase=String(d.orchestration_phase||"");
  _syncHitlFormFromPayload(kind,inner);
  c.chatHitlResumeMsg=aiMsg||c.chatHitlResumeMsg;
  if(aiMsg){
    aiMsg.content=(aiMsg.content&& !isChatLoadingPlaceholder(aiMsg.content))
      ?aiMsg.content
      :("⏸ "+h.title+"："+h.message);
    aiMsg._answerStreaming=false;
    flushAnswerStream(aiMsg);
  }
  if(c.curTask)c.curTask.status="paused";
  if(kind==="rewrite_confirm"&&c.chatHitl.editText){
    c.rewriteConfirmOpen=true;
    startRewriteCountdown();
  }
  scheduleChatPersist();
}
function buildChatStreamBasePayload(extra){
  const payload={
    session_id:c.sid,
    model:c.model,
    agent_id:c.agentId,
    rag_prefetch:!!c.ragPrefetch,
    web_search:!!c.webSearch,
    read_comments:!!c.readComments,
    deep_think:!!c.deepThink,
    cur_task:c.curTask,
    main_task_history:c.mainTaskHistory||[],
    chat_max_tool_rounds:c.chatPrefs.maxToolRounds||15,
    chat_tool_timeout_sec:c.chatPrefs.toolTimeoutSec||60,
    chat_tool_max_retry:c.chatPrefs.maxToolRetry||3,
    chat_distinct_tool_fail_limit:c.chatPrefs.distinctToolFailLimit||3,
    orch_pipeline_nodes:mergeOrchPipelineNodes(c.chatPrefs&&c.chatPrefs.orchPipelineNodes),
  };
  const ap=getAgentProfilePayload();
  if(ap)payload.agent_profile=ap;
  return Object.assign(payload,extra||{});
}
function buildHitlResumeBody(action,extra){
  const act=String(action||"confirm").trim().toLowerCase();
  const hitl={action:act};
  const kind=c.chatHitl.kind||"";
  if(kind==="rewrite_confirm"){
    const q=String((extra&&extra.rewritten_query)||c.chatHitl.editText||c.rewriteDraft||"").trim();
    if(q)hitl.rewritten_query=q;
  }
  if(kind==="rag_filter_confirm"){
    hitl.rag_metadata_filter={...c.chatHitl.ragFilter};
    hitl.filter_form={...c.chatHitl.ragFilter};
  }
  if(kind==="rag_confirm"){
    const raw=String((extra&&extra.keywordsText)!=null?extra.keywordsText:c.chatHitl.keywordsLines||"");
    const kws=raw.split(/\n|,|，/).map(s=>s.trim()).filter(Boolean);
    if(kws.length)hitl.keywords=kws;
  }
  if(kind==="slot_confirm"){
    hitl.slot_snapshot={
      domain:c.chatHitl.slotDomain,
      module:c.chatHitl.slotModule,
      needs_rag:!!c.chatHitl.slotNeedsRag,
    };
  }
  if(extra&&typeof extra==="object"){
    const ex={...extra};
    delete ex.action;
    Object.assign(hitl,ex);
  }
  return buildChatStreamBasePayload({
    session_id:c.sid||c.chatHitl.threadId,
    thread_id:c.chatHitl.threadId||c.sid,
    message:String((extra&&extra.message)||c.inp||"(HITL resume)").trim()||"(HITL resume)",
    hitl,
  });
}
function chatPauseStreaming(){
  if(c.chatAbort){try{c.chatAbort.abort()}catch(_){}}
  c.chatStreaming=false;
  const m=c.chatHitlResumeMsg||c.msgs[c.msgs.length-1];
  if(m&&m.role==="assistant"){
    m._answerStreaming=false;
    flushAnswerStream(m);
    if(!m.content||isChatLoadingPlaceholder(m.content))
      m.content="[已暂停] 流式输出已中断，可调整输入后重新发送";
  }
  if(c.curTask)c.curTask.status="paused";
  scheduleChatPersist();
  showToastMsg("已暂停当前流式请求");
}
function _snapshotChatHitl(){
  const h=c.chatHitl;
  return{
    active:h.active,kind:h.kind,title:h.title,message:h.message,
    payload:h.payload?JSON.parse(JSON.stringify(h.payload)):null,
    traceId:h.traceId,taskId:h.taskId,threadId:h.threadId,phase:h.phase,
    editText:h.editText,keywordsLines:h.keywordsLines,
    slotDomain:h.slotDomain,slotModule:h.slotModule,slotNeedsRag:h.slotNeedsRag,
    toolOptions:Array.isArray(h.toolOptions)?h.toolOptions.slice():[],
    rewriteDraft:c.rewriteDraft,rewriteConfirmOpen:c.rewriteConfirmOpen,
  };
}
function _restoreChatHitl(snap,aiMsg){
  if(!snap||!snap.active)return;
  const h=c.chatHitl;
  Object.assign(h,snap);
  h.payload=snap.payload;
  h.toolOptions=snap.toolOptions||[];
  c.chatHitlResumeMsg=aiMsg;
  c.rewriteDraft=snap.rewriteDraft||"";
  c.rewriteConfirmOpen=!!snap.rewriteConfirmOpen;
}
async function chatResumeHitl(action,extra){
  if(!c.chatHitl.active){showToastMsg("当前无待确认的编排节点");return}
  const aiMsg=c.chatHitlResumeMsg;
  if(!aiMsg||aiMsg.role!=="assistant"){showToastMsg("缺少可恢复的助手消息上下文");return}
  const hitlSnap=_snapshotChatHitl();
  const ac=new AbortController();
  c.chatAbort=ac;
  c.chatStreaming=true;
  clearChatHitl();
  try{
    const body=buildHitlResumeBody(action,extra);
    await runRepeatableChatSseStream(body,aiMsg,{signal:ac.signal,url:"/api/chat/graph/resume"});
  }catch(e){
    _restoreChatHitl(hitlSnap,aiMsg);
    if(e&&e.name==="AbortError"){
      aiMsg.content=(aiMsg.content||"")+"\n\n[已暂停]";
      if(c.curTask)c.curTask.status="paused";
    }else{
      aiMsg.content="[HITL 恢复失败] "+(e.message||e);
      showToastMsg(String(e.message||e));
    }
  }finally{
    c.chatStreaming=false;
    c.chatAbort=null;
    syncMainTaskResultIndex(aiMsg);
    scheduleChatPersist();
  }
}
function chatHitlConfirm(){
  chatResumeHitl("confirm",{});
}
function chatHitlPause(){
  if(c.chatStreaming)chatPauseStreaming();
  chatResumeHitl("pause",{});
}
function chatHitlReintent(){
  clearRewriteCountdown();
  chatResumeHitl("reintent",{});
}
function chatHitlToolOption(optId){
  const id=String(optId||"").trim();
  if(id==="pause"){chatHitlPause();return}
  chatResumeHitl(id||"confirm",{});
}
function chatPrimaryActionLabel(){
  if(c.chatHitl.active)return"确认继续";
  if(c.chatStreaming)return"暂停";
  return"发送";
}
function chatPrimaryActionDisabled(){
  if(c.chatHitl.active)return false;
  return !c.chatStreaming&&!c.inp.trim();
}

function ingestChatSseEvent(curEvent,d,aiMsg){
  if(curEvent==='stream_error'){
    // 禁止直出原始报错；后端应已改走 answer 事件，此处仅兜底等待分析结果
    aiMsg._errorAnalyzing=true;
    if(!aiMsg.content||/^(正在连接|正在准备)/.test(String(aiMsg.content||'')))
      aiMsg.content='正在分析错误原因，请稍候…';
    aiMsg._answerStreaming=false;
    flushAnswerStream(aiMsg);
    clearChatHitl();
    return;
  }
  if(curEvent==='hitl_required'||curEvent==='graph_interrupt'){
    applyChatHitlFromSse(d,aiMsg);
    return;
  }
  if(curEvent==='stream_open'){
    const stage=String(d.stage||'').trim();
    if(!aiMsg.content||isChatLoadingPlaceholder(aiMsg.content)){
      if(d.orchestration_engine==='langgraph')
        setChatProgressText(aiMsg,stage?'正在编排：'+stage:'','正在编排：启动');
      else if(d.orchestration_engine==='pending'||stage.indexOf('会话')>=0)
        aiMsg.content='正在准备会话上下文…';
      else if(stage)setChatProgressText(aiMsg,stage,'正在准备');
      else aiMsg.content='正在准备…';
    }
    aiMsg.orchestrationEngine=d.orchestration_engine||'';
    if(d.orchestration_engine==='langgraph'&&Array.isArray(d.expected_orchestration_phases))
      aiMsg.expectedOrchestrationPhases=d.expected_orchestration_phases.slice();
  }else if(curEvent==='pipeline_progress'){
    aiMsg.pipelineStage=d.stage||d.detail||'';
    setChatProgressText(aiMsg,d.stage||d.detail,'');
    const tidPg=String(d.task_id||aiMsg.task_id||'').trim();
    if(tidPg){
      if(!c.curTask||String(c.curTask.task_id||'')!==tidPg){
        const histRow=(c.mainTaskHistory||[]).find(t=>t.task_id===tidPg);
        c.curTask={
          task_id:tidPg,
          user_query:String(histRow&&histRow.user_query||'').trim(),
          query_summary:String(histRow&&histRow.query_summary||'').slice(0,80),
          status:'executing',task_kind:'main',sub_plan_id:'',steps:[],
          result_msg_index:null,result_status:'pending',
        };
      }
      aiMsg.task_id=tidPg;
      c.taskExpanded=true;
      aiMsg.thinkingExpanded=true;
    }
  }else if(curEvent==='orchestration_node_start'){
    aiMsg.pipelineStage=d.progress_hint||d.step_name||'';
    if(!Array.isArray(aiMsg.thinking))aiMsg.thinking=[];
    const orchStub={
      step_id:d.step_id||('orch_'+Date.now()),
      step_name:d.step_name||d.stage||'编排节点',
      status:'running',phase:d.phase||'orchestration',step_lane:'orchestration',
      node_kind:'orchestration',sub_plan_id:d.sub_plan_id||'',sub_index:d.sub_index||0,
      think_text:'',description:d.progress_hint||'',duration_ms:0,io_expanded:false,expanded:false,
    };
    aiMsg.thinking.push(orchStub);
    const tid=String(d.task_id||aiMsg.task_id||'').trim();
    if(tid){
      if(!c.curTask||String(c.curTask.task_id||'')!==tid){
        c.curTask={
          task_id:tid,user_query:'',query_summary:'',status:'executing',task_kind:'main',
          sub_plan_id:d.sub_plan_id||'',steps:[],result_msg_index:null,result_status:'pending',
        };
      }
      c.curTask.steps=c.curTask.steps||[];
      const dup=c.curTask.steps.find(x=>x.step_id===orchStub.step_id);
      if(!dup)c.curTask.steps.push({...orchStub});
      aiMsg.task_id=tid;
      c.taskExpanded=true;
      aiMsg.thinkingExpanded=true;
    }
    setChatProgressText(aiMsg,'正在编排：'+(d.step_name||d.stage||'节点'),'');
  }else if(curEvent==='thinking_start'){
    if(d.ephemeral){
      if(!aiMsg.content||aiMsg.content==='正在连接…')aiMsg.content='正在识别意图…';
    }else if(c.curTask&&(!d.task_id||c.curTask.task_id===d.task_id)){
      c.curTask.status='executing';
      upsertMainTaskHistory({task_id:c.curTask.task_id,status:'executing'});
    }
  }else if(curEvent==='task_created'){
    const uq=String(d.user_query||'').trim();
    const qs=String(d.query_summary||uq||'').slice(0,80);
    const tid=String(d.task_id||'').trim();
    const keepSteps=(c.curTask&&c.curTask.task_id===tid&&Array.isArray(c.curTask.steps))?c.curTask.steps.slice():[];
    const keepFromMsg=Array.isArray(aiMsg.thinking)?aiMsg.thinking.filter(Boolean).slice():[];
    const mergedSteps=keepSteps.length?keepSteps:keepFromMsg;
    c.curTask={
      task_id:d.task_id,user_query:uq,query_summary:qs,
      status:normalizeParentTaskStatus(d.status||'planning','planning'),
      task_kind:d.task_kind||'main',sub_plan_id:'',steps:mergedSteps,
      result_msg_index:null,result_status:'pending',
      rewrite_snapshot:d.rewrite_snapshot||null,
    };
    aiMsg.task_id=d.task_id;
    aiMsg.result_status='pending';
    aiMsg.execTaskKind=d.task_kind||'main';
    if(!aiMsg.content||isChatLoadingPlaceholder(aiMsg.content))
      aiMsg.content='正在分析任务…';
    upsertMainTaskHistory({
      task_id:d.task_id,
      user_query:uq,
      query_summary:qs,
      status:c.curTask.status,
      task_kind:c.curTask.task_kind,
      result_msg_index:null,
      result_status:'pending',
    });
    if(d.rewrite_snapshot&&c.curTask)c.curTask.rewrite_snapshot=d.rewrite_snapshot;
    if((d.task_kind||'main')!=='simple'&&d.persist_main_task!==false){
      c.taskExpanded=true;
      aiMsg.thinkingExpanded=true;
      if(tid&&(!aiMsg.thinking||!aiMsg.thinking.length)){
        aiMsg.thinking=[{
          step_id:'orch_boot_'+tid.slice(-8),
          step_name:'主任务编排',
          status:'running',
          phase:'execute_prep',
          node_kind:'orchestration',
          step_lane:'orchestration',
          sub_plan_id:d.sub_plan_id||'',
          sub_index:0,
          think_text:'',
          description:'',
          duration_ms:0,
          io_expanded:true,
          expanded:true,
        }];
        if(c.curTask){
          c.curTask.steps=Array.isArray(c.curTask.steps)?c.curTask.steps.slice():[];
          if(!c.curTask.steps.length)c.curTask.steps=aiMsg.thinking.slice();
        }
      }
    }
  }else if(curEvent==='context_memory'){
    if(d&&typeof d==='object'){
      c.memoryMeta=Object.assign({},c.memoryMeta||{},d);
      if(d.type==='context_pre_summary')showToastMsg(d.message||(d.llm_powered?'已用 LLM 生成会话摘要':'会话摘要压缩完成'));
      if(d.type==='context_force_switch'){
        showToastMsg(d.message||'上下文已满，请新建会话');
        c.mode='archived';
      }
    }
  }else if(curEvent==='intent_resolved'){
    const kind=d.task_kind||'main';
    const isContinue=!!d.continue_main_task||d.task_action==='continue_main';
    aiMsg.execTaskKind=kind;
    aiMsg.execSubPlanId=d.sub_plan_id||'';
    if(d.is_simple||kind==='simple'||d.persist_main_task===false){
      if(!shouldKeepCurTaskOnSimpleIntent())c.curTask=null;
      if(!d.task_id&&!shouldKeepCurTaskOnSimpleIntent())aiMsg.task_id='';
      if(!aiMsg.content||isChatLoadingPlaceholder(aiMsg.content))
        aiMsg.content='正在生成回答…';
    }else{
      const tid=String(d.task_id||'').trim();
      if(tid){
        aiMsg.task_id=tid;
        const preserve=!!d.preserve_task_identity||isContinue;
        const uq=String(d.user_query||'').trim();
        const lastUser=(c.msgs||[]).slice().reverse().find(m=>m&&m.role==='user');
        let userQ=uq||(lastUser&&lastUser.content)||'';
        let qs=String(d.query_summary||userQ||'').slice(0,80);
        if(preserve){
          const prev=c.curTask&&c.curTask.task_id===tid?c.curTask:null;
          const histRow=(c.mainTaskHistory||[]).find(t=>t.task_id===tid);
          if(prev&&prev.user_query&&!isMainTaskFollowUpQuery(prev.user_query)){
            userQ=String(prev.user_query).trim();
            qs=String(prev.query_summary||userQ).slice(0,80);
          }else if(histRow&&histRow.user_query&&!isMainTaskFollowUpQuery(histRow.user_query)){
            userQ=String(histRow.user_query).trim();
            qs=String(histRow.query_summary||userQ).slice(0,80);
          }else if(uq&&!isMainTaskFollowUpQuery(uq)){
            /* 后端已下发锁定摘要 */
          }else if(isMainTaskFollowUpQuery(userQ)){
            userQ=String(histRow&&histRow.user_query||prev&&prev.user_query||'').trim();
            qs=String(histRow&&histRow.query_summary||prev&&prev.query_summary||userQ).slice(0,80);
          }
        }
        const prevSteps=(c.curTask&&c.curTask.task_id===tid&&Array.isArray(c.curTask.steps))?c.curTask.steps.slice():[];
        const msgSteps=Array.isArray(aiMsg.thinking)?aiMsg.thinking.filter(Boolean).slice():[];
        const mergedSteps=prevSteps.length?prevSteps:msgSteps;
        if(!c.curTask||c.curTask.task_id!==tid){
          c.curTask={
            task_id:tid,user_query:userQ,query_summary:qs,
            status:isContinue?'executing':'planning',task_kind:kind,sub_plan_id:d.sub_plan_id||'',steps:mergedSteps,
            result_msg_index:null,result_status:'pending',
            rewrite_snapshot:d.rewrite_snapshot||null,
          };
        }else{
          if(!c.curTask.steps||!c.curTask.steps.length)c.curTask.steps=mergedSteps;
          c.curTask.task_kind=kind;
          c.curTask.sub_plan_id=d.sub_plan_id||c.curTask.sub_plan_id||'';
          if(isContinue)c.curTask.status='executing';
          if(!preserve){
            if(userQ&&!c.curTask.user_query)c.curTask.user_query=userQ;
            if(qs)c.curTask.query_summary=qs;
          }else{
            if(userQ&&!isMainTaskFollowUpQuery(userQ)){
              if(!c.curTask.user_query||isMainTaskFollowUpQuery(c.curTask.user_query))c.curTask.user_query=userQ;
              if(!c.curTask.query_summary||isMainTaskFollowUpQuery(c.curTask.query_summary))c.curTask.query_summary=qs;
            }
          }
          if(d.rewrite_snapshot)c.curTask.rewrite_snapshot=d.rewrite_snapshot;
        }
        upsertMainTaskHistory({
          task_id:tid,
          user_query:c.curTask.user_query,
          query_summary:c.curTask.query_summary,
          status:c.curTask.status,
          task_kind:kind,
        });
      }
      if(d.rewrite_snapshot&&c.curTask)c.curTask.rewrite_snapshot=d.rewrite_snapshot;
      if(isContinue)setChatProgressText(aiMsg,(d.needs_rag||c.ragPrefetch)?'正在检索知识库':'正在延续主任务','');
      if(kind==='main'&&!d.is_simple){
        c.taskExpanded=true;
        aiMsg.thinkingExpanded=true;
      }
    }
  }else if(curEvent==='step_think_start'){
    const ph=String(d.phase||'').toLowerCase();
    const stub={
      step_id:d.step_id,step_name:d.step_name,status:'thinking',think_text:'',description:'',
      duration_ms:0,io_expanded:false,think_kind:d.think_kind||'',llm_powered:!!d.llm_powered,
      phase:d.phase||'',
      sub_plan_id:d.sub_plan_id,sub_index:d.sub_index,step_lane:d.step_lane||'execution',
      node_kind:ph==='react_round'||ph==='react_think'?'llm_call':(d.node_kind||'sub_task'),
    };
    const arr=aiMsg.thinking||[];
    const ex=arr.find(x=>x.step_id===d.step_id);
    if(ex)Object.assign(ex,stub);else arr.push(stub);
    aiMsg.thinking=arr;
    if(c.curTask){
      c.curTask.steps=c.curTask.steps||[];
      const ex2=c.curTask.steps.find(x=>x.step_id===d.step_id);
      if(ex2)Object.assign(ex2,stub);else c.curTask.steps.push({...stub});
    }
  }else if(curEvent==='step_think_delta'){
    if(!Array.isArray(aiMsg.thinking))return;
    const st=aiMsg.thinking.find(x=>x.step_id===d.step_id);
    if(st){
      st.think_text=stripReactDisplayMarkers((st.think_text||'')+(d.content||''));
      st.llm_powered=!!(st.llm_powered||d.llm_powered);
      if(d.think_kind)st.think_kind=d.think_kind;
      scheduleThinkDeltaFlush();
    }
  }else if(curEvent==='thought_step_start'){
    const ph=String(d.phase||"").toLowerCase();
    const nk=d.node_kind||'sub_task';
    const isOrch=nk==='orchestration'||ph==='execute_prep'||ph==='intent'||d.step_lane==='orchestration';
    const ioOpen=!!(c.chatPrefs&&c.chatPrefs.showToolIo)||nk==='tool_call';
    const stub={
      step_id:d.step_id,step_name:d.step_name,status:d.status||'running',
      duration_ms:0,description:d.description||'',think_text:d.think_text||'',
      input_text:d.input_text||'',output_text:d.output_text||'',
      sub_plan_id:d.sub_plan_id,sub_index:d.sub_index,operation:d.operation,target:d.target,
      node_kind:isOrch?'orchestration':nk,llm_powered:!!d.llm_powered,think_kind:d.think_kind||'',
      phase:d.phase||'',step_lane:isOrch?'orchestration':(d.step_lane||'execution'),
      io_expanded:ioOpen,expanded:ioOpen||isOrch,
    };
    const arr=aiMsg.thinking||[];
    const ex=arr.find(x=>x.step_id===d.step_id);
    if(ex)Object.assign(ex,stub);else arr.push(stub);
    aiMsg.thinking=arr;
    const tid=String(d.task_id||aiMsg.task_id||(c.curTask&&c.curTask.task_id)||'').trim();
    if(tid){
      aiMsg.task_id=tid;
      if(!c.curTask||String(c.curTask.task_id||'')!==tid){
        const histRow=(c.mainTaskHistory||[]).find(t=>t.task_id===tid);
        c.curTask={
          task_id:tid,
          user_query:String(histRow&&histRow.user_query||'').trim(),
          query_summary:String(histRow&&histRow.query_summary||'').slice(0,80),
          status:'executing',task_kind:'main',sub_plan_id:d.sub_plan_id||'',steps:[],
          result_msg_index:null,result_status:'pending',
        };
      }
      c.curTask.steps=c.curTask.steps||[];
      const ex2=c.curTask.steps.find(x=>x.step_id===stub.step_id);
      if(ex2)Object.assign(ex2,stub);else c.curTask.steps.push({...stub});
      c.taskExpanded=true;
      aiMsg.thinkingExpanded=true;
    }
  }else if(curEvent==='thought_step_end'){
    if(!Array.isArray(aiMsg.thinking))aiMsg.thinking=[];
    mergeThinkingStep(aiMsg.thinking,d);
    if(c.curTask){
      c.curTask.steps=Array.isArray(c.curTask.steps)?c.curTask.steps:[];
      mergeThinkingStep(c.curTask.steps,d);
    }
    if(d.phase==='intent'||(d.step_name||'').includes('意图')){
      const j=parseStepJson(d.output_text);
      const kind=(j&&j.task_kind)||(j&&j.is_simple?'simple':'main');
      aiMsg.execTaskKind=kind;
      if(c.curTask){c.curTask.task_kind=kind;if(d.sub_plan_id)c.curTask.sub_plan_id=d.sub_plan_id}
      const snap={
        query:String(d.input_text||''),
        rewritten_query:(j&&j.rewritten_query)||String(d.input_text||''),
        keywords:(j&&j.keywords)||[],
        needs_rag:!!(j&&j.needs_rag),
        metadata:(j&&j.metadata)||{},
        rewrite_state:(j&&j.rewrite_state)||'rewrite_confirm',
        confidence:(j&&j.confidence)||0,
      };
      if(c.curTask)c.curTask.rewrite_snapshot=snap;
      aiMsg.rewriteSnapshot=snap;
    }
  }else if(curEvent==='thinking_delta'){
    c.th=(c.th||'')+(d.content||'');
  }else if(curEvent==='thinking_end'){
    c.th='';
    if(c.curTask)c.curTask.bundle=d.bundle;
  }else if(curEvent==='answer_preface'){
    if(d.content){
      if(!aiMsg.content||/^(正在连接|正在准备|正在分析任务)/.test(String(aiMsg.content||'')))aiMsg.content='';
      aiMsg.content=String(d.content||'');
      chatScrollBottom();
    }
    if(c.curTask)c.curTask.status='executing';
  }else if(curEvent==='rag_prefetch_slices'){
    const slices=Array.isArray(d.slices)?d.slices:[];
    if(slices.length){
      aiMsg.ragPrefetchSlices=slices;
      if(c.curTask)c.curTask.rag_prefetch_slices=slices;
      const arr=Array.isArray(aiMsg.thinking)?aiMsg.thinking:[];
      const hit=arr.find(x=>x&&String(x.phase||"").toLowerCase()==="rag_decision");
      if(hit){
        hit.rag_slices=slices;
        try{
          const j=parseStepJson(hit.output_text);
          if(j&&typeof j==="object"){
            j.rag_slices=slices;
            j.prefetch_count=slices.length;
            hit.output_text=JSON.stringify(j);
          }
        }catch(_){}
      }
    }
  }else if(curEvent==='prefetch_segment_start'){
    aiMsg.prefetchSegmentLabel=d.label||'检索预取';
    if(c.curTask)c.curTask.status='executing';
  }else if(curEvent==='execution_segment_start'){
    aiMsg.executionSegmentLabel=d.label||'ReAct 执行';
    if(c.curTask)c.curTask.status='executing';
  }else if(curEvent==='pipeline_wait_start'){
    aiMsg.pipelineWaitLabel=d.label||'等待后台流水线';
    aiMsg.pipelineWaitIds=Array.isArray(d.pipeline_task_ids)?d.pipeline_task_ids:[];
    if(c.curTask){
      c.curTask.status='executing';
      c.curTask.pipeline_wait=true;
    }
  }else if(curEvent==='pipeline_wait_progress'){
    const st=d.statuses||{};
    const parts=Object.keys(st).map(k=>k+':'+st[k]);
    aiMsg.pipelineWaitLabel='流水线 '+parts.join(' · ');
    if(c.curTask)c.curTask.pipeline_statuses=st;
  }else if(curEvent==='pipeline_wait_end'){
    aiMsg.pipelineWaitLabel=d.ok?'流水线已完成':(d.timeout?'流水线等待超时':'流水线结束');
    if(c.curTask){
      c.curTask.pipeline_wait=false;
      if(d.ok)c.curTask.status='executing';
    }
  }else if(curEvent==='answer_generating'){
    aiMsg.answerStageLabel=d.label||'生成回答';
    if(!aiMsg.content||/^(正在连接|正在准备|正在分析任务|正在识别意图)/.test(String(aiMsg.content||'')))
      aiMsg.content='正在生成回答…';
  }else if(curEvent==='answer_start'){
    aiMsg._answerStreaming=true;
    if(d.error_analyzed){
      aiMsg._errorAnalyzing=false;
      resetAnswerStream(aiMsg);
    }
    if(d.stream_mode)aiMsg._answerStreamMode=d.stream_mode;
  }else if(curEvent==='answer_delta'){
    if(d.content&&d.kind!=='preface'){
      if(d.stream_mode)aiMsg._answerStreamMode=d.stream_mode;
      enqueueAnswerStream(aiMsg,d.content,{stream_mode:d.stream_mode||aiMsg._answerStreamMode});
    }
  }else if(curEvent==='answer_end'){
    flushAnswerStream(aiMsg);
    aiMsg._answerStreaming=false;
    aiMsg.span={
      ...aiMsg.span,
      total_token_count:d.token_usage?d.token_usage.prompt+d.token_usage.completion:0,
      answer_stream_done:true,
    };
    if(d.search_results)aiMsg.span.search_results=d.search_results;
    if(isChatLoadingPlaceholder(aiMsg.content))aiMsg.content='';
  }else if(curEvent==='task_completed'){
    aiMsg.span={...aiMsg.span,...d};
    if(Array.isArray(d.tool_outputs))aiMsg.span.tool_outputs=d.tool_outputs;
    if(d.snapshot_json)aiMsg.span.snapshot_json=d.snapshot_json;
    const persist=d.persist_main_task!==false&&!d.ephemeral&&!!(d.task_id||'').trim();
    if(persist){
      aiMsg.task_audit={task_id:d.task_id,status:d.status,snapshot_json:d.snapshot_json,tool_outputs:d.tool_outputs};
      aiMsg.result_status=mapResultJudgment(d.status||'resolved');
      if(c.curTask){
        c.curTask.status=normalizeParentTaskStatus(d.status||'resolved','resolved');
        c.curTask.total_duration_ms=d.total_duration_ms;
        c.curTask.total_token_count=d.total_token_count;
        c.curTask.result_status=aiMsg.result_status;
        if(d.pause_reason)c.curTask.pause_reason=d.pause_reason;
        if(Array.isArray(d.tool_outputs))c.curTask.tool_outputs=d.tool_outputs;
        if(d.snapshot_json)c.curTask.snapshot_json=d.snapshot_json;
        upsertMainTaskHistory({
          task_id:c.curTask.task_id,
          status:c.curTask.status,
          result_status:aiMsg.result_status,
          total_duration_ms:d.total_duration_ms,
        });
      }
      syncMainTaskResultIndex(aiMsg);
    }else if(!shouldKeepCurTaskOnSimpleIntent()){
      c.curTask=null;
      aiMsg.task_id='';
      aiMsg.execTaskKind='simple';
    }else{
      aiMsg.execTaskKind='simple';
    }
    if(c.chatPrefs.autoFoldChain)aiMsg.thinkingExpanded=false;
  }else if(curEvent==='tools_discovered'){
    aiMsg.span={...aiMsg.span||{},tools_catalog:d.tools||[],tools_meta:{
      total:d.total,builtin_count:d.builtin_count,mcp_count:d.mcp_count,
      skill_count:d.skill_count,mcp_error:d.mcp_error||'',read_comments:!!d.read_comments,
      discovery_stage:d.discovery_stage||'',mcp_pending:!!d.mcp_pending,stage:d.stage||'',
    }};
    if(d.silent)return;
    const loadingMcp=d.discovery_stage==="loading_mcp";
    if(loadingMcp){
      if(!aiMsg.content||isChatLoadingPlaceholder(aiMsg.content))
        aiMsg.content="正在绑定执行工具…";
    }
  }else if(curEvent==='span_update'){
    const {status:_stepSt,...spanRest}=d;
    aiMsg.span={...aiMsg.span,...spanRest};
    if(d.search_results)aiMsg.span.search_results=d.search_results;
    if(c.curTask&&d.parent_status&&isParentTaskStatusRaw(d.parent_status)){
      c.curTask.status=normalizeParentTaskStatus(d.parent_status,c.curTask.status);
    }
    const patch={duration_ms:d.elapsed_ms,token_count:d.token_count,success:d.success,confidence:d.confidence};
    const st=aiMsg.thinking.find(x=>x.step_id===d.step_id);
    if(st)Object.assign(st,patch);
    if(c.curTask){
      const ts=(c.curTask.steps||[]).find(x=>x.step_id===d.step_id);
      if(ts)Object.assign(ts,patch);
    }
  }
}
function parseSseBuffer(buf,onEvent){
  const blocks=buf.split('\n\n');
  const rest=blocks.pop()||'';
  for(const block of blocks){
    if(!block.trim())continue;
    let ev='';
    let data='';
    for(const line of block.split('\n')){
      if(line.startsWith('event:'))ev=line.slice(6).trim();
      else if(line.startsWith('data:'))data=line.slice(5).trim();
    }
    if(!data)continue;
    try{onEvent(ev,JSON.parse(data))}catch(_){}
  }
  return rest;
}
/** 统一 SSE 接收入口：问答发送、重试等共用同一 fetch + 解析 + ingest 链路 */
async function runRepeatableChatSseStream(payload,aiMsg,{signal,url='/api/chat/stream'}={}){
  const headers={'Content-Type':'application/json'};
  const auth=authBearerHeaders&&authBearerHeaders();
  if(auth&&auth.Authorization)Object.assign(headers,auth);
  const r=await fetch(url,{
    method:'POST',headers,body:JSON.stringify(payload),signal,
  });
  if(!r.ok){
    let detail='';
    try{detail=await r.text()}catch(_){}
    if(r.status===401)throw new Error('未登录或登录已过期，请重新登录');
    if(r.status===403)throw new Error('权限不足，无法调用 AI 问答');
    throw new Error('AI 请求失败 HTTP '+r.status+(detail?': '+detail.slice(0,200):''));
  }
  if(!r.body)throw new Error('AI 运行时未返回流式正文');
  const reader=r.body.getReader();
  const decoder=new TextDecoder();
  let buf='';
  while(true){
    const{value,done}=await reader.read();
    if(done)break;
    buf+=decoder.decode(value,{stream:true});
    buf=parseSseBuffer(buf,(ev,d)=>ingestChatSseEvent(ev,d,aiMsg));
  }
  if(buf.trim())parseSseBuffer(buf+'\n\n',(ev,d)=>ingestChatSseEvent(ev,d,aiMsg));
}
async function solidifyClosedTask(taskId){
  if(!c.sid||!taskId)return;
  try{
  await fetch('/api/chat/sessions/'+encodeURIComponent(c.sid)+'/solidify-task',{
    method:'POST',
    headers:authJsonHeaders(),
    body:JSON.stringify({task_id:taskId,cur_task:c.curTask}),
  });
  }catch(_){}
}
function chatApplyTaskStatus(target){
  if(!c.curTask)return;
  const allowed=parentStatusTransitions(c.curTask.status);
  if(!allowed.includes(target)){showToastMsg("不允许的状态转换");return}
  const prevStatus=c.curTask.status;
  c.curTask.status=normalizeParentTaskStatus(target,c.curTask.status);
  upsertMainTaskHistory({task_id:c.curTask.task_id,status:c.curTask.status});
  c.taskStatusMenuOpen=false;
  _detachTaskStatusMenuCloser();
  scheduleChatPersist();
  showToastMsg("主任务状态已更新为 "+parentStatusLabel(c.curTask.status));
  if((target==='closed'||target==='resolved')&&c.curTask.task_id){
    solidifyClosedTask(c.curTask.task_id);
  }
}
function onTaskHistPick(){
  const id=c.taskHistPick;
  if(!id)return;
  const h=(c.mainTaskHistory||[]).find(x=>x.task_id===id);
  if(h)jumpToTaskResult(h);
  c.taskHistPick="";
}
let _taskHistMenuDocCloser=null;
let _taskStatusMenuDocCloser=null;
function _detachTaskHistMenuCloser(){
  if(_taskHistMenuDocCloser){
    document.removeEventListener("click",_taskHistMenuDocCloser,true);
    _taskHistMenuDocCloser=null;
  }
}
function _detachTaskStatusMenuCloser(){
  if(_taskStatusMenuDocCloser){
    document.removeEventListener("click",_taskStatusMenuDocCloser,true);
    _taskStatusMenuDocCloser=null;
  }
}
function toggleTaskHistMenu(){
  const next=!c.taskHistMenuOpen;
  c.taskHistMenuOpen=next;
  _detachTaskHistMenuCloser();
  if(next){
    c.taskStatusMenuOpen=false;
    _detachTaskStatusMenuCloser();
    refreshChatSessionTaskHistory();
    nextTick(()=>{
      _taskHistMenuDocCloser=(ev)=>{
        const wrap=document.querySelector(".chat-task-hist-wrap");
        if(wrap&&wrap.contains(ev.target))return;
        c.taskHistMenuOpen=false;
        _detachTaskHistMenuCloser();
      };
      setTimeout(()=>{
        if(_taskHistMenuDocCloser)document.addEventListener("click",_taskHistMenuDocCloser,true);
      },0);
    });
  }
}
function toggleTaskStatusMenu(){
  const next=!c.taskStatusMenuOpen;
  c.taskStatusMenuOpen=next;
  _detachTaskStatusMenuCloser();
  if(next){
    c.taskHistMenuOpen=false;
    _detachTaskHistMenuCloser();
    nextTick(()=>{
      _taskStatusMenuDocCloser=(ev)=>{
        const wrap=document.querySelector(".chat-task-status-edit");
        if(wrap&&wrap.contains(ev.target))return;
        c.taskStatusMenuOpen=false;
        _detachTaskStatusMenuCloser();
      };
      setTimeout(()=>{
        if(_taskStatusMenuDocCloser)document.addEventListener("click",_taskStatusMenuDocCloser,true);
      },0);
    });
  }
}
function chatCloseTask(){chatApplyTaskStatus("closed")}
function chatTogglePause(){
  if(c.chatHitl.active){chatHitlPause();return}
  if(c.chatStreaming){chatPauseStreaming();return}
  if(c.curTask)c.curTask.status=c.curTask.status==="paused"?"executing":"paused";
  if(c.curTask&&c.curTask.status==="executing"&&c.rewriteDraft&&!c.rewriteConfirmOpen){
    reopenRewriteConfirm();
  }
}
function clearRewriteCountdown(){
  if(c.rewriteTimer){clearInterval(c.rewriteTimer);c.rewriteTimer=null}
  c.rewriteCountdown=0;
}
function startRewriteCountdown(){
  clearRewriteCountdown();
  c.rewriteCountdown=3;
  c.rewriteTimer=setInterval(()=>{
    c.rewriteCountdown=Math.max(0,(c.rewriteCountdown||0)-1);
    if(c.rewriteCountdown<=0){
      clearRewriteCountdown();
      if(c.rewriteConfirmOpen&&c.rewriteDraft){
        const draft=String(c.rewriteDraft||'').trim();
        if(draft){
          c.inp=draft;
          c.rewriteConfirmOpen=false;
          showToastMsg('已自动恢复到输入框，可直接发送');
        }
      }
    }
  },1000);
}
function openRewriteConfirm(snapshot){
  c.rewriteSnapshot=snapshot||null;
  c.rewriteDraft=String((snapshot&&snapshot.rewritten_query)||'').trim();
  c.rewriteConfirmOpen=!!c.rewriteDraft;
  if(c.rewriteConfirmOpen)startRewriteCountdown();
}
function reopenRewriteConfirm(){
  if(!c.rewriteDraft)return;
  c.inp=String(c.rewriteDraft).trim();
  c.rewriteConfirmOpen=true;
  startRewriteCountdown();
}
function acceptRewriteDraft(){
  if(c.chatHitl.active&&c.chatHitl.kind==="rewrite_confirm"){
    c.chatHitl.editText=String(c.rewriteDraft||c.chatHitl.editText||"").trim();
    c.rewriteDraft=c.chatHitl.editText;
    chatHitlConfirm();
    return;
  }
  if(!c.rewriteDraft)return;
  c.inp=String(c.rewriteDraft||'').trim();
  c.rewriteConfirmOpen=false;
  clearRewriteCountdown();
  if(c.curTask)c.curTask.status='executing';
  scheduleChatPersist();
  showToastMsg('已填入改写结果，可继续执行');
}
function pauseRewriteDraft(){
  if(c.chatHitl.active){chatHitlPause();return}
  c.rewriteConfirmOpen=false;
  clearRewriteCountdown();
  if(c.rewriteDraft){c.inp=String(c.rewriteDraft).trim()}
  if(c.curTask)c.curTask.status='paused';
  scheduleChatPersist();
  showToastMsg('已暂停，改写内容保留在输入框');
}
async function chatSend(){
  slashOpen.value=false;
  if(c.chatHitl.active){chatHitlConfirm();return}
  if(c.chatStreaming){chatTogglePause();return}
  const msg=String(c.inp||'').trim();if(!msg)return;
  clearChatHitl();
  clearRewriteCountdown();
  c.rewriteConfirmOpen=false;
  await ensureChatSessionForSend(msg);
  await ensureChatWarmupBeforeSend();
  c.inp='';
  const aiMsg={role:'assistant',content:'正在连接服务端…',thinking:[],span:{},thinkingExpanded:true,execTaskKind:'pending',execSubPlanId:'',task_id:'',result_status:'pending',_answerStreamBuf:'',rewriteSnapshot:null};
  resetAnswerStream(aiMsg);
  c.msgs.push({role:'user',content:msg});c.msgs.push(aiMsg);c.summaryPatches=[];c.th='';
  c.chatHitlResumeMsg=aiMsg;
  const ac=new AbortController();c.chatAbort=ac;c.chatStreaming=true;
  try{
    const payload={message:msg,session_id:c.sid,model:c.model,agent_id:c.agentId,rag_prefetch:!!c.ragPrefetch,web_search:!!c.webSearch,read_comments:!!c.readComments,include_rss:!!c.includeRss,deep_think:!!c.deepThink,
      cur_task:c.curTask,
      main_task_history:c.mainTaskHistory||[],
      chat_max_tool_rounds:c.chatPrefs.maxToolRounds||15,
      chat_tool_timeout_sec:c.chatPrefs.toolTimeoutSec||60,
      chat_tool_max_retry:c.chatPrefs.maxToolRetry||3,
      chat_distinct_tool_fail_limit:c.chatPrefs.distinctToolFailLimit||3,
    };
    const ap=getAgentProfilePayload();
    if(ap)payload.agent_profile=ap;
    await runRepeatableChatSseStream(payload,aiMsg,{signal:ac.signal});
  }catch(e){if(e&&e.name==="AbortError"){aiMsg.content=(aiMsg.content||"")+"\n\n[已暂停]";if(c.curTask)c.curTask.status="paused"}else{aiMsg.content='请求未能完成，请检查网络连接或稍后重试'}}
  finally{c.chatStreaming=false;c.chatAbort=null;c.th='';syncMainTaskResultIndex(aiMsg);scheduleChatPersist()}
}
function chatKeydown(e){
  if(slashOpen.value){
    if(e.key==="ArrowDown"&&slashItems.value.length){e.preventDefault();slashIdx.value=Math.min(slashIdx.value+1,slashItems.value.length-1);return}
    if(e.key==="ArrowUp"&&slashItems.value.length){e.preventDefault();slashIdx.value=Math.max(slashIdx.value-1,0);return}
    if(e.key==="Escape"){e.preventDefault();slashOpen.value=false;return}
    if(e.key==="Enter"&&!e.shiftKey&&slashItems.value.length){e.preventDefault();pickSlash(slashItems.value[slashIdx.value]);return}
  }
  if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();chatSend()}
}
function copyMsg(m){const t=m&&m.content?String(m.content):'';navigator.clipboard.writeText(t).then(()=>showToastMsg('已复制到剪贴板')).catch(()=>alert('复制失败'))}
function copyQueryToInput(m){
  const t=m&&m.content?String(m.content):'';
  c.inp=t;
  nextTick(()=>{const ta=document.querySelector('.input-row textarea');if(ta){ta.focus();ta.setSelectionRange(t.length,t.length)}});
  showToastMsg('已填入输入框（未自动发送）');
}
async function regenerateAt(msgIndex){
  if(c.chatStreaming)return;
  const cur=c.msgs[msgIndex];
  if(!cur)return;
  let userIdx=msgIndex;
  if(cur.role==='assistant')userIdx=msgIndex-1;
  const userMsg=c.msgs[userIdx];
  if(!userMsg||userMsg.role!=='user')return;
  const text=String(userMsg.content||'').trim();
  if(!text)return;
  const cut=cur.role==='assistant'?msgIndex:msgIndex+1;
  c.msgs.splice(cut);
  c.curTask=null;
  c.inp=text;
  await chatSend();
}
function collectMsg(m){alert('已收藏')}
function readMsg(m){}

/* ══ Agent 个性化（分层 Prompt，/api/agent-personalization/*）══ */
const apz=reactive({
  cat:{builtins:[],customs:[],db:null},
  selTk:"",
  version:0,
  saving:false,
  err:"",
  previewXml:"",
  hist:[],
  layers:{
    layer0:{display_name:"",reply_style:"",user_relationship:""},
    layer1:{role:"",task_requirements:"",action_content:"",execution_framework:"COT",tools_scope:""},
    layer2:{standards_must:"",output_template:"",few_shots:"",no_doing:""}
  }
});
function makeEmptyApzLayers(){
  return{
    layer0:{display_name:"",reply_style:"",user_relationship:""},
    layer1:{role:"",task_requirements:"",action_content:"",execution_framework:"COT",tools_scope:""},
    layer2:{standards_must:"",output_template:"",few_shots:"",no_doing:""}
  };
}
function assignApzLayers(dst,src){
  const s=src&&typeof src==="object"?src:{};
  const e=makeEmptyApzLayers();
  for(const k of ["layer0","layer1","layer2"]){Object.assign(dst[k],e[k],s[k]&&typeof s[k]==="object"?s[k]:{})}
}
async function ldApzCatalog(){
  apz.err="";
  try{
    const r=await fetch("/api/agent-personalization/catalog",{headers:authBearerHeaders()});
    if(r.status===401){
      apz.cat={builtins:[],customs:[],db:null};
      dbChatAgents.value=[];
      apz.err="未登录，请先登录";
      return;
    }
    if(!r.ok){
      const d=await r.json().catch(()=>({}));
      apz.cat={builtins:[],customs:[],db:null};
      dbChatAgents.value=[];
      apz.err="目录加载失败: "+(d.detail||r.statusText||"未知错误");
      return;
    }
    const d=await r.json();
    const builtinsRaw=d.builtins||[];
    const builtinsFiltered=builtinsRaw.filter(b=>{
      const o=b&&typeof b==="object"?b:{};
      const aid=String(o.agent_id!=null?o.agent_id:"");
      const tk=String(o.template_key!=null?o.template_key:"");
      return aid==="default"||tk.endsWith("default");
    });
    apz.cat={builtins:builtinsFiltered,customs:d.customs||[],db:d.db||null};
    dbChatAgents.value=(d.customs||[]).map(u=>({id:u.agent_id||(String(u.template_key||"").replace(/^custom:/,"")||""),label:u.label||u.agent_id||""}));
  }catch(e){
    apz.cat={builtins:[],customs:[],db:null};
    dbChatAgents.value=[];
    apz.err="目录加载失败: "+(e.message||String(e));
  }
}
async function selectApzTemplate(tk){
  if(!tk)return;
  apz.selTk=tk;
  await ldApzCurrent();
  await ldApzHist();
}
async function ldApzCurrent(){
  apz.err="";
  if(!apz.selTk){assignApzLayers(apz.layers,{});apz.version=0;apz.previewXml="";return}
  try{
    const r=await fetch("/api/agent-personalization/current?template_key="+encodeURIComponent(apz.selTk),{headers:authBearerHeaders()});
    const d=await r.json();
    if(!r.ok){const msg=d&&d.detail?(typeof d.detail==="string"?d.detail:JSON.stringify(d.detail)):(r.statusText||"加载失败");throw new Error(msg)}
    apz.version=d.version||0;
    assignApzLayers(apz.layers,d.layers);
    apz.previewXml=d.rendered_system||"";
  }catch(e){apz.err=e.message||String(e)}
}
async function ldApzHist(){
  if(!apz.selTk){apz.hist=[];return}
  try{
    const r=await fetch("/api/agent-personalization/history?template_key="+encodeURIComponent(apz.selTk)+"&limit=40",{headers:authBearerHeaders()});
    const d=await r.json();
    apz.hist=d.revisions||[];
  }catch(_){apz.hist=[]}
}
async function loadApzRevision(h){
  if(!h)return;
  try{
    let lj=h.layers_json;
    if(typeof lj==="string")lj=JSON.parse(lj);
    assignApzLayers(apz.layers,lj);
    apz.version=h.version||0;
    apz.previewXml=h.rendered_system||"";
    apz.err="";
  }catch(e){apz.err=e.message||String(e)}
}
async function saveApzTemplate(){
  if(!apz.selTk){alert("请先选择左侧模板");return}
  apz.saving=true;
  apz.err="";
  try{
    const r=await fetch("/api/agent-personalization/save",{method:"POST",headers:authJsonHeaders(),body:JSON.stringify({template_key:apz.selTk,layers:apz.layers})});
    const d=await r.json();
    if(!r.ok)throw new Error(typeof d.detail==="string"?d.detail:JSON.stringify(d.detail||d)||"保存失败");
    if(d.version!=null)apz.version=d.version;
    await ldApzCurrent();
    await ldApzHist();
    await ldApzCatalog();
    showToastMsg("已保存为新版本");
  }catch(e){apz.err=e.message||String(e)}
  finally{apz.saving=false}
}
async function newApzCustom(){
  if(!confirm("用当前表单内容新建自定义模板（分配新 agent_id）？"))return;
  apz.saving=true;
  apz.err="";
  try{
    const r=await fetch("/api/agent-personalization/custom",{method:"POST",headers:authJsonHeaders(),body:JSON.stringify({layers:apz.layers})});
    const d=await r.json();
    if(!r.ok)throw new Error(typeof d.detail==="string"?d.detail:JSON.stringify(d.detail||d)||"创建失败");
    if(!d.template_key)throw new Error(JSON.stringify(d));
    apz.selTk=d.template_key;
    await ldApzCatalog();
    await ldApzCurrent();
    await ldApzHist();
    showToastMsg("已创建自定义模板");
  }catch(e){apz.err=e.message||String(e)}
  finally{apz.saving=false}
}
function useApzInChat(){
  const tk=String(apz.selTk||"");
  if(!tk){showToastMsg("请先选择模板");return}
  let aid="default";
  if(tk.startsWith("builtin:"))aid=tk.slice(8);
  else if(tk.startsWith("custom:"))aid=tk.slice(7);
  c.agentId=aid;
  persistChatPrefs();
  page.value="chat";
  showToastMsg("已切换问答 Agent 为: "+aid);
}
async function deactivateApzCustom(){
  const tk=String(apz.selTk||"");
  if(!tk.startsWith("custom:"))return;
  const aid=tk.slice(7);
  if(!aid.startsWith("c_")){alert("仅支持自定义模板停用");return}
  if(!confirm("停用该自定义模板？"))return;
  try{
    const r=await fetch("/api/agent-personalization/deactivate",{method:"POST",headers:authJsonHeaders(),body:JSON.stringify({agent_id:aid})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(typeof d.detail==="string"?d.detail:JSON.stringify(d.detail||d)||"失败");
    if(c.agentId===aid)c.agentId="default";
    persistChatPrefs();
    await ldApzCatalog();
    apz.selTk="";
    assignApzLayers(apz.layers,{});
    apz.version=0;
    apz.hist=[];
    apz.previewXml="";
    showToastMsg("已停用");
  }catch(e){alert(e.message||String(e))}
}
function goAgentPersonalization(){page.value="agpz";}
async function syncApzTemplateToCurrentAgent(){
  const aid=String(c.agentId||"default").trim().toLowerCase();
  let tk;
  if(aid.startsWith("c_"))tk="custom:"+aid;
  else if(["default","doc","ops"].includes(aid))tk="builtin:"+aid;
  else tk="builtin:default";
  const rows=[...(apz.cat.builtins||[]),...(apz.cat.customs||[])];
  const hit=rows.find(x=>x.template_key===tk);
  if(hit)await selectApzTemplate(tk);
  else if((apz.cat.builtins||[])[0])await selectApzTemplate(apz.cat.builtins[0].template_key);
}

/* ══ P4 RAG ══ */
const kb=reactive({
  st:{listChunkSum:null,chunkMismatch:false},fs:[],si:-1,pv:"",libs:[],activeLib:"",cfgSlice:"auto",
  cfgMeta:'{\n  "domain": "",\n  "module": "",\n  "doc_type": "",\n  "keyword1": "",\n  "keyword2": ""\n}',
  cfgRecallFilter:'{\n  "domain": "",\n  "module": "",\n  "doc_type": "",\n  "keyword1": "",\n  "keyword2": ""\n}',
  recallVocab:{domain:[],module:[],doc_type:[],keyword1:[],keyword2:[]},
  metaOpts:{domains:[],modules:[],doc_types:[]},
  detailShow:false,detailLoading:false,detailFile:null,detailChunks:[],detailChunksHint:"",
  editMeta:{domain:"",module:"",doc_type:"",keyword1:"",keyword2:""},
  syncBusy:false,
  conn:{status:"disconnected",host:"127.0.0.1",port:"19530",error:"",version:"",latency_ms:0,last_checked_at:0,retry_count:0,params:{}},
  connBusy:false,connDetailOpen:false,
  importHistory:[],
});
const kbMilvusStatusText=computed(()=>{
  const s=String(kb.conn.status||'disconnected');
  if(s==='connecting')return '正在连接';
  if(s==='connected')return kb.st.milvusDegraded ? '已连接 · 集合未就绪' : '已连接 · 可查询';
  if(s==='failed')return '连接失败';
  if(s==='disconnected')return '未连接';
  if(s==='degraded')return '已连接 · 降级';
  return '未连接';
});
const kbMilvusStatusColor=computed(()=>{
  const s=String(kb.conn.status||'disconnected');
  if(s==='connecting')return '#b45309';
  if(s==='connected')return kb.st.milvusDegraded ? '#b45309' : 'var(--ok)';
  if(s==='failed')return 'var(--err)';
  if(s==='degraded')return '#b45309';
  return 'var(--err)';
});
const kbImportMeta=reactive({show:false,paths:[],mode:"auto",busy:false,progress:{current:0,total:0,name:""}});
const kbImportBtnLabel=computed(()=>{
  if(!kbImportMeta.busy)return "确认导入";
  const t=kbImportMeta.progress.total||kbImportMeta.paths.length||0;
  const c=kbImportMeta.progress.current||0;
  const n=kbImportMeta.progress.name||"";
  return n?("正在导入 "+c+"/"+t+"："+n):("正在导入 "+c+"/"+t+" 个文件");
});
function kbLoadImportHistory(){
  try{
    const raw=localStorage.getItem("sba_kb_import_log");
    kb.importHistory=raw?JSON.parse(raw)||[]:[];
  }catch(_){kb.importHistory=[]}
}
function kbPushImportHistory(entry){
  const row={...entry,at:entry.at||new Date().toLocaleString("zh-CN",{hour12:false})};
  kb.importHistory.unshift(row);
  if(kb.importHistory.length>40)kb.importHistory.length=40;
  try{localStorage.setItem("sba_kb_import_log",JSON.stringify(kb.importHistory))}catch(_){}
}
function kbImportBaseName(p){
  const s=String(p||"");
  const i=Math.max(s.lastIndexOf("/"),s.lastIndexOf("\\"));
  return i>=0?s.slice(i+1):s;
}
const kbBrowse=reactive({show:false,stack:[],entries:[],picks:[],current:""});
const kbFolderInp=ref(null);
const KB_LOCAL_IMPORT_SUFFIXES=new Set([".md",".txt",".markdown",".mdx"]);
function kbIsImportableLocalFile(fileOrName){
  const name=typeof fileOrName==="string"?fileOrName:(fileOrName&&fileOrName.name)||"";
  const n=String(name).toLowerCase().trim();
  if(!n)return false;
  const i=n.lastIndexOf(".");
  if(i<0)return false;
  return KB_LOCAL_IMPORT_SUFFIXES.has(n.slice(i));
}
async function kbUploadLocalFile(file){
  const fd=new FormData();
  fd.append("file",file);
  const rel=(file.webkitRelativePath||file.name||"").replace(/\\/g,"/");
  if(rel)fd.append("relative_path",rel);
  const r=await fetch("/api/doc/rag/upload",{method:"POST",body:fd});
  const j=await r.json().catch(()=>({}));
  if(!r.ok){
    const det=j.detail;
    const msg=typeof det==="string"?det:(Array.isArray(det)&&det[0]&&det[0].msg)||(det&&String(det))||r.statusText||"上传失败";
    throw new Error(msg);
  }
  return j;
}
function kbPickLocalFolder(){
  const el=kbFolderInp.value;
  if(el)el.click();
}
async function onKbLocalFolderPick(e){
  const all=Array.from(e.target.files||[]);
  e.target.value="";
  if(!all.length){
    showToastMsg("未读到任何文件。请重新选择文件夹（选含 .md 的那一层目录，不要只选空子文件夹）");
    return;
  }
  const files=all.filter(f=>kbIsImportableLocalFile(f));
  if(!files.length){
    const sample=all.slice(0,3).map(f=>f.webkitRelativePath||f.name).join("；");
    showToastMsg("已扫描 "+all.length+" 个文件，未发现 .md/.txt/.markdown（含子目录）。示例："+sample);
    return;
  }
  kb.syncBusy=true;
  const paths=[];
  let fail=0;
  for(const f of files){
    try{
      const j=await kbUploadLocalFile(f);
      if(j.path)paths.push(j.path);
    }catch(err){
      fail++;
      _logKbLocalFail(f,err);
    }
  }
  kb.syncBusy=false;
  if(!paths.length){
    showToastMsg("上传失败，请检查文件大小与格式");
    return;
  }
  showToastMsg("已识别并上传 "+paths.length+" 个文件"+(fail?("，"+fail+" 个失败"):"")+"，请选择入库方式");
  kbOpenImportMeta(paths);
}
function _logKbLocalFail(file,err){
  console.warn("[kb local upload]",file&&file.name,err&&err.message||err);
}
async function kbLoadBrowse(path){
  const q=path?("?path="+encodeURIComponent(path)):"";
  try{
    const r=await fetch("/api/fs/browse"+q);
    const d=await r.json();
    if(!d.ok){showToastMsg(d.error||"无法浏览");kbBrowse.entries=[];return}
    kbBrowse.entries=d.entries||[];
  }catch(e){showToastMsg("浏览失败");kbBrowse.entries=[]}
}
function openKbBrowse(){openPageOverlay("kbBrowse",()=>{kbBrowse.show=true;kbBrowse.stack=[];kbBrowse.picks=[];kbBrowse.current="";kbLoadBrowse("");});}
function kbBrowseEnter(p){kbBrowse.stack.push(kbBrowse.current);kbBrowse.current=p;kbLoadBrowse(p)}
function kbBrowseUp(){const prev=kbBrowse.stack.pop();if(prev===undefined){kbBrowse.show=false;return}kbBrowse.current=prev;kbLoadBrowse(prev||"")}
function kbMetaFromLibraryTemplate(){
  try{
    const t=JSON.parse(kb.cfgMeta||"{}");
    return {domain:t.domain||"",module:t.module||"",doc_type:t.doc_type||"",keyword1:t.keyword1||"",keyword2:t.keyword2||""};
  }catch(_){return {domain:"",module:"",doc_type:"",keyword1:"",keyword2:""};}
}
function kbOpenImportMeta(paths){
  const list=[...paths].filter(Boolean);
  if(!list.length)return;
  openPageOverlay("kbMeta",()=>{
    kbImportMeta.paths=list;
    kbImportMeta.mode="auto";
    kbImportMeta.show=true;
    kbImportMeta.busy=false;
  });
}
async function kbImportSelectedFiles(){
  const paths=[...kbBrowse.picks];
  if(!paths.length)return;
  kbBrowse.show=false;
  kbOpenImportMeta(paths);
}
async function kbConfirmImportWithMeta(){
  const paths=[...kbImportMeta.paths];
  if(!paths.length){kbImportMeta.show=false;return;}
  kbImportMeta.busy=true;
  kbImportMeta.progress={current:0,total:paths.length,name:""};
  let meta=null;
  if(kbImportMeta.mode==="template"){
    meta=kbMetaFromLibraryTemplate();
    if(!meta.domain||!meta.module||!meta.doc_type){showToastMsg("库 metadata 模板缺少 domain/module/doc_type");kbImportMeta.busy=false;return;}
  }else if(kbImportMeta.mode==="manual"){
    meta={...kb.editMeta};
    if(!meta.domain||!meta.module||!meta.doc_type){showToastMsg("请填写必填元数据字段");kbImportMeta.busy=false;return;}
  }
  let ok=0;
  let fail=0;
  for(let i=0;i<paths.length;i++){
    const p=paths[i];
    kbImportMeta.progress.current=i+1;
    kbImportMeta.progress.name=kbImportBaseName(p);
    try{
      let m=meta;
      if(kbImportMeta.mode==="auto"){
        const ar=await fetch("/api/doc/rag/metadata/auto",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:p,mode:"rule"})});
        const ad=await ar.json();
        if(ad.ok)m=ad.metadata;
      }
      const r=await fetch("/api/doc/rag/add-file",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:p,slice_method:kb.cfgSlice,metadata:m})});
      const d=await r.json();
      if(r.ok&&d.ok!==false)ok++;
      else fail++;
    }catch(_){fail++}
  }
  kbPushImportHistory({
    total:paths.length,
    ok,
    fail,
    mode:kbImportMeta.mode,
    lib:(kb.libs.find(x=>x.id===kb.activeLib)||{}).name||kb.activeLib,
  });
  kbImportMeta.busy=false;
  kbImportMeta.show=false;
  kbImportMeta.paths=[];
  kbImportMeta.progress={current:0,total:0,name:""};
  kbBrowse.picks=[];
  showToastMsg("已导入 "+ok+"/"+paths.length+" 个文件"+(fail?("，失败 "+fail+" 个"):""));
  ldKbS();ldKbF();
}
async function kbImportFolderHere(){
  const p=kbBrowse.current;
  if(!p)return;
  try{
    const r=await fetch("/api/doc/rag/add-folder",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:p,extensions:".md,.txt,.markdown,.pdf"})});
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||d.message||"失败");
    showToastMsg("文件夹导入：成功 "+(d.success||0)+"/"+(d.total||0));
    ldKbS();ldKbF();
  }catch(e){showToastMsg(e.message||String(e))}
}
async function ldKbLibs(){
  try{
    const r=await fetch("/api/rag/libraries");
    const d=await r.json();
    kb.libs=d.libraries||[];
    kb.activeLib=d.active_id||((kb.libs[0]||{}).id)||"";
    syncKbCfgFromLib();
  }catch(e){kb.libs=[]}
}
async function ldKbConn(){
  try{
    const r=await fetch('/api/vector/connection');
    const d=await r.json();
    kb.conn={...kb.conn,...d};
  }catch(_){kb.conn.status='disconnected'}
}
async function kbSetConnectionParams(){
  kb.connBusy=true;
  try{
    const r=await fetch('/api/vector/connection/params',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({host:kb.conn.host,port:kb.conn.port})});
    const d=await r.json().catch(()=>({}));
    kb.conn={...kb.conn,...(d||{})};
    showToastMsg('连接参数已保存');
  }catch(e){showToastMsg(e.message||String(e))}
  finally{kb.connBusy=false}
}
async function kbProbeConnection(){
  kb.connBusy=true;
  kb.conn.status='connecting';
  try{
    const r=await fetch('/api/vector/connection/probe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({host:kb.conn.host,port:kb.conn.port})});
    const d=await r.json();
    kb.conn={...kb.conn,...d};
    if(d.probe&&d.probe.milvus_ok){showToastMsg('向量库已连接');ldKbS();}
    else showToastMsg('连接失败：'+(d.error||d.probe?.error||'未知错误'));
  }catch(e){kb.conn.status='failed';kb.conn.error=String(e.message||e);showToastMsg('连接失败：'+kb.conn.error)}
  finally{kb.connBusy=false}
}
async function kbRetryConnection(){
  kb.connBusy=true;
  kb.conn.status='connecting';
  try{
    const r=await fetch('/api/vector/connection/retry',{method:'POST'});
    const d=await r.json();
    kb.conn={...kb.conn,...d};
    if(d.connected){showToastMsg('重试成功');ldKbS();}
    else showToastMsg('重试失败：'+(d.error||'未知错误'));
  }catch(e){kb.conn.status='failed';kb.conn.error=String(e.message||e);showToastMsg('重试失败：'+kb.conn.error)}
  finally{kb.connBusy=false}
}
function kbConnFmtTs(ts){
  const n=Number(ts);
  if(!n||n<=0)return '—';
  try{return new Date(n*1000).toLocaleString('zh-CN',{hour12:false});}catch(_){return '—'}
}
function kbResetConnection(){
  kb.conn.host='127.0.0.1';
  kb.conn.port='19530';
  showToastMsg('已重置为默认 host/port，请点「保存」或「探测」生效');
}
function kbToggleConnDetail(){
  kb.connDetailOpen=!kb.connDetailOpen;
}
function syncKbCfgFromLib(){
  const L=(kb.libs||[]).find(x=>x.id===kb.activeLib);
  if(L){
    kb.cfgSlice=L.slice_method||"auto";
    kb.cfgMeta=L.metadata_json||"{}";
    kb.cfgRecallFilter=L.recall_filter_json||'{"domain":"","module":"","doc_type":"","keyword1":"","keyword2":""}';
  }
}
async function kbLoadRecallVocab(){
  try{
    const r=await fetch("/api/doc/rag/metadata/vocabulary");
    const d=await parseApiJson(r);
    if(d.ok&&d.vocabulary)kb.recallVocab=d.vocabulary;
  }catch(_){}
}
const KB_INTERVIEW_IMPORT_PATH="F:\\java\\AIOPS\\SuperBizAgent-release-2026-01-02\\demo_wendanghua\\output\\AI\\面试必背";
async function kbImportInterviewFolder(){
  if(kb.syncBusy)return;
  if(!confirm("将从服务端目录导入「面试必背」全部 .md/.txt（约百余个），并按大/中/小粒度标注元数据。继续？"))return;
  kb.syncBusy=true;
  try{
    const r=await fetch("/api/doc/rag/import-local-path",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:KB_INTERVIEW_IMPORT_PATH,slice_method:kb.cfgSlice,granularity:true})});
    const d=await parseApiJson(r);
    if(!d.ok)throw new Error(d.error||"导入失败");
    kbPushImportHistory({total:d.total||0,ok:d.success||0,fail:d.failed||0,mode:"服务端·面试必背·粒度标注"});
    showToastMsg("导入完成：成功 "+(d.success||0)+"/"+(d.total||0));
    await ldKbS();await ldKbF();await kbLoadRecallVocab();
  }catch(e){showToastMsg(e.message||String(e))}
  finally{kb.syncBusy=false}
}
async function onKbLibChange(){
  try{
    await fetch("/api/rag/libraries/active",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id:kb.activeLib})});
    syncKbCfgFromLib();
  }catch(_){}
}
function promptCreateKbLib(){const n=prompt("新库名称");if(!n||!n.trim())return;fetch("/api/rag/libraries",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:n.trim()})}).then(r=>r.json()).then(async d=>{if(!d.ok)throw new Error(d.detail||"");await ldKbLibs();kb.activeLib=d.library.id;await onKbLibChange();showToastMsg("已创建")}).catch(e=>alert(e.message||String(e)))}
async function deleteKbLib(){if(kb.activeLib==="lib_default")return;if(!confirm("删除当前库？"))return;await fetch("/api/rag/libraries/"+encodeURIComponent(kb.activeLib),{method:"DELETE"});await ldKbLibs();syncKbCfgFromLib()}
async function saveKbLibCfg(){
  try{
    JSON.parse(kb.cfgMeta||"{}");
    JSON.parse(kb.cfgRecallFilter||"{}");
  }catch(e){alert("metadata / 召回筛选 须为合法 JSON");return}
  try{
    const r=await fetch("/api/rag/libraries/"+encodeURIComponent(kb.activeLib)+"/config",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({slice_method:kb.cfgSlice,metadata_json:kb.cfgMeta,recall_filter_json:kb.cfgRecallFilter})});
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||"保存失败");
    await ldKbLibs();
    showToastMsg("库配置已保存");
  }catch(e){alert(e.message||String(e))}
}
async function ldKbMetaOpts(){try{const r=await fetch("/api/doc/rag/metadata/options");const d=await parseApiJson(r);if(d.ok){kb.metaOpts.domains=d.domains||[];kb.metaOpts.modules=d.modules||[];kb.metaOpts.doc_types=d.doc_types||[];}}catch(_){}}
async function ldKbS(){
  try{
    const r=await fetch('/api/doc/rag/stats',{headers:authBearerHeaders()});
    const d=await parseApiJson(r);
    if(!d.ok){showToastMsg('知识库统计加载失败');return;}
    const data=d.data||{};
    kb.st.tc=data.total_chunks||0;
    kb.st.tf=data.total_files||0;
    kb.st.ed=data.embedding_dim||0;
    kb.st.lu=data.last_update||data.last_updated||'';
    kb.st.backend=data.storage_backend||'';
    kb.st.milvus=!!data.milvus_ok;
    kb.st.chunkSrc=data.chunk_count_source||'';
    kb.st.milvusDegraded=!!data.milvus_degraded;
    kb.st.recordsPath=data.file_records_path||'';
    if((kb.st.tf||0)===0&&data.records_merged_count>0){
      kb.st.tf=data.records_merged_count;
      kb.st.tc=kb.st.tc||0;
    }
    if((kb.st.tf||0)===0&&data.file_records_path){
      console.warn('[kb] 统计为 0，登记路径',data.file_records_path);
    }
  }catch(e){
    console.error('[kb] ldKbS',e);
    showToastMsg('知识库统计：'+(e.message||String(e)));
  }
}
async function ldKbF(){
  try{
    const r=await fetch('/api/doc/rag/files?page=1&size=500',{headers:authBearerHeaders()});
    const d=await parseApiJson(r);
    if(!d.ok){showToastMsg('文件列表加载失败');return;}
    kb.fs=d.files||[];
    if(!kb.fs.length&&d.total>0){
      showToastMsg('列表为空但服务端共 '+d.total+' 条，请刷新或检查权限');
    }
    const sum=typeof d.list_chunk_sum==='number'?d.list_chunk_sum:kb.fs.reduce((a,f)=>a+(parseInt(f.chunk_count,10)||0),0);
    kb.st.listChunkSum=sum;
    kb.st.chunkMismatch=Math.abs(sum-(kb.st.tc||0))>0;
  }catch(e){
    console.error('[kb] ldKbF',e);
    showToastMsg('知识库列表：'+(e.message||String(e)));
  }
}
async function kbSyncChunkCounts(){
  kb.syncBusy=true;
  try{
    const r=await fetch('/api/doc/rag/sync-chunk-counts',{method:'POST'});
    const d=await parseApiJson(r);
    if(!d.ok)throw new Error(d.error||'同步失败');
    showToastMsg('已回写 '+ (d.updated||0) +' 条记录（Milvus 合计 '+ (d.milvus_total_chunks||'?') +' 切片）');
    await ldKbS();await ldKbF();
  }catch(e){showToastMsg(e.message||String(e));}
  finally{kb.syncBusy=false;}
}
async function kbRestoreCatalog(reindex){
  kb.syncBusy=true;
  try{
    const r=await fetch('/api/doc/rag/rebuild-catalog',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reindex_milvus:!!reindex})});
    const d=await parseApiJson(r);
    if(!d.ok)throw new Error(d.error||'恢复失败');
    let msg='已恢复 '+ (d.records||0) +' 条目录登记';
    if(d.missing_sources)msg+='（'+d.missing_sources+' 个源文件路径已不存在）';
    if(reindex)msg+='；已重入库 '+ (d.reindexed||0) +' 个文件到 Milvus';
    showToastMsg(msg);
    await ldKbS();await ldKbF();
  }catch(e){showToastMsg(e.message||String(e));}
  finally{kb.syncBusy=false;}
}
async function openKbFileDetail(f){
  if(!f||!f.path)return;
  kb.si=kb.fs.findIndex(x=>x.path===f.path);
  kb.detailShow=true;
  kb.detailLoading=true;
  kb.detailFile=null;
  kb.detailChunks=[];
  kb.detailChunksHint="";
  try{
    const r=await fetch("/api/doc/rag/file/detail?path="+encodeURIComponent(f.path));
    const d=await parseApiJson(r);
    if(!d.ok)throw new Error(d.error||"加载失败");
    kb.detailFile=d.file||f;
    const m=d.file||{};
    kb.editMeta={domain:m.domain||"",module:m.module||"",doc_type:m.doc_type||"",keyword1:m.keyword1||"",keyword2:m.keyword2||""};
    kb.pv=JSON.stringify({file:d.file,record:d.record},null,2);
    const cr=await fetch("/api/doc/rag/file/chunks?path="+encodeURIComponent(f.path)+"&limit=20");
    const cd=await parseApiJson(cr);
    if(cd.ok){kb.detailChunks=cd.chunks||[];kb.detailChunksHint=cd.hint||"";}
  }catch(e){showToastMsg(e.message||String(e));kb.detailShow=false;}
  finally{kb.detailLoading=false;}
}
function closeKbFileDetail(){kb.detailShow=false;}
async function kbAutoFillMeta(mode){
  const p=kb.detailFile?.path;
  if(!p)return;
  kb.detailLoading=true;
  try{
    const r=await fetch("/api/doc/rag/metadata/auto",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:p,mode:mode||"rule"})});
    const d=await r.json();
    if(!d.ok)throw new Error(d.error||"识别失败");
    kb.editMeta={...d.metadata};
    showToastMsg("已填充（"+(d.source||mode)+")");
  }catch(e){showToastMsg(e.message||String(e));}
  finally{kb.detailLoading=false;}
}
async function kbSaveFileMeta(){
  const p=kb.detailFile?.path;
  if(!p)return;
  try{
    const r=await fetch("/api/doc/rag/file/metadata",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:p,metadata:kb.editMeta})});
    const d=await r.json();
    if(!d.ok)throw new Error(d.error||"保存失败");
    showToastMsg("元数据已保存");
    Object.assign(kb.detailFile,d.file||{});
    ldKbF();
    ldKbS();
  }catch(e){showToastMsg(e.message||String(e));}
}
function kbRowPv(f){if(f)openKbFileDetail(f);else kb.pv=""}
async function kbRm(){if(kb.si<0)return;const f=kb.fs[kb.si];if(!f)return;await fetch('/api/doc/rag/delete',{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:f.path})});ldKbS();ldKbF()}
const mm=reactive({
  queue:[],qi:-1,busy:false,
  mode:"doc",
  page:1,
  columnBands:0,
  columnBandSplit:true,
  skipArrows:true,
  fcResult:null,
});
const mmBrowse=reactive({show:false,stack:[],entries:[],picks:[],current:""});
const mmFileInp=ref(null);
const d=reactive({log:""});
async function mmLoadBrowse(path){
  const q=path?("?path="+encodeURIComponent(path)):"";
  try{
    const r=await fetch("/api/fs/browse"+q);
    const d=await r.json();
    if(!d.ok){showToastMsg(d.error||"无法浏览");mmBrowse.entries=[];return}
    mmBrowse.entries=d.entries||[];
  }catch(e){showToastMsg("浏览失败");mmBrowse.entries=[]}
}
function openMmBrowse(){openPageOverlay("mmBrowse",()=>{mmBrowse.show=true;mmBrowse.stack=[];mmBrowse.picks=[];mmBrowse.current="";mmLoadBrowse("");});}
function mmBrowseEnter(p){mmBrowse.stack.push(mmBrowse.current);mmBrowse.current=p;mmLoadBrowse(p)}
function mmBrowseUp(){const prev=mmBrowse.stack.pop();if(prev===undefined){mmBrowse.show=false;return}mmBrowse.current=prev;mmLoadBrowse(prev||"")}
function mmAddBrowsePicks(){
  const seen=new Set(mm.queue.map(x=>x.path));
  for(const pth of mmBrowse.picks){
    if(seen.has(pth))continue;
    const name=pth.split(/[/\\]/).pop()||pth;
    mm.queue.push({path:pth,name,src:"srv"});
    seen.add(pth);
  }
  mmBrowse.picks=[];
  showToastMsg("已加入队列");
}
function mmClearQueue(){mm.queue=[];mm.qi=-1}
function mmRmQueueSel(){if(mm.qi<0||mm.qi>=mm.queue.length)return;mm.queue.splice(mm.qi,1);mm.qi=Math.min(mm.qi,mm.queue.length-1)}
function mmClearDocLog(){d.log=""}
function docLogLine(s){d.log=(d.log?d.log+"\n":"")+s}
async function mmUploadOne(file){
  const fd=new FormData();
  fd.append("file",file);
  const r=await fetch("/api/doc/upload",{method:"POST",body:fd});
  const j=await r.json().catch(()=>({}));
  if(!r.ok){
    const det=j.detail;
    const msg=typeof det==="string"?det:(Array.isArray(det)&&det[0]&&det[0].msg)||(det&&String(det))||r.statusText||"上传失败";
    throw new Error(msg);
  }
  return j;
}
function mmPickLocal(){const el=mmFileInp.value;if(el)el.click()}
async function mmOnLocalPick(e){
  const files=Array.from(e.target.files||[]);
  e.target.value="";
  await mmIngestFiles(files,"up");
}
async function mmOnDrop(e){
  const files=Array.from(e.dataTransfer?.files||[]);
  await mmIngestFiles(files,"up");
}
async function mmIngestFiles(files,srcLabel){
  if(!files.length)return;
  let ok=0;
  for(const f of files){
    try{
      const j=await mmUploadOne(f);
      const path=j.path;
      if(!path)continue;
      if(mm.queue.some(x=>x.path===path))continue;
      mm.queue.push({path:path,name:j.name||f.name,src:srcLabel});
      ok++;
    }catch(err){showToastMsg((f.name||"文件")+": "+(err.message||String(err)))}
  }
  if(ok)showToastMsg("已上传并入队 "+ok+" 个文件");
}
function mmFcApplyResult(res){
  if(!res||!res.ok){mm.fcResult=null;return}
  const geom=res.geometry_score||{};
  mm.fcResult={
    final_block_count:res.final_block_count,
    overlap_ok:!!res.overlap_ok,
    overlap_pair_count:geom.overlap_pair_count!=null?geom.overlap_pair_count:"?",
    column_band_cuts:res.column_band_cuts||[],
    overlay_url:res.overlay_url||"",
    work_dir:res.work_dir||"",
  };
}
async function docProcFlowchart(){
  if(mm.busy)return;
  if(!mm.queue.length){showToastMsg("请先将 PDF/图片加入队列");return}
  mm.busy=true;
  mm.fcResult=null;
  const stamp=()=>new Date().toLocaleTimeString();
  try{
    docLogLine("["+stamp()+"] 流程图得分 "+mm.queue.length+" 个文件 …");
    docLogLine("  参数: page="+mm.page+" column_bands="+mm.columnBands+" column_band_split="+mm.columnBandSplit);
    const list=[...mm.queue];
    for(let i=0;i<list.length;i++){
      const it=list[i];
      docLogLine("  ["+(i+1)+"/"+list.length+"] "+it.name);
      try{
        const body={
          path:it.path,
          page:mm.page,
          column_bands:mm.columnBands,
          column_band_split:mm.columnBandSplit,
          skip_arrows:mm.skipArrows,
        };
        const r=await fetch("/api/doc/flowchart/score",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
        const res=await r.json().catch(()=>({}));
        if(!r.ok){
          const det=res.detail;
          const msg=typeof det==="string"?det:(Array.isArray(det)&&det[0]&&det[0].msg)||(det&&String(det))||r.statusText||"请求失败";
          docLogLine("      → HTTP "+r.status+" "+msg);
          continue;
        }
        if(!res.ok){
          docLogLine("      → FAIL "+((res.error||"").slice(0,400)));
          continue;
        }
        const geom=res.geometry_score||{};
        const cuts=(res.column_band_cuts||[]).join(",");
        docLogLine("      → OK 块数:"+res.final_block_count+" 重叠对:"+geom.overlap_pair_count+(cuts?" 切线y:"+cuts:""));
        if(i===list.length-1)mmFcApplyResult(res);
      }catch(e){docLogLine("      → 请求异常: "+(e.message||String(e)))}
    }
    docLogLine("["+stamp()+"] 流程图得分完成");
    showToastMsg("流程图得分已跑完");
  }finally{mm.busy=false}
}
async function docProc(){
  if(mm.busy)return;
  if(!mm.queue.length){showToastMsg("请先将文件加入队列");return}
  if(mm.mode==="flowchart"){await docProcFlowchart();return}
  mm.busy=true;
  const stamp=()=>new Date().toLocaleTimeString();
  try{
    docLogLine("["+stamp()+"] 开始处理 "+mm.queue.length+" 个文件 …");
    const list=[...mm.queue];
    for(let i=0;i<list.length;i++){
      const it=list[i];
      docLogLine("  ["+(i+1)+"/"+list.length+"] "+it.name);
      try{
        const r=await fetch("/api/doc/process",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:it.path})});
        const res=await r.json().catch(()=>({}));
        if(!r.ok){
          const det=res.detail;
          const msg=typeof det==="string"?det:(Array.isArray(det)&&det[0]&&det[0].msg)||(det&&String(det))||r.statusText||"请求失败";
          docLogLine("      → HTTP "+r.status+" "+msg);
          continue;
        }
        const ok=!!res.ok;
        const err=(res.error||"").trim();
        const dt=res.doc_type||"?";
        const ntxt=(res.text&&String(res.text).length)||0;
        docLogLine("      → "+(ok?"OK":"FAIL")+" 类型:"+dt+" 文本长度:"+ntxt+(err?" 错误:"+err.slice(0,400):""));
      }catch(e){docLogLine("      → 请求异常: "+(e.message||String(e)))}
    }
    docLogLine("["+stamp()+"] 全部完成");
    showToastMsg("队列已跑完");
  }finally{mm.busy=false}
}

/* ══ P5 缓存 ══ */
const ca=reactive({rs:[],gs:[],dt:'',ek:'',eid:'',st:'',qf:{a:'',s:'',kw:'',g:'全部分类'}});
async function caQ(){try{const p=new URLSearchParams();if(ca.qf.a)p.set('artifact',ca.qf.a);if(ca.qf.s)p.set('source',ca.qf.s);if(ca.qf.kw)p.set('keyword',ca.qf.kw);if(ca.qf.g&&ca.qf.g!=='全部分类')p.set('group',ca.qf.g);const r=await fetch('/api/cache/query?'+p.toString());const d=await r.json();ca.rs=d.rows||[];ca.gs=d.groups||[];ca.st=`共 ${d.total||0} 条`}catch(e){}}
function caPickRow(r){if(!r)return;ca.eid=String(r.id);ca.dt=JSON.stringify(r.data!==undefined?r.data:r,null,2);ca.ek=r.task_key||''}
function caSel(){if(ca.rs.length>0)caPickRow(ca.rs[0])}
async function caSv(){
  if(!ca.eid){showToastMsg("请先点击表格一行或点「选中」");return}
  let data;const raw=(ca.dt||"").trim();if(!raw){showToastMsg("详情为空");return};try{data=JSON.parse(raw)}catch(e){alert("详情须为合法 JSON");return}
  try{
    const r=await fetch("/api/cache/entry/"+encodeURIComponent(ca.eid),{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({data})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(typeof d.detail==="string"?d.detail:JSON.stringify(d.detail||d)||"保存失败");
    showToastMsg("已保存");
    await caQ();
  }catch(e){showToastMsg(e.message||String(e))}
}
async function caEx(){if(!ca.ek)return alert('需要 task_key');try{const r=await fetch('/api/cache/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task_key:ca.ek})});const d=await r.json();alert('导出 '+d.count+' 条')}catch(e){}}

/* ══ P6 Agent 配置 ══ */
const st=reactive({sec:"agent",lk:"run",sub:"gw",rt:"rte",pa:"doc_standardize_agent",ma:"doc_standardize_agent",ni:-1,nds:[],nf:{id:"",name:"",provider:"ark",base_url:"",api_key:"",endpoint_id:"",priority:"10",weight:"100",status:"active"},ags:{summary_agent:{label:"摘要化",mode:"system_compete",nodes:""},doc_standardize_agent:{label:"原文整理",mode:"system_compete",nodes:""},ops_agent:{label:"运维",mode:"system_compete",nodes:""},qa_orchestrator_agent:{label:"AI对话",mode:"system_compete",nodes:""},longpage_html_assembler_agent:{label:"HTML 编排",mode:"system_compete",nodes:""},longpage_diagram_legend_agent:{label:"HTML 图例生成",mode:"system_compete",nodes:""}},wfs:{},mc:"",tpl:{ot:"",fnr:""},htm:{en:true,ad:true,mb:20971520,to:60,ato:600},fs:{ci:"",pi:60,pz:20,en:false},tcBusy:false,tcResult:null,imPlatforms:[],imDetail:"",imFsTab:"cfg",imFs:{enabled:false,app_id:"",app_secret:"",verification_token:"",encrypt_key:"",auto_reply:false,chat_id:"",poll_interval_sec:10,page_size:20,running:false,recent_count:0,last_error:"",last_event_at:0,webhook_path:"/api/feishu/events/webhook"},imFsMessages:[]});
const agtKeys=computed(()=>Object.keys(st.ags));
async function ldAr(){
  try{
    const r=await fetch("/api/settings/agent-routing");
    const d=await r.json();
    const rules=d.rules||{};
    for(const k of Object.keys(st.ags)){
      const row=rules[k];
      if(!row||typeof row!=="object")continue;
      if(row.mode)st.ags[k].mode=row.mode;
      const n=row.nodes;
      st.ags[k].nodes=Array.isArray(n)?n.join(","):(typeof n==="string"?n:(st.ags[k].nodes||""));
    }
  }catch(_){}
}
function onWebreplayNavClick(){
  if(navCollapsed.value){openWebreplaySec(wr.sec||"scripts");return}
  webreplayOpen.value=!webreplayOpen.value;
  try{localStorage.setItem("sba_webreplay_open",webreplayOpen.value?"1":"0")}catch(_){}
}
function openWebreplaySec(sec){
  page.value="webreplay";
  wr.sec=sec||"scripts";
  webreplayOpen.value=true;
  try{
    localStorage.setItem("sba_webreplay_open","1");
    localStorage.setItem("sba_webreplay_sec",wr.sec);
  }catch(_){}
  if(sec==="scripts")ldWrScripts();
  if(sec==="bridge")ldWrBridge();
}
function wrHost(url){
  try{return new URL(url).hostname}catch(_){return url||"—"}
}
function wrFmtTime(ts){
  if(!ts)return "—";
  try{
    const d=new Date(ts);
    return isNaN(d.getTime())?String(ts):d.toLocaleString();
  }catch(_){return String(ts)}
}
function wrStepKindLabel(st){
  const k=String(st&&st.kind||"");
  if(k==="click")return "点击";
  if(k==="input")return "输入";
  if(k==="wait")return "等待";
  return k||"—";
}
function wrStepDesc(st){
  if(!st)return "—";
  if(st.kind==="click"||st.kind==="input"){
    const css=st.selector&&st.selector.css;
    return (css?css.slice(0,72):"—")+(st.kind==="input"&&st.value?" · 值="+String(st.value).slice(0,24):"");
  }
  if(st.kind==="wait")return (st.reason||"fixed")+" "+(st.timeoutMs||0)+"ms";
  return "—";
}
async function ldWrScripts(){
  wr.loading=true;wr.err="";
  try{
    const r=await fetch("/api/webreplay/scripts",{headers:authBearerHeaders()});
    const d=await parseApiJson(r);
    if(!r.ok)throw new Error(d.detail||"加载失败");
    wr.scripts=d.scripts||[];
    if(wr.selId){
      const hit=wr.scripts.find(s=>s.id===wr.selId);
      wr.selDetail=hit||null;
      if(!hit)wr.selId="";
    }
  }catch(e){wr.err=e.message||String(e)}finally{wr.loading=false}
}
async function wrSelectScript(id){
  wr.selId=id;wr.err="";
  try{
    const r=await fetch("/api/webreplay/scripts/"+encodeURIComponent(id),{headers:authBearerHeaders()});
    const d=await parseApiJson(r);
    if(!r.ok)throw new Error(d.detail||"加载失败");
    wr.selDetail=d.script||null;
  }catch(e){wr.err=e.message||String(e)}
}
async function wrDeleteScript(id){
  if(!id||!confirm("确定删除该脚本？"))return;
  wr.err="";
  try{
    const r=await fetch("/api/webreplay/scripts/"+encodeURIComponent(id),{method:"DELETE",headers:authBearerHeaders()});
    const d=await parseApiJson(r);
    if(!r.ok)throw new Error(d.detail||"删除失败");
    showToastMsg("已删除");
    wr.selId="";wr.selDetail=null;
    await ldWrScripts();
  }catch(e){wr.err=e.message||String(e)}
}
async function wrExportAll(){
  try{
    const r=await fetch("/api/webreplay/scripts/export/all",{headers:authBearerHeaders()});
    const d=await parseApiJson(r);
    if(!r.ok)throw new Error(d.detail||"导出失败");
    const blob=new Blob([JSON.stringify(d,null,2)],{type:"application/json"});
    const url=URL.createObjectURL(blob);
    const a=document.createElement("a");
    a.href=url;a.download="webreplay-scripts-"+Date.now()+".json";a.click();
    URL.revokeObjectURL(url);
    showToastMsg("已导出");
  }catch(e){wr.err=e.message||String(e)}
}
function wrExportOne(script){
  if(!script)return;
  const blob=new Blob([JSON.stringify({scripts:[script]},null,2)],{type:"application/json"});
  const url=URL.createObjectURL(blob);
  const a=document.createElement("a");
  a.href=url;a.download=(script.name||"script")+".webreplay.json";a.click();
  URL.revokeObjectURL(url);
}
async function wrImportFile(ev){
  const f=ev.target&&ev.target.files&&ev.target.files[0];
  ev.target.value="";
  if(!f)return;
  wr.err="";
  try{
    const text=await f.text();
    const payload=JSON.parse(text);
    const scripts=Array.isArray(payload)?payload:(payload.scripts||[]);
    const r=await fetch("/api/webreplay/scripts/import",{method:"POST",headers:authJsonHeaders(),body:JSON.stringify({scripts})});
    const d=await parseApiJson(r);
    if(!r.ok)throw new Error(d.detail||"导入失败");
    showToastMsg("已导入 "+(d.imported||0)+" 条");
    await ldWrScripts();
  }catch(e){wr.err=e.message||String(e)}
}
async function ldWrBridge(){
  wr.err="";
  try{
    const r=await fetch("/api/webreplay/bridge",{headers:authBearerHeaders()});
    const d=await parseApiJson(r);
    if(!r.ok)throw new Error(d.detail||"加载失败");
    wr.bridge.extensionId=d.extensionId||"";
    wr.bridge.origin=d.origin||(typeof location!=="undefined"?location.origin:"");
  }catch(e){wr.err=e.message||String(e)}
}
async function wrSaveBridge(){
  wr.err="";
  try{
    const r=await fetch("/api/webreplay/bridge",{method:"POST",headers:authJsonHeaders(),body:JSON.stringify({
      extensionId:(wr.bridge.extensionId||"").trim(),
      origin:(typeof location!=="undefined"?location.origin:""),
    })});
    const d=await parseApiJson(r);
    if(!r.ok)throw new Error(d.detail||"保存失败");
    wr.bridge.extensionId=d.extensionId||"";
    showToastMsg("扩展配置已保存");
  }catch(e){wr.err=e.message||String(e)}
}
async function wrCopyMcpSnippet(){
  try{
    await navigator.clipboard.writeText(wrMcpSnippet.value);
    showToastMsg("已复制 MCP 示例");
  }catch(_){prompt("复制以下内容",wrMcpSnippet.value)}
}
function onSettingsNavClick(){
  if(navCollapsed.value){openSettingsSec(isAdmin.value?(st.sec||"agent"):"link");return}
  settingsOpen.value=!settingsOpen.value;
  try{localStorage.setItem("sba_settings_open",settingsOpen.value?"1":"0")}catch(_){}
}
function openSettingsSec(sec){
  page.value="settings";
  if(sec==="agent"&&!isAdmin.value)st.sec="link";
  else st.sec=sec;
  settingsOpen.value=true;
  try{localStorage.setItem("sba_settings_open","1")}catch(_){}
  if(sec==="im")ldImPlatforms();
}
const IM_PLATFORM_ICONS={feishu:"飞",dingtalk:"钉",qq:"Q",wework:"企",wechat:"微"};
function imPlatformIcon(id){return IM_PLATFORM_ICONS[id]||"IM"}
function imPlatformBadge(pf){
  if(pf.status==="connected")return"已连接";
  if(pf.status==="coming_soon")return"即将上线";
  if(pf.status==="configured")return"已配置";
  return pf.available?"配置":"即将上线";
}
function imDetailTitle(){
  const pf=(st.imPlatforms||[]).find(x=>x.id===st.imDetail);
  return pf?pf.name+" 配置":"IM 平台";
}
async function ldImPlatforms(){
  try{
    const r=await fetch("/api/settings/im-robots/platforms");
    const d=await r.json();
    st.imPlatforms=d.platforms||[];
    if(st.imDetail==="wechat")await ldImWechat();
  }catch(e){console.warn("[IM] ldImPlatforms",e)}
}
function openImPlatform(pf){
  if(!pf||!pf.available){alert("该平台即将上线");return}
  st.imDetail=pf.id;
  if(pf.id==="feishu"){st.imFsTab="cfg";ldImFeishu()}
}
function closeImDetail(){st.imDetail=""}
function imFsTime(ts){
  if(!ts)return"-";
  try{const d=new Date(Number(ts));return isNaN(d.getTime())?String(ts):d.toLocaleString()}catch(_){return String(ts)}
}
const imFsWebhookUrl=computed(()=>{
  const path=(st.imFs.webhook_path||"/api/feishu/events/webhook").trim();
  if(path.startsWith("http://")||path.startsWith("https://"))return path;
  const base=(typeof location!=="undefined"&&location.origin)?location.origin:"http://127.0.0.1:8000";
  return base.replace(/\/$/,"")+(path.startsWith("/")?path:"/"+path);
});
async function imFsCopyWebhook(){
  const url=imFsWebhookUrl.value;
  try{
    await navigator.clipboard.writeText(url);
    showToastMsg("Webhook URL 已复制");
  }catch(_){
    try{await navigator.clipboard.writeText(url)}catch(__){prompt("复制 Webhook URL",url)}
  }
}
async function ldImFeishu(){
  try{
    const [cr,sr]=await Promise.all([fetch("/api/settings/feishu"),fetch("/api/settings/feishu/status")]);
    const d=await cr.json();const s=await sr.json();
    st.imFs.enabled=!!d.enabled;
    st.imFs.app_id=d.app_id||"";
    st.imFs.app_secret="";
    st.imFs.chat_id=d.chat_id||"";
    st.imFs.poll_interval_sec=d.poll_interval_sec||10;
    st.imFs.page_size=d.page_size||20;
    st.imFs.running=!!(s.running||d.running);
    st.imFs.recent_count=s.recent_count||0;
    st.imFs.last_error=s.last_error||d.last_error||"";
    st.imFs.last_event_at=s.last_event_at||0;
    st.imFs.webhook_path=s.webhook_path||d.webhook_path||"/api/feishu/events/webhook";
    await ldImPlatforms();
    await ldImFeishuMsgs();
  }catch(e){console.warn("[IM] ldImFeishu",e)}
}
async function ldImFeishuMsgs(){
  try{
    const r=await fetch("/api/settings/feishu/messages?limit=100");
    const d=await r.json();
    st.imFsMessages=d.messages||[];
  }catch(e){console.warn("[IM] ldImFeishuMsgs",e)}
}
async function imFsSave(){
  try{
    const body={
      feishu_group_trigger_enabled:!!st.imFs.enabled,
      feishu_group_chat_id:st.imFs.chat_id,
      feishu_app_id:st.imFs.app_id,
      feishu_im_auto_reply:!!st.imFs.auto_reply,
      feishu_im_mode:"event"
    };
    if(st.imFs.app_secret)body.feishu_app_secret=st.imFs.app_secret;
    if(st.imFs.verification_token)body.feishu_verification_token=st.imFs.verification_token;
    if(st.imFs.encrypt_key)body.feishu_encrypt_key=st.imFs.encrypt_key;
    const r=await fetch("/api/settings/feishu/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||d.error||"保存失败");
    alert("飞书事件订阅配置已保存");
    await ldImFeishu();
  }catch(e){alert(e.message||"保存失败")}
}
async function ldGw(){try{const r=await fetch('/api/settings/gateway-nodes');const d=await r.json();st.nds=d.nodes||[]}catch(e){}}
function ldNf(n){Object.assign(st.nf,{id:n.id||"",name:n.name||"",provider:n.provider||"ark",base_url:n.base_url||"",api_key:n.api_key||"",endpoint_id:n.endpoint_id||"",priority:String(n.priority||10),weight:String(n.weight||100),status:n.status||"active"})}
async function testConn(){st.tcBusy=true;st.tcResult=null;
try{const r=await fetch('/api/settings/test-connection',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({provider:st.nf.provider,api_key:st.nf.api_key,base_url:st.nf.base_url,model:st.nf.endpoint_id})});st.tcResult=await r.json()}catch(e){st.tcResult={ok:false,status:'error',error:e.message}}st.tcBusy=false}
async function ndUpSert(){await fetch('/api/settings/gateway-nodes/upsert',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(st.nf)});ldGw();alert('已保存')}
async function ndPoolSv(){await fetch('/api/settings/gateway-nodes/reorder',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({nodes:st.nds})});alert('已保存')}
function ndUp(){if(st.ni>0){const t=st.nds[st.ni];st.nds.splice(st.ni,1);st.nds.splice(st.ni-1,0,t);st.ni--}}
function ndDn(){if(st.ni<st.nds.length-1){const t=st.nds[st.ni];st.nds.splice(st.ni,1);st.nds.splice(st.ni+1,0,t);st.ni++}}
async function ndDel(){if(st.ni<0)return;const n=st.nds[st.ni];await fetch('/api/settings/gateway-nodes/'+n.id,{method:'DELETE'});ldGw()}
async function rtSv(){const rules={};for(const[k,ag]of Object.entries(st.ags)){rules[k]={mode:ag.mode,nodes:(ag.nodes||'').split(',').map(s=>s.trim()).filter(Boolean)}}await fetch('/api/settings/agent-routing/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({rules})});alert('已保存')}
async function ldWf(ak){try{const r=await fetch('/api/settings/workflow-instructions/'+ak);const d=await r.json();st.wfs=d.fields||{};st.pa=ak}catch(e){}}
async function svWf(){await fetch('/api/settings/workflow-instructions/'+st.pa,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({fields:st.wfs})});alert('已保存')}
async function ldMd(ak){try{const r=await fetch('/api/settings/agents-md/'+ak);const d=await r.json();st.mc=d.content||'';st.ma=ak}catch(e){}}
async function svMd(){await fetch('/api/settings/agents-md/'+st.ma,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content:st.mc})});alert('已保存')}
async function svFs(){await fetch('/api/settings/feishu/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({feishu_group_trigger_enabled:!!st.fs.en,feishu_group_chat_id:st.fs.ci,feishu_im_mode:'event'})});alert('飞书事件订阅配置已保存')}
async function ldFsCfg(){try{const r=await fetch('/api/settings/feishu');const d=await r.json();st.fs.ci=d.chat_id||'';st.fs.en=!!d.enabled}catch(e){}}
async function ldTpl(){try{const r=await fetch('/api/settings/template');const d=await r.json();st.tpl.ot=d.output_template||'';st.tpl.fnr=d.file_naming_rule||''}catch(e){}}
async function svTpl(){await fetch('/api/settings/template/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({output_template:st.tpl.ot,file_naming_rule:st.tpl.fnr})});alert('模板已保存')}
async function ldHtmlCfg(){try{const r=await fetch('/api/settings/html-config');const d=await r.json();st.htm.en=d.longpage_html_enabled!==false;st.htm.ad=d.longpage_html_async_diagram_pipeline!==false;st.htm.mb=d.longpage_html_max_bytes||20971520;st.htm.to=d.longpage_html_timeout_sec||60;st.htm.ato=d.longpage_html_async_timeout_sec||600}catch(e){}}
async function svHtmlCfg(){await fetch('/api/settings/html-config/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({longpage_html_enabled:st.htm.en,longpage_html_async_diagram_pipeline:st.htm.ad,longpage_html_max_bytes:st.htm.mb,longpage_html_timeout_sec:st.htm.to,longpage_html_async_timeout_sec:st.htm.ato})});alert('HTML 配置已保存')}
const aicf=reactive({name:"",api_key:"",base_url:"",endpoint_id:"",provider:"ark",route_mode:"priority",backups:[]});
async function ldAiCfg(){try{const r=await fetch('/api/settings/ai-config');const d=await r.json();const cfg=d.config||{};aicf.name=cfg.name||'';aicf.api_key=cfg.api_key||'';aicf.base_url=cfg.base_url||'';aicf.endpoint_id=cfg.endpoint_id||'';aicf.provider=cfg.provider||'ark';aicf.route_mode=cfg.route_mode||'priority';aicf.backups=cfg.backup_configs||[]}catch(e){}}
async function svAiCfg(){await fetch('/api/settings/ai-config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({config:aicf})});alert('AI 配置已保存')}
const thcf=reactive({max_workers:8,llm_workers:256,background_workers:256,system_workers:4,rag_workers:2,whisper_pool_core_size:4,whisper_pool_size:16,mineru_workers:2,queue_max_size:50});
async function ldThCfg(){try{const r=await fetch('/api/settings/thread-config');const d=await r.json();Object.assign(thcf,d)}catch(e){}}
async function svThCfg(){await fetch('/api/settings/thread-config/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({max_workers:thcf.max_workers,llm_workers:thcf.llm_workers,background_workers:thcf.background_workers,system_workers:thcf.system_workers,rag_workers:thcf.rag_workers,whisper_pool_core_size:thcf.whisper_pool_core_size,whisper_pool_size:thcf.whisper_pool_size,mineru_workers:thcf.mineru_workers,queue_max_size:thcf.queue_max_size})});alert('线程配置已保存')}

const INTERNAL_IAG_TABS=[
  {key:"summary_agent",label:"1 摘要化",hint:"链接转写后的摘要提示、规则与成品 Markdown 排版"},
  {key:"doc_standardize_agent",label:"2 原文整理",hint:"去噪、分段、繁简转换等原文清洗链路"},
  {key:"ops_agent",label:"3 运维",hint:"运维对话 Agent 的系统提示"},
  {key:"qa_orchestrator_agent",label:"4 AI对话",hint:"编排用多段框架（业务/规则/约束等）"},
  {key:"longpage_html_assembler_agent",label:"5 HTML 编排",hint:"长页 HTML、多相、图表管线开关与阈值"},
  {key:"longpage_diagram_legend_agent",label:"6 HTML 图例生成",hint:"图例侧翼 LLM 与上下文上限"}
];
const IAG_LAB={
  summary_prompt:"摘要前置说明 summary_prompt",system_prompt:"模型人设 system_prompt",rules:"摘要规则 rules",
  output_template:"成品 Markdown 模板 output_template",file_naming_rule:"文件命名 file_naming_rule",
  comments_viewpoint_prompt:"评论观点提炼 comments_viewpoint_prompt",comments_viewpoint_rules:"评论观点规则 comments_viewpoint_rules",
  comments_user_prompt:"评论用户补充 comments_user_prompt",comments_summary_mode:"评论并入摘要模式 comments_summary_mode",
  comments_section_template:"【评论区】MD 片段 comments_section_template",
  article_polish_prompt:"原文抛光提示词",article_system_prompt:"原文 System",article_rules:"原文规则",
  framework_business_layer:"业务层框架",framework_rules_layer:"规则层框架",framework_constraints_layer:"约束层",
  framework_response_format_layer:"响应格式层",framework_optimization_layer:"优化层",
  ops_system_prompt:"运维 System 提示词",
  longpage_html_enabled:"启用 HTML 长页",longpage_html_max_bytes:"HTML 最大字节",longpage_html_timeout_sec:"HTML 超时(秒)",
  longpage_html_async_diagram_pipeline:"异步图表管线",longpage_html_async_timeout_sec:"异步超时(秒)",
  longpage_multiphase_enabled:"多相编排(longpage_multiphase)",longpage_s3_html_assembler_enabled:"S3 HTML 总装",
  longpage_s3_html_assembler_timeout_sec:"S3 总装超时(秒)",longpage_diagram_max_parallel:"图表最大并行",
  longpage_diagram_plan_max_chars:"图表规划最大字符",longpage_diagram_draw_context_max_chars:"绘图上下文最大字符",
  longpage_diagram_extraction_timeout:"图表抽取超时",longpage_diagram_draw_timeout:"单图绘制超时",
  longpage_legend_agent_enabled:"启用图例 LLM",longpage_legend_llm_required:"图例必须由 LLM 产出",
  longpage_legend_agent_timeout_sec:"图例超时(秒)",longpage_legend_agent_context_max_chars:"图例上下文最大字符",
  longpage_analysis_inline_diagrams_enabled:"分析区内联图",
  diagram_style_mermaid_json:"Mermaid 样式 JSON",diagram_style_legend_suite_json:"图例块 CSS 变量 JSON",
  diagram_style_diag_slot_json:"图表槽样式 JSON",diagram_style_er_json:"E-R/对照表样式 JSON",
  diagram_style_tool_flow_pill_json:"工具页药丸流样式 JSON"
};
const IAG_HELP={
  summary_prompt:"写入「摘要链路」时附在正文前的说明段。可含 {text}/{transcript}/{raw_text}/{article} 等占位符；若完全没有占位符，系统仍会把正文拼在末尾，避免漏内容。",
  system_prompt:"摘要模型的系统人设：语气、角色、输出习惯。",
  rules:"对摘要内容的硬性要求：要点、语言、禁忌、格式等。",
  output_template:"最终落盘的 Markdown 骨架。花括号里是「变量」，右侧预览用演示数据替换，便于检查版式。",
  file_naming_rule:"命名策略说明或带占位符的模板。运行时为 月-日-{doc_title}_{类型}分析.md；首层用 {link_title}，成品名用 {doc_title}（AI 摘要首行）。",
  comments_viewpoint_prompt:"独立于原文整理的评论 Agent 指令。占位符 {article_context}、{comments}；输出 Markdown 观点表 + 总览。与 system_prompt 共用角色层。",
  comments_viewpoint_rules:"评论观点提炼的硬性规则（表头、派别、AI分析维度等）。",
  comments_user_prompt:"跑单时用户对评论分析的额外说明（与原文 user_prompt 分离）。",
  comments_summary_mode:"dual=两次 LLM（观点表→摘要）；merged=评论原文一次并入摘要输入。MD 均不放全量 raw 评论。",
  comments_section_template:"嵌在 AI 分析章节之后的【评论区】Markdown 骨架。占位符：{comments_analysis}（观点表 LLM 输出）、{comments_file_link}、{comments_section}（整段）。也可在 output_template 内写 {comments_section} 控制位置。",
  article_polish_prompt:"原文整理步骤中发给模型的指令。",
  article_system_prompt:"原文整理步骤的系统人设。",
  article_rules:"原文整理的规则文本。",
  framework_business_layer:"AI 对话编排：业务层提示片段。",
  framework_rules_layer:"AI 对话编排：规则层提示片段。",
  framework_constraints_layer:"AI 对话编排：约束层提示片段。",
  framework_response_format_layer:"AI 对话编排：响应格式层。",
  framework_optimization_layer:"AI 对话编排：优化层。",
  ops_system_prompt:"运维 Agent 系统提示。",
  longpage_html_enabled:"是否生成长页 HTML。",
  longpage_html_max_bytes:"单页 HTML 最大字节数。",
  longpage_html_timeout_sec:"HTML 生成同步超时。",
  longpage_html_async_diagram_pipeline:"是否走异步图表管线。",
  longpage_html_async_timeout_sec:"异步管线总超时。",
  longpage_multiphase_enabled:"是否启用多相编排。",
  longpage_s3_html_assembler_enabled:"是否启用 S3 总装。",
  longpage_s3_html_assembler_timeout_sec:"S3 总装超时。",
  longpage_diagram_max_parallel:"图表任务最大并行数。",
  longpage_diagram_plan_max_chars:"图表规划阶段最大字符。",
  longpage_diagram_draw_context_max_chars:"绘图上下文最大字符。",
  longpage_diagram_extraction_timeout:"图表抽取超时。",
  longpage_diagram_draw_timeout:"单图绘制超时。",
  longpage_legend_agent_enabled:"是否启用图例 LLM。",
  longpage_legend_llm_required:"图例是否必须由 LLM 产出。",
  longpage_legend_agent_timeout_sec:"图例 LLM 超时。",
  longpage_legend_agent_context_max_chars:"图例上下文最大字符。",
  longpage_analysis_inline_diagrams_enabled:"分析区是否内联图表。",
  diagram_style_mermaid_json:"长页与工具页 Mermaid.initialize 参数（theme、themeVariables、flowchart 等）。",
  diagram_style_legend_suite_json:"长页 .legend-suite 块样式键值。",
  diagram_style_diag_slot_json:"长页 .diag-slot 单图容器样式。",
  diagram_style_er_json:"E-R 或双栏对照表样式键值。",
  diagram_style_tool_flow_pill_json:"工具页 SKILL 药丸流程图视觉参数。"
};
const IAG_SUMMARY_PREVIEW_VARS=[
  {tok:"{platform}",title:"平台名称",desc:"根据链接识别：抖音、B站、小红书、微信公众号等。",sample:"小红书"},
  {tok:"{transcript}",title:"转写正文",desc:"语音转写或抓取的原文，可能很长。",sample:"（演示）主播：大家好，今天用三步完成部署…"},
  {tok:"{summary}",title:"模型摘要",desc:"模型按规则生成的结构化摘要。",sample:"要点：①环境 ②配置 ③验证"},
  {tok:"{link}",title:"原始链接",desc:"用户提交的视频/帖子 URL。",sample:"https://www.xiaohongshu.com/explore/sample"},
  {tok:"{datetime}",title:"生成时间",desc:"写入文档时的时间戳。",sample:"2026-05-15 14:30:00"},
  {tok:"{date}",title:"日期简写",desc:"月-日形式（若模板使用）。",sample:"05-15"},
  {tok:"{link_title}",title:"链接首层标题",desc:"从帖子/视频页面 title 或链接解析，处理早期即可展示。",sample:"淘天集团AI Agent岗位笔记"},
  {tok:"{doc_title}",title:"AI 摘要标题",desc:"从摘要首行/标题行提取，用于导出文件名（月-日-文档名_类型分析.md）。",sample:"这份内容是淘天集团AI_Agent岗位的"},
  {tok:"{text}",title:"正文占位",desc:"与 summary_prompt 联用时注入正文。",sample:"（演示正文片段）"},
  {tok:"{article}",title:"整理后正文",desc:"原文整理链路输出（若引用）。",sample:"（演示整理段落）"},
  {tok:"{comments_section}",title:"评论区整段",desc:"由 comments_section_template 渲染，通常接在 AI 分析后。",sample:"## 【评论区】\n\n| 层次 | 角色 | 观点原句 | … |"},
  {tok:"{comments_analysis}",title:"评论观点表",desc:"评论观点 Agent 输出（表格+总览）。",sample:"| 提问 | 网友 | 怎么玩 agent | … |"},
  {tok:"{comments_file_link}",title:"评论原文链接",desc:"独立评论文件的 Markdown 链接行。",sample:"评论原文已单独保存，请查看: [comments_xxx.md](./comments_xxx.md)"}
];
const IAG_COMMENTS_SECTION_VARS=[
  {tok:"{comments_analysis}",title:"观点表+总览",desc:"LLM 按层次（提问/作者回复/派别）提炼后的正文。",sample:"| 层次 | 角色/派别 | 观点原句 | 精简解释 | AI分析 |"},
  {tok:"{comments_file_link}",title:"原文文件",desc:"全量评论落盘后的引用行。",sample:"评论原文已单独保存，请查看: [comments.md](./comments.md)"},
  {tok:"{comments_section}",title:"整段渲染结果",desc:"本模板渲染后的完整【评论区】块。",sample:"## 【评论区】\n…"}
];
const iagCommentsSectionPreview=computed(()=>iagFormatPreview((iag.fields&&iag.fields.comments_section_template)||""));
const IAG_SUMMARY_BODY_TOKENS=[
  {tok:"{text}",title:"正文"},
  {tok:"{transcript}",title:"转写"},
  {tok:"{raw_text}",title:"原始文本"},
  {tok:"{article}",title:"整理稿"}
];
function iagFormatPreview(str){
  if(str==null)return"";
  var out=String(str);
  var map={
    "{platform}":"小红书",
    "{transcript}":"（演示转写）主播：大家好，今天用三步完成部署：准备环境、复制配置、启动验证。",
    "{summary}":"（演示摘要）\n1. 环境：Node 18+ / Python 3.10+\n2. 配置：填写网关与模型 ID\n3. 验证：访问 /api/health 返回 ok",
    "{link}":"https://www.xiaohongshu.com/explore/sample",
    "{datetime}":"2026-05-15 14:30:00",
    "{date}":"05-15",
    "{link_title}":"淘天集团AI Agent岗位笔记",
    "{doc_title}":"这份内容是淘天集团AI_Agent岗位的",
    "{article}":"（演示整理后正文）",
    "{text}":"（演示正文片段）",
    "{raw_text}":"（演示 raw_text）",
    "{comments_analysis}":"| 层次 | 角色/派别 | 观点原句 | 精简解释 | AI分析 |\n| 提问 | 网友 | 怎么玩 agent | 求学习路径 | 建议 0→1 vibe coding + tradeoff |\n| 作者回复 | 博主 | 蓝区不用 langchain | 一线面经 | 可采纳 |",
    "{comments_file_link}":"评论原文已单独保存，请查看: [comments_demo.md](./comments_demo.md)",
    "{comments_section}":"## 【评论区】\n\n（演示观点表）"
  };
  Object.keys(map).forEach(function(k){out=out.split(k).join(map[k]);});
  return out;
}
function iagHelpText(k){return IAG_HELP[k]||""}
const iagRawMode=ref(false);
const IAG_KEY_ORDER={
  summary_agent:["summary_prompt","system_prompt","rules","comments_viewpoint_prompt","comments_viewpoint_rules","comments_user_prompt","comments_summary_mode","comments_section_template","output_template","file_naming_rule"],
  doc_standardize_agent:["article_polish_prompt","article_system_prompt","article_rules"],
  qa_orchestrator_agent:["framework_business_layer","framework_rules_layer","framework_constraints_layer","framework_response_format_layer","framework_optimization_layer"],
  ops_agent:["ops_system_prompt"],
  longpage_html_assembler_agent:["longpage_html_enabled","longpage_html_max_bytes","longpage_html_timeout_sec","longpage_html_async_diagram_pipeline","longpage_html_async_timeout_sec","longpage_multiphase_enabled","longpage_s3_html_assembler_enabled","longpage_s3_html_assembler_timeout_sec","longpage_diagram_max_parallel","longpage_diagram_plan_max_chars","longpage_diagram_draw_context_max_chars","longpage_diagram_extraction_timeout","longpage_diagram_draw_timeout"],
  longpage_diagram_legend_agent:["longpage_legend_agent_enabled","longpage_legend_llm_required","longpage_legend_agent_timeout_sec","longpage_legend_agent_context_max_chars","longpage_analysis_inline_diagrams_enabled","diagram_style_mermaid_json","diagram_style_legend_suite_json","diagram_style_diag_slot_json","diagram_style_er_json","diagram_style_tool_flow_pill_json"]
};
const iag=reactive({tab:"summary_agent",fields:{},md:"",mdPath:"",saving:false,err:""});
function iagFieldKeys(){
  const order=IAG_KEY_ORDER[iag.tab]||[];
  const f=iag.fields||{};
  const keys=Object.keys(f);
  const head=order.filter(x=>Object.prototype.hasOwnProperty.call(f,x));
  const tail=keys.filter(x=>head.indexOf(x)<0).sort();
  return head.concat(tail);
}
function iagLabel(k){return IAG_LAB[k]||k}
function iagIsLongText(k,v){return typeof v==="string"&&(String(v).length>100||/prompt|template|layer|rules|polish|system|content/i.test(k));}
const iagTplPreviewMd=computed(()=>iagFormatPreview((iag.fields&&iag.fields.output_template)||""));
const iagTplPreviewFn=computed(()=>iagFormatPreview((iag.fields&&iag.fields.file_naming_rule)||""));
function iagGenericFieldKeys(){
  const diagramKeys=iagDiagramStyleKeys().map(m=>m.key);
  if(iag.tab==="summary_agent"&&!iagRawMode.value){
    var hide={summary_prompt:1,system_prompt:1,rules:1,comments_viewpoint_prompt:1,comments_viewpoint_rules:1,comments_user_prompt:1,comments_summary_mode:1,comments_section_template:1,output_template:1,file_naming_rule:1};
    return iagFieldKeys().filter(function(k){return!hide[k]&&!diagramKeys.includes(k);});
  }
  if(iag.tab==="longpage_diagram_legend_agent"){
    return iagFieldKeys().filter(function(k){return!diagramKeys.includes(k);});
  }
  return iagFieldKeys();
}
function insertIagToken(fieldKey, token){
  if(!iag.fields)return;
  var cur=String(iag.fields[fieldKey]||"");
  var sep=cur.length&&!/\n$/.test(cur)?"\n":"";
  iag.fields[fieldKey]=cur+sep+token;
}
function iagToggleRawMode(){iagRawMode.value=!iagRawMode.value;}
async function ldIag(){
  if(!isAdmin.value)return;
  iag.err="";
  const ak=iag.tab;
  try{
    const wf=await fetch("/api/settings/workflow-instructions/"+encodeURIComponent(ak));
    const wd=await wf.json();
    if(!wf.ok)throw new Error(typeof wd.detail==="string"?wd.detail:JSON.stringify(wd.detail||wd)||wf.statusText);
    iag.fields=Object.assign({},wd.fields||{});
    diagramStyleFields.value=Object.assign({},iag.fields);
    ensureDiagramStyleDefaults();
    iagDiagramStyleKeys().forEach(m=>{
      if(iag.fields[m.key]==null||iag.fields[m.key]==="")iag.fields[m.key]=diagramStyleFields.value[m.key];
    });
    const mr=await fetch("/api/settings/agents-md/"+encodeURIComponent(ak));
    const md=await mr.json();
    if(!mr.ok)throw new Error(typeof md.detail==="string"?md.detail:JSON.stringify(md.detail||md)||mr.statusText);
    iag.md=md.content||"";iag.mdPath=md.path||"";
  }catch(e){iag.err=e.message||String(e)}
}
async function saveIag(){
  if(!isAdmin.value)return;
  const ak=iag.tab;
  iag.saving=true;iag.err="";
  try{
    const r=await fetch("/api/settings/workflow-instructions/"+encodeURIComponent(ak),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({fields:iag.fields})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(typeof d.detail==="string"?d.detail:JSON.stringify(d.detail||d)||"保存失败");
    const r2=await fetch("/api/settings/agents-md/"+encodeURIComponent(ak),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({content:iag.md})});
    const d2=await r2.json().catch(()=>({}));
    if(!r2.ok)throw new Error(typeof d2.detail==="string"?d2.detail:JSON.stringify(d2.detail||d2)||"保存 MD 失败");
    showToastMsg("已保存当前内部 Agent");
  }catch(e){iag.err=e.message||String(e)}
  finally{iag.saving=false}
}

/* ══ P7 OPS ══ */
const op=reactive({
  sub:"sp",
  ov:{tc:0,sc:0,fc:0,ac:0,tp:[]},
  ev:[],
  st:{llm_ready:false,model:"",model_backup:"",memory_count:0,report_count:0},
  reports:[],
  memory:[],
  suggestions:[],
  daily:{entry_count:0,error_by_type:{}},
  reportId:"",
  reportBody:"",
  analysis:"",
  analysisLlm:false,
  busy:false,
  err:"",
  hooks:{log_incident_enabled:true,ops_async_check_enabled:true},
  health:{items:[],summary:{ok:0,warn:0,error:0},all_ok:false},
  healthLoading:false,
  eval:{},
  spanTasks:[],
  spanExceptions:[],
  spanTaskId:"",
  spanDetail:null,
  spanStepSel:null,
  spanLoading:false,
  spanStats:{task_count:0,step_count:0,total_ms:0,failed_count:0,tokens:0}
});
function opSpanStatusClass(st){
  const s=(st||"").toLowerCase();
  if(s==="completed"||s==="ok")return "ok";
  if(s==="failed"||s==="timeout"||s==="error")return "err";
  if(s==="running"||s==="pending"||s==="created")return "run";
  if(s==="warn"||s==="warning")return "warn";
  return "";
}
function opSpanMaxMs(spans){
  const arr=spans||[];
  let m=1;
  for(const s of arr)m=Math.max(m,Number(s&&s.duration_ms)||0);
  return m;
}
function opSpanBarPct(span,maxMs){
  const m=maxMs||1;
  return Math.min(100,Math.round((Number(span&&span.duration_ms)||0)/m*100));
}
function opFmtJson(v){
  if(v==null||v==="")return "—";
  if(typeof v==="string")return v.length>12000?v.slice(0,12000)+"\n…(已截断)":v;
  try{return JSON.stringify(v,null,2);}catch(_){return String(v);}
}
function opPickSpanStep(s){op.spanStepSel=s;}
function opSpanTypeLabel(t){
  const m={retrieval:"检索",llm_call:"LLM",tool_call:"工具",api_call:"API",summary:"摘要",reasoning:"推理"};
  return m[t]||t||"—";
}
function opComputeSpanStats(){
  const tasks=op.spanTasks||[];
  let steps=0,totalMs=0,failed=0,tokens=0;
  for(const t of tasks){
    totalMs+=Number(t.total_duration_ms)||0;
    tokens+=Number(t.total_token_count)||0;
    failed+=Number(t.failed_steps)||0;
    steps+=Number(t.total_steps)||0;
  }
  op.spanStats={task_count:tasks.length,step_count:steps,total_ms:totalMs,failed_count:failed,tokens};
}
async function ldOpSpans(){
  op.err="";
  op.spanLoading=true;
  try{
    const r=await fetch("/api/ops/spans/tasks?limit=80");
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||d.error||"加载 SPAN 任务失败");
    op.spanTasks=d.tasks||[];
    opComputeSpanStats();
    if(op.spanTasks.length){
      const keep=op.spanTaskId&&op.spanTasks.some(t=>t.task_id===op.spanTaskId);
      if(!keep)await opLoadSpanTask(op.spanTasks[0].task_id);
    }else{
      op.spanTaskId="";op.spanDetail=null;op.spanStepSel=null;
    }
  }catch(e){
    op.err=e.message||String(e);
    op.spanTasks=[];
    opComputeSpanStats();
  }finally{op.spanLoading=false;}
}
async function ldOpSpanExceptions(){
  try{
    const r=await fetch("/api/ops/spans/exceptions?limit=100");
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||d.error||"加载异常 SPAN 失败");
    op.spanExceptions=d.items||[];
  }catch(e){op.err=e.message||String(e)}
}
async function opLoadSpanTask(taskId){
  const tid=(taskId||"").trim();
  if(!tid)return;
  op.spanTaskId=tid;
  op.spanLoading=true;
  try{
    const r=await fetch("/api/ops/spans/tasks/"+encodeURIComponent(tid));
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||d.error||"加载失败");
    op.spanDetail=d;
    const spans=d.spans||[];
    op.spanStepSel=spans.length?spans[0]:null;
  }catch(e){op.err=e.message||String(e);op.spanDetail=null;op.spanStepSel=null;}
  finally{op.spanLoading=false;}
}
async function ldOpSpansAll(){
  await Promise.all([ldOpSpans(),ldOpSpanExceptions()]);
}
async function ldOp(){
  try{
    const[r1,r2]=await Promise.all([
      fetch("/api/ops/observability/overview"),
      fetch("/api/ops/observability/events?limit=50")
    ]);
    const d1=await r1.json();const d2=await r2.json();
    const ov=d1.data||{};
    op.ov.tc=ov.total_calls||0;op.ov.sc=ov.success_calls||0;op.ov.fc=ov.failed_calls||0;
    op.ov.ac=ov.avg_cost_ms||0;op.ov.tp=ov.top_paths||[];
    op.ev=d2.data?.events||[];
  }catch(e){}
}
async function ldOpAg(){
  op.err="";
  try{
    const[rs,rr,rm,rd,sg,db]=await Promise.all([
      fetch("/api/ops/agent/status"),
      fetch("/api/ops/reports?limit=40"),
      fetch("/api/ops/memory?limit=25"),
      fetch("/api/ops/daily-stats"),
      fetch("/api/ops/route/suggestions"),
      fetch("/api/ops/dashboard")
    ]);
    const js=await rs.json();const jr=await rr.json();const jm=await rm.json();
    const jd=await rd.json();const jg=await sg.json();const jdb=await db.json();
    if(js.ok&&js.data)Object.assign(op.st,js.data);
    op.reports=jr.data?.reports||[];
    op.memory=jm.data?.items||[];
    op.daily=jd.data||{entry_count:0,error_by_type:{}};
    op.suggestions=jg.data?.suggestions||[];
    if(jdb.ok&&jdb.data){
      if(jdb.data.hooks)Object.assign(op.hooks,jdb.data.hooks);
      if(jdb.data.eval)op.eval=jdb.data.eval;
      if(jdb.data.platform_health&&jdb.data.platform_health.ready)op.health=jdb.data.platform_health;
    }
  }catch(e){op.err=e.message||String(e);}
}
async function ldOpHealth(refresh){
  op.healthLoading=true;
  try{
    const url="/api/platform/health"+(refresh?"?refresh=1":"");
    const r=await fetch(url,{headers:authBearerHeaders()});
    const d=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(fmtApiErr(d,r));
    op.health=d&&typeof d==="object"?d:{items:[],summary:{ok:0,warn:0,error:0}};
    const er=await fetch("/api/eval/status");
    const ed=await er.json().catch(()=>({}));
    if(ed.ok&&ed.data)op.eval=ed.data;
  }catch(e){
    op.health={items:[],summary:{ok:0,warn:0,error:1},all_ok:false,error:e.message||String(e)};
  }finally{op.healthLoading=false;}
}
async function opLoadReport(id){
  if(!id)return;
  op.reportId=id;op.reportBody="";
  try{
    const r=await fetch("/api/ops/reports/"+encodeURIComponent(id));
    const d=await r.json();
    if(!r.ok||!d.ok)throw new Error(d.error||"加载失败");
    op.reportBody=d.data?.content||"";
  }catch(e){op.err=e.message||String(e);}
}
async function opAnalyzeLogs(useLlm){
  op.busy=true;op.err="";
  try{
    const r=await fetch("/api/ops/analyze-logs",{
      method:"POST",
      headers:authJsonHeaders(),
      body:JSON.stringify({use_llm:!!useLlm})
    });
    const d=await r.json();
    if(!r.ok||!d.ok)throw new Error(d.error||"分析失败");
    op.analysis=d.analysis||"";
    op.analysisLlm=!!d.llm_powered;
    if(d.stats)op.daily=d.stats;
    showToastMsg(useLlm?"日志 LLM 分析完成":"规则日报已生成");
    await ldOpAg();
  }catch(e){op.err=e.message||String(e);}
  finally{op.busy=false;}
}

watch(v,()=>{try{localStorage.setItem('sba_link_prefs',JSON.stringify({wf:v.wf,fs:v.fs,fp:v.fp,html:v.html,subtitle:v.subtitle,cookies:v.cookies,pr:v.pr}))}catch(_){}},{deep:true});
// 页面切换后的数据加载
watch(()=>c.readComments,(v)=>{
  if(v)requestChatWarmup({wait:false,readComments:true}).catch(()=>{});
});
watch(page,(p)=>{
  try{
    if(!isAuthenticated.value && REQUIRES_AUTH_PAGES.includes(p)){
      const token=localStorage.getItem('sba_token');
      if(token){
        const user=safeJsonParse(localStorage.getItem('sba_user'),null);
        if(user&&user.id){
          AuthManager.state.setAuth(token,user);
          isAuthenticated.value=AuthManager.state.isAuthenticated;
          isAdmin.value=AuthManager.state.isAdmin;
          authUser.value=AuthManager.state.user;
        }
      }
      if(!isAuthenticated.value)return;
    }
    if(p==='orch'){
      try{
        const saved=localStorage.getItem('sba_orch_sec');
        orchTocActive.value=(saved&&ORCH_SEC_CRUMB[saved])?saved:'orch-sec-tool';
      }catch(_){orchTocActive.value='orch-sec-tool';}
      ldSkills();ldBuiltinTools();ldMcpCfg();ldMcpVendors();ldDiagramStyles();
      nextTick(()=>setupOrchSectionSpy());
    }else if(orchSecObs){orchSecObs.disconnect();orchSecObs=null;}
    if(p==='chat'){refreshSlash();beginChatConnect();loadPlatformHealth(false);requestChatWarmup({wait:false}).catch(()=>{});}
    if(p==='rag'){kbLoadImportHistory();ldKbLibs();ldKbMetaOpts();ldKbS();ldKbF();ldKbConn();kbLoadRecallVocab();}
    if(p==='rss'){ldRssAll();}
    if(p==='video'&&videoSubTab.value==='up'){ldSubscriptions();}
    if(p==='agpz'){ldApzCatalog().then(async()=>{await syncApzTemplateToCurrentAgent();});}
    if(p==='iag'){ldIag();}
    if(p==='settings'){
      if(!isAdmin.value&&st.sec==='agent')st.sec='link';
      if(isAdmin.value&&st.sec==='agent'){ldGw();ldAr();}
      if(st.sec==='im')ldImPlatforms();
    }
    if(p==='profile'){syncProfFromAuth();ldUserPortrait();}
    if(p==='tasks'){scheduleTaskRegistryReload();}
    if(p==='ops'){
      ldOp();ldOpAg();
      if(op.sub==='ph')ldOpHealth(false);
      if(op.sub==='sp')ldOpSpansAll();
    }
  }catch(err){
    console.error('[SBA watch(page) load]',err);
    revealRuntimeFault('页面数据加载失败',err&&(err.message||err));
  }
});
watch(()=>iag.tab,()=>{
  if(iag.tab!=="summary_agent")iagRawMode.value=false;
  if(page.value==="iag"&&isAdmin.value)ldIag();
});

onMounted(async()=>{
  // 挂载后立即去掉 v-cloak，避免与鉴权遮罩叠加成「整页白屏」
  const appRoot=document.getElementById('app');
  if(appRoot)appRoot.removeAttribute('v-cloak');

  // 初始化认证状态
  AuthManager.state.restore();
  
  // 检查当前是否在登录页
  const currentPath = window.location.pathname;
  const isLoginPage = currentPath === '/login.html' || currentPath.endsWith('/login.html');
  
  const pathPage=currentPath.replace(/^\//,'').split('/')[0];
  const allMenuItemsBoot=[...menuMainBase,{key:'iag',label:'内部 Agent 配置'},{key:'webreplay',label:'浏览器自动化'}];
  const wantPage=pathPage&&allMenuItemsBoot.some(m=>m.key===pathPage)?pathPage:'';
  
  // 如果在登录页，不显示遮罩
  if(isLoginPage){
    return;
  }
  
  // 监听浏览器前进后退
  window.addEventListener('popstate',(e)=>{
    let target='';
    if(e.state&&e.state.page)target=e.state.page;
    else{
      const path=window.location.pathname.replace(/^\//,'').split('/')[0];
      const allMenuItems=[...menuMainBase,{key:'iag',label:'内部 Agent 配置'},{key:'webreplay',label:'浏览器自动化'}];
      if(path&&allMenuItems.some(m=>m.key===path))target=path;
    }
    if(!target)return;
    if(guardPageSwitch(target)){
      page.value=target;
      if(target==='webreplay')openWebreplaySec(wr.sec||'scripts');
    }else if(target!=='video')page.value='video';
  });
  
  // 仅有 token、无 sba_user 时从服务端补全（避免 AI 问答等页被鉴权遮罩挡死）
  async function hydrateAuthFromServer(){
    const token=localStorage.getItem('sba_token');
    if(!token)return false;
    try{
      const probeResp=await fetch('/api/auth/me',{headers:authBearerHeaders()});
      if(probeResp.status===401){
        AuthManager.logout();
        window.location.href='/login.html?expired=1';
        return false;
      }
      if(!probeResp.ok)return false;
      const probeData=await probeResp.json();
      if(probeData.user){
        AuthManager.login(token,probeData.user);
        authUser.value=probeData.user;
        isAdmin.value=!!(probeData.user.roles&&probeData.user.roles.includes('admin'));
        isAuthenticated.value=true;
        const mask=document.getElementById('auth-required-mask');
        if(mask)mask.style.display='none';
        return true;
      }
    }catch(_){}
    return false;
  }

  if(!AuthManager.checkAuth()){
    const hydrated=await hydrateAuthFromServer();
    if(!hydrated){
      showAuthRequiredMask(true);
      if(wantPage&&wantPage!=='video'){
        page.value='video';
        if(history.replaceState)history.replaceState({page:'video'},'','/video');
      }
      return;
    }
  }else{
    await hydrateAuthFromServer();
  }

  if(wantPage){
    if(guardPageSwitch(wantPage)){
      page.value=wantPage;
      if(!openTabs.value.includes(wantPage))openTabs.value=openTabs.value.concat([wantPage]);
      if(wantPage==='chat'&&c.chatPanelTab!=='config')c.chatPanelTab='room';
      if(wantPage==='webreplay')openWebreplaySec(wr.sec||'scripts');
    }else if(wantPage!=='video'){
      page.value='video';
      if(history.replaceState)history.replaceState({page:'video'},'','/video');
    }
  }
  
  // 已登录，加载数据
  loadOrchToggle();
  loadUiPrefs();
  const linkPrefs=safeJsonParse(localStorage.getItem('sba_link_prefs'),null);
  if(linkPrefs&&typeof linkPrefs==='object')Object.assign(v,linkPrefs);
  await ldLinkPipelinePrefs();
  
  // 加载用户数据
  await ldAuthUser();
  
  // 加载各项数据
  ldWfs();ldVec();pollQueue();
  queueTimer=setInterval(pollQueue,2000);
  vecTimer=setInterval(ldVec,30000);
  ldCs();kbLoadImportHistory();ldKbS();ldKbF();ldKbMetaOpts();caQ();ldOp();ldOpAg();
  if(isAdmin.value){ldGw().catch(()=>{});}
  ldHist();histTimer=setInterval(ldHist,5000);
  ldSkills();ldKbLibs();ldAgentProfiles();
  ldApzCatalog().catch(()=>{});
  try{
    const ch=localStorage.getItem("sba_chat_prefs");
    if(ch){
      const o=safeJsonParse(ch,{});
      if(o&&typeof o==='object'){
        if(o.model!=null)c.model=o.model;
        if(o.agentId)c.agentId=o.agentId;
        if(o.deepThink!=null)c.deepThink=!!o.deepThink;
        if(o.webSearch!=null)c.webSearch=!!o.webSearch;
        if(o.ragPrefetch!=null)c.ragPrefetch=!!o.ragPrefetch;
        if(o.readComments!=null)c.readComments=!!o.readComments;
        if(o.includeRss!=null)c.includeRss=!!o.includeRss;
        if(o.chatPrefs&&typeof o.chatPrefs==='object'){
          const merged={...c.chatPrefs,...o.chatPrefs};
          merged.orchPipelineNodes=mergeOrchPipelineNodes(o.chatPrefs.orchPipelineNodes||merged.orchPipelineNodes);
          c.chatPrefs=merged;
        }
      }
    }
    const loc=localStorage.getItem("sba_chat_local_v1");
    if(loc&&!c.msgs.length){
      const o=safeJsonParse(loc,null);
      if(o&&typeof o==='object'){
        if(o.sid)c.sid=o.sid;
        if(Array.isArray(o.msgs))c.msgs=o.msgs.map(normalizeChatMsg);
        if(o.curTask)c.curTask=normalizeCurTask(o.curTask);
        if(Array.isArray(o.mainTaskHistory)&&o.mainTaskHistory.length)c.mainTaskHistory=o.mainTaskHistory;
      }
    }
    if(c.sid&&c.sid!=="temp"&&c.msgs.length){
      if(!(c.mainTaskHistory||[]).length)rebuildMainTaskHistoryFromMsgs();
      else{
        try{
          const r=await fetch("/api/chat/sessions/"+encodeURIComponent(c.sid));
          if(r.ok){
            const d=await r.json();
            const srv=Array.isArray(d.main_task_history)?d.main_task_history:[];
            if(srv.length>(c.mainTaskHistory||[]).length)c.mainTaskHistory=srv;
            else if(!srv.length&&c.msgs.length)rebuildMainTaskHistoryFromMsgs();
            if(d.cur_task&&!c.curTask)c.curTask=normalizeCurTask(d.cur_task);
          }
        }catch(_){}
      }
    }
  }catch(err){
    console.error('[SBA chat local restore]',err);
    try{localStorage.removeItem("sba_chat_local_v1")}catch(_){}
  }
  loadPlatformHealth(false);
  kickoffChatWarmup();
  if(page.value==='chat')beginChatConnect();
  if(c.sid)prefetchChatSessionContext(c.sid);
  resetNavIslandTimer();
  bumpModalLayer();
  window.addEventListener("keydown",(ev)=>{
    if(ev.key!=="Escape")return;
    if(skillImport.show||kbImportMeta.show||kbBrowse.show||mmBrowse.show||modalOut.show||modalArtifact.show||showHist.value||chatExpandOpen.value||c.taskHistModalOpen){
      ev.preventDefault();
      closeAllPageOverlays();
    }
  });
  nextTick(()=>{
    bindNavIslandScroll(document.querySelector('.p-60'));
    const chatMsgsEl=document.querySelector('.chat-msgs');
    bindNavIslandScroll(chatMsgsEl);
    bindChatMsgsScrollState(chatMsgsEl);
    bindNavIslandScroll(document.querySelector('.chat-config-scroll'));
  });
});
return{page,menuMain,isAdmin,authUser,authDisplayName,authAvatarChar,userAvatarUrl,userAvatarInp,pickUserAvatar,onUserAvatarFile,uiPrefs,navTabCompact,navTabExpanded,onNavIslandEnter,onNavIslandLeave,onUiPrefsChange,persistUiPrefs,prof,portrait,goPersonalSettings,saveProfile,savePassword,saveUserPortrait,ldUserPortrait,closeUserDd,doLogout,wfs,navCollapsed,toggleNav,settingsOpen,onSettingsNavClick,openSettingsSec,webreplayOpen,onWebreplayNavClick,openWebreplaySec,wr,wrMcpSnippet,ldWrScripts,wrSelectScript,wrDeleteScript,wrExportAll,wrExportOne,wrImportFile,ldWrBridge,wrSaveBridge,wrCopyMcpSnippet,wrHost,wrFmtTime,wrStepKindLabel,wrStepDesc,appBreadcrumbs,goAppBreadcrumb,openTabs,appTabs,canCloseTab,switchPage,closeTab,closeOtherTabs,showTabContextMenu,
  v,vec,videoSubTab,subForm,subList,subSelId,subSelRow,subFmtTime,selectSubscription,subDigest,subProfile,subViewTab,ldSubscriptions,addSubscription,syncSubscription,syncAllSubscriptions,loadSubDigest,loadSubProfile,runCreatorProfile,pauseSubscription,resumeSubscription,deleteSubscription,renderSubDigestMd,taskQueue,sortTaskQueueFifo,pendingQueueIndex,isFirstPendingTask,isLastPendingTask,logFocusId,outDirInp,toast,modalOut,modalArtifact,logs,logRowClass,startProc,clrV,persistLinkPipelinePrefs,ldLinkPipelinePrefs,openOut,copyOutPath,saveServerOutPath,configureOutputFolder,onOutDirNative,shortLink,clampTaskText,histStatusLabel,histPipelineSteps,histFailedStageLabel,histResumeHint,copyHistLink,detectPlatform,taskContentKind,taskRouteLabel,taskRouteTagClass,taskCardLinkTitle,taskCardHeadTitle,taskCardSubTitle,taskCardMetricsLine,taskFeishuHint,taskCardDocSubTitle,taskCoverUrl,onTaskCoverError,histTaskTitle,histTaskSubTitle,histStatusStyle,taskHasMd,taskHasHtml,taskHtmlReady,taskHtmlPending,histHasMd,histHasHtml,openTaskMd,openTaskHtml,openTaskHtmlExplorer,openTaskArtifactsLocation,openArtifactModalExplorer,openHistMd,openHistHtml,openHistHtmlExplorer,openLocalOutput,copyArtifactItem,selectQueueTask,onLogFocusChange,moveQueueTask,cancelQueueTask,cleanupQueueTasks,taskQueueFmtTime,taskShowReadBadge,taskIsUnread,taskReadLabel,markQueueTaskRead,deleteQueueTask,
  showHist,openHistPanel,ht,hs,ldHist,restartTask,stopTask,moveTask,deleteTask,clearCompleted,regenerateHtml,
  histLogPanel,openHistLogs,closeHistLogPanel,histLogSourceLabel,
  ldOpSpans,ldOpSpanExceptions,ldOpSpansAll,opLoadSpanTask,opSpanStatusClass,opSpanMaxMs,opSpanBarPct,opFmtJson,opPickSpanStep,opSpanTypeLabel,
  o,ldNodes,skills,skillsFiltered,skillCmdDraft,saveSkillCommand,saveAllSkillCommands,importProjectSkillsBatch,retagAllSkillsBoard,sk,skillImport,openSkillImport,closeSkillImport,orchSkillAttachActive,skillAttachKindLabel,selectSkillAttachment,orchToolSearch,orchBoardTab,orchBoardByCategory,orchBoardFilteredItems,orchBoardTotalCount,orchBoardOpenItem,builtinTools,mcpDiscovered,mcpDiscoveredFiltered,mcpEnabledListFiltered,mcpByServer,mcpVendors,mcpMarketOpen,mcpEnabledList,mcpServerKeys,ldMcpVendors,insertMcpVendorMerge,addMcpFromMarket,openMcpServerConfig,saveMcpServerConfigFromRail,removeMcpServer,orchStage,orchRail,orchFlowDisplay,orchFlowPanStart,orchFlowPanMove,orchFlowPanEnd,resetOrchRailView,fitOrchFlowToViewport,Math,closeOrchRail,openOrchFullscreen,dockOrchFromFullscreen,selectOrchBuiltin,selectOrchMcpServer,selectOrchMcpTool,selectOrchSkill,openSkillDiff,onSkillVersionClick,clearSkillDiff,refreshSkillFlow,onOrchRailTabChange,onOrchDetailTabChange,orchRailTabIsFlow,diagramStyleFields,ldDiagramStyles,iagDiagramStyleKeys,iagDiagramStyleLabel,iagDiagramStyleHint,resetIagDiagramStyle,mcpConfigEditText,mcpFeishuForm,mcpDiscKey,orchToggle,isOrchOn,setOrchOn,orchTocActive,scrollOrchTo,orchDetailToc,orchDetailTocActive,scrollOrchDetailTo,orchDiffStats,orchDiffDisplay,skillAliasCn,mcpAliasCn,skillDescParts,skillCardSummary,skillCardTags,mcpSyncMsg,mcpJsonText,mcpPlaceholder,ldBuiltinTools,ldMcpCfg,saveMcpCfg,mcpSyncPull,ldSkills,importSkillForm,onSkillFile,onSkillFolder,delSkill,orchSubTabs,switchOrchSubTab,
  chatSbCollapsed,toggleChatSb,
  c,cs,filteredCs,chatSessionTitle,chatTopKpi,chatMainTaskHistory,taskHistDisplayCount,refreshChatSessionTaskHistory,chatConnectVisible,chatConnectClass,chatConnectLabel,taskRegistryKindLabel:taskRegistryKindLabel,taskRegistryKindClass:taskRegistryKindClass,openRegistryTask,openTaskDetailFromChat,openTaskHistModal,closeTaskHistModal,closeTaskHistModalBack,taskHistModalDetail,taskHistModalLoading,loadTaskHistDetail,taskHistDetailOf,taskHistDetailCounts,taskFieldLabel,taskFieldDisplayValue,taskStepTypeLabel,taskMetaLabel,taskHistDetailKey,preloadTaskHistDetails,chatCurrentSubtask,chatGroupedSubPlans,groupExecPlans,filterExecThinking,hasVisibleExecChain,execThinkingForMsg,showOrchestrationThink,formatOrchThinkDisplay,stripReactDisplayMarkers,hasStepIo,isRagDecisionStep,ragSliceParentName,extractRagSlicesFromStep,formatOrchStepInputDisplay,formatOrchStepOutputDisplay,ORCH_IO_PHASES,stepIsSkipped,pillStatusClass,execPillClass,execSubPlanTitle,formatToolPillPrimary,formatToolPillResult,mainTaskCardLabel,execCardLabel,activeTaskHistoryEntry,jumpToTaskResult,jumpToCurTaskResult,jumpToMsgIndex,resultJudgmentLabel,resultJudgmentClass,msgErrLabel,msgErrClass,parentStatusLabel,parentStatusClass,parentStatusTransitions,formatStepBrief,formatDuration,stepSuccessLabel,stepStatusIcoClass,stepConfidencePct,formatStepInputDisplay,formatStepOutputDisplay,isDocStepOutput,chatCtxPct,chatCtxPctLabel,switchChatPanel,orchPipelineNodes:ORCH_PIPELINE_NODE_DEFS,chatApplyTaskStatus,onTaskHistPick,toggleTaskHistMenu,toggleTaskStatusMenu,setCurrentMainTaskFromHistory,loadTaskRegistry,scheduleTaskRegistryReload,setTaskHistKindFilter,setTaskHistSort,syncTaskToMysql,chatCloseTask,chatTogglePause,hitlKindTitle,chatHitlConfirm,chatHitlPause,chatHitlReintent,chatHitlToolOption,chatPrimaryActionLabel,chatPrimaryActionDisabled,chatModels,chatAgents,customAgents,goAgentPersonalization,persistChatPrefs,newChatSess,delCs,renameCs,closeCs,exportCsMd,exportMsgMd,loadChatSession,toggleCsMenu,upImg,upFile,autoResize,onChatInput,chatKeydown,chatSend,toggleVoice,chatExpandOpen,renderMsg,renderWebSearchPanel,copyMsg,copyQueryToInput,loadPlatformHealth,goHealthSettings,regenerateAt,collectMsg,readMsg,slashOpen,slashItems,slashIdx,slashTotal,pickSlash,chatScrollAwayFromBottom,chatScrollBottomClick,
  apz,ldApzCatalog,selectApzTemplate,ldApzCurrent,ldApzHist,loadApzRevision,saveApzTemplate,newApzCustom,useApzInChat,deactivateApzCustom,
  kb,kbImportMeta,kbImportBtnLabel,kbMilvusStatusText,kbMilvusStatusColor,ldKbS,ldKbF,ldKbMetaOpts,ldKbConn,kbSetConnectionParams,kbProbeConnection,kbRetryConnection,kbConnFmtTs,kbResetConnection,kbToggleConnDetail,kbSyncChunkCounts,kbRestoreCatalog,kbRm,ldKbLibs,onKbLibChange,promptCreateKbLib,deleteKbLib,saveKbLibCfg,kbLoadRecallVocab,kbImportInterviewFolder,kbFolderInp,kbPickLocalFolder,onKbLocalFolderPick,openKbBrowse,kbBrowse,kbBrowseEnter,kbBrowseUp,kbImportSelectedFiles,kbImportFolderHere,kbConfirmImportWithMeta,kbOpenImportMeta,openKbFileDetail,closeKbFileDetail,kbAutoFillMeta,kbSaveFileMeta,kbRowPv,
  rss,rssFmtTime,rssFeedTitle,rssArticleTitle,ldRssAll,rssSelectFeed,rssSelectItem,rssOpenDoc,rssEnqueueDoc,rssAddFeed,rssDeleteFeed,rssSyncOne,rssSyncAll,rssToggleFilter,rssToggleRead,rssToggleStar,rssExportOpml,rssImportOpmlFile,rssTriggerOpmlImport,
  d,docProc,mm,mmBrowse,mmFileInp,mmPickLocal,openMmBrowse,mmBrowseEnter,mmBrowseUp,mmLoadBrowse,mmAddBrowsePicks,mmOnLocalPick,mmOnDrop,mmRmQueueSel,mmClearQueue,mmClearDocLog,
  ca,caQ,caSel,caPickRow,caSv,caEx,
  st,agtKeys,ldGw,ldAr,ldNf,testConn,ndUpSert,ndPoolSv,ndUp,ndDn,ndDel,rtSv,ldWf,svWf,ldMd,svMd,svFs,ldFsCfg,aicf,ldAiCfg,svAiCfg,thcf,ldThCfg,svThCfg,ldTpl,svTpl,ldHtmlCfg,svHtmlCfg,ldImPlatforms,openImPlatform,closeImDetail,imPlatformIcon,imPlatformBadge,imDetailTitle,ldImFeishu,ldImFeishuMsgs,imFsSave,imFsTime,imFsWebhookUrl,imFsCopyWebhook,
  INTERNAL_IAG_TABS,IAG_KEY_ORDER,IAG_SUMMARY_PREVIEW_VARS,IAG_COMMENTS_SECTION_VARS,iag,iagRawMode,ldIag,saveIag,iagLabel,iagHelpText,iagIsLongText,iagFieldKeys,iagGenericFieldKeys,iagTplPreviewMd,iagTplPreviewFn,iagCommentsSectionPreview,insertIagToken,
  op,ldOp,ldOpAg,ldOpHealth,opLoadReport,opAnalyzeLogs
};
}});
_sbaApp.config.errorHandler=function(err,inst,info){
  revealRuntimeFault('界面渲染异常',(err&&err.message||err)+' · '+String(info||''));
};
window.addEventListener('error',function(ev){
  if(ev&&ev.message)revealRuntimeFault('脚本运行错误',ev.message);
});
window.addEventListener('unhandledrejection',function(ev){
  const r=ev&&ev.reason;
  if(r&&r.name==='AbortError')return;
  revealRuntimeFault('未处理的 Promise 异常',r&&(r.message||r));
});
document.addEventListener('click',function(ev){
  const a=ev.target&&ev.target.closest&&ev.target.closest('a[href]');
  if(!a||a.dataset.mdPreview==='0')return;
  const href=(a.getAttribute('href')||'').trim();
  if(!href.startsWith('/output/'))return;
  const name=decodeURIComponent((href.split('/').pop()||'').split('?')[0]);
  if(!/\.(md|txt|markdown|mdx)$/i.test(name))return;
  ev.preventDefault();
  window.open('/preview/md.html?file='+encodeURIComponent(name),'_blank','noopener');
},true);
try{
  _sbaApp.mount('#app');
  window.__SBA_VUE_MOUNTED__=true;
}catch(e){
  console.error('Vue mount failed',e);
  revealBootError('<div style="padding:32px 20px;max-width:520px;margin:24px auto;font-family:system-ui,sans-serif;line-height:1.55;color:#1e293b;background:#f8fafc;border-radius:12px;border:1px solid #e2e8f0"><h2 style="font-size:18px;margin:0 0 12px">界面启动失败</h2><p style="margin:0;font-size:14px;color:#475569">Vue 挂载异常：'+String(e&&e.message||e)+'</p><p style="margin:12px 0 0;font-size:13px;color:#64748b">请打开浏览器控制台查看详情，或 <a href="/login.html">重新登录</a> 后刷新。</p></div>');
}
})();
