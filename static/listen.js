/* ──────────────────────────────────────────────────────────────
   Listening Mode v2
   两阶段启动：
   Phase 1 — GET /listening/sentences（<1s）→ 立刻渲染文章 + 激活控制栏
   Phase 2 — POST /listening/prepare（10-30s）→ 后台跑 AI，完成后填充生词
   ────────────────────────────────────────────────────────────── */

const $ = (id) => document.getElementById(id);
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );

const RATES = [0.85, 1.0, 1.15, 1.3];

const state = {
  articleId: "",
  title: "",
  sentences: [],       // [{ index, para, text, vocab?, translation? }]
  analysisReady: false,
  currentIndex: 0,
  audioCache: new Map(),   // index -> Promise<url>
  currentAudio: null,
  alignedAudio: null,
  alignedReady: false,
  alignedLoading: false,
  alignedFailed: false,
  videoExporting: false,
  isPlaying: false,
  playbackRate: 1.0,
  fallbackActive: false,
  theme: "dark",
  provider: "",          // chosen TTS provider for the aligned timeline
  variants: [],          // [{ provider, label, model, voice, cached, configured }]
  stage: "blind",
  lastAttempt: null,
  practiceByIndex: new Map(),
  reviewQueue: [],
  reviewOpen: false,
  repeatCount: 1,
  repeatRemaining: 1,
  pauseMs: 0,
  sentenceLoop: false,
  segmentTargetIndex: null,
  transitionTimer: null,
  dictationDrafts: new Map(),
  playToken: 0,
};

// ── Helpers ──
async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = `${res.status}`;
    try { const d = await res.json(); msg = d.detail || d.message || msg; } catch {}
    throw new Error(msg);
  }
  return res.json();
}

function getId() {
  return new URLSearchParams(window.location.search).get("id") || "";
}

function getRequestedSentence() {
  const value = Number(new URLSearchParams(window.location.search).get("sentence"));
  return Number.isFinite(value) && value >= 0 ? value : null;
}

function applyTheme(t) {
  window.ELTheme?.apply("dark");
  state.theme = "dark";
}

function setSub(text) {
  const el = $("lpSub");
  if (el) el.textContent = text;
}

function setControls(enabled) {
  ["lpPrevBtn", "lpRepeatBtn", "lpPlayBtn", "lpNextBtn"].forEach((id) => {
    const btn = $(id);
    if (btn) btn.disabled = !enabled;
  });
}

// ── Progress bar ──
function updateProgress() {
  const n = state.sentences.length;
  if (!n) return;
  $("lpProgressIndex").textContent = String(state.currentIndex + 1);
  $("lpProgressTotal").textContent  = String(n);
  const pct = ((state.currentIndex + 1) / n) * 100;
  $("lpProgressFill").style.right = `${100 - pct}%`;
}

function updatePlayBtn() {
  $("lpPlayBtn").innerHTML = state.isPlaying
    ? '<svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="7" y="5" width="3.4" height="14" rx="1"/><rect x="13.6" y="5" width="3.4" height="14" rx="1"/></svg>'
    : '<svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5.5v13l11-6.5z"/></svg>';
}

// ── Vocab panel ──
function setVocabStatus(mode) {
  const el = $("lpVocabStatus");
  if (!el) return;
  el.className = "lp-vocab-status";
  if (mode === "analyzing") {
    el.classList.add("analyzing");
    el.textContent = "分析中";
  } else if (mode === "done") {
    el.classList.add("done");
    el.textContent = "✓";
    setTimeout(() => { el.textContent = ""; el.className = "lp-vocab-status"; }, 2500);
  } else {
    el.textContent = "";
  }
}

function renderVocab(idx) {
  const body = $("lpVocabBody");
  const s = state.sentences[idx];
  if (!s) return;

  // Still analyzing and no vocab yet — show inline waiting state
  if (!state.analysisReady && (!s.vocab || s.vocab.length === 0)) {
    body.innerHTML = `<div class="lp-vocab-hint">生词分析中，稍候…</div>`;
    return;
  }

  const items = (s.vocab || []).slice(0, 10);
  let html = items.length === 0
    ? `<div class="lp-vocab-hint">本句无标记生词</div>`
    : items.map((v) => `
        <div class="lp-vocab-item">
          <div class="lp-vocab-term">${esc(v.term)}</div>
          ${v.meaning ? `<div class="lp-vocab-meaning">${esc(v.meaning)}</div>` : ""}
        </div>`).join("");

  if (s.translation) {
    html += `
      <div class="lp-vocab-translation">
        <div class="lp-vocab-translation-label">中文译文</div>
        ${esc(s.translation)}
      </div>`;
  }
  body.innerHTML = html;
  body.scrollTop = 0;
}

// ── Article rendering ──
function renderArticle() {
  const container = $("lpArticle");
  const byPara = new Map();
  for (const s of state.sentences) {
    if (!byPara.has(s.para)) byPara.set(s.para, []);
    byPara.get(s.para).push(s);
  }
  const html = Array.from(byPara.entries())
    .sort((a, b) => a[0] - b[0])
    .map(([, list]) =>
      `<p class="lp-para">${list.map((s) =>
        `<span class="lp-sent${(state.practiceByIndex.get(s.index)?.review_score ?? 100) < 85 ? " weak" : ""}" data-idx="${s.index}">${esc(s.text)} </span>`
      ).join("")}</p>`
    ).join("");
  container.innerHTML = html;
}

function highlightSentence(idx) {
  document.querySelectorAll(".lp-sent").forEach((el) => {
    const i = Number(el.dataset.idx);
    el.classList.toggle("playing", i === idx);
    el.classList.toggle("read",    i < idx);
  });
  const target = document.querySelector(`.lp-sent[data-idx="${idx}"]`);
  if (target) target.scrollIntoView({ behavior: "smooth", block: "center" });
  renderPractice();
}

// ── Four-stage practice + review queue ──
function stageTitle(stage) {
  return ({ blind: "盲听", dictation: "逐句听写", correction: "对照纠错", shadowing: "跟读模仿" })[stage] || "精听训练";
}

function setPracticeStage(stage) {
  if (!["blind", "dictation", "correction", "shadowing"].includes(stage)) return;
  state.stage = stage;
  state.reviewOpen = false;
  $("lpRoot").dataset.stage = stage;
  document.querySelectorAll("#lpStageTabs [data-stage]").forEach((button) => {
    button.setAttribute("aria-selected", String(button.dataset.stage === stage));
  });
  renderPractice();
  if (stage === "dictation") setTimeout(() => $("lpDictationInput")?.focus(), 0);
}

function renderDiff(result) {
  const segments = result?.segments || [];
  if (!segments.length) return "";
  return `<div class="lp-diff">${segments.map((item) => {
    const label = item.kind === "extra"
      ? `+ ${item.actual}`
      : item.kind === "wrong"
        ? `${item.actual} → ${item.expected}`
        : item.expected || item.actual;
    return `<span class="${esc(item.kind)}">${esc(label)}</span>`;
  }).join("")}</div>`;
}

function renderPractice() {
  const body = $("lpPracticeBody");
  const sentence = state.sentences[state.currentIndex];
  if (!body || !sentence) return;
  if (state.reviewOpen) {
    body.innerHTML = `
      <h3>错句复习队列</h3>
      <p>${state.reviewQueue.length ? "优先复习到期或得分偏低的句子。" : "当前没有待复习错句。"}</p>
      <div class="lp-review-list">${state.reviewQueue.map((item, index) => `
        <button class="lp-review-item" data-review-index="${index}">
          <strong>${esc(item.sentence_text)}</strong>
          <small>${esc(item.article_title)} · ${Math.round(item.review_score ?? item.last_score)} 分 · ${item.due ? "已到期" : "薄弱句"}</small>
        </button>`).join("")}</div>`;
    return;
  }

  const progress = state.practiceByIndex.get(state.currentIndex);
  if (state.stage === "blind") {
    body.innerHTML = `
      <h3>1 · 盲听</h3>
      <p>正文已隐藏。先完整听懂语义，不急着逐词辨认；可用复读、倍速和单句循环。</p>
      ${progress ? `<p>本句上次得分：<strong>${Math.round(progress.last_score)}</strong> · 已练 ${progress.attempts} 次</p>` : ""}
      <div class="lp-practice-actions">
        <button data-practice-action="replay">播放当前句</button>
        <button class="primary" data-practice-action="dictation">听清后开始听写</button>
      </div>`;
    return;
  }
  if (state.stage === "dictation") {
    body.innerHTML = `
      <h3>2 · 逐句听写</h3>
      <p>输入你听到的英文。提交后系统会标出漏词、错词、多词和建议重点重听的连读片段。</p>
      <textarea class="lp-dictation-input" id="lpDictationInput" placeholder="Type what you hear…" spellcheck="false">${esc(state.dictationDrafts.get(state.currentIndex) || "")}</textarea>
      <div class="lp-practice-actions">
        <button data-practice-action="replay">再听一次</button>
        <button class="primary" data-practice-action="submit-dictation">提交并纠错</button>
      </div>`;
    return;
  }
  if (state.stage === "correction") {
    const attempt = state.lastAttempt?.index === state.currentIndex
      ? state.lastAttempt.result
      : progress?.last_result;
    if (!attempt?.segments?.length && Number(attempt?.score || 0) < 100) {
      body.innerHTML = `<h3>3 · 对照纠错</h3><p>请先完成本句听写，再查看逐词对照。</p><div class="lp-practice-actions"><button class="primary" data-practice-action="dictation">返回听写</button></div>`;
      return;
    }
    const counts = attempt?.counts || {};
    const focuses = attempt?.focus_phrases || [];
    body.innerHTML = `
      <h3>3 · 对照纠错</h3>
      <div class="lp-score"><strong>${Math.round(attempt?.score || 0)}</strong><span>漏词 ${counts.missing || 0} · 错词 ${counts.wrong || 0} · 多词 ${counts.extra || 0}</span></div>
      ${renderDiff(attempt)}
      <p><strong>原句：</strong>${esc(sentence.text)}</p>
      ${focuses.length ? `<p>建议关注连读 / 弱读：</p><div class="lp-focus-list">${focuses.map((item) => `<span>${esc(item)}</span>`).join("")}</div>` : ""}
      <div class="lp-practice-actions">
        <button data-practice-action="replay">对照重听</button>
        <button data-practice-action="retry">重新听写</button>
        <button class="primary" data-practice-action="shadowing">进入跟读</button>
      </div>`;
    return;
  }

  body.innerHTML = `
    <h3>4 · 跟读模仿</h3>
    <p>${esc(sentence.text)}</p>
    ${sentence.translation ? `<p>${esc(sentence.translation)}</p>` : ""}
    <p>先播放原声，紧跟节奏复述；完成后按流畅度自评，系统据此安排下次复习。</p>
    <div class="lp-practice-actions"><button data-practice-action="replay">播放原声</button></div>
    <div class="lp-rating" aria-label="跟读自评">
      ${[1, 2, 3, 4, 5].map((rating) => `<button data-shadow-rating="${rating}" title="${rating} 星">${rating}★</button>`).join("")}
    </div>`;
}

async function loadPracticeData() {
  try {
    const [articleData, reviewData] = await Promise.all([
      api(`/api/v1/listening/articles/${encodeURIComponent(state.articleId)}/practice`),
      api("/api/v1/listening/review?limit=40"),
    ]);
    state.practiceByIndex = new Map((articleData.items || []).map((item) => [Number(item.sentence_index), item]));
    state.reviewQueue = reviewData.items || [];
    $("lpReviewCount").textContent = String(state.reviewQueue.length);
    renderArticle();
    highlightSentence(state.currentIndex);
  } catch (err) {
    console.warn("practice data failed", err);
  }
}

async function submitDictation() {
  const sentence = state.sentences[state.currentIndex];
  const input = $("lpDictationInput");
  const answer = input?.value.trim() || "";
  if (!sentence || !answer) { input?.focus(); return; }
  state.dictationDrafts.set(state.currentIndex, answer);
  const data = await api("/api/v1/listening/attempts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      article_id: state.articleId,
      sentence_index: state.currentIndex,
      sentence_text: sentence.text,
      stage: "dictation",
      answer,
    }),
  });
  state.lastAttempt = { index: state.currentIndex, result: data.result };
  state.practiceByIndex.set(state.currentIndex, data.progress);
  state.dictationDrafts.delete(state.currentIndex);
  await refreshReviewQueue();
  renderArticle();
  setPracticeStage("correction");
  highlightSentence(state.currentIndex);
}

async function submitShadowing(rating) {
  const sentence = state.sentences[state.currentIndex];
  if (!sentence) return;
  const data = await api("/api/v1/listening/attempts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      article_id: state.articleId,
      sentence_index: state.currentIndex,
      sentence_text: sentence.text,
      stage: "shadowing",
      rating,
    }),
  });
  state.practiceByIndex.set(state.currentIndex, data.progress);
  await refreshReviewQueue();
  const next = Math.min(state.sentences.length - 1, state.currentIndex + 1);
  setPracticeStage("blind");
  if (next !== state.currentIndex) playSentence(next);
  else setSub("本篇四阶段训练完成");
}

async function refreshReviewQueue() {
  try {
    const data = await api("/api/v1/listening/review?limit=40");
    state.reviewQueue = data.items || [];
    $("lpReviewCount").textContent = String(state.reviewQueue.length);
  } catch {}
}

function openReviewItem(item) {
  if (!item) return;
  if (item.article_id !== state.articleId) {
    location.href = `/static/listen.html?id=${encodeURIComponent(item.article_id)}&sentence=${encodeURIComponent(item.sentence_index)}`;
    return;
  }
  state.currentIndex = Number(item.sentence_index);
  state.reviewOpen = false;
  setPracticeStage("blind");
  playSentence(state.currentIndex);
}

// ── Persistence ──
const progressKey = () => `lp_progress_${state.articleId}`;
const saveProgress = () => {
  try { localStorage.setItem(progressKey(), String(state.currentIndex)); } catch {}
};
const loadProgress = () => {
  try { return Number(localStorage.getItem(progressKey()) || 0); } catch { return 0; }
};

// ── Audio ──
async function fetchAudio(idx) {
  if (state.audioCache.has(idx)) return state.audioCache.get(idx);
  const s = state.sentences[idx];
  if (!s) return null;
  const p = api("/api/audio/sentence", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: s.text }),
  })
    .then((d) => d.audio_url)
    .catch((err) => { state.audioCache.delete(idx); throw err; });
  state.audioCache.set(idx, p);
  return p;
}

function prefetch(idx) {
  if (state.alignedReady) return;
  for (let k = 1; k <= 2; k++) {
    const next = idx + k;
    if (next < state.sentences.length) fetchAudio(next).catch(() => {});
  }
}

function validAlignment(s) {
  return Number.isFinite(s?.begin_ms) && Number.isFinite(s?.end_ms) && s.end_ms > s.begin_ms;
}

function hasAlignedTimeline() {
  return state.alignedReady && state.sentences.some(validAlignment);
}

function ensureAlignedAudio() {
  if (state.alignedAudio) return state.alignedAudio;
  const url = state.alignedAudioUrl;
  if (!url) return null;
  const audio = new Audio(url);
  audio.playbackRate = state.playbackRate;
  audio.preload = "auto";
  audio.ontimeupdate = syncHighlightToAudio;
  audio.onended = () => {
    state.isPlaying = false;
    updatePlayBtn();
    setSub("播放完毕");
  };
  audio.onerror = () => {
    if (state.currentAudio !== audio) return;
    state.alignedFailed = true;
    browserTts(state.sentences[state.currentIndex]?.text || "", state.currentIndex, state.playToken);
  };
  state.alignedAudio = audio;
  return audio;
}

function findSentenceByTime(ms) {
  let fallback = state.currentIndex;
  for (const s of state.sentences) {
    if (!validAlignment(s)) continue;
    if (ms >= s.begin_ms && ms < s.end_ms) return s.index;
    if (ms >= s.end_ms) fallback = s.index;
  }
  return fallback;
}

function syncHighlightToAudio() {
  if (!state.currentAudio || state.currentAudio !== state.alignedAudio) return;
  const ms = state.currentAudio.currentTime * 1000;
  const segment = state.sentences[state.segmentTargetIndex];
  if (segment && validAlignment(segment) && ms >= segment.end_ms) {
    state.currentAudio.pause();
    state.segmentTargetIndex = null;
    onSentenceEnd(segment.index);
    return;
  }
  const idx = findSentenceByTime(ms);
  if (idx !== state.currentIndex && state.sentences[idx]) {
    state.currentIndex = idx;
    highlightSentence(idx);
    renderVocab(idx);
    updateProgress();
    saveProgress();
    updateMediaMeta();
  }
}

// ── Playback ──
function stopCurrent() {
  state.playToken += 1;
  if (state.transitionTimer) {
    clearTimeout(state.transitionTimer);
    state.transitionTimer = null;
  }
  if (state.currentAudio) {
    if (state.currentAudio !== state.alignedAudio) {
      state.currentAudio.onended = null;
      state.currentAudio.onerror = null;
    }
    try { state.currentAudio.pause(); } catch {}
    state.currentAudio = null;
  }
  state.segmentTargetIndex = null;
  if (state.fallbackActive && "speechSynthesis" in window) {
    window.speechSynthesis.cancel();
    state.fallbackActive = false;
  }
}

function browserTts(text, idx = state.currentIndex, token = state.playToken) {
  if (!("speechSynthesis" in window)) { state.isPlaying = false; updatePlayBtn(); return; }
  state.fallbackActive = true;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "en-US";
  u.rate = 0.9 * state.playbackRate;
  u.onend  = () => {
    if (token !== state.playToken) return;
    state.fallbackActive = false;
    onSentenceEnd(idx);
  };
  u.onerror = () => {
    if (token !== state.playToken) return;
    state.fallbackActive = false;
    onSentenceEnd(idx);
  };
  window.speechSynthesis.speak(u);
}

function onSentenceEnd(completedIndex = state.currentIndex) {
  if (!state.isPlaying) return;
  let next = completedIndex + 1;
  let continuation = false;
  if (state.sentenceLoop) {
    next = completedIndex;
    state.repeatRemaining = state.repeatCount;
    continuation = true;
  } else if (state.repeatRemaining > 1) {
    state.repeatRemaining -= 1;
    next = completedIndex;
    continuation = true;
  }
  if (next >= state.sentences.length) {
    state.isPlaying = false;
    updatePlayBtn();
    setSub("播放完毕");
    return;
  }
  const run = () => {
    state.transitionTimer = null;
    if (state.isPlaying) playSentence(next, { continuation });
  };
  if (state.pauseMs > 0) {
    setSub(`停顿 ${(state.pauseMs / 1000).toFixed(1).replace(/\.0$/, "")} 秒…`);
    state.transitionTimer = setTimeout(run, state.pauseMs);
  } else {
    run();
  }
}

async function playSentence(idx, options = {}) {
  if (idx < 0 || idx >= state.sentences.length) return;
  stopCurrent();
  const playToken = state.playToken;
  if (!options.continuation) state.repeatRemaining = state.repeatCount;
  state.currentIndex = idx;
  state.isPlaying = true;
  highlightSentence(idx);
  renderVocab(idx);
  updateProgress();
  saveProgress();
  updatePlayBtn();
  updateMediaMeta();
  prefetch(idx);

  if (hasAlignedTimeline() && validAlignment(state.sentences[idx])) {
    const audio = ensureAlignedAudio();
    if (!audio) {
      state.alignedFailed = true;
    } else {
      state.currentAudio = audio;
      audio.playbackRate = state.playbackRate;
      state.segmentTargetIndex = (state.repeatCount > 1 || state.pauseMs > 0 || state.sentenceLoop) ? idx : null;
      audio.currentTime = Math.max(0, state.sentences[idx].begin_ms / 1000);
      await audio.play().catch(() => {
        state.alignedFailed = true;
        browserTts(state.sentences[idx].text, idx, playToken);
      });
      return;
    }
  }

  try {
    const url = await fetchAudio(idx);
    if (!url) throw new Error("no url");
    if (state.currentIndex !== idx || !state.isPlaying) return; // user jumped away
    const audio = new Audio(url);
    audio.playbackRate = state.playbackRate;
    audio.preload = "auto";
    audio.onended = () => onSentenceEnd(idx);
    audio.onerror = () => browserTts(state.sentences[idx].text, idx, playToken);
    state.currentAudio = audio;
    await audio.play();
  } catch {
    browserTts(state.sentences[idx].text, idx, playToken);
  }
}

function pause() {
  state.isPlaying = false;
  if (state.transitionTimer) {
    clearTimeout(state.transitionTimer);
    state.transitionTimer = null;
  }
  try { state.currentAudio?.pause(); } catch {}
  if (state.fallbackActive && "speechSynthesis" in window) window.speechSynthesis.pause();
  updatePlayBtn();
}

function resume() {
  if (state.currentAudio?.paused) {
    state.isPlaying = true;
    state.currentAudio.play().catch(() => playSentence(state.currentIndex));
    updatePlayBtn();
    return;
  }
  if (state.fallbackActive && window.speechSynthesis?.paused) {
    state.isPlaying = true;
    window.speechSynthesis.resume();
    updatePlayBtn();
    return;
  }
  playSentence(state.currentIndex);
}

function togglePlay() {
  if (state.isPlaying) pause(); else resume();
}

function repeatCurrent() {
  if (hasAlignedTimeline() && validAlignment(state.sentences[state.currentIndex])) {
    playSentence(state.currentIndex);
    return;
  }
  if (state.currentAudio) {
    state.currentAudio.currentTime = 0;
    state.isPlaying = true;
    state.currentAudio.play().catch(() => playSentence(state.currentIndex));
    updatePlayBtn();
  } else {
    playSentence(state.currentIndex);
  }
}

function toggleSentenceLoop() {
  state.sentenceLoop = !state.sentenceLoop;
  const button = $("lpLoopBtn");
  button.setAttribute("aria-pressed", String(state.sentenceLoop));
  button.textContent = state.sentenceLoop ? "循环中" : "单句循环";
  if (state.isPlaying && hasAlignedTimeline()) {
    state.segmentTargetIndex = (state.sentenceLoop || state.repeatCount > 1 || state.pauseMs > 0) ? state.currentIndex : null;
  }
}

function cycleRate() {
  const cur = state.playbackRate;
  const i = RATES.findIndex((r) => Math.abs(r - cur) < 0.01);
  const next = RATES[(i + 1) % RATES.length];
  state.playbackRate = next;
  $("lpRateBtn").textContent = `${next.toFixed(2).replace(/\.?0+$/, "")}x`;
  if (state.currentAudio) state.currentAudio.playbackRate = next;
}

// ── Media Session ──
function setupMediaSession() {
  if (!("mediaSession" in navigator)) return;
  navigator.mediaSession.setActionHandler("play",          () => resume());
  navigator.mediaSession.setActionHandler("pause",         () => pause());
  navigator.mediaSession.setActionHandler("previoustrack", () => playSentence(Math.max(0, state.currentIndex - 1)));
  navigator.mediaSession.setActionHandler("nexttrack",     () => playSentence(Math.min(state.sentences.length - 1, state.currentIndex + 1)));
}

function updateMediaMeta() {
  if (!("mediaSession" in navigator) || !("MediaMetadata" in window)) return;
  try {
    navigator.mediaSession.metadata = new MediaMetadata({
      title:  state.sentences[state.currentIndex]?.text?.slice(0, 80) || state.title,
      artist: "English Lab · 听力模式",
      album:  state.title,
    });
  } catch {}
}

// ── Boot: Phase 1 ── fetch sentences immediately
async function phase1() {
  state.articleId = getId();
  if (!state.articleId) { setSub("缺少文章 ID"); return false; }

  try {
    const data = await api(`/api/articles/${encodeURIComponent(state.articleId)}/listening/sentences`);
    state.title     = data.title || "";
    state.sentences = data.sentences || [];

    if (!state.sentences.length) { setSub("文章暂无可朗读的句子"); return false; }

    $("lpTitle").textContent = state.title || "听力模式";
    document.title = `${state.title} · 听力模式`;

    renderArticle();

    // Restore saved position
    const requestedSentence = getRequestedSentence();
    const saved = loadProgress();
    state.currentIndex = requestedSentence !== null && requestedSentence < state.sentences.length
      ? requestedSentence
      : (saved > 0 && saved < state.sentences.length ? saved : 0);
    highlightSentence(state.currentIndex);
    updateProgress();

    if (requestedSentence !== null) {
      setSub(`复习第 ${state.currentIndex + 1} 句`);
    } else if (saved > 0 && saved < state.sentences.length) {
      setSub(`从第 ${saved + 1} 句继续`);
    } else {
      setSub(`共 ${state.sentences.length} 句`);
    }

    setControls(true);
    setVocabStatus("analyzing");
    setupMediaSession();
    return true;
  } catch (err) {
    setSub(`加载失败：${err.message}`);
    return false;
  }
}

// ── Boot: Phase 2 ── AI analysis in background
async function phase2() {
  try {
    const data = await api(
      `/api/articles/${encodeURIComponent(state.articleId)}/listening/prepare`,
      { method: "POST" }
    );
    const byIdx = new Map((data.sentences || []).map((s) => [s.index, s]));
    for (const s of state.sentences) {
      const enriched = byIdx.get(s.index);
      if (enriched) { s.vocab = enriched.vocab; s.translation = enriched.translation; }
    }
    state.analysisReady = true;
    setVocabStatus("done");
    renderVocab(state.currentIndex);
    renderPractice();
  } catch (err) {
    console.warn("Background prepare failed:", err);
    setVocabStatus("");
  }
}

// ── Phase 3 ── precise whole-article audio timeline (with progress)
const STAGE_LABELS = {
  pending:     "排队中",
  prep:        "准备文本",
  tts:         "合成语音",
  concat:      "拼接音频",
  oss_upload:  "上传到 OSS",
  asr_submit:  "提交 ASR",
  asr_polling: "OSS 内容解析中",
  asr_fetch:   "拉取转写",
  align:       "对齐原文",
  cleanup:     "清理临时文件",
  ready:       "时间轴已就绪",
  failed:      "准备失败",
};

function showPrepare() {
  const el = $("lpPrepare");
  if (el) { el.hidden = false; el.classList.remove("done", "failed"); }
}
function setPrepareStage(stage, pct, msg) {
  const stageEl = $("lpPrepareStage");
  const msgEl   = $("lpPrepareMsg");
  const pctEl   = $("lpPreparePct");
  const fill    = $("lpPrepareFill");
  if (stageEl) stageEl.textContent = STAGE_LABELS[stage] || stage || "处理中";
  if (msgEl)   msgEl.textContent   = msg || "";
  if (pctEl)   pctEl.textContent   = `${pct ?? 0}%`;
  if (fill)    fill.style.right    = `${Math.max(0, 100 - (pct || 0))}%`;
}
function setPrepareElapsed(seconds) {
  const el = $("lpPrepareElapsed");
  if (!el) return;
  const mm = String(Math.floor(seconds / 60)).padStart(2, "0");
  const ss = String(seconds % 60).padStart(2, "0");
  el.textContent = `${mm}:${ss}`;
}
function markPrepareDone() {
  const el = $("lpPrepare");
  if (!el) return;
  el.classList.add("done");
  setTimeout(() => { el.hidden = true; }, 2500);
}
function markPrepareFailed(msg) {
  const el = $("lpPrepare");
  if (!el) return;
  el.classList.add("failed");
  setPrepareStage("failed", 0, msg || "准备失败");
}

function applyAlignedResult(data, bustCache = false) {
  // Regenerated audio reuses the same /audio/<key>.wav filename, so append a
  // cache-buster to force the browser to fetch the fresh bytes.
  state.alignedAudioUrl = bustCache ? `${data.audio_url}?t=${Date.now()}` : data.audio_url;
  const byIdx = new Map((data.alignments || []).map((s) => [s.index, s]));
  for (const s of state.sentences) {
    const aligned = byIdx.get(s.index);
    if (!aligned) continue;
    s.begin_ms = Number(aligned.begin_ms);
    s.end_ms = Number(aligned.end_ms);
    s.asr_text = aligned.asr_text;
    s.align_confidence = aligned.confidence;
    s.words = aligned.words || [];
  }
  state.alignedReady = true;
  state.alignedFailed = false;
  ensureAlignedAudio();
}

async function fetchVariants() {
  try {
    const data = await api(`/api/articles/${encodeURIComponent(state.articleId)}/listening/audio-variants`);
    state.variants = data.variants || [];
    if (!state.provider) {
      // Prefer the linked original audio; otherwise reuse a cached generated voice.
      const original = state.variants.find((v) => v.provider === "original" && (v.cached || v.configured));
      const cached = state.variants.find((v) => v.cached);
      state.provider = (original && original.provider) || (cached && cached.provider) || data.current || (state.variants[0] && state.variants[0].provider) || "";
    }
    renderSourceSelector();
  } catch (err) {
    console.warn("variants failed", err);
  }
}

function renderSourceSelector() {
  const box = $("lpSource");
  if (!box) return;
  if (!state.variants.length) { box.hidden = true; return; }
  box.hidden = false;
  const pills = state.variants.map((v) => {
    const active = v.provider === state.provider ? " active" : "";
    const cached = v.cached ? " cached" : "";
    const disabled = !v.configured && !v.cached ? " disabled" : "";
    const tip = v.cached ? "已生成" : (v.configured ? "未生成，点击生成" : "未配置该模型");
    return `<button class="lp-src-pill${active}${cached}" data-provider="${esc(v.provider)}"${disabled} title="${esc(v.label)} · ${esc(v.model)} · ${tip}">
      <span class="lp-src-dot"></span>${esc(v.label)}
    </button>`;
  }).join("");
  const active = state.variants.find((v) => v.provider === state.provider);
  const ready = Boolean((active && active.cached) || state.alignedReady);
  const exportReady = ready && active?.provider !== "original";
  const downloadDisabled = state.alignedLoading || state.videoExporting || !exportReady ? " disabled" : "";
  const downloadTitle = exportReady
    ? "下载第三版视频素材包（横屏 16:9 + 竖屏 9:16）"
    : (active?.provider === "original" ? "视频素材包需切换到 AI 朗读音频" : "请先生成当前朗读模型的整篇音频");
  const scrollDownloadTitle = exportReady
    ? "下载滚动听力视频素材包（16:9）"
    : (active?.provider === "original" ? "视频素材包需切换到 AI 朗读音频" : "请先生成当前朗读模型的整篇音频");
  box.innerHTML = `<span class="lp-source-label">音频来源</span>${pills}
    <button class="lp-video-export-btn" id="lpVideoExportBtn" aria-label="下载第三版视频素材包" title="${downloadTitle}"${downloadDisabled}>⇩</button>
    <button class="lp-video-export-btn" id="lpScrollVideoExportBtn" aria-label="下载滚动听力视频素材包" title="${scrollDownloadTitle}"${downloadDisabled}>▤</button>`;
}

function filenameFromDisposition(value) {
  if (!value) return "";
  const utf = value.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf) {
    try { return decodeURIComponent(utf[1]); } catch {}
  }
  const plain = value.match(/filename="?([^";]+)"?/i);
  return plain ? plain[1] : "";
}

async function downloadVideoPackage() {
  if (state.videoExporting) return;
  const active = state.variants.find((v) => v.provider === state.provider);
  if (state.alignedLoading) {
    setSub("整篇音频仍在生成中，请完成后再下载视频包");
    return;
  }
  if (!state.alignedReady && !(active && active.cached)) {
    setSub("请先生成当前朗读模型的整篇音频，再下载视频包");
    return;
  }
  const label = (active && active.label) || state.provider || "当前模型";
  const ok = confirm(
    `将渲染并下载第三版视频素材包（横屏 16:9 + 竖屏 9:16），每个比例含逐帧画面、音频和 render.bat。\n\n` +
    `当前朗读模型：${label}\n` +
    `服务器逐帧渲染约需 1 分钟，请耐心等待。下载后在电脑上运行 render.bat（需本机 ffmpeg）即可合成 MP4。\n\n` +
    `是否继续？`
  );
  if (!ok) return;

  const btn = $("lpVideoExportBtn");
  state.videoExporting = true;
  if (btn) btn.disabled = true;
  setSub("正在渲染并打包第三版视频帧（横屏+竖屏），约需 1 分钟…");
  try {
    const res = await fetch(`/api/articles/${encodeURIComponent(state.articleId)}/video/render/download`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: state.provider || undefined, ratios: ["16:9", "9:16"] }),
    });
    if (!res.ok) {
      let msg = `${res.status}`;
      try { const d = await res.json(); msg = d.detail || d.message || msg; } catch {}
      throw new Error(msg);
    }
    const blob = await res.blob();
    const filename = filenameFromDisposition(res.headers.get("Content-Disposition"))
      || `video_v3_${state.articleId}.zip`;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    setSub("视频素材包已开始下载");
  } catch (err) {
    console.warn("Video export download failed:", err);
    setSub(`下载失败：${err.message}`);
  } finally {
    state.videoExporting = false;
    renderSourceSelector();
  }
}

async function downloadListeningScrollPackage() {
  if (state.videoExporting) return;
  const active = state.variants.find((v) => v.provider === state.provider);
  if (state.alignedLoading) {
    setSub("整篇音频仍在生成中，请完成后再下载滚动听力视频包");
    return;
  }
  if (!state.alignedReady && !(active && active.cached)) {
    setSub("请先生成当前朗读模型的整篇音频，再下载滚动听力视频包");
    return;
  }
  const label = (active && active.label) || state.provider || "当前模型";
  const ok = confirm(
    `将渲染并下载滚动听力视频素材包（16:9）。画面会模拟听力模式：左侧文章自动滚动并高亮当前句，右侧显示当前句译文和全部生词。\n\n` +
    `当前朗读模型：${label}\n` +
    `下载后在电脑上运行 render.bat（需本机 Chrome/Edge + ffmpeg）即可合成 MP4。\n\n` +
    `是否继续？`
  );
  if (!ok) return;

  const btn = $("lpScrollVideoExportBtn");
  state.videoExporting = true;
  if (btn) btn.disabled = true;
  setSub("正在打包滚动听力视频帧（16:9），请稍候…");
  try {
    const res = await fetch(`/api/articles/${encodeURIComponent(state.articleId)}/video/listening-scroll/download`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: state.provider || undefined, ratios: ["listen-scroll-16:9"] }),
    });
    if (!res.ok) {
      let msg = `${res.status}`;
      try { const d = await res.json(); msg = d.detail || d.message || msg; } catch {}
      throw new Error(msg);
    }
    const blob = await res.blob();
    const filename = filenameFromDisposition(res.headers.get("Content-Disposition"))
      || `video_listen_scroll_${state.articleId}.zip`;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    setSub("滚动听力视频素材包已开始下载");
  } catch (err) {
    console.warn("Listening scroll video export failed:", err);
    setSub(`下载失败：${err.message}`);
  } finally {
    state.videoExporting = false;
    renderSourceSelector();
  }
}

async function switchProvider(provider) {
  if (!provider || provider === state.provider) return;
  if (state.alignedLoading) return;
  const v = state.variants.find((x) => x.provider === provider);
  if (v && !v.configured && !v.cached) {
    setSub(`${v.label} 未配置，请先在设置中填入 API Key`);
    return;
  }
  const action = provider === "original" ? "识别并精确对齐原版音频" : `使用 ${v?.label || provider} 生成整篇音频`;
  if (v && !v.cached && !confirm(`${action}？可能需要一些时间。`)) return;
  state.provider = provider;
  // Reset the timeline so the newly chosen provider's audio is applied. Use a
  // non-forced run so an already-cached variant is reused instantly.
  stopCurrent();
  if (state.alignedAudio) { try { state.alignedAudio.pause(); } catch {} }
  state.alignedAudio = null;
  state.alignedReady = false;
  state.alignedFailed = false;
  renderSourceSelector();
  await phaseAlignedAudio(false);
}

async function regenerateAligned() {
  if (state.alignedLoading) return;
  const message = state.provider === "original"
    ? "重新识别并对齐原版音频？将再次调用 Qwen ASR。"
    : "重新解析并生成整篇音频？将重新调用 TTS，可能需要一些时间。";
  if (!confirm(message)) return;
  const btn = $("lpRegenBtn");
  if (btn) btn.classList.add("spinning");
  try {
    await phaseAlignedAudio(true);
  } finally {
    if (btn) btn.classList.remove("spinning");
  }
}

async function phaseAlignedAudio(force = false) {
  if (state.alignedLoading) return;
  if (state.alignedReady && !force) return;
  if (force) {
    // Discard the existing timeline + cached audio element so the regenerated
    // result is re-applied from scratch.
    stopCurrent();
    if (state.alignedAudio) { try { state.alignedAudio.pause(); } catch {} }
    state.alignedAudio = null;
    state.alignedReady = false;
    state.alignedFailed = false;
  }
  state.alignedLoading = true;
  showPrepare();
  setPrepareStage("pending", 0, force ? "重新生成中…" : "请求开始…");

  let startedAt = Date.now();
  const tickElapsed = () => setPrepareElapsed(Math.floor((Date.now() - startedAt) / 1000));
  const elapsedTimer = setInterval(tickElapsed, 1000);
  tickElapsed();

  try {
    const original = state.provider === "original";
    const startPath = original
      ? `/api/articles/${encodeURIComponent(state.articleId)}/listening/original-audio/start`
      : `/api/articles/${encodeURIComponent(state.articleId)}/listening/aligned-audio/start`;
    const startData = await api(
      startPath,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(original
          ? { enable_words: true, refresh: force }
          : { enable_words: true, refresh: force, provider: state.provider || undefined }),
      }
    );

    // Cache hit → no polling needed
    if (startData.cached && startData.result) {
      applyAlignedResult(startData.result, force);
      setPrepareStage("ready", 100, "命中缓存,无需重新生成");
      markPrepareDone();
      return;
    }

    const taskId = startData.task_id;
    if (!taskId) throw new Error("后端未返回 task_id");

    // Poll status until done
    while (true) {
      await new Promise((r) => setTimeout(r, 1500));
      let status;
      try {
        const statusPath = original
          ? `/api/articles/${encodeURIComponent(state.articleId)}/listening/original-audio/status/${encodeURIComponent(taskId)}`
          : `/api/articles/${encodeURIComponent(state.articleId)}/listening/aligned-audio/status/${encodeURIComponent(taskId)}`;
        status = await api(statusPath);
      } catch (err) {
        // transient network/404 — give up after a few retries
        throw err;
      }
      if (status.started_at) startedAt = status.started_at * 1000;
      setPrepareStage(status.stage, status.pct, status.msg);
      if (status.error) {
        markPrepareFailed(status.error);
        state.alignedFailed = true;
        return;
      }
      if (status.result) {
        applyAlignedResult(status.result, force);
        setPrepareStage("ready", 100, force ? "已重新生成" : "时间轴已就绪");
        markPrepareDone();
        fetchVariants();
        return;
      }
    }
  } catch (err) {
    state.alignedFailed = true;
    console.warn("Aligned audio failed:", err);
    markPrepareFailed(err.message || "准备失败");
  } finally {
    clearInterval(elapsedTimer);
    state.alignedLoading = false;
  }
}

// ── Events ──
function wireEvents() {
  $("lpBackBtn").addEventListener("click", () => {
    stopCurrent();
    window.location.href = "/static/index.html";
  });
  $("lpRegenBtn").addEventListener("click", regenerateAligned);
  $("lpSource").addEventListener("click", (e) => {
    const scrollExportBtn = e.target.closest("#lpScrollVideoExportBtn");
    if (scrollExportBtn && !scrollExportBtn.disabled) {
      downloadListeningScrollPackage();
      return;
    }
    const exportBtn = e.target.closest(".lp-video-export-btn");
    if (exportBtn && !exportBtn.disabled) {
      downloadVideoPackage();
      return;
    }
    const pill = e.target.closest(".lp-src-pill");
    if (pill && !pill.disabled) switchProvider(pill.dataset.provider);
  });
  $("lpFullscreenBtn").addEventListener("click", async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch {}
  });

  $("lpPlayBtn").addEventListener("click",   togglePlay);
  $("lpPrevBtn").addEventListener("click",   () => playSentence(Math.max(0, state.currentIndex - 1)));
  $("lpNextBtn").addEventListener("click",   () => playSentence(Math.min(state.sentences.length - 1, state.currentIndex + 1)));
  $("lpRepeatBtn").addEventListener("click", repeatCurrent);
  $("lpRateBtn").addEventListener("click",   cycleRate);
  $("lpLoopBtn").addEventListener("click", toggleSentenceLoop);
  $("lpRepeatCount").addEventListener("change", (event) => {
    state.repeatCount = Math.max(1, Number(event.target.value) || 1);
    state.repeatRemaining = state.repeatCount;
    if (state.isPlaying && hasAlignedTimeline()) {
      state.segmentTargetIndex = (state.sentenceLoop || state.repeatCount > 1 || state.pauseMs > 0) ? state.currentIndex : null;
    }
  });
  $("lpPauseDuration").addEventListener("change", (event) => {
    state.pauseMs = Math.max(0, Number(event.target.value) || 0);
    if (state.isPlaying && hasAlignedTimeline()) {
      state.segmentTargetIndex = (state.sentenceLoop || state.repeatCount > 1 || state.pauseMs > 0) ? state.currentIndex : null;
    }
  });

  $("lpStageTabs").addEventListener("click", (event) => {
    const stage = event.target.closest("[data-stage]")?.dataset.stage;
    if (stage) { setPracticeStage(stage); return; }
    if (event.target.closest("#lpReviewBtn")) {
      state.reviewOpen = !state.reviewOpen;
      renderPractice();
    }
  });
  $("lpPracticeBody").addEventListener("input", (event) => {
    if (event.target.id === "lpDictationInput") state.dictationDrafts.set(state.currentIndex, event.target.value);
  });
  $("lpPracticeBody").addEventListener("click", async (event) => {
    try {
      const review = event.target.closest("[data-review-index]");
      if (review) { openReviewItem(state.reviewQueue[Number(review.dataset.reviewIndex)]); return; }
      const rating = event.target.closest("[data-shadow-rating]")?.dataset.shadowRating;
      if (rating) { await submitShadowing(Number(rating)); return; }
      const action = event.target.closest("[data-practice-action]")?.dataset.practiceAction;
      if (!action) return;
      if (action === "replay") repeatCurrent();
      else if (action === "dictation" || action === "retry") setPracticeStage("dictation");
      else if (action === "submit-dictation") await submitDictation();
      else if (action === "shadowing") setPracticeStage("shadowing");
    } catch (error) {
      setSub(`保存练习失败：${error.message}`);
    }
  });

  // Tap any sentence → jump there
  $("lpArticle").addEventListener("click", (e) => {
    const el = e.target.closest(".lp-sent");
    if (!el) return;
    const idx = Number(el.dataset.idx);
    if (Number.isFinite(idx)) playSentence(idx);
  });

  // Click progress bar → jump
  $("lpProgressBar").addEventListener("click", (e) => {
    const rect  = e.currentTarget.getBoundingClientRect();
    const ratio = (e.clientX - rect.left) / rect.width;
    let idx;
    if (hasAlignedTimeline() && state.alignedAudio?.duration) {
      idx = findSentenceByTime(ratio * state.alignedAudio.duration * 1000);
    } else {
      idx = Math.floor(ratio * state.sentences.length);
    }
    idx = Math.max(0, Math.min(state.sentences.length - 1, idx));
    playSentence(idx);
  });

  // Keyboard shortcuts
  window.addEventListener("keydown", (e) => {
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
    if      (e.code === "Space")      { e.preventDefault(); togglePlay(); }
    else if (e.code === "ArrowLeft")  { e.preventDefault(); playSentence(Math.max(0, state.currentIndex - 1)); }
    else if (e.code === "ArrowRight") { e.preventDefault(); playSentence(Math.min(state.sentences.length - 1, state.currentIndex + 1)); }
    else if (e.code === "KeyR")       { e.preventDefault(); repeatCurrent(); }
    else if (e.code === "KeyL")       { e.preventDefault(); toggleSentenceLoop(); }
    else if (/^Digit[1-4]$/.test(e.code)) {
      e.preventDefault();
      setPracticeStage(["blind", "dictation", "correction", "shadowing"][Number(e.code.at(-1)) - 1]);
    }
  });

  document.addEventListener("visibilitychange", saveProgress);
}

// ── Entry ──
async function boot() {
  applyTheme(state.theme);
  $("lpRoot").dataset.stage = state.stage;
  wireEvents();
  const ok = await phase1();
  if (ok) {
    phase2(); // fire-and-forget background analysis
    loadPracticeData(); // user-specific weak sentences and due reviews
    await fetchVariants(); // resolve which provider's timeline to use
    phaseAlignedAudio(); // fire-and-forget precise timeline
  }
}

document.addEventListener("DOMContentLoaded", boot);
