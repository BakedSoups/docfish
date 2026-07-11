const modelSelect = document.querySelector('#model');
const statusEl = document.querySelector('#status');
const statusText = statusEl.querySelector('span');
const form = document.querySelector('#composer');
const input = document.querySelector('#input');
const send = document.querySelector('#send');
const welcome = document.querySelector('#welcome');
const messagesEl = document.querySelector('#messages');
const docList = document.querySelector('#doc-list');
const pageResults = document.querySelector('#page-results');
const ragToggle = document.querySelector('#rag-toggle');
let messages = [];
let controller = null;
let docs = [];
let selectedDoc = localStorage.getItem('angler-doc') || '';
const anglerPrompt = 'You are Angler, a concise coding and documentation assistant. Answer code questions accurately and ideally in one shot. Lead with the solution or code. Keep explanations brief and practical unless the user asks for more detail. Do not add unnecessary background, follow-up questions, or filler.';

async function loadModels() {
  try {
    const response = await fetch('/api/models');
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Ollama unavailable');
    modelSelect.innerHTML = '';
    for (const model of data.models || []) modelSelect.add(new Option(model.name, model.name));
    if (!modelSelect.options.length) modelSelect.add(new Option('No models installed', ''));
    const saved = localStorage.getItem('ollama-model');
    if (saved && [...modelSelect.options].some(o => o.value === saved)) modelSelect.value = saved;
    statusEl.className = 'status online'; statusText.textContent = '';
  } catch (error) {
    modelSelect.innerHTML = '<option value="">Ollama unavailable</option>';
    statusEl.className = 'status error'; statusText.textContent = '';
  }
}

function addMessage(role, content = '') {
  welcome.hidden = true; messagesEl.classList.add('active');
  const el = document.createElement('article');
  el.className = `message ${role}`;
  el.innerHTML = `<div class="role">${role === 'user' ? 'You' : 'Angler'}</div><div class="content"></div>`;
  el.querySelector('.content').textContent = content;
  messagesEl.append(el); window.scrollTo({ top: document.body.scrollHeight, behavior:'smooth' });
  return el.querySelector('.content');
}

async function chat(text) {
  if (!modelSelect.value || controller) return;
  messages.push({ role:'user', content:text }); addMessage('user', text);
  const output = addMessage('assistant'); output.classList.add('thinking');
  controller = new AbortController(); send.disabled = true;
  let answer = '';
  try {
    let sources = [];
    let systemPrompt = anglerPrompt;
    if (ragToggle.checked && selectedDoc) {
      output.textContent = 'Searching documentation…';
      const lookup = await fetch('/api/rag/search', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({doc:selectedDoc, query:text})});
      const found = await lookup.json();
      if (!lookup.ok) throw new Error(found.error || 'Documentation search failed');
      sources = found.results || [];
      const context = sources.map((s,i) => `[${i+1}] PAGE: ${s.path}\nTITLE: ${s.title}\n${s.text}`).join('\n\n');
      systemPrompt += `\n\nUse only the documentation excerpts below for factual claims about the selected library. Cite claims inline as [1], [2], etc. Clearly say when the excerpts do not contain the answer. End with a short Sources list containing the cited page paths.\n\n${context}`;
      output.textContent = '';
    }
    const response = await fetch('/api/chat', { method:'POST', headers:{'Content-Type':'application/json'}, signal:controller.signal,
      body:JSON.stringify({ model:modelSelect.value, messages:[{role:'system',content:systemPrompt}, ...messages], stream:true }) });
    if (!response.ok) { const data = await response.json(); throw new Error(data.error || 'Request failed'); }
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
    while (true) {
      const { value, done } = await reader.read(); if (done) break;
      buffer += decoder.decode(value, {stream:true}); const lines = buffer.split('\n'); buffer = lines.pop();
      for (const line of lines) if (line.trim()) { const part = JSON.parse(line); answer += part.message?.content || ''; output.textContent = answer; window.scrollTo(0, document.body.scrollHeight); }
    }
    messages.push({ role:'assistant', content:answer });
    if (sources.length) addSourceLinks(output, sources);
  } catch (error) { output.textContent = error.name === 'AbortError' ? 'Generation stopped.' : `Error: ${error.message}`; }
  finally { output.classList.remove('thinking'); controller = null; send.disabled = false; input.focus(); }
}

form.addEventListener('submit', e => { e.preventDefault(); const text=input.value.trim(); if (text) { input.value=''; input.style.height='auto'; chat(text); } });
input.addEventListener('keydown', e => { if (e.key==='Enter' && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } });
input.addEventListener('input', () => { input.style.height='auto'; input.style.height=Math.min(input.scrollHeight,160)+'px'; });
modelSelect.addEventListener('change', () => localStorage.setItem('ollama-model', modelSelect.value));
document.querySelector('#clear').addEventListener('click', () => { if (controller) controller.abort(); messages=[]; messagesEl.innerHTML=''; messagesEl.classList.remove('active'); welcome.hidden=false; input.focus(); });

function addSourceLinks(output, sources) {
  const links = document.createElement('div'); links.className = 'source-links';
  sources.forEach((source, index) => {
    const a = document.createElement('button'); a.type='button'; a.textContent=`[${index+1}] ${source.title || source.path}`;
    a.addEventListener('click', () => openDocument(selectedDoc, source.path, source.title)); links.append(a);
  });
  output.append(links);
}

async function loadDocs() {
  try { const response=await fetch('/api/docs'); const data=await response.json(); docs=data.docs || []; if (!selectedDoc && docs.length) selectedDoc=docs[0].id; renderDocs(); }
  catch { docList.innerHTML='<p class="doc-error">Documentation unavailable</p>'; }
}

function renderDocs() {
  docList.innerHTML='';
  for (const doc of docs) {
    const item=document.createElement('div'); item.className=`doc-item ${doc.id===selectedDoc?'selected':''}`;
    const state=doc.state==='indexing' ? `Indexing ${doc.progress}%` : doc.indexed ? 'RAG ready' : 'Not indexed';
    item.innerHTML=`<span>${doc.name}</span><small class="${doc.indexed?'ready':''}">${state}</small>${doc.indexed?'':`<button class="index-doc" type="button">Index</button>`}`;
    item.addEventListener('click', e => { if (e.target.classList.contains('index-doc')) return; selectedDoc=doc.id; localStorage.setItem('angler-doc',selectedDoc); renderDocs(); searchPages(); if (doc.home) openDocument(doc.id,doc.home,doc.name); });
    item.querySelector('.index-doc')?.addEventListener('click', async () => { await fetch('/api/docs/index',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({doc:doc.id})}); pollDocs(); });
    docList.append(item);
  }
}

let docTimer;
async function pollDocs() { clearTimeout(docTimer); await loadDocs(); if (docs.some(d=>d.state==='indexing')) docTimer=setTimeout(pollDocs,1500); }
async function searchPages() {
  const query=document.querySelector('#doc-search').value.trim(); if (!selectedDoc) return;
  const response=await fetch(`/api/docs/pages?doc=${encodeURIComponent(selectedDoc)}&q=${encodeURIComponent(query)}`); const data=await response.json();
  pageResults.innerHTML='';
  (data.pages || []).slice(0,40).forEach(page => { const b=document.createElement('button'); b.className='page-result'; b.textContent=page.path; b.onclick=()=>openDocument(selectedDoc,page.path,page.title); pageResults.append(b); });
}
function openDocument(doc,path,title) { const url=`/docs/${encodeURIComponent(doc)}/${path.split('/').map(encodeURIComponent).join('/')}`; document.querySelector('#doc-frame').src=url; document.querySelector('#viewer-title').textContent=title || path; document.querySelector('#open-doc').href=url; document.querySelector('#doc-viewer').hidden=false; }
document.querySelector('#doc-search').addEventListener('input',()=>{ clearTimeout(window.docSearchTimer); window.docSearchTimer=setTimeout(searchPages,250); });
document.querySelector('#close-viewer').addEventListener('click',()=>document.querySelector('#doc-viewer').hidden=true);
document.querySelector('#collapse-docs').addEventListener('click',()=>{ document.body.classList.add('docs-collapsed'); document.body.classList.remove('docs-mobile-open'); });
document.querySelector('#show-docs').addEventListener('click',()=>{ document.body.classList.remove('docs-collapsed'); document.body.classList.add('docs-mobile-open'); });
ragToggle.checked=localStorage.getItem('angler-rag')==='true'; ragToggle.addEventListener('change',()=>localStorage.setItem('angler-rag',ragToggle.checked));
loadModels();
loadDocs();
