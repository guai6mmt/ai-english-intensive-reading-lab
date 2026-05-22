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
  theme: localStorage.getItem("lp_theme") || "dark",
  provider: "",          // chosen TTS provider for the aligned timeline
  variants: [],          // [{ provider, label, model, voice, cached, configured }]
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
      // Prefer a provider that already has cached audio, else the current setting.
      const cached = state.variants.find((v) => v.cached);
      state.provider = (cached && cached.provider) || data.current || (state.variants[0] && state.variants[0].provider) || "";
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
  const downloadDisabled = state.alignedLoading || state.videoExporting || !ready ? " disabled" : "";
  const downloadTitle = ready
    ? "下载 16:9 视频素材包"
    : "请先生成当前朗读模型的整篇音频";
  box.innerHTML = `<span class="lp-source-label">朗读模型</span>${pills}
    <button class="lp-video-export-btn" id="lpVideoExportBtn" aria-label="下载 16:9 视频素材包" title="${downloadTitle}"${downloadDisabled}>⇩</button>`;
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
    `将下载当前文章的 16:9 视频素材包，包含音频、字幕、背景和 render.bat。\n\n` +
    `当前朗读模型：${label}\n\n` +
    `请确认你正在电脑上操作。是否继续下载？`
  );
  if (!ok) return;

  const btn = $("lpVideoExportBtn");
  state.videoExporting = true;
  if (btn) btn.disabled = true;
  setSub("正在打包视频素材…");
  try {
    const res = await fetch(`/api/articles/${encodeURIComponent(state.articleId)}/video/export-package/download`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: state.provider || undefined }),
    });
    if (!res.ok) {
      let msg = `${res.status}`;
      try { const d = await res.json(); msg = d.detail || d.message || msg; } catch {}
      throw new Error(msg);
    }
    const blob = await res.blob();
    const filename = filenameFromDisposition(res.headers.get("Content-Disposition"))
      || `video_16x9_${state.articleId}.zip`;
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

async function switchProvider(provider) {
  if (!provider || provider === state.provider) return;
  if (state.alignedLoading) return;
  const v = state.variants.find((x) => x.provider === provider);
  if (v && !v.configured && !v.cached) {
    setSub(`${v.label} 未配置，请先在设置中填入 API Key`);
    return;
  }
  if (v && !v.cached && !confirm(`使用 ${v.label} 生成整篇音频？将调用 TTS，可能需要一些时间。`)) return;
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
  if (!confirm("重新解析并生成整篇音频？将重新调用 TTS，可能需要一些时间。")) return;
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
    const startData = await api(
      `/api/articles/${encodeURIComponent(state.articleId)}/listening/aligned-audio/start`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enable_words: true, refresh: force, provider: state.provider || undefined }),
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
        status = await api(
          `/api/articles/${encodeURIComponent(state.articleId)}/listening/aligned-audio/status/${encodeURIComponent(taskId)}`
        );
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
  $("lpThemeBtn").addEventListener("click", () => {
    applyTheme(state.theme === "dark" ? "light" : "dark");
  });
  $("lpRegenBtn").addEventListener("click", regenerateAligned);
  $("lpSource").addEventListener("click", (e) => {
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
    await fetchVariants(); // resolve which provider's timeline to use
    phaseAlignedAudio(); // fire-and-forget precise timeline
  }
}

document.addEventListener("DOMContentLoaded", boot);
