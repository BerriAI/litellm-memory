import { marked } from './vendor/marked.esm.js';
import DOMPurify from './vendor/purify.es.mjs';

const $ = id => document.getElementById(id);
const STORAGE = 'moegent.conversations.v2';
const uid = () => crypto.randomUUID();
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
};
let chats = [], currentId, view = 'chat', searchController, searchTimer, searchVersion = 0;
let nextOffset = null, resultRevision = null, lastSearch = {}, sourceIds = [], sourceVersion = 0, toastTimer;
const running = new Set(), renderedAnswers = new Map(), sourceCache = new Map();
try {
  const saved = JSON.parse(localStorage.getItem(STORAGE) || '[]');
  chats = Array.isArray(saved) ? saved.filter(c => c.id && Array.isArray(c.messages)).slice(0, 30) : [];
  currentId = localStorage.getItem(STORAGE + '.current');
} catch { /* Storage is optional, including in private browsing. */ }
function freshChat() { return {id:uid(), title:'New chat', created:Date.now(), messages:[], draft:''}; }
if (!chats.some(c => c.id === currentId)) { const chat = freshChat(); chats.unshift(chat); currentId = chat.id; }
const current = () => chats.find(c => c.id === currentId);
const pending = () => current()?.messages.findLast(m => m.role === 'assistant' && m.state === 'pending');
function save() {
  try { localStorage.setItem(STORAGE, JSON.stringify(chats.slice(0, 30))); localStorage.setItem(STORAGE + '.current', currentId); }
  catch { /* The live session continues if the browser cannot save history. */ }
}
function toast(text, action, label = 'Undo') {
  clearTimeout(toastTimer); $('toast').replaceChildren(document.createTextNode(text));
  if (action) { const b = el('button', '', label); b.onclick = () => { action(); $('toast').hidden = true; }; $('toast').append(b); }
  $('toast').hidden = false; toastTimer = setTimeout(() => { $('toast').hidden = true; }, 6000);
}
function announce(text) { $('announcement').textContent = text; }
class ApiError extends Error { constructor(message, status) { super(message); this.status = status; } }
async function api(path, options = {}) {
  const timeout = AbortSignal.timeout(options.timeout || 30000);
  let response;
  try {
    response = await fetch(path, {...options, headers:{'Content-Type':'application/json', ...options.headers},
      signal:options.signal ? AbortSignal.any([options.signal, timeout]) : timeout});
  } catch (error) {
    if (options.signal?.aborted) throw error;
    throw new ApiError(navigator.onLine ? 'The connection timed out. Try again in a moment.' : 'You’re offline. Reconnect and try again.', 0);
  }
  let data;
  try { data = await response.json(); } catch { data = {}; }
  if (!response.ok) {
    const messages = {401:'Sign in with Slack to continue.',403:'Your account doesn’t have access to this request.',
      404:'This request is no longer available. Try asking again.',429:'Moegent is busy. Give it a moment, then retry.',
      502:'Moegent is restarting. Try again in a moment.',503:'The memory service is unavailable. Try again in a moment.',
      504:'This request took too long. Try again with a more specific question.'};
    throw new ApiError(typeof data.detail === 'string' ? data.detail : messages[response.status] || 'Something went wrong. Please try again.', response.status);
  }
  return data;
}
function markdown(node, text) {
  node.innerHTML = DOMPurify.sanitize(marked.parse(text || '', {breaks:false}), {
    USE_PROFILES:{html:true}, FORBID_TAGS:['img','style','form','input','button','iframe','video','audio'], FORBID_ATTR:['style'],
  });
  for (const a of node.querySelectorAll('a')) {
    let url; try { url = new URL(a.getAttribute('href'), location.origin); } catch { a.removeAttribute('href'); continue; }
    if (!['https:','http:'].includes(url.protocol)) { a.removeAttribute('href'); continue; }
    a.rel = 'noopener noreferrer'; a.target = '_blank';
    if (url.origin === location.origin && /^\/memory\/[\w-]+$/.test(url.pathname)) {
      a.removeAttribute('target');
      a.onclick = e => { e.preventDefault(); const ids = citations(text); openSources(ids.length ? ids : [url.pathname.split('/').pop()], url.pathname.split('/').pop()); };
    }
  }
}
function citations(text) {
  return [...new Set([...text.matchAll(/\/memory\/([a-zA-Z0-9_-]+)/g)].map(m => m[1]))].slice(0, 20);
}
function atBottom() { return window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 180; }
function scrollBottom(force = false) { if (force || atBottom()) requestAnimationFrame(() => window.scrollTo({top:document.documentElement.scrollHeight, behavior:'instant'})); }
function resizeComposer() {
  $('question').style.height = 'auto'; $('question').style.height = Math.min($('question').scrollHeight, 200) + 'px';
  const n = $('question').value.length; $('character-count').hidden = n < 14000;
  $('character-count').textContent = `${n.toLocaleString()} / 16,000`; $('composer-hint').hidden = n >= 14000;
  $('send').disabled = !n || !$('question').value.trim() || Boolean(pending());
}
function controls() {
  const busy = Boolean(pending()); $('stop').hidden = !busy; $('send').hidden = busy;
  $('question').placeholder = busy ? 'Write your next question…' : 'Ask about Moe’s memories…';
  $('new-chat').disabled = false; resizeComposer();
}
function showView(next) {
  view = next; $('chat-view').hidden = next !== 'chat'; $('memories-view').hidden = next !== 'memories';
  for (const name of ['chat','memories']) { $(name + '-tab').classList.toggle('active', name === next); if (name === next) $(name + '-tab').setAttribute('aria-current','page'); else $(name + '-tab').removeAttribute('aria-current'); }
  history.replaceState(null,'',next === 'memories' ? '/#memories' : '/#chat');
  $('jump-bottom').hidden = true;
  if (next === 'memories' && !$('results').children.length) search();
}
function renderChat() {
  const chat = current(); renderedAnswers.clear(); $('messages').replaceChildren();
  $('welcome').hidden = chat.messages.length > 0; $('suggestions').hidden = chat.messages.length > 0;
  $('chat-view').classList.toggle('empty', chat.messages.length === 0);
  for (const m of chat.messages) {
    if (m.role === 'user') { const article = el('article','message user'); article.append(el('div','user-bubble',m.content)); $('messages').append(article); }
    else {
      const article = el('article','message assistant'); const heading = el('div','answer-heading');
      heading.append(el('span','avatar','m'), document.createTextNode('Moegent'));
      const status = el('div','working'); const content = el('div','answer-content'); const actions = el('div','answer-actions');
      article.append(heading,status,content,actions); $('messages').append(article);
      renderedAnswers.set(m.id,{article,status,content,actions,text:null,state:null,phase:null}); updateAnswer(chat,m);
    }
  }
  $('question').value = chat.draft || ''; controls();
}
function updateAnswer(chat, m) {
  if (chat.id !== currentId) return;
  const dom = renderedAnswers.get(m.id); if (!dom) return;
  const stick = atBottom(); const active = m.state === 'pending';
  dom.status.hidden = !active;
  if (active) {
    const seconds = Math.max(0,Math.floor((Date.now() - m.started)/1000));
    dom.status.replaceChildren(el('span','spinner'), el('span','',m.phase || 'Sending your question'),el('span','elapsed',seconds ? `${seconds}s` : ''));
    if (m.phase !== dom.phase) { announce(m.phase || 'Sending your question'); dom.phase = m.phase; }
  }
  const text = active ? m.partial || '' : m.content || '';
  if (dom.text !== text) { markdown(dom.content,text); dom.text = text; }
  if (dom.state !== m.state) {
    dom.actions.replaceChildren();
    if (m.state === 'failed') {
      const box = el('div','error-card'); box.setAttribute('role','alert'); box.append(el('p','',m.error || 'The answer could not be completed.'));
      const retry = el('button','button outline small','Retry'); retry.onclick = () => retryAnswer(chat,m); box.append(retry);
      if (m.errorStatus === 401) { const login = el('a','button quiet small','Sign in'); login.href = '/auth/slack'; box.append(login); }
      dom.content.replaceChildren(box); dom.text = null;
    } else if (m.state === 'cancelled') {
      dom.content.append(el('p','stopped','Stopped.')); const retry = el('button','button quiet small','Try again'); retry.onclick = () => retryAnswer(chat,m); dom.actions.append(retry);
    } else if (m.state === 'done') {
      const copy = el('button','button quiet small','Copy'); copy.onclick = () => copyText(m.content,copy);
      dom.actions.append(copy);
      const ids = citations(m.content);
      if (ids.length) { const sources = el('button','button quiet small',`Sources (${ids.length})`); sources.onclick = () => openSources(ids); dom.actions.append(sources); }
      const retry = el('button','button quiet small','Retry'); retry.onclick = () => retryAnswer(chat,m); dom.actions.append(retry);
    }
    dom.state = m.state;
  }
  if (stick && view === 'chat') scrollBottom(true);
  controls();
}
function contextBefore(chat, m) {
  const index = chat.messages.indexOf(m);
  return chat.messages.slice(0, index - 1).filter(x => x.role === 'user' || x.state === 'done')
    .map(x => ({role:x.role,content:x.content.slice(0,24000)})).slice(-16);
}
async function execute(chat, m) {
  if (running.has(m.id)) return;
  running.add(m.id); let failures = 0, lastSave = 0;
  try {
    if (!m.jobId) {
      const job = await api('/api/chat',{method:'POST',body:JSON.stringify({question:m.question,history:contextBefore(chat,m),request_id:m.requestId})});
      m.jobId = job.id; m.phase = 'Queued'; save(); updateAnswer(chat,m);
    }
    while (m.state === 'pending') {
      try {
        const result = await api(`/api/jobs/${m.jobId}`,{timeout:15000}); failures = 0;
        if (result.status === 'done') { m.state = 'done'; m.content = result.answer || 'No answer was returned. Please try again.'; }
        else if (result.status === 'cancelled') { m.state = 'cancelled'; m.content = result.partial || m.partial || ''; }
        else if (['failed','ignored'].includes(result.status)) { m.state = 'failed'; m.error = result.error || 'This request could not be completed.'; }
        else { m.phase = result.status === 'queued' ? 'Queued' : result.phase || 'Working'; m.partial = result.partial || ''; }
        updateAnswer(chat,m);
        if (Date.now() - lastSave > 1500 || m.state !== 'pending') { save(); lastSave = Date.now(); }
        if (m.state !== 'pending') break;
      } catch (error) {
        if ([401,403,404].includes(error.status)) throw error;
        failures++; m.phase = navigator.onLine ? 'Connection interrupted — reconnecting' : 'Offline — waiting for a connection'; updateAnswer(chat,m);
        if (failures >= 12) throw error;
        await wait(Math.min(failures * 1500, 6000));
      }
      await wait(document.hidden ? 2500 : 650);
    }
    if (m.state === 'done') { announce('Answer ready'); if (chat.id !== currentId) toast('An answer is ready.', () => openChat(chat.id),'Open'); }
  } catch (error) { m.state = 'failed'; m.error = error.message; m.errorStatus = error.status; updateAnswer(chat,m); }
  finally { running.delete(m.id); save(); controls(); }
}
async function retryAnswer(chat,m) {
  if (pending()) { toast('Stop the current answer before retrying.'); return; }
  // Keep the question and context in place, and discard later turns that relied on this answer.
  const index = chat.messages.indexOf(m);
  if (index < chat.messages.length - 1) {
    const branch = {...chat,id:uid(),created:Date.now(),messages:chat.messages.slice(0,index+1).map(x => ({...x,id:uid()}))};
    chats.unshift(branch); currentId = branch.id; chat = branch; m = branch.messages.at(-1);
  }
  // An uncertain POST retry reuses its request ID. A known finished request gets a new ID.
  if (m.jobId) { m.jobId = null; m.requestId = uid(); }
  Object.assign(m,{state:'pending',content:'',partial:'',error:'',phase:'Sending your question',started:Date.now()});
  renderChat(); save(); execute(chat,m);
}
function submitQuestion(question) {
  const chat = current();
  if (pending()) { toast('Stop the current answer to send another question.'); return; }
  question = question.trim(); if (!question) return;
  if (question.length > 16000) { $('composer-error').textContent = 'Keep your question under 16,000 characters.'; $('composer-error').hidden = false; return; }
  $('composer-error').hidden = true;
  if (!chat.messages.length) chat.title = question.slice(0,80);
  chat.messages.push({id:uid(),role:'user',content:question});
  const m = {id:uid(),role:'assistant',question,requestId:uid(),state:'pending',phase:'Sending your question',started:Date.now(),content:''};
  chat.messages.push(m); chat.draft = ''; renderChat(); save(); scrollBottom(true); execute(chat,m); $('question').focus();
}
function openChat(id) { currentId = id; $('history-dialog').close(); showView('chat'); renderChat(); save(); scrollBottom(true); $('question').focus(); }
$('chat-form').onsubmit = e => { e.preventDefault(); submitQuestion($('question').value); };
$('question').oninput = () => { current().draft = $('question').value; resizeComposer(); save(); };
$('question').onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && window.innerWidth > 680) { e.preventDefault(); if (!pending()) $('chat-form').requestSubmit(); } };
$('stop').onclick = async () => {
  const m = pending(); if (!m) return; $('stop').disabled = true;
  try {
    // A fast Stop click can happen before the server returns the job ID.
    for (let i = 0; !m.jobId && m.state === 'pending' && i < 30; i++) await wait(100);
    if (!m.jobId) throw new Error('The request is still connecting. Try Stop again in a moment.');
    const result = await api(`/api/jobs/${m.jobId}/cancel`,{method:'POST'});
    if (result.status === 'cancelled') { m.state = 'cancelled'; m.content = m.partial || ''; updateAnswer(current(),m); save(); announce('Answer stopped'); }
  } catch (error) { toast(error.message); }
  finally { $('stop').disabled = false; controls(); }
};
$('new-chat').onclick = () => { const chat = freshChat(); chats.unshift(chat); openChat(chat.id); };
$('chat-tab').onclick = () => { showView('chat'); $('question').focus(); };
document.querySelector('.brand').onclick = e => { e.preventDefault(); showView('chat'); $('question').focus(); };
$('memories-tab').onclick = () => { showView('memories'); $('search-query').focus(); };
document.querySelectorAll('[data-question]').forEach(b => b.onclick = () => submitQuestion(b.dataset.question));
$('jump-bottom').onclick = () => scrollBottom(true);
window.addEventListener('scroll',() => { $('jump-bottom').hidden = view !== 'chat' || current().messages.length === 0 || atBottom(); },{passive:true});

function renderHistory() {
  $('history-list').replaceChildren(); const records = chats.filter(c => c.messages.length);
  if (!records.length) { $('history-list').append(el('div','empty-results','No conversations yet.')); return; }
  for (const chat of records) {
    const row = el('div','history-row' + (chat.id === currentId ? ' current' : ''));
    const open = el('button'); open.append(el('strong','',chat.title),el('small','',new Date(chat.created).toLocaleDateString(undefined,{month:'short',day:'numeric'}))); open.onclick = () => openChat(chat.id);
    const remove = el('button','icon-button','×'); remove.setAttribute('aria-label','Delete conversation: ' + chat.title);
    remove.onclick = () => {
      if (chat.messages.some(m => m.state === 'pending')) { toast('Stop the answer before deleting this conversation.'); return; }
      const index = chats.indexOf(chat); chats.splice(index,1);
      if (chat.id === currentId) { const fresh = freshChat(); chats.unshift(fresh); currentId = fresh.id; renderChat(); }
      save(); renderHistory(); toast('Conversation deleted.',() => { chats.splice(index,0,chat); save(); renderHistory(); });
    };
    row.append(open,remove); $('history-list').append(row);
  }
}
$('history-button').onclick = () => { renderHistory(); $('history-dialog').showModal(); };
$('close-history').onclick = () => $('history-dialog').close();

function filterArgs() {
  const args = {query:$('search-query').value.trim(),mode:$('search-mode').value,limit:15};
  const [sort,order] = $('sort').value.split(':'); args.sort_by = sort; args.sort_order = order || 'desc';
  if ($('scope').value.trim()) args.scope = $('scope').value.trim();
  if ($('kind').value) args.kinds = [$('kind').value];
  if ($('created-from').value) args.created_from = $('created-from').value;
  if ($('created-through').value) { const date = new Date($('created-through').value + 'T00:00:00Z'); date.setUTCDate(date.getUTCDate()+1); args.created_before = date.toISOString().slice(0,10); }
  args.include_archive = $('archive').checked;
  return args;
}
function filterCount() {
  const count = ['scope','kind','created-from','created-through'].filter(id => $(id).value).length + Number($('archive').checked);
  $('filter-count').hidden = !count; $('filter-count').textContent = count; $('clear-filters').hidden = !count;
}
function skeletons() {
  $('results').replaceChildren();
  for (let i=0;i<4;i++) { const row = el('div','skeleton'); row.setAttribute('aria-hidden','true'); row.append(el('span'),el('span'),el('span')); $('results').append(row); }
}
const dateLabel = value => value ? new Date(value).toLocaleDateString(undefined,{year:'numeric',month:'short',day:'numeric',timeZone:'UTC'}) : 'Date unknown';
function metadata(memory) {
  const row = el('div','memory-meta');
  if (memory.kind) row.append(el('span','badge ' + memory.kind,memory.kind));
  row.append(el('span','',memory.created_at ? dateLabel(memory.created_at) : memory.updated_at ? 'Updated ' + dateLabel(memory.updated_at) : 'Date unknown'));
  if (memory.scope) row.append(el('span','',memory.scope));
  if (memory.layer === 'archive') row.append(el('span','badge','archived'));
  return row;
}
async function search(append = false) {
  clearTimeout(searchTimer); searchController?.abort(); const controller = new AbortController(); searchController = controller;
  const version = ++searchVersion; filterCount(); $('load-more').hidden = true;
  if ($('created-from').value && $('created-through').value && $('created-from').value > $('created-through').value) {
    $('search-feedback').textContent = '“Created through” must be on or after the start date.'; $('search-feedback').className = 'search-feedback error'; $('results').setAttribute('aria-busy','false'); return;
  }
  const args = append ? lastSearch : filterArgs();
  $('search-feedback').className = 'search-feedback'; $('search-feedback').replaceChildren(el('span','spinner'),document.createTextNode('Searching…'));
  $('results').setAttribute('aria-busy','true'); $('load-more').disabled = true;
  if (!append) skeletons();
  try {
    const data = await api('/api/memories/search',{method:'POST',body:JSON.stringify({...args,offset:append ? nextOffset : 0}),signal:controller.signal,timeout:180000});
    if (version !== searchVersion) return;
    if (append && resultRevision && resultRevision !== data.revision) { toast('Memories changed. Results refreshed.'); return search(false); }
    if (!append) $('results').replaceChildren(); lastSearch = args; resultRevision = data.revision;
    for (const memory of data.results || []) {
      const card = el('a','memory-card'); card.href = '/memory/' + encodeURIComponent(memory.id);
      card.append(el('h2','',memory.title || 'Untitled memory'),el('p','',memory.excerpt || memory.when_to_use),metadata(memory));
      card.onclick = e => { if (e.metaKey || e.ctrlKey || e.shiftKey) return; e.preventDefault(); openSources([memory.id]); };
      $('results').append(card);
    }
    const total = data.total_matches || 0;
    $('search-feedback').textContent = total ? `${total.toLocaleString()} ${total === 1 ? 'memory' : 'memories'}` : '';
    if (!total) {
      const empty = el('div','empty-results'); empty.append(el('h2','','No memories found'),el('p','',args.query ? 'Try different words or remove a filter.' : 'No memories match these filters.'));
      const clear = el('button','button outline','Reset search'); clear.onclick = resetSearch; empty.append(clear); $('results').append(empty);
    }
    nextOffset = data.next_offset; $('load-more').hidden = nextOffset == null;
  } catch (error) {
    if (controller.signal.aborted || version !== searchVersion) return;
    $('search-feedback').className = 'search-feedback error'; $('search-feedback').textContent = error.message;
    if (!append) $('results').replaceChildren();
    const retry = el('button','button outline small','Retry'); retry.onclick = () => search(append); $('search-feedback').append(retry);
  } finally {
    if (version === searchVersion) { $('results').setAttribute('aria-busy','false'); $('load-more').disabled = false; }
  }
}
function scheduleSearch() { clearTimeout(searchTimer); searchTimer = setTimeout(() => search(),400); }
function resetSearch() {
  for (const id of ['scope','kind','created-from','created-through']) $(id).value = '';
  $('archive').checked = false; $('search-query').value = ''; $('sort').value = 'relevance'; search();
}
$('search-form').onsubmit = e => { e.preventDefault(); search(); };
$('search-query').oninput = scheduleSearch; $('scope').oninput = scheduleSearch;
for (const id of ['search-mode','sort','kind','created-from','created-through','archive']) $(id).onchange = () => search();
$('filters-toggle').onclick = () => { const open = $('filters').hidden; $('filters').hidden = !open; $('filters-toggle').setAttribute('aria-expanded',String(open)); };
$('clear-filters').onclick = () => { for (const id of ['scope','kind','created-from','created-through']) $(id).value = ''; $('archive').checked = false; search(); };
$('load-more').onclick = () => search(true);

async function openSources(ids, focusId) {
  sourceIds = [...new Set(ids)].filter(id => /^[\w-]+$/.test(id)).slice(0,20); const version = ++sourceVersion;
  $('source-body').replaceChildren(); $('ask-source').hidden = true; $('copy-source').hidden = false;
  $('source-dialog').querySelector('.dialog-top>span').textContent = sourceIds.length > 1 ? `Sources (${sourceIds.length})` : 'Source';
  if (!$('source-dialog').open) $('source-dialog').showModal();
  const heading = el('h1','sr-only','Source memories'); heading.id = 'source-title'; $('source-body').append(heading);
  if (!sourceIds.length) { $('source-body').append(el('p','empty-results','No memories were included in this link.')); $('copy-source').hidden = true; return; }
  const sections = sourceIds.map(id => { const section = el('section','source-card'); section.dataset.id = id; section.append(el('div','working','Loading memory…')); $('source-body').append(section); return section; });
  let cursor = 0;
  async function load() {
    while (cursor < sourceIds.length) {
      const index = cursor++, id = sourceIds[index], section = sections[index];
      try {
        const cached = sourceCache.get(id);
        const memory = cached && Date.now() - cached.time < 60000 ? cached.memory : await api('/api/memories/' + encodeURIComponent(id),{timeout:90000});
        sourceCache.set(id,{memory,time:Date.now()});
        if (version !== sourceVersion) return;
        const title = el('h1','',memory.title), content = el('div','answer-content'); markdown(content,memory.content);
        section.replaceChildren(title,metadata(memory),content);
        if (memory.evidence || memory.source) {
          const evidence = el('details','source-evidence'); evidence.append(el('summary','','Evidence and origin'));
          if (memory.evidence) evidence.append(el('p','',memory.evidence));
          if (memory.source) evidence.append(el('p','',memory.source));
          section.append(evidence);
        }
        if (focusId === id) section.scrollIntoView({block:'start'});
      } catch (error) {
        if (version !== sourceVersion) return;
        const box = el('div','error-card'); box.append(el('p','',error.status === 404 ? 'This memory is unavailable or outside Moe’s memory collection.' : error.message));
        const retry = el('button','button outline small','Retry'); retry.onclick = () => openSources(sourceIds,focusId); box.append(retry); section.replaceChildren(box);
      }
    }
  }
  await Promise.all([load(),load()]);
  if (version === sourceVersion) $('ask-source').hidden = sourceIds.length === 0;
}
$('close-source').onclick = () => $('source-dialog').close();
$('source-dialog').onclose = () => { sourceVersion++; if (location.pathname !== '/') history.replaceState(null,'',view === 'memories' ? '/#memories' : '/#chat'); };
$('copy-source').onclick = () => copyText(sourceIds.length === 1 ? `${location.origin}/memory/${sourceIds[0]}` : `${location.origin}/sources?ids=${sourceIds.join(',')}`,$('copy-source'));
$('ask-source').onclick = () => {
  const links = sourceIds.map(id => `${location.origin}/memory/${id}`).join('\n'); $('source-dialog').close(); showView('chat');
  $('question').value = `Help me understand ${sourceIds.length > 1 ? 'these memories' : 'this memory'}:\n${links}`; current().draft = $('question').value; resizeComposer(); $('question').focus();
};
for (const dialog of [$('source-dialog'),$('history-dialog')]) dialog.addEventListener('click',event => {
  const r = dialog.getBoundingClientRect(); if (event.target === dialog && (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom)) dialog.close();
});
async function copyText(text, button) {
  try { await navigator.clipboard.writeText(text); const label = button.textContent; button.textContent = 'Copied'; setTimeout(() => { button.textContent = label; },1800); }
  catch { toast('Copy was blocked by your browser. Select the text and copy it manually.'); }
}
document.addEventListener('keydown',e => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'k') { e.preventDefault(); if (!$('source-dialog').open && !$('history-dialog').open) { showView('memories'); $('search-query').focus(); } }
  if (e.key === '/' && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName) && !document.querySelector('dialog[open]')) { e.preventDefault(); showView('memories'); $('search-query').focus(); }
});
function connectivity() { $('connection').hidden = navigator.onLine; $('connection').textContent = 'You’re offline. Your conversation is saved in this browser.'; }
window.addEventListener('offline',connectivity); window.addEventListener('online',() => { connectivity(); toast('Back online.'); });
renderChat(); connectivity();
const sourcePath = location.pathname.match(/^\/memory\/([\w-]+)$/);
if (sourcePath) openSources([sourcePath[1]]);
else if (location.pathname === '/sources') openSources((new URLSearchParams(location.search).get('ids') || '').split(','));
else if (location.hash === '#memories') showView('memories');
api('/api/config').then(config => {
  $('login').hidden = !config.login_available || config.signed_in || config.access_mode === 'public';
  if (config.slack_installed && new URLSearchParams(location.search).get('slack') === 'connected') { toast('Moegent is connected to Slack.'); history.replaceState(null,'','/'); }
  if (config.access_mode === 'allowlist' && !config.signed_in) { $('connection').hidden = false; $('connection').replaceChildren(document.createTextNode('Sign in with Slack to ask questions. ')); const a = el('a','','Sign in'); a.href = '/auth/slack'; $('connection').append(a); }
  for (const chat of chats) for (const m of chat.messages) if (m.state === 'pending') execute(chat,m);
}).catch(error => { $('connection').hidden = false; $('connection').textContent = error.message; });
