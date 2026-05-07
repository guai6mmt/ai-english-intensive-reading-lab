const state = {
  library: { sources: [] },
  summary: {},
  config: {},
  currentList: "all",
  currentArticle: null,
  useCleanedText: true,
  vocabulary: [],
  outputs: [],
  progress: {},
  settings: {},
  activeSentence: "",
  longSentences: [],
  readingQuestions: [],
  dictationItems: [],
  currentAudio: null,
};

const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function splitListText(value) {
  return String(value ?? "")
    .split(/\r?\n|[;；]|[,，]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function asArray(value) {
  if (Array.isArray(value)) return value;
  if (value === null || value === undefined || value === "") return [];
  if (typeof value === "string") {
    const parts = splitListText(value);
    return parts.length > 1 ? parts : [value.trim()].filter(Boolean);
  }
  if (typeof value === "object") {
    if (Array.isArray(value.items)) return value.items;
    return Object.keys(value).length ? [value] : [];
  }
  return [value];
}

function renderPills(value, renderItem = (item) => escapeHtml(item)) {
  const items = asArray(value);
  if (!items.length) return `<p class="muted">暂无。</p>`;
  return `<div class="tag-row">${items.map((item) => `<span class="pill">${renderItem(item)}</span>`).join("")}</div>`;
}

function normalizeDictationItems(value) {
  return asArray(value)
    .map((item, index) => {
      if (typeof item === "string") {
        return { id: `dictation-${index}-${item}`, source: item, focus: "提交前隐藏原文。先播放音频，再输入你听到的内容。" };
      }
      if (!item || typeof item !== "object") return null;
      const source = item.source || item.sentence || item.text || "";
      return {
        ...item,
        id: item.id || `dictation-${index}-${source}`,
        source,
        focus: item.focus || "提交前隐藏原文。先播放音频，再输入你听到的内容。",
        rounds: asArray(item.rounds),
      };
    })
    .filter((item) => item && item.source);
}

function normalizeReadingQuestions(value) {
  return asArray(value)
    .map((item, index) => {
      if (typeof item === "string") return { id: `question-${index}-${item}`, question: item };
      if (!item || typeof item !== "object") return null;
      const question = item.question || item.text || "";
      return { ...item, id: item.id || `question-${index}-${question}`, question };
    })
    .filter((item) => item && item.question);
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString();
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
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(`服务器返回无效响应 (${response.status})，请查看后端日志。`);
  }
  if (!response.ok) {
    throw new Error(data.detail || `请求失败 (${response.status})`);
  }
  return data;
}

function allArticles() {
  return asArray(state.library.sources).flatMap((source) =>
    asArray(source.articles).map((article) => ({
      ...article,
      sourceFilename: source.filename,
    }))
  );
}

async function refreshAll() {
  const [library, vocab, outputs, progress, config, settings] = await Promise.all([
    api("/api/library"),
    api("/api/vocabulary"),
    api("/api/outputs"),
    api("/api/progress"),
    api("/api/config"),
    api("/api/settings"),
  ]);
  state.library = library.library;
  state.summary = library.summary;
  state.vocabulary = asArray(vocab.items);
  state.outputs = asArray(outputs.items);
  state.progress = progress.items && typeof progress.items === "object" && !Array.isArray(progress.items) ? progress.items : {};
  state.config = config;
  state.settings = settings;
  renderGlobalStats();
  renderAiStatus();
  renderFilterOptions();
  renderSources();
  renderArticles();
  renderVocabulary();
  renderOutputs();
  fillSettingsForm();
}

function renderGlobalStats() {
  const s = state.summary || {};
  $("globalStats").innerHTML = `
    <div><strong>${formatNumber(s.source_count)}</strong><span>文件</span></div>
    <div><strong>${formatNumber(s.article_count)}</strong><span>文章</span></div>
    <div><strong>${formatNumber(s.word_count)}</strong><span>词数</span></div>
    <div><strong>${formatNumber(s.reading_minutes)}</strong><span>分钟</span></div>
  `;
}

function renderAiStatus() {
  const providers = state.config.providers || {};
  const tasks = state.config.task_providers || { text: state.config.primary_provider || "deepseek", image: "qwen", audio: "qwen" };
  const rows = [
    `<div><strong>文本</strong><span>${escapeHtml(tasks.text || "deepseek")} · ${escapeHtml(providers[tasks.text]?.model || state.config.primary_model || "")}</span></div>`,
    `<div><strong>图片</strong><span>${escapeHtml(tasks.image || "qwen")} · ${escapeHtml(providers.qwen?.image_model || "qwen-vl-plus")}</span></div>`,
    `<div><strong>音频</strong><span>${escapeHtml(tasks.audio || "qwen")} · ${escapeHtml(providers.qwen?.tts?.model || "qwen3-tts-flash")}</span></div>`,
    ...["deepseek", "qwen"]
    .map((name) => {
      const item = providers[name] || {};
      return `<div><strong>${name}</strong><span>${item.configured ? item.model : "未配置 · fallback"}</span></div>`;
    }),
  ];
  $("aiStatus").innerHTML = rows.join("");
}

function fillSettingsForm() {
  const providers = state.settings.providers || state.config.providers || {};
  const tasks = state.settings.task_providers || state.config.task_providers || {};
  const deepseek = providers.deepseek || {};
  const qwen = providers.qwen || {};
  const qwenTts = qwen.tts || {};
  $("textProviderInput").value = tasks.text || state.settings.primary_provider || state.config.primary_provider || "deepseek";
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
}

function setSelectValue(select, value) {
  if (![...select.options].some((option) => option.value === value)) {
    select.append(new Option(value, value));
  }
  select.value = value;
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
  if (meta.used_ai) {
    return `连接成功：${meta.model} @ ${meta.base_url}`;
  }
  return `连接失败：${meta.error || JSON.stringify(data.result)}`;
}

function renderFilterOptions() {
  const articles = allArticles();
  const topics = new Set();
  const language = new Set();
  articles.forEach((article) => {
    asArray(article.ai_tags?.topics).forEach((tag) => topics.add(tag));
    asArray(article.ai_tags?.language).forEach((tag) => language.add(tag));
  });
  fillSelect($("topicFilter"), "全部题材", [...topics].sort());
  fillSelect($("languageFilter"), "全部语言特点", [...language].sort());
}

function fillSelect(select, firstLabel, values) {
  const current = select.value;
  select.innerHTML = `<option value="">${firstLabel}</option>${values
    .map((value) => `<option>${escapeHtml(value)}</option>`)
    .join("")}`;
  select.value = values.includes(current) ? current : "";
}

function renderSources() {
  // 来源信息已整合进文章列表分组，此处清空避免重复
  $("sourceList").innerHTML = "";
}

function filteredArticles() {
  const query = $("searchInput").value.trim().toLowerCase();
  const topic = $("topicFilter").value;
  const lang = $("languageFilter").value;
  const difficulty = $("difficultyFilter").value;
  let articles = allArticles();

  if (state.currentList === "favorite") {
    articles = articles.filter((article) => article.favorite);
  }
  if (state.currentList === "recent") {
    const progressIds = new Set(Object.keys(state.progress || {}));
    articles = articles.filter((article) => progressIds.has(article.id));
    articles.sort((a, b) => String(b.last_opened_at || "").localeCompare(String(a.last_opened_at || "")));
  }
  if (query) {
    articles = articles.filter((article) => {
      const haystack = [
        article.title,
        article.subtitle,
        article.section,
        article.sourceFilename,
        ...asArray(article.ai_tags?.topics),
        ...asArray(article.ai_tags?.language),
        ...asArray(article.stats?.top_terms).map((t) => t.term),
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(query);
    });
  }
  if (topic) {
    articles = articles.filter((article) => asArray(article.ai_tags?.topics).includes(topic));
  }
  if (lang) {
    articles = articles.filter((article) => asArray(article.ai_tags?.language).includes(lang));
  }
  if (difficulty) {
    articles = articles.filter((article) => article.stats.cefr === difficulty);
  }
  return articles;
}

function renderArticles() {
  const articles = filteredArticles();
  if (!articles.length) {
    $("articleList").innerHTML = `<div class="article-row-empty">没有匹配文章。</div>`;
    return;
  }

  // 按来源文件分组
  const groups = new Map();
  articles.forEach((article) => {
    const key = article.source_id || article.sourceFilename || "unknown";
    if (!groups.has(key)) {
      groups.set(key, { sourceId: article.source_id || "", filename: article.sourceFilename, articles: [] });
    }
    groups.get(key).articles.push(article);
  });

  $("articleList").innerHTML = [...groups.values()]
    .map((group) => {
      const label = (group.filename || "未知来源").replace(/\.[^.]+$/, "");
      const rows = group.articles
        .map((article) => {
          const studied = state.progress[article.id];
          const dot = studied
            ? `<span class="progress-dot" title="已学习"></span>`
            : `<span class="progress-dot-empty"></span>`;
          const active = state.currentArticle?.id === article.id ? " active" : "";
          return `<div class="article-row${active}" data-open-article="${article.id}">
            ${dot}
            <span class="article-row-title">${escapeHtml(article.title)}</span>
            <span class="pill pill-xs">${escapeHtml(article.stats?.cefr || "?")}</span>
          </div>`;
        })
        .join("");
      return `<div class="source-group">
        <div class="source-group-header">
          <span class="source-group-name">${escapeHtml(label)}</span>
          <span class="pill pill-xs">${group.articles.length}篇</span>
          ${group.sourceId ? `<button class="del-source-btn" data-delete-source="${escapeHtml(group.sourceId)}" title="删除此来源">✕</button>` : ""}
        </div>
        ${rows}
      </div>`;
    })
    .join("");
}

async function openArticle(articleId) {
  const data = await api(`/api/articles/${articleId}`);
  state.currentArticle = data.article;
  state.useCleanedText = true;
  state.activeSentence = "";
  state.longSentences = asArray(state.currentArticle.long_sentence_analysis);
  state.readingQuestions = normalizeReadingQuestions(state.currentArticle.reading_questions);
  state.dictationItems = normalizeDictationItems(state.currentArticle.dictation_items);
  showView("studyView");
  $("appShell").classList.add("teacher-collapsed");
  renderStudyHeader();
  renderArticleText();
  renderAllPanels();
  setTeacherContent(`<p class="muted">已载入文章。建议先完成“文本检查”，再进入文章总览和段落分析。</p>`);
  const notesEl = $("articleNotes");
  if (notesEl) {
    notesEl.value = data.article.notes || "";
    notesEl.removeEventListener("input", scheduleNotesSave);
    notesEl.removeEventListener("blur", saveNotesNow);
    notesEl.addEventListener("input", scheduleNotesSave);
    notesEl.addEventListener("blur", saveNotesNow);
  }
}

function renderStudyHeader() {
  const article = state.currentArticle;
  $("articleSection").textContent = article.section || "Article";
  $("articleTitle").textContent = article.title;
  $("articleSubtitle").textContent = article.subtitle || "";
  $("favoriteBtn").textContent = article.favorite ? "取消收藏" : "收藏";
  $("toggleTeacherBtn").textContent = $("appShell").classList.contains("teacher-collapsed") ? "显示AI面板" : "隐藏AI面板";
  const tags = [
    article.stats.cefr,
    ...asArray(article.ai_tags?.topics),
    ...asArray(article.ai_tags?.language),
  ].slice(0, 5);
  $("articleTags").innerHTML = asArray(tags).map((tag) => `<span class="pill">${escapeHtml(tag)}</span>`).join("");
}

function renderArticleText() {
  const article = state.currentArticle;
  const cleanedParagraphs = asArray(article.cleaned_paragraphs);
  const paragraphs = state.useCleanedText && cleanedParagraphs.length
    ? cleanedParagraphs
    : asArray(article.paragraphs);
  const articleText = $("articleText");
  articleText.innerHTML = paragraphs
    .map((paragraph, index) => `<p><span class="paragraph-label">P${index + 1}</span>${renderSentences(paragraph)}</p>`)
    .join("");
  articleText.onclick = (event) => {
    const sentence = sentenceFromClick(event);
    if (sentence) selectSentence(sentence);
  };
}

function splitSentences(paragraph) {
  // 保护缩写词中的句号，避免错误断句
  const abbrs = ["Mr.", "Mrs.", "Ms.", "Dr.", "Prof.", "Sr.", "Jr.", "vs.", "i.e.", "e.g.", "etc.", "St.", "U.S.", "U.K.", "a.m.", "p.m.", "Inc.", "Ltd."];
  let protected_ = paragraph;
  abbrs.forEach((abbr) => {
    protected_ = protected_.split(abbr).join(abbr.replace(/\./g, "\x01"));
  });
  // 保护数字中的小数点
  protected_ = protected_.replace(/(\d+)\.(\d+)/g, "$1\x01$2");
  const chunks = protected_.split(/(?<=[.!?]["']?)\s+(?=[A-Z"'])/) ;
  return chunks.map((c) => c.replace(/\x01/g, ".").trim()).filter((c) => c.split(/\s+/).length > 2);
}

function renderSentences(paragraph) {
  const chunks = splitSentences(paragraph);
  if (!chunks.length) {
    return `<span class="sentence" data-sentence="${escapeHtml(paragraph)}">${escapeHtml(paragraph)} </span>`;
  }
  return chunks
    .map((sentence) => {
      const clean = sentence.trim();
      return clean
        ? `<span class="sentence" data-sentence="${escapeHtml(clean)}">${escapeHtml(clean)} </span>`
        : "";
    })
    .join("");
}

function renderAllPanels() {
  renderTextPanel();
  renderOverviewPanel();
  renderParagraphPanel();
  renderSentencePanel();
  renderVocabPanel();
  renderReadingPanel();
  renderDictationPanel();
  renderWritingPanel();
}

function renderTextPanel() {
  const article = state.currentArticle;
  const check = article.text_check;
  $("textTab").innerHTML = `
    <div class="two-col">
      <div class="analysis-card">
        <h3>文本检查</h3>
        <p>正式学习前先让 AI 检查格式、换行、OCR、标点和明显拼写疑点。系统会保留原文版本和 AI 清洗版本。</p>
        <div class="inline-actions">
          <button class="primary-btn" id="runTextCheckBtn">运行 AI 文本检查</button>
          <button class="secondary-btn" id="toggleTextVersionBtn">${state.useCleanedText ? "显示原文版本" : "显示清洗版本"}</button>
        </div>
      </div>
      <div class="analysis-card">
        <h3>检查结果</h3>
        ${check ? renderTextCheckResult(check) : renderTextCheckResult({
          summary: "已应用导入时规则清洗。可继续运行 DeepSeek 做二次检查。",
          removed_paragraphs: article.removed_paragraphs || [],
          normalization_notes: article.normalization_notes || [],
          issues: [],
        })}
      </div>
    </div>
  `;
}

function renderTextCheckResult(check) {
  return `
    <p>${escapeHtml(check.summary || "")}</p>
    <h4>已删除无效段落</h4>
    ${asArray(check.removed_paragraphs).length
      ? asArray(check.removed_paragraphs).map((item) => `<div class="diff-line">P${item.index} · ${escapeHtml(item.reason)} · ${escapeHtml(item.text)}</div>`).join("")
      : `<p class="muted">没有删除段落。</p>`}
    <h4>已修复文本</h4>
    ${asArray(check.normalization_notes).length
      ? asArray(check.normalization_notes).map((item) => `<div class="diff-line">P${item.index} · ${escapeHtml(item.reason)}<br>Before: ${escapeHtml(item.before)}<br>After: ${escapeHtml(item.after)}</div>`).join("")
      : `<p class="muted">没有发现明显格式修复。</p>`}
  `;
}

function renderOverviewPanel() {
  const overview = state.currentArticle.overview;
  $("overviewTab").innerHTML = overview
    ? renderOverview(overview)
    : `<div class="analysis-card"><h3>文章总览</h3><p>由 DeepSeek 生成主旨、核心观点、结构、背景知识和阅读难点。</p><button class="primary-btn" id="runOverviewBtn">生成 AI 文章总览</button></div>`;
}

function renderBilingualList(items) {
  const list = asArray(items);
  if (!list.length) return `<p class="muted">暂无。</p>`;
  if (typeof list[0] === "string") return renderList(list);
  return list
    .map(
      (item) => `<div class="bilingual-item">
        <p class="zh-text">${escapeHtml(item.zh || "")}</p>
        ${item.en ? `<p class="en-quote">&ldquo;${escapeHtml(item.en)}&rdquo;</p>` : ""}
      </div>`
    )
    .join("");
}

function renderVocabPills(items) {
  const list = asArray(items);
  if (!list.length) return `<p class="muted">暂无。</p>`;
  if (typeof list[0] === "string") {
    return renderPills(list);
  }
  return `<div class="tag-row">${list
    .map((x) => `<span class="pill">${escapeHtml(x.term || x)} <span class="pill-sep">·</span> ${escapeHtml(x.translation || "")}</span>`)
    .join("")}</div>`;
}

function renderOverview(overview) {
  const mainIdea = overview.main_idea_zh || overview.main_idea || "";
  const mainEn = overview.main_idea_en || "";
  const background = overview.background_zh || overview.background || "";
  const difficulties = overview.reading_difficulties_zh || overview.reading_difficulties || [];
  return `
    <div class="two-col">
      <div class="analysis-card">
        <h3>文章主旨</h3>
        <p class="zh-text">${escapeHtml(mainIdea)}</p>
        ${mainEn ? `<p class="en-quote">&ldquo;${escapeHtml(mainEn)}&rdquo;</p>` : ""}
        <h4>核心观点</h4>
        ${renderBilingualList(overview.core_viewpoints)}
      </div>
      <div class="analysis-card">
        <h3>文章结构</h3>
        ${renderList(overview.structure)}
        <h4>背景知识</h4>
        <p>${escapeHtml(background)}</p>
      </div>
    </div>
    <div class="two-col">
      <div class="analysis-card">
        <h3>关键词汇</h3>
        ${renderVocabPills(overview.key_vocabulary)}
      </div>
      <div class="analysis-card">
        <h3>阅读难点提醒</h3>
        ${renderList(difficulties)}
      </div>
    </div>
  `;
}

function renderParagraphPanel() {
  const rawItems = state.currentArticle.paragraph_analysis;
  if (!rawItems) {
    $("paragraphTab").innerHTML = `<div class="analysis-card"><h3>段落分析</h3><p>分析每段主旨、段落功能、内部逻辑、重要表达和可模仿写作结构。</p><button class="primary-btn" id="runParagraphBtn">生成 AI 段落分析</button></div>`;
    return;
  }
  const items = asArray(rawItems);
  $("paragraphTab").innerHTML = items
    .map(
      (item) => `
        <div class="analysis-card">
          <h3>P${item.index} · ${escapeHtml(item.function)}</h3>
          <p><strong>段落主旨：</strong>${escapeHtml(item.main_idea)}</p>
          <p><strong>逻辑关系：</strong>${escapeHtml(item.logic)}</p>
          <p><strong>可模仿结构：</strong>${escapeHtml(item.writing_template)}</p>
          <p><strong>中文辅助理解：</strong>${escapeHtml(item.chinese_help)}</p>
          ${renderPills(item.expressions)}
        </div>
      `
    )
    .join("");
}

function renderSentencePanel() {
  const items = asArray(state.longSentences);
  $("sentenceTab").innerHTML = `
    <div class="analysis-card">
      <h3>句子与长难句</h3>
      <p>点击正文中的任意句子可获得单句 AI 分析。也可以让系统筛选最值得精读的长难句。</p>
      <button class="primary-btn" id="runLongSentenceBtn">生成长难句解析</button>
    </div>
    ${items
      .map(
        (item) => {
          const structure = item.sentence_structure || {
            main_clause: item.core_structure,
            modifiers: item.modifiers || [],
            logic: item.logic,
            reading_order: item.understanding_order || [],
          };
          return `
          <div class="analysis-card">
            <h3>长难句</h3>
            <p>${escapeHtml(item.sentence)}</p>
            <div><strong>较难词汇：</strong>${renderDifficultVocabulary(item.difficult_vocabulary || [])}</div>
            <p><strong>句子主干：</strong>${escapeHtml(structure.main_clause || "")}</p>
            <p><strong>逻辑关系：</strong>${escapeHtml(structure.logic || "")}</p>
            <p><strong>理解顺序：</strong>${escapeHtml(asArray(structure.reading_order).join(" → "))}</p>
            <p><strong>翻译：</strong>${escapeHtml(item.translation)}</p>
            <p><strong>仿写任务：</strong>${escapeHtml(item.imitation_task)}</p>
            <button class="secondary-btn" data-say="${escapeHtml(item.sentence)}">朗读</button>
          </div>
        `;
        }
      )
      .join("")}
  `;
}

function renderVocabPanel() {
  const rawItems = state.currentArticle.vocabulary_analysis;
  if (!rawItems) {
    $("vocabTab").innerHTML = `<div class="analysis-card"><h3>词汇掌握</h3><p>AI 将筛选真正值得学习的词，并按核心必会词、阅读理解词、写作可用词、学术表达词、熟词僻义词分层。</p><button class="primary-btn" id="runVocabBtn">生成 AI 词汇分析</button></div>`;
    return;
  }
  const items = asArray(rawItems);
  $("vocabTab").innerHTML = items
    .map(
      (item, index) => `
        <div class="analysis-card">
          <h3>${escapeHtml(item.term)} <span class="pill">${escapeHtml(item.layer)}</span></h3>
          <p><strong>语境义：</strong>${escapeHtml(item.translation)}</p>
          <p><strong>原文语境：</strong>${escapeHtml(item.context)}</p>
          <p><strong>常见搭配：</strong>${escapeHtml(asArray(item.collocations).join("; "))}</p>
          <p><strong>辨析：</strong>${escapeHtml(item.synonym_note)}</p>
          <p><strong>造句任务：</strong>${escapeHtml(item.imitation_task)}</p>
          <textarea id="vocabSentence-${index}" placeholder="用这个词写一句英文"></textarea>
          <div class="inline-actions">
            <button class="secondary-btn" data-add-vocab="${escapeHtml(item.term)}" data-context="${escapeHtml(item.context)}" data-translation="${escapeHtml(item.translation)}" data-layer="${escapeHtml(item.layer)}">加入生词本</button>
            <button class="secondary-btn" data-vocab-feedback="${index}" data-term="${escapeHtml(item.term)}">AI 纠错</button>
            <button class="secondary-btn" data-say="${escapeHtml(item.term)}">朗读</button>
          </div>
          <div id="vocabFeedback-${index}" class="feedback" hidden></div>
        </div>
      `
    )
    .join("");
}

function renderReadingPanel() {
  const questions = asArray(state.readingQuestions);
  const responses = state.currentArticle.reading_responses || {};
  $("readingTab").innerHTML = `
    <div class="analysis-card">
      <h3>阅读答题 + AI 评分</h3>
      <p>先用英文作答，提交后再显示评分、问题分析、优化答案和参考答案。</p>
      <button class="primary-btn" id="runReadingQuestionsBtn">生成阅读理解题</button>
    </div>
    ${questions
      .map(
        (q, index) => {
          const saved = responses[q.id] || {};
          return `
          <div class="exercise-card">
            <h3>Q${index + 1}</h3>
            <p>${escapeHtml(q.question)}</p>
            <p class="muted">Focus: ${escapeHtml(q.focus || "")}</p>
            <textarea id="readingAnswer-${index}" placeholder="请用英文回答，提交前不会显示参考答案。">${escapeHtml(saved.answer || "")}</textarea>
            <button class="primary-btn" data-grade-reading="${index}">提交并获取 AI 反馈</button>
            <div id="readingFeedback-${index}" class="feedback" ${saved.feedback ? "" : "hidden"}>${saved.feedback ? renderReadingFeedback(saved.feedback, saved.meta) : ""}</div>
          </div>
        `;
        }
      )
      .join("")}
  `;
}

function renderDictationPanel() {
  const items = asArray(state.dictationItems);
  const responses = state.currentArticle.dictation_responses || {};
  $("dictationTab").innerHTML = `
    <div class="analysis-card">
      <h3>听写四轮训练</h3>
      <ol class="list">
        <li>整体听：不看原文，只听大意。</li>
        <li>逐句听：播放一句，写一句。</li>
        <li>对照纠错：提交后显示原文、漏听词和拼写问题。</li>
        <li>跟读模仿：对照原文模仿重音和节奏。</li>
      </ol>
      <button class="primary-btn" id="prepareDictationBtn">${items.length ? "重新生成听写题" : "生成听写跟读材料"}</button>
      <p class="muted">听写材料会从文章正文独立筛选，不需要先生成长难句。</p>
    </div>
    ${items
      .map(
        (item, index) => {
          const saved = responses[item.id] || {};
          return `
          <div class="exercise-card">
            <h3>听写 ${index + 1}</h3>
            <p class="muted">${escapeHtml(item.focus || "提交前隐藏原文。先播放音频，再输入你听到的内容。")}</p>
            <button class="secondary-btn" data-say="${escapeHtml(item.source)}">播放</button>
            <textarea id="dictationAnswer-${index}" placeholder="输入你听到的完整句子">${escapeHtml(saved.answer || "")}</textarea>
            <button class="primary-btn" data-dictation-feedback="${index}">提交听写</button>
            <div id="dictationFeedback-${index}" class="feedback" ${saved.feedback ? "" : "hidden"}>${saved.feedback ? renderDictationFeedback(saved.feedback, saved.meta) : ""}</div>
          </div>
        `;
        }
      )
      .join("")}
  `;
}

function renderWritingPanel() {
  const saved = state.currentArticle.last_writing_feedback || {};
  $("writingTab").innerHTML = `
    <div class="two-col">
      <div class="analysis-card">
        <h3>写作输出任务</h3>
        <ol class="list">
          <li>Write an 80-word neutral summary.</li>
          <li>Write a 150-word response using at least three article expressions.</li>
          <li>Imitate one paragraph structure from the article.</li>
        </ol>
        <p>AI 会从内容准确性、结构、语法、词汇自然度和文章表达迁移几个维度反馈。</p>
      </div>
      <div class="analysis-card">
        <h3>写作区</h3>
        <select id="writingTask">
          <option>Write an 80-word neutral summary.</option>
          <option>Write a 150-word response using at least three article expressions.</option>
          <option>Imitate one paragraph structure from the article.</option>
        </select>
        <textarea id="writingDraft" placeholder="在这里写英文摘要、观点短评或仿写段落。">${escapeHtml(saved.content || "")}</textarea>
        <button class="primary-btn" id="runWritingFeedbackBtn">提交给 Qwen 批改</button>
        <div id="writingFeedback" class="feedback" ${saved.feedback ? "" : "hidden"}>${saved.feedback ? renderWritingFeedback(saved.feedback, saved.meta) : ""}</div>
      </div>
    </div>
  `;
  if (saved.task) setSelectValue($("writingTask"), saved.task);
}

function renderVocabulary() {
  if (!state.vocabulary.length) {
    $("vocabList").innerHTML = `<div class="empty-state"><h3>生词本是空的</h3><p>在词汇掌握模块中加入需要复习的词。</p></div>`;
    return;
  }
  $("vocabList").innerHTML = asArray(state.vocabulary)
    .map(
      (item) => `
        <div class="vocab-item">
          <h3>${escapeHtml(item.term)} <span class="pill">${escapeHtml(item.layer || item.kind)}</span></h3>
          <p>${escapeHtml(item.translation || "未填写释义")}</p>
          <p class="muted">${escapeHtml(asArray(item.contexts).slice(-1)[0] || "")}</p>
          <button class="secondary-btn" data-say="${escapeHtml(item.term)}">朗读</button>
        </div>
      `
    )
    .join("");
}

function parseFeedback(value) {
  if (typeof value === "object" && value !== null) return value;
  try { return JSON.parse(value); } catch { return null; }
}

function renderOutputFeedback(kind, feedback) {
  const fb = parseFeedback(feedback);
  if (!fb) return `<pre class="feedback">${escapeHtml(String(feedback))}</pre>`;
  if (kind === "writing") return renderWritingFeedback(fb, null);
  if (kind === "reading-answer") return renderReadingFeedback(fb, null);
  if (kind === "dictation") return renderDictationFeedback(fb, null);
  return `<div class="analysis-card">${renderObject(fb)}</div>`;
}

function renderOutputs() {
  if (!asArray(state.outputs).length) {
    $("outputsList").innerHTML = `<div class="empty-state"><h3>还没有产出</h3><p>阅读答题、口语转写和写作反馈会保存到这里。</p></div>`;
    return;
  }
  $("outputsList").innerHTML = asArray(state.outputs)
    .slice()
    .reverse()
    .map(
      (item) => `
        <div class="output-item">
          <div class="tag-row"><span class="pill">${escapeHtml(item.kind)}</span><span class="pill">${escapeHtml(item.created_at)}</span></div>
          <p>${escapeHtml(item.content)}</p>
          ${item.feedback ? renderOutputFeedback(item.kind, item.feedback) : ""}
        </div>
      `
    )
    .join("");
}

function renderObject(obj) {
  if (!obj) return "";
  if (Array.isArray(obj)) return renderList(obj);
  return Object.entries(obj)
    .map(([key, value]) => {
      if (Array.isArray(value)) return `<h4>${escapeHtml(key)}</h4>${renderList(value)}`;
      if (typeof value === "object" && value !== null) return `<h4>${escapeHtml(key)}</h4>${renderObject(value)}`;
      return `<p><strong>${escapeHtml(key)}：</strong>${escapeHtml(value)}</p>`;
    })
    .join("");
}

function teacherMeta(meta) {
  if (!meta) return "";
  if (meta.cached) {
    return `<p class="muted">来源：已保存结果</p>`;
  }
  const requested = meta.requested_provider && meta.requested_provider !== meta.provider
    ? ` · 原任务默认 ${escapeHtml(meta.requested_provider)}`
    : "";
  return `<p class="muted">模型：${escapeHtml(meta.provider)} / ${escapeHtml(meta.model)}${meta.used_ai ? "" : "（本地降级）"}${requested}</p>`;
}

function renderSentenceTeacher(analysis, meta) {
  const structure = analysis.sentence_structure || {
    main_clause: analysis.core_structure,
    modifiers: analysis.modifiers || [],
    logic: analysis.logic,
    reading_order: analysis.understanding_order || [],
  };
  const difficultVocabulary = analysis.difficult_vocabulary || analysis.key_vocabulary || [];
  return `
    <div class="analysis-card">
      <h3>句子分析</h3>
      ${teacherMeta(meta)}
      <p>${escapeHtml(analysis.sentence)}</p>
      <div class="teacher-section"><strong>较难词汇</strong>${renderDifficultVocabulary(difficultVocabulary)}</div>
      <div class="teacher-section"><strong>句式结构</strong>
        <p><strong>主干：</strong>${escapeHtml(structure.main_clause || "")}</p>
        <p><strong>逻辑：</strong>${escapeHtml(structure.logic || "")}</p>
        <div><strong>修饰成分：</strong>${renderList(structure.modifiers || [])}</div>
        <div><strong>理解顺序：</strong>${renderList(structure.reading_order || [])}</div>
      </div>
      <div class="teacher-section"><strong>翻译</strong><p>${escapeHtml(analysis.translation)}</p></div>
      <div class="teacher-section"><strong>可迁移表达</strong>${renderPills(analysis.transferable_expressions)}</div>
      <div class="teacher-section"><strong>仿写任务</strong><p>${escapeHtml(analysis.imitation_task)}</p></div>
    </div>
  `;
}

function renderDifficultVocabulary(items) {
  const list = asArray(items);
  if (!list.length) return `<p class="muted">暂无。</p>`;
  return list
    .map((item) => {
      if (typeof item === "string") return `<p><strong>${escapeHtml(item)}</strong></p>`;
      return `<p><strong>${escapeHtml(item.term || "")}</strong>：${escapeHtml(item.meaning || item.translation || "")}<br><span class="muted">${escapeHtml(item.note || "")}</span></p>`;
    })
    .join("");
}

function renderReadingFeedback(feedback, meta) {
  return `
    <div class="analysis-card">
      <h3>阅读答题反馈</h3>
      ${teacherMeta(meta)}
      <div class="score-badge">${escapeHtml(feedback.score ?? "-")}</div>
      <div class="teacher-section"><strong>内容准确性</strong><p>${escapeHtml(feedback.content)}</p></div>
      <div class="teacher-section"><strong>逻辑</strong><p>${escapeHtml(feedback.logic)}</p></div>
      <div class="teacher-section"><strong>语法</strong><p>${escapeHtml(feedback.grammar)}</p></div>
      <div class="teacher-section"><strong>词汇表达</strong><p>${escapeHtml(feedback.vocabulary)}</p></div>
      <div class="teacher-section"><strong>优化答案</strong><p>${escapeHtml(feedback.improved_answer)}</p></div>
      <div class="teacher-section"><strong>参考答案</strong><p>${escapeHtml(feedback.reference_answer)}</p></div>
    </div>
  `;
}

function renderDictationFeedback(feedback, meta) {
  return `
    <div class="analysis-card">
      <h3>听写纠错</h3>
      ${teacherMeta(meta)}
      <div class="score-badge">${escapeHtml(feedback.score ?? "-")}</div>
      <div class="teacher-section"><strong>原文</strong><p>${escapeHtml(feedback.original)}</p></div>
      <div class="teacher-section"><strong>你的输入</strong><p>${escapeHtml(feedback.user_answer)}</p></div>
      <div class="teacher-section"><strong>漏听词</strong>${renderPills(feedback.missing_words)}</div>
      <div class="teacher-section"><strong>拼写或多余词</strong>${renderPills(feedback.spelling_or_extra)}</div>
      <div class="teacher-section"><strong>听力提示</strong>${renderList(feedback.listening_notes || [])}</div>
      <div class="teacher-section"><strong>为什么容易错</strong><p>${escapeHtml(feedback.why_difficult)}</p></div>
    </div>
  `;
}

function renderWritingFeedback(feedback, meta) {
  return `
    <div class="analysis-card">
      <h3>写作批改</h3>
      ${teacherMeta(meta)}
      <div class="score-badge">${escapeHtml(feedback.score ?? "-")}</div>
      <div class="teacher-section"><strong>内容</strong><p>${escapeHtml(feedback.content)}</p></div>
      <div class="teacher-section"><strong>结构</strong><p>${escapeHtml(feedback.structure)}</p></div>
      <div class="teacher-section"><strong>语法</strong><p>${escapeHtml(feedback.grammar)}</p></div>
      <div class="teacher-section"><strong>词汇</strong><p>${escapeHtml(feedback.vocabulary)}</p></div>
      <div class="teacher-section"><strong>修改版</strong><p>${escapeHtml(feedback.improved_version)}</p></div>
      <div class="teacher-section"><strong>下一步</strong><p>${escapeHtml(feedback.next_step)}</p></div>
    </div>
  `;
}

function renderList(items) {
  const list = asArray(items);
  if (!list.length) return `<p class="muted">暂无。</p>`;
  return `<ul class="list">${list.map((item) => `<li>${escapeHtml(typeof item === "object" ? JSON.stringify(item) : item)}</li>`).join("")}</ul>`;
}

function setTeacherContent(html) {
  $("teacherContent").innerHTML = html;
}

function selectSentence(sentenceEl) {
  document.querySelectorAll(".sentence").forEach((s) => s.classList.remove("selected"));
  sentenceEl.classList.add("selected");
  state.activeSentence = sentenceEl.dataset.sentence;
  $("appShell").classList.remove("teacher-collapsed");
  const teacherToggle = $("toggleTeacherBtn");
  if (teacherToggle) teacherToggle.textContent = "隐藏AI面板";
  setTeacherContent(`
    <div class="analysis-card">
      <h3>已选择句子</h3>
      <p>${escapeHtml(state.activeSentence)}</p>
      <div class="inline-actions">
        <button class="secondary-btn" data-say="${escapeHtml(state.activeSentence)}">朗读</button>
        <button class="primary-btn" id="analyzeSelectedSentenceBtn">AI 分析句子</button>
      </div>
    </div>
  `);
}

function elementContainsPoint(element, x, y) {
  return [...element.getClientRects()].some(
    (rect) => x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom
  );
}

function sentenceFromClick(event) {
  const rawTarget = event.target;
  const targetElement = rawTarget instanceof Element ? rawTarget : rawTarget?.parentElement;
  const direct = targetElement?.closest(".sentence");
  if (direct) return direct;
  const articleText = $("articleText");
  if (!articleText || !targetElement?.closest("#articleText")) return null;
  return [...articleText.querySelectorAll(".sentence")].find((sentence) =>
    elementContainsPoint(sentence, event.clientX, event.clientY)
  ) || null;
}

function showView(viewId) {
  document.querySelectorAll(".view").forEach((view) => view.classList.remove("active"));
  $(viewId).classList.add("active");
}

function showTab(tabId) {
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === tabId));
  document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.id === tabId));
}

function fallbackBrowserSpeak(text) {
  if (!("speechSynthesis" in window)) {
    alert("当前浏览器不支持语音合成。");
    return;
  }
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "en-US";
  utterance.rate = 0.9;
  window.speechSynthesis.speak(utterance);
}

async function speak(text) {
  const clean = String(text || "").trim();
  if (!clean) return;
  try {
    if (state.currentAudio) {
      state.currentAudio.pause();
      state.currentAudio = null;
    }
    const data = await api("/api/audio/speech", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: clean }),
    });
    if (data.audio_url) {
      state.currentAudio = new Audio(data.audio_url);
    } else if (data.audio_data) {
      state.currentAudio = new Audio(`data:${data.mime_type || "audio/wav"};base64,${data.audio_data}`);
    }
    if (!state.currentAudio) throw new Error("AI 朗读没有返回音频。");
    await state.currentAudio.play();
  } catch (error) {
    console.warn("AI 朗读失败，已回退浏览器朗读：", error);
    fallbackBrowserSpeak(clean);
  }
}

async function reloadCurrentArticle() {
  if (!state.currentArticle) return;
  const data = await api(`/api/articles/${state.currentArticle.id}`);
  state.currentArticle = data.article;
  renderStudyHeader();
  renderArticleText();
  renderAllPanels();
}

async function runAction(button, label, action) {
  const old = button.textContent;
  button.textContent = label;
  button.disabled = true;
  try {
    return await action();
  } catch (error) {
    alert(error.message);
  } finally {
    button.textContent = old;
    button.disabled = false;
  }
}

async function addVocab(term, context, translation, layer) {
  await api("/api/vocabulary", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      article_id: state.currentArticle?.id,
      term,
      translation,
      context,
      layer,
      kind: term.includes(" ") ? "phrase" : "word",
    }),
  });
  await refreshAll();
}

function showArticleInfo() {
  const article = state.currentArticle;
  const stats = article.cleaned_stats || article.stats;
  $("articleInfoContent").innerHTML = `
    <div class="two-col">
      <div>
        <p><strong>词数：</strong>${formatNumber(stats.word_count)}</p>
        <p><strong>句数：</strong>${formatNumber(stats.sentence_count)}</p>
        <p><strong>段落：</strong>${formatNumber(stats.paragraph_count)}</p>
        <p><strong>预计阅读：</strong>${stats.reading_minutes} 分钟</p>
      </div>
      <div>
        <p><strong>难度：</strong>${stats.cefr}</p>
        <p><strong>长难句：</strong>${stats.long_sentence_count}</p>
        <p><strong>平均句长：</strong>${stats.avg_sentence_words}</p>
        <p><strong>词汇密度：</strong>${stats.lexical_density}</p>
      </div>
    </div>
    <h4>高频词</h4>
    <div class="tag-row">${asArray(stats.top_terms).map((x) => `<span class="pill">${escapeHtml(x.term)} · ${x.count}</span>`).join("")}</div>
  `;
  $("articleInfoDialog").showModal();
}

document.addEventListener("click", async (event) => {
  const rawTarget = event.target;
  const targetElement = rawTarget instanceof Element ? rawTarget : rawTarget?.parentElement;
  if (!targetElement) return;
  const target = targetElement.closest(
    "button, [data-open-article], .tab, .sentence, [data-say], [data-add-vocab], [data-vocab-feedback], [data-grade-reading], [data-dictation-feedback]"
  ) || targetElement;

  if (target.matches(".nav-btn[data-list]")) {
    state.currentList = target.dataset.list;
    document.querySelectorAll(".nav-btn").forEach((btn) => btn.classList.remove("active"));
    target.classList.add("active");
    showView("libraryView");
    renderArticles();
  }
  if (target.matches(".nav-btn[data-view]")) {
    document.querySelectorAll(".nav-btn").forEach((btn) => btn.classList.remove("active"));
    target.classList.add("active");
    showView(target.dataset.view);
  }
  if (target.matches("[data-open-article]")) {
    await openArticle(target.dataset.openArticle);
  }
  if (target.matches(".tab")) {
    showTab(target.dataset.tab);
  }
  if (target.matches(".sentence")) {
    selectSentence(target);
  } else if (target.closest("#articleText")) {
    const sentence = sentenceFromClick(event);
    if (sentence) selectSentence(sentence);
  }
  if (target.matches("[data-say]")) {
    await speak(target.dataset.say);
  }
  if (target.id === "backToLibrary") {
    showView("libraryView");
  }
  if (target.id === "articleInfoBtn") {
    showArticleInfo();
  }
  if (target.id === "toggleSidebarBtn") {
    $("appShell").classList.toggle("sidebar-collapsed");
    target.textContent = $("appShell").classList.contains("sidebar-collapsed") ? "展开侧栏" : "隐藏侧栏";
  }
  if (target.id === "toggleTeacherBtn") {
    $("appShell").classList.toggle("teacher-collapsed");
    target.textContent = $("appShell").classList.contains("teacher-collapsed") ? "显示AI面板" : "隐藏AI面板";
  }
  if (target.id === "focusModeBtn") {
    $("appShell").classList.toggle("focus-mode");
    target.textContent = $("appShell").classList.contains("focus-mode") ? "退出专注" : "专注模式";
    $("toggleSidebarBtn").textContent = $("appShell").classList.contains("focus-mode") ? "退出专注" : "隐藏侧栏";
  }
  if (target.id === "closeInfoBtn") {
    $("articleInfoDialog").close();
  }
  if (target.id === "openSettingsBtn") {
    $("settingsDialog").showModal();
  }
  if (target.id === "closeSettingsBtn") {
    $("settingsDialog").close();
  }
  if (target.id === "saveSettingsBtn") {
    await runAction(target, "保存中...", async () => {
      state.settings = await api("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settingsPayload()),
      });
      $("deepseekKeyInput").value = "";
      $("qwenKeyInput").value = "";
      await refreshAll();
    });
  }
  if (target.id === "testDeepseekBtn" || target.id === "testQwenBtn") {
    const provider = target.id === "testDeepseekBtn" ? "deepseek" : "qwen";
    const box = provider === "deepseek" ? $("deepseekTestResult") : $("qwenTestResult");
    await runAction(target, "测试中...", async () => {
      const data = await api(`/api/settings/test/${provider}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settingsPayload()),
      });
      box.textContent = renderProviderTestResult(data);
    });
  }
  if (target.id === "rebuildLibraryBtn") {
    if (!confirm("将使用新解析规则重建文章库，AI分析缓存会被清空。继续吗？")) return;
    await runAction(target, "重建中...", async () => {
      const data = await api("/api/library/rebuild", { method: "POST" });
      state.library = data.library;
      state.summary = data.summary;
      await refreshAll();
      alert("文章库已重建。");
    });
  }
  if (target.matches("[data-delete-source]")) {
    const sourceId = target.dataset.deleteSource;
    if (!confirm("确认删除此来源及其全部文章？此操作不可撤销。")) return;
    await runAction(target, "删除中...", async () => {
      const data = await api(`/api/sources/${sourceId}`, { method: "DELETE" });
      state.library = data.library;
      state.summary = data.summary;
      if (state.currentArticle && !data.library.sources.some((s) => s.articles?.some((a) => a.id === state.currentArticle.id))) {
        state.currentArticle = null;
        showView("libraryView");
      }
      renderGlobalStats();
      renderAiStatus();
      renderSources();
      renderArticles();
    });
  }
  if (target.id === "batchAnalyzeBtn") {
    await runBatchAnalyze();
  }
  if (target.id === "favoriteBtn") {
    await api(`/api/articles/${state.currentArticle.id}/favorite`, { method: "POST" });
    await reloadCurrentArticle();
    await refreshAll();
  }
  if (target.id === "runTextCheckBtn") {
    await runAction(target, "检查中...", async () => {
      const data = await api(`/api/articles/${state.currentArticle.id}/text-check`, { method: "POST" });
      state.currentArticle = data.article;
      state.useCleanedText = true;
      renderArticleText();
      renderTextPanel();
      setTeacherContent(`<div class="analysis-card"><h3>文本检查完成</h3>${teacherMeta(data.meta)}${renderTextCheckResult(data.text_check)}</div>`);
    });
  }
  if (target.id === "toggleTextVersionBtn") {
    state.useCleanedText = !state.useCleanedText;
    renderArticleText();
    renderTextPanel();
  }
  if (target.id === "runOverviewBtn") {
    await runAction(target, "生成中...", async () => {
      const data = await api(`/api/articles/${state.currentArticle.id}/overview`, { method: "POST" });
      if (data.article) state.currentArticle = data.article;
      state.currentArticle.overview = data.overview;
      renderOverviewPanel();
      setTeacherContent(`<div class="analysis-card"><h3>文章总览</h3>${teacherMeta(data.meta)}${renderOverview(data.overview)}</div>`);
    });
  }
  if (target.id === "runParagraphBtn") {
    await runAction(target, "分析中...", async () => {
      const data = await api(`/api/articles/${state.currentArticle.id}/paragraphs/analyze`, { method: "POST" });
      if (data.article) state.currentArticle = data.article;
      state.currentArticle.paragraph_analysis = data.paragraphs;
      renderParagraphPanel();
    });
  }
  if (target.id === "runLongSentenceBtn") {
    await runAction(target, "解析中...", async () => {
      const data = await api(`/api/articles/${state.currentArticle.id}/long-sentences`, { method: "POST" });
      if (data.article) state.currentArticle = data.article;
      state.longSentences = data.sentences;
      state.currentArticle.long_sentence_analysis = data.sentences;
      renderSentencePanel();
    });
  }
  if (target.id === "analyzeSelectedSentenceBtn") {
    await runAction(target, "分析中...", async () => {
      const data = await api(`/api/articles/${state.currentArticle.id}/sentence/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sentence: state.activeSentence }),
      });
      if (data.article) state.currentArticle = data.article;
      setTeacherContent(renderSentenceTeacher(data.analysis, data.meta));
    });
  }
  if (target.id === "runVocabBtn") {
    await runAction(target, "筛选中...", async () => {
      const data = await api(`/api/articles/${state.currentArticle.id}/vocabulary/analyze`, { method: "POST" });
      if (data.article) state.currentArticle = data.article;
      state.currentArticle.vocabulary_analysis = data.items;
      renderVocabPanel();
    });
  }
  if (target.matches("[data-add-vocab]")) {
    await addVocab(target.dataset.addVocab, target.dataset.context, target.dataset.translation, target.dataset.layer);
    alert("已加入生词本。");
  }
  if (target.matches("[data-vocab-feedback]")) {
    const index = target.dataset.vocabFeedback;
    const term = target.dataset.term;
    const sentence = $(`vocabSentence-${index}`).value;
    await runAction(target, "纠错中...", async () => {
      const data = await api(`/api/articles/${state.currentArticle.id}/vocabulary/sentence-feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ term, sentence }),
      });
      const box = $(`vocabFeedback-${index}`);
      box.hidden = false;
      const fb = data.feedback || {};
      box.innerHTML = `
        <div class="teacher-section"><strong>自然度</strong><p>${escapeHtml(fb.naturalness || "")}</p></div>
        ${fb.issue ? `<div class="teacher-section"><strong>问题</strong><p>${escapeHtml(fb.issue)}</p></div>` : ""}
        <div class="teacher-section"><strong>改进句</strong><p>${escapeHtml(fb.improved_sentence || "")}</p></div>
        <div class="teacher-section"><strong>用法提示</strong><p>${escapeHtml(fb.usage_tip || "")}</p></div>
      `;
    });
  }
  if (target.id === "runReadingQuestionsBtn") {
    await runAction(target, "生成中...", async () => {
      const data = await api(`/api/articles/${state.currentArticle.id}/reading/questions`, { method: "POST" });
      if (data.article) state.currentArticle = data.article;
      state.readingQuestions = normalizeReadingQuestions(data.questions);
      state.currentArticle.reading_questions = data.questions;
      renderReadingPanel();
    });
  }
  if (target.matches("[data-grade-reading]")) {
    const index = target.dataset.gradeReading;
    const question = state.readingQuestions[index];
    const answer = $(`readingAnswer-${index}`).value;
    await runAction(target, "评分中...", async () => {
      const data = await api(`/api/articles/${state.currentArticle.id}/reading/grade`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question_id: question.id, question: question.question, answer }),
      });
      const box = $(`readingFeedback-${index}`);
      box.hidden = false;
      box.innerHTML = renderReadingFeedback(data.feedback, data.meta);
      setTeacherContent(renderReadingFeedback(data.feedback, data.meta));
      if (data.article) {
        state.currentArticle = data.article;
        state.readingQuestions = normalizeReadingQuestions(data.article.reading_questions || state.readingQuestions);
        renderReadingPanel();
      }
      await refreshAll();
    });
  }
  if (target.id === "prepareDictationBtn") {
    await runAction(target, "生成中...", async () => {
      const data = await api(`/api/articles/${state.currentArticle.id}/dictation/items`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ count: 6 }),
      });
      if (data.article) state.currentArticle = data.article;
      state.dictationItems = normalizeDictationItems(data.items);
      state.currentArticle.dictation_items = data.items;
      renderDictationPanel();
    });
  }
  if (target.matches("[data-dictation-feedback]")) {
    const index = target.dataset.dictationFeedback;
    const item = state.dictationItems[index];
    const answer = $(`dictationAnswer-${index}`).value;
    await runAction(target, "批改中...", async () => {
      const data = await api(`/api/articles/${state.currentArticle.id}/dictation/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: item.source, answer }),
      });
      const box = $(`dictationFeedback-${index}`);
      box.hidden = false;
      box.innerHTML = renderDictationFeedback(data.feedback, data.meta);
      setTeacherContent(renderDictationFeedback(data.feedback, data.meta));
      if (data.article) {
        state.currentArticle = data.article;
        state.dictationItems = normalizeDictationItems(data.article.dictation_items || state.dictationItems);
        renderDictationPanel();
      }
    });
  }
  if (target.id === "runWritingFeedbackBtn") {
    const task = $("writingTask").value;
    const content = $("writingDraft").value;
    await runAction(target, "批改中...", async () => {
      const data = await api(`/api/articles/${state.currentArticle.id}/writing/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task, content }),
      });
      const box = $("writingFeedback");
      box.hidden = false;
      box.innerHTML = renderWritingFeedback(data.feedback, data.meta);
      setTeacherContent(renderWritingFeedback(data.feedback, data.meta));
      if (data.article) {
        state.currentArticle = data.article;
        renderWritingPanel();
      }
      await refreshAll();
    });
  }
});

// 一键批量分析
async function runBatchAnalyze() {
  const btn = $("batchAnalyzeBtn");
  if (!state.currentArticle) return;
  const id = state.currentArticle.id;
  const steps = [
    { label: "文本检查", run: () => api(`/api/articles/${id}/text-check`, { method: "POST" }), done: (d) => { state.currentArticle = d.article; renderArticleText(); renderTextPanel(); } },
    { label: "文章总览", run: () => api(`/api/articles/${id}/overview`, { method: "POST" }), done: (d) => { if (d.article) state.currentArticle = d.article; state.currentArticle.overview = d.overview; renderOverviewPanel(); } },
    { label: "段落分析", run: () => api(`/api/articles/${id}/paragraphs/analyze`, { method: "POST" }), done: (d) => { if (d.article) state.currentArticle = d.article; state.currentArticle.paragraph_analysis = d.paragraphs; renderParagraphPanel(); } },
    { label: "长难句", run: () => api(`/api/articles/${id}/long-sentences`, { method: "POST" }), done: (d) => { if (d.article) state.currentArticle = d.article; state.longSentences = d.sentences; state.currentArticle.long_sentence_analysis = d.sentences; renderSentencePanel(); } },
    { label: "词汇分析", run: () => api(`/api/articles/${id}/vocabulary/analyze`, { method: "POST" }), done: (d) => { if (d.article) state.currentArticle = d.article; state.currentArticle.vocabulary_analysis = d.items; renderVocabPanel(); } },
    { label: "阅读理解题", run: () => api(`/api/articles/${id}/reading/questions`, { method: "POST" }), done: (d) => { if (d.article) state.currentArticle = d.article; state.readingQuestions = normalizeReadingQuestions(d.questions); state.currentArticle.reading_questions = d.questions; renderReadingPanel(); } },
  ];
  const orig = btn.textContent;
  btn.disabled = true;
  for (let i = 0; i < steps.length; i++) {
    const step = steps[i];
    btn.textContent = `${step.label}… (${i + 1}/${steps.length})`;
    try {
      const data = await step.run();
      step.done(data);
    } catch (err) {
      setTeacherContent(`<p class="status-text">批量分析在"${step.label}"步骤出错：${escapeHtml(err.message)}</p>`);
      break;
    }
  }
  btn.disabled = false;
  btn.textContent = orig;
  renderArticles();
}

// 学习笔记自动保存
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
    if (status) { status.textContent = "已保存"; setTimeout(() => { status.textContent = ""; }, 2000); }
  } catch {
    if (status) status.textContent = "保存失败";
  }
}

function scheduleNotesSave() {
  clearTimeout(notesSaveTimer);
  notesSaveTimer = setTimeout(saveNotesNow, 1500);
}

// 移动端侧栏开关
function openMobileSidebar() {
  $("appShell").classList.add("sidebar-open");
}
function closeMobileSidebar() {
  $("appShell").classList.remove("sidebar-open");
}

document.addEventListener("click", (e) => {
  const rawTarget = e.target;
  const targetElement = rawTarget instanceof Element ? rawTarget : rawTarget?.parentElement;
  if (!targetElement) return;
  const target = targetElement.closest("button, [data-open-article], #sidebarBackdrop") || targetElement;
  if (target.id === "mobileMenuBtn" || target.id === "mobileMenuBtn2") {
    openMobileSidebar();
  }
  if (target.id === "sidebarBackdrop") {
    closeMobileSidebar();
  }
  // 移动端点击文章后自动关闭侧栏
  if (target.matches("[data-open-article]") && window.innerWidth <= 900) {
    closeMobileSidebar();
  }
}, true); // 捕获阶段，避免被其他监听器拦截

$("uploadBtn").addEventListener("click", async () => {
  const file = $("fileInput").files[0];
  if (!file) {
    $("uploadStatus").textContent = "请选择文件。";
    return;
  }
  const formData = new FormData();
  formData.append("file", file);
  $("uploadStatus").textContent = "正在导入并解析...";
  try {
    const result = await api("/api/upload", { method: "POST", body: formData });
    $("uploadStatus").textContent = result.duplicate ? "该文件已导入，已打开现有文章库。" : "导入完成。";
    await refreshAll();
  } catch (error) {
    $("uploadStatus").textContent = error.message;
  }
});

["searchInput", "topicFilter", "languageFilter", "difficultyFilter"].forEach((id) => {
  $(id).addEventListener("input", renderArticles);
  $(id).addEventListener("change", renderArticles);
});

refreshAll().catch((error) => {
  console.error(error);
  $("articleList").innerHTML = `<div class="article-item">${escapeHtml(error.message)}</div>`;
});
