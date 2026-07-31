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
  hideBootLoading();
}
function hideBootLoading(){
  var ld=document.getElementById('sba-boot-loading');
  if(ld)ld.classList.add('sba-boot-done');
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

/** 未登录时跳转登录页（保留 next 回跳地址） */
function redirectToLogin(nextPath){
  if(typeof AuthManager!=='undefined'&&AuthManager.redirectToLogin){
    AuthManager.redirectToLogin(nextPath);
    return;
  }
  const next=nextPath||(window.location.pathname+window.location.search);
  const q=next&&next!=='/login.html'?'?next='+encodeURIComponent(next):'';
  window.location.replace('/login.html'+q);
}

let _runtimeFaultShown=false;
/** 扩展/Cursor 浏览器注入脚本报错，不应触发整页「界面渲染异常」遮罩 */
function isExternalInjectedError(evOrMsg,filename){
  const msg=String(typeof evOrMsg==='string'?evOrMsg:(evOrMsg&&evOrMsg.message)||'');
  const fn=String(filename!=null?filename:(evOrMsg&&evOrMsg.filename)||'');
  if(/chrome\.runtime\.|fetch-interceptor|extension:\/\//i.test(msg))return true;
  if(/\bgetURL\b/i.test(msg))return true;
  if(/chrome-extension:|moz-extension:|fetch-interceptor/i.test(fn))return true;
  if(fn&&!/^\s*$/.test(fn)){
    try{
      const u=new URL(fn,location.origin);
      if(u.origin!==location.origin&&!fn.includes('/assets/js/')&&!fn.includes('/assets/css/'))return true;
    }catch(_){
      if(fn.startsWith('http')&&!fn.includes(location.host))return true;
    }
  }
  return false;
}
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
  if(kind==="fleet")return"多 Agent";
  if(kind==="main")return"AI 主任务";
  return kind||"任务";
}
function taskRegistryKindClass(h){
  const kind=String(h&&h.task_kind||"main").toLowerCase();
  if(kind==="pipeline")return"kind-pipeline";
  if(kind==="fleet")return"kind-fleet";
  return"kind-main";
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
  react_round:"ReAct 轮次",sub_plan_id:"子计划 ID",sub_index:"步骤组序号",
  invoke_mode:"调用定语",invoke_purpose:"调用目的",sub_plan_groups:"步骤组",
  input_preview:"输入预览",output_preview:"输出预览",
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
const REQUIRES_AUTH_PAGES=['video','subscribe','sched','orch','chat','reader','tasks','fleet','agpz','rag','rss','multimodal','cache','ops','webreplay','profile','settings'];
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
      redirectToLogin('/'+toPage);
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
  {key:"reader",label:"文本阅读"},
  {key:"tasks",label:"任务中心"},{key:"fleet",label:"多 Agent"},
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

/* 手机竖屏：独立布局状态（不影响桌面/横屏） */
const mobilePortrait=ref(false);
const mobileNavOpen=ref(false);
const mobileAgpzStep=ref('tpl');
let _mobilePortraitMq=null;
const MOBILE_BOTTOM_KEYS=new Set(['chat','video','orch','tasks']);
const mobileBottomTabs=[
  {key:'chat',label:'问答',icon:'icon-chat'},
  {key:'video',label:'链接',icon:'icon-video'},
  {key:'orch',label:'工具',icon:'icon-orch'},
  {key:'tasks',label:'任务',icon:'icon-tasks'},
  {key:'_more',label:'更多',icon:''},
];
function bindMobilePortraitMq(){
  if(typeof window==='undefined'||!window.matchMedia)return;
  /* 竖屏：宽≤768 且 高≥宽；比 orientation 在部分浏览器/真机上更可靠 */
  _mobilePortraitMq=window.matchMedia('(max-width: 768px) and (max-aspect-ratio: 1/1)');
  const apply=()=>{
    const on=!!(_mobilePortraitMq&&_mobilePortraitMq.matches);
    mobilePortrait.value=on;
    if(!on){
      mobileNavOpen.value=false;
    }else if(page.value==='chat'){
      chatSbCollapsed.value=true;
    }
  };
  apply();
  if(_mobilePortraitMq.addEventListener)_mobilePortraitMq.addEventListener('change',apply);
  else if(_mobilePortraitMq.addListener)_mobilePortraitMq.addListener(apply);
}
bindMobilePortraitMq();
const MOBILE_PAGE_TITLES={
  video:'链接文档化',orch:'工具',chat:'AI 问答',reader:'文本阅读',
  tasks:'任务中心',fleet:'多 Agent 管理',agpz:'Agent 个性化',iag:'内部 Agent 配置',rag:'RAG 知识库',
  rss:'RSS 阅读',multimodal:'多模态文档',cache:'Redis 缓存',ops:'OPS 运维',
  subscribe:'订阅',sched:'定时任务',webreplay:'浏览器自动化',settings:'设置',
};
const mobilePageTitle=computed(()=>MOBILE_PAGE_TITLES[page.value]||'SuperBizAgent');
const mobileDrawerItems=computed(()=>{
  const items=[];
  const seen=new Set();
  (menuMain.value||[]).forEach(m=>{
    if(MOBILE_BOTTOM_KEYS.has(m.key))return;
    items.push({key:m.key,label:m.label});
    seen.add(m.key);
  });
  [{key:'subscribe',label:'订阅'},{key:'sched',label:'定时'},{key:'webreplay',label:'自动化'},{key:'settings',label:'设置'}].forEach(e=>{
    if(seen.has(e.key))return;
    items.push(e);
    seen.add(e.key);
  });
  return items;
});
function onMobileBottomTap(key){
  if(key==='_more'){mobileNavOpen.value=!mobileNavOpen.value;return;}
  mobileNavOpen.value=false;
  switchPage(key);
}
function onMobileDrawerTap(key){
  mobileNavOpen.value=false;
  switchPage(key);
}
function setMobileAgpzStep(step){
  if(step==='tpl'||step==='edit'||step==='hist')mobileAgpzStep.value=step;
}

const settingsOpen=ref((()=>{try{return localStorage.getItem("sba_settings_open")==="1"}catch(_){return false}})());
const webreplayOpen=ref((()=>{try{return localStorage.getItem("sba_webreplay_open")==="1"}catch(_){return true}})());
const subscribeOpen=ref((()=>{try{return localStorage.getItem("sba_subscribe_open")==="1"}catch(_){return true}})());
const schedOpen=ref((()=>{try{return localStorage.getItem("sba_sched_open")==="1"}catch(_){return true}})());
const subscribeXhsOpen=ref((()=>{try{return localStorage.getItem("sba_subscribe_xhs_open")==="1"}catch(_){return true}})());
const sub=reactive({
  sec:(()=>{try{const s=localStorage.getItem("sba_sub_sec");return s==="fav"?"fav":s==="bind"?"bind":"up"}catch(_){return "up"}})(),
});
const wr=reactive({
  sec:(()=>{try{return localStorage.getItem("sba_webreplay_sec")||"scripts"}catch(_){return "scripts"}})(),
  scripts:[],
  selId:"",
  selDetail:null,
  loading:false,
  err:"",
  replayBusy:false,
  bridge:{extensionId:"",origin:""},
  cdp:{
    connected:false,
    port:null,
    tabCount:0,
    tabs:[],
    tabHint:"",
    recName:"",
    sessionId:"",
    tabUrl:"",
    stepCount:0,
    frameCount:0,
    recording:false,
    busy:false,
    error:"",
    pollTimer:null,
  },
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
  syncActive:false,
  syncStage:"",
  syncDetail:"",
  syncSteps:[],
  syncItemProgress:null,
});
function rssSyncStepClass(st){
  const s=String(st||"").toLowerCase();
  if(s==="running"||s==="thinking")return"run";
  if(s==="failed"||s==="error")return"fail";
  if(s==="completed"||s==="done")return"ok";
  return"";
}
function rssSyncActionLabel(action){
  if(action==="linked")return"复用已有 MD";
  if(action==="stub")return"新建摘录 MD";
  if(action==="skip")return"已有文档";
  return action||"";
}
function rssIngestSyncSseEvent(ev,d){
  if(ev==="thought_step_start"){
    const sid=d.step_id||("");
    const row=rss.syncSteps.find(x=>x.step_id===sid);
    const stub={
      step_id:sid,
      step_name:d.step_name||"同步",
      status:d.status||"running",
      description:d.description||"",
      output_text:"",
    };
    if(row)Object.assign(row,stub);else rss.syncSteps.push(stub);
    rss.syncStage=d.step_name||"正在同步";
    rss.syncDetail=d.description||d.input_text||"";
    return;
  }
  if(ev==="thought_step_end"){
    const sid=d.step_id||"";
    const row=rss.syncSteps.find(x=>x.step_id===sid);
    if(row){
      row.status=d.status||"completed";
      row.output_text=d.output_text||"";
      if(d.description)row.description=d.description;
    }else{
      rss.syncSteps.push({
        step_id:sid,
        step_name:d.step_name||"同步",
        status:d.status||"completed",
        description:d.description||"",
        output_text:d.output_text||"",
      });
    }
    if(d.status==="failed"){
      rss.syncStage=(d.step_name||"同步")+"失败";
      rss.syncDetail=d.output_text||"";
    }else{
      rss.syncStage=d.step_name||"同步";
      rss.syncDetail=d.output_text||"";
    }
    return;
  }
  if(ev==="sync_item_progress"){
    rss.syncItemProgress={
      index:d.index||0,
      total:d.total||0,
      title:d.title||"",
      action:d.action||"",
      doc_filename:d.doc_filename||"",
    };
    rss.syncDetail="映射文档 "+(d.index||0)+"/"+(d.total||0)+"："+(d.title||"");
    return;
  }
  if(ev==="sync_complete"){
    rss.syncStage="同步完成";
    rss.syncDetail=d.item_count!=null?("共 "+d.item_count+" 篇文章"):(
      d.ok_count!=null?("成功 "+d.ok_count+" 个源，失败 "+(d.fail_count||0)+" 个"):""
    );
    return;
  }
  if(ev==="error"){
    throw new Error(d.error||"同步失败");
  }
}
async function runRssSyncSse(url){
  const headers={"Content-Type":"application/json",...authBearerHeaders()};
  const r=await fetch(url,{method:"POST",headers});
  if(!r.ok){
    const d=await parseApiJson(r).catch(()=>({}));
    throw new Error(d.detail||d.error||("同步失败 HTTP "+r.status));
  }
  if(!r.body)throw new Error("同步未返回流式进度");
  const reader=r.body.getReader();
  const decoder=new TextDecoder();
  let buf="";
  while(true){
    const{value,done}=await reader.read();
    if(done)break;
    buf+=decoder.decode(value,{stream:true});
    buf=parseSseBuffer(buf,(ev,d)=>{rssIngestSyncSseEvent(ev,d)});
  }
  if(buf.trim())parseSseBuffer(buf+"\n\n",(ev,d)=>{rssIngestSyncSseEvent(ev,d)});
}
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
  rss.busy=true;rss.syncActive=true;rss.err="";
  rss.syncSteps=[];rss.syncStage="准备同步";rss.syncDetail="";rss.syncItemProgress=null;
  try{
    await runRssSyncSse("/api/rss/feeds/"+encodeURIComponent(feedId)+"/sync/stream");
    await ldRssAll();
    showToastMsg("同步完成，本地文档已映射");
  }catch(e){rss.err=e.message||String(e);showToastMsg(rss.err)}finally{
    rss.busy=false;
    setTimeout(()=>{rss.syncActive=false;rss.syncItemProgress=null},1200);
  }
}
async function rssSyncAll(){
  if(rss.busy)return;
  rss.busy=true;rss.syncActive=true;rss.err="";
  rss.syncSteps=[];rss.syncStage="全部同步";rss.syncDetail="";rss.syncItemProgress=null;
  try{
    await runRssSyncSse("/api/rss/sync/stream");
    await ldRssAll();
    showToastMsg("全部订阅已同步");
  }catch(e){rss.err=e.message||String(e);showToastMsg(rss.err)}finally{
    rss.busy=false;
    setTimeout(()=>{rss.syncActive=false;rss.syncItemProgress=null},1200);
  }
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
    if(mobilePortrait.value&&!chatSbCollapsed.value)mobileNavOpen.value=false;
    nextTick(()=>{try{localStorage.setItem("sba_chat_sb_collapsed",chatSbCollapsed.value?"1":"0")}catch(_){}});
  });
}
const wfs=ref([]);async function ldWfs(){try{const r=await fetch('/api/workflow/selector');const d=await r.json();wfs.value=d.workflows||[];if(!v.wf&&wfs.value.length)v.wf=wfs.value[0].key}catch(e){}}

/* ══ P1 链接文档化 ══ */
const v=reactive({wf:"",link:"",fs:true,fp:"",html:true,submitting:false,submitPulse:false,pg:0,stage:"就绪",stxt:"就绪",sd:"i",qs:"0",pr:"",sp:false,lpAdv:false,rd:null,htmlStat:"",htmlMsg:"",subtitle:true,cookies:"",importance:5,taskNote:"",taskKeywords:"",taskMetaHintsJson:"",videoTranscriptMode:"audio_only",comments:{enabled:false,count:10,sort:"hot"}});
const linkMetaSchema=reactive({enabled:true,cardDisplay:false,fields:[],fieldsJson:"",prompt:""});
const LINK_META_FIELDS_EXAMPLE=JSON.stringify([
  {key:"domain",label:"领域",description:"文档所属业务领域（大粒度）",show_on_card:true},
  {key:"module",label:"模块",description:"所属功能模块（中粒度）",show_on_card:true},
  {key:"doc_type",label:"文档类型",description:"如产品手册/技术文档/FAQ/笔记",show_on_card:false},
  {key:"author_name",label:"作者",description:"博主昵称（智能提取后写入结构化 JSON）",show_on_card:true},
  {key:"keyword1",label:"关键词1",description:"核心主题词或实体",show_on_card:true},
  {key:"keyword2",label:"关键词2",description:"次要主题词或补充实体",show_on_card:true}
],null,2);
const linkMetaCardDisplayEnabled=computed(()=>!!linkMetaSchema.cardDisplay);
function _metaFieldShowOnCard(f){
  if(!f||typeof f!=="object")return false;
  if(f.show_on_card===false)return false;
  if(f.show_on_card===true)return true;
  const k=String(f.key||"");
  return k==="author_name"||k.startsWith("keyword")||["domain","module","doc_type"].includes(k);
}
function _formatMetaFieldValue(v){
  if(v==null||v==="")return"";
  if(Array.isArray(v))return v.map(x=>String(x)).filter(Boolean).join(", ");
  if(typeof v==="object"){try{return JSON.stringify(v)}catch(_){return""}}
  return String(v).trim();
}
function taskCardMetaValue(t,key){
  if(!t||!key)return"";
  const meta=(t.extracted_metadata&&typeof t.extracted_metadata==="object")?t.extracted_metadata:{};
  let v=meta[key];
  if(key==="author_name"&&!_formatMetaFieldValue(v))v=t.author_name;
  return _formatMetaFieldValue(v);
}
function taskCardMetaRows(t){
  if(!linkMetaSchema.cardDisplay)return[];
  const fields=Array.isArray(linkMetaSchema.fields)?linkMetaSchema.fields:[];
  const rows=[];
  for(const f of fields){
    if(!_metaFieldShowOnCard(f))continue;
    const key=String(f.key||"").trim();
    if(!key)continue;
    const display=taskCardMetaValue(t,key);
    if(!display)continue;
    rows.push({
      key,
      label:String(f.label||key),
      display,
      isAuthor:key==="author_name",
      profileUrl:key==="author_name"?taskAuthorProfileUrl(t):"",
    });
  }
  return rows;
}
function parseTaskMetaHintsJson(raw){
  const text=String(raw||"").trim();
  if(!text)return{};
  try{
    const obj=JSON.parse(text);
    if(obj&&typeof obj==="object"&&!Array.isArray(obj)){
      const out={};
      Object.keys(obj).forEach(k=>{
        const key=String(k||"").trim();
        if(!key)return;
        const val=obj[k];
        if(val==null)out[key]="";
        else if(typeof val==="object")out[key]=val;
        else out[key]=String(val).trim();
      });
      return out;
    }
  }catch(_){}
  const parts=text.split(/[,，\s]+/).map(s=>s.trim()).filter(Boolean);
  const hints={};
  parts.slice(0,8).forEach((p,i)=>{hints["keyword"+(i+1)]=p});
  return hints;
}
function refreshLinkMetaFieldsEdit(){
  const rows=Array.isArray(linkMetaSchema.fields)&&linkMetaSchema.fields.length?linkMetaSchema.fields:null;
  try{
    linkMetaSchema.fieldsJson=JSON.stringify(rows||JSON.parse(LINK_META_FIELDS_EXAMPLE),null,2);
  }catch(_){
    linkMetaSchema.fieldsJson=LINK_META_FIELDS_EXAMPLE;
  }
}
async function saveLinkMetaSettings(){
  let fields;
  try{
    fields=JSON.parse(linkMetaSchema.fieldsJson||"[]");
    if(!Array.isArray(fields))throw new Error("须为 JSON 数组");
    fields.forEach(row=>{
      if(!row||typeof row!=="object"||!String(row.key||"").trim())
        throw new Error("每项须含完整 key 字段名");
    });
  }catch(e){showToastMsg("字段 JSON 无效："+(e.message||String(e)));return}
  try{
    await fetch("/api/settings/link-pipeline-prefs",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({
        meta_card_display_enabled:!!linkMetaSchema.cardDisplay,
        meta_extract_enabled:!!linkMetaSchema.enabled,
        meta_extract_fields:fields,
        meta_extract_prompt:String(linkMetaSchema.prompt||""),
      }),
    });
    linkMetaSchema.fields=fields;
    showToastMsg("结构化元数据设置已保存");
  }catch(e){showToastMsg("保存失败："+(e.message||String(e)))}
}
function resetLinkMetaFieldsExample(){
  linkMetaSchema.fields=JSON.parse(LINK_META_FIELDS_EXAMPLE);
  refreshLinkMetaFieldsEdit();
  showToastMsg("已载入示例字段（保存后生效）");
}
async function linkApplyKbMetaSchema(){
  try{
    const lib=(kb&&kb.activeLib)?String(kb.activeLib):"";
    const url="/api/settings/meta-extract-schema"+(lib?("?lib="+encodeURIComponent(lib)):"");
    const d=await fetchJsonSafe(url);
    linkMetaSchema.fields=d.fields||[];
    refreshLinkMetaFieldsEdit();
    showToastMsg("已同步知识库 metadata 字段");
  }catch(e){showToastMsg("同步失败："+(e.message||String(e)))}
}
const videoSubTab=ref("single"); // 保留兼容；UP/收藏夹已迁至 subscribe 页
const subForm=reactive({profile_url:"",display_name:"",submitting:false,error:""});
const subList=ref([]);
const subSelId=ref("");
const subDigest=reactive({digest_md:"",rag_degraded:false,digest_id:""});
const subProfile=reactive({
  hasData:false,busy:false,
  profile_md:"",profile_md_path:"",profile_doc_id:"",profile_run_id:"",
  display_name:"",red_id:"",creator_id:"",llm_model:"",
  industry:"",domain:"",niche:"",persona_summary:"",target_audience:"",content_style:"",
  deep_directions:[],recent_topics:[],selected_notes:[],sampled_articles:[],
  content_types:[],output_themes:[],output_formats:[],cadence_hint:"",freshness:"",
  title_topic_buckets:[],recent_direction_shift:"",evidence_notes:[],
  collaboration_scenarios:[],confidence:"",open_questions:[],
  run_status:"",catalog_count:0,selected_count:0,deep_ok_count:0,deep_fail_count:0,
  finished_at:"",error_code:"",error_message:""
});
const subProfileViewMode=ref("summary");
const subViewTab=ref("digest");
const subBlogNotes=reactive({loading:false,items:[],total:0,error:"",seedBusy:false});
const favForm=reactive({syncing:false,cookieSyncing:false,error:"",syncBatchSize:""});
const favProgress=reactive({phase:"idle",level:"info",message:"",step:0,total:0});
const favUpForm=reactive({pulling:false,loading:false,subscribing:false,profiling:false,removing:false,error:"",hint:"",query:"",subscribed:"all",expandedTerms:[],persistedTotal:0});
const favUpList=ref([]);
const favUpSelId=ref("");
const upSelId=ref("");
const favCatalogSeqMap=ref({});
function _upRowSortTs(u){
  if(!u)return 0;
  const pick=(u.last_pulled_at||u.updated_at||u.last_fetch_at||u.created_at||"").trim();
  const t=Date.parse(pick);
  return Number.isFinite(t)?t:0;
}
function _cacheCatalogSeq(items){
  const m={};
  for(const c of (items||[])){
    const nid=(c.note_id||"").trim();
    if(nid&&c.seq)m[nid]=Number(c.seq);
  }
  favCatalogSeqMap.value=m;
}
function _attachCatalogSeqToCards(cards){
  const seqMap=favCatalogSeqMap.value||{};
  const page=Math.max(1,Number(subLinkPaging.page)||1);
  const ps=Math.max(1,Number(subLinkPaging.page_size)||20);
  return (cards||[]).map((c,i)=>{
    const nid=(c.note_id||"").trim();
    const favSeq=seqMap[nid];
    const seq=favSeq||((page-1)*ps+i+1);
    return {...c,seq,seq_source:favSeq?"favorite":"page"};
  });
}
function favCardSeqTitle(c){
  if(!c||!c.seq)return"";
  if(c.seq_source==="favorite")return"小红书收藏夹顺序第 "+c.seq+" 条（与 App 收藏序一致）";
  return"本页第 "+c.seq+" 条（按入库时间分页，非收藏夹全局序）";
}
function favCardSeqText(c){
  if(!c||!c.seq)return"";
  return c.seq_source==="favorite"?("收藏#"+c.seq):("页内#"+c.seq);
}
const favUpSelRow=computed(()=>(favUpList.value||[]).find(u=>u.creator_id===favUpSelId.value)||null);
const upUnifiedList=computed(()=>{
  const subs=subList.value||[];
  const follows=favUpList.value||[];
  const byId=new Map();
  for(const s of subs){
    const cid=(s.creator_id||"").trim();
    if(!cid)continue;
    byId.set(cid,{...s,creator_id:cid,already_subscribed:true,source:"subscription",note_count:0,sample_titles:[]});
  }
  for(const f of follows){
    const cid=(f.creator_id||"").trim();
    if(!cid)continue;
    if(byId.has(cid)){
      const row=byId.get(cid);
      row.note_count=f.note_count||row.note_count||0;
      row.sample_titles=f.sample_titles||row.sample_titles||[];
      if(f.has_profile)row.has_profile=true;
      row.already_subscribed=true;
    }else{
      byId.set(cid,{...f,creator_id:cid,already_subscribed:!!f.already_subscribed,source:"follow_up"});
    }
  }
  return [...byId.values()].sort((a,b)=>{
    if(!!a.already_subscribed!==!!b.already_subscribed)return a.already_subscribed?-1:1;
    const ta=_upRowSortTs(a),tb=_upRowSortTs(b);
    if(ta!==tb)return tb-ta;
    return String(a.display_name||a.creator_id).localeCompare(String(b.display_name||b.creator_id),"zh-CN");
  });
});
const favSub=reactive({subscription_id:"",display_name:"",status:"",red_id:"",initial_backfill_done:false,cursor_offset:0});
const favSession=reactive({chrome_profile_ok:false,chrome_gaia:"",xhs_nickname:"",xhs_owner_ok:false,cdp_ready:false,cookie_logged_in:false,prefer_cookie_fetch:false,fetch_mode:"none",expected_gaia:"",login_hint:""});
const favDigest=reactive({digest_md:"",rag_degraded:false,digest_id:""});
const favHabit=reactive({top_authors:[],interest_topics:[],preferred_content_types:[],persona_md:"",total_analyzed:0});
const favCards=ref([]);
const favDetail=reactive({open:false,fullscreen:false,card:null});
const favDetailCard=computed(()=>{
  const fallback=favDetail.card||{};
  const noteId=String(fallback.note_id||"").trim();
  if(!noteId)return fallback;
  return (favCards.value||[]).find(row=>String(row.note_id||"").trim()===noteId)||fallback;
});
const subLinkCards=ref([]);
const subLinkPaging=reactive({page:1,page_size:20,total:0,view:"grid",loading:false,subscription_id:""});
const favSyncReport=reactive({hasRun:false,run:null,items:[],summary:{},digest_md:"",loading:false});
let _favSyncPollTimer=null;
function _stopFavSyncPoll(){
  if(_favSyncPollTimer){clearInterval(_favSyncPollTimer);_favSyncPollTimer=null;}
}
function _startFavSyncPoll(){
  _stopFavSyncPoll();
  _favSyncPollTimer=setInterval(async()=>{
    if(!favForm.syncing)return;
    try{
      const syncR=await fetch("/api/favorites/sync/latest",{headers:authBearerHeaders()});
      if(!syncR.ok)return;
      const d=await syncR.json();
      _applyFavSyncPayload(d);
      const st=(d.run&&d.run.status)||"";
      if(/completed|partial|failed/.test(st)){
        _setFavProgress(st==="failed"?"error":"ok",`同步 ${st} · 已分析 ${d.run.analyzed_count??0} · 失败 ${d.run.failed_count??0}`,"sync_favorites",4,4);
      }else if(st==="analyzing"){
        _setFavProgress("running",`步骤 3/4：分析收藏卡片任务中…（${d.items&&d.items.filter(x=>x.analysis_status==="completed").length||0}/${d.items&&d.items.length||0}）`,"sync_favorites",3,4);
      }
    }catch(ex){console.error("_favSyncPoll",ex);}
  },2500);
}
function resetSubProfile(){
  Object.assign(subProfile,{
    hasData:false,busy:false,
    profile_md:"",profile_md_path:"",profile_doc_id:"",profile_run_id:"",
    display_name:"",red_id:"",creator_id:"",llm_model:"",
    industry:"",domain:"",niche:"",persona_summary:"",target_audience:"",content_style:"",
    deep_directions:[],recent_topics:[],selected_notes:[],sampled_articles:[],
    content_types:[],output_themes:[],output_formats:[],cadence_hint:"",freshness:"",
    title_topic_buckets:[],recent_direction_shift:"",evidence_notes:[],
    collaboration_scenarios:[],confidence:"",open_questions:[],
    run_status:"",catalog_count:0,selected_count:0,deep_ok_count:0,deep_fail_count:0,
    finished_at:"",error_code:"",error_message:""
  });
}
function applySubProfileFromApi(d){
  const doc=(d&&d.profile_doc)||{};
  const run=(d&&d.latest_run)||{};
  const profileJson=(doc.profile_json&&typeof doc.profile_json==="object")?doc.profile_json:{};
  const light=(profileJson.light_profile&&typeof profileJson.light_profile==="object")
    ?profileJson.light_profile:((run.light_profile_json&&typeof run.light_profile_json==="object")?run.light_profile_json:{});
  const output=(doc.output_analysis&&typeof doc.output_analysis==="object")
    ?doc.output_analysis:((profileJson.output_analysis&&typeof profileJson.output_analysis==="object")?profileJson.output_analysis:{});
  const distribution=(doc.content_type_distribution&&typeof doc.content_type_distribution==="object")
    ?doc.content_type_distribution:((light.content_type_distribution&&typeof light.content_type_distribution==="object")?light.content_type_distribution:{});
  const typeLabels={video:"视频",graphic:"图文",image:"图文",text:"文字",other:"其他"};
  const contentTypes=Object.entries(distribution)
    .map(([key,value])=>({key,label:typeLabels[String(key).toLowerCase()]||String(key),count:Number(value)||0}))
    .filter(row=>row.count>0);
  const dirs=Array.isArray(doc.deep_directions)?doc.deep_directions:(doc.deep_directions?[doc.deep_directions]:[]);
  const notes=Array.isArray(doc.selected_notes)?doc.selected_notes:[];
  const sampled=Array.isArray(doc.sampled_articles)?doc.sampled_articles:(doc.profile_json&&doc.profile_json.sampled_articles)||[];
  const byId={};
  for(const a of sampled){if(a&&a.note_id)byId[String(a.note_id)]=a;}
  const mergedNotes=notes.map(n=>{
    const ex=byId[String(n.note_id||"")]||{};
    const charLen=ex.char_len!=null?ex.char_len:n.char_len;
    const fetchOk=ex.fetch_ok!=null?ex.fetch_ok:n.fetch_ok;
    return {...n,...ex,char_len:charLen,fetch_ok:fetchOk===false?false:(fetchOk===true?true:!(charLen>0&&charLen<400))};
  });
  const topics=Array.isArray(doc.recent_topics)?doc.recent_topics:[];
  Object.assign(subProfile,{
    hasData:!!(doc.profile_md||doc.persona_summary||doc.industry),
    profile_md:doc.profile_md||"",
    profile_md_path:doc.profile_md_path||"",
    profile_doc_id:doc.profile_doc_id||"",
    profile_run_id:doc.profile_run_id||run.profile_run_id||"",
    display_name:doc.display_name||"",
    red_id:doc.red_id||"",
    creator_id:doc.creator_id||"",
    llm_model:doc.llm_model||run.llm_model||"",
    industry:doc.industry||"",
    domain:doc.domain||"",
    niche:doc.niche||"",
    persona_summary:doc.persona_summary||"",
    target_audience:doc.target_audience||"",
    content_style:doc.content_style||"",
    deep_directions:dirs.filter(Boolean),
    recent_topics:topics.filter(Boolean),
    selected_notes:mergedNotes,
    sampled_articles:sampled,
    content_types:contentTypes,
    output_themes:Array.isArray(output.themes)?output.themes.filter(Boolean):[],
    output_formats:Array.isArray(output.formats)?output.formats.filter(Boolean):[],
    cadence_hint:String(output.cadence_hint||"").trim(),
    freshness:String(output.freshness||"").trim(),
    title_topic_buckets:Array.isArray(light.title_topic_buckets)?light.title_topic_buckets.filter(x=>x&&x.topic):[],
    recent_direction_shift:String(profileJson.recent_direction_shift||"").trim(),
    evidence_notes:Array.isArray(profileJson.evidence_notes)?profileJson.evidence_notes.filter(Boolean):[],
    collaboration_scenarios:Array.isArray(profileJson.collaboration_scenarios)?profileJson.collaboration_scenarios.filter(Boolean):[],
    confidence:String(profileJson.confidence||"").trim(),
    open_questions:Array.isArray(profileJson.open_questions)?profileJson.open_questions.filter(Boolean):[],
    run_status:run.status||"",
    catalog_count:Number(run.catalog_count)||0,
    selected_count:Number(run.selected_count)||0,
    deep_ok_count:Number(run.deep_ok_count)||0,
    deep_fail_count:Number(run.deep_fail_count)||0,
    finished_at:run.finished_at||doc.created_at||"",
    error_code:run.error_code||"",
    error_message:run.error_message||""
  });
}
function subProfileRunLabel(){
  const s=subProfile.run_status||"";
  if(s==="completed")return"已完成";
  if(s==="partial")return"部分完成";
  if(s==="failed")return"失败";
  if(s==="running")return"运行中";
  return s||"—";
}
function subProfileRunClass(){
  const s=subProfile.run_status||"";
  if(s==="completed")return"ok";
  if(s==="partial")return"warn";
  if(s==="failed")return"err";
  if(s==="running")return"run";
  return"";
}
const subSelRow=computed(()=>(subList.value||[]).find(s=>s.subscription_id===subSelId.value)||null);
function subFmtTime(iso){
  if(!iso)return"";
  try{
    const d=new Date(iso);
    if(Number.isNaN(d.getTime()))return String(iso).slice(0,16);
    return d.toLocaleString("zh-CN",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"});
  }catch(_){return String(iso).slice(0,16)}
}
function upAvatarText(row){
  const name=String((row&&(row.display_name||row.author_name))||"").trim();
  if(!name)return"UP";
  const chars=Array.from(name.replace(/^[@＠]/,""));
  return (chars[0]||"UP").toUpperCase();
}
function upPublicId(row){
  if(!row)return"";
  const direct=String(row.red_id||"").trim();
  if(direct)return direct;
  const tag=(row.tags||[]).find(t=>String(t).startsWith("red_id:"));
  return tag?String(tag).replace(/^red_id:/,"").trim():"";
}
function upStatusLabel(row){
  if(!row||!row.already_subscribed)return"收藏候选";
  const status=String(row.status||"active").toLowerCase();
  if(status==="paused")return"已暂停";
  if(status==="active")return"订阅中";
  if(status==="error"||status==="failed")return"同步异常";
  return"已订阅";
}
function upStatusClass(row){
  if(!row||!row.already_subscribed)return"candidate";
  const status=String(row.status||"active").toLowerCase();
  if(status==="paused")return"paused";
  if(status==="error"||status==="failed")return"error";
  return"active";
}
function upSourceLabel(row){
  if(!row)return"";
  if(row.source==="follow_up"||!row.already_subscribed)return"来自收藏作者";
  return"来自作者订阅";
}
async function selectSubscription(id){
  subSelId.value=id;
  const row=(subList.value||[]).find(s=>s.subscription_id===id);
  upSelId.value=(row&&row.creator_id)||"";
  favUpSelId.value=upSelId.value;
  const loads=[loadSubDigest(id),loadSubProfile(id),loadSubBlogNotes(id)];
  if(subViewTab.value==="links")loads.push(loadSubLinkCards(id));
  await Promise.all(loads);
  if(subProfile.hasData)subViewTab.value="profile";
}
async function selectUpCard(u){
  if(!u||!u.creator_id)return;
  upSelId.value=u.creator_id;
  favUpSelId.value=u.creator_id;
  if(u.already_subscribed&&u.subscription_id){
    await selectSubscription(u.subscription_id);
  }else{
    subSelId.value="";
    resetSubProfile();
    subDigest.digest_md="";
  }
}
async function ldUpPage(){
  await Promise.all([ldSubscriptions(),loadFollowUps()]);
}
async function ldSubscriptions(){
  subForm.error="";
  try{
    const r=await fetch("/api/subscriptions",{headers:authBearerHeaders()});
    if(!r.ok){const e=await r.json().catch(()=>({}));subForm.error=(e.detail&&e.detail.message)||e.detail||("HTTP "+r.status);return;}
    const d=await r.json();
    subList.value=(d.items||[]).filter(s=>s.platform==="xiaohongshu");
  }catch(e){subForm.error=String(e);}
}
async function addSubscription(){
  subForm.error="";subForm.submitting=true;
  try{
    const r=await fetch("/api/subscriptions",{method:"POST",headers:authJsonHeaders(),body:JSON.stringify({platform:"xiaohongshu",profile_url:subForm.profile_url.trim(),display_name:(subForm.display_name||"").trim()})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok){
      const code=(d.detail&&d.detail.error_code)||d.error_code||"";
      const msg=(d.detail&&d.detail.message)||d.detail||("HTTP "+r.status);
      if(code==="SUB_DUPLICATE"||r.status===409)showToastMsg("该博主已订阅，无需重复添加");
      else subForm.error=msg;
      return;
    }
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
    const skipped=Number(d.skipped_imported_count||0);
    let msg="同步完成："+(d.status||"");
    if(skipped>0)msg+=" · 跳过已导入 "+skipped+" 篇（历史库/url_hash 判重）";
    else if(Number(d.new_count||0)===0&&Number(d.analyzed_count||0)===0)msg+=" · 无新增待分析内容";
    showToastMsg(msg);
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
async function loadSubBlogNotes(subscriptionId){
  if(!subscriptionId){subBlogNotes.items=[];subBlogNotes.total=0;return;}
  subBlogNotes.loading=true;subBlogNotes.error="";
  try{
    const r=await fetch("/api/subscriptions/"+encodeURIComponent(subscriptionId)+"/blog-notes?page_size=200",{headers:authBearerHeaders()});
    const d=await r.json().catch(()=>({}));
    if(!r.ok||d.ok===false){subBlogNotes.error=(d.detail&&d.detail.message)||d.error||("HTTP "+r.status);subBlogNotes.items=[];return;}
    subBlogNotes.items=Array.isArray(d.items)?d.items:[];
    subBlogNotes.total=d.total!=null?d.total:subBlogNotes.items.length;
  }catch(e){subBlogNotes.error=String(e);}finally{subBlogNotes.loading=false;}
}
async function seedSubCatalog(limit,enqueue){
  const sid=(subSelId.value||"").trim();
  if(!sid){showToastMsg("请先选择已订阅 UP");return;}
  subBlogNotes.seedBusy=true;subBlogNotes.error="";
  try{
    const r=await fetch("/api/subscriptions/"+encodeURIComponent(sid)+"/catalog/seed",{
      method:"POST",headers:authJsonHeaders(),
      body:JSON.stringify({limit:Math.max(1,Math.min(Number(limit)||20,200)),enqueue:!!enqueue}),
    });
    const d=await r.json().catch(()=>({}));
    if(!r.ok||d.ok===false){
      subBlogNotes.error=(d.detail&&d.detail.message)||d.error||"摘录失败";
      return;
    }
    showToastMsg("已摘录 "+(d.seed_count||0)+" 条链接"+(d.enqueued_count?" · 入队 "+d.enqueued_count:""));
    subViewTab.value="blog";
    await loadSubBlogNotes(sid);
  }catch(e){subBlogNotes.error=String(e);}finally{subBlogNotes.seedBusy=false;}
}
async function repairSubCatalogLinks(){
  const sid=(subSelId.value||"").trim();
  if(!sid){showToastMsg("请先选择已订阅 UP");return;}
  subBlogNotes.seedBusy=true;subBlogNotes.error="";
  try{
    const r=await fetch("/api/subscriptions/"+encodeURIComponent(sid)+"/catalog/repair-links",{
      method:"POST",headers:authJsonHeaders(),
    });
    const d=await r.json().catch(()=>({}));
    if(!r.ok||d.ok===false){
      subBlogNotes.error=(d.detail&&d.detail.message)||d.error||"修复失败";
      return;
    }
    showToastMsg("裸链 "+(d.bare_total||0)+" 条 · 已补 token "+(d.repaired||0)+(d.tasks_updated?" · 同步任务 "+d.tasks_updated:"")+(d.still_bare?" · 仍缺 "+d.still_bare:""));
    await loadSubBlogNotes(sid);
  }catch(e){subBlogNotes.error=String(e);}finally{subBlogNotes.seedBusy=false;}
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
  if(!subscriptionId){resetSubProfile();return;}
  try{
    const r=await fetch("/api/subscriptions/"+encodeURIComponent(subscriptionId)+"/profile/latest",{headers:authBearerHeaders()});
    if(!r.ok){resetSubProfile();return;}
    const d=await r.json();
    applySubProfileFromApi(d);
  }catch(e){console.error(e);resetSubProfile();}
}
async function runCreatorProfile(subscriptionId){
  if(subProfile.busy)return;
  subProfile.busy=true;subViewTab.value="profile";subProfileViewMode.value="summary";
  try{
    showToastMsg("UP 画像流水线已启动（五阶段）…");
    const r=await fetch("/api/subscriptions/"+encodeURIComponent(subscriptionId)+"/profile/run",{method:"POST",headers:authBearerHeaders()});
    const d=await r.json();
    if(!r.ok){alert((d.detail&&d.detail.message)||d.error||d.detail||"画像失败");return;}
    showToastMsg("画像完成：目录"+(d.catalog_count||"?")+"篇 · 深度"+(d.deep_ok_count||"?")+"/"+(d.selected_count||"?"));
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
  const sid=String(id||"").trim();
  if(!sid){showToastMsg("订阅 ID 无效");return;}
  if(!confirm("确定删除该订阅？\n删除后若仍在收藏博主列表中，会显示为「候选」而非已订阅。"))return;
  try{
    const r=await fetch("/api/subscriptions/"+encodeURIComponent(sid),{method:"DELETE",headers:authBearerHeaders()});
    const d=await r.json().catch(()=>({}));
    if(!r.ok){
      const msg=(d.detail&&d.detail.message)||(typeof d.detail==="string"?d.detail:"")||("HTTP "+r.status);
      showToastMsg("删除失败："+msg);
      return;
    }
    if(subSelId.value===sid){
      subSelId.value="";
      subLinkCards.value=[];
      subLinkPaging.total=0;
      resetSubProfile();
      subDigest.digest_md="";
    }
    await ldSubscriptions();
    await loadFollowUps();
    showToastMsg("订阅已删除");
  }catch(e){showToastMsg("删除失败："+String(e));}
}
function _setFavProgress(level,message,phase,step=0,total=0){
  favProgress.level=level||"info";
  favProgress.message=message||"";
  favProgress.phase=phase||"idle";
  favProgress.step=step||0;
  favProgress.total=total||0;
}
function _applyFavPayload(d){
  const sess=d.owner_session||{};
  const cdiag=d.cookie_diagnosis||{};
  Object.assign(favSession,{
    chrome_profile_ok:!!sess.chrome_profile_ok,
    chrome_gaia:sess.chrome_gaia||"",
    xhs_nickname:sess.xhs_nickname||"",
    xhs_owner_ok:!!sess.xhs_owner_ok,
    cdp_ready:!!sess.cdp_ready,
    cookie_logged_in:!!sess.cookie_logged_in,
    prefer_cookie_fetch:!!sess.prefer_cookie_fetch,
    fetch_mode:sess.fetch_mode||"none",
    expected_gaia:sess.expected_gaia||"",
    login_hint:""
  });
  favForm.error=d.subscription_error||"";
  if(favForm.syncing||favForm.cookieSyncing)return;
  const nick=sess.xhs_nickname||"已配置账号";
  const gaia=sess.chrome_gaia||"已配置用户";
  if(sess.xhs_owner_ok&&sess.cookie_logged_in){
    _setFavProgress("ok",`小红书「${nick}」已登录 · Cookie 模式 · Chrome「${gaia}」`,"ready",4,4);
    return;
  }
  if(cdiag.cdp_blocked_default_profile){
    _setFavProgress("error",cdiag.hint||"Chrome 149 须用「Google Chrome CDP 9223」快捷方式","need_cdp",1,4);
    return;
  }
  if(!cdiag.cdp_port&&!cdiag.chrome_running){
    _setFavProgress("error","请先双击桌面「Google Chrome CDP 9223」启动 Chrome","need_cdp",1,4);
    return;
  }
  if(cdiag.cdp_port&&!cdiag.logged_in){
    _setFavProgress("warn",cdiag.hint||"CDP 已就绪，请点击「从 Chrome 同步 Cookie」","need_cookie_sync",2,4);
    return;
  }
  if(cdiag.guest||!sess.cookie_logged_in){
    _setFavProgress("warn","浏览器可能已登录，但后端 Cookie 未就绪 · 请点击「从 Chrome 同步 Cookie」","need_cookie_sync",2,4);
    return;
  }
  if(!sess.xhs_owner_ok){
    _setFavProgress("error",sess.login_hint||"请在 CDP Chrome 登录配置的小红书账号","need_xhs_login",3,4);
    return;
  }
  _setFavProgress("info",sess.login_hint||"检测完成","checking",0,4);
}
function subLinkArtifactClass(st){
  const s=(st||"").toLowerCase();
  if(s==="ready")return"btn-artifact-ready";
  if(s==="running"||s==="failed")return"btn-artifact-off";
  return"btn-artifact-off";
}
function subLinkArtifactLabel(kind,st){
  const s=(st||"").toLowerCase();
  if(s==="ready")return kind.toUpperCase();
  if(s==="running")return kind+"…";
  if(s==="failed")return kind+"!";
  return kind;
}
let _subLinkLoadSeq=0;
async function loadSubscriptionLinkCards(subscriptionId,opts={}){
  const sid=String(subscriptionId||"").trim();
  if(!sid)return;
  const page=Math.max(1,Number(opts.page||subLinkPaging.page)||1);
  const pageSize=Math.min(100,Math.max(1,Number(opts.page_size||subLinkPaging.page_size)||20));
  const target=opts.target==="up"?subLinkCards:favCards;
  const seq=++_subLinkLoadSeq;
  subLinkPaging.loading=true;
  subLinkPaging.subscription_id=sid;
  try{
    const r=await fetch(`/api/subscriptions/${encodeURIComponent(sid)}/link-cards?page=${page}&page_size=${pageSize}`,{headers:authBearerHeaders()});
    const d=await r.json().catch(()=>({}));
    if(seq!==_subLinkLoadSeq)return;
    if(!r.ok||!d.ok){
      console.warn("link-cards",d);
      showToastMsg((d.detail&&d.detail.message)||d.error||"链接卡片加载失败");
      return;
    }
    target.value=_attachCatalogSeqToCards(d.items||[]);
    if(target===favCards&&favDetail.open){
      const selectedId=String((favDetail.card||{}).note_id||"").trim();
      if(selectedId&&!target.value.some(row=>String(row.note_id||"").trim()===selectedId))closeFavDetail();
    }
    subLinkPaging.page=d.page||page;
    subLinkPaging.page_size=d.page_size||pageSize;
    subLinkPaging.total=d.total||target.value.length;
  }catch(e){
    if(seq===_subLinkLoadSeq)console.error("loadSubscriptionLinkCards",e);
  }finally{
    if(seq===_subLinkLoadSeq)subLinkPaging.loading=false;
  }
}
async function loadSubLinkCards(id){
  const sid=String(id||subSelId.value||"").trim();
  if(!sid)return;
  await loadSubscriptionLinkCards(sid,{target:"up"});
}
async function openSubscriptionLinkTask(c){
  if(!c)return;
  switchPage("video");
  await pollQueue();
  const task=findQueueTaskForLinkCard(c);
  if(!task){
    showToastMsg("暂无关联任务，请先同步分析");
    return;
  }
  ensureQueueTaskVisible(task.task_id);
  selectQueueTask(task.task_id);
  scrollToQueueTask(task.task_id);
}
function setSubLinkPageSize(n){
  const v=Math.min(100,Math.max(5,Number(n)||20));
  subLinkPaging.page_size=v;
  try{localStorage.setItem("sba_sub_link_page_size",String(v));}catch(_){}
  const tgt=subLinkPaging.subscription_id===subSelId.value?"up":"fav";
  loadSubscriptionLinkCards(subLinkPaging.subscription_id,{page:1,page_size:v,target:tgt});
}
function setSubLinkViewMode(mode){
  subLinkPaging.view=(mode==="row")?"row":"grid";
  try{localStorage.setItem("sba_sub_link_view",subLinkPaging.view);}catch(_){}
}
function subLinkPagePrev(){
  if(subLinkPaging.page<=1)return;
  const tgt=subLinkPaging.subscription_id===subSelId.value?"up":"fav";
  loadSubscriptionLinkCards(subLinkPaging.subscription_id,{page:subLinkPaging.page-1,target:tgt});
}
function subLinkPageNext(){
  const max=Math.max(1,Math.ceil((subLinkPaging.total||0)/(subLinkPaging.page_size||20)));
  if(subLinkPaging.page>=max)return;
  const tgt=subLinkPaging.subscription_id===subSelId.value?"up":"fav";
  loadSubscriptionLinkCards(subLinkPaging.subscription_id,{page:subLinkPaging.page+1,target:tgt});
}
function favCardClass(c){
  const st=(c.analysis_status||c.task_status||"").toLowerCase();
  if(st==="running"||st==="pending"||st==="started")return"q-card--running";
  if(st==="completed")return"q-card--completed";
  if(st==="failed"||st==="cancelled")return"q-card--abnormal";
  if(st==="already_imported"||st==="skipped"||st==="baseline")return"q-card--pending";
  return"";
}
function taskSourceLabel(t){
  if(!t)return"";
  const lbl=String(t.source_label||"").trim();
  if(lbl)return lbl;
  const src=String(t.import_source||"").trim();
  const map={
    manual:"导入链接",
    subscription_creator:"自动订阅博主",
    subscription_favorites:"自动订阅收藏夹",
    chat:"对话自动导入",
    catalog_seed:"博主目录摘录",
    rss:"RSS 订阅",
    link_scan:"链接扫描",
    other:"其他来源",
  };
  return map[src]||src||"";
}
function taskActionLabel(t){
  if(!t)return"链接导入";
  const src=String(t.import_source||"").trim();
  const map={
    manual:"链接导入",
    subscription_creator:"订阅博主",
    subscription_favorites:"订阅收藏夹",
    chat:"对话导入",
    catalog_seed:"目录摘录",
    rss:"RSS订阅",
    link_scan:"链接扫描",
    other:"链接导入",
  };
  return map[src]||"链接导入";
}
function taskAuthorName(t){
  if(!t)return"";
  const meta=t.extracted_metadata;
  if(meta&&typeof meta==="object"){
    const fromMeta=String(meta.author_name||"").trim();
    if(fromMeta)return fromMeta;
  }
  return String(t.author_name||"").trim();
}
function taskAuthorProfileUrl(t){
  if(!t)return"";
  const u=String(t.author_profile_url||"").trim();
  if(u)return u;
  const plat=taskCardPlatform(t);
  const aid=String(t.author_id||"").trim();
  const link=String(t.link||"").trim();
  if(plat==="小红书"&&aid&&/^[0-9a-fA-F]{16,32}$/.test(aid))return"https://www.xiaohongshu.com/user/profile/"+aid;
  if(plat==="抖音"&&aid&&(aid.startsWith("MS4w")||aid.length>=20||/^\d+$/.test(aid)))return"https://www.douyin.com/user/"+aid;
  if(plat==="B站"&&aid&&/^\d+$/.test(aid))return"https://space.bilibili.com/"+aid;
  if((plat==="微信"||plat==="微信公众号")&&link.includes("mp.weixin.qq.com")){
    const m=link.match(/[?&]__biz=([^&#]+)/i);
    if(m&&m[1])return"https://mp.weixin.qq.com/mp/profile_ext?action=home&__biz="+encodeURIComponent(m[1])+"#wechat_redirect";
  }
  return"";
}
function taskOpsReportId(t){
  if(!t)return"";
  return String(t.ops_report_id||"").trim();
}
function taskCardPlatform(t){
  return String((t&&t.platform)||detectPlatform(t&&t.link)||"").trim();
}
function taskCardPureTitle(t){
  if(!t)return"";
  const lt=String(t.link_title||"").trim();
  let title="";
  if(lt&&!isJunkTaskTitle(lt))title=clampTaskText(lt,72);
  else{
    const dt=String(t.doc_title||"").trim();
    if(dt&&!isJunkTaskTitle(dt))title=clampTaskText(dt,72);
    else{
      const tt=String(t.title||"").trim();
      if(tt&&!isJunkTaskTitle(tt))title=clampTaskText(tt,72);
      else title=((t.task_id||t.id||"")+"").slice(0,8);
    }
  }
  const plat=taskCardPlatform(t);
  if(plat){
    const suf="·"+plat;
    if(title.endsWith(suf))title=title.slice(0,-suf.length).trim();
    const suf2="-"+plat;
    if(title.endsWith(suf2))title=title.slice(0,-suf2.length).trim();
  }
  return title;
}
function linkCardSourceLine(c){
  const src=String((c&&c.source_label)||"").trim();
  const author=String((c&&c.author_name)||"").trim();
  const parts=[];
  if(src)parts.push("来源："+src);
  if(author)parts.push("作者："+author);
  return parts.join(" · ");
}
function linkCardPublishedLine(c){
  if(!c)return"";
  const pd=String(c.published_date||"").trim();
  if(pd)return"发布 "+pd;
  const pa=String(c.published_at||"").trim();
  if(pa)return"发布 "+subFmtTime(pa);
  return"";
}
function linkCardAsTask(c){
  if(!c)return null;
  const docPath=String(c.doc_path||"").trim();
  const htmlPath=String(c.html_path||"").trim();
  const htmlSt=String(c.html_status||"").toLowerCase();
  return{
    task_id:String(c.task_id||"").trim(),
    doc_path:docPath,
    doc_filename:pathBasename(docPath),
    html_path:htmlPath,
    html_status:htmlSt==="ready"?"completed":htmlSt,
  };
}
function linkCardHasMd(c){
  if(!c)return false;
  if(String(c.doc_path||"").trim())return true;
  return String(c.md_status||"").toLowerCase()==="ready";
}
function linkCardHasHtml(c){
  if(!c)return false;
  if(String(c.html_path||"").trim())return true;
  return String(c.html_status||"").toLowerCase()==="ready";
}
function openLinkCardMd(c){
  const t=linkCardAsTask(c);
  if(!t||!linkCardHasMd(c)){showToastMsg("MD 尚未生成");return}
  return openTaskMd(t);
}
async function onLinkCardHtmlClick(c){
  const t=linkCardAsTask(c);
  if(!t){showToastMsg("尚无 MD，无法生成 HTML");return}
  return onTaskHtmlClick(t);
}
function openLinkCardFeishu(c){
  const url=String((c&&c.feishu_doc_url)||"").trim();
  if(!url){showToastMsg("飞书文档尚未生成");return}
  window.open(url,"_blank","noopener");
}
function findQueueTaskForLinkCard(c){
  if(!c)return null;
  const tid=String(c.task_id||"").trim();
  const rows=taskQueue.value||[];
  if(tid){
    const hit=rows.find(t=>t.task_id===tid);
    if(hit)return hit;
  }
  const uh=String(c.url_hash||"").trim();
  const link=String(c.canonical_url||"").trim().toLowerCase();
  if(!uh&&!link)return null;
  return rows.find(t=>{
    if(uh&&String(t.url_hash||"").trim()===uh)return true;
    const tl=String(t.link||"").trim().toLowerCase();
    const nl=String(t.normalized_link||"").trim().toLowerCase();
    if(!link)return false;
    return tl===link||nl===link||tl.includes(link)||link.includes(tl);
  })||null;
}
function ensureQueueTaskVisible(tid){
  const all=taskQueue.value||[];
  if(!all.some(t=>t.task_id===tid))return false;
  let rows=filteredTaskQueue.value||[];
  if(!rows.some(t=>t.task_id===tid)){
    taskQueueFilter.query="";
    taskQueueFilter.authorPicks={};
    taskQueueFilter.conditions=[];
    rows=filteredTaskQueue.value||[];
  }
  const idx=rows.findIndex(t=>t.task_id===tid);
  if(idx<0)return false;
  if(taskQueueViewMode.value!=="expanded"){
    const ps=Math.max(1,Number(taskQueuePaging.page_size)||12);
    taskQueuePaging.page=Math.floor(idx/ps)+1;
  }
  return true;
}
function scrollToQueueTask(tid){
  const id=String(tid||"").trim();
  if(!id)return;
  nextTick(()=>{
    setTimeout(()=>{
      const el=document.getElementById("queue-task-"+id);
      if(!el)return;
      el.scrollIntoView({behavior:"smooth",block:"center"});
      el.classList.add("q-card-scroll-flash");
      setTimeout(()=>el.classList.remove("q-card-scroll-flash"),2200);
    },100);
  });
}
function favCardMetaLine(c){
  const parts=[];
  const published=favCardPublishedText(c);
  if(published)parts.push(published);
  if(c.like_count!=null)parts.push("点赞 "+favFormatCount(c.like_count));
  if(c.comment_count!=null)parts.push("评论 "+favFormatCount(c.comment_count));
  return parts.join(" · ");
}
function favFormatCount(value){
  const n=Math.max(0,Number(value)||0);
  if(n>=100000000)return (n/100000000).toFixed(n>=1000000000?0:1).replace(/\.0$/,"")+"亿";
  if(n>=10000)return (n/10000).toFixed(n>=100000?0:1).replace(/\.0$/,"")+"万";
  return String(Math.round(n));
}
function favCardPublishedText(c){
  if(!c)return"";
  const pd=String(c.published_date||"").trim();
  if(pd)return"发布 "+pd;
  const pa=String(c.published_at||"").trim();
  return pa?("发布 "+subFmtTime(pa)):"";
}
function favCardCollectedText(c){
  if(!c)return"";
  const collected=String(c.collected_at||"").trim();
  if(!collected)return"";
  const published=String(c.published_at||"").trim();
  if(published&&collected===published)return"";
  return"收藏 "+subFmtTime(collected);
}
function favCardFollowersText(c){
  const count=Number(c&&c.author_followers)||0;
  return count>0?("粉丝 "+favFormatCount(count)):"";
}
function favCardSourceText(c){
  const source=String((c&&c.fetch_source)||"").trim().toLowerCase();
  if(!source)return"";
  if(source==="collect_page"||source.startsWith("collect_page_"))return"页面响应";
  if(/cdp|playwright|headless|dom|state|meta|scrape/.test(source))return"页面解析";
  return"收藏目录";
}
function favCardSourceClass(c){
  return favCardSourceText(c)==="页面响应"?"fav-note-source-badge--capture":"";
}
function openFavDetail(c){
  if(!c)return;
  favDetail.card={...c};
  favDetail.open=true;
}
function closeFavDetail(){
  favDetail.open=false;
  favDetail.fullscreen=false;
  favDetail.card=null;
  document.body.classList.remove("fav-detail-fullscreen-active");
}
function toggleFavDetailFullscreen(){
  if(!favDetail.open)return;
  favDetail.fullscreen=!favDetail.fullscreen;
  document.body.classList.toggle("fav-detail-fullscreen-active",favDetail.fullscreen);
}
async function openFavTaskFromDetail(){
  const card={...(favDetailCard.value||{})};
  closeFavDetail();
  await openSubscriptionLinkTask(card);
}
function favDetailDisplay(value,fallback="暂无"){
  if(value===null||value===undefined||value==="")return fallback;
  return String(value);
}
function favCardKeywords(c){
  const kws=Array.isArray(c.hashtags)?c.hashtags:[];
  return kws.filter(Boolean).slice(0,6);
}
function favCardKeywordsLine(c){
  const kws=favCardKeywords(c);
  return kws.length?kws.map(k=>"#"+k).join(" · "):"";
}
function favCardStatusText(c){
  const st=(c.analysis_status||c.task_status||"").toLowerCase();
  const map={pending:"待分析",running:"分析中",started:"分析中",completed:"已完成",failed:"失败",cancelled:"已取消",skipped:"已跳过",already_imported:"已导入",baseline:"基线记录"};
  if(st&&map[st])return map[st];
  if(c.task_status)return c.task_status;
  return"未分析";
}
function favCardStatusColor(c){
  const cls=favCardClass(c);
  if(cls==="q-card--completed")return"var(--ok)";
  if(cls==="q-card--running")return"#2563eb";
  if(cls==="q-card--abnormal")return"var(--err)";
  if(cls==="q-card--pending")return"var(--warn)";
  return"var(--t2)";
}
function onFavCoverError(ev){
  const target=ev&&ev.currentTarget;
  const wrap=target&&target.closest?target.closest(".fav-note-cover-wrap"):null;
  if(wrap)wrap.style.display="none";
}
function _mergeFavCardsWithCatalog(cards,catalog){
  const metaById={};
  for(const item of (catalog||[])){
    const nid=String(item&&item.note_id||"").trim();
    if(nid)metaById[nid]=item;
  }
  const base=(cards||[]).length?(cards||[]):(catalog||[]);
  return base.map(card=>{
    const nid=String(card&&card.note_id||"").trim();
    const meta=metaById[nid];
    if(!meta)return card;
    const merged={...meta,...card};
    if(meta.canonical_url&&(!card.canonical_url||("xsec_token" in meta.canonical_url&&!("xsec_token" in card.canonical_url))))merged.canonical_url=meta.canonical_url;
    for(const key of ["title","content_type","published_at","published_date","author_id","author_name","cover_url","collected_at","fetch_source","note_url"]){
      if(!merged[key]&&meta[key])merged[key]=meta[key];
    }
    if(meta.like_count!=null)merged.like_count=meta.like_count;
    if(meta.comment_count!=null)merged.comment_count=meta.comment_count;
    if(Number(meta.author_followers)>0)merged.author_followers=meta.author_followers;
    if(Array.isArray(meta.hashtags)&&meta.hashtags.length)merged.hashtags=meta.hashtags;
    return merged;
  });
}
function _mergeFavCardsWithSync(cards,syncItems){
  const byId={};
  for(const it of (syncItems||[]))byId[it.note_id]=it;
  return (cards||[]).map(c=>{
    const s=byId[c.note_id];
    if(!s)return c;
    return {...c,...s,title:c.title||s.title,author_name:c.author_name||s.author_name};
  });
}
function _applyFavSyncPayload(d){
  if(!d)return;
  const run=d.run||null;
  favSyncReport.hasRun=!!run;
  favSyncReport.run=run;
  favSyncReport.items=d.items||[];
  favSyncReport.summary=d.summary||{};
  favSyncReport.digest_md=(d.digest&&d.digest.digest_md)||d.digest_md||"";
  if((d.items||[]).length){
    favCards.value=_attachCatalogSeqToCards(_mergeFavCardsWithSync(favCards.value,d.items));
  }
  if(d.digest){
    favDigest.digest_md=d.digest.digest_md||"";
    favDigest.rag_degraded=!!d.digest.rag_degraded;
    favDigest.digest_id=d.digest.digest_id||"";
  }
}
async function loadFavBoard(limit=20){
  const lim=Math.min(Math.max(Number(limit)||20,1),80);
  try{subLinkPaging.page_size=Number(localStorage.getItem("sba_sub_link_page_size")||subLinkPaging.page_size)||20;}catch(_){}
  try{subLinkPaging.view=localStorage.getItem("sba_sub_link_view")||subLinkPaging.view;}catch(_){}
  if(!favCards.value.length)favSyncReport.loading=true;
  try{
    const [catR,syncR,subR]=await Promise.all([
      fetch("/api/favorites/catalog?limit="+lim,{headers:authBearerHeaders()}),
      fetch("/api/favorites/sync/latest",{headers:authBearerHeaders()}),
      fetch("/api/favorites/subscription",{headers:authBearerHeaders()}),
    ]);
    const cat=catR.ok?await catR.json():{items:[],error:"catalog_failed"};
    const sync=syncR.ok?await syncR.json():{items:[]};
    const subD=subR.ok?await subR.json():{};
    _cacheCatalogSeq(cat.items||[]);
    const subId=(subD.subscription&&subD.subscription.subscription_id)||favSub.subscription_id||"";
    if(subId){
      favSub.subscription_id=subId;
      await loadSubscriptionLinkCards(subId,{page:subLinkPaging.page,page_size:subLinkPaging.page_size,target:"fav"});
    }
    favCards.value=_attachCatalogSeqToCards(_mergeFavCardsWithCatalog(favCards.value,cat.items||[]));
    _applyFavSyncPayload(sync);
    favCards.value=_attachCatalogSeqToCards(favCards.value);
    if(favCards.value.length&&!favForm.syncing){
      _setFavProgress("ok",`已加载订阅链接 ${favCards.value.length} 篇（Redis 卡片 · 最新优先）`,"ready",4,4);
    }
  }catch(e){
    console.error("loadFavBoard",e);
    _setFavProgress("error","加载收藏卡片失败："+String(e),"error");
  }finally{favSyncReport.loading=false;}
}
async function ldXhsBinding(){
  favForm.error="";
  _setFavProgress("running","正在检测 Chrome / CDP / Cookie 状态…","checking",0,4);
  try{
    const r=await fetch("/api/favorites/subscription",{headers:authBearerHeaders()});
    const d=await r.json().catch(()=>({}));
    if(!r.ok){
      favForm.error=(d.detail&&d.detail.message)||d.detail||(d.subscription_error)||("HTTP "+r.status);
      if(d.owner_session)_applyFavPayload(d);
      else _setFavProgress("error",favForm.error,"error");
      return;
    }
    _applyFavPayload(d);
  }catch(e){
    favForm.error=String(e);
    _setFavProgress("error",String(e),"error");
  }
}
async function ldFavorites(){
  favForm.error="";
  await loadFavBoard(20);
  try{
    const r=await fetch("/api/favorites/subscription",{headers:authBearerHeaders()});
    const ct=(r.headers.get("content-type")||"").toLowerCase();
    const raw=await r.text();
    if((!ct.includes("json"))&&(raw.trim().startsWith("<")||raw.includes("<!DOCTYPE"))){
      favForm.error="API 返回了 HTML 页面（非 JSON）。请重启后端服务以加载 /api/favorites 路由。";
      return;
    }
    let d={};
    try{d=JSON.parse(raw);}catch(parseEx){
      favForm.error="响应解析失败: "+parseEx.message;
      return;
    }
    if(!r.ok){
      favForm.error=(d.detail&&d.detail.message)||d.detail||(d.subscription_error)||("HTTP "+r.status);
      if(d.owner_session)_applyFavPayload(d);
      else _setFavProgress("error",favForm.error,"error");
      return;
    }
    const sub=d.subscription||{};
    Object.assign(favSub,{
      subscription_id:sub.subscription_id||"",
      display_name:sub.display_name||"",
      status:sub.status||"",
      initial_backfill_done:!!sub.initial_backfill_done,
      cursor_offset:Number(sub.cursor_offset||0),
      red_id:((sub.tags||[]).find(t=>String(t).startsWith("red_id:"))||"").replace(/^red_id:/,"")
    });
    const habit=(d.habit&&d.habit.habit_json)||{};
    Object.assign(favHabit,{
      top_authors:habit.top_authors||[],
      interest_topics:habit.interest_topics||[],
      preferred_content_types:habit.preferred_content_types||[],
      persona_md:(d.habit&&d.habit.persona_md)||"",
      total_analyzed:habit.total_analyzed||0
    });
    const dig=d.latest_digest||{};
    favDigest.digest_md=dig.digest_md||"";
    favDigest.rag_degraded=!!dig.rag_degraded;
    favDigest.digest_id=dig.digest_id||"";
    _applyFavPayload(d);
  }catch(e){
    favForm.error=String(e);
    _setFavProgress("error",String(e),"error");
  }
}
function _fmtFavSyncErr(d){
  const msg=(d&&(d.detail&&(d.detail.message||d.detail.error)||d.error||d.detail)||"").toString();
  if(/SUB_XHS_GUEST|访客态/i.test(msg))return msg+"（Chrome 149 须用「Google Chrome CDP 9223」快捷方式，不能用普通 Chrome）";
  if(/SUB_OWNER_CDP|CDP 未就绪|CHROME_CDP_BROKEN/i.test(msg))return msg+"（请完全退出 Chrome，双击桌面「Google Chrome CDP 9223」再试）";
  if(/SUB_OWNER_XHS_LOGIN|Cookie 未处于登录/i.test(msg))return msg+"（请在本机 Chrome 登录配置的小红书账号后重试）";
  return msg;
}
/** AI 问答工具失败 SSE：规范 CDP/Cookie/red_id 错误文案 */
function formatChatToolFailBrief(code,msg){
  const c=String(code||"").trim();
  const m=String(msg||"").trim();
  if(c==="SUB_XHS_CDP_REQUIRED")return "CDP 未就绪：请用「Google Chrome CDP 9223」打开已登录小红书页面";
  if(c==="SUB_XHS_CDP_SEARCH_FAILED")return "CDP 已连接但搜索未命中："+(m.slice(0,120)||"请打开含 red_id 的搜索结果 Tab");
  if(c==="SUB_RED_ID_NOT_FOUND")return "未找到对应小红书用户："+(m.slice(0,120)||"请核对 red_id 或提供 profile 链接");
  if(c==="SUB_XHS_COOKIE_UNAVAILABLE")return "HTTP Cookie 未就绪（CDP 可用时可忽略）："+(m.slice(0,100)||"可点 sync_xhs_cookies");
  if(c==="SUB_FETCH_AUTH_FAILED")return "HTTP 通道无登录 Cookie："+(m.slice(0,100)||"优先使用 CDP 浏览器");
  if(c)return c+(m?(" · "+m.slice(0,140)):"");
  return m.slice(0,200)||"工具执行失败";
}
async function refreshFavoritesCookies(){
  favForm.cookieSyncing=true;favForm.error="";
  _setFavProgress("running","步骤 1/3：连接 CDP Chrome (9223)…","sync_cookie",1,3);
  try{
    showToastMsg("正在从 Chrome 同步 Cookie…");
    _setFavProgress("running","步骤 2/3：从浏览器 Tab 读取登录 Cookie…","sync_cookie",2,3);
    const r=await fetch("/api/favorites/refresh-cookies",{method:"POST",headers:authBearerHeaders()});
    const d=await r.json();
    if(!d.ok){
      _applyFavPayload(d);
      _setFavProgress("error",(d.error||d.detail||"Cookie 同步失败")+(d.cookie_diagnosis&&d.cookie_diagnosis.hint?(" · "+d.cookie_diagnosis.hint):""),"sync_cookie",2,3);
      return false;
    }
    _setFavProgress("running","步骤 3/3：写入后端 Cookie 文件…","sync_cookie",3,3);
    showToastMsg("Cookie 已同步："+(d.nickname||"已登录"));
    await ldFavorites();
    return true;
  }catch(e){
    favForm.error=String(e);
    _setFavProgress("error",String(e),"sync_cookie");
    return false;
  }finally{favForm.cookieSyncing=false;}
}
function _favSyncResultMsg(d){
  const skipped=Number((d&&d.skipped_imported_count)||0);
  let msg="收藏同步完成："+((d&&d.status)||"");
  if(d&&d.cursor_offset!=null&&!d.initial_backfill_done)msg+=" · cursor "+d.cursor_offset;
  if(skipped>0)msg+=" · 跳过已导入 "+skipped+" 篇（url_hash 判重）";
  else msg+=" 新增 "+((d&&d.new_count)!=null?d.new_count:"?");
  return msg;
}
async function _afterFavSyncDone(d){
  if(d&&d.items&&d.items.length){
    favCards.value=_mergeFavCardsWithSync(favCards.value,d.items);
    favSyncReport.hasRun=true;
    favSyncReport.run={sync_run_id:d.sync_run_id,status:d.status,new_count:d.new_count,analyzed_count:d.analyzed_count,failed_count:d.failed_count};
    favSyncReport.items=d.items;
    if(d.summary)favSyncReport.summary=d.summary;
    if(d.digest_md)favSyncReport.digest_md=d.digest_md;
  }
  await loadFavBoard(20);
  try{
    const syncR=await fetch("/api/favorites/sync/latest",{headers:authBearerHeaders()});
    if(syncR.ok)_applyFavSyncPayload(await syncR.json());
  }catch(ex){console.error("_afterFavSyncDone",ex);}
}
async function syncFavorites(){
  favForm.syncing=true;favForm.error="";
  const raw=favForm.syncBatchSize;
  const batch=(raw===""||raw==null||Number(raw)<=0)?0:Math.min(Math.floor(Number(raw)),80);
  _setFavProgress("running","步骤 1/4：加载收藏卡片…","sync_favorites",1,4);
  await loadFavBoard(batch>0?Math.max(batch,20):80);
  _startFavSyncPoll();
  try{
    showToastMsg(batch>0?"收藏夹：每批 "+batch+" 篇同步…":"收藏夹：全量同步…");
    _setFavProgress("running","步骤 2/4：拉取收藏并启动分析…","sync_favorites",2,4);
    const r=await fetch("/api/favorites/sync",{method:"POST",headers:authJsonHeaders(),body:JSON.stringify({sync_batch_size:batch})});
    const d=await r.json();
    if(!r.ok){
      const err=_fmtFavSyncErr(d)||"同步失败";
      if(/SUB_XHS_GUEST|访客态/i.test(err)&&!favSession.cookie_logged_in){
        _setFavProgress("warn","Cookie 未就绪，请至「小红书绑定」页同步 Cookie","sync_favorites",2,4);
        favForm.error=err+" · 请打开「订阅 → 小红书 → 小红书绑定」同步 Cookie";
        return;
      }
      favForm.error=err;
      _setFavProgress("error",err,"sync_favorites",2,4);
      return;
    }
    if(d.cursor_offset!=null)favSub.cursor_offset=Number(d.cursor_offset)||0;
    _setFavProgress("running","步骤 4/4：同步完成，刷新状态…","sync_favorites",4,4);
    showToastMsg(_favSyncResultMsg(d));
    await _afterFavSyncDone(d);
    await ldFavorites();
  }catch(e){
    favForm.error=String(e);
    _setFavProgress("error",String(e),"sync_favorites");
  }finally{
    _stopFavSyncPoll();
    favForm.syncing=false;
  }
}
function _fmtFavUpApiErr(d,r){
  const det=d&&d.detail;
  if(det&&typeof det==="object")return det.message||det.error_code||JSON.stringify(det);
  return det||("HTTP "+r.status);
}
function _followUpQueryParams(){
  const p=new URLSearchParams();
  const q=(favUpForm.query||"").trim();
  if(q)p.set("q",q);
  if(favUpForm.subscribed&&favUpForm.subscribed!=="all")p.set("subscribed",favUpForm.subscribed);
  p.set("page_size","500");
  return p.toString();
}
async function loadFollowUps(){
  favUpForm.loading=true;favUpForm.error="";
  try{
    const qs=_followUpQueryParams();
    const r=await fetch("/api/follow-ups"+(qs?"?"+qs:""),{headers:authBearerHeaders()});
    const d=await r.json().catch(()=>({}));
    if(!r.ok){favUpForm.error=_fmtFavUpApiErr(d,r);return;}
    favUpList.value=Array.isArray(d.items)?d.items:[];
    favUpForm.expandedTerms=Array.isArray(d.expanded_terms)?d.expanded_terms:[];
    const total=d.total!=null?d.total:favUpList.value.length;
    favUpForm.persistedTotal=total;
    const shown=favUpList.value.length;
    const filterNote=(favUpForm.subscribed&&favUpForm.subscribed!=="all")?(" · 筛选="+(favUpForm.subscribed==="yes"?"已订阅":"未订阅")):"";
    const queryNote=(favUpForm.query||"").trim()?" · 关键词筛选":"";
    favUpForm.hint="MariaDB 持久化 "+total+" 位 · 当前显示 "+shown+" 位（按最近拉取/同步时间倒序）"+filterNote+queryNote;
    if(total>shown)favUpForm.hint+=" · 仍有 "+(total-shown)+" 位未加载，请缩小筛选或联系管理员调高 page_size";
  }catch(e){favUpForm.error=String(e);}finally{favUpForm.loading=false;}
}
async function pullFollowUps(reset){
  favUpForm.pulling=true;favUpForm.error="";favUpForm.hint="";
  try{
    const qs=new URLSearchParams({limit:"20",fast:"0"});
    if(reset)qs.set("reset","1");
    const r=await fetch("/api/follow-ups/pull?"+qs.toString(),{method:"POST",headers:authBearerHeaders()});
    const d=await r.json().catch(()=>({}));
    if(!r.ok||d.ok===false){
      favUpForm.error=_fmtFavUpApiErr(d,r)||d.error||"拉取失败";
      return;
    }
    const pull=d.pull||{};
    const scanned=pull.notes_scanned||0;
    const pulled=pull.authors_pulled||0;
    const created=pull.created||0;
    const updated=pull.updated||0;
    const off=pull.note_offset??0;
    const next=pull.next_note_offset??off;
    const total=pull.catalog_total??"?";
    favUpForm.hint="收藏笔记 #"+off+"→#"+next+"（共 "+total+" 条）· 本批扫描 "+scanned+" 条 · 新增博主 "+pulled+"（入库 +"+created+" · 更新 "+updated+"）"+(pull.source?" · "+pull.source:"")+(pull.pull_done?" · 已扫完":"");
    if(!pulled&&!pull.pull_done)favUpForm.error="本批无新博主（可能均已入库）。可再点一次继续向后扫描，或先同步收藏夹。";
    await loadFollowUps();
  }catch(e){favUpForm.error=String(e);}finally{favUpForm.pulling=false;}
}
async function subscribeFollowUp(row,syncAfter){
  if(!row||!row.creator_id||row.already_subscribed)return;
  favUpForm.subscribing=true;favUpForm.error="";
  try{
    const r=await fetch("/api/follow-ups/"+encodeURIComponent(row.creator_id)+"/subscribe",{
      method:"POST",headers:authJsonHeaders(),
      body:JSON.stringify({sync_after:!!syncAfter}),
    });
    const d=await r.json().catch(()=>({}));
    if(!r.ok){favUpForm.error=_fmtFavUpApiErr(d,r);return;}
    showToastMsg(d.already_subscribed?"已在订阅列表":"已加入订阅"+(syncAfter?"并同步":""));
    await ldUpPage();
    const sid=(d.subscription_id||"").trim();
    if(sid)subSelId.value=sid;
  }catch(e){favUpForm.error=String(e);}finally{favUpForm.subscribing=false;}
}
async function profileFollowUp(row){
  if(!row||!row.creator_id)return;
  favUpForm.profiling=true;favUpForm.error="";
  try{
    showToastMsg("UP 画像流水线已启动…");
    const r=await fetch("/api/follow-ups/"+encodeURIComponent(row.creator_id)+"/profile",{method:"POST",headers:authBearerHeaders()});
    const d=await r.json().catch(()=>({}));
    if(!r.ok){favUpForm.error=_fmtFavUpApiErr(d,r);return;}
    const pr=d.profile||{};
    showToastMsg("画像完成：目录"+(pr.catalog_count||"?")+"篇");
    await ldUpPage();
    const sid=(d.subscription_id||"").trim();
    if(sid){
      subSelId.value=sid;
      subViewTab.value="profile";
      await loadSubProfile(sid);
    }
  }catch(e){favUpForm.error=String(e);}finally{favUpForm.profiling=false;}
}
async function removeFollowUp(row){
  if(!row||!row.creator_id)return;
  if(!confirm("从关注列表移出「"+(row.display_name||row.creator_id)+"」？（不影响已有订阅）"))return;
  favUpForm.removing=true;favUpForm.error="";
  try{
    const r=await fetch("/api/follow-ups/"+encodeURIComponent(row.creator_id),{method:"DELETE",headers:authBearerHeaders()});
    const d=await r.json().catch(()=>({}));
    if(!r.ok){favUpForm.error=_fmtFavUpApiErr(d,r);return;}
    if(favUpSelId.value===row.creator_id)favUpSelId.value="";
    if(upSelId.value===row.creator_id)upSelId.value="";
    await ldUpPage();
    showToastMsg("已移出关注列表");
  }catch(e){favUpForm.error=String(e);}finally{favUpForm.removing=false;}
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
const taskQueueFilter=reactive({
  query:"",
  expandedTerms:[],
  aiExpandedTerms:[],
  aiSearchPowered:false,
  aiSearchLoading:false,
  aiSearchStatus:"idle",
  aiSearchMessage:"",
  aiSearchReady:false,
  aiMatchedIds:{},
  aiGrepSummary:"",
  aiIntentHint:"",
  useAiSearch:false,
  authorPicks:{},
  authorExpandAll:false,
  sort:"default",
  readStatus:"all",
  enableTitle:true,
  enableAuthor:false,
  enableRead:false,
  enableSource:false,
  advancedOpen:false,
  advancedLogic:"and",
  conditions:[],
  sources:{
    manual:true,
    subscription_creator:true,
    subscription_favorites:true,
    chat:true,
    catalog_seed:true,
    rss:true,
    link_scan:true,
    other:true,
  },
});
const TASK_QUEUE_RECENT_KEY="sba_task_queue_recent_searches";
const TASK_QUEUE_TAGS_KEY="sba_task_queue_search_tags";
const TASK_QUEUE_COND_FIELDS=[
  {id:"title",label:"标题"},
  {id:"author",label:"作者"},
  {id:"source",label:"导入渠道"},
  {id:"read",label:"已读状态"},
];
const TASK_QUEUE_COND_MODES={
  title:[
    {id:"synonym",label:"近义词包含"},
    {id:"exact",label:"精确匹配"},
    {id:"prefix",label:"开头是"},
    {id:"suffix",label:"结尾是"},
    {id:"exclude",label:"不包含"},
  ],
  author:[
    {id:"synonym",label:"近义词包含"},
    {id:"exact",label:"精确匹配"},
    {id:"prefix",label:"开头是"},
    {id:"suffix",label:"结尾是"},
    {id:"exclude",label:"不包含"},
  ],
  source:[
    {id:"in",label:"属于"},
    {id:"not_in",label:"不属于"},
  ],
  read:[{id:"is",label:"是"}],
};
const TASK_QUEUE_SOURCE_OPTIONS=[
  {id:"manual",label:"手动导入"},
  {id:"subscription_creator",label:"订阅博主"},
  {id:"subscription_favorites",label:"订阅收藏夹"},
  {id:"link_scan",label:"链接扫描"},
  {id:"chat",label:"对话导入"},
  {id:"catalog_seed",label:"目录摘录"},
  {id:"rss",label:"RSS 订阅"},
  {id:"other",label:"其他"},
];
const taskQueueRecentSearches=ref([]);
const taskQueueSearchTags=ref([]);
const taskQueueSearchDropdownOpen=ref(false);
let _taskQueueSearchBlurTimer=null;
let _taskQueueCondSeq=0;
function _loadTaskQueueRecentSearches(){
  try{
    const raw=localStorage.getItem(TASK_QUEUE_RECENT_KEY);
    const arr=JSON.parse(raw);
    return Array.isArray(arr)?arr.slice(0,15):[];
  }catch(_){return [];}
}
function _loadTaskQueueSearchTags(){
  try{
    const raw=localStorage.getItem(TASK_QUEUE_TAGS_KEY);
    const arr=JSON.parse(raw);
    return Array.isArray(arr)?arr.slice(0,30):[];
  }catch(_){return [];}
}
function _persistTaskQueueRecentSearches(list){
  try{localStorage.setItem(TASK_QUEUE_RECENT_KEY,JSON.stringify((list||[]).slice(0,15)));}catch(_){}
  taskQueueRecentSearches.value=(list||[]).slice(0,15);
}
function _persistTaskQueueSearchTags(list){
  try{localStorage.setItem(TASK_QUEUE_TAGS_KEY,JSON.stringify((list||[]).slice(0,30)));}catch(_){}
  taskQueueSearchTags.value=(list||[]).slice(0,30);
}
taskQueueRecentSearches.value=_loadTaskQueueRecentSearches();
taskQueueSearchTags.value=_loadTaskQueueSearchTags();
function _recordTaskQueueRecentSearch(){
  const q=String(taskQueueFilter.query||"").trim();
  if(!q)return;
  const entry={query:q,useAiSearch:!!taskQueueFilter.useAiSearch,ts:Date.now()};
  const list=_loadTaskQueueRecentSearches().filter(x=>!(String(x.query||"").trim()===q&&!!x.useAiSearch===!!entry.useAiSearch));
  list.unshift(entry);
  _persistTaskQueueRecentSearches(list);
}
function taskQueueSearchTagExists(label){
  const n=String(label||"").trim();
  if(!n)return false;
  return (taskQueueSearchTags.value||[]).some(t=>String(t.label||"").trim()===n);
}
function onTaskQueueSearchFocus(){
  clearTimeout(_taskQueueSearchBlurTimer);
  taskQueueSearchDropdownOpen.value=true;
}
function onTaskQueueSearchBlur(){
  clearTimeout(_taskQueueSearchBlurTimer);
  _taskQueueSearchBlurTimer=setTimeout(()=>{taskQueueSearchDropdownOpen.value=false;},160);
}
function onTaskQueueSearchEnter(){
  _recordTaskQueueRecentSearch();
  onTaskQueueFilterQueryInput();
  taskQueueSearchDropdownOpen.value=false;
}
function applyTaskQueueRecentSearch(item){
  if(!item)return;
  taskQueueFilter.query=String(item.query||"");
  taskQueueFilter.useAiSearch=!!item.useAiSearch;
  onTaskQueueFilterQueryInput();
  taskQueueSearchDropdownOpen.value=false;
}
function applyTaskQueueSearchTag(tag){
  if(!tag)return;
  taskQueueFilter.query=String(tag.query||tag.label||"");
  taskQueueFilter.useAiSearch=!!tag.useAiSearch;
  if(tag.advancedSnapshot&&typeof tag.advancedSnapshot==="object"){
    const snap=tag.advancedSnapshot;
    taskQueueFilter.advancedLogic=snap.advancedLogic==="or"?"or":"and";
    taskQueueFilter.conditions=Array.isArray(snap.conditions)?snap.conditions.map(c=>({...c,id:c.id||("c"+(++_taskQueueCondSeq))})):[];
    if(snap.authorPicks&&typeof snap.authorPicks==="object"){
      taskQueueFilter.authorPicks={...snap.authorPicks};
    }
    if(snap.sources&&typeof snap.sources==="object"){
      Object.keys(taskQueueFilter.sources).forEach(k=>{
        if(Object.prototype.hasOwnProperty.call(snap.sources,k))taskQueueFilter.sources[k]=!!snap.sources[k];
      });
    }
    taskQueueFilter.advancedOpen=true;
  }
  onTaskQueueFilterQueryInput();
  taskQueueSearchDropdownOpen.value=false;
}
function _taskQueueAdvancedSnapshot(){
  return{
    advancedLogic:taskQueueFilter.advancedLogic,
    conditions:(taskQueueFilter.conditions||[]).map(c=>({id:c.id,field:c.field,mode:c.mode,value:c.value})),
    authorPicks:{...(taskQueueFilter.authorPicks||{})},
    sources:{...(taskQueueFilter.sources||{})},
  };
}
function promoteTaskQueueSearchToTag(item){
  const q=String((item&&item.query)||"").trim();
  if(!q)return;
  if(taskQueueSearchTagExists(q)){
    showToastMsg("该检索已是标签");
    return;
  }
  const tags=_loadTaskQueueSearchTags();
  tags.unshift({
    id:"tag-"+Date.now(),
    label:q,
    query:q,
    useAiSearch:!!(item&&item.useAiSearch),
    advancedSnapshot:_taskQueueAdvancedSnapshot(),
    ts:Date.now(),
  });
  _persistTaskQueueSearchTags(tags);
  showToastMsg("已标签化："+q);
}
function toggleTaskQueueAdvanced(){
  taskQueueFilter.advancedOpen=!taskQueueFilter.advancedOpen;
}
function _newTaskQueueCondition(field){
  const f=String(field||"title");
  const modes=TASK_QUEUE_COND_MODES[f]||TASK_QUEUE_COND_MODES.title;
  let value="";
  if(f==="read")value="unread";
  else if(f==="source")value="manual";
  return{id:"c"+(++_taskQueueCondSeq),field:f,mode:(modes[0]&&modes[0].id)||"synonym",value};
}
function addTaskQueueCondition(field){
  taskQueueFilter.conditions=(taskQueueFilter.conditions||[]).concat([_newTaskQueueCondition(field)]);
  taskQueueFilter.advancedOpen=true;
}
function removeTaskQueueCondition(id){
  const cid=String(id||"");
  taskQueueFilter.conditions=(taskQueueFilter.conditions||[]).filter(c=>String(c.id)!==cid);
}
function onTaskQueueCondFieldChange(cond){
  if(!cond)return;
  const modes=taskQueueCondModes(cond.field);
  cond.mode=(modes[0]&&modes[0].id)||"synonym";
  if(cond.field==="read")cond.value="unread";
  else if(cond.field==="source")cond.value="manual";
  else cond.value="";
}
function taskQueueCondModes(field){
  return TASK_QUEUE_COND_MODES[String(field||"title")]||TASK_QUEUE_COND_MODES.title;
}
function taskQueueCondValuePlaceholder(field){
  const f=String(field||"");
  if(f==="title")return"如：智能体 / Java / 待看";
  if(f==="author")return"如：沧海九粟 / 匿名";
  return"输入关键词";
}
function _taskQueueSourceRestricted(){
  const s=taskQueueFilter.sources||{};
  const allKeys=Object.keys(s);
  const active=allKeys.filter(k=>s[k]);
  if(!active.length||active.length===allKeys.length)return null;
  return active;
}
function _taskQueueActiveConditions(){
  return (taskQueueFilter.conditions||[]).filter(c=>String(c.value||"").trim()||c.field==="read");
}
function taskQueueAdvancedActive(){
  return !!(
    _taskQueueActiveConditions().length||
    _taskQueueSelectedAuthors().length||
    _taskQueueSourceRestricted()
  );
}
function taskQueueAdvancedActiveCount(){
  let n=_taskQueueActiveConditions().length;
  if(_taskQueueSelectedAuthors().length)n+=1;
  if(_taskQueueSourceRestricted())n+=1;
  return n;
}
function _taskQueueAuthorBlob(t){
  const name=taskAuthorName(t);
  const aid=String((t&&t.author_id)||"").trim();
  return [name,aid].filter(Boolean).join(" ");
}
function _taskQueueTaskSource(t){
  return String((t&&t.import_source)||"other").trim()||"other";
}
function _taskQueueMatchText(blob,term,mode,terms){
  const low=String(blob||"").toLowerCase();
  const compact=_taskQueueNormCompact(blob);
  const m=String(mode||"synonym");
  if(m==="exclude"){
    const list=terms&&terms.length?terms:[String(term||"").toLowerCase()];
    return !list.some(t=>{
      const tl=String(t||"").toLowerCase();
      const tn=_taskQueueNormCompact(t);
      return (tl&&low.includes(tl))||(tn&&compact.includes(tn));
    });
  }
  if(m==="exact"){
    const t=String(term||"").toLowerCase();
    const tn=_taskQueueNormCompact(term);
    return low===t||compact===tn||low.split(/\s+/).some(w=>w===t);
  }
  if(m==="prefix"){
    const t=String(term||"").toLowerCase();
    const tn=_taskQueueNormCompact(term);
    return low.startsWith(t)||compact.startsWith(tn);
  }
  if(m==="suffix"){
    const t=String(term||"").toLowerCase();
    const tn=_taskQueueNormCompact(term);
    return low.endsWith(t)||compact.endsWith(tn);
  }
  const list=terms&&terms.length?terms:[String(term||"").toLowerCase()];
  return _taskQueueMatchTerms(blob,list);
}
function _evalTaskQueueCondition(t,cond){
  if(!cond)return true;
  const field=String(cond.field||"title");
  const mode=String(cond.mode||"synonym");
  const val=String(cond.value||"").trim();
  if(field==="read"){
    const rs=taskEffectiveReadStatus(t);
    if(!rs)return false;
    return val==="read"?rs==="read":rs==="unread";
  }
  if(field==="source"){
    const src=_taskQueueTaskSource(t);
    if(mode==="not_in")return src!==val;
    return src===val;
  }
  if(field==="author"){
    if(!val&&mode!=="exclude")return true;
    return _taskQueueMatchText(_taskQueueAuthorBlob(t),val,mode,[val]);
  }
  if(field==="title"){
    if(!val&&mode!=="exclude")return true;
    return _taskQueueMatchText(_taskQueueTitleBlob(t),val,mode,[val]);
  }
  return true;
}
function _evalTaskQueueAdvanced(t){
  const conds=_taskQueueActiveConditions();
  const authorPicks=_taskQueueSelectedAuthors();
  const sourceRestricted=_taskQueueSourceRestricted();
  if(!conds.length&&!authorPicks.length&&!sourceRestricted)return true;
  const parts=[];
  conds.forEach(c=>{parts.push(()=>_evalTaskQueueCondition(t,c));});
  if(authorPicks.length){
    parts.push(()=>{
      const name=taskAuthorName(t);
      return !!name&&authorPicks.includes(name);
    });
  }
  if(sourceRestricted){
    parts.push(()=>{
      const src=_taskQueueTaskSource(t);
      const allowed=sourceRestricted;
      return allowed.includes(src)||(allowed.includes("other")&&!_TASK_QUEUE_KNOWN_SOURCES.includes(src));
    });
  }
  if(!parts.length)return true;
  const logic=String(taskQueueFilter.advancedLogic||"and")==="or"?"or":"and";
  if(logic==="or")return parts.some(fn=>fn());
  return parts.every(fn=>fn());
}
const taskQueueAuthorFacets=ref([]);
const TASK_QUEUE_AUTHOR_FACET_COLLAPSED=12;
let _taskQueueSuggestTimer=null;
const _TASK_QUEUE_KNOWN_SOURCES=["manual","subscription_creator","subscription_favorites","chat","catalog_seed","rss","link_scan"];
function _taskQueueActiveSources(){
  const s=taskQueueFilter.sources||{};
  return Object.keys(s).filter(k=>s[k]);
}
function _taskQueueSelectedAuthors(){
  const picks=taskQueueFilter.authorPicks||{};
  return Object.keys(picks).filter(k=>picks[k]);
}
function _taskQueueNormCompact(s){
  return String(s||"").toLowerCase().replace(/[\s_\-·、，。！？；：（）()【】/\\|]+/g,"");
}
function _taskQueueTitleBlob(t){
  const meta=(t&&t.extracted_metadata)||{};
  return [
    t&&t.link_title,t&&t.doc_title,t&&t.task_note,t&&t.task_keywords,
    meta.keyword1,meta.keyword2,meta.domain,meta.module,
    Array.isArray(meta.tags)?meta.tags.join(" "):""
  ].filter(Boolean).join(" ");
}
function _taskQueueMatchTerms(blob,terms){
  if(!terms||!terms.length)return true;
  const low=String(blob||"").toLowerCase();
  const compact=_taskQueueNormCompact(blob);
  return terms.some(term=>{
    const t=String(term||"").toLowerCase();
    const tn=_taskQueueNormCompact(term);
    return (t&&low.includes(t))||(tn&&compact.includes(tn));
  });
}
function taskEffectiveReadStatus(t){
  const st=String((t&&t.status)||"").toLowerCase();
  if(st!=="completed")return"";
  const rs=String((t&&t.read_status)||"unread").toLowerCase();
  return rs==="read"?"read":"unread";
}
function _taskQueueAiSearchActive(){
  return !!(taskQueueFilter.useAiSearch&&String(taskQueueFilter.query||"").trim());
}
function _taskQueueFilterOne(t){
  const q=String(taskQueueFilter.query||"").trim();
  if(_taskQueueAiSearchActive()){
    if(taskQueueFilter.aiSearchLoading)return false;
    if(taskQueueFilter.aiSearchReady){
      const tid=String((t&&t.task_id)||"").trim();
      if(!tid||!(taskQueueFilter.aiMatchedIds||{})[tid])return false;
    }else if(taskQueueFilter.aiSearchStatus!=="err"){
      return false;
    }else{
      const terms=taskQueueFilter.expandedTerms.length?taskQueueFilter.expandedTerms:[q.toLowerCase()];
      if(terms.length&&!_taskQueueMatchTerms(_taskQueueTitleBlob(t),terms))return false;
    }
  }else if(q){
    const terms=taskQueueFilter.expandedTerms.length?taskQueueFilter.expandedTerms:[q.toLowerCase()];
    if(terms.length&&!_taskQueueMatchTerms(_taskQueueTitleBlob(t),terms))return false;
  }
  if(taskQueueFilter.enableRead&&taskQueueFilter.readStatus!=="all"){
    const rs=taskEffectiveReadStatus(t);
    if(!rs)return false;
    if(taskQueueFilter.readStatus==="unread"&&rs!=="unread")return false;
    if(taskQueueFilter.readStatus==="read"&&rs!=="read")return false;
  }
  if(!_evalTaskQueueAdvanced(t))return false;
  return true;
}
function _taskQueueSortRows(rows){
  const mode=String(taskQueueFilter.sort||"default");
  const list=(Array.isArray(rows)?rows:[]).slice();
  if(mode==="updated"){
    return list.sort((a,b)=>String(b.updated_at||b.created_at||"").localeCompare(String(a.updated_at||a.created_at||"")));
  }
  if(mode==="importance"){
    return list.sort((a,b)=>{
      const ia=clampImportance(a.importance),ib=clampImportance(b.importance);
      if(ia!==ib)return ib-ia;
      return sortTaskQueueFifo([a,b])[0].task_id===a.task_id?-1:1;
    });
  }
  return sortTaskQueueFifo(list);
}
const filteredTaskQueue=computed(()=>{
  const rows=taskQueue.value||[];
  const filtered=rows.filter(_taskQueueFilterOne);
  return _taskQueueSortRows(filtered);
});
const displayedTaskQueueAuthorFacets=computed(()=>{
  const rows=taskQueueAuthorFacets.value||[];
  if(taskQueueFilter.authorExpandAll)return rows;
  return rows.slice(0,TASK_QUEUE_AUTHOR_FACET_COLLAPSED);
});
function taskQueueAuthorFacetsHiddenCount(){
  const total=(taskQueueAuthorFacets.value||[]).length;
  if(taskQueueFilter.authorExpandAll||total<=TASK_QUEUE_AUTHOR_FACET_COLLAPSED)return 0;
  return total-TASK_QUEUE_AUTHOR_FACET_COLLAPSED;
}
const taskQueuePaging=reactive({
  page:1,
  page_size:(()=>{try{return Number(localStorage.getItem("sba_task_queue_page_size"))||12}catch(_){return 12}})(),
});
const taskQueueViewMode=ref((()=>{
  try{
    const mode=localStorage.getItem("sba_task_queue_view_mode");
    if(mode==="expanded"||mode==="compact")return mode;
    return localStorage.getItem("sba_task_queue_collapsed")==="0"?"expanded":"compact";
  }catch(_){return "compact"}
})());
function taskQueuePageCount(){
  const total=(filteredTaskQueue.value||[]).length;
  const ps=Math.max(1,Number(taskQueuePaging.page_size)||12);
  return Math.max(1,Math.ceil(total/ps));
}
const displayedTaskQueue=computed(()=>{
  const rows=filteredTaskQueue.value||[];
  if(taskQueueViewMode.value==="expanded")return rows;
  const ps=Math.max(1,Number(taskQueuePaging.page_size)||12);
  const maxPage=Math.max(1,Math.ceil(rows.length/ps));
  if(taskQueuePaging.page>maxPage)taskQueuePaging.page=maxPage;
  const start=(Math.max(1,Number(taskQueuePaging.page)||1)-1)*ps;
  return rows.slice(start,start+ps);
});
function setTaskQueuePageSize(n){
  const v=Math.min(100,Math.max(4,Number(n)||12));
  taskQueuePaging.page_size=v;
  taskQueuePaging.page=1;
  try{localStorage.setItem("sba_task_queue_page_size",String(v))}catch(_){}
}
function taskQueuePagePrev(){
  if(taskQueuePaging.page<=1)return;
  taskQueuePaging.page-=1;
}
function taskQueuePageNext(){
  if(taskQueuePaging.page>=taskQueuePageCount())return;
  taskQueuePaging.page+=1;
}
function jumpTaskQueuePage(n){
  if(taskQueueViewMode.value!=="compact")return;
  const max=taskQueuePageCount();
  const v=Math.min(max,Math.max(1,parseInt(String(n),10)||1));
  taskQueuePaging.page=v;
}
function buildMdNavContext(opts){
  const o=opts||{};
  return{
    from:o.from||page.value||"video",
    taskQueuePage:taskQueuePaging.page,
    taskQueueViewMode:taskQueueViewMode.value,
    taskId:o.taskId||"",
    subLinkPage:subLinkPaging.page,
  };
}
function restoreMdReturnContext(){
  let raw;
  try{raw=sessionStorage.getItem("sba_md_restore_pending");}catch(_){}
  if(!raw)return;
  let ctx;
  try{ctx=JSON.parse(raw);sessionStorage.removeItem("sba_md_restore_pending");}catch(_){return}
  if(!ctx||typeof ctx!=="object")return;
  const from=String(ctx.from||"").trim();
  if(from&&guardPageSwitch(from)){
    page.value=from;
    if(!openTabs.value.includes(from))openTabs.value=openTabs.value.concat([from]);
    const path=from==="video"?"/":("/"+from);
    if(history.replaceState)history.replaceState({page:from},"",path);
  }
  if(ctx.taskQueueViewMode==="compact"||ctx.taskQueueViewMode==="expanded"){
    taskQueueViewMode.value=ctx.taskQueueViewMode;
  }
  if(ctx.taskQueuePage!=null)taskQueuePaging.page=Math.max(1,Number(ctx.taskQueuePage)||1);
  if(ctx.subLinkPage!=null)subLinkPaging.page=Math.max(1,Number(ctx.subLinkPage)||1);
  const applyScroll=()=>{
    const y=Number(ctx.scrollY)||0;
    const el=ctx.scrollTarget===".p-60"?document.querySelector(".p-60"):null;
    if(el)el.scrollTop=y;
    else window.scrollTo(0,y);
    const tid=String(ctx.taskId||"").trim();
    if(tid){
      if(ensureQueueTaskVisible(tid))scrollToQueueTask(tid);
    }
  };
  nextTick(()=>{setTimeout(applyScroll,150);setTimeout(applyScroll,500);});
}
function toggleTaskQueueViewMode(){
  taskQueueViewMode.value=taskQueueViewMode.value==="compact"?"expanded":"compact";
  nextTick(()=>{
    try{
      localStorage.setItem("sba_task_queue_view_mode",taskQueueViewMode.value);
      localStorage.setItem("sba_task_queue_collapsed",taskQueueViewMode.value==="compact"?"1":"0");
    }catch(_){}
  });
}
function taskQueueViewModeLabel(){
  return taskQueueViewMode.value==="compact"?"展开全部":"双行浏览";
}
function taskQueueViewModeTitle(){
  return taskQueueViewMode.value==="compact"?"展开显示全部任务卡片（忽略分页高度限制）":"折叠为双行区域，上下滑动浏览当前页";
}
watch(
  ()=>[
    taskQueueFilter.query,
    taskQueueFilter.useAiSearch,
    JSON.stringify(taskQueueFilter.authorPicks||{}),
    taskQueueFilter.authorExpandAll,
    taskQueueFilter.sort,
    taskQueueFilter.readStatus,
    taskQueueFilter.enableRead,
    taskQueueFilter.advancedLogic,
    JSON.stringify(taskQueueFilter.conditions||[]),
    JSON.stringify(taskQueueFilter.sources||{}),
  ],
  ()=>{taskQueuePaging.page=1}
);
function refreshTaskQueueAuthorFacets(){
  const map=new Map();
  for(const t of taskQueue.value||[]){
    const name=taskAuthorName(t);
    if(!name)continue;
    map.set(name,(map.get(name)||0)+1);
  }
  taskQueueAuthorFacets.value=[...map.entries()]
    .sort((a,b)=>b[1]-a[1]||String(a[0]).localeCompare(String(b[0]),"zh-CN"))
    .map(([author_name,count])=>({author_name,count}));
}
let _taskQueueAiSearchSeq=0;
function _taskQueueClearAiSearchStatus(){
  taskQueueFilter.aiSearchLoading=false;
  taskQueueFilter.aiSearchStatus="idle";
  taskQueueFilter.aiSearchMessage="";
  taskQueueFilter.aiSearchReady=false;
  taskQueueFilter.aiMatchedIds={};
  taskQueueFilter.aiGrepSummary="";
  taskQueueFilter.aiIntentHint="";
}
function _taskQueueApplyAiFilters(applied){
  const a=applied||{};
  if(a.enable_read&&a.read_status&&a.read_status!=="all"){
    taskQueueFilter.enableRead=true;
    taskQueueFilter.readStatus=a.read_status;
  }
  if(Array.isArray(a.authors)&&a.authors.length){
    taskQueueFilter.advancedOpen=true;
    a.authors.forEach(name=>{
      const n=String(name||"").trim();
      if(n)taskQueueFilter.authorPicks[n]=true;
    });
  }
  if(a.sort&&String(a.sort)!=="default")taskQueueFilter.sort=String(a.sort);
}
function _taskQueueBeginAiSearch(q){
  taskQueueFilter.aiSearchLoading=true;
  taskQueueFilter.aiSearchStatus="loading";
  taskQueueFilter.aiSearchMessage="AI 检索中：解析意图并 GREP 字段/正文…";
  taskQueueFilter.aiSearchReady=false;
  taskQueueFilter.aiMatchedIds={};
  taskQueueFilter.aiGrepSummary="";
  taskQueueFilter.expandedTerms=[];
  taskQueueFilter.aiExpandedTerms=[];
  taskQueueFilter.aiSearchPowered=false;
}
function _taskQueueFinishAiSearch(opts){
  const o=opts||{};
  const q=String(o.q||"").trim();
  const matched=Number(o.matched);
  const llmPowered=!!o.llmPowered;
  taskQueueFilter.aiSearchLoading=false;
  taskQueueFilter.aiSearchReady=!!o.ready;
  if(o.grepSummary)taskQueueFilter.aiGrepSummary=String(o.grepSummary);
  if(o.intentHint)taskQueueFilter.aiIntentHint=String(o.intentHint);
  if(o.status==="err"){
    taskQueueFilter.aiSearchStatus="err";
    taskQueueFilter.aiSearchMessage=String(o.message||"AI 检索失败，已退回关键词 GREP");
    taskQueueFilter.aiSearchReady=false;
    taskQueueFilter.aiMatchedIds={};
    return;
  }
  const summary=String(o.grepSummary||"").trim();
  if(summary){
    taskQueueFilter.aiSearchStatus=matched>0?"ok":"warn";
    taskQueueFilter.aiSearchMessage=summary+(llmPowered?" · LLM":"")+(o.intentHint?(" · "+String(o.intentHint).slice(0,80)):"");
    return;
  }
  if(matched===0){
    taskQueueFilter.aiSearchStatus="warn";
    taskQueueFilter.aiSearchMessage="未找到匹配项 · 可调整关键词或取消 AI 检索改用标题筛选";
    return;
  }
  taskQueueFilter.aiSearchStatus="ok";
  taskQueueFilter.aiSearchMessage="AI 检索完成 · 命中 "+matched+" 条"+(llmPowered?" · LLM":"");
}
function taskQueueAiSearchStatusVisible(){
  if(!taskQueueFilter.useAiSearch)return false;
  return !!(
    taskQueueFilter.aiSearchLoading||
    taskQueueFilter.aiSearchMessage||
    String(taskQueueFilter.query||"").trim()
  );
}
function taskQueueAiSearchStatusClass(){
  const st=String(taskQueueFilter.aiSearchStatus||"idle");
  if(taskQueueFilter.aiSearchLoading||st==="loading")return"is-loading";
  if(st==="ok")return"is-ok";
  if(st==="warn")return"is-warn";
  if(st==="err")return"is-err";
  return"";
}
function scheduleTaskQueueSuggest(){
  clearTimeout(_taskQueueSuggestTimer);
  const useAi=!!taskQueueFilter.useAiSearch;
  const qNow=String(taskQueueFilter.query||"").trim();
  if(useAi&&qNow)_taskQueueBeginAiSearch(qNow);
  else if(!qNow)_taskQueueClearAiSearchStatus();
  _taskQueueSuggestTimer=setTimeout(async()=>{
    const q=String(taskQueueFilter.query||"").trim();
    if(!q){
      taskQueueFilter.expandedTerms=[];
      taskQueueFilter.aiExpandedTerms=[];
      taskQueueFilter.aiSearchPowered=false;
      _taskQueueClearAiSearchStatus();
      return;
    }
    const seq=++_taskQueueAiSearchSeq;
    const useAiSearch=!!taskQueueFilter.useAiSearch;
    if(useAiSearch){
      taskQueueFilter.aiSearchLoading=true;
      taskQueueFilter.aiSearchStatus="loading";
      taskQueueFilter.aiSearchMessage="AI 检索中：解析意图并 GREP 字段/正文…";
    }else{
      _taskQueueClearAiSearchStatus();
    }
    try{
      if(useAiSearch){
        const d=await fetchJsonSafe("/api/process/queue/ai-search",{
          method:"POST",
          headers:{"Content-Type":"application/json"},
          body:JSON.stringify({q,use_llm:true}),
        });
        if(seq!==_taskQueueAiSearchSeq)return;
        const ids=Array.isArray(d.matched_task_ids)?d.matched_task_ids:[];
        const idMap={};
        ids.forEach(id=>{
          const k=String(id||"").trim();
          if(k)idMap[k]=true;
        });
        taskQueueFilter.aiMatchedIds=idMap;
        taskQueueFilter.aiSearchReady=true;
        const expanded=Array.isArray(d.expanded_terms)?d.expanded_terms:[];
        taskQueueFilter.expandedTerms=expanded;
        taskQueueFilter.aiExpandedTerms=expanded.slice();
        taskQueueFilter.aiSearchPowered=!!d.llm_powered;
        _taskQueueApplyAiFilters(d.applied_filters||{});
        _taskQueueFinishAiSearch({
          q,
          matched:Number(d.total)||ids.length,
          llmPowered:!!d.llm_powered,
          grepSummary:d.grep_summary||"",
          intentHint:d.intent_hint||"",
          ready:true,
        });
        _recordTaskQueueRecentSearch();
      }else{
        const d=await fetchJsonSafe("/api/process/queue/suggest?q="+encodeURIComponent(q));
        if(seq!==_taskQueueAiSearchSeq)return;
        taskQueueFilter.expandedTerms=Array.isArray(d.expanded_terms)?d.expanded_terms:[];
        taskQueueFilter.aiExpandedTerms=[];
        taskQueueFilter.aiSearchPowered=false;
        _recordTaskQueueRecentSearch();
      }
    }catch(e){
      if(seq!==_taskQueueAiSearchSeq)return;
      if(useAiSearch){
        const errMsg=String((e&&e.message)||e||"请求失败");
        taskQueueFilter.expandedTerms=[q.toLowerCase()];
        taskQueueFilter.aiExpandedTerms=[];
        taskQueueFilter.aiSearchPowered=false;
        _taskQueueFinishAiSearch({status:"err",message:"AI 检索失败："+errMsg+" · 已退回关键词 GREP",q});
        showToastMsg("AI 检索失败，已退回关键词 GREP");
      }else{
        taskQueueFilter.expandedTerms=[q.toLowerCase()];
        taskQueueFilter.aiExpandedTerms=[];
        taskQueueFilter.aiSearchPowered=false;
      }
    }
  },useAi?420:280);
}
function onTaskQueueAiSearchToggle(){
  if(taskQueueFilter.useAiSearch){
    showToastMsg("AI 检索已启用：支持自然语言 + 字段/正文 GREP");
    if(String(taskQueueFilter.query||"").trim()){
      _taskQueueBeginAiSearch(String(taskQueueFilter.query||"").trim());
    }else{
      taskQueueFilter.aiSearchStatus="ok";
      taskQueueFilter.aiSearchMessage="AI 检索已启用：可输入如「备注含待看 未读」「正文里提到智能体」";
    }
  }else{
    _taskQueueClearAiSearchStatus();
  }
  onTaskQueueFilterQueryInput();
}
function onTaskQueueFilterQueryInput(){
  scheduleTaskQueueSuggest();
}
function toggleTaskQueueAuthorPick(name,ev){
  const n=String(name||"").trim();
  if(!n)return;
  taskQueueFilter.authorPicks[n]=!!(ev&&ev.target&&ev.target.checked);
}
function toggleTaskQueueReadFilter(kind){
  const k=String(kind||"").toLowerCase();
  if(k!=="unread"&&k!=="read")return;
  if(taskQueueFilter.enableRead&&taskQueueFilter.readStatus===k){
    taskQueueFilter.readStatus="all";
    taskQueueFilter.enableRead=false;
  }else{
    taskQueueFilter.readStatus=k;
    taskQueueFilter.enableRead=true;
  }
  taskQueuePaging.page=1;
}
function resetTaskQueueFilter(){
  taskQueueFilter.query="";
  taskQueueFilter.expandedTerms=[];
  taskQueueFilter.aiExpandedTerms=[];
  taskQueueFilter.aiSearchPowered=false;
  _taskQueueClearAiSearchStatus();
  taskQueueFilter.useAiSearch=false;
  taskQueueFilter.authorPicks={};
  taskQueueFilter.authorExpandAll=false;
  taskQueueFilter.sort="default";
  taskQueueFilter.readStatus="all";
  taskQueueFilter.enableRead=false;
  taskQueueFilter.advancedOpen=false;
  taskQueueFilter.advancedLogic="and";
  taskQueueFilter.conditions=[];
  Object.keys(taskQueueFilter.sources).forEach(k=>{taskQueueFilter.sources[k]=true;});
}
function taskQueueFilterActive(){
  return !!(
    String(taskQueueFilter.query||"").trim()||
    taskQueueAdvancedActive()||
    (taskQueueFilter.enableRead&&taskQueueFilter.readStatus!=="all")||
    taskQueueFilter.sort!=="default"
  );
}
const queueNoteOpen=reactive({});
const queueNoteDraft=reactive({});
const queueDismissedIds=reactive({});
const queueBatchSel=reactive({});
const queueBatchMode=ref(false);
const logFocusId=ref("");
const outDirInp=ref(null);
const toast=reactive({show:false,msg:""});
const uiOverlay=reactive({z:10060});
function bumpModalLayer(){
  uiOverlay.z=Math.min(10120,uiOverlay.z+2);
  const root=document.getElementById("sba-modal-root");
  if(root)root.style.setProperty("--modal-z",String(uiOverlay.z));
}
const sidePanelFs=reactive({open:false,id:""});
function isSidePanelFs(id){return sidePanelFs.open&&sidePanelFs.id===id;}
function toggleSidePanelFs(id){
  if(isSidePanelFs(id)){closeSidePanelFs();return;}
  sidePanelFs.open=true;
  sidePanelFs.id=id;
  document.body.classList.add("rss-panel-fs-active");
}
function closeSidePanelFs(){
  sidePanelFs.open=false;
  sidePanelFs.id="";
  document.body.classList.remove("rss-panel-fs-active");
}
function closeAllPageOverlays(opts){
  const except=(opts&&opts.except)||"";
  closeSidePanelFs();
  if(except!=="skill")skillImport.show=false;
  if(except!=="kbMeta"){kbImportMeta.show=false;kbImportMeta.busy=false;}
  if(except!=="kbBrowse")kbBrowse.show=false;
  if(except!=="mmBrowse")mmBrowse.show=false;
  if(except!=="modalOut")modalOut.show=false;
  if(except!=="modalArtifact")modalArtifact.show=false;
  if(except!=="queueBatch")exitQueueBatchMode();
  if(except!=="csBatch")exitCsBatchMode();
  if(except!=="hist")showHist.value=false;
  if(except!=="chatExpand")chatExpandOpen.value=false;
  if(except!=="taskHistModal"){c.taskHistModalOpen=false;c.taskHistModalRow=null;c.taskHistModalFromChat=false;}
  if(except!=="opsSpanModal")closeOpsSpanModal();
  if(except!=="favDetail")closeFavDetail();
}
function openPageOverlay(kind,openFn){
  closeAllPageOverlays({except:kind});
  bumpModalLayer();
  if(typeof openFn==="function")openFn();
}
const modalOut=reactive({show:false,path:"",files:[],newAbs:""});
const modalDupLink=reactive({show:false,task_id:"",link:"",doc_title:"",link_title:"",doc_filename:"",doc_path:""});
function resubmitDupLink(dupAction){if(!modalDupLink.link)return;modalDupLink.show=false;v.link=modalDupLink.link;setTimeout(()=>{startProcInternal(dupAction)},100)}
let toastT=null;let procEs=null;let queueTimer=null;let queuePollMs=2000;let schedTimer=null;let vecTimer=null;
function _queueNeedsFastPoll(){
  return (taskQueue.value||[]).some(t=>{
    const s=String((t&&t.status)||"").toLowerCase();
    return s==="pending"||s==="running"||s==="started"||s==="in_progress"||s==="downloading"||s==="transcribing"||s==="consolidating"||s==="generating"||s==="generating_html"||s==="extracting"||s==="ocr"||s==="comments"||s==="assembling"||s==="feishu_upload";
  });
}
function scheduleQueuePoll(){
  clearInterval(queueTimer);
  queuePollMs=_queueNeedsFastPoll()?2000:8000;
  queueTimer=setInterval(pollQueue,queuePollMs);
}
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
    mergeQueueTasksPreserveOrder(d.tasks||[]);
    refreshTaskQueueAuthorFacets();
    scheduleQueuePoll();
  }catch(e){console.warn("[pollQueue]",e.message||e)}
}
/** 轮询合并：保留当前卡片顺序，仅递补删除位；新任务插入最左 */
function mergeQueueTasksPreserveOrder(incoming){
  const rows=(Array.isArray(incoming)?incoming:[]).filter(t=>t&&t.task_id&&!queueDismissedIds[t.task_id]);
  // 轮询偶发空数组时保留已有卡片，避免「提交后任务消失」
  if(!rows.length){
    if(!Array.isArray(incoming)||taskQueue.value.length>0)return;
    taskQueue.value=[];
    return;
  }
  const byId=Object.fromEntries(rows.map(t=>[t.task_id,t]));
  const prev=taskQueue.value.filter(t=>!queueDismissedIds[t.task_id]);
  if(!prev.length){taskQueue.value=sortTaskQueueFifo(rows);return}
  const kept=prev.filter(t=>byId[t.task_id]).map(t=>{
    const inc=byId[t.task_id];
    const merged={...t,...inc};
    if(queueNoteOpen[t.task_id]&&queueNoteDraft[t.task_id]!==undefined){
      merged.task_note=queueNoteDraft[t.task_id];
    }
    if(String(t.read_status||"").toLowerCase()==="read"&&String(inc.read_status||"").toLowerCase()!=="read"){
      merged.read_status="read";
    }
    if(Array.isArray(inc.read_history)&&inc.read_history.length){
      merged.read_history=inc.read_history;
      merged.read_count=inc.read_count||inc.read_history.length;
    }
    if(inc.extracted_metadata&&typeof inc.extracted_metadata==="object"&&Object.keys(inc.extracted_metadata).length){
      merged.extracted_metadata=inc.extracted_metadata;
    }
    return merged;
  });
  const prevSet=new Set(prev.map(t=>t.task_id));
  const fresh=sortTaskQueueFifo(rows.filter(t=>!prevSet.has(t.task_id)));
  taskQueue.value=fresh.length?[...fresh,...kept]:kept;
}
function clampImportance(n){const x=Number(n);if(!Number.isFinite(x))return 5;return Math.max(1,Math.min(10,Math.round(x)))}
function taskImportancePct(n){return(clampImportance(n)*10)+"%"}
function taskImportanceColor(n){
  const v=clampImportance(n);
  const hue=Math.round(48-(v-1)*4.2);
  const sat=Math.min(95,58+v*4);
  const light=Math.max(28,62-v*3.2);
  return`hsl(${hue}, ${sat}%, ${light}%)`;
}
function taskImportanceBg(n){
  const v=clampImportance(n);
  const hue=Math.round(48-(v-1)*4.2);
  const sat=Math.min(95,58+v*4);
  return`hsla(${hue}, ${sat}%, 62%, 0.12)`;
}
function queueCardBorderStyle(t){
  const s=String((t&&t.status)||"pending");
  let color="var(--warn)";
  if(s==="completed")color="var(--ok)";
  else if(s==="failed"||s==="cancelled")color="var(--err)";
  else if(s==="running"||s==="started"||s==="in_progress")color="var(--a1)";
  return{borderLeft:"3px solid "+color};
}
async function updateQueueImportance(t,ev){
  if(!t||!t.task_id||t.status!=="pending")return;
  const val=clampImportance(ev&&ev.target?ev.target.value:t.importance);
  try{
    await fetchJsonSafe("/api/process/queue/importance",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({task_id:t.task_id,importance:val})});
    patchQueueTask(t.task_id,{importance:val});
  }catch(e){showToastMsg("更新重要度失败："+(e.message||String(e)))}
}
async function saveQueueTaskMeta(t,kind,ev){
  if(!t||!t.task_id)return;
  let val="";
  if(kind==="note"){
    if(queueNoteDraft[t.task_id]!==undefined)val=String(queueNoteDraft[t.task_id]||"");
    else if(ev&&ev.target&&(ev.target.tagName==="TEXTAREA"||ev.target.tagName==="INPUT"))val=String(ev.target.value||"");
    else val=String(t.task_note||"");
    val=val.trim();
  }else{
    if(!ev||!ev.target)return;
    let el=ev.target;
    if(el.tagName!=="TEXTAREA"&&el.tagName!=="INPUT"){
      el=el.parentElement?.parentElement?.querySelector("textarea")||el.parentElement?.querySelector("textarea");
      if(!el||(el.tagName!=="TEXTAREA"&&el.tagName!=="INPUT"))return;
    }
    val=String(el.value||"").trim();
  }
  const body={task_id:t.task_id};
  if(kind==="note")body.task_note=val; else body.task_keywords=val;
  try{
    await fetchJsonSafe("/api/process/queue/meta",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    if(kind==="note"){
      queueNoteDraft[t.task_id]=val;
      const versions=Array.isArray(t.task_note_versions)?[...t.task_note_versions]:[];
      versions.push({version:versions.length+1,text:val,saved_at:new Date().toISOString()});
      patchQueueTask(t.task_id,{task_note:val,task_note_versions:versions.slice(-50)});
      showToastMsg("备注已保存");
    }else{
      patchQueueTask(t.task_id,{task_keywords:val});
      if(ev&&ev.target&&ev.target.tagName==="TEXTAREA")showToastMsg("已保存");
    }
  }catch(e){showToastMsg("保存失败："+(e.message||String(e)))}
}
function getQueueNoteDraft(t){
  if(!t||!t.task_id)return "";
  if(queueNoteDraft[t.task_id]!==undefined)return String(queueNoteDraft[t.task_id]);
  return String(t.task_note||"");
}
function setQueueNoteDraft(t,val){
  if(!t||!t.task_id)return;
  queueNoteDraft[t.task_id]=String(val??"");
}
function queueTaskHasNote(t){return !!(t&&String(getQueueNoteDraft(t)||t.task_note||"").trim())}
function isQueueTaskNoteOpen(t){return !!(t&&t.task_id&&queueNoteOpen[t.task_id])}
function closeQueueTaskNoteEdit(t){
  if(!t||!t.task_id)return;
  queueNoteOpen[t.task_id]=false;
  delete queueNoteDraft[t.task_id];
}
function cancelQueueTaskNoteEdit(t){
  closeQueueTaskNoteEdit(t);
}
function toggleQueueTaskNote(t){
  if(!t||!t.task_id)return;
  const wasOpen=!!queueNoteOpen[t.task_id];
  if(wasOpen){
    closeQueueTaskNoteEdit(t);
    return;
  }
  queueNoteOpen[t.task_id]=true;
  queueNoteDraft[t.task_id]=String(t.task_note||"");
}
function queueTaskNoteBtnClass(t){
  if(isQueueTaskNoteOpen(t)||queueTaskHasNote(t))return "btn-artifact-ready";
  return "btn-artifact-off";
}
function queueTaskNoteBtnTitle(t){
  if(isQueueTaskNoteOpen(t))return "收起编辑";
  if(queueTaskHasNote(t))return "编辑备注";
  return "添加备注";
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
function taskReadCount(t){
  const n=Number(t&&t.read_count);
  if(Number.isFinite(n)&&n>0)return n;
  const hist=Array.isArray(t&&t.read_history)?t.read_history:[];
  if(hist.length)return hist.length;
  return taskIsUnread(t)?0:1;
}
function taskReadLabel(t){
  if(taskIsUnread(t))return"未读";
  const n=taskReadCount(t);
  return n<=1?"已读":"已读x"+n;
}
function _taskReadSnapshot(t){
  return{
    read_status:t.read_status,
    read_count:t.read_count,
    read_history:Array.isArray(t.read_history)?t.read_history.slice():undefined,
  };
}
function _buildOptimisticReadPatch(t,note){
  const prevCount=taskReadCount(t);
  const nextCount=prevCount<=0?1:prevCount+1;
  const entry={read_at:new Date().toISOString().slice(0,19),note:note||"",note_version:0};
  const hist=Array.isArray(t.read_history)?t.read_history.slice():[];
  hist.push(entry);
  return{read_status:"read",read_count:nextCount,read_history:hist};
}
function _reconcileReadPatch(cur,server){
  const nowCount=taskReadCount(cur);
  const serverCount=Number(server&&server.read_count)||0;
  const entries=Array.isArray(server&&server.entries)?server.entries:[];
  const curHist=Array.isArray(cur.read_history)?cur.read_history:[];
  return{
    read_status:"read",
    read_count:Math.max(nowCount,serverCount),
    read_history:entries.length>=curHist.length?entries:curHist,
  };
}
function _postTaskReadAsync(tid,note,onOk,onFail){
  fetchJsonSafe("/api/process/queue/read",{method:"POST",headers:authJsonHeaders(),body:JSON.stringify({task_id:tid,note})})
    .then(onOk)
    .catch(e=>{console.error("标记已读失败:",e);onFail(e)});
}
function markQueueTaskRead(t){
  const tid=String((t&&t.task_id)||(t&&t.id)||"").trim();
  if(!tid||!taskShowReadBadge(t))return;
  const note=String(getQueueNoteDraft(t)||t.task_note||"").trim();
  const snapshot=_taskReadSnapshot(t);
  patchQueueTaskMetrics(tid,_buildOptimisticReadPatch(t,note));
  _postTaskReadAsync(tid,note,(d)=>{
    const i=taskQueue.value.findIndex(x=>x.task_id===tid);
    if(i<0)return;
    patchQueueTaskMetrics(tid,_reconcileReadPatch(taskQueue.value[i],d));
  },()=>{
    patchQueueTaskMetrics(tid,snapshot);
    showToastMsg("标记已读失败，已回滚");
  });
}
function onQueueReadBadgeClick(t){markQueueTaskRead(t)}
function onQueueReadBadgeContext(t,ev){
  if(ev)ev.preventDefault();
  void openQueueReadHistory(t);
}
async function openQueueReadHistory(t){
  if(!t)return;
  const tid=String(t.task_id||t.id||"").trim();
  if(!tid)return;
  let entries=Array.isArray(t.read_history)?t.read_history:[];
  if(!entries.length){
    try{
      const d=await fetchJsonSafe("/api/process/queue/read-history?task_id="+encodeURIComponent(tid),{headers:authBearerHeaders()});
      entries=Array.isArray(d.entries)?d.entries:[];
    }catch(_){}
  }
  queueReadHistModal.show=true;
  queueReadHistModal.taskId=tid;
  queueReadHistModal.title=taskCardPureTitle(t);
  queueReadHistModal.entries=entries;
}
function closeQueueReadHistory(){queueReadHistModal.show=false}
function formatReadHistTime(iso){return taskQueueFmtTime(iso)||String(iso||"").replace("T"," ").slice(0,16)||"—"}
function _removeQueueTaskLocal(taskId){
  const idx=taskQueue.value.findIndex(t=>t.task_id===taskId);
  if(idx>=0)taskQueue.value.splice(idx,1);
  delete queueBatchSel[taskId];
  if(logFocusId.value===taskId){
    logFocusId.value="";
    logs.value=[];
    if(procEs){procEs.close();procEs=null}
  }
}
async function deleteQueueTask(taskId,opts){
  const silent=!!(opts&&opts.silent);
  const skipConfirm=!!(opts&&opts.skipConfirm);
  if(!taskId)return false;
  if(!skipConfirm&&!confirm("确定移除此任务卡片？\n（不会删除历史记录与产出文件）"))return false;
  queueDismissedIds[taskId]=true;
  try{
    const d=await fetchJsonSafe("/api/process/queue/delete",{method:"POST",headers:authJsonHeaders(),body:JSON.stringify({task_id:taskId})});
    if(d&&d.ok===false)throw new Error("移除失败");
    _removeQueueTaskLocal(taskId);
    if(!silent)showToastMsg("已移除卡片");
    return true;
  }catch(e){
    delete queueDismissedIds[taskId];
    console.error("移除卡片失败:",e);
    if(!silent)showToastMsg("移除失败："+(e.message||String(e)));
    return false;
  }
}
function clearQueueBatchSel(){
  for(const k of Object.keys(queueBatchSel))delete queueBatchSel[k];
}
function exitQueueBatchMode(){
  queueBatchMode.value=false;
  clearQueueBatchSel();
}
function toggleQueueBatchMode(){
  if(queueBatchMode.value)exitQueueBatchMode();
  else queueBatchMode.value=true;
}
function onQueueCardClick(t){
  if(!t||!t.task_id)return;
  if(queueBatchMode.value)toggleQueueBatchSel(t.task_id);
  else selectQueueTask(t.task_id);
}
function isQueueBatchSelected(taskId){return !!queueBatchSel[taskId]}
function toggleQueueBatchSel(taskId,ev){
  if(ev)ev.stopPropagation();
  if(!taskId||!queueBatchMode.value)return;
  if(queueBatchSel[taskId])delete queueBatchSel[taskId];
  else queueBatchSel[taskId]=true;
}
function queueBatchSelCount(){return Object.keys(queueBatchSel).filter(k=>queueBatchSel[k]).length}
function _queueBatchVisibleRows(){
  return displayedTaskQueue.value||[];
}
function queueBatchSelAllChecked(){
  const rows=_queueBatchVisibleRows();
  if(!rows.length)return false;
  return rows.every(t=>queueBatchSel[t.task_id]);
}
function toggleQueueBatchSelAll(){
  const rows=_queueBatchVisibleRows();
  if(!rows.length)return;
  const allOn=queueBatchSelAllChecked();
  for(const t of rows){
    if(allOn)delete queueBatchSel[t.task_id];
    else queueBatchSel[t.task_id]=true;
  }
}
async function batchDeleteQueueTasks(){
  const ids=Object.keys(queueBatchSel).filter(k=>queueBatchSel[k]);
  if(!ids.length){showToastMsg("请先勾选要删除的卡片");return}
  if(!confirm("确定批量移除 "+ids.length+" 个任务卡片？\n（不会删除历史记录与产出文件）"))return;
  for(const id of ids)queueDismissedIds[id]=true;
  try{
    const d=await fetchJsonSafe("/api/process/queue/delete-batch",{method:"POST",headers:authJsonHeaders(),body:JSON.stringify({task_ids:ids})});
    const removed=Number(d&&d.removed)||0;
    if(removed<=0)throw new Error("未移除任何卡片");
    for(const id of ids){
      if(queueDismissedIds[id])_removeQueueTaskLocal(id);
      delete queueDismissedIds[id];
    }
    showToastMsg("已批量移除 "+removed+" 个卡片");
    exitQueueBatchMode();
  }catch(e){
    for(const id of ids)delete queueDismissedIds[id];
    console.error("批量移除失败:",e);
    showToastMsg("批量移除失败："+(e.message||String(e)));
  }
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
const TASK_ACTIVE_STATUSES=new Set(["pending","running","started","in_progress"]);
function taskShowProgress(t){return TASK_ACTIVE_STATUSES.has(String((t&&t.status)||"pending"))}
function taskCardLinkUrl(t){
  const raw=String((t&&(t.link||t.url))||"").trim();
  if(!raw)return"";
  if(/^https?:\/\//i.test(raw))return raw;
  if(raw.startsWith("//"))return"https:"+raw;
  return raw;
}
function taskCardStatusText(t){
  const status=String((t&&t.status)||"pending");
  const label=histStatusLabel(t);
  const stage=String((t&&t.stage)||"").trim();
  if(!stage)return label;
  if(status==="completed"&&/^(完成|done|100%?)$/i.test(stage))return label;
  if(status==="failed"&&/^(失败|fail(?:ed)?)/i.test(stage))return label;
  if(status==="cancelled"&&/^(已取消|取消|cancel(?:led)?)/i.test(stage))return label;
  if(stage===label)return label;
  return label+" · "+stage;
}
function taskCardStatusColor(t){
  const s=(t&&t.status)||"pending";
  if(s==="pending")return"var(--warn)";
  if(s==="running"||s==="started"||s==="in_progress")return"var(--a1)";
  if(s==="failed"||s==="cancelled")return"var(--err)";
  if(s==="completed")return"var(--ok)";
  return"var(--t3)";
}
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
function histTaskId(t){return String((t&&t.id)||(t&&t.task_id)||"").trim()}
function histShowReadBadge(t){return String((t&&t.status)||"").toLowerCase()==="completed"}
function histTaskIsUnread(t){
  if(!histShowReadBadge(t))return false;
  return String((t&&t.read_status)||"unread").toLowerCase()!=="read";
}
function histTaskReadLabel(t){return taskReadLabel(t)}
function markHistTaskRead(t){
  const tid=histTaskId(t);
  if(!tid||!histShowReadBadge(t))return;
  const note=String(t.task_note||"").trim();
  const snapshot=_taskReadSnapshot(t);
  Object.assign(t,_buildOptimisticReadPatch(t,note));
  _postTaskReadAsync(tid,note,(d)=>Object.assign(t,_reconcileReadPatch(t,d)),()=>{
    Object.assign(t,snapshot);
    showToastMsg("标记已读失败，已回滚");
  });
}
function onHistReadBadgeClick(t){markHistTaskRead(t)}
function onHistReadBadgeContext(t,ev){if(ev)ev.preventDefault();void openQueueReadHistory(t)}
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
function taskCardHeadTitle(t){
  const plat=taskCardPlatform(t);
  const imp=clampImportance(t&&t.importance);
  const title=taskCardPureTitle(t);
  if(plat)return plat+"·"+imp+" "+title;
  return imp+" "+title;
}
function taskCardDocSubTitle(t){return""}
function taskCardSubTitle(t){return""}
const modalTaskOps=reactive({show:false,loading:false,taskId:"",reportId:"",body:"",err:""});
const queueReadHistModal=reactive({show:false,taskId:"",title:"",entries:[]});
async function openTaskOpsReport(t){
  const rid=taskOpsReportId(t);
  if(!rid){showToastMsg("暂无失败分析报告");return}
  modalTaskOps.show=true;
  modalTaskOps.taskId=(t&&t.task_id)||(t&&t.id)||"";
  modalTaskOps.reportId=rid;
  modalTaskOps.body="";
  modalTaskOps.loading=true;
  modalTaskOps.err="";
  try{
    const r=await fetch("/api/ops/reports/"+encodeURIComponent(rid));
    const d=await r.json();
    if(!r.ok||!d.ok)throw new Error(d.error||"加载失败");
    modalTaskOps.body=d.data?.content||"";
  }catch(e){modalTaskOps.err=e.message||String(e);}
  finally{modalTaskOps.loading=false;}
}
function closeTaskOpsReport(){modalTaskOps.show=false;}
const META_KW_KEYS=["keyword1","keyword2","keyword3","keyword4","keyword5","keyword6","keyword7","keyword8"];
function taskCardExtractedKeywords(t){
  if(!t)return[];
  const meta=t.extracted_metadata;
  if(!meta||typeof meta!=="object"||Array.isArray(meta))return[];
  const out=[];
  const push=v=>{
    const s=String(v==null?"":v).trim();
    if(s&&!out.includes(s))out.push(s);
  };
  META_KW_KEYS.forEach(k=>{if(meta[k]!=null)push(meta[k]);});
  Object.keys(meta).sort().forEach(k=>{
    if(/^keyword\d+$/i.test(k))push(meta[k]);
  });
  return out.slice(0,8);
}
function taskCardExtractedKeywordsLine(t){
  const kws=taskCardExtractedKeywords(t);
  return kws.length?kws.join(","):"";
}
function taskCardStatusInline(t){
  const st=String((t&&t.status)||"").toLowerCase();
  if(st==="completed")return histStatusLabel(t);
  return"";
}
function taskCardStatusExtra(t){
  const status=String((t&&t.status)||"pending");
  const label=histStatusLabel(t);
  const stage=String((t&&t.stage)||"").trim();
  if(status==="completed")return"";
  if(!stage)return label;
  if(status==="failed"&&/^(失败|fail(?:ed)?)/i.test(stage))return label;
  if(status==="cancelled"&&/^(已取消|取消|cancel(?:led)?)/i.test(stage))return label;
  if(stage===label)return label;
  return label+" · "+stage;
}
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
function patchQueueTask(tid,patch){patchQueueTaskMetrics(tid,patch)}
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
function histTaskTitle(t){return taskCardHeadTitle(t)}
function histTaskSubTitle(t){return taskCardSubTitle(t)}
function taskDocFilename(t){
  return (t&&(t.doc_filename||pathBasename(t.doc_path||""))||"").trim();
}
function taskHasMd(t){
  if(!t)return false;
  return!!((t.doc_path||"").trim()||(t.doc_filename||"").trim());
}
function taskHtmlClickTitle(t){
  if(!t||!taskHasMd(t))return"需要先有 MD 产物";
  if(taskHtmlPending(t))return"HTML 生成中";
  if(taskHtmlReady(t))return"打开 HTML 长页";
  return"尚未生成或已失败，点击重新生成 HTML";
}
async function onTaskHtmlClick(t){
  if(!t)return;
  if(!taskHasMd(t)){showToastMsg("尚无 MD，无法生成 HTML");return}
  if(taskHtmlPending(t)){showToastMsg("HTML 正在生成中…");return}
  if(taskHtmlReady(t)){await openTaskHtml(t);return}
  const name=taskDocFilename(t);
  const msg=name
    ?("尚未生成 HTML（或上次生成失败）。\n\n是否基于 MD「"+name+"」重新生成长页 HTML？")
    :"尚未生成 HTML（或上次生成失败）。\n\n是否现在开始重新生成？";
  if(!confirm(msg))return;
  await regenerateHtml(t,true);
}
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
function outputMdPreviewUrl(path,preset){
  const b=pathBasename(path);
  if(!b)return"";
  const low=b.toLowerCase();
  if(low.endsWith(".md")||low.endsWith(".txt")||low.endsWith(".markdown")||low.endsWith(".mdx")){
    const p=(preset||"split").trim();
    return "/preview/md.html?file="+encodeURIComponent(b)+(p?("&preset="+encodeURIComponent(p)):"");
  }
  return outputHttpUrl(path);
}
function artifactBrowserUrl(path){
  return outputMdPreviewUrl(path)||outputHttpUrl(path);
}
function openOutputMdByPath(path,preset,opts){
  const p=(path||'').trim();
  if(!p){showToastMsg('MD 路径不可用');return}
  const b=pathBasename(p);
  if(!b){showToastMsg('MD 文件名无效');return}
  const nav=buildMdNavContext(opts||{});
  nav.newTab=uiPrefs.openArtifactInNewTab;
  if(typeof SBA_READER_HUB!=='undefined'&&SBA_READER_HUB.navigateToMdPreview){
    SBA_READER_HUB.navigateToMdPreview(b,preset||'split',nav);
    return;
  }
  const url=outputMdPreviewUrl(p,preset||'split');
  if(url)openAppUrl(url,{newTab:uiPrefs.openArtifactInNewTab});
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
  openOutputMdByPath(path,"split",{from:page.value,taskId:t.task_id});
}
async function openTaskHtml(t){
  const htmlPath=(t.html_path||"").trim();
  if(htmlPath){
    const url=outputHttpUrl(htmlPath);
    if(url){openAppUrl(url,{newTab:uiPrefs.openArtifactInNewTab});return}
    await openLocalOutput(htmlPath,"file");
    return;
  }
  const mdPath=(t.doc_path||t.doc_filename||"").trim();
  if(mdPath&&taskHtmlReady(t)){
    const b=pathBasename(mdPath);
    openAppUrl("/preview/md.html?file="+encodeURIComponent(b)+"&preset=html",{newTab:uiPrefs.openArtifactInNewTab});
    return;
  }
  showToastMsg("HTML 尚未生成");
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
function detectPlatform(link){const u=(link||"").toLowerCase();if(u.includes("mp.weixin.qq.com"))return"微信";if(u.includes("douyin.com")||u.includes("iesdouyin")||u.includes("tiktok"))return"抖音";if(u.includes("bilibili.com")||u.includes("b23.tv"))return"B站";return"小红书"}
const procActive=new Set(["pending","queued","started","running","downloading","transcribing","generating","extracting","ocr","comments","assembling","consolidating","feishu_upload"]);
const logs=ref([]);
const logHighlightIdx=ref(-1);
let logHighlightTimer=null;
function aLog(ts,lv,msg){logs.value.push({timestamp:ts,level:lv,message:msg});nextTick(()=>{const el=document.getElementById("lb");if(el)el.scrollTop=el.scrollHeight})}
function logRowClass(lv){const x=String(lv||"").toUpperCase();if(x==="ERROR"||x==="ERR")return"err";if(x==="WARN"||x==="WARNING")return"warn";return"info"}
function isLogLevelError(lv){
  const x=String(lv||"").toUpperCase();
  return x==="ERROR"||x==="ERR";
}
function findLastErrorLogIndex(list){
  if(!Array.isArray(list)||!list.length)return -1;
  for(let i=list.length-1;i>=0;i--){
    if(isLogLevelError(list[i].level))return i;
  }
  return -1;
}
function flashProcessLogRow(idx){
  if(idx<0)return;
  logHighlightIdx.value=idx;
  if(logHighlightTimer)clearTimeout(logHighlightTimer);
  logHighlightTimer=setTimeout(()=>{logHighlightIdx.value=-1},2200);
  nextTick(()=>{
    const row=document.getElementById("log-row-"+idx);
    const lb=document.getElementById("lb");
    if(!row||!lb)return;
    const rowTop=row.offsetTop;
    const rowH=row.offsetHeight;
    const viewH=lb.clientHeight;
    lb.scrollTop=Math.max(0,rowTop-Math.floor((viewH-rowH)/2));
  });
}
async function loadProcessLogsForTask(tid){
  const taskRow=taskQueue.value.find(x=>x.task_id===tid);
  const taskStatus=String((taskRow&&taskRow.status)||"").trim();
  const isTerminalTask=taskStatus==="failed"||taskStatus==="cancelled"||taskStatus==="completed";
  const sameTask=logFocusId.value===tid;
  if(sameTask&&logs.value.length){
    const hit=findLastErrorLogIndex(logs.value);
    if(hit>=0)return hit;
  }else if(!isTerminalTask){
    logFocusId.value=tid;
    connectLogEs(tid);
  }else{
    logFocusId.value=tid;
  }
  if(isTerminalTask){
    try{
      const token=localStorage.getItem("sba_token");
      const r=await fetch("/api/history/logs/"+encodeURIComponent(tid)+(token?"?sba_token="+encodeURIComponent(token):""));
      const d=await r.json();
      if(r.ok){
        logs.value=d.text_logs||d.logs||[];
        return findLastErrorLogIndex(logs.value);
      }
    }catch(_){ }
  }
  for(let n=0;n<80;n++){
    await new Promise(r=>setTimeout(r,50));
    const hit=findLastErrorLogIndex(logs.value);
    if(hit>=0)return hit;
    const t=taskQueue.value.find(x=>x.task_id===tid);
    const st=t&&t.status;
    if(logs.value.length&&(st==="failed"||st==="cancelled"||st==="completed"))break;
  }
  let hit=findLastErrorLogIndex(logs.value);
  if(hit>=0)return hit;
  try{
    const token=localStorage.getItem("sba_token");
    const r=await fetch("/api/history/logs/"+encodeURIComponent(tid)+(token?"?sba_token="+encodeURIComponent(token):""));
    const d=await r.json();
    if(r.ok){
      logs.value=d.text_logs||d.logs||[];
      return findLastErrorLogIndex(logs.value);
    }
  }catch(_){}
  return -1;
}
async function jumpToTaskErrorLog(taskId){
  const tid=String(taskId||"").trim();
  if(!tid)return;
  const logGlass=document.querySelector("#lb")?.closest(".glass");
  if(logGlass)logGlass.scrollIntoView({block:"nearest",behavior:"smooth"});
  const idx=await loadProcessLogsForTask(tid);
  if(idx<0){
    showToastMsg("未找到 ERROR 日志");
    return;
  }
  flashProcessLogRow(idx);
}
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
function findRunningTasksForLink(linkTrim){
  const needle=(linkTrim||"").trim().toLowerCase();
  if(!needle)return [];
  return taskQueue.value.filter(t=>{
    if(!procActive.has(t.status||""))return false;
    const uh=(t.url_hash||"").trim();
    const tl=(t.link||"").trim().toLowerCase();
    const nl=(t.normalized_link||"").trim().toLowerCase();
    return tl===needle||nl===needle||tl.includes(needle)||needle.includes(tl);
  });
}
async function startProcInternal(dupAction){
  const linkTrim=(v.link||"").trim();
  if(!linkTrim){alert("请输入链接");return}
  if(v.submitting)return;
  v.submitting=true;
  try{
    if(!dupAction){
      const running=findRunningTasksForLink(linkTrim);
      if(running.length&&!confirm("该链接已有任务在执行中，仍要再提交？")){v.submitting=false;return}
    }
    const hints=parseTaskMetaHintsJson(v.taskMetaHintsJson||(v.taskKeywords||""));
    const payload={
      platform:detectPlatform(linkTrim),
      link:linkTrim,
      user_prompt:v.pr||"",
      video_transcript_mode:v.videoTranscriptMode||"audio_only",
      comments:v.comments||{enabled:false,count:10,sort:"hot"},
      importance:clampImportance(v.importance),
      task_note:(v.taskNote||"").trim(),
      task_keywords:(v.taskKeywords||"").trim(),
      task_meta_hints:hints,
    };
    if(dupAction)payload.dup_action=dupAction;
    const r=await fetch("/api/process/start",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    const d=await parseApiJson(r);
    if(r.status===409&&d.detail&&d.detail.conflict==="duplicate_completed"){
      v.submitting=false;
      const ex=d.detail.existing||{};
      modalDupLink.task_id=ex.task_id||"";
      modalDupLink.link=linkTrim;
      modalDupLink.doc_title=ex.doc_title||"";
      modalDupLink.link_title=ex.link_title||"";
      modalDupLink.doc_filename=ex.doc_filename||"";
      modalDupLink.doc_path=ex.doc_path||"";
      modalDupLink.show=true;
      return;
    }
    if(!r.ok){
      const msg=d.detail||d.message||d.error||("HTTP "+r.status);
      throw new Error(typeof msg==="string"?msg:JSON.stringify(msg));
    }
    v.submitting=false;
    v.submitPulse=true;setTimeout(()=>{v.submitPulse=false},700);
    showToastMsg(d.reused?"同链接已复用原卡片继续处理":"已加入处理队列");
    v.link="";
    logFocusId.value=d.task_id;
    connectLogEs(d.task_id);
    pollQueue();
  }catch(e){v.submitting=false;showToastMsg("提交失败："+(e.message||String(e)));aLog("","ERROR",e.message||String(e))}
  finally{v.submitting=false}
}
async function startProc(){return startProcInternal("")}
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
    if(d.meta_extract_enabled!=null)linkMetaSchema.enabled=!!d.meta_extract_enabled;
    if(d.meta_card_display_enabled!=null)linkMetaSchema.cardDisplay=!!d.meta_card_display_enabled;
    if(Array.isArray(d.meta_extract_fields)&&d.meta_extract_fields.length)
      linkMetaSchema.fields=d.meta_extract_fields;
    if(d.meta_extract_prompt!=null)linkMetaSchema.prompt=String(d.meta_extract_prompt||"");
    refreshLinkMetaFieldsEdit();
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
function filteredHistTasks(){
  const rows=(ht.tasks||[]).map(t=>({...t,task_id:t.id||t.task_id}));
  return _taskQueueSortRows(rows.filter(_taskQueueFilterOne));
}
const histLogPanel=reactive({
  open:false,loading:false,taskId:"",title:"",source:"",logCount:0,tab:"text",
  textLogs:[],spans:[],errors:[],spanTask:null
});
const opsSpanModal=reactive({
  open:false,loading:false,taskId:"",title:"",link:"",tab:"io",
  textLogs:[],spans:[],errors:[],spanTask:null,stepSel:null
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
async function regenerateHtml(t,skipConfirm){
  const doc=taskDocFilename(t);
  if(!doc){alert("没有 MD 文件，无法生成 HTML");return}
  if(!skipConfirm&&!confirm("确定重新生成 HTML？"))return
  try{
    const r=await fetch("/api/history/regenerate-html",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({link:t.link,doc_filename:doc})});
    const d=await r.json();
    if(d.ok){
      showToastMsg("HTML 重新生成中…");
      const patch={html_status:"async_pending",html_path:"",html_message:"HTML 重新生成中..."};
      Object.assign(t,patch);
      const tid=(t.task_id||t.id||d.task_id||"").trim();
      if(tid)patchQueueTaskMetrics(tid,patch);
      await pollQueue();
      ldHist();
    }else{alert(d.error||d.detail||"生成失败")}
  }catch(e){alert("请求失败: "+(e.message||e))}
}

/* ══ P2 编排 API（兼容保留）；工具页用 SKILL ══ */
const o=reactive({nds:[]});async function ldNodes(){try{const r=await fetch('/api/orchestration/nodes');const d=await r.json();o.nds=d.nodes||[]}catch(e){}}

const skills=ref([]);
const boardUsageStats=ref({});
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
const orchBoardSort=ref((()=>{try{const v=localStorage.getItem("sba_orch_board_sort");if(v)return v}catch(_){}return"heat_desc"})());
const orchBoardJoinRange=ref("all");
watch(orchBoardSort,(v)=>{try{localStorage.setItem("sba_orch_board_sort",String(v||"heat_desc"))}catch(_){}});
function _boardUsageForKey(key){
  const m=boardUsageStats.value||{};
  const row=m[key];
  if(!row||typeof row!=="object")return{total_count:0,mount_count:0,invoke_count:0,last_used_at:"",first_used_at:""};
  return row;
}
function _parseIsoMs(v){
  if(typeof v==="number"&&Number.isFinite(v))return v;
  const s=String(v||"").trim();
  if(!s)return 0;
  const t=Date.parse(s);
  return Number.isFinite(t)?t:0;
}
function _formatBoardDate(v){
  const ms=_parseIsoMs(v);
  if(!ms)return "";
  const d=new Date(ms);
  if(Number.isNaN(d.getTime()))return "";
  const p=n=>String(n).padStart(2,"0");
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}`;
}
function _orchBoardJoinCutoffMs(range){
  const r=String(range||"all");
  if(r==="all")return 0;
  const days=r==="7d"?7:r==="30d"?30:r==="90d"?90:0;
  if(!days)return 0;
  return Date.now()-days*86400000;
}
function orchBoardItemTitle(it){
  const parts=[it.description||it.name||""];
  if(it.createdAt)parts.push(`加入：${it.createdAt}`);
  if(it.usageCount>0)parts.push(`热度：${it.usageCount} 次（挂载 ${it.usageMount||0} · 调用 ${it.usageInvoke||0}）`);
  else parts.push("热度：暂无使用记录");
  if(it.lastUsedAt)parts.push(`最近使用：${it.lastUsedAt}`);
  return parts.filter(Boolean).join("\n");
}
function _orchBoardSortItems(list){
  const sort=orchBoardSort.value||"default";
  const arr=[...(list||[])];
  if(sort==="default")return arr;
  if(sort==="join_desc")return arr.sort((a,b)=>_parseIsoMs(b.joinMs)-_parseIsoMs(a.joinMs));
  if(sort==="join_asc")return arr.sort((a,b)=>_parseIsoMs(a.joinMs)-_parseIsoMs(b.joinMs));
  if(sort==="heat_desc")return arr.sort((a,b)=>(b.usageCount||0)-(a.usageCount||0)||_parseIsoMs(b.lastUsedMs)-_parseIsoMs(a.lastUsedMs));
  if(sort==="heat_asc")return arr.sort((a,b)=>(a.usageCount||0)-(b.usageCount||0)||_parseIsoMs(a.lastUsedMs)-_parseIsoMs(b.lastUsedMs));
  return arr;
}
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
function _skillHeatMeta(s){
  const uKey="skill:"+(s&&s.id||"");
  const u=s&&s.usage&&typeof s.usage==="object"?s.usage:_boardUsageForKey(uKey);
  return{
    count:Number(u.total_count||(s&&s.usage_count)||0),
    lastMs:_parseIsoMs(u.last_used_at||(s&&s.last_used_at)),
  };
}
const skillsSorted=computed(()=>{
  const arr=[...(skills.value||[])];
  return arr.sort((a,b)=>{
    const ha=_skillHeatMeta(a),hb=_skillHeatMeta(b);
    return hb.count-ha.count||hb.lastMs-ha.lastMs||String(a.name||"").localeCompare(String(b.name||""),"zh-CN");
  });
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
    const uKey="skill:"+(s.id||"");
    const u=s.usage&&typeof s.usage==="object"?s.usage:_boardUsageForKey(uKey);
    const createdAt=_formatBoardDate(s.created_at);
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
      createdAt,
      joinMs:_parseIsoMs(s.created_at),
      usageCount:Number(u.total_count||s.usage_count||0),
      usageMount:Number(u.mount_count||s.usage_mount_count||0),
      usageInvoke:Number(u.invoke_count||s.usage_invoke_count||0),
      lastUsedAt:_formatBoardDate(u.last_used_at||s.last_used_at),
      lastUsedMs:_parseIsoMs(u.last_used_at||s.last_used_at),
    });
  });
  mcpEnabledList.value.forEach(es=>{
    const uKey="mcp-srv:"+es.alias;
    const u=_boardUsageForKey(uKey);
    items.push({
      type:"mcp-server",typeLabel:"MCP服务",id:es.alias,name:es.alias,aliasCn:mcpAliasCn(es),description:es.summary||"",key:uKey,raw:es,
      createdAt:"",joinMs:0,
      usageCount:Number(u.total_count||0),
      usageMount:Number(u.mount_count||0),
      usageInvoke:Number(u.invoke_count||0),
      lastUsedAt:_formatBoardDate(u.last_used_at),
      lastUsedMs:_parseIsoMs(u.last_used_at),
    });
  });
  (mcpDiscovered.value||[]).forEach((mt,i)=>{
    const discKey=mcpDiscKey(mt,i);
    const srv=mt.server||"";
    const uKey=srv?`mcp:${srv}:${mt.name||""}`:`mcp:${mt.name||""}`;
    const u=_boardUsageForKey(uKey);
    items.push({
      type:"mcp",typeLabel:"MCP工具",id:discKey,name:mt.name||"—",description:mt.description||"",server:srv,key:discKey,raw:mt,idx:i,
      createdAt:"",joinMs:0,
      usageCount:Number(u.total_count||0),
      usageMount:Number(u.mount_count||0),
      usageInvoke:Number(u.invoke_count||0),
      lastUsedAt:_formatBoardDate(u.last_used_at),
      lastUsedMs:_parseIsoMs(u.last_used_at),
    });
  });
  return items;
}
const orchBoardTotalCount=computed(()=>orchBoardCatalogItems().length);
const orchBoardFilteredItems=computed(()=>{
  const tab=orchBoardTab.value;
  const q=orchToolSearch.value;
  const joinCut=_orchBoardJoinCutoffMs(orchBoardJoinRange.value);
  const filtered=orchBoardCatalogItems().filter(it=>{
    if(tab==="skill"&&it.type!=="skill")return false;
    if(tab==="mcp"&&it.type==="skill")return false;
    if(joinCut>0){
      if(!it.joinMs||it.joinMs<joinCut)return false;
    }
    return orchMatchToolItem(it,q);
  });
  return _orchBoardSortItems(filtered);
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
  const sort=orchBoardSort.value||"default";
  return cat.ORCH_CATEGORIES.map(c=>{
    const items=(buckets[c.id]&&buckets[c.id].items)||[];
    return {...c,items:sort==="default"?items:_orchBoardSortItems(items)};
  }).filter(c=>c.items.length>0);
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
  video:"链接文档化",subscribe:"链接订阅",sched:"定时任务",orch:"工具",chat:"AI 问答",reader:"文本阅读",tasks:"任务中心",agpz:"Agent 个性化设置",
  iag:"内部 Agent 配置",rag:"RAG 知识库",rss:"RSS 阅读",multimodal:"多模态文档",cache:"Redis 缓存",
  ops:"OPS 运维",webreplay:"浏览器自动化",settings:"设置",profile:"个人信息"
};
const SUB_SEC_CRUMB={up:"UP订阅",fav:"收藏夹",bind:"小红书绑定"};
// 顶栏标签定义（key → 展示元数据）；iag 仅管理员可见
const ALL_TAB_DEFS={
  video:{key:"video",label:"链接文档化"},
  chat:{key:"chat",label:"AI 问答"},
  reader:{key:"reader",label:"文本阅读"},
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
  subscribe:{key:"subscribe",label:"链接订阅"},
  sched:{key:"sched",label:"定时任务"},
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
const uiPrefs=reactive({navDynamicIsland:true,openArtifactInNewTab:true});
const navTabCompact=ref(false);
const navTabExpanded=ref(false);
const userAvatarUrl=ref('');
const userAvatarInp=ref(null);
let navIslandTimer=null;
function loadUiPrefs(){
  const o=safeJsonParse(localStorage.getItem('sba_ui_prefs'),{});
  if(o.navDynamicIsland!=null)uiPrefs.navDynamicIsland=!!o.navDynamicIsland;
  if(o.openArtifactInNewTab!=null)uiPrefs.openArtifactInNewTab=!!o.openArtifactInNewTab;
  const av=localStorage.getItem('sba_user_avatar');
  if(av)userAvatarUrl.value=av;
}
function persistUiPrefs(){
  try{localStorage.setItem('sba_ui_prefs',JSON.stringify({navDynamicIsland:uiPrefs.navDynamicIsland,openArtifactInNewTab:uiPrefs.openArtifactInNewTab}))}catch(_){}
}
function openAppUrl(url,opts){
  const u=String(url||"").trim();
  if(!u)return;
  const o=opts||{};
  const newTab=o.newTab!=null?!!o.newTab:!!uiPrefs.openArtifactInNewTab;
  if(newTab)window.open(u,"_blank","noopener");
  else window.location.assign(u);
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
  if(key==="chat")clearChatCompletionHeart();
  if(page.value===key)return;
  closeSidePanelFs();
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
  mobileNavOpen.value=false;
  if(key==='agpz')mobileAgpzStep.value='tpl';
  if(key==='chat'&&mobilePortrait.value)chatSbCollapsed.value=true;
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
  }else if(p==="subscribe"){
    crumbs.push({key:"sub-xhs",label:"小红书",isLast:false});
    crumbs.push({key:"sub-sec-"+sub.sec,label:SUB_SEC_CRUMB[sub.sec]||sub.sec,isLast:true});
  }else if(p==="sched"){
    crumbs.push({key:"sched-jobs",label:"任务管理",isLast:true});
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
  if(c.key&&String(c.key).startsWith("sub-sec-")){
    openSubscribeSec(String(c.key).replace("sub-sec-",""));
    return;
  }
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
let skillIntelPollTimer=null;
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
  intelligence:{status:"none",analysis:null,error:""},
  usageArchives:[],
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
    {id:"orch-dsec-intel",label:"智能分析"},
    {id:"orch-dsec-io",label:"输入输出"},
    {id:"orch-dsec-body",label:"正文"}
  ];
  const atts=orchRail.skillAttachments||[];
  if(atts.length)items.push({id:"orch-dsec-files",label:"附件"});
  items.push({id:"orch-dsec-archives",label:"使用归档"});
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
  if(window.SBA_RICH_CONTENT&&typeof window.SBA_RICH_CONTENT.renderMarkdownHtml==="function"){
    inner=window.SBA_RICH_CONTENT.renderMarkdownHtml(raw);
  }else if(typeof marked!=="undefined"){
    try{
      const src=window.SBA_RICH_CONTENT&&typeof window.SBA_RICH_CONTENT.normalizeMarkdownSource==="function"
        ?window.SBA_RICH_CONTENT.normalizeMarkdownSource(raw):raw;
      if(typeof marked.setOptions==="function"){
        marked.setOptions({breaks:false,gfm:true,headerIds:false,mangle:false});
      }
      inner=marked.parse(src,{breaks:false,gfm:true});
      if(typeof DOMPurify!=="undefined")inner=DOMPurify.sanitize(inner);
    }catch(_){inner=""}
  }
  if(!inner){
    inner=raw.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/\n/g,"<br>");
  }
  return "<div class=\"orch-skill-doc\">"+inner+"</div>";
}
function renderRagSliceContent(text){
  const raw=String(text||"");
  if(!raw.trim())return"";
  const html=window.SBA_RICH_CONTENT&&typeof window.SBA_RICH_CONTENT.renderRichContentHtml==="function"
    ?window.SBA_RICH_CONTENT.renderRichContentHtml(raw)
    :raw.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/\n/g,"<br>");
  nextTick(()=>{
    if(window.SBA_RICH_CONTENT&&window.SBA_RICH_CONTENT.scheduleMermaidHydrate){
      window.SBA_RICH_CONTENT.scheduleMermaidHydrate(document.querySelector(".chat-msgs"));
    }
  });
  return html;
}
function scheduleChatRichHydrate(){
  nextTick(()=>{
    const root=document.querySelector(".chat-msgs");
    if(root&&window.SBA_RICH_CONTENT&&window.SBA_RICH_CONTENT.scheduleMermaidHydrate){
      window.SBA_RICH_CONTENT.scheduleMermaidHydrate(root);
    }
  });
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
function applySkillIntelligenceToRail(analysis){
  if(!analysis||typeof analysis!=="object")return;
  if(analysis.desc_zh&&!orchRail.skillDescZh){
    orchRail.skillDescZh=String(analysis.desc_zh);
    orchRail.skillDescZhLabel="AI 翻译";
    orchRail.docText=orchRail.skillDescZh||orchRail.docText;
  }
  if(analysis.desc_en&&!orchRail.skillDescEn){
    orchRail.skillDescEn=String(analysis.desc_en);
    orchRail.skillDescEnLabel="AI 翻译";
  }
}
async function loadSkillUsageArchives(skillId){
  if(!skillId){orchRail.usageArchives=[];return}
  try{
    const r=await fetch("/api/skills/"+encodeURIComponent(skillId)+"/usage-archives?limit=12");
    const d=await r.json();
    orchRail.usageArchives=(d&&d.archives)||[];
  }catch(_){orchRail.usageArchives=[]}
}
function _skillRailTabs(){
  const tabs=[{id:"io",label:"说明"},{id:"intel",label:"智能分析"},{id:"body",label:"正文"},{id:"flow",label:"流程"}];
  if((orchRail.skillAttachments||[]).length)tabs.push({id:"attach",label:"附件"});
  if((orchRail.usageArchives||[]).length)tabs.push({id:"archives",label:"归档"});
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
  orchRail.intelligence={status:"pending",analysis:null,error:""};
  orchRail.usageArchives=[];
  resetOrchRailView();
  orchRail.open=true;
  orchStage.fullscreen=wantFullscreen;
  orchTocActive.value=wantFullscreen?"orch-skill-detail":"orch-sec-skill";
  await _loadSkillRailData(s,sid);
  await loadSkillUsageArchives(sid);
  orchRail.tabs=_skillRailTabs();
  orchRail.tab="io";
  pollSkillFlow(sid);
  pollSkillIntelligence(sid);
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
async function pollSkillIntelligence(skillId){
  if(skillIntelPollTimer){clearInterval(skillIntelPollTimer);skillIntelPollTimer=null}
  async function tick(){
    try{
      const r=await fetch("/api/skills/"+encodeURIComponent(skillId)+"/intelligence");
      const d=await r.json();
      const st=d.status||"none";
      orchRail.intelligence.status=st;
      orchRail.intelligence.analysis=d.analysis||null;
      orchRail.intelligence.error=d.error||"";
      if(st==="done"&&d.analysis)applySkillIntelligenceToRail(d.analysis);
      if(st!=="pending"&&skillIntelPollTimer){clearInterval(skillIntelPollTimer);skillIntelPollTimer=null}
    }catch(_){}
  }
  await tick();
  if(orchRail.intelligence.status==="none"){
    try{await fetch("/api/skills/"+encodeURIComponent(skillId)+"/intelligence",{method:"POST"})}catch(_){}
    orchRail.intelligence.status="pending";
    await tick();
  }
  if(orchRail.intelligence.status==="pending")skillIntelPollTimer=setInterval(tick,2500);
}
async function refreshSkillIntelligence(skillId){
  if(!skillId||orchRail.intelligence.status==="pending")return;
  orchRail.intelligence={status:"pending",analysis:null,error:""};
  try{
    await fetch("/api/skills/"+encodeURIComponent(skillId)+"/intelligence",{method:"POST"});
  }catch(_){}
  pollSkillIntelligence(skillId);
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
  if(tabId==="flow"){
    nextTick(()=>{
      if(orchRail.flow.flow||orchRail.flow.mermaid){
        fitOrchFlowToViewport();
        if(orchRail.flow.mermaid&&!orchRail.flow.flow)renderOrchMermaid();
      }else if(orchRail.skillId&&(orchRail.flow.status==="none"||orchRail.flow.status==="error")){
        refreshSkillFlow(orchRail.skillId);
      }
    });
    return;
  }
  if(tabId==="intel"&&orchRail.skillId&&(orchRail.intelligence.status==="none"||orchRail.intelligence.status==="error")){
    refreshSkillIntelligence(orchRail.skillId);
  }
  if(tabId==="archives"&&orchRail.skillId)loadSkillUsageArchives(orchRail.skillId);
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
    const d=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:(d.detail?'SKILL 列表加载失败':String(r.status)));
    skills.value=d.skills||[];
    boardUsageStats.value=(d.usage_stats&&typeof d.usage_stats==="object")?d.usage_stats:{};
    Object.keys(skillCmdDraft).forEach(k=>{delete skillCmdDraft[k]});
    (skills.value||[]).forEach(s=>{skillCmdDraft[s.id]=String(s.command||'')});
  }catch(e){
    if(!(skills.value||[]).length)skills.value=[];
    boardUsageStats.value=boardUsageStats.value||{};
    showToastMsg("SKILL 加载失败："+String((e&&e.message)||e));
  }
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
const c=reactive({sid:"",mode:"normal",search:"",inp:"",msgs:[],th:"",model:"",agentId:"default",deepThink:false,webSearch:false,ragPrefetch:false,readComments:false,includeRss:false,uploads:[],recording:false,curTask:null,mainTaskHistory:[],taskExpanded:false,mainTaskHistoryOpen:false,taskHistPick:"",taskHistMenuOpen:false,taskHistSearchId:"",taskHistSearchName:"",taskHistSort:"time_desc",taskHistFilterSession:"",taskHistFilterStatus:"",taskHistFilterKind:"all",taskHistLoading:false,taskHistRemoteList:[],taskHistTotal:0,taskHistStats:null,taskHistMysqlInfo:null,taskHistSyncingId:"",taskHistDetailCache:{},taskHistDetailLoading:"",taskHistModalOpen:false,taskHistModalRow:null,taskHistModalFromChat:false,taskSubPlanSel:null,taskStatusMenuOpen:false,chatContextExpanded:false,chatStreaming:false,chatAbort:null,summaryPatches:[],rewriteDraft:"",rewriteCountdown:0,rewriteTimer:null,rewriteConfirmOpen:false,rewriteSnapshot:null,chatHitl:{active:false,kind:"",title:"",message:"",payload:null,traceId:"",checkpointNs:"",taskId:"",threadId:"",phase:"",editText:"",keywordsLines:"",slotDomain:"",slotModule:"",slotNeedsRag:false,ragFilter:{domain:"",module:"",doc_type:"",keyword1:"",keyword2:""},ragVocab:{domain:[],module:[],doc_type:[],keyword1:[],keyword2:[]},termNotes:"",toolOptions:[]},chatHitlResumeMsg:null,platformHealth:null,platformHealthLoading:false,platformHealthOpen:false,memoryMeta:null,chatWarmup:{loading:false,ready:false,warming:false,readCommentsCached:false,toolsTotal:0,elapsedMs:0,phases:{},error:''},chatConnect:{active:false,doneFlash:false,stallWarn:false,stallDetail:''},chatPrefs:{showToolIo:false,autoFoldChain:true,showThinkBlocks:true,showTaskRail:false,showFooterOps:true,showCopyExport:true,wideChatArea:true,maxToolRounds:15,toolTimeoutSec:60,maxToolRetry:3,distinctToolFailLimit:3,streamIntervalMs:14,streamIntervalFastMs:5,contextMaxTokens:128000,contextWarnPct:80,orchPipelineNodes:defaultOrchPipelineNodes()},chatPanelTab:"room",sessionMenuId:""});
c.chatCompletionHeart=localStorage.getItem("sba_chat_completion_heart")==="1";
function markChatCompletionHeart(){
  c.chatCompletionHeart=true;
  try{localStorage.setItem("sba_chat_completion_heart","1")}catch(_){}
}
function clearChatCompletionHeart(){
  c.chatCompletionHeart=false;
  try{localStorage.removeItem("sba_chat_completion_heart")}catch(_){}
}
function switchChatPanel(tab){
  const t=tab==="config"?"config":"room";
  c.chatPanelTab=t;
  if(t==="room")chatScrollBottom(true);
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
function isParentTaskTerminal(st){
  const s=normalizeParentTaskStatus(st,"executing");
  return ["resolved","closed","failed"].includes(s);
}
function isActiveCurTask(t){
  if(!t||!String(t.task_id||"").trim())return false;
  if(String(t.task_kind||"").toLowerCase()==="simple")return false;
  return !isParentTaskTerminal(t.status);
}
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
const MAX_PERSISTED_MESSAGES=40;
const MAX_STORED_CONTENT_CHARS=12000;
const MAX_STORED_STEP_WHAT_CHARS=240;
const MAX_STORED_STEP_RESULT_CHARS=6000;
const MAX_STEP_RESULT_BRIEF_CHARS=15;
function clampStepResultBrief(text,maxLen){
  const cap=maxLen||MAX_STEP_RESULT_BRIEF_CHARS;
  const t=String(text||"").trim();
  if(!t)return"";
  if(t.length<=cap)return t;
  return t.slice(0,cap-1)+"…";
}
function briefRagHitsBrief(hits){
  const n=(Array.isArray(hits)?hits:[]).length;
  if(!n)return"无检索结果";
  return clampStepResultBrief(`检索到 ${n} 处片段`);
}
function extractToolNameFromStep(s){
  if(s&&s.tool_name)return String(s.tool_name).trim();
  const io=getStepIoFields(s);
  const jIn=parseStepJson(io.input_text);
  if(jIn&&jIn.tool_name)return String(jIn.tool_name).trim();
  const jOut=parseStepJson(io.output_text);
  if(jOut&&jOut.tool_name)return String(jOut.tool_name).trim();
  const nm=String(s&&s.step_name||"").trim();
  const m=nm.match(/^调用\s+([^\s(（]+)/);
  if(m)return m[1];
  if(nm.startsWith("MCP 工具:"))return nm.replace(/^MCP 工具:\s*/,"").trim();
  if(nm.startsWith("MCP:"))return nm.replace(/^MCP:\s*/,"").trim();
  if(nm.startsWith("Tool Call ·"))return nm.replace(/^Tool Call ·\s*/,"").trim();
  if(nm==="联网搜索")return"web_search";
  return nm||"工具";
}
const INVOKE_MODE_CN={fixed_node:"固定节点",react:"ReAct",retry:"重试"};
const TOOL_CHANNEL_CN={builtin:"Tool Call",mcp:"MCP",skill:"SKILL"};
function normalizeLegacyToolText(text){
  let t=String(text||"").trim();
  if(!t)return"";
  t=t.replace(/^MCP\s*工具\s*:\s*/i,"MCP · ");
  t=t.replace(/^MCP\s*:\s*/i,"MCP · ");
  t=t.replace(/模型工具调用/g,"按需调用");
  t=t.replace(/模型按需检索/g,"按需检索");
  t=t.replace(/模型按需联网/g,"按需联网");
  return t;
}
function stepToolSource(s,d){
  const raw=String((s&&s.tool_source)||(d&&d.tool_source)||stepUi(s).tool_source||"").trim().toLowerCase();
  if(raw&&TOOL_CHANNEL_CN[raw])return raw;
  const w=normalizeLegacyToolText(String((s&&s.what)||(d&&d.what)||""));
  if(/\bMCP\s*·/.test(w)||/^MCP\b/.test(w))return"mcp";
  if(/\bSKILL\s*·/.test(w))return"skill";
  if(/\bTool Call\s*·/.test(w))return"builtin";
  const sn=String((s&&s.step_name)||(d&&d.step_name)||"");
  if(/^MCP\s*工具\s*:/i.test(sn)||/^MCP\s*:/i.test(sn))return"mcp";
  return"builtin";
}
function formatToolChannelLabel(s,d){
  return TOOL_CHANNEL_CN[stepToolSource(s,d)]||"Tool Call";
}
function stepInvokeMode(s,d){
  const raw=String(
    (s&&s.invoke_mode)||(d&&d.invoke_mode)||stepUi(s).invoke_mode||""
  ).trim();
  if(raw&&INVOKE_MODE_CN[raw])return raw;
  const lane=String(
    (s&&s.step_lane)||(d&&d.step_lane)||stepUi(s).step_lane||""
  ).toLowerCase();
  const ph=String((s&&s.phase)||(d&&d.phase)||stepUi(s).phase||"").toLowerCase();
  if(lane==="prefetch"||ph==="rag_decision")return"fixed_node";
  if(stepIsToolCall(s||d)&&(lane==="execution"||ph==="tool"))return"react";
  return"";
}
function formatInvokeModeLabel(s,d){
  const mode=stepInvokeMode(s,d);
  return mode?INVOKE_MODE_CN[mode]||"":"";
}
function stepInvokePurpose(s,d){
  return String(
    (s&&s.invoke_purpose)||(d&&d.invoke_purpose)||stepUi(s).invoke_purpose||""
  ).trim().slice(0,10);
}
function formatToolPillAction(s){
  const w=String(s&&s.what||"").trim();
  if(w){
    const m=w.match(/^(?:固定节点|ReAct|重试)\s*·\s*(.+)$/);
    return m?m[1]:w;
  }
  const nm=extractToolNameFromStep(s);
  return nm?`调用 ${nm}`:"工具";
}
function formatToolPillPrimary(s){
  if(s&&String(s.what||"").trim())return String(s.what).trim();
  const tag=formatInvokeModeLabel(s);
  const purpose=stepInvokePurpose(s);
  const action=formatToolPillAction(s);
  if(tag)return purpose?`${tag} · ${action} · ${purpose}`:`${tag} · ${action}`;
  return"调用 "+extractToolNameFromStep(s);
}
function formatToolPillResult(s){
  if(s&&String(s.result||"").trim())return clampStepResultBrief(String(s.result).trim());
  return summarizeToolResultCn(s);
}
function stepIsToolCall(s){
  if(!s)return false;
  if(s.kind==="tool")return true;
  if(s._ui&&s._ui.node_kind==="tool_call")return true;
  return s.node_kind==="tool_call";
}
function stepUi(s){
  return(s&&s._ui)||{};
}
function inferStepKindFromSse(d){
  const nk=String(d.node_kind||"").toLowerCase();
  const ph=String(d.phase||"").toLowerCase();
  if(nk==="tool_call"||ph==="rag"||ph==="tool")return"tool";
  if(ph==="react_round"||ph==="react_think")return"react";
  return"orch";
}
function formatRagHitsUserResult(hits,maxLen){
  const cap=maxLen||MAX_STORED_STEP_RESULT_CHARS;
  const lines=[];
  (Array.isArray(hits)?hits:[]).slice(0,8).forEach((h,i)=>{
    if(!h||typeof h!=="object")return;
    const title=String(h.title||h.file||h.source||`片段${i+1}`).trim();
    const body=String(h.snippet||h.content||h.text||"").trim();
    lines.push(`${i+1}. ${title}${body?"\n"+body.slice(0,900):""}`);
  });
  return lines.join("\n\n").slice(0,cap);
}
function formatWebSearchUserResult(tr,maxLen){
  const cap=maxLen||MAX_STORED_STEP_RESULT_CHARS;
  if(!tr||typeof tr!=="object")return"";
  const res=Array.isArray(tr.results)?tr.results:[];
  if(!res.length)return briefWebSearchDict(tr);
  const lines=[briefWebSearchDict(tr)];
  res.slice(0,6).forEach((r,i)=>{
    if(!r||typeof r!=="object")return;
    const t=String(r.title||r.url||`结果${i+1}`).slice(0,120);
    const sn=String(r.snippet||r.content||"").slice(0,400);
    lines.push(`${i+1}. ${t}${sn?"\n"+sn:""}`);
  });
  return lines.join("\n\n").slice(0,cap);
}
function extractWhatFromSse(d){
  if(!d)return"";
  if(String(d.what||"").trim())return String(d.what).trim().slice(0,MAX_STORED_STEP_WHAT_CHARS);
  const jIn=parseStepJson(d.input_text);
  const jOut=parseStepJson(d.output_text);
  const tn=String((jOut&&jOut.tool_name)||(jIn&&jIn.tool_name)||"").trim();
  const args=(jOut&&jOut.tool_args)||(jIn&&jIn.tool_args)||jIn||{};
  if(tn||d.node_kind==="tool_call"||(jOut&&jOut.tool_call)){
    if(String(d.what||"").trim())return String(d.what).trim().slice(0,MAX_STORED_STEP_WHAT_CHARS);
    const name=tn||extractToolNameFromStep({step_name:d.step_name});
    const tag=formatInvokeModeLabel(null,d);
    let action="";
    if(/read|file|文档/.test(name)&&args.path)action=`读取文件 ${String(args.path).slice(0,180)}`.trim();
    else if(/rag|kb|知识库/.test(name)){
      action="知识库检索";
    }else if(name==="web_search"||name.includes("search")){
      const q=String(args.query||"").trim()||formatQueryListBrief(args.search_queries||jIn&&jIn.search_queries);
      action=q?`联网搜索 · ${q.slice(0,10)}`:`联网搜索`;
    }else{
      const tgt=String(d.target||d.operation||"").trim();
      const channel=formatToolChannelLabel(null,d);
      action=tgt?`${channel} · ${name} · ${tgt.slice(0,120)}`:`${channel} · ${name}`;
    }
    const purpose=stepInvokePurpose(null,d);
    if(tag)return purpose?`${tag} · ${action} · ${purpose}`:`${tag} · ${action}`;
    return action;
  }
  if(String(d.invoke_mode||"").trim()&&String(d.what||"").trim()){
    return String(d.what).trim().slice(0,MAX_STORED_STEP_WHAT_CHARS);
  }
  let label=String(d.step_name||d.operation||"步骤").trim();
  if(/推理分析|工具调用规划/.test(label))label="ReAct 推理";
  return label.slice(0,MAX_STORED_STEP_WHAT_CHARS);
}
const ORCH_STEP_WHAT_CN={
  intent:"意图识别",rewrite:"问题改写",slot:"业务对齐",decompose:"任务分解",
  enhance:"意图增强",rag_decision:"知识库检索",execute_prep:"执行准备",
  react_round:"ReAct 推理",react_think:"ReAct 推理",tool:"工具执行",
};
function stepDisplayName(s){
  if(!s)return"未命名步骤";
  const what=String(s.what||"").trim();
  if(what&&!/^未命名/.test(what))return what.replace(/^推理分析\s*\/?\s*工具调用规划$/,"ReAct 推理");
  const ph=String(stepUi(s).phase||s.phase||"").toLowerCase();
  if(ph&&ORCH_STEP_WHAT_CN[ph])return ORCH_STEP_WHAT_CN[ph];
  const sn=String(s.step_name||"").trim();
  if(sn)return sn.replace(/^推理分析\s*\/?\s*工具调用规划$/,"ReAct 推理");
  return"未命名步骤";
}
function stepIsReactThink(s){
  if(!s)return false;
  const ph=String(stepUi(s).phase||s.phase||"").toLowerCase();
  if(ph==="react_round"||ph==="react_think"||s.kind==="react")return showOrchestrationThink(s);
  const what=String(s.what||s.step_name||"");
  if(/推理分析|工具调用规划/.test(what)&&showOrchestrationThink(s))return true;
  return false;
}
function stepIsOrchExecPill(s){
  if(!s||stepIsToolCall(s)||stepIsReactThink(s))return false;
  const what=String(s.what||s.step_name||"").trim();
  if(/推理分析|工具调用规划|推理与行动/.test(what))return false;
  return true;
}
function preserveCurTaskSteps(tid){
  const id=String(tid||"").trim();
  if(!id||!c.curTask||String(c.curTask.task_id||"")!==id)return[];
  return Array.isArray(c.curTask.steps)?c.curTask.steps.filter(Boolean).slice():[];
}
function touchCurTaskGroupSeq(subIndex){
  const n=Number(subIndex);
  if(!Number.isFinite(n)||n<=0||!c.curTask)return;
  c.curTask.group_seq=Math.max(Number(c.curTask.group_seq)||0,n);
}
function stepGroupId(s){
  if(!s)return"";
  const ui=stepUi(s);
  return String(ui.sub_plan_id||s.sub_plan_id||"").trim();
}
function stepSubIndex(s){
  const n=Number(s&&s.sub_index!=null?s.sub_index:stepUi(s).sub_index);
  return Number.isFinite(n)&&n>0?n:0;
}
function hasStepGroupMeta(s){
  return !!stepGroupId(s)&&stepSubIndex(s)>0;
}
function applyStepGroupMeta(st,subPlanId,subIndex){
  if(!st||!subPlanId||!subIndex)return st;
  st.sub_plan_id=subPlanId;
  st.sub_index=subIndex;
  if(!st._ui)st._ui={};
  st._ui.sub_plan_id=subPlanId;
  st._ui.sub_index=subIndex;
  return st;
}
function resolveStepGroupKey(s,fallbackSeq){
  const sid=stepGroupId(s);
  if(sid)return sid;
  const si=stepSubIndex(s);
  if(si>0)return `idx_${si}`;
  const fb=Number(fallbackSeq);
  if(Number.isFinite(fb)&&fb>0)return `seq_${fb}`;
  return"_default";
}
/** 无 sub_plan 元数据时按 ReAct 轮次补全：一轮 = 推理 + 单次工具，工具结束后下一组 */
function reconcileStepGroups(steps){
  const arr=Array.isArray(steps)?steps.filter(Boolean):[];
  let seq=0;
  let openReactPlan=null;
  let openReactIdx=0;
  let lastPrefetchStepId="";
  let lastPrefetchPlan=null;
  return arr.map(s=>{
    if(!s||typeof s!=="object")return s;
    const out={...s};
    if(hasStepGroupMeta(out)){
      seq=Math.max(seq,stepSubIndex(out));
      return out;
    }
    const ph=String(out.phase||stepUi(out).phase||"").toLowerCase();
    const lane=String(out.step_lane||stepUi(out).step_lane||"").toLowerCase();
    const nk=String(out.node_kind||stepUi(out).node_kind||"").toLowerCase();
    const label=String(out.what||out.step_name||"");
    const isReact=ph==="react_round"||ph==="react_think"||out.kind==="react"
      ||(nk==="llm_call"&&/ReAct|推理/.test(label));
    const isExecTool=stepIsToolCall(out)||out.kind==="tool"||ph==="tool"
      ||(nk==="tool_call"&&lane!=="prefetch"&&ph!=="rag_decision"&&ph!=="web");
    const isPrefetch=lane==="prefetch"||ph==="rag_decision"||ph==="web";
    const isOrch=lane==="orchestration"||ORCH_IO_PHASES.has(ph);
    if(isReact){
      seq+=1;
      openReactPlan=`subplan_react_${seq}`;
      openReactIdx=seq;
      applyStepGroupMeta(out,openReactPlan,seq);
      return out;
    }
    if(isExecTool){
      if(!openReactPlan){
        seq+=1;
        openReactPlan=`subplan_react_${seq}`;
        openReactIdx=seq;
      }
      applyStepGroupMeta(out,openReactPlan,openReactIdx);
      openReactPlan=null;
      openReactIdx=0;
      return out;
    }
    if(isOrch||isPrefetch){
      const sid=String(out.step_id||"").trim();
      if(isPrefetch&&lastPrefetchPlan&&sid&&sid===lastPrefetchStepId){
        applyStepGroupMeta(out,lastPrefetchPlan,seq);
      }else{
        seq+=1;
        const plan=`subplan_${seq}`;
        applyStepGroupMeta(out,plan,seq);
        if(isPrefetch){
          lastPrefetchPlan=plan;
          lastPrefetchStepId=sid;
        }else{
          lastPrefetchPlan=null;
          lastPrefetchStepId="";
        }
      }
      openReactPlan=null;
      openReactIdx=0;
      return out;
    }
    seq+=1;
    applyStepGroupMeta(out,`subplan_${seq}`,seq);
    openReactPlan=null;
    openReactIdx=0;
    return out;
  });
}
function copyStepGroupFields(st,d){
  if(!st||!d)return st;
  ["sub_plan_id","sub_index","step_lane","phase","node_kind","react_round","step_name","invoke_mode","invoke_purpose","what"].forEach(k=>{
    if(d[k]!=null&&d[k]!=="")st[k]=d[k];
  });
  return st;
}
function dedupeContinueMainSteps(steps){
  const arr=Array.isArray(steps)?steps.filter(Boolean):[];
  const dropBoot=/^(step_boot|step_early|orch_boot_)/i;
  const filtered=arr.filter(s=>{
    const id=String(s.step_id||"");
    if(dropBoot.test(id))return false;
    const w=String(s.what||s.step_name||"");
    const r=String(s.result||"").trim();
    if(w==="主任务编排"||w==="未命名步骤")return false;
    if((w==="延续主任务"||w.includes("延续主任务"))&&/^正在执行/.test(r))return false;
    if(/^正在执行[:：]/.test(r)&&!stepGroupId(s))return false;
    return true;
  });
  let lastCont=-1,lastIntent=-1;
  filtered.forEach((s,i)=>{
    const w=String(s.what||s.step_name||"");
    if(w==="延续主任务"||w.includes("延续主任务"))lastCont=i;
    if(w==="意图识别"||String(s.phase||"").toLowerCase()==="intent")lastIntent=i;
  });
  return filtered.filter((s,i)=>{
    const w=String(s.what||s.step_name||"");
    if(w==="延续主任务"||w.includes("延续主任务"))return i===lastCont;
    if(w==="意图识别"||String(s.phase||"").toLowerCase()==="intent")return i===lastIntent;
    return true;
  });
}
function extractResultFromSse(d){
  if(!d)return"";
  const rb=String(d.result_brief||d.description||"").trim();
  if(rb&&!looksLikeJsonBlob(rb))return clampStepResultBrief(rb);
  if(String(d.result||"").trim())return clampStepResultBrief(String(d.result).trim());
  const jOut=parseStepJson(d.output_text);
  const ph=String(d.phase||"").toLowerCase();
  if(jOut&&jOut.error)return clampStepResultBrief(String(jOut.error));
  if(jOut&&jOut.tool_call){
    const briefCn=String(jOut.result_brief_cn||"").trim();
    if(briefCn&&!looksLikeJsonBlob(briefCn))return clampStepResultBrief(briefCn);
    const tr=coerceToolResult(jOut.tool_result);
    if(tr&&Array.isArray(tr.hits)&&tr.hits.length)return briefRagHitsBrief(tr.hits);
    if(tr&&Array.isArray(tr.slices)&&tr.slices.length)return briefRagHitsBrief(tr.slices);
    if(tr&&Array.isArray(tr.rag_slices)&&tr.rag_slices.length)return briefRagHitsBrief(tr.rag_slices);
    if(tr&&Array.isArray(tr.results)&&tr.results.length){
      const line=briefWebSearchDict(tr);
      if(line)return clampStepResultBrief(line);
    }
    let msg=String(jOut.result_msg||"").trim();
    if(msg.startsWith((jOut.tool_name||"")+":"))msg=msg.slice(String(jOut.tool_name||"").length+1).trim();
    if(msg&&!looksLikeJsonBlob(msg))return clampStepResultBrief(msg);
    return"执行完成";
  }
  if(jOut&&typeof jOut==="object"&&!jOut.tool_call){
    const orchBrief=String(jOut.result_brief_cn||jOut.summary_cn||"").trim();
    if(orchBrief&&!looksLikeJsonBlob(orchBrief))return clampStepResultBrief(orchBrief);
    if(ph==="intent"){
      const mode=String(jOut.mode||jOut.task_action||"").toLowerCase();
      if(mode==="continue_main")return clampStepResultBrief(orchBrief||"延续主任务");
      if(mode==="simple")return clampStepResultBrief("简单问答");
      if(mode==="new_main")return clampStepResultBrief("新建主任务");
    }
  }
  const think=stripReactDisplayMarkers(String(d.think_text||"").trim());
  if(think&&!looksLikeJsonBlob(think)&&ph!=="intent")return think.slice(0,MAX_STORED_STEP_RESULT_CHARS);
  return"";
}
/** 用户视角：只做两件事 — 做了啥 / 结果是什么（落盘仅 what+result） */
function thinkingStepUserLayer(d){
  if(!d||!d.step_id)return null;
  const what=extractWhatFromSse(d);
  const result=extractResultFromSse(d);
  if(!what&&!result)return null;
  return copyStepGroupFields({
    step_id:d.step_id,
    kind:inferStepKindFromSse(d),
    what:what||String(d.step_name||"步骤").slice(0,MAX_STORED_STEP_WHAT_CHARS),
    result:result||"",
  },d);
}
function attachStepUiRuntime(st,d){
  if(!st||!d)return st;
  const ph=String(d.phase||"").toLowerCase();
  copyStepGroupFields(st,d);
  st._ui={
    node_kind:d.node_kind||(st.kind==="tool"?"tool_call":ph==="react_round"?"llm_call":"sub_task"),
    phase:d.phase||st.phase||"",
    status:(()=>{
      let s=String(d.status||st._ui&&st._ui.status||st.status||"").toLowerCase();
      if((!s||["running","thinking","started","executing"].includes(s))&&(String(d.output_text||"").trim()||String(d.result||"").trim()))
        return"done";
      return s||"done";
    })(),
    duration_ms:d.elapsed_ms!=null?d.elapsed_ms:d.duration_ms,
    sub_plan_id:d.sub_plan_id||st.sub_plan_id,
    sub_index:d.sub_index!=null?d.sub_index:st.sub_index,
    step_lane:d.step_lane||st.step_lane||"",
    io_expanded:!!(c.chatPrefs&&c.chatPrefs.showToolIo),
    token_count:d.token_count,
    success:d.success,
    confidence:d.confidence,
    invoke_mode:d.invoke_mode||"",
    invoke_purpose:d.invoke_purpose||"",
  };
  if(d.io_expanded!=null)st._ui.io_expanded=!!d.io_expanded;
  return st;
}
function slimThinkingStepForStorage(step){
  if(!step||typeof step!=="object")return null;
  const out={};
  if(step.step_id)out.step_id=step.step_id;
  const kind=step.kind||inferStepKindFromSse(step);
  if(kind)out.kind=kind;
  const what=String(step.what||step.step_name||"").trim();
  let result=String(step.result||step.result_brief||"").trim();
  if(!result&&step.description&&!looksLikeJsonBlob(step.description))result=String(step.description).trim();
  if(what)out.what=what.slice(0,MAX_STORED_STEP_WHAT_CHARS);
  if(result)out.result=result.slice(0,MAX_STORED_STEP_RESULT_CHARS);
  ["sub_plan_id","step_lane","phase","node_kind","invoke_mode","invoke_purpose"].forEach(k=>{
    if(step[k]!=null&&step[k]!=="")out[k]=step[k];
  });
  const si=Number(step.sub_index!=null?step.sub_index:stepUi(step).sub_index);
  if(Number.isFinite(si)&&si>0)out.sub_index=si;
  const rr=Number(step.react_round);
  if(Number.isFinite(rr)&&rr>=0)out.react_round=rr;
  if(!out.what&&!out.result)return null;
  return out;
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
  if(s&&String(s.result||"").trim())return clampStepResultBrief(String(s.result).trim());
  const ui=stepUi(s);
  const st=String(ui.status||s.status||"").toLowerCase();
  const inFlight=["running","thinking","started","executing"].includes(st);
  const io=getStepIoFields(s);
  const hasOut=!!(String(io.output_text||"").trim());
  if(inFlight){
    const wait=String(s&&s.status_text||"").trim();
    if(wait&&wait!=="已完成"&&wait!=="完成")return wait.slice(0,120);
    return hasOut?"处理中…":"执行中…";
  }
  const rb=String(s&&(s.result_brief||s.description)||"").trim();
  if(rb&&rb!=="已返回"&&!looksLikeJsonBlob(rb))return clampStepResultBrief(rb);
  const j=parseStepJson(io.output_text);
  if(j&&j.tool_call===true){
    const name=j.tool_name||extractToolNameFromStep(s);
    if(j.error)return clampStepResultBrief(String(j.error));
    const briefCn=String(j.result_brief_cn||"").trim();
    if(briefCn&&!looksLikeJsonBlob(briefCn))return clampStepResultBrief(briefCn);
    const tr=coerceToolResult(j.tool_result);
    if(/rag|kb|知识库/.test(String(name||""))&&tr){
      if(Array.isArray(tr.hits)&&tr.hits.length)return briefRagHitsBrief(tr.hits);
      if(Array.isArray(tr.slices)&&tr.slices.length)return briefRagHitsBrief(tr.slices);
    }
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
function formatStepBrief(s){
  if(!s)return"";
  if(stepIsToolCall(s))return formatToolPillPrimary(s)+" · "+formatToolPillResult(s).slice(0,120);
  const what=String(s.what||s.step_name||s.operation||"步骤").trim();
  const result=String(s.result||s.result_brief||s.description||"").trim();
  if(what&&result)return what+" — "+result.slice(0,120);
  if(what)return what;
  return"步骤";
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
    x.thinking=(Array.isArray(x.thinking)?x.thinking:[]).map(slimThinkingStepForStorage).filter(Boolean);
    x.thinkingExpanded=x.thinkingExpanded!==false;
    hydrateMsgCitations(x);
  }
  return x;
}
/** 当前会话内存：SSE 全量 IO 仅用于 live 展开，不落 session / PUT */
function ensureStepIoCache(){
  if(!c._stepIoCache||typeof c._stepIoCache!=="object")c._stepIoCache={};
  return c._stepIoCache;
}
function cacheStepIoFromEvent(d){
  if(!d||!d.step_id)return;
  const cache=ensureStepIoCache();
  const id=String(d.step_id);
  const prev=cache[id]||{input_text:"",output_text:"",think_text:""};
  if(d.input_text!=null&&d.input_text!=="")prev.input_text=String(d.input_text);
  if(d.output_text!=null&&d.output_text!=="")prev.output_text=String(d.output_text);
  if(d.think_text!=null&&d.think_text!==""){
    prev.think_text=stripReactDisplayMarkers(String(d.think_text));
  }
  cache[id]=prev;
  const keys=Object.keys(cache);
  if(keys.length>240){
    keys.slice(0,keys.length-200).forEach(k=>{delete cache[k];});
  }
}
function appendStepThinkDeltaToCache(stepId,content){
  if(!stepId||!content)return;
  const cache=ensureStepIoCache();
  const id=String(stepId);
  const prev=cache[id]||{input_text:"",output_text:"",think_text:""};
  prev.think_text=stripReactDisplayMarkers((prev.think_text||"")+String(content));
  cache[id]=prev;
}
function getStepIoFields(s){
  if(!s||!s.step_id)return {input_text:"",output_text:"",think_text:""};
  const hit=ensureStepIoCache()[String(s.step_id)];
  return hit||{input_text:"",output_text:"",think_text:""};
}
function stepForIoDisplay(s){
  return Object.assign({},s||{},getStepIoFields(s));
}
const SPAN_STORAGE_KEYS=["task_id","trace_id","task_kind","ephemeral","persist_main_task","status"];
function slimSpanForStorage(span){
  if(!span||typeof span!=="object")return null;
  const out={};
  SPAN_STORAGE_KEYS.forEach(k=>{if(span[k]!=null)out[k]=span[k];});
  return Object.keys(out).length?out:null;
}
function slimMessageForStorage(msg){
  if(!msg||typeof msg!=="object")return null;
  const role=msg.role;
  if(role!=="user"&&role!=="assistant")return null;
  let content=String(msg.content||"");
  if(content.length>MAX_STORED_CONTENT_CHARS)content=content.slice(0,MAX_STORED_CONTENT_CHARS-20)+"\n\n…（正文已截断）";
  const thinking=(Array.isArray(msg.thinking)?msg.thinking:[]).map(slimThinkingStepForStorage).filter(Boolean);
  const out={role,content,thinking:thinking.length?thinking:null,span:slimSpanForStorage(msg.span),thinkingExpanded:!!msg.thinkingExpanded};
  if(msg.task_id)out.task_id=msg.task_id;
  if(msg.result_status)out.result_status=msg.result_status;
  if(msg.task_kind)out.task_kind=msg.task_kind;
  if(msg.ephemeral)out.ephemeral=true;
  if(msg.task_audit&&typeof msg.task_audit==="object"){
    const ta={};
    ["task_id","task_kind","status","ephemeral","user_query","query_summary"].forEach(k=>{if(msg.task_audit[k]!=null)ta[k]=msg.task_audit[k];});
    if(Object.keys(ta).length)out.task_audit=ta;
  }
  return out;
}
function slimCurTaskForStorage(curTask){
  if(!curTask||typeof curTask!=="object")return null;
  const out={};
  ["task_id","user_query","query_summary","status","task_kind","sub_plan_id","group_seq"].forEach(k=>{if(curTask[k]!=null)out[k]=curTask[k];});
  const steps=(Array.isArray(curTask.steps)?curTask.steps:[]).slice(-16).map(slimThinkingStepForStorage).filter(Boolean);
  if(steps.length)out.steps=steps;
  return Object.keys(out).length?out:null;
}
function slimMainTaskHistoryForStorage(hist){
  if(!Array.isArray(hist))return [];
  return hist.slice(-32).filter(h=>h&&h.task_id).map(h=>{
    const o={};
    ["task_id","user_query","query_summary","status","task_kind","result_status","async_pipeline_pending"].forEach(k=>{if(h[k]!=null)o[k]=h[k];});
    return o;
  });
}
function slimPayloadForServer(payload){
  const p=payload&&typeof payload==="object"?payload:{};
  let msgs=(Array.isArray(p.messages)?p.messages:[]).map(slimMessageForStorage).filter(Boolean);
  if(msgs.length>MAX_PERSISTED_MESSAGES)msgs=msgs.slice(-MAX_PERSISTED_MESSAGES);
  return{
    ...p,
    messages:msgs,
    cur_task:slimCurTaskForStorage(p.cur_task),
    main_task_history:slimMainTaskHistoryForStorage(p.main_task_history),
  };
}
const STEP_LANE_ORDER={orchestration:0,prefetch:1,execution:2};
function stepLaneOrder(lane){
  const k=String(lane||"execution").toLowerCase();
  return STEP_LANE_ORDER[k]!=null?STEP_LANE_ORDER[k]:2;
}
function groupStepsBySubPlan(steps){
  const map=new Map();
  reconcileStepGroups(Array.isArray(steps)?steps:[]).filter(Boolean).forEach((s,ord)=>{
    if(!s||typeof s!=="object")return;
    const ui=stepUi(s);
    const pid=resolveStepGroupKey(s,ord+1);
    if(!map.has(pid)){
      map.set(pid,{
        sub_plan_id:stepGroupId(s)||pid,
        sub_index:stepSubIndex(s)||ui.sub_index||s.sub_index||0,
        step_lane:ui.step_lane||s.step_lane||"",
        steps:[],
      });
    }
    const g=map.get(pid);
    if(!g||!Array.isArray(g.steps))return;
    g.steps.push(s);
    const si=stepSubIndex(s)||Number(s.sub_index);
    if(Number.isFinite(si)&&si>0)g.sub_index=si;
    if(s.step_lane&&!g.step_lane)g.step_lane=s.step_lane;
  });
  return Array.from(map.values())
    .filter(p=>p&&Array.isArray(p.steps))
    .sort((a,b)=>{
      const ia=Number(a.sub_index)||0;
      const ib=Number(b.sub_index)||0;
      if(ia!==ib)return ia-ib;
      const oa=stepLaneOrder(a.step_lane||a.steps?.[0]?.step_lane);
      const ob=stepLaneOrder(b.step_lane||b.steps?.[0]?.step_lane);
      if(oa!==ob)return oa-ob;
      return String(a.sub_plan_id||"").localeCompare(String(b.sub_plan_id||""));
    })
    .map((g,i)=>({...g,display_index:i+1}));
}
function stepIsSkipped(s){
  if(!s)return false;
  if(s.executed===false)return true;
  const st=String(stepUi(s).status||s.status||"").toLowerCase();
  return st==="skipped"||st==="skip";
}
function pillStatusClass(s){
  if(stepIsSkipped(s))return"skip";
  const st=String(stepUi(s).status||s.status||"").toLowerCase();
  if(["failed","abnormal"].includes(st)||s.success===false||stepUi(s).success===false)return"fail";
  if(["running","thinking","started","executing"].includes(st)){
    if(stepHasCompletedOutput(s)&&!s._thinkStreaming)return"ok";
    return"run";
  }
  return"ok";
}
function execPillClass(s){
  if(!s)return"orch";
  if(stepIsToolCall(s))return["is-tool",pillStatusClass(s)].filter(Boolean).join(" ");
  if(stepIsSkipped(s))return"skip orch";
  const st=pillStatusClass(s);
  return[st,st!=="ok"?"orch":""].filter(Boolean).join(" ");
}
function platformHealthStats(h){
  if(!h)return{ok:0,warn:0,error:0};
  if(h.ready&&h.summary&&typeof h.summary==='object')return h.summary;
  const summary={ok:0,warn:0,error:0};
  for(const it of (Array.isArray(h.items)?h.items:[])){
    const st=it.status||'error';
    if(st==='ok')summary.ok++;
    else if(st==='warn')summary.warn++;
    else summary.error++;
  }
  if(!h.items?.length&&h.error)summary.error=Math.max(summary.error,1);
  return summary;
}
const platformHealthSummaryText=computed(()=>{
  if(c.platformHealthLoading)return'检测中…';
  const h=c.platformHealth;
  if(!h)return'启动检测中…';
  const s=platformHealthStats(h);
  const parts=[`${s.ok} 正常`];
  if(s.warn)parts.push(`${s.warn} 降级`);
  if(s.error)parts.push(`${s.error} 异常`);
  let text=parts.join(' · ');
  const bad=(h.items||[]).filter(i=>i.status==='warn'||i.status==='error');
  if(bad.length){
    const names=bad.map(i=>String(i.label||i.id||'').trim()).filter(Boolean);
    if(names.length)text+=' · '+names.join('、');
  }else if(!h.ready&&h.error){
    text='后端不可用 · '+String(h.error).slice(0,48);
  }
  return text;
});
const platformHealthSummaryClass=computed(()=>{
  if(c.platformHealthLoading)return'';
  const h=c.platformHealth;
  if(!h)return'';
  const s=platformHealthStats(h);
  if(!h.ready&&h.error)return'ph-warn';
  if((s.error||0)>0||(s.warn||0)>0)return'ph-warn';
  return'ph-ok';
});
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
    const r=await fetchWithTimeout(url,{headers:authBearerHeaders()},20000);
    const d=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(fmtApiErr(d,r));
    c.platformHealth=d&&typeof d==='object'?d:null;
  }catch(e){
    const errMsg=String(e&&e.message||e);
    c.platformHealth={
      ready:false,
      items:[{
        id:'backend_api',
        label:'后端 API (8000)',
        status:'error',
        latency_ms:0,
        error:errMsg,
        settings_href:'',
      }],
      summary:{ok:0,warn:0,error:1},
      all_ok:false,
      error:errMsg,
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
  const disp=Number(plan.display_index);
  const n=plan.sub_index;
  const idx=Number.isFinite(disp)&&disp>0?disp:(Number.isFinite(Number(n))&&Number(n)>0?Number(n):(epi||0)+1);
  const steps=Array.isArray(plan.steps)?plan.steps:[];
  const lane=String(plan.step_lane||steps[0]?.step_lane||"").toLowerCase();
  if(lane==="prefetch"){
    const tool=steps.find(s=>s&&s.node_kind==="tool_call");
    return tool?`步骤组 #${idx} · ${String(tool.step_name||"检索预取")}`:`步骤组 #${idx} · 检索预取`;
  }
  const tool=steps.find(s=>stepIsToolCall(s)||String(stepUi(s).node_kind||s.node_kind||"").toLowerCase()==="tool_call");
  const react=steps.find(s=>{
    const ph=String(s&&s.phase||stepUi(s).phase||"").toLowerCase();
    return ph==="react_round"||ph==="react_think"||stepIsReactThink(s);
  });
  if(tool&&react){
    const tlab=formatToolPillPrimary(tool).replace(/^(?:固定节点|ReAct|重试)\s*·\s*/,"");
    return`步骤组 #${idx} · ReAct → ${tlab.replace(/^调用\s+/,"")}`;
  }
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
    lines.push("<strong>意图模式</strong>："+(modeCn[mode]||mode));
  }
  if(j.reason)lines.push("<strong>原因</strong>："+String(j.reason).slice(0,200));
  if(j.rewritten_query)lines.push("<strong>改写后</strong>："+String(j.rewritten_query).slice(0,200));
  if(j.query_summary&&!j.rewritten_query)lines.push("<strong>摘要</strong>："+String(j.query_summary).slice(0,120));
  if(j.domain)lines.push("<strong>业务域</strong>："+j.domain);
  if(j.operation_type)lines.push("<strong>操作</strong>："+j.operation_type);
  if(j.needs_rag!=null)lines.push("<strong>需知识库</strong>："+(j.needs_rag?"是":"否"));
  if(j.needs_web_search!=null)lines.push("<strong>需联网</strong>："+(j.needs_web_search?"是":"否"));
  const arrKeys=[["retrieval_hints","检索提示"],["search_keyword_queries","检索词"],["verification_points","核验"],["sub_tasks","子任务"],["retrieval_terms","检索词"],["query_keywords","原问关键词"],["keywords","原问关键词"]];
  for(const[key,label]of arrKeys){
    const raw=j[key];
    if(Array.isArray(raw)&&raw.length){
      raw.slice(0,5).forEach((x,i)=>lines.push("<strong>"+label+(i+1)+"</strong>："+String(x).slice(0,100)));
      if(raw.length>5)lines.push("…共 "+raw.length+" 条");
    }
  }
  if(j.task_summary)lines.push("<strong>任务摘要</strong>："+String(j.task_summary).slice(0,200));
  if(j.summary_cn)lines.push(String(j.summary_cn));
  if(j.search_objective)lines.push("<strong>检索目标</strong>："+String(j.search_objective).slice(0,160));
  if(j.result_brief_cn)lines.push(String(j.result_brief_cn));
  if(!lines.length&&ph)return"";
  return lines.join("<br>");
}
function formatOrchThinkDisplay(s){
  if(!s)return"";
  if(String(s.result||"").trim())return String(s.result).trim();
  const ph=String(stepUi(s).phase||s.phase||"").toLowerCase();
  if(ph==="intent"){
    const io=getStepIoFields(s);
    const j=parseStepJson(io.output_text);
    const brief=String(j&&j.result_brief_cn||s.result_brief||"").trim();
    if(brief)return brief;
    const mode=String(j&&j.mode||j&&j.task_action||"").toLowerCase();
    if(mode==="continue_main")return"延续主任务";
  }
  const raw=String(getStepIoFields(s).think_text||"").trim();
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
  if(s&&String(s.result||"").trim()&&s.kind==="react")return true;
  const io=getStepIoFields(s);
  if(!s||!String(io.think_text||"").trim())return false;
  if(c.chatPrefs&&c.chatPrefs.showThinkBlocks===false)return false;
  if(stepIsToolCall(s))return false;
  const ph=String(stepUi(s).phase||s.phase||"").toLowerCase();
  if(ph==='react_round'||ph==='react_think'){
    return !!String(io.think_text||"").trim();
  }
  if(ph==='tool')return false;
  if(s.think_kind==="node_analysis")return true;
  if(ORCH_IO_PHASES.has(ph))return true;
  return!!s.llm_powered;
}
function hasStepIo(s){
  if(!s)return false;
  if(stepIsSkipped(s))return false;
  const io=getStepIoFields(s);
  if(stepIsToolCall(s))return !!(io.input_text||io.output_text);
  const ph=String(stepUi(s).phase||s.phase||"").toLowerCase();
  if(ORCH_IO_PHASES.has(ph)){
    return !!(String(io.input_text||"").trim()||String(io.output_text||"").trim());
  }
  return false;
}
function formatOrchStepInputDisplay(s){
  if(!s)return"—";
  s=stepForIoDisplay(s);
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
  return String(sl.parent_document||sl.title||sl.parent_name||"知识库片段").trim()||"知识库片段";
}
/** 从回答正文拆出「文献切片明细 / 注释」，正文不再在气泡里重复展示占位链接 */
function splitAnswerBodyAndCitations(raw){
  const text=String(raw||"");
  const sliceRe=/(?:^|\n)#{1,3}\s*文献切片明细\s*(?:\n|$)/;
  const annoRe=/(?:^|\n)#{1,3}\s*注释\s*(?:\n|$)/;
  const sliceIdx=text.search(sliceRe);
  const annoIdx=text.search(annoRe);
  let body=text;
  let sliceSection="";
  let annoSection="";
  if(sliceIdx>=0){
    const end=annoIdx>sliceIdx?annoIdx:text.length;
    sliceSection=text.slice(sliceIdx,end).replace(/^#{1,3}\s*文献切片明细\s*/m,"").trim();
    body=text.slice(0,sliceIdx).trim();
  }
  if(annoIdx>=0){
    annoSection=text.slice(annoIdx).replace(/^#{1,3}\s*注释\s*/m,"").trim();
    if(annoIdx<0||sliceIdx<0||annoIdx<sliceIdx)body=text.slice(0,annoIdx).trim();
  }
  body=body.replace(/\n?---\s*$/,"").trim();
  return{body,sliceSection,annoSection};
}
function parseSlicesFromMarkdown(section){
  if(!String(section||"").trim())return[];
  const re=/[-•]\s*切片\[(\d+)\][：:]([\s\S]*?)(?=\n[-•]\s*切片\[|\n#{1,3}\s|$)/g;
  const out=[];
  let m;
  while((m=re.exec(section))){
    const block=String(m[2]||"").trim();
    const parentM=block.match(/所属父文档[《「]([^》」]+)[》」](?:[（(]([^）)]+)[）)])?/);
    const sliceM=block.match(/切片全文[：:]\s*([\s\S]*?)(?=\n\s*父文档全文|$)/);
    let parentPath="";
    const pathM=block.match(/父文档全文[：:]\s*([\s\S]*?)(?=\n\s*[-•]|$)/);
    if(pathM){
      const p=String(pathM[1]||"").trim();
      const seeM=p.match(/见路径\s*([^\s，,；;]+)/);
      if(seeM)parentPath=seeM[1].trim();
      else if(!/前端可点击|点击查看/.test(p))parentPath=p;
    }
    if(!parentPath&&parentM&&parentM[2])parentPath=String(parentM[2]).trim();
    out.push({
      ref_id:Number(m[1]),
      parent_name:parentM?String(parentM[1]).trim():"知识库片段",
      source_file:parentPath,
      slice_content:(sliceM?sliceM[1]:block).trim(),
    });
  }
  return out;
}
function parseAnnotationsFromMarkdown(section){
  if(!String(section||"").trim())return[];
  return String(section).split(/\n(?=\d+\.\s)/).map(line=>{
    const t=line.trim();
    if(!t)return null;
    const m=t.match(/^(\d+)\.\s*([\s\S]+)/);
    if(!m)return null;
    return{index:Number(m[1]),text:String(m[2]).trim()};
  }).filter(Boolean);
}
function prefetchSliceToCitation(sl){
  if(!sl||typeof sl!=="object")return null;
  return{
    ref_id:sl.ref_id,
    parent_name:ragSliceParentName(sl),
    source_file:String(sl.source_file||"").trim(),
    slice_content:String(sl.content||sl.snippet||"").trim(),
    score:sl.score,
    _raw:sl,
  };
}
function hydrateMsgCitations(aiMsg){
  if(!aiMsg||aiMsg.role!=="assistant")return;
  const raw=String(aiMsg.content||"");
  const{body,sliceSection,annoSection}=splitAnswerBodyAndCitations(raw);
  aiMsg._answerBody=body;
  const parsed=parseSlicesFromMarkdown(sliceSection);
  if(Array.isArray(aiMsg.ragPrefetchSlices)&&aiMsg.ragPrefetchSlices.length){
    aiMsg.ragCitationSlices=aiMsg.ragPrefetchSlices.map(prefetchSliceToCitation).filter(Boolean);
  }else if(parsed.length){
    aiMsg.ragCitationSlices=parsed;
  }else if(!Array.isArray(aiMsg.ragCitationSlices)){
    aiMsg.ragCitationSlices=[];
  }
  aiMsg.ragCitationAnnotations=parseAnnotationsFromMarkdown(annoSection);
}
function answerBodyForMsg(m){
  if(!m)return"";
  if(m._answerStreaming)return String(m.content||"");
  if(m._answerBody!=null)return String(m._answerBody);
  hydrateMsgCitations(m);
  return String(m._answerBody!=null?m._answerBody:m.content||"");
}
function ragCitationSlicesForMsg(m){
  if(!m)return[];
  if(Array.isArray(m.ragCitationSlices)&&m.ragCitationSlices.length)return m.ragCitationSlices;
  if(Array.isArray(m.ragPrefetchSlices)&&m.ragPrefetchSlices.length)
    return m.ragPrefetchSlices.map(prefetchSliceToCitation).filter(Boolean);
  hydrateMsgCitations(m);
  return Array.isArray(m.ragCitationSlices)?m.ragCitationSlices:[];
}
function ragCitationAnnotationsForMsg(m){
  if(!m)return[];
  if(Array.isArray(m.ragCitationAnnotations))return m.ragCitationAnnotations;
  hydrateMsgCitations(m);
  return Array.isArray(m.ragCitationAnnotations)?m.ragCitationAnnotations:[];
}
function hasRagCitationCards(m){
  return ragCitationSlicesForMsg(m).length>0||ragCitationAnnotationsForMsg(m).length>0;
}
const ragParentBodyCache=reactive({});
async function ensureRagParentBody(path){
  const p=String(path||"").trim();
  if(!p)return"";
  const hit=ragParentBodyCache[p];
  if(hit&&hit!=="__loading__")return hit;
  ragParentBodyCache[p]="__loading__";
  try{
    let text="";
    let err="";
    try{
      const rText=await fetch("/api/doc/rag/file/text?path="+encodeURIComponent(p)+"&limit=120000",{headers:authBearerHeaders()});
      const dText=await parseApiJson(rText);
      if(dText&&dText.ok){
        text=String(dText.text||"").trim();
      }else{
        err=String(dText&&dText.error||"");
      }
    }catch(e){
      err=String(e&&e.message||e||"");
    }
    if(!text){
      const r=await fetch("/api/doc/rag/file/chunks?path="+encodeURIComponent(p)+"&limit=300",{headers:authBearerHeaders()});
      const d=await parseApiJson(r);
      if(!d.ok)throw new Error(d.error||err||"加载失败");
      const chunks=Array.isArray(d.chunks)?d.chunks:[];
      text=chunks.map(c=>String(c.content||c.text||c.snippet||c.preview||"").trim()).filter(Boolean).join("\n\n");
    }
    ragParentBodyCache[p]=text||"（父文档无可显示正文，请在 RAG 知识库页打开该路径）";
  }catch(e){
    ragParentBodyCache[p]="（加载父文档失败："+String(e&&e.message||e).slice(0,120)+"）";
  }
  return ragParentBodyCache[p];
}
async function onRagCiteParentToggle(ev,sl){
  if(!ev||!ev.target||!ev.target.open||!sl)return;
  const path=String(sl.source_file||"").trim();
  if(path)await ensureRagParentBody(path);
}
function ragParentBodyText(path){
  const p=String(path||"").trim();
  if(!p)return"";
  const v=ragParentBodyCache[p];
  if(!v||v==="__loading__")return"";
  return v;
}
function ragParentBodyLoading(path){
  const v=ragParentBodyCache[String(path||"").trim()];
  return v==="__loading__";
}
function ragSlicePreview(text,max){
  const t=String(text||"").trim();
  const cap=max||320;
  if(t.length<=cap)return t;
  return t.slice(0,cap)+"…";
}
const ragSliceModal=reactive({show:false,slice:null});
function openRagSliceDetail(sl){
  if(!sl||typeof sl!=="object")return;
  ragSliceModal.slice=sl;
  ragSliceModal.show=true;
  const path=String(sl.source_file||"").trim();
  if(path)ensureRagParentBody(path);
}
function closeRagSliceDetail(){
  ragSliceModal.show=false;
  ragSliceModal.slice=null;
}
function sortedRagSlicesForMsg(m,extra){
  const raw=(m&&Array.isArray(m.ragPrefetchSlices)&&m.ragPrefetchSlices.length)
    ?m.ragPrefetchSlices
    :(Array.isArray(extra)?extra:[]);
  return [...raw].sort((a,b)=>Number(b.score||0)-Number(a.score||0));
}
function ragCapsuleScoreLabel(sl){
  if(!sl||sl.score==null||sl.score==="")return"—";
  const n=Number(sl.score);
  return Number.isFinite(n)?n.toFixed(3):"—";
}
function toggleRagCapsule(m,sl){
  if(!m||!sl)return;
  const id=sl.ref_id;
  if(m._activeRagCapsule===id){
    m._activeRagCapsule=null;
    m._activeRagSlice=null;
  }else{
    m._activeRagCapsule=id;
    m._activeRagSlice=sl;
  }
}
function activeRagSliceForMsg(m){
  return m&&m._activeRagSlice?m._activeRagSlice:null;
}
function jumpToRagCapsule(m,refId){
  if(!m)return;
  const slices=ragCitationSlicesForMsg(m);
  let sl=slices.find(s=>String(s.ref_id)===String(refId));
  if(!sl){
    const prefetch=(m.ragPrefetchSlices||[]).find(s=>String(s.ref_id)===String(refId));
    if(prefetch)sl=prefetchSliceToCitation(prefetch);
  }
  if(!sl)return;
  const raw=sl._raw||{
    ref_id:sl.ref_id,
    content:sl.slice_content||sl.content,
    source_file:sl.source_file,
    parent_document:sl.parent_name,
    title:sl.parent_name,
    score:sl.score,
  };
  toggleRagCapsule(m,raw);
  nextTick(()=>{
    const el=document.querySelector('[data-rag-capsule="'+refId+'"]');
    if(!el)return;
    el.scrollIntoView({behavior:'smooth',block:'nearest'});
    el.classList.remove('rag-capsule-flash');
    void el.offsetWidth;
    el.classList.add('rag-capsule-flash');
  });
}
function onAnswerCitationClick(ev,m){
  if(window.SBA_RICH_CONTENT&&typeof window.SBA_RICH_CONTENT.handleKbPictureClick==="function"){
    if(window.SBA_RICH_CONTENT.handleKbPictureClick(ev,(src)=>{mm.lightboxSrc=src}))return;
  }
  const t=ev&&ev.target;
  if(!t||!t.classList||!t.classList.contains('rag-cite-sup'))return;
  ev.preventDefault();
  const ref=t.getAttribute('data-ref');
  if(ref)jumpToRagCapsule(m,ref);
}
function injectAnswerCitationSuperscripts(html,slices){
  if(!html||!slices||!slices.length)return html;
  const valid=new Set(slices.map(s=>String(s.ref_id)));
  const mk=(nums)=>String(nums||"").split(/[,，]/).map(x=>x.trim()).filter(Boolean)
    .filter(n=>valid.has(n))
    .map(n=>'<sup class="rag-cite-sup" data-ref="'+n+'" title="查看文献切片 '+n+'">'+n+'</sup>')
    .join("");
  let out=html;
  out=out.replace(/【(\d+(?:[，,]\d+)*)】/g,(_,g)=>mk(g));
  out=out.replace(/([\u4e00-\u9fff])(\d+(?:[，,]\d+)*)(?=[。！？；<\s])/g,(full,ch,nums)=>ch+mk(nums));
  return out;
}
async function openRagParentInKb(sl){
  const path=String(sl&&sl.source_file||"").trim();
  if(!path){showToastMsg("无父文档路径");return;}
  closeRagSliceDetail();
  openPage("rag");
  await nextTick();
  try{await openKbFileDetail({path,name:ragSliceParentName(sl)});}catch(e){showToastMsg(e.message||String(e));}
}
function extractRagSlicesFromStep(s){
  const io=getStepIoFields(s);
  const j=parseStepJson(io.output_text);
  if(j&&Array.isArray(j.rag_slices)&&j.rag_slices.length)return j.rag_slices;
  if(s&&Array.isArray(s.rag_slices)&&s.rag_slices.length)return s.rag_slices;
  return[];
}
function isRagDecisionStep(s){
  if(!s)return false;
  if(String(s.what||"").includes("知识库检索"))return true;
  const ph=String(stepUi(s).phase||s.phase||"").toLowerCase();
  return ph==="rag_decision"||ph==="rag";
}
function formatOrchStepOutputDisplay(s){
  s=stepForIoDisplay(s);
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
  const filtered=(Array.isArray(thinking)?thinking:[]).filter(s=>{
    if(!s||typeof s!=="object")return false;
    const what=String(s.what||s.step_name||"").trim();
    if(!what&&!s.result&&!getStepIoFields(s).think_text)return false;
    if(/答案组织|LLM生成回答/.test(what))return false;
    if(stepIsToolCall(s)||s.kind==="tool")return true;
    const ph=String(stepUi(s).phase||s.phase||"").toLowerCase();
    if(ph==="react_round"||ph==="react_think"||s.kind==="react"){
      return !!(getStepIoFields(s).think_text||s.result);
    }
    if(/推理分析|推理与行动|工具调用规划/.test(what))return false;
    if(s.kind==="orch"||s.kind==="react")return !!(what||s.result);
    if(ph==="rag_decision")return true;
    return !!(what||s.result);
  });
  return dedupeContinueMainSteps(filtered);
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
const chatActiveCurTask=computed(()=>isActiveCurTask(c.curTask)?c.curTask:null);
const chatSubPlanGroupCount=computed(()=>chatGroupedSubPlans.value.length);
function stepHasCompletedOutput(s){
  if(s&&String(s.result||"").trim())return true;
  const io=getStepIoFields(s);
  return !!(String(io.output_text||"").trim());
}
function stepSuccessLabel(s){
  if(stepIsSkipped(s))return"跳过";
  const ui=stepUi(s);
  if(s&&s.success===false||ui.success===false)return"失败";
  if(s&&s.success===true||ui.success===true)return"成功";
  const st=String(ui.status||s.status||"").toLowerCase();
  if(["failed","abnormal"].includes(st))return"失败";
  if(["done","completed","resolved"].includes(st))return"成功";
  if(["running","thinking","started","executing"].includes(st)){
    if(stepHasCompletedOutput(s)&&!s._thinkStreaming)return"成功";
    return"进行中";
  }
  if(s&&s.result)return"成功";
  if(stepHasCompletedOutput(s))return"成功";
  if(stepIsToolCall(s))return"—";
  return"编排";
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
  if(!stepIsToolCall(s))return"—";
  const n=Number(stepUi(s).confidence!=null?stepUi(s).confidence:s.confidence);
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
  const j=parseStepJson(getStepIoFields(s).output_text);
  if(j&&(j.links||j.path||j.url||j.doc_id||j.file_path))return true;
  const ph=String(s.phase||"").toLowerCase();
  const nm=String(s.step_name||"");
  if(/doc|文档|longpage|feishu|docx|改写|创建/.test(nm+ph))return true;
  return false;
}
function formatStepInputDisplay(s){
  s=stepForIoDisplay(s);
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
  s=stepForIoDisplay(s);
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
function taskKindLabel(taskKind,taskId,subPlanId,querySummary){
  const cont=String(taskKind||"").toLowerCase();
  if(cont==="continue_main"||cont==="continue"){
    const tid=formatTaskIdFull(taskId);
    const title=String(querySummary||"").trim().slice(0,48);
    if(tid&&title)return`延续复杂任务 · ${tid} ·【${title}】`;
    if(tid)return`延续复杂任务 · ${tid}`;
    return"延续复杂任务";
  }
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
  const qs=execCardQueryLine(m);
  const kind=m.execContinueMain?"continue_main":(m.execTaskKind||(c.curTask&&c.curTask.task_kind));
  return taskKindLabel(kind,tid,sub,qs);
}
function execCardQueryLine(m){
  if(!m)return"";
  const kind=String(m.execContinueMain?"continue_main":(m.execTaskKind||(c.curTask&&c.curTask.task_kind)||"")).toLowerCase();
  if(kind==="simple"||kind==="pending")return"";
  // 延续复杂任务摘要已写入 execCardLabel 蓝色标签，不再重复展示
  if(kind==="continue_main"||kind==="continue")return"";
  const fromMsg=String(m.query_summary||m.user_query||"").trim();
  if(fromMsg)return fromMsg;
  const ta=m.task_audit;
  if(ta){
    const taQs=String(ta.query_summary||ta.user_query||"").trim();
    if(taQs)return taQs;
  }
  const tid=String(m.task_id||"").trim();
  const cur=isActiveCurTask(c.curTask)?c.curTask:null;
  if(tid&&cur&&String(cur.task_id||"")===tid){
    const cqs=String(cur.query_summary||cur.user_query||"").trim();
    if(cqs)return cqs;
  }
  return"";
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
    "当前任务","当前的任务","任务是啥","执行到哪","进行到哪","做到哪","忘了","忘记了","啥来着",
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
  const tw=chatTaskWriteCtx();
  const hist=Array.isArray(tw.mainTaskHistory)?tw.mainTaskHistory:[];
  const i=hist.findIndex(t=>t.task_id===tid);
  if(i>=0){
    const prev=hist[i]||{};
    const merged={...prev,...patch};
    const prevUq=String(prev.user_query||"").trim();
    if(prevUq&&!isMainTaskFollowUpQuery(prevUq)){
      merged.user_query=prevUq;
      if(prev.query_summary)merged.query_summary=prev.query_summary;
    }
    hist[i]=merged;
  }else hist.push(patch);
  tw.mainTaskHistory=dedupeMainTaskHistoryList(hist);
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
  const msgs=chatStreamMsgsForAi(aiMsg);
  const idx=Array.isArray(msgs)?msgs.indexOf(aiMsg):-1;
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
  const tw=chatTaskWriteCtx();
  if(tw.curTask&&tw.curTask.task_id===aiMsg.task_id){
    tw.curTask.result_msg_index=idx;
    tw.curTask.result_status=judgment;
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
  if(kind==="fleet"||tid.startsWith("fleet_")){
    switchPage("fleet");
    fleet.selSessionId=tid;
    await ldFleetAll();
    await ldFleetSessionDetail(tid);
    return;
  }
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

/* ── 多 Agent 舰队管理 ── */
const FLEET_STATUS_COLUMNS=[
  {key:"pending",label:"待派发"},{key:"running",label:"运行中"},{key:"review",label:"待审查"},
  {key:"done",label:"已完成"},{key:"failed",label:"已失败"},{key:"cancelled",label:"已取消"},
];
const FLEET_ROLES=[
  {key:"planner",label:"规划"},{key:"implementer",label:"实现"},{key:"reviewer",label:"审查"},
  {key:"explorer",label:"探索"},{key:"orchestrator",label:"编排"},
];
const FLEET_STATUS_LABELS={
  pending:"待派发",running:"运行中",review:"待审查",done:"已完成",failed:"已失败",cancelled:"已取消",
};
const fleet=reactive({
  loading:false,err:"",summary:null,harnesses:[],projects:[],sessions:[],ownership:[],
  selSessionId:"",selSession:null,logs:[],logsLoading:false,streamOpen:false,
  filterProjectId:"",filterStatus:"",
  projName:"",projPath:"",projHarness:"shell",projBusy:false,
  sessProjectId:"",sessHarness:"shell",sessRole:"implementer",sessTitle:"",sessPrompt:"",
  sessScopes:"frontend/\nbackend/",sessBusy:false,
  planProjectId:"",planGoal:"",planImplHarness:"codex",planReviewHarness:"claude_code",
  planScopes:"frontend/\nbackend/\nsrc/",planBusy:false,actionBusy:"",
});
let _fleetLogEs=null;
let _fleetPollTimer=null;
function fleetStatusLabel(st){return FLEET_STATUS_LABELS[String(st||"").toLowerCase()]||st||"—";}
function fleetStatusClass(st){
  const s=String(st||"").toLowerCase();
  if(s==="running")return"st-running";
  if(s==="review")return"st-review";
  if(s==="done")return"st-done";
  if(s==="failed")return"st-failed";
  if(s==="cancelled")return"st-cancelled";
  return"st-pending";
}
function fleetRoleLabel(role){
  const r=FLEET_ROLES.find(x=>x.key===role);
  return r?r.label:role||"—";
}
function fleetSessionsByStatus(status){
  const st=String(status||"").toLowerCase();
  return(fleet.sessions||[]).filter(s=>String(s.status||"").toLowerCase()===st);
}
function fleetHarnessLabel(hid){
  const h=(fleet.harnesses||[]).find(x=>x.harness_id===hid);
  return h?(h.label||hid):hid||"—";
}
function fleetParseScopes(text){
  return String(text||"").split(/[\n,;]+/).map(s=>s.trim()).filter(Boolean);
}
async function ldFleetSummary(){
  const r=await fetch("/api/agent-fleet/summary",{headers:authBearerHeaders()});
  const d=await r.json();
  if(!r.ok)throw new Error(d.detail||d.error||"摘要加载失败");
  fleet.summary=d;
}
async function ldFleetHarnesses(){
  const r=await fetch("/api/agent-fleet/harnesses",{headers:authBearerHeaders()});
  const d=await r.json();
  if(!r.ok)throw new Error(d.detail||d.error||"Harness 探测失败");
  fleet.harnesses=Array.isArray(d.harnesses)?d.harnesses:[];
}
async function ldFleetProjects(){
  const r=await fetch("/api/agent-fleet/projects",{headers:authBearerHeaders()});
  const d=await r.json();
  if(!r.ok)throw new Error(d.detail||d.error||"项目加载失败");
  fleet.projects=Array.isArray(d.projects)?d.projects:[];
  if(!fleet.sessProjectId&&fleet.projects.length)fleet.sessProjectId=fleet.projects[0].project_id;
  if(!fleet.planProjectId&&fleet.projects.length)fleet.planProjectId=fleet.projects[0].project_id;
}
async function ldFleetSessions(){
  const qs=new URLSearchParams();
  if(fleet.filterProjectId)qs.set("project_id",fleet.filterProjectId);
  if(fleet.filterStatus)qs.set("status",fleet.filterStatus);
  const r=await fetch("/api/agent-fleet/sessions?"+qs,{headers:authBearerHeaders()});
  const d=await r.json();
  if(!r.ok)throw new Error(d.detail||d.error||"会话加载失败");
  fleet.sessions=Array.isArray(d.sessions)?d.sessions:[];
}
async function ldFleetOwnership(){
  const qs=new URLSearchParams();
  if(fleet.filterProjectId)qs.set("project_id",fleet.filterProjectId);
  const r=await fetch("/api/agent-fleet/ownership?"+qs,{headers:authBearerHeaders()});
  const d=await r.json();
  if(!r.ok)throw new Error(d.detail||d.error||"作用域锁加载失败");
  fleet.ownership=Array.isArray(d.ownership)?d.ownership:[];
}
async function ldFleetAll(){
  fleet.loading=true;fleet.err="";
  try{
    await Promise.all([ldFleetSummary(),ldFleetHarnesses(),ldFleetProjects(),ldFleetSessions(),ldFleetOwnership()]);
  }catch(e){
    fleet.err=e.message||String(e);
    showToastMsg("多 Agent 数据加载失败："+fleet.err);
  }finally{
    fleet.loading=false;
  }
}
async function ldFleetSessionDetail(sid){
  const id=String(sid||fleet.selSessionId||"").trim();
  if(!id){fleet.selSession=null;fleet.logs=[];return;}
  fleet.selSessionId=id;
  fleet.logsLoading=true;
  try{
    const r=await fetch("/api/agent-fleet/sessions/"+encodeURIComponent(id),{headers:authBearerHeaders()});
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||d.error||"会话详情失败");
    fleet.selSession=d.session||null;
    await fleetRefreshLogs();
    const st=String(fleet.selSession&&fleet.selSession.status||"");
    if(st==="running"||st==="pending")fleetStartLogStream(id);
    else fleetStopLogStream();
  }catch(e){
    showToastMsg("会话详情失败："+(e.message||e));
  }finally{
    fleet.logsLoading=false;
  }
}
function selectFleetSession(s){
  if(!s)return;
  ldFleetSessionDetail(s.session_id);
}
async function fleetRefreshLogs(){
  const id=String(fleet.selSessionId||"").trim();
  if(!id)return;
  fleet.logsLoading=true;
  try{
    const r=await fetch("/api/agent-fleet/sessions/"+encodeURIComponent(id)+"/logs?tail=300",{headers:authBearerHeaders()});
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||d.error||"日志加载失败");
    fleet.logs=Array.isArray(d.logs)?d.logs:[];
  }catch(e){
    showToastMsg("日志加载失败："+(e.message||e));
  }finally{
    fleet.logsLoading=false;
  }
}
function fleetStopLogStream(){
  fleet.streamOpen=false;
  if(_fleetLogEs){try{_fleetLogEs.close();}catch(_){}_fleetLogEs=null;}
  if(_fleetPollTimer){clearInterval(_fleetPollTimer);_fleetPollTimer=null;}
}
function fleetStartLogStream(sid){
  fleetStopLogStream();
  const id=String(sid||fleet.selSessionId||"").trim();
  if(!id)return;
  fleet.streamOpen=true;
  if(typeof EventSource!=="undefined"){
    try{
      const es=new EventSource("/api/agent-fleet/sessions/"+encodeURIComponent(id)+"/stream");
      _fleetLogEs=es;
      es.addEventListener("log_line",ev=>{
        try{
          const rec=JSON.parse(ev.data||"{}");
          fleet.logs=(fleet.logs||[]).concat([rec]).slice(-500);
        }catch(_){}
      });
      es.addEventListener("session_status",ev=>{
        try{
          const d=JSON.parse(ev.data||"{}");
          if(d.session)fleet.selSession=d.session;
        }catch(_){}
      });
      es.addEventListener("session_done",ev=>{
        try{
          const d=JSON.parse(ev.data||"{}");
          if(d.session)fleet.selSession=d.session;
        }catch(_){}
        ldFleetSessions().catch(()=>{});
        fleetStopLogStream();
      });
      es.addEventListener("stream_end",()=>{fleetStopLogStream();});
      es.onerror=()=>{fleetStopLogStream();};
      return;
    }catch(_){}
  }
  _fleetPollTimer=setInterval(()=>{
    fleetRefreshLogs().catch(()=>{});
    ldFleetSessions().catch(()=>{});
  },2000);
}
async function fleetAddProject(){
  fleet.projBusy=true;
  try{
    const r=await fetch("/api/agent-fleet/projects",{
      method:"POST",headers:authJsonHeaders(),
      body:JSON.stringify({name:fleet.projName,workspace_path:fleet.projPath,default_harness:fleet.projHarness}),
    });
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||d.error||"注册失败");
    showToastMsg("项目已注册");
    fleet.projName="";fleet.projPath="";
    await ldFleetProjects();
  }catch(e){showToastMsg("注册项目失败："+(e.message||e));}
  finally{fleet.projBusy=false;}
}
async function fleetDeleteProject(pid){
  if(!pid||!confirm("确定删除该项目？关联会话仍保留。"))return;
  fleet.actionBusy=pid;
  try{
    const r=await fetch("/api/agent-fleet/projects/"+encodeURIComponent(pid),{method:"DELETE",headers:authBearerHeaders()});
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||d.error||"删除失败");
    showToastMsg("项目已删除");
    await ldFleetProjects();
  }catch(e){showToastMsg("删除失败："+(e.message||e));}
  finally{fleet.actionBusy="";}
}
async function fleetCreateSession(){
  if(!fleet.sessProjectId||!fleet.sessPrompt.trim()){showToastMsg("请选择项目并填写任务提示词");return;}
  fleet.sessBusy=true;
  try{
    const r=await fetch("/api/agent-fleet/sessions",{
      method:"POST",headers:authJsonHeaders(),
      body:JSON.stringify({
        project_id:fleet.sessProjectId,harness_id:fleet.sessHarness,role:fleet.sessRole,
        prompt:fleet.sessPrompt,title:fleet.sessTitle,
        scope_paths:fleetParseScopes(fleet.sessScopes),
      }),
    });
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||d.error||"创建失败");
    showToastMsg("会话已创建："+(d.session&&d.session.session_id||""));
    fleet.sessPrompt="";fleet.sessTitle="";
    await ldFleetSessions();await ldFleetOwnership();
    if(d.session)selectFleetSession(d.session);
  }catch(e){showToastMsg("创建会话失败："+(e.message||e));}
  finally{fleet.sessBusy=false;}
}
async function fleetCreatePlan(){
  if(!fleet.planProjectId||!fleet.planGoal.trim()){showToastMsg("请选择项目并填写目标");return;}
  fleet.planBusy=true;
  try{
    const r=await fetch("/api/agent-fleet/plans",{
      method:"POST",headers:authJsonHeaders(),
      body:JSON.stringify({
        project_id:fleet.planProjectId,goal:fleet.planGoal,
        implement_harness:fleet.planImplHarness,review_harness:fleet.planReviewHarness,
        scope_paths:fleetParseScopes(fleet.planScopes),
      }),
    });
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||d.error||"编排失败");
    showToastMsg("三角色编排已创建（Planner→Implementer→Reviewer）");
    fleet.planGoal="";
    await ldFleetSessions();await ldFleetOwnership();
    const sessions=(d.plan&&d.plan.sessions)||[];
    if(sessions.length)selectFleetSession(sessions[0]);
  }catch(e){showToastMsg("编排失败："+(e.message||e));}
  finally{fleet.planBusy=false;}
}
async function fleetDispatchSession(sid){
  const id=String(sid||fleet.selSessionId||"").trim();
  if(!id)return;
  fleet.actionBusy="dispatch:"+id;
  try{
    const r=await fetch("/api/agent-fleet/sessions/"+encodeURIComponent(id)+"/dispatch",{method:"POST",headers:authBearerHeaders()});
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||d.error||"派发失败");
    showToastMsg("已启动真实 CLI 派发");
    await ldFleetSessions();await ldFleetSessionDetail(id);
    fleetStartLogStream(id);
  }catch(e){showToastMsg("派发失败："+(e.message||e));}
  finally{fleet.actionBusy="";}
}
async function fleetCancelSession(sid){
  const id=String(sid||fleet.selSessionId||"").trim();
  if(!id)return;
  fleet.actionBusy="cancel:"+id;
  try{
    const r=await fetch("/api/agent-fleet/sessions/"+encodeURIComponent(id)+"/cancel",{method:"POST",headers:authBearerHeaders()});
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||d.error||"取消失败");
    showToastMsg("会话已取消");
    fleetStopLogStream();
    await ldFleetSessions();await ldFleetSessionDetail(id);
  }catch(e){showToastMsg("取消失败："+(e.message||e));}
  finally{fleet.actionBusy="";}
}
async function fleetReviewSession(approved){
  const id=String(fleet.selSessionId||"").trim();
  if(!id)return;
  fleet.actionBusy="review:"+id;
  try{
    const r=await fetch("/api/agent-fleet/sessions/"+encodeURIComponent(id)+"/review",{
      method:"POST",headers:authJsonHeaders(),body:JSON.stringify({approved:!!approved}),
    });
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||d.error||"审查确认失败");
    showToastMsg(approved?"审查通过":"审查未通过");
    await ldFleetSessions();await ldFleetSessionDetail(id);
  }catch(e){showToastMsg("审查确认失败："+(e.message||e));}
  finally{fleet.actionBusy="";}
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
  c.taskSubPlanSel=null;
  openPageOverlay("taskHistModal",()=>{c.taskHistModalOpen=true;});
  if(!taskHistDetailOf(h))await loadTaskHistDetail(h);
}
function closeTaskHistModal(){
  c.taskHistModalOpen=false;
  c.taskHistModalRow=null;
  c.taskHistModalFromChat=false;
  c.taskSubPlanSel=null;
}
function closeTaskHistModalBack(){
  closeTaskHistModal();
}
function taskMetaLabel(key){
  return taskFieldLabel(key);
}
function normalizeTaskDetailStep(s){
  if(!s||typeof s!=="object")return null;
  const out={...s};
  if(!out.what){
    out.what=extractWhatFromSse({
      what:out.what,step_name:out.step_name,phase:out.phase,node_kind:out.node_kind,
      invoke_mode:out.invoke_mode,invoke_purpose:out.invoke_purpose,
      input_text:typeof out.input_payload==="object"?JSON.stringify(out.input_payload):out.input_text,
      output_text:typeof out.output_payload==="object"?JSON.stringify(out.output_payload):out.output_text,
      target:out.target,operation:out.operation,
    });
  }
  if(!out.result){
    const brief=String(out.result_brief||out.description||out.objective||out.current_assessment||"").trim();
    if(brief)out.result=brief.slice(0,MAX_STORED_STEP_RESULT_CHARS);
    else if(out.output_payload&&typeof out.output_payload==="object"){
      const tr=out.output_payload.tool_result;
      if(tr!=null)out.result=String(typeof tr==="string"?tr:JSON.stringify(tr)).slice(0,MAX_STORED_STEP_RESULT_CHARS);
    }
  }
  if(!out.invoke_mode){
    const lane=String(out.step_lane||"").toLowerCase();
    const ph=String(out.phase||"").toLowerCase();
    if(lane==="prefetch"||ph==="rag_decision")out.invoke_mode="fixed_node";
    else if(out.step_type==="tool_call"||out.tool_name)out.invoke_mode="react";
  }
  if(!out._ui)out._ui={};
  ["sub_plan_id","sub_index","step_lane","phase","node_kind","invoke_mode","invoke_purpose"].forEach(k=>{
    if(out[k]!=null&&out[k]!=="")out._ui[k]=out[k];
  });
  return out;
}
function taskHistSubPlanGroups(detail){
  if(!detail||typeof detail!=="object")return[];
  if(Array.isArray(detail.sub_plan_groups)&&detail.sub_plan_groups.length){
    return detail.sub_plan_groups.map((g,ord)=>{
      const steps=(Array.isArray(g.steps)?g.steps:[]).map(normalizeTaskDetailStep).filter(Boolean);
      return{
        sub_plan_id:g.sub_plan_id||steps[0]?.sub_plan_id||`subplan_hist_${ord+1}`,
        sub_index:g.sub_index||steps[0]?.sub_index||ord+1,
        step_lane:g.step_lane||steps[0]?.step_lane||"",
        status:g.status||"completed",
        tool_count:Number(g.tool_count)||steps.filter(s=>stepIsToolCall(s)||s.step_type==="tool_call").length,
        step_count:Number(g.step_count)||steps.length,
        invoke_modes:Array.isArray(g.invoke_modes)?g.invoke_modes.slice():[],
        steps,
      };
    });
  }
  const steps=(Array.isArray(detail.steps)?detail.steps:[]).map(normalizeTaskDetailStep).filter(Boolean);
  return groupStepsBySubPlan(steps).map(g=>({
    ...g,
    status:"completed",
    tool_count:(g.steps||[]).filter(s=>stepIsToolCall(s)||s.step_type==="tool_call").length,
    step_count:(g.steps||[]).length,
    invoke_modes:[...new Set((g.steps||[]).map(s=>stepInvokeMode(s)).filter(Boolean))],
  }));
}
function taskSubPlanInvokeTags(plan){
  const modes=Array.isArray(plan&&plan.invoke_modes)?plan.invoke_modes:[];
  const fromSteps=[...new Set((plan&&plan.steps||[]).map(s=>stepInvokeMode(s)).filter(Boolean))];
  return [...new Set([...modes,...fromSteps])].filter(Boolean);
}
function taskSubPlanInvokeTagLabel(mode){
  return INVOKE_MODE_CN[String(mode||"")]||String(mode||"");
}
function taskSubPlanStatusLabel(st){
  const s=String(st||"").toLowerCase();
  if(["failed","abnormal","error"].includes(s))return"失败";
  if(["running","executing","started"].includes(s))return"执行中";
  return"已完成";
}
function taskSubPlanStatusClass(st){
  const s=String(st||"").toLowerCase();
  if(["failed","abnormal","error"].includes(s))return"fail";
  if(["running","executing","started"].includes(s))return"run";
  return"ok";
}
function openTaskSubPlanDetail(plan){
  if(!plan)return;
  const pid=String(plan.sub_plan_id||"").trim();
  const cur=c.taskSubPlanSel;
  if(cur&&String(cur.sub_plan_id||"")===pid&&Number(cur.sub_index)===Number(plan.sub_index)){
    c.taskSubPlanSel=null;
    return;
  }
  c.taskSubPlanSel=plan;
}
function closeTaskSubPlanDetail(){
  c.taskSubPlanSel=null;
}
function taskSubPlanDetailStepName(s){
  if(!s)return"—";
  const what=String(s.what||"").trim();
  if(what)return what.replace(/^推理分析\s*\/?\s*工具调用规划$/,"ReAct 推理");
  return stepDisplayName(s);
}
function taskSubPlanDetailPayload(s,key){
  if(!s)return"—";
  const raw=s[key];
  if(raw==null||raw==="")return"—";
  if(typeof raw==="object")return opFmtJson(raw);
  return String(raw);
}
function taskHistDetailCounts(h){
  const d=taskHistDetailOf(h);
  if(!d)return null;
  const groups=taskHistSubPlanGroups(d);
  return{
    fixed:(d.snapshot_fixed_rows||[]).length,
    open:(d.snapshot_open_rows||[]).length,
    groups:groups.length,
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

// ── 用户反馈：回答满意度（星级即时提交）+ 可选文字反馈 + 意图评价 ──

const intentLabels=["知识库","资料处理","研发运维","业务系统","社媒分析","通用"];

const msgFeedbackStore=reactive({});

function msgFeedback(m){
  if(!m._msgKey)m._msgKey='fb_'+Math.random().toString(36).slice(2,10);
  const k=m._msgKey;
  if(!msgFeedbackStore[k]){
    msgFeedbackStore[k]=reactive({
      rating:0,_hoverStar:0,intent_liked:null,corrected_intent:"",comment:"",
      _showCommentBox:false,_ratingSubmitted:false,_commentSubmitted:false,
      _ratingPending:false,_commentPending:false,
      _intentAlt:null,_intentAltLoading:false,_intentAltError:"",
      _customIntent:"",
    });
  }
  return msgFeedbackStore[k];
}

function resolveMsgIdx(m,msgIdx){
  return msgIdx!=null?msgIdx:(c.msgs||[]).indexOf(m);
}

function feedbackDetectedIntent(m){
  if(m._detectedIntent)return m._detectedIntent;
  const snap=m._rewriteSnapshot||(c.curTask&&c.curTask.rewrite_snapshot)||null;
  if(snap&&typeof snap==='object'){
    return {domain:snap.domain||snap.domain_code,mode:snap.mode,domain_code:snap.domain_code,pipeline_source:snap.pipeline_source};
  }
  return null;
}

function feedbackIntentLabel(m){
  const d=feedbackDetectedIntent(m);
  if(!d)return '';
  return String(d.domain||d.mode||'').trim();
}

async function loadIntentAlternatives(m,msgIdx){
  const fb=msgFeedback(m);
  if(!c.sid)return;
  fb._intentAltLoading=true;fb._intentAltError='';
  try{
    const det=feedbackDetectedIntent(m)||{};
    const terms=det.retrieval_terms||[];
    const qs=new URLSearchParams({session_id:c.sid,message_index:String(resolveMsgIdx(m,msgIdx))});
    if(Array.isArray(terms)&&terms.length)qs.set('retrieval_terms',terms.join(','));
    const r=await fetch('/api/chat/intent-alternatives?'+qs,{headers:authBearerHeaders()});
    if(r.ok){fb._intentAlt=await r.json();}
    else fb._intentAltError='备选意图加载失败';
  }catch(e){fb._intentAltError='备选意图加载失败';}
  finally{fb._intentAltLoading=false;}
}

function pickIntentAlternative(m,label,code,msgIdx){
  const fb=msgFeedback(m);
  fb.corrected_intent=label||code||'';
  fb._customIntent='';
  submitIntentFeedback(m,msgIdx);
}

function applyCustomIntentCorrection(m,msgIdx){
  const fb=msgFeedback(m);
  const t=(fb._customIntent||'').trim();
  if(!t)return;
  fb.corrected_intent=t;
  submitIntentFeedback(m,msgIdx);
}

async function loadSessionFeedback(sid){
  if(!sid)return;
  try{
    const r=await fetch('/api/chat/feedback/session?session_id='+encodeURIComponent(sid),{headers:authBearerHeaders()});
    if(!r.ok)return;
    const d=await r.json();
    const items=Array.isArray(d.items)?d.items:[];
    const asst=(c.msgs||[]).filter(m=>m&&m.role==='assistant');
    items.forEach(row=>{
      const idx=parseInt(row.message_index,10);
      const m=asst[idx];
      if(!m)return;
      const fb=msgFeedback(m);
      if(row.rating)fb.rating=parseInt(row.rating,10);
      if(row.intent_liked!=null)fb.intent_liked=!!row.intent_liked;
      if(row.corrected_intent)fb.corrected_intent=row.corrected_intent;
      if(row.comment)fb.comment=row.comment;
      if(row.rating)fb._ratingSubmitted=true;
      if(row.comment)fb._commentSubmitted=true;
      if(row.detected_intent)m._detectedIntent=row.detected_intent;
    });
  }catch(e){console.warn('loadSessionFeedback',e);}
}

async function setMsgRating(m,star,msgIdx){
  const fb=msgFeedback(m);
  fb.rating=star;
  fb._showCommentBox=star>0&&star<5;
  if(star===5)fb._commentSubmitted=false;
  await submitRatingOnly(m,msgIdx);
}

function dismissFeedbackComment(m){
  const fb=msgFeedback(m);
  fb._showCommentBox=false;
}

async function submitRatingOnly(m,msgIdx){
  const fb=msgFeedback(m);
  if(!fb.rating||fb._ratingPending)return;
  fb._ratingPending=true;
  try{
    const r=await fetch('/api/chat/feedback',{
      method:'POST',
      headers:authJsonHeaders(),
      body:JSON.stringify({
        session_id:c.sid,
        message_index:resolveMsgIdx(m,msgIdx),
        rating:fb.rating,
        intent_liked:fb.intent_liked,
        detected_intent:feedbackDetectedIntent(m),
        corrected_intent:fb.corrected_intent||undefined,
        corrected_intent_label:fb.corrected_intent||undefined,
      }),
    });
    const d=await r.json();
    if(d&&d.ok){
      fb._ratingSubmitted=true;
      showToastMsg(fb.rating<5?'已记录评分，欢迎补充反馈':'感谢 5 星评价');
    }else console.warn('rating submit failed',d);
  }catch(e){console.warn('rating submit error',e);}
  finally{fb._ratingPending=false;}
}

async function submitFeedbackComment(m,msgIdx){
  const fb=msgFeedback(m);
  const text=(fb.comment||'').trim();
  if(!text||fb._commentPending||fb._commentSubmitted)return;
  fb._commentPending=true;
  try{
    const payload={
      session_id:c.sid,
      message_index:resolveMsgIdx(m,msgIdx),
      comment:text,
    };
    if(fb.rating)payload.rating=fb.rating;
    const r=await fetch('/api/chat/feedback',{
      method:'POST',
      headers:authJsonHeaders(),
      body:JSON.stringify(payload),
    });
    const d=await r.json();
    if(d&&d.ok){
      fb._commentSubmitted=true;
      fb._showCommentBox=false;
      showToastMsg('补充反馈已提交');
    }else console.warn('comment submit failed',d);
  }catch(e){console.warn('comment submit error',e);}
  finally{fb._commentPending=false;}
}

function toggleIntentLike(m,liked,msgIdx){
  const fb=msgFeedback(m);
  if(fb.intent_liked===liked)fb.intent_liked=null;
  else fb.intent_liked=liked;
  if(!liked){
    fb.corrected_intent="";
    loadIntentAlternatives(m,msgIdx);
  }else{
    fb._intentAlt=null;
  }
  submitIntentFeedback(m,msgIdx);
}

async function submitIntentFeedback(m,msgIdx){
  const fb=msgFeedback(m);
  if(fb.intent_liked==null&&!fb.corrected_intent)return;
  try{
    const payload={
      session_id:c.sid,
      message_index:resolveMsgIdx(m,msgIdx),
      intent_liked:fb.intent_liked,
      detected_intent:feedbackDetectedIntent(m),
      corrected_intent:fb.corrected_intent||undefined,
      corrected_intent_label:fb.corrected_intent||undefined,
    };
    if(fb.rating)payload.rating=fb.rating;
    const r=await fetch('/api/chat/feedback',{
      method:'POST',
      headers:authJsonHeaders(),
      body:JSON.stringify(payload),
    });
    const d=await r.json();
    if(!(d&&d.ok))console.warn('intent feedback failed',d);
  }catch(e){console.warn('intent feedback error',e);}
}

const chatExpandOpen=ref(false);
const cs=ref([]);
const csBatchSel=reactive({});
const csBatchMode=ref(false);
const chatModels=ref([{id:"",label:"auto（节点池）"}]);
async function ldChatModels(){
  try{
    const r=await fetchWithTimeout('/api/chat/models',{headers:authBearerHeaders()},8000);
    const d=await r.json().catch(()=>({}));
    if(!r.ok||!d||!d.ok)throw new Error((d&&d.error)||'加载模型列表失败');
    const rows=Array.isArray(d.models)?d.models:[];
    chatModels.value=rows.length?rows:[{id:"",label:"auto（节点池）"}];
    const ids=new Set(chatModels.value.map(m=>String(m.id||'')));
    const cur=String(c.model||'').trim();
    if(cur&&!ids.has(cur)){
      const def=String(d.default_id||'').trim();
      c.model=def&&ids.has(def)?def:"";
      persistChatPrefs();
    }
  }catch(e){
    console.warn('[SBA] load chat models failed',e);
    if(!chatModels.value.length)chatModels.value=[{id:"",label:"auto（节点池）"}];
  }
}
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
const CHAT_FETCH_TIMEOUT_MS=12000;
const CHAT_SSE_HEADERS_TIMEOUT_MS=22000;
const CHAT_SSE_FIRST_EVENT_TIMEOUT_MS=35000;
const CHAT_CONNECT_STALL_MS=45000;
function bindAbortForward(targetAc,source){
  if(!source)return;
  if(source.aborted){targetAc.abort(source.reason);return;}
  source.addEventListener('abort',()=>targetAc.abort(source.reason),{once:true});
}
async function fetchWithTimeout(url,opts={},timeoutMs=CHAT_FETCH_TIMEOUT_MS){
  const ms=Math.max(1000,Number(timeoutMs)||CHAT_FETCH_TIMEOUT_MS);
  const timeoutAc=new AbortController();
  const mergedAc=new AbortController();
  bindAbortForward(mergedAc,opts.signal);
  const timer=setTimeout(()=>{
    const ex=new Error('请求超时（'+ms+'ms）');
    ex.name='TimeoutError';
    ex.code='fetch_timeout';
    timeoutAc.abort(ex);
  },ms);
  bindAbortForward(mergedAc,timeoutAc.signal);
  try{
    return await fetch(url,{...opts,signal:mergedAc.signal});
  }catch(e){
    if(timeoutAc.signal.aborted){
      throw timeoutAc.signal.reason||Object.assign(new Error('请求超时'),{name:'TimeoutError',code:'fetch_timeout'});
    }
    throw e;
  }finally{clearTimeout(timer);}
}
async function probeBackendQuick(timeoutMs=3500){
  try{
    const r=await fetchWithTimeout('/api/health',{},timeoutMs);
    if(!r.ok)return false;
    const d=await r.json().catch(()=>({}));
    return!!(d&&d.ok);
  }catch(_){return false;}
}
function collectChatDependencyHints(){
  const hints=[];
  if(c.chatWarmup.error)hints.push('预热：'+c.chatWarmup.error);
  if(c.platformHealth&&c.platformHealth.error)hints.push('健康检查：'+c.platformHealth.error);
  const bad=(c.platformHealth&&c.platformHealth.items||[]).filter(i=>i.status==='error');
  if(bad.length)hints.push('阻断项：'+bad.map(i=>i.label||i.id).join('、'));
  const soft=(c.platformHealth&&c.platformHealth.items||[]).filter(i=>i.status==='warn');
  if(soft.length&&!bad.length)hints.push('可选依赖未就绪（不影响普通问答）：'+soft.map(i=>i.label||i.id).join('、'));
  return hints;
}
async function buildChatStreamFailureMessage(err,aiMsg){
  const code=String(err&&err.code||'');
  const name=String(err&&err.name||'');
  const msg=String(err&&err.message||err||'');
  const isTimeout=name==='TimeoutError'||code==='fetch_timeout'||code==='sse_first_event_timeout'||code==='sse_headers_timeout';
  const isConnFail=isTimeout||name==='TypeError'||/Failed to fetch|NetworkError|load failed/i.test(msg);
  if(!isConnFail)return'请求未能完成：'+msg.slice(0,300);
  const backendOk=await probeBackendQuick(4000);
  const hints=collectChatDependencyHints();
  let text='**连接 AI 问答服务失败或超时**\n\n';
  if(!backendOk){
    text+='后端 `http://127.0.0.1:8000` 当前无响应（探活超时）。\n\n';
    text+='**常见原因**：后端进程卡死（LangGraph/MCP 并发导入死锁）、8000 端口被旧进程占用、或 uvicorn 未启动。\n\n';
    text+='**建议**：\n1. 关闭占用 8000 的旧进程后重启 `start_backend.bat` 或 uvicorn\n2. 刷新页面后重试（普通问答**不需要** Milvus）\n3. 若仍失败，查看后端控制台是否有 `deadlock detected` 日志';
  }else if(code==='sse_first_event_timeout'||code==='sse_headers_timeout'){
    text+='后端探活正常，但问答 SSE 流在时限内未返回首包。\n\n';
    text+='**建议**：稍等 10 秒后重试；若反复出现，重启后端（可能是 MCP 预热未完成）。\n\n';
    text+='普通问答不依赖 Milvus/RAG，无需启动 Docker。';
  }else{
    text+='网络请求失败，请确认浏览器访问的是 `http://127.0.0.1:8000/` 且已登录。';
  }
  if(hints.length)text+='\n\n**诊断**：'+hints.join('；');
  if(code==='sse_first_event_timeout'&&isChatLoadingPlaceholder(aiMsg&&aiMsg.content))
    text+='\n\n（已等待 '+Math.round(CHAT_SSE_FIRST_EVENT_TIMEOUT_MS/1000)+'s，未收到 stream_open 事件）';
  return text;
}
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
let _chatConnectStallTimer=null;
function resetChatConnectStall(){
  c.chatConnect.stallWarn=false;
  c.chatConnect.stallDetail='';
  if(_chatConnectStallTimer){clearTimeout(_chatConnectStallTimer);_chatConnectStallTimer=null;}
}
function markChatConnectStall(detail){
  c.chatConnect.stallWarn=true;
  c.chatConnect.stallDetail=String(detail||'连接预热超时，请检查后端').slice(0,140);
  showToastMsg(c.chatConnect.stallDetail);
}
function beginChatConnect(){
  if(c.chatWarmup.ready&&!c.chatWarmup.warming&&!c.chatWarmup.loading&&!c.platformHealthLoading&&c.platformHealth&&c.platformHealth.ready)return;
  resetChatConnectStall();
  c.chatConnect.active=true;
  c.chatConnect.doneFlash=false;
  if(_chatConnectDoneTimer){clearTimeout(_chatConnectDoneTimer);_chatConnectDoneTimer=null;}
  if(_chatConnectStallTimer)clearTimeout(_chatConnectStallTimer);
  _chatConnectStallTimer=setTimeout(()=>{
    if(!c.chatConnect.active||c.chatConnect.doneFlash)return;
    const hints=collectChatDependencyHints();
    markChatConnectStall('连接预热超时'+(hints.length?'：'+hints[0]:'，请检查后端'));
  },CHAT_CONNECT_STALL_MS);
}
function finishChatConnect(){
  if(!c.chatConnect.active)return;
  resetChatConnectStall();
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
  if(warmOk)finishChatConnect();
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
      const r=await fetchWithTimeout('/api/chat/warmup?'+q.toString(),{headers:authBearerHeaders()},CHAT_FETCH_TIMEOUT_MS);
      const d=await r.json().catch(()=>({}));
      applyChatWarmupStatus(d);
      if(!c.chatWarmup.warming&&c.chatWarmup.ready)stopWarmupPoll();
    }catch(e){
      c.chatWarmup.error=String(e&&e.message||e||'warmup_poll_failed').slice(0,120);
    }
  },700);
}
const CHAT_CONNECT_STEPS=[
  {phase:'langgraph',label:'任务编排',needsRag:false},
  {phase:'tools_mcp',label:'MCP',needsRag:false},
  {phase:'rag_milvus',label:'RAG',needsRag:true},
  {phase:'rag_embedder',label:'RAG 嵌入',needsRag:true,after:'rag_milvus'},
  {phase:'llm',label:'LLM',needsRag:false,isHealth:true},
];
const chatConnectVisible=computed(()=>!!c.chatConnect.active);
const chatConnectClass=computed(()=>{
  if(c.chatConnect.stallWarn)return'cc-stall';
  if(c.chatConnect.doneFlash)return'cc-done';
  return'cc-loading';
});
const chatConnectLabel=computed(()=>{
  if(c.chatConnect.stallWarn)return c.chatConnect.stallDetail||'连接超时';
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
  if(c.platformHealthLoading)return'连接 LLM';
  return'连接中';
});
/** 后台/阻塞预热 MCP 工具、LangGraph；RAG/Milvus 不阻塞普通问答 */
async function requestChatWarmup(opts={}){
  const readComments=!!(opts.readComments!=null?opts.readComments:c.readComments);
  const includeRag=!!opts.includeRag;
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
    const r=await fetchWithTimeout('/api/chat/warmup?'+q.toString(),{headers:authBearerHeaders()},wait?90000:CHAT_FETCH_TIMEOUT_MS);
    const d=await r.json().catch(()=>({}));
    applyChatWarmupStatus(d);
    return d;
  }catch(e){
    console.warn('[SBA chat warmup]',e);
    c.chatWarmup.error=String(e&&e.message||e||'warmup_fetch_failed').slice(0,200);
    return null;
  }finally{
    c.chatWarmup.loading=false;
  }
}
async function ensureChatWarmupBeforeSend(){
  const needComments=!!c.readComments;
  const maxWaitMs=8000;
  const withCap=(p)=>Promise.race([
    p,
    new Promise((resolve)=>setTimeout(()=>resolve(null),maxWaitMs)),
  ]);
  if(!c.chatWarmup.ready){
    await withCap(requestChatWarmup({wait:true,readComments:needComments,includeRag:false}));
    return;
  }
  if(needComments&&!c.chatWarmup.readCommentsCached){
    await withCap(requestChatWarmup({wait:true,readComments:true,includeRag:false}));
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
const _chatSessionCache=new Map();
const CHAT_SESSION_CACHE_MAX=16;
function snapshotChatSessionState(){
  return{
    msgs:(c.msgs||[]).map(m=>({...m})),
    curTask:c.curTask,
    mainTaskHistory:Array.isArray(c.mainTaskHistory)?[...c.mainTaskHistory]:[],
    memoryMeta:c.memoryMeta&&typeof c.memoryMeta==="object"?{...c.memoryMeta}:null,
    prefs:{
      model:c.model,agentId:c.agentId,deepThink:c.deepThink,webSearch:c.webSearch,
      ragPrefetch:c.ragPrefetch,readComments:c.readComments,includeRss:c.includeRss,
      chatPrefs:{...c.chatPrefs},
    },
  };
}
function cacheChatSessionSnapshot(sid){
  if(!sid||sid==="temp")return;
  _chatSessionCache.set(sid,snapshotChatSessionState());
  if(_chatSessionCache.size>CHAT_SESSION_CACHE_MAX){
    const oldest=_chatSessionCache.keys().next().value;
    if(oldest!=null)_chatSessionCache.delete(oldest);
  }
}
function applyChatSessionPrefs(p){
  if(!p||typeof p!=="object")return;
  if(p.model!=null)c.model=p.model;
  if(p.agentId)c.agentId=p.agentId;
  if(p.deepThink!=null)c.deepThink=!!p.deepThink;
  if(p.webSearch!=null)c.webSearch=!!p.webSearch;
  if(p.ragPrefetch!=null)c.ragPrefetch=!!p.ragPrefetch;
  if(p.readComments!=null)c.readComments=!!p.readComments;
  if(p.includeRss!=null)c.includeRss=!!p.includeRss;
  if(p.chatPrefs)c.chatPrefs={...c.chatPrefs,...p.chatPrefs};
}
function applyChatSessionDocument(d,sid){
  if(_chatStreamPark&&_chatStreamPark.sid===sid){
    c.msgs=_chatStreamPark.msgs;
    c.curTask=_chatStreamPark.curTask;
    c.mainTaskHistory=filterChatSessionMainHistory(Array.isArray(_chatStreamPark.mainTaskHistory)?_chatStreamPark.mainTaskHistory:[]);
    clearChatStreamPark();
  }else{
    c.msgs=(d.messages||[]).map(normalizeChatMsg);
    c.curTask=normalizeCurTask(d.cur_task);
    c.mainTaskHistory=filterChatSessionMainHistory(Array.isArray(d.main_task_history)?d.main_task_history:[]);
  }
  c.memoryMeta=d.memory_meta&&typeof d.memory_meta==="object"?d.memory_meta:null;
  if(!c.mainTaskHistory.length&&c.msgs.length)rebuildMainTaskHistoryFromMsgs();
  applyChatSessionPrefs(d.prefs||{});
  if(c.chatPrefs.autoFoldChain){
    c.msgs.forEach(m=>{if(m.role==="assistant"&&m.thinking&&m.thinking.length)m.thinkingExpanded=false;});
  }
}
function applyCachedChatSession(snap){
  if(!snap)return false;
  c.msgs=(snap.msgs||[]).map(normalizeChatMsg);
  c.curTask=normalizeCurTask(snap.curTask);
  c.mainTaskHistory=filterChatSessionMainHistory(Array.isArray(snap.mainTaskHistory)?snap.mainTaskHistory:[]);
  c.memoryMeta=snap.memoryMeta&&typeof snap.memoryMeta==="object"?snap.memoryMeta:null;
  applyChatSessionPrefs(snap.prefs||{});
  return true;
}
function scheduleChatPersist(){
  clearTimeout(chatSaveTimer);
  chatSaveTimer=setTimeout(()=>persistChatSession(),800);
}
function persistChatLocalCache(){
  try{
    const payload=slimPayloadForServer({sid:c.sid,msgs:c.msgs,curTask:c.curTask,mainTaskHistory:c.mainTaskHistory||[]});
    localStorage.setItem("sba_chat_local_v1",JSON.stringify({...payload,updated_at:new Date().toISOString()}));
  }catch(_){}
}
async function persistChatSession(){
  if(!c.sid||c.sid==="temp")return;
  else if(c.msgs.length)rebuildMainTaskHistoryFromMsgs();
  c.mainTaskHistory=filterChatSessionMainHistory(c.mainTaskHistory||[]);
  const payload={messages:c.msgs,cur_task:c.curTask,main_task_history:c.mainTaskHistory,prefs:{model:c.model,agentId:c.agentId,deepThink:c.deepThink,webSearch:c.webSearch,ragPrefetch:c.ragPrefetch,readComments:c.readComments,includeRss:c.includeRss,chatPrefs:c.chatPrefs}};
  persistChatLocalCache();
  try{
    await fetch("/api/chat/sessions/"+encodeURIComponent(c.sid)+"/state",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(slimPayloadForServer(payload))});
    ldCs();
  }catch(_){}
}
async function loadChatSession(sid){
  if(!sid||sid===c.sid)return;
  const prevSid=c.sid;
  const bgStreaming=c.chatStreaming;
  if(bgStreaming){
    parkChatStreamForLeave();
    if(_chatStreamPark&&_chatStreamPark.sid===prevSid){
      _chatSessionCache.set(prevSid,{
        msgs:_chatStreamPark.msgs,
        curTask:_chatStreamPark.curTask,
        mainTaskHistory:Array.isArray(_chatStreamPark.mainTaskHistory)?[..._chatStreamPark.mainTaskHistory]:[],
        memoryMeta:c.memoryMeta&&typeof c.memoryMeta==="object"?{...c.memoryMeta}:null,
        prefs:{
          model:c.model,agentId:c.agentId,deepThink:c.deepThink,webSearch:c.webSearch,
          ragPrefetch:c.ragPrefetch,readComments:c.readComments,includeRss:c.includeRss,
          chatPrefs:{...c.chatPrefs},
        },
      });
    }else if(prevSid&&prevSid!=="temp")cacheChatSessionSnapshot(prevSid);
    void persistChatSession();
    showToastMsg("生成在后台继续，可随时返回该会话查看");
  }else if(prevSid&&prevSid!=="temp"){
    cacheChatSessionSnapshot(prevSid);
  }
  c.sid=sid;c.mode="normal";c._stepIoCache={};c.taskExpanded=false;c.sessionMenuId="";
  let usedCache=false;
  if(_chatStreamPark&&_chatStreamPark.sid===sid){
    c.msgs=_chatStreamPark.msgs;
    c.curTask=_chatStreamPark.curTask;
    c.mainTaskHistory=filterChatSessionMainHistory(Array.isArray(_chatStreamPark.mainTaskHistory)?_chatStreamPark.mainTaskHistory:[]);
    clearChatStreamPark();
    usedCache=true;
  }else{
    const cached=_chatSessionCache.get(sid);
    if(cached){
      applyCachedChatSession(cached);
      usedCache=true;
    }else{
      c.msgs=[];c.curTask=null;c.mainTaskHistory=[];c.memoryMeta=null;
    }
  }
  if(usedCache)nextTick(()=>chatScrollBottom(true));
  try{
    const r=await fetch("/api/chat/sessions/"+encodeURIComponent(sid),{headers:authBearerHeaders()});
    if(c.sid!==sid)return;
    if(!r.ok){
      if(!usedCache){c.msgs=[];c.curTask=null;}
      return;
    }
    const d=await r.json();
    if(c.sid!==sid)return;
    applyChatSessionDocument(d,sid);
    cacheChatSessionSnapshot(sid);
    void loadSessionFeedback(sid);
    chatScrollBottom(true);
  }catch(_){
    if(!usedCache){c.msgs=[];c.curTask=null;}
  }
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
  c.sid="";c.msgs=[];c.curTask=null;c.uploads=[];c.mode="normal";c.inp="";c.rewriteDraft="";c.rewriteConfirmOpen=false;c._stepIoCache={};
  clearRewriteCountdown();
  persistChatLocalCache();
}
async function delCs(sid){
  if(!confirm("删除此对话？"))return;
  await fetch('/api/chat/sessions/'+encodeURIComponent(sid),{method:'DELETE'});
  _chatSessionCache.delete(sid);
  if(c.sid===sid){await newChatSess()}
  ldCs();
}
function clearCsBatchSel(){
  for(const k of Object.keys(csBatchSel))delete csBatchSel[k];
}
function exitCsBatchMode(){
  csBatchMode.value=false;
  clearCsBatchSel();
  c.sessionMenuId="";
}
function toggleCsBatchMode(){
  if(csBatchMode.value)exitCsBatchMode();
  else{
    c.sessionMenuId="";
    csBatchMode.value=true;
  }
}
function onCsItemClick(s){
  if(!s||!s.id)return;
  if(csBatchMode.value)toggleCsBatchSel(s.id);
  else{
    loadChatSession(s.id);c.sessionMenuId="";
    if(mobilePortrait.value)chatSbCollapsed.value=true;
  }
}
function isCsBatchSelected(sid){return !!csBatchSel[sid]}
function toggleCsBatchSel(sid,ev){
  if(ev)ev.stopPropagation();
  if(!sid||!csBatchMode.value)return;
  if(csBatchSel[sid])delete csBatchSel[sid];
  else csBatchSel[sid]=true;
}
function csBatchSelCount(){return Object.keys(csBatchSel).filter(k=>csBatchSel[k]).length}
function csBatchSelAllChecked(){
  const rows=filteredCs.value||[];
  if(!rows.length)return false;
  return rows.every(s=>csBatchSel[s.id]);
}
function toggleCsBatchSelAll(){
  const rows=filteredCs.value||[];
  if(!rows.length)return;
  const allOn=csBatchSelAllChecked();
  for(const s of rows){
    if(allOn)delete csBatchSel[s.id];
    else csBatchSel[s.id]=true;
  }
}
async function batchDeleteCs(){
  const ids=Object.keys(csBatchSel).filter(k=>csBatchSel[k]);
  if(!ids.length){showToastMsg("请先勾选要删除的对话");return}
  if(c.chatStreaming&&ids.includes(c.sid)){
    showToastMsg("当前会话正在生成，请先暂停或等待完成后再删除");
    return;
  }
  if(!confirm("确定批量删除 "+ids.length+" 个对话？此操作不可恢复。"))return;
  let ok=0,fail=0;
  for(const sid of ids){
    try{
      const r=await fetch("/api/chat/sessions/"+encodeURIComponent(sid),{method:"DELETE"});
      if(r.ok){
        ok++;
        _chatSessionCache.delete(sid);
        if(c.sid===sid)await newChatSess();
      }else fail++;
    }catch(_){fail++}
  }
  ldCs();
  exitCsBatchMode();
  if(fail)showToastMsg("已删除 "+ok+" 个，"+fail+" 个失败");
  else showToastMsg("已批量删除 "+ok+" 个对话");
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
function renderMsg(m,msgIndex){
  if(!m||!m.content)return'';
  const raw=answerBodyForMsg(m);
  if(!raw&&!m._answerStreaming)return'';
  if(m._answerStreaming){
    let t=raw.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    t=t.replace(/\n/g,'<br>');
    return t+'<span class="stream-cursor" aria-hidden="true">▊</span>';
  }
  const slices=ragCitationSlicesForMsg(m);
  let html="";
  if(window.SBA_RICH_CONTENT&&typeof window.SBA_RICH_CONTENT.renderRichContentHtml==='function'){
    try{
      html=window.SBA_RICH_CONTENT.renderRichContentHtml(raw);
    }catch(_){html=""}
  }
  if(!html&&typeof marked!=='undefined'){
    try{
      const src=window.SBA_RICH_CONTENT&&typeof window.SBA_RICH_CONTENT.normalizeMarkdownSource==='function'
        ?window.SBA_RICH_CONTENT.normalizeMarkdownSource(raw):raw;
      if(typeof marked.setOptions==='function')marked.setOptions({breaks:false,gfm:true,headerIds:false,mangle:false});
      html=marked.parse(src,{breaks:false,gfm:true});
      if(typeof DOMPurify!=='undefined')html=DOMPurify.sanitize(html,{ADD_ATTR:['data-ref']});
    }catch(_){html=""}
  }
  if(!html){
    let t=raw.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    t=t.replace(/```(\w*)\n([\s\S]*?)```/g,'<pre><code>$2</code></pre>');
    t=t.replace(/`([^`]+)`/g,'<code>$1</code>');
    t=t.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
    t=t.replace(/\n/g,'<br>');
    html=t;
  }
  if(slices.length)html=injectAnswerCitationSuperscripts(html,slices);
  return html;
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
/** 流式生成时仅当用户仍在底部附近才自动滚底，避免强制拉回「准备中…」占位区 */
function chatScrollBottom(force){
  if(!force&&chatScrollAwayFromBottom.value)return;
  nextTick(()=>{
    const el=_chatMsgsEl();
    if(!el)return;
    el.scrollTop=el.scrollHeight;
    updateChatScrollState();
  });
}
function chatScrollBottomClick(){chatScrollBottom(true)}
/** 用户切走会话时，后台流式仍写入 park 槽，避免污染当前可见会话任务态 */
let _chatStreamPark=null;
function chatTaskWriteCtx(){
  if(_chatStreamPark&&c.sid!==_chatStreamPark.sid)return _chatStreamPark;
  return c;
}
function parkChatStreamForLeave(){
  if(!c.chatStreaming||!c.sid||c.sid==="temp")return;
  _chatStreamPark={
    sid:c.sid,
    msgs:c.msgs,
    curTask:c.curTask,
    mainTaskHistory:c.mainTaskHistory,
  };
}
function clearChatStreamPark(){_chatStreamPark=null}
function chatStreamMsgsForAi(aiMsg){
  const ctx=chatTaskWriteCtx();
  const msgs=ctx===c?c.msgs:(ctx.msgs||[]);
  if(Array.isArray(msgs)&&msgs.includes(aiMsg))return msgs;
  if(_chatStreamPark&&Array.isArray(_chatStreamPark.msgs)&&_chatStreamPark.msgs.includes(aiMsg))return _chatStreamPark.msgs;
  return c.msgs;
}
async function persistChatSessionSnapshot(sid,msgs,curTask,mainTaskHistory){
  if(!sid||sid==="temp")return;
  const payload={
    messages:msgs||[],
    cur_task:curTask,
    main_task_history:filterChatSessionMainHistory(mainTaskHistory||[]),
    prefs:{model:c.model,agentId:c.agentId,deepThink:c.deepThink,webSearch:c.webSearch,ragPrefetch:c.ragPrefetch,readComments:c.readComments,includeRss:c.includeRss,chatPrefs:c.chatPrefs},
  };
  try{
    await fetch("/api/chat/sessions/"+encodeURIComponent(sid)+"/state",{
      method:"PUT",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify(slimPayloadForServer(payload)),
    });
    ldCs();
  }catch(_){}
}
async function flushParkedChatStreamIfAny(aiMsg){
  const park=_chatStreamPark;
  if(!park||!park.sid)return;
  if(aiMsg)syncMainTaskResultIndex(aiMsg);
  await persistChatSessionSnapshot(park.sid,park.msgs,park.curTask,park.mainTaskHistory);
  if(c.sid===park.sid){
    c.curTask=park.curTask;
    c.mainTaskHistory=park.mainTaskHistory;
  }
  clearChatStreamPark();
}
watch(()=>c.msgs.length,()=>nextTick(updateChatScrollState));
watch(()=>c.th,()=>nextTick(updateChatScrollState));
watch(()=>c.msgs.map(m=>String(m.content||"")+(m._answerStreaming?"s":"")).join("|"),()=>scheduleChatRichHydrate(),{flush:"post"});
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
/** 将 SSE 阶段/技术文案转为用户可读的进度提示（禁止「编排引擎」等内部术语） */
function formatUserChatProgressText(raw){
  let s=String(raw||'').trim();
  if(!s)return'';
  if(/编排引擎|LangGraph|编排启动|运行时|MCP 已在|checkpoint|handoff|graph/i.test(s))
    return'正在处理您的请求';
  const exact={
    '意图识别':'正在识别意图',
    '改写':'正在理解您的问题',
    '改写确认':'正在确认问题表述',
    '槽位确认':'正在确认任务信息',
    '槽位填充':'正在确认任务信息',
    '任务拆解':'正在规划执行步骤',
    '上下文增强':'正在补充上下文',
    'RAG决策':'正在判断是否需要检索',
    '知识库检索':'正在检索知识库',
    '知识库预检索中…':'正在检索知识库',
    '绑定执行工具':'正在准备工具能力',
    '简单任务直答':'正在生成最终回答',
    '正在处理您的请求':'正在处理您的请求',
    '正在处理…':'正在处理您的请求',
    '正在准备…':'正在处理您的请求',
  };
  if(exact[s])return exact[s];
  for(const[k,v] of Object.entries(exact)){
    if(s.includes(k))return v;
  }
  const execM=s.match(/^正在执行[:：]\s*(.+)$/);
  if(execM){
    const step=String(execM[1]||'').trim();
    return step?`正在执行下一步：${step}`:'正在执行下一步';
  }
  if(/^正在编排[:：]/.test(s)){
    const step=s.replace(/^正在编排[:：]\s*/,'').trim();
    return step?`正在执行下一步：${step}`:'正在执行下一步';
  }
  if(/^正在生成/.test(s))return s.replace(/…+$/,'')+'…';
  if(/^正在/.test(s)&&!/…$/.test(s))return s.slice(0,48)+'…';
  return s.endsWith('…')?s:s.slice(0,48)+'…';
}
/** 流式占位文案：可被 pipeline_progress / orchestration_node_start 等真实 SSE 覆盖 */
function isChatLoadingPlaceholder(text){
  const t=String(text||'').trim();
  if(!t)return false;
  if(/…$/.test(t)&&/^正在/.test(t))return true;
  return /^(正在处理|正在准备|正在分析|正在生成|正在识别|正在延续|正在检索|正在执行|正在理解|正在确认|正在规划|正在补充|正在判断|正在绑定)/.test(t);
}
function setChatProgressText(aiMsg,stage,fallback){
  const st=formatUserChatProgressText(stage||fallback);
  if(!st)return;
  if(isChatLoadingPlaceholder(aiMsg.content)||!aiMsg.content)
    aiMsg.content=st.endsWith('…')?st:st+'…';
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
  if(isActiveCurTask(c.curTask))return true;
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
    _answerStreamState.set(aiMsg,{pending:'',mode:'token',pumpTimer:0,flushRequested:false,onDrainDone:null});
  }
  return _answerStreamState.get(aiMsg);
}
function resetAnswerStream(aiMsg){
  const st=_answerStreamState.get(aiMsg);
  if(st&&st.pumpTimer){clearTimeout(st.pumpTimer);st.pumpTimer=0}
  _answerStreamState.delete(aiMsg);
  if(aiMsg){aiMsg._answerStreamBuf='';aiMsg._answerStreamMode='token'}
}
function _finalizeAnswerStream(aiMsg){
  const st=_answerStreamState.get(aiMsg);
  const done=st&&st.onDrainDone;
  if(st&&st.pumpTimer){clearTimeout(st.pumpTimer);st.pumpTimer=0}
  _answerStreamState.delete(aiMsg);
  if(aiMsg){
    aiMsg._answerStreamBuf='';
    aiMsg._answerStreaming=false;
    hydrateMsgCitations(aiMsg);
  }
  chatScrollBottom();
  if(typeof done==='function')done();
}
function _pumpAnswerStreamTick(aiMsg){
  const st=_ensureAnswerStreamState(aiMsg);
  st.pumpTimer=0;
  if(!st.pending){
    if(st.flushRequested)_finalizeAnswerStream(aiMsg);
    return;
  }
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
  }else if(st.flushRequested){
    _finalizeAnswerStream(aiMsg);
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
/** 正常结束：按打字机速率排空 pending，禁止 answer_end 一次性贴全文 */
function flushAnswerStream(aiMsg,opts){
  const force=!!(opts&&opts.force);
  const st=_answerStreamState.get(aiMsg);
  if(!st){
    if(aiMsg)aiMsg._answerStreaming=false;
    return Promise.resolve();
  }
  if(force){
    if(st.pumpTimer){clearTimeout(st.pumpTimer);st.pumpTimer=0}
    if(st.pending){
      _clearAnswerPlaceholder(aiMsg);
      aiMsg.content=(aiMsg.content||'')+st.pending;
      st.pending='';
    }
    _finalizeAnswerStream(aiMsg);
    return Promise.resolve();
  }
  st.flushRequested=true;
  if(!st.pending&&!st.pumpTimer){
    _finalizeAnswerStream(aiMsg);
    return Promise.resolve();
  }
  if(!st.pumpTimer)_pumpAnswerStreamTick(aiMsg);
  return new Promise(resolve=>{st.onDrainDone=resolve});
}
function mergeThinkingStep(arr,d){
  if(!Array.isArray(arr))return;
  cacheStepIoFromEvent(d);
  const i=arr.findIndex(x=>x.step_id===d.step_id);
  const prev=i>=0?arr[i]:null;
  let st=thinkingStepUserLayer(d);
  if(!st&&prev){
    st={...prev};
    attachStepUiRuntime(st,d);
  }else if(!st){
    return;
  }else{
    if(prev)copyStepGroupFields(st,prev);
    copyStepGroupFields(st,d);
    attachStepUiRuntime(st,d);
  }
  const endSt=String(d.status||stepUi(st).status||"").toLowerCase();
  if(st._ui&&['done','completed','failed'].includes(endSt)&&!(c.chatPrefs&&c.chatPrefs.showToolIo))st._ui.io_expanded=false;
  if(i>=0){
    const keepIo=arr[i]._ui&&arr[i]._ui.io_expanded;
    Object.assign(arr[i],st);
    if(keepIo&&arr[i]._ui)arr[i]._ui.io_expanded=keepIo;
  }else arr.push(st);
  const tw=chatTaskWriteCtx();
  if(tw.curTask&&Array.isArray(tw.curTask.steps)){
    const ti=tw.curTask.steps.findIndex(x=>x&&x.step_id===d.step_id);
    const ct=slimThinkingStepForStorage(st)||{...st};
    ct._ui=st._ui;
    if(ti>=0){
      Object.assign(tw.curTask.steps[ti],ct);
    }else tw.curTask.steps.push(ct);
  }
}
let _thinkDeltaRaf=0;
const _thinkStreamState=new WeakMap();
function _ensureThinkStreamState(step){
  if(!_thinkStreamState.has(step)){
    _thinkStreamState.set(step,{pending:'',pumpTimer:0});
  }
  return _thinkStreamState.get(step);
}
function enqueueThinkStream(step,chunk,stepId){
  if(!step||!chunk)return;
  const st=_ensureThinkStreamState(step);
  st.pending+=String(chunk);
  appendStepThinkDeltaToCache(stepId,chunk);
  _scheduleThinkStreamPump(step);
}
function _scheduleThinkStreamPump(step){
  const st=_ensureThinkStreamState(step);
  if(st.pumpTimer)return;
  _pumpThinkStreamTick(step);
}
function _pumpThinkStreamTick(step){
  const st=_ensureThinkStreamState(step);
  st.pumpTimer=0;
  if(!st.pending){
    step._thinkStreaming=false;
    return;
  }
  const {piece,rest}=_takeOneGrapheme(st.pending);
  st.pending=rest;
  if(piece){
    step._thinkStreaming=true;
    step.think_text=stripReactDisplayMarkers((step.think_text||'')+piece);
    if(tw.curTask&&Array.isArray(tw.curTask.steps)){
      const ts=tw.curTask.steps.find(x=>x&&x.step_id===step.step_id);
      if(ts)ts.think_text=step.think_text;
    }
    chatScrollBottom();
  }
  if(st.pending){
    st.pumpTimer=setTimeout(()=>_pumpThinkStreamTick(step),_streamIntervalMs(st.pending.length));
  }else{
    step._thinkStreaming=false;
  }
}
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
  task_switch_confirm:"主任务切换确认",
  paused:"编排已暂停",
  unknown:"人工确认",
};
function hitlKindTitle(kind){return HITL_KIND_LABELS[kind]||HITL_KIND_LABELS.unknown}
function clearChatHitl(){
  const h=c.chatHitl;
  h.active=false;h.kind="";h.title="";h.message="";h.payload=null;
  h.traceId="";h.checkpointNs="";h.taskId="";h.threadId="";h.phase="";
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
  h.checkpointNs=String(d.checkpoint_ns||inner.checkpoint_ns||h.traceId||"");
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
    flushAnswerStream(aiMsg,{force:true});
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
  const hitl={action:act,checkpoint_ns:c.chatHitl.checkpointNs||c.chatHitl.traceId||""};
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
    checkpoint_ns:c.chatHitl.checkpointNs||c.chatHitl.traceId||"",
    message:String((extra&&extra.message)||c.inp||"(HITL resume)").trim()||"(HITL resume)",
    hitl,
  });
}
function chatPauseStreaming(){
  if(c.chatAbort){try{c.chatAbort.abort()}catch(_){}}
  c.chatStreaming=false;
  const parkAi=_chatStreamPark&&Array.isArray(_chatStreamPark.msgs)
    ? _chatStreamPark.msgs.slice().reverse().find(x=>x&&x.role==="assistant")
    : null;
  const m=parkAi||c.chatHitlResumeMsg||c.msgs[c.msgs.length-1];
  if(m&&m.role==="assistant"){
    m._answerStreaming=false;
    flushAnswerStream(m,{force:true});
    if(!m.content||isChatLoadingPlaceholder(m.content))
      m.content="[已暂停] 流式输出已中断，可调整输入后重新发送";
  }
  const tw=chatTaskWriteCtx();
  if(tw.curTask)tw.curTask.status="paused";
  if(_chatStreamPark){
    persistChatSessionSnapshot(_chatStreamPark.sid,_chatStreamPark.msgs,_chatStreamPark.curTask,_chatStreamPark.mainTaskHistory);
    clearChatStreamPark();
  }else scheduleChatPersist();
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
    await flushAnswerStream(aiMsg);
    c.chatStreaming=false;
    c.chatAbort=null;
    c.th='';
    if(_chatStreamPark){
      await flushParkedChatStreamIfAny(aiMsg);
    }else{
      syncMainTaskResultIndex(aiMsg);
      scheduleChatPersist();
    }
  }
}
function chatHitlConfirm(){
  const kind=c.chatHitl.kind||"";
  if(kind==="task_switch_confirm"){
    const p=(c.chatHitl.payload&&typeof c.chatHitl.payload==="object")?c.chatHitl.payload:{};
    const pending=String(p.pending_query||c.chatHitl.message||"").trim();
    const qs=pending.slice(0,80);
    if(pending){
      c.curTask=normalizeCurTask({
        task_id:"pending_new",
        user_query:pending,
        query_summary:qs,
        status:"planning",
        task_kind:"main",
        sub_plan_id:"",
        steps:[],
        result_msg_index:null,
        result_status:"pending",
      });
      const aiMsg=c.chatHitlResumeMsg;
      if(aiMsg&&aiMsg.role==="assistant"){
        aiMsg.task_id="pending_new";
        aiMsg.execTaskKind="main";
        aiMsg.thinkingExpanded=true;
        if(!aiMsg.content||isChatLoadingPlaceholder(aiMsg.content))
          setChatProgressText(aiMsg,"分析任务中","正在创建新主任务…");
      }
    }
    chatResumeHitl("switch_new",{});
    return;
  }
  chatResumeHitl("confirm",{});
}
function chatHitlContinueCurrentTask(){
  chatResumeHitl("continue_main",{});
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
  const tw=chatTaskWriteCtx();
  if(curEvent==='stream_error'){
    // 禁止直出原始报错；后端应已改走 answer 事件，此处仅兜底等待分析结果
    aiMsg._errorAnalyzing=true;
    if(!aiMsg.content||isChatLoadingPlaceholder(aiMsg.content))
      aiMsg.content='正在分析错误原因，请稍候…';
    aiMsg._answerStreaming=false;
    flushAnswerStream(aiMsg,{force:true});
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
        setChatProgressText(aiMsg,stage||'意图识别','正在处理您的请求');
      else if(d.orchestration_engine==='pending'||stage.indexOf('会话')>=0)
        aiMsg.content='正在准备会话上下文…';
      else if(stage)setChatProgressText(aiMsg,stage,'正在处理您的请求');
      else aiMsg.content='正在处理您的请求…';
    }
    aiMsg.orchestrationEngine=d.orchestration_engine||'';
    if(d.orchestration_engine==='langgraph'&&Array.isArray(d.expected_orchestration_phases))
      aiMsg.expectedOrchestrationPhases=d.expected_orchestration_phases.slice();
  }else if(curEvent==='pipeline_progress'){
    aiMsg.pipelineStage=d.stage||d.detail||'';
    setChatProgressText(aiMsg,d.stage||d.detail,'');
    const tidPg=String(d.task_id||aiMsg.task_id||'').trim();
    if(tidPg){
      if(!tw.curTask||String(tw.curTask.task_id||'')!==tidPg){
        const histRow=(tw.mainTaskHistory||[]).find(t=>t.task_id===tidPg);
        tw.curTask={
          task_id:tidPg,
          user_query:String(histRow&&histRow.user_query||'').trim(),
          query_summary:String(histRow&&histRow.query_summary||'').slice(0,80),
          status:'executing',task_kind:'main',sub_plan_id:'',steps:[],
          result_msg_index:null,result_status:'pending',
        };
      }
      aiMsg.task_id=tidPg;
      aiMsg.thinkingExpanded=true;
    }
  }else if(curEvent==='orchestration_node_start'){
    aiMsg.pipelineStage=d.progress_hint||d.step_name||'';
    if(!Array.isArray(aiMsg.thinking))aiMsg.thinking=[];
    const ph0=String(d.phase||'orchestration').toLowerCase();
    const nm0=String(d.step_name||d.stage||'编排节点').trim();
    const arr=aiMsg.thinking;
    const dupOrch=arr.find(x=>x&&String(x.step_name||'').trim()===nm0&&String(x.phase||stepUi(x).phase||'').toLowerCase()===ph0&&(x.status==='running'||!String(x.result||getStepIoFields(x).think_text||'').trim()));
    const orchStub={
      step_id:dupOrch&&dupOrch.step_id||(d.step_id||('orch_'+Date.now())),
      step_name:d.step_name||d.stage||'编排节点',
      status:'running',phase:d.phase||'orchestration',step_lane:'orchestration',
      node_kind:'orchestration',sub_plan_id:d.sub_plan_id||'',sub_index:d.sub_index||0,
      think_text:'',description:d.progress_hint||'',      duration_ms:0,io_expanded:false,expanded:false,
    };
    if(dupOrch)Object.assign(dupOrch,orchStub);else arr.push(orchStub);
    aiMsg.thinking=arr;
    const tid=String(d.task_id||aiMsg.task_id||'').trim();
    if(tid){
      if(!tw.curTask||String(tw.curTask.task_id||'')!==tid){
        tw.curTask={
          task_id:tid,user_query:'',query_summary:'',status:'executing',task_kind:'main',
          sub_plan_id:d.sub_plan_id||'',steps:[],result_msg_index:null,result_status:'pending',
        };
      }
      tw.curTask.steps=tw.curTask.steps||[];
      const dup=tw.curTask.steps.find(x=>x.step_id===orchStub.step_id);
      if(!dup)tw.curTask.steps.push({...orchStub});
      aiMsg.task_id=tid;
      aiMsg.thinkingExpanded=true;
    }
    setChatProgressText(aiMsg,d.progress_hint||d.step_name||d.stage,'正在执行下一步');
  }else if(curEvent==='thinking_start'){
    if(d.ephemeral){
      if(!aiMsg.content||isChatLoadingPlaceholder(aiMsg.content))aiMsg.content='正在识别意图…';
    }else if(tw.curTask&&(!d.task_id||tw.curTask.task_id===d.task_id)){
      tw.curTask.status='executing';
      upsertMainTaskHistory({task_id:tw.curTask.task_id,status:'executing'});
    }
  }else if(curEvent==='task_created'){
    const uq=String(d.user_query||'').trim();
    const qs=String(d.query_summary||uq||'').slice(0,80);
    const tid=String(d.task_id||'').trim();
    const keepSteps=(tw.curTask&&tw.curTask.task_id===tid&&Array.isArray(tw.curTask.steps))?tw.curTask.steps.slice():[];
    const keepFromMsg=Array.isArray(aiMsg.thinking)?aiMsg.thinking.filter(Boolean).slice():[];
    const mergedSteps=keepSteps.length?keepSteps:keepFromMsg;
    tw.curTask={
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
      aiMsg.content='正在规划任务…';
    upsertMainTaskHistory({
      task_id:d.task_id,
      user_query:uq,
      query_summary:qs,
      status:tw.curTask.status,
      task_kind:tw.curTask.task_kind,
      result_msg_index:null,
      result_status:'pending',
    });
    if(d.rewrite_snapshot&&tw.curTask)tw.curTask.rewrite_snapshot=d.rewrite_snapshot;
    if((d.task_kind||'main')!=='simple'&&d.persist_main_task!==false){
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
        if(tw.curTask){
          tw.curTask.steps=Array.isArray(tw.curTask.steps)?tw.curTask.steps.slice():[];
          if(!tw.curTask.steps.length)tw.curTask.steps=aiMsg.thinking.slice();
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
    if(d.detected_intent&&typeof d.detected_intent==='object')aiMsg._detectedIntent=d.detected_intent;
    if(d.rewrite_snapshot)aiMsg._rewriteSnapshot=d.rewrite_snapshot;
    const kind=d.task_kind||'main';
    const isContinue=!!d.continue_main_task||d.task_action==='continue_main';
    aiMsg.execContinueMain=!!isContinue;
    aiMsg.execTaskKind=isContinue?'continue_main':kind;
    aiMsg.execSubPlanId=d.sub_plan_id||'';
    if(d.is_simple||kind==='simple'||d.persist_main_task===false){
      aiMsg.execTaskKind='simple';
      if(!shouldKeepCurTaskOnSimpleIntent())tw.curTask=null;
      aiMsg.task_id='';
      if(!aiMsg.content||isChatLoadingPlaceholder(aiMsg.content))
        aiMsg.content='正在生成最终回答…';
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
          const prev=tw.curTask&&tw.curTask.task_id===tid?tw.curTask:null;
          const histRow=(tw.mainTaskHistory||[]).find(t=>t.task_id===tid);
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
        const prevSteps=(tw.curTask&&tw.curTask.task_id===tid&&Array.isArray(tw.curTask.steps))?tw.curTask.steps.slice():[];
        const msgSteps=Array.isArray(aiMsg.thinking)?aiMsg.thinking.filter(Boolean).slice():[];
        const mergedSteps=prevSteps.length?prevSteps:msgSteps;
        if(!tw.curTask||tw.curTask.task_id!==tid){
          tw.curTask={
            task_id:tid,user_query:userQ,query_summary:qs,
            status:isContinue?'executing':'planning',task_kind:kind,sub_plan_id:d.sub_plan_id||'',steps:mergedSteps,
            result_msg_index:null,result_status:'pending',
            rewrite_snapshot:d.rewrite_snapshot||null,
          };
        }else{
          if(!tw.curTask.steps||!tw.curTask.steps.length)tw.curTask.steps=mergedSteps;
          tw.curTask.task_kind=kind;
          tw.curTask.sub_plan_id=d.sub_plan_id||tw.curTask.sub_plan_id||'';
          if(isContinue)tw.curTask.status='executing';
          if(!preserve){
            if(userQ&&!tw.curTask.user_query)tw.curTask.user_query=userQ;
            if(qs)tw.curTask.query_summary=qs;
          }else{
            if(userQ&&!isMainTaskFollowUpQuery(userQ)){
              if(!tw.curTask.user_query||isMainTaskFollowUpQuery(tw.curTask.user_query))tw.curTask.user_query=userQ;
              if(!tw.curTask.query_summary||isMainTaskFollowUpQuery(tw.curTask.query_summary))tw.curTask.query_summary=qs;
            }
          }
          if(d.rewrite_snapshot)tw.curTask.rewrite_snapshot=d.rewrite_snapshot;
        }
        upsertMainTaskHistory({
          task_id:tid,
          user_query:tw.curTask.user_query,
          query_summary:tw.curTask.query_summary,
          status:tw.curTask.status,
          task_kind:kind,
        });
      }
      if(d.rewrite_snapshot&&tw.curTask)tw.curTask.rewrite_snapshot=d.rewrite_snapshot;
      if(isContinue)setChatProgressText(aiMsg,(d.needs_rag||c.ragPrefetch)?'正在检索知识库':'正在延续主任务','');
      if(kind==='main'&&!d.is_simple){
        maybeExpandTaskChain(true);
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
    if(tw.curTask){
      tw.curTask.steps=tw.curTask.steps||[];
      const ex2=tw.curTask.steps.find(x=>x.step_id===d.step_id);
      if(ex2)Object.assign(ex2,stub);else tw.curTask.steps.push({...stub});
    }
  }else if(curEvent==='step_think_delta'){
    if(!Array.isArray(aiMsg.thinking))return;
    const st=aiMsg.thinking.find(x=>x.step_id===d.step_id);
    if(st){
      st.llm_powered=!!(st.llm_powered||d.llm_powered);
      if(d.think_kind)st.think_kind=d.think_kind;
      enqueueThinkStream(st,d.content||'',d.step_id);
      scheduleThinkDeltaFlush();
    }
  }else if(curEvent==='step_output_delta'){
    if(!Array.isArray(aiMsg.thinking))return;
    const st=aiMsg.thinking.find(x=>x.step_id===d.step_id);
    if(st){
      st.output_text=(st.output_text||'')+(d.content||'');
      const cache=ensureStepIoCache();
      const id=String(d.step_id);
      const prev=cache[id]||{input_text:'',output_text:'',think_text:''};
      prev.output_text=(prev.output_text||'')+(d.content||'');
      cache[id]=prev;
      if(tw.curTask&&Array.isArray(tw.curTask.steps)){
        const ts=tw.curTask.steps.find(x=>x&&x.step_id===d.step_id);
        if(ts)ts.output_text=st.output_text;
      }
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
    const tid=String(d.task_id||aiMsg.task_id||'').trim();
    if(tid&&shouldAttachStepToCurTask(tid,aiMsg)){
      aiMsg.task_id=tid;
      if(!tw.curTask||String(tw.curTask.task_id||'')!==tid){
        const histRow=(tw.mainTaskHistory||[]).find(t=>t.task_id===tid);
        tw.curTask={
          task_id:tid,
          user_query:String(histRow&&histRow.user_query||'').trim(),
          query_summary:String(histRow&&histRow.query_summary||'').slice(0,80),
          status:'executing',task_kind:'main',sub_plan_id:d.sub_plan_id||'',steps:[],
          result_msg_index:null,result_status:'pending',
        };
      }
      tw.curTask.steps=tw.curTask.steps||[];
      const ex2=tw.curTask.steps.find(x=>x.step_id===stub.step_id);
      if(ex2)Object.assign(ex2,stub);else tw.curTask.steps.push({...stub});
      maybeExpandTaskChain(true);
      aiMsg.thinkingExpanded=true;
    }
  }else if(curEvent==='thought_step_end'){
    if(!Array.isArray(aiMsg.thinking))aiMsg.thinking=[];
    mergeThinkingStep(aiMsg.thinking,d);
    if(tw.curTask){
      tw.curTask.steps=Array.isArray(tw.curTask.steps)?tw.curTask.steps:[];
      mergeThinkingStep(tw.curTask.steps,d);
    }
    if(d.phase==='intent'||(d.step_name||'').includes('意图')){
      const j=parseStepJson(d.output_text);
      const isCont=!!(j&&(j.continue_main_task||['continue_main','continue'].includes(String(j.task_action||j.mode||''))));
      aiMsg.execContinueMain=!!isCont;
      if(isCont){
        aiMsg.execTaskKind='continue_main';
      }else{
        const kind=(j&&j.task_kind)||(j&&j.is_simple?'simple':'main');
        aiMsg.execTaskKind=kind;
        if(kind==='simple'){
          if(!shouldKeepCurTaskOnSimpleIntent())tw.curTask=null;
          aiMsg.task_id='';
        }
      }
      if(tw.curTask&&aiMsg.execTaskKind!=='simple'){tw.curTask.task_kind=aiMsg.execTaskKind;if(d.sub_plan_id)tw.curTask.sub_plan_id=d.sub_plan_id}
      const snap={
        query:String(d.input_text||''),
        rewritten_query:(j&&j.rewritten_query)||String(d.input_text||''),
        keywords:(j&&j.keywords)||[],
        needs_rag:!!(j&&j.needs_rag),
        metadata:(j&&j.metadata)||{},
        rewrite_state:(j&&j.rewrite_state)||'rewrite_confirm',
        confidence:(j&&j.confidence)||0,
      };
      if(tw.curTask)tw.curTask.rewrite_snapshot=snap;
      aiMsg.rewriteSnapshot=snap;
    }
  }else if(curEvent==='thinking_delta'){
    c.th=(c.th||'')+(d.content||'');
  }else if(curEvent==='thinking_end'){
    c.th='';
    if(tw.curTask)tw.curTask.bundle=d.bundle;
  }else if(curEvent==='answer_preface'){
    if(d.content){
      if(!aiMsg.content||isChatLoadingPlaceholder(aiMsg.content))aiMsg.content='';
      aiMsg.content=String(d.content||'');
      chatScrollBottom();
    }
    if(tw.curTask)tw.curTask.status='executing';
  }else if(curEvent==='rag_prefetch_slices'){
    const slices=Array.isArray(d.slices)?d.slices:[];
    if(slices.length){
      aiMsg.ragPrefetchSlices=slices;
      if(tw.curTask)tw.curTask.rag_prefetch_slices=slices;
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
    if(tw.curTask)tw.curTask.status='executing';
  }else if(curEvent==='execution_segment_start'){
    aiMsg.executionSegmentLabel=d.label||'ReAct 执行';
    if(tw.curTask)tw.curTask.status='executing';
  }else if(curEvent==='pipeline_wait_start'){
    aiMsg.pipelineWaitLabel=d.label||'等待后台流水线';
    aiMsg.pipelineWaitIds=Array.isArray(d.pipeline_task_ids)?d.pipeline_task_ids:[];
    if(tw.curTask){
      tw.curTask.status='executing';
      tw.curTask.pipeline_wait=true;
    }
  }else if(curEvent==='tool_wait_checkpoint'){
    aiMsg._hadToolWait=true;
    aiMsg.toolWaitCheckpoint=d;
    setChatProgressText(
      aiMsg,
      d.message||'已提交耗时工具，正在等待执行完成；完成后会自动继续当前回答。',
      '工具调用中'
    );
    if(tw.curTask){
      tw.curTask.status='executing';
      tw.curTask.execution_state='tool_calling';
      tw.curTask.execution_status_text='工具调用中';
      tw.curTask.tool_wait_checkpoint=d;
    }
  }else if(curEvent==='pipeline_wait_progress'){
    const st=d.statuses||{};
    const parts=Object.keys(st).map(k=>k+':'+st[k]);
    aiMsg.pipelineWaitLabel='流水线 '+parts.join(' · ');
    if(tw.curTask)tw.curTask.pipeline_statuses=st;
  }else if(curEvent==='pipeline_wait_end'){
    aiMsg.pipelineWaitLabel=d.ok?'流水线已完成':(d.timeout?'流水线等待超时':'流水线结束');
    if(tw.curTask){
      tw.curTask.pipeline_wait=false;
      if(d.ok)tw.curTask.status='executing';
    }
  }else if(curEvent==='tool_checkpoint_resumed'){
    aiMsg._toolCheckpointResumed=true;
    aiMsg.toolWaitCheckpoint=d;
    setChatProgressText(
      aiMsg,
      d.message||'工具执行结束，已恢复 Agent，正在继续生成当前回答。',
      '正在继续回答'
    );
    if(tw.curTask){
      tw.curTask.status='executing';
      tw.curTask.execution_state='agent_resumed';
      tw.curTask.execution_status_text='已恢复执行';
      tw.curTask.tool_wait_checkpoint=d;
    }
  }else if(curEvent==='answer_generating'){
    aiMsg.answerStageLabel=formatUserChatProgressText(d.label||'正在生成最终回答');
    if(!aiMsg.content||isChatLoadingPlaceholder(aiMsg.content))
      setChatProgressText(aiMsg,d.label||'正在生成最终回答','正在生成最终回答');
  }else if(curEvent==='answer_start'){
    _clearAnswerPlaceholder(aiMsg);
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
    aiMsg.span={
      ...aiMsg.span,
      total_token_count:d.token_usage?d.token_usage.prompt+d.token_usage.completion:0,
      answer_stream_done:true,
    };
    if(d.search_results)aiMsg.span.search_results=d.search_results;
    if(isChatLoadingPlaceholder(aiMsg.content))aiMsg.content='';
  }else if(curEvent==='tool_call_failed'){
    const fn=String(d.tool_name||'').trim()||'工具';
    const code=String(d.error_code||'').trim();
    const em=String(d.error_message||'').trim();
    const brief=formatChatToolFailBrief(code,em);
    if(!Array.isArray(aiMsg.thinking))aiMsg.thinking=[];
    let st=aiMsg.thinking.find(x=>x&&x.status==='running'&&String(x.step_name||'').indexOf(fn)>=0);
    if(!st)st=aiMsg.thinking.find(x=>x&&x.status==='running'&&String(x.phase||'')==='tool');
    if(!st){
      st={
        step_id:'tool_fail_'+(d.trace_id||Date.now()),
        step_name:fn,
        status:'failed',
        phase:'tool',
        step_lane:'execution',
        node_kind:'tool_call',
        sub_plan_id:'',
        sub_index:0,
        think_text:'',
        description:brief,
        result_brief:brief,
        duration_ms:0,
        io_expanded:true,
        expanded:true,
        error_code:code,
      };
      aiMsg.thinking.push(st);
    }else{
      st.status='failed';
      st.error_code=code;
      st.result_brief=brief;
      st.description=String(d.retry_hint||'').trim()||brief;
    }
    if(tw.curTask&&String(d.task_id||'').trim()&&String(tw.curTask.task_id||'')===String(d.task_id)){
      tw.curTask.steps=tw.curTask.steps||[];
      const sid=st.step_id;
      const dup=tw.curTask.steps.find(x=>x.step_id===sid);
      const row={step_id:sid,kind:'react',what:fn,result:brief};
      if(dup)Object.assign(dup,row);else tw.curTask.steps.push(row);
    }
    aiMsg.thinkingExpanded=true;
    setChatProgressText(aiMsg,fn+' 失败',brief);
  }else if(curEvent==='task_completed'){
    aiMsg._errorAnalyzing=false;
    aiMsg._answerStreaming=false;
    flushAnswerStream(aiMsg,{force:true});
    aiMsg.span={...aiMsg.span,...d};
    if(aiMsg._hadToolWait&&aiMsg._toolCheckpointResumed)markChatCompletionHeart();
    if(Array.isArray(d.tool_outputs))aiMsg.span.tool_outputs=d.tool_outputs;
    if(d.snapshot_json)aiMsg.span.snapshot_json=d.snapshot_json;
    const persist=d.persist_main_task!==false&&!d.ephemeral&&!!(d.task_id||'').trim();
    if(persist){
      aiMsg.task_audit={task_id:d.task_id,status:d.status,snapshot_json:d.snapshot_json,tool_outputs:d.tool_outputs};
      aiMsg.result_status=mapResultJudgment(d.status||'resolved');
      if(isChatLoadingPlaceholder(aiMsg.content)){
        const pr=String(d.pause_reason||'').trim();
        if(pr)aiMsg.content=pr;
      }
      if(tw.curTask){
        tw.curTask.status=normalizeParentTaskStatus(d.status||'resolved','resolved');
        tw.curTask.total_duration_ms=d.total_duration_ms;
        tw.curTask.total_token_count=d.total_token_count;
        tw.curTask.result_status=aiMsg.result_status;
        if(d.pause_reason)tw.curTask.pause_reason=d.pause_reason;
        if(Array.isArray(d.tool_outputs))tw.curTask.tool_outputs=d.tool_outputs;
        if(d.snapshot_json)tw.curTask.snapshot_json=d.snapshot_json;
        upsertMainTaskHistory({
          task_id:tw.curTask.task_id,
          status:tw.curTask.status,
          result_status:aiMsg.result_status,
          total_duration_ms:d.total_duration_ms,
        });
      }
      syncMainTaskResultIndex(aiMsg);
      if(tw.curTask&&isParentTaskTerminal(tw.curTask.status)){
        c.taskExpanded=false;
        tw.curTask=null;
      }else if(tw.curTask&&normalizeParentTaskStatus(tw.curTask.status,'executing')==='abnormal'){
        c.taskExpanded=true;
      }
    }else if(!shouldKeepCurTaskOnSimpleIntent()){
      tw.curTask=null;
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
    if(tw.curTask&&d.parent_status&&isParentTaskStatusRaw(d.parent_status)){
      tw.curTask.status=normalizeParentTaskStatus(d.parent_status,tw.curTask.status);
    }
    const patch={duration_ms:d.elapsed_ms,token_count:d.token_count,success:d.success,confidence:d.confidence};
    const st=aiMsg.thinking.find(x=>x.step_id===d.step_id);
    if(st)Object.assign(st,patch);
    if(tw.curTask){
      const ts=(tw.curTask.steps||[]).find(x=>x.step_id===d.step_id);
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
  const r=await fetchWithTimeout(url,{
    method:'POST',headers,body:JSON.stringify(payload),signal,
  },CHAT_SSE_HEADERS_TIMEOUT_MS);
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
  let gotFirst=false;
  const firstDeadline=Date.now()+CHAT_SSE_FIRST_EVENT_TIMEOUT_MS;
  const readWithFirstDeadline=()=>{
    const readP=reader.read();
    if(gotFirst)return readP;
    const remain=Math.max(400,firstDeadline-Date.now());
    return Promise.race([
      readP,
      new Promise((_,rej)=>setTimeout(()=>{
        const ex=new Error('等待服务端 SSE 首包超时');
        ex.name='TimeoutError';
        ex.code='sse_first_event_timeout';
        rej(ex);
      },remain)),
    ]);
  };
  while(true){
    const{value,done}=await readWithFirstDeadline();
    if(done)break;
    buf+=decoder.decode(value,{stream:true});
    buf=parseSseBuffer(buf,(ev,d)=>{
      if(!gotFirst){gotFirst=true;}
      ingestChatSseEvent(ev,d,aiMsg);
    });
  }
  if(buf.trim())parseSseBuffer(buf+'\n\n',(ev,d)=>{
    if(!gotFirst){gotFirst=true;}
    ingestChatSseEvent(ev,d,aiMsg);
  });
  if(!gotFirst){
    const ex=new Error('服务端未返回任何 SSE 事件');
    ex.name='TimeoutError';
    ex.code='sse_first_event_timeout';
    throw ex;
  }
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
  if(c.curTask&&!isActiveCurTask(c.curTask)){
    c.curTask=null;
    c.taskExpanded=false;
  }
  c.inp='';
  const aiMsg={role:'assistant',content:'正在处理您的请求…',thinking:[],span:{},thinkingExpanded:true,execTaskKind:'pending',execSubPlanId:'',task_id:'',result_status:'pending',_answerStreamBuf:'',rewriteSnapshot:null};
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
  }catch(e){
    if(e&&e.name==="AbortError"&&!(e.code)){
      aiMsg.content=(aiMsg.content||"")+"\n\n[已暂停]";
      if(c.curTask)c.curTask.status="paused";
    }else{
      aiMsg.content=await buildChatStreamFailureMessage(e,aiMsg);
      aiMsg.result_status='error';
      if(c.curTask&&['planning','executing','created','summarizing'].includes(String(c.curTask.status||'')))
        c.curTask.status='abnormal';
    }
  }
  finally{
    await flushAnswerStream(aiMsg,{force:true});
    aiMsg._answerStreaming=false;
    if(isChatLoadingPlaceholder(aiMsg.content)&&String(aiMsg._answerStreamBuf||'').trim()){
      aiMsg.content=String(aiMsg._answerStreamBuf||'').trim();
    }
    c.chatStreaming=false;
    c.chatAbort=null;
    c.th='';
    if(_chatStreamPark){
      await flushParkedChatStreamIfAny(aiMsg);
    }else{
      syncMainTaskResultIndex(aiMsg);
      scheduleChatPersist();
    }
  }
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

/* ══ 文本阅读入口（阅读器在 /preview/md.html 弹窗） ══ */
const _readerMod=typeof SBA_CREATE_READER_MODULE==="function"?SBA_CREATE_READER_MODULE({
  reactive,showToastMsg,
}):null;
const rh=_readerMod?_readerMod.rh:reactive({recent:[]});
const initReaderPage=_readerMod?_readerMod.initReaderPage:()=>{};
const readerPickLocalFile=_readerMod?_readerMod.readerPickLocalFile:()=>{};
const onReaderLocalFile=_readerMod?_readerMod.onReaderLocalFile:()=>{};
const readerOpenRecent=_readerMod?_readerMod.readerOpenRecent:()=>{};
const readerOpenOutputFile=_readerMod?_readerMod.readerOpenOutputFile:()=>{};
const fmtRecentTime=_readerMod?_readerMod.fmtRecentTime:()=>"—";
const refreshReaderRecent=_readerMod?_readerMod.refreshReaderRecent:()=>{};

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
  if(mobilePortrait.value)mobileAgpzStep.value='edit';
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
  if(s==='connecting')return '正在处理';
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
async function ldKbS(refresh){
  try{
    const q=refresh?'?refresh=1':'';
    const r=await fetch('/api/doc/rag/stats'+q,{headers:authBearerHeaders()});
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
    kb.st.chunkAggMs=data.chunk_agg_ms!=null?data.chunk_agg_ms:null;
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
async function ldKbF(refresh){
  try{
    const q=refresh?'&refresh=1':'';
    const r=await fetch('/api/doc/rag/files?page=1&size=500'+q,{headers:authBearerHeaders()});
    const d=await parseApiJson(r);
    if(!d.ok){showToastMsg('文件列表加载失败');return;}
    kb.fs=d.files||[];
    if(!kb.fs.length&&d.total>0){
      showToastMsg('列表为空但服务端共 '+d.total+' 条，请刷新或检查权限');
    }
    const sum=typeof d.list_chunk_sum==='number'?d.list_chunk_sum:kb.fs.reduce((a,f)=>a+(parseInt(f.chunk_count,10)||0),0);
    kb.st.listChunkSum=sum;
    kb.st.chunkMismatch=Math.abs(sum-(kb.st.tc||0))>0;
    if(d.chunk_agg_ms!=null)kb.st.chunkAggMs=d.chunk_agg_ms;
    if(d.chunk_count_source)kb.st.chunkSrc=d.chunk_count_source;
  }catch(e){
    console.error('[kb] ldKbF',e);
    showToastMsg('知识库列表：'+(e.message||String(e)));
  }
}
async function kbRefreshAll(){
  await ldKbS(true);
  await ldKbF(true);
}
async function kbSyncChunkCounts(){
  kb.syncBusy=true;
  try{
    const r=await fetch('/api/doc/rag/sync-chunk-counts',{method:'POST'});
    const d=await parseApiJson(r);
    if(!d.ok)throw new Error(d.error||'同步失败');
    showToastMsg('已回写 '+ (d.updated||0) +' 条记录（Milvus 合计 '+ (d.milvus_total_chunks||'?') +' 切片）');
    await kbRefreshAll();
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
    await kbRefreshAll();
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
  exportTxt:false,
  summarize:false,
  page:1,
  zoom:3,
  ocrEngine:"auto",
  flowDirection:"auto",
  vlmRefine:true,
  columnBands:0,
  columnBandSplit:true,
  skipArrows:true,
  fcResult:null,
  previewHtml:"",
  previewMdPath:"",
  previewMeta:null,
  lightboxSrc:"",
});
function mmCloseLightbox(){mm.lightboxSrc=""}
function mmOnPreviewClick(e){
  if(window.SBA_RICH_CONTENT&&typeof window.SBA_RICH_CONTENT.handleKbPictureClick==="function"){
    window.SBA_RICH_CONTENT.handleKbPictureClick(e,(src)=>{mm.lightboxSrc=src});
  }
}
function mmRenderPreviewHtml(raw){
  if(window.SBA_RICH_CONTENT&&typeof window.SBA_RICH_CONTENT.renderRichContentHtml==="function"){
    return window.SBA_RICH_CONTENT.renderRichContentHtml(raw);
  }
  return "";
}
async function mmLoadPreview(res){
  const mdPath=(res&&res.md_path||"").trim();
  mm.previewMdPath=mdPath;
  mm.previewMeta={
    image_count_exported:res&&res.image_count_exported,
    image_count:res&&res.image_count,
    assets_bundle_dir:res&&res.assets_bundle_dir,
    manifest_export_path:res&&res.manifest_export_path,
    export_url_prefix:res&&res.export_url_prefix,
  };
  if(!mdPath){mm.previewHtml="";return}
  try{
    const r=await fetch("/api/doc/export-md?path="+encodeURIComponent(mdPath));
    const d=await r.json().catch(()=>({}));
    if(!r.ok||!d.ok){mm.previewHtml="";return}
    mm.previewHtml=mmRenderPreviewHtml(d.content||"");
    nextTick(()=>{
      if(window.SBA_RICH_CONTENT&&typeof window.SBA_RICH_CONTENT.scheduleMermaidHydrate==="function"){
        const el=document.querySelector(".mm-preview-body");
        if(el)window.SBA_RICH_CONTENT.scheduleMermaidHydrate(el);
      }
    });
  }catch(_){mm.previewHtml=""}
}
function mmOpenPreviewMd(){
  if(!mm.previewMdPath){showToastMsg("暂无导出 MD");return}
  openOutputMdByPath(mm.previewMdPath,"split",{from:"multimodal"});
}
function persistMmPrefs(){
  try{
    localStorage.setItem("sba_mm_export_txt",mm.exportTxt?"1":"0");
    localStorage.setItem("sba_mm_summarize",mm.summarize?"1":"0");
    localStorage.setItem("sba_mm_flow_ocr_engine",mm.ocrEngine);
    localStorage.setItem("sba_mm_flow_direction",mm.flowDirection);
    localStorage.setItem("sba_mm_flow_vlm_refine",mm.vlmRefine?"1":"0");
  }catch(_){}
}
function ldMmPrefs(){
  try{
    mm.exportTxt=localStorage.getItem("sba_mm_export_txt")==="1";
    mm.summarize=localStorage.getItem("sba_mm_summarize")==="1";
    mm.ocrEngine=localStorage.getItem("sba_mm_flow_ocr_engine")||"auto";
    mm.flowDirection=localStorage.getItem("sba_mm_flow_direction")||"auto";
    mm.vlmRefine=localStorage.getItem("sba_mm_flow_vlm_refine")!=="0";
  }catch(_){}
}
ldMmPrefs();
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
    node_count:Number(res.node_count||res.final_block_count||0),
    edge_count:Number(res.edge_count||0),
    page_count:Number(res.page_count||1),
    direction:res.direction||"",
    diagram_title:res.diagram_title||"",
    mermaid:res.mermaid||"",
    mermaid_path:res.mermaid_path||"",
    ocr_diagnostics:res.ocr_diagnostics||[],
  };
}
function mmOcrProviderLabel(){
  const names=[];
  for(const diag of (mm.fcResult&&mm.fcResult.ocr_diagnostics||[])){
    for(const provider of (diag&&diag.providers||[])){
      if(provider&&provider.ok&&provider.name)names.push(provider.name);
    }
    for(const used of (diag&&diag.used||[])){
      if(used)names.push(used);
    }
  }
  return [...new Set(names)].join(" + ")||"已完成";
}
async function mmCopyMermaid(){
  const code=(mm.fcResult&&mm.fcResult.mermaid||"").trim();
  if(!code){showToastMsg("暂无 Mermaid");return}
  try{await navigator.clipboard.writeText(code);showToastMsg("Mermaid 已复制")}
  catch(_){prompt("复制 Mermaid",code)}
}
function mmDownloadMermaid(){
  const code=(mm.fcResult&&mm.fcResult.mermaid||"").trim();
  if(!code){showToastMsg("暂无 Mermaid");return}
  const blob=new Blob([code+"\n"],{type:"text/plain;charset=utf-8"});
  const a=document.createElement("a");
  a.href=URL.createObjectURL(blob);
  a.download="flowchart-"+Date.now()+".mmd";
  a.click();
  setTimeout(()=>URL.revokeObjectURL(a.href),1000);
}
async function docProcFlowchart(){
  if(mm.busy)return;
  if(!mm.queue.length){showToastMsg("请先将 PDF/图片加入队列");return}
  mm.busy=true;
  mm.fcResult=null;
  const stamp=()=>new Date().toLocaleTimeString();
  try{
    docLogLine("["+stamp()+"] 流程图转 Mermaid "+mm.queue.length+" 个文件 …");
    docLogLine("  参数: page="+mm.page+" zoom="+mm.zoom+" ocr="+mm.ocrEngine+" direction="+mm.flowDirection+" vlm_refine="+mm.vlmRefine);
    const list=[...mm.queue];
    for(let i=0;i<list.length;i++){
      const it=list[i];
      docLogLine("  ["+(i+1)+"/"+list.length+"] "+it.name);
      try{
        const body={
          path:it.path,
          page:mm.page,
          zoom:mm.zoom,
          ocr_engine:mm.ocrEngine,
          direction:mm.flowDirection,
          vlm_refine:!!mm.vlmRefine,
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
        const providers=(res.ocr_diagnostics||[]).flatMap(x=>x.used||[]).filter((v,i,a)=>a.indexOf(v)===i).join("+");
        docLogLine("      → OK 节点:"+res.node_count+" 连线:"+res.edge_count+" 方向:"+res.direction+(providers?" OCR:"+providers:"")+(cuts?" 切线y:"+cuts:""));
        if(i===list.length-1)mmFcApplyResult(res);
      }catch(e){docLogLine("      → 请求异常: "+(e.message||String(e)))}
    }
    docLogLine("["+stamp()+"] 流程图转 Mermaid 完成");
    showToastMsg("流程图已转换为 Mermaid");
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
    docLogLine("  选项: export_md=是 export_txt="+mm.exportTxt+" summarize="+mm.summarize);
    const list=[...mm.queue];
    for(let i=0;i<list.length;i++){
      const it=list[i];
      docLogLine("  ["+(i+1)+"/"+list.length+"] "+it.name);
      try{
        const r=await fetch("/api/doc/process",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
          path:it.path,
          export_txt:!!mm.exportTxt,
          summarize:!!mm.summarize,
        })});
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
        const mdPath=(res.md_path||"").trim();
        const txtPath=(res.txt_path||"").trim();
        const sumNote=res.summarized?" 已摘要":(res.summarize_requested&&res.summarize_error?" 摘要失败:"+String(res.summarize_error).slice(0,120):"");
        const imgNote=res.image_count_exported?" 图片:"+res.image_count_exported:"";
        docLogLine("      → "+(ok?"OK":"FAIL")+" 类型:"+dt+" 文本长度:"+ntxt+(mdPath?" MD:"+mdPath:"")+(txtPath?" TXT:"+txtPath:"")+imgNote+sumNote+(err?" 错误:"+err.slice(0,400):""));
        if(ok&&mdPath)await mmLoadPreview(res);
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
/** 网关节点池：各厂商 API 格式、分区表单与默认接入参数 */
const GW_POOL_SECTION={
  title:"节点池调度",
  layout:"grid-2",
  tone:"muted",
  desc:"以下字段仅在本系统节点池与「任务路由」中使用，与厂商控制台无关。",
  fields:[
    {key:"name",label:"节点名称",placeholder:"如：主接入点 / 备用 DeepSeek",hint:"运维可读别名，列表中快速识别",source:"本系统自定义"},
    {key:"id",label:"节点 ID",placeholder:"node_primary",hint:"任务路由配置里引用的 ID 须与此一致",source:"英文+下划线，如 node_primary"},
    {key:"priority",label:"优先级",type:"number",placeholder:"10",hint:"strict_priority：数字越小越优先",source:"本系统 · 任务路由"},
    {key:"weight",label:"权重",type:"number",placeholder:"100",hint:"system_compete：按权重随机分流",source:"本系统 · 任务路由"},
    {key:"status",label:"启用状态",type:"select",wide:true,hint:"inactive 节点不参与任何 Agent 路由",source:"本系统配置"},
  ],
};
const GW_PROVIDERS=[
  {id:"ark",label:"火山引擎",brand:"ARK",apiFormat:"Ark SDK · Chat Completions",accent:"#6366f1",
   defaultBase:"https://ark.cn-beijing.volces.com/api/v3",needsBase:true,
   modelLabel:"接入点 ID",modelPlaceholder:"ep-20260616011833-xxxxx",
   modelHint:"方舟控制台 → 在线推理 → 复制 Endpoint ID",keyHint:"火山方舟 API Key",
   desc:"火山方舟在线推理：请求走 Chat Completions，model 字段填 ep- 前缀接入点 ID（非模型名）。",
   sections:[
    {title:"推理接入点",layout:"stack",tone:"primary",desc:"决定调用哪个已部署模型/版本，按接入点计费。",
     fields:[
      {key:"endpoint_id",label:"接入点 ID",required:true,wide:true,mono:true,
       placeholder:"ep-20260616011833-xxxxx",
       hint:"对应 HTTP 请求体 model 字段，必须以 ep- 开头",
       source:"火山方舟控制台 → 在线推理 → 推理接入点 → 复制 Endpoint ID"},
     ]},
    {title:"服务地址",layout:"stack",tone:"default",
     fields:[
      {key:"base_url",label:"API Base URL",wide:true,mono:true,
       placeholder:"https://ark.cn-beijing.volces.com/api/v3",
       hint:"默认北京区；新加坡/美区部署请在控制台查看对应域名",
       source:"控制台 → 在线推理 → API 调用 → Base URL（含 /api/v3）"},
     ]},
    {title:"身份凭证",layout:"stack",tone:"secret",
     fields:[
      {key:"api_key",label:"API Key",type:"password",required:true,wide:true,
       placeholder:"火山方舟 API Key",
       hint:"写入 Authorization: Bearer … 请求头",
       source:"火山方舟控制台 → API Key 管理 → 创建并复制（勿泄露）"},
     ]},
    GW_POOL_SECTION,
   ]},
  {id:"openai",label:"OpenAI",brand:"GPT",apiFormat:"OpenAI · /v1/chat/completions",accent:"#10a37f",
   defaultBase:"https://api.openai.com/v1",needsBase:true,
   modelLabel:"Model",modelPlaceholder:"gpt-4o · gpt-4-turbo · o3-mini",
   modelHint:"与 OpenAI 官方模型 ID 一致",keyHint:"sk-proj-...",
   desc:"官方 OpenAI Chat Completions；也适用于多数 OpenAI 兼容中转（需自行改 Base URL）。",
   sections:[
    {title:"模型与端点",layout:"stack",tone:"primary",
     fields:[
      {key:"endpoint_id",label:"Model ID",required:true,wide:true,mono:true,
       placeholder:"gpt-4o · gpt-4-turbo · o3-mini",
       hint:"即 POST /v1/chat/completions 的 model 参数",
       source:"OpenAI Platform → Models → 复制 Model name"},
      {key:"base_url",label:"Base URL",wide:true,mono:true,
       placeholder:"https://api.openai.com/v1",
       hint:"官方默认；Azure / 私有网关请填网关提供的根路径",
       source:"OpenAI 文档或网关文档 · 通常以 /v1 结尾"},
     ]},
    {title:"身份凭证",layout:"stack",tone:"secret",
     fields:[
      {key:"api_key",label:"API Key",type:"password",required:true,wide:true,
       placeholder:"sk-proj-...",
       hint:"Project Key 或 Legacy User Key",
       source:"platform.openai.com → API keys → Create new secret key"},
     ]},
    GW_POOL_SECTION,
   ]},
  {id:"anthropic",label:"Anthropic",brand:"Claude",apiFormat:"Anthropic · Messages API",accent:"#d97706",
   defaultBase:"https://api.anthropic.com",needsBase:false,baseOptional:true,
   modelLabel:"Model",modelPlaceholder:"claude-sonnet-4-20250514",
   modelHint:"Anthropic 模型名，非 deployment",keyHint:"sk-ant-...",
   desc:"Claude Messages API（非 OpenAI Chat Completions）；验证连接会走 /v1/messages。",
   sections:[
    {title:"Claude 模型",layout:"stack",tone:"primary",
     fields:[
      {key:"endpoint_id",label:"Model",required:true,wide:true,mono:true,
       placeholder:"claude-sonnet-4-20250514 · claude-3-5-sonnet-20241022",
       hint:"Anthropic 模型字符串，不是 Azure deployment 名",
       source:"console.anthropic.com → Models → 复制 Model ID"},
     ]},
    {title:"身份凭证",layout:"stack",tone:"secret",
     fields:[
      {key:"api_key",label:"API Key",type:"password",required:true,wide:true,
       placeholder:"sk-ant-api03-...",
       hint:"x-api-key 请求头；与 OpenAI sk- 格式不同",
       source:"Anthropic Console → API Keys → Create Key"},
     ]},
    {title:"可选 · 自定义 API 地址",layout:"stack",tone:"default",
     fields:[
      {key:"base_url",label:"Base URL",wide:true,mono:true,optional:true,
       placeholder:"https://api.anthropic.com",
       hint:"留空则使用官方 api.anthropic.com；私有代理需填完整根域名",
       source:"企业代理文档或 Anthropic 官方端点"},
     ]},
    GW_POOL_SECTION,
   ]},
  {id:"deepseek",label:"DeepSeek",brand:"DS",apiFormat:"OpenAI 兼容 · DeepSeek",accent:"#2563eb",
   defaultBase:"https://api.deepseek.com/v1",needsBase:true,
   modelLabel:"Model",modelPlaceholder:"deepseek-chat · deepseek-reasoner",
   modelHint:"DeepSeek 开放平台模型 ID",keyHint:"sk-...",
   desc:"DeepSeek 开放平台，OpenAI 兼容 /chat/completions。",
   sections:[
    {title:"模型",layout:"stack",tone:"primary",
     fields:[
      {key:"endpoint_id",label:"Model",required:true,wide:true,mono:true,
       placeholder:"deepseek-chat · deepseek-reasoner",
       hint:"deepseek-reasoner 为推理模型，响应较慢",
       source:"platform.deepseek.com → API 文档 → 模型列表"},
      {key:"base_url",label:"Base URL",wide:true,mono:true,
       placeholder:"https://api.deepseek.com/v1",
       hint:"官方固定；勿与 chat.deepseek.com 网页地址混淆",
       source:"DeepSeek 开放平台 → API 文档"},
     ]},
    {title:"身份凭证",layout:"stack",tone:"secret",
     fields:[
      {key:"api_key",label:"API Key",type:"password",required:true,wide:true,placeholder:"sk-...",
       hint:"OpenAI 兼容 Bearer Token",
       source:"DeepSeek 控制台 → API Keys"},
     ]},
    GW_POOL_SECTION,
   ]},
  {id:"dashscope",label:"通义千问",brand:"Qwen",apiFormat:"DashScope · OpenAI 兼容",accent:"#7c3aed",
   defaultBase:"https://dashscope.aliyuncs.com/compatible-mode/v1",needsBase:true,
   modelLabel:"Model",modelPlaceholder:"qwen-plus · qwen-max",
   modelHint:"百炼 compatible-mode 模型名",keyHint:"sk-...",
   desc:"阿里云 DashScope OpenAI 兼容模式（compatible-mode/v1）。",
   sections:[
    {title:"百炼模型",layout:"stack",tone:"primary",
     fields:[
      {key:"endpoint_id",label:"Model",required:true,wide:true,mono:true,
       placeholder:"qwen-plus · qwen-max · qwen-turbo",
       hint:"须使用 compatible-mode 支持的模型名",
       source:"阿里云百炼控制台 → 模型广场 → 复制模型名称"},
      {key:"base_url",label:"Base URL",wide:true,mono:true,
       placeholder:"https://dashscope.aliyuncs.com/compatible-mode/v1",
       hint:"必须含 compatible-mode/v1 路径",
       source:"百炼文档 → OpenAI 兼容接口 → Base URL"},
     ]},
    {title:"身份凭证",layout:"stack",tone:"secret",
     fields:[
      {key:"api_key",label:"API Key",type:"password",required:true,wide:true,placeholder:"sk-...",
       hint:"DashScope API-KEY（非 AccessKey 对）",
       source:"百炼控制台 → API-KEY 管理 → 创建"},
     ]},
    GW_POOL_SECTION,
   ]},
  {id:"moonshot",label:"月之暗面",brand:"Kimi",apiFormat:"Moonshot · OpenAI 兼容",accent:"#0f172a",
   defaultBase:"https://api.moonshot.cn/v1",needsBase:true,
   modelLabel:"Model",modelPlaceholder:"moonshot-v1-8k",
   modelHint:"Moonshot 模型 ID",keyHint:"sk-...",
   desc:"Kimi / Moonshot 开放平台，OpenAI 兼容协议。",
   sections:[
    {title:"Kimi 模型",layout:"stack",tone:"primary",
     fields:[
      {key:"endpoint_id",label:"Model",required:true,wide:true,mono:true,
       placeholder:"moonshot-v1-8k · moonshot-v1-32k · moonshot-v1-128k",
       hint:"后缀表示上下文窗口档位",
       source:"Moonshot 开放平台 → 模型与定价"},
      {key:"base_url",label:"Base URL",wide:true,mono:true,
       placeholder:"https://api.moonshot.cn/v1",
       source:"Moonshot 开放平台 → API 文档"},
     ]},
    {title:"身份凭证",layout:"stack",tone:"secret",
     fields:[
      {key:"api_key",label:"API Key",type:"password",required:true,wide:true,placeholder:"sk-...",
       source:"Moonshot 控制台 → API Key 管理"},
     ]},
    GW_POOL_SECTION,
   ]},
  {id:"zhipu",label:"智谱 AI",brand:"GLM",apiFormat:"Zhipu · OpenAI 兼容",accent:"#0891b2",
   defaultBase:"https://open.bigmodel.cn/api/paas/v4",needsBase:true,
   modelLabel:"Model",modelPlaceholder:"glm-4-plus",
   modelHint:"智谱模型 ID",keyHint:"...",
   desc:"智谱 BigModel OpenAI 兼容端点（/api/paas/v4）。",
   sections:[
    {title:"GLM 模型",layout:"stack",tone:"primary",
     fields:[
      {key:"endpoint_id",label:"Model",required:true,wide:true,mono:true,
       placeholder:"glm-4-plus · glm-4-flash · glm-4-air",
       source:"open.bigmodel.cn → 模型列表 → 模型编码"},
      {key:"base_url",label:"Base URL",wide:true,mono:true,
       placeholder:"https://open.bigmodel.cn/api/paas/v4",
       hint:"v4 兼容 OpenAI 格式；旧版 v3 路径不同",
       source:"智谱开放平台 → API 文档"},
     ]},
    {title:"身份凭证",layout:"stack",tone:"secret",
     fields:[
      {key:"api_key",label:"API Key",type:"password",required:true,wide:true,
       placeholder:"智谱 API Key",
       source:"智谱控制台 → API Keys → 创建"},
     ]},
    GW_POOL_SECTION,
   ]},
  {id:"ollama",label:"Ollama",brand:"Local",apiFormat:"Ollama · OpenAI 兼容",accent:"#64748b",
   defaultBase:"http://127.0.0.1:11434/v1",needsBase:true,keyOptional:true,
   modelLabel:"Model",modelPlaceholder:"llama3.2 · qwen2.5",
   modelHint:"本地 ollama list 中的模型名",keyHint:"可选",
   desc:"本地 Ollama 服务；需先 ollama pull 模型，API Key 通常可留空。",
   sections:[
    {title:"本地推理",layout:"stack",tone:"primary",
     fields:[
      {key:"endpoint_id",label:"Model",required:true,wide:true,mono:true,
       placeholder:"llama3.2 · qwen2.5 · deepseek-r1",
       hint:"与 ollama list 中 NAME 列一致（不含 :latest 亦可）",
       source:"终端执行 ollama list · 或 ollama.com/library"},
      {key:"base_url",label:"Ollama 地址",wide:true,mono:true,
       placeholder:"http://127.0.0.1:11434/v1",
       hint:"远程机器填 http://IP:11434/v1；Docker 注意端口映射",
       source:"本机默认 11434 · 环境变量 OLLAMA_HOST"},
     ]},
    {title:"身份凭证",layout:"stack",tone:"default",
     fields:[
      {key:"api_key",label:"API Key",type:"password",optional:true,wide:true,
       placeholder:"可选 · 默认 ollama",
       hint:"本地默认不校验；若 nginx 加了 Bearer 鉴权再填写",
       source:"通常留空"},
     ]},
    GW_POOL_SECTION,
   ]},
  {id:"custom",label:"自定义",brand:"Compat",apiFormat:"OpenAI 兼容 · 自定义 Base",accent:"#475569",
   defaultBase:"",needsBase:true,
   modelLabel:"Model / Deployment",modelPlaceholder:"your-model-id",
   modelHint:"网关文档中的 model 参数",keyHint:"按网关要求",
   desc:"任意 OpenAI Chat Completions 兼容网关（OneAPI、New API、私有中转等）。",
   sections:[
    {title:"网关参数",layout:"stack",tone:"primary",
     fields:[
      {key:"base_url",label:"Base URL",required:true,wide:true,mono:true,
       placeholder:"https://your-gateway.example.com/v1",
       hint:"须能 POST …/chat/completions；以网关文档为准",
       source:"网关管理后台 · 接入文档"},
      {key:"endpoint_id",label:"Model / Deployment",required:true,wide:true,mono:true,
       placeholder:"your-model-id",
       hint:"有些网关用 deployment 名而非公开模型名",
       source:"网关模型列表或渠道配置"},
     ]},
    {title:"身份凭证",layout:"stack",tone:"secret",
     fields:[
      {key:"api_key",label:"API Key",type:"password",required:true,wide:true,
       placeholder:"按网关要求",
       source:"网关后台 → Token / API Key"},
     ]},
    GW_POOL_SECTION,
   ]},
];
function gwProvMeta(id){return GW_PROVIDERS.find(p=>p.id===id)||GW_PROVIDERS[0]}
function gwFormSections(providerId){
  const meta=gwProvMeta(providerId);
  const sections=meta.sections||[];
  return sections.map(function(sec){
    const fields=(sec.fields||[]).map(function(f){
      const out=Object.assign({},f);
      if(out.key==="base_url"&&!out.placeholder&&meta.defaultBase)out.placeholder=meta.defaultBase;
      if(out.key==="api_key"){
        if(meta.keyOptional)out.optional=true;
        if(!out.placeholder&&meta.keyHint)out.placeholder=meta.keyHint;
      }
      if(out.key==="endpoint_id"){
        if(!out.label&&meta.modelLabel)out.label=meta.modelLabel;
        if(!out.placeholder&&meta.modelPlaceholder)out.placeholder=meta.modelPlaceholder;
        if(!out.hint&&meta.modelHint)out.hint=meta.modelHint;
      }
      return out;
    });
    return Object.assign({},sec,{fields:fields});
  });
}
function gwFieldPlaceholder(f,providerId){
  const meta=gwProvMeta(providerId);
  if(f.key==="base_url")return f.placeholder||meta.defaultBase||"";
  if(f.key==="endpoint_id")return f.placeholder||meta.modelPlaceholder||"";
  if(f.key==="api_key")return f.placeholder||meta.keyHint||"sk-...";
  return f.placeholder||"";
}
function gwProvCardStyle(p){return{"--gw-accent":p.accent||"#6366f1"}}
function pickGwProvider(pid){
  const prev=gwProvMeta(st.nf.provider);
  const next=gwProvMeta(pid);
  const prevBase=(prev.defaultBase||"").trim();
  const curBase=(st.nf.base_url||"").trim();
  st.nf.provider=pid;
  if(next.needsBase||next.baseOptional){
    if(!curBase||curBase===prevBase)st.nf.base_url=next.defaultBase||"";
  }else if(!curBase||curBase===prevBase){
    st.nf.base_url="";
  }
  st.tcResult=null;
}
function gwTestPayload(){
  const p=(st.nf.provider||"ark").trim();
  let provider=p;
  if(["dashscope","moonshot","zhipu","ollama","custom","deepseek"].includes(p))provider="openai";
  return{provider,api_key:st.nf.api_key,base_url:st.nf.base_url,model:st.nf.endpoint_id};
}
function providerBadge(n){return gwProvMeta((n&&n.provider)||"ark").brand}
function gwNodeAccent(n){return gwProvMeta((n&&n.provider)||"ark").accent}
function resetGwForm(){
  const d=GW_PROVIDERS[0];
  Object.assign(st.nf,{id:"",name:"",provider:"ark",base_url:d.defaultBase||"",api_key:"",endpoint_id:"",priority:"10",weight:"100",status:"active"});
  st.ni=-1;st.tcResult=null;
}
const st=reactive({sec:"agent",lk:"run",sub:"gw",rt:"rte",pa:"doc_standardize_agent",ma:"doc_standardize_agent",ni:-1,nds:[],nf:{id:"",name:"",provider:"ark",base_url:"https://ark.cn-beijing.volces.com/api/v3",api_key:"",endpoint_id:"",priority:"10",weight:"100",status:"active"},ags:{summary_agent:{label:"摘要化",mode:"system_compete",nodes:""},doc_standardize_agent:{label:"原文整理",mode:"system_compete",nodes:""},ops_agent:{label:"运维",mode:"system_compete",nodes:""},qa_orchestrator_agent:{label:"AI对话",mode:"system_compete",nodes:""},longpage_html_assembler_agent:{label:"HTML 编排",mode:"system_compete",nodes:""},longpage_diagram_legend_agent:{label:"HTML 图例生成",mode:"system_compete",nodes:""}},wfs:{},mc:"",tpl:{ot:"",fnr:""},htm:{en:true,ad:true,mb:20971520,to:60,ato:600},fs:{ci:"",pi:60,pz:20,en:false},tcBusy:false,tcResult:null,imPlatforms:[],imDetail:"",imFsTab:"cfg",imFs:{enabled:false,app_id:"",app_secret:"",verification_token:"",encrypt_key:"",auto_reply:false,chat_id:"",poll_interval_sec:10,page_size:20,running:false,recent_count:0,last_error:"",last_event_at:0,webhook_path:"/api/feishu/events/webhook"},imFsMessages:[],imWx:{enabled:false,bridge_url:"",bridge_token:"",connected:false,wxid:"",nickname:"",reply_mode:"mention_only",agent_key:"qa_orchestrator_agent",webhook_secret:"",group_whitelist_text:"",last_connected_at:"",has_bridge_token:false,has_webhook_secret:false},imWxQr:{session_id:"",qr_image:"",status:"idle",error:"",polling:false}});
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
function onSchedNavClick(){
  if(navCollapsed.value){openSchedSec("jobs");return}
  schedOpen.value=!schedOpen.value;
  try{localStorage.setItem("sba_sched_open",schedOpen.value?"1":"0")}catch(_){}
}
function onSchedSubNavClick(){
  if(navCollapsed.value){openSubscribeSec(sub.sec||"up");return}
  subscribeOpen.value=!subscribeOpen.value;
  try{
    localStorage.setItem("sba_subscribe_open",subscribeOpen.value?"1":"0");
    localStorage.setItem("sba_sched_sub_open",subscribeOpen.value?"1":"0");
  }catch(_){}
}
function openSchedSec(sec){
  if(sec==="jobs"){
    page.value="sched";
    schedOpen.value=true;
    try{localStorage.setItem("sba_sched_open","1")}catch(_){}
    ldSchedJobs();
    return;
  }
  openSubscribeSec(sec==="sub"?"up":sec);
}
function onSubscribeNavClick(){
  if(navCollapsed.value){openSubscribeSec(sub.sec||"up");return}
  subscribeOpen.value=!subscribeOpen.value;
  try{localStorage.setItem("sba_subscribe_open",subscribeOpen.value?"1":"0")}catch(_){}
}
function onSubscribeXhsNavClick(){
  if(navCollapsed.value){openSubscribeSec(sub.sec||"up");return}
  subscribeXhsOpen.value=!subscribeXhsOpen.value;
  try{localStorage.setItem("sba_subscribe_xhs_open",subscribeXhsOpen.value?"1":"0")}catch(_){}
}
function openSubscribeSec(sec){
  const s=sec==="fav"?"fav":sec==="bind"?"bind":"up";
  page.value="subscribe";
  sub.sec=s;
  subscribeOpen.value=true;
  subscribeXhsOpen.value=true;
  try{
    localStorage.setItem("sba_subscribe_open","1");
    localStorage.setItem("sba_sched_sub_open","1");
    localStorage.setItem("sba_subscribe_xhs_open","1");
    localStorage.setItem("sba_sub_sec",s);
  }catch(_){}
  if(s==="up")ldUpPage();
  if(s==="fav")ldFavorites();
  if(s==="bind")ldXhsBinding();
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
  if(sec==="cdp"){ldWrCdpStatus();ldWrBridge()}
}
function wrStepShotUrl(st){
  if(!st||!st.screenshot||!wr.selDetail)return "";
  const sid=wr.selDetail.recordSessionId;
  if(!sid)return "";
  return "/api/webreplay/media/"+encodeURIComponent(sid)+"/"+encodeURIComponent(st.screenshot);
}
async function ldWrCdpStatus(){
  wr.cdp.busy=true;wr.err="";
  try{
    const r=await fetch("/api/webreplay/cdp/status",{headers:authBearerHeaders()});
    const d=await parseApiJson(r);
    if(!r.ok)throw new Error(d.detail||"CDP 状态失败");
    wr.cdp.connected=!!d.connected;
    wr.cdp.port=d.port||null;
    wr.cdp.tabCount=d.tabCount||0;
    wr.cdp.tabs=d.tabs||[];
    wr.cdp.error=d.connected?"":(d.error||"未连接");
    if(!wr.cdp.tabHint&&wr.cdp.tabs[0])wr.cdp.tabHint=wr.cdp.tabs[0].url||"";
  }catch(e){
    wr.cdp.connected=false;
    wr.cdp.error=e.message||String(e);
  }finally{wr.cdp.busy=false}
}
function wrCdpStopPoll(){
  if(wr.cdp.pollTimer){clearInterval(wr.cdp.pollTimer);wr.cdp.pollTimer=null}
}
async function wrCdpPollOnce(){
  if(!wr.cdp.sessionId)return;
  try{
    const r=await fetch("/api/webreplay/cdp/record/"+encodeURIComponent(wr.cdp.sessionId),{headers:authBearerHeaders()});
    const d=await parseApiJson(r);
    if(!r.ok)return;
    wr.cdp.stepCount=d.stepCount||0;
    wr.cdp.frameCount=d.frameCount||0;
    if(d.tabUrl)wr.cdp.tabUrl=d.tabUrl;
  }catch(_){}
}
async function wrCdpStartRecord(){
  wr.err="";wr.cdp.busy=true;
  try{
    await ldWrCdpStatus();
    if(!wr.cdp.connected)throw new Error(wr.cdp.error||"CDP 未连接");
    const name=(wr.cdp.recName||"").trim()||("cdp-"+Date.now());
    const r=await fetch("/api/webreplay/cdp/record/start",{method:"POST",headers:authJsonHeaders(),body:JSON.stringify({
      name,
      tabUrlHint:wr.cdp.tabHint||"",
    })});
    const d=await parseApiJson(r);
    if(!r.ok)throw new Error(d.detail||d.error||"启动失败");
    wr.cdp.recording=true;
    wr.cdp.sessionId=d.sessionId||"";
    wr.cdp.tabUrl=d.tabUrl||"";
    wr.cdp.stepCount=0;
    wr.cdp.frameCount=0;
    wr.cdp.recName=name;
    wrCdpStopPoll();
    wr.cdp.pollTimer=setInterval(wrCdpPollOnce,2000);
    showToastMsg("CDP 录制已开始，请在 Chrome 操作目标页");
  }catch(e){wr.err=e.message||String(e)}finally{wr.cdp.busy=false}
}
async function wrCdpStopRecord(){
  if(!wr.cdp.sessionId)return;
  wr.err="";wr.cdp.busy=true;
  try{
    const r=await fetch("/api/webreplay/cdp/record/"+encodeURIComponent(wr.cdp.sessionId)+"/stop",{method:"POST",headers:authBearerHeaders()});
    const d=await parseApiJson(r);
    if(!r.ok)throw new Error(d.detail||d.error||"保存失败");
    wrCdpStopPoll();
    wr.cdp.recording=false;
    wr.cdp.sessionId="";
    showToastMsg("已保存 "+(d.stepCount||0)+" 步脚本");
    openWebreplaySec("scripts");
    if(d.script&&d.script.id){
      wr.selId=d.script.id;
      await wrSelectScript(d.script.id);
    }else await ldWrScripts();
  }catch(e){wr.err=e.message||String(e)}finally{wr.cdp.busy=false}
}
async function wrReplayCdp(scriptId){
  if(!scriptId)return;
  wr.replayBusy=true;wr.err="";
  try{
    const r=await fetch("/api/webreplay/cdp/replay/"+encodeURIComponent(scriptId),{method:"POST",headers:authBearerHeaders()});
    const d=await parseApiJson(r);
    if(!r.ok)throw new Error(d.detail||d.error||"重放失败");
    showToastMsg("CDP 重放完成 "+(d.doneSteps||0)+"/"+(d.totalSteps||0)+" 步");
  }catch(e){wr.err=e.message||String(e)}finally{wr.replayBusy=false}
}
function wrReplayExt(script){
  if(!script)return;
  const extId=(wr.bridge.extensionId||"").trim();
  if(!extId){wr.err="请先在「扩展连接」填写 WebReplay 扩展 ID";return}
  if(typeof chrome==="undefined"||!chrome.runtime||!chrome.runtime.sendMessage){
    wr.err="扩展重放须在 Chrome 中打开本站（或已配置 externally_connectable 的页面）";
    return
  }
  wr.replayBusy=true;wr.err="";
  chrome.runtime.sendMessage(extId,{method:"run_script",params:{name:script.name}},(res)=>{
    wr.replayBusy=false;
    if(chrome.runtime.lastError){wr.err=chrome.runtime.lastError.message||"扩展调用失败";return}
    if(res&&res.ok!==false)showToastMsg("已通知扩展重放："+script.name);
    else wr.err=(res&&res.error)||"扩展重放失败";
  });
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
  if(k==="key")return "按键";
  if(k==="scroll")return "滚动";
  if(k==="wait")return "等待";
  return k||"—";
}
function wrStepDesc(st){
  if(!st)return "—";
  if(st.kind==="click"||st.kind==="input"){
    const css=st.selector&&st.selector.css;
    const inputValue=st.kind==="input"?(st.control==="checked"?" · 选中="+String(!!st.checked):(st.value?" · 值="+String(st.value).slice(0,24):"")):"";
    return (css?css.slice(0,72):"—")+inputValue;
  }
  if(st.kind==="key")return (st.key||"按键")+" · "+((st.selector&&st.selector.css)||"—").slice(0,72);
  if(st.kind==="scroll")return (st.selector&&st.selector.css?st.selector.css.slice(0,60):"页面")+" · ("+(st.x||0)+", "+(st.y||0)+")";
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
  if(pf.id==="wechat")ldImWechat()
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
const imWxInboundUrl=computed(()=>{
  const base=(typeof location!=="undefined"&&location.origin)?location.origin:"http://127.0.0.1:8000";
  return base.replace(/\/$/,"")+"/api/im-robots/wechat/inbound";
});
async function ldImWechat(){
  try{
    const r=await fetch("/api/settings/im-robots/wechat");
    const d=await r.json();
    st.imWx.enabled=!!d.enabled;
    st.imWx.bridge_url=d.bridge_url||"";
    st.imWx.connected=!!d.connected;
    st.imWx.wxid=d.wxid||"";
    st.imWx.nickname=d.nickname||"";
    st.imWx.reply_mode=d.reply_mode||"mention_only";
    st.imWx.agent_key=d.agent_key||"qa_orchestrator_agent";
    st.imWx.group_whitelist_text=Array.isArray(d.group_whitelist)?d.group_whitelist.join("\n"):"";
    st.imWx.last_connected_at=d.last_connected_at||"";
    st.imWx.has_bridge_token=!!d.has_bridge_token;
    st.imWx.has_webhook_secret=!!d.has_webhook_secret;
  }catch(e){console.warn("[IM] ldImWechat",e)}
}
async function imWxSave(){
  try{
    const body={
      enabled:!!st.imWx.enabled,
      bridge_url:(st.imWx.bridge_url||"").trim(),
      group_whitelist:(st.imWx.group_whitelist_text||"").split(/\r?\n|,/).map(s=>s.trim()).filter(Boolean),
      reply_mode:st.imWx.reply_mode||"mention_only",
      agent_key:st.imWx.agent_key||"qa_orchestrator_agent"
    };
    if(st.imWx.bridge_token)body.bridge_token=st.imWx.bridge_token;
    if(st.imWx.webhook_secret)body.webhook_secret=st.imWx.webhook_secret;
    const r=await fetch("/api/settings/im-robots/wechat/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const d=await r.json();
    if(!r.ok||d.ok===false)throw new Error(d.detail||d.error||"保存失败");
    st.imWx.bridge_token="";
    st.imWx.webhook_secret="";
    alert("微信机器人配置已保存");
    await ldImWechat();
    await ldImPlatforms();
  }catch(e){alert(e.message||"保存失败")}
}
async function imWxStartQr(){
  try{
    st.imWxQr.polling=false;
    const r=await fetch("/api/settings/im-robots/wechat/qr/start",{method:"POST"});
    const d=await r.json();
    if(!r.ok||d.ok===false)throw new Error(d.hint||d.error||"获取二维码失败");
    st.imWxQr.session_id=d.session_id||"";
    st.imWxQr.qr_image=d.qr_image||"";
    st.imWxQr.status="waiting_scan";
    st.imWxQr.error="";
    imWxPollQr();
  }catch(e){st.imWxQr.error=e.message||"获取二维码失败"}
}
async function imWxPollQr(){
  if(!st.imWxQr.session_id)return;
  st.imWxQr.polling=true;
  try{
    while(st.imWxQr.polling&&st.imWxQr.session_id){
      const r=await fetch("/api/settings/im-robots/wechat/qr/poll?session_id="+encodeURIComponent(st.imWxQr.session_id));
      const d=await r.json();
      if(!r.ok||d.ok===false){
        st.imWxQr.error=d.error||"轮询失败";
        st.imWxQr.status=d.status||"error";
        break;
      }
      st.imWxQr.status=d.status||"waiting_scan";
      if(d.status==="connected"){
        st.imWxQr.polling=false;
        await ldImWechat();
        await ldImPlatforms();
        break;
      }
      await new Promise(rs=>setTimeout(rs,2000));
    }
  }catch(e){st.imWxQr.error=e.message||"轮询失败"}
  st.imWxQr.polling=false;
}
async function imWxRefreshStatus(){
  try{
    const r=await fetch("/api/settings/im-robots/wechat/refresh-status",{method:"POST"});
    const d=await r.json();
    if(!r.ok||d.ok===false)throw new Error(d.error||"刷新失败");
    await ldImWechat();
    await ldImPlatforms();
  }catch(e){alert(e.message||"刷新失败")}
}
async function imWxDisconnect(){
  try{
    const r=await fetch("/api/settings/im-robots/wechat/disconnect",{method:"POST"});
    const d=await r.json();
    if(!r.ok||d.ok===false)throw new Error(d.error||"断开失败");
    st.imWxQr.polling=false;
    await ldImWechat();
    await ldImPlatforms();
  }catch(e){alert(e.message||"断开失败")}
}
async function imWxCopyInbound(){
  try{
    await navigator.clipboard.writeText(imWxInboundUrl.value);
    showToastMsg("微信回调地址已复制");
  }catch(_){prompt("复制回调地址",imWxInboundUrl.value)}
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
function ldNf(n){
  const prov=n.provider||"ark";
  const meta=gwProvMeta(prov);
  Object.assign(st.nf,{id:n.id||"",name:n.name||"",provider:prov,base_url:n.base_url||meta.defaultBase||"",api_key:n.api_key||"",endpoint_id:n.endpoint_id||"",priority:String(n.priority||10),weight:String(n.weight||100),status:n.status||"active"});
  st.tcResult=null;
}
async function testConn(){st.tcBusy=true;st.tcResult=null;
try{const r=await fetch('/api/settings/test-connection',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(gwTestPayload())});st.tcResult=await r.json()}catch(e){st.tcResult={ok:false,status:'error',error:e.message}}st.tcBusy=false}
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
  meta_extract_enabled:"启用元数据提取 meta_extract_enabled",
  meta_extract_fields:"元数据字段 JSON meta_extract_fields",
  meta_extract_prompt:"元数据提取 Prompt meta_extract_prompt",
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
  meta_extract_enabled:"摘要完成后额外调用 LLM，按下方 JSON 字段定义提取元数据（与摘要 title/summary 独立）。",
  meta_extract_fields:"字段数组 JSON：[{key,label,description}]。可与知识库 metadata 模板一键对齐。",
  meta_extract_prompt:"元数据提取时的补充说明（可选）。",
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
  {tok:"{comments_file_link}",title:"评论原文链接",desc:"独立评论文件的 Markdown 链接行。",sample:"评论原文已单独保存，请查看: [comments_xxx.md](./comments_xxx.md)"},
  {tok:"{meta_json}",title:"结构化元数据",desc:"按 meta_extract_fields 提取的 JSON，默认 Markdown 代码块。",sample:"## 结构化元数据\n\n```json\n{\"domain\":\"…\"}\n```"},
  {tok:"{task_note}",title:"任务备注",desc:"提交链接时填写的备注。",sample:"待跟进-面试向"},
  {tok:"{task_keywords}",title:"任务关键词",desc:"提交时填写的关键词，参与元数据提取。",sample:"Agent, 面试, RAG"}
];
const IAG_COMMENTS_SECTION_VARS=[
  {tok:"{comments_analysis}",title:"观点表+总览",desc:"LLM 按层次（提问/作者回复/派别）提炼后的正文。",sample:"| 层次 | 角色/派别 | 观点原句 | 精简解释 | AI分析 |"},
  {tok:"{comments_file_link}",title:"原文文件",desc:"全量评论落盘后的引用行。",sample:"评论原文已单独保存，请查看: [comments.md](./comments.md)"},
  {tok:"{comments_section}",title:"整段渲染结果",desc:"本模板渲染后的完整【评论区】块。",sample:"## 【评论区】\n…"}
];
const iagCommentsSectionPreview=computed(()=>iagFormatPreview((iag.fields&&iag.fields.comments_section_template)||""));
const IAG_DEFAULT_META_FIELDS=[
  {key:"domain",label:"领域",description:"文档所属业务领域（大粒度）",show_on_card:true},
  {key:"module",label:"模块",description:"所属功能模块（中粒度）",show_on_card:true},
  {key:"doc_type",label:"文档类型",description:"如产品手册/技术文档/FAQ/政策/笔记",show_on_card:false},
  {key:"author_name",label:"作者",description:"博主昵称（智能提取后写入结构化 JSON）",show_on_card:true},
  {key:"keyword1",label:"关键词1",description:"核心主题词或实体",show_on_card:true},
  {key:"keyword2",label:"关键词2",description:"次要主题词或补充实体",show_on_card:true}
];
const iagMetaExtractFieldsJson=computed({
  get(){
    var raw=iag.fields&&iag.fields.meta_extract_fields;
    if(raw==null||raw==="")return JSON.stringify(IAG_DEFAULT_META_FIELDS,null,2);
    if(typeof raw==="string"){
      try{return JSON.stringify(JSON.parse(raw),null,2)}catch(_){return String(raw)}
    }
    try{return JSON.stringify(raw,null,2)}catch(_){return "[]"}
  },
  set(v){
    if(!iag.fields)iag.fields={};
    iag.fields.meta_extract_fields=String(v||"");
  }
});
function iagNormalizeMetaFieldsAfterLoad(){
  if(!iag.fields)return;
  if(iag.fields.meta_extract_enabled==null||iag.fields.meta_extract_enabled==="")iag.fields.meta_extract_enabled=true;
  var raw=iag.fields.meta_extract_fields;
  if(raw==null||raw==="")iag.fields.meta_extract_fields=JSON.stringify(IAG_DEFAULT_META_FIELDS);
  else if(typeof raw!=="string"){
    try{iag.fields.meta_extract_fields=JSON.stringify(raw,null,2)}catch(_){}
  }
}
async function iagApplyKbMetaSchema(){
  try{
    var lib=(kb&&kb.activeLib)?String(kb.activeLib):"";
    var url="/api/settings/meta-extract-schema"+(lib?("?lib="+encodeURIComponent(lib)):"");
    var d=await fetchJsonSafe(url);
    iag.fields.meta_extract_fields=JSON.stringify(d.fields||IAG_DEFAULT_META_FIELDS,null,2);
    showToastMsg("已同步知识库 metadata 字段");
  }catch(e){showToastMsg("同步失败："+(e.message||String(e)))}
}
function iagResetMetaExtractFields(){
  iag.fields.meta_extract_fields=JSON.stringify(IAG_DEFAULT_META_FIELDS,null,2);
  showToastMsg("已恢复默认元数据字段");
}
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
    "{comments_section}":"## 【评论区】\n\n（演示观点表）",
    "{meta_json}":"## 结构化元数据\n\n```json\n{\"domain\":\"测试领域\",\"module\":\"Agent\",\"doc_type\":\"视频笔记\"}\n```",
    "{task_note}":"待跟进-面试向",
    "{task_keywords}":"Agent, RAG, 面试"
  };
  Object.keys(map).forEach(function(k){out=out.split(k).join(map[k]);});
  return out;
}
function iagHelpText(k){return IAG_HELP[k]||""}
const iagRawMode=ref(false);
const IAG_KEY_ORDER={
  summary_agent:["summary_prompt","system_prompt","rules","comments_viewpoint_prompt","comments_viewpoint_rules","comments_user_prompt","comments_summary_mode","comments_section_template","meta_extract_enabled","meta_extract_fields","meta_extract_prompt","output_template","file_naming_rule"],
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
    var hide={summary_prompt:1,system_prompt:1,rules:1,comments_viewpoint_prompt:1,comments_viewpoint_rules:1,comments_user_prompt:1,comments_summary_mode:1,comments_section_template:1,meta_extract_enabled:1,meta_extract_fields:1,meta_extract_prompt:1,output_template:1,file_naming_rule:1};
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
/** 使用独立向导 UI 的 Agent 页：不展示通用 iag-fields-block */
function iagIsGuidedTab(){
  const tab=String(iag.tab||"");
  if(tab==="reader_agent")return true;
  if(tab==="longpage_diagram_legend_agent")return true;
  if(tab==="summary_agent"&&!iagRawMode.value)return true;
  return false;
}
async function ldIag(){
  if(!isAdmin.value)return;
  iag.err="";
  const ak=iag.tab;
  try{
    const wf=await fetch("/api/settings/workflow-instructions/"+encodeURIComponent(ak));
    const wd=await wf.json();
    if(!wf.ok)throw new Error(typeof wd.detail==="string"?wd.detail:JSON.stringify(wd.detail||wd)||wf.statusText);
    iag.fields=Object.assign({},wd.fields||{});
    iagNormalizeMetaFieldsAfterLoad();
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
    if(ak==="summary_agent"){
      try{
        var parsed=JSON.parse(String(iagMetaExtractFieldsJson.value||"[]"));
        iag.fields.meta_extract_fields=JSON.stringify(parsed);
      }catch(e){throw new Error("meta_extract_fields 不是合法 JSON："+(e.message||String(e)))}
    }
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

/* ══ 定时任务管理 ══ */
const sched=reactive({jobs:[],runs:[],activeCards:[],runningCount:0,presets:[],loading:false,err:"",busyKey:"",busyRunId:"",st:{scheduler_running:false,job_count:0}});
const SCHED_STATUS_LABEL={running:"运行中",started:"已启动",in_progress:"处理中",completed:"已完成",failed:"失败",cancelled:"已取消",partial:"部分完成"};
function schedStatusLabel(c){const s=(c&&c.status)||"running";return SCHED_STATUS_LABEL[s]||s}
function schedCardStatusColor(c){
  const s=(c&&c.status)||"running";
  if(s==="running"||s==="started"||s==="in_progress")return"var(--a1)";
  if(s==="failed"||s==="cancelled")return"var(--err)";
  if(s==="completed"||s==="partial")return"var(--ok)";
  return"var(--t2)";
}
function schedCardBorderStyle(c){
  const s=String((c&&c.status)||"running");
  let color="var(--a1)";
  if(s==="completed"||s==="partial")color="var(--ok)";
  else if(s==="failed"||s==="cancelled")color="var(--err)";
  else if(s==="pending")color="var(--warn)";
  return{borderLeft:"3px solid "+color};
}
function schedCardMetricsLine(c){
  if(!c)return"";
  const parts=[];
  if(c.duration_ms>0)parts.push("耗时 "+(c.duration_ms/1000).toFixed(1)+"s");
  if(c.retry_count>0)parts.push("重试 "+c.retry_count);
  if(c.trigger)parts.push(c.trigger==="scheduled"?"周期触发":c.trigger==="manual_test"?"测试执行":c.trigger==="retry"?"手动重试":c.trigger);
  return parts.join(" · ");
}
function schedFmtTime(iso){
  if(!iso)return"";
  return String(iso).slice(0,19).split("T").join(" ");
}
function schedDescPreview(c,maxLen=48){
  const t=String((c&&c.description)||"");
  return t.length>maxLen?t.slice(0,maxLen)+"…":t;
}
function schedErrPreview(c,maxLen=120){
  const t=String((c&&c.error_message)||"");
  return t.length>maxLen?t.slice(0,maxLen)+"…":t;
}
function schedShowProgress(c){return ["running","started","in_progress"].includes(String((c&&c.status)||""))}
async function pollSchedActive(){
  try{
    const r=await fetch("/api/scheduled-jobs/active");
    const d=await r.json();
    if(!r.ok)return;
    sched.activeCards=Array.isArray(d.cards)?d.cards:[];
    sched.runningCount=Number(d.running_count)||0;
  }catch(_){}
}
async function ldSchedJobs(){
  sched.loading=true;sched.err="";
  try{
    const [rj,st,rr]=await Promise.all([
      fetch("/api/scheduled-jobs"),
      fetch("/api/scheduled-jobs/scheduler/status"),
      fetch("/api/scheduled-jobs/runs?limit=40")
    ]);
    const dj=await rj.json();const ds=await st.json();const dr=await rr.json();
    if(!rj.ok)throw new Error((dj.detail&&dj.detail.message)||dj.error||"加载任务失败");
    sched.presets=dj.presets||[];
    sched.jobs=(dj.jobs||[]).map(j=>({...j,_preset:j.frequency_preset,_enabled:!!j.enabled}));
    sched.st=ds||{};
    sched.runs=dr.runs||[];
    await pollSchedActive();
  }catch(e){sched.err=e.message||String(e);}
  finally{sched.loading=false;}
}
async function cancelSchedRun(c){
  if(!c||!c.run_id)return;
  if(!confirm("确定取消该定时任务执行？\n将在当前步骤结束后停止。"))return;
  sched.busyRunId=c.run_id;
  try{
    const r=await fetch("/api/scheduled-jobs/runs/"+encodeURIComponent(c.run_id)+"/cancel",{method:"POST"});
    const d=await r.json();
    if(!r.ok)throw new Error((d.detail&&d.detail.message)||d.error||"取消失败");
    showToastMsg(d.message||"已请求取消");
    await pollSchedActive();
  }catch(e){showToastMsg(e.message||String(e));}
  finally{sched.busyRunId="";}
}
async function retrySchedRun(c){
  if(!c||!c.run_id)return;
  sched.busyRunId=c.run_id;
  try{
    const r=await fetch("/api/scheduled-jobs/runs/"+encodeURIComponent(c.run_id)+"/retry",{method:"POST"});
    const d=await r.json();
    if(!r.ok)throw new Error((d.detail&&d.detail.message)||d.error||"重试失败");
    showToastMsg(d.summary||"已重新执行");
    await ldSchedJobs();
  }catch(e){showToastMsg(e.message||String(e));}
  finally{sched.busyRunId="";}
}
function schedPresetChange(j){j.frequency_preset=j._preset;}
async function schedSaveJob(j){
  try{
    const body={
      frequency_preset:j._preset||j.frequency_preset,
      custom_cron:j.custom_cron,
      custom_interval_minutes:j.custom_interval_minutes,
      daily_hour:j.daily_hour,
      daily_minute:j.daily_minute,
      enabled:j._enabled!=null?j._enabled:j.enabled
    };
    const r=await fetch("/api/scheduled-jobs/"+encodeURIComponent(j.job_key),{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const d=await r.json();
    if(!r.ok)throw new Error((d.detail&&d.detail.message)||d.error||"保存失败");
    showToastMsg("已保存 "+(j.name||j.job_key));
    await ldSchedJobs();
  }catch(e){showToastMsg(e.message||String(e));}
}
async function schedTestRun(j){
  sched.busyKey=j.job_key;
  try{
    const r=await fetch("/api/scheduled-jobs/"+encodeURIComponent(j.job_key)+"/run",{method:"POST"});
    const d=await r.json();
    if(!r.ok)throw new Error((d.detail&&d.detail.message)||d.error||"执行失败");
    showToastMsg((d.summary||d.status||"已触发")+" ("+((d.duration_ms||0)/1000).toFixed(1)+"s)");
    await ldSchedJobs();
    await pollSchedActive();
  }catch(e){showToastMsg(e.message||String(e));}
  finally{sched.busyKey="";}
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
  spanStats:{task_count:0,step_count:0,total_ms:0,failed_count:0,tokens:0},
  spanExcSel:null,
  spanOpsBusy:false,
  spanOpsMsg:"",
  apiEvtSel:null,
  sjRuns:[],
  sjLoading:false,
  exCatalog:[],
  exQuery:"",
  exMatch:null,
  exDetail:"",
  fb:{days:30,loading:false,data:null,list:[],err:""},
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
function opPickApiEvent(e){op.apiEvtSel=e;}
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
async function ldOpSchedJobs(){
  op.sjLoading=true;
  try{
    const [rr,re]=await Promise.all([
      fetch("/api/scheduled-jobs/runs?limit=80"),
      fetch("/api/ops/scheduled-jobs/events?limit=80")
    ]);
    const dr=await rr.json();const de=await re.json();
    const dbRuns=dr.runs||[];
    const memEv=(de.items||[]).map(e=>({...e,started_at:e.ts||e.started_at}));
    op.sjRuns=dbRuns.length?dbRuns:memEv;
  }catch(e){op.err=e.message||String(e);}
  finally{op.sjLoading=false;}
}
async function ldOpExceptions(){
  try{
    const r=await fetch("/api/ops/exception-catalog");
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||"加载失败");
    op.exCatalog=d.items||[];
    op.exDetail="";
    op.exMatch=null;
  }catch(e){op.err=e.message||String(e);}
}
async function ldOpExMatch(){
  const q=(op.exQuery||"").trim();
  if(!q){showToastMsg("请粘贴错误信息");return;}
  try{
    const r=await fetch("/api/ops/exception-proposals?error="+encodeURIComponent(q));
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||"匹配失败");
    op.exMatch=d;
    if(d.primary)op.exDetail=d.primary.proposal_md||"";
  }catch(e){op.err=e.message||String(e);}
}
function opShowExDetail(x){
  op.exDetail=(x&&x.analysis?x.analysis+"\n\n":"")+(EXCEPTION_PROPOSAL_TEXT[x.code]||"请在异常库中查看对应解决码");
  fetch("/api/ops/exception-proposals?error="+encodeURIComponent(x.title||x.code)).then(r=>r.json()).then(d=>{
    if(d.primary&&d.primary.proposal_md)op.exDetail=d.primary.proposal_md;
  }).catch(()=>{});
}
const EXCEPTION_PROPOSAL_TEXT={};
function opCopyProposal(txt){
  const t=String(txt||op.exDetail||"");
  if(!t)return;
  try{navigator.clipboard.writeText(t);showToastMsg("已复制提案");}
  catch(_){showToastMsg("复制失败，请手动选择文本");}
}
function closeOpsSpanModal(){
  opsSpanModal.open=false;
  opsSpanModal.loading=false;
}
function opPickSpanStepInModal(s){opsSpanModal.stepSel=s;}
function opFormatLogLine(ln){
  if(ln==null)return "—";
  if(typeof ln==="string")return ln;
  const parts=[];
  if(ln.level)parts.push(String(ln.level));
  if(ln.message)parts.push(String(ln.message));
  else if(ln.extra){try{parts.push(JSON.stringify(ln.extra));}catch(_){parts.push(String(ln.extra));}}
  return parts.join(" ")||"—";
}
function _fillOpsSpanModalFromBundle(d,stepSel){
  opsSpanModal.taskId=d.task_id||"";
  opsSpanModal.title=d.title||"";
  opsSpanModal.link=d.link||"";
  opsSpanModal.textLogs=d.text_logs||[];
  opsSpanModal.spans=d.spans||[];
  opsSpanModal.errors=d.errors||[];
  opsSpanModal.spanTask=d.span_task||null;
  const spans=opsSpanModal.spans;
  opsSpanModal.stepSel=stepSel||(spans.length?spans[0]:null);
}
async function openOpsSpanModal(taskId,opts){
  const tid=(taskId||"").trim();
  if(!tid){showToastMsg("无任务 ID，无法打开 SPAN 详情");return;}
  openPageOverlay("opsSpanModal",()=>{opsSpanModal.open=true;});
  opsSpanModal.loading=true;
  opsSpanModal.tab=(opts&&opts.tab)||"io";
  try{
    const r=await fetch("/api/ops/spans/tasks/"+encodeURIComponent(tid));
    const d=await r.json();
    if(!r.ok||d.ok===false)throw new Error(d.detail||d.error||"加载 SPAN 详情失败");
    _fillOpsSpanModalFromBundle(d,opts&&opts.stepSel);
  }catch(e){
    showToastMsg("SPAN 详情加载失败："+(e.message||String(e)));
    closeOpsSpanModal();
  }finally{opsSpanModal.loading=false;}
}
function opOpenSpanModalFromTask(){
  const d=op.spanDetail;
  const tid=(op.spanTaskId||"").trim();
  if(!d||!tid){showToastMsg("请先选择 SPAN 任务");return;}
  openPageOverlay("opsSpanModal",()=>{opsSpanModal.open=true;});
  opsSpanModal.loading=false;
  opsSpanModal.tab="io";
  _fillOpsSpanModalFromBundle({
    task_id:tid,
    title:d.title,
    link:d.link,
    text_logs:d.text_logs||[],
    spans:d.spans||[],
    errors:d.errors||[],
    span_task:d.span_task||null
  },op.spanStepSel);
}
async function opLoadSpanException(ex){
  if(!ex)return;
  op.spanExcSel=ex;
  const tid=(ex.task_id||"").trim();
  if(!tid)return;
  await opLoadSpanTask(tid);
  const spans=(op.spanDetail&&op.spanDetail.spans)||[];
  const stepSel=ex.step_id?(spans.find(s=>s.step_id===ex.step_id)||ex):(spans[0]||null);
  openPageOverlay("opsSpanModal",()=>{opsSpanModal.open=true;});
  opsSpanModal.tab=ex.error_message?"err":"span";
  if(op.spanDetail){
    _fillOpsSpanModalFromBundle({
      task_id:tid,
      title:op.spanDetail.title,
      link:op.spanDetail.link,
      text_logs:op.spanDetail.text_logs||[],
      spans,
      errors:op.spanDetail.errors||[],
      span_task:op.spanDetail.span_task||null
    },stepSel);
  }else{
    await openOpsSpanModal(tid,{tab:opsSpanModal.tab,stepSel});
  }
}
async function opTriggerSpanOpsAnalysis(stepSel){
  const step=stepSel||opsSpanModal.stepSel||op.spanStepSel;
  if(!step){showToastMsg("请先选择步骤");return;}
  const taskId=(opsSpanModal.taskId||op.spanTaskId||step.task_id||"").trim();
  const link=opsSpanModal.link||(op.spanDetail&&op.spanDetail.link)||"";
  op.spanOpsBusy=true;
  op.spanOpsMsg="";
  try{
    const r=await fetch("/api/ops/monitor",{
      method:"POST",
      headers:authJsonHeaders(),
      body:JSON.stringify({
        task_id:taskId,
        link,
        status:step.status||"failed",
        error_info:{
          step_id:step.step_id,
          step_name:step.step_name,
          step_type:step.step_type,
          error_message:step.error_message,
          error_code:step.error_code,
          input_payload:step.input_payload,
          output_payload:step.output_payload
        }
      })
    });
    const d=await r.json();
    if(!r.ok||!d.ok)throw new Error(d.error||"运维分析失败");
    op.spanOpsMsg=d.report_path?("分析完成："+d.report_path):(d.llm_powered?"分析已提交":"已记录（LLM 未配置）");
    showToastMsg(op.spanOpsMsg);
  }catch(e){
    op.spanOpsMsg=e.message||String(e);
    showToastMsg("运维分析失败："+op.spanOpsMsg);
  }finally{op.spanOpsBusy=false;}
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
async function ldOpFeedback(){
  op.fb.loading=true;op.fb.err="";
  try{
    const r=await fetch("/api/chat/feedback/analytics?full=1&days="+encodeURIComponent(op.fb.days||30),{headers:authBearerHeaders()});
    const d=await r.json();
    if(!r.ok)throw new Error(fmtApiErr(d,r));
    op.fb.data=d;
    const lr=await fetch("/api/chat/feedback/list?limit=50",{headers:authBearerHeaders()});
    const ld=await lr.json();
    op.fb.list=Array.isArray(ld.items)?ld.items:[];
  }catch(e){op.fb.err=e.message||String(e);}
  finally{op.fb.loading=false;}
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
      if(!isAuthenticated.value){
        redirectToLogin('/'+p);
        return;
      }
    }
    if(p==='orch'){
      try{
        const saved=localStorage.getItem('sba_orch_sec');
        orchTocActive.value=(saved&&ORCH_SEC_CRUMB[saved])?saved:'orch-sec-tool';
      }catch(_){orchTocActive.value='orch-sec-tool';}
      ldSkills();ldBuiltinTools();ldMcpCfg();ldMcpVendors();ldDiagramStyles();
      nextTick(()=>setupOrchSectionSpy());
    }else if(orchSecObs){orchSecObs.disconnect();orchSecObs=null;}
    if(p==='chat'){ldChatModels().catch(()=>{});refreshSlash();beginChatConnect();loadPlatformHealth(false);requestChatWarmup({wait:false}).catch(()=>{});}
    if(p==='rag'){kbLoadImportHistory();ldKbLibs();ldKbMetaOpts();ldKbS();ldKbF();ldKbConn();kbLoadRecallVocab();}
    if(p==='rss'){ldRssAll();}
    if(p==='subscribe'){
      subscribeOpen.value=true;
      subscribeXhsOpen.value=true;
      if(sub.sec==='fav')ldFavorites();
      else if(sub.sec==='bind')ldXhsBinding();
      else ldUpPage();
    }
    if(p==='agpz'){ldApzCatalog().then(async()=>{await syncApzTemplateToCurrentAgent();});}
    if(p==='iag'){ldIag();}
    if(p==='settings'){
      if(!isAdmin.value&&st.sec==='agent')st.sec='link';
      if(isAdmin.value&&st.sec==='agent'){ldGw();ldAr();}
      if(st.sec==='im')ldImPlatforms();
    }
    if(p==='profile'){syncProfFromAuth();ldUserPortrait();}
    if(p==='tasks'){scheduleTaskRegistryReload();}
    if(p==='fleet'){ldFleetAll();}
    if(p==='sched')ldSchedJobs();
    if(p==='ops'){
      ldOp();ldOpAg();
      if(op.sub==='ph')ldOpHealth(false);
      if(op.sub==='sp')ldOpSpansAll();
      if(op.sub==='sj')ldOpSchedJobs();
      if(op.sub==='ex')ldOpExceptions();
    }
    if(p==='reader'){initReaderPage();}
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
  hideBootLoading();

  // 初始化认证状态
  AuthManager.state.restore();
  
  // 检查当前是否在登录页
  const currentPath = window.location.pathname;
  const isLoginPage = currentPath === '/login.html' || currentPath.endsWith('/login.html');
  
  const pathPage=currentPath.replace(/^\//,'').split('/')[0];
  const allMenuItemsBoot=[...menuMainBase,{key:'subscribe',label:'链接订阅'},{key:'sched',label:'定时任务'},{key:'iag',label:'内部 Agent 配置'},{key:'webreplay',label:'浏览器自动化'}];
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
      const allMenuItems=[...menuMainBase,{key:'subscribe',label:'订阅'},{key:'iag',label:'内部 Agent 配置'},{key:'webreplay',label:'浏览器自动化'}];
      if(path&&allMenuItems.some(m=>m.key===path))target=path;
    }
    if(!target)return;
    if(guardPageSwitch(target)){
      page.value=target;
      if(target==='webreplay')openWebreplaySec(wr.sec||'scripts');
      if(target==='subscribe')openSubscribeSec(sub.sec||'up');
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
      redirectToLogin(currentPath+window.location.search);
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
  ldWfs();ldVec();await pollQueue();pollSchedActive();
  scheduleQueuePoll();
  schedTimer=setInterval(pollSchedActive,2000);
  vecTimer=setInterval(ldVec,30000);
  ldCs();kbLoadImportHistory();ldKbS();ldKbF();ldKbMetaOpts();caQ();ldOp();ldOpAg();
  if(isAdmin.value){ldGw().catch(()=>{});}
  ldHist();histTimer=setInterval(ldHist,5000);
  restoreMdReturnContext();
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
    await ldChatModels();
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
    if(favDetail.open){
      ev.preventDefault();
      if(favDetail.fullscreen)toggleFavDetailFullscreen();
      else closeFavDetail();
      return;
    }
    if(sidePanelFs.open){
      ev.preventDefault();
      closeSidePanelFs();
      return;
    }
    if(skillImport.show||kbImportMeta.show||kbBrowse.show||mmBrowse.show||modalOut.show||modalDupLink.show||modalTaskOps.show||modalArtifact.show||queueBatchMode.value||csBatchMode.value||showHist.value||chatExpandOpen.value||c.taskHistModalOpen||opsSpanModal.open){
      ev.preventDefault();
      if(queueBatchMode.value)exitQueueBatchMode();
      else if(csBatchMode.value)exitCsBatchMode();
      else closeAllPageOverlays();
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
return{page,menuMain,isAdmin,mobilePortrait,mobileNavOpen,mobileAgpzStep,mobilePageTitle,mobileBottomTabs,mobileDrawerItems,onMobileBottomTap,onMobileDrawerTap,setMobileAgpzStep,authUser,authDisplayName,authAvatarChar,userAvatarUrl,userAvatarInp,pickUserAvatar,onUserAvatarFile,uiPrefs,navTabCompact,navTabExpanded,onNavIslandEnter,onNavIslandLeave,onUiPrefsChange,persistUiPrefs,prof,portrait,goPersonalSettings,saveProfile,savePassword,saveUserPortrait,ldUserPortrait,closeUserDd,doLogout,wfs,navCollapsed,toggleNav,settingsOpen,onSettingsNavClick,openSettingsSec,webreplayOpen,onWebreplayNavClick,openWebreplaySec,schedOpen,onSchedNavClick,onSchedSubNavClick,openSchedSec,sched,ldSchedJobs,pollSchedActive,schedStatusLabel,schedCardStatusColor,schedCardBorderStyle,schedCardMetricsLine,schedFmtTime,schedDescPreview,schedErrPreview,schedShowProgress,cancelSchedRun,retrySchedRun,schedSaveJob,schedTestRun,schedPresetChange,subscribeOpen,schedSubOpen:subscribeOpen,subscribeXhsOpen,onSubscribeNavClick,onSubscribeXhsNavClick,openSubscribeSec,sub,wr,wrMcpSnippet,ldWrScripts,wrSelectScript,wrDeleteScript,wrExportAll,wrExportOne,wrImportFile,ldWrBridge,wrSaveBridge,wrCopyMcpSnippet,wrHost,wrFmtTime,wrStepKindLabel,wrStepDesc,wrStepShotUrl,ldWrCdpStatus,wrCdpStartRecord,wrCdpStopRecord,wrReplayCdp,wrReplayExt,appBreadcrumbs,goAppBreadcrumb,openTabs,appTabs,canCloseTab,switchPage,closeTab,closeOtherTabs,showTabContextMenu,sidePanelFs,isSidePanelFs,toggleSidePanelFs,closeSidePanelFs,
  v,vec,videoSubTab,subForm,subList,subSelId,subSelRow,subFmtTime,upAvatarText,upPublicId,upStatusLabel,upStatusClass,upSourceLabel,selectSubscription,subDigest,subProfile,subBlogNotes,subProfileViewMode,subViewTab,subProfileRunLabel,subProfileRunClass,ldSubscriptions,addSubscription,syncSubscription,syncAllSubscriptions,loadSubDigest,loadSubProfile,loadSubBlogNotes,seedSubCatalog,repairSubCatalogLinks,subLinkCards,subLinkPaging,loadSubLinkCards,openSubscriptionLinkTask,subLinkArtifactClass,subLinkArtifactLabel,setSubLinkPageSize,setSubLinkViewMode,subLinkPagePrev,subLinkPageNext,runCreatorProfile,pauseSubscription,resumeSubscription,deleteSubscription,favForm,favProgress,favSub,favSession,favCards,favSyncReport,favDigest,favHabit,favUpForm,favUpList,favUpSelId,favUpSelRow,upUnifiedList,upSelId,selectUpCard,ldUpPage,ldXhsBinding,ldFavorites,loadFavBoard,syncFavorites,refreshFavoritesCookies,favCardClass,favCardStatusText,favCardStatusColor,favCardSeqTitle,favCardSeqText,favCardMetaLine,favCardKeywords,favCardKeywordsLine,favCardCollectedText,favCardFollowersText,favCardSourceText,favCardSourceClass,onFavCoverError,pullFollowUps,loadFollowUps,subscribeFollowUp,profileFollowUp,removeFollowUp,renderSubDigestMd,pathBasename,outputMdPreviewUrl,openOutputMdByPath,taskQueue,taskQueueFilter,filteredTaskQueue,displayedTaskQueue,taskQueuePaging,taskQueueViewMode,taskQueuePageCount,setTaskQueuePageSize,taskQueuePagePrev,taskQueuePageNext,jumpTaskQueuePage,toggleTaskQueueViewMode,taskQueueViewModeLabel,taskQueueViewModeTitle,filteredHistTasks,taskQueueAuthorFacets,displayedTaskQueueAuthorFacets,taskQueueAuthorFacetsHiddenCount,taskQueueRecentSearches,taskQueueSearchTags,taskQueueSearchDropdownOpen,TASK_QUEUE_COND_FIELDS,TASK_QUEUE_SOURCE_OPTIONS,onTaskQueueSearchFocus,onTaskQueueSearchBlur,onTaskQueueSearchEnter,applyTaskQueueRecentSearch,applyTaskQueueSearchTag,promoteTaskQueueSearchToTag,taskQueueSearchTagExists,toggleTaskQueueAdvanced,addTaskQueueCondition,removeTaskQueueCondition,onTaskQueueCondFieldChange,taskQueueCondModes,taskQueueCondValuePlaceholder,taskQueueAdvancedActive,taskQueueAdvancedActiveCount,taskSourceLabel,taskActionLabel,taskAuthorName,taskAuthorProfileUrl,taskOpsReportId,taskCardPlatform,linkCardSourceLine,linkCardPublishedLine,linkCardAsTask,linkCardHasMd,linkCardHasHtml,openLinkCardMd,onLinkCardHtmlClick,openLinkCardFeishu,onTaskQueueFilterQueryInput,onTaskQueueAiSearchToggle,taskQueueAiSearchStatusVisible,taskQueueAiSearchStatusClass,toggleTaskQueueAuthorPick,toggleTaskQueueReadFilter,resetTaskQueueFilter,taskQueueFilterActive,sortTaskQueueFifo,pendingQueueIndex,isFirstPendingTask,isLastPendingTask,logFocusId,logHighlightIdx,jumpToTaskErrorLog,outDirInp,toast,modalOut,modalDupLink,modalTaskOps,openTaskOpsReport,closeTaskOpsReport,resubmitDupLink,startProcInternal,modalArtifact,logs,logRowClass,startProc,clrV,persistLinkPipelinePrefs,ldLinkPipelinePrefs,openOut,copyOutPath,saveServerOutPath,configureOutputFolder,onOutDirNative,shortLink,clampTaskText,histStatusLabel,taskShowProgress,taskCardLinkUrl,taskCardStatusText,taskCardStatusColor,histPipelineSteps,histFailedStageLabel,histResumeHint,copyHistLink,detectPlatform,taskContentKind,taskRouteLabel,taskRouteTagClass,taskCardLinkTitle,taskCardHeadTitle,taskCardPureTitle,taskCardSubTitle,taskCardMetricsLine,taskFeishuHint,taskCardDocSubTitle,taskCoverUrl,onTaskCoverError,histTaskTitle,histTaskSubTitle,histStatusStyle,taskHasMd,taskHasHtml,taskHtmlReady,taskHtmlPending,taskHtmlClickTitle,histHasMd,histHasHtml,openTaskMd,openTaskHtml,openTaskHtmlExplorer,openTaskArtifactsLocation,openArtifactModalExplorer,openHistMd,openHistHtml,openHistHtmlExplorer,openLocalOutput,copyArtifactItem,selectQueueTask,onLogFocusChange,moveQueueTask,cancelQueueTask,cleanupQueueTasks,taskQueueFmtTime,clampImportance,taskImportancePct,taskImportanceColor,taskImportanceBg,queueCardBorderStyle,updateQueueImportance,saveQueueTaskMeta,queueTaskHasNote,isQueueTaskNoteOpen,toggleQueueTaskNote,closeQueueTaskNoteEdit,getQueueNoteDraft,setQueueNoteDraft,queueTaskNoteBtnClass,queueTaskNoteBtnTitle,taskCardExtractedKeywordsLine,taskCardMetaRows,linkMetaSchema,linkMetaCardDisplayEnabled,LINK_META_FIELDS_EXAMPLE,saveLinkMetaSettings,resetLinkMetaFieldsExample,refreshLinkMetaFieldsEdit,linkApplyKbMetaSchema,taskShowReadBadge,taskIsUnread,taskReadCount,taskReadLabel,markQueueTaskRead,onQueueReadBadgeClick,onQueueReadBadgeContext,openQueueReadHistory,closeQueueReadHistory,formatReadHistTime,queueReadHistModal,taskCardStatusInline,taskCardStatusExtra,cancelQueueTaskNoteEdit,deleteQueueTask,queueBatchMode,toggleQueueBatchMode,exitQueueBatchMode,onQueueCardClick,isQueueBatchSelected,toggleQueueBatchSel,queueBatchSelCount,queueBatchSelAllChecked,toggleQueueBatchSelAll,batchDeleteQueueTasks,onTaskHtmlClick,
  favDetail,favDetailCard,openFavDetail,closeFavDetail,toggleFavDetailFullscreen,openFavTaskFromDetail,favDetailDisplay,favFormatCount,favCardPublishedText,
  showHist,openHistPanel,ht,hs,ldHist,filteredHistTasks,restartTask,stopTask,moveTask,deleteTask,clearCompleted,regenerateHtml,
  histTaskId,histShowReadBadge,histTaskIsUnread,histTaskReadLabel,markHistTaskRead,onHistReadBadgeClick,onHistReadBadgeContext,
  histLogPanel,openHistLogs,closeHistLogPanel,histLogSourceLabel,
  opsSpanModal,openOpsSpanModal,closeOpsSpanModal,opOpenSpanModalFromTask,opPickSpanStepInModal,opFormatLogLine,opLoadSpanException,opTriggerSpanOpsAnalysis,
  ldOpSpans,ldOpSpanExceptions,ldOpSpansAll,opLoadSpanTask,opSpanStatusClass,opSpanMaxMs,opSpanBarPct,opFmtJson,opPickSpanStep,opPickApiEvent,opSpanTypeLabel,
  o,ldNodes,skills,skillsSorted,skillsFiltered,skillCmdDraft,saveSkillCommand,saveAllSkillCommands,importProjectSkillsBatch,retagAllSkillsBoard,sk,skillImport,openSkillImport,closeSkillImport,orchSkillAttachActive,skillAttachKindLabel,selectSkillAttachment,orchToolSearch,orchBoardTab,orchBoardJoinRange,orchBoardSort,orchBoardByCategory,orchBoardFilteredItems,orchBoardTotalCount,orchBoardOpenItem,orchBoardItemTitle,builtinTools,mcpDiscovered,mcpDiscoveredFiltered,mcpEnabledListFiltered,mcpByServer,mcpVendors,mcpMarketOpen,mcpEnabledList,mcpServerKeys,ldMcpVendors,insertMcpVendorMerge,addMcpFromMarket,openMcpServerConfig,saveMcpServerConfigFromRail,removeMcpServer,orchStage,orchRail,orchFlowDisplay,orchFlowPanStart,orchFlowPanMove,orchFlowPanEnd,resetOrchRailView,fitOrchFlowToViewport,Math,closeOrchRail,openOrchFullscreen,dockOrchFromFullscreen,selectOrchBuiltin,selectOrchMcpServer,selectOrchMcpTool,selectOrchSkill,openSkillDiff,onSkillVersionClick,clearSkillDiff,refreshSkillFlow,refreshSkillIntelligence,pollSkillIntelligence,loadSkillUsageArchives,onOrchRailTabChange,onOrchDetailTabChange,orchRailTabIsFlow,diagramStyleFields,ldDiagramStyles,iagDiagramStyleKeys,iagDiagramStyleLabel,iagDiagramStyleHint,resetIagDiagramStyle,mcpConfigEditText,mcpFeishuForm,mcpDiscKey,orchToggle,isOrchOn,setOrchOn,orchTocActive,scrollOrchTo,orchDetailToc,orchDetailTocActive,scrollOrchDetailTo,orchDiffStats,orchDiffDisplay,skillAliasCn,mcpAliasCn,skillDescParts,skillCardSummary,skillCardTags,mcpSyncMsg,mcpJsonText,mcpPlaceholder,ldBuiltinTools,ldMcpCfg,saveMcpCfg,mcpSyncPull,ldSkills,importSkillForm,onSkillFile,onSkillFolder,delSkill,orchSubTabs,switchOrchSubTab,
  chatSbCollapsed,toggleChatSb,
  c,cs,filteredCs,csBatchMode,chatSessionTitle,chatTopKpi,chatMainTaskHistory,taskHistDisplayCount,refreshChatSessionTaskHistory,chatConnectVisible,chatConnectClass,chatConnectLabel,taskRegistryKindLabel:taskRegistryKindLabel,taskRegistryKindClass:taskRegistryKindClass,openRegistryTask,openTaskDetailFromChat,openTaskHistModal,closeTaskHistModal,closeTaskHistModalBack,taskHistModalDetail,taskHistModalLoading,loadTaskHistDetail,taskHistDetailOf,taskHistDetailCounts,taskHistSubPlanGroups,taskFieldLabel,taskFieldDisplayValue,taskStepTypeLabel,taskMetaLabel,taskHistDetailKey,preloadTaskHistDetails,chatCurrentSubtask,chatGroupedSubPlans,chatActiveCurTask,chatSubPlanGroupCount,groupExecPlans,filterExecThinking,hasVisibleExecChain,execThinkingForMsg,showOrchestrationThink,formatOrchThinkDisplay,stripReactDisplayMarkers,hasStepIo,isRagDecisionStep,ragSliceParentName,ragSliceModal,openRagSliceDetail,closeRagSliceDetail,openRagParentInKb,sortedRagSlicesForMsg,ragCapsuleScoreLabel,toggleRagCapsule,activeRagSliceForMsg,jumpToRagCapsule,onAnswerCitationClick,extractRagSlicesFromStep,formatOrchStepInputDisplay,formatOrchStepOutputDisplay,ORCH_IO_PHASES,stepIsSkipped,pillStatusClass,execPillClass,execSubPlanTitle,formatToolPillPrimary,formatToolPillResult,formatToolPillAction,formatInvokeModeLabel,stepInvokeMode,stepInvokePurpose,stepUi,stepIsToolCall,stepIsReactThink,stepIsOrchExecPill,stepDisplayName,mainTaskCardLabel,execCardLabel,execCardQueryLine,activeTaskHistoryEntry,jumpToTaskResult,jumpToCurTaskResult,jumpToMsgIndex,resultJudgmentLabel,resultJudgmentClass,msgErrLabel,msgErrClass,parentStatusLabel,parentStatusClass,parentStatusTransitions,formatStepBrief,formatDuration,stepSuccessLabel,stepStatusIcoClass,stepConfidencePct,formatStepInputDisplay,formatStepOutputDisplay,isDocStepOutput,chatCtxPct,chatCtxPctLabel,switchChatPanel,orchPipelineNodes:ORCH_PIPELINE_NODE_DEFS,chatApplyTaskStatus,onTaskHistPick,toggleTaskHistMenu,toggleTaskStatusMenu,setCurrentMainTaskFromHistory,loadTaskRegistry,scheduleTaskRegistryReload,setTaskHistKindFilter,setTaskHistSort,syncTaskToMysql,chatCloseTask,chatTogglePause,hitlKindTitle,chatHitlConfirm,chatHitlPause,chatHitlReintent,chatHitlToolOption,chatPrimaryActionLabel,chatPrimaryActionDisabled,chatModels,chatAgents,customAgents,goAgentPersonalization,persistChatPrefs,newChatSess,delCs,renameCs,closeCs,exportCsMd,exportMsgMd,loadChatSession,toggleCsMenu,onCsItemClick,toggleCsBatchMode,exitCsBatchMode,isCsBatchSelected,toggleCsBatchSel,csBatchSelCount,csBatchSelAllChecked,toggleCsBatchSelAll,batchDeleteCs,upImg,upFile,autoResize,onChatInput,chatKeydown,chatSend,toggleVoice,chatExpandOpen,renderMsg,renderRagSliceContent,renderWebSearchPanel,copyMsg,copyQueryToInput,loadPlatformHealth,goHealthSettings,platformHealthSummaryText,platformHealthSummaryClass,regenerateAt,collectMsg,readMsg,ragCitationSlicesForMsg,ragCitationAnnotationsForMsg,hasRagCitationCards,onRagCiteParentToggle,ragParentBodyText,ragParentBodyLoading,answerBodyForMsg,rh,initReaderPage,readerPickLocalFile,onReaderLocalFile,readerOpenRecent,readerOpenOutputFile,fmtRecentTime,refreshReaderRecent,slashOpen,slashItems,slashIdx,slashTotal,pickSlash,chatScrollAwayFromBottom,chatScrollBottomClick,openTaskSubPlanDetail,closeTaskSubPlanDetail,taskSubPlanStatusLabel,taskSubPlanStatusClass,taskSubPlanInvokeTags,taskSubPlanInvokeTagLabel,taskSubPlanDetailStepName,taskSubPlanDetailPayload,
  apz,ldApzCatalog,selectApzTemplate,ldApzCurrent,ldApzHist,loadApzRevision,saveApzTemplate,newApzCustom,useApzInChat,deactivateApzCustom,
  kb,kbImportMeta,kbImportBtnLabel,kbMilvusStatusText,kbMilvusStatusColor,ldKbS,ldKbF,kbRefreshAll,ldKbMetaOpts,ldKbConn,kbSetConnectionParams,kbProbeConnection,kbRetryConnection,kbConnFmtTs,kbResetConnection,kbToggleConnDetail,kbSyncChunkCounts,kbRestoreCatalog,kbRm,ldKbLibs,onKbLibChange,promptCreateKbLib,deleteKbLib,saveKbLibCfg,kbLoadRecallVocab,kbImportInterviewFolder,kbFolderInp,kbPickLocalFolder,onKbLocalFolderPick,openKbBrowse,kbBrowse,kbBrowseEnter,kbBrowseUp,kbImportSelectedFiles,kbImportFolderHere,kbConfirmImportWithMeta,kbOpenImportMeta,openKbFileDetail,closeKbFileDetail,kbAutoFillMeta,kbSaveFileMeta,kbRowPv,
   rss,rssFmtTime,rssFeedTitle,rssArticleTitle,rssSyncStepClass,rssSyncActionLabel,ldRssAll,rssSelectFeed,rssSelectItem,rssOpenDoc,rssEnqueueDoc,rssAddFeed,rssDeleteFeed,rssSyncOne,rssSyncAll,rssToggleFilter,rssToggleRead,rssToggleStar,rssExportOpml,rssImportOpmlFile,rssTriggerOpmlImport,
  d,docProc,mm,mmBrowse,mmFileInp,mmPickLocal,openMmBrowse,mmBrowseEnter,mmBrowseUp,mmLoadBrowse,mmAddBrowsePicks,mmOnLocalPick,mmOnDrop,mmRmQueueSel,mmClearQueue,mmClearDocLog,persistMmPrefs,ldMmPrefs,mmLoadPreview,mmOnPreviewClick,mmCloseLightbox,mmOpenPreviewMd,mmOcrProviderLabel,mmCopyMermaid,mmDownloadMermaid,
  ca,caQ,caSel,caPickRow,caSv,caEx,
  st,agtKeys,GW_PROVIDERS,gwProvMeta,gwFormSections,gwFieldPlaceholder,gwProvCardStyle,pickGwProvider,gwTestPayload,providerBadge,gwNodeAccent,resetGwForm,ldGw,ldAr,ldNf,testConn,ndUpSert,ndPoolSv,ndUp,ndDn,ndDel,rtSv,ldWf,svWf,ldMd,svMd,svFs,ldFsCfg,aicf,ldAiCfg,svAiCfg,thcf,ldThCfg,svThCfg,ldTpl,svTpl,ldHtmlCfg,svHtmlCfg,ldImPlatforms,openImPlatform,closeImDetail,imPlatformIcon,imPlatformBadge,imDetailTitle,ldImFeishu,ldImFeishuMsgs,imFsSave,imFsTime,imFsWebhookUrl,imFsCopyWebhook,ldImWechat,imWxSave,imWxStartQr,imWxRefreshStatus,imWxDisconnect,imWxCopyInbound,imWxInboundUrl,
  INTERNAL_IAG_TABS,IAG_KEY_ORDER,IAG_SUMMARY_PREVIEW_VARS,IAG_SUMMARY_BODY_TOKENS,IAG_COMMENTS_SECTION_VARS,iag,iagRawMode,ldIag,saveIag,iagLabel,iagHelpText,iagIsLongText,iagIsGuidedTab,iagFieldKeys,iagGenericFieldKeys,iagTplPreviewMd,iagTplPreviewFn,iagCommentsSectionPreview,iagMetaExtractFieldsJson,iagApplyKbMetaSchema,iagResetMetaExtractFields,insertIagToken,iagToggleRawMode,iagDiagramStyleKeys,resetIagDiagramStyle,
  op,ldOp,ldOpAg,ldOpHealth,ldOpFeedback,ldOpSchedJobs,ldOpExceptions,ldOpExMatch,opShowExDetail,opCopyProposal,opLoadReport,opAnalyzeLogs,
  fleet,FLEET_STATUS_COLUMNS,FLEET_ROLES,fleetStatusLabel,fleetStatusClass,fleetRoleLabel,fleetSessionsByStatus,fleetHarnessLabel,
  ldFleetAll,ldFleetSessionDetail,selectFleetSession,fleetRefreshLogs,fleetAddProject,fleetDeleteProject,
  fleetCreateSession,fleetCreatePlan,fleetDispatchSession,fleetCancelSession,fleetReviewSession,
  intentLabels,msgFeedback,feedbackIntentLabel,setMsgRating,toggleIntentLike,submitIntentFeedback,
  loadIntentAlternatives,pickIntentAlternative,applyCustomIntentCorrection,
  submitFeedbackComment,dismissFeedbackComment,msgFeedbackStore
};
}});
_sbaApp.config.errorHandler=function(err,inst,info){
  if(isExternalInjectedError(err))return;
  revealRuntimeFault('界面渲染异常',(err&&err.message||err)+' · '+String(info||''));
};
window.addEventListener('error',function(ev){
  if(!ev||!ev.message)return;
  if(isExternalInjectedError(ev))return;
  revealRuntimeFault('脚本运行错误',ev.message);
});
window.addEventListener('unhandledrejection',function(ev){
  const r=ev&&ev.reason;
  if(r&&r.name==='AbortError')return;
  if(isExternalInjectedError(r))return;
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
  openOutputMdByPath(name,'split',{from:page.value});
},true);
try{
  _sbaApp.mount('#app');
  window.__SBA_VUE_MOUNTED__=true;
  hideBootLoading();
}catch(e){
  console.error('Vue mount failed',e);
  revealBootError('<div style="padding:32px 20px;max-width:520px;margin:24px auto;font-family:system-ui,sans-serif;line-height:1.55;color:#1e293b;background:#f8fafc;border-radius:12px;border:1px solid #e2e8f0"><h2 style="font-size:18px;margin:0 0 12px">界面启动失败</h2><p style="margin:0;font-size:14px;color:#475569">Vue 挂载异常：'+String(e&&e.message||e)+'</p><p style="margin:12px 0 0;font-size:13px;color:#64748b">请打开浏览器控制台查看详情，或 <a href="/login.html">重新登录</a> 后刷新。</p></div>');
}
})();
