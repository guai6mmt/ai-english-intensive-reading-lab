/* AI English Intensive Reading Lab — redesigned frontend (vanilla JS) */
const STEPS = [
  { id: "text",    n: "01", title: "文本检查",   sub: "正文清洗 · 抽段落",     railTab: "context" },
  { id: "long",    n: "02", title: "长难句拆解", sub: "结构 · 同位 · 修辞",    railTab: "long" },
  { id: "vocab",   n: "03", title: "词汇掌握",   sub: "AI 分层词条",            railTab: "vocab" },
  { id: "reading", n: "04", title: "阅读答题",   sub: "5 题 · 提交后批改",      railTab: "reading" },
  { id: "dict",    n: "05", title: "听写跟读",   sub: "整体 · 逐句 · 纠错 · 跟读", railTab: "dict" },
  { id: "write",   n: "06", title: "写作反馈",   sub: "summary · opinion · 仿写", railTab: "write" },
];

const TWEAK_KEY = "english-lab-tweaks-v1";
const TWEAK_DEFAULTS = { theme: "", density: "normal", readerSize: 18, showSide: true, showRail: true };

const state = {
  library: { sources: [] },
  summary: {},
  config: {},
  settings: {},
  vocabulary: [],
  outputs: [],
  progress: {},
  currentArticle: null,
  currentList: "all",
  diffFilter: "",
  searchQuery: "",
  view: "library",
  step: "text",
  railTab: "context",
  activeSentenceText: "",
  activeSentenceId: "",
  longSentences: [],
  readingQuestions: [],
  dictationItems: [],
  currentAudio: null,
  ui: { ...TWEAK_DEFAULTS },
  reading: { picks: {} },
  dictation: { rounds: {}, speeds: {}, plays: {}, durations: {} },
  writing: { task: "", draft: "" },
  collapsedSources: new Set(),
  collapsedSections: new Set(),
};

// ───────── Utilities ─────────
const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function asArray(value) {
  if (Array.isArray(value)) return value;
  if (value === null || value === undefined || value === "") return [];
  if (typeof value === "string") {
    const parts = value.split(/\r?\n|[;；]|[,，]/).map((s) => s.trim()).filter(Boolean);
    return parts.length > 1 ? parts : [value.trim()].filter(Boolean);
  }
  if (typeof value === "object") {
    if (Array.isArray(value.items)) return value.items;
    return Object.keys(value).length ? [value] : [];
  }
  return [value];
}

function formatNumber(value) { return Number(value || 0).toLocaleString(); }

function timeAgo(iso) {
  if (!iso) return "未学习";
  const t = new Date(iso).getTime();
  if (!t) return "未学习";
  const diff = Math.max(0, Date.now() - t);
  const m = Math.floor(diff / 60000);
  if (m < 1) return "刚刚";
  if (m < 60) return `${m} 分钟前`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} 小时前`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d} 天前`;
  return new Date(t).toISOString().slice(0, 10);
}

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new Error("网络连接失败，请检查服务是否运行。");
  }
  const text = await response.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; }
  catch { throw new Error(`服务器返回无效响应 (${response.status})`); }
  if (!response.ok) throw new Error(data.detail || `请求失败 (${response.status})`);
  return data;
}

async function runAction(button, label, action) {
  if (!button) return action();
  const old = button.textContent;
  button.textContent = label;
  button.disabled = true;
  try { return await action(); }
  catch (error) { alert(error.message); }
  finally { button.textContent = old; button.disabled = false; }
}

function setSelectValue(select, value) {
  if (!select) return;
  if (![...select.options].some((o) => o.value === value)) {
    select.append(new Option(value, value));
  }
  select.value = value;
}

function allArticles() {
  return asArray(state.library.sources).flatMap((source) =>
    asArray(source.articles).map((article) => ({
      ...article,
      sourceFilename: source.filename,
      sourceId: source.id,
    }))
  );
}

function normalizeReadingQuestions(value) {
  return asArray(value)
    .map((item, index) => {
      if (typeof item === "string") return { id: `q-${index}`, question: item };
      if (!item || typeof item !== "object") return null;
      const question = item.question || item.text || "";
      return { ...item, id: item.id || `q-${index}-${question.slice(0, 16)}`, question };
    })
    .filter((item) => item && item.question);
}

function normalizeDictationItems(value) {
  return asArray(value)
    .map((item, index) => {
      if (typeof item === "string") {
        return { id: `d-${index}`, source: item, focus: "提交前隐藏原文。先播放音频，再输入你听到的内容。" };
      }
      if (!item || typeof item !== "object") return null;
      const source = item.source || item.sentence || item.text || "";
      return {
        ...item,
        id: item.id || `d-${index}-${source.slice(0, 16)}`,
        source,
        focus: item.focus || "提交前隐藏原文。先播放音频，再输入你听到的内容。",
      };
    })
    .filter((item) => item && item.source);
}

// ───────── Tweaks (theme / density / reader size) ─────────
function loadTweaks() {
  try {
    const saved = JSON.parse(localStorage.getItem(TWEAK_KEY) || "{}");
    state.ui = { ...TWEAK_DEFAULTS, ...saved };
  } catch { state.ui = { ...TWEAK_DEFAULTS }; }
  applyTweaks();
}

function saveTweaks() {
  try { localStorage.setItem(TWEAK_KEY, JSON.stringify(state.ui)); } catch {}
}

function applyTweaks() {
  const root = document.documentElement;
  root.dataset.theme = state.ui.theme || "";
  root.style.setProperty("--reader-size", state.ui.readerSize + "px");
  root.style.setProperty(
    "--reader-leading",
    state.ui.density === "tight" ? "1.6" : state.ui.density === "loose" ? "1.95" : "1.78"
  );
  $("appShell").dataset.sidebar = state.ui.showSide ? "shown" : "collapsed";
  $("appShell").dataset.rail = state.ui.showRail ? "shown" : "hidden";
  $("reopenSideBtn")?.classList.toggle("hidden", state.ui.showSide);
  $("reopenRailBtn")?.classList.toggle("hidden", state.ui.showRail || state.view !== "study");
  syncTweaksUI();
}

function syncTweaksUI() {
  document.querySelectorAll("#themeGroup button").forEach((b) =>
    b.setAttribute("aria-pressed", String((b.dataset.theme || "") === (state.ui.theme || "")))
  );
  document.querySelectorAll("#densityGroup button").forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset.density === state.ui.density))
  );
  const range = $("readerSizeRange");
  if (range) range.value = state.ui.readerSize;
  const label = $("readerSizeLabel");
  if (label) label.textContent = state.ui.readerSize + "px";
  const railToggle = $("toggleRailBtn");
  if (railToggle) railToggle.textContent = state.ui.showRail ? "▭ 隐藏 AI" : "▭ 显示 AI";
}

// ───────── Boot ─────────
async function refreshAll() {
  try {
    const [library, vocab, outputs, progress, config, settings] = await Promise.all([
      api("/api/library"),
      api("/api/vocabulary"),
      api("/api/outputs"),
      api("/api/progress"),
      api("/api/config"),
      api("/api/settings"),
    ]);
    state.library = library.library || { sources: [] };
    state.summary = library.summary || {};
    state.vocabulary = asArray(vocab.items);
    state.outputs = asArray(outputs.items);
    state.progress = progress.items && typeof progress.items === "object" && !Array.isArray(progress.items) ? progress.items : {};
    state.config = config;
    state.settings = settings;
  } catch (error) {
    console.error(error);
    $("articleList").innerHTML = `<div class="error-state">${escapeHtml(error.message)}</div>`;
    return;
  }
  renderSidebar();
  renderAiFootMeta();
  renderLibraryView();
  fillSettingsForm();
  if (state.currentArticle) {
    const fresh = allArticles().find((a) => a.id === state.currentArticle.id);
    if (fresh && state.view === "study") {
      // keep article state but refresh meta
      renderArticleHeader();
      renderStepRail();
    }
  }
}

// ───────── Sidebar ─────────
function renderAiFootMeta() {
  const providers = state.config.providers || {};
  const tasks = state.config.task_providers || {};
  const text = tasks.text || state.config.primary_provider || "deepseek";
  const model = providers[text]?.model || state.config.primary_model || "—";
  $("aiFootMeta").textContent = `${text} · ${model}`;
  const pill = $("aiRailPill");
  if (pill) pill.textContent = `${text} · ${(model || "").replace(/^.*-/, "")}`;
}

function renderSidebar() {
  const articles = allArticles();
  $("countAll").textContent = articles.length;
  document.querySelectorAll(".side-tabs button").forEach((b) =>
    b.setAttribute("aria-selected", String(b.dataset.list === state.currentList))
  );
  document.querySelectorAll("#diffChips .chip").forEach((c) =>
    c.setAttribute("aria-pressed", String((c.dataset.diff || "") === state.diffFilter))
  );

  const filtered = filteredArticles();
  if (!filtered.length) {
    $("articleList").innerHTML = `<div class="rail-empty" style="margin: 12px 8px;">没有匹配的文章。</div>`;
    return;
  }

  // Build: source → sections → articles
  const sources = new Map();
  filtered.forEach((article) => {
    const srcKey = article.sourceId || article.sourceFilename || "unknown";
    if (!sources.has(srcKey)) {
      sources.set(srcKey, { sourceId: article.sourceId, filename: article.sourceFilename || "未命名来源", sections: new Map() });
    }
    const src = sources.get(srcKey);
    const secName = article.section || "Articles";
    if (!src.sections.has(secName)) src.sections.set(secName, []);
    src.sections.get(secName).push(article);
  });

  $("articleList").innerHTML = [...sources.values()].map((src) => {
    const label = (src.filename || "未知来源").replace(/\.[^.]+$/, "");
    const srcKey = src.sourceId || src.filename || "unknown";
    const srcCollapsed = state.collapsedSources.has(srcKey);
    const totalCount = [...src.sections.values()].reduce((s, a) => s + a.length, 0);

    const sectionsHtml = srcCollapsed ? "" : [...src.sections.entries()].map(([secName, secArticles]) => {
      const secKey = `${srcKey}:${secName}`;
      const secCollapsed = state.collapsedSections.has(secKey);

      const rows = secCollapsed ? "" : secArticles.map((article) => {
        const studied = state.progress[article.id];
        const status = studied ? (studied.status === "completed" ? "done" : "in-progress") : "";
        const isActive = state.currentArticle?.id === article.id ? "true" : "false";
        const diff = article.stats?.cefr || "?";
        const words = article.stats?.word_count || 0;
        const updated = timeAgo(article.last_opened_at || studied?.updated_at);
        return `<div class="art-row" data-open-article="${escapeHtml(article.id)}" aria-selected="${isActive}">
          <span class="art-dot ${status}"></span>
          <div style="min-width: 0;">
            <div class="art-title">${escapeHtml(article.title)}</div>
            <div class="art-meta">${escapeHtml(diff)} · ${formatNumber(words)} w · ${escapeHtml(updated)}</div>
          </div>
          ${article.favorite ? `<span class="art-meta" title="已收藏">★</span>` : ""}
        </div>`;
      }).join("");

      return `<div class="section-group">
        <div class="section-group-h" data-collapse-section="${escapeHtml(secKey)}">
          <span class="section-chevron">${secCollapsed ? "▸" : "▾"}</span>
          <span class="section-name">${escapeHtml(secName)}</span>
          <span class="group-count">${secArticles.length}</span>
        </div>
        ${rows}
      </div>`;
    }).join("");

    return `<div class="source-group">
      <div class="source-group-h">
        <button class="collapse-toggle" data-collapse-source="${escapeHtml(srcKey)}" title="${srcCollapsed ? "展开" : "折叠"}">${srcCollapsed ? "▸" : "▾"}</button>
        <span class="source-label">${escapeHtml(label)}</span>
        <span class="group-count">${totalCount}</span>
        ${src.sourceId ? `<button class="del-source-btn" data-delete-source="${escapeHtml(src.sourceId)}" title="删除此来源">✕</button>` : ""}
      </div>
      ${sectionsHtml}
    </div>`;
  }).join("");
}

function filteredArticles() {
  const query = state.searchQuery.trim().toLowerCase();
  let articles = allArticles();
  if (state.currentList === "favorite") articles = articles.filter((a) => a.favorite);
  if (state.currentList === "recent") {
    const ids = new Set(Object.keys(state.progress || {}));
    articles = articles.filter((a) => ids.has(a.id));
    articles.sort((a, b) => String(b.last_opened_at || "").localeCompare(String(a.last_opened_at || "")));
  }
  if (state.diffFilter === "C1") articles = articles.filter((a) => /^(C1|C2)$/i.test(a.stats?.cefr || ""));
  if (state.diffFilter === "B2") articles = articles.filter((a) => /^(B1|B2)$/i.test(a.stats?.cefr || ""));
  if (query) {
    articles = articles.filter((article) => {
      const haystack = [
        article.title, article.subtitle, article.section, article.sourceFilename,
        ...asArray(article.ai_tags?.topics),
        ...asArray(article.ai_tags?.language),
        ...asArray(article.stats?.top_terms).map((t) => t.term || t),
      ].join(" ").toLowerCase();
      return haystack.includes(query);
    });
  }
  return articles;
}

// ───────── Library view ─────────
function renderLibraryView() {
  const articles = allArticles();
  const studiedCount = Object.keys(state.progress || {}).length;
  const unmastered = state.vocabulary.filter((v) => !v.recognise && !v.write).length;
  const lastOpened = articles.map((a) => a.last_opened_at).filter(Boolean).sort().reverse()[0];
  $("libSubline").textContent = `${articles.length} 篇文章 · ${unmastered} 个未掌握生词等待复习${lastOpened ? ` · 上次学习 ${timeAgo(lastOpened)}` : ""}`;

  $("libStats").innerHTML = `
    <div class="stat"><span class="l">本周精读</span><span class="n">${studiedCount}</span></div>
    <div class="stat"><span class="l">生词总数</span><span class="n">${state.vocabulary.length}</span></div>
    <div class="stat"><span class="l">待复习生词</span><span class="n">${unmastered}</span></div>
  `;

  const list = filteredArticles();
  if (!list.length) {
    $("libGrid").innerHTML = `<div class="lib-empty">
      <h3>文章库是空的</h3>
      <p>点击右上角"导入 EPUB / DOCX / TXT"添加文章。</p>
    </div>`;
    return;
  }

  $("libGrid").innerHTML = list.map((a) => {
    const stats = a.stats || {};
    const diff = stats.cefr || "?";
    const words = stats.word_count || 0;
    const minutes = stats.reading_minutes || Math.max(1, Math.round(words / 170));
    const progress = state.progress[a.id];
    const pct = progress?.status === "completed" ? 1 : (progress ? 0.5 : 0);
    const topic = asArray(a.ai_tags?.topics)[0] || "";
    return `<div class="card" data-open-article="${escapeHtml(a.id)}">
      <div class="src">${escapeHtml(a.sourceFilename?.replace(/\.[^.]+$/, "") || "")}${topic ? " · " + escapeHtml(topic) : ""}</div>
      <h3>${escapeHtml(a.title)}</h3>
      <div class="excerpt">${escapeHtml(a.subtitle || a.section || "—")}</div>
      <div class="row">
        <span class="tag diff" data-level="${escapeHtml(diff)}" style="font-size: 10.5px; padding: 1px 6px;">${escapeHtml(diff)}</span>
        <span>${formatNumber(words)} w</span>
        <span>·</span>
        <span>${minutes} min</span>
        <span style="flex: 1;"></span>
        <span>${Math.round(pct * 100)}%</span>
      </div>
      <div class="row" style="border-top: 0; padding: 0; margin: 0;">
        <div class="pg" style="margin-left: 0;"><span style="width: ${(pct * 100).toFixed(0)}%;"></span></div>
      </div>
    </div>`;
  }).join("");
}

// ───────── Article open ─────────
async function openArticle(articleId) {
  try {
    const data = await api(`/api/articles/${articleId}`);
    state.currentArticle = data.article;
    state.longSentences = asArray(state.currentArticle.long_sentence_analysis);
    state.readingQuestions = normalizeReadingQuestions(state.currentArticle.reading_questions);
    state.dictationItems = normalizeDictationItems(state.currentArticle.dictation_items);
    state.activeSentenceText = "";
    state.activeSentenceId = "";
    state.view = "study";
    state.step = "text";
    state.railTab = "context";
    showStudyView();
    renderArticleHeader();
    renderStepRail();
    renderWorkMain();
    renderRail();
    const notesEl = $("articleNotes");
    if (notesEl) {
      notesEl.value = data.article.notes || "";
      notesEl.removeEventListener("input", scheduleNotesSave);
      notesEl.removeEventListener("blur", saveNotesNow);
      notesEl.addEventListener("input", scheduleNotesSave);
      notesEl.addEventListener("blur", saveNotesNow);
    }
    renderSidebar();
  } catch (error) { alert(error.message); }
}

function showStudyView() {
  $("libraryView").classList.add("hidden");
  $("studyView").classList.remove("hidden");
  $("favoriteBtn").hidden = false;
  $("batchAnalyzeBtn").hidden = false;
  $("listenModeBtn").hidden = false;
  $("toggleRailBtn").hidden = false;
  applyTweaks();
}

function showLibraryView() {
  state.view = "library";
  $("studyView").classList.add("hidden");
  $("libraryView").classList.remove("hidden");
  $("favoriteBtn").hidden = true;
  $("batchAnalyzeBtn").hidden = true;
  $("listenModeBtn").hidden = true;
  $("toggleRailBtn").hidden = true;
  $("reopenRailBtn")?.classList.add("hidden");
  renderCrumbs();
  renderLibraryView();
}

function renderArticleHeader() {
  const a = state.currentArticle;
  if (!a) return;
  $("articleTitle").textContent = a.title || "";
  const stats = a.cleaned_stats || a.stats || {};
  const diff = stats.cefr || a.stats?.cefr || "?";
  const words = stats.word_count || a.stats?.word_count || 0;
  const minutes = stats.reading_minutes || Math.max(1, Math.round(words / 170));
  const topic = asArray(a.ai_tags?.topics)[0];
  $("articleTags").innerHTML = `
    <span class="tag diff" data-level="${escapeHtml(diff)}">${escapeHtml(diff)}</span>
    ${topic ? `<span class="tag topic">${escapeHtml(topic)}</span>` : ""}
    <span class="tag mono">${formatNumber(words)} w · ${minutes}′</span>
  `;
  $("favoriteBtn").innerHTML = a.favorite ? "★ 已收藏" : "☆ 收藏";
  renderCrumbs();
}

function renderCrumbs() {
  if (state.view === "library") {
    $("crumbs").innerHTML = `<span class="crumb-link" data-go="library">文章库</span>`;
    return;
  }
  const a = state.currentArticle;
  const src = (a?.sourceFilename || "").replace(/\.[^.]+$/, "");
  $("crumbs").innerHTML = `
    <span class="crumb-link" data-go="library">文章库</span>
    <span class="sep">/</span>
    ${src ? `<span>${escapeHtml(src)}</span><span class="sep">/</span>` : ""}
    <strong>${escapeHtml(a?.title || "")}</strong>
  `;
}

// ───────── Step rail ─────────
function renderStepRail() {
  const railEl = $("stepRail");
  if (!railEl) return;
  const a = state.currentArticle;
  const progressMap = {
    text: a?.text_check ? 1 : 0.1,
    long: state.longSentences.length ? 1 : 0,
    vocab: asArray(a?.vocabulary_analysis).length ? 1 : 0,
    reading: computeReadingProgress(),
    dict: computeDictationProgress(),
    write: a?.last_writing_feedback ? 1 : (state.writing.draft ? 0.4 : 0),
  };
  railEl.innerHTML = STEPS.map((s) => {
    const progress = progressMap[s.id] || 0;
    const done = progress >= 1;
    const sub = stepSub(s, a, progress);
    return `<button class="step ${done ? "done" : ""}" aria-current="${state.step === s.id}" data-step="${s.id}">
      <div class="step-row">
        <span class="step-num">${s.n}</span>
        <span class="step-title">${escapeHtml(s.title)}</span>
      </div>
      <div class="step-state">${escapeHtml(sub)}</div>
      <div class="step-bar"><span style="width: ${(progress * 100).toFixed(0)}%;"></span></div>
    </button>`;
  }).join("");
}

function computeReadingProgress() {
  const total = state.readingQuestions.length;
  if (!total) return 0;
  const responses = state.currentArticle?.reading_responses || {};
  return Object.keys(responses).length / total;
}

function computeDictationProgress() {
  const total = state.dictationItems.length;
  if (!total) return 0;
  const responses = state.currentArticle?.dictation_responses || {};
  return Object.keys(responses).length / total;
}

function stepSub(step, article, progress) {
  if (!article) return step.sub;
  if (step.id === "text") return article.text_check ? "已完成 AI 检查" : "正文清洗 · 抽段落";
  if (step.id === "long") return state.longSentences.length ? `${state.longSentences.length} 条长难句` : "结构 · 同位 · 修辞";
  if (step.id === "vocab") {
    const n = asArray(article.vocabulary_analysis).length;
    return n ? `${n} 个词条` : "AI 分层词条";
  }
  if (step.id === "reading") {
    const total = state.readingQuestions.length;
    const answered = Object.keys(article.reading_responses || {}).length;
    return total ? `${total} 题 · 已答 ${answered}` : "5 题 · 提交后批改";
  }
  if (step.id === "dict") {
    const total = state.dictationItems.length;
    const answered = Object.keys(article.dictation_responses || {}).length;
    return total ? `${total} 段 · 已交 ${answered}` : "整体 · 逐句 · 纠错 · 跟读";
  }
  if (step.id === "write") return article.last_writing_feedback ? "已批改" : (state.writing.draft ? "草稿已保存" : "summary · opinion · 仿写");
  return step.sub;
}

function setStep(id) {
  state.step = id;
  const map = STEPS.find((s) => s.id === id);
  if (map) state.railTab = map.railTab;
  renderStepRail();
  renderWorkMain();
  renderRail();
}

// ───────── Workspace renderers ─────────
function renderWorkMain() {
  // Article text is always in the centre; tasks live in the right panel
  renderReader($("workMain"));
}

// Reader
function splitSentences(paragraph) {
  const abbrs = ["Mr.", "Mrs.", "Ms.", "Dr.", "Prof.", "Sr.", "Jr.", "vs.", "i.e.", "e.g.", "etc.", "St.", "U.S.", "U.K.", "a.m.", "p.m.", "Inc.", "Ltd."];
  let protectedText = paragraph;
  abbrs.forEach((abbr) => {
    protectedText = protectedText.split(abbr).join(abbr.replace(/\./g, "\x01"));
  });
  protectedText = protectedText.replace(/(\d+)\.(\d+)/g, "$1\x01$2");
  const chunks = protectedText.split(/(?<=[.!?]["']?)\s+(?=[A-Z"'])/);
  return chunks.map((c) => c.replace(/\x01/g, ".").trim()).filter((c) => c.split(/\s+/).length > 2);
}

function paragraphsForReader(article) {
  const cleaned = asArray(article.cleaned_paragraphs);
  const raw = asArray(article.paragraphs);
  return cleaned.length ? cleaned : raw;
}

function renderReader(container) {
  const article = state.currentArticle;
  if (!article) { container.innerHTML = ""; return; }
  const paragraphs = paragraphsForReader(article);
  const longSet = new Set(state.longSentences.map((s) => (s.sentence || "").trim()));
  const totalP = paragraphs.length;
  const currentP = Math.min(totalP, 4);
  const percent = totalP ? Math.round((currentP / totalP) * 100) : 0;

  container.innerHTML = `
    <div class="reader" id="readerEl">
      <div class="reader-tools">
        <div class="group" data-density-toggle>
          <button data-density="loose" aria-pressed="${state.ui.density === 'loose'}">宽松</button>
          <button data-density="normal" aria-pressed="${state.ui.density === 'normal'}">标准</button>
          <button data-density="tight" aria-pressed="${state.ui.density === 'tight'}">紧凑</button>
        </div>
        <div class="group" data-size-toggle>
          <button data-size="-1" title="字号 -">A−</button>
          <button data-size="+1" title="字号 +">A+</button>
        </div>
        <button class="btn ghost" id="readAllBtn" title="朗读全文">♪ 朗读全文</button>
        ${state.step === "text" ? `<button class="btn" id="runTextCheckBtn">↻ AI 文本检查</button>` : ""}
        ${state.step === "text" ? `<button class="btn" id="exportAudioBtn" title="将文章导出为音频文件（含提示音+标题）">⬇ 生成音频</button>` : ""}
        ${state.step === "long" ? `<button class="btn" id="runLongSentenceBtn">⊕ 生成长难句</button>` : ""}
        <span class="spacer"></span>
        <span class="read-progress">P ${currentP} / ${totalP} · ${percent}%</span>
      </div>
      ${article.subtitle ? `<p class="lede">${escapeHtml(article.subtitle)}</p>` : ""}
      ${paragraphs.map((p, i) => `<p>
        <span class="pmark">P${i + 1}</span>
        ${renderSentencesHtml(p, longSet, i)}
      </p>`).join("")}
    </div>
  `;
}

function renderSentencesHtml(paragraph, longSet, paraIdx) {
  const chunks = splitSentences(paragraph);
  if (!chunks.length) {
    const trimmed = paragraph.trim();
    return `<span class="sent" data-sentence="${escapeHtml(trimmed)}" data-sid="p${paraIdx}-s0">${escapeHtml(trimmed)} </span>`;
  }
  return chunks.map((sentence, i) => {
    const clean = sentence.trim();
    if (!clean) return "";
    const isLong = longSet.has(clean);
    const sid = `p${paraIdx}-s${i}`;
    const cls = ["sent"];
    if (isLong) cls.push("long");
    if (state.activeSentenceId === sid) cls.push("active");
    return `<span class="${cls.join(" ")}" data-sentence="${escapeHtml(clean)}" data-sid="${sid}">${escapeHtml(clean)} </span>`;
  }).join("");
}

// Vocab board
function renderVocabBoard(container) {
  const article = state.currentArticle;
  const items = asArray(article?.vocabulary_analysis);
  if (!items.length) {
    container.innerHTML = `<div class="ai-card">
      <div class="ai-card-h"><span class="label">词汇掌握</span></div>
      <div class="ai-card-b">
        <p>AI 将筛选真正值得学习的词，并按核心必会词、阅读理解词、写作可用词、学术表达词、熟词僻义词分层。</p>
        <button class="btn primary" id="runVocabBtn" style="margin-top: 8px;">⚡ 生成 AI 词汇分析</button>
      </div>
    </div>`;
    return;
  }
  const knownTerms = new Set(state.vocabulary.map((v) => (v.term || "").toLowerCase()));
  container.innerHTML = `
    <div style="max-width: 880px;">
      <div class="row-flex" style="margin-bottom: 14px;">
        <div class="eyebrow">Vocabulary · 本文 ${items.length} 个词条</div>
        <span class="spacer"></span>
        <button class="btn" id="readAllVocabBtn">♪ 朗读全部</button>
        <button class="btn" id="reRunVocabBtn">↻ 重新生成</button>
      </div>
      <div class="voc-table">
        <div class="voc-row head">
          <span>词条</span><span>词性</span><span style="text-align: right;">分层</span><span style="text-align: right;">状态</span>
        </div>
        ${items.map((v, i) => renderVocabRow(v, i, knownTerms)).join("")}
      </div>
    </div>
  `;
}

function renderVocabRow(item, index, knownTerms) {
  const pos = item.pos || (item.term && item.term.includes(" ") ? "phrase" : "word");
  const inBook = knownTerms.has((item.term || "").toLowerCase());
  const status = inBook ? "review" : "new";
  const label = inBook ? "已收录" : "新词";
  return `<div class="voc-row">
    <span class="w">${escapeHtml(item.term || "")}${item.ipa ? `<small>${escapeHtml(item.ipa)}</small>` : ""}</span>
    <span class="pos">${escapeHtml(pos)}</span>
    <span class="freq">${escapeHtml(item.layer || "—")}</span>
    <span class="vstat"><span class="pill-status" data-state="${status}">${label}</span></span>
    <div class="gloss-line">${escapeHtml(item.translation || "")}${item.context ? ` · <em>${escapeHtml(item.context)}</em>` : ""}${item.synonym_note ? ` · ${escapeHtml(item.synonym_note)}` : ""}</div>
    <div class="gloss-actions">
      <button class="mini" data-say="${escapeHtml(item.term)}">♪ 朗读</button>
      <button class="mini" data-add-vocab="${index}">+ 加入生词本</button>
      <button class="mini" data-vocab-imitate="${index}">仿写练习</button>
    </div>
  </div>`;
}

// Reading board
function renderReadingBoard(container) {
  const questions = state.readingQuestions;
  const responses = state.currentArticle?.reading_responses || {};
  const answered = Object.keys(responses).length;
  if (!questions.length) {
    container.innerHTML = `<div class="ai-card">
      <div class="ai-card-h"><span class="label">阅读答题</span></div>
      <div class="ai-card-b">
        <p>AI 会基于文章生成英文阅读理解输出题，要求用英文作答。提交后给出评分、问题分析、优化答案与参考答案。</p>
        <button class="btn primary" id="runReadingQuestionsBtn" style="margin-top: 8px;">⚡ 生成阅读理解题</button>
      </div>
    </div>`;
    return;
  }
  container.innerHTML = `
    <div style="max-width: 820px;">
      <div class="row-flex" style="margin-bottom: 14px;">
        <div class="eyebrow">Reading questions · ${questions.length} 题 · 已答 ${answered} / ${questions.length}</div>
        <span class="spacer"></span>
        <button class="btn" id="reRunReadingQuestionsBtn">↻ 重新生成题目</button>
      </div>
      ${questions.map((q, i) => renderReadingQ(q, i, responses)).join("")}
    </div>
  `;
}

function renderReadingQ(question, index, responses) {
  const saved = responses[question.id] || {};
  const value = saved.answer || "";
  return `<div class="q" id="q-${index}">
    <div class="qh">
      <span class="qix">Q${index + 1}<small>${escapeHtml(question.focus || "")}</small></span>
      <span class="qt">${escapeHtml(question.question || "")}</span>
    </div>
    <div class="answer-zone">
      <textarea id="readingAnswer-${index}" placeholder="请用英文回答…">${escapeHtml(value)}</textarea>
      <div class="answer-actions">
        <span class="grow">提交后会显示评分、问题分析、优化答案和参考答案。</span>
        <button class="btn primary" data-grade-reading="${index}">提交并获取 AI 反馈 ⌘↵</button>
      </div>
    </div>
    ${saved.feedback ? renderReadingWhy(saved.feedback) : ""}
  </div>`;
}

function renderReadingWhy(feedback) {
  return `<div class="why">
    <strong>评分：${escapeHtml(feedback.score ?? "—")}</strong>
    ${feedback.content ? `<div class="why-section"><strong>内容</strong><p style="margin: 4px 0;">${escapeHtml(feedback.content)}</p></div>` : ""}
    ${feedback.logic ? `<div class="why-section"><strong>逻辑</strong><p style="margin: 4px 0;">${escapeHtml(feedback.logic)}</p></div>` : ""}
    ${feedback.grammar ? `<div class="why-section"><strong>语法</strong><p style="margin: 4px 0;">${escapeHtml(feedback.grammar)}</p></div>` : ""}
    ${feedback.vocabulary ? `<div class="why-section"><strong>词汇</strong><p style="margin: 4px 0;">${escapeHtml(feedback.vocabulary)}</p></div>` : ""}
    ${feedback.improved_answer ? `<div class="why-section"><strong>优化答案</strong><p style="margin: 4px 0; font-family: var(--serif);">${escapeHtml(feedback.improved_answer)}</p></div>` : ""}
    ${feedback.reference_answer ? `<div class="why-section"><strong>参考答案</strong><p style="margin: 4px 0; font-family: var(--serif);">${escapeHtml(feedback.reference_answer)}</p></div>` : ""}
  </div>`;
}

// Dictation board
function renderDictBoard(container) {
  const items = state.dictationItems;
  const responses = state.currentArticle?.dictation_responses || {};
  if (!items.length) {
    container.innerHTML = `<div class="ai-card">
      <div class="ai-card-h"><span class="label">听写跟读</span></div>
      <div class="ai-card-b">
        <p>系统从正文独立筛选 8–28 词的训练句。建议四轮：整体 → 逐句 → 纠错 → 跟读。</p>
        <button class="btn primary" id="prepareDictationBtn" style="margin-top: 8px;">⚡ 生成听写跟读材料</button>
      </div>
    </div>`;
    return;
  }
  container.innerHTML = `
    <div style="max-width: 820px;">
      <div class="row-flex" style="margin-bottom: 14px;">
        <div class="eyebrow">Dictation · ${items.length} 段 · 整体 → 逐句 → 纠错 → 跟读</div>
        <span class="spacer"></span>
        <button class="btn" id="prepareDictationBtn">↻ 换一组听写</button>
      </div>
      ${items.map((d, i) => renderDictItem(d, i, responses)).join("")}
    </div>
  `;
}

function renderDictItem(item, index, responses) {
  const saved = responses[item.id] || {};
  const round = state.dictation.rounds[item.id] || (saved.feedback ? 3 : 0);
  const speed = state.dictation.speeds[item.id] || 1;
  const dur = state.dictation.durations[item.id] || estimateDuration(item.source);
  const played = state.dictation.plays[item.id] ? 36 : 0;
  const value = saved.answer || "";
  return `<div class="dict-shell" data-dict-id="${escapeHtml(item.id)}">
    <div class="dict-h">
      <span class="nm">D${index + 1}</span>
      <span class="ix">${escapeHtml(item.focus || "提交前隐藏原文。")}</span>
      <div class="dict-rounds">
        <button class="round-pill" data-round="0" aria-pressed="${round === 0}">整体</button>
        <button class="round-pill" data-round="1" aria-pressed="${round === 1}">逐句</button>
        <button class="round-pill" data-round="2" aria-pressed="${round === 2}">纠错</button>
        <button class="round-pill" data-round="3" aria-pressed="${round === 3}">跟读</button>
      </div>
    </div>
    <div class="player">
      <button class="play-btn" data-play-dict title="播放">▶</button>
      <div class="wave">${renderWaveBars(64, played, index)}</div>
      <span class="player-meta">0:${String(Math.min(99, played > 0 ? Math.floor(dur / 2) : 0)).padStart(2, "0")} / 0:${String(Math.min(99, dur)).padStart(2, "0")}</span>
      <div class="player-speed">
        <button data-speed="0.7" aria-pressed="${speed === 0.7}">0.7×</button>
        <button data-speed="0.85" aria-pressed="${speed === 0.85}">0.85×</button>
        <button data-speed="1" aria-pressed="${speed === 1}">1×</button>
        <button data-speed="1.15" aria-pressed="${speed === 1.15}">1.15×</button>
      </div>
    </div>
    <div class="dict-input">
      <textarea id="dictationAnswer-${index}" placeholder="先听 1–2 遍，把听到的句子完整写下来。Cmd/Ctrl+↵ 提交。">${escapeHtml(value)}</textarea>
      <div class="row">
        <span class="grow">提交后会隐藏文本与你的输入对照，给出 diff、错因标签和读音建议。</span>
        <button class="btn" data-toggle-source="${index}">👁 显示原文</button>
        <button class="btn primary" data-dictation-feedback="${index}">${saved.feedback ? "重新提交" : "提交听写  ⌘↵"}</button>
      </div>
      <div class="diff-line hidden" data-source-line="${index}" style="margin-top: 8px;">${escapeHtml(item.source || "")}</div>
    </div>
    ${saved.feedback ? renderDictFeedback(saved.feedback, saved.meta, item) : ""}
  </div>`;
}

function estimateDuration(text) {
  const wc = String(text || "").split(/\s+/).filter(Boolean).length;
  return Math.max(6, Math.min(40, Math.round(wc * 0.55)));
}

function renderWaveBars(count, played, seed) {
  let bars = "";
  for (let j = 0; j < count; j++) {
    const h = 6 + Math.abs(Math.sin(j * 0.7 + seed) * 16) + (j % 5 === 0 ? 6 : 0);
    bars += `<span class="${j < played ? "played" : ""}" style="height: ${h.toFixed(1)}px;"></span>`;
  }
  return bars;
}

function buildDiffSpans(original, userAnswer, missingWords, spellingExtra) {
  const orig = (original || "").trim();
  const user = (userAnswer || "").trim();
  if (!orig) return [];
  const origTokens = orig.split(/(\s+)/);
  const userTokensRaw = user.split(/\s+/).filter(Boolean);
  const userTokens = new Set(userTokensRaw.map((w) => w.toLowerCase().replace(/[^a-z']/g, "")));
  const missingSet = new Set(asArray(missingWords).map((w) => String(w).toLowerCase().replace(/[^a-z']/g, "")));
  const spelling = asArray(spellingExtra).map((w) => String(w).toLowerCase());

  const spans = [];
  for (const tok of origTokens) {
    if (/^\s+$/.test(tok)) { spans.push({ kind: "ok", t: tok }); continue; }
    const norm = tok.toLowerCase().replace(/[^a-z']/g, "");
    if (!norm) { spans.push({ kind: "ok", t: tok }); continue; }
    if (missingSet.has(norm) || (!userTokens.has(norm) && userTokensRaw.length)) {
      spans.push({ kind: "add", t: tok });
    } else if (spelling.some((s) => s.replace(/[^a-z']/g, "") === norm)) {
      spans.push({ kind: "sp", t: tok });
    } else {
      spans.push({ kind: "ok", t: tok });
    }
  }
  return spans;
}

function deriveDictNotes(feedback) {
  const notes = [];
  asArray(feedback.listening_notes).forEach((n) => {
    if (typeof n === "string") notes.push({ tag: "LISTEN", text: n });
    else if (n && typeof n === "object") notes.push({ tag: (n.tag || "LISTEN").toUpperCase().slice(0, 6), text: n.note || n.text || "" });
  });
  asArray(feedback.missing_words).forEach((w) => {
    notes.push({ tag: "WORD", text: `漏听词：${typeof w === "string" ? w : (w.term || JSON.stringify(w))}` });
  });
  asArray(feedback.spelling_or_extra).forEach((w) => {
    notes.push({ tag: "SPELL", text: `拼写或多余：${typeof w === "string" ? w : (w.term || JSON.stringify(w))}` });
  });
  if (feedback.why_difficult) notes.push({ tag: "WHY", text: feedback.why_difficult });
  return notes;
}

function renderDictFeedback(feedback, meta, item) {
  const spans = buildDiffSpans(feedback.original || item.source, feedback.user_answer, feedback.missing_words, feedback.spelling_or_extra);
  const notes = deriveDictNotes(feedback);
  const provider = meta?.provider || "deepseek";
  return `<div class="dict-feedback">
    <div class="head">
      <span class="what">AI 纠错</span>
      <span class="score">${escapeHtml(feedback.score ?? "—")} <small>/100</small></span>
      <span class="spacer"></span>
      <span class="muted" style="font-size: 11px; font-family: var(--mono);">${escapeHtml(provider)}${meta?.cached ? " · 已缓存" : ""}</span>
    </div>
    <div class="original-line"><strong>原文</strong>${escapeHtml(feedback.original || item.source || "")}</div>
    ${feedback.user_answer ? `<div class="original-line"><strong>你的输入</strong>${escapeHtml(feedback.user_answer)}</div>` : ""}
    <div class="diff">${spans.map((s) => `<span class="${s.kind}">${escapeHtml(s.t)}</span>`).join("")}</div>
    ${notes.length ? `<ul class="tagged-list">${notes.map((n) => `<li><span class="tg">${escapeHtml(n.tag)}</span><span class="body">${escapeHtml(n.text)}</span></li>`).join("")}</ul>` : ""}
    <div class="row-flex" style="margin-top: 12px;">
      <button class="btn" data-say="${escapeHtml(feedback.original || item.source || "")}">♪ 慢速对照播</button>
      <span class="spacer"></span>
    </div>
  </div>`;
}

// Writing board
function renderWriteBoard(container) {
  const article = state.currentArticle;
  const saved = article?.last_writing_feedback || {};
  const draft = state.writing.draft || saved.content || "";
  const task = state.writing.task || saved.task || "Write an 80-word neutral summary.";
  const wc = countWords(draft);
  const sc = countSentences(draft);
  container.innerHTML = `
    <div style="max-width: 880px;">
      <div class="row-flex" style="margin-bottom: 14px;">
        <div class="eyebrow">Writing · 输出与反馈</div>
        <span class="spacer"></span>
      </div>

      <div class="write-shell">
        <div class="write-h">
          <span class="label">写作任务</span>
          <select id="writingTask">
            <option>Write an 80-word neutral summary.</option>
            <option>Write a 150-word response using at least three article expressions.</option>
            <option>Imitate one paragraph structure from the article.</option>
          </select>
        </div>
        <div class="write-body">
          <p style="font-family: var(--serif); color: var(--ink-2); font-style: italic;">120–150 words · summary + opinion · 使用本文至少 3 个核心表达。</p>
        </div>
      </div>

      <div class="write-shell">
        <div class="write-h">
          <span class="label">你的草稿</span>
          <span class="meta">draft · ${draft ? "已保存" : "未提交"}</span>
        </div>
        <div class="write-body">
          <textarea id="writingDraft" placeholder="在这里写英文摘要、观点短评或仿写段落…">${escapeHtml(draft)}</textarea>
        </div>
        <div class="write-foot">
          <span>${wc} words · ${sc} sentences · avg ${sc ? (wc / sc).toFixed(1) : 0} w/sent</span>
          <span class="grow"></span>
          <button class="btn primary" id="runWritingFeedbackBtn">✓ 提交批改  ⌘↵</button>
        </div>
      </div>

      ${saved.feedback ? renderWritingResult(saved.feedback, saved.meta) : ""}
    </div>
  `;
  setSelectValue($("writingTask"), task);
}

function countWords(text) { return String(text || "").trim().split(/\s+/).filter(Boolean).length; }
function countSentences(text) { return String(text || "").split(/[.!?]+/).filter((s) => s.trim().length > 2).length; }

function renderWritingResult(feedback, meta) {
  const overall = Number(feedback.score ?? 0);
  const overallTen = overall > 10 ? (overall / 2).toFixed(1) : overall.toFixed(1);
  const subScore = (sectionText) => {
    if (!sectionText) return "—";
    const len = String(sectionText).length;
    if (len > 120) return "9.0";
    if (len > 60) return "8.0";
    return "7.0";
  };
  return `
    <div class="row-flex" style="margin: 22px 0 12px;">
      <div class="eyebrow">AI 反馈 · 已批改</div>
      <span class="spacer"></span>
      <span class="score">${overallTen} <small>/10</small></span>
    </div>
    <div class="fb-grid">
      <div class="fb-cell">
        <div class="l">CONTENT</div>
        <div class="v">${subScore(feedback.content)}<small>/10</small></div>
        <div class="body">${escapeHtml((feedback.content || "").slice(0, 120))}</div>
      </div>
      <div class="fb-cell">
        <div class="l">LANGUAGE</div>
        <div class="v">${subScore(feedback.grammar)}<small>/10</small></div>
        <div class="body">${escapeHtml((feedback.grammar || "").slice(0, 120))}</div>
      </div>
      <div class="fb-cell">
        <div class="l">STRUCTURE</div>
        <div class="v">${subScore(feedback.structure)}<small>/10</small></div>
        <div class="body">${escapeHtml((feedback.structure || "").slice(0, 120))}</div>
      </div>
      <div class="fb-cell">
        <div class="l">VOCAB</div>
        <div class="v">${subScore(feedback.vocabulary)}<small>/10</small></div>
        <div class="body">${escapeHtml((feedback.vocabulary || "").slice(0, 120))}</div>
      </div>
    </div>

    ${feedback.improved_version ? `<div class="write-shell">
      <div class="write-h"><span class="label">修改版</span></div>
      <div class="write-body"><p style="font-family: var(--serif);">${escapeHtml(feedback.improved_version)}</p></div>
    </div>` : ""}

    <div class="write-shell">
      <div class="write-h"><span class="label">分项点评</span></div>
      <div class="write-body">
        <ul class="tagged-list" style="grid-template-columns: 1fr;">
          ${["content","structure","grammar","vocabulary","next_step"].filter((k) => feedback[k]).map((k) => `
            <li style="grid-template-columns: 96px 1fr;">
              <span class="tg">${k.toUpperCase()}</span>
              <span class="body">${escapeHtml(feedback[k])}</span>
            </li>`).join("")}
        </ul>
      </div>
    </div>
  `;
}

// ───────── AI rail ─────────
function renderRail() {
  const tabs = $("railTabs");
  const a = state.currentArticle;
  const rp = computeReadingProgress();
  const dp = computeDictationProgress();
  const progressMap = {
    context: a?.text_check ? "done" : "",
    long: state.longSentences.length ? "done" : "",
    vocab: asArray(a?.vocabulary_analysis).length ? "done" : "",
    reading: rp >= 1 ? "done" : rp > 0 ? "partial" : "",
    dict: dp >= 1 ? "done" : dp > 0 ? "partial" : "",
    write: a?.last_writing_feedback ? "done" : state.writing.draft ? "partial" : "",
  };
  tabs.querySelectorAll("button").forEach((b) => {
    b.setAttribute("aria-selected", String(b.dataset.tab === state.railTab));
    b.dataset.progress = progressMap[b.dataset.tab] || "";
  });
  const body = $("railBody");
  if (state.railTab === "context") { body.innerHTML = renderRailContext(); return; }
  if (state.railTab === "long") { body.innerHTML = renderRailLong(); return; }
  if (state.railTab === "vocab") { body.innerHTML = renderRailVocab(); return; }
  if (state.railTab === "reading") { body.innerHTML = renderRailReading(); return; }
  if (state.railTab === "dict") { body.innerHTML = renderRailDict(); return; }
  if (state.railTab === "write") {
    body.innerHTML = renderRailWrite();
    const task = state.writing.task || state.currentArticle?.last_writing_feedback?.task || "Write an 80-word neutral summary.";
    setSelectValue($("writingTask"), task);
  }
}

function renderRailContext() {
  const a = state.currentArticle;
  if (state.activeSentenceText) {
    return `<div class="ai-card">
      <div class="ai-card-h">
        <span class="label">已选择句子</span>
        <span class="meta">${state.activeSentenceText.split(/\s+/).length} 词</span>
      </div>
      <div class="ai-card-b">
        <div class="quote">${escapeHtml(state.activeSentenceText)}</div>
        <div class="row-flex">
          <button class="btn primary" id="analyzeSelectedSentenceBtn">⚡ AI 分析句子</button>
          <button class="btn" data-say="${escapeHtml(state.activeSentenceText)}">♪ 朗读</button>
        </div>
      </div>
    </div>
    <div id="sentenceAnalysisHolder"></div>`;
  }
  if (!a) {
    return `<div class="rail-empty">在左侧选择一篇文章开始精读。</div>`;
  }
  const summary = (a.section || "").trim();
  return `<div class="ai-card">
    <div class="ai-card-h">
      <span class="label">本文要点</span>
      <span class="meta">${a.last_opened_at ? timeAgo(a.last_opened_at) : "刚打开"}</span>
    </div>
    <div class="ai-card-b">
      ${summary ? `<p style="margin: 0 0 8px;">${escapeHtml(summary)}</p>` : ""}
      ${a.subtitle ? `<p style="margin: 0 0 8px; font-style: italic; color: var(--ink-2);">${escapeHtml(a.subtitle)}</p>` : ""}
      <ul style="margin: 6px 0 0; padding-left: 18px; font-size: 13px; color: var(--ink-2);">
        <li>核心张力：扫描原文寻找作者立场。</li>
        <li>关键句：可点击文中带虚线的句子查看长难句解析。</li>
        <li>建议顺序：文本检查 → 长难句 → 词汇 → 阅读 → 听写 → 写作。</li>
      </ul>
    </div>
  </div>
  <div class="rail-empty">
    点击正文里<strong>任意句子</strong>或<span style="border-bottom: 1px dotted var(--accent);">带虚线的长难句</span>，<br>
    面板会显示对应的解析、改写、同位结构与生词。
    <div class="hint-key"><span class="kbd">Click</span> 句子 · <span class="kbd">D</span> 解析 · <span class="kbd">[</span> / <span class="kbd">]</span> 切换步骤</div>
  </div>`;
}

function renderRailLong() {
  const items = state.longSentences;
  if (!items.length) {
    return `<div class="ai-card">
      <div class="ai-card-h"><span class="label">长难句</span></div>
      <div class="ai-card-b">
        <p>系统会从文章中筛选最值得精读的长难句。</p>
        <button class="btn primary" id="runLongSentenceBtn" style="margin-top: 8px; width: 100%; justify-content: center;">⚡ 生成长难句解析</button>
      </div>
    </div>`;
  }
  return items.slice(0, 6).map((item, i) => {
    const struct = item.sentence_structure || {};
    const tree = [];
    if (struct.main_clause) tree.push({ role: "main", label: "MAIN CLAUSE", text: struct.main_clause });
    asArray(struct.modifiers).forEach((m) => tree.push({ role: "sub", label: "MODIFIER", text: typeof m === "string" ? m : (m.text || JSON.stringify(m)) }));
    if (struct.logic) tree.push({ role: "adv", label: "LOGIC", text: struct.logic });
    asArray(struct.reading_order).forEach((r) => tree.push({ role: "sub", label: "ORDER", text: r }));
    return `<div class="ai-card">
      <div class="ai-card-h">
        <span class="label">长难句 · L${i + 1}</span>
        <span class="meta">${asArray(item.difficult_vocabulary).length} 词</span>
      </div>
      <div class="ai-card-b">
        <div class="quote">${escapeHtml(item.sentence || "")}</div>
        <div class="row-flex" style="margin-bottom: 10px;">
          <button class="mini" aria-pressed="true">结构树</button>
          <button class="mini" data-say="${escapeHtml(item.sentence || "")}">♪ 朗读</button>
        </div>
        ${tree.length ? `<div class="tree">${tree.map((n) => `<div class="lvl" data-role="${n.role}"><span class="role" data-role="${n.role}">${escapeHtml(n.label)}</span>${escapeHtml(n.text)}</div>`).join("")}</div>` : ""}
        ${item.translation ? `<div style="margin-top: 10px; padding-top: 8px; border-top: 1px dashed var(--line); font-size: 13px; color: var(--ink-2);"><strong style="font-size: 10.5px; font-family: var(--mono); color: var(--muted); text-transform: uppercase;">中译</strong><br>${escapeHtml(item.translation)}</div>` : ""}
      </div>
    </div>`;
  }).join("");
}

function renderRailVocab() {
  const items = asArray(state.currentArticle?.vocabulary_analysis);
  if (!items.length) {
    return `<div class="ai-card">
      <div class="ai-card-h"><span class="label">词汇</span></div>
      <div class="ai-card-b">
        <p>AI 会按分层（核心 / 阅读理解 / 写作可用 / 学术 / 熟词僻义）筛选词条。</p>
        <button class="btn primary" id="runVocabBtn" style="margin-top: 8px; width: 100%; justify-content: center;">⚡ 生成词汇分析</button>
      </div>
    </div>`;
  }
  const knownTerms = new Set(state.vocabulary.map((v) => (v.term || "").toLowerCase()));
  return `<div>
    <div class="row-flex" style="margin-bottom: 10px;">
      <span class="eyebrow">词汇 · ${items.length} 条</span>
      <span class="spacer"></span>
      <button class="btn" id="reRunVocabBtn" style="font-size: 11px;">↻ 重新生成</button>
    </div>
    ${items.map((v, i) => {
      const inBook = knownTerms.has((v.term || "").toLowerCase());
      return `<div class="gloss">
        <div class="w">${escapeHtml(v.term || "")}${v.ipa ? ` <small>${escapeHtml(v.ipa)}</small>` : ""}<small>${escapeHtml(v.layer || "")}</small></div>
        <div class="d">${escapeHtml(v.translation || "")}${v.context ? `<em> · ${escapeHtml(v.context)}</em>` : ""}</div>
        <div class="b">
          <button class="mini" data-say="${escapeHtml(v.term || "")}">♪</button>
          ${inBook
            ? `<span class="mini" style="cursor:default;opacity:0.55;">已收录</span>`
            : `<button class="mini" data-add-vocab="${i}">+ 生词本</button>`}
          <button class="mini" data-vocab-imitate="${i}">仿写</button>
        </div>
      </div>`;
    }).join("")}
  </div>`;
}

function renderRailReading() {
  const questions = state.readingQuestions;
  const responses = state.currentArticle?.reading_responses || {};
  const answered = Object.keys(responses).length;
  if (!questions.length) {
    return `<div class="ai-card">
      <div class="ai-card-h"><span class="label">阅读答题</span></div>
      <div class="ai-card-b">
        <p>AI 基于文章生成 5 道英文阅读理解题，要求英文作答，提交后获得评分和分析。</p>
        <button class="btn primary" id="runReadingQuestionsBtn" style="margin-top: 8px; width: 100%; justify-content: center;">⚡ 生成阅读理解题</button>
      </div>
    </div>`;
  }
  const dots = questions.map((q) => {
    const r = responses[q.id];
    const c = r ? (Number(r.feedback?.score || 0) >= 14 ? "good" : "warn") : "line";
    return `<span style="flex:1;height:5px;border-radius:3px;background:var(--${c});"></span>`;
  }).join("");
  return `<div>
    <div class="row-flex" style="margin-bottom: 8px;">
      <span class="eyebrow">阅读理解 · ${answered}/${questions.length} 已答</span>
      <span class="spacer"></span>
      <button class="btn" id="reRunReadingQuestionsBtn" style="font-size: 11px;">↻ 重新生成</button>
    </div>
    <div style="display:flex;gap:3px;margin-bottom:14px;">${dots}</div>
    ${questions.map((q, i) => {
      const saved = responses[q.id] || {};
      return `<div class="rail-q" id="q-${i}">
        <div class="rq-head">
          <span class="qix">Q${i + 1}</span>
          <span class="qt">${escapeHtml(q.question || "")}</span>
        </div>
        <textarea class="rail-textarea" id="readingAnswer-${i}" placeholder="请用英文回答…">${escapeHtml(saved.answer || "")}</textarea>
        <div class="rq-foot">
          <button class="btn primary" data-grade-reading="${i}" style="width:100%;justify-content:center;">提交并获取 AI 反馈</button>
        </div>
        ${saved.feedback ? `<div class="rail-fb">
          <div class="rq-score">评分 <strong>${escapeHtml(saved.feedback.score ?? "—")}</strong> / 20</div>
          ${saved.feedback.content ? `<p><strong>内容</strong> ${escapeHtml(saved.feedback.content)}</p>` : ""}
          ${saved.feedback.improved_answer ? `<p><strong>优化</strong> <em>${escapeHtml(saved.feedback.improved_answer)}</em></p>` : ""}
        </div>` : ""}
      </div>`;
    }).join("")}
  </div>`;
}

function renderRailDict() {
  const items = state.dictationItems;
  const responses = state.currentArticle?.dictation_responses || {};
  if (!items.length) {
    return `<div class="ai-card">
      <div class="ai-card-h"><span class="label">听写跟读</span></div>
      <div class="ai-card-b">
        <p>系统从正文筛选 8–28 词的训练句。建议四轮：整体 → 逐句 → 纠错 → 跟读。</p>
        <button class="btn primary" id="prepareDictationBtn" style="margin-top: 8px; width: 100%; justify-content: center;">⚡ 生成听写跟读材料</button>
      </div>
    </div>`;
  }
  return `<div>
    <div class="row-flex" style="margin-bottom: 10px;">
      <span class="eyebrow">听写 · ${items.length} 段 · 整体→逐句→纠错→跟读</span>
      <span class="spacer"></span>
      <button class="btn" id="prepareDictationBtn" style="font-size: 11px;">↻ 换一组</button>
    </div>
    ${items.map((d, i) => {
      const saved = responses[d.id] || {};
      const round = state.dictation.rounds[d.id] || (saved.feedback ? 3 : 0);
      const speed = state.dictation.speeds[d.id] || 1;
      const dur = state.dictation.durations[d.id] || estimateDuration(d.source);
      const played = state.dictation.plays[d.id] ? 24 : 0;
      return `<div class="dict-shell" data-dict-id="${escapeHtml(d.id)}">
        <div class="dict-h">
          <span class="nm">D${i + 1}</span>
          <span class="ix">${escapeHtml(d.focus || "提交前隐藏原文。")}</span>
          <div class="dict-rounds">
            <button class="round-pill" data-round="0" aria-pressed="${round === 0}">整体</button>
            <button class="round-pill" data-round="1" aria-pressed="${round === 1}">逐句</button>
            <button class="round-pill" data-round="2" aria-pressed="${round === 2}">纠错</button>
            <button class="round-pill" data-round="3" aria-pressed="${round === 3}">跟读</button>
          </div>
        </div>
        <div class="player">
          <button class="play-btn" data-play-dict title="播放">▶</button>
          <div class="wave">${renderWaveBars(32, played, i)}</div>
          <span class="player-meta">0:${String(played > 0 ? Math.floor(dur / 2) : 0).padStart(2, "0")} / 0:${String(Math.min(99, dur)).padStart(2, "0")}</span>
          <div class="player-speed">
            <button data-speed="0.7" aria-pressed="${speed === 0.7}">0.7×</button>
            <button data-speed="0.85" aria-pressed="${speed === 0.85}">0.85×</button>
            <button data-speed="1" aria-pressed="${speed === 1}">1×</button>
            <button data-speed="1.15" aria-pressed="${speed === 1.15}">1.15×</button>
          </div>
        </div>
        <div class="dict-input">
          <textarea class="rail-textarea" id="dictationAnswer-${i}" placeholder="先听 1–2 遍，把听到的句子完整写下来。Ctrl+↵ 提交。">${escapeHtml(saved.answer || "")}</textarea>
          <div style="display:flex;gap:6px;margin-top:6px;">
            <button class="btn" data-toggle-source="${i}">👁 原文</button>
            <button class="btn primary" data-dictation-feedback="${i}" style="flex:1;justify-content:center;">${saved.feedback ? "重新提交" : "提交听写"}</button>
          </div>
          <div class="diff-line hidden" data-source-line="${i}" style="margin-top: 8px;">${escapeHtml(d.source || "")}</div>
        </div>
        ${saved.feedback ? renderDictFeedback(saved.feedback, saved.meta, d) : ""}
      </div>`;
    }).join("")}
  </div>`;
}

function renderRailWrite() {
  const article = state.currentArticle;
  const saved = article?.last_writing_feedback || {};
  const draft = state.writing.draft || saved.content || "";
  const task = state.writing.task || saved.task || "Write an 80-word neutral summary.";
  const wc = countWords(draft);
  const sc = countSentences(draft);
  const fb = saved.feedback;
  return `<div>
    <div class="ai-card" style="margin-bottom: 10px;">
      <div class="ai-card-h"><span class="label">写作任务</span></div>
      <div class="ai-card-b" style="padding-top: 6px;">
        <select id="writingTask" style="width:100%;">
          <option>Write an 80-word neutral summary.</option>
          <option>Write a 150-word response using at least three article expressions.</option>
          <option>Imitate one paragraph structure from the article.</option>
        </select>
      </div>
    </div>
    <div class="ai-card">
      <div class="ai-card-h">
        <span class="label">草稿</span>
        <span class="meta">${draft ? `${wc} words · ${sc} sent` : "未提交"}</span>
      </div>
      <div class="ai-card-b" style="padding-top: 6px;">
        <textarea class="rail-textarea rail-textarea-tall" id="writingDraft" placeholder="在这里写英文摘要、观点短评或仿写段落…">${escapeHtml(draft)}</textarea>
        <div style="display:flex;justify-content:flex-end;margin-top:8px;">
          <button class="btn primary" id="runWritingFeedbackBtn">✓ 提交批改  ⌘↵</button>
        </div>
      </div>
    </div>
    ${fb ? `<div class="ai-card" style="margin-top: 10px;">
      <div class="ai-card-h">
        <span class="label">AI 批改</span>
        <span class="score">${(Number(fb.score ?? 0) > 10 ? Number(fb.score) / 2 : Number(fb.score ?? 0)).toFixed(1)} <small>/10</small></span>
      </div>
      <div class="ai-card-b">
        ${["content","structure","grammar","vocabulary","next_step"].filter((k) => fb[k]).map((k) => `
          <div style="margin-bottom: 8px;">
            <strong style="font-size:10.5px;font-family:var(--mono);color:var(--muted);text-transform:uppercase;">${k.replace("_", " ")}</strong>
            <p style="margin:2px 0;font-size:13px;color:var(--ink-2);">${escapeHtml(fb[k])}</p>
          </div>`).join("")}
        ${fb.improved_version ? `<div style="margin-top:10px;padding-top:8px;border-top:1px dashed var(--line);">
          <strong style="font-size:10.5px;font-family:var(--mono);color:var(--muted);">IMPROVED</strong>
          <p style="font-family:var(--serif);font-size:13px;margin:4px 0;">${escapeHtml(fb.improved_version)}</p>
        </div>` : ""}
      </div>
    </div>` : ""}
  </div>`;
}

// ───────── Sentence selection ─────────
function selectSentence(sentenceEl) {
  document.querySelectorAll(".sent.active").forEach((s) => s.classList.remove("active"));
  sentenceEl.classList.add("active");
  state.activeSentenceText = sentenceEl.dataset.sentence || "";
  state.activeSentenceId = sentenceEl.dataset.sid || "";
  if (sentenceEl.classList.contains("long")) {
    state.railTab = "long";
  } else {
    state.railTab = "context";
  }
  renderRail();
}

// ───────── Notes auto-save ─────────
let notesSaveTimer = null;
async function saveNotesNow() {
  const textarea = $("articleNotes");
  if (!textarea || !state.currentArticle) return;
  const articleId = state.currentArticle.id;
  const notes = textarea.value;
  const status = $("notesStatus");
  try {
    await api(`/api/articles/${articleId}/notes`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notes }),
    });
    if (state.currentArticle?.id === articleId) state.currentArticle.notes = notes;
    if (status) { status.textContent = "· saved"; setTimeout(() => { status.textContent = ""; }, 2000); }
  } catch {
    if (status) status.textContent = "· 保存失败";
  }
}

function scheduleNotesSave() {
  clearTimeout(notesSaveTimer);
  notesSaveTimer = setTimeout(saveNotesNow, 1500);
}

// ───────── TTS ─────────
function fallbackBrowserSpeak(text) {
  if (!("speechSynthesis" in window)) { alert("当前浏览器不支持语音合成。"); return; }
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "en-US";
  u.rate = 0.9;
  window.speechSynthesis.speak(u);
}

async function speak(text) {
  const clean = String(text || "").trim();
  if (!clean) return;
  try {
    if (state.currentAudio) { state.currentAudio.pause(); state.currentAudio = null; }
    const data = await api("/api/audio/speech", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: clean }),
    });
    if (data.audio_url) state.currentAudio = new Audio(data.audio_url);
    else if (data.audio_data) state.currentAudio = new Audio(`data:${data.mime_type || "audio/wav"};base64,${data.audio_data}`);
    if (!state.currentAudio) throw new Error("AI 朗读没有返回音频。");
    await state.currentAudio.play();
  } catch (error) {
    console.warn("AI 朗读失败，已回退浏览器朗读：", error);
    fallbackBrowserSpeak(clean);
  }
}

async function exportArticleAudio() {
  const btn = $("exportAudioBtn");
  if (!state.currentArticle) return;
  const original = btn ? btn.textContent : "";

  // Auto-run text check first if not yet done
  if (!state.currentArticle.text_check) {
    if (btn) { btn.disabled = true; btn.textContent = "文本检查中…"; }
    try {
      const data = await api(`/api/articles/${state.currentArticle.id}/text-check`, { method: "POST" });
      if (data.article) state.currentArticle = data.article;
      renderWorkMain();
      renderStepRail();
    } catch (err) {
      alert(`文本检查失败：${err.message}`);
      if (btn) { btn.disabled = false; btn.textContent = original; }
      return;
    }
  }

  if (btn) { btn.disabled = true; btn.textContent = "生成中…"; }
  try {
    const response = await fetch(`/api/articles/${state.currentArticle.id}/export-audio`);
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || `请求失败 (${response.status})`);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${state.currentArticle.title || "article"}.wav`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch (err) {
    alert(`音频生成失败：${err.message}`);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = original; }
  }
}

// ───────── Settings dialog ─────────
function fillSettingsForm() {
  const providers = state.settings.providers || state.config.providers || {};
  const tasks = state.settings.task_providers || state.config.task_providers || {};
  const deepseek = providers.deepseek || {};
  const qwen = providers.qwen || {};
  const qwenTts = qwen.tts || {};
  $("textProviderInput").value = tasks.text || state.settings.primary_provider || "deepseek";
  $("imageProviderInput").value = tasks.image || "qwen";
  $("audioProviderInput").value = tasks.audio || "qwen";
  $("deepseekBaseInput").value = deepseek.base_url || "https://api.deepseek.com";
  setSelectValue($("deepseekModelInput"), deepseek.model || "deepseek-v4-flash");
  $("qwenBaseInput").value = qwen.base_url || "https://dashscope.aliyuncs.com/compatible-mode/v1";
  setSelectValue($("qwenModelInput"), qwen.model || "qwen-plus");
  setSelectValue($("qwenImageModelInput"), qwen.image_model || "qwen-vl-plus");
  $("qwenTtsBaseInput").value = qwenTts.base_url || "https://dashscope.aliyuncs.com/api/v1";
  setSelectValue($("qwenTtsModelInput"), qwenTts.model || "qwen3-tts-flash");
  $("qwenTtsVoiceInput").value = qwenTts.voice || "Ethan";
  $("qwenTtsLanguageInput").value = qwenTts.language_type || "English";
  $("deepseekKeyInput").placeholder = deepseek.api_key_masked || "留空则使用环境变量";
  $("qwenKeyInput").placeholder = qwen.api_key_masked || "留空则使用环境变量";

  const stats = state.summary || {};
  $("globalStats").innerHTML = `
    <div><strong>${formatNumber(stats.source_count)}</strong><span>文件</span></div>
    <div><strong>${formatNumber(stats.article_count)}</strong><span>文章</span></div>
    <div><strong>${formatNumber(stats.word_count)}</strong><span>词数</span></div>
    <div><strong>${formatNumber(stats.reading_minutes)}</strong><span>分钟</span></div>
  `;
  const rows = [
    `<div><strong>文本</strong><span>${escapeHtml(tasks.text || "deepseek")} · ${escapeHtml(providers[tasks.text]?.model || state.config.primary_model || "")}</span></div>`,
    `<div><strong>图片</strong><span>${escapeHtml(tasks.image || "qwen")} · ${escapeHtml(qwen.image_model || "")}</span></div>`,
    `<div><strong>音频</strong><span>${escapeHtml(tasks.audio || "qwen")} · ${escapeHtml(qwenTts.model || "")}</span></div>`,
    ...["deepseek", "qwen"].map((name) => {
      const p = providers[name] || {};
      return `<div><strong>${name}</strong><span>${p.configured ? p.model : "未配置 · fallback"}</span></div>`;
    }),
  ];
  $("aiStatus").innerHTML = rows.join("");
}

function settingsPayload() {
  return {
    primary_provider: $("textProviderInput").value,
    text_provider: $("textProviderInput").value,
    image_provider: $("imageProviderInput").value,
    audio_provider: $("audioProviderInput").value,
    deepseek_api_key: $("deepseekKeyInput").value,
    deepseek_base_url: $("deepseekBaseInput").value,
    deepseek_model: $("deepseekModelInput").value,
    qwen_api_key: $("qwenKeyInput").value,
    qwen_base_url: $("qwenBaseInput").value,
    qwen_model: $("qwenModelInput").value,
    qwen_image_model: $("qwenImageModelInput").value,
    qwen_tts_base_url: $("qwenTtsBaseInput").value,
    qwen_tts_model: $("qwenTtsModelInput").value,
    qwen_tts_voice: $("qwenTtsVoiceInput").value,
    qwen_tts_language_type: $("qwenTtsLanguageInput").value,
  };
}

function renderProviderTestResult(data) {
  const meta = data.meta || {};
  if (meta.used_ai) return `连接成功：${meta.model} @ ${meta.base_url}`;
  return `连接失败：${meta.error || JSON.stringify(data.result)}`;
}

// ───────── Reload current article ─────────
async function reloadCurrentArticle() {
  if (!state.currentArticle) return;
  const data = await api(`/api/articles/${state.currentArticle.id}`);
  state.currentArticle = data.article;
  state.longSentences = asArray(state.currentArticle.long_sentence_analysis);
  state.readingQuestions = normalizeReadingQuestions(state.currentArticle.reading_questions);
  state.dictationItems = normalizeDictationItems(state.currentArticle.dictation_items);
  renderArticleHeader();
  renderStepRail();
  renderWorkMain();
  renderRail();
}

// ───────── Batch analysis ─────────
async function runBatchAnalyze() {
  const btn = $("batchAnalyzeBtn");
  if (!state.currentArticle) return;
  const id = state.currentArticle.id;
  const original = btn.textContent;
  btn.disabled = true;
  const steps = [
    { label: "文本检查", run: () => api(`/api/articles/${id}/text-check`, { method: "POST" }) },
    { label: "长难句", run: () => api(`/api/articles/${id}/long-sentences`, { method: "POST" }) },
    { label: "词汇分析", run: () => api(`/api/articles/${id}/vocabulary/analyze`, { method: "POST" }) },
    { label: "阅读理解题", run: () => api(`/api/articles/${id}/reading/questions`, { method: "POST" }) },
    { label: "听写材料", run: () => api(`/api/articles/${id}/dictation/items`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ count: 6 }) }) },
  ];
  for (let i = 0; i < steps.length; i++) {
    const step = steps[i];
    btn.textContent = `${step.label}… (${i + 1}/${steps.length})`;
    try {
      const data = await step.run();
      if (data.article) state.currentArticle = data.article;
      if (data.sentences) state.longSentences = data.sentences;
      if (data.questions) state.readingQuestions = normalizeReadingQuestions(data.questions);
      if (data.items && step.label === "听写材料") state.dictationItems = normalizeDictationItems(data.items);
    } catch (err) {
      alert(`批量分析在"${step.label}"步骤出错：${err.message}`);
      break;
    }
  }
  btn.disabled = false;
  btn.textContent = original;
  renderStepRail();
  renderWorkMain();
  renderRail();
}

// ───────── Event delegation ─────────
document.addEventListener("click", async (event) => {
  const rawTarget = event.target;
  const targetEl = rawTarget instanceof Element ? rawTarget : rawTarget?.parentElement;
  if (!targetEl) return;
  const target = targetEl.closest(
    "button, [data-open-article], [data-step], [data-tab], [data-list], [data-diff], .sent, [data-say], [data-add-vocab], [data-vocab-imitate], [data-grade-reading], [data-dictation-feedback], [data-toggle-source], [data-go], [data-density], [data-size], [data-theme], [data-round], [data-speed], [data-play-dict], [data-delete-source], .crumb-link"
  ) || targetEl;

  // Sidebar list switch
  if (target.matches(".side-tabs button[data-list]")) {
    state.currentList = target.dataset.list;
    renderSidebar();
    if (state.view === "library") renderLibraryView();
    return;
  }
  // Difficulty filter
  if (target.matches(".chip[data-diff]")) {
    state.diffFilter = target.dataset.diff || "";
    renderSidebar();
    if (state.view === "library") renderLibraryView();
    return;
  }
  // Open article
  if (target.matches("[data-open-article]")) {
    await openArticle(target.dataset.openArticle);
    return;
  }
  // Crumb / library go
  if (target.matches("[data-go]") && target.dataset.go === "library") {
    showLibraryView();
    return;
  }
  // Step rail
  if (target.matches("[data-step]")) {
    setStep(target.dataset.step);
    return;
  }
  // Rail tab — also drives the main step so top rail is not needed
  if (target.matches(".rail-tabs button[data-tab]")) {
    const tabToStep = { context: "text", long: "long", vocab: "vocab", reading: "reading", dict: "dict", write: "write" };
    const stepId = tabToStep[target.dataset.tab];
    if (stepId) {
      setStep(stepId);
    } else {
      state.railTab = target.dataset.tab;
      renderRail();
    }
    return;
  }
  // Sentence click
  if (target.matches(".sent")) {
    selectSentence(target);
    return;
  }
  // Say (TTS)
  if (target.matches("[data-say]")) {
    await speak(target.dataset.say);
    return;
  }
  // Reader controls
  if (target.matches("[data-density]")) {
    state.ui.density = target.dataset.density;
    saveTweaks();
    applyTweaks();
    document.querySelectorAll("[data-density-toggle] button").forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.density === state.ui.density))
    );
    return;
  }
  if (target.matches("[data-size]")) {
    const delta = Number(target.dataset.size);
    state.ui.readerSize = Math.max(15, Math.min(22, state.ui.readerSize + delta));
    saveTweaks();
    applyTweaks();
    return;
  }
  if (target.matches("[data-theme]")) {
    state.ui.theme = target.dataset.theme || "";
    saveTweaks();
    applyTweaks();
    return;
  }
  // Toolbar
  if (target.id === "toggleSideBtn" || target.id === "collapseSideBtn") {
    state.ui.showSide = !state.ui.showSide;
    saveTweaks();
    applyTweaks();
    return;
  }
  if (target.id === "reopenSideBtn") {
    state.ui.showSide = true;
    saveTweaks();
    applyTweaks();
    return;
  }
  if (target.id === "toggleRailBtn" || target.id === "closeRailBtn") {
    state.ui.showRail = !state.ui.showRail;
    saveTweaks();
    applyTweaks();
    return;
  }
  if (target.id === "reopenRailBtn") {
    state.ui.showRail = true;
    saveTweaks();
    applyTweaks();
    return;
  }
  if (target.id === "exportAudioBtn") { await exportArticleAudio(); return; }
  if (target.id === "kbdHelpBtn") { $("kbdDialog").showModal(); return; }
  if (target.id === "closeKbdBtn") { $("kbdDialog").close(); return; }
  if (target.id === "openSettingsBtn") { $("settingsDialog").showModal(); return; }
  if (target.id === "closeSettingsBtn") { $("settingsDialog").close(); return; }
  if (target.id === "saveSettingsBtn") { saveTweaks(); applyTweaks(); $("settingsDialog").close(); return; }
  if (target.id === "saveAllSettingsBtn") {
    await runAction(target, "保存中…", async () => {
      state.settings = await api("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settingsPayload()),
      });
      $("deepseekKeyInput").value = "";
      $("qwenKeyInput").value = "";
      saveTweaks();
      applyTweaks();
      await refreshAll();
      $("settingsDialog").close();
    });
    return;
  }
  if (target.id === "testDeepseekBtn" || target.id === "testQwenBtn") {
    const provider = target.id === "testDeepseekBtn" ? "deepseek" : "qwen";
    const box = $(provider === "deepseek" ? "deepseekTestResult" : "qwenTestResult");
    await runAction(target, "测试中…", async () => {
      const data = await api(`/api/settings/test/${provider}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settingsPayload()),
      });
      box.textContent = renderProviderTestResult(data);
    });
    return;
  }
  if (target.id === "uploadBtn" || target.id === "libUploadBtn") {
    $("fileInput").click();
    return;
  }
  if (target.id === "rebuildLibraryBtn") {
    if (!confirm("将使用新解析规则重建文章库，AI 分析缓存会被保留。继续吗？")) return;
    await runAction(target, "重建中…", async () => {
      await api("/api/library/rebuild", { method: "POST" });
      await refreshAll();
      alert("文章库已重建。");
    });
    return;
  }
  if (target.matches("[data-collapse-source]")) {
    const key = target.dataset.collapseSource;
    if (state.collapsedSources.has(key)) state.collapsedSources.delete(key);
    else state.collapsedSources.add(key);
    renderSidebar();
    return;
  }
  if (target.matches("[data-collapse-section]")) {
    const key = target.dataset.collapseSection;
    if (state.collapsedSections.has(key)) state.collapsedSections.delete(key);
    else state.collapsedSections.add(key);
    renderSidebar();
    return;
  }
  if (target.matches(".section-group-h")) {
    const key = target.dataset.collapseSection;
    if (key) {
      if (state.collapsedSections.has(key)) state.collapsedSections.delete(key);
      else state.collapsedSections.add(key);
      renderSidebar();
    }
    return;
  }
  if (target.matches("[data-delete-source]")) {
    const sourceId = target.dataset.deleteSource;
    if (!confirm("确认删除此来源及其全部文章？此操作不可撤销。")) return;
    event.stopPropagation();
    await runAction(target, "删除中…", async () => {
      await api(`/api/sources/${sourceId}`, { method: "DELETE" });
      if (state.currentArticle) {
        const stillExists = allArticles().some((a) => a.id === state.currentArticle.id);
        if (!stillExists) showLibraryView();
      }
      await refreshAll();
    });
    return;
  }
  if (target.id === "favoriteBtn") {
    await api(`/api/articles/${state.currentArticle.id}/favorite`, { method: "POST" });
    await reloadCurrentArticle();
    await refreshAll();
    return;
  }
  if (target.id === "batchAnalyzeBtn") { await runBatchAnalyze(); return; }
  if (target.id === "listenModeBtn") {
    if (!state.currentArticle) return;
    window.location.href = `/static/listen.html?id=${encodeURIComponent(state.currentArticle.id)}`;
    return;
  }
  // Step actions
  if (target.id === "runTextCheckBtn") {
    await runAction(target, "检查中…", async () => {
      const data = await api(`/api/articles/${state.currentArticle.id}/text-check`, { method: "POST" });
      if (data.article) state.currentArticle = data.article;
      renderWorkMain();
      renderStepRail();
    });
    return;
  }
  if (target.id === "runLongSentenceBtn") {
    await runAction(target, "解析中…", async () => {
      const data = await api(`/api/articles/${state.currentArticle.id}/long-sentences`, { method: "POST" });
      if (data.article) state.currentArticle = data.article;
      state.longSentences = data.sentences || [];
      renderWorkMain();
      renderStepRail();
      renderRail();
    });
    return;
  }
  if (target.id === "runVocabBtn" || target.id === "reRunVocabBtn") {
    await runAction(target, "筛选中…", async () => {
      const data = await api(`/api/articles/${state.currentArticle.id}/vocabulary/analyze`, { method: "POST" });
      if (data.article) state.currentArticle = data.article;
      renderWorkMain();
      renderStepRail();
      renderRail();
    });
    return;
  }
  if (target.matches("[data-add-vocab]")) {
    const idx = Number(target.dataset.addVocab);
    const item = asArray(state.currentArticle.vocabulary_analysis)[idx];
    if (!item) return;
    await runAction(target, "加入中…", async () => {
      await api("/api/vocabulary", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          article_id: state.currentArticle.id,
          term: item.term,
          translation: item.translation,
          context: item.context,
          layer: item.layer,
          kind: item.term?.includes(" ") ? "phrase" : "word",
        }),
      });
      await refreshAll();
      renderWorkMain();
    });
    return;
  }
  if (target.id === "runReadingQuestionsBtn" || target.id === "reRunReadingQuestionsBtn") {
    await runAction(target, "生成中…", async () => {
      const data = await api(`/api/articles/${state.currentArticle.id}/reading/questions`, { method: "POST" });
      if (data.article) state.currentArticle = data.article;
      state.readingQuestions = normalizeReadingQuestions(data.questions);
      renderWorkMain();
      renderStepRail();
      renderRail();
    });
    return;
  }
  if (target.matches("[data-grade-reading]")) {
    const idx = Number(target.dataset.gradeReading);
    const question = state.readingQuestions[idx];
    const answer = $(`readingAnswer-${idx}`)?.value;
    if (!answer?.trim()) { alert("请先输入答案。"); return; }
    await runAction(target, "评分中…", async () => {
      const data = await api(`/api/articles/${state.currentArticle.id}/reading/grade`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question_id: question.id, question: question.question, answer }),
      });
      if (data.article) state.currentArticle = data.article;
      renderWorkMain();
      renderStepRail();
      state.railTab = "reading";
      renderRail();
    });
    return;
  }
  if (target.id === "prepareDictationBtn") {
    await runAction(target, "生成中…", async () => {
      const data = await api(`/api/articles/${state.currentArticle.id}/dictation/items`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ count: 6 }),
      });
      if (data.article) state.currentArticle = data.article;
      state.dictationItems = normalizeDictationItems(data.items);
      renderWorkMain();
      renderStepRail();
    });
    return;
  }
  if (target.matches("[data-toggle-source]")) {
    const idx = target.dataset.toggleSource;
    const line = document.querySelector(`[data-source-line="${idx}"]`);
    if (line) line.classList.toggle("hidden");
    return;
  }
  if (target.matches("[data-dictation-feedback]")) {
    const idx = Number(target.dataset.dictationFeedback);
    const item = state.dictationItems[idx];
    const answer = $(`dictationAnswer-${idx}`)?.value;
    if (!answer?.trim()) { alert("请先听写后再提交。"); return; }
    await runAction(target, "批改中…", async () => {
      const data = await api(`/api/articles/${state.currentArticle.id}/dictation/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: item.source, answer }),
      });
      if (data.article) state.currentArticle = data.article;
      state.dictation.rounds[item.id] = 2;
      renderWorkMain();
      renderStepRail();
      state.railTab = "dict";
      renderRail();
    });
    return;
  }
  if (target.matches("[data-play-dict]")) {
    const shell = target.closest(".dict-shell");
    if (!shell) return;
    const id = shell.dataset.dictId;
    const item = state.dictationItems.find((d) => d.id === id);
    if (!item) return;
    state.dictation.plays[id] = true;
    renderWorkMain();
    await speak(item.source);
    return;
  }
  if (target.matches("[data-round]")) {
    const shell = target.closest(".dict-shell");
    if (!shell) return;
    const id = shell.dataset.dictId;
    state.dictation.rounds[id] = Number(target.dataset.round);
    renderWorkMain();
    return;
  }
  if (target.matches("[data-speed]")) {
    const shell = target.closest(".dict-shell");
    if (!shell) return;
    const id = shell.dataset.dictId;
    state.dictation.speeds[id] = Number(target.dataset.speed);
    renderWorkMain();
    return;
  }
  if (target.id === "runWritingFeedbackBtn") {
    const task = $("writingTask").value;
    const content = $("writingDraft").value;
    if (!content.trim()) { alert("请先写草稿。"); return; }
    state.writing.task = task;
    state.writing.draft = content;
    await runAction(target, "批改中…", async () => {
      const data = await api(`/api/articles/${state.currentArticle.id}/writing/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task, content }),
      });
      if (data.article) state.currentArticle = data.article;
      renderWorkMain();
      renderStepRail();
      state.railTab = "write";
      renderRail();
      await refreshAll();
    });
    return;
  }
  if (target.id === "analyzeSelectedSentenceBtn") {
    const sentence = state.activeSentenceText;
    if (!sentence) return;
    const holder = $("sentenceAnalysisHolder");
    if (holder) holder.innerHTML = `<div class="loading-state">分析中…</div>`;
    try {
      const data = await api(`/api/articles/${state.currentArticle.id}/sentence/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sentence }),
      });
      if (data.article) state.currentArticle = data.article;
      const a = data.analysis || {};
      const struct = a.sentence_structure || {};
      if (holder) holder.innerHTML = `<div class="ai-card">
        <div class="ai-card-h"><span class="label">AI 句子分析</span><span class="meta">${escapeHtml(data.meta?.provider || "")}</span></div>
        <div class="ai-card-b">
          ${struct.main_clause ? `<p><strong style="font-size: 11px; font-family: var(--mono); color: var(--muted); text-transform: uppercase;">主干</strong><br>${escapeHtml(struct.main_clause)}</p>` : ""}
          ${struct.logic ? `<p><strong style="font-size: 11px; font-family: var(--mono); color: var(--muted); text-transform: uppercase;">逻辑</strong><br>${escapeHtml(struct.logic)}</p>` : ""}
          ${a.translation ? `<p><strong style="font-size: 11px; font-family: var(--mono); color: var(--muted); text-transform: uppercase;">译文</strong><br>${escapeHtml(a.translation)}</p>` : ""}
          ${asArray(a.difficult_vocabulary).length ? `<div style="margin-top: 8px;"><strong style="font-size: 11px; font-family: var(--mono); color: var(--muted); text-transform: uppercase;">较难词汇</strong>${asArray(a.difficult_vocabulary).map((d) => typeof d === "string" ? `<p style="margin: 4px 0;">${escapeHtml(d)}</p>` : `<p style="margin: 4px 0;"><strong>${escapeHtml(d.term || "")}</strong> · ${escapeHtml(d.meaning || d.translation || "")}</p>`).join("")}</div>` : ""}
        </div>
      </div>`;
    } catch (error) {
      if (holder) holder.innerHTML = `<div class="error-state">${escapeHtml(error.message)}</div>`;
    }
    return;
  }
  if (target.id === "readAllBtn") {
    const article = state.currentArticle;
    if (!article) return;
    const text = paragraphsForReader(article).slice(0, 4).join(" ");
    await speak(text);
    return;
  }
  if (target.id === "readAllVocabBtn") {
    const items = asArray(state.currentArticle?.vocabulary_analysis);
    if (!items.length) return;
    await speak(items.slice(0, 8).map((v) => v.term).join(", "));
    return;
  }
  if (target.id === "prevArticleBtn" || target.id === "nextArticleBtn") {
    const articles = filteredArticles();
    if (!articles.length || !state.currentArticle) return;
    const idx = articles.findIndex((a) => a.id === state.currentArticle.id);
    const next = target.id === "prevArticleBtn"
      ? articles[(idx - 1 + articles.length) % articles.length]
      : articles[(idx + 1) % articles.length];
    if (next) await openArticle(next.id);
    return;
  }
});

// Search & writing draft input
document.addEventListener("input", (event) => {
  const t = event.target;
  if (!(t instanceof Element)) return;
  if (t.id === "searchInput") {
    state.searchQuery = t.value;
    renderSidebar();
    if (state.view === "library") renderLibraryView();
  }
  if (t.id === "writingDraft") { state.writing.draft = t.value; }
  if (t.id === "writingTask") { state.writing.task = t.value; }
  if (t.id === "readerSizeRange") {
    state.ui.readerSize = Number(t.value);
    saveTweaks();
    applyTweaks();
  }
});

document.addEventListener("change", (event) => {
  const t = event.target;
  if (!(t instanceof Element)) return;
  if (t.id === "writingTask") state.writing.task = t.value;
});

// File upload
document.addEventListener("change", async (event) => {
  const t = event.target;
  if (!(t instanceof HTMLInputElement)) return;
  if (t.id !== "fileInput") return;
  const file = t.files?.[0];
  if (!file) return;
  $("uploadStatus").textContent = "正在导入并解析…";
  try {
    const formData = new FormData();
    formData.append("file", file);
    const result = await api("/api/upload", { method: "POST", body: formData });
    $("uploadStatus").textContent = result.duplicate ? "该文件已导入。" : "导入完成。";
    await refreshAll();
  } catch (error) {
    $("uploadStatus").textContent = error.message;
  } finally {
    t.value = "";
  }
});

// Keyboard shortcuts
document.addEventListener("keydown", (event) => {
  if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLSelectElement) {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      const inDict = event.target.id?.startsWith("dictationAnswer-");
      const inWrite = event.target.id === "writingDraft";
      const inReading = event.target.id?.startsWith("readingAnswer-");
      if (inDict) {
        const idx = event.target.id.split("-")[1];
        document.querySelector(`[data-dictation-feedback="${idx}"]`)?.click();
      } else if (inWrite) {
        $("runWritingFeedbackBtn")?.click();
      } else if (inReading) {
        const idx = event.target.id.split("-")[1];
        document.querySelector(`[data-grade-reading="${idx}"]`)?.click();
      }
      event.preventDefault();
    }
    return;
  }
  if (state.view !== "study") return;
  if (event.key === "[") {
    const idx = STEPS.findIndex((s) => s.id === state.step);
    if (idx > 0) setStep(STEPS[idx - 1].id);
    event.preventDefault();
  } else if (event.key === "]") {
    const idx = STEPS.findIndex((s) => s.id === state.step);
    if (idx < STEPS.length - 1) setStep(STEPS[idx + 1].id);
    event.preventDefault();
  } else if (event.key === "\\") {
    state.ui.showRail = !state.ui.showRail;
    saveTweaks();
    applyTweaks();
    event.preventDefault();
  } else if (event.key === "d" || event.key === "D") {
    if (state.activeSentenceText) {
      state.railTab = "context";
      renderRail();
      setTimeout(() => $("analyzeSelectedSentenceBtn")?.click(), 50);
      event.preventDefault();
    }
  }
});

// ───────── Rail resizer (drag to resize AI panel) ─────────
function initRailResizer() {
  const resizer = $("workResizer");
  const work = $("workspace");
  if (!resizer || !work) return;

  const saved = localStorage.getItem("railWidth");
  if (saved) work.style.setProperty("--rail-width", saved + "px");

  let startX = 0, startWidth = 0;

  resizer.addEventListener("mousedown", (e) => {
    startX = e.clientX;
    startWidth = $("workSide").offsetWidth;
    resizer.classList.add("dragging");
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    e.preventDefault();
  });

  function onMove(e) {
    const dx = startX - e.clientX;
    const newWidth = Math.max(300, Math.min(720, startWidth + dx));
    work.style.setProperty("--rail-width", newWidth + "px");
  }

  function onUp() {
    resizer.classList.remove("dragging");
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
    const w = parseInt(work.style.getPropertyValue("--rail-width"));
    if (!isNaN(w)) localStorage.setItem("railWidth", w);
  }
}

// Boot
loadTweaks();
refreshAll();
initRailResizer();
