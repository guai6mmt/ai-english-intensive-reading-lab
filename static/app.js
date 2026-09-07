"use strict";
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let sources=[], article=null, selected=null, routeVersion=0, translationVersion=0, settings={}, audioMedia=null, lastAudioSave=0;
let audioTimeline=[], playingSentence=null, syncVersion=0;
async function api(url, body, method) {
 const response=await fetch(url, body === undefined ? {} : {method:method||'POST', ...(body instanceof FormData ? {body} : {headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
 const data=await response.json().catch(()=>({})); if(!response.ok) throw new Error(typeof data.detail==='string'?data.detail:'操作失败，请稍后重试。'); return data;
}
let noticeTimer;
function notice(message){$('notice').textContent=message;clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>$('notice').textContent='',6500);}
async function run(fn){try{await fn();}catch(e){notice(e.message);}}
function splitSentences(text){if(typeof Intl.Segmenter==='function')return [...new Intl.Segmenter('en',{granularity:'sentence'}).segment(text)].map(s=>s.segment);return text.match(/[^.!?]+(?:[.!?]+["'”’)]*\s*|$)/g)||[text];}
async function library(){sources=(await api('/api/library')).library.sources||[];}
function renderLibrary(){
 const remembered=localStorage.getItem('library-magazine')||'all';
 $('main').innerHTML=`<div class="intro"><div><small>YOUR READING SHELF</small><h1>今天，读一篇。</h1><p>先选杂志，再选板块，很快回到想读的内容。</p></div><button class="primary" id="importBtn">导入文章</button></div><section class="library-filters" aria-label="筛选文章"><label>杂志<select id="magazineFilter"><option value="all">全部杂志</option>${sources.map(source=>`<option value="${esc(source.id)}">${esc(source.filename)}</option>`).join('')}</select></label><label>板块<select id="sectionFilter"></select></label><label class="filter-search">搜索<input id="search" aria-label="搜索文章" placeholder="输入文章标题…"></label><button id="clearFilters">清除筛选</button></section><p class="library-result" id="libraryResult" aria-live="polite"></p><div id="libraryList"></div>`;
 $('magazineFilter').value=sources.some(source=>source.id===remembered)?remembered:'all';
 populateSections();
 $('importBtn').onclick=()=>$('import').showModal();
 $('magazineFilter').onchange=()=>{localStorage.setItem('library-magazine',$('magazineFilter').value);localStorage.setItem('library-section','all');populateSections(true);renderCards();};
 $('sectionFilter').onchange=()=>{localStorage.setItem('library-section',$('sectionFilter').value);renderCards();};
 $('search').oninput=renderCards;
 $('clearFilters').onclick=()=>{$('magazineFilter').value='all';localStorage.removeItem('library-magazine');localStorage.removeItem('library-section');$('search').value='';populateSections(true);renderCards();};
 renderCards();
}
function populateSections(reset=false){
 const sourceId=$('magazineFilter').value;const selected=reset?'all':(localStorage.getItem('library-section')||'all');
 const relevant=sourceId==='all'?sources:sources.filter(source=>source.id===sourceId);
 const sections=[...new Set(relevant.flatMap(source=>source.articles.map(item=>item.section||'未分类')))];
 $('sectionFilter').innerHTML=`<option value="all">全部板块</option>${sections.map(section=>`<option value="${esc(section)}">${esc(section)}</option>`).join('')}`;
 $('sectionFilter').value=sections.includes(selected)?selected:'all';
}
function renderCards(){const q=$('search').value.toLowerCase().trim();const sourceId=$('magazineFilter').value;const section=$('sectionFilter').value;let count=0;const visibleSources=sourceId==='all'?sources:sources.filter(source=>source.id===sourceId);$('libraryList').innerHTML=visibleSources.map(source=>{const items=source.articles.filter(a=>(section==='all'||(a.section||'未分类')===section)&&`${a.title} ${a.section} ${source.filename}`.toLowerCase().includes(q));count+=items.length;return items.length?`<section class="source"><h2 class="source-title"><span>${esc(source.filename)}</span><small>${items.length} 篇</small></h2><div class="cards">${items.map(a=>`<a class="card" href="#article/${encodeURIComponent(a.id)}"><small>${esc(a.section||'未分类')}</small><h3 lang="en">${esc(a.title)}</h3><small>${a.linked_media?'◉ 配有原版音频':'文章阅读'}${a.last_opened_at?' · 已读过':''}</small></a>`).join('')}</div></section>`:'';}).join('');$('libraryResult').textContent=`找到 ${count} 篇文章`;if(!count)$('libraryList').innerHTML=`<div class="empty">${sources.length?'当前筛选条件下没有文章。':'书架还是空的。导入第一篇文章，开始阅读。'}</div>`;}
async function persistAudio(){const media=audioMedia;if(!media||!Number.isFinite($('audio').currentTime))return;await api(`/api/v1/media/items/${encodeURIComponent(media.id)}/progress`,{position_ms:Math.round($('audio').currentTime*1000),playback_rate:$('audio').playbackRate,completed:$('audio').ended},'PUT');}
function delay(ms){return new Promise(resolve=>setTimeout(resolve,ms));}
function installAudioTimeline(result){
 const entries=(result?.alignments||[]).filter(item=>Number.isFinite(Number(item.begin_ms))&&Number.isFinite(Number(item.end_ms))&&Number(item.end_ms)>Number(item.begin_ms)).map(item=>({...item,begin_ms:Number(item.begin_ms),end_ms:Number(item.end_ms)})).sort((a,b)=>a.begin_ms-b.begin_ms);
 audioTimeline=entries;
 const precise=entries.filter(item=>!item.estimated).length;const estimated=entries.length-precise;
 $('syncStatus').textContent=entries.length?`逐句高亮已就绪 · ${precise} 句精确${estimated?`，${estimated} 句补间`:''}`:'当前音频没有可用的句子时间轴';
 updateAudioHighlight();
}
async function loadAudioTimeline(articleId,version){
 const requestVersion=++syncVersion;
 const started=await api(`/api/articles/${encodeURIComponent(articleId)}/listening/original-audio/start`,{refresh:false,enable_words:true});
 if(version!==routeVersion||requestVersion!==syncVersion)return;
 if(started.result){installAudioTimeline(started.result);return;}
 if(!started.task_id)throw new Error('服务器没有返回对齐任务');
 for(let attempt=0;attempt<150;attempt+=1){
  await delay(2000);if(version!==routeVersion||requestVersion!==syncVersion)return;
  const status=await api(`/api/articles/${encodeURIComponent(articleId)}/listening/original-audio/status/${encodeURIComponent(started.task_id)}`);
  if(status.msg)$('syncStatus').textContent=`正在准备逐句高亮 · ${status.pct||0}%`;
  if(status.error)throw new Error(status.error);
  if(status.result){installAudioTimeline(status.result);return;}
 }
 throw new Error('准备时间超过 5 分钟，请稍后重新打开文章');
}
function updateAudioHighlight(){
 if(!audioTimeline.length||!article)return;
 const now=$('audio').currentTime*1000;
 const current=audioTimeline.find(item=>now>=item.begin_ms&&now<item.end_ms)||null;
 const element=current?document.querySelector(`.sentence[data-align-index="${current.index}"]`):null;
 if(element===playingSentence)return;
 playingSentence?.classList.remove('playing','estimated');playingSentence=element;
 if(!element)return;
 element.classList.add('playing');if(current.estimated)element.classList.add('estimated');
 if(!$('audio').paused){const box=element.getBoundingClientRect();const top=95,bottom=window.innerHeight-150;if(box.top<top||box.bottom>bottom)element.scrollIntoView({behavior:'smooth',block:'center'});}
}
async function openArticle(id, version){
 const data=await api(`/api/articles/${encodeURIComponent(id)}`);if(version!==routeVersion)return;
 article=data.article;const a=article;let sentenceItems=[];try{sentenceItems=(await api(`/api/articles/${encodeURIComponent(id)}/listening/sentences`)).sentences||[];}catch(_error){}
 const paragraphs=(a.cleaned_paragraphs||a.paragraphs||[]).map((text,index)=>({text,index})).filter(({text,index})=>index!==0||text.trim()!==a.title?.trim());
 const byParagraph=new Map();for(const item of sentenceItems){const list=byParagraph.get(item.para)||[];list.push(item);byParagraph.set(item.para,list);}
 const renderLoose=text=>splitSentences(text).map(part=>part.trim()?`<span class="sentence" role="button" tabindex="0">${esc(part)}</span>`:esc(part)).join('');
 const renderParagraph=(text,paraIndex)=>{let cursor=0,html='';for(const item of (byParagraph.get(paraIndex)||[])){const position=text.indexOf(item.text,cursor);if(position<0)continue;html+=renderLoose(text.slice(cursor,position));html+=`<span class="sentence" role="button" tabindex="0" data-align-index="${item.index}">${esc(item.text)}</span>`;cursor=position+item.text.length;}return html+renderLoose(text.slice(cursor));};
 $('main').innerHTML=`<article class="reader"><a class="muted" href="#">← 文章库</a><h1 lang="en">${esc(a.title)}</h1><div class="meta">${esc(a.section||'英语阅读')} · 点击句子查看 AI 译文、长难句拆分与语法解析${a.linked_media?' · 播放原版音频时自动高亮当前句':' · 暂无配套音频'}</div><div class="prose" lang="en">${paragraphs.map(({text,index})=>`<p>${renderParagraph(text,index)}</p>`).join('')}</div></article>`;
 const last=localStorage.getItem('read-position:'+id);if(last){const els=document.querySelectorAll('.sentence');els[Number(last)]?.scrollIntoView({block:'center'});}
 $('main').onclick=e=>{const el=e.target.closest('.sentence');if(el)run(()=>translateSentence(el));};
 $('main').onkeydown=e=>{if(e.target.matches('.sentence')&&['Enter',' '].includes(e.key)){e.preventDefault();run(()=>translateSentence(e.target));}};
 if(a.linked_media){audioMedia=a.linked_media;$('audioTitle').textContent='原版音频';$('audioHint').textContent=a.linked_media.title;$('syncStatus').textContent='正在读取句子时间轴…';$('dock').hidden=false;$('audio').src=a.linked_media.stream_url;
  const mediaId=audioMedia.id;try{const saved=await api(`/api/v1/media/items/${encodeURIComponent(mediaId)}`);if(version!==routeVersion)return;const progress=saved.item?.progress||saved.progress||saved.item||{};const restore=()=>{if(audioMedia?.id!==mediaId)return;$('audio').currentTime=(progress.position_ms||0)/1000;const requested=Number(progress.playback_rate)||1;const choices=[...$('rate').options].map(option=>Number(option.value));const restored=choices.reduce((best,value)=>Math.abs(value-requested)<Math.abs(best-requested)?value:best,1);$('audio').playbackRate=restored;$('rate').value=String(restored);};if($('audio').readyState>=1)restore();else $('audio').addEventListener('loadedmetadata',restore,{once:true});}catch(e){notice('音频可播放，但历史播放进度读取失败。');}
  loadAudioTimeline(id,version).catch(error=>{if(version===routeVersion)$('syncStatus').textContent=`逐句高亮暂不可用：${error.message}`;});
 }
}
async function translateSentence(el){
 document.querySelector('.sentence.selected')?.classList.remove('selected');el.classList.add('selected');
 localStorage.setItem('read-position:'+article.id,[...document.querySelectorAll('.sentence')].indexOf(el));
 const spec={article_id:article.id,sentence:el.textContent.trim()};selected=spec;const version=++translationVersion;
 $('selectedSentence').textContent=spec.sentence;$('analysisStatus').textContent='正在结合上下文拆解句子与分析语法…';$('analysisResult').hidden=true;$('saveSentence').disabled=true;$('saveSentence').textContent='收藏到句子本';$('translation').showModal();
 try{const result=await api('/api/sentences/translate',spec);if(version!==translationVersion)return;$('translated').textContent=result.translation;$('sentenceStructure').textContent=result.structure;$('clauseAnalysis').innerHTML=result.clauses.map(item=>`<li><p lang="en">${esc(item.text)}</p><strong>${esc(item.role)}</strong><span>${esc(item.explanation)}</span></li>`).join('');$('grammarAnalysis').innerHTML=result.grammar_points.map(item=>`<li><strong>${esc(item.point)}</strong><code lang="en">${esc(item.evidence)}</code><span>${esc(item.explanation)}</span></li>`).join('');$('analysisStatus').textContent='';$('analysisResult').hidden=false;$('saveSentence').disabled=false;}catch(e){if(version===translationVersion)$('analysisStatus').textContent=e.message;}
}
async function renderSentences(due=false,version=routeVersion){
 const data=await api('/api/vocabulary?kind=sentence&limit=2000'+(due?'&due=true':''));if(version!==routeVersion)return;
 $('main').innerHTML=`<div class="intro"><div><small>SENTENCE NOTEBOOK</small><h1>留住好句子。</h1><p>先理解英文，再展开译文。每一次回顾，都会更熟悉。</p></div></div><div class="view-switch"><button id="allSentences" ${due?'':'class="primary"'}>全部句子</button><button id="dueSentences" ${due?'class="primary"':''}>今日复习</button><a class="button" href="/api/vocabulary/export/json">备份学习记录</a></div><div id="sentenceList">${data.items.map(item=>{const ctx=item.context_items?.[0];return `<section class="review-card"><p class="english" lang="en">${esc(item.term)}</p><small>${ctx?.article_id?`<a href="#article/${encodeURIComponent(ctx.article_id)}">${esc(ctx.source||'回到原文')} ↗</a>`:'来源文章已不可用'} · 下次复习 ${esc(item.due_at)}</small><details><summary>查看译文</summary><p>${esc(item.translation)}</p></details><div class="review-actions">${[['again','还不熟'],['good','记住了'],['easy','很熟悉']].map(([rating,label])=>`<button data-review="${esc(item.id)}" data-rating="${rating}">${label}</button>`).join('')}<button data-delete="${esc(item.id)}">移出句子本</button></div></section>`;}).join('')||'<div class="empty">'+(due?'今天的复习已完成。也可以在全部句子里自由回顾。':'阅读时点击句子，再收藏到这里。')+'</div>'}</div>`;
 $('allSentences').onclick=()=>run(()=>renderSentences(false));$('dueSentences').onclick=()=>run(()=>renderSentences(true));
 $('main').onclick=e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.review)run(async()=>{b.disabled=true;try{await api('/api/vocabulary/review/'+encodeURIComponent(b.dataset.review),{rating:b.dataset.rating});await renderSentences(due);notice('复习已记录');}finally{b.disabled=false;}});if(b.dataset.delete&&confirm('将这个句子移出句子本？'))run(async()=>{await api('/api/vocabulary/'+encodeURIComponent(b.dataset.delete),{},'DELETE');await renderSentences(due);});};
}
async function route(){const version=++routeVersion;++translationVersion;++syncVersion;await persistAudio().catch(()=>{});if(version!==routeVersion)return;$('audio').pause();$('audio').removeAttribute('src');$('audio').load();playingSentence?.classList.remove('playing','estimated');playingSentence=null;audioTimeline=[];audioMedia=null;article=null;$('dock').hidden=true;$('main').onclick=null;$('main').onkeydown=null;$('main').innerHTML='<p class="empty">正在打开…</p>';window.scrollTo(0,0);const hash=location.hash.slice(1);if(hash.startsWith('article/'))await openArticle(decodeURIComponent(hash.slice(8)),version);else if(hash==='sentences')await renderSentences(false,version);else{await library();if(version===routeVersion)renderLibrary();}}
function providerFields(){const p=settings.providers[$('provider').value];$('model').value=p.model;$('baseUrl').value=p.base_url;$('configured').textContent=p.configured?'已配置密钥；留空即可保留。':'尚未配置密钥。';$('apiKey').value='';}
function alignmentFields(){const asr=settings.providers.qwen.asr,oss=settings.oss;$('asrKey').value='';$('asrModel').value=asr.model;$('asrBaseUrl').value=asr.base_url;$('ossKeyId').value='';$('ossKeySecret').value='';$('ossBucket').value=oss.bucket||'';$('ossEndpoint').value=oss.endpoint||'';$('alignmentConfigured').textContent=asr.configured&&oss.configured?'逐句高亮服务已配置。':'配置完整后可自动生成原版音频时间轴。';}
$('settingsBtn').onclick=()=>run(async()=>{settings=await api('/api/settings');$('provider').value=settings.primary_provider;providerFields();alignmentFields();$('settings').showModal();});$('provider').onchange=providerFields;
$('aiSettings').onsubmit=e=>{e.preventDefault();run(async()=>{const p=$('provider').value;settings=await api('/api/settings',{text_provider:p,[p+'_api_key']:$('apiKey').value,[p+'_model']:$('model').value,[p+'_base_url']:$('baseUrl').value,dashscope_api_key:$('asrKey').value,qwen_asr_model:$('asrModel').value,qwen_asr_base_url:$('asrBaseUrl').value,oss_access_key_id:$('ossKeyId').value,oss_access_key_secret:$('ossKeySecret').value,oss_bucket:$('ossBucket').value,oss_endpoint:$('ossEndpoint').value});providerFields();alignmentFields();notice('设置已保存');});};
$('saveSentence').onclick=()=>run(async()=>{const spec=selected;const version=translationVersion;$('saveSentence').disabled=true;try{await api('/api/sentences',spec);if(version===translationVersion)$('saveSentence').textContent='已收藏';notice('已加入句子本');}catch(e){if(version===translationVersion)$('saveSentence').disabled=false;throw e;}});
$('translation').addEventListener('close',()=>{++translationVersion;});
$('importForm').onsubmit=e=>{e.preventDefault();run(async()=>{const file=$('articleFile').files[0],zip=$('audioFile').files[0];if(zip&&!file.name.toLowerCase().endsWith('.epub'))throw new Error('配套音频 ZIP 请与 EPUB 一起导入。');const form=new FormData();form.append(zip?'epub':'file',file);if(zip)form.append('audio_zip',zip);const b=$('importForm').querySelector('button');b.disabled=true;$('importStatus').textContent='正在上传并导入，请保持页面打开…';try{const result=await api(zip?'/api/issues/import':'/api/upload',form);$('importStatus').textContent=result.audio?.error?`文章已导入，音频失败：${result.audio.error}`:'导入完成。'+(zip?'请在设置 → 音频管理中检查并确认配对。':'');await library();if(!location.hash||location.hash==='#')renderLibrary();}catch(e){$('importStatus').textContent=e.message;}finally{b.disabled=false;}});};
$('fontSize').value=localStorage.getItem('reading-size')||'19';document.documentElement.style.setProperty('--reading-size',$('fontSize').value+'px');$('fontSize').onchange=()=>{localStorage.setItem('reading-size',$('fontSize').value);document.documentElement.style.setProperty('--reading-size',$('fontSize').value+'px');};
$('rate').onchange=()=>{$('audio').playbackRate=Number($('rate').value);run(persistAudio);};$('audio').ontimeupdate=()=>{updateAudioHighlight();if(Date.now()-lastAudioSave>10000){lastAudioSave=Date.now();persistAudio().catch(()=>{});}};$('audio').onseeked=updateAudioHighlight;$('audio').onpause=()=>persistAudio().catch(()=>{});$('audio').onended=()=>{updateAudioHighlight();persistAudio().catch(()=>{});};$('audio').onerror=()=>{if(audioMedia)notice('音频加载失败，请检查网络或音频管理中的文件。');};
$('logout').onclick=()=>run(()=>window.EnglishLabAuth.logout());window.addEventListener('hashchange',()=>run(route));document.addEventListener('visibilitychange',()=>{if(document.hidden)persistAudio().catch(()=>{});});run(async()=>{await window.EnglishLabAuth.ready;const oldId=new URLSearchParams(location.search).get('article');if(oldId&&!location.hash)history.replaceState(null,'','/#article/'+encodeURIComponent(oldId));await route();if(new URLSearchParams(location.search).has('first_setup'))$('settingsBtn').click();});
