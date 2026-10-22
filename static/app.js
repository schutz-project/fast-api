'use strict';
const $ = id => document.getElementById(id);
let csrf = null, currentUser = null, searchGeneration = 0, authGeneration = 0;
let retryAction = null, controller = null, view = 'home', logoutBusy = false, loginBusy = false;
const dialog = $('portal-dialog');
let dismissDialog = null;
const displayName = user => user.username === 'test_user' ? '테스트 사용자' : user.username === 'admin01' ? '관리자' : user.username;

function status(text, error = false, retry = null) {
  $('status').textContent = text;
  $('feedback').hidden = !text;
  $('feedback').classList.toggle('error', error);
  retryAction = retry;
  $('retry').hidden = !retry;
}
function invalidateSearch() {
  ++searchGeneration;
  window.portalContent?.cancel();
  controller?.abort();
  controller = null;
}
function clearFieldErrors() {
  for (const id of ['username', 'password', 'name']) $(id).removeAttribute('aria-invalid');
  $('name-error').hidden = true;
}
function showUser(user) {
  const changed = user?.user_id !== currentUser?.user_id;
  currentUser = user;
  $('login-panel').hidden = !!user;
  $('portal-panel').hidden = !user;
  (user ? $('portal-feedback') : $('login-feedback')).append($('feedback'));
  clearFieldErrors();
  if (user) {
    $('user-label').textContent = displayName(user);
    $('sidebar-user-name').textContent = displayName(user);
    $('sidebar-user-id').textContent = user.username;
    $('password').value = '';
    if (changed) { window.portalContent?.clear(); invalidateSearch(); $('name').value = ''; resetResults(); navigate('home'); }
  } else {
    invalidateSearch();
    $('results').replaceChildren();
    window.portalContent?.clear();
    $('name').value = '';
    for (const id of ['user-label', 'sidebar-user-name', 'sidebar-user-id']) $(id).textContent = '';
    $('password').value = '';
    view = 'home';
  }
}
function navigate(next) {
  if (!currentUser || logoutBusy) return;
  if (view !== next) { invalidateSearch(); resetResults(); status(''); }
  view = next;
  $('home-view').hidden = next !== 'home';
  $('search-view').hidden = next !== 'search';
  $('home-nav').classList.toggle('active', next === 'home');
  $('search-nav').classList.toggle('active', next === 'search');
  $('home-nav').setAttribute('aria-current', next === 'home' ? 'page' : 'false');
  $('search-nav').setAttribute('aria-current', next === 'search' ? 'page' : 'false');
  for (const v of ['notices','board']) { $(v+'-view').hidden = next !== v; $(v+'-nav').classList.toggle('active', next===v); }
  $('breadcrumb').textContent = ({home:'메인 화면',search:'직원 검색',notices:'공지사항',board:'게시판'})[next];
  window.portalContent?.navigate(next);
  if (next === 'search') $('name').focus();
}
async function api(path, options = {}) {
  const timeout = new AbortController();
  const abort = () => timeout.abort();
  options.signal?.addEventListener('abort', abort, {once:true});
  if (options.signal?.aborted) timeout.abort();
  const timer = setTimeout(() => timeout.abort(), 8000);
  try {
    const response = await fetch(path, {...options, signal:timeout.signal, credentials:'same-origin', cache:'no-store'});
    const isJSON = (response.headers.get('content-type') || '').includes('application/json');
    let body = {};
    if (isJSON) {
      try { body = await response.json(); }
      catch { throw new Error('응답을 확인할 수 없습니다. 다시 시도해 주세요.'); }
    }
    if (!response.ok) {
      const error = new Error(body.message || '서비스에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.');
      Object.assign(error, {status:response.status, code:body.error_code, requestId:body.request_id || response.headers.get('x-request-id')});
      throw error;
    }
    if (!isJSON) throw new Error('응답을 확인할 수 없습니다. 다시 시도해 주세요.');
    return body;
  } catch (error) {
    if (error.name === 'AbortError' && !options.signal?.aborted) throw new Error('서버 응답이 지연되고 있습니다. 다시 시도해 주세요.');
    throw error;
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener('abort', abort);
  }
}
function describe(error, action = '') {
  let message = error.message;
  if (error.status === 503) message = action === 'login' ? '일시적으로 로그인할 수 없습니다. 잠시 후 다시 시도해 주세요.' : action === 'logout' ? '일시적으로 로그아웃할 수 없습니다. 잠시 후 다시 시도해 주세요.' : '일시적으로 검색할 수 없습니다. 잠시 후 다시 시도해 주세요.';
  else if (error.status === 502 || error.status === 504) message = '서비스에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.';
  else if (error.status === 500) message = '처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.';
  else if (error instanceof TypeError) message = '서버에 연결할 수 없습니다. 네트워크 상태를 확인하고 다시 시도해 주세요.';
  return message + (error.status >= 500 && error.requestId ? '\n요청 ID: ' + error.requestId : '');
}
async function context() {
  const generation = authGeneration;
  const data = await api('/api/auth/context');
  if (generation !== authGeneration) return null;
  csrf = data.csrf_token;
  showUser(data.user);
  $('login-button').disabled = !csrf || loginBusy;
  return data;
}
async function startup() {
  $('login-button').disabled = true;
  try { const data = await context(); if (data) status(''); }
  catch (error) { csrf = null; showUser(null); $('login-button').disabled = true; status(describe(error), true, startup); }
}
function closeDialog() {
  dismissDialog = null;
  if (dialog.open) dialog.close();
}
function modal(kind, title, message, actions, onDismiss = null) {
  closeDialog();
  $('dialog-icon').className = 'dialog-icon ' + kind;
  $('dialog-icon').textContent = kind === 'success' ? '✓' : kind === 'info' ? 'i' : '!';
  if (kind === 'loading') {
    const spinner = document.createElement('span'); spinner.className = 'spinner';
    $('dialog-icon').replaceChildren(spinner);
  }
  $('dialog-title').textContent = title;
  $('dialog-message').textContent = message;
  $('dialog-actions').replaceChildren();
  for (const action of actions) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = action.primary ? 'primary' : 'secondary';
    button.textContent = action.label; button.onclick = action.run;
    $('dialog-actions').append(button);
  }
  dismissDialog = onDismiss;
  dialog.showModal();
  const firstButton = $('dialog-actions').querySelector('button');
  if (firstButton) firstButton.focus();
}
dialog.addEventListener('cancel', event => {
  event.preventDefault();
  if (dismissDialog) dismissDialog();
});
async function expireSession() {
  ++authGeneration;
  csrf = null;
  showUser(null);
  $('login-button').disabled = true;
  status('로그인이 만료되었습니다. 다시 로그인해 주세요.', true);
  const move = () => { closeDialog(); $('username').focus(); };
  modal('error', '로그인이 만료되었습니다.', '보안을 위해 로그인 시간이 만료되어\n다시 로그인이 필요합니다.', [{label:'로그인 화면으로 이동', primary:true, run:move}], move);
  // Never repeat the original protected request after re-authentication.
  try { await context(); }
  catch (error) { csrf = null; status(describe(error), true, startup); }
}
function setLoginBusy(busy) {
  loginBusy = busy;
  $('login-form').hidden = busy;
  $('login-loading').hidden = !busy;
  $('account-help').hidden = busy;
  $('login-button').disabled = busy || !csrf;
}
function rememberUsername(username) {
  try { if ($('remember-id').checked) localStorage.setItem('schutz.remembered-id', username); else localStorage.removeItem('schutz.remembered-id'); }
  catch { /* Storage can be disabled; authentication does not depend on it. */ }
}
try {
  const saved = localStorage.getItem('schutz.remembered-id');
  if (saved && [...saved].length <= 50) { $('username').value = saved; $('remember-id').checked = true; }
} catch { /* Optional local preference. */ }
$('remember-id').onchange = () => { if (!$('remember-id').checked) rememberUsername(''); };
$('retry').onclick = () => retryAction?.();
$('login-form').onsubmit = async event => {
  event.preventDefault();
  if ($('login-button').disabled || loginBusy) return;
  clearFieldErrors();
  const username = $('username').value.trim(), password = $('password').value;
  if (!username || [...username].length > 50) {
    status(!username ? '아이디를 입력해 주세요.' : '아이디를 1~50자로 입력해 주세요.', true);
    $('username').setAttribute('aria-invalid', 'true'); $('username').focus(); return;
  }
  if (!password || [...password].length > 128) {
    status(!password ? '비밀번호를 입력해 주세요.' : '비밀번호를 1~128자로 입력해 주세요.', true);
    $('password').setAttribute('aria-invalid', 'true'); $('password').focus(); return;
  }
  const generation = ++authGeneration;
  setLoginBusy(true); status('');
  try {
    const data = await api('/api/auth/login', {method:'POST', headers:{'Content-Type':'application/json', 'X-CSRF-Token':csrf}, body:JSON.stringify({username, password})});
    if (generation !== authGeneration) return;
    csrf = data.csrf_token; rememberUsername(username); showUser(data.user); resetResults(); status('');
  } catch (error) {
    if (generation !== authGeneration) return;
    if (error.status === 403) {
      try { await context(); status('로그인 정보를 새로 확인했습니다. 다시 입력해 주세요.', true); }
      catch (failure) { csrf = null; status(describe(failure), true, startup); }
    } else if (!error.status) {
      // A lost POST response can still have committed. Read state instead of retrying.
      try {
        const data = await context();
        if (data?.user) { rememberUsername(username); resetResults(); status('로그인 상태를 확인했습니다.'); }
        else status('로그인 상태를 확인했습니다. 다시 로그인해 주세요.', true);
      } catch (failure) { csrf = null; status(describe(failure), true, startup); }
    } else {
      status(error.code === 'INVALID_CREDENTIALS' ? '아이디 또는 비밀번호를 확인해 주세요.' : describe(error, 'login'), true);
    }
  } finally { $('password').value = ''; setLoginBusy(false); }
};
function emptyState(title, subtitle = '', loading = false) {
  const box = document.createElement('div'); box.className = 'empty-state';
  const icon = document.createElement('span'); icon.className = loading ? 'spinner' : 'search-icon'; icon.setAttribute('aria-hidden', 'true');
  const text = document.createElement('p'); text.textContent = title; box.append(icon, text);
  if (subtitle) { const small = document.createElement('small'); small.textContent = subtitle; box.append(small); }
  $('results').replaceChildren(box);
  $('results').setAttribute('aria-busy', String(loading));
}
function resetResults() { emptyState('검색할 직원 이름을 입력해 주세요.', '이름을 입력하면 직원 정보를 확인할 수 있습니다.'); }
function renderResults(data) {
  $('results').setAttribute('aria-busy', 'false');
  if (!data.items.length) { emptyState('일치하는 직원이 없습니다.', '다른 이름으로 다시 검색해 주세요.'); return; }
  const count = document.createElement('p'); count.className = 'result-count';
  const number = document.createElement('strong'); number.textContent = String(data.count);
  count.append('검색 결과 ', number, '건'); $('results').replaceChildren(count);
  const wrap = document.createElement('div'); wrap.className = 'table-wrap';
  const table = document.createElement('table'); const head = table.createTHead().insertRow();
  const caption = table.createCaption(); caption.textContent = '직원 검색 결과'; caption.hidden = true;
  for (const label of ['이름', '부서', '직책', '이메일', '내선']) { const th = document.createElement('th'); th.scope = 'col'; th.textContent = label; head.append(th); }
  const body = table.createTBody();
  for (const item of data.items) {
    const row = body.insertRow(); row.dataset.employeeId = String(item.employee_id);
    for (const key of ['name', 'department', 'position', 'email', 'extension']) row.insertCell().textContent = item[key] ?? '-';
  }
  wrap.append(table); $('results').append(wrap);
}
async function search() {
  if (!currentUser || logoutBusy || view !== 'search') return;
  clearFieldErrors();
  const name = $('name').value.trim().normalize('NFC');
  invalidateSearch();
  if (!name || [...name].length > 50) {
    resetResults(); status('');
    $('name').setAttribute('aria-invalid', 'true');
    $('name-error').textContent = !name ? '직원 이름을 입력해 주세요.' : '직원 이름을 1~50자로 입력해 주세요.';
    $('name-error').hidden = false; $('name').focus(); return;
  }
  const generation = searchGeneration;
  controller = new AbortController();
  emptyState('검색 중입니다.', '잠시만 기다려 주세요.', true); status('');
  try {
    const data = await api('/api/employees?name=' + encodeURIComponent(name), {signal:controller.signal});
    if (generation !== searchGeneration || !currentUser) return;
    renderResults(data); status('');
  } catch (error) {
    if (generation !== searchGeneration || error.name === 'AbortError') return;
    $('results').replaceChildren(); $('results').setAttribute('aria-busy', 'false');
    if (error.status === 401) await expireSession();
    else if (error.status === 400) {
      $('name').setAttribute('aria-invalid', 'true');
      $('name-error').textContent = '직원 이름을 확인해 주세요.'; $('name-error').hidden = false;
    } else status(describe(error, 'search'), true, search);
  }
}
$('search-form').onsubmit = event => { event.preventDefault(); search(); };
$('home-nav').onclick = () => navigate('home');
$('search-nav').onclick = $('open-search').onclick = () => navigate('search');
document.querySelector('.sidebar .wordmark').onclick = event => { event.preventDefault(); navigate('home'); };

function finishLogout(message = '안전하게 로그아웃되었습니다.', preserveContext = false) {
  if (!preserveContext) { csrf = null; $('login-button').disabled = true; }
  showUser(null); status('');
  const move = () => { closeDialog(); $('username').focus(); };
  modal('success', message, '다시 이용할 때 로그인해 주세요.', [{label:'로그인 화면으로 이동', primary:true, run:move}], move);
}
async function logout() {
  if (logoutBusy || !currentUser) return;
  logoutBusy = true; ++authGeneration;
  invalidateSearch(); $('results').replaceChildren();
  $('logout-button').disabled = true; status('');
  modal('loading', '로그아웃 중입니다.', '잠시만 기다려 주세요.', []);
  try {
    await api('/api/auth/logout', {method:'POST', headers:{'X-CSRF-Token':csrf}});
    finishLogout();
    try { await context(); } catch (failure) { csrf = null; status(describe(failure), true, startup); }
  } catch (error) {
    closeDialog();
    if (error.status === 401) await expireSession();
    else if (error.status === 403) {
      try { const data = await context(); if (data?.user) { resetResults(); status('요청 정보를 갱신했습니다. 다시 로그아웃해 주세요.', true); } else finishLogout('로그아웃 상태를 확인했습니다.', true); }
      catch (failure) { status(describe(failure), true); }
    } else if (!error.status) {
      try {
        const data = await context();
        if (data?.user) { resetResults(); status('로그인 상태입니다. 다시 로그아웃해 주세요.', true); }
        else finishLogout('로그아웃 상태를 확인했습니다.', true);
      } catch (failure) { status('로그아웃 여부를 확인할 수 없습니다.\n' + describe(failure), true, checkLogoutState); }
    } else { resetResults(); status(describe(error, 'logout'), true); }
  } finally { logoutBusy = false; $('logout-button').disabled = false; }
}
async function checkLogoutState() {
  try { const data = await context(); if (data?.user) { resetResults(); status('로그인 상태입니다. 다시 로그아웃해 주세요.', true); } else { finishLogout('로그아웃 상태를 확인했습니다.', true); } }
  catch (error) { status('로그아웃 여부를 확인할 수 없습니다.\n' + describe(error), true, checkLogoutState); }
}
$('logout-button').onclick = () => {
  if (logoutBusy) return;
  modal('error', '로그아웃 하시겠습니까?', '로그아웃하면 현재 세션이 종료됩니다.', [
    {label:'취소', run:closeDialog}, {label:'로그아웃', primary:true, run:logout}
  ], closeDialog);
};
$('account-help').onclick = () => modal('info', '가상 계정으로 이용해 주세요.', '이 포털은 실습 환경입니다.\n발급된 계정과 비밀번호는 프로젝트 담당자에게 확인해 주세요.', [{label:'확인', primary:true, run:closeDialog}], closeDialog);
$('current-date').textContent = new Intl.DateTimeFormat('ko-KR', {year:'numeric', month:'2-digit', day:'2-digit', weekday:'short', timeZone:'Asia/Seoul'}).format(new Date());
window.addEventListener('pageshow', event => { if (event.persisted) { ++authGeneration; closeDialog(); csrf = null; showUser(null); startup(); } });
startup();

const contentState={generation:0,page:1,total:0,edit:null,busy:false};
const contentNode=(tag,text,className='')=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;e.className=className;return e;};
function clearPostForm(){contentState.edit=null;$('post-form').reset();$('post-form-title').textContent='새 글 작성';$('post-cancel').hidden=true;}
window.portalContent={
  cancel(){++contentState.generation;},
  clear(){++contentState.generation;clearPostForm();$('post-list').replaceChildren();$('notice-list').replaceChildren();},
  navigate(next){if(next==='notices'||next==='board'){clearPostForm();loadContent(next);}}
};
function postButton(text,run){const b=contentNode('button',text,'secondary');b.type='button';b.onclick=run;return b;}
async function loadContent(next=view){
  if(!currentUser||!['notices','board'].includes(next))return;
  const generation=++contentState.generation;
  const root=$(next==='notices'?'notice-list':'post-list');root.replaceChildren(contentNode('p','불러오는 중입니다.'));
  try{
    const data=await api(next==='notices'?'/api/notices':'/api/posts?page='+contentState.page);
    if(generation!==contentState.generation||view!==next||!currentUser)return;
    root.replaceChildren();
    if(!data.items.length)root.append(contentNode('p',next==='notices'?'등록된 공지가 없습니다.':'등록된 게시글이 없습니다. 첫 글을 작성해 보세요.'));
    for(const row of data.items){
      const article=contentNode('article',undefined,'content-item');
      article.append(contentNode('h2',row.title),contentNode('small',(row.author?row.author+' · ':'')+new Date(row.created_at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'}),'content-meta'),contentNode('p',row.content));
      if(next==='board'){
        const actions=contentNode('div',undefined,'post-actions');
        if(row.author_id===currentUser.user_id)actions.append(postButton('수정',()=>{contentState.edit=row;$('post-title').value=row.title;$('post-content').value=row.content;$('post-form-title').textContent='게시글 수정';$('post-cancel').hidden=false;$('post-title').focus();}));
        if(row.author_id===currentUser.user_id||currentUser.role==='admin')actions.append(postButton('삭제',()=>modal('error','게시글을 삭제할까요?','삭제하면 목록에서 제거됩니다.',[{label:'취소',run:closeDialog},{label:'삭제',primary:true,run:()=>{closeDialog();savePost('DELETE',row);}}],closeDialog)));
        article.append(actions);
      }
      root.append(article);
    }
    if(next==='board'){contentState.total=data.total;$('post-page').textContent=contentState.page+' / '+Math.max(1,Math.ceil(data.total/20));$('post-prev').disabled=contentState.page===1;$('post-next').disabled=contentState.page*20>=data.total;}
  }catch(error){if(generation!==contentState.generation)return;root.replaceChildren();if(error.status===401)await expireSession();else status(describe(error),true,()=>loadContent(next));}
}
async function savePost(method,row=null){
  if(contentState.busy||!currentUser||view!=='board')return;
  contentState.busy=true;$('post-submit').disabled=true;
  const generation=contentState.generation;
  let body=method==='DELETE'?{version:row.version}:{title:$('post-title').value.trim(),content:$('post-content').value.trim()};
  const edit=row||contentState.edit;if(method==='PUT')body.version=edit.version;
  try{
    await api('/api/posts'+(method==='POST'?'':'/'+edit.id),{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(body)});
    if(!currentUser||generation!==contentState.generation)return;
    clearPostForm();status(method==='DELETE'?'삭제했습니다.':'저장했습니다.');await loadContent('board');
  }catch(error){
    if(error.status===401)await expireSession();
    else{
      if(error.status===403)await context();
      status(error.status?error.message:'처리 결과를 확인할 수 없습니다. 다시 보내기 전에 목록을 확인해 주세요.',true);
      if(!error.status||error.status===409)await loadContent('board');
    }
  }finally{contentState.busy=false;$('post-submit').disabled=false;}
}
$('post-form').onsubmit=e=>{e.preventDefault();savePost(contentState.edit?'PUT':'POST');};
$('post-cancel').onclick=clearPostForm;
$('notices-nav').onclick=()=>navigate('notices');$('board-nav').onclick=()=>{contentState.page=1;navigate('board');};
$('post-prev').onclick=()=>{contentState.page--;loadContent('board');};$('post-next').onclick=()=>{contentState.page++;loadContent('board');};
