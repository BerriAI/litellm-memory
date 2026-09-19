const $ = id => document.getElementById(id);
let history = [], busy = false, searchArgs = {}, nextOffset = null;
async function api(path, options = {}) {
  const response = await fetch(path, {headers: {'Content-Type': 'application/json'}, ...options});
  if (response.status === 401) { location.href = '/auth/slack'; throw new Error('Sign in to continue'); }
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'That request could not be completed.');
  return data;
}
function view(explore) {
  $('chat-view').hidden = explore; $('explore-view').hidden = !explore;
  $('chat-tab').classList.toggle('active', !explore); $('explore-tab').classList.toggle('active', explore);
  $('page-label').textContent = explore ? 'A SHARED BODY OF KNOWLEDGE' : 'YOUR MEMORY, IN CONVERSATION';
}
$('chat-tab').onclick = () => view(false);
$('explore-tab').onclick = () => view(true);
$('new-chat').onclick = () => { if (busy) return; history = []; $('messages').replaceChildren(); $('welcome').hidden = false; view(false); $('question').focus(); };
// Render text and HTTPS links only; never inject model-provided HTML.
function richText(node, text) {
  const regex = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g;
  let last = 0;
  for (const match of text.matchAll(regex)) {
    node.append(document.createTextNode(text.slice(last, match.index)));
    const a = document.createElement('a'); a.textContent = match[1]; a.href = match[2]; a.target = '_blank'; a.rel = 'noopener noreferrer'; node.append(a);
    last = match.index + match[0].length;
  }
  node.append(document.createTextNode(text.slice(last)));
}
function message(role, text) {
  const el = document.createElement('div'); el.className = `message ${role}`; richText(el, text); $('messages').append(el); el.scrollIntoView({behavior:'smooth', block:'nearest'}); return el;
}
$('question').onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); $('chat-form').requestSubmit(); } };
document.querySelectorAll('.suggestions button').forEach(button => button.onclick = () => { $('question').value = button.textContent; $('chat-form').requestSubmit(); });
$('chat-form').onsubmit = async e => {
  e.preventDefault(); if (busy) return;
  const question = $('question').value.trim(); if (!question) return;
  busy = true; $('send').disabled = true; $('welcome').hidden = true; $('question').value = '';
  message('user', question); $('chat-status').textContent = 'Searching memories and connecting the dots…';
  try {
    const job = await api('/api/chat', {method:'POST', body:JSON.stringify({question, history:history.slice(-16)})});
    let result;
    while (true) {
      await new Promise(resolve => setTimeout(resolve, 1400));
      result = await api(`/api/jobs/${job.id}`);
      if (['done','failed','ignored'].includes(result.status)) break;
      $('chat-status').textContent = result.status === 'queued' ? 'Your question is queued…' : 'Reading sources and preparing an answer…';
    }
    if (result.status !== 'done') throw new Error(result.error || 'Please try again.');
    message('assistant', result.answer); history.push({role:'user',content:question},{role:'assistant',content:result.answer});
    $('chat-status').textContent = 'Follow the sources for the full story.';
  } catch (error) { message('error', error.message); $('chat-status').textContent = 'Your question wasn’t completed.'; }
  finally { busy = false; $('send').disabled = false; $('question').focus(); }
};
async function search(append = false) {
  $('search-status').textContent = 'Searching…'; $('load-more').hidden = true;
  if (!append) $('results').replaceChildren();
  try {
    const data = await api('/api/memories/search', {method:'POST',body:JSON.stringify({...searchArgs,offset:append ? nextOffset : 0})});
    for (const result of data.results || []) {
      const card = document.createElement('article'); card.className = 'memory-card';
      const meta = document.createElement('div'); meta.className = 'meta'; meta.textContent = [result.layer?.replace('_',' '), result.kind, result.created_at?.slice(0,10) || 'Creation date unknown'].filter(Boolean).join(' · ');
      const title = document.createElement('h2'); const link = document.createElement('a'); link.textContent = result.title || result.id; link.href = '/memory/' + encodeURIComponent(result.id); link.target = '_blank'; link.rel = 'noopener'; title.append(link);
      const excerpt = document.createElement('p'); excerpt.textContent = result.preview || result.excerpt || result.when_to_use || '';
      card.append(meta,title,excerpt); $('results').append(card);
    }
    nextOffset = data.next_offset; $('load-more').hidden = nextOffset == null;
    $('search-status').textContent = `${data.total_matches ?? $('results').children.length} matching memories${data.mode ? ' · ' + data.mode : ''}`;
  } catch(error) { $('search-status').textContent = error.message; }
}
$('search-form').onsubmit = e => {
  e.preventDefault(); searchArgs = {query:$('search-query').value, mode:$('mode').value, sort_by:$('sort').value, sort_order:'desc', limit:12, include_archive:$('archive').checked};
  if ($('scope').value) searchArgs.scope = $('scope').value;
  if ($('created-from').value) searchArgs.created_from = $('created-from').value;
  if ($('created-before').value) searchArgs.created_before = $('created-before').value;
  if ($('kind').value) searchArgs.kinds = [$('kind').value];
  search();
};
$('load-more').onclick = () => search(true);
$('logout').onclick = async () => { await api('/auth/logout', {method:'POST'}); location.reload(); };
api('/api/config').then(config => { $('login').hidden = !config.login_available || config.signed_in; $('logout').hidden = !config.signed_in; }).catch(() => {});
