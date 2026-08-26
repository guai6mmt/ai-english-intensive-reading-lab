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
  sort: "created",
  saveTimer: null,
  lastProgressSave: 0,
  total: 0,
  loopA: null,
  loopB: null,
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

function showNotice(message, error = false) {
  const box = $("notice");
  box.hidden = !message;
  box.textContent = message || "";
  box.style.background = error ? "#fff0ec" : "";
  box.style.borderColor = error ? "#e5b5aa" : "";
  box.style.color = error ? "#943d30" : "";
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
      <span>${esc(collection.name)}</span><b>${collection.item_count}</b>
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
    return `<article class="media-row" data-id="${esc(item.id)}">
      <button class="track-icon" data-play="${esc(item.id)}" aria-label="播放">${state.current?.id === item.id && !audio.paused ? "❚❚" : "▶"}</button>
      <div class="track-main" data-play="${esc(item.id)}"><strong>${esc(item.title)}</strong><small>${esc(item.collection_name || item.relative_path || item.original_name)}${progress ? ` · 已听 ${progress}%` : ""}</small></div>
      <div class="track-tags">${item.difficulty ? `<span>${esc(item.difficulty)}</span>` : ""}${tags}</div>
      <div class="track-meta">${duration} · ${formatBytes(item.file_size)}</div>
      <div class="row-actions">
        <button data-favorite="${esc(item.id)}" title="收藏">${item.favorite ? "★" : "☆"}</button>
        ${state.deleted ? `<button data-restore="${esc(item.id)}" title="恢复">↶</button>` : `<button data-edit="${esc(item.id)}" title="编辑">⋯</button><button data-delete="${esc(item.id)}" title="移入回收站">×</button>`}
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
  $("playerBar").dataset.open = Boolean(item);
  $("nowTitle").textContent = item?.title || "选择一条音频开始练习";
  $("nowMeta").textContent = item ? (item.collection_name || item.relative_path || item.original_name) : "—";
  $("playBtn").textContent = item && !audio.paused ? "❚❚" : "▶";
  $("favoriteBtn").textContent = item?.favorite ? "★" : "☆";
  $("abBtn").textContent = state.loopA === null ? "A–B" : state.loopB === null ? `A ${formatTime(state.loopA)}` : "A↔B";
  $("currentTime").textContent = formatTime(audio.currentTime);
  $("duration").textContent = formatTime(audio.duration);
  $("seek").value = Number.isFinite(audio.duration) && audio.duration ? Math.round(audio.currentTime / audio.duration * 1000) : 0;
}

async function saveProgress(completed = false) {
  if (!state.current || !Number.isFinite(audio.currentTime)) return;
  state.lastProgressSave = Date.now();
  state.current.position_ms = Math.round(audio.currentTime * 1000);
  state.current.playback_rate = audio.playbackRate;
  state.current.completed = completed;
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

document.addEventListener("click", async (event) => {
  const button = event.target.closest("button, [data-play]");
  if (!button) return;
  try {
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
$("scanBtn").addEventListener("click", () => $("scanDialog").showModal());
$("refreshBtn").addEventListener("click", () => loadData());
$("loadMoreBtn").addEventListener("click", () => loadData(true));
$("userBtn").addEventListener("click", () => window.EnglishLabAuth.logout());
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
});
audio.addEventListener("ended", async () => { await saveProgress(true); moveTrack(1); });
window.addEventListener("pagehide", () => saveProgress());

if ("mediaSession" in navigator) {
  navigator.mediaSession.setActionHandler("play", () => audio.play());
  navigator.mediaSession.setActionHandler("pause", () => audio.pause());
  navigator.mediaSession.setActionHandler("seekbackward", () => { audio.currentTime = Math.max(0, audio.currentTime - 15); });
  navigator.mediaSession.setActionHandler("seekforward", () => { audio.currentTime = Math.min(audio.duration || Infinity, audio.currentTime + 15); });
  navigator.mediaSession.setActionHandler("previoustrack", () => moveTrack(-1));
  navigator.mediaSession.setActionHandler("nexttrack", () => moveTrack(1));
}

window.EnglishLabAuth.ready.then((session) => { $("userBtn").textContent = session.user.username.slice(0,1).toUpperCase(); loadData(); });
