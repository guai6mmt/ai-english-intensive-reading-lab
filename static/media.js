const $ = (id) => document.getElementById(id);
const audio = $("audio");

const state = {
  items: [],
  collections: [],
  current: null,
  currentIndex: -1,
  collectionId: "",
  favoriteOnly: false,
  deleted: false,
  query: "",
  sort: "track",
  saveTimer: null,
  lastProgressSave: 0,
  total: 0,
  loopA: null,
  loopB: null,
  restoredPlayback: false,
  deepLinkRestored: false,
  pairing: { options: null, candidates: [], media: [], original: new Map() },
};

const PLAYBACK_KEY = "el_media_playback_v1";
const svg = (path, fill = "none") => `<svg width="20" height="20" viewBox="0 0 24 24" fill="${fill}" stroke="${fill === "none" ? "currentColor" : "none"}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
const ICONS = {
  play: svg('<path d="M8 5.5v13l11-6.5z"/>', "currentColor"),
  pause: svg('<rect x="7" y="5" width="3.4" height="14" rx="1"/><rect x="13.6" y="5" width="3.4" height="14" rx="1"/>', "currentColor"),
  star: svg('<path d="m12 4 2.3 5.1 5.7.6-4.2 3.8 1.2 5.5L12 16.3 7 19l1.2-5.5L4 9.7l5.7-.6z"/>'),
  more: svg('<circle cx="12" cy="5" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="12" cy="19" r="1.4"/>'),
  trash: svg('<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/>'),
};

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const text = await response.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch {}
  if (!response.ok) throw new Error(data.detail || `请求失败 (${response.status})`);
  return data;
}

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let n = value / 1024;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
  return `${n >= 10 ? n.toFixed(0) : n.toFixed(1)} ${units[i]}`;
}

function formatTime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

function friendlyCollectionName(collection) {
  const name = String(collection?.name || "未命名集合");
  const date = name.match(/20\d{2}[-_. ](?:0[1-9]|1[0-2])[-_. ](?:[0-2]\d|3[01])/)?.[0]?.replace(/[_. ]/g, "-");
  if (/economist/i.test(name) && date) return `The Economist · ${date}`;
  const parts = name.split(/[\\/]/).filter(Boolean);
  return parts.at(-1) || name;
}

function showNotice(message, error = false) {
  const box = $("notice");
  box.hidden = !message;
  box.textContent = message || "";
  box.classList.toggle("is-error", Boolean(error));
}

async function loadData(append = false) {
  const params = new URLSearchParams({ sort: state.sort, limit: "200", offset: append ? String(state.items.length) : "0" });
  if (state.query) params.set("query", state.query);
  if (state.collectionId) params.set("collection_id", state.collectionId);
  if (state.favoriteOnly) params.set("favorite", "true");
  if (state.deleted) params.set("deleted", "true");
  try {
    const [library, collections, status] = await Promise.all([
      api(`/api/v1/media/items?${params}`),
      api("/api/v1/media/collections"),
      api("/api/v1/media/status"),
    ]);
    state.items = append ? state.items.concat(library.items || []) : (library.items || []);
    state.total = library.total || 0;
    state.collections = collections.items || [];
    renderCollections();
    renderItems();
    renderStats(status, library.total || 0);
    restorePlayback();
    const requestedMedia = new URLSearchParams(location.search).get("media");
    if (requestedMedia && !state.deepLinkRestored) {
      state.deepLinkRestored = true;
      const requested = state.items.find((item) => item.id === requestedMedia);
      if (requested) {
        await playItem(requested.id, false);
        document.querySelector(`[data-id="${CSS.escape(requested.id)}"]`)?.scrollIntoView({ block: "center" });
      }
    }
  } catch (error) {
    showNotice(error.message, true);
  }
}

function renderStats(status, visible) {
  $("stats").innerHTML = `
    <div class="stat"><b>${Number(status.items || 0).toLocaleString()}</b><span>全部音频</span></div>
    <div class="stat"><b>${formatBytes(status.bytes)}</b><span>媒体容量</span></div>
    <div class="stat"><b>${formatBytes(status.disk_free)}</b><span>磁盘可用</span></div>`;
  $("summaryText").textContent = `${visible} 条结果 · ${state.collections.length} 个分类${status.ffprobe_available ? "" : " · 服务器未安装 ffprobe"}`;
  $("allCount").textContent = status.items || 0;
  $("deletedCount").textContent = status.deleted || 0;
}

function renderCollections() {
  $("collectionList").innerHTML = state.collections.map((collection) => `
    <button class="collection${state.collectionId === collection.id ? " active" : ""}" data-collection="${esc(collection.id)}">
      <span title="${esc(collection.name)}">${esc(friendlyCollectionName(collection))}</span><b>${collection.item_count}</b>
    </button>`).join("");
  document.querySelectorAll(".collection[data-special], .collection[data-collection='']").forEach((button) => {
    const special = button.dataset.special || "";
    button.classList.toggle("active", special === "favorite" ? state.favoriteOnly : special === "deleted" ? state.deleted : !state.collectionId && !state.favoriteOnly && !state.deleted);
  });
}

function renderItems() {
  $("emptyState").hidden = state.items.length > 0;
  $("mediaList").innerHTML = state.items.map((item) => {
    const tags = (item.tags || []).slice(0, 3).map((tag) => `<span>${esc(tag)}</span>`).join("");
    const duration = item.duration_ms ? formatTime(item.duration_ms / 1000) : "--:--";
    const progress = item.duration_ms ? Math.min(100, Math.round((item.position_ms || 0) / item.duration_ms * 100)) : 0;
    return `<article class="media-row" data-id="${esc(item.id)}" data-playing="${state.current?.id === item.id && !audio.paused}">
      <button class="track-icon" data-play="${esc(item.id)}" aria-label="播放">${state.current?.id === item.id && !audio.paused ? ICONS.pause : ICONS.play}</button>
      <div class="track-main" data-play="${esc(item.id)}"><strong>${esc(item.title)}</strong><small>${esc(item.collection_name || item.relative_path || item.original_name)}${progress ? ` · 已听 ${progress}%` : ""}</small></div>
      <div class="track-tags">${item.linked_article_id ? `<a class="linked-article" href="/?article=${encodeURIComponent(item.linked_article_id)}">配套文章</a>` : ""}${item.difficulty ? `<span>${esc(item.difficulty)}</span>` : ""}${tags}</div>
      <div class="track-meta">${duration} · ${formatBytes(item.file_size)}</div>
      <div class="row-actions">
        <button data-favorite="${esc(item.id)}" title="收藏" aria-label="收藏音频" class="${item.favorite ? "is-favorite" : ""}">${ICONS.star}</button>
        ${state.deleted ? `<button data-restore="${esc(item.id)}" title="恢复">恢复</button>` : `<button data-edit="${esc(item.id)}" title="编辑" aria-label="编辑音频">${ICONS.more}</button><button data-delete="${esc(item.id)}" title="移入回收站" aria-label="移入回收站">${ICONS.trash}</button>`}
      </div>
    </article>`;
  }).join("");
  $("loadMoreBtn").hidden = state.items.length >= state.total;
}

function selectCollection(button) {
  state.collectionId = button.dataset.collection || "";
  state.favoriteOnly = button.dataset.special === "favorite";
  state.deleted = button.dataset.special === "deleted";
  loadData();
}

async function playItem(id, autoPlay = true) {
  const item = state.items.find((entry) => entry.id === id);
  if (!item || item.deleted_at) return;
  if (state.current?.id === id) {
    if (audio.paused && autoPlay) await audio.play().catch(() => {}); else audio.pause();
    updatePlayer();
    renderItems();
    return;
  }
  await saveProgress();
  state.current = item;
  state.loopA = null;
  state.loopB = null;
  state.currentIndex = state.items.findIndex((entry) => entry.id === id);
  audio.src = item.stream_url;
  audio.playbackRate = Number(item.playback_rate || 1);
  $("rateSelect").value = String(audio.playbackRate);
  audio.addEventListener("loadedmetadata", () => {
    if (item.position_ms && item.position_ms < audio.duration * 1000 - 3000) audio.currentTime = item.position_ms / 1000;
    updatePlayer();
  }, { once: true });
  updateMediaSession();
  updatePlayer();
  renderItems();
  if (autoPlay) await audio.play().catch((error) => showNotice(`无法播放：${error.message}`, true));
}

function updatePlayer() {
  const item = state.current;
  const articleButton = $("openCurrentArticleBtn");
  $("playerBar").dataset.open = Boolean(item);
  $("nowTitle").textContent = item?.title || "选择一条音频开始练习";
  $("nowMeta").textContent = item
    ? `${item.collection_name || item.relative_path || item.original_name}${item.linked_article_id ? " · 点击进入配套文章" : ""}`
    : "—";
  articleButton.disabled = !item?.linked_article_id;
  articleButton.setAttribute("aria-label", item?.linked_article_id ? `进入《${item.title}》的配套文章` : "当前音频没有配套文章");
  articleButton.title = item?.linked_article_id ? "进入配套文章并继续播放" : "当前音频尚未配套文章";
  $("playBtn").innerHTML = item && !audio.paused ? ICONS.pause : ICONS.play;
  $("favoriteBtn").innerHTML = ICONS.star;
  $("favoriteBtn").classList.toggle("is-favorite", Boolean(item?.favorite));
  $("abBtn").textContent = state.loopA === null ? "A–B" : state.loopB === null ? `A ${formatTime(state.loopA)}` : "A↔B";
  $("currentTime").textContent = formatTime(audio.currentTime);
  $("duration").textContent = formatTime(audio.duration);
  $("seek").value = Number.isFinite(audio.duration) && audio.duration ? Math.round(audio.currentTime / audio.duration * 1000) : 0;
}

async function openCurrentArticle() {
  const item = state.current;
  if (!item?.linked_article_id) return;
  await saveProgress();
  const params = new URLSearchParams({
    article: item.linked_article_id,
    media: item.id,
    position: String(Math.max(0, Math.round((audio.currentTime || 0) * 1000))),
    rate: String(audio.playbackRate || 1),
    autoplay: audio.paused ? "0" : "1",
  });
  location.href = `/?${params.toString()}`;
}

function persistPlayback() {
  if (!state.current) return;
  try {
    sessionStorage.setItem(PLAYBACK_KEY, JSON.stringify({
      id: state.current.id,
      position: Math.round((audio.currentTime || 0) * 1000),
      rate: audio.playbackRate || 1,
    }));
  } catch {}
}

function restorePlayback() {
  if (state.restoredPlayback || state.current) return;
  state.restoredPlayback = true;
  try {
    const saved = JSON.parse(sessionStorage.getItem(PLAYBACK_KEY) || "null");
    const item = saved && state.items.find((entry) => entry.id === saved.id);
    if (!item) return;
    item.position_ms = Number(saved.position || item.position_ms || 0);
    item.playback_rate = Number(saved.rate || item.playback_rate || 1);
    playItem(item.id, false);
  } catch {}
}

async function saveProgress(completed = false) {
  if (!state.current || !Number.isFinite(audio.currentTime)) return;
  state.lastProgressSave = Date.now();
  state.current.position_ms = Math.round(audio.currentTime * 1000);
  state.current.playback_rate = audio.playbackRate;
  state.current.completed = completed;
  persistPlayback();
  try {
    await api(`/api/v1/media/items/${state.current.id}/progress`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ position_ms: state.current.position_ms, playback_rate: audio.playbackRate, completed }),
      keepalive: true,
    });
  } catch {}
}

function updateMediaSession() {
  if (!("mediaSession" in navigator) || !state.current) return;
  try {
    navigator.mediaSession.metadata = new MediaMetadata({ title: state.current.title, artist: "English Lab", album: state.current.collection_name || "听力资料库" });
  } catch {}
}

function moveTrack(delta) {
  if (!state.items.length) return;
  const next = Math.max(0, Math.min(state.items.length - 1, state.currentIndex + delta));
  if (state.items[next]) playItem(state.items[next].id);
}

async function toggleFavorite(id) {
  const result = await api(`/api/v1/media/items/${id}/favorite`, { method: "POST" });
  const item = state.items.find((entry) => entry.id === id);
  if (item) item.favorite = result.favorite;
  if (state.current?.id === id) state.current.favorite = result.favorite;
  renderItems(); updatePlayer();
}

async function uploadOne(jobId, file) {
  if (file.size > 16 * 1024 * 1024) return uploadOneChunked(jobId, file);
  const auth = await window.EnglishLabAuth.ready;
  const form = new FormData();
  form.append("file", file, file.name);
  form.append("relative_path", file.webkitRelativePath || file.name);
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/v1/media/imports/${jobId}/file`);
    xhr.withCredentials = true;
    xhr.setRequestHeader("X-CSRF-Token", auth.csrf_token);
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText || "{}"); } catch {}
      if (xhr.status >= 200 && xhr.status < 300) resolve(data); else reject(new Error(data.detail || `上传失败 (${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error("网络连接中断"));
    xhr.send(form);
  });
}

async function uploadOneChunked(jobId, file) {
  const relativePath = file.webkitRelativePath || file.name;
  const initialized = await api(`/api/v1/media/imports/${jobId}/uploads`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ relative_path: relativePath, file_size: file.size }),
  });
  const chunkSize = initialized.chunk_size || 8 * 1024 * 1024;
  let offset = initialized.received_bytes || 0;
  while (offset < file.size) {
    const endExclusive = Math.min(file.size, offset + chunkSize);
    const chunk = file.slice(offset, endExclusive);
    let lastError;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        const result = await api(`/api/v1/media/imports/${jobId}/uploads/${initialized.upload_id}`, {
          method: "PUT",
          headers: {
            "Content-Type": "application/octet-stream",
            "Content-Range": `bytes ${offset}-${endExclusive - 1}/${file.size}`,
          },
          body: chunk,
        });
        offset = result.received_bytes;
        $("importText").textContent = `${relativePath} · ${Math.round(offset / file.size * 100)}%`;
        lastError = null;
        break;
      } catch (error) {
        lastError = error;
        await new Promise((resolve) => setTimeout(resolve, 500 * (attempt + 1)));
      }
    }
    if (lastError) throw lastError;
  }
  return api(`/api/v1/media/imports/${jobId}/uploads/${initialized.upload_id}/complete`, { method: "POST" });
}

async function importFolder(files) {
  const supported = Array.from(files).filter((file) => /\.(mp3|m4a|aac|wav|flac|ogg|opus|m4b)$/i.test(file.name));
  if (!supported.length) { showNotice("选择的文件夹中没有支持的音频。", true); return; }
  $("importProgress").hidden = false;
  $("folderInput").disabled = true;
  const created = await api("/api/v1/media/imports", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ total_files: supported.length, source: supported[0].webkitRelativePath?.split("/")[0] || "browser" }),
  });
  let cursor = 0, completed = 0, failed = 0;
  const worker = async () => {
    while (cursor < supported.length) {
      const index = cursor++;
      const file = supported[index];
      $("importText").textContent = `正在上传 ${file.webkitRelativePath || file.name}`;
      try { await uploadOne(created.job_id, file); } catch { failed += 1; }
      completed += 1;
      const pct = Math.round(completed / supported.length * 100);
      $("importBar").value = pct; $("importPct").textContent = `${pct}%`;
    }
  };
  await Promise.all([worker(), worker()]);
  await api(`/api/v1/media/imports/${created.job_id}/complete`, { method: "POST" });
  $("importText").textContent = `完成：${completed - failed} 个成功，${failed} 个失败`;
  $("folderInput").disabled = false;
  showNotice(`文件夹导入完成：${completed - failed} 个成功，${failed} 个失败。`, failed > 0);
  await loadData();
}

async function pollJob(jobId) {
  while (true) {
    const data = await api(`/api/v1/media/imports/${jobId}`);
    const job = data.job;
    const pct = job.total_files ? Math.round(job.processed_files / job.total_files * 100) : 100;
    showNotice(`服务器扫描：${job.processed_files}/${job.total_files}，导入 ${job.imported_files}，重复 ${job.duplicate_files}，失败 ${job.failed_files}`);
    if (["completed", "failed", "interrupted"].includes(job.status)) return data;
    await new Promise((resolve) => setTimeout(resolve, 1200));
  }
}

function openEdit(id) {
  const item = state.items.find((entry) => entry.id === id);
  if (!item) return;
  $("editForm").dataset.id = id;
  $("editTitle").value = item.title || "";
  $("editDifficulty").value = item.difficulty || "";
  $("editTags").value = (item.tags || []).join(", ");
  $("editDescription").value = item.description || "";
  $("editDialog").showModal();
}

function pairStatus(candidate) {
  if (candidate.status === "confirmed") return ["已确认", "confirmed"];
  if (!candidate.media_id) return ["待手动匹配", "unmatched"];
  if (candidate.match_method === "manual") return ["人工选择", "manual"];
  if (candidate.status === "review") return ["建议复核", "review"];
  return ["自动匹配", "matched"];
}

function updatePairFooter() {
  const chosen = state.pairing.candidates.filter((item) => item.media_id).length;
  const total = state.pairing.candidates.length;
  const duplicates = total - new Set(state.pairing.candidates.filter((item) => item.media_id).map((item) => item.media_id)).size - state.pairing.candidates.filter((item) => !item.media_id).length;
  $("pairFooterText").textContent = duplicates > 0
    ? `存在 ${duplicates} 条重复音频，请重新选择。`
    : `已选择 ${chosen}/${total} 组；未选择的文章会保持未配套状态。`;
  $("confirmPairBtn").disabled = !total || duplicates > 0 || chosen === 0;
}

function renderPairing() {
  const options = state.pairing.media;
  const optionHtml = options.map((item) => `<option value="${esc(item.id)}">${esc(item.title)}</option>`).join("");
  $("pairTable").innerHTML = state.pairing.candidates.map((candidate) => {
    const [label, status] = pairStatus(candidate);
    return `<div class="pair-row" data-pair-row="${esc(candidate.article_id)}" data-status="${status}">
      <div class="pair-article"><small>${esc(candidate.section)}</small><strong>${esc(candidate.article_title)}</strong><span>${esc(candidate.reason || "")}</span></div>
      <div class="pair-arrow" aria-hidden="true">→</div>
      <div class="pair-audio">
        <select data-pair-select="${esc(candidate.article_id)}" aria-label="为 ${esc(candidate.article_title)} 选择音频">
          <option value="">— 暂不配套 —</option>${optionHtml}
        </select>
        <button class="quiet" data-pair-preview="${esc(candidate.article_id)}" ${candidate.media_id ? "" : "disabled"}>试听</button>
      </div>
      <span class="pair-status ${status}">${label}${candidate.media_id ? ` · ${Math.round(Number(candidate.confidence || 0) * 100)}%` : ""}</span>
    </div>`;
  }).join("") || `<div class="empty"><h2>没有可配套的文章</h2></div>`;
  state.pairing.candidates.forEach((candidate) => {
    const select = document.querySelector(`[data-pair-select="${CSS.escape(candidate.article_id)}"]`);
    if (select) select.value = candidate.media_id || "";
  });
  updatePairFooter();
}

async function openPairing() {
  $("pairDialog").showModal();
  $("pairTable").innerHTML = `<div class="loading-state">正在读取文章和音频集合…</div>`;
  $("pairSummary").hidden = true;
  $("confirmPairBtn").disabled = true;
  try {
    const options = await api("/api/v1/content-links/options");
    state.pairing.options = options;
    $("pairSourceSelect").innerHTML = (options.sources || []).map((item) => `<option value="${esc(item.id)}">${esc(item.filename)} · ${item.article_count} 篇</option>`).join("");
    $("pairCollectionSelect").innerHTML = (options.collections || []).map((item) => `<option value="${esc(item.id)}">${esc(friendlyCollectionName(item))} · ${item.item_count} 条</option>`).join("");
    if (!options.sources?.length || !options.collections?.length) {
      $("pairTable").innerHTML = `<div class="empty"><h2>资料还不完整</h2><p>请先导入文章文件和音频文件夹。</p></div>`;
      return;
    }
    const source = options.sources[0];
    if (source.suggested_collection_id) $("pairCollectionSelect").value = source.suggested_collection_id;
    await previewPairing();
  } catch (error) {
    $("pairTable").innerHTML = "";
    $("pairNotice").hidden = false;
    $("pairNotice").textContent = error.message;
    $("pairNotice").classList.add("is-error");
  }
}

async function previewPairing() {
  const sourceId = $("pairSourceSelect").value;
  const collectionId = $("pairCollectionSelect").value;
  if (!sourceId || !collectionId) return;
  $("previewPairBtn").disabled = true;
  $("previewPairBtn").textContent = "匹配中…";
  $("pairNotice").hidden = true;
  try {
    const data = await api("/api/v1/content-links/preview", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_id: sourceId, collection_id: collectionId }),
    });
    state.pairing.candidates = data.candidates || [];
    state.pairing.media = data.media_options || [];
    state.pairing.original = new Map(state.pairing.candidates.map((item) => [item.article_id, item.media_id || ""]));
    const summary = data.summary || {};
    $("pairSummary").hidden = false;
    $("pairSummary").innerHTML = `<div><b>${summary.articles || 0}</b><span>文章</span></div><div><b>${summary.audio || 0}</b><span>音频</span></div><div><b>${summary.matched || 0}</b><span>已匹配</span></div><div><b>${summary.review || 0}</b><span>建议复核</span></div><div><b>${summary.unmatched || 0}</b><span>待手动</span></div>`;
    renderPairing();
  } catch (error) {
    $("pairNotice").hidden = false;
    $("pairNotice").textContent = error.message;
    $("pairNotice").classList.add("is-error");
  } finally {
    $("previewPairBtn").disabled = false;
    $("previewPairBtn").textContent = "自动匹配";
  }
}

async function confirmPairing() {
  const links = state.pairing.candidates.filter((item) => item.media_id).map((item) => ({
    article_id: item.article_id,
    media_id: item.media_id,
    match_method: item.match_method === "manual" ? "manual" : "automatic",
    confidence: item.match_method === "manual" ? 1 : Number(item.confidence || 0),
  }));
  $("confirmPairBtn").disabled = true;
  $("confirmPairBtn").textContent = "保存中…";
  try {
    const data = await api("/api/v1/content-links/confirm", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_id: $("pairSourceSelect").value, collection_id: $("pairCollectionSelect").value, links }),
    });
    state.pairing.candidates = data.candidates || [];
    state.pairing.original = new Map(state.pairing.candidates.map((item) => [item.article_id, item.media_id || ""]));
    renderPairing();
    $("pairNotice").hidden = false;
    $("pairNotice").classList.remove("is-error");
    $("pairNotice").textContent = data.message || "配套关系已经保存。";
    await loadData();
  } catch (error) {
    $("pairNotice").hidden = false;
    $("pairNotice").classList.add("is-error");
    $("pairNotice").textContent = error.message;
  } finally {
    $("confirmPairBtn").textContent = "确认并保存配套";
    updatePairFooter();
  }
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest("button, [data-play]");
  if (!button) return;
  try {
    if (button.dataset.pairPreview) {
      const candidate = state.pairing.candidates.find((item) => item.article_id === button.dataset.pairPreview);
      const item = state.pairing.media.find((media) => media.id === candidate?.media_id);
      if (item) {
        $("pairPreviewTitle").textContent = item.title;
        $("pairPreviewAudio").src = item.stream_url;
        $("pairPreviewPlayer").hidden = false;
        await $("pairPreviewAudio").play().catch(() => {});
      }
      return;
    }
    if (button.dataset.play) return playItem(button.dataset.play);
    if (button.classList.contains("collection")) return selectCollection(button);
    if (button.dataset.favorite) return toggleFavorite(button.dataset.favorite);
    if (button.dataset.edit) return openEdit(button.dataset.edit);
    if (button.dataset.delete) {
      if (!confirm("将此音频移入回收站？媒体文件会保留。")) return;
      await api(`/api/v1/media/items/${button.dataset.delete}`, { method: "DELETE" }); await loadData(); return;
    }
    if (button.dataset.restore) { await api(`/api/v1/media/items/${button.dataset.restore}/restore`, { method: "POST" }); await loadData(); return; }
  } catch (error) { showNotice(error.message, true); }
});

$("importBtn").addEventListener("click", () => $("importDialog").showModal());
$("pairBtn").addEventListener("click", () => openPairing());
$("closePairBtn").addEventListener("click", () => {
  $("pairPreviewAudio").pause();
  $("pairDialog").close();
});
$("previewPairBtn").addEventListener("click", () => previewPairing());
$("confirmPairBtn").addEventListener("click", () => confirmPairing());
$("pairSourceSelect").addEventListener("change", () => {
  const source = state.pairing.options?.sources?.find((item) => item.id === $("pairSourceSelect").value);
  if (source?.suggested_collection_id) $("pairCollectionSelect").value = source.suggested_collection_id;
});
$("pairTable").addEventListener("change", (event) => {
  const select = event.target.closest("[data-pair-select]");
  if (!select) return;
  const candidate = state.pairing.candidates.find((item) => item.article_id === select.dataset.pairSelect);
  if (!candidate) return;
  candidate.media_id = select.value || null;
  candidate.media_title = state.pairing.media.find((item) => item.id === select.value)?.title || null;
  const original = state.pairing.original.get(candidate.article_id) || "";
  if ((select.value || "") !== original) {
    candidate.match_method = select.value ? "manual" : "unmatched";
    candidate.confidence = select.value ? 1 : 0;
    candidate.status = select.value ? "review" : "unmatched";
    candidate.reason = select.value ? "由管理员手动选择" : "暂不配套";
  }
  renderPairing();
});
$("scanBtn").addEventListener("click", () => $("scanDialog").showModal());
$("refreshBtn").addEventListener("click", () => loadData());
$("loadMoreBtn").addEventListener("click", () => loadData(true));
$("userBtn").addEventListener("click", () => window.EnglishLabAuth.logout());
$("mediaThemeBtn").addEventListener("click", () => window.ELTheme?.cycle());
$("folderInput").addEventListener("change", (event) => importFolder(event.target.files).catch((error) => showNotice(error.message, true)));
$("searchInput").addEventListener("input", (event) => { clearTimeout(state.searchTimer); state.searchTimer = setTimeout(() => { state.query = event.target.value.trim(); loadData(); }, 250); });
$("sortSelect").addEventListener("change", (event) => { state.sort = event.target.value; loadData(); });

$("scanForm").addEventListener("submit", async (event) => {
  event.preventDefault(); $("scanDialog").close();
  try {
    const data = await api("/api/v1/media/imports/scan", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({relative_path:$("scanPath").value.trim()}) });
    await pollJob(data.job_id); await loadData();
  } catch (error) { showNotice(error.message, true); }
});

$("editForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const id = event.currentTarget.dataset.id;
    await api(`/api/v1/media/items/${id}`, { method:"PATCH", headers:{"Content-Type":"application/json"}, body:JSON.stringify({ title:$("editTitle").value.trim(), difficulty:$("editDifficulty").value, tags:$("editTags").value.split(/[,，]/).map((tag)=>tag.trim()).filter(Boolean), description:$("editDescription").value.trim() }) });
    $("editDialog").close(); await loadData();
  } catch (error) { showNotice(error.message, true); }
});

$("playBtn").addEventListener("click", () => state.current && (audio.paused ? audio.play() : audio.pause()));
$("openCurrentArticleBtn").addEventListener("click", openCurrentArticle);
$("prevBtn").addEventListener("click", () => moveTrack(-1));
$("nextBtn").addEventListener("click", () => moveTrack(1));
$("backBtn").addEventListener("click", () => { audio.currentTime = Math.max(0, audio.currentTime - 15); });
$("forwardBtn").addEventListener("click", () => { audio.currentTime = Math.min(audio.duration || Infinity, audio.currentTime + 15); });
$("favoriteBtn").addEventListener("click", () => state.current && toggleFavorite(state.current.id));
$("abBtn").addEventListener("click", () => {
  if (!state.current) return;
  if (state.loopA === null) {
    state.loopA = audio.currentTime;
    showNotice(`A 点已设置在 ${formatTime(state.loopA)}，播放到终点后再次点击。`);
  } else if (state.loopB === null && audio.currentTime > state.loopA + 0.5) {
    state.loopB = audio.currentTime;
    audio.currentTime = state.loopA;
    audio.play();
    showNotice(`A-B 循环：${formatTime(state.loopA)} – ${formatTime(state.loopB)}`);
  } else {
    state.loopA = null; state.loopB = null; showNotice("A-B 循环已取消。");
  }
  updatePlayer();
});
$("rateSelect").addEventListener("change", (event) => { audio.playbackRate = Number(event.target.value); saveProgress(); });
$("seek").addEventListener("input", (event) => { if (audio.duration) audio.currentTime = Number(event.target.value) / 1000 * audio.duration; });

audio.addEventListener("play", () => { updatePlayer(); renderItems(); });
audio.addEventListener("pause", () => { updatePlayer(); renderItems(); saveProgress(); });
audio.addEventListener("timeupdate", () => {
  if (state.loopA !== null && state.loopB !== null && audio.currentTime >= state.loopB) audio.currentTime = state.loopA;
  updatePlayer();
  if (Date.now() - state.lastProgressSave > 15000) saveProgress();
  persistPlayback();
});
audio.addEventListener("ended", async () => { await saveProgress(true); moveTrack(1); });
window.addEventListener("pagehide", () => { persistPlayback(); saveProgress(); });

if ("mediaSession" in navigator) {
  navigator.mediaSession.setActionHandler("play", () => audio.play());
  navigator.mediaSession.setActionHandler("pause", () => audio.pause());
  navigator.mediaSession.setActionHandler("seekbackward", () => { audio.currentTime = Math.max(0, audio.currentTime - 15); });
  navigator.mediaSession.setActionHandler("seekforward", () => { audio.currentTime = Math.min(audio.duration || Infinity, audio.currentTime + 15); });
  navigator.mediaSession.setActionHandler("previoustrack", () => moveTrack(-1));
  navigator.mediaSession.setActionHandler("nexttrack", () => moveTrack(1));
}

window.EnglishLabAuth.ready.then((session) => { $("userBtn").textContent = session.user.username.slice(0,1).toUpperCase(); loadData(); });
