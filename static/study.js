"use strict";
// Reader controls share the original audio element, timeline and saved progress.
let batchPoll, segmentTimer, repeatTimer, segmentPlayback=null, dictationItem=null;
let dictationVersion=0;
const sentenceElements=()=>[...document.querySelectorAll('.prose .sentence')];
const alignedElements=()=>audioTimeline.filter(t=>document.querySelector(`.sentence[data-align-index="${t.index}"]`));
const timeLabel=s=>{s=Math.max(0,Math.floor(Number(s)||0));return `${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`;};
const playerIcon=name=>`<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">${{
 prev:'<path d="M6 5v14M18 5l-9 7 9 7z"/>',next:'<path d="M18 5v14M6 5l9 7-9 7z"/>',
 play:'<path d="m9 5 10 7-10 7z"/>',pause:'<path d="M8 5v14M16 5v14"/>',
 more:'<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
 pen:'<path d="m15 4 5 5M4 20l5-1L21 7l-5-5L4 14z"/>',
 headphones:'<path d="M4 15v-3a8 8 0 0 1 16 0v3M4 15v4h4v-6H6a2 2 0 0 0-2 2Zm16 0v4h-4v-6h2a2 2 0 0 1 2 2Z"/>',
 repeat:'<path d="m17 2 4 4-4 4M3 11V9a3 3 0 0 1 3-3h15M7 22l-4-4 4-4m14-1v2a3 3 0 0 1-3 3H3"/>'
 }[name]}</svg>`;

// Keep the close/navigation bar outside the scrollable analysis content.
const analysisDialog=$('translation');
const closeForm=analysisDialog.querySelector('form');
const analysisHeader=document.createElement('header');analysisHeader.className='dialog-bar';
analysisHeader.innerHTML=`<div class="dialog-title"><strong>句子精读</strong><small id="analysisPosition"></small></div><div class="dialog-toolbar"><button id="analysisListen" class="analysis-tool-action" aria-label="听本句并跟读" title="听本句并跟读">${playerIcon('headphones')}<span>听本句</span></button><button id="analysisDictate" class="analysis-tool-action" aria-label="本句听写" title="本句听写">${playerIcon('pen')}<span>听写</span></button><span class="toolbar-divider" aria-hidden="true"></span><div class="analysis-nav"><button id="analysisPrev" aria-label="上一句" title="上一句">${playerIcon('prev')}</button><button id="analysisNext" aria-label="下一句" title="下一句">${playerIcon('next')}</button></div></div>`;
analysisHeader.querySelector('.dialog-toolbar').append(closeForm);
const analysisBody=document.createElement('div');analysisBody.id='analysisBody';analysisBody.className='dialog-body';
while(analysisDialog.firstChild)analysisBody.append(analysisDialog.firstChild);
analysisDialog.append(analysisHeader,analysisBody);
$('grammarAnalysis').closest('section').remove();
$('translated').closest('section').insertAdjacentHTML('afterend','<section class="analysis-section"><h3>重点生词</h3><ul id="sentenceVocabulary" class="analysis-list"></ul></section>');
function updateAnalysisNavigation(el){
 const items=sentenceElements(),i=items.indexOf(el);
 $('analysisPosition').textContent=`${i+1}/${items.length}`;
 $('analysisPrev').disabled=i<=0;$('analysisNext').disabled=i>=items.length-1;
 const hasAudio=!!audioTimeline.find(t=>String(t.index)===el.dataset.alignIndex);
 $('analysisListen').disabled=!hasAudio;$('analysisDictate').disabled=!hasAudio;
}
function moveAnalysis(delta){
 const items=sentenceElements(),i=items.indexOf(document.querySelector('.sentence.selected'));
 const el=items[i+delta];if(!el)return;
 el.scrollIntoView({block:'center'});run(()=>translateSentence(el));
}
$('analysisPrev').onclick=()=>moveAnalysis(-1);$('analysisNext').onclick=()=>moveAnalysis(1);
function selectedAlignment(){return audioTimeline.find(t=>String(t.index)===document.querySelector('.sentence.selected')?.dataset.alignIndex);}
$('analysisListen').onclick=()=>playSegment(selectedAlignment());
$('analysisDictate').onclick=()=>openDictation(selectedAlignment());

// Compact custom player: no browser-native transport UI.
$('audio').controls=false;$('audio').hidden=true;
$('audioHint').hidden=true;$('syncStatus').hidden=true;
$('dock').firstElementChild.hidden=true;
$('dock').insertAdjacentHTML('beforeend',`<div class="player-controls">
 <div class="transport"><button id="audioPrev" aria-label="播放上一句" title="上一句">${playerIcon('prev')}</button><button id="audioToggle" class="primary" aria-label="播放" title="播放">${playerIcon('play')}</button><button id="audioNext" aria-label="播放下一句" title="下一句">${playerIcon('next')}</button></div>
 <div class="seek-row"><small id="audioElapsed">0:00</small><div class="seek-track"><input id="audioSeek" type="range" min="0" max="0" step="0.1" value="0" aria-label="音频播放进度"><output id="seekTooltip" hidden></output></div><small id="audioDuration">0:00</small></div>
 <div id="playerExtras"><button id="desktopDictate" class="desktop-action">${playerIcon('pen')}<span>听写</span></button><button id="desktopRepeat" class="desktop-action" aria-expanded="false" aria-controls="repeatPanel">${playerIcon('repeat')}<span>跟读</span></button><button id="playerMore" aria-label="更多播放选项" title="更多" aria-expanded="false" aria-controls="playerMenu">${playerIcon('more')}</button></div>
 <div id="playerMenu" class="player-popover" hidden><button id="audioDictate">${playerIcon('pen')}原音听写</button><button id="menuRepeat">${playerIcon('repeat')}跟读设置</button></div>
 <div id="repeatPanel" class="player-popover" hidden><div class="popover-heading"><strong>单句跟读</strong><button id="repeatClose" aria-label="关闭跟读设置">×</button></div><label>播放次数 <select id="repeatCount"><option value="1">1 次</option><option value="2">2 次</option><option value="3">3 次</option></select></label><label>跟读停顿 <select id="repeatGap"><option value="0">无</option><option value="2">2 秒</option><option value="4">4 秒</option><option value="6">6 秒</option></select></label><small>选句后生效；最后一遍结束自动暂停。</small></div>
 </div>`);
const rateLabel=$('rate').closest('label');
rateLabel.childNodes.forEach(node=>{if(node.nodeType===Node.TEXT_NODE)node.textContent='';});
$('rate').setAttribute('aria-label','播放速度');rateLabel.className='player-rate';
$('playerExtras').before(rateLabel);$('playerMenu').append($('realignBtn'));
function closePlayerPanels(restoreFocus=false){
 const wasOpen=!$('playerMenu').hidden||!$('repeatPanel').hidden;
 $('playerMenu').hidden=true;$('repeatPanel').hidden=true;
 $('playerMore').setAttribute('aria-expanded','false');$('desktopRepeat').setAttribute('aria-expanded','false');
 if(restoreFocus&&wasOpen)$('playerMore').focus();
}
function openRepeatPanel(){closePlayerPanels();$('repeatPanel').hidden=false;$('desktopRepeat').setAttribute('aria-expanded','true');$('repeatCount').focus();}
$('playerMore').onclick=()=>{const opening=$('playerMenu').hidden;closePlayerPanels();$('playerMenu').hidden=!opening;$('playerMore').setAttribute('aria-expanded',String(opening));if(opening)$('playerMenu').querySelector('button:not(:disabled)')?.focus();};
$('desktopRepeat').onclick=()=>{if($('repeatPanel').hidden)openRepeatPanel();else closePlayerPanels();};
$('menuRepeat').onclick=openRepeatPanel;$('repeatClose').onclick=()=>closePlayerPanels(true);
$('desktopDictate').onclick=()=>$('audioDictate').click();
$('realignBtn').addEventListener('click',()=>closePlayerPanels());
document.addEventListener('pointerdown',event=>{if(!$('playerExtras').contains(event.target)&&!$('playerMenu').contains(event.target)&&!$('repeatPanel').contains(event.target))closePlayerPanels();});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&(!$('playerMenu').hidden||!$('repeatPanel').hidden)){closePlayerPanels(true);event.preventDefault();}});
document.addEventListener('focusin',event=>{if(!$('dock').contains(event.target))closePlayerPanels();});
let seekTooltipTimer;
function showSeekTime(){clearTimeout(seekTooltipTimer);$('seekTooltip').textContent=`${timeLabel($('audioSeek').value)} / ${timeLabel($('audio').duration)}`;$('seekTooltip').hidden=false;}
function hideSeekTime(){clearTimeout(seekTooltipTimer);seekTooltipTimer=setTimeout(()=>$('seekTooltip').hidden=true,800);}
$('audioSeek').addEventListener('pointerdown',showSeekTime);
$('audioSeek').addEventListener('input',showSeekTime);
$('audioSeek').addEventListener('focus',showSeekTime);
$('audioSeek').addEventListener('blur',hideSeekTime);
$('audioSeek').addEventListener('change',hideSeekTime);
document.addEventListener('pointerup',hideSeekTime);
document.addEventListener('pointercancel',hideSeekTime);
function updatePlayer(){
 const audio=$('audio'),valid=Number.isFinite(audio.duration)&&audio.duration>0;
 $('audioSeek').max=valid?audio.duration:0;$('audioSeek').value=audio.currentTime||0;$('audioSeek').disabled=!valid;
 $('audioSeek').style.setProperty('--played',`${valid?Math.min(100,Math.max(0,audio.currentTime/audio.duration*100)):0}%`);
 $('audioElapsed').textContent=timeLabel(audio.currentTime);$('audioDuration').textContent=timeLabel(audio.duration);
 $('audioSeek').setAttribute('aria-valuetext',`${timeLabel(audio.currentTime)} / ${timeLabel(audio.duration)}`);
 const playing=!!(!audio.paused||repeatTimer),label=playing?'暂停':'播放';
 if($('audioToggle').getAttribute('aria-label')!==label){$('audioToggle').innerHTML=playerIcon(playing?'pause':'play');$('audioToggle').setAttribute('aria-label',label);$('audioToggle').title=label;}
 const items=alignedElements(),index=timelinePosition(items);
 $('audioPrev').disabled=!items.length||index===0;
 $('audioNext').disabled=!items.length||index===items.length-1;
 $('audioDictate').disabled=!items.length;
 $('desktopDictate').disabled=!items.length;
 if(analysisDialog.open){const el=document.querySelector('.sentence.selected');if(el)updateAnalysisNavigation(el);}
}
function timelinePosition(items=alignedElements()){
 const now=$('audio').currentTime*1000;
 for(let i=items.length-1;i>=0;i--)if(now>=items[i].begin_ms)return i;
 return -1;
}
function stopSegment(){clearTimeout(segmentTimer);clearTimeout(repeatTimer);repeatTimer=null;segmentPlayback=null;}
function scheduleBoundary(){
 clearTimeout(segmentTimer);
 if(!segmentPlayback||$('audio').paused||repeatTimer)return;
 const remaining=segmentPlayback.item.end_ms/1000-$('audio').currentTime;
 segmentTimer=setTimeout(checkBoundary,Math.max(10,remaining*1000/$('audio').playbackRate));
}
function checkBoundary(){
 const state=segmentPlayback;if(!state||$('audio').paused||repeatTimer)return;
 if($('audio').currentTime<state.item.end_ms/1000-.025){scheduleBoundary();return;}
 $('audio').pause();clearTimeout(segmentTimer);
 if(--state.remaining>0){
  repeatTimer=setTimeout(()=>{repeatTimer=null;if(segmentPlayback!==state)return;$('audio').currentTime=state.item.begin_ms/1000;run(()=>$('audio').play());},Number($('repeatGap').value)*1000);
 }else segmentPlayback=null;
 updatePlayer();
}
function playSegment(item){
 if(!item){notice('本句还没有可用的原音时间轴，请等待对齐完成。');return;}
 stopSegment();segmentPlayback={item,remaining:Number($('repeatCount').value)};
 $('audio').currentTime=item.begin_ms/1000;
 run(async()=>{try{await $('audio').play();scheduleBoundary();}catch(e){stopSegment();throw e;}});
 if(item.estimated)notice('本句时间为估算，音频边界可能略有偏差。');
}
function moveAudio(delta){const items=alignedElements(),index=timelinePosition(items);playSegment(items[index<0?0:index+delta]);}
$('audioPrev').onclick=()=>moveAudio(-1);$('audioNext').onclick=()=>moveAudio(1);
$('audioToggle').onclick=()=>{const playing=!$('audio').paused||repeatTimer;stopSegment();if(playing)$('audio').pause();else run(()=>$('audio').play());updatePlayer();};
$('audioSeek').oninput=()=>{stopSegment();$('audio').currentTime=Number($('audioSeek').value);updatePlayer();};
for(const event of ['timeupdate','loadedmetadata','durationchange','play','pause','seeked','ended'])$('audio').addEventListener(event,updatePlayer);
for(const event of ['play','seeked','ratechange'])$('audio').addEventListener(event,scheduleBoundary);
$('audio').addEventListener('timeupdate',checkBoundary);
$('audio').addEventListener('pause',()=>clearTimeout(segmentTimer));
$('audio').addEventListener('ended',()=>{if(segmentPlayback){const item=segmentPlayback.item,remaining=segmentPlayback.remaining-1;stopSegment();if(remaining>0){segmentPlayback={item,remaining};repeatTimer=setTimeout(()=>{repeatTimer=null;$('audio').currentTime=item.begin_ms/1000;run(()=>$('audio').play());},Number($('repeatGap').value)*1000);}}});
new ResizeObserver(()=>document.documentElement.style.setProperty('--dock-height',$('dock').hidden?'0px':`${$('dock').getBoundingClientRect().height}px`)).observe($('dock'));
new MutationObserver(()=>{
 const text=$('syncStatus').textContent;
 $('realignBtn').textContent=/正在/.test(text)?(/\d+%/.exec(text)?.[0]||'对齐中…'):'重新生成逐句对齐';
 $('realignBtn').title=text;updatePlayer();
 if(/失败|不可用/.test(text))notice(text);
}).observe($('syncStatus'),{childList:true,characterData:true,subtree:true});

// Batch queue survives navigation; polling belongs only to the visible article.
function initializeArticleStudy(){
 clearTimeout(batchPoll);
 document.querySelector('.meta').insertAdjacentHTML('afterend','<div class="batch-bar"><button id="batchStart">一键分析全文</button><button id="batchPause" hidden>暂停</button><small id="batchStatus" role="status">按顺序生成并缓存；会调用已配置的 AI 服务。</small></div>');
 $('batchStart').onclick=()=>run(async()=>{const id=article.id,version=routeVersion;$('batchStart').disabled=true;try{const result=await api('/api/sentences/batch',{article_id:id,sentences:sentenceElements().map(el=>el.textContent.trim())});if(version===routeVersion){renderBatch(result);pollBatch(id,version);}}catch(e){if(version===routeVersion)$('batchStart').disabled=false;throw e;}});
 $('batchPause').onclick=()=>run(async()=>{const version=routeVersion;const result=await api(`/api/sentences/batch/${encodeURIComponent(article.id)}/pause`,{});if(version===routeVersion)renderBatch(result);});
 pollBatch(article.id,routeVersion);
}
function renderBatch(job){
 if(!$('batchStatus'))return;
 $('batchStart').disabled=job.busy;$('batchPause').hidden=job.state!=='running';
 $('batchStart').textContent=job.state==='idle'?'一键分析全文':job.state==='completed'?'检查并补齐解析':'继续 / 重试失败句';
 const labels={running:'正在分析',paused:job.busy?'正在暂停（等待当前句完成）':'已暂停',completed:'已完成',partial:'部分失败'};
 if(job.state!=='idle')$('batchStatus').textContent=`${labels[job.state]||job.state} · ${job.completed}/${job.total}${job.failed?` · ${job.failed} 句失败：${job.error}`:''}`;
}
async function pollBatch(id,version){
 clearTimeout(batchPoll);
 try{const job=await api(`/api/sentences/batch/${encodeURIComponent(id)}`);if(version!==routeVersion)return;renderBatch(job);if(job.busy||job.state==='running')batchPoll=setTimeout(()=>pollBatch(id,version),1800);}
 catch(e){if(version===routeVersion&&$('batchStatus')){$('batchStatus').textContent=`进度读取失败：${e.message}，正在重试…`;batchPoll=setTimeout(()=>pollBatch(id,version),5000);}}
}

// Dictation uses the existing word-level comparison and saves practice history.
document.body.insertAdjacentHTML('beforeend',`<dialog id="dictation"><header class="dialog-bar"><span>原音听写 <small id="dictationPosition"></small></span><button id="dictationClose" class="close" aria-label="关闭听写">×</button></header><div class="dialog-body"><p>先听原音，输入听到的英文，再核对。英文原句在提交前隐藏。</p><div class="study-actions"><button id="dictationPrev">上一句</button><button id="dictationPlay">播放本句</button><button id="dictationNext">下一句</button></div><label for="dictationAnswer">我的听写</label><textarea id="dictationAnswer" rows="5" spellcheck="false" autocapitalize="off" autocomplete="off"></textarea><button id="dictationCheck" class="primary">核对并保存</button><p id="dictationStatus" role="status"></p><div id="dictationResult" hidden></div></div></dialog>`);
function openDictation(item){
 if(!item){notice('请等待原音逐句对齐完成后开始听写。');return;}
 if(analysisDialog.open)analysisDialog.close();
 dictationItem=item;++dictationVersion;stopSegment();$('audio').pause();
 $('dictationAnswer').value='';$('dictationResult').hidden=true;$('dictationResult').textContent='';$('dictationStatus').textContent='';$('dictationCheck').disabled=false;
 const items=alignedElements(),i=items.indexOf(item);$('dictationPosition').textContent=`${i+1}/${items.length}`;
 $('dictationPrev').disabled=i<=0;$('dictationNext').disabled=i>=items.length-1;
 if(!$('dictation').open)$('dictation').showModal();$('dictation').querySelector('.dialog-body').scrollTop=0;playSegment(item);
}
$('audioDictate').onclick=()=>{closePlayerPanels();const items=alignedElements();openDictation(items[Math.max(0,timelinePosition(items))]);};
$('dictationClose').onclick=()=>$('dictation').close();
$('dictation').addEventListener('close',()=>{++dictationVersion;stopSegment();$('audio').pause();dictationItem=null;});
$('dictationPlay').onclick=()=>playSegment(dictationItem);
for(const [id,delta] of [['dictationPrev',-1],['dictationNext',1]])$(id).onclick=()=>{const items=alignedElements();openDictation(items[items.indexOf(dictationItem)+delta]);};
$('dictationCheck').onclick=async()=>{
 if(!dictationItem||!article)return;
 if(!$('dictationAnswer').value.trim()){$('dictationStatus').textContent='请先输入听到的英文。';return;}
 const version=dictationVersion,el=document.querySelector(`.sentence[data-align-index="${dictationItem.index}"]`);
 $('dictationCheck').disabled=true;$('dictationStatus').textContent='正在核对并保存…';
 try{const data=await api('/api/v1/listening/attempts',{article_id:article.id,sentence_index:dictationItem.index,sentence_text:el.textContent.trim(),stage:'dictation',answer:$('dictationAnswer').value});if(version!==dictationVersion)return;
  const r=data.result;$('dictationStatus').textContent=`匹配度 ${r.score}% · 漏词 ${r.counts.missing} · 错词 ${r.counts.wrong} · 多词 ${r.counts.extra}，已保存。`;
  $('dictationResult').innerHTML=`<h3>原文对照</h3><p lang="en">${esc(el.textContent)}</p><p class="dictation-diff" lang="en">${r.segments.map(s=>s.kind==='correct'?`<span>${esc(s.expected)}</span>`:`<mark>${s.actual?`<del>${esc(s.actual)}</del> `:''}${esc(s.expected)||'（多写）'}</mark>`).join(' ')}</p><small>标记处为漏词、错词或多写；忽略大小写与标点。</small>`;$('dictationResult').hidden=false;
 }catch(e){if(version===dictationVersion)$('dictationStatus').textContent=e.message;}finally{if(version===dictationVersion)$('dictationCheck').disabled=false;}
};
window.addEventListener('hashchange',()=>{closePlayerPanels();$('seekTooltip').hidden=true;clearTimeout(batchPoll);stopSegment();if(analysisDialog.open)analysisDialog.close();if($('dictation').open)$('dictation').close();});
updatePlayer();
