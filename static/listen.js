/* ──────────────────────────────────────────────────────────────
   Listening Mode — playback engine
   Flow: prepare(全篇句子分析) → 渲染 → 用户点开始 → 顺序播放
   预取后续 2 句音频；TTS 失败降级浏览器 SpeechSynthesis；
   Media Session 锁屏控制；localStorage 续播。
   ────────────────────────────────────────────────────────────── */

const $ = (id) => document.getElementById(id);
const escapeHtml = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const PLAYBACK_RATES = [0.85, 1.0, 1.15, 1.3];

const state = {
  articleId: "",
  title: "",
  sentences: [],         // [{ index, para, text, translation, vocab: [...] }]
  currentIndex: 0,
  audioUrlCache: new Map(),     // index -> Promise<url> (server-side cached URL)
  currentAudio: null,           // HTMLAudioElement currently playing
  isPlaying: false,
  playbackRate: 1.0,
  fallbackTtsActive: false,     // true while browser SpeechSynthesis is the source
  hasStarted: false,
  theme: localStorage.getItem("lp_theme") || "dark",
};

// ──────── Utils ────────
async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const data = await res.json();
      detail = data.detail || data.message || detail;
    } catch {}
    throw new Error(detail);
  }
  return res.json();
}

function getArticleIdFromUrl() {
  const p = new URLSearchParams(window.location.search);
  return p.get("id") || "";
}

function setStatus(text) {
  const el = $("lpSub");
  if (el) el.textContent = text;
}

function setLoadingText(text) {
  const el = $("lpLoadingText");
  if (el) el.textContent = text;
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  state.theme = theme;
  localStorage.setItem("lp_theme", theme);
}

// ──────── Loading: prepare ────────
async function prepareArticle() {
  state.articleId = getArticleIdFromUrl();
  if (!state.articleId) {
    setLoadingText("缺少文章 ID");
    return;
  }
  setLoadingText("分析全文句子中…");
  try {
    const data = await api(`/api/articles/${encodeURIComponent(state.articleId)}/listening/prepare`, {
      method: "POST",
    });
    state.title = data.title || "";
    state.sentences = data.sentences || [];
    if (!state.sentences.length) {
      setLoadingText("此文章没有可朗读的句子。");
      return;
    }
    $("lpTitle").textContent = state.title || "听力模式";
    document.title = `${state.title || "听力模式"} · 听力模式`;
    renderArticle();
    enterReadyState();
  } catch (err) {
    console.error(err);
    setLoadingText(`加载失败：${err.message || err}`);
  }
}

function renderArticle() {
  const container = $("lpArticle");
  const byPara = new Map();
  for (const s of state.sentences) {
    if (!byPara.has(s.para)) byPara.set(s.para, []);
    byPara.get(s.para).push(s);
  }
  const html = Array.from(byPara.entries())
    .sort((a, b) => a[0] - b[0])
    .map(([_, list]) => {
      const inner = list
        .map(
          (s) =>
            `<span class="lp-sent" data-idx="${s.index}">${escapeHtml(s.text)} </span>`
        )
        .join("");
      return `<p class="lp-para">${inner}</p>`;
    })
    .join("");
  container.innerHTML = html;
}

function enterReadyState() {
  $("lpRoot").dataset.state = "ready";
  $("lpLoading").hidden = true;
  $("lpMain").hidden = false;
  $("lpControls").hidden = false;
  $("lpStartOverlay").hidden = false;
  $("lpProgressTotal").textContent = state.sentences.length;

  const resumed = readProgress();
  if (resumed > 0 && resumed < state.sentences.length) {
    $("lpStartText").textContent = `从第 ${resumed + 1} 句继续`;
    $("lpStartSub").textContent = "上次的阅读位置已保存";
    state.currentIndex = resumed;
  } else {
    $("lpStartText").textContent = "开始播放";
    $("lpStartSub").textContent = `共 ${state.sentences.length} 句`;
  }
  updateProgress();
  setupMediaSession();
}

// ──────── Audio fetch + prefetch ────────
async function fetchSentenceAudio(idx) {
  if (state.audioUrlCache.has(idx)) return state.audioUrlCache.get(idx);
  const sentence = state.sentences[idx];
  if (!sentence) return null;
  const promise = api("/api/audio/sentence", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: sentence.text }),
  })
    .then((d) => d.audio_url)
    .catch((err) => {
      state.audioUrlCache.delete(idx);
      throw err;
    });
  state.audioUrlCache.set(idx, promise);
  return promise;
}

function prefetchAround(idx) {
  for (let k = 1; k <= 2; k++) {
    const next = idx + k;
    if (next < state.sentences.length) {
      fetchSentenceAudio(next).catch(() => {}); // fire-and-forget
    }
  }
}

// ──────── Playback ────────
async function playSentence(idx) {
  if (idx < 0 || idx >= state.sentences.length) return;
  stopCurrentAudio();
  state.currentIndex = idx;
  state.isPlaying = true;
  highlightSentence(idx);
  renderVocab(idx);
  updateProgress();
  saveProgress();
  updatePlayButton();
  updateMediaSessionMetadata();
  prefetchAround(idx);

  try {
    const url = await fetchSentenceAudio(idx);
    if (!url) throw new Error("audio url empty");
    // If user clicked next/prev while waiting, abandon this start.
    if (state.currentIndex !== idx || !state.isPlaying) return;
    const audio = new Audio(url);
    audio.playbackRate = state.playbackRate;
    audio.preload = "auto";
    audio.onended = handleSentenceEnded;
    audio.onerror = () => {
      console.warn("audio element error, fallback to browser TTS");
      playWithBrowserTts(state.sentences[idx].text);
    };
    state.currentAudio = audio;
    await audio.play();
  } catch (err) {
    console.warn("Qwen TTS failed, falling back to browser SpeechSynthesis:", err);
    playWithBrowserTts(state.sentences[idx].text);
  }
}

function playWithBrowserTts(text) {
  if (!("speechSynthesis" in window)) {
    alert("当前浏览器不支持语音合成。");
    state.isPlaying = false;
    updatePlayButton();
    return;
  }
  state.fallbackTtsActive = true;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "en-US";
  u.rate = 0.9 * state.playbackRate;
  u.onend = () => {
    state.fallbackTtsActive = false;
    handleSentenceEnded();
  };
  u.onerror = () => {
    state.fallbackTtsActive = false;
    handleSentenceEnded();
  };
  window.speechSynthesis.speak(u);
}

function stopCurrentAudio() {
  if (state.currentAudio) {
    state.currentAudio.onended = null;
    state.currentAudio.onerror = null;
    try { state.currentAudio.pause(); } catch {}
    state.currentAudio = null;
  }
  if (state.fallbackTtsActive && "speechSynthesis" in window) {
    window.speechSynthesis.cancel();
    state.fallbackTtsActive = false;
  }
}

function handleSentenceEnded() {
  if (!state.isPlaying) return;
  const next = state.currentIndex + 1;
  if (next >= state.sentences.length) {
    state.isPlaying = false;
    updatePlayButton();
    setStatus("播放完毕");
    return;
  }
  playSentence(next);
}

function pause() {
  state.isPlaying = false;
  if (state.currentAudio) {
    try { state.currentAudio.pause(); } catch {}
  }
  if (state.fallbackTtsActive && "speechSynthesis" in window) {
    window.speechSynthesis.pause();
  }
  updatePlayButton();
}

function resume() {
  if (state.currentAudio && state.currentAudio.paused) {
    state.isPlaying = true;
    state.currentAudio.play().catch(() => playSentence(state.currentIndex));
    updatePlayButton();
    return;
  }
  if (state.fallbackTtsActive && "speechSynthesis" in window && window.speechSynthesis.paused) {
    state.isPlaying = true;
    window.speechSynthesis.resume();
    updatePlayButton();
    return;
  }
  playSentence(state.currentIndex);
}

function togglePlay() {
  if (!state.hasStarted) {
    state.hasStarted = true;
    $("lpStartOverlay").hidden = true;
    playSentence(state.currentIndex);
    return;
  }
  if (state.isPlaying) pause();
  else resume();
}

function repeatCurrent() {
  if (state.currentAudio) {
    state.currentAudio.currentTime = 0;
    state.isPlaying = true;
    state.currentAudio.play().catch(() => playSentence(state.currentIndex));
    updatePlayButton();
    return;
  }
  playSentence(state.currentIndex);
}

function cycleRate() {
  const cur = state.playbackRate;
  const idx = PLAYBACK_RATES.findIndex((r) => Math.abs(r - cur) < 0.01);
  const next = PLAYBACK_RATES[(idx + 1) % PLAYBACK_RATES.length];
  state.playbackRate = next;
  $("lpRateBtn").textContent = `${next.toFixed(2).replace(/\.?0+$/, "")}x`;
  if (state.currentAudio) state.currentAudio.playbackRate = next;
}

// ──────── Rendering helpers ────────
function highlightSentence(idx) {
  document.querySelectorAll(".lp-sent.playing").forEach((el) => el.classList.remove("playing"));
  document.querySelectorAll(".lp-sent").forEach((el) => {
    const i = Number(el.dataset.idx);
    el.classList.toggle("read", i < idx);
  });
  const target = document.querySelector(`.lp-sent[data-idx="${idx}"]`);
  if (target) {
    target.classList.add("playing");
    target.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

function renderVocab(idx) {
  const body = $("lpVocabBody");
  const s = state.sentences[idx];
  if (!s) return;
  const items = (s.vocab || []).slice(0, 10);
  let html = "";
  if (items.length === 0) {
    html += `<div class="lp-vocab-empty">本句无标记生词</div>`;
  } else {
    html += items
      .map(
        (v) => `
        <div class="lp-vocab-item">
          <div class="lp-vocab-term">${escapeHtml(v.term)}</div>
          ${v.meaning ? `<div class="lp-vocab-meaning">${escapeHtml(v.meaning)}</div>` : ""}
          ${v.note ? `<div class="lp-vocab-note">${escapeHtml(v.note)}</div>` : ""}
        </div>`
      )
      .join("");
  }
  if (s.translation) {
    html += `
      <div class="lp-vocab-translation">
        <div class="lp-vocab-translation-label">中文译文</div>
        ${escapeHtml(s.translation)}
      </div>`;
  }
  body.innerHTML = html;
  body.scrollTop = 0;
}

function updateProgress() {
  $("lpProgressIndex").textContent = String(state.currentIndex + 1);
  const total = state.sentences.length || 1;
  const pct = ((state.currentIndex + 1) / total) * 100;
  $("lpProgressFill").style.right = `${100 - pct}%`;
}

function updatePlayButton() {
  $("lpPlayBtn").textContent = state.isPlaying ? "❚❚" : "▶";
}

// ──────── Progress persistence ────────
function progressKey() {
  return `lp_progress_${state.articleId}`;
}
function saveProgress() {
  try { localStorage.setItem(progressKey(), String(state.currentIndex)); } catch {}
}
function readProgress() {
  try { return Number(localStorage.getItem(progressKey()) || 0); } catch { return 0; }
}

// ──────── Media Session (lock-screen controls) ────────
function setupMediaSession() {
  if (!("mediaSession" in navigator)) return;
  navigator.mediaSession.setActionHandler("play", () => {
    if (!state.hasStarted) togglePlay();
    else resume();
  });
  navigator.mediaSession.setActionHandler("pause", () => pause());
  navigator.mediaSession.setActionHandler("previoustrack", () => playSentence(Math.max(0, state.currentIndex - 1)));
  navigator.mediaSession.setActionHandler("nexttrack", () => playSentence(Math.min(state.sentences.length - 1, state.currentIndex + 1)));
}

function updateMediaSessionMetadata() {
  if (!("mediaSession" in navigator) || !("MediaMetadata" in window)) return;
  try {
    navigator.mediaSession.metadata = new MediaMetadata({
      title: state.sentences[state.currentIndex]?.text?.slice(0, 80) || state.title,
      artist: "English Lab · 听力模式",
      album: state.title,
    });
  } catch {}
}

// ──────── Event wiring ────────
function wireEvents() {
  $("lpBackBtn").addEventListener("click", () => {
    stopCurrentAudio();
    window.location.href = "/static/index.html";
  });
  $("lpThemeBtn").addEventListener("click", () => {
    applyTheme(state.theme === "dark" ? "light" : "dark");
  });
  $("lpFullscreenBtn").addEventListener("click", async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch (err) {
      console.warn("fullscreen failed", err);
    }
  });

  $("lpStartBtn").addEventListener("click", togglePlay);
  $("lpPlayBtn").addEventListener("click", togglePlay);
  $("lpPrevBtn").addEventListener("click", () => playSentence(Math.max(0, state.currentIndex - 1)));
  $("lpNextBtn").addEventListener("click", () => playSentence(Math.min(state.sentences.length - 1, state.currentIndex + 1)));
  $("lpRepeatBtn").addEventListener("click", repeatCurrent);
  $("lpRateBtn").addEventListener("click", cycleRate);

  // Click any sentence to jump
  $("lpArticle").addEventListener("click", (e) => {
    const target = e.target.closest(".lp-sent");
    if (!target) return;
    const idx = Number(target.dataset.idx);
    if (!Number.isFinite(idx)) return;
    state.hasStarted = true;
    $("lpStartOverlay").hidden = true;
    playSentence(idx);
  });

  // Click progress bar to jump
  $("lpProgressBar").addEventListener("click", (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const ratio = (e.clientX - rect.left) / rect.width;
    const idx = Math.max(0, Math.min(state.sentences.length - 1, Math.floor(ratio * state.sentences.length)));
    state.hasStarted = true;
    $("lpStartOverlay").hidden = true;
    playSentence(idx);
  });

  // Keyboard shortcuts (desktop)
  window.addEventListener("keydown", (e) => {
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
    if (e.code === "Space") { e.preventDefault(); togglePlay(); }
    else if (e.code === "ArrowLeft") { e.preventDefault(); playSentence(Math.max(0, state.currentIndex - 1)); }
    else if (e.code === "ArrowRight") { e.preventDefault(); playSentence(Math.min(state.sentences.length - 1, state.currentIndex + 1)); }
    else if (e.code === "KeyR") { e.preventDefault(); repeatCurrent(); }
  });

  // Pause when tab hidden to avoid double playback after unlocking
  document.addEventListener("visibilitychange", () => {
    // Intentionally do not auto-pause — user may want background playback.
    // Just save progress.
    saveProgress();
  });
}

// ──────── Boot ────────
function boot() {
  applyTheme(state.theme);
  wireEvents();
  prepareArticle();
}

document.addEventListener("DOMContentLoaded", boot);
