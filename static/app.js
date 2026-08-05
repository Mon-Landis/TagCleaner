const app = {
  state: {
    loadedPath: "",
    images: [],
    tags: [],
    translations: {},
    rules: [],
    favorites: [],
    characters: [],
    llmConfig: {},
    llmModels: { volcano: [], gemini: [] },
    llmJobs: [],
    changedCount: 0,
  },
  currentId: null,
  selectedIds: new Set(),
  filteredIds: [],
  lastClickedIndex: -1,
  showCurrentTags: true,
  activeMode: "favorites",
  ruleSaveTimer: null,
  revertHold: null,
  revertFadeTimer: null,
  clearLlmCacheHold: null,
  llmJobId: null,
  llmJob: null,
  llmPollTimer: null,
  llmPollBusy: false,
  llmLastDone: -1,
  llmRecentStages: [],
  activeCharacterKey: null,
  clearVolcanoKey: false,
  clearGeminiKey: false,
  savedKeyDrafts: {
    volcano: "",
    gemini: "",
  },
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const els = {
  loadedPath: $("#loadedPath"),
  serverState: $("#serverState"),
  changeCount: $("#changeCount"),
  revertButton: $("#revertButton"),
  clearLlmCacheButton: $("#clearLlmCacheButton"),
  statusMessage: $("#statusMessage"),
  folderPath: $("#folderPath"),
  imageList: $("#imageList"),
  emptyImages: $("#emptyImages"),
  imagePanel: $(".image-panel"),
  imageCount: $("#imageCount"),
  sessionCount: $("#sessionCount"),
  selectionCount: $("#selectionCount"),
  filterReadout: $("#filterReadout"),
  imageSearch: $("#imageSearch"),
  includeFilter: $("#includeFilter"),
  includeMode: $("#includeMode"),
  excludeFilter: $("#excludeFilter"),
  excludeMode: $("#excludeMode"),
  previewTitle: $("#previewTitle"),
  previewImage: $("#previewImage"),
  previewEmpty: $("#previewEmpty"),
  previewStage: $("#previewStage"),
  previewStatus: $("#previewStatus"),
  previewPath: $("#previewPath"),
  previewTagCount: $("#previewTagCount"),
  currentTags: $("#currentTags"),
  naturalDescriptionBlock: $("#naturalDescriptionBlock"),
  naturalDescription: $("#naturalDescription"),
  tagCount: $("#tagCount"),
  tagTable: $("#tagTable"),
  emptyTags: $("#emptyTags"),
  tagPanel: $(".tag-panel"),
  tagSearch: $("#tagSearch"),
  toggleCurrentTags: $("#toggleCurrentTags"),
  toggleAllTags: $("#toggleAllTags"),
  favoriteTarget: $("#favoriteTarget"),
  favoritesList: $("#favoritesList"),
  rulesList: $("#rulesList"),
  ruleCount: $("#ruleCount"),
  llmConfigReadout: $("#llmConfigReadout"),
  llmConfigState: $("#llmConfigState"),
  llmModelReadout: $("#llmModelReadout"),
  llmProgress: $("#llmProgress"),
  llmProgressLabel: $("#llmProgressLabel"),
  llmProgressCount: $("#llmProgressCount"),
  llmProgressBar: $("#llmProgressBar"),
  llmStageList: $("#llmStageList"),
  llmProgressCurrent: $("#llmProgressCurrent"),
  styleMode: $("#styleMode"),
  allowNaturalLanguage: $("#allowNaturalLanguage"),
  autoSaveLlmResults: $("#autoSaveLlmResults"),
  tagProvider: $("#tagProvider"),
  tagModel: $("#tagModel"),
  aposeProvider: $("#aposeProvider"),
  aposeModel: $("#aposeModel"),
  cleanProvider: $("#cleanProvider"),
  cleanModel: $("#cleanModel"),
  tagModelCapability: $("#tagModelCapability"),
  aposeModelCapability: $("#aposeModelCapability"),
  cleanModelCapability: $("#cleanModelCapability"),
  promptAdaptationReadout: $("#promptAdaptationReadout"),
  llmConfigDialog: $("#llmConfigDialog"),
  characterDialog: $("#characterDialog"),
  llmResultDialog: $("#llmResultDialog"),
  characterList: $("#characterList"),
  emptyCharacters: $("#emptyCharacters"),
  characterTag: $("#characterTag"),
  characterAposePath: $("#characterAposePath"),
  characterDescription: $("#characterDescription"),
  characterKeyReadout: $("#characterKeyReadout"),
  characterPreview: $("#characterPreview"),
  characterPreviewEmpty: $("#characterPreviewEmpty"),
};

function setStatus(message, type = "ready") {
  els.statusMessage.textContent = message;
  els.serverState.dataset.state = type === "ready" ? "idle" : type;
  els.serverState.lastChild.textContent = ` ${type === "busy" ? "PROCESSING" : type === "error" ? "CHECK INPUT" : "LOCAL READY"}`;
}

function syncRevertButton() {
  const hasChanges = app.state.changedCount > 0;
  els.revertButton.disabled = !hasChanges;
  els.revertButton.setAttribute("aria-disabled", String(!hasChanges));
  if (hasChanges) {
    if (app.revertFadeTimer) {
      clearTimeout(app.revertFadeTimer);
      app.revertFadeTimer = null;
    }
    els.revertButton.classList.remove("is-hidden", "is-fading");
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
    body: options.body && typeof options.body !== "string" ? JSON.stringify(options.body) : options.body,
  });
  let payload = {};
  try { payload = await response.json(); } catch { payload = {}; }
  if (!response.ok) {
    const error = new Error(payload.error || payload.message || `请求失败 ${response.status}`);
    error.payload = payload;
    error.status = response.status;
    throw error;
  }
  return payload;
}

function parseTokens(value) {
  return String(value || "").split(",").map((token) => token.trim()).filter(Boolean).filter((token, index, list) => list.indexOf(token) === index);
}

function getImage(id) { return app.state.images.find((image) => image.id === id) || null; }
function getCurrentImage() { return getImage(app.currentId); }

function hydrate(payload) {
  app.state = {
    loadedPath: payload.loadedPath || "",
    images: payload.images || [],
    tags: payload.tags || [],
    translations: payload.translations || {},
    rules: Array.isArray(payload.rules) ? payload.rules : [],
    favorites: Array.isArray(payload.favorites) ? payload.favorites : [],
    characters: Array.isArray(payload.characters) ? payload.characters : [],
    llmConfig: payload.llmConfig || app.state.llmConfig || {},
    llmModels: payload.llmConfig?.modelCatalog || app.state.llmModels || { volcano: [], gemini: [] },
    llmJobs: Array.isArray(payload.llmJobs) ? payload.llmJobs : [],
    changedCount: payload.changedCount || 0,
  };
  const validIds = new Set(app.state.images.map((image) => image.id));
  app.selectedIds = new Set([...app.selectedIds].filter((id) => validIds.has(id)));
  if (!validIds.has(app.currentId)) app.currentId = app.state.images[0]?.id || null;
  if (!app.currentId && app.state.images[0]) app.currentId = app.state.images[0].id;
  els.loadedPath.textContent = app.state.loadedPath || "未加载目录";
  els.changeCount.textContent = String(app.state.changedCount).padStart(2, "0");
  syncRevertButton();
  els.sessionCount.textContent = String(app.state.images.length).padStart(2, "0");
  renderFavorites();
  renderRules();
  renderImages();
  renderPreview();
  renderTags();
  renderLlmState();
  if (els.characterDialog.open) renderCharacterManager();
}

function filterMatches(tags, required, mode) {
  if (!required.length) return true;
  const present = new Set(tags);
  return mode === "any" ? required.some((tag) => present.has(tag)) : required.every((tag) => present.has(tag));
}

function filteredImages() {
  const search = els.imageSearch.value.trim().toLowerCase();
  const include = parseTokens(els.includeFilter.value);
  const exclude = parseTokens(els.excludeFilter.value);
  const includeMode = els.includeMode.value;
  const excludeMode = els.excludeMode.value;
  return app.state.images.filter((image) => {
    const haystack = `${image.name} ${image.relativePath}`.toLowerCase();
    if (search && !haystack.includes(search)) return false;
    if (!filterMatches(image.tags, include, includeMode)) return false;
    if (exclude.length && filterMatches(image.tags, exclude, excludeMode)) return false;
    return true;
  });
}

function renderImages() {
  const visible = filteredImages();
  app.filteredIds = visible.map((image) => image.id);
  els.imageList.innerHTML = "";
  visible.forEach((image, index) => {
    const row = $("#imageRowTemplate").content.firstElementChild.cloneNode(true);
    row.dataset.id = image.id;
    row.classList.toggle("selected", app.selectedIds.has(image.id));
    row.classList.toggle("current", app.currentId === image.id);
    row.classList.toggle("modified", image.modified);
    row.classList.toggle("llm-cached", image.llmStatus === "cached");
    row.classList.toggle("llm-completed", image.llmStatus === "completed");
    row.setAttribute("aria-selected", String(app.selectedIds.has(image.id)));
    $(".image-index", row).textContent = String(index + 1).padStart(2, "0");
    $(".image-name", row).textContent = image.name;
    $(".image-path", row).textContent = image.relativePath;
    $(".image-row-tags", row).textContent = String(image.tags.length).padStart(2, "0");
    const llmMark = $(".llm-mark", row);
    llmMark.title = image.llmStatus ? "LLM / " + image.llmStatus.toUpperCase() : "尚无 LLM 缓存";
    els.imageList.appendChild(row);
  });
  els.imagePanel.classList.toggle("has-images", app.state.images.length > 0);
  els.imageCount.textContent = String(app.state.images.length).padStart(2, "0");
  els.filterReadout.textContent = `${String(visible.length).padStart(2, "0")} / ${String(app.state.images.length).padStart(2, "0")} visible`;
  els.selectionCount.textContent = `${app.selectedIds.size} selected`;
}

function renderPreview() {
  const image = getCurrentImage();
  els.previewStage.classList.toggle("has-image", Boolean(image));
  els.previewEmpty.hidden = Boolean(image);
  els.previewImage.hidden = !image;
  if (!image) {
    els.previewTitle.textContent = "等待图像";
    els.previewPath.textContent = "--";
    els.previewTagCount.textContent = "00";
    els.previewStatus.textContent = "STANDBY";
    els.previewStatus.classList.remove("modified");
    els.currentTags.innerHTML = "";
    els.naturalDescriptionBlock.hidden = true;
    els.naturalDescription.textContent = "";
    return;
  }
  els.previewTitle.textContent = image.name;
  els.previewPath.textContent = image.relativePath;
  els.previewTagCount.textContent = String(image.tags.length).padStart(2, "0");
  els.previewStatus.textContent = image.modified ? "UNSAVED" : "READY";
  els.previewStatus.classList.toggle("modified", image.modified);
  els.previewImage.src = image.src;
  els.previewImage.alt = image.name;
  els.currentTags.innerHTML = "";
  image.tags.forEach((tag) => {
    const chip = document.createElement("span");
    chip.className = "current-tag";
    chip.innerHTML = `<span></span><button title="从当前图片移除" aria-label="从当前图片移除">×</button>`;
    chip.firstElementChild.textContent = tag;
    chip.lastElementChild.addEventListener("click", () => removeTag(tag, "current"));
    els.currentTags.appendChild(chip);
  });
  const description = String(image.description || "").trim();
  els.naturalDescriptionBlock.hidden = !description;
  els.naturalDescription.textContent = description;
}

function filteredTags() {
  const query = els.tagSearch.value.trim().toLowerCase();
  const current = getCurrentImage();
  return app.state.tags.filter((entry) => {
    if (app.showCurrentTags && current && !current.tags.includes(entry.tag)) return false;
    if (!query) return true;
    return `${entry.tag} ${entry.translation || ""}`.toLowerCase().includes(query);
  });
}

function renderTags() {
  const current = getCurrentImage();
  const visible = filteredTags();
  els.tagTable.innerHTML = "";
  visible.forEach((entry) => {
    const row = $("#tagRowTemplate").content.firstElementChild.cloneNode(true);
    row.classList.toggle("in-current", Boolean(current?.tags.includes(entry.tag)));
    const main = $(".tag-main", row);
    const state = $(".tag-state", row);
    const name = $(".tag-name", row);
    const translation = $(".tag-translation", row);
    name.textContent = entry.tag;
    translation.textContent = entry.translation || "点击编辑译文";
    translation.title = "点击编辑译文";
    $(".tag-frequency", row).textContent = String(entry.count).padStart(2, "0");
    main.addEventListener("click", () => addTag(entry.tag));
    state.title = current?.tags.includes(entry.tag) ? "当前图片已有此标签" : "添加到当前图片";
    translation.addEventListener("click", (event) => {
      event.stopPropagation();
      startTranslationEdit(row, entry);
    });
    $(".favorite-tag-button", row).addEventListener("click", () => addFavoriteTag(entry.tag));
    const removeButton = $(".remove-tag-button", row);
    const isInCurrent = Boolean(current?.tags.includes(entry.tag));
    removeButton.disabled = !isInCurrent;
    removeButton.title = isInCurrent ? "从当前图片移除" : "当前图片没有此标签";
    removeButton.setAttribute("aria-label", removeButton.title);
    removeButton.addEventListener("click", () => removeTag(entry.tag, "current"));
    els.tagTable.appendChild(row);
  });
  els.tagPanel.classList.toggle("has-tags", app.state.tags.length > 0);
  els.tagCount.textContent = String(app.state.tags.length).padStart(2, "0");
  els.emptyTags.hidden = app.state.tags.length > 0 || Boolean(visible.length);
}

function startTranslationEdit(row, entry) {
  const span = $(".tag-translation", row);
  if (row.querySelector(".translation-editor")) return;
  const input = document.createElement("input");
  input.className = "translation-editor";
  input.value = entry.translation || "";
  input.placeholder = "输入中文译文";
  span.replaceWith(input);
  input.focus();
  input.select();
  let submitted = false;
  const submit = async () => {
    if (submitted) return;
    submitted = true;
    try {
      setStatus("保存翻译键...", "busy");
      const payload = await api("/api/translations", { method: "POST", body: { tag: entry.tag, translation: input.value } });
      hydrate(payload);
      setStatus(`已更新 ${entry.tag}`);
    } catch (error) {
      setStatus(error.message, "error");
      renderTags();
    }
  };
  input.addEventListener("blur", submit);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") input.blur();
    if (event.key === "Escape") { submitted = true; renderTags(); }
  });
}

function renderFavorites() {
  els.favoriteTarget.innerHTML = "";
  app.state.favorites.forEach((group, index) => {
    const option = document.createElement("option");
    option.value = group.id;
    option.textContent = `${String(index + 1).padStart(2, "0")} / ${group.name}`;
    els.favoriteTarget.appendChild(option);
  });
  if (app.state.favorites.length && !app.state.favorites.some((group) => group.id === els.favoriteTarget.value)) {
    els.favoriteTarget.value = app.state.favorites[0].id;
  }
  els.favoritesList.innerHTML = "";
  app.state.favorites.forEach((group) => {
    const section = document.createElement("div");
    section.className = "favorite-group";
    const head = document.createElement("div");
    head.className = "favorite-group-head";
    const title = document.createElement("strong");
    title.textContent = group.name;
    const actions = document.createElement("div");
    const rename = document.createElement("button");
    rename.textContent = "edit";
    rename.title = "重命名收藏栏";
    rename.addEventListener("click", () => renameFavoriteGroup(group.id));
    const remove = document.createElement("button");
    remove.textContent = "×";
    remove.title = "删除收藏栏";
    remove.addEventListener("click", () => removeFavoriteGroup(group.id));
    actions.append(rename, remove);
    head.append(title, actions);
    const chips = document.createElement("div");
    chips.className = "favorite-chips";
    group.tags.forEach((tag) => {
      const chip = document.createElement("span");
      chip.className = "tag-chip";
      chip.innerHTML = `<button title="添加到当前图片"></button><button title="移除收藏">×</button>`;
      chip.firstElementChild.textContent = tag;
      chip.firstElementChild.addEventListener("click", () => addTag(tag));
      chip.lastElementChild.addEventListener("click", () => removeFavoriteTag(group.id, tag));
      chips.appendChild(chip);
    });
    section.append(head, chips);
    els.favoritesList.appendChild(section);
  });
}

function serializeRules() {
  return $$(".rule-item", els.rulesList).map((item) => ({
    delete: $(".rule-delete", item).value,
    deleteMode: $(".rule-delete-mode", item).value,
    write: $(".rule-write", item).value,
    writeMode: $(".rule-write-mode", item).value,
    condition: $(".rule-condition", item).value,
    conditionMode: $(".rule-condition-mode", item).value,
  }));
}

function renderRules() {
  els.rulesList.innerHTML = "";
  app.state.rules.forEach((rule, index) => {
    const item = $("#ruleTemplate").content.firstElementChild.cloneNode(true);
    $(".rule-index", item).textContent = `R${String(index + 1).padStart(2, "0")}`;
    $(".rule-delete", item).value = rule.delete || "";
    $(".rule-delete-mode", item).value = rule.deleteMode || "any";
    $(".rule-write", item).value = rule.write || "";
    $(".rule-write-mode", item).value = rule.writeMode || "any";
    $(".rule-condition", item).value = rule.condition || "";
    $(".rule-condition-mode", item).value = rule.conditionMode || "any";
    $(".remove-rule", item).addEventListener("click", () => {
      item.remove();
      persistRules(true);
    });
    $$('input, select', item).forEach((field) => field.addEventListener("input", () => persistRules()));
    $$('input, select', item).forEach((field) => field.addEventListener("change", () => persistRules(true)));
    els.rulesList.appendChild(item);
  });
  els.ruleCount.textContent = String(app.state.rules.length).padStart(2, "0");
}

function persistRules(immediate = false) {
  if (app.ruleSaveTimer) clearTimeout(app.ruleSaveTimer);
  const send = async () => {
    try {
      const payload = await api("/api/rules", { method: "POST", body: { rules: serializeRules() } });
      app.state.rules = payload.rules || serializeRules();
      els.ruleCount.textContent = String(app.state.rules.length).padStart(2, "0");
    } catch (error) { setStatus(error.message, "error"); }
  };
  app.ruleSaveTimer = setTimeout(send, immediate ? 0 : 450);
}

async function loadFolder() {
  const path = els.folderPath.value.trim();
  if (!path) { setStatus("请先输入文件夹路径", "error"); els.folderPath.focus(); return; }
  try {
    setStatus("扫描目录与标签...", "busy");
    const payload = await api("/api/library/load", { method: "POST", body: { path } });
    app.selectedIds.clear();
    hydrate(payload);
    setStatus(`已装载 ${payload.images?.length || 0} 张图片`);
  } catch (error) { setStatus(error.message, "error"); }
}

async function chooseFolderPath() {
  try {
    setStatus("打开本机路径选择器...", "busy");
    const payload = await api("/api/library/choose-folder", { method: "POST", body: {} });
    if (!payload.path) {
      setStatus("已取消路径选择");
      return;
    }
    els.folderPath.value = payload.path;
    setStatus("已选择路径，请扫描");
  } catch (error) { setStatus(error.message, "error"); }
}

function clickImageRow(event) {
  const row = event.target.closest(".image-row");
  if (!row) return;
  const id = row.dataset.id;
  const index = app.filteredIds.indexOf(id);
  if (event.shiftKey && app.lastClickedIndex >= 0) {
    const [start, end] = [app.lastClickedIndex, index].sort((a, b) => a - b);
    app.selectedIds = new Set(app.filteredIds.slice(start, end + 1));
  } else if (event.ctrlKey || event.metaKey) {
    if (app.selectedIds.has(id)) app.selectedIds.delete(id); else app.selectedIds.add(id);
    app.currentId = id;
  } else {
    app.selectedIds = new Set([id]);
    app.currentId = id;
  }
  app.lastClickedIndex = index;
  renderImages();
  renderPreview();
  renderTags();
}

function targetImageIds() {
  return app.selectedIds.size ? [...app.selectedIds] : (app.currentId ? [app.currentId] : []);
}

async function addTag(tag) {
  const ids = targetImageIds();
  if (!ids.length) { setStatus("请先选择图片", "error"); return; }
  try {
    const payload = await api("/api/tags", { method: "POST", body: { imageIds: ids, tags: [tag], action: "add" } });
    hydrate(payload);
    setStatus(`已写入 ${tag}`);
  } catch (error) { setStatus(error.message, "error"); }
}

async function removeTag(tag, scope = "targets") {
  const ids = scope === "current" ? (app.currentId ? [app.currentId] : []) : targetImageIds();
  if (!ids.length) return;
  try {
    const payload = await api("/api/tags", { method: "POST", body: { imageIds: ids, tags: [tag], action: "remove" } });
    hydrate(payload);
    setStatus(`已移除 ${tag}`);
  } catch (error) { setStatus(error.message, "error"); }
}

async function addFavoriteTag(tag) {
  const groupId = els.favoriteTarget.value || app.state.favorites[0]?.id;
  if (!groupId) return;
  const favorites = app.state.favorites.map((group) => group.id === groupId ? { ...group, tags: [...new Set([...group.tags, tag])] } : group);
  try { hydrate(await api("/api/favorites", { method: "POST", body: { favorites } })); setStatus(`已收藏 ${tag}`); } catch (error) { setStatus(error.message, "error"); }
}

async function removeFavoriteTag(groupId, tag) {
  const favorites = app.state.favorites.map((group) => group.id === groupId ? { ...group, tags: group.tags.filter((item) => item !== tag) } : group);
  try { hydrate(await api("/api/favorites", { method: "POST", body: { favorites } })); setStatus("已更新收藏栏"); } catch (error) { setStatus(error.message, "error"); }
}

async function renameFavoriteGroup(groupId) {
  const group = app.state.favorites.find((item) => item.id === groupId);
  if (!group) return;
  const name = window.prompt("收藏栏名称", group.name);
  if (name === null) return;
  const favorites = app.state.favorites.map((item) => item.id === groupId ? { ...item, name: name.trim() || item.name } : item);
  try { hydrate(await api("/api/favorites", { method: "POST", body: { favorites } })); setStatus("收藏栏已重命名"); } catch (error) { setStatus(error.message, "error"); }
}

async function removeFavoriteGroup(groupId) {
  if (app.state.favorites.length <= 1) { setStatus("至少保留一个收藏栏", "error"); return; }
  const favorites = app.state.favorites.filter((group) => group.id !== groupId);
  try { hydrate(await api("/api/favorites", { method: "POST", body: { favorites } })); setStatus("收藏栏已移除"); } catch (error) { setStatus(error.message, "error"); }
}

async function addFavoriteGroup() {
  const name = window.prompt("新收藏栏名称", `收藏栏 ${String(app.state.favorites.length + 1).padStart(2, "0")}`);
  if (name === null) return;
  const favorites = [...app.state.favorites, { id: crypto.randomUUID(), name: name.trim() || `收藏栏 ${app.state.favorites.length + 1}`, tags: [] }];
  try { hydrate(await api("/api/favorites", { method: "POST", body: { favorites } })); setStatus("已新建收藏栏"); } catch (error) { setStatus(error.message, "error"); }
}

async function applyRules(scope = "selected") {
  const rules = serializeRules();
  if (!rules.length) { setStatus("请先新增替换规则", "error"); return; }
  const ids = scope === "all" ? app.state.images.map((image) => image.id) : [...app.selectedIds];
  if (!ids.length) {
    setStatus(scope === "all" ? "当前没有可处理的图片" : "请先选择图片", "error");
    return;
  }
  try {
    setStatus(scope === "all" ? "正在对全部图片执行规则..." : "正在对选中图片执行规则...", "busy");
    const payload = await api("/api/apply-rules", { method: "POST", body: { rules, imageIds: ids } });
    hydrate(payload);
    setStatus(`${scope === "all" ? "全部图片" : "选中图片"}完成：${payload.changedImages || 0} 张产生改动`);
  } catch (error) { setStatus(error.message, "error"); }
}

async function saveChanged() {
  try {
    setStatus("保存标签文件...", "busy");
    const payload = await api("/api/library/save", { method: "POST", body: {} });
    hydrate(payload);
    setStatus(payload.failed?.length ? `保存完成，${payload.failed.length} 个文件失败` : `已保存 ${payload.saved || 0} 个标签文件`, payload.failed?.length ? "error" : "ready");
  } catch (error) { setStatus(error.message, "error"); }
}

const REVERT_HOLD_MS = 900;
const REVERT_FADE_MS = 180;

function beginRevertFade() {
  if (app.revertFadeTimer) clearTimeout(app.revertFadeTimer);
  els.revertButton.classList.remove("is-holding", "is-complete", "is-canceling");
  els.revertButton.classList.add("is-fading");
  app.revertFadeTimer = window.setTimeout(() => {
    app.revertFadeTimer = null;
    if (app.state.changedCount === 0 && els.revertButton.classList.contains("is-fading")) {
      els.revertButton.classList.remove("is-fading");
      els.revertButton.classList.add("is-hidden");
    }
  }, REVERT_FADE_MS);
}

function cancelRevertHold(message = "") {
  const hold = app.revertHold;
  if (!hold) return;
  cancelAnimationFrame(hold.frame);
  clearTimeout(hold.timer);
  app.revertHold = null;
  els.revertButton.classList.remove("is-holding", "is-complete");
  els.revertButton.classList.add("is-canceling");
  els.revertButton.style.setProperty("--hold-progress", "0");
  window.setTimeout(() => els.revertButton.classList.remove("is-canceling"), 260);
  if (message) setStatus(message);
}

function updateRevertHold(now) {
  const hold = app.revertHold;
  if (!hold) return;
  const progress = Math.min(1, (now - hold.startedAt) / REVERT_HOLD_MS);
  els.revertButton.style.setProperty("--hold-progress", String(progress));
  if (progress >= 1) {
    hold.complete = true;
    els.revertButton.classList.add("is-complete");
    return;
  }
  hold.frame = requestAnimationFrame(updateRevertHold);
}

async function revertChanges() {
  beginRevertFade();
  try {
    els.revertButton.disabled = true;
    setStatus("正在清除未保存改动...", "busy");
    const payload = await api("/api/library/revert", { method: "POST", body: {} });
    hydrate(payload);
    setStatus(`已清除 ${payload.revertedImages || 0} 张图片的改动`);
  } catch (error) {
    if (app.revertFadeTimer) {
      clearTimeout(app.revertFadeTimer);
      app.revertFadeTimer = null;
    }
    els.revertButton.classList.remove("is-holding", "is-complete", "is-fading", "is-hidden");
    els.revertButton.style.setProperty("--hold-progress", "0");
    syncRevertButton();
    setStatus(error.message, "error");
  }
}

function startRevertHold(event) {
  if (els.revertButton.disabled || app.revertHold) return;
  if (event.pointerType === "mouse" && event.button !== 0) return;
  event.preventDefault();
  try { els.revertButton.setPointerCapture(event.pointerId); } catch {}
  const startedAt = performance.now();
  const hold = { pointerId: event.pointerId, startedAt, complete: false, frame: 0, timer: 0 };
  app.revertHold = hold;
  els.revertButton.classList.remove("is-canceling", "is-hidden");
  els.revertButton.classList.add("is-holding");
  els.revertButton.style.setProperty("--hold-progress", "0");
  hold.frame = requestAnimationFrame(updateRevertHold);
  hold.timer = window.setTimeout(() => updateRevertHold(performance.now()), REVERT_HOLD_MS);
}

async function finishRevertHold(event) {
  const hold = app.revertHold;
  if (!hold || (event.pointerId !== undefined && hold.pointerId !== event.pointerId)) return;
  event.preventDefault();
  try { els.revertButton.releasePointerCapture(event.pointerId); } catch {}
  if (!hold.complete) {
    cancelRevertHold("已取消清除改动");
    return;
  }
  cancelAnimationFrame(hold.frame);
  clearTimeout(hold.timer);
  app.revertHold = null;
  await revertChanges();
}

function cancelClearLlmCacheHold(message = "") {
  const hold = app.clearLlmCacheHold;
  if (!hold) return;
  cancelAnimationFrame(hold.frame);
  clearTimeout(hold.timer);
  app.clearLlmCacheHold = null;
  els.clearLlmCacheButton.classList.remove("is-holding", "is-complete");
  els.clearLlmCacheButton.classList.add("is-canceling");
  els.clearLlmCacheButton.style.setProperty("--hold-progress", "0");
  window.setTimeout(() => els.clearLlmCacheButton.classList.remove("is-canceling"), 260);
  if (message) setStatus(message);
}

function updateClearLlmCacheHold(now) {
  const hold = app.clearLlmCacheHold;
  if (!hold) return;
  const progress = Math.min(1, (now - hold.startedAt) / REVERT_HOLD_MS);
  els.clearLlmCacheButton.style.setProperty("--hold-progress", String(progress));
  if (progress >= 1) {
    hold.complete = true;
    els.clearLlmCacheButton.classList.add("is-complete");
    return;
  }
  hold.frame = requestAnimationFrame(updateClearLlmCacheHold);
}

async function clearLlmCache() {
  const button = els.clearLlmCacheButton;
  button.disabled = true;
  try {
    setStatus("正在清除 LLM 缓存标记...", "busy");
    const payload = await api("/api/llm/cache/clear", { method: "POST", body: {} });
    hydrate(payload);
    setStatus(`已清除 ${payload.clearedEntries || 0} 个 LLM 缓存标记，TXT 与图片未修改`);
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    button.classList.remove("is-holding", "is-complete", "is-canceling");
    button.style.setProperty("--hold-progress", "0");
    renderLlmState();
  }
}

function startClearLlmCacheHold(event) {
  const button = els.clearLlmCacheButton;
  if (button.disabled || app.clearLlmCacheHold) return;
  if (event.pointerType === "mouse" && event.button !== 0) return;
  event.preventDefault();
  try { button.setPointerCapture(event.pointerId); } catch {}
  const hold = { pointerId: event.pointerId, startedAt: performance.now(), complete: false, frame: 0, timer: 0 };
  app.clearLlmCacheHold = hold;
  button.classList.remove("is-canceling");
  button.classList.add("is-holding");
  button.style.setProperty("--hold-progress", "0");
  hold.frame = requestAnimationFrame(updateClearLlmCacheHold);
  hold.timer = window.setTimeout(() => updateClearLlmCacheHold(performance.now()), REVERT_HOLD_MS);
}

async function finishClearLlmCacheHold(event) {
  const hold = app.clearLlmCacheHold;
  if (!hold || (event.pointerId !== undefined && hold.pointerId !== event.pointerId)) return;
  event.preventDefault();
  try { els.clearLlmCacheButton.releasePointerCapture(event.pointerId); } catch {}
  if (!hold.complete) {
    cancelClearLlmCacheHold("已取消清除 LLM 缓存");
    return;
  }
  cancelAnimationFrame(hold.frame);
  clearTimeout(hold.timer);
  app.clearLlmCacheHold = null;
  await clearLlmCache();
}

function addRule() {
  app.state.rules = [...app.state.rules, { delete: "", deleteMode: "any", write: "", writeMode: "any", condition: "", conditionMode: "any" }];
  renderRules();
  persistRules(true);
  const last = $(".rule-item:last-child", els.rulesList);
  last?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  $(".rule-delete", last)?.focus();
}

function setMode(mode) {
  app.activeMode = mode;
  $$(".mode-tab").forEach((tab) => { const active = tab.dataset.mode === mode; tab.classList.toggle("active", active); tab.setAttribute("aria-selected", String(active)); });
  $$('[data-mode-panel]').forEach((panel) => panel.classList.toggle("active", panel.dataset.modePanel === mode));
}

async function importJson(file, kind) {
  try {
    const content = await file.text();
    const parsed = JSON.parse(content);
    const path = kind === "rules" ? "/api/rules" : "/api/translations";
    const body = kind === "rules" ? { rules: parsed } : { translations: parsed };
    hydrate(await api(path, { method: "POST", body }));
    setStatus(kind === "rules" ? "替换规则已导入" : "翻译键已导入");
  } catch (error) { setStatus(`JSON 导入失败: ${error.message}`, "error"); }
}

function resetFilters() {
  [els.imageSearch, els.includeFilter, els.excludeFilter].forEach((field) => { field.value = ""; });
  els.includeMode.value = "all";
  els.excludeMode.value = "all";
  renderImages();
}

function relocateSourcePanel() {
  const source = document.querySelector(".source-section");
  const imagePanel = document.querySelector(".image-panel");
  const heading = imagePanel?.querySelector(".panel-heading");
  if (!source || !imagePanel || !heading || source.parentElement === imagePanel) return;
  source.classList.add("image-source-strip");
  const title = source.querySelector(".section-title span");
  const index = source.querySelector(".section-title em");
  if (title) title.textContent = "SOURCE FOLDER";
  if (index) index.textContent = "02";
  imagePanel.insertBefore(source, heading.nextElementSibling);
}

function fileNameFromPath(value) {
  const parts = String(value || "").split(/[\\/]/);
  return (parts[parts.length - 1] || "").toLocaleLowerCase();
}

function showDialog(dialog) {
  if (!dialog.open) dialog.showModal();
  document.body.classList.add("dialog-open");
}

function closeDialog(dialog) {
  if (dialog.open) dialog.close();
  document.body.classList.toggle("dialog-open", Boolean(document.querySelector("dialog[open]")));
}

const LLM_STAGE_LABELS = {
  cache: ["CACHE", "缓存检查"],
  apose: ["APOSE", "角色参考"],
  vision: ["VISION", "视觉反推"],
  clean: ["CLEAN", "数据清洗"],
  write: ["WRITE", "结果写入"],
};

function formatLlmDuration(value) {
  const total = Math.max(0, Math.floor(Number(value || 0)));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours) return `${hours}h ${String(minutes).padStart(2, "0")}m ${String(seconds).padStart(2, "0")}s`;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function renderLlmJob(job) {
  const previousJob = app.llmJob;
  const incomingActive = Array.isArray(job?.activeStages) ? job.activeStages : [];
  if (job && previousJob?.id === job.id && Number(job.done || 0) > Number(previousJob.done || 0)) {
    const currentNames = new Set(incomingActive.map((entry) => entry.image));
    const failedNames = new Set((job.errors || []).map((message) => String(message).split(":", 1)[0]));
    (previousJob.activeStages || [])
      .filter((entry) => entry.image && !currentNames.has(entry.image))
      .forEach((entry) => {
        app.llmRecentStages = [
          {
            image: entry.image,
            status: failedNames.has(entry.image) ? "failed" : "completed",
            finishedAt: new Date().toISOString(),
          },
          ...app.llmRecentStages.filter((recent) => recent.image !== entry.image),
        ].slice(0, 3);
      });
  } else if (job && previousJob?.id !== job.id) {
    app.llmRecentStages = [];
  }
  if (Array.isArray(job?.recentStages) && job.recentStages.length) {
    app.llmRecentStages = job.recentStages.slice(0, 3);
  }
  app.llmJob = job || null;
  els.llmProgress.hidden = !job;
  if (!job) {
    app.llmRecentStages = [];
    els.llmStageList?.replaceChildren();
    if (els.llmStageList) els.llmStageList.hidden = true;
    return;
  }
  const total = Number(job.total || 0);
  const done = Number(job.done || 0);
  const activeStages = incomingActive;
  const totalElapsed = Number(job.elapsedSeconds || 0);
  els.llmProgressLabel.textContent = `${String(job.status || "queue").toUpperCase().replaceAll("_", " ")} · ${formatLlmDuration(totalElapsed)}`;
  els.llmProgressCount.textContent = String(done).padStart(2, "0") + " / " + String(total).padStart(2, "0");
  els.llmProgressBar.max = Math.max(1, total);
  els.llmProgressBar.value = Math.min(done, Math.max(1, total));
  if (els.llmStageList) {
    els.llmStageList.replaceChildren();
    activeStages.forEach((entry) => {
      const row = document.createElement("div");
      const signal = document.createElement("i");
      const code = document.createElement("b");
      const name = document.createElement("span");
      const elapsed = document.createElement("time");
      const labels = LLM_STAGE_LABELS[entry.stage] || [String(entry.stage || "RUN").toUpperCase(), "执行中"];
      const started = Date.parse(entry.startedAt || "");
      const updated = Date.parse(entry.updatedAt || "");
      const seconds = Number.isFinite(started) ? Math.max(0, Math.floor((Date.now() - started) / 1000)) : 0;
      const attempt = Math.max(1, Number(entry.attempt || 1));
      const maxAttempts = Math.max(attempt, Number(entry.maxAttempts || 1));
      const retryRemaining = entry.phase === "retry_wait" && Number.isFinite(updated)
        ? Math.max(0, Math.ceil(Number(entry.retryInSeconds || 0) - ((Date.now() - updated) / 1000)))
        : 0;
      const requestCode = maxAttempts > 1 ? `${labels[0]} ${attempt}/${maxAttempts}` : labels[0];
      const codeLabel = entry.phase === "retry_wait" ? `RETRY ${attempt}/${maxAttempts}` : (entry.phase === "response" ? "CHECK" : requestCode);
      const activityLabel = entry.phase === "retry_wait" ? `${retryRemaining}s 后重试` : labels[1];
      const route = [entry.provider, entry.model].filter(Boolean).join(" / ");
      const requestPixels = Number(entry.requestImageMaxPixels || 0);
      const imageProfile = requestPixels
        ? `请求图像 ≤ ${(requestPixels / 1_000_000).toFixed(requestPixels % 1_000_000 ? 1 : 0)}MP${entry.requestImageQuality ? ` · JPEG ${entry.requestImageQuality}` : ""}`
        : "";
      row.className = "llm-stage-row";
      row.dataset.stage = entry.stage || "run";
      row.dataset.phase = entry.phase || "stage";
      row.dataset.compression = String(entry.imageCompressionLevel || 0);
      row.title = [
        `${entry.image || "--"} · ${labels[1]}`,
        route,
        entry.timeoutSeconds ? `单次超时 ${entry.timeoutSeconds}s` : "",
        imageProfile,
        entry.reason ? `重试原因 ${entry.reason}` : "",
      ].filter(Boolean).join(" · ");
      code.textContent = codeLabel;
      name.textContent = `${entry.image || "--"} · ${activityLabel}`;
      elapsed.textContent = `${seconds}s`;
      row.append(signal, code, name, elapsed);
      els.llmStageList.appendChild(row);
    });
    const recentLimit = Math.max(1, 3 - activeStages.length);
    app.llmRecentStages.slice(0, recentLimit).forEach((entry) => {
      const row = document.createElement("div");
      const signal = document.createElement("i");
      const code = document.createElement("b");
      const name = document.createElement("span");
      const elapsed = document.createElement("time");
      const failed = entry.status === "failed";
      const finished = Date.parse(entry.finishedAt || "");
      const age = Number.isFinite(finished) ? Math.max(0, Math.floor((Date.now() - finished) / 1000)) : 0;
      row.className = "llm-stage-row llm-stage-recent";
      row.dataset.phase = failed ? "failed" : "completed";
      row.title = `${entry.image || "--"} · ${failed ? "处理失败" : "最近完成"}`;
      code.textContent = failed ? "FAIL" : (entry.status === "cached" ? "CACHE" : "DONE");
      name.textContent = `${entry.image || "--"} · ${failed ? "处理失败" : "最近完成"}`;
      elapsed.textContent = `${age}s`;
      row.append(signal, code, name, elapsed);
      els.llmStageList.appendChild(row);
    });
    els.llmStageList.hidden = !activeStages.length && !app.llmRecentStages.length;
  }
  const summary = [`ACTIVE ${String(activeStages.length).padStart(2, "0")}`, `CACHE ${String(job.cached || 0).padStart(2, "0")}`];
  if (job.failed) summary.push(`FAILED ${String(job.failed).padStart(2, "0")}`);
  els.llmProgressCurrent.textContent = activeStages.length ? summary.join(" · ") : (job.current || "--");
}

function selectedCallProviders() {
  const config = app.state.llmConfig || {};
  const useDraft = Boolean(els.llmConfigDialog?.open);
  const value = (field, name, fallback = "volcano") => useDraft && field?.value ? field.value : (config[name] || fallback);
  const providers = new Set([
    value(els.tagProvider, "tagProvider"),
    value(els.cleanProvider, "cleanProvider"),
  ]);
  if (!els.styleMode?.checked) providers.add(value(els.aposeProvider, "aposeProvider"));
  return providers;
}

function renderLlmState() {
  const config = app.state.llmConfig || {};
  const ready = [...selectedCallProviders()].every((provider) => provider === "gemini" ? config.geminiKeySet : config.volcanoKeySet);
  els.llmConfigReadout.dataset.ready = String(ready);
  els.llmConfigState.textContent = ready ? "API READY" : "API 待配置";
  const useDraft = Boolean(els.llmConfigDialog?.open);
  const fieldValue = (field, name) => useDraft && field?.value ? field.value : config[name];
  const route = (name, provider, model) => `${name}: ${(provider || "volcano").toUpperCase()} / ${model || "未选择"}`;
  els.llmModelReadout.textContent = [
    route("TAG", fieldValue(els.tagProvider, "tagProvider"), fieldValue(els.tagModel, "tagModel")),
    route("APOSE", fieldValue(els.aposeProvider, "aposeProvider"), fieldValue(els.aposeModel, "aposeModel")),
    route("CLEAN", fieldValue(els.cleanProvider, "cleanProvider"), fieldValue(els.cleanModel, "cleanModel")),
  ].join(" · ");
  const busy = Boolean(
    (app.llmJob && ["queued", "running"].includes(app.llmJob.status))
    || app.state.llmJobs.some((job) => ["queued", "running"].includes(job.status))
  );
  $("#llmButton").disabled = busy || !app.state.images.length;
  $("#llmAllButton").disabled = busy || !app.state.images.length;
  $("#llmConfigButton").disabled = busy;
  $("#characterManagerButton").disabled = busy;
  els.clearLlmCacheButton.disabled = busy || !app.state.images.length;
  els.clearLlmCacheButton.setAttribute("aria-disabled", String(els.clearLlmCacheButton.disabled));
  if (els.clearLlmCacheButton.disabled && app.clearLlmCacheHold) cancelClearLlmCacheHold();
  els.styleMode.disabled = busy;
  els.allowNaturalLanguage.disabled = busy;
  [els.tagProvider, els.tagModel, els.aposeProvider, els.aposeModel, els.cleanProvider, els.cleanModel].forEach((field) => { if (field) field.disabled = busy; });
  renderModelCapabilities();
  if (!busy && app.llmJob && !["queued", "running"].includes(app.llmJob.status)) renderLlmJob(null);
}

const CONFIG_FIELDS = [
  "volcanoUrl", "volcanoProtocol",
  "geminiUrl", "geminiProtocol",
  "tagProvider", "tagModel", "aposeProvider", "aposeModel", "cleanProvider", "cleanModel",
  "timeoutSeconds", "retryCount", "retryDelaySeconds", "imageDetail", "imageMaxPixels",
  "visionPrompt", "cleanPrompt", "characterPrompt", "characterAuditMode",
];

function syncCharacterAuditMode() {
  const mode = $("#characterAuditMode");
  const auditor = $("#skillPipelineList input[data-skill-id='character-tag-auditor']");
  if (mode) mode.disabled = !auditor || auditor.disabled || !auditor.checked;
}

function modelCatalogFor(provider) {
  return Array.isArray(app.state.llmModels?.[provider]) ? app.state.llmModels[provider] : [];
}

function renderModelCatalog(provider) {
  const list = $("#" + provider + "ModelCatalog");
  if (!list) return;
  list.replaceChildren();
  const models = modelCatalogFor(provider);
  if (!models.length) return;
  models.forEach((model) => {
    const item = document.createElement("span");
    item.className = "model-catalog-item";
    const name = document.createElement("b");
    name.textContent = model.id || "UNKNOWN";
    const capability = document.createElement("small");
    capability.textContent = model.vision === true ? "VISION" : model.vision === false ? "TEXT" : "CAPABILITY ?";
    capability.dataset.vision = model.vision === true ? "yes" : model.vision === false ? "no" : "unknown";
    item.append(name, capability);
    list.appendChild(item);
  });
}

function renderModelDatalist(input, provider) {
  if (!input) return;
  const datalist = $("#" + input.id + "Options");
  if (!datalist) return;
  datalist.replaceChildren();
  modelCatalogFor(provider).forEach((model) => {
    const option = document.createElement("option");
    option.value = model.id || "";
    option.label = model.vision === true ? "VISION" : model.vision === false ? "TEXT" : "CAPABILITY ?";
    datalist.appendChild(option);
  });
}

function renderModelCapabilities() {
  const stages = [
    { input: els.tagModel, provider: els.tagProvider, output: els.tagModelCapability, visionRequired: true },
    { input: els.aposeModel, provider: els.aposeProvider, output: els.aposeModelCapability, visionRequired: true },
    { input: els.cleanModel, provider: els.cleanProvider, output: els.cleanModelCapability, visionRequired: false },
  ];
  stages.forEach(({ input, provider, output, visionRequired }) => {
    if (!input || !provider || !output) return;
    renderModelDatalist(input, provider.value);
    const record = modelCatalogFor(provider.value).find((item) => item.id === input.value.trim());
    output.dataset.state = record?.vision === false && visionRequired ? "error" : record?.vision === true ? "success" : "unknown";
    if (!input.value.trim()) output.textContent = "未选择模型";
    else if (!visionRequired) output.textContent = (provider.value === "volcano" ? "火山方舟 Key" : "Gemini Key") + " · 文本调用";
    else if (record?.vision === true) output.textContent = "VISION READY · 支持图像输入";
    else if (record?.vision === false) output.textContent = "此模型未标记视觉能力，请更换模型";
    else output.textContent = "视觉能力待模型目录确认 · 手动模型需自行确认支持图片";
  });
}

function renderPromptAdaptation() {
  if (!els.promptAdaptationReadout) return;
  const mode = els.styleMode?.checked
    ? "画风模式：不发送角色描述，最终提示词强制删除画风/技法词并保留内容描述。"
    : "人物模式：匹配到角色词典时注入 A-pose 外观描述并执行角色审计。";
  const language = els.allowNaturalLanguage?.checked
    ? "自然语言：输出 tags 后保留一段描述。"
    : "自然语言：视觉描述仅用于内部校验，最终归档 description 为空。";
  els.promptAdaptationReadout.textContent = mode + " " + language;
}

function ensurePromptAdaptationTokens() {
  const additions = [
    ["#visionPrompt", [
      ["{{tags}}", "\n\nPrimary WD14 reference tags:\n{{tags}}"],
      ["{{dataset_mode_policy}}", "\n\nRuntime mode policy:\n{{dataset_mode_policy}}"],
      ["{{natural_language_policy}}", "\nFinal archive natural-language policy:\n{{natural_language_policy}}"],
    ]],
    ["#cleanPrompt", [
      ["{{tags}}", "\n\nOriginal WD14 tags (primary):\n{{tags}}"],
      ["{{vision_tags}}", "\n\nVision-model tags (secondary):\n{{vision_tags}}"],
      ["{{merged_tag_candidates}}", "\n\nMerged candidates (WD14 first):\n{{merged_tag_candidates}}"],
      ["{{vision_description}}", "\n\nVisual description:\n{{vision_description}}"],
      ["{{protected_dataset_tags}}", "\n\nProtected dataset activation tags:\n{{protected_dataset_tags}}"],
      ["{{dataset_mode_policy}}", "\n\nDataset mode policy:\n{{dataset_mode_policy}}"],
      ["{{natural_language_policy}}", "\n\nNatural-language policy:\n{{natural_language_policy}}"],
    ]],
    ["#characterPrompt", [
      ["{{dataset_mode_policy}}", "\n\nRuntime mode policy:\n{{dataset_mode_policy}}"],
    ]],
  ];
  additions.forEach(([selector, tokens]) => {
    const field = $(selector);
    if (!field) return;
    tokens.forEach(([token, suffix]) => {
      if (!field.value.includes(token)) field.value = field.value.trimEnd() + suffix;
    });
  });
}

async function refreshProviderModels(provider) {
  const button = $(provider === "volcano" ? "#refreshVolcanoModels" : "#refreshGeminiModels");
  const status = $(provider === "volcano" ? "#volcanoModelCatalogStatus" : "#geminiModelCatalogStatus");
  if (!button || !status) return;
  button.disabled = true;
  status.textContent = "正在获取...";
  try {
    const result = await api("/api/llm/models", {
      method: "POST",
      body: { provider, config: collectLlmTestConfig(provider) },
    });
    app.state.llmModels = { ...(app.state.llmModels || {}), [provider]: Array.isArray(result.models) ? result.models : [] };
    renderModelCatalog(provider);
    renderModelCapabilities();
    status.textContent = result.partial
      ? `目录接口不可用 · 显示 ${app.state.llmModels[provider].length} 个本地已知模型`
      : `已获取 ${app.state.llmModels[provider].length} 个模型`;
    status.title = result.warning || result.endpoint || "";
    setStatus(result.partial ? "供应商未开放模型目录，已保留手动模型输入" : `${provider === "volcano" ? "火山" : "Gemini"} 模型列表已更新`);
  } catch (error) {
    status.textContent = "获取失败 · " + error.message;
    setStatus(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

function renderSkillPipeline() {
  const list = $("#skillPipelineList");
  if (!list) return;
  const config = app.state.llmConfig || {};
  const skills = Array.isArray(config.skills) ? config.skills : [];
  const enabled = new Set(Array.isArray(config.enabledSkills) ? config.enabledSkills : []);
  list.replaceChildren();
  if (!skills.length) {
    const empty = document.createElement("div");
    empty.className = "skill-pipeline-empty";
    empty.textContent = "NO SKILLS DISCOVERED";
    list.appendChild(empty);
    syncCharacterAuditMode();
    return;
  }
  skills.forEach((skill) => {
    const row = document.createElement("label");
    row.className = "skill-pipeline-row";
    row.classList.toggle("invalid", !skill.valid);
    row.title = skill.valid ? (skill.description || skill.id) : (skill.error || "Skill 文件无效");

    const input = document.createElement("input");
    input.type = "checkbox";
    input.dataset.skillId = skill.id;
    input.checked = Boolean(skill.valid && enabled.has(skill.id));
    input.disabled = !skill.valid;
    input.addEventListener("change", syncCharacterAuditMode);

    const toggle = document.createElement("i");
    toggle.setAttribute("aria-hidden", "true");
    const copy = document.createElement("span");
    copy.className = "skill-pipeline-copy";
    const name = document.createElement("strong");
    name.textContent = skill.displayName || skill.name || skill.id;
    const id = document.createElement("small");
    id.textContent = skill.id || "UNKNOWN";
    copy.append(name, id);
    const state = document.createElement("span");
    state.className = "skill-pipeline-state";
    state.textContent = skill.valid ? ((skill.stage || "RUNTIME") + " / " + (skill.hash || "NO HASH")) : "INVALID";
    row.append(input, toggle, copy, state);
    list.appendChild(row);
  });
  syncCharacterAuditMode();
}

function populateLlmConfig() {
  const config = app.state.llmConfig || {};
  CONFIG_FIELDS.forEach((name) => {
    const field = $("#" + name);
    if (field) field.value = config[name] ?? "";
  });
  ensurePromptAdaptationTokens();
  // Keep only keys entered during this page session; the server never returns saved key values.
  $("#volcanoKey").value = app.savedKeyDrafts.volcano || "";
  $("#geminiKey").value = app.savedKeyDrafts.gemini || "";
  $("#volcanoKey").placeholder = config.volcanoKeySet ? "已安全保存 · 留空表示保留" : "输入火山 API Key";
  $("#geminiKey").placeholder = config.geminiKeySet ? "已安全保存 · 留空表示保留" : "输入 Gemini API Key";
  app.clearVolcanoKey = false;
  app.clearGeminiKey = false;
  $("#clearVolcanoKey").classList.remove("pending-clear");
  $("#clearGeminiKey").classList.remove("pending-clear");
  $("#clearVolcanoKey").textContent = "清除已存密钥";
  $("#clearGeminiKey").textContent = "清除已存密钥";
  const volcanoKeySet = Boolean(config.volcanoKeySet);
  const geminiKeySet = Boolean(config.geminiKeySet);
  $("#volcanoKeyStatus").textContent = volcanoKeySet ? "KEY SECURED / 已配置" : "KEY EMPTY / 未配置";
  $("#geminiKeyStatus").textContent = geminiKeySet ? "KEY SECURED / 已配置" : "KEY EMPTY / 未配置";
  $("#volcanoKeyStatus").classList.toggle("is-set", volcanoKeySet);
  $("#geminiKeyStatus").classList.toggle("is-set", geminiKeySet);
  $("#keyStorageReadout").textContent = config.keyStorage || "LOCAL SECURE STORE";
  setLlmTestStatus();
  renderModelCatalog("volcano");
  renderModelCatalog("gemini");
  renderModelCapabilities();
  renderPromptAdaptation();
  renderSkillPipeline();
}

function setLlmTestStatus(message = "", type = "") {
  const output = $("#llmTestStatus");
  if (!output) return;
  output.textContent = message;
  output.dataset.state = type;
  output.classList.toggle("is-visible", Boolean(message));
}

function setConfigTab(name) {
  $$(".dialog-tab", els.llmConfigDialog).forEach((tab) => {
    const active = tab.dataset.configTab === name;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  $$(".config-page", els.llmConfigDialog).forEach((page) => page.classList.toggle("active", page.dataset.configPage === name));
}

function openLlmConfig() {
  populateLlmConfig();
  setConfigTab("providers");
  showDialog(els.llmConfigDialog);
}

function toggleSecretClear(provider) {
  const volcano = provider === "volcano";
  const property = volcano ? "clearVolcanoKey" : "clearGeminiKey";
  const button = volcano ? $("#clearVolcanoKey") : $("#clearGeminiKey");
  app[property] = !app[property];
  button.classList.toggle("pending-clear", app[property]);
  button.textContent = app[property] ? "将清除" : "清除已存密钥";
}

function collectLlmConfig() {
  const payload = {};
  CONFIG_FIELDS.forEach((name) => {
    const field = $("#" + name);
    if (field) payload[name] = field.value;
  });
  if ($("#volcanoKey").value.trim()) payload.volcanoKey = $("#volcanoKey").value.trim();
  if ($("#geminiKey").value.trim()) payload.geminiKey = $("#geminiKey").value.trim();
  payload.enabledSkills = $$("#skillPipelineList input[data-skill-id]:checked").map((field) => field.dataset.skillId);
  payload.clearVolcanoKey = app.clearVolcanoKey;
  payload.clearGeminiKey = app.clearGeminiKey;
  return payload;
}

function collectLlmTestConfig(provider) {
  const payload = {};
  CONFIG_FIELDS.forEach((name) => {
    const field = $("#" + name);
    if (field) payload[name] = field.value;
  });
  const keyField = provider === "gemini" ? $("#geminiKey") : $("#volcanoKey");
  const key = keyField?.value.trim();
  if (key) payload[provider + "Key"] = key;
  payload["clear" + (provider === "gemini" ? "Gemini" : "Volcano") + "Key"] = provider === "gemini" ? app.clearGeminiKey : app.clearVolcanoKey;
  return payload;
}

async function saveLlmConfig(closeAfter = true) {
  try {
    setStatus("保存模型配置...", "busy");
    const submittedKeys = {
      volcano: $("#volcanoKey").value.trim(),
      gemini: $("#geminiKey").value.trim(),
    };
    app.state.llmConfig = await api("/api/llm/config", { method: "POST", body: collectLlmConfig() });
    if (app.clearVolcanoKey) app.savedKeyDrafts.volcano = "";
    else if (submittedKeys.volcano) app.savedKeyDrafts.volcano = submittedKeys.volcano;
    if (app.clearGeminiKey) app.savedKeyDrafts.gemini = "";
    else if (submittedKeys.gemini) app.savedKeyDrafts.gemini = submittedKeys.gemini;
    populateLlmConfig();
    renderLlmState();
    if (closeAfter) closeDialog(els.llmConfigDialog);
    setStatus("模型配置已保存");
    return true;
  } catch (error) {
    setStatus(error.message, "error");
    return false;
  }
}

async function testLlmProvider(provider) {
  const button = provider === "gemini" ? $("#testGeminiButton") : $("#testVolcanoButton");
  const label = provider === "gemini" ? "Gemini" : "火山";
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "TESTING...";
  setLlmTestStatus(label + " 正在测试连接，失败时会自动重试...", "busy");
  setStatus(label + " 连接测试中...", "busy");
  try {
    const result = await api("/api/llm/test", {
      method: "POST",
      body: { provider, config: collectLlmTestConfig(provider) },
    });
    const preview = String(result.preview || "OK").replace(/\s+/g, " ").slice(0, 120);
    setLlmTestStatus(label + " 连接成功 · 返回: " + preview, "success");
    setStatus(label + " 连接成功");
  } catch (error) {
    setLlmTestStatus(label + " 连接失败 · " + error.message, "error");
    setStatus(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function currentCharacter() {
  return app.state.characters.find((entry) => entry.key === app.activeCharacterKey) || null;
}

function populateCharacterEditor() {
  const entry = currentCharacter();
  els.characterTag.value = entry?.tag || "";
  els.characterAposePath.value = entry?.aposePath || "";
  els.characterDescription.value = entry?.description || "";
  els.characterKeyReadout.textContent = entry?.aposeName || fileNameFromPath(els.characterAposePath.value) || "--";
  els.characterPreview.hidden = !entry;
  els.characterPreviewEmpty.hidden = Boolean(entry);
  if (entry) {
    els.characterPreview.src = entry.src + "?v=" + encodeURIComponent(entry.description?.length || 0);
    els.characterPreview.alt = entry.tag || entry.aposeName;
  } else {
    els.characterPreview.removeAttribute("src");
  }
  $("#deleteCharacterButton").disabled = !entry;
  $("#generateCharacterButton").disabled = !entry;
}

function renderCharacterManager() {
  if (app.activeCharacterKey && app.activeCharacterKey !== "__new__" && !app.state.characters.some((entry) => entry.key === app.activeCharacterKey)) {
    app.activeCharacterKey = null;
  }
  if (app.activeCharacterKey === null && app.state.characters.length) {
    app.activeCharacterKey = app.state.characters[0].key;
  }
  els.characterList.innerHTML = "";
  app.state.characters.forEach((entry) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "character-item";
    button.classList.toggle("active", entry.key === app.activeCharacterKey);
    button.classList.toggle("has-description", Boolean(entry.description));
    const text = document.createElement("span");
    const tag = document.createElement("strong");
    tag.textContent = entry.tag || "UNTAGGED";
    const name = document.createElement("small");
    name.textContent = entry.aposeName || entry.key;
    const state = document.createElement("span");
    state.className = "description-state";
    state.title = entry.description ? "已有外观描述" : "等待生成外观描述";
    text.append(tag, name);
    button.append(text, state);
    button.addEventListener("click", () => {
      app.activeCharacterKey = entry.key;
      renderCharacterManager();
    });
    els.characterList.appendChild(button);
  });
  els.emptyCharacters.hidden = app.state.characters.length > 0;
  populateCharacterEditor();
}

function newCharacter() {
  app.activeCharacterKey = "__new__";
  renderCharacterManager();
  els.characterTag.focus();
}

function openCharacterManager() {
  renderCharacterManager();
  showDialog(els.characterDialog);
}

async function chooseAposeImage() {
  try {
    setStatus("选择角色 A-pose 图片...", "busy");
    const payload = await api("/api/library/choose-image", { method: "POST", body: {} });
    if (!payload.path) {
      setStatus("已取消图片选择");
      return;
    }
    els.characterAposePath.value = payload.path;
    els.characterKeyReadout.textContent = fileNameFromPath(payload.path) || "--";
    setStatus("已选择 A-pose 图片");
  } catch (error) {
    setStatus(error.message, "error");
  }
}

async function saveCharacter() {
  const path = els.characterAposePath.value.trim();
  try {
    setStatus("保存角色描述...", "busy");
    const payload = await api("/api/characters", {
      method: "POST",
      body: {
        oldKey: app.activeCharacterKey || "",
        tag: els.characterTag.value,
        aposePath: path,
        description: els.characterDescription.value,
      },
    });
    app.activeCharacterKey = fileNameFromPath(path);
    hydrate(payload);
    renderCharacterManager();
    setStatus("角色描述已保存");
    return true;
  } catch (error) {
    setStatus(error.message, "error");
    return false;
  }
}

async function deleteCharacter() {
  const entry = currentCharacter();
  if (!entry || !window.confirm("删除角色描述 " + entry.tag + "？")) return;
  try {
    setStatus("删除角色描述...", "busy");
    const payload = await api("/api/characters/delete", { method: "POST", body: { key: entry.key } });
    app.activeCharacterKey = null;
    hydrate(payload);
    renderCharacterManager();
    setStatus("角色描述已删除");
  } catch (error) {
    setStatus(error.message, "error");
  }
}

async function generateCharacterDescription() {
  const entry = currentCharacter();
  if (!entry) {
    setStatus("请先保存角色与 A-pose 图片", "error");
    return;
  }
  const button = $("#generateCharacterButton");
  button.disabled = true;
  button.textContent = "GENERATING...";
  try {
    setStatus("Gemini 正在生成角色外观 tags...", "busy");
    const payload = await api("/api/characters/generate", { method: "POST", body: { key: entry.key } });
    hydrate(payload);
    app.activeCharacterKey = entry.key;
    renderCharacterManager();
    setStatus("角色外观 tags 已生成");
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = "Gemini 生成";
  }
}

function showLlmReport(job) {
  const summary = $("#llmResultSummary");
  const messages = $("#llmResultMessages");
  summary.innerHTML = "";
  messages.innerHTML = "";
  [
    ["PROCESSED", job.done || 0],
    ["CACHE HIT", job.cached || 0],
    ["FAILED", job.failed || 0],
    ["TOTAL TIME", formatLlmDuration(job.elapsedSeconds || 0)],
  ].forEach(([label, value]) => {
    const metric = document.createElement("div");
    metric.className = "result-metric";
    const name = document.createElement("span");
    name.textContent = label;
    const count = document.createElement("b");
    count.textContent = String(value).padStart(2, "0");
    metric.append(name, count);
    summary.appendChild(metric);
  });
  const entries = [
    ...(job.errors || []).map((message) => ({ message, error: true })),
    ...(job.warnings || []).map((message) => ({ message, error: false })),
  ];
  entries.forEach((entry) => {
    const line = document.createElement("div");
    line.className = "result-message";
    line.classList.toggle("error", entry.error);
    line.textContent = entry.message;
    messages.appendChild(line);
  });
  $("#llmResultTitle").textContent = job.failed ? "清洗完成 / 存在失败" : "清洗完成 / 注意事项";
  showDialog(els.llmResultDialog);
}

async function pollLlmJob() {
  if (!app.llmJobId || app.llmPollBusy) return;
  app.llmPollBusy = true;
  try {
    const job = await api("/api/llm/jobs/" + app.llmJobId);
    const completionChanged = app.llmLastDone < 0 ? Number(job.done || 0) > 0 : Number(job.done || 0) > app.llmLastDone;
    app.llmLastDone = Number(job.done || 0);
    renderLlmJob(job);
    renderLlmState();
    if (completionChanged) {
      try {
        const imageScrollTop = els.imageList.scrollTop;
        hydrate(await api("/api/state"));
        els.imageList.scrollTop = imageScrollTop;
      } catch (syncError) {
        setStatus("LLM 单图状态同步失败: " + syncError.message, "error");
      }
    }
    setStatus("LLM 清洗 " + job.done + " / " + job.total + (job.current ? " · " + job.current : ""), "busy");
    if (!["queued", "running"].includes(job.status)) {
      if (app.llmPollTimer) clearInterval(app.llmPollTimer);
      app.llmPollTimer = null;
      app.llmJobId = null;
      if (!completionChanged) hydrate(await api("/api/state"));
      if (job.failed || job.warnings?.length) showLlmReport(job);
      setStatus(job.failed ? "LLM 清洗完成，部分图片失败" : "LLM 清洗完成", job.failed ? "error" : "ready");
      renderLlmJob(null);
      renderLlmState();
    }
  } catch (error) {
    if (app.llmPollTimer) clearInterval(app.llmPollTimer);
    app.llmPollTimer = null;
    app.llmJobId = null;
    renderLlmJob(null);
    renderLlmState();
    setStatus("LLM 任务状态读取失败: " + error.message, "error");
  } finally {
    app.llmPollBusy = false;
  }
}

function startLlmPolling(job) {
  app.llmJobId = job.id;
  app.llmLastDone = -1;
  app.llmPollBusy = false;
  renderLlmJob(job);
  renderLlmState();
  if (app.llmPollTimer) clearInterval(app.llmPollTimer);
  app.llmPollTimer = window.setInterval(pollLlmJob, 1200);
  pollLlmJob();
}

async function runLlm(scope = "selected") {
  const ids = scope === "all" ? app.state.images.map((image) => image.id) : targetImageIds();
  if (!ids.length) {
    setStatus(scope === "all" ? "当前没有可处理图片" : "请先选择图片", "error");
    return;
  }
  const config = app.state.llmConfig || {};
  const requiredProviders = selectedCallProviders();
  const missingProviders = [...requiredProviders].filter((provider) => provider === "gemini" ? !config.geminiKeySet : !config.volcanoKeySet);
  if (missingProviders.length) {
    setStatus("请先配置所选供应商的 API Key", "error");
    openLlmConfig();
    return;
  }
  const stageModels = [
    { name: "标签识别", provider: config.tagProvider || "volcano", model: config.tagModel || config.volcanoModel, vision: true },
    { name: "A-pose 识别", provider: config.aposeProvider || "volcano", model: config.aposeModel || config.volcanoModel, vision: true },
    { name: "数据清洗", provider: config.cleanProvider || "volcano", model: config.cleanModel || config.geminiModel, vision: false },
  ];
  const missingModel = stageModels.find((stage) => !stage.model);
  if (missingModel) {
    setStatus(`${missingModel.name} 尚未选择模型`, "error");
    openLlmConfig();
    setConfigTab("calls");
    return;
  }
  const badVision = stageModels.find((stage) => stage.vision && modelCatalogFor(stage.provider).find((item) => item.id === stage.model)?.vision === false);
  if (badVision) {
    setStatus(`${badVision.name} 所选模型不支持图像输入`, "error");
    openLlmConfig();
    setConfigTab("calls");
    return;
  }
  try {
    setStatus("创建 LLM 清洗任务...", "busy");
    const job = await api("/api/llm/clean", {
      method: "POST",
      body: {
        imageIds: ids,
        options: {
          styleMode: els.styleMode.checked,
          allowNaturalLanguage: els.allowNaturalLanguage.checked,
          autoSaveResults: els.autoSaveLlmResults.checked,
        },
      },
    });
    startLlmPolling(job);
  } catch (error) {
    if (error.status === 400) {
      try {
        const state = await api("/api/state");
        const activeJob = Array.isArray(state.llmJobs) ? state.llmJobs[0] : null;
        hydrate(state);
        if (activeJob) {
          startLlmPolling(activeJob);
          setStatus("已接入正在运行的 LLM 清洗任务", "busy");
          return;
        }
        setStatus("页面状态已同步，请重新执行", "error");
        return;
      } catch {}
    }
    setStatus(error.message, "error");
  }
}

function bindEvents() {
  $("#choosePathButton").addEventListener("click", chooseFolderPath);
  $("#scanButton").addEventListener("click", loadFolder);
  els.folderPath.addEventListener("keydown", (event) => { if (event.key === "Enter") loadFolder(); });
  els.revertButton.addEventListener("pointerdown", startRevertHold);
  els.revertButton.addEventListener("pointerup", finishRevertHold);
  els.revertButton.addEventListener("pointercancel", () => cancelRevertHold("已取消清除改动"));
  els.revertButton.addEventListener("lostpointercapture", () => {
    if (app.revertHold && !app.revertHold.complete) cancelRevertHold("已取消清除改动");
  });
  els.revertButton.addEventListener("contextmenu", (event) => event.preventDefault());
  els.clearLlmCacheButton.addEventListener("pointerdown", startClearLlmCacheHold);
  els.clearLlmCacheButton.addEventListener("pointerup", finishClearLlmCacheHold);
  els.clearLlmCacheButton.addEventListener("pointercancel", () => cancelClearLlmCacheHold("已取消清除 LLM 缓存"));
  els.clearLlmCacheButton.addEventListener("lostpointercapture", () => {
    if (app.clearLlmCacheHold && !app.clearLlmCacheHold.complete) cancelClearLlmCacheHold("已取消清除 LLM 缓存");
  });
  els.clearLlmCacheButton.addEventListener("contextmenu", (event) => event.preventDefault());
  $("#saveButton").addEventListener("click", saveChanged);
  $("#saveButtonInline").addEventListener("click", saveChanged);
  $("#addFavoriteGroup").addEventListener("click", addFavoriteGroup);
  els.imageList.addEventListener("click", clickImageRow);
  [els.imageSearch, els.includeFilter, els.excludeFilter, els.includeMode, els.excludeMode].forEach((field) => field.addEventListener("input", renderImages));
  [els.includeMode, els.excludeMode].forEach((field) => field.addEventListener("change", renderImages));
  $("#resetFiltersButton").addEventListener("click", resetFilters);
  $("#selectVisibleButton").addEventListener("click", () => { app.selectedIds = new Set(app.filteredIds); renderImages(); setStatus(`已选择 ${app.selectedIds.size} 张图片`); });
  $("#clearSelectionButton").addEventListener("click", () => { app.selectedIds.clear(); renderImages(); setStatus("已清空选择"); });
  els.tagSearch.addEventListener("input", renderTags);
  els.toggleCurrentTags.addEventListener("click", () => { app.showCurrentTags = true; els.toggleCurrentTags.classList.add("active"); els.toggleAllTags.classList.remove("active"); renderTags(); });
  els.toggleAllTags.addEventListener("click", () => { app.showCurrentTags = false; els.toggleAllTags.classList.add("active"); els.toggleCurrentTags.classList.remove("active"); renderTags(); });
  els.favoriteTarget.addEventListener("change", renderFavorites);
  $("#addRuleButton").addEventListener("click", addRule);
  $("#applyRulesSelectedButton").addEventListener("click", () => applyRules("selected"));
  $("#applyRulesAllButton").addEventListener("click", () => applyRules("all"));
  $("#exportRulesButton").addEventListener("click", () => { window.location.href = "/api/rules/export"; });
  $("#importRulesButton").addEventListener("click", () => $("#rulesFileInput").click());
  $("#rulesFileInput").addEventListener("change", (event) => { if (event.target.files[0]) importJson(event.target.files[0], "rules"); event.target.value = ""; });
  $("#exportTranslationsButton").addEventListener("click", () => { window.location.href = "/api/translations/export"; });
  $("#importTranslationsButton").addEventListener("click", () => $("#translationsFileInput").click());
  $("#translationsFileInput").addEventListener("change", (event) => { if (event.target.files[0]) importJson(event.target.files[0], "translations"); event.target.value = ""; });
  $("#addTranslationButton").addEventListener("click", async () => {
    const tag = window.prompt("新增翻译键");
    if (!tag?.trim()) return;
    try { hydrate(await api("/api/translations", { method: "POST", body: { tag: tag.trim(), translation: "" } })); setStatus(`已新增 ${tag.trim()}`); } catch (error) { setStatus(error.message, "error"); }
  });
  $("#llmConfigButton").addEventListener("click", openLlmConfig);
  $("#characterManagerButton").addEventListener("click", openCharacterManager);
  $("#refreshVolcanoModels").addEventListener("click", () => refreshProviderModels("volcano"));
  $("#refreshGeminiModels").addEventListener("click", () => refreshProviderModels("gemini"));
  [els.tagProvider, els.tagModel, els.aposeProvider, els.aposeModel, els.cleanProvider, els.cleanModel].forEach((field) => field?.addEventListener("input", () => { renderModelCapabilities(); renderLlmState(); }));
  [els.tagProvider, els.aposeProvider, els.cleanProvider].forEach((field) => field?.addEventListener("change", () => { renderModelCapabilities(); renderLlmState(); }));
  $("#llmButton").addEventListener("click", () => runLlm("selected"));
  $("#llmAllButton").addEventListener("click", () => runLlm("all"));
  $("#saveLlmConfigButton").addEventListener("click", () => saveLlmConfig(true));
  $("#testVolcanoButton").addEventListener("click", () => testLlmProvider("volcano"));
  $("#testGeminiButton").addEventListener("click", () => testLlmProvider("gemini"));
  $("#clearVolcanoKey").addEventListener("click", () => toggleSecretClear("volcano"));
  $("#clearGeminiKey").addEventListener("click", () => toggleSecretClear("gemini"));
  $$(".dialog-tab", els.llmConfigDialog).forEach((tab) => tab.addEventListener("click", () => setConfigTab(tab.dataset.configTab)));
  $$(".dialog-close").forEach((button) => button.addEventListener("click", () => closeDialog(button.closest("dialog"))));
  $$(".dialog-cancel").forEach((button) => button.addEventListener("click", () => closeDialog(button.closest("dialog"))));
  $$("dialog").forEach((dialog) => dialog.addEventListener("close", () => {
    document.body.classList.toggle("dialog-open", Boolean(document.querySelector("dialog[open]")));
  }));
  $("#newCharacterButton").addEventListener("click", newCharacter);
  $("#chooseAposeButton").addEventListener("click", chooseAposeImage);
  $("#saveCharacterButton").addEventListener("click", saveCharacter);
  $("#deleteCharacterButton").addEventListener("click", deleteCharacter);
  $("#generateCharacterButton").addEventListener("click", generateCharacterDescription);
  [els.styleMode, els.allowNaturalLanguage, els.autoSaveLlmResults].forEach((field) => field.addEventListener("change", () => {
    localStorage.setItem("tagcleaner." + field.id, String(field.checked));
    renderPromptAdaptation();
    renderLlmState();
  }));
  $$(".mode-tab").forEach((tab) => tab.addEventListener("click", () => setMode(tab.dataset.mode)));
}

async function init() {
  relocateSourcePanel();
  bindEvents();
  els.styleMode.checked = localStorage.getItem("tagcleaner.styleMode") === "true";
  els.allowNaturalLanguage.checked = localStorage.getItem("tagcleaner.allowNaturalLanguage") === "true";
  const storedAutoSave = localStorage.getItem("tagcleaner.autoSaveLlmResults");
  els.autoSaveLlmResults.checked = storedAutoSave === null ? true : storedAutoSave === "true";
  renderPromptAdaptation();
  try {
    setStatus("同步本地状态...", "busy");
    hydrate(await api("/api/state"));
    const activeJob = app.state.llmJobs[0];
    if (activeJob) startLlmPolling(activeJob);
    else setStatus("准备就绪");
  } catch (error) {
    setStatus(`服务未连接: ${error.message}`, "error");
  }
}

init();
