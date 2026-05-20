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
  isPlaying: false,
  playbackRate: 1.0,
  fallbackActive: false,
  theme: localStorage.getItem("lp_theme") || "dark",
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

function applyTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  state.theme = t;
  localStorage.setItem("lp_theme", t);
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
  $("lpPlayBtn").textContent = state.isPlaying ? "❚❚" : "▶";
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
          ${v.note    ? `<div class="lp-vocab-note">${esc(v.note)}</div>` : ""}
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
        `<span class="lp-sent" data-idx="${s.index}">${esc(s.text)} </span>`
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
    state.alignedFailed = true;
    browserTts(state.sentences[state.currentIndex]?.text || "");
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
  if (state.currentAudio) {
    state.currentAudio.onended = null;
    state.currentAudio.onerror = null;
    try { state.currentAudio.pause(); } catch {}
    state.currentAudio = null;
  }
  if (state.fallbackActive && "speechSynthesis" in window) {
    window.speechSynthesis.cancel();
    state.fallbackActive = false;
  }
}

function browserTts(text) {
  if (!("speechSynthesis" in window)) { state.isPlaying = false; updatePlayBtn(); return; }
  state.fallbackActive = true;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "en-US";
  u.rate = 0.9 * state.playbackRate;
  u.onend  = () => { state.fallbackActive = false; onSentenceEnd(); };
  u.onerror = () => { state.fallbackActive = false; onSentenceEnd(); };
  window.speechSynthesis.speak(u);
}

function onSentenceEnd() {
  if (!state.isPlaying) return;
  const next = state.currentIndex + 1;
  if (next >= state.sentences.length) {
    state.isPlaying = false;
    updatePlayBtn();
    setSub("播放完毕");
    return;
  }
  playSentence(next);
}

async function playSentence(idx) {
  if (idx < 0 || idx >= state.sentences.length) return;
  stopCurrent();
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
      audio.currentTime = Math.max(0, state.sentences[idx].begin_ms / 1000);
      await audio.play().catch(() => {
        state.alignedFailed = true;
        browserTts(state.sentences[idx].text);
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
    audio.onended = onSentenceEnd;
    audio.onerror = () => browserTts(state.sentences[idx].text);
    state.currentAudio = audio;
    await audio.play();
  } catch {
    browserTts(state.sentences[idx].text);
  }
}

function pause() {
  state.isPlaying = false;
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
    const saved = loadProgress();
    state.currentIndex = (saved > 0 && saved < state.sentences.length) ? saved : 0;
    highlightSentence(state.currentIndex);
    updateProgress();

    if (saved > 0 && saved < state.sentences.length) {
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
    // Refresh vocab panel if currently playing
    if (state.isPlaying || state.currentAudio) renderVocab(state.currentIndex);
  } catch (err) {
    console.warn("Background prepare failed:", err);
    setVocabStatus("");
  }
}

// ── Phase 3 ── precise whole-article audio timeline
async function phaseAlignedAudio() {
  if (state.alignedLoading || state.alignedReady) return;
  state.alignedLoading = true;
  const previousSub = $("lpSub")?.textContent || "";
  if (!state.isPlaying) setSub(`${previousSub || `共 ${state.sentences.length} 句`} · 正在生成精准时间轴`);
  try {
    const data = await api(
      `/api/articles/${encodeURIComponent(state.articleId)}/listening/aligned-audio`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enable_words: false }),
      }
    );
    state.alignedAudioUrl = data.audio_url;
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
    if (!state.isPlaying) setSub(`共 ${state.sentences.length} 句 · 精准时间轴已就绪`);
  } catch (err) {
    state.alignedFailed = true;
    console.warn("Aligned audio failed:", err);
    if (!state.isPlaying) setSub(previousSub || `共 ${state.sentences.length} 句`);
  } finally {
    state.alignedLoading = false;
  }
}

// ── Events ──
function wireEvents() {
  $("lpBackBtn").addEventListener("click", () => {
    stopCurrent();
    window.location.href = "/static/index.html";
  });
  $("lpThemeBtn").addEventListener("click", () => {
    applyTheme(state.theme === "dark" ? "light" : "dark");
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
  });

  document.addEventListener("visibilitychange", saveProgress);
}

// ── Entry ──
async function boot() {
  applyTheme(state.theme);
  wireEvents();
  const ok = await phase1();
  if (ok) {
    phase2(); // fire-and-forget background analysis
    phaseAlignedAudio(); // fire-and-forget precise timeline
  }
}

document.addEventListener("DOMContentLoaded", boot);
