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
const ragDoc = document.querySelector('#rag-doc');
let messages = [];
let controller = null;
let docs = [];
let selectedDoc = localStorage.getItem('docfish-source') || localStorage.getItem('angler-doc') || '';
const docfishPrompt = 'You are Docfish, a concise local-first learning assistant for programmers. Help the learner reason from visible evidence and produce an effective answer in one to three shots. Keep retrieved context focused. Lead with the solution, explain the key reasoning without hidden chain-of-thought, and preserve the learner’s autonomy.';
const sourceEstimates = new Map();
marked.use({gfm:true,breaks:true});
function renderMarkdown(target,text) { target.innerHTML=DOMPurify.sanitize(marked.parse(text)); }

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
  el.innerHTML = `<div class="role">${role === 'user' ? 'You' : 'Docfish'}</div><div class="content"></div>`;
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
    let systemPrompt = docfishPrompt;
    if (ragToggle.checked && selectedDoc) {
      output.textContent = 'Searching documentation…';
      const lookup = await fetch('/api/rag/search', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({doc:selectedDoc, query:text})});
      const found = await lookup.json();
      if (!lookup.ok) {
        const source=docs.find(doc=>doc.id===selectedDoc); const sourceName=source?.name || 'Selected source';
        const progress=source?.state==='indexing' && source.progress ? ` (${source.progress}%)` : '';
        output.textContent=`${sourceName} is still indexing${progress}. RAG requires source evidence, so no unsourced answer was generated.`;
        messages.pop();
        return;
      }
      sources = found.results || [];
      const context = sources.map((s,i) => `[${i+1}] PAGE: ${s.page || s.path}\nPATH: ${s.path}\nTITLE: ${s.title}\n${s.text}`).join('\n\n');
      systemPrompt += `\n\nWork from the evidence excerpts below. First identify which exact passage answers the question, then formulate the concise answer from that evidence. Use only these excerpts for factual claims about the selected library. Cite every factual claim inline as [1], [2], etc. For PDFs, state the physical page number. For HTML documentation, state the page path and section anchor. Never invent a page reference. Clearly say when the evidence does not contain the answer. End with a short Sources list containing every cited page or path. Do not reveal private chain-of-thought; provide the answer and supporting citations only.\n\n${context}`;
      output.textContent = '';
    }
    const response = await fetch('/api/chat', { method:'POST', headers:{'Content-Type':'application/json'}, signal:controller.signal,
      body:JSON.stringify({ model:modelSelect.value, messages:[{role:'system',content:systemPrompt}, ...messages], stream:true }) });
    if (!response.ok) { const data = await response.json(); throw new Error(data.error || 'Request failed'); }
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
    while (true) {
      const { value, done } = await reader.read(); if (done) break;
      buffer += decoder.decode(value, {stream:true}); const lines = buffer.split('\n'); buffer = lines.pop();
      for (const line of lines) if (line.trim()) { const part = JSON.parse(line); answer += part.message?.content || ''; renderMarkdown(output,answer); window.scrollTo(0, document.body.scrollHeight); }
    }
    messages.push({ role:'assistant', content:answer });
    if (sources.length) {
      addSourceLinks(output, sources, text);
      const validation=await fetch('/api/questions/validate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({answer,source_count:sources.length})}).then(r=>r.json());
      if(!validation.grounded){const warning=document.createElement('span');warning.className='rag-notice';warning.textContent=validation.warning;output.append(warning);}
    }
  } catch (error) { output.textContent = error.name === 'AbortError' ? 'Generation stopped.' : `Error: ${error.message}`; }
  finally { output.classList.remove('thinking'); controller = null; send.disabled = false; input.focus(); }
}

form.addEventListener('submit', e => { e.preventDefault(); const text=input.value.trim(); if (text) { input.value=''; input.style.height='auto'; chat(text); } });
input.addEventListener('keydown', e => { if (e.key==='Enter' && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } });
input.addEventListener('input', () => { input.style.height='auto'; input.style.height=Math.min(input.scrollHeight,160)+'px'; });
modelSelect.addEventListener('change', () => localStorage.setItem('ollama-model', modelSelect.value));
function resetChat() { if (controller) controller.abort(); messages=[]; messagesEl.innerHTML=''; messagesEl.classList.remove('active'); welcome.hidden=false; input.value=''; input.focus(); }
document.querySelector('#clear').addEventListener('click',resetChat);
document.addEventListener('keydown',e=>{ if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase()==='r') { e.preventDefault(); resetChat(); } });

function addSourceLinks(output, sources, query) {
  const links = document.createElement('div'); links.className = 'source-links';
  sources.forEach((source, index) => {
    const a = document.createElement('button'); a.type='button'; a.textContent=`[${index+1}] ${source.title || source.path}`;
    a.addEventListener('click', () => openDocument(selectedDoc, source.path, source.page ? `Page ${source.page}` : source.title, 'side')); links.append(a);
  });
  output.append(links);
  const evidence=document.createElement('details'); evidence.className='evidence'; evidence.innerHTML='<summary>View supporting passages</summary>';
  sources.forEach((source,index)=>{ const passage=document.createElement('div'); passage.className='evidence-passage'; const label=document.createElement('b'); label.textContent=`[${index+1}] ${source.page?`Page ${source.page}`:source.title}`; passage.append(label); passage.append(highlightText(source.text || '',query)); evidence.append(passage); });
  output.append(evidence);
}
function highlightText(text,query) { const wrap=document.createElement('p'); const terms=[...new Set(query.match(/[a-z0-9_]{3,}/gi)||[])].sort((a,b)=>b.length-a.length); if (!terms.length) { wrap.textContent=text; return wrap; } const pattern=new RegExp(`(${terms.map(t=>t.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('|')})`,'gi'); text.slice(0,2200).split(pattern).forEach(part=>{ if(terms.some(t=>t.toLowerCase()===part.toLowerCase())) { const mark=document.createElement('mark'); mark.textContent=part; wrap.append(mark); } else wrap.append(document.createTextNode(part)); }); return wrap; }

async function loadDocs() {
  try { const response=await fetch('/api/docs'); const data=await response.json(); docs=data.docs || []; if (!selectedDoc && docs.length) selectedDoc=docs[0].id; renderRagSelect(); renderDocs(); }
  catch { docList.innerHTML='<p class="doc-error">Documentation unavailable</p>'; }
}

function renderDocs() {
  docList.innerHTML='';
  for (const doc of docs) {
    const item=document.createElement('div'); item.className=`doc-item ${doc.id===selectedDoc?'selected':''}`;
    const state=doc.state==='indexing' ? `Indexing ${doc.progress}%` : doc.state==='queued' ? 'Queued' : doc.indexed ? 'RAG ready' : 'Not indexed';
    const estimate=sourceEstimates.get(doc.id); const size=estimate ? ` · ${formatBytes(estimate.estimated_index_bytes)} index` : '';
    const action=doc.state==='indexing'||doc.state==='queued' ? 'Cancel' : doc.indexed ? 'Refresh' : 'Index';
    item.innerHTML=`<div class="doc-cover"><img src="/api/docs/cover?doc=${encodeURIComponent(doc.id)}" alt="${doc.name} cover" onload="this.parentElement.classList.add('has-art')" onerror="this.remove()"><strong>${doc.name}</strong><em>${doc.type.toUpperCase()} SOURCE</em></div><span>${doc.name}</span><small class="${doc.indexed?'ready':''}">${state}${size}</small><div class="doc-actions"><button class="index-doc" type="button">${action}</button><button class="remove-doc" type="button" title="Remove source record; files stay on disk">Remove</button></div>`;
    item.querySelector('.index-doc').addEventListener('click',async e=>{ e.stopPropagation(); const active=doc.state==='indexing'||doc.state==='queued'; await fetch(`/api/sources/${encodeURIComponent(doc.id)}/${active?'cancel':'index'}`,{method:'POST'}); pollDocs(); });
    item.querySelector('.remove-doc').addEventListener('click',async e=>{ e.stopPropagation(); if (!confirm(`Remove ${doc.name} from Docfish? Source files will not be deleted.`)) return; await fetch(`/api/sources/${encodeURIComponent(doc.id)}`,{method:'DELETE'}); if(selectedDoc===doc.id) selectedDoc=''; await loadDocs(); });
    item.addEventListener('click', () => { selectDoc(doc.id); document.querySelector('#library-modal').hidden=true; if (doc.type==='pdf' || doc.home) openDocument(doc.id,doc.home,doc.name,'reader'); });
    docList.append(item);
  }
}

function renderRagSelect() {
  const current=selectedDoc; ragDoc.innerHTML='';
  for (const doc of docs) ragDoc.add(new Option(`${doc.indexed?'●':'○'} ${doc.name}`,doc.id));
  if (docs.some(doc=>doc.id===current)) ragDoc.value=current;
}
function selectDoc(id) { selectedDoc=id; localStorage.setItem('docfish-source',id); ragDoc.value=id; renderDocs(); searchPages(); }

function formatBytes(value) { if (!value) return '0 B'; const units=['B','KB','MB','GB','TB']; const power=Math.min(Math.floor(Math.log(value)/Math.log(1024)),units.length-1); return `${(value/1024**power).toFixed(power?1:0)} ${units[power]}`; }

const sourceForm=document.querySelector('#source-form');
document.querySelector('#add-source').addEventListener('click',()=>{ sourceForm.hidden=false; document.querySelector('#source-name').focus(); });
document.querySelector('#cancel-source').addEventListener('click',()=>{ sourceForm.hidden=true; document.querySelector('#source-feedback').textContent=''; });
sourceForm.addEventListener('submit',async e=>{
  e.preventDefault(); const feedback=document.querySelector('#source-feedback'); feedback.textContent='Inspecting supported files…';
  const response=await fetch('/api/sources',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:document.querySelector('#source-name').value,path:document.querySelector('#source-path').value,kind:document.querySelector('#source-kind').value})});
  const data=await response.json(); if(!response.ok){feedback.textContent=data.error||'Could not add source';return;}
  sourceEstimates.set(data.source.id,data.source); selectedDoc=data.source.id; feedback.textContent=`${data.source.files} supported files · ${formatBytes(data.source.source_bytes)} source · approximately ${formatBytes(data.source.estimated_index_bytes)} index. Review it below, then choose Index.`;
  sourceForm.reset(); await loadDocs();
});

function questionData(){return {goal:document.querySelector('#q-goal').value,context:document.querySelector('#q-context').value,constraints:document.querySelector('#q-constraints').value,question:document.querySelector('#q-question').value,format:document.querySelector('#q-format').value,examples:document.querySelector('#q-examples').value.split(/\n\s*---\s*\n/).filter(Boolean),model:modelSelect.value};}
function compileQuestion(data){return [['Goal',data.goal],['Known context',data.context],['Constraints',data.constraints],['Exact question',data.question],['Desired response format',data.format]].filter(([,value])=>value.trim()).map(([label,value])=>`${label}: ${value.trim()}`).concat(data.examples.length?[`Examples:\n${data.examples.slice(0,3).map((value,index)=>`Example ${index+1}:\n${value.trim()}`).join('\n\n')}`]:[]).join('\n\n');}
async function craftQuestion(mode){const feedback=document.querySelector('#question-feedback');feedback.textContent=mode==='missing'?'Checking what context is missing…':'Crafting a focused question…';const response=await fetch('/api/questions/craft',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...questionData(),mode})});const data=await response.json();document.querySelector('#q-proposal').value=data.proposal||'';feedback.textContent=response.ok?'Review and edit the proposal before using it.':data.error||'Question crafting failed.';}
document.querySelector('#improve-question').addEventListener('click',()=>craftQuestion('improve'));
document.querySelector('#missing-context').addEventListener('click',()=>craftQuestion('missing'));
document.querySelector('#use-question').addEventListener('click',()=>{const proposal=document.querySelector('#q-proposal').value.trim();input.value=proposal||compileQuestion(questionData());input.dispatchEvent(new Event('input'));input.focus();document.querySelector('#question-feedback').textContent='Question moved to the composer. You remain in control of the final edit and send.';});

let docTimer;
async function pollDocs() { clearTimeout(docTimer); await loadDocs(); if (docs.some(d=>['queued','indexing'].includes(d.state))) docTimer=setTimeout(pollDocs,1500); }
async function searchPages() {
  const query=document.querySelector('#doc-search').value.trim(); if (!selectedDoc) return;
  if (!query) { pageResults.innerHTML=''; return; }
  const response=await fetch(`/api/docs/pages?doc=${encodeURIComponent(selectedDoc)}&q=${encodeURIComponent(query)}`); const data=await response.json();
  pageResults.innerHTML='';
  (data.pages || []).slice(0,40).forEach(page => { const b=document.createElement('button'); b.className='page-result'; b.textContent=page.snippet ? `${page.title} — ${page.snippet}` : page.path; b.onclick=()=>openDocument(selectedDoc,page.path,page.title,'reader'); pageResults.append(b); });
}
function openDocument(doc,path,title,mode='side') { const isPage=path.startsWith('#page='); const [base,fragment='']=isPage?['',path.slice(1)]:path.split('#',2); const safePath=base.split('/').map(encodeURIComponent).join('/'); const hash=fragment?`#${fragment}`:''; const url=`/docs/${encodeURIComponent(doc)}/${safePath}${hash}`; document.body.classList.toggle('reader-open',mode==='reader'); document.body.classList.toggle('source-open',mode==='side'); document.querySelector('#doc-frame').src=url; document.querySelector('#viewer-title').textContent=title || path; document.querySelector('#open-doc').href=url; document.querySelector('#doc-viewer').hidden=false; }
document.querySelector('#doc-search').addEventListener('input',()=>{ clearTimeout(window.docSearchTimer); window.docSearchTimer=setTimeout(searchPages,250); });
document.querySelector('#google-stack').addEventListener('click',()=>{ const query=document.querySelector('#doc-search').value.trim(); if (query) window.open(`https://www.google.com/search?q=${encodeURIComponent(`site:stackoverflow.com ${query}`)}`,'_blank','noopener'); });
document.querySelector('#doc-search').addEventListener('keydown',e=>{ if (e.key==='Enter' && e.shiftKey) { e.preventDefault(); document.querySelector('#google-stack').click(); } });
document.querySelector('#close-viewer').addEventListener('click',()=>{ document.querySelector('#doc-viewer').hidden=true; document.body.classList.remove('reader-open','source-open'); });
document.querySelector('#open-library').addEventListener('click',()=>{ document.querySelector('#library-modal').hidden=false; document.querySelector('#doc-search').focus(); });
document.querySelector('#close-library').addEventListener('click',()=>document.querySelector('#library-modal').hidden=true);
document.querySelector('#library-modal').addEventListener('click',e=>{ if (e.target.id==='library-modal') e.currentTarget.hidden=true; });
ragDoc.addEventListener('change',()=>selectDoc(ragDoc.value));
ragToggle.checked=(localStorage.getItem('docfish-rag') || localStorage.getItem('angler-rag'))==='true'; ragToggle.addEventListener('change',()=>localStorage.setItem('docfish-rag',ragToggle.checked));
loadModels();
loadDocs();
