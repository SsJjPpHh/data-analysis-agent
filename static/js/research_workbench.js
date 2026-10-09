(function () {
  function workbenchViewFromHash() {
    try {
      const hash = String((typeof location !== "undefined" && location.hash) || "").replace(/^#/, "").toLowerCase();
      if (hash === "results") return "results";
      if (hash === "assistant") return "assistant";
    } catch {
      // Node test harness has no window.location.
    }
    return "";
  }

  function readActiveWorkbenchView() {
    const fromHash = workbenchViewFromHash();
    if (fromHash) return fromHash;
    try {
      return localStorage.getItem("rsw.activeView") === "results" ? "results" : "assistant";
    } catch {
      return "assistant";
    }
  }

  const state = {
    sid: "",
    datasets: [],
    activeDataset: null,
    activeSummary: null,
    analysisResult: null,
    resultState: null,
    resultView: "overview",
    activeTurnId: "",
    turnFilter: "all",
    turnSearch: "",
    readiness: null,
    aiKeyInvalid: false,
    selectedTableIndex: 0,
    selectedFigureIndex: 0,
    selectedModelIndex: 0,
    resultScope: "latest",
    showAllCurrentOutputs: false,
    history: [],
    status: {
      kind: "idle",
      title: "工作台就绪",
      detail: "等待上传科研数据。",
      action: "",
    },
    lastAction: null,
    streaming: false,
    inspecting: false,
    canceling: false,
    chatStarting: false,
    continuationContexts: {},
    toolAuditSequence: 0,
    recoveringRStep: false,
    rRepairStopMessage: "",
    analysisTurnId: "",
    activeAnalysisPlan: null,
    activePreparationPlan: null,
    preparationResumeTemplateId: "",
    preparationResumePrompt: "",
    dependencyGate: null,
    dependencyResume: null,
    dependencyBusy: false,
    dependencyJobId: "",
    dependencyProgress: "",
    activeView: readActiveWorkbenchView(),
    templateCategory: "all",
    templatePreparations: {},
    templateInputValues: {},
    templatePrepareLoading: "",
    templateWorkflow: "all",
    templateSearch: "",
    templateSelectedId: "",
    templateMeta: {},
    activeTemplateContext: null,
    templatesLoaded: false,
    templatesLoading: false,
    templateLoadError: "",
    systemRuntime: null,
    rLibraryDirty: false,
    workspaceStorage: null,
    activeArtifactPreviewId: "",
    activeArtifactPreviewText: "",
    analysisTimeline: [],
    turnEvidence: {},
    pendingTemplateId: "",
    pendingChatImages: [],
    modelSharingPolicy: defaultModelSharingPolicy(),
    modelSharingSessionId: "",
    modelSharingLoading: false,
    modelSharingSaving: false,
    modelSharingDirty: false,
    modelSharingStatus: { message: "", kind: "" },
  };

  let TEMPLATE_WORKFLOWS = ["数据准备", "研究设计", "方法建模", "验证诊断", "论文交付", "高级专题"];
  const TEMPLATE_CACHE_MS = 10 * 60 * 1000;
  const CURRENT_OUTPUT_LIMITS = {
    table: 6,
    figure: Infinity,
    text: Infinity,
    execution: Infinity,
  };
  const CONTINUATION_RESULT_MESSAGE = "本次结果已整理到结果页，可继续下一项分析或基于当前结果追问。";
  let templateLoadPromise = null;
  let templateLoadSessionId = "";
  let templateLoadedAt = 0;
  const templatePreparePromises = new Map();

  let TEMPLATE_LIBRARY = [
    {
      id: "data_quality",
      category: "基础统计",
      title: "数据质量与变量字典",
      subtitle: "导入核查、变量类型、缺失、重复、异常值",
      outputs: "变量字典、缺失概览、异常值清单、下一步建议",
      packages: "tidyverse, data.table, janitor, skimr, DataExplorer",
      prompt: "请对当前数据进行完整数据质量体检。识别变量类型、标签、重复记录、极端值、缺失模式、单位或编码异常，并给出适合本数据的下一步统计路线。",
      workflow: "数据准备",
    },
  ];

  const $ = (id) => document.getElementById(id);
  const qs = (selector, root = document) => root.querySelector(selector);
  const qsa = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const PHASE_LABELS = {
    preflight: "检查数据",
    prepare: "选包",
    analyze: "跑 R",
    diagnose: "诊断",
    publish: "解释",
  };
  function toolTimelineId(tool) {
    if (tool === "inspect_dataset" || tool === "set_analysis_plan") return "preflight";
    if (tool === "get_r_package_inventory" || tool === "get_r_package_help") return "prepare";
    if (tool === "run_r_code") return "analyze";
    return "";
  }
  let modalReturnFocus = null;
  let desktopImportBridgePromise = null;
  let modelSharingLoadSequence = 0;

  function defaultModelSharingPolicy() {
    return {
      version: 1,
      result_mode: "summary_only",
      sensitive_columns: [],
      send_result_images: false,
      send_attached_images: true,
    };
  }

  function validatedModelSharingPolicy(policy) {
    if (policy?.version !== 1
        || !["summary_only", "deidentified"].includes(policy.result_mode)
        || !Array.isArray(policy.sensitive_columns)
        || policy.sensitive_columns.some((name) => typeof name !== "string")
        || typeof policy.send_result_images !== "boolean"
        || typeof policy.send_attached_images !== "boolean") {
      throw new Error("服务端返回的模型外发设置无效，请刷新设置后重试。");
    }
    return {
      version: 1,
      result_mode: policy.result_mode,
      sensitive_columns: [...new Set(policy.sensitive_columns)],
      send_result_images: policy.send_result_images,
      send_attached_images: policy.send_attached_images,
    };
  }

  function resetModelSharingForSession() {
    modelSharingLoadSequence += 1;
    state.modelSharingPolicy = defaultModelSharingPolicy();
    state.modelSharingSessionId = "";
    state.modelSharingLoading = false;
    state.modelSharingSaving = false;
    state.modelSharingDirty = false;
    state.modelSharingStatus = { message: "", kind: "" };
    renderModelSharingControls();
  }

  function modelSharingAnalysisBusy() {
    return Boolean(state.streaming || state.inspecting || state.chatStarting || state.canceling);
  }

  function setModelSharingStatus(message, kind = "") {
    state.modelSharingStatus = { message, kind };
    const element = $("rsw-model-sharing-status");
    if (element) {
      element.textContent = message;
      element.className = `rsw-ai-key-status ${kind ? `is-${kind}` : ""}`.trim();
    }
  }

  function modelSharingSensitiveColumns() {
    return [...new Set(String($("rsw-model-sharing-sensitive-columns")?.value || "")
      .split(/\r?\n/).filter((name) => name.trim() !== ""))];
  }

  function renderModelSharingControls() {
    const mode = $("rsw-model-sharing-result-mode");
    const columns = $("rsw-model-sharing-sensitive-columns");
    const resultImages = $("rsw-model-sharing-send-result-images");
    const attachedImages = $("rsw-model-sharing-send-attached-images");
    const save = $("rsw-model-sharing-save");
    const policy = state.modelSharingPolicy;
    const loaded = Boolean(state.sid && state.modelSharingSessionId === state.sid);
    const busy = modelSharingAnalysisBusy();
    const disabled = busy || state.modelSharingLoading || state.modelSharingSaving || !loaded;
    if (!state.modelSharingDirty) {
      if (mode) mode.value = policy.result_mode;
      if (columns) columns.value = policy.sensitive_columns.join("\n");
      if (resultImages) resultImages.checked = policy.send_result_images;
      if (attachedImages) attachedImages.checked = policy.send_attached_images;
    }
    [mode, columns, resultImages, attachedImages, save].forEach((element) => {
      if (element) element.disabled = disabled;
    });
    if (save) save.textContent = state.modelSharingSaving ? "正在保存…" : "保存当前会话外发设置";
    const session = $("rsw-model-sharing-session");
    if (session) session.textContent = loaded
      ? `仅作用于当前会话（${state.sid}）。已保存方式：${policy.result_mode === "deidentified" ? "去标识个体结果" : "仅汇总结果"}。`
      : "尚未读取当前会话外发设置；读取成功后可修改。";
    const description = $("rsw-model-sharing-description");
    if (description) {
      description.textContent = (mode?.value || policy.result_mode) === "deidentified"
        ? "保留个体数值，隐藏身份列及指定敏感列；这不是完全匿名，也不保证图中文字脱敏。"
        : "向模型提供经过筛选的汇总结果，个体结果保留在本机。图片由下面的独立开关控制。";
      if (state.modelSharingDirty) description.textContent += " 当前修改尚未保存。";
    }
    const lock = $("rsw-model-sharing-lock");
    if (lock) lock.textContent = busy
      ? "分析运行中不能修改外发设置；本轮使用开始时已保存的策略。"
      : "保存后用于后续模型请求；不会撤回之前已经发送的内容。";
    const options = $("rsw-model-sharing-column-options");
    if (options) {
      const variables = state.activeSummary?.variables || summaryOf(state.activeDataset).variables || [];
      const names = [...new Set(variables.map((variable) => variable.name || variable.column || "").filter(Boolean))];
      options.innerHTML = names.length
        ? names.map((name) => `<button class="rsw-secondary-btn" type="button" data-model-sharing-column="${escapeHtml(name)}" ${disabled ? "disabled" : ""}>${escapeHtml(name)}</button>`).join("")
        : "检查数据后可在这里选择字段；也可在上方输入精确列名。";
    }
    setModelSharingStatus(state.modelSharingStatus.message, state.modelSharingStatus.kind);
  }

  function modelSharingDraftChanged() {
    state.modelSharingDirty = true;
    setModelSharingStatus("设置尚未保存；模型仍使用上次已保存的策略。");
    renderModelSharingControls();
  }

  async function loadModelSharingPolicy({ preserveDraft = false } = {}) {
    const requestSessionId = state.sid;
    if (!requestSessionId) throw new Error("当前会话尚未建立，无法读取外发设置。");
    if (state.modelSharingSaving) throw new Error("外发设置正在保存，请稍后刷新。");
    const sequence = ++modelSharingLoadSequence;
    const retainDraft = preserveDraft && state.modelSharingDirty;
    state.modelSharingLoading = true;
    setModelSharingStatus("正在读取当前会话外发设置…", "busy");
    renderModelSharingControls();
    try {
      const payload = await requestJson(`/api/session/${encodeURIComponent(requestSessionId)}/model-sharing`);
      const policy = validatedModelSharingPolicy(payload.policy);
      if (state.sid !== requestSessionId || sequence !== modelSharingLoadSequence) return null;
      state.modelSharingPolicy = policy;
      state.modelSharingSessionId = requestSessionId;
      state.modelSharingDirty = retainDraft;
      setModelSharingStatus(retainDraft
        ? "当前会话已保存的策略已刷新；界面中的修改尚未保存。"
        : "已读取当前会话外发设置。");
      return policy;
    } catch (error) {
      if (state.sid === requestSessionId && sequence === modelSharingLoadSequence) {
        state.modelSharingSessionId = "";
        setModelSharingStatus(`外发设置读取失败：${error.message}。请重新打开设置刷新；设置未确认前不会发送附件。`, "error");
      }
      throw error;
    } finally {
      if (state.sid === requestSessionId && sequence === modelSharingLoadSequence) {
        state.modelSharingLoading = false;
        renderModelSharingControls();
      }
    }
  }

  async function saveModelSharingPolicy() {
    if (modelSharingAnalysisBusy()) {
      setModelSharingStatus("分析运行中不能修改外发设置，请在分析结束后保存。", "error");
      renderModelSharingControls();
      return false;
    }
    if (state.modelSharingLoading || state.modelSharingSaving || state.modelSharingSessionId !== state.sid || !state.sid) {
      setModelSharingStatus("请先成功读取当前会话外发设置，再保存修改。", "error");
      return false;
    }
    const requestSessionId = state.sid;
    const policy = validatedModelSharingPolicy({
      version: 1,
      result_mode: $("rsw-model-sharing-result-mode")?.value,
      sensitive_columns: modelSharingSensitiveColumns(),
      send_result_images: Boolean($("rsw-model-sharing-send-result-images")?.checked),
      send_attached_images: Boolean($("rsw-model-sharing-send-attached-images")?.checked),
    });
    state.modelSharingDirty = true;
    state.modelSharingSaving = true;
    setModelSharingStatus("正在保存当前会话外发设置…", "busy");
    renderModelSharingControls();
    try {
      const payload = await requestJson(`/api/session/${encodeURIComponent(requestSessionId)}/model-sharing`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(policy),
      });
      const savedPolicy = validatedModelSharingPolicy(payload.policy);
      if (state.sid !== requestSessionId) return false;
      state.modelSharingPolicy = savedPolicy;
      state.modelSharingSessionId = requestSessionId;
      state.modelSharingDirty = false;
      setModelSharingStatus(!savedPolicy.send_attached_images && state.pendingChatImages.length
        ? "设置已保存。附件发送已关闭；请移除待发送图片后再提问，或重新开启并保存。"
        : "当前会话外发设置已保存，后续模型请求使用此策略。", "ok");
      return true;
    } catch (error) {
      if (state.sid === requestSessionId) {
        setModelSharingStatus(`保存失败：${error.message}。修改尚未生效，已保存的策略保持不变。`, "error");
      }
      throw error;
    } finally {
      if (state.sid === requestSessionId) {
        state.modelSharingSaving = false;
        renderModelSharingControls();
      }
    }
  }

  function assertAttachedImagesAllowed() {
    if (!state.sid || state.modelSharingSessionId !== state.sid || state.modelSharingLoading) {
      const error = new Error("尚未确认当前会话的附件外发设置，请在系统设置中刷新后重试。");
      error.code = "model_sharing_unavailable";
      throw error;
    }
    if (!state.modelSharingPolicy.send_attached_images) {
      const error = new Error("当前会话已关闭用户附件发送。请移除待发送图片，或在系统设置中开启并保存后再发送。");
      error.code = "model_attached_images_disabled";
      throw error;
    }
  }

  function getDesktopImportBridge() {
    if (desktopImportBridgePromise) return desktopImportBridgePromise;
    if (!globalThis.qt?.webChannelTransport || typeof globalThis.QWebChannel !== "function") {
      return Promise.resolve(null);
    }
    desktopImportBridgePromise = new Promise((resolve) => {
      let settled = false;
      const finish = (bridge) => {
        if (settled) return;
        settled = true;
        globalThis.clearTimeout(timeoutId);
        resolve(bridge || null);
      };
      // A partially bundled QtWebChannel can expose both globals but never
      // invoke its initialization callback. File clicks must then fall back to
      // the same browser File upload path used by drag-and-drop, not wait
      // forever. This timeout covers channel setup only; it never limits how
      // long a user may keep the native file dialog open.
      const timeoutId = globalThis.setTimeout(() => finish(null), 1500);
      try {
        new globalThis.QWebChannel(globalThis.qt.webChannelTransport, (channel) => {
          finish(channel?.objects?.desktopImport || null);
        });
      } catch {
        finish(null);
      }
    });
    return desktopImportBridgePromise;
  }

  async function requestDesktopImportSelection(kind) {
    const bridge = await getDesktopImportBridge();
    if (!bridge) return null;
    const method = kind === "directory" ? "selectDirectory" : "selectFiles";
    if (typeof bridge[method] !== "function") return null;
    const raw = await new Promise((resolve, reject) => {
      try {
        bridge[method]((value) => resolve(value));
      } catch (error) {
        reject(error);
      }
    });
    if (!raw) return { canceled: true };
    const selection = JSON.parse(raw);
    if (selection?.error) throw new Error(selection.error);
    return selection;
  }

  function rememberModalFocus() {
    modalReturnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  }

  function restoreModalFocus() {
    const anotherOpen = ["rsw-artifact-modal", "rsw-ai-modal", "rsw-template-modal", "rsw-dependency-modal", "rsw-replace-modal"]
      .some((id) => $(id)?.hidden === false);
    if (!anotherOpen) {
      modalReturnFocus?.focus();
      modalReturnFocus = null;
    }
  }

  function trapModalFocus(event) {
    if (event.key !== "Tab") return;
    const modal = ["rsw-artifact-modal", "rsw-ai-modal", "rsw-template-modal", "rsw-dependency-modal", "rsw-replace-modal"]
      .map((id) => $(id))
      .find((item) => item?.hidden === false);
    if (!modal) return;
    const focusable = qsa('button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])', modal)
      .filter((item) => !item.hidden && item.getClientRects().length > 0);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function toast(message, kind = "") {
    const el = $("rsw-toast");
    if (!el) return;
    el.textContent = message;
    el.className = `rsw-toast ${kind ? `is-${kind}` : ""}`.trim();
    el.hidden = false;
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => {
      el.hidden = true;
    }, 3200);
  }

  function setRunState(title, detail, mode = "") {
    const el = $("rsw-run-state");
    if (!el) return;
    el.classList.toggle("is-busy", mode === "busy");
    el.classList.toggle("is-error", mode === "error");
    el.classList.toggle("is-ok", mode === "ok");
    el.dataset.mode = mode || "idle";
    const strong = qs("strong", el);
    const small = qs("small", el);
    if (strong) strong.textContent = title;
    if (small) small.textContent = detail;
  }

  function formatAnalysisElapsed(startedAt) {
    const seconds = Math.max(1, Math.floor((Date.now() - startedAt) / 1000));
    if (seconds < 60) return `已用时 ${seconds} 秒`;
    return `已用时 ${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
  }

  function csrfToken() {
    return sessionStorage.getItem("rsw.csrfToken") || "";
  }

  async function apiFetch(url, options = {}) {
    const method = String(options.method || "GET").toUpperCase();
    const headers = new Headers(options.headers || {});
    if (!headers.has("Accept")) headers.set("Accept", "application/json");
    if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
      const token = csrfToken();
      if (token) headers.set("X-CSRF-Token", token);
    }
    return fetch(url, { ...options, headers });
  }

  async function requestJson(url, options = {}) {
    const response = await apiFetch(url, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.error) {
      const error = new Error(payload.error || payload.errors?.join("; ") || `请求失败：HTTP ${response.status}`);
      error.code = payload.code || "";
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  function parseToolPayload(content) {
    const text = String(content || "").trim();
    const start = text.indexOf("{");
    if (start < 0) return null;
    try {
      return JSON.parse(text.slice(start));
    } catch {
      return null;
    }
  }

  function artifactUrl(artifact, format = "", download = false) {
    const params = new URLSearchParams();
    params.set("path", artifact?.path || "");
    if (format) params.set("format", format);
    if (download) params.set("download", "1");
    return `/api/session/${encodeURIComponent(state.sid)}/research/artifact?${params.toString()}`;
  }

  function artifactDownloadUrl(artifact, format = "") {
    return artifactUrl(artifact, format, true);
  }

  async function fetchArtifactJson(artifact) {
    if (!artifact?.path) return null;
    return requestJson(artifactUrl(artifact, "json"));
  }

  async function loadAnalysisTemplates({ force = false } = {}) {
    if (!state.sid) return false;
    const requestSessionId = state.sid;
    const cacheFresh = state.templatesLoaded && Date.now() - templateLoadedAt < TEMPLATE_CACHE_MS;
    if (!force && cacheFresh) return true;
    if (!force && templateLoadPromise && templateLoadSessionId === requestSessionId) {
      return templateLoadPromise;
    }
    state.templatesLoading = true;
    state.templateLoadError = "";
    const promise = requestJson(`/api/session/${encodeURIComponent(requestSessionId)}/research/templates`)
      .then((payload) => {
        if (state.sid !== requestSessionId) return false;
        if (Array.isArray(payload.templates) && payload.templates.length) {
          TEMPLATE_LIBRARY = payload.templates.filter((item) => item?.visibility !== "internal");
        }
        if (Array.isArray(payload.workflows) && payload.workflows.length) {
          TEMPLATE_WORKFLOWS = payload.workflows;
        }
        state.templateMeta = {
          inventory: payload.inventory || {},
          capabilityGroups: payload.capability_groups || 0,
          aliases: payload.aliases || {},
          schemaVersion: payload.schema_version || "",
        };
        state.templatesLoaded = true;
        templateLoadedAt = Date.now();
        return true;
      })
      .catch((error) => {
        if (state.sid === requestSessionId) {
          state.templateLoadError = error.message || "模板库加载失败";
        }
        throw error;
      })
      .finally(() => {
        if (templateLoadPromise === promise) {
          templateLoadPromise = null;
          templateLoadSessionId = "";
          state.templatesLoading = false;
        }
      });
    templateLoadPromise = promise;
    templateLoadSessionId = requestSessionId;
    return promise;
  }

  function resultArtifacts(kind) {
    return state.analysisResult?.payload?.result?.[kind] || [];
  }

  function resultCount(kind) {
    return resultArtifacts(kind).length;
  }

  function resultArtifactTotal(result = null) {
    const payload = result || state.analysisResult?.payload?.result || {};
    return (payload.tables?.length || 0)
      + (payload.figures?.length || 0)
      + (payload.texts?.length || 0)
      + (payload.models?.length || 0);
  }

  function artifactKey(item) {
    if (!item) return "";
    return String(item.path || `${item.name || ""}|${item.kind || ""}|${item.mime_type || ""}`);
  }

  function mergeArtifactList(existing = [], incoming = []) {
    const merged = [];
    const seen = new Set();
    [...(existing || []), ...(incoming || [])].forEach((item) => {
      if (!item) return;
      const key = artifactKey(item);
      if (!key) return;
      if (seen.has(key)) {
        const index = merged.findIndex((entry) => artifactKey(entry) === key);
        if (index >= 0 && !merged[index].analysis_turn_id && item.analysis_turn_id) {
          merged[index] = { ...merged[index], ...item };
        }
        return;
      }
      seen.add(key);
      merged.push(item);
    });
    return merged;
  }

  function mergeDerivedTableList(existing = [], incoming = []) {
    const merged = [];
    const seen = new Set();
    [...(existing || []), ...(incoming || [])].forEach((table) => {
      if (!table) return;
      const key = JSON.stringify({
        name: table.name || "",
        columns: table.columns || [],
        rows: (table.rows || []).slice(0, 3),
        count: table.rows?.length || 0,
      });
      if (seen.has(key)) return;
      seen.add(key);
      merged.push(table);
    });
    return merged;
  }

  function uniqueMessages(items = []) {
    const seen = new Set();
    const output = [];
    (items || []).forEach((item) => {
      const text = String(item || "").trim();
      if (!text || seen.has(text)) return;
      seen.add(text);
      output.push(text);
    });
    return output;
  }

  function mergeArtifactPreviews(existing = {}, incoming = {}) {
    const merged = {};
    ["tables", "texts", "models"].forEach((kind) => {
      merged[kind] = mergeArtifactList(existing?.[kind] || [], incoming?.[kind] || []);
    });
    return merged;
  }

  function executionFromPayload(payload) {
    const result = payload?.result || {};
    const hasExecutionSignal = Boolean(
      payload?.job_id ||
      result.code ||
      result.stdout ||
      result.stderr ||
      result.error ||
      resultArtifactTotal(result) > 0
    );
    if (!hasExecutionSignal) return null;
    return {
      job_id: String(payload?.job_id || result.job_id || ""),
      ok: result.ok !== false,
      code: result.code || "",
      stdout: result.stdout || "",
      stderr: result.stderr || "",
      error: result.error || "",
      warnings: result.warnings || [],
      tables: result.tables || [],
      figures: result.figures || [],
      texts: result.texts || [],
      models: result.models || [],
      artifacts: result.artifacts || [],
      analysis_turn_id: result.analysis_turn_id || payload?.analysis_turn_id || "",
      turn_label: result.turn_label || payload?.turn_label || "",
    };
  }

  function executionKey(execution) {
    if (!execution) return "";
    if (execution.job_id) return `job:${execution.job_id}`;
    return JSON.stringify({
      code: execution.code || "",
      stdout: execution.stdout || "",
      stderr: execution.stderr || "",
      error: execution.error || "",
      tables: (execution.tables || []).map(artifactKey),
      figures: (execution.figures || []).map(artifactKey),
      texts: (execution.texts || []).map(artifactKey),
      models: (execution.models || []).map(artifactKey),
    });
  }

  function normalizeExecutionList(payload) {
    const result = payload?.result || {};
    const existing = Array.isArray(result.executions) ? result.executions : [];
    const inferred = executionFromPayload(payload);
    return mergeExecutionList(existing, inferred ? [inferred] : []);
  }

  function mergeExecutionList(existing = [], incoming = []) {
    const merged = [];
    const seen = new Set();
    [...(existing || []), ...(incoming || [])].forEach((execution) => {
      if (!execution) return;
      const key = executionKey(execution);
      if (!key || seen.has(key)) return;
      seen.add(key);
      merged.push({
        job_id: String(execution.job_id || ""),
        ok: execution.ok !== false,
        code: execution.code || "",
        stdout: execution.stdout || "",
        stderr: execution.stderr || "",
        error: execution.error || "",
        warnings: execution.warnings || [],
        tables: execution.tables || [],
        figures: execution.figures || [],
        texts: execution.texts || [],
        models: execution.models || [],
        artifacts: execution.artifacts || [],
        analysis_turn_id: execution.analysis_turn_id || "",
        turn_label: execution.turn_label || "",
      });
    });
    return merged;
  }

  function stampArtifactsWithTurn(items = [], turnId = "", turnLabel = "") {
    return (items || []).map((item) => item && typeof item === "object"
      ? {
        ...item,
        analysis_turn_id: item.analysis_turn_id || turnId,
        turn_label: item.turn_label || turnLabel,
      }
      : item);
  }

  function stampResearchPayloadWithTurn(payload, turnId = "") {
    if (!payload?.result || !turnId) return payload;
    const result = payload.result || {};
    const turnLabel = result.turn_label || payload.turn_label || "当前分析";
    return {
      ...payload,
      analysis_turn_id: payload.analysis_turn_id || turnId,
      turn_label: turnLabel,
      result: {
        ...result,
        analysis_turn_id: result.analysis_turn_id || turnId,
        turn_label: turnLabel,
        tables: stampArtifactsWithTurn(result.tables || [], turnId, turnLabel),
        figures: stampArtifactsWithTurn(result.figures || [], turnId, turnLabel),
        texts: stampArtifactsWithTurn(result.texts || [], turnId, turnLabel),
        models: stampArtifactsWithTurn(result.models || [], turnId, turnLabel),
        artifacts: stampArtifactsWithTurn(result.artifacts || [], turnId, turnLabel),
        executions: (result.executions || []).map((execution) => ({
          ...execution,
          analysis_turn_id: execution.analysis_turn_id || turnId,
          turn_label: execution.turn_label || turnLabel,
          tables: stampArtifactsWithTurn(execution.tables || [], turnId, turnLabel),
          figures: stampArtifactsWithTurn(execution.figures || [], turnId, turnLabel),
          texts: stampArtifactsWithTurn(execution.texts || [], turnId, turnLabel),
          models: stampArtifactsWithTurn(execution.models || [], turnId, turnLabel),
          artifacts: stampArtifactsWithTurn(execution.artifacts || [], turnId, turnLabel),
        })),
      },
    };
  }

  function payloadWithExecutions(payload) {
    if (!payload?.result) return payload;
    const result = payload.result || {};
    return {
      ...payload,
      result: {
        ...result,
        warnings: uniqueMessages([
          ...(result.warnings || []),
          result.ok === false ? result.error : "",
        ]),
        executions: normalizeExecutionList(payload),
      },
    };
  }

  function mergeResearchPayload(existingPayload, incomingPayload) {
    if (!existingPayload?.result) return payloadWithExecutions(incomingPayload);
    if (!incomingPayload?.result) return payloadWithExecutions(existingPayload);
    existingPayload = payloadWithExecutions(existingPayload);
    incomingPayload = payloadWithExecutions(incomingPayload);
    const existing = existingPayload.result || {};
    const incoming = incomingPayload.result || {};
    const warnings = uniqueMessages([
      ...(existing.warnings || []),
      ...(incoming.warnings || []),
      incoming.ok === false ? incoming.error : "",
    ]);
    const mergedResult = {
      ...existing,
      ...incoming,
      tables: mergeArtifactList(existing.tables || [], incoming.tables || []),
      figures: mergeArtifactList(existing.figures || [], incoming.figures || []),
      texts: mergeArtifactList(existing.texts || [], incoming.texts || []),
      models: mergeArtifactList(existing.models || [], incoming.models || []),
      artifacts: mergeArtifactList(existing.artifacts || [], incoming.artifacts || []),
      executions: mergeExecutionList(existing.executions || [], incoming.executions || []),
      warnings,
      code: incoming.code || existing.code || "",
      stdout: incoming.stdout || existing.stdout || "",
      stderr: incoming.stderr || existing.stderr || "",
    };
    const hasArtifacts = resultArtifactTotal(mergedResult) > 0;
    mergedResult.ok = hasArtifacts ? true : (existing.ok !== false && incoming.ok !== false);
    mergedResult.error = hasArtifacts ? "" : (incoming.error || existing.error || "");
    return {
      ...existingPayload,
      ...incomingPayload,
      result: mergedResult,
      artifact_previews: mergeArtifactPreviews(
        existingPayload.artifact_previews || {},
        incomingPayload.artifact_previews || {}
      ),
    };
  }

  function isLowSignalWarning(message) {
    const text = String(message || "");
    return (
      text.includes("Unknown or uninitialised column: `variable`") ||
      text.includes("The dot-dot notation (`..density..`) was deprecated") ||
      text.includes("Template R execution budget reached") ||
      text.includes("turn_budget_reached")
    );
  }

  function visibleWarnings(warnings) {
    const seen = new Set();
    return (warnings || []).filter((item) => {
      const text = String(item || "").trim();
      if (!text || isLowSignalWarning(text) || seen.has(text)) return false;
      seen.add(text);
      return true;
    });
  }

  function blockingResultFailure(result) {
    return Boolean(result?.ok === false && resultArtifactTotal(result) === 0);
  }

  function currentAnalysisArtifactTotal() {
    if (!state.streaming && !state.inspecting && !state.chatStarting && activeTurn()) {
      return turnArtifacts(activeTurn()).length;
    }
    return resultArtifactTotal(state.analysisResult?.payload?.result);
  }

  function isOperationalContinuationText(content) {
    const text = normalizeMarkdownText(content || "").replace(/\s+/g, " ").trim();
    if (!text || text.length > 260) return false;
    return [
      "本次分析结果已更新到结果页",
      "本次结果已整理到结果页",
      "本轮已经生成可用的 R 分析产物",
      "系统已停止后续追加步骤",
      "可以直接查看结果页",
    ].some((phrase) => text.includes(phrase));
  }

  function isRecoverableStopError(error) {
    const text = String(error?.message || error || "");
    return [
      "分析超时",
      "分析已停止",
      "已终止",
    ].some((phrase) => text.includes(phrase));
  }

  function hasStructuredResult(result = null) {
    const payload = result || state.analysisResult?.payload?.result || {};
    return Boolean(payload && (payload.ok === false || resultArtifactTotal(payload) > 0));
  }

  function resultViewState() {
    const result = state.analysisResult?.payload?.result;
    const failed = blockingResultFailure(result);
    if (state.streaming) return { label: "运行中", kind: "busy" };
    const outcome = resultTurnOutcome(activeTurn());
    if (outcome) {
      if (outcome.analysis_status === "failed") return { label: "需处理", kind: "error" };
      if (outcome.analysis_status === "incomplete") return { label: "待补齐", kind: "warning" };
      if (outcome.analysis_status === "complete") {
        return { label: "已生成", kind: outcome.diagnostic_quality === "passed" ? "ok" : "warning" };
      }
      return { label: "对话完成", kind: "ok" };
    }
    if (failed) return { label: "需处理", kind: "error" };
    if (hasStructuredResult() || hasInterpretation()) return { label: "已生成", kind: "ok" };
    if (state.activeSummary?.variables?.length) return { label: "待提问", kind: "" };
    if (state.activeDataset) return { label: "待理解", kind: "" };
    return { label: "待分析", kind: "" };
  }

  function syncWorkbenchHash() {
    try {
      if (typeof location === "undefined" || typeof history === "undefined" || typeof history.replaceState !== "function") {
        return;
      }
      const next = state.activeView === "results" ? "#results" : "#assistant";
      if (location.hash !== next) {
        history.replaceState(null, "", `${location.pathname || ""}${location.search || ""}${next}`);
      }
    } catch {
      // Node tests and non-browser shells have no document location.
    }
  }

  function renderWorkbenchView() {
    const activeView = state.activeView === "results" ? "results" : "assistant";
    qsa("[data-workbench-view]").forEach((button) => {
      const active = button.dataset.workbenchView === activeView;
      button.classList.toggle("is-active", active);
      if (active) {
        button.setAttribute("aria-current", "page");
      } else {
        button.removeAttribute("aria-current");
      }
    });
    qsa("[data-workbench-panel]").forEach((panel) => {
      const isTarget = panel.dataset.workbenchPanel === activeView;
      panel.hidden = !isTarget;
      panel.classList.toggle("is-active", isTarget);
    });
    const badge = $("rsw-result-view-state");
    if (badge) {
      const viewState = resultViewState();
      badge.textContent = viewState.label;
      badge.className = viewState.kind ? `is-${viewState.kind}` : "";
    }
    const banner = $("rsw-results-ready");
    if (banner) banner.hidden = activeView === "results" || currentAnalysisArtifactTotal() === 0;
  }

  function notifyResultsReady() {
    renderWorkbenchView();
    const banner = $("rsw-results-ready");
    if (banner) banner.hidden = state.activeView === "results" || currentAnalysisArtifactTotal() === 0;
  }

  function resetAnalysisTimeline() {
    state.analysisTimeline = Object.entries(PHASE_LABELS).map(([id, label]) => ({
      id,
      label,
      status: "waiting",
    }));
    renderPhaseTimeline();
  }

  function recordTimelineStep(id, label, status) {
    if (!id) return;
    const existing = state.analysisTimeline.find((item) => item.id === id);
    if (existing) {
      existing.label = label || existing.label;
      existing.status = status || existing.status;
    } else {
      state.analysisTimeline.push({ id, label: label || id, status: status || "active" });
    }
    renderPhaseTimeline();
  }

  function applyPhaseStatus(phaseStatus) {
    if (!phaseStatus || typeof phaseStatus !== "object") return;
    Object.entries(PHASE_LABELS).forEach(([key, label]) => {
      const value = String(phaseStatus[key] || "");
      if (!value || value === "not_started") return;
      const ui = value === "completed" || value === "succeeded"
        ? "done"
        : value === "failed" || value === "error"
          ? "error"
          : "active";
      recordTimelineStep(key, label, ui);
    });
    const active = Object.entries(PHASE_LABELS).find(([key]) => {
      const value = String(phaseStatus[key] || "");
      return value && value !== "not_started" && value !== "completed" && value !== "succeeded";
    });
    const done = [...Object.entries(PHASE_LABELS)].reverse().find(([key]) => {
      const value = String(phaseStatus[key] || "");
      return value === "completed" || value === "succeeded";
    });
    const top = $("rsw-top-stage");
    if (top) top.textContent = active ? active[1] : (done ? done[1] : top.textContent);
  }

  function timelineStatusLabel(status) {
    if (status === "done") return "完成";
    if (status === "error") return "需处理";
    if (status === "active") return "进行中";
    return "待开始";
  }

  function renderPhaseTimeline() {
    const el = $("rsw-phase-timeline");
    if (!el) return;
    const steps = state.analysisTimeline || [];
    el.hidden = !steps.length || (
      !state.streaming && !state.inspecting && steps.some((item) => item.status === "active")
    );
    if (!steps.length) {
      el.innerHTML = "";
      return;
    }
    el.innerHTML = steps.map((item) => `
      <li class="is-${escapeHtml(item.status || "waiting")}">
        <strong>${escapeHtml(item.label || item.id)}</strong>
        <small>${escapeHtml(timelineStatusLabel(item.status))}</small>
      </li>`).join("");
  }

  function recordTurnEvidence(payload) {
    const turnId = state.analysisTurnId;
    if (!turnId || !payload) return;
    const list = state.turnEvidence[turnId] || [];
    list.push(payload);
    state.turnEvidence[turnId] = list.slice(-8);
  }

  function turnEvidenceText(turn) {
    const live = state.turnEvidence[turn?.turn_id] || [];
    const refs = Array.isArray(turn?.execution_refs) ? turn.execution_refs : [];
    const blocks = [];
    live.forEach((item, index) => {
      blocks.push(formatEvidencePayload(item, item.sourceLabel || `实时步骤 ${index + 1}`));
    });
    refs.forEach((item, index) => {
      const packages = item.reproducibility?.packages;
      const packageNames = packages && typeof packages === "object"
        ? Object.keys(packages).filter(Boolean).slice(0, 12).join("、")
        : "";
      blocks.push(formatEvidencePayload({
        ok: item.ok,
        job_id: item.job_id,
        error: item.error,
        warnings: item.warnings,
        code: item.code,
        stdout: item.stdout,
        stderr: item.stderr,
        packages: packageNames,
      }, item.job_id ? `R 执行 ${index + 1} · ${item.job_id}` : `R 执行 ${index + 1}`));
    });
    return blocks.join("\n\n" + "=".repeat(64) + "\n\n");
  }

  function bindWorkbenchPages() {
    if (bindWorkbenchPages.bound) return;
    bindWorkbenchPages.bound = true;
    window.addEventListener("hashchange", () => {
      const view = workbenchViewFromHash() || "assistant";
      if (view === state.activeView) return;
      setWorkbenchView(view, { persist: true, syncHash: false });
    });
  }

  function setWorkbenchView(view, options = {}) {
    state.activeView = view === "results" ? "results" : "assistant";
    if (options.persist !== false) {
      try {
        localStorage.setItem("rsw.activeView", state.activeView);
      } catch {
        // Ignore storage failures; the current view still updates for this page.
      }
    }
    if (options.syncHash !== false) syncWorkbenchHash();
    renderWorkbenchView();
    const banner = $("rsw-results-ready");
    if (banner && state.activeView === "results") banner.hidden = true;
  }

  function derivedTables() {
    return state.analysisResult?.derivedTables || [];
  }

  function totalTableCount() {
    return resultCount("tables") + derivedTables().length;
  }

  function hasInterpretation() {
    return Boolean(displayInterpretationText());
  }

  function interpretationParts() {
    const parts = state.analysisResult?.interpretationParts;
    if (Array.isArray(parts)) return parts;
    const legacy = state.analysisResult?.interpretation;
    return legacy ? [{ title: "AI 结果解释", text: legacy }] : [];
  }

  function cachedTextOutputs() {
    const cache = state.analysisResult?.textPreviews || {};
    return [...resultArtifacts("texts"), ...resultArtifacts("models")]
      .map((artifact) => {
        const text = cache[artifactKey(artifact)];
        return text ? { artifact, text } : null;
      })
      .filter(Boolean);
  }

  function displayInterpretationText() {
    const sections = [];
    cachedTextOutputs().forEach(({ artifact, text }) => {
      const label = artifact.kind === "model" ? "R 模型摘要" : "R 文本输出";
      sections.push(`## ${label}：${artifact.name || "结果摘要"}\n\n${normalizeMarkdownText(text)}`);
    });
    interpretationParts().forEach((part, index) => {
      const text = normalizeMarkdownText(part?.text || "");
      if (!text) return;
      sections.push(`## ${part?.title || `AI 结果解释 ${index + 1}`}\n\n${text}`);
    });
    return sections.join("\n\n---\n\n").trim();
  }

  function displayProtocolInterpretationText(protocol) {
    if (!protocol || state.resultScope === "all") return displayInterpretationText();
    const sections = [];
    (protocol.visibleTexts || []).forEach((item) => {
      const text = normalizeMarkdownText(item.fullText || item.preview || "");
      if (!text) return;
      const prefix = item.source === "artifact"
        ? (item.kind === "model" ? "R 模型摘要" : "R 文本输出")
        : "AI 结果解释";
      sections.push(`## ${prefix}：${item.title || "结果解释"}\n\n${text}`);
    });
    return sections.join("\n\n---\n\n").trim();
  }

  function currentDisplayInterpretationText() {
    return displayProtocolInterpretationText(researchOutputProtocol());
  }

  function deliveryItems() {
    const variables = state.activeSummary?.variables || [];
    const result = state.analysisResult?.payload?.result || null;
    const failed = blockingResultFailure(result);
    const hasTables = totalTableCount() > 0;
    const hasFigures = resultCount("figures") > 0;
    const hasText = hasInterpretation() || resultCount("texts") > 0;
    const rRuntimeReady = state.readiness?.r_runtime?.available === true;
    const llmReady = state.readiness ? isAiConfigured() : false;
    return [
      {
        key: "dataset",
        label: "数据理解",
        ok: Boolean(state.activeDataset && variables.length),
        status: state.activeDataset && !variables.length ? "warn" : "",
        detail: variables.length
          ? `${formatNumber(state.activeSummary?.rows)} 行 · ${formatNumber(state.activeSummary?.columns)} 变量`
          : (state.activeDataset ? "文件已上传，等待变量结构" : "等待上传数据"),
      },
      {
        key: "runtime",
        label: "模型与执行",
        ok: Boolean(llmReady && rRuntimeReady),
        status: llmReady && rRuntimeReady ? "" : "warn",
        detail: llmReady && rRuntimeReady ? "DeepSeek 与本地 R runtime 就绪" : "需要配置 DeepSeek Key 或检查 R runtime",
      },
      {
        key: "execution",
        label: "R 执行",
        ok: Boolean(result && !failed),
        status: failed ? "error" : "",
        detail: failed ? (result?.error || "R 返回异常") : (result ? "真实 R 输出已返回" : "等待运行分析"),
      },
      {
        key: "artifacts",
        label: "结果产物",
        ok: Boolean(hasTables || hasFigures || hasText),
        status: result && !(hasTables || hasFigures || hasText) ? "warn" : "",
        detail: hasTables || hasFigures || hasText
          ? `${totalTableCount()} 表 · ${resultCount("figures")} 图 · ${hasText ? "有解释" : "无解释"}`
          : "等待表格、图形或文本结果",
      },
      {
        key: "export",
        label: "可导出",
        ok: Boolean(hasTables || hasFigures || displayInterpretationText()),
        status: hasTables || hasFigures || displayInterpretationText() ? "" : "warn",
        detail: hasTables || hasFigures
          ? "表格/图形可下载"
          : (displayInterpretationText() ? "解释可下载" : "等待正式产物"),
      },
    ];
  }

  function deliveryScore() {
    const items = deliveryItems();
    const passed = state.activeDataset ? items.filter((item) => item.ok).length : 0;
    return { passed, total: items.length, items };
  }

  function deliveryChecklistText() {
    const score = deliveryScore();
    const dataset = state.activeDataset?.name || "待上传";
    const result = state.analysisResult?.payload?.result || {};
    return [
      "# 科研统计分析交付清单",
      "",
      `- 数据集：${dataset}`,
      `- 验收进度：${score.passed}/${score.total}`,
      `- 表格：${totalTableCount()}`,
      `- 图形：${resultCount("figures")}`,
      `- 解释：${hasInterpretation() ? "已生成" : "待生成"}`,
      "",
      ...score.items.map((item) => `- ${item.ok ? "[x]" : "[ ]"} ${item.label}：${item.detail}`),
      "",
      result.error ? `R 执行错误：${result.error}` : "R 执行错误：无",
    ].join("\n");
  }

  function variableNames(limit = 20) {
    return (state.activeSummary?.variables || [])
      .map((item) => item.name)
      .filter(Boolean)
      .slice(0, limit);
  }

  function templateInputSpecs(item) {
    const inputs = item?.input_contract?.inputs || [];
    return Array.isArray(inputs) ? inputs.filter((input) => input?.id && input?.kind) : [];
  }

  function templatePreparation(templateId) {
    return state.templatePreparations[templateId] || null;
  }

  function templateInputValues(templateId) {
    return state.templateInputValues[templateId] || {};
  }

  function templateById(id) {
    return TEMPLATE_LIBRARY.find((item) => item.id === id) || null;
  }

  function userTemplates() {
    return TEMPLATE_LIBRARY.filter((item) => item?.visibility !== "internal");
  }

  function familyLabel(family) {
    const labels = {
      data_import_cleaning_profiling: "数据导入、清洗与画像",
      publication_tables_and_reporting: "论文表格与报告",
      classical_statistics_and_mixed_models: "经典统计与混合模型",
      clinical_trials_power_meta_analysis: "临床试验、效能与 Meta 分析",
      psychology_questionnaires_social_science: "心理测量、问卷与社会科学",
      machine_learning_and_explainable_prediction: "机器学习与可解释预测",
      clinical_prediction_and_diagnostic_performance: "临床预测与诊断效能",
      survival_competing_risks_prognosis: "生存、竞争风险与预后",
      causal_inference_and_real_world_evidence: "因果推断与真实世界证据",
      epidemiology_and_public_health: "流行病学与公共卫生",
      survey_weighted_analysis: "复杂抽样与加权分析",
      time_series_longitudinal_trends_signal_processing: "时间序列、纵向趋势与信号",
      spatial_environmental_geographic_analysis: "空间、环境与地理分析",
      text_mining_and_network_analysis: "文本挖掘与网络分析",
      bulk_omics_and_differential_expression: "组学与差异表达",
      pathway_enrichment_annotation: "通路富集与注释",
      single_cell_and_cell_annotation: "单细胞与细胞注释",
      microbiome_proteomics_multi_omics_medical_imaging: "微生物、蛋白、多组学与影像",
    };
    const value = String(family || "general_analysis");
    return labels[value] || value.replaceAll("_", " ");
  }

  function resourceLabel(value) {
    return value === "extended" ? "扩展资源" : "标准资源";
  }

  function unansweredPreparationQuestions(item, preparation) {
    const values = templateInputValues(item?.id || "");
    const questions = Array.isArray(preparation?.questions) ? preparation.questions : [];
    return questions.filter((question) => {
      const value = values[question.input_id];
      return Array.isArray(value) ? !value.length : value === undefined || value === null || value === "";
    });
  }

  function templateRequiredOutputs(item) {
    const structured = item?.output_contract?.required;
    if (Array.isArray(structured) && structured.length) return structured;
    const labels = item?.template_contract?.output_contract?.required_outputs || [];
    return labels.map((label, index) => ({
      role: `output_${index + 1}`,
      kind: "text",
      required: true,
      label,
    }));
  }

  function userFacingResolvedInput(item) {
    if (!item?.label || item.value === undefined || item.value === null) return "";
    if (
      ["dataset", "object"].includes(item.kind)
      && String(item.value) === String(state.activeDataset?.dataset_id || "")
    ) {
      const name = state.activeDataset?.name || "当前数据";
      return `${item.label}=当前上传数据（${name}）`;
    }
    if (item.input_id === "__matrix_orientation__") {
      const labels = {
        samples_by_features: "样本为行、科学特征为列",
        requires_restructure: "需要先整理矩阵与样本注释",
        not_target_matrix: "不是目标科学矩阵",
      };
      return `${item.label}=${labels[item.value] || item.value}`;
    }
    return `${item.label}=${Array.isArray(item.value) ? item.value.join("、") : item.value}`;
  }

  function promptTemplateText(kind) {
    const names = variableNames();
    const group = firstLikelyGroup(state.activeSummary?.variables || []);
    const template = templateById(kind) || TEMPLATE_LIBRARY[0];
    const fallbackPrompt = [
      `请按「${template.title || "科研统计"}」完成${templateWorkflow(template)}分析。`,
      `目标：${template.prompt || template.subtitle || "根据当前数据完成科研统计分析。"}`,
      "R：真实计算，输出表格、图形和论文式解释；缺关键字段先问，不要猜测。",
    ].join("\n");
    const basePrompt = String(template.analysis_prompt || fallbackPrompt).trim();
    const context = [];
    const preparation = templatePreparation(template.id);
    const resolved = Object.values(preparation?.plan?.resolved_inputs || {})
      .map(userFacingResolvedInput)
      .filter(Boolean);
    if (resolved.length) {
      context.push(`分析输入已在模板面板中确认并冻结（${resolved.length} 项）；如需改变分组、结局、暴露、协变量、水平或变量集合，请返回模板输入修改，不能通过改写这段自然语言摘要替换。`);
      context.push("适用性判定：本地结构和关键输入已通过；矩阵科学语义若来自用户确认，它是待验证声明而不是数据证据。发送后第一步必须由 R 检查特征类型与数量、取值尺度、缺失/非有限值、重复标识及样本和分组对齐；不满足方法要求时停止并返回具体准备方案，不能强行计算。");
    }
    if (Array.isArray(preparation?.warnings) && preparation.warnings.length) {
      context.push(`标签与语义边界：${preparation.warnings.join("；")}`);
    }
    if (names.length) {
      context.push(`当前变量参考：${names.slice(0, 24).join("、")}。`);
    } else {
      context.push("请先根据上传数据识别关键变量；变量不足时先询问，不要猜测。");
    }
    if (!resolved.length && group && group !== "待识别") {
      context.push(`如需分组比较，可优先评估「${group}」。`);
    }
    return [basePrompt, context.join("\n")].filter(Boolean).join("\n\n");
  }

  function templateExecutionContext(kind, preparation = templatePreparation(kind)) {
    const template = templateById(kind);
    if (!template) return null;
    const contract = template.template_contract || {};
    return {
      id: template.id || "",
      plan_id: preparation?.plan?.plan_id || "",
      title: template.title || "",
      category: template.category || "",
      family: template.family || "",
      scenario: template.scenario || "",
      resource_class: template.resource_class || "standard",
      required_outputs: templateRequiredOutputs(template),
      workflow: templateWorkflow(template),
      package_names: Array.isArray(template.package_names)
        ? template.package_names.slice(0, 16)
        : String(template.packages || "").split(/[,，、;/|]+/).map((item) => item.trim()).filter(Boolean).slice(0, 16),
      variable_bindings: preparation?.plan?.variable_bindings || {},
      resolved_inputs: preparation?.plan?.resolved_inputs || {},
      template_contract: {
        schema_version: contract.schema_version || "",
        applicability: contract.applicability || {},
        methodology: contract.methodology || {},
        output_contract: contract.output_contract || {},
        execution: contract.execution || {},
        fallback_packages: Array.isArray(contract.fallback_packages) ? contract.fallback_packages.slice(0, 10) : [],
      },
    };
  }

  function templatePlanPreview(kind) {
    return templatePreparation(kind)?.plan || null;
  }

  async function prepareTemplate(templateId, { silent = false } = {}) {
    const template = templateById(templateId);
    if (!template || !state.activeDataset?.dataset_id) return null;
    const requestSessionId = state.sid;
    const requestDatasetId = state.activeDataset.dataset_id;
    const requestInputs = JSON.parse(JSON.stringify(templateInputValues(templateId)));
    const inputSignature = JSON.stringify(requestInputs);
    const pending = templatePreparePromises.get(templateId);
    if (
      pending
      && pending.sessionId === requestSessionId
      && pending.datasetId === requestDatasetId
      && pending.inputSignature === inputSignature
    ) {
      state.templatePrepareLoading = templateId;
      if (!silent) setStatus("busy", "正在准备模板", "准备完成后会自动继续，不需要再次点击");
      scheduleTemplateLibraryRender();
      return pending.promise;
    }
    state.templatePrepareLoading = templateId;
    if (!silent) setStatus("busy", "正在准备模板", "本地解析数据结构、参数和适用条件");
    scheduleTemplateLibraryRender();
    let preparationPromise;
    preparationPromise = requestJson(
        `/api/session/${encodeURIComponent(requestSessionId)}/research/templates/${encodeURIComponent(templateId)}/prepare`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            dataset_id: requestDatasetId,
            inputs: requestInputs,
          }),
        }
      )
      .then((preparation) => {
        if (state.sid !== requestSessionId || state.activeDataset?.dataset_id !== requestDatasetId) {
          return null;
        }
        const latestRequest = templatePreparePromises.get(templateId);
        const latestInputSignature = JSON.stringify(templateInputValues(templateId));
        if (
          (latestRequest && latestRequest.promise !== preparationPromise)
          || latestInputSignature !== inputSignature
        ) {
          return null;
        }
        state.templatePreparations[templateId] = preparation;
        if (state.activeTemplateContext?.id === templateId) {
          if (preparation.status === "ready" && preparation.plan) {
            state.activeTemplateContext = templateExecutionContext(templateId, preparation);
            state.activeAnalysisPlan = preparation.plan;
          } else {
            state.activeTemplateContext = null;
            state.activeAnalysisPlan = null;
          }
        }
        const currentValues = { ...templateInputValues(templateId) };
        (preparation.questions || []).forEach((question) => {
          if (
            currentValues[question.input_id] === undefined
            && question.default !== undefined
            && question.default !== null
          ) {
            currentValues[question.input_id] = question.default;
          }
        });
        state.templateInputValues[templateId] = currentValues;
        if (!silent) {
          const details = {
            ready: "系统已冻结输入和分析计划，可直接使用。",
            needs_input: `需确认 ${preparation.questions?.length || 0} 个关键项。`,
            not_applicable: preparation.reason || "当前数据结构不满足此模板。",
            needs_preparation: preparation.preparation_plan?.summary || "当前数据需先完成分析前准备。",
          };
          setStatus(
            ["not_applicable", "needs_preparation"].includes(preparation.status) ? "warning" : "ok",
            preparation.status_label || "模板准备完成",
            details[preparation.status] || "模板准备完成"
          );
        }
        return preparation;
      })
      .finally(() => {
        const current = templatePreparePromises.get(templateId);
        if (current?.promise === preparationPromise) {
          templatePreparePromises.delete(templateId);
          if (state.templatePrepareLoading === templateId) state.templatePrepareLoading = "";
        }
        scheduleTemplateLibraryRender();
      });
    templatePreparePromises.set(templateId, {
      sessionId: requestSessionId,
      datasetId: requestDatasetId,
      inputSignature,
      promise: preparationPromise,
    });
    return preparationPromise;
  }

  function templateCategories() {
    return ["all", ...Array.from(new Set(userTemplates().map((item) => item.category)))];
  }

  function templateWorkflows() {
    return ["all", ...TEMPLATE_WORKFLOWS];
  }

  function categoryLabel(category) {
    return category === "all" ? "全部能力" : category;
  }

  function workflowLabel(workflow) {
    return workflow === "all" ? "全部工作流" : workflow;
  }

  function templateWorkflow(item) {
    if (item.workflow) return item.workflow;
    const text = `${item.id} ${item.title} ${item.subtitle} ${item.outputs} ${item.prompt}`;
    if (/质量|导入|清洗|缺失|EDA|探索|降维|注释|QC|标准化|变量字典|数据处理/.test(text)) return "数据准备";
    if (/样本量|设计|随机|SAP|方案|DAG|因果路径|非劣效|等效|队列|终点/.test(text)) return "研究设计";
    if (/诊断|校准|验证|敏感性|假设|重分类|漂移|阈值|一致性|等值|PH|DCA|ROC/.test(text)) return "验证诊断";
    if (/论文|导出|报告|Table 1|三线表|图表|GRADE|SoF|报告卡|交付/.test(text)) return "论文交付";
    if (/组学|空间|文本|网络|贝叶斯|机器学习|单细胞|微生物|蛋白|图谱|时空|函数型|知识图谱/.test(text)) return "高级专题";
    return "方法建模";
  }

  function categoryMatches(item, category = state.templateCategory) {
    return category === "all" || item.category === category;
  }

  function workflowMatches(item, workflow = state.templateWorkflow) {
    return workflow === "all" || templateWorkflow(item) === workflow;
  }

  function normalizedSearchText(value) {
    return String(value || "").normalize("NFKC").toLowerCase();
  }

  function templateSearchIndex(item) {
    const packages = Array.isArray(item.package_names) ? item.package_names : [item.packages || ""];
    return normalizedSearchText([
      item.title,
      item.scenario,
      item.family,
      ...(Array.isArray(item.search_terms) ? item.search_terms : []),
      ...packages,
      ...(Array.isArray(item.aliases) ? item.aliases : []),
      item.id,
      item.category,
      templateWorkflow(item),
    ].filter(Boolean).join(" "));
  }

  function filteredTemplates() {
    const term = normalizedSearchText(state.templateSearch.trim());
    const terms = term.split(/\s+/).filter(Boolean);
    return userTemplates().filter((item) => {
      if (!categoryMatches(item) || !workflowMatches(item)) return false;
      if (!term) return true;
      const haystack = templateSearchIndex(item);
      return terms.every((itemTerm) => haystack.includes(itemTerm));
    });
  }

  function sessionHistoryKey() {
    return `rsw.history.${state.sid || "default"}`;
  }

  function readStoredHistory() {
    if (!state.sid) return [];
    try {
      const raw = sessionStorage.getItem(sessionHistoryKey());
      const parsed = raw ? JSON.parse(raw) : [];
      if (Array.isArray(parsed)) return parsed.slice(0, 12);
      const createdAt = Date.parse(parsed?.created_at || "");
      if (createdAt && Date.now() - createdAt > 24 * 60 * 60 * 1000) {
        sessionStorage.removeItem(sessionHistoryKey());
        return [];
      }
      return Array.isArray(parsed?.history) ? parsed.history.slice(0, 12) : [];
    } catch {
      return [];
    }
  }

  function writeStoredHistory() {
    if (!state.sid) return;
    sessionStorage.setItem(sessionHistoryKey(), JSON.stringify({
      created_at: new Date().toISOString(),
      history: state.history.slice(0, 12),
    }));
  }

  async function persistResultState() {
    if (!state.sid) return;
    await loadResultState({ silent: true }).catch(() => {});
  }

  async function clearPersistedResultState() {
    if (!state.sid) return;
    await apiFetch(`/api/session/${encodeURIComponent(state.sid)}/research/result-state`, {
      method: "DELETE",
    }).catch(() => {});
  }

  async function loadPersistedResultState() {
    if (!state.sid || state.analysisResult) return;
    const payload = await requestJson(`/api/session/${encodeURIComponent(state.sid)}/research/result-state`);
    if (payload.exists && payload.state?.schema_version === 2) {
      applyResultState(payload.state);
      const outcome = resultTurnOutcome(activeTurn());
      if (outcome) showAnalysisOutcome(outcome);
      else setStatus("ok", "已恢复短期结果", payload.saved_at ? `恢复 ${payload.saved_at} 保存的分析结果。` : "已恢复当前会话结果。");
      renderAll();
      return;
    }
    if (!payload.exists || !payload.state?.analysisResult) return;
    const saved = payload.state;
    const datasetId = saved.active_dataset_id || "";
    if (datasetId && state.datasets.length) {
      const dataset = state.datasets.find((item) => item.dataset_id === datasetId);
      if (!dataset) return;
      state.activeDataset = dataset;
      state.activeSummary = summaryOf(dataset);
    }
    state.analysisResult = saved.analysisResult;
    state.selectedTableIndex = Number(saved.selectedTableIndex || 0);
    state.selectedFigureIndex = Number(saved.selectedFigureIndex || 0);
    state.resultScope = saved.resultScope === "all" ? "all" : "latest";
    if (Array.isArray(saved.history)) {
      state.history = saved.history.slice(0, 12);
      writeStoredHistory();
    }
    setStatus("ok", "已恢复短期结果", payload.saved_at ? `恢复 ${payload.saved_at} 保存的分析结果。` : "已恢复当前会话结果。");
    const restoredTable = state.analysisResult?.payload?.result?.tables?.[state.selectedTableIndex];
    if (restoredTable?.path) {
      await loadTablePreview(state.selectedTableIndex).catch(() => {});
    }
    renderAll();
  }

  async function loadResultState({ silent = false } = {}) {
    if (!state.sid) return null;
    const payload = await requestJson(`/api/session/${encodeURIComponent(state.sid)}/research/result-state`);
    if (payload.exists && payload.state?.schema_version === 2) {
      applyResultState(payload.state);
      if (!silent) {
        setStatus("ok", "结果已更新", "分析轮次和论文整理状态已同步。");
      }
      renderAll();
      return state.resultState;
    }
    return null;
  }

  function applyResultState(resultState) {
    state.resultState = resultState || null;
    const turns = resultTurns();
    if (!state.activeTurnId || !turns.some((turn) => turn.turn_id === state.activeTurnId)) {
      state.activeTurnId = turns.length ? turns[turns.length - 1].turn_id : "";
    }
    const dataset = resultState?.dataset || {};
    if (dataset.dataset_id && (!state.activeDataset || state.activeDataset.dataset_id === dataset.dataset_id)) {
      state.activeDataset = { ...(state.activeDataset || {}), ...dataset };
      state.activeSummary = dataset.summary || state.activeSummary;
    }
  }

  function resultTurns() {
    return Array.isArray(state.resultState?.analysis_turns) ? state.resultState.analysis_turns : [];
  }

  function resultOverview() {
    return state.resultState?.overview || {};
  }

  function activeTurn() {
    const turns = resultTurns();
    return turns.find((turn) => turn.turn_id === state.activeTurnId) || turns[turns.length - 1] || null;
  }

  function turnArtifacts(turn, { includeMethods = false } = {}) {
    const artifacts = Array.isArray(turn?.artifacts) ? turn.artifacts : [];
    return includeMethods
      ? artifacts
      : artifacts.filter((artifact) => !["method_note", "quality_note"].includes(artifact.role));
  }

  function artifactTypeLabel(artifact) {
    if (artifact?.kind === "table") return "表格";
    if (artifact?.kind === "figure") return "图形";
    if (artifact?.kind === "text") return artifact.role === "quality_note" ? "限制" : "文本";
    if (artifact?.kind === "model") return "模型";
    return "产物";
  }

  function artifactRoleLabel(role) {
    return {
      primary_table: "主表",
      primary_figure: "主图",
      supporting_table: "补充表",
      diagnostic_table: "诊断表",
      diagnostic_figure: "诊断图",
      paper_text: "论文写法",
      quality_note: "质量限制",
      method_note: "Methods Note",
      model_summary: "模型摘要",
    }[role] || "科研产物";
  }

  function artifactDisplayTitle(artifact) {
    return artifact?.title || artifact?.name || "科研产物";
  }

  function truncateText(value, limit = 280) {
    const text = String(value || "").replace(/\s+/g, " ").trim();
    if (text.length <= limit) return text;
    return `${text.slice(0, limit).trim()}...`;
  }

  function artifactPreviewLabel(artifact) {
    if (artifact?.kind === "table") return "预览表格";
    if (artifact?.kind === "figure") return "放大查看";
    if (artifact?.kind === "text") return "阅读全文";
    if (artifact?.kind === "model") return "查看模型摘要";
    return "查看详情";
  }

  function artifactCardPreviewText(artifact) {
    const direct = artifact?.preview || artifact?.caption || artifact?.footnote || "";
    if (direct) return truncateText(direct, 320);
    if (artifact?.kind === "table") return "表格已保存，可在结果页直接预览、横向查看并导出。";
    if (artifact?.kind === "figure") return "图形已保存，可放大查看图像、图注和导出格式。";
    if (artifact?.kind === "model") return "模型摘要已保存，可在线查看并下载。";
    return "产物已归档，可打开查看详细内容。";
  }

  function artifactTextForAction(artifact) {
    if (!artifact) return "";
    const previewText = String(artifact.preview || artifact.caption || artifact.footnote || "").trim();
    if (artifact.kind === "text" && artifact.source === "ai" && artifact.role === "paper_text") {
      const turn = resultTurns().find((candidate) =>
        (candidate.artifacts || []).some((item) => item.artifact_id === artifact.artifact_id)
      );
      const narrativeText = String(turn?.narrative || "").trim();
      if (narrativeText.length > previewText.length) return narrativeText;
    }
    return previewText;
  }

  function artifactActionButtonsHtml(
    artifact,
    { paperMode = false, includeOpen = true, includeTextTools = false } = {}
  ) {
    const downloadButtons = (artifact.export_formats || [])
      .filter((format) => artifact.path)
      .map((format) => `<button type="button" class="rsw-artifact-action" data-artifact-download="${escapeHtml(artifact.artifact_id)}" data-format="${escapeHtml(format)}">下载 ${escapeHtml(format.toUpperCase())}</button>`)
      .join("");
    const virtualTextDownload = !artifact.path && artifact.kind === "text" && artifactTextForAction(artifact)
      ? `<button type="button" class="rsw-artifact-action" data-artifact-text-download="${escapeHtml(artifact.artifact_id)}">下载 TXT</button>`
      : "";
    const copyTextButton = includeTextTools && ["text", "model"].includes(artifact.kind)
      ? `<button type="button" class="rsw-artifact-action" data-artifact-copy="${escapeHtml(artifact.artifact_id)}">复制文本</button>`
      : "";
    return `
      ${includeOpen ? `<button type="button" class="rsw-artifact-action is-primary" data-artifact-open="${escapeHtml(artifact.artifact_id)}">${artifactPreviewLabel(artifact)}</button>` : ""}
      <button type="button" class="rsw-artifact-action ${artifact.core_selected ? "is-active" : ""}" data-artifact-core="${escapeHtml(artifact.artifact_id)}">${artifact.core_selected ? "移出核心" : "设为核心"}</button>
      ${artifact.paper_eligible || paperMode ? `<button type="button" class="rsw-artifact-action ${artifact.included_in_paper ? "is-active" : ""}" data-artifact-paper="${escapeHtml(artifact.artifact_id)}">${artifact.included_in_paper ? "移出论文" : "纳入论文"}</button>` : ""}
      ${copyTextButton}
      ${downloadButtons || virtualTextDownload}
    `;
  }

  function primaryNarrativeArtifact(artifacts) {
    return (artifacts || []).find((artifact) => artifact.kind === "text" && artifact.role === "paper_text" && artifact.source === "ai")
      || (artifacts || []).find((artifact) => artifact.kind === "text" && artifact.role === "paper_text")
      || (artifacts || []).find((artifact) => artifact.kind === "text" && artifact.role !== "quality_note");
  }

  function artifactPreviewMetaHtml(artifact, payload = null) {
    const pieces = [artifactTypeLabel(artifact), artifactRoleLabel(artifact.role)];
    if (artifact.paper_group) pieces.push(artifact.paper_group);
    if (payload?.kind === "table") {
      pieces.push(`${formatNumber(payload.total_rows ?? (payload.rows || []).length)} 行`);
      pieces.push(`${formatNumber(payload.total_columns ?? (payload.columns || []).length)} 列`);
      if (payload.truncated) pieces.push("已截断预览");
    }
    if (artifact.source === "ai") pieces.push("AI 解释");
    if (artifact.source === "r") pieces.push("R 产物");
    return pieces.filter(Boolean).map((item) => `<span>${escapeHtml(item)}</span>`).join("");
  }

  function artifactTablePreviewHtml(payload, artifact) {
    const rawRows = Array.isArray(payload?.rows) ? payload.rows : [];
    const rawColumns = Array.isArray(payload?.columns) && payload.columns.length
      ? payload.columns
      : Object.keys(rawRows[0] || {});
    if (!rawColumns.length) {
      return `<div class="rsw-empty-note">该表格暂无可预览行。</div>`;
    }
    const table = normalizeArtifactTable({
      name: artifactDisplayTitle(artifact),
      columns: rawColumns,
      rows: rawRows,
    }, artifact);
    const columns = table.columns || rawColumns;
    const rows = table.rows || rawRows;
    return `
      <div class="rsw-artifact-table-shell">
        <table class="rsw-artifact-preview-table">
          <thead>
            <tr>${columns.map((column) => `<th>${escapeHtml(column)}</th>`).join("")}</tr>
          </thead>
          <tbody>
            ${rows.map((row) => `
              <tr>${columns.map((column) => tableCellHtml(row?.[column] ?? "")).join("")}</tr>
            `).join("")}
          </tbody>
        </table>
      </div>
      ${payload?.truncated ? `<p class="rsw-artifact-preview-note">当前在线预览最多显示 100 行、50 列，长字段会缩略；完整数据请使用下载导出。</p>` : ""}
    `;
  }

  function artifactPreviewBodyHtml(artifact, payload = null) {
    if (isBrowserPreviewableFigure(artifact)) {
      const caption = artifact.caption || artifact.preview || "";
      return `
        <figure class="rsw-artifact-preview-figure">
          <img src="${escapeHtml(artifactUrl(artifact))}" alt="${escapeHtml(artifactDisplayTitle(artifact))}">
          ${caption ? `<figcaption>${escapeHtml(caption)}</figcaption>` : ""}
        </figure>
      `;
    }
    if (payload?.kind === "table" || artifact.kind === "table") {
      return artifactTablePreviewHtml(payload || {}, artifact);
    }
    if (payload?.kind === "text" || ["text", "model"].includes(artifact.kind)) {
      const text = String(payload?.text || artifactTextForAction(artifact) || "").trim();
      state.activeArtifactPreviewText = text;
      return text
        ? `<div class="rsw-artifact-preview-text rsw-message-content">${renderMarkdown(text)}</div>${payload?.truncated ? `<p class="rsw-artifact-preview-note">文本较长，当前仅显示前 200,000 字。</p>` : ""}`
        : `<div class="rsw-empty-note">该文本产物暂无可预览内容。</div>`;
    }
    return `
      <div class="rsw-empty-note">
        该产物暂不支持在线预览，可使用下载按钮查看完整文件。
      </div>
    `;
  }

  function ensureArtifactPreviewDialog() {
    let modal = $("rsw-artifact-modal");
    if (modal) return modal;
    document.body.insertAdjacentHTML("beforeend", `
      <div class="rsw-artifact-modal" id="rsw-artifact-modal" hidden>
        <div class="rsw-artifact-backdrop" data-artifact-preview-close></div>
        <section class="rsw-artifact-dialog" role="dialog" aria-modal="true" aria-labelledby="rsw-artifact-preview-title">
          <header class="rsw-artifact-dialog-head">
            <div>
              <div class="rsw-artifact-preview-meta" id="rsw-artifact-preview-meta"></div>
              <h2 id="rsw-artifact-preview-title">科研产物</h2>
            </div>
            <button class="rsw-icon-btn" type="button" aria-label="关闭预览" data-artifact-preview-close>
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12" /><path d="M18 6L6 18" /></svg>
            </button>
          </header>
          <div class="rsw-artifact-preview-body" id="rsw-artifact-preview-body"></div>
          <footer class="rsw-artifact-preview-actions" id="rsw-artifact-preview-actions"></footer>
        </section>
      </div>
    `);
    modal = $("rsw-artifact-modal");
    modal.addEventListener("click", (event) => {
      if (event.target.closest("[data-artifact-preview-close]")) {
        closeArtifactPreview();
        return;
      }
      const coreButton = event.target.closest("[data-artifact-core]");
      if (coreButton) {
        toggleArtifactCore(coreButton.dataset.artifactCore)
          .then(() => refreshArtifactPreviewActions(coreButton.dataset.artifactCore))
          .catch((error) => handleUiError(error));
        return;
      }
      const paperButton = event.target.closest("[data-artifact-paper]");
      if (paperButton) {
        toggleArtifactPaper(paperButton.dataset.artifactPaper)
          .then(() => refreshArtifactPreviewActions(paperButton.dataset.artifactPaper))
          .catch((error) => handleUiError(error));
        return;
      }
      const downloadButton = event.target.closest("[data-artifact-download]");
      if (downloadButton) {
        const artifact = findResultArtifact(downloadButton.dataset.artifactDownload);
        if (artifact) downloadUrl(artifactDownloadUrl(artifact, downloadButton.dataset.format || ""));
        return;
      }
      const textDownloadButton = event.target.closest("[data-artifact-text-download]");
      if (textDownloadButton) {
        downloadArtifactText(textDownloadButton.dataset.artifactTextDownload);
        return;
      }
      const copyButton = event.target.closest("[data-artifact-copy]");
      if (copyButton) {
        const artifact = findResultArtifact(copyButton.dataset.artifactCopy);
        copyText(state.activeArtifactPreviewText || artifactTextForAction(artifact), "产物文本").catch((error) => handleUiError(error));
      }
    });
    return modal;
  }

  function closeArtifactPreview() {
    const modal = $("rsw-artifact-modal");
    if (!modal) return;
    modal.hidden = true;
    state.activeArtifactPreviewId = "";
    state.activeArtifactPreviewText = "";
    if ($("rsw-ai-modal")?.hidden !== false && $("rsw-template-modal")?.hidden !== false) {
      document.body.classList.remove("rsw-modal-open");
    }
    restoreModalFocus();
  }

  function refreshArtifactPreviewActions(artifactId) {
    const artifact = findResultArtifact(artifactId);
    const actions = $("rsw-artifact-preview-actions");
    const meta = $("rsw-artifact-preview-meta");
    if (!artifact || !actions) return;
    actions.innerHTML = artifactActionButtonsHtml(artifact, { includeOpen: false, includeTextTools: true });
    if (meta) meta.innerHTML = artifactPreviewMetaHtml(artifact);
  }

  function downloadArtifactText(artifactId) {
    const artifact = findResultArtifact(artifactId);
    if (!artifact) return;
    const text = state.activeArtifactPreviewId === artifactId && state.activeArtifactPreviewText
      ? state.activeArtifactPreviewText
      : artifactTextForAction(artifact);
    if (!text) {
      toast("当前文本为空，无法下载。", "error");
      return;
    }
    downloadText(`${safeFileStem(artifactDisplayTitle(artifact), "research-text")}.txt`, text, "text/plain;charset=utf-8");
  }

  async function openArtifactPreview(artifactId) {
    const artifact = findResultArtifact(artifactId);
    if (!artifact) {
      toast("未找到该产物。", "error");
      return;
    }
    const modal = ensureArtifactPreviewDialog();
    rememberModalFocus();
    state.activeArtifactPreviewId = artifactId;
    state.activeArtifactPreviewText = artifactTextForAction(artifact);
    $("rsw-artifact-preview-title").textContent = artifactDisplayTitle(artifact);
    $("rsw-artifact-preview-meta").innerHTML = artifactPreviewMetaHtml(artifact);
    $("rsw-artifact-preview-actions").innerHTML = artifactActionButtonsHtml(artifact, { includeOpen: false, includeTextTools: true });
    $("rsw-artifact-preview-body").innerHTML = `<div class="rsw-artifact-preview-loading">正在加载产物预览...</div>`;
    modal.hidden = false;
    document.body.classList.add("rsw-modal-open");
    setTimeout(() => qs("button", modal)?.focus(), 30);

    let payload = null;
    if (artifact.path && artifact.kind !== "figure") {
      try {
        payload = await fetchArtifactJson(artifact);
      } catch (error) {
        $("rsw-artifact-preview-body").innerHTML = `<div class="rsw-empty-note">预览加载失败：${escapeHtml(error.message || "未知错误")}</div>`;
        return;
      }
    }
    $("rsw-artifact-preview-meta").innerHTML = artifactPreviewMetaHtml(artifact, payload);
    $("rsw-artifact-preview-body").innerHTML = artifactPreviewBodyHtml(artifact, payload);
  }

  function resultViewLabel(view) {
    return {
      overview: "研究概览",
      turns: "分析轮次",
      paper: "论文整理",
      delivery: "交付验收",
    }[view] || "研究概览";
  }

  function setResultView(view) {
    state.resultView = ["overview", "turns", "paper", "delivery"].includes(view) ? view : "overview";
    renderResultV2();
  }

  function filteredTurns() {
    const search = state.turnSearch.trim().toLowerCase();
    return resultTurns().filter((turn) => {
      const artifacts = turnArtifacts(turn, { includeMethods: true });
      if (state.turnFilter === "core" && !artifacts.some((artifact) => artifact.core_selected)) return false;
      if (state.turnFilter === "paper" && !artifacts.some((artifact) => artifact.included_in_paper)) return false;
      if (!search) return true;
      const haystack = [
        turn.title,
        turn.question,
        turn.template_title,
        turn.source,
        ...artifacts.map((artifact) => `${artifact.title} ${artifact.name} ${artifact.role} ${artifact.paper_group}`),
      ].join(" ").toLowerCase();
      return haystack.includes(search);
    });
  }

  function renderResultV2() {
    qsa("[data-result-view]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.resultView === state.resultView);
    });
    qsa("[data-result-view-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.resultViewPanel !== state.resultView;
    });
    renderTurnList();
    renderOverviewV2();
    renderTurnDetail();
    renderPaperBoard();
    renderDeliveryV2();
    renderResultAssist();
  }

  function renderTurnList() {
    const el = $("rsw-turn-list");
    if (!el) return;
    const turns = filteredTurns();
    if (!turns.length) {
      el.innerHTML = `<div class="rsw-empty-note">暂无符合条件的分析轮次。</div>`;
      return;
    }
    el.innerHTML = turns.map((turn, index) => {
      const summary = turn.summary || {};
      return `
        <button type="button" class="rsw-turn-card ${turn.turn_id === state.activeTurnId ? "is-active" : ""}" data-turn-id="${escapeHtml(turn.turn_id)}">
          <strong>${escapeHtml(turn.title || `分析轮次 ${index + 1}`)}</strong>
          <span>${escapeHtml(turn.template_title || (turn.source === "template" ? "模板分析" : "自然语言分析"))}</span>
          <small>${formatNumber(summary.table_count || 0)} 表 · ${formatNumber(summary.figure_count || 0)} 图 · ${formatNumber(summary.text_count || 0)} 文本</small>
        </button>`;
    }).join("");
  }

  function renderOverviewV2() {
    const summaryEl = $("rsw-overview-summary");
    const updatedEl = $("rsw-overview-updated");
    const metricsEl = $("rsw-overview-metrics");
    const turnsEl = $("rsw-overview-turns");
    const overview = resultOverview();
    const dataset = state.resultState?.dataset || state.activeDataset || {};
    const datasetSummary = dataset.summary || state.activeSummary || {};
    if (updatedEl) updatedEl.textContent = state.resultState?.updated_at ? `更新 ${state.resultState.updated_at}` : "等待结果";
    if (summaryEl) {
      summaryEl.textContent = dataset.name
        ? `${dataset.name} 已进入短期研究会话；当前累计 ${formatNumber(overview.analysis_turn_count || 0)} 个分析轮次。`
        : "上传数据并运行分析后，这里只展示会话级状态，不混入具体图表。";
    }
    if (metricsEl) {
      metricsEl.innerHTML = `
        <div><span>样本量</span><strong>${formatNumber(datasetSummary.rows)}</strong></div>
        <div><span>变量数</span><strong>${formatNumber(datasetSummary.columns)}</strong></div>
        <div><span>分析轮次</span><strong>${formatNumber(overview.analysis_turn_count || 0)}</strong></div>
        <div><span>纳入论文</span><strong>${formatNumber(overview.included_in_paper_count || 0)}</strong></div>`;
    }
    if (turnsEl) {
      const turns = resultTurns().slice(-4).reverse();
      turnsEl.innerHTML = turns.length
        ? turns.map((turn) => `
          <button type="button" class="rsw-turn-card" data-turn-id="${escapeHtml(turn.turn_id)}" data-result-view-open="turns">
            <strong>${escapeHtml(turn.title || "分析轮次")}</strong>
            <small>${formatNumber(turn.summary?.table_count || 0)} 表 · ${formatNumber(turn.summary?.figure_count || 0)} 图 · ${formatNumber(turn.summary?.text_count || 0)} 文本</small>
          </button>`).join("")
        : `<div class="rsw-empty-note">运行模板或自然语言分析后，这里显示最近轮次。</div>`;
    }
  }

  function artifactCardHtml(artifact, { paperMode = false } = {}) {
    const isFigure = isBrowserPreviewableFigure(artifact);
    const preview = artifactCardPreviewText(artifact);
    return `
      <article class="rsw-artifact-card" data-artifact-id="${escapeHtml(artifact.artifact_id)}">
        ${isFigure ? `<figure><img src="${escapeHtml(artifactUrl(artifact))}" alt="${escapeHtml(artifact.title || artifact.name || "图形")}" loading="lazy"></figure>` : ""}
        <strong>${escapeHtml(artifactDisplayTitle(artifact))}</strong>
        <span>${artifactTypeLabel(artifact)} · ${artifactRoleLabel(artifact.role)}${artifact.paper_group ? ` · ${escapeHtml(artifact.paper_group)}` : ""}</span>
        ${preview ? `<small>${escapeHtml(preview)}</small>` : ""}
        <div class="rsw-artifact-actions">
          ${artifactActionButtonsHtml(artifact, { paperMode })}
        </div>
      </article>`;
  }

  function renderTurnDetail() {
    const el = $("rsw-turn-detail");
    if (!el) return;
    const turn = activeTurn();
    if (!turn) {
      el.innerHTML = `<div class="rsw-result-card"><h3>分析轮次</h3><p>运行分析后，每次模板或自然语言请求都会形成独立轮次。</p></div>`;
      return;
    }
    const artifacts = turnArtifacts(turn);
    const narrativeArtifact = primaryNarrativeArtifact(artifacts);
    const narrativeText = artifactTextForAction(narrativeArtifact) || turn.narrative || "";
    const cardArtifacts = narrativeArtifact
      ? artifacts.filter((artifact) => artifact.artifact_id !== narrativeArtifact.artifact_id)
      : artifacts;
    const coreArtifacts = cardArtifacts
      .filter((artifact) => artifact.core_selected)
      .sort((a, b) => (a.priority || 999) - (b.priority || 999))
      .slice(0, 3);
    const fullArtifacts = cardArtifacts.filter((artifact) => !coreArtifacts.some((core) => core.artifact_id === artifact.artifact_id));
    const conclusions = turn.conclusion_items || [];
    el.innerHTML = `
      <div class="rsw-turn-layout">
        <section class="rsw-result-card">
          <div class="rsw-card-headline">
            <h3>${escapeHtml(turn.title || "分析轮次")}</h3>
            <span>${escapeHtml(turn.template_title || (turn.source === "template" ? "模板分析" : "自然语言分析"))}</span>
          </div>
          <p>${escapeHtml(turn.question || "本轮问题未记录。")}</p>
        </section>
        <section class="rsw-turn-conclusions">
          <h3>本轮结论</h3>
          ${conclusions.length ? `<ul>${conclusions.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : `<p>等待 AI 生成论文式结果解释。</p>`}
        </section>
        ${narrativeText ? `
          <section class="rsw-turn-narrative">
            <div class="rsw-card-headline">
              <h3>本轮完整解释</h3>
              ${narrativeArtifact ? `<button type="button" class="rsw-mini-btn" data-artifact-open="${escapeHtml(narrativeArtifact.artifact_id)}">阅读全文</button>` : ""}
            </div>
            <div class="rsw-turn-narrative-body rsw-message-content">
              ${renderMarkdown(narrativeText.length > 4200 ? `${narrativeText.slice(0, 4200)}\n\n...` : narrativeText)}
            </div>
          </section>
        ` : ""}
        <section class="rsw-result-card">
          <div class="rsw-card-headline"><h3>核心产物</h3><span>最多展示 3 个</span></div>
          <div class="rsw-artifact-grid rsw-core-artifacts">
            ${coreArtifacts.length ? coreArtifacts.map((artifact) => artifactCardHtml(artifact)).join("") : `<div class="rsw-empty-note">本轮尚无核心产物。</div>`}
          </div>
        </section>
        <section class="rsw-result-card">
          <div class="rsw-card-headline"><h3>完整产物</h3><span>${formatNumber(fullArtifacts.length)} 个折叠候选</span></div>
          <div class="rsw-artifact-grid rsw-full-artifacts">
            ${fullArtifacts.length ? fullArtifacts.map((artifact) => artifactCardHtml(artifact)).join("") : `<div class="rsw-empty-note">没有更多产物。</div>`}
          </div>
        </section>
        ${turnEvidenceText(turn) ? `
          <details class="rsw-turn-evidence">
            <summary>本轮运行摘要</summary>
            <pre>${escapeHtml(turnEvidenceText(turn))}</pre>
          </details>` : ""}
      </div>`;
  }

  function renderPaperBoard() {
    const el = $("rsw-paper-board");
    if (!el) return;
    const statePayload = state.resultState || {};
    const groups = statePayload.paper_collection?.groups || [
      "Baseline / Table 1",
      "Primary analysis",
      "Secondary analysis",
      "Subgroup analysis",
      "Sensitivity analysis",
      "Diagnostics / Quality",
      "Supplementary",
    ];
    const included = resultTurns()
      .flatMap((turn) => turnArtifacts(turn).map((artifact) => ({ ...artifact, turn })))
      .filter((artifact) => artifact.included_in_paper);
    const groupsWithItems = groups.filter((group) => included.some((artifact) => (artifact.paper_group || "Primary analysis") === group));
    const renderGroups = groupsWithItems.length ? groupsWithItems : [];
    const groupHtml = renderGroups.map((group) => {
      const items = included.filter((artifact) => (artifact.paper_group || "Primary analysis") === group);
      return `
        <section class="rsw-paper-group">
          <h4>${escapeHtml(group)}</h4>
          ${items.map((artifact) => `
            <article class="rsw-paper-item" data-artifact-id="${escapeHtml(artifact.artifact_id)}">
              <strong>${escapeHtml(artifact.title || artifact.name || "论文产物")}</strong>
              <span>${artifactTypeLabel(artifact)} · ${escapeHtml(artifact.turn?.title || "")}</span>
              <label><small>标题</small><input value="${escapeHtml(artifact.title || "")}" data-paper-field="title" data-artifact-id="${escapeHtml(artifact.artifact_id)}"></label>
              ${artifact.kind === "table" ? `<label><small>脚注</small><textarea data-paper-field="footnote" data-artifact-id="${escapeHtml(artifact.artifact_id)}">${escapeHtml(artifact.footnote || "")}</textarea></label>` : ""}
              ${artifact.kind === "figure" ? `<label><small>图注</small><textarea data-paper-field="caption" data-artifact-id="${escapeHtml(artifact.artifact_id)}">${escapeHtml(artifact.caption || "")}</textarea></label>` : ""}
	              <label><small>科研分组</small><select data-paper-field="paper_group" data-artifact-id="${escapeHtml(artifact.artifact_id)}">
	                ${groups.map((candidate) => `<option value="${escapeHtml(candidate)}" ${candidate === group ? "selected" : ""}>${escapeHtml(candidate)}</option>`).join("")}
	              </select></label>
	              <div class="rsw-paper-actions">
	                <button type="button" class="rsw-paper-action" data-artifact-open="${escapeHtml(artifact.artifact_id)}">预览内容</button>
	                <button type="button" class="rsw-paper-action" data-artifact-paper="${escapeHtml(artifact.artifact_id)}">移出论文</button>
	              </div>
	            </article>`).join("")}
        </section>`;
    }).join("");
    el.innerHTML = groupHtml || `<div class="rsw-empty-note">尚未纳入论文产物。可在分析轮次中选择表格、图形或文本进入论文整理。</div>`;
  }

  function renderDeliveryV2() {
    const scoreEl = $("rsw-delivery-score-v2");
    const listEl = $("rsw-delivery-list-v2");
    const delivery = state.resultState?.delivery_status || { score: 0, items: [] };
    if (scoreEl) {
      const total = delivery.items?.length || 5;
      scoreEl.classList.toggle("is-warning", delivery.score > 0 && delivery.score < total);
      scoreEl.classList.toggle("is-error", delivery.score === 0);
      scoreEl.innerHTML = `<strong>${formatNumber(delivery.score || 0)}/${formatNumber(total)}</strong><span>${delivery.score ? "当前会话已有可交付产物" : "等待上传数据"}</span>`;
    }
    if (listEl) {
      listEl.innerHTML = (delivery.items || []).length
        ? delivery.items.map((item) => `<li class="${item.ok ? "is-ok" : "is-warn"}"><strong>${escapeHtml(item.label)}</strong><small>${escapeHtml(item.detail || "")}</small></li>`).join("")
        : `<li class="is-warn"><strong>等待分析</strong><small>上传数据并运行分析后生成交付状态。</small></li>`;
    }
  }

  function renderResultAssist() {
    const basket = $("rsw-paper-basket");
    const overview = resultOverview();
    if (basket) {
      basket.textContent = `${formatNumber(overview.included_in_paper_count || 0)} 个产物已纳入论文整理。`;
    }
  }

  function resultSummaryTextForCopy() {
    const dataset = state.resultState?.dataset || state.activeDataset || {};
    const overview = resultOverview();
    const lines = [
      "# 研究概览",
      "",
      `数据文件：${dataset.name || "未上传"}`,
      `分析轮次：${formatNumber(overview.analysis_turn_count || 0)}`,
      `表格产物：${formatNumber(overview.table_count || 0)}`,
      `图形产物：${formatNumber(overview.figure_count || 0)}`,
      `纳入论文：${formatNumber(overview.included_in_paper_count || 0)}`,
    ];
    const turns = resultTurns();
    if (turns.length) {
      lines.push("", "## 分析轮次");
      turns.forEach((turn, index) => {
        const summary = turn.summary || {};
        lines.push(
          `${index + 1}. ${turn.title || "分析轮次"}：${formatNumber(summary.table_count || 0)} 表、${formatNumber(summary.figure_count || 0)} 图、${formatNumber(summary.text_count || 0)} 文本`
        );
        (turn.conclusion_items || []).slice(0, 3).forEach((item) => {
          lines.push(`   - ${item}`);
        });
      });
    }
    return lines.join("\n");
  }

  function setStatus(kind, title, detail, action = "") {
    state.status = { kind, title, detail, action };
    renderStatus();
  }

  function statusActionLabel(action) {
    if (action === "configure-ai") return "输入 Key";
    if (action === "refresh") return "重新检查";
    return "重试";
  }

  function isAiConfigured() {
    return !state.aiKeyInvalid && state.readiness?.llm?.configured !== false;
  }

  function classifyError(error) {
    const message = String(error?.message || error || "");
    const lower = message.toLowerCase();
    if (lower.includes("there is no package called") || lower.includes("package") || message.includes("R包")) {
      return {
        kind: "error",
        title: "R 包或运行环境异常",
        detail: message || "R 执行缺少依赖，请检查本地 R 环境。",
      };
    }
    if (lower.includes("rscript") || lower.includes("r runtime") || message.includes("R 执行")) {
      return {
        kind: "error",
        title: "R 运行环境异常",
        detail: message || "本地 Rscript 不可用，请检查 R 环境。",
      };
    }
    if (lower.includes("api key") || message.includes("未配置") || lower.includes("llm")) {
      return {
        kind: "warning",
        title: "AI 模型待配置",
        detail: "输入 DeepSeek API Key 即可启用 AI 分析助手。",
      };
    }
    return {
      kind: "error",
      title: "操作未完成",
      detail: message || "请重试或查看日志。",
    };
  }

  async function copyText(text, label = "内容") {
    const value = String(text || "");
    if (!value.trim()) {
      toast(`${label}为空`, "error");
      return;
    }
    try {
      await navigator.clipboard.writeText(value);
      toast(`${label}已复制`, "ok");
    } catch {
      const textarea = document.createElement("textarea");
      textarea.value = value;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "fixed";
      textarea.style.left = "-9999px";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      textarea.remove();
      toast(`${label}已复制`, "ok");
    }
  }

  function downloadText(filename, text, mimeType = "text/markdown;charset=utf-8") {
    const blob = new Blob([String(text || "")], { type: mimeType });
    const url = URL.createObjectURL(blob);
    downloadUrl(url, filename, true);
  }

  function downloadUrl(url, filename = "", revoke = false) {
    const link = document.createElement("a");
    link.href = url;
    if (filename) link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    if (revoke) setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  function safeFileStem(value, fallback = "research-output") {
    return String(value || fallback)
      .trim()
      .replace(/\.[^.]+$/, "")
      .replace(/[\\/:*?"<>|\s]+/g, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 80) || fallback;
  }

  function repairCompactMarkdownTables(value) {
    let text = String(value || "");
    // Some model responses collapse Markdown table rows into `||`, which reads
    // as prose unless we restore row boundaries before rendering/parsing.
    text = text.replace(/\s*\|\|\s*/g, "|\n|");
    text = text.replace(/([：:])\s+(\|[^\n]+\|\n\|)/g, "$1\n\n$2");
    text = text.replace(/([。；;])\s+(\|[^\n]+\|\n\|)/g, "$1\n\n$2");
    return text;
  }

  function normalizeMarkdownText(text) {
    if (window.ResearchMarkdown?.normalize) {
      return window.ResearchMarkdown.normalize(text);
    }
    return repairCompactMarkdownTables(text)
      .replace(/\r\n?/g, "\n")
      .replace(/[ \t]+(#{2,4}\s+)/g, "\n\n$1")
      .replace(/[ \t]+(---+)[ \t]+(#{2,4}\s+)/g, "\n\n$1\n\n$2")
      .trim();
  }

  function splitMarkdownTableRow(line) {
    return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
  }

  function tableCellText(value) {
    return String(value ?? "")
      .replace(/<br\s*\/?>/gi, "")
      .replace(/\u00a0/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function tableCellHtml(value) {
    const text = tableCellText(value);
    const attrs = text ? ` title="${escapeHtml(text)}"` : "";
    const className = text.length >= 48 ? ` class="rsw-cell-long"` : "";
    return `<td${className}${attrs}>${escapeHtml(text)}</td>`;
  }

  function isMarkdownTableSeparator(line) {
    return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line || "");
  }

  function parseMarkdownTables(text) {
    const lines = normalizeMarkdownText(text).split("\n");
    const tables = [];
    let lastHeading = "";
    let inCode = false;

    for (let index = 0; index < lines.length; index += 1) {
      const line = (lines[index] || "").trim();
      if (line.startsWith("```")) {
        inCode = !inCode;
        continue;
      }
      if (inCode) continue;

      const heading = /^(#{1,4})\s+(.+)$/.exec(line);
      if (heading) {
        lastHeading = heading[2].replace(/\*\*/g, "").trim();
        continue;
      }

      if (!line.includes("|") || !isMarkdownTableSeparator(lines[index + 1] || "")) continue;
      const columns = splitMarkdownTableRow(line).map((cell) => cell.replace(/\*\*/g, "").trim());
      index += 2;
      const rows = [];
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        const values = splitMarkdownTableRow(lines[index]);
        const row = {};
        columns.forEach((column, columnIndex) => {
          row[column || `列${columnIndex + 1}`] = (values[columnIndex] || "").replace(/\*\*/g, "").trim();
        });
        rows.push(row);
        index += 1;
      }
      index -= 1;
      if (columns.length && rows.length) {
        tables.push({
          name: lastHeading || `解释表格 ${tables.length + 1}`,
          columns,
          rows,
          kind: "ai_markdown_table",
          source: "AI解释",
        });
      }
    }
    return tables;
  }

  function preferredTableIndex(tables) {
    const names = (tables || []).map((item) => String(item?.name || "").toLowerCase());
    const preferred = names.findIndex((name) => /(table[_ -]?1|table[_ -]?one|baseline|characteristic)/.test(name));
    return preferred >= 0 ? preferred : 0;
  }

  function firstMatchingColumn(columns, candidates) {
    const lowered = new Map((columns || []).map((column) => [String(column).trim().toLowerCase(), column]));
    for (const candidate of candidates) {
      const found = lowered.get(String(candidate).toLowerCase());
      if (found) return found;
    }
    return "";
  }

  function tableOneLikeArtifact(table, artifact, columns) {
    const text = [
      table?.name,
      artifact?.name,
      artifact?.title,
      artifact?.role,
      artifact?.method_family,
      ...(columns || []),
    ].filter(Boolean).join(" ").toLowerCase();
    return /(table[_ -]?1|table[_ -]?one|baseline|characteristic|三线表|基线)/.test(text);
  }

  function parseCollapsedLevelStats(value) {
    const text = tableCellText(value).trim();
    if (!text || !text.includes(";") || !/:\s*/.test(text)) return null;
    const parsed = {};
    const pieces = text.split(/\s*;\s*/).filter(Boolean);
    if (pieces.length < 2) return null;
    for (const piece of pieces) {
      const match = /^\s*([^:]+?)\s*:\s*(.+?)\s*$/.exec(piece);
      if (!match) return null;
      parsed[match[1].trim()] = match[2].trim();
    }
    return Object.keys(parsed).length >= 2 ? parsed : null;
  }

  function expandCollapsedTableOneRows(table, artifact, columns, rows) {
    if (!tableOneLikeArtifact(table, artifact, columns)) return null;
    const variableColumn = firstMatchingColumn(columns, ["Variable", "变量", "variable"]) || columns[0];
    if (!variableColumn) return null;
    const existingLevelColumn = firstMatchingColumn(columns, ["Level", "水平", "level"]);
    const levelColumn = existingLevelColumn || "Level";
    const nextColumns = existingLevelColumn
      ? columns.slice()
      : [variableColumn, levelColumn, ...columns.filter((column) => column !== variableColumn)];
    const sideColumns = nextColumns.filter((column) => {
      const key = String(column).trim().toLowerCase();
      return ![
        String(variableColumn).trim().toLowerCase(),
        String(levelColumn).trim().toLowerCase(),
        "p_value",
        "p value",
        "p.value",
        "p值",
        "missing",
        "缺失",
        "smd",
      ].includes(key);
    });
    if (!sideColumns.length) return null;

    const expandedRows = [];
    let changed = false;
    rows.forEach((row) => {
      const levelText = tableCellText(row[levelColumn]).trim();
      const parsedByColumn = {};
      sideColumns.forEach((column) => {
        const parsed = parseCollapsedLevelStats(row[column]);
        if (parsed) parsedByColumn[column] = parsed;
      });
      const parsedColumns = Object.keys(parsedByColumn);
      if (levelText || !parsedColumns.length) {
        expandedRows.push({ ...row, [levelColumn]: row[levelColumn] || "" });
        return;
      }
      const levels = [];
      parsedColumns.forEach((column) => {
        Object.keys(parsedByColumn[column]).forEach((level) => {
          if (!levels.includes(level)) levels.push(level);
        });
      });
      if (levels.length < 2) {
        expandedRows.push({ ...row, [levelColumn]: row[levelColumn] || "" });
        return;
      }
      changed = true;
      levels.forEach((level, index) => {
        const nextRow = { ...row, [levelColumn]: level };
        nextRow[variableColumn] = index === 0 ? row[variableColumn] : "";
        parsedColumns.forEach((column) => {
          nextRow[column] = parsedByColumn[column][level] || "";
        });
        nextColumns.forEach((column) => {
          if (!sideColumns.includes(column) && column !== variableColumn && column !== levelColumn && index > 0) {
            nextRow[column] = "";
          }
        });
        expandedRows.push(nextRow);
      });
    });
    if (!changed) return null;
    return {
      ...table,
      columns: nextColumns,
      rows: expandedRows,
      source: "规范化 Table 1 预览",
    };
  }

  function normalizeArtifactTable(table, artifact = null) {
    if (!table?.rows?.length) return table;
    const rawColumns = table.columns || Object.keys(table.rows[0] || {});
    const rows = table.rows.map((row) => {
      const cleaned = {};
      rawColumns.forEach((column) => {
        cleaned[column] = tableCellText(row[column]);
      });
      return cleaned;
    });
    const columns = rawColumns.map(tableCellText);

    const expandedTableOne = expandCollapsedTableOneRows(table, artifact, columns, rows);
    if (expandedTableOne) return expandedTableOne;

    const hasGtsummaryShape = ["row_type", "label", "stat_1", "stat_2"].every((column) => rawColumns.includes(column));
    if (hasGtsummaryShape) {
      const statColumns = rawColumns.filter((column) => /^stat_\d+$/.test(column));
      const displayColumns = [
        "变量",
        ...statColumns.map((column, index) => (tableCellText(column).startsWith("stat_") ? `组 ${index + 1}` : tableCellText(column))),
        "P 值",
      ];
      const displayRows = rows
        .filter((row) => tableCellText(row.label || row.var_label || row.variable))
        .map((row) => {
          const isLevel = row.row_type === "level";
          const variable = isLevel ? `  ${row.label}` : (row.var_label || row.label || row.variable);
          const next = { "变量": variable };
          statColumns.forEach((column, index) => {
            next[displayColumns[index + 1]] = row[column] || "";
          });
          next["P 值"] = isLevel ? "" : (row["p.value"] || row.p_value || row.p || "");
          return next;
        });
      return {
        ...table,
        name: artifact?.name || table.name || "Table 1",
        columns: displayColumns,
        rows: displayRows,
        source: "R Table One",
      };
    }

    return {
      ...table,
      columns,
      rows,
    };
  }

  function renderMarkdown(text) {
    if (window.ResearchMarkdown?.render) {
      return window.ResearchMarkdown.render(text);
    }
    const source = normalizeMarkdownText(text);
    return source ? `<p>${escapeHtml(source).replace(/\n/g, "<br>")}</p>` : "<p></p>";
  }

  function renderMessageContent(target, content) {
    if (!target) return;
    target.innerHTML = renderMarkdown(content);
  }

  function createStreamingMessageRenderer(target, options = {}) {
    const render = options.render || renderMessageContent;
    const schedule = options.schedule || ((callback) => setTimeout(callback, 80));
    const cancel = options.cancel || ((timer) => clearTimeout(timer));
    let latest = "";
    let timer = null;

    const commit = () => {
      timer = null;
      render(target, latest);
    };

    return {
      update(value) {
        latest = String(value ?? "");
        if (timer === null) timer = schedule(commit);
      },
      flush(value = latest) {
        latest = String(value ?? "");
        if (timer !== null) {
          cancel(timer);
          timer = null;
        }
        render(target, latest);
      },
      cancel() {
        if (timer !== null) cancel(timer);
        timer = null;
      },
    };
  }

  async function ensureSession() {
    const cached = state.sid || "";
    if (cached) {
      let resumed = false;
      try {
        await requestJson(`/api/session/${encodeURIComponent(cached)}/resume`, { method: "POST" });
        resumed = true;
      } catch {
        state.sid = "";
        resetModelSharingForSession();
      }
      if (resumed) {
        await loadModelSharingPolicy({ preserveDraft: true });
        return cached;
      }
    }
    const payload = await requestJson("/api/session/new", { method: "POST" });
    state.sid = payload.session_id;
    resetModelSharingForSession();
    await loadModelSharingPolicy();
    return state.sid;
  }

  async function loadLocalSession() {
    const payload = await requestJson("/api/local/session");
    if (payload.csrf_token) sessionStorage.setItem("rsw.csrfToken", payload.csrf_token);
    state.sid = payload.current_session_id || "";
    resetModelSharingForSession();
    if (state.sid) await loadModelSharingPolicy().catch(() => {});
  }

  function summaryOf(dataset) {
    return dataset?.profile?.summary || {};
  }

  function sortDatasets(datasets) {
    return [...(datasets || [])].sort((a, b) => {
      const left = Date.parse(a.created_at || "") || 0;
      const right = Date.parse(b.created_at || "") || 0;
      return right - left;
    });
  }

  function firstLikelyGroup(variables) {
    const idPattern = /(^|[_-])(id|uuid|uid|key|code|no|number|record)([_-]|$)|patient_id|subject_id|sample_id|identifier/;
    const rows = Number(state.activeSummary?.rows || state.activeDataset?.profile?.summary?.rows || 0);
    const candidates = (variables || []).map((item) => {
      const type = `${item.type || ""}`.toLowerCase();
      const name = `${item.name || ""}`.toLowerCase();
      const unique = Number(item.unique || item.n_unique || item.n_distinct || 0);
      const missing = Number(item.missing_pct || item.pct_miss || 0);
      const highCardinality = rows > 0 && unique >= Math.max(12, rows * 0.5);
      if (!name || idPattern.test(name) || highCardinality || missing >= 80) return null;
      const categorical = type.includes("factor") || type.includes("character") || type.includes("logical") || (unique >= 2 && unique <= 8);
      if (!categorical) return null;
      let score = 0;
      if (/group|treat|treatment|arm|allocation|intervention|control|exposure/.test(name)) score += 40;
      if (/sex|gender/.test(name)) score += 25;
      if (/case|status|class|category|type/.test(name)) score += 16;
      if (unique >= 2 && unique <= 5) score += 10;
      if (type.includes("character") || type.includes("factor")) score += 6;
      return { name: item.name, score };
    }).filter(Boolean).sort((a, b) => b.score - a.score);
    const best = candidates.find((item) => item.score > 0);
    return best?.name || "待识别";
  }

  function formatNumber(value) {
    if (value === null || value === undefined || value === "") return "--";
    const number = Number(value);
    if (!Number.isFinite(number)) return String(value);
    return new Intl.NumberFormat("zh-CN").format(number);
  }

  function formatMissing(value) {
    if (value === null || value === undefined || value === "") return "--";
    const number = Number(value);
    if (!Number.isFinite(number)) return String(value);
    return `${number.toFixed(number >= 10 ? 1 : 2)}%`;
  }

  function formatBytes(value) {
    const bytes = Number(value || 0);
    if (!Number.isFinite(bytes) || bytes < 0) return "--";
    if (bytes < 1024) return `${formatNumber(bytes)} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let amount = bytes;
    let unit = "B";
    for (const candidate of units) {
      amount /= 1024;
      unit = candidate;
      if (amount < 1024) break;
    }
    return `${amount.toFixed(amount >= 10 ? 1 : 2)} ${unit}`;
  }

  function renderRuntimeSettings() {
    const runtime = state.systemRuntime || {};
    const rRuntime = state.readiness?.r_runtime || {};
    const inventory = state.templateMeta?.inventory || {};
    const dependencies = runtime.dependencies || {};
    const dependencyStates = Array.isArray(dependencies.states) ? dependencies.states : [];
    const dependencyProblems = dependencyStates.filter((item) => item.status !== "ready");
    const hostPlatform = dependencies.platform || {};
    const installedPackages = runtime.r?.installed_packages;
    const storage = state.workspaceStorage || {};
    const values = {
      "rsw-settings-app-version": runtime.app?.version || "--",
      "rsw-settings-r-version": runtime.r?.r_version || rRuntime.r_version || inventory.live_r_version || (rRuntime.available ? "已就绪" : "--"),
      "rsw-settings-platform": [hostPlatform.system, hostPlatform.version, hostPlatform.architecture].filter(Boolean).join(" · ") || "等待本机检测",
      "rsw-settings-python-qt": dependencies.python_version
        ? `Python ${dependencies.python_version} · Qt ${dependencies.qt_version || "未就绪"}`
        : "等待本机检测",
      "rsw-settings-r-resources": Number(runtime.r?.worker_count || rRuntime.worker_count || 0) > 0
        ? `${formatNumber(runtime.r?.worker_count || rRuntime.worker_count)} 个标准工作进程 · ${formatBytes(runtime.r?.memory_budget_bytes || rRuntime.memory_budget_bytes || 0)} 内存预算`
        : "等待本机资源检测",
      "rsw-settings-package-count": Number.isInteger(installedPackages) && installedPackages >= 0
        ? `${formatNumber(installedPackages)} 个已安装包`
        : inventory.live_inventory_ok
          ? `${formatNumber(inventory.live_installed_packages)} 个已安装包`
          : "运行时清单待读取",
      "rsw-settings-runtime-digest": runtime.r?.rscript_bin || rRuntime.rscript_bin || "尚未找到本机 R",
      "rsw-settings-external-readiness": dependencyStates.length
        ? (dependencyProblems.length
          ? `待处理：${dependencyProblems.map((item) => `${item.label}（${item.detail}）`).join("；")}`
          : `Python、Qt 与运行依赖已就绪（${dependencyStates.length} 项）`)
        : "等待本机运行依赖检测",
      "rsw-settings-workspace-usage": storage.active
        ? `${formatBytes(storage.total_bytes)}（数据 ${formatBytes(storage.dataset_bytes)} · 结果 ${formatBytes(storage.result_bytes)}）`
        : "当前工作区无活动数据",
    };
    Object.entries(values).forEach(([id, value]) => {
      const element = $(id);
      if (element) element.textContent = value;
    });
    const currentPath = $("rsw-r-library-current-path");
    if (currentPath) currentPath.textContent = runtime.r?.package_library || "尚未解析";
    if (!state.rLibraryDirty) {
      const mode = runtime.r?.library_mode === "managed" ? "managed" : "user";
      const userChoice = $("rsw-r-library-user");
      const managedChoice = $("rsw-r-library-managed");
      const customPath = $("rsw-r-library-custom-path");
      if (userChoice) userChoice.checked = mode === "user";
      if (managedChoice) managedChoice.checked = mode === "managed";
      if (customPath) {
        customPath.value = runtime.r?.custom_library_root || "";
        customPath.disabled = mode !== "managed";
        customPath.placeholder = mode === "managed"
          ? `留空则使用 ${runtime.r?.managed_library_root || "应用数据目录/r-library"}`
          : "选择“应用 / 项目专属库”后可填写";
      }
      if (runtime.r?.library_settings_warning) {
        setRLibraryStatus(runtime.r.library_settings_warning, "error");
      } else if (runtime.r?.available === false) {
        setRLibraryStatus(`${runtime.r.error || "尚未找到可用的 R"}。安装或修复本机 R 后，点击刷新重新检测。`, "error");
      }
    }
  }

  function selectedRLibraryMode() {
    return qs('input[name="r-library-mode"]:checked')?.value || "user";
  }

  function syncRLibraryControls() {
    const customPath = $("rsw-r-library-custom-path");
    if (!customPath) return;
    customPath.disabled = selectedRLibraryMode() !== "managed";
  }

  function setRLibraryStatus(message, kind = "") {
    const element = $("rsw-r-library-status");
    if (!element) return;
    element.textContent = message;
    element.className = `rsw-ai-key-status ${kind ? `is-${kind}` : ""}`.trim();
  }

  async function saveRLibrarySettings() {
    const mode = selectedRLibraryMode();
    const customDirectory = mode === "managed"
      ? ($("rsw-r-library-custom-path")?.value || "").trim()
      : "";
    setRLibraryStatus("正在验证并切换 R 包目录...", "busy");
    const payload = await requestJson("/api/system/r-library", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, custom_directory: customDirectory }),
    });
    state.rLibraryDirty = false;
    state.templatesLoaded = false;
    state.systemRuntime = {
      ...(state.systemRuntime || {}),
      r: { ...(state.systemRuntime?.r || {}), ...(payload.r || {}) },
    };
    renderRuntimeSettings();
    setRLibraryStatus("R 包目录已切换；后续缺失包会写入这里。", "ok");
    await loadRuntimeSettings();
  }

  async function loadRuntimeSettings() {
    const requests = [
      requestJson("/api/system/runtime").then((payload) => {
        state.systemRuntime = payload;
        renderRuntimeSettings();
      }),
      loadReadiness().then(() => renderRuntimeSettings()),
      loadAnalysisTemplates().then(() => renderRuntimeSettings()),
    ];
    if (state.sid) {
      requests.push(
        requestJson(`/api/session/${encodeURIComponent(state.sid)}/research/storage`).then((payload) => {
          state.workspaceStorage = payload.storage || {};
          renderRuntimeSettings();
        })
      );
    }
    const results = await Promise.allSettled(requests);
    renderRuntimeSettings();
    if (results.every((result) => result.status === "rejected")) {
      throw results[0].reason;
    }
  }

  function setAiKeyStatus(message, kind = "") {
    const el = $("rsw-ai-key-status");
    if (!el) return;
    el.textContent = message;
    el.className = `rsw-ai-key-status ${kind ? `is-${kind}` : ""}`.trim();
  }

  function openAiConfigDialog() {
    const modal = $("rsw-ai-modal");
    const input = $("rsw-ai-key-input");
    if (!modal) return;
    rememberModalFocus();
    const current = $("rsw-ai-key-current");
    if (current) {
      current.textContent = state.aiKeyInvalid
        ? "本机保存的 DeepSeek API Key 已被服务端判定无效，请输入新的 Key。"
        : isAiConfigured()
        ? "本机已保存 DeepSeek API Key。输入新 Key 可覆盖更新。"
        : "本机尚未配置 DeepSeek API Key。";
      current.className = `rsw-ai-key-status ${isAiConfigured() ? "is-ok" : "is-error"}`;
    }
    setAiKeyStatus("");
    modal.hidden = false;
    document.body.classList.add("rsw-modal-open");
    setTimeout(() => input?.focus(), 30);
    renderRuntimeSettings();
    loadRuntimeSettings().catch((error) => {
      toast(`运行环境信息刷新失败：${error.message}`, "error");
    });
    renderModelSharingControls();
    return (state.sid ? loadModelSharingPolicy({ preserveDraft: true }) : ensureSession()).catch((error) => {
      setModelSharingStatus(`外发设置读取失败：${error.message}`, "error");
      renderModelSharingControls();
    });
  }

  function closeAiConfigDialog() {
    const modal = $("rsw-ai-modal");
    if (!modal) return;
    modal.hidden = true;
    document.body.classList.remove("rsw-modal-open");
    const input = $("rsw-ai-key-input");
    if (input) input.value = "";
    restoreModalFocus();
  }

  function requiredDeepSeekKey() {
    const input = $("rsw-ai-key-input");
    const apiKey = (input?.value || "").trim();
    if (!apiKey) {
      setAiKeyStatus("请先输入 DeepSeek API Key。", "error");
      input?.focus();
      return "";
    }
    return apiKey;
  }

  async function testDeepSeekKey() {
    const apiKey = requiredDeepSeekKey();
    if (!apiKey) return false;
    setAiKeyStatus("正在测试 DeepSeek 连接...", "busy");
    const payload = await requestJson("/api/models/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: "deepseek", api_key: apiKey }),
    });
    if (!payload.success) {
      setAiKeyStatus(payload.message || "连接测试失败。", "error");
      return false;
    }
    setAiKeyStatus("连接测试通过，可以保存启用。", "ok");
    return true;
  }

  async function saveDeepSeekKey() {
    const apiKey = requiredDeepSeekKey();
    if (!apiKey) return;
    setAiKeyStatus("正在保存并启用 AI 分析助手...", "busy");
    await requestJson("/api/models/set-builtin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: "deepseek", api_key: apiKey }),
    });
    state.aiKeyInvalid = false;
    await loadReadiness();
    closeAiConfigDialog();
    setStatus("ok", "AI 分析助手已启用", "现在可以运行 DeepSeek AI 分析。");
    toast("DeepSeek API Key 已保存到本机", "ok");
  }

  async function clearDeepSeekKey() {
    setAiKeyStatus("正在清除本机 API Key...", "busy");
    await requestJson("/api/models/clear-builtin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: "deepseek" }),
    });
    state.aiKeyInvalid = false;
    await loadReadiness();
    const input = $("rsw-ai-key-input");
    if (input) input.value = "";
    setAiKeyStatus("本机 API Key 已清除。", "ok");
    setStatus("warning", "AI 模型待配置", "输入 DeepSeek API Key 即可启用 AI 分析助手。", "configure-ai");
  }

  function renderResultStatus(variables = []) {
    const badge = $("rsw-result-state");
    if (!badge) return;
    const view = resultViewState();
    badge.textContent = view.label;
    badge.className = `rsw-result-state is-${view.kind || (variables.length || state.activeDataset ? "ready" : "idle")}`;
  }

  function formatEvidencePayload(payload, sourceLabel) {
    const blocks = [
      `来源：${sourceLabel}`,
      `状态：${payload?.ok === false ? "异常" : "已记录"}`,
    ];
    if (payload?.job_id) {
      blocks.push(`Job ID：${payload.job_id}`);
    }
    if (payload?.packages) {
      blocks.push(`R 包：${payload.packages}`);
    }
    if (payload?.error) {
      blocks.push("", "错误：", String(payload.error));
    }
    const warnings = visibleWarnings(payload?.warnings || []);
    if (warnings.length) {
      blocks.push("", "警告：", warnings.map((item) => `- ${item}`).join("\n"));
    }
    if (payload?.tables || payload?.figures || payload?.texts || payload?.models) {
      blocks.push(
        "",
        "产物：",
        `- 表格：${formatNumber(payload.tables?.length || 0)}`,
        `- 图形：${formatNumber(payload.figures?.length || 0)}`,
        `- 模型：${formatNumber(payload.models?.length || 0)}`,
        `- 文本：${formatNumber(payload.texts?.length || 0)}`
      );
    }
    blocks.push("", "R 代码：", payload?.code || "暂无可展示代码");
    if (payload?.stdout) blocks.push("", "标准输出：", payload.stdout);
    if (payload?.stderr) blocks.push("", "错误输出：", payload.stderr);
    return blocks.join("\n");
  }

  function renderReadiness() {
    const top = $("rsw-top-readiness");
    const readiness = state.readiness;
    const renderTopReadiness = (label, warning = false) => {
      if (!top) return;
      top.innerHTML = `
        <svg class="rsw-side-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h9" /><path d="M17 7h3" /><path d="M15 5v4" /><path d="M4 17h3" /><path d="M11 17h9" /><path d="M9 15v4" /></svg>
        <span>${escapeHtml(label)}</span>`;
      top.classList.toggle("is-warning", warning);
    };

    if (!readiness) {
      renderTopReadiness("检查中");
      return;
    }

    const capabilities = readiness.capabilities || [];
    const ai = capabilities.find((item) => item.id === "ai");
    const rExecute = capabilities.find((item) => item.id === "r_execute");
    const inspect = capabilities.find((item) => item.id === "inspect");
    const blockers = [rExecute, inspect, ai].filter((item) => item?.status === "needs_setup");
    const headline = blockers.length
      ? (blockers.some((item) => item?.id === "ai") && blockers.length === 1 ? "AI 模型待配置" : "环境待处理")
      : "可分析";

    renderTopReadiness(ai?.status === "needs_setup" ? "AI 模型待配置" : headline, ai?.status === "needs_setup");
  }

  function renderStatus() {
    const board = $("rsw-status-board");
    const title = $("rsw-status-title");
    const detail = $("rsw-status-detail");
    const action = $("rsw-status-action");
    if (!board || !title || !detail) return;
    const kind = state.status?.kind || "idle";
    board.classList.toggle("is-busy", kind === "busy");
    board.classList.toggle("is-error", kind === "error");
    board.classList.toggle("is-warning", kind === "warning");
    board.classList.toggle("is-ok", kind === "ok");
    title.textContent = state.status?.title || "工作台就绪";
    detail.textContent = state.status?.detail || "等待上传科研数据。";
    if (action) {
      action.hidden = !state.status?.action;
      action.textContent = statusActionLabel(state.status?.action);
    }
  }

  function analysisOutputLabel(output) {
    if (typeof output === "string") return output;
    if (!output || typeof output !== "object") return "";
    return output.label || output.role || output.kind || "";
  }

  function renderAnalysisPlan() {
    const panel = $("rsw-analysis-plan");
    if (!panel) return;
    const plan = state.activeAnalysisPlan;
    panel.hidden = !plan || Boolean(state.activePreparationPlan);
    if (!plan || state.activePreparationPlan) return;
    const resolvedInputs = Object.values(plan.resolved_inputs || {})
      .map(userFacingResolvedInput)
      .filter(Boolean);
    const bindings = resolvedInputs.length
      ? resolvedInputs
      : Object.entries(plan.variable_bindings || {})
        .filter(([role, variable]) => role && variable)
        .map(([role, variable]) => `${role}=${variable}`);
    const packages = Array.isArray(plan.packages) ? plan.packages.filter(Boolean) : [];
    const outputs = Array.isArray(plan.required_outputs)
      ? plan.required_outputs.map(analysisOutputLabel).filter(Boolean)
      : [];
    const source = plan.source === "template"
      ? `模板计划${plan.template_id ? ` · ${plan.template_id}` : ""}`
      : "AI 对话计划";
    const fields = {
      "rsw-plan-source": source,
      "rsw-plan-resource": resourceLabel(plan.resource_class),
      "rsw-plan-family": familyLabel(plan.method_family),
      "rsw-plan-bindings": bindings.length ? `${bindings.length} 项输入已确认` : "无需额外输入",
      "rsw-plan-packages": packages.length ? `${packages.length} 个候选 R 包` : "实时选择 R 包",
      "rsw-plan-outputs": outputs.length ? `${outputs.length} 项预期产物` : "生成分析产物",
    };
    Object.entries(fields).forEach(([id, value]) => {
      const element = $(id);
      if (element) element.textContent = value;
    });
    const scenario = plan.scenario || plan.intent || "已根据当前数据形成分析计划。";
    panel.title = scenario;
    panel.setAttribute("aria-label", `${source}；${fields["rsw-plan-family"]}；${fields["rsw-plan-bindings"]}；${fields["rsw-plan-packages"]}；${fields["rsw-plan-outputs"]}`);
  }

  function renderPreparationPlan() {
    const panel = $("rsw-preparation-plan");
    if (!panel) return;
    const plan = state.activePreparationPlan;
    panel.hidden = !plan;
    if (!plan) return;
    const target = plan.target || {};
    const actions = Array.isArray(plan.actions) ? plan.actions.filter((item) => item?.title) : [];
    const targetElement = $("rsw-preparation-target");
    const headline = $("rsw-preparation-headline");
    const summary = $("rsw-preparation-summary");
    const steps = $("rsw-preparation-steps");
    if (targetElement) targetElement.textContent = target.title ? `目标 · ${target.title}` : "分析前准备";
    if (headline) headline.textContent = plan.headline || "当前数据需要先准备";
    if (summary) summary.textContent = plan.summary || "按数据预检结果完成准备后，再返回原分析。";
    if (steps) {
      steps.innerHTML = actions.slice(0, 4).map((item, index) => (
        `<span title="${escapeHtml(item.summary || "")}">${index + 1}. ${escapeHtml(item.title)}</span>`
      )).join("");
    }
    panel.title = [
      plan.summary || "",
      ...actions.map((item) => `${item.title}：${item.summary || ""}`),
    ].filter(Boolean).join("\n");
  }

  function preparationPlanMarkdown(plan) {
    const actions = Array.isArray(plan?.actions) ? plan.actions : [];
    const target = plan?.target || {};
    const actionLines = actions.flatMap((action, index) => {
      const lines = [`${index + 1}. **${action.title || "数据准备"}**：${action.summary || "按预检要求完成处理。"}`];
      if (action.required_inputs?.length) lines.push(`   - 所需输入：${action.required_inputs.join("、")}`);
      if (action.r_packages?.length) lines.push(`   - 可用 R 包：${action.r_packages.join("、")}`);
      if (action.external_components?.length) lines.push(`   - 本机组件：${action.external_components.join("、")}`);
      if (action.expected_outputs?.length) lines.push(`   - 处理后应得到：${action.expected_outputs.join("、")}`);
      return lines;
    });
    return [
      `## ${plan?.headline || "分析前数据准备"}`,
      "",
      plan?.summary || "当前数据需先完成准备，再执行目标分析。",
      "",
      ...(target.accepted_formats?.length
        ? [`- 目标接受格式：${target.accepted_formats.join("、")}`]
        : []),
      ...(target.preconditions?.length
        ? [`- 方法前置条件：${target.preconditions.join("；")}`]
        : []),
      ...(target.critical_inputs?.length
        ? [`- 关键分析输入：${target.critical_inputs.join("、")}`]
        : []),
      ...(plan?.alternative_templates?.length
        ? [
          `- 适合当前数据的替代模板：${plan.alternative_templates.map((item) => `${item.title}（${item.id}）`).join("、")}`,
        ]
        : []),
      "",
      ...actionLines,
      "",
      plan?.status === "compatibility_guidance"
        ? "如果当前数据确属目标方法，请保留原始数据和处理记录，完成后导入处理后的文件/文件夹；如果它是其他类型的研究表，请直接选择上方推荐的替代模板，不要强行转换科学含义。"
        : "处理时保留原始数据和处理记录；完成后使用上方“导入处理后文件/文件夹”，系统会重新预检并恢复原模板或原分析需求。",
    ].join("\n");
  }

  function renderRunControls() {
    const stopButton = $("rsw-stop-run");
    const sendButton = qs('.rsw-composer button[type="submit"]');
    const runPlan = $("rsw-run-plan-btn");
    const requestBusy = state.streaming || state.inspecting || state.chatStarting;
    if (stopButton) {
      stopButton.hidden = !(state.streaming || state.inspecting);
      stopButton.disabled = state.canceling;
      stopButton.textContent = state.canceling ? "正在终止" : "停止分析";
      stopButton.classList.toggle("rsw-primary-btn", Boolean(state.streaming || state.inspecting));
    }
    if (runPlan) {
      const canRunPlan = Boolean(state.activeAnalysisPlan) && !requestBusy;
      runPlan.hidden = !canRunPlan || Boolean(state.streaming || state.inspecting);
      runPlan.disabled = requestBusy;
    }
    if (sendButton) {
      sendButton.hidden = requestBusy || (Boolean(state.activeAnalysisPlan) && !state.streaming);
      sendButton.disabled = requestBusy;
    }
    qsa("[data-continue-analysis]").forEach((button) => {
      button.disabled = requestBusy || state.canceling;
    });
    renderModelSharingControls();
  }

  function renderTemplateList(title, values, limit = 8) {
    const items = Array.isArray(values) ? values.filter(Boolean).slice(0, limit) : [];
    if (!items.length) return "";
    return `
      <section>
        <h4>${escapeHtml(title)}</h4>
        <ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
      </section>`;
  }

  function renderPreparationQuestions(item, preparation) {
    const questions = Array.isArray(preparation?.questions) ? preparation.questions : [];
    const controls = Array.isArray(preparation?.input_controls) && preparation.input_controls.length
      ? preparation.input_controls.filter((control) => control?.editable !== false)
      : questions;
    if (!controls.length) return "";
    const values = templateInputValues(item.id);
    const unresolvedIds = new Set(questions.map((question) => question.input_id));
    const renderControl = (control) => {
      const candidates = Array.isArray(control.candidates) ? control.candidates : [];
      const hasOverride = Object.prototype.hasOwnProperty.call(values, control.input_id);
      const current = hasOverride ? values[control.input_id] : (control.value ?? control.default ?? "");
      const requiredAttribute = control.required ? "true" : "false";
      if (candidates.length && ["column", "level", "specification"].includes(control.kind)) {
        return `
          <select data-template-input-id="${escapeHtml(control.input_id)}" data-template-input-kind="${escapeHtml(control.kind)}" data-template-input-required="${requiredAttribute}">
            <option value="">${control.required ? "请选择" : "不指定（自动处理）"}</option>
            ${candidates.map((candidate) => `<option value="${escapeHtml(candidate.value)}" ${String(current) === String(candidate.value) ? "selected" : ""}>${escapeHtml(candidate.label || candidate.value)}</option>`).join("")}
          </select>`;
      }
      if (control.kind === "level" && !candidates.length) {
        return `<select disabled data-template-input-kind="level"><option>所选变量没有可验证的本地水平</option></select>`;
      }
      if (control.kind === "columns") {
        const selected = new Set(Array.isArray(current) ? current : []);
        return `
          <select multiple size="${Math.min(8, Math.max(3, candidates.length))}" data-template-input-id="${escapeHtml(control.input_id)}" data-template-input-kind="columns" data-template-input-required="${requiredAttribute}">
            ${candidates.map((candidate) => `<option value="${escapeHtml(candidate.value)}" ${selected.has(candidate.value) ? "selected" : ""}>${escapeHtml(candidate.label || candidate.value)}</option>`).join("")}
          </select>`;
      }
      const inputType = control.kind === "parameter" && typeof current === "number" ? "number" : "text";
      const displayValue = Array.isArray(current) ? JSON.stringify(current) : current;
      return `<input type="${inputType}" value="${escapeHtml(displayValue)}" data-template-input-id="${escapeHtml(control.input_id)}" data-template-input-kind="${escapeHtml(control.kind)}" data-template-input-required="${requiredAttribute}" placeholder="请输入${escapeHtml(control.label)}">`;
    };
    return `
      <section class="rsw-template-bindings rsw-template-preparation-questions">
        <h4>分析输入（可按研究问题修改）</h4>
        <p>模板固定统计方法与输出规范，不固定你的研究角色。修改任一项后，系统会重新校验并冻结一份新计划。</p>
        ${controls.map((control) => {
          const hasOverride = Object.prototype.hasOwnProperty.call(values, control.input_id);
          const provenance = hasOverride
            ? "用户修改"
            : ({ inferred: "本地建议", default: "模板默认", memory: "历史建议", dataset: "当前数据" }[control.provenance] || "待确认");
          return `
          <label>
            <span>${escapeHtml(control.label)} · ${control.required ? "必需" : "可选"} · ${escapeHtml(provenance)}${unresolvedIds.has(control.input_id) ? " · 待确认" : ""}</span>
            ${renderControl(control)}
            ${hasOverride ? `<button type="button" class="rsw-template-input-reset" data-template-input-reset="${escapeHtml(control.input_id)}">恢复本地建议</button>` : ""}
            ${control.description ? `<small>${escapeHtml(control.description)}</small>` : ""}
          </label>`;
        }).join("")}
      </section>`;
  }

  function renderInlinePlanInputs() {
    const panel = $("rsw-plan-inputs");
    if (!panel) return;
    const templateId = state.pendingTemplateId || "";
    const item = templateId ? templateById(templateId) : null;
    const preparation = templateId ? templatePreparation(templateId) : null;
    const needsInput = Boolean(item && preparation?.status === "needs_input");
    panel.hidden = !needsInput;
    if (!needsInput) {
      panel.innerHTML = "";
      return;
    }
    panel.innerHTML = `
      <strong>在计划条内确认关键输入</strong>
      ${renderPreparationQuestions(item, preparation)}
      <p>确认后即可按此计划运行，不必回到模板库。</p>`;
  }

  function renderTemplateDetail(item) {
    const detail = $("rsw-template-detail");
    if (!detail) return;
    if (!item) {
      detail.innerHTML = `<div class="rsw-template-detail-empty">选择一个模板查看适用条件、输出合同和执行约束。</div>`;
      return;
    }
    const contract = item.template_contract || {};
    const applicability = contract.applicability || {};
    const methodology = contract.methodology || {};
    const output = contract.output_contract || {};
    const input = item.input_contract || {};
    const preparation = templatePreparation(item.id);
    const dependencyGate = preparation?.dependency_gate || null;
    const packageNames = Array.isArray(item.package_names) ? item.package_names : [];
    const formats = Array.isArray(input.formats) ? input.formats.map((value) => value.toUpperCase()) : [];
    const requiresNumericMatrix = Array.isArray(input.inputs) && input.inputs.some((spec) => (
      spec?.kind === "object" && Array.isArray(spec.accepts) && spec.accepts.includes("numeric_matrix")
    ));
    const recognizedVariables = variableNames(1000).length;
    const loading = state.templatePrepareLoading === item.id;
    const preparationStatus = !state.activeDataset
      ? "上传数据后自动准备"
      : loading
      ? "正在自动准备"
      : preparation?.status_label || "等待自动准备";
    const compatibilityGuidance = preparation?.compatibility_guidance || null;
    const alternativeTemplates = Array.isArray(compatibilityGuidance?.alternative_templates)
      ? compatibilityGuidance.alternative_templates
      : [];
    const mappingGuidance = !state.activeDataset
      ? "上传一个数据文件后，系统会在本地自动识别结构并准备此模板。"
      : loading
      ? `正在使用当前数据画像（${recognizedVariables} 个变量）解析此模板。`
      : preparation?.status === "ready"
      ? `可直接使用：已自动解析 ${preparation.resolved_inputs?.length || 0} 个输入并冻结分析计划。`
      : preparation?.status === "needs_input"
      ? `需确认关键项：仍有 ${preparation.questions?.length || 0} 项科学含义无法可靠推断。`
      : preparation?.status === "not_applicable"
      ? `当前数据不适用：${preparation.reason || "数据结构不满足模板要求。"}${compatibilityGuidance ? " 可查看准备方法或改用适合当前数据的模板。" : ""}`
      : preparation?.status === "needs_preparation"
      ? `需先准备：${preparation.preparation_plan?.summary || preparation.reason || "完成上游处理后可返回此模板。"}`
      : `当前数据已自动理解（${recognizedVariables} 个变量）；点击下方按钮开始自动准备。`;
    const applicableData = [
      ...(Array.isArray(applicability.data_types) ? applicability.data_types : []),
      input.shape === "r_object"
        ? "RDS 或原生科学对象"
        : requiresNumericMatrix
        ? "科学特征矩阵：样本为行、特征为列；其他方向需先整理"
        : "单个表格数据",
      formats.length ? `格式：${formats.join(" / ")}` : "",
    ].filter(Boolean);
    const unanswered = unansweredPreparationQuestions(item, preparation);
    const canApply = Boolean(
      state.activeDataset
      && !loading
      && (preparation?.status !== "not_applicable" || compatibilityGuidance)
      && (preparation?.status !== "needs_input" || unanswered.length === 0)
    );
    const buttonLabel = loading
      ? "准备中 · 点击后自动使用"
      : dependencyGateNeedsAction(dependencyGate)
      ? "处理本机依赖后使用"
      : preparation?.status === "ready"
      ? "使用此模板"
      : preparation?.status === "needs_input"
      ? (unanswered.length ? `请确认 ${unanswered.length} 个关键项` : "确认并使用此模板")
      : preparation?.status === "not_applicable"
      ? (compatibilityGuidance ? "查看适用条件与替代方案" : "当前数据不适用")
      : preparation?.status === "needs_preparation"
      ? "查看数据准备方案"
      : "自动准备此模板";
    detail.innerHTML = `
      <div class="rsw-template-detail-head">
        <span>${escapeHtml(item.category)} · ${escapeHtml(templateWorkflow(item))} · ${escapeHtml(item.id)}</span>
        <h3>${escapeHtml(item.title)}</h3>
        <p>${escapeHtml(item.scenario || item.subtitle || "")}</p>
      </div>
      <div class="rsw-template-mapping-guidance is-required"><strong>${escapeHtml(item.method_validation?.label || "实现验证待补")}</strong> · ${escapeHtml(item.method_validation?.scope || "产物完整不代表统计方法已验证。")}${item.method_validation?.note ? `<br>${escapeHtml(item.method_validation.note)}` : ""}</div>
      <div class="rsw-template-mapping-guidance ${["needs_input", "needs_preparation", "not_applicable"].includes(preparation?.status) ? "is-required" : "is-ready"}"><strong>${escapeHtml(preparationStatus)}</strong> · ${escapeHtml(mappingGuidance)}</div>
      ${Array.isArray(preparation?.warnings) && preparation.warnings.length ? `<div class="rsw-template-mapping-guidance is-required"><strong>标签待确认</strong> · ${escapeHtml(preparation.warnings.join("；"))}</div>` : ""}
      ${dependencyGateNeedsAction(dependencyGate) ? `<div class="rsw-template-mapping-guidance is-required"><strong>${escapeHtml(dependencyGate.headline || "本机依赖待处理")}</strong> · ${escapeHtml(dependencyGate.summary || "确认处理后可恢复此模板计划。")}</div>` : ""}
      ${renderPreparationQuestions(item, preparation)}
      ${alternativeTemplates.length ? `
        <section class="rsw-template-alternatives">
          <h4>适合当前数据的替代模板</h4>
          <p>如果当前数据不是目标实验数据，不需要强行转换；可以直接选择：</p>
          <div>${alternativeTemplates.map((candidate) => `
            <button type="button" data-template-alternative="${escapeHtml(candidate.id)}" title="${escapeHtml(candidate.reason || "")}">${escapeHtml(candidate.title)}</button>
          `).join("")}</div>
        </section>
      ` : ""}
      <div class="rsw-template-detail-top-actions">
        <button type="button" class="${loading ? "is-loading" : ""}" aria-busy="${loading ? "true" : "false"}" data-template-apply="${escapeHtml(item.id)}" ${canApply || (!preparation && state.activeDataset) ? "" : "disabled"}>${escapeHtml(buttonLabel)}</button>
      </div>
      <div class="rsw-template-detail-badges">
        <strong class="is-curated">${escapeHtml(familyLabel(item.family))}</strong>
        <strong>${escapeHtml(resourceLabel(item.resource_class))}</strong>
        <strong>${formatNumber(packageNames.length)} 个候选 R 包</strong>
      </div>
      ${renderTemplateList("适用数据", applicableData, 8)}
      <section>
        <h4>方法说明</h4>
        <p>${escapeHtml(item.prompt || item.scenario || "根据数据结构执行标准统计流程。")}</p>
      </section>
      <div class="rsw-template-contract-grid">
        <section>
          <h4>资源等级</h4>
          <p>${escapeHtml(resourceLabel(item.resource_class))}${item.resource_class === "extended" ? " · 允许较长执行时间" : " · 常规本地执行"}</p>
        </section>
        <section>
          <h4>候选 R 包</h4>
          <p>${escapeHtml(packageNames.join("、") || "按用户本机 R 的实时能力选择")}</p>
        </section>
      </div>
      ${renderTemplateList("前置条件", applicability.prerequisites, 6)}
      ${renderTemplateList("方法假设", methodology.assumptions, 8)}
      ${renderTemplateList("诊断要求", methodology.diagnostics, 6)}
      ${renderTemplateList("可能需要的本机组件", item.external_setup_requirements, 6)}
      ${dependencyGate ? renderTemplateList(
        "本机依赖预检",
        [
          ...(dependencyGate.selected_packages || []).map((name) => `R 包：${name}`),
          ...(dependencyGate.external_dependencies || []).filter((entry) => entry.status !== "ready").map((entry) => `${entry.label || entry.dependency_id}：${dependencyStatusLabel(entry.status)}`),
        ],
        8
      ) : ""}
      ${renderTemplateList("预期产物", output.required_outputs, 12)}
      ${renderTemplateList("可替代包", contract.fallback_packages, 8)}`;
  }

  function groupTemplatesByFamily(templates) {
    const groups = new Map();
    templates.forEach((item) => {
      const family = item.family || "general_analysis";
      if (!groups.has(family)) groups.set(family, []);
      groups.get(family).push(item);
    });
    return Array.from(groups, ([family, items]) => ({ family, items }));
  }

  function renderTemplateLibrary() {
    const modal = $("rsw-template-modal");
    if (!modal || modal.hidden) return;
    const categoryNav = $("rsw-template-categories");
    const workflowNav = $("rsw-template-workflows");
    const cardGrid = $("rsw-template-cards");
    const count = $("rsw-template-count");
    const search = $("rsw-template-search");
    if (search && search.value !== state.templateSearch) {
      search.value = state.templateSearch;
    }
    if (categoryNav) {
      categoryNav.innerHTML = templateCategories().map((category) => {
        const total = category === "all"
          ? userTemplates().length
          : userTemplates().filter((item) => item.category === category).length;
        return `
          <button type="button" class="${state.templateCategory === category ? "is-active" : ""}" data-template-category="${escapeHtml(category)}">
            <span>${escapeHtml(categoryLabel(category))}</span>
            <small>${formatNumber(total)}</small>
          </button>`;
      }).join("");
    }
    if (workflowNav) {
      workflowNav.innerHTML = templateWorkflows().map((workflow) => {
        const total = userTemplates().filter((item) => categoryMatches(item) && workflowMatches(item, workflow)).length;
        return `
          <button type="button" class="${state.templateWorkflow === workflow ? "is-active" : ""}" data-template-workflow="${escapeHtml(workflow)}">
            <span>${escapeHtml(workflowLabel(workflow))}</span>
            <small>${formatNumber(total)}</small>
          </button>`;
      }).join("");
    }
    const templates = filteredTemplates();
    if (count) {
      const inventory = state.templateMeta?.inventory;
      const inventoryNote = inventory?.live_inventory_ok && inventory?.live_installed_packages
        ? ` · 运行包 ${formatNumber(inventory.live_installed_packages)}`
        : inventory?.declared_research_packages
        ? ` · 规划包 ${formatNumber(inventory.declared_research_packages)}`
        : "";
      const loadingNote = state.templatesLoading
        ? (state.templatesLoaded ? " · 正在更新" : " · 正在加载完整模板库")
        : state.templateLoadError
        ? " · 使用本地基础模板"
        : "";
      count.textContent = `${formatNumber(templates.length)} / ${formatNumber(userTemplates().length)} 个模板${inventoryNote}${loadingNote}`;
    }
    if (!cardGrid) return;
    if (!templates.length) {
      cardGrid.innerHTML = `<div class="rsw-template-empty">没有匹配模板。换一个关键词，或切回全部能力。</div>`;
      renderTemplateDetail(null);
      return;
    }
    if (!templates.some((item) => item.id === state.templateSelectedId)) {
      state.templateSelectedId = templates[0]?.id || "";
    }
    cardGrid.innerHTML = groupTemplatesByFamily(templates).map(({ family, items }) => `
      <section class="rsw-template-family-group">
        <header>
          <div>
            <span>方法族</span>
            <h3>${escapeHtml(familyLabel(family))}</h3>
          </div>
          <small>${formatNumber(items.length)} 个场景</small>
        </header>
        <div class="rsw-template-family-cards">
          ${items.map((item) => {
            const preparation = templatePreparation(item.id);
            const roleStatus = !state.activeDataset
              ? "上传后自动准备"
              : state.templatePrepareLoading === item.id
              ? "正在自动准备"
              : preparation?.status_label || "自动识别输入";
            const packageCount = Array.isArray(item.package_names) ? item.package_names.length : 0;
            return `
              <article class="rsw-template-card ${state.templateSelectedId === item.id ? "is-selected" : ""}" data-template-select="${escapeHtml(item.id)}" role="button" tabindex="0" aria-pressed="${state.templateSelectedId === item.id ? "true" : "false"}">
                <div class="rsw-template-card-main">
                  <span>${escapeHtml(item.id)}</span>
                  <h3>${escapeHtml(item.title)}</h3>
                  <p>${escapeHtml(item.scenario || item.subtitle || "")}</p>
                </div>
                <div class="rsw-template-badges">
                  <strong>${escapeHtml(resourceLabel(item.resource_class))}</strong>
                  <strong>${escapeHtml(roleStatus)}</strong>
                  <strong>${escapeHtml(item.method_validation?.label || "实现验证待补")}</strong>
                  <strong>${formatNumber(packageCount)} 包</strong>
                </div>
                <span class="rsw-template-card-action">${state.templateSelectedId === item.id ? "当前选择" : "查看并使用"}<b aria-hidden="true">→</b></span>
              </article>`;
          }).join("")}
        </div>
      </section>`).join("");
    renderTemplateDetail(templateById(state.templateSelectedId) || templates[0]);
  }

  function scheduleTemplateLibraryRender() {
    const modal = $("rsw-template-modal");
    if (!modal || modal.hidden) return;
    const render = () => renderTemplateLibrary();
    if (typeof window.requestAnimationFrame === "function") {
      window.requestAnimationFrame(render);
    } else {
      setTimeout(render, 0);
    }
  }

  function tableToCsv(table) {
    const columns = table?.columns || Object.keys(table?.rows?.[0] || {});
    const rows = table?.rows || [];
    return [
      columns.map(csvQuote).join(","),
      ...rows.map((row) => columns.map((column) => csvQuote(row[column])).join(",")),
    ].join("\n");
  }

  function safeSpreadsheetCell(value) {
    const text = String(value ?? "");
    const numeric = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(text.trim());
    if (numeric) return text;
    const formulaLike = /^[=+\-@\t\r]/.test(text) || /^\s+[=+\-@]/.test(text);
    return formulaLike ? `'${text}` : text;
  }

  function csvQuote(value) {
    return `"${safeSpreadsheetCell(value).replaceAll('"', '""')}"`;
  }

  function currentRenderedTable() {
    const columns = qsa("#rsw-variable-table thead th").map((cell) => cell.textContent.trim());
    const rows = qsa("#rsw-variable-table tbody tr").map((tr) => {
      const row = {};
      qsa("td", tr).forEach((cell, index) => {
        row[columns[index] || `col_${index + 1}`] = cell.textContent.trim();
      });
      return row;
    }).filter((row) => Object.keys(row).length);
    return { columns, rows };
  }

  function tableToWordHtml(table, title = "科研统计表格") {
    const columns = table?.columns || Object.keys(table?.rows?.[0] || {});
    const rows = table?.rows || [];
    return `<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body { font-family: Arial, 'Microsoft YaHei', sans-serif; color: #1f2937; }
    h1 { font-size: 18pt; margin: 0 0 14pt; }
    table { border-collapse: collapse; width: 100%; font-size: 10pt; }
    th, td { border: 1px solid #cbd5e1; padding: 6pt 7pt; text-align: left; }
    th { background: #f1f5f9; font-weight: 700; }
  </style>
</head>
<body>
  <h1>${escapeHtml(title)}</h1>
  <table>
    <thead><tr>${columns.map((column) => `<th>${escapeHtml(column)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((row) => `<tr>${columns.map((column) => `<td>${escapeHtml(row[column] ?? "")}</td>`).join("")}</tr>`).join("")}</tbody>
  </table>
</body>
</html>`;
  }

  function selectedTableArtifact() {
    const tables = resultArtifacts("tables");
    return state.selectedTableIndex < tables.length ? tables[state.selectedTableIndex] : null;
  }

  function tableArtifactSupportsFormalExport(artifact) {
    return [".csv", ".tsv"].includes(artifactSuffix(artifact));
  }

  function selectedDerivedTable() {
    const artifactCount = resultCount("tables");
    const index = state.selectedTableIndex - artifactCount;
    return derivedTables()[index] || (artifactCount ? null : derivedTables()[0]) || null;
  }

  function selectedFigureArtifact() {
    const figures = resultArtifacts("figures");
    return figures[state.selectedFigureIndex] || figures[0] || null;
  }

  function artifactSuffix(artifact) {
    const path = String(artifact?.path || artifact?.name || "").toLowerCase();
    const match = /\.([a-z0-9]+)(?:$|[?#])/.exec(path);
    return match ? `.${match[1]}` : "";
  }

  function isBrowserPreviewableFigure(artifact) {
    if (artifact?.kind !== "figure" || !artifact?.path) return false;
    const suffix = artifactSuffix(artifact);
    const mimeType = String(artifact?.mime_type || "").toLowerCase();
    return [".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif"].includes(suffix)
      || ["image/png", "image/jpeg", "image/gif", "image/webp", "image/avif"].includes(mimeType);
  }

  function artifactMetadata(artifact) {
    return artifact?.metadata && typeof artifact.metadata === "object" ? artifact.metadata : {};
  }

  function artifactReadableName(artifact, fallback = "科研产物") {
    const metadata = artifactMetadata(artifact);
    const title = metadata.title || metadata.label || metadata.caption || artifact?.name || fallback;
    return String(title || fallback)
      .replace(/\.[a-z0-9]+$/i, "")
      .replaceAll("_", " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function normalizedArtifactSearchText(artifact) {
    const metadata = artifactMetadata(artifact);
    return [
      artifact?.name,
      artifact?.path,
      artifact?.kind,
      metadata.role,
      metadata.method_family,
      metadata.method,
      metadata.title,
      metadata.caption,
      metadata.outcome,
      metadata.exposure,
      metadata.formula,
    ].filter(Boolean).join(" ").toLowerCase();
  }

  function inferMethodFamily(artifact) {
    const metadata = artifactMetadata(artifact);
    if (metadata.method_family) return String(metadata.method_family);
    const text = normalizedArtifactSearchText(artifact);
    if (/cox|surv|kaplan|km|hazard|frailty|competing|fine.?gray/.test(text)) return "生存/预后";
    if (/logistic|glm|odds|binary|binomial/.test(text)) return "广义线性模型";
    if (/linear|lm_|ols|regression|anova|ancova/.test(text)) return "线性模型";
    if (/roc|auc|calibration|decision|dca|prediction|pred_prob/.test(text)) return "预测/诊断";
    if (/propensity|iptw|matching|causal|did|evalue/.test(text)) return "因果推断";
    if (/meta|forest|heterogeneity|i2|network/.test(text)) return "证据合成";
    if (/pca|umap|volcano|heatmap|pathway|gsea|omics|rna|single|microbiome/.test(text)) return "组学/生信";
    if (/map|spatial|gis|raster|geo/.test(text)) return "空间/地理";
    return metadata.method ? String(metadata.method) : "通用统计";
  }

  function roleFromRules(kind, artifact) {
    const metadata = artifactMetadata(artifact);
    const explicit = String(metadata.role || metadata.output_role || "").trim().toLowerCase();
    const text = normalizedArtifactSearchText(artifact);
    const tableRoles = [
      ["baseline", "基线/描述", 10, /table.?1|baseline|demographic|characteristic|descriptive|summary|描述|基线/],
      ["primary", "主结果", 20, /primary|main|effect|estimate|contrast|difference|association|主分析|主结果/],
      ["model", "模型结果", 30, /model|coefficient|coefficients|cox|glm|logistic|linear|regression|or_|hr_|rr_|beta|fit_stats|模型|回归/],
      ["diagnostic", "诊断/假设", 40, /diagnostic|assumption|residual|vif|ph_test|goodness|fit|missing|quality|dictionary|profile|诊断|假设|缺失|质量/],
      ["sensitivity", "敏感性/稳健性", 50, /sensitivity|robust|bootstrap|validation|external|敏感性|稳健/],
      ["supplement", "补充材料", 60, /supplement|appendix|raw|detail|明细|补充/],
    ];
    const figureRoles = [
      ["eda", "探索图", 10, /distribution|boxplot|hist|density|scatter|bar|profile|missing|eda|分布|探索/],
      ["primary", "主结果图", 20, /primary|main|effect|forest|estimate|contrast|主结果/],
      ["model", "模型图", 30, /model|pred|fit|obs_vs|prediction|prob|模型|预测/],
      ["diagnostic", "诊断图", 40, /diagnostic|residual|qq|vif|calibration|ph|assumption|诊断|残差|校准/],
      ["survival", "生存/预后图", 50, /surv|km|kaplan|cox|hazard|cif|生存|预后/],
      ["omics", "组学/生信图", 60, /volcano|heatmap|pca|umap|pathway|gsea|omics|rna|single|microbiome|组学|通路/],
      ["spatial", "空间/地图", 70, /map|spatial|gis|raster|geo|地图|空间/],
    ];
    const textRoles = [
      ["manuscript", "论文结果解释", 10, /manuscript|results|discussion|interpretation|paper|论文|结果解释/],
      ["model", "模型摘要", 20, /model|summary|cox|glm|logistic|linear|regression|模型|回归/],
      ["method", "方法说明", 30, /method|code|package|evidence|方法|代码|依据/],
      ["diagnostic", "诊断与限制", 40, /diagnostic|limitation|warning|assumption|repair|诊断|限制|警告/],
    ];
    const roles = kind === "figure" ? figureRoles : ["text", "model"].includes(kind) ? textRoles : tableRoles;
    const byExplicit = roles.find(([id]) => explicit === id || explicit.includes(id));
    if (byExplicit) return { id: byExplicit[0], label: byExplicit[1], rank: byExplicit[2] };
    const matched = roles.find(([, , , pattern]) => pattern.test(text));
    if (matched) return { id: matched[0], label: matched[1], rank: matched[2] };
    return {
      id: kind === "figure" ? "primary" : kind === "model" ? "model" : kind === "text" ? "manuscript" : "primary",
      label: kind === "figure" ? "主结果图" : kind === "model" ? "模型摘要" : kind === "text" ? "论文结果解释" : "主结果",
      rank: 25,
    };
  }

  function executionLookupByArtifact(result) {
    const lookup = new Map();
    (result?.executions || []).forEach((execution, executionIndex) => {
      ["tables", "figures", "texts", "models"].forEach((kind) => {
        (execution?.[kind] || []).forEach((artifact) => {
          const key = artifactKey(artifact);
          if (!key || lookup.has(key)) return;
          lookup.set(key, {
            execution,
            executionIndex,
            analysisTurnId: execution.analysis_turn_id || artifact.analysis_turn_id || "",
            turnLabel: execution.turn_label || artifact.turn_label || `分析 ${executionIndex + 1}`,
          });
        });
      });
    });
    return lookup;
  }

  function normalizeOutputArtifact(artifact, kind, index, lookup, extra = {}) {
    const metadata = artifactMetadata(artifact);
    const role = roleFromRules(kind, artifact);
    const executionInfo = lookup?.get(artifactKey(artifact)) || {};
    const hasPaperReady = Object.prototype.hasOwnProperty.call(metadata, "paper_ready");
    const explicitPaperReady = metadata.paper_ready === true || metadata.paper_ready === "TRUE" || metadata.paper_ready === "true";
    return {
      key: artifactKey(artifact) || `${kind}:${index}`,
      kind,
      index,
      artifact,
      metadata,
      title: artifactReadableName(artifact, `${kind} ${index + 1}`),
      role,
      methodFamily: inferMethodFamily(artifact),
      paperReady: hasPaperReady ? explicitPaperReady : /table.?1|manuscript|cox|model|figure|plot|results|baseline|论文|模型/.test(normalizedArtifactSearchText(artifact)),
      caption: String(metadata.caption || metadata.note || ""),
      executionIndex: Number.isInteger(executionInfo.executionIndex) ? executionInfo.executionIndex : -1,
      analysisTurnId: artifact.analysis_turn_id || executionInfo.analysisTurnId || "",
      turnLabel: artifact.turn_label || executionInfo.turnLabel || "会话累计",
      ...extra,
    };
  }

  function groupOutputs(items) {
    const groups = [];
    const byRole = new Map();
    items.forEach((item) => {
      const id = item.role?.id || "other";
      if (!byRole.has(id)) {
        byRole.set(id, {
          id,
          label: item.role?.label || "其他",
          rank: item.role?.rank || 999,
          items: [],
        });
        groups.push(byRole.get(id));
      }
      byRole.get(id).items.push(item);
    });
    groups.forEach((group) => {
      group.items.sort((a, b) => {
        const turnDiff = (a.executionIndex < 0 ? 999 : a.executionIndex) - (b.executionIndex < 0 ? 999 : b.executionIndex);
        if (turnDiff) return turnDiff;
        return a.index - b.index;
      });
    });
    return groups.sort((a, b) => a.rank - b.rank || a.label.localeCompare(b.label, "zh-CN"));
  }

  function latestAnalysisTurnId(result) {
    const executions = result?.executions || [];
    for (let index = executions.length - 1; index >= 0; index -= 1) {
      const turnId = executions[index]?.analysis_turn_id;
      if (turnId) return turnId;
    }
    for (const collection of ["models", "texts", "figures", "tables"]) {
      const items = result?.[collection] || [];
      for (let index = items.length - 1; index >= 0; index -= 1) {
        const turnId = items[index]?.analysis_turn_id;
        if (turnId) return turnId;
      }
    }
    return "";
  }

  function latestExecutionIndex(executions = []) {
    return executions.length ? executions.length - 1 : -1;
  }

  function scopedOutputItems(items, result) {
    if (state.resultScope !== "latest") return items;
    const turnId = latestAnalysisTurnId(result);
    if (turnId) {
      const scoped = items.filter((item) => item.analysisTurnId === turnId);
      if (scoped.length) return scoped;
    }
    const latest = latestExecutionIndex(result?.executions || []);
    if (latest >= 0) {
      const scoped = items.filter((item) => item.executionIndex === latest);
      if (scoped.length) return scoped;
    }
    return items;
  }

  function scopedExecutions(executions, result) {
    if (state.resultScope !== "latest") return executions;
    const turnId = latestAnalysisTurnId(result);
    if (turnId) {
      const scoped = executions.filter((item) => item.analysisTurnId === turnId);
      if (scoped.length) return scoped;
    }
    return executions.length ? [executions[executions.length - 1]] : executions;
  }

  function limitCurrentOutputs(items, kind) {
    if (state.resultScope !== "latest" || state.showAllCurrentOutputs) return items;
    const limit = CURRENT_OUTPUT_LIMITS[kind] || 6;
    if (!Number.isFinite(limit)) return items;
    return items.slice(0, limit);
  }

  function hiddenCurrentOutputCount(items, kind) {
    if (state.resultScope !== "latest") return 0;
    const limit = CURRENT_OUTPUT_LIMITS[kind] || 6;
    if (!Number.isFinite(limit)) return 0;
    return Math.max(0, items.length - limit);
  }

  function researchOutputProtocol() {
    const result = state.analysisResult?.payload?.result || {};
    const lookup = executionLookupByArtifact(result);
    const tables = (result.tables || []).map((artifact, index) => normalizeOutputArtifact(artifact, "table", index, lookup, {
      tableIndex: index,
      source: "artifact",
    }));
    derivedTables().forEach((table, index) => {
      const artifact = {
        name: table.name || `解释表格 ${index + 1}`,
        path: "",
        kind: "table",
        metadata: {
          role: "supplement",
          title: table.name || `解释表格 ${index + 1}`,
          paper_ready: false,
        },
      };
      tables.push(normalizeOutputArtifact(artifact, "table", resultCount("tables") + index, lookup, {
        tableIndex: resultCount("tables") + index,
        source: "derived",
      }));
    });
    const figures = (result.figures || []).map((artifact, index) => normalizeOutputArtifact(artifact, "figure", index, lookup, {
      figureIndex: index,
      source: "artifact",
    }));
    const texts = (result.texts || []).map((artifact, index) => normalizeOutputArtifact(artifact, "text", index, lookup, {
      textIndex: index,
      source: "artifact",
      fullText: state.analysisResult?.textPreviews?.[artifactKey(artifact)] || "",
      preview: state.analysisResult?.textPreviews?.[artifactKey(artifact)] || "",
    }));
    const models = (result.models || []).map((artifact, index) => normalizeOutputArtifact(artifact, "model", index, lookup, {
      modelIndex: index,
      source: "artifact",
      fullText: state.analysisResult?.textPreviews?.[artifactKey(artifact)] || "",
      preview: state.analysisResult?.textPreviews?.[artifactKey(artifact)] || "",
    }));
    const aiExplanations = interpretationParts().map((part, index) => ({
      key: `ai:${index}`,
      kind: "text",
      index: resultCount("texts") + index,
      artifact: { name: part?.title || `AI 结果解释 ${index + 1}`, path: "", kind: "text", metadata: { role: "manuscript" } },
      metadata: { role: "manuscript" },
      title: part?.title || `AI 结果解释 ${index + 1}`,
      role: { id: "manuscript", label: "论文结果解释", rank: 10 },
      methodFamily: "AI 结果解释",
      paperReady: true,
      caption: "",
      executionIndex: -1,
      analysisTurnId: part?.turn_id || "",
      turnLabel: part?.turn_label || `解释 ${index + 1}`,
      source: "ai",
      fullText: normalizeMarkdownText(part?.text || ""),
      preview: normalizeMarkdownText(part?.text || "").slice(0, 600),
    }));
    const executionCount = (result.executions || []).length;
    const executions = (result.executions || []).map((execution, index) => {
      const executionTables = (execution?.tables || []).length;
      const executionFigures = (execution?.figures || []).length;
      const executionTexts = (execution?.texts || []).length;
      const executionModels = (execution?.models || []).length;
      const inferredMethod = inferMethodFamily({
        name: [
          ...(execution?.tables || []).map((item) => item.name),
          ...(execution?.figures || []).map((item) => item.name),
          ...(execution?.texts || []).map((item) => item.name),
          ...(execution?.models || []).map((item) => item.name),
          executionCount === 1 ? [...tables, ...figures, ...texts, ...models].map((item) => item.title).join(" ") : "",
        ].join(" "),
        metadata: {},
      });
      return {
        key: executionKey(execution) || `execution:${index}`,
        execution,
        index,
        title: `分析轮次 ${index + 1}`,
        ok: execution?.ok !== false,
        methodFamily: inferredMethod,
        analysisTurnId: execution?.analysis_turn_id || "",
        turnLabel: execution?.turn_label || `分析轮次 ${index + 1}`,
        tableCount: executionTables || (executionCount === 1 ? tables.length : 0),
        figureCount: executionFigures || (executionCount === 1 ? figures.length : 0),
        textCount: executionTexts || (executionCount === 1 ? texts.length : 0),
        modelCount: executionModels || (executionCount === 1 ? models.length : 0),
        warnings: visibleWarnings(execution?.warnings || []),
        error: execution?.error || "",
        hasCode: Boolean(execution?.code),
      };
    });
    const scopedTables = scopedOutputItems(tables, result);
    const scopedFigures = scopedOutputItems(figures, result);
    const scopedTexts = scopedOutputItems([...texts, ...models, ...aiExplanations], result);
    const scopedExecutionItems = scopedExecutions(executions, result);
    const visibleTables = limitCurrentOutputs(scopedTables, "table");
    const visibleFigures = limitCurrentOutputs(scopedFigures, "figure");
    const visibleTexts = limitCurrentOutputs(scopedTexts, "text");
    const visibleExecutions = limitCurrentOutputs(scopedExecutionItems, "execution");
    const hiddenCurrentCounts = {
      tables: hiddenCurrentOutputCount(scopedTables, "table"),
      figures: hiddenCurrentOutputCount(scopedFigures, "figure"),
      texts: hiddenCurrentOutputCount(scopedTexts, "text"),
      executions: hiddenCurrentOutputCount(scopedExecutionItems, "execution"),
    };
    hiddenCurrentCounts.total = hiddenCurrentCounts.tables + hiddenCurrentCounts.figures
      + hiddenCurrentCounts.texts + hiddenCurrentCounts.executions;
    return {
      tables,
      figures,
      models,
      texts: [...texts, ...aiExplanations],
      executions,
      scopedTables,
      scopedFigures,
      scopedTexts,
      scopedExecutions: scopedExecutionItems,
      visibleTables,
      visibleFigures,
      visibleTexts,
      visibleExecutions,
      hiddenCurrentCounts,
      groupedTables: groupOutputs(tables),
      groupedFigures: groupOutputs(figures),
      groupedTexts: groupOutputs([...texts, ...models, ...aiExplanations]),
      groupedVisibleTables: groupOutputs(visibleTables),
      groupedVisibleFigures: groupOutputs(visibleFigures),
      groupedVisibleTexts: groupOutputs(visibleTexts),
      latestTurnId: latestAnalysisTurnId(result),
    };
  }

  function figureSupportsFormat(figure, format) {
    if (!figure?.path) return false;
    const suffix = artifactSuffix(figure);
    if (format === "png") return suffix === ".png";
    if (format === "pdf") return [".png", ".jpg", ".jpeg", ".pdf"].includes(suffix);
    if (format === "tiff") return [".png", ".jpg", ".jpeg", ".tif", ".tiff"].includes(suffix);
    return false;
  }

  function currentTablePreview() {
    const selectedDerived = selectedDerivedTable();
    if (selectedDerived?.rows?.length) return selectedDerived;
    const artifact = selectedTableArtifact();
    if (artifact?.path) {
      const cached = state.analysisResult?.tablePreviews?.[artifactKey(artifact)];
      if (cached?.rows?.length) return cached;
      return null;
    }
    return state.analysisResult?.tablePreview || null;
  }

  function cacheTablePreview(artifact, table) {
    if (!state.analysisResult || !artifact?.path || !table?.rows?.length) return;
    const normalized = normalizeArtifactTable(table, artifact);
    state.analysisResult.tablePreviews = {
      ...(state.analysisResult.tablePreviews || {}),
      [artifactKey(artifact)]: normalized,
    };
    state.analysisResult.tablePreviewKey = artifactKey(artifact);
    state.analysisResult.tablePreview = normalized;
  }

  function cacheTextPreview(artifact, text) {
    if (!state.analysisResult || !artifact?.path) return;
    const normalized = normalizeMarkdownText(text || "");
    if (!normalized) return;
    state.analysisResult.textPreviews = {
      ...(state.analysisResult.textPreviews || {}),
      [artifactKey(artifact)]: normalized,
    };
    state.analysisResult.textPreview = normalized;
  }

  function exportLabel(format) {
    return {
      csv: "CSV",
      docx: "DOCX",
      word: "DOCX",
      xlsx: "XLSX",
      png: "PNG",
      tiff: "TIFF",
      pdf: "PDF",
    }[format] || String(format || "").toUpperCase();
  }

  function downloadCurrentTable(format) {
    const artifact = selectedTableArtifact();
    if (artifact?.path) {
      if (!tableArtifactSupportsFormalExport(artifact)) {
        toast("当前表格格式暂不支持正式导出", "error");
        return;
      }
      const exportFormat = format === "word" ? "docx" : format;
      downloadUrl(artifactDownloadUrl(artifact, exportFormat));
      toast(`${exportLabel(exportFormat)} 表格开始下载`, "ok");
      return;
    }
    const table = selectedDerivedTable()?.rows?.length
      ? selectedDerivedTable()
      : currentTablePreview()?.rows?.length
      ? currentTablePreview()
      : currentRenderedTable();
    if (!table?.rows?.length) {
      toast("当前没有可下载表格", "error");
      return;
    }
    const name = safeFileStem(selectedDerivedTable()?.name || selectedTableArtifact()?.name || state.activeDataset?.name || "research_table");
    if (format === "word" || format === "docx") {
      downloadText(`${name}.doc`, tableToWordHtml(table, name), "application/msword;charset=utf-8");
      toast("当前预览表格已生成兼容 Word 文件", "ok");
    } else if (format === "xlsx") {
      toast("XLSX 需要 R 生成的正式表格，请先运行分析", "error");
    } else {
      downloadText(`${name}.csv`, tableToCsv(table), "text/csv;charset=utf-8");
      toast("CSV 表格已生成", "ok");
    }
  }

  function downloadCurrentFigure(format) {
    const figure = selectedFigureArtifact();
    if (!figure?.path) {
      toast("当前没有可下载图形", "error");
      return;
    }
    if (!figureSupportsFormat(figure, format)) {
      toast(`当前图形不支持导出为 ${exportLabel(format)}`, "error");
      return;
    }
    downloadUrl(artifactDownloadUrl(figure, format));
    toast(`${exportLabel(format)} 图形开始下载`, "ok");
  }

  function generateReportDraft() {
    const summary = state.activeSummary || {};
    const variables = summary.variables || [];
    const result = state.analysisResult?.payload?.result || {};
    const tableName = result.tables?.[state.selectedTableIndex]?.name || result.tables?.[0]?.name || "待生成";
    const figureName = result.figures?.[state.selectedFigureIndex]?.name || result.figures?.[0]?.name || "待生成";
    const displayedInterpretation = displayInterpretationText();
    const modelText = displayedInterpretation || "待生成";
    const modelTextCount = cachedTextOutputs().length + interpretationParts().length;
    const missing = formatMissing(summary.overall_missing_pct);
    return [
      "# 科研统计分析草稿",
      "",
      `## 数据概况`,
      `- 数据集：${state.activeDataset?.name || "待上传"}`,
      `- 样本量：${formatNumber(summary.rows)}`,
      `- 变量数：${formatNumber(summary.columns)}`,
      `- 整体缺失比例：${missing}`,
      `- 可能的分组变量：${firstLikelyGroup(variables)}`,
      "",
      "## 分析产物",
      `- 表格：${tableName}${derivedTables().length ? `；AI解释表格 ${derivedTables().length} 个` : ""}`,
      `- 图形：${figureName}`,
      `- 文本/模型摘要：${modelTextCount} 个`,
      "",
      "## 交付验收",
      ...deliveryScore().items.map((item) => `- ${item.ok ? "通过" : "待完成"}：${item.label}（${item.detail}）`),
      "",
      "## 结果解释",
      blockingResultFailure(result)
        ? `R 执行返回异常：${result.error || "未知错误"}`
        : (displayedInterpretation || (state.analysisResult ? "R 分析已完成。" : "等待运行分析。")),
      "",
      "## 模型摘要",
      modelText,
      "",
      "## 限制与下一步",
      variables.length
        ? `当前已识别 ${formatNumber(summary.missing_columns)} 个存在缺失的变量，建模前需要确认缺失处理策略、结局变量和协变量。`
        : "上传并理解数据后补充。",
    ].join("\n");
  }

  function renderDataset() {
    const dataset = state.activeDataset;
    const summary = state.activeSummary || summaryOf(dataset);
    const variables = summary.variables || [];
    const uploadState = $("rsw-upload-state");
    const uploadCopy = $("rsw-upload-copy");

    const topDataset = $("rsw-top-dataset-name");
    const topStage = $("rsw-top-stage");
    qs(".rsw-assistant-ops")?.classList.toggle("is-compact", Boolean(dataset));
    if (!dataset) {
      if (topDataset) topDataset.textContent = "等待上传";
      if (topStage) topStage.textContent = "数据准备";
      if (uploadState) uploadState.textContent = "等待文件";
      if (uploadCopy) uploadCopy.textContent = "拖放或点击导入 CSV、Excel、SPSS、Stata、RDS、Parquet";
      return;
    }

    if (topDataset) topDataset.textContent = dataset.name || "已上传数据";
    if (topStage) {
      const outcome = resultTurnOutcome(activeTurn());
      const activePhase = state.analysisTimeline.find((step) => step.status === "active");
      topStage.textContent = state.streaming || state.inspecting
        ? activePhase?.label || (state.inspecting ? "检查数据" : "运行分析")
        : outcome?.analysis_status === "failed" ? "执行未完成"
        : outcome?.analysis_status === "incomplete" ? "分析待补齐"
        : outcome?.analysis_status === "complete" ? "分析产物齐全"
        : outcome?.analysis_status === "conversation_only" ? "对话完成"
        : variables.length ? "已理解数据" : "等待理解";
    }
    if (uploadState) uploadState.textContent = variables.length ? "已理解" : "已上传";
    if (uploadCopy) uploadCopy.textContent = dataset.name || "数据已上传";
  }

  function renderResults() {
    renderResultStatus(state.activeSummary?.variables || []);
  }

  function renderAll() {
    renderReadiness();
    renderStatus();
    renderAnalysisPlan();
    renderPreparationPlan();
    renderRunControls();
    renderTemplateLibrary();
    renderDataset();
    renderResults();
    renderResultV2();
    renderPhaseTimeline();
    renderInlinePlanInputs();
    renderWorkbenchView();
  }

  async function loadDatasets() {
    if (!state.sid) return;
    const payload = await requestJson(`/api/session/${encodeURIComponent(state.sid)}/research/datasets`);
    const previousId = state.activeDataset?.dataset_id;
    state.datasets = sortDatasets(payload.datasets || []);
    const serverActiveId = payload.active_dataset_id || "";
    const nextDataset = state.datasets.find((dataset) => dataset.dataset_id === serverActiveId)
      || state.datasets.find((dataset) => dataset.dataset_id === previousId && state.datasets.length === 1)
      || (state.datasets.length === 1 ? state.datasets[0] : null);
    if (previousId && nextDataset?.dataset_id !== previousId) {
      state.templatePreparations = {};
      state.templateInputValues = {};
      state.templatePrepareLoading = "";
      templatePreparePromises.clear();
      state.activeAnalysisPlan = null;
      if (!state.preparationResumeTemplateId) state.activePreparationPlan = null;
      state.dependencyGate = null;
      state.dependencyResume = null;
      closeDependencyGate({ clear: true });
      state.continuationContexts = {};
    }
    state.activeDataset = nextDataset;
    state.activeSummary = summaryOf(state.activeDataset);
    renderAll();
  }

  async function loadReadiness() {
    if (!state.sid) return;
    const payload = await requestJson(`/api/session/${encodeURIComponent(state.sid)}/research/readiness`);
    state.readiness = payload;
    if (payload.llm?.configured === false && state.status.kind === "idle") {
      setStatus("warning", "AI 模型待配置", "输入 DeepSeek API Key 即可启用 AI 分析助手。", "configure-ai");
    }
    renderReadiness();
  }

  async function loadTablePreview(index) {
    const tables = resultArtifacts("tables");
    state.selectedTableIndex = Math.max(0, Number(index) || 0);
    const table = tables[state.selectedTableIndex];
    if (!state.analysisResult || !table?.path) {
      renderAll();
      return;
    }
    const cached = state.analysisResult.tablePreviews?.[artifactKey(table)];
    if (cached?.rows?.length) {
      state.analysisResult.tablePreview = cached;
      renderAll();
      return;
    }
    setStatus("busy", "读取表格", table.name || "R 表格输出");
    const payload = await fetchArtifactJson(table);
    if (payload?.kind === "table") {
      cacheTablePreview(table, payload);
      setStatus("ok", "表格已载入", `${formatNumber(payload.rows?.length || 0)} 行预览`);
    }
    renderAll();
  }

  function recordAnalysisHistory(payload) {
    const result = payload?.result || {};
    const historyId = state.analysisTurnId || `analysis-${artifactKey(result.tables?.[0]) || artifactKey(result.figures?.[0]) || Date.now()}`;
    const item = {
      id: historyId,
      title: state.activeDataset?.name ? `${state.activeDataset.name} 的 R 分析` : "R 分析结果",
      created_at: new Date().toLocaleString("zh-CN", { hour12: false }),
      tables: result.tables?.length || 0,
      figures: result.figures?.length || 0,
      models: result.models?.length || 0,
      texts: result.texts?.length || 0,
      ok: result.ok !== false,
    };
    const existingIndex = state.history.findIndex((entry) => entry.id === historyId);
    if (existingIndex >= 0) {
      state.history = [
        item,
        ...state.history.slice(0, existingIndex),
        ...state.history.slice(existingIndex + 1),
      ].slice(0, 12);
    } else {
      state.history = [item, ...state.history].slice(0, 12);
    }
    writeStoredHistory();
  }

  function hasCurrentAnalysisResults() {
    if (state.activePreparationPlan) {
      const overview = state.resultState?.overview || {};
      return Boolean(
        currentAnalysisArtifactTotal()
        || Number(overview.table_count || 0)
        || Number(overview.figure_count || 0)
        || Number(overview.text_count || 0)
        || Number(overview.model_count || 0)
      );
    }
    return Boolean(
      state.analysisResult
      || state.resultState?.analysis_turns?.length
      || state.resultState?.overview?.analysis_turn_count,
    );
  }

  function chooseDatasetReplacement(upload) {
    const modal = $("rsw-replace-modal");
    if (!modal) return Promise.resolve("cancel");
    const summary = state.activeSummary || summaryOf(state.activeDataset);
    const currentMeta = [
      summary?.rows !== undefined && summary?.columns !== undefined
        ? `${formatNumber(summary.rows)} 行 × ${formatNumber(summary.columns)} 列`
        : "",
      state.resultState?.overview?.analysis_turn_count
        ? `${formatNumber(state.resultState.overview.analysis_turn_count)} 轮分析`
        : (state.analysisResult ? "已有分析结果" : ""),
    ].filter(Boolean).join(" · ") || "当前会话中的活动数据";
    const extension = upload?.kind === "directory"
      ? "科研数据文件夹"
      : upload?.kind === "bundle"
        ? "多文件科研数据包"
        : String(upload?.name || "").split(".").pop()?.toUpperCase() || "数据文件";
    const incomingSize = Number(upload?.size || 0);
    const incomingMeta = [
      extension,
      incomingSize > 0 ? formatBytes(incomingSize) : "大小将在导入时安全核对",
    ].filter(Boolean).join(" · ");
    const values = {
      "rsw-replace-current-name": state.activeDataset?.name || "当前数据",
      "rsw-replace-current-meta": currentMeta,
      "rsw-replace-incoming-name": upload?.name || "新数据文件",
      "rsw-replace-incoming-meta": incomingMeta,
      "rsw-replace-impact": "替换成功后，本页中的分析计划、结果和对话将重置；新文件验证失败时会保留当前数据。",
    };
    Object.entries(values).forEach(([id, value]) => {
      const element = $(id);
      if (element) element.textContent = value;
    });
    rememberModalFocus();
    modal.hidden = false;
    document.body.classList.add("rsw-modal-open");
    setTimeout(() => qs('[data-replace-action="replace"]', modal)?.focus(), 30);
    return new Promise((resolve) => {
      const finish = (action) => {
        modal.removeEventListener("click", onClick);
        modal.hidden = true;
        document.body.classList.remove("rsw-modal-open");
        restoreModalFocus();
        resolve(action);
      };
      const onClick = (event) => {
        const button = event.target.closest("[data-replace-action]");
        if (button) finish(button.dataset.replaceAction || "cancel");
      };
      modal.addEventListener("click", onClick);
    });
  }

  async function uploadFiles(files) {
    if (!files || !files.length) return;
    if (files.length !== 1) throw new Error("每次必须且只能上传一个数据文件");
    await ensureSession();
    const upload = files[0];
    if (state.activeDataset && hasCurrentAnalysisResults()) {
      const action = await chooseDatasetReplacement(upload);
      if (action === "cancel") return;
      if (action === "export") await downloadResearchExport("package");
    }
    state.lastAction = { type: "upload", files: [upload] };
    setWorkbenchView("assistant");
    const body = new FormData();
    body.append("file", upload);
    setRunState("上传中", "保存数据文件", "busy");
    setStatus("busy", "正在替换数据", upload.name || "1 个文件进入研究会话");
    const payload = await requestJson(`/api/session/${encodeURIComponent(state.sid)}/research/upload`, {
      method: "POST",
      body,
      headers: {},
    });
    await activateImportedDataset(payload, "科研数据上传成功");
  }

  async function activateImportedDataset(payload, successTitle) {
    state.activeDataset = payload.dataset;
    state.activeSummary = summaryOf(state.activeDataset);
    state.templatePreparations = {};
    state.templateInputValues = {};
    state.templatePrepareLoading = "";
    templatePreparePromises.clear();
    state.analysisResult = null;
    state.resultState = null;
    state.activeTurnId = "";
    state.analysisTurnId = "";
    state.activeTemplateContext = null;
    state.activeAnalysisPlan = null;
    state.activePreparationPlan = null;
    state.dependencyGate = null;
    state.dependencyResume = null;
    closeDependencyGate({ clear: true });
    state.canceling = false;
    state.chatStarting = false;
    state.continuationContexts = {};
    state.history = [];
    writeStoredHistory();
    state.selectedTableIndex = 0;
    state.selectedFigureIndex = 0;
    resetMessages("数据已替换。请基于当前数据重新选择模板或直接描述分析需求。");
    await loadDatasets();
    toast(successTitle || "科研数据导入成功", "ok");
    setStatus("ok", "数据替换完成", state.activeDataset?.name || "科研数据已保存");
    renderAll();
    loadReadiness().catch(() => {});
    try {
      await inspectDataset(state.activeDataset.dataset_id);
    } catch (error) {
      setRunState("错误", "数据已上传，理解失败", "error");
      const classified = classifyError(error);
      setStatus(classified.kind, "数据已上传，理解失败", classified.detail, "retry");
      toast(`数据已上传，但理解失败：${error.message}`, "error");
      renderAll();
    }
  }

  async function importLocalSelection(kind = "files") {
    const selection = await requestDesktopImportSelection(kind);
    if (selection === null) {
      if (kind === "files") {
        $("rsw-file-input")?.click();
        return;
      }
      throw new Error("当前运行方式不支持文件夹导入，请从桌面应用打开工作台");
    }
    if (selection.canceled) return;

    await ensureSession();
    if (state.activeDataset && hasCurrentAnalysisResults()) {
      const action = await chooseDatasetReplacement({
        name: selection.name,
        size: Number(selection.size_bytes || 0),
        kind: selection.kind,
      });
      if (action === "cancel") return;
      if (action === "export") await downloadResearchExport("package");
    }

    state.lastAction = { type: "local-import", kind };
    setWorkbenchView("assistant");
    setRunState("导入中", "复制本地科研数据", "busy");
    setStatus(
      "busy",
      "正在导入科研数据",
      `${selection.name || "本地数据"} · ${
        Number(selection.size_bytes || 0) > 0 ? formatBytes(selection.size_bytes) : "正在核对数据包"
      } · 复制到应用存储`,
    );
    const payload = await requestJson(`/api/session/${encodeURIComponent(state.sid)}/research/import-local`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        grant_token: selection.token,
      }),
    });
    await activateImportedDataset(payload, "本地科研数据导入成功");
  }

  async function inspectDataset(datasetId) {
    if (!datasetId) return;
    if (state.inspecting) return;
    state.lastAction = { type: "inspect", datasetId };
    state.inspecting = true;
    state.canceling = false;
    setRunState("运行中", "本地 R 正在理解数据", "busy");
    setStatus("busy", "正在理解数据", "本地 R 正在读取变量结构、样本量和缺失情况");
    renderRunControls();
    try {
      const payload = await requestJson(
        `/api/session/${encodeURIComponent(state.sid)}/research/datasets/${encodeURIComponent(datasetId)}/inspect`,
        { method: "POST" }
      );
      state.activeDataset = payload.dataset;
      state.activeSummary = payload.summary || summaryOf(payload.dataset);
      await loadDatasets();
      state.activeDataset = payload.dataset;
      state.activeSummary = payload.summary || summaryOf(payload.dataset);
      setRunState("空闲", "数据理解完成", "ok");
      toast("数据理解完成", "ok");
      setStatus("ok", "数据理解完成", `${formatNumber(state.activeSummary.rows)} 行 · ${formatNumber(state.activeSummary.columns)} 个变量`);
      await loadResultState({ silent: true }).catch(() => {});
      renderAll();
      loadReadiness().catch(() => {});
      await resumePreparedTemplateIfPossible();
      return payload;
    } catch (error) {
      if (error?.code === "analysis_canceled") {
        setRunState("已停止", "数据已保留，可重新执行数据理解");
        setStatus("idle", "数据理解已停止", "数据没有被删除，可随时重试。", "retry");
        toast("数据理解已停止", "ok");
        return null;
      }
      if (dependencyGateNeedsAction(error?.payload?.dependency_gate)) {
        openDependencyGate(error.payload.dependency_gate, {
          type: "inspect",
          datasetId,
        });
        setRunState("待处理", "等待数据读取依赖");
        setStatus(
          "warning",
          error.payload.dependency_gate.headline || "数据读取依赖尚未就绪",
          error.payload.dependency_gate.summary || "处理并重新检测后会自动继续理解当前数据。"
        );
        return null;
      }
      throw error;
    } finally {
      state.inspecting = false;
      state.canceling = false;
      renderRunControls();
    }
  }

  async function resumePreparedTemplateIfPossible() {
    const templateId = String(state.preparationResumeTemplateId || "").trim();
    const conversationPrompt = String(state.preparationResumePrompt || "").trim();
    if (!state.activeDataset?.dataset_id) return;
    if (!templateId && conversationPrompt) {
      const input = $("rsw-chat-input");
      if (input) {
        input.value = conversationPrompt;
        input.focus();
      }
      state.preparationResumePrompt = "";
      state.activePreparationPlan = null;
      setStatus("ok", "处理后数据已载入", "原分析需求已恢复，请确认后发送。");
      toast("处理后数据已载入，已恢复原分析需求", "ok");
      renderAll();
      return;
    }
    if (!templateId) return;
    const preparation = await prepareTemplate(templateId, { silent: true });
    if (!preparation) return;
    if (preparation.status === "ready") {
      state.preparationResumeTemplateId = "";
      await applyPromptTemplate(templateId);
      setStatus("ok", "处理后数据已通过预检", "原模板已恢复，请确认后发送分析。");
      toast("处理后数据已载入，已返回原模板", "ok");
      return;
    }
    if (preparation.status === "needs_input") {
      state.templateSelectedId = templateId;
      setStatus("warning", "处理后数据已载入", "还需确认少量会改变科学含义的关键输入。");
      await openTemplateLibrary();
      return;
    }
    if (preparation.status === "needs_preparation") {
      state.activePreparationPlan = preparation.preparation_plan || state.activePreparationPlan;
      setStatus(
        "warning",
        "数据仍未达到分析条件",
        preparation.preparation_plan?.summary || preparation.reason || "请继续完成上游准备。"
      );
      renderAll();
      return;
    }
    if (preparation.status === "not_applicable") {
      state.activePreparationPlan = preparation.compatibility_guidance || state.activePreparationPlan;
      state.templateSelectedId = templateId;
      setStatus(
        "warning",
        "处理后数据仍不适用",
        preparation.reason || "请查看适用条件、继续准备目标数据，或改用匹配当前数据的模板。"
      );
      renderAll();
    }
  }

  async function hydrateAnalysisResult(payload, options = {}) {
    const previousAnalysis = state.analysisResult;
    const previousTablePath = selectedTableArtifact()?.path || "";
    const previousFigurePath = selectedFigureArtifact()?.path || "";
    const turnId = state.analysisTurnId || payload?.analysis_turn_id || "";
    const stampedPayload = stampResearchPayloadWithTurn(payload, turnId);
    const mergedPayload = mergeResearchPayload(previousAnalysis?.payload, stampedPayload);
    const result = mergedPayload?.result || {};
    const intermediate = Boolean(options.intermediate);
    const recoverableFailure = Boolean(options.recoverableFailure);
    const terminalFailure = Boolean(options.terminalFailure);
    const recovered = Boolean(options.recovered);
    const preparationRequired = Boolean(options.preparationRequired);
    state.analysisResult = {
      payload: mergedPayload,
      tablePreview: previousAnalysis?.tablePreview || null,
      tablePreviewKey: previousAnalysis?.tablePreviewKey || "",
      tablePreviews: previousAnalysis?.tablePreviews || {},
      textPreview: previousAnalysis?.textPreview || "",
      textPreviews: previousAnalysis?.textPreviews || {},
      interpretation: previousAnalysis?.interpretation || "",
      interpretationParts: previousAnalysis?.interpretationParts || [],
      derivedTables: previousAnalysis?.derivedTables || [],
    };
    const currentTurnTables = turnId
      ? (result.tables || []).map((item, index) => ({ item, index })).filter(({ item }) => item?.analysis_turn_id === turnId)
      : [];
    const currentTurnFigures = turnId
      ? (result.figures || []).map((item, index) => ({ item, index })).filter(({ item }) => item?.analysis_turn_id === turnId)
      : [];
    const preferredCurrentTable = currentTurnTables.length
      ? currentTurnTables[preferredTableIndex(currentTurnTables.map(({ item }) => item))]
      : null;
    const nextTableIndex = previousTablePath
      ? (result.tables || []).findIndex((item) => item?.path === previousTablePath)
      : -1;
    const nextFigureIndex = previousFigurePath
      ? (result.figures || []).findIndex((item) => item?.path === previousFigurePath)
      : -1;
    state.selectedTableIndex = preferredCurrentTable
      ? preferredCurrentTable.index
      : (nextTableIndex >= 0 ? nextTableIndex : preferredTableIndex(result.tables || []));
    state.selectedFigureIndex = currentTurnFigures.length
      ? currentTurnFigures[0].index
      : (nextFigureIndex >= 0 ? nextFigureIndex : 0);
    const failed = blockingResultFailure(result) && !preparationRequired;
    setStatus(
      preparationRequired ? "warning" : (terminalFailure ? "error" : (recoverableFailure ? "busy" : (failed ? "error" : (intermediate ? "busy" : "ok")))),
      preparationRequired
        ? "当前数据需要先准备"
        : terminalFailure
        ? "R 自动修正已停止"
        : recoverableFailure
        ? "R 步骤需要调整，AI 正在修正"
        : (failed ? "R 分析异常" : (recovered ? "R 步骤已修正" : (intermediate ? "R 步骤已完成" : "R 分析完成"))),
      preparationRequired
        ? (payload?.preparation_plan?.summary || "完成准备并重新导入后，可返回原分析。")
        : terminalFailure
        ? (options.terminalMessage || result.error || "重复错误已停止自动重试。")
        : recoverableFailure
        ? `${options.repairError || "R 返回了可修正错误"}；诊断记录已保留，本轮分析仍在继续。`
        : (result.error || `生成 ${result.tables?.length || 0} 个表格、${result.figures?.length || 0} 张图形、${result.models?.length || 0} 个模型${intermediate ? "，AI 正在整理解释" : ""}`)
    );
    renderAll();
    if (!preparationRequired) notifyResultsReady();

    const firstTable = result.tables?.[state.selectedTableIndex] || result.tables?.[0];
    const textArtifacts = [...(result.texts || []), ...(result.models || [])];
    const updates = [];
    if (firstTable?.path && !state.analysisResult.tablePreviews?.[artifactKey(firstTable)]) {
      updates.push(
        fetchArtifactJson(firstTable).then((table) => {
          if (state.analysisResult?.payload === mergedPayload && table?.kind === "table") {
            cacheTablePreview(firstTable, table);
          }
        })
      );
    }
    textArtifacts.forEach((textArtifact) => {
      if (!textArtifact?.path || state.analysisResult?.textPreviews?.[artifactKey(textArtifact)]) return;
      updates.push(
        fetchArtifactJson(textArtifact).then((text) => {
          if (state.analysisResult?.payload === mergedPayload && text?.kind === "text") {
            cacheTextPreview(textArtifact, text.text || "");
          }
        })
      );
    });
    if (updates.length) {
      await Promise.all(updates);
      renderAll();
    }
    if (!preparationRequired) recordAnalysisHistory(mergedPayload);
    persistResultState().catch(() => {});
  }

  function attachFinalAnswerToResults(text) {
    const interpretation = normalizeMarkdownText(text);
    if (!interpretation) return;
    const previousAnalysis = state.analysisResult;
    const existingPayload = state.analysisResult?.payload || {
      result: {
        ok: true,
        tables: [],
        figures: [],
        texts: [],
        models: [],
        warnings: [],
        code: "",
        stdout: "",
        stderr: "",
        error: "",
      },
    };
    const parsedTables = parseMarkdownTables(interpretation);
    const previousParts = previousAnalysis?.interpretationParts || (
      previousAnalysis?.interpretation
        ? [{ title: "AI 结果解释 1", text: previousAnalysis.interpretation }]
        : []
    );
    const alreadyRecorded = previousParts.some((part) => normalizeMarkdownText(part?.text || "") === interpretation);
    const nextParts = alreadyRecorded
      ? previousParts
      : [
        ...previousParts,
        {
          title: `AI 结果解释 ${previousParts.length + 1}`,
          text: interpretation,
          turn_id: state.analysisTurnId || "",
          turn_label: state.analysisTurnId ? "当前分析" : `解释 ${previousParts.length + 1}`,
        },
      ];
    const previousDerived = previousAnalysis?.derivedTables || [];
    state.analysisResult = {
      payload: existingPayload,
      tablePreview: previousAnalysis?.tablePreview || null,
      tablePreviewKey: previousAnalysis?.tablePreviewKey || "",
      tablePreviews: previousAnalysis?.tablePreviews || {},
      textPreview: previousAnalysis?.textPreview || "",
      textPreviews: previousAnalysis?.textPreviews || {},
      interpretation,
      interpretationParts: nextParts,
      derivedTables: mergeDerivedTableList(previousDerived, parsedTables),
    };
    if (!currentTablePreview()?.rows?.length && !resultArtifacts("tables").length && parsedTables.length) {
      state.analysisResult.tablePreview = parsedTables[0];
      state.analysisResult.tablePreviewKey = "";
      state.selectedTableIndex = 0;
    }
    persistResultState().catch(() => {});
  }

  function toolResultArtifact(event) {
    return (event.artifacts || []).find((item) => item?.type === "tool_result" && item.url);
  }

  async function loadPersistedToolPayload(event) {
    const artifact = toolResultArtifact(event);
    if (!artifact?.url) return null;
    const record = await requestJson(`${artifact.url}?format=json`);
    if (!record?.data) return null;
    if (typeof record.data === "string") {
      return JSON.parse(record.data);
    }
    return record.data;
  }

  async function handleRunRToolAudit(event) {
    let payload = parseToolPayload(event.content);
    if (!payload?.result) {
      payload = await loadPersistedToolPayload(event);
    }
    if (dependencyGateNeedsAction(payload?.dependency_gate)) {
      state.recoveringRStep = false;
      openDependencyGate(payload.dependency_gate, {
        type: "analysis",
        turnId: state.analysisTurnId || state.activeTurnId || "",
      });
      setStatus(
        "warning",
        payload.dependency_gate.headline || "分析依赖尚未就绪",
        payload.dependency_gate.summary || "完成处理并重新检测后，可恢复当前分析计划。"
      );
      renderAll();
      return;
    }
    if (
      state.dependencyGate?.mode === "candidate"
      && state.dependencyResume?.type === "analysis"
    ) {
      closeDependencyGate({ clear: true });
    }
    if (payload?.preparation_plan) {
      state.activePreparationPlan = payload.preparation_plan;
      state.preparationResumeTemplateId = String(
        payload.preparation_plan.resume?.template_id || state.preparationResumeTemplateId || ""
      );
      state.preparationResumePrompt = String(
        payload.preparation_plan.resume?.conversation_prompt || state.preparationResumePrompt || ""
      );
      state.recoveringRStep = false;
      setStatus(
        "warning",
        "当前数据需要先准备",
        payload.preparation_plan.summary || "完成上游准备并重新导入后，可返回原分析。"
      );
      renderAll();
    }
    if (!payload?.result) return;
    const result = payload.result || {};
    const hasArtifacts = resultArtifactTotal(result) > 0;
    const wasRecovering = state.recoveringRStep;
    const repairStopped = Boolean(payload?.repair_policy?.stop);
    const repairStopMessage = String(payload?.repair_policy?.message || "").trim();
    const recoverableFailure = Boolean(
      state.streaming && result.ok === false && !payload?.preparation_plan && !repairStopped
    );
    if (repairStopped) {
      state.recoveringRStep = true;
      state.rRepairStopMessage = repairStopMessage || result.error || "重复错误已停止自动重试。";
    } else if (recoverableFailure) {
      state.recoveringRStep = true;
    } else if (result.ok !== false) {
      state.recoveringRStep = false;
      state.rRepairStopMessage = "";
      state.activePreparationPlan = null;
    }
    if (!hasArtifacts && result.ok !== false) {
      if (wasRecovering) {
        setStatus("busy", "R 步骤已修正", "AI 正在继续完成本轮分析和科研产物。");
        renderAll();
      }
      return;
    }
    await hydrateAnalysisResult(payload, {
      intermediate: state.streaming,
      recoverableFailure,
      terminalFailure: repairStopped,
      terminalMessage: state.rRepairStopMessage,
      recovered: wasRecovering && result.ok !== false,
      repairError: result.error || event.error || "",
      preparationRequired: Boolean(payload?.preparation_plan),
    });
  }

  function handleResearchToolAudit(event) {
    if (event.analysis_turn_id) {
      state.activeTurnId = event.analysis_turn_id;
    }
    if (event.result_state) {
      loadResultState({ silent: true }).catch(() => {});
    }
    if (event.tool === "set_analysis_plan") {
      const payload = parseToolPayload(event.content);
      if (payload?.analysis_plan) {
        state.activeAnalysisPlan = payload.analysis_plan;
        if (dependencyGateNeedsAction(payload?.dependency_gate)) {
          openDependencyGate(payload.dependency_gate, {
            type: "analysis",
            turnId: state.analysisTurnId || state.activeTurnId || "",
          });
          setStatus(
            "warning",
            payload.dependency_gate.headline || "分析候选依赖尚未就绪",
            "AI 会继续核对实际 R 代码；若确实需要，分析结束后可直接确认安装。"
          );
        } else if (payload?.dependency_gate) {
          state.dependencyGate = payload.dependency_gate;
        }
        state.activePreparationPlan = null;
        const turnId = String(event.analysis_turn_id || state.analysisTurnId || state.activeTurnId || "").trim();
        if (turnId) {
          const priorContinuation = state.continuationContexts[turnId] || {};
          state.continuationContexts[turnId] = {
            ...priorContinuation,
            templateId: String(payload.analysis_plan.template_id || priorContinuation.templateId || ""),
            planId: String(payload.analysis_plan.plan_id || priorContinuation.planId || ""),
            title: String(payload.analysis_plan.intent || priorContinuation.title || ""),
          };
        }
        if (!dependencyGateNeedsAction(payload?.dependency_gate)) {
          setStatus("busy", "分析计划已形成", "正在按计划执行本地 R 分析");
        }
        renderAll();
      }
      return;
    }
    if (event.tool === "inspect_dataset") {
      const payload = parseToolPayload(event.content);
      if (payload?.dataset) {
        state.activeDataset = payload.dataset;
        state.activeSummary = payload.summary || summaryOf(payload.dataset);
        renderAll();
      }
      return;
    }
    if (event.tool !== "run_r_code") return;
    state.toolAuditSequence += 1;
    const sequence = state.toolAuditSequence;
    return handleRunRToolAudit(event, sequence).catch((error) => {
      toast(`结果读取失败：${error.message}`, "error");
    });
  }

  function appendMessage(role, content, options = {}) {
    const list = $("rsw-message-list");
    if (!list) return null;
    const article = document.createElement("article");
    article.className = `rsw-message ${role === "user" ? "rsw-message-user" : "rsw-message-ai"}`;
    article.innerHTML = `
      <div class="rsw-avatar" aria-hidden="true">
        <svg viewBox="0 0 24 24">${role === "user" ? '<path d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z" /><path d="M4 21a8 8 0 0 1 16 0" />' : '<path d="M12 3l2.3 5.2L20 10.5l-5.7 2.1L12 18l-2.3-5.4L4 10.5l5.7-2.3z" />'}</svg>
      </div>
      <div class="rsw-bubble"><div class="rsw-message-content"></div></div>`;
    renderMessageContent(qs(".rsw-message-content", article), content);
    const imageUrls = Array.isArray(options.imageUrls) ? options.imageUrls.filter(Boolean) : [];
    if (imageUrls.length) {
      const row = document.createElement("div");
      row.className = "rsw-chat-image-previews";
      imageUrls.forEach((url) => {
        const img = document.createElement("img");
        img.src = url;
        img.alt = "附带图片";
        row.appendChild(img);
      });
      qs(".rsw-bubble", article)?.appendChild(row);
    }
    list.appendChild(article);
    article.scrollIntoView({ block: "end", behavior: "smooth" });
    return qs(".rsw-message-content", article);
  }

  function resultTurnOutcome(turn) {
    // Derive the selected-turn view from the same manifest/execution facts as
    // the server outcome. Do not hydrate another legacy analysisResult cache.
    if (!turn) return null;
    const refs = Array.isArray(turn.execution_refs) ? turn.execution_refs : [];
    const latest = refs[refs.length - 1] || {};
    const planId = String(turn.portfolio_plan_id || latest.plan_id || "").trim();
    const manifests = state.resultState?.portfolio_manifests || {};
    const manifest = planId && manifests[planId] && typeof manifests[planId] === "object"
      ? manifests[planId]
      : null;
    const assessment = manifest?.latest_assessment || latest.result_assessment || {};
    const failed = turn.status === "failed" || latest.ok === false
      || ["failed", "blocked", "canceled", "cancelled", "error"].includes(String(latest.status || "").toLowerCase());
    const requested = Boolean(planId || refs.length);
    const portfolioComplete = manifest ? manifest.complete === true : turn.portfolio_complete === true;
    const statuses = [
      ...Object.values(manifest?.diagnostic_status || {}),
      assessment.diagnostic_quality || turn.diagnostic_quality,
    ].filter(Boolean);
    const diagnosticQuality = statuses.includes("failed") ? "failed"
      : statuses.includes("warning") ? "warning"
      : statuses.includes("incomplete") ? "incomplete"
      : statuses.includes("not_assessed") ? "not_assessed"
      : statuses.includes("passed") ? "passed" : "not_assessed";
    return {
      completed: !requested || (portfolioComplete && !failed),
      analysis_status: !requested ? "conversation_only" : failed ? "failed" : portfolioComplete ? "complete" : "incomplete",
      portfolio_complete: requested ? portfolioComplete : null,
      plan_id: planId,
      diagnostic_quality: diagnosticQuality,
      outstanding_roles: manifest?.outstanding_roles || assessment.outstanding_roles || assessment.missing_roles || [],
    };
  }

  function resultCompletionPresentation(turn) {
    const summary = turn?.summary || {};
    const outcome = resultTurnOutcome(turn);
    const failed = outcome?.analysis_status === "failed";
    const complete = outcome?.analysis_status === "complete";
    const diagnosticFailed = outcome?.diagnostic_quality === "failed";
    const diagnosticUnassessed = outcome?.diagnostic_quality === "not_assessed";
    const diagnosticWarning = outcome?.diagnostic_quality === "warning";
    const outstanding = Array.isArray(outcome?.outstanding_roles)
      ? outcome.outstanding_roles.length
      : 0;
    const counts = [
      `${formatNumber(summary.table_count || 0)} 表`,
      `${formatNumber(summary.figure_count || 0)} 图`,
      `${formatNumber(summary.text_count || 0)} 文本`,
    ];
    if (Number(summary.model_count || 0) > 0) {
      counts.push(`${formatNumber(summary.model_count || 0)} 模型`);
    }
    return {
      complete,
      statusText: failed ? "本轮执行未完成；已保留结果与执行依据"
        : complete
        ? diagnosticFailed ? "产物齐全；方法诊断未通过"
          : diagnosticWarning ? "产物齐全；方法诊断有警告，统计结论待复核"
          : diagnosticUnassessed ? "产物齐全；部分诊断未评估，统计结论待复核"
          : "产物齐全；统计结论待复核"
        : outstanding > 0
          ? `仍有 ${formatNumber(outstanding)} 项科研产物待补齐`
          : "本轮仍有科研产物待补齐",
      actionLabel: failed ? "修复执行" : complete ? "继续分析" : "补齐产物",
      countsText: counts.join(" · "),
      validationText: "自动校验范围：产物文件与结构；科学审核：尚未进行",
    };
  }

  function appendResultCompletionCard(target, turnId = "", outcome = null) {
    if (!target || outcome?.analysis_status === "conversation_only") return;
    const requestedTurnId = String(turnId || "").trim();
    const turn = requestedTurnId
      ? resultTurns().find((item) => item.turn_id === requestedTurnId) || null
      : activeTurn();
    const continuationTurnId = requestedTurnId || turn?.turn_id || "";
    const presentation = resultCompletionPresentation(turn);
    const canViewTurn = Boolean(turn?.turn_id);
    const card = document.createElement("div");
    card.className = "rsw-analysis-complete-card";
    card.innerHTML = `
      <strong>${escapeHtml(turn?.title || presentation.statusText)}</strong>
      <span>${escapeHtml(presentation.statusText)} · ${escapeHtml(presentation.countsText)}</span>
      <span>${escapeHtml(presentation.validationText)}</span>
      <div class="rsw-artifact-actions">
        <button type="button" class="rsw-artifact-action is-active" data-view-turn="${escapeHtml(turn?.turn_id || "")}" ${canViewTurn ? "" : "disabled"}>查看本轮结果</button>
        <button type="button" class="rsw-artifact-action" data-continue-analysis="${escapeHtml(continuationTurnId)}">${escapeHtml(presentation.actionLabel)}</button>
      </div>`;
    target.appendChild(card);
    renderRunControls();
  }

  function continuationPrompt(turnId = "") {
    const requestedTurnId = String(turnId || "").trim();
    const turn = requestedTurnId
      ? resultTurns().find((item) => item.turn_id === requestedTurnId) || null
      : activeTurn();
    const liveContext = state.continuationContexts[requestedTurnId || turn?.turn_id || ""] || {};
    const turnTitle = String(turn?.title || liveContext.title || "").trim();
    const templateId = String(turn?.template_id || liveContext.templateId || "").trim();
    const target = templateId ? `模板「${templateId}」` : "当前分析";
    return [
      `请继续所选分析轮次${turnTitle ? `「${turnTitle}」` : ""}尚未完成的工作。`,
      `沿用当前上传的数据、已确认的变量映射和已有结果，继续完成${target}的既定目标，不要重复已经完成的步骤。`,
      "优先执行该轮结果中最直接且最符合当前目标的下一步建议；如果有多个方向，请自动选择无需新增用户信息、并能补全模板既定表格、图形和解释的方案。",
      "仅当缺少无法从现有数据与上下文推断的关键输入时，再提出一个明确问题。",
    ].join("\n");
  }

  function continuationTemplateContext(turnId = "") {
    const requestedTurnId = String(turnId || "").trim();
    const turn = requestedTurnId
      ? resultTurns().find((item) => item.turn_id === requestedTurnId) || null
      : activeTurn();
    const liveContext = state.continuationContexts[requestedTurnId || turn?.turn_id || ""] || {};
    const templateId = String(turn?.template_id || liveContext.templateId || "").trim();
    const executionRefs = Array.isArray(turn?.execution_refs) ? turn.execution_refs : [];
    const storedPlanId = [...executionRefs].reverse().find((item) => item?.plan_id)?.plan_id || "";
    const planId = String(storedPlanId || liveContext.planId || "").trim();
    if (!planId) return null;
    if (templateId) return { id: templateId, plan_id: planId };
    return {
      id: "",
      plan_id: planId,
      title: String(turn?.title || liveContext.title || "继续当前分析"),
    };
  }

  function isChatImageFile(file) {
    const type = String(file?.type || "").toLowerCase();
    const name = String(file?.name || "").toLowerCase();
    return ["image/png", "image/jpeg", "image/gif", "image/webp"].includes(type)
      || /\.(png|jpe?g|gif|webp)$/.test(name);
  }

  function renderChatImageChips() {
    const row = $("rsw-chat-image-chips");
    if (!row) return;
    const items = state.pendingChatImages || [];
    row.hidden = items.length === 0;
    row.innerHTML = items.map((item, index) => `
      <span class="rsw-chat-image-chip">
        <img src="${escapeHtml(item.url)}" alt="">
        <em>${escapeHtml(item.name || "图片")}</em>
        <button type="button" data-remove-chat-image="${index}" aria-label="移除图片">×</button>
      </span>`).join("");
  }

  function clearPendingChatImages() {
    (state.pendingChatImages || []).forEach((item) => {
      if (item.url) URL.revokeObjectURL(item.url);
    });
    state.pendingChatImages = [];
    renderChatImageChips();
  }

  function addPendingChatImages(fileList) {
    try {
      assertAttachedImagesAllowed();
    } catch (error) {
      toast(error.message, "error");
      return false;
    }
    const incoming = Array.from(fileList || []).filter(isChatImageFile);
    if (!incoming.length) {
      toast("只支持 JPEG、PNG、GIF 或 WebP 图片", "error");
      return;
    }
    const existing = state.pendingChatImages || [];
    const room = Math.max(0, 6 - existing.length);
    incoming.slice(0, room).forEach((file) => {
      if (file.size > 8 * 1024 * 1024) {
        toast(`${file.name || "图片"}超过 8MB`, "error");
        return;
      }
      existing.push({
        file,
        name: file.name || "图片",
        url: URL.createObjectURL(file),
      });
    });
    state.pendingChatImages = existing;
    renderChatImageChips();
    if (incoming.length > room) toast("一次最多附带 6 张图片", "error");
  }

  async function uploadPendingChatImages() {
    const items = state.pendingChatImages || [];
    if (!items.length) return [];
    assertAttachedImagesAllowed();
    const form = new FormData();
    items.forEach((item) => form.append("file", item.file, item.name || "image.png"));
    const payload = await requestJson(`/api/session/${encodeURIComponent(state.sid)}/chat/images`, {
      method: "POST",
      body: form,
    });
    return (payload.images || []).map((item) => item.id).filter(Boolean);
  }

  function tryStartChatRequest() {
    if (state.streaming || state.canceling || state.chatStarting || state.modelSharingSaving) {
      toast("当前分析仍在运行，请完成后再继续。", "error");
      return false;
    }
    state.chatStarting = true;
    renderRunControls();
    return true;
  }

  async function continueCurrentAnalysis(turnId = "", sendMessage = streamChat) {
    if (!tryStartChatRequest()) return false;
    setWorkbenchView("assistant");
    state.activeTemplateContext = continuationTemplateContext(turnId);
    try {
      await sendMessage(continuationPrompt(turnId), { lockAcquired: true, attachPending: false });
      return true;
    } finally {
      state.chatStarting = false;
      renderRunControls();
    }
  }

  async function continueAnalysisFromButton(button, sendMessage = streamChat) {
    if (!button) return false;
    return continueCurrentAnalysis(button.dataset.continueAnalysis || "", sendMessage);
  }

  function resetMessages(message) {
    const list = $("rsw-message-list");
    if (!list) return;
    list.innerHTML = `
      <article class="rsw-message rsw-message-ai">
        <div class="rsw-avatar" aria-hidden="true">
          <svg viewBox="0 0 24 24"><path d="M12 3l2.3 5.2L20 10.5l-5.7 2.1L12 18l-2.3-5.4L4 10.5l5.7-2.3z" /></svg>
        </div>
        <div class="rsw-bubble"><div class="rsw-message-content"></div></div>
      </article>`;
    renderMessageContent(
      qs(".rsw-message-ai .rsw-message-content", list),
      message || "你好，我是你的科研统计助手。上传数据后，我会先检查变量类型、样本量、缺失情况和潜在分组变量，再根据你的研究问题生成分析、模型、图表和结果解释。"
    );
  }

  async function startNewSession() {
    await clearPersistedResultState();
    state.sid = "";
    resetModelSharingForSession();
    state.datasets = [];
    state.activeDataset = null;
    state.activeSummary = null;
    state.analysisResult = null;
    state.resultState = null;
    state.activeTurnId = "";
    state.readiness = null;
    state.history = [];
    state.analysisTurnId = "";
    state.activeTemplateContext = null;
    state.activeAnalysisPlan = null;
    state.activePreparationPlan = null;
    state.preparationResumeTemplateId = "";
    state.preparationResumePrompt = "";
    state.dependencyGate = null;
    state.dependencyResume = null;
    closeDependencyGate({ clear: true });
    state.canceling = false;
    state.chatStarting = false;
    state.continuationContexts = {};
    state.analysisTimeline = [];
    state.turnEvidence = {};
    state.pendingTemplateId = "";
    clearPendingChatImages();
    state.templatePreparations = {};
    state.templateInputValues = {};
    state.templatePrepareLoading = "";
    templatePreparePromises.clear();
    state.showAllCurrentOutputs = false;
    state.selectedTableIndex = 0;
    state.selectedFigureIndex = 0;
    state.resultScope = "latest";
    setWorkbenchView("assistant");
    resetMessages("已创建新的科研分析会话。请上传数据开始分析。");
    renderAll();
    setRunState("创建中", "准备新会话", "busy");
    await ensureSession();
    state.history = readStoredHistory();
    await loadReadiness();
    setStatus(
      isAiConfigured() ? "idle" : "warning",
      isAiConfigured() ? "新会话已创建" : "AI 模型待配置",
      isAiConfigured() ? "请上传科研数据开始分析。" : "输入 DeepSeek API Key 即可启用 AI 分析助手。",
      isAiConfigured() ? "" : "configure-ai"
    );
    setRunState("空闲", "就绪");
    toast("新会话已创建", "ok");
  }

  async function stopActiveAnalysis() {
    if (!(state.streaming || state.inspecting) || state.canceling || !state.sid) return;
    state.canceling = true;
    setRunState("正在终止", "正在停止 AI 与本地 R 执行", "busy");
    setStatus("busy", "正在终止", "正在结束模型请求、R 进程和当前分析任务");
    renderRunControls();
    try {
      await requestJson(`/api/session/${encodeURIComponent(state.sid)}/stop`, { method: "POST" });
    } catch (error) {
      state.canceling = false;
      setRunState("运行中", "停止请求未送达，分析仍在运行", "busy");
      renderRunControls();
      throw error;
    }
  }

  async function streamChat(message, { lockAcquired = false, imageIds = null, attachPending = true } = {}) {
    const pending = attachPending ? (state.pendingChatImages || []) : [];
    const reusedIds = Array.isArray(imageIds) ? imageIds.filter(Boolean) : [];
    const outgoingText = String(message || "").trim();
    if (!outgoingText && !pending.length && !reusedIds.length) return;
    if (!lockAcquired && !tryStartChatRequest()) return;
    let target = null;
    let streamingRenderer = null;
    let heartbeatTimer = null;
    let analysisStartedAt = 0;
    let analysisPhase = "AI 正在分析";
    const renderFinalMessage = (value) => {
      if (streamingRenderer) streamingRenderer.flush(value);
      else renderMessageContent(target, value);
    };
    const setAnalysisPhase = (detail) => {
      analysisPhase = detail || "AI 正在分析";
      if (state.streaming) {
        const elapsed = analysisStartedAt ? ` · ${formatAnalysisElapsed(analysisStartedAt)}` : "";
        setRunState("运行中", `${analysisPhase}${elapsed}`, "busy");
        const top = $("rsw-top-stage");
        if (top) top.textContent = analysisPhase;
      }
    };

    try {
      await ensureSession();
      if (pending.length || reusedIds.length) assertAttachedImagesAllowed();
      await loadReadiness().catch(() => {});
      if (!isAiConfigured()) {
        state.lastAction = { type: "chat", message: outgoingText, imageIds: reusedIds };
        setStatus("warning", "AI 模型待配置", "输入 DeepSeek API Key 即可启用 AI 分析助手。", "configure-ai");
        openAiConfigDialog();
        return;
      }
      const templateContext = state.activeTemplateContext;
      state.activeTemplateContext = null;
      if (!templateContext) state.activeAnalysisPlan = null;
      let attachedIds = reusedIds;
      const previewUrls = pending.map((item) => item.url).filter(Boolean);
      if (!attachedIds.length && pending.length) {
        attachedIds = await uploadPendingChatImages();
      }
      const displayMessage = outgoingText || "请解读这些图片，并说明它们与当前分析的关系。";
      state.lastAction = { type: "chat", message: outgoingText, templateContext, imageIds: attachedIds };
      setWorkbenchView("assistant");
      appendMessage("user", displayMessage, { imageUrls: previewUrls });
      if (attachPending) {
        state.pendingChatImages = [];
        renderChatImageChips();
      }
      target = appendMessage("ai", "正在准备分析...");
      streamingRenderer = createStreamingMessageRenderer(target);
      state.streaming = true;
      state.canceling = false;
      state.toolAuditSequence = 0;
      state.recoveringRStep = false;
      state.rRepairStopMessage = "";
      analysisStartedAt = Date.now();
      state.analysisTurnId = `turn-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
      state.continuationContexts[state.analysisTurnId] = {
        templateId: String(templateContext?.id || state.activeAnalysisPlan?.template_id || ""),
        planId: String(templateContext?.plan_id || state.activeAnalysisPlan?.plan_id || ""),
        title: String(templateContext?.title || state.activeAnalysisPlan?.intent || ""),
      };
      state.resultScope = "latest";
      state.showAllCurrentOutputs = false;
      resetAnalysisTimeline();
      recordTimelineStep("preflight", "检查数据", "active");
      renderAll();
      setAnalysisPhase("AI 正在规划统计步骤");
      heartbeatTimer = setInterval(() => setAnalysisPhase(analysisPhase), 1000);
      setStatus("busy", "AI 正在分析", "正在生成 R 代码并执行统计分析");
      const response = await apiFetch(`/api/session/${encodeURIComponent(state.sid)}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({
          message: outgoingText,
          image_ids: attachedIds,
          template_context: templateContext,
          turn_id: state.analysisTurnId,
          dataset_id: state.activeDataset?.dataset_id || "",
        }),
      });
      if (!response.ok || !response.body) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.error || `分析请求失败：HTTP ${response.status}`);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let text = "";
      let analysisOutcome = null;
      let stepCount = 0;
      let statusOnlyFinal = false;
      let deferredOperationalText = "";
      let toolAuditChain = Promise.resolve();
      const updatePendingMessage = (messageText) => {
        if (target && !text) streamingRenderer.flush(messageText);
      };
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop() || "";
        for (const raw of events) {
          const line = raw.split("\n").find((item) => item.startsWith("data:"));
          if (!line) continue;
          const event = JSON.parse(line.slice(5).trim());
          if (event.type === "analysis_turn") {
            state.analysisTurnId = event.turn_id || state.analysisTurnId;
            state.activeTurnId = state.analysisTurnId;
            const priorContinuation = state.continuationContexts[state.analysisTurnId] || {};
            state.continuationContexts[state.analysisTurnId] = {
              ...priorContinuation,
              templateId: String(event.template_id || priorContinuation.templateId || ""),
              planId: String(event.analysis_plan?.plan_id || priorContinuation.planId || ""),
              title: String(event.title || priorContinuation.title || ""),
            };
            if (event.analysis_plan) {
              state.activeAnalysisPlan = {
                ...event.analysis_plan,
                template_id: event.template_id || "",
              };
            }
            renderAnalysisPlan();
          } else if (event.type === "text_delta") {
            text += event.content || "";
            if (analysisPhase !== "AI 正在撰写结果解释") setAnalysisPhase("AI 正在撰写结果解释");
            recordTimelineStep("publish", "解释", "active");
            streamingRenderer.update(text || "正在生成解释...");
          } else if (event.type === "text") {
            if (!normalizeMarkdownText(text) && isOperationalContinuationText(event.content)) {
              statusOnlyFinal = true;
              deferredOperationalText = event.content || "";
              streamingRenderer.flush(CONTINUATION_RESULT_MESSAGE);
              continue;
            }
            if (text && event.content && event.content.startsWith(text)) {
              text = event.content;
            } else {
              text += event.content || "";
            }
            streamingRenderer.flush(text || "正在生成解释...");
          } else if (event.type === "tool_progress") {
            setAnalysisPhase(event.content || "R 执行中");
          } else if (event.type === "tool_start") {
            stepCount += 1;
            setAnalysisPhase(event.display || event.tool || "正在执行分析工具");
            const phaseId = toolTimelineId(event.tool);
            if (phaseId) recordTimelineStep(phaseId, event.display || PHASE_LABELS[phaseId], "active");
            updatePendingMessage(`正在执行第 ${stepCount} 个步骤：${event.display || event.tool || "调用分析工具"}...`);
          } else if (event.type === "tool_audit") {
            const payload = parseToolPayload(event.content);
            const result = payload?.result || {};
            applyPhaseStatus(event.phase_status || payload?.execution?.workflow?.phase_status);
            if (dependencyGateNeedsAction(payload?.dependency_gate)) {
              setAnalysisPhase("等待本机分析依赖");
              updatePendingMessage(payload.dependency_gate.headline || "当前分析需要先处理本机依赖。");
              toolAuditChain = toolAuditChain.then(() => handleResearchToolAudit(event));
              continue;
            }
            if (payload?.preparation_plan) {
              setAnalysisPhase("当前数据需要先完成分析前准备");
              updatePendingMessage(payload.preparation_plan.headline || "已生成数据准备方案，等待处理后数据。");
            }
            if (payload?.policy_blocked) {
              setAnalysisPhase("AI 正在整理已生成产物");
              updatePendingMessage("本轮已生成主要结果，AI 正在整理可用产物...");
              continue;
            }
            if (event.tool === "run_r_code" || event.tool === "inspect_dataset") {
              recordTurnEvidence({
                ok: event.ok !== false && result.ok !== false,
                job_id: payload?.job_id || "",
                warnings: result.warnings || [],
                code: result.code || "",
                stdout: result.stdout || "",
                stderr: result.stderr || event.error || "",
                error: result.error || event.error || "",
                tables: result.tables,
                figures: result.figures,
                texts: result.texts,
                models: result.models,
                sourceLabel: event.tool,
              });
            }
            if (payload?.preparation_plan) {
              // Preflight is an actionable readiness state, not a transient R
              // error. The audit handler renders the preparation panel.
            } else if (event.ok === false || result.ok === false) {
              setAnalysisPhase("AI 正在修正分析步骤");
              updatePendingMessage("一个中间 R 步骤返回异常，AI 正在修正并继续执行...");
            } else if (resultArtifactTotal(result) > 0) {
              setAnalysisPhase("AI 正在校验结果并决定下一步");
              updatePendingMessage(`R 已生成 ${result.tables?.length || 0} 个表格、${result.figures?.length || 0} 张图形、${result.models?.length || 0} 个模型和 ${result.texts?.length || 0} 段文本，AI 正在整理最终解释...`);
            } else if (event.tool !== "run_r_code") {
              setAnalysisPhase("AI 正在确认分析能力");
              updatePendingMessage("AI 正在确认方法依据和可用分析能力...");
            } else {
              setAnalysisPhase("AI 正在继续分析");
              updatePendingMessage("R 已完成一个检查步骤，AI 正在继续分析...");
            }
            toolAuditChain = toolAuditChain.then(() => handleResearchToolAudit(event));
          } else if (event.type === "analysis_outcome") {
            analysisOutcome = event;
          } else if (event.type === "stopped" || event.type === "job_canceled") {
            const canceled = new Error("分析已停止");
            canceled.code = "analysis_canceled";
            throw canceled;
          } else if (event.type === "error") {
            const streamError = new Error(event.message || "分析失败");
            streamError.code = event.code || "";
            throw streamError;
          }
        }
      }
      await toolAuditChain;
      if (state.activePreparationPlan) {
        state.streaming = false;
        const guidance = text || state.activePreparationPlan.headline || "当前数据需要先完成分析前准备。";
        if (target) streamingRenderer.flush(guidance);
        await loadResultState({ silent: true }).catch(() => {});
        setWorkbenchView("assistant");
        setRunState("待准备", "等待处理后数据");
        setStatus(
          "warning",
          "当前数据需要先准备",
          state.activePreparationPlan.summary || "完成准备并重新导入后，可返回原分析。"
        );
        renderAll();
        return;
      }
      if (dependencyGateNeedsAction(state.dependencyGate) && state.dependencyResume?.type === "analysis") {
        state.streaming = false;
        const gate = state.dependencyGate;
        if (target && !text) streamingRenderer.flush(gate.headline || "当前分析需要先处理本机依赖。");
        setRunState("待处理", gate.headline || "等待本机依赖");
        setStatus(
          "warning",
          gate.headline || "分析依赖尚未就绪",
          gate.summary || "完成处理并重新检测后，可恢复当前分析计划。"
        );
        renderDependencyGate();
        return;
      }
      const unresolvedRRepair = state.recoveringRStep;
      if (unresolvedRRepair && blockingResultFailure(state.analysisResult?.payload?.result)) {
        const rError = new Error(
          state.rRepairStopMessage
          || state.analysisResult?.payload?.result?.error
          || "最后一个 R 步骤未能自动修正"
        );
        rError.code = "r_analysis_failed";
        throw rError;
      }
      state.streaming = false;
      const hasCurrentArtifacts = currentAnalysisArtifactTotal() > 0;
      if (statusOnlyFinal && hasCurrentArtifacts && target) {
        streamingRenderer.flush(CONTINUATION_RESULT_MESSAGE);
      } else if (statusOnlyFinal && deferredOperationalText) {
        text = deferredOperationalText;
        streamingRenderer.flush(text);
      } else if (target && !text) {
        streamingRenderer.flush(
          state.analysisResult
            ? (analysisOutcome?.completed === false ? "本轮分析尚未完成，已生成的结果已保留。" : "本轮产物已保存；本次没有返回额外文字解释。")
            : "分析完成，但没有返回可展示文本。"
        );
      } else if (target) {
        streamingRenderer.flush(text);
      }
      attachFinalAnswerToResults(statusOnlyFinal && hasCurrentArtifacts ? "" : text);
      await loadResultState({ silent: true }).catch(() => {});
      renderAll();
      notifyResultsReady();
      appendResultCompletionCard(target, state.analysisTurnId, analysisOutcome);
      if (analysisOutcome) {
        showAnalysisOutcome(analysisOutcome);
        return;
      }
      setRunState(unresolvedRRepair ? "需确认" : "空闲", unresolvedRRepair ? "部分 R 步骤未修正" : "分析完成", unresolvedRRepair ? "" : "ok");
      setStatus(
        unresolvedRRepair ? "warning" : (state.analysisResult ? "ok" : "warning"),
        unresolvedRRepair ? "分析生成了部分结果" : (state.analysisResult ? "分析完成" : "分析未生成结构化结果"),
        unresolvedRRepair
          ? "可用产物已保留，但仍有一个 R 步骤未自动修正；请查看执行依据或继续分析。"
          : (state.analysisResult ? "结果已进入分析结果页，可继续分析或查看本轮结果。" : "请换一种描述，或要求 AI 保存表格、图形或文本结果。")
      );
    } catch (error) {
      if (error?.code === "analysis_canceled" || state.canceling) {
        state.streaming = false;
        state.canceling = false;
        renderFinalMessage(
          currentAnalysisArtifactTotal() > 0
            ? "分析已停止。停止前已生成的可用结果仍保留在结果页。"
            : "分析已停止，本轮未生成可用结果。"
        );
        await loadResultState({ silent: true }).catch(() => {});
        setRunState("已停止", "分析已停止");
        setStatus("warning", "分析已停止", "这是用户取消，不是分析失败；已生成的本地产物继续保留。");
        renderAll();
        appendResultCompletionCard(target, state.analysisTurnId);
        return;
      }
      if (error?.code === "invalid_api_key") {
        state.aiKeyInvalid = true;
        renderFinalMessage(`分析暂未完成：${error.message}`);
        setRunState("错误", "DeepSeek API Key 无效", "error");
        setStatus(
          "error",
          "DeepSeek API Key 无效",
          "请在系统设置中重新填写有效的 API Key；重新保存时会先自动验证连接。",
          "configure-ai"
        );
        openAiConfigDialog();
        return;
      }
      if (currentAnalysisArtifactTotal() > 0 && isRecoverableStopError(error)) {
        renderFinalMessage(CONTINUATION_RESULT_MESSAGE);
        await loadResultState({ silent: true }).catch(() => {});
        renderAll();
        notifyResultsReady();
        appendResultCompletionCard(target, state.analysisTurnId);
        setRunState("空闲", "结果已更新", "ok");
        setStatus("ok", "结果已更新", "本次已生成可用结果，可以继续下一项分析。");
        return;
      }
      renderFinalMessage(`分析暂未完成：${error.message}`);
      setRunState("错误", error.message || "请检查模型配置或日志", "error");
      const classified = error?.code === "r_analysis_failed"
        ? { kind: "error", title: "R 分析未完成", detail: error.message }
        : classifyError(error);
      setStatus(classified.kind, classified.title, classified.detail, "retry");
      toast(error.message, "error");
    } finally {
      if (heartbeatTimer !== null) clearInterval(heartbeatTimer);
      streamingRenderer?.cancel();
      state.chatStarting = false;
      state.streaming = false;
      state.canceling = false;
      state.recoveringRStep = false;
      state.rRepairStopMessage = "";
      state.analysisTurnId = "";
      renderAll();
      if ($("rsw-dependency-modal")?.hidden === false) renderDependencyGate();
    }
  }

  function handleUiError(error, fallbackTitle = "操作未完成") {
    const classified = classifyError(error);
    setStatus(classified.kind || "error", classified.title || fallbackTitle, classified.detail || String(error?.message || error), "retry");
    toast(error.message || String(error), "error");
  }

  function showAnalysisOutcome(outcome) {
    const incomplete = outcome.completed === false;
    const conversationOnly = outcome.analysis_status === "conversation_only";
    const diagnosticsPending = !conversationOnly && outcome.diagnostic_quality !== "passed";
    const title = incomplete ? "分析暂未完成" : conversationOnly ? "对话完成" : "分析产物已齐全";
    const detail = incomplete
      ? "已保留本轮结果与执行依据，可继续完成当前方案。"
      : conversationOnly ? "本轮问答已结束。"
      : outcome.diagnostic_quality === "failed" ? "方法诊断未通过，请检查诊断并限制结论或调整方案。"
      : diagnosticsPending ? "方法诊断仍需确认，科研结论需研究者复核。"
      : "产物结构和方法诊断已检查，科研结论需研究者复核。";
    setRunState(incomplete || diagnosticsPending ? "需确认" : "空闲", title, incomplete || diagnosticsPending ? "" : "ok");
    setStatus(incomplete || diagnosticsPending ? "warning" : "ok", title, detail);
  }

  // Reconnect to a conversation analysis that is still running on the server
  // (page refresh / network drop no longer cancel it). The server keeps the
  // analysis alive and replays everything published so far (after=0), then
  // tails the live stream until the turn finishes.
  async function reattachRunningAnalysis() {
    if (!state.sid || state.streaming) return false;
    let jobsPayload;
    try {
      jobsPayload = await requestJson(
        `/api/session/${encodeURIComponent(state.sid)}/jobs?active=true&limit=20`
      );
    } catch {
      return false;
    }
    const runningJob = (jobsPayload?.jobs || []).find(
      (job) => job.type === "conversation_analysis" && ["queued", "running"].includes(job.status)
    );
    if (!runningJob) return false;

    setWorkbenchView("assistant");
    state.streaming = true;
    state.canceling = false;
    state.toolAuditSequence = 0;
    state.recoveringRStep = false;
    state.rRepairStopMessage = "";
    resetAnalysisTimeline();
    recordTimelineStep("preflight", "重连分析", "active");
    setRunState("运行中", "正在重新连接进行中的分析…", "busy");
    setStatus("busy", "分析仍在进行", "页面已重新连接到正在运行的统计分析；完成后结果自动展示。");
    renderAll();

    const target = appendMessage("ai", "正在恢复进行中的分析...");
    const streamingRenderer = createStreamingMessageRenderer(target);
    let text = "";
    let analysisOutcome = null;
    let stepCount = 0;
    let toolAuditChain = Promise.resolve();
    const setAttachPhase = (detail) => {
      setRunState("运行中", `${detail}（已重连）`, "busy");
      const top = $("rsw-top-stage");
      if (top) top.textContent = detail;
    };
    const finalizeAttach = async (finalText) => {
      state.streaming = false;
      if (target) streamingRenderer.flush(finalText || text || "连接已结束，请检查本轮结果与后台任务状态。");
      await toolAuditChain.catch(() => {});
      await loadResultState({ silent: true }).catch(() => {});
      renderAll();
    };

    try {
      const response = await apiFetch(
        `/api/session/${encodeURIComponent(state.sid)}/jobs/${encodeURIComponent(runningJob.id)}/stream?after=0`,
        { headers: { Accept: "text/event-stream" } }
      );
      if (!response.ok || !response.body) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.error || `重连失败：HTTP ${response.status}`);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop() || "";
        for (const raw of events) {
          const line = raw.split("\n").find((item) => item.startsWith("data:"));
          if (!line) continue;
          let event;
          try {
            event = JSON.parse(line.slice(5).trim());
          } catch {
            continue;
          }
          if (event.type === "analysis_turn") {
            state.analysisTurnId = event.turn_id || state.analysisTurnId;
            state.activeTurnId = state.analysisTurnId;
            if (event.analysis_plan) {
              state.activeAnalysisPlan = {
                ...event.analysis_plan,
                template_id: event.template_id || "",
              };
            }
            renderAnalysisPlan();
          } else if (event.type === "text_delta") {
            text += event.content || "";
            recordTimelineStep("publish", "解释", "active");
            streamingRenderer.update(text || "正在生成解释...");
          } else if (event.type === "text") {
            if (text && event.content && event.content.startsWith(text)) text = event.content;
            else text += event.content || "";
            streamingRenderer.flush(text || "正在生成解释...");
          } else if (event.type === "tool_start") {
            stepCount += 1;
            setAttachPhase(event.display || event.tool || "正在执行分析工具");
            const phaseId = toolTimelineId(event.tool);
            if (phaseId) recordTimelineStep(phaseId, event.display || PHASE_LABELS[phaseId], "active");
          } else if (event.type === "tool_audit") {
            const payload = parseToolPayload(event.content);
            const result = payload?.result || {};
            applyPhaseStatus(event.phase_status || payload?.execution?.workflow?.phase_status);
            if (event.tool === "run_r_code" || event.tool === "inspect_dataset") {
              recordTurnEvidence({
                ok: event.ok !== false && result.ok !== false,
                job_id: payload?.job_id || "",
                warnings: result.warnings || [],
                code: result.code || "",
                stdout: result.stdout || "",
                stderr: result.stderr || event.error || "",
                error: result.error || event.error || "",
                tables: result.tables,
                figures: result.figures,
                texts: result.texts,
                models: result.models,
                sourceLabel: event.tool,
              });
            }
            toolAuditChain = toolAuditChain.then(() => handleResearchToolAudit(event));
            setAttachPhase("AI 正在继续分析");
          } else if (event.type === "analysis_outcome") {
            analysisOutcome = event;
          } else if (event.type === "tool_progress") {
            setAttachPhase(event.content || "R 执行中");
          } else if (event.type === "stopped" || event.type === "job_canceled") {
            await finalizeAttach("分析已停止。停止前已生成的可用结果仍保留在结果页。");
            setRunState("已停止", "分析已停止");
            return true;
          } else if (event.type === "error") {
            await finalizeAttach(`分析暂未完成：${event.message || "分析失败"}`);
            setRunState("错误", event.message || "分析失败", "error");
            return true;
          } else if (event.type === "done") {
            await finalizeAttach(text || (analysisOutcome?.completed === false ? "本轮分析尚未完成，已生成的结果已保留。" : "本轮产物已保存到分析结果页。"));
            if (analysisOutcome) showAnalysisOutcome(analysisOutcome);
            else setRunState("需确认", "本轮对话已结束，请检查结果状态");
            return true;
          }
        }
      }
      // Stream closed without a done frame (e.g. server restart); buffered
      // results survive on disk and were reloaded above.
      await finalizeAttach("");
      setRunState("需确认", "连接已结束，请检查本轮结果与后台任务状态");
      return true;
    } catch (error) {
      state.streaming = false;
      streamingRenderer.flush(text || "重连已中断；分析仍在后台运行，可稍后刷新页面重新查看。");
      setRunState("运行中", "重连中断，分析仍在后台运行", "busy");
      return true;
    }
  }

  async function clearConversation() {
    if (!state.sid) return;
    await requestJson(`/api/session/${encodeURIComponent(state.sid)}/clear`, { method: "POST" });
    state.history = [];
    sessionStorage.removeItem(sessionHistoryKey());
    resetMessages("当前对话已清空。数据集和已生成结果仍保留，你可以继续提出分析问题。");
    setStatus("ok", "对话已清空", "服务端对话上下文已同步清除。");
  }

  async function loadCurrentConversation() {
    if (!state.sid) return;
    const payload = await requestJson(`/api/session/${encodeURIComponent(state.sid)}/load-current`);
    const visible = (payload.history || []).filter((item) =>
      item && ["user", "assistant"].includes(item.role) && typeof item.content === "string" && item.content.trim()
    );
    if (!visible.length) return;
    const list = $("rsw-message-list");
    if (list) list.innerHTML = "";
    visible.slice(-40).forEach((item) => appendMessage(item.role === "user" ? "user" : "ai", item.content));
  }

  async function retryLastAction() {
    const action = state.lastAction;
    if (state.status?.action === "configure-ai") {
      openAiConfigDialog();
      return;
    }
    if (!action) {
      await loadReadiness();
      setStatus("ok", "状态已刷新", "运行环境状态已重新读取。");
      return;
    }
    if (state.status?.action === "refresh") {
      await loadReadiness();
      setStatus(isAiConfigured() ? "ok" : "warning", isAiConfigured() ? "DeepSeek 已启用" : "AI 模型待配置", isAiConfigured() ? "可以运行真实分析。" : "输入 DeepSeek API Key 即可启用 AI 分析助手。", isAiConfigured() ? "" : "configure-ai");
      return;
    }
    if (action.type === "upload" && action.files?.length) {
      await uploadFiles(action.files);
      return;
    }
    if (action.type === "local-import") {
      await importLocalSelection(action.kind || "files");
      return;
    }
    if (action.type === "inspect" && action.datasetId) {
      await inspectDataset(action.datasetId);
      return;
    }
    if (action.type === "chat" && (action.message || action.imageIds?.length)) {
      state.activeTemplateContext = action.templateContext || null;
      await streamChat(action.message || "", { imageIds: action.imageIds || [], attachPending: false });
      return;
    }
    await loadReadiness();
  }

  async function openTemplateLibrary() {
    const modal = $("rsw-template-modal");
    if (!modal) return;
    rememberModalFocus();
    modal.hidden = false;
    document.body.classList.add("rsw-modal-open");
    scheduleTemplateLibraryRender();
    setTimeout(() => $("rsw-template-search")?.focus(), 30);
    loadAnalysisTemplates()
      .then(async () => {
        scheduleTemplateLibraryRender();
        if (state.activeDataset && state.templateSelectedId && !templatePreparation(state.templateSelectedId)) {
          await prepareTemplate(state.templateSelectedId, { silent: true });
        }
      })
      .catch((error) => {
        scheduleTemplateLibraryRender();
        toast(`模板库使用本地基础版本：${error.message}`, "error");
      });
  }

  function closeTemplateLibrary() {
    const modal = $("rsw-template-modal");
    if (!modal) return;
    modal.hidden = true;
    document.body.classList.remove("rsw-modal-open");
    restoreModalFocus();
  }

  function dependencyGateNeedsAction(gate) {
    return Boolean(gate && gate.status && gate.status !== "ready");
  }

  function dependencyStatusLabel(status) {
    return {
      ready: "已就绪",
      warning: "可用但有警告",
      missing: "未安装",
      load_error: "加载失败",
      needs_setup: "需配置运行组件",
      installable: "可安装",
      repair_required: "需修复",
      unsupported: "当前机器不支持",
      unknown_package: "名称不存在",
      package_names_unresolved: "包名待修正",
    }[status] || "待处理";
  }

  function renderDependencyGate() {
    const modal = $("rsw-dependency-modal");
    const gate = state.dependencyGate;
    if (!modal || !gate) return;
    const title = $("rsw-dependency-title");
    const summary = $("rsw-dependency-summary");
    if (title) title.textContent = gate.headline || "分析依赖确认";
    if (summary) summary.textContent = gate.summary || "完成依赖处理后可继续当前分析计划。";

    const selected = new Set(Array.isArray(gate.selected_packages) ? gate.selected_packages : []);
    const packageItems = (Array.isArray(gate.package_readiness) ? gate.package_readiness : [])
      .filter((item) => gate.mode !== "candidate" || selected.has(item.package));
    const packageSection = $("rsw-dependency-packages");
    const packageList = $("rsw-dependency-package-list");
    if (packageSection) packageSection.hidden = packageItems.length === 0;
    if (packageList) {
      packageList.innerHTML = packageItems.map((item) => {
        const status = String(item.status || "missing");
        const detail = item.error || (Array.isArray(item.warnings) ? item.warnings.join("；") : "");
        return `<article class="rsw-dependency-item">
          <strong>${escapeHtml(item.package || "R 包")}</strong>
          <span class="${status === "ready" ? "is-ready" : ""}">${escapeHtml(dependencyStatusLabel(status))}</span>
          ${detail ? `<p>${escapeHtml(detail)}</p>` : ""}
        </article>`;
      }).join("");
    }

    const externalItems = (Array.isArray(gate.external_dependencies) ? gate.external_dependencies : [])
      .filter((item) => item.status !== "ready");
    const externalSection = $("rsw-dependency-external");
    const externalList = $("rsw-dependency-external-list");
    if (externalSection) externalSection.hidden = externalItems.length === 0;
    if (externalList) {
      externalList.innerHTML = externalItems.map((item) => `
        <article class="rsw-dependency-item">
          <strong>${escapeHtml(item.label || item.dependency_id || "本机组件")}</strong>
          <span>${escapeHtml(dependencyStatusLabel(item.status))}</span>
          ${item.detail ? `<p>${escapeHtml(item.detail)}</p>` : ""}
          ${item.setup_action ? `<small><b>配置：</b>${escapeHtml(item.setup_action)}</small>` : ""}
          ${item.verification_action ? `<small><b>验证：</b>${escapeHtml(item.verification_action)}</small>` : ""}
        </article>`).join("");
    }

    const note = $("rsw-dependency-note");
    if (note) {
      const repair = Array.isArray(gate.repair_packages) ? gate.repair_packages : [];
      note.textContent = repair.length
        ? `需要修复：${repair.join("、")}。请按加载错误重新安装或修复对应包，完成后点击“重新检测”。`
        : gate.status === "unsupported"
        ? "当前方法依赖此机器不支持的组件。请改用兼容路径，或在支持该组件的机器上运行。"
        : gate.can_install
        ? "安装只会在你确认后进行，并写入系统设置中选择的本机 R 包库；不会修改系统级 R 目录。"
        : "请按上方说明完成本机组件配置，然后点击“重新检测”。";
    }

    const install = qs('[data-dependency-action="install"]', modal);
    const recheck = qs('[data-dependency-action="recheck"]', modal);
    const cancel = qs('[data-dependency-action="cancel-install"]', modal);
    if (cancel) cancel.hidden = !state.dependencyJobId;
    if (note && state.dependencyProgress) note.textContent = state.dependencyProgress;
    const busy = state.dependencyBusy || state.streaming;
    if (install) {
      install.hidden = !gate.can_install;
      install.disabled = busy || !gate.ticket_id;
      install.textContent = state.dependencyBusy ? "正在安装并验证..." : (state.dependencyJobId ? "查看安装进度" : "确认安装缺失 R 包");
    }
    if (recheck) {
      recheck.hidden = !gate.can_recheck;
      recheck.disabled = busy || !gate.ticket_id;
      recheck.textContent = state.dependencyBusy ? "正在检测..." : (state.streaming ? "分析结束后可检测" : "重新检测");
    }
  }

  function openDependencyGate(gate, resume = null) {
    const modal = $("rsw-dependency-modal");
    if (!modal || !dependencyGateNeedsAction(gate)) return;
    if (modal.hidden) rememberModalFocus();
    state.dependencyGate = gate;
    if (resume) state.dependencyResume = resume;
    renderDependencyGate();
    modal.hidden = false;
    document.body.classList.add("rsw-modal-open");
    setTimeout(() => qs('[data-dependency-action="install"]:not([hidden]), [data-dependency-action="recheck"]', modal)?.focus(), 30);
  }

  function closeDependencyGate({ clear = false } = {}) {
    if (state.dependencyBusy) return;
    const modal = $("rsw-dependency-modal");
    if (!modal) return;
    modal.hidden = true;
    state.dependencyBusy = false;
    if (clear) {
      state.dependencyGate = null;
      state.dependencyResume = null;
    }
    const anotherOpen = ["rsw-artifact-modal", "rsw-ai-modal", "rsw-template-modal", "rsw-replace-modal"]
      .some((id) => $(id)?.hidden === false);
    if (!anotherOpen) document.body.classList.remove("rsw-modal-open");
    restoreModalFocus();
  }

  async function resumeDependencyAnalysis(gate, resume) {
    if (!resume || resume.type !== "analysis") return;
    if (!tryStartChatRequest()) return;
    state.activeTemplateContext = {
      id: String(gate.template_id || ""),
      plan_id: String(gate.plan_id || ""),
      title: String(state.activeAnalysisPlan?.intent || "继续当前分析"),
    };
    setWorkbenchView("assistant");
    try {
      await streamChat(
        "本机依赖已完成安装或配置并通过重新检测。请沿用当前冻结分析计划，从被依赖门暂停的步骤继续，不要更换方法或重复已经完成的工作。",
        { lockAcquired: true }
      );
    } finally {
      state.chatStarting = false;
      renderRunControls();
    }
  }

  async function completeDependencyGate(gate) {
    const resume = state.dependencyResume;
    if (resume?.type === "template" && resume.templateId) {
      const preparation = state.templatePreparations[resume.templateId];
      if (preparation) preparation.dependency_gate = gate;
    }
    state.dependencyGate = gate;
    if (dependencyGateNeedsAction(gate)) {
      renderDependencyGate();
      setStatus("warning", gate.headline || "分析依赖尚未就绪", gate.summary || "处理后重新检测即可继续。");
      return;
    }
    closeDependencyGate({ clear: true });
    setStatus("ok", "分析依赖已就绪", "正在恢复此前冻结的分析计划。");
    if (resume?.type === "inspect" && resume.datasetId) {
      await inspectDataset(resume.datasetId);
    } else if (resume?.type === "template" && resume.templateId) {
      await applyPromptTemplate(resume.templateId);
    } else {
      await resumeDependencyAnalysis(gate, resume);
    }
  }

  async function runDependencyAction(action) {
    if (action === "cancel-install") {
      if (!state.dependencyJobId) return;
      try {
        await requestJson(`/api/session/${encodeURIComponent(state.sid)}/jobs/${encodeURIComponent(state.dependencyJobId)}/cancel`, { method: "POST" });
        state.dependencyProgress = "正在停止安装进程；已安装完成的包会保留。";
        renderDependencyGate();
        if (!state.dependencyBusy) await runDependencyAction("check");
      } catch (error) {
        handleUiError(error, "停止安装请求未完成，请重新检测任务状态");
      }
      return;
    }
    const gate = state.dependencyGate;
    if (!gate?.ticket_id || state.dependencyBusy || state.streaming) return;
    state.dependencyBusy = true;
    state.dependencyProgress = "";
    renderDependencyGate();
    try {
      let payload = state.dependencyJobId ? { job_id: state.dependencyJobId } : await requestJson(
        `/api/session/${encodeURIComponent(state.sid)}/research/dependencies/${encodeURIComponent(gate.ticket_id)}/${action}`,
        { method: "POST" }
      );
      if (payload.job_id) {
        state.dependencyJobId = payload.job_id;
        renderDependencyGate();
        while (true) {
          const { job } = await requestJson(`/api/session/${encodeURIComponent(state.sid)}/jobs/${encodeURIComponent(state.dependencyJobId)}`);
          state.dependencyProgress = job.message || "正在安装 R 包…";
          renderDependencyGate();
          if (job.status === "succeeded") { payload = job.result || {}; break; }
          if (job.status === "failed" || job.status === "canceled") {
            state.dependencyJobId = "";
            throw new Error(job.status === "canceled" ? "已取消安装，已完成的包会保留。" : job.error || "R 包安装失败");
          }
          await new Promise((resolve) => setTimeout(resolve, 800));
        }
      }
      state.dependencyJobId = "";
      state.dependencyProgress = "";
      state.dependencyBusy = false;
      await completeDependencyGate(payload.dependency_gate || gate);
    } catch (error) {
      if (error.status === 404) state.dependencyJobId = "";
      state.dependencyProgress = state.dependencyJobId ? "暂时无法读取安装进度。点击“查看安装进度”或“重新检测”可重新连接，也可尝试停止安装。" : "";
      state.dependencyBusy = false;
      if (error?.payload?.dependency_gate) state.dependencyGate = error.payload.dependency_gate;
      renderDependencyGate();
      handleUiError(error, action === "install" ? "R 包安装未完成" : "依赖检测未完成");
    }
  }

  function markFigureLoadError(target) {
    if (!(target instanceof HTMLImageElement) || !target.matches("[data-rsw-figure-img]")) return;
    target.closest(".rsw-figure-card")?.classList.add("is-load-error");
    target.closest(".rsw-analysis-plot")?.classList.add("is-load-error");
  }

  async function applyPromptTemplate(kind) {
    const input = $("rsw-chat-input");
    if (!input) return;
    const template = templateById(kind);
    if (!template || !state.activeDataset) {
      toast("请先上传并完成数据理解", "error");
      return;
    }
    const preparation = await prepareTemplate(kind);
    if (!preparation) {
      toast("当前数据不适用此模板", "error");
      return;
    }
    if (preparation.status === "not_applicable" && preparation.compatibility_guidance) {
      state.activeAnalysisPlan = null;
      state.activeTemplateContext = null;
      state.activePreparationPlan = preparation.compatibility_guidance;
      state.preparationResumeTemplateId = kind;
      state.preparationResumePrompt = "";
      setWorkbenchView("assistant");
      closeTemplateLibrary();
      renderAll();
      setStatus(
        "warning",
        "当前数据不能直接使用此模板",
        "可查看目标数据准备方法、让 AI 协助判断，或改用适合当前数据的模板。"
      );
      return;
    }
    if (preparation.status === "not_applicable") {
      toast(preparation?.reason || "当前数据不适用此模板", "error");
      renderTemplateDetail(template);
      return;
    }
    if (preparation.status === "needs_input") {
      const unanswered = unansweredPreparationQuestions(template, preparation);
      state.pendingTemplateId = kind;
      state.activeTemplateContext = null;
      state.activeAnalysisPlan = null;
      setWorkbenchView("assistant");
      closeTemplateLibrary();
      renderAll();
      setStatus(
        unanswered.length ? "warning" : "ok",
        unanswered.length ? "请在计划条确认关键输入" : "关键项已填写，请再次确认后运行",
        unanswered.length
          ? `只需确认：${unanswered.map((item) => item.label).join("、")}`
          : "确认后即可按此计划运行。"
      );
      return;
    }
    if (preparation.status === "needs_preparation") {
      state.activeAnalysisPlan = null;
      state.activeTemplateContext = null;
      state.activePreparationPlan = preparation.preparation_plan || null;
      state.preparationResumeTemplateId = kind;
      state.preparationResumePrompt = "";
      setWorkbenchView("assistant");
      closeTemplateLibrary();
      renderAll();
      setStatus(
        "warning",
        "当前数据需要先准备",
        preparation.preparation_plan?.summary || preparation.reason || "完成准备并重新导入后即可返回原模板。"
      );
      return;
    }
    if (dependencyGateNeedsAction(preparation.dependency_gate)) {
      closeTemplateLibrary();
      openDependencyGate(preparation.dependency_gate, { type: "template", templateId: kind });
      setStatus(
        "warning",
        preparation.dependency_gate.headline || "分析依赖尚未就绪",
        preparation.dependency_gate.summary || "完成依赖处理后会恢复当前模板计划。"
      );
      return;
    }
    input.value = promptTemplateText(kind);
    state.activeTemplateContext = templateExecutionContext(kind, preparation);
    state.activeAnalysisPlan = preparation.plan;
    state.activePreparationPlan = null;
    state.pendingTemplateId = "";
    state.preparationResumeTemplateId = "";
    state.preparationResumePrompt = "";
    setWorkbenchView("assistant");
    closeTemplateLibrary();
    renderAll();
    setStatus(
      "ok",
      "按此计划运行",
      "输入和方法合同已冻结，正在按此计划执行。"
    );
    if (!tryStartChatRequest()) return;
    try {
      const prompt = input.value;
      input.value = "";
      await streamChat(prompt, { lockAcquired: true });
    } finally {
      state.chatStarting = false;
      renderRunControls();
    }
  }

  function findResultArtifact(artifactId) {
    for (const turn of resultTurns()) {
      for (const artifact of turnArtifacts(turn, { includeMethods: true })) {
        if (artifact.artifact_id === artifactId) return artifact;
      }
    }
    return null;
  }

  async function toggleArtifactCore(artifactId) {
    const artifact = findResultArtifact(artifactId);
    if (!artifact) return;
    const payload = await requestJson(`/api/session/${encodeURIComponent(state.sid)}/research/artifacts/${encodeURIComponent(artifactId)}/core`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ core_selected: !artifact.core_selected }),
    });
    applyResultState(payload.state);
    renderAll();
  }

  async function toggleArtifactPaper(artifactId) {
    const artifact = findResultArtifact(artifactId);
    if (!artifact) return;
    const payload = await requestJson(`/api/session/${encodeURIComponent(state.sid)}/research/artifacts/${encodeURIComponent(artifactId)}/paper`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ included_in_paper: !artifact.included_in_paper }),
    });
    applyResultState(payload.state);
    renderAll();
  }

  async function updatePaperArtifactField(artifactId, field, value) {
    if (!artifactId || !field) return;
    const payload = await requestJson(`/api/session/${encodeURIComponent(state.sid)}/research/artifacts/${encodeURIComponent(artifactId)}/paper`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [field]: value, included_in_paper: true }),
    });
    applyResultState(payload.state);
    renderAll();
  }

  async function downloadResearchExport(kind) {
    const endpoints = {
      word: ["word", "research_results.docx"],
      excel: ["excel", "research_tables.xlsx"],
      figures: ["figures", "research_figures.zip"],
      package: ["package", "research_delivery_package.zip"],
    };
    const target = endpoints[kind];
    if (!target || !state.sid) return;
    const response = await apiFetch(`/api/session/${encodeURIComponent(state.sid)}/research/export/${target[0]}`, {
      method: "POST",
      headers: { Accept: "application/octet-stream" },
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error || `导出失败：HTTP ${response.status}`);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    downloadUrl(url, target[1], true);
  }

  function bindEvents() {
    $("rsw-mobile-menu")?.addEventListener("click", (event) => {
      const sidebar = qs(".rsw-left");
      const open = sidebar?.classList.toggle("is-mobile-open") || false;
      event.currentTarget.setAttribute("aria-expanded", String(open));
      event.currentTarget.setAttribute("aria-label", open ? "收起工作台菜单" : "展开工作台菜单");
    });

    qsa("[data-result-view]").forEach((button) => {
      button.addEventListener("click", () => setResultView(button.dataset.resultView || "overview"));
    });

    document.addEventListener("click", (event) => {
      const startPreparation = event.target.closest("[data-start-preparation]");
      if (startPreparation) {
        event.preventDefault();
        if (state.activePreparationPlan) {
          setWorkbenchView("assistant");
          appendMessage("ai", preparationPlanMarkdown(state.activePreparationPlan));
          setStatus(
            "warning",
            "等待处理后数据",
            "完成准备后导入文件或文件夹，系统会重新预检并恢复原分析。"
          );
        }
        return;
      }
      const assistPreparation = event.target.closest("[data-assist-preparation]");
      if (assistPreparation) {
        event.preventDefault();
        const prompt = String(state.activePreparationPlan?.prompt || "").trim();
        if (prompt) {
          streamChat(prompt).catch((error) => handleUiError(error, "AI 准备协助失败"));
        }
        return;
      }
      const uploadPrepared = event.target.closest("[data-upload-prepared]");
      if (uploadPrepared) {
        event.preventDefault();
        state.preparationResumeTemplateId = String(
          state.activePreparationPlan?.resume?.template_id
          || state.activePreparationPlan?.target?.template_id
          || state.preparationResumeTemplateId
          || ""
        );
        state.preparationResumePrompt = String(
          state.activePreparationPlan?.resume?.conversation_prompt
          || state.preparationResumePrompt
          || ""
        );
        importLocalSelection(uploadPrepared.dataset.uploadPrepared || "files")
          .catch((error) => handleUiError(error, "处理后数据导入失败"));
        return;
      }
      const jump = event.target.closest("[data-result-view-jump]");
      if (jump) {
        setResultView(jump.dataset.resultViewJump || "overview");
        return;
      }
      const openTurn = event.target.closest("[data-result-view-open]");
      if (openTurn) {
        const turnId = openTurn.dataset.turnId || openTurn.closest("[data-turn-id]")?.dataset.turnId || "";
        if (turnId) state.activeTurnId = turnId;
        const outcome = resultTurnOutcome(activeTurn());
        if (outcome && !state.streaming) showAnalysisOutcome(outcome);
        setWorkbenchView("results");
        setResultView(openTurn.dataset.resultViewOpen || "turns");
        renderAll();
        return;
      }
      const viewTurn = event.target.closest("[data-view-turn]");
      if (viewTurn) {
        state.activeTurnId = viewTurn.dataset.viewTurn || state.activeTurnId;
        const outcome = resultTurnOutcome(activeTurn());
        if (outcome && !state.streaming) showAnalysisOutcome(outcome);
        setWorkbenchView("results");
        setResultView("turns");
        renderAll();
        return;
      }
      const continueButton = event.target.closest("[data-continue-analysis]");
      if (continueButton) {
        event.preventDefault();
        continueAnalysisFromButton(continueButton)
          .catch((error) => handleUiError(error, "继续分析失败"));
        return;
      }
    });

    $("rsw-turn-search")?.addEventListener("input", (event) => {
      state.turnSearch = event.target.value || "";
      renderTurnList();
    });

    qsa("[data-turn-filter]").forEach((button) => {
      button.addEventListener("click", () => {
        state.turnFilter = button.dataset.turnFilter || "all";
        qsa("[data-turn-filter]").forEach((item) => item.classList.toggle("is-active", item === button));
        renderTurnList();
      });
    });

    $("rsw-turn-list")?.addEventListener("click", (event) => {
      const card = event.target.closest("[data-turn-id]");
      if (!card) return;
      state.activeTurnId = card.dataset.turnId || "";
      state.resultView = "turns";
      const outcome = resultTurnOutcome(activeTurn());
      if (outcome && !state.streaming) showAnalysisOutcome(outcome);
      renderAll();
    });

    $("rsw-result-v2-layout")?.addEventListener("click", (event) => {
      const openButton = event.target.closest("[data-artifact-open]");
      if (openButton) {
        openArtifactPreview(openButton.dataset.artifactOpen).catch((error) => handleUiError(error));
        return;
      }
      const coreButton = event.target.closest("[data-artifact-core]");
      if (coreButton) {
        toggleArtifactCore(coreButton.dataset.artifactCore).catch((error) => handleUiError(error));
        return;
      }
      const paperButton = event.target.closest("[data-artifact-paper]");
      if (paperButton) {
        toggleArtifactPaper(paperButton.dataset.artifactPaper).catch((error) => handleUiError(error));
        return;
      }
      const downloadButton = event.target.closest("[data-artifact-download]");
      if (downloadButton) {
        const artifact = findResultArtifact(downloadButton.dataset.artifactDownload);
        if (artifact) downloadUrl(artifactDownloadUrl(artifact, downloadButton.dataset.format || ""));
        return;
      }
      const textDownloadButton = event.target.closest("[data-artifact-text-download]");
      if (textDownloadButton) {
        downloadArtifactText(textDownloadButton.dataset.artifactTextDownload);
      }
    });

    $("rsw-paper-board")?.addEventListener("change", (event) => {
      const field = event.target.dataset.paperField;
      const artifactId = event.target.dataset.artifactId;
      if (!field || !artifactId) return;
      updatePaperArtifactField(artifactId, field, event.target.value || "").catch((error) => handleUiError(error));
    });

    $("rsw-export-word")?.addEventListener("click", () => downloadResearchExport("word").catch((error) => handleUiError(error)));
    $("rsw-export-excel")?.addEventListener("click", () => downloadResearchExport("excel").catch((error) => handleUiError(error)));
    $("rsw-export-figures")?.addEventListener("click", () => downloadResearchExport("figures").catch((error) => handleUiError(error)));
    $("rsw-export-package")?.addEventListener("click", () => downloadResearchExport("package").catch((error) => handleUiError(error)));

    $("rsw-status-action")?.addEventListener("click", () => {
      retryLastAction().catch((error) => handleUiError(error));
    });

    qsa("[data-workbench-view]").forEach((button) => {
      button.addEventListener("click", () => {
        setWorkbenchView(button.dataset.workbenchView || "assistant");
      });
    });

    $("rsw-open-assistant")?.addEventListener("click", () => {
      setWorkbenchView("assistant");
    });

    $("rsw-side-upload")?.addEventListener("click", () => {
      setWorkbenchView("assistant");
      importLocalSelection("files").catch((error) => handleUiError(error, "数据导入失败"));
    });

    $("rsw-native-files")?.addEventListener("click", () => {
      importLocalSelection("files").catch((error) => handleUiError(error, "数据导入失败"));
    });

    $("rsw-native-directory")?.addEventListener("click", () => {
      importLocalSelection("directory").catch((error) => handleUiError(error, "文件夹导入失败"));
    });

    $("rsw-attach-image")?.addEventListener("click", () => {
      try {
        assertAttachedImagesAllowed();
        $("rsw-chat-image-input")?.click();
      } catch (error) {
        toast(error.message, "error");
      }
    });

    $("rsw-chat-image-input")?.addEventListener("change", (event) => {
      addPendingChatImages(event.target.files);
      event.target.value = "";
    });

    $("rsw-chat-image-chips")?.addEventListener("click", (event) => {
      const button = event.target instanceof Element
        ? event.target.closest("[data-remove-chat-image]")
        : null;
      if (!button) return;
      const index = Number(button.dataset.removeChatImage);
      const items = state.pendingChatImages || [];
      const [removed] = items.splice(index, 1);
      if (removed?.url) URL.revokeObjectURL(removed.url);
      state.pendingChatImages = items;
      renderChatImageChips();
    });

    $("rsw-side-templates")?.addEventListener("click", () => {
      openTemplateLibrary().catch((error) => handleUiError(error, "模板库打开失败"));
    });

    $("rsw-side-model")?.addEventListener("click", () => {
      openAiConfigDialog();
    });

    $("rsw-stop-run")?.addEventListener("click", () => {
      stopActiveAnalysis().catch((error) => {
        toast(`停止请求失败：${error.message}`, "error");
      });
    });

    $("rsw-side-results")?.addEventListener("click", () => {
      setWorkbenchView("results");
    });

    $("rsw-side-clear")?.addEventListener("click", () => {
      setWorkbenchView("assistant");
      clearConversation().catch((error) => handleUiError(error, "清空对话失败"));
    });

    qsa("[data-ai-modal-close]").forEach((el) => {
      el.addEventListener("click", () => closeAiConfigDialog());
    });

    qsa("[data-template-modal-close]").forEach((el) => {
      el.addEventListener("click", () => closeTemplateLibrary());
    });

    $("rsw-ai-test-key")?.addEventListener("click", () => {
      testDeepSeekKey().catch((error) => {
        setAiKeyStatus(error.message || "连接测试失败。", "error");
      });
    });

    $("rsw-refresh-runtime")?.addEventListener("click", () => {
      state.templatesLoaded = false;
      loadRuntimeSettings()
        .then(() => toast("运行环境信息已刷新", "ok"))
        .catch((error) => toast(`刷新失败：${error.message}`, "error"));
    });

    qsa('input[name="r-library-mode"]').forEach((input) => {
      input.addEventListener("change", () => {
        state.rLibraryDirty = true;
        syncRLibraryControls();
        setRLibraryStatus("");
      });
    });

    $("rsw-r-library-custom-path")?.addEventListener("input", () => {
      state.rLibraryDirty = true;
      setRLibraryStatus("");
    });

    $("rsw-r-library-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      saveRLibrarySettings().catch((error) => {
        setRLibraryStatus(error.message || "R 包目录设置保存失败。", "error");
      });
    });

    ["rsw-model-sharing-result-mode", "rsw-model-sharing-send-result-images", "rsw-model-sharing-send-attached-images"].forEach((id) => {
      $(id)?.addEventListener("change", modelSharingDraftChanged);
    });
    $("rsw-model-sharing-sensitive-columns")?.addEventListener("input", modelSharingDraftChanged);
    $("rsw-model-sharing-column-options")?.addEventListener("click", (event) => {
      const button = event.target instanceof Element ? event.target.closest("[data-model-sharing-column]") : null;
      if (!button || button.disabled || modelSharingAnalysisBusy() || state.modelSharingLoading || state.modelSharingSaving) return;
      const field = $("rsw-model-sharing-sensitive-columns");
      if (!field) return;
      const names = modelSharingSensitiveColumns();
      const name = button.dataset.modelSharingColumn;
      if (name && !names.includes(name)) names.push(name);
      field.value = names.join("\n");
      modelSharingDraftChanged();
    });
    $("rsw-model-sharing-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      return saveModelSharingPolicy().catch(() => {});
    });

    $("rsw-ai-key-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      saveDeepSeekKey().catch((error) => {
        setAiKeyStatus(error.message || "保存失败。", "error");
      });
    });

    $("rsw-ai-clear-key")?.addEventListener("click", () => {
      clearDeepSeekKey().catch((error) => {
        setAiKeyStatus(error.message || "清除失败。", "error");
      });
    });

    document.addEventListener("keydown", (event) => {
      trapModalFocus(event);
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
        const input = $("rsw-chat-input");
        if (input && document.activeElement === input) {
          event.preventDefault();
          $("rsw-chat-form")?.requestSubmit();
        }
      }
      if (event.key === "Escape" && (state.streaming || state.inspecting) && $("rsw-template-modal")?.hidden && $("rsw-ai-modal")?.hidden && $("rsw-dependency-modal")?.hidden && $("rsw-artifact-modal")?.hidden && $("rsw-replace-modal")?.hidden) {
        event.preventDefault();
        stopActiveAnalysis().catch((error) => handleUiError(error));
        return;
      }
      if (event.key === "Escape" && !$("rsw-artifact-modal")?.hidden) {
        closeArtifactPreview();
      }
      if (event.key === "Escape" && !$("rsw-ai-modal")?.hidden) {
        closeAiConfigDialog();
      }
      if (event.key === "Escape" && !$("rsw-template-modal")?.hidden) {
        closeTemplateLibrary();
      }
      if (event.key === "Escape" && !$("rsw-dependency-modal")?.hidden) {
        closeDependencyGate({ clear: true });
      }
      if (event.key === "Escape" && !$("rsw-replace-modal")?.hidden) {
        qs('[data-replace-action="cancel"]', $("rsw-replace-modal"))?.click();
      }
    });

    $("rsw-copy-summary")?.addEventListener("click", () => {
      copyText(resultSummaryTextForCopy(), "研究概览");
    });

    $("rsw-download-interpretation")?.addEventListener("click", () => {
      const text = currentDisplayInterpretationText();
      if (!text) {
        toast("当前没有可下载解释", "error");
        return;
      }
      downloadText(`${safeFileStem(state.activeDataset?.name || "research_interpretation")}_interpretation.md`, text);
      toast("结果解释已下载", "ok");
    });

    $("rsw-export-draft")?.addEventListener("click", () => {
      downloadResearchExport("word").catch((error) => handleUiError(error));
    });

    $("rsw-run-plan-btn")?.addEventListener("click", () => {
      const input = $("rsw-chat-input");
      const kind = state.activeTemplateContext?.id || state.activeAnalysisPlan?.template_id || state.pendingTemplateId || "";
      const value = (input?.value || "").trim() || (kind ? promptTemplateText(kind) : "");
      if (!value) {
        toast("请先确认分析计划", "error");
        return;
      }
      if (input) input.value = "";
      streamChat(value).catch((error) => handleUiError(error));
    });

    $("rsw-result-scope")?.addEventListener("click", (event) => {
      if (!(event.target instanceof Element)) return;
      const toggle = event.target.closest("[data-current-output-toggle]");
      if (toggle) {
        state.showAllCurrentOutputs = !state.showAllCurrentOutputs;
        renderAll();
        persistResultState().catch(() => {});
        return;
      }
      const button = event.target.closest("[data-result-scope]");
      if (!button || button.hasAttribute("disabled")) return;
      state.resultScope = button.dataset.resultScope === "all" ? "all" : "latest";
      state.showAllCurrentOutputs = false;
      renderAll();
      persistResultState().catch(() => {});
    });

    document.addEventListener("error", (event) => {
      markFigureLoadError(event.target);
    }, true);

    const uploadZone = $("rsw-upload-zone");
    const fileInput = $("rsw-file-input");
    if (uploadZone && fileInput) {
      uploadZone.addEventListener("click", () => {
        importLocalSelection("files").catch((error) => handleUiError(error, "数据导入失败"));
      });
      uploadZone.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          importLocalSelection("files").catch((error) => handleUiError(error, "数据导入失败"));
        }
      });
      uploadZone.addEventListener("dragover", (event) => {
        event.preventDefault();
        uploadZone.classList.add("is-dragover");
      });
      uploadZone.addEventListener("dragleave", () => {
        uploadZone.classList.remove("is-dragover");
      });
      uploadZone.addEventListener("drop", (event) => {
        event.preventDefault();
        uploadZone.classList.remove("is-dragover");
        uploadFiles(event.dataTransfer.files).catch((error) => {
          setRunState("错误", "上传失败", "error");
          handleUiError(error, "上传失败");
        });
      });
    }

    $("rsw-file-input")?.addEventListener("change", (event) => {
      uploadFiles(event.target.files).catch((error) => {
        setRunState("错误", "上传失败", "error");
        handleUiError(error, "上传失败");
      }).finally(() => {
        event.target.value = "";
      });
    });

    $("rsw-chat-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      const input = $("rsw-chat-input");
      const value = input?.value || "";
      if (input) input.value = "";
      streamChat(value).catch((error) => handleUiError(error));
    });

    $("rsw-dependency-modal")?.addEventListener("click", (event) => {
      if (!(event.target instanceof Element)) return;
      const button = event.target.closest("[data-dependency-action]");
      if (!button) return;
      const action = button.dataset.dependencyAction || "close";
      if (action === "close") {
        closeDependencyGate({ clear: true });
        return;
      }
      runDependencyAction(action).catch((error) => handleUiError(error, "依赖处理未完成"));
    });

    $("rsw-template-search")?.addEventListener("input", (event) => {
      state.templateSearch = event.target.value || "";
      renderTemplateLibrary();
    });

    $("rsw-template-categories")?.addEventListener("click", (event) => {
      if (!(event.target instanceof Element)) return;
      const button = event.target.closest("[data-template-category]");
      if (!button) return;
      state.templateCategory = button.dataset.templateCategory || "all";
      renderTemplateLibrary();
    });

    $("rsw-template-workflows")?.addEventListener("click", (event) => {
      if (!(event.target instanceof Element)) return;
      const button = event.target.closest("[data-template-workflow]");
      if (!button) return;
      state.templateWorkflow = button.dataset.templateWorkflow || "all";
      renderTemplateLibrary();
    });

    $("rsw-template-cards")?.addEventListener("click", (event) => {
      if (!(event.target instanceof Element)) return;
      const card = event.target.closest("[data-template-select]");
      if (card) {
        state.templateSelectedId = card.dataset.templateSelect || "";
        renderTemplateLibrary();
        if (state.activeDataset && !templatePreparation(state.templateSelectedId)) {
          prepareTemplate(state.templateSelectedId, { silent: true }).catch((error) => {
            toast(`模板准备失败：${error.message}`, "error");
          });
        }
      }
    });

    $("rsw-template-cards")?.addEventListener("keydown", (event) => {
      if (!(event.target instanceof Element)) return;
      if (event.key !== "Enter" && event.key !== " ") return;
      const card = event.target.closest("[data-template-select]");
      if (!card) return;
      event.preventDefault();
      state.templateSelectedId = card.dataset.templateSelect || "";
      renderTemplateLibrary();
      if (state.activeDataset && !templatePreparation(state.templateSelectedId)) {
        prepareTemplate(state.templateSelectedId, { silent: true }).catch((error) => {
          toast(`模板准备失败：${error.message}`, "error");
        });
      }
    });

    $("rsw-template-detail")?.addEventListener("click", (event) => {
      if (!(event.target instanceof Element)) return;
      const resetButton = event.target.closest("[data-template-input-reset]");
      if (resetButton) {
        event.preventDefault();
        const templateId = state.templateSelectedId;
        const inputId = resetButton.dataset.templateInputReset || "";
        if (!templateId || !inputId) return;
        const nextValues = { ...templateInputValues(templateId) };
        delete nextValues[inputId];
        state.templateInputValues[templateId] = nextValues;
        prepareTemplate(templateId, { silent: true }).catch((error) => {
          handleUiError(error, "模板输入恢复失败");
        });
        return;
      }
      const alternativeButton = event.target.closest("[data-template-alternative]");
      if (alternativeButton) {
        event.preventDefault();
        const alternativeId = alternativeButton.dataset.templateAlternative || "";
        state.templateSelectedId = alternativeId;
        renderTemplateLibrary();
        applyPromptTemplate(alternativeId).catch((error) => {
          handleUiError(error, "替代模板准备失败");
        });
        return;
      }
      const applyButton = event.target.closest("[data-template-apply]");
      if (!applyButton) return;
      applyPromptTemplate(applyButton.dataset.templateApply || "data_quality").catch((error) => {
        handleUiError(error, "模板准备失败");
      });
    });

    $("rsw-template-detail")?.addEventListener("change", (event) => {
      const control = event.target.closest("[data-template-input-id]");
      if (!control) return;
      const templateId = state.templateSelectedId;
      const inputId = control.dataset.templateInputId || "";
      const kind = control.dataset.templateInputKind || "";
      const required = control.dataset.templateInputRequired === "true";
      if (!templateId || !inputId) return;
      let value = control.value || "";
      if (kind === "columns") {
        value = Array.from(control.selectedOptions || []).map((option) => option.value).filter(Boolean);
      } else if (kind === "parameter" && value !== "") {
        const numericValue = Number(value);
        value = Number.isFinite(numericValue) ? numericValue : value;
      }
      const nextValues = { ...templateInputValues(templateId) };
      if (Array.isArray(value) && !value.length && !required) {
        nextValues[inputId] = [];
      } else if ((Array.isArray(value) && !value.length) || value === "") {
        delete nextValues[inputId];
      } else {
        nextValues[inputId] = value;
      }
      state.templateInputValues[templateId] = nextValues;
      prepareTemplate(templateId, { silent: true }).catch((error) => {
        handleUiError(error, "模板输入重新校验失败");
      });
    });

    $("rsw-new-session")?.addEventListener("click", () => {
      startNewSession().catch((error) => {
        setRunState("错误", "新会话创建失败", "error");
        handleUiError(error, "新会话创建失败");
      });
    });

    $("rsw-plan-inputs")?.addEventListener("change", (event) => {
      const control = event.target.closest("[data-template-input-id]");
      if (!control) return;
      const templateId = state.pendingTemplateId || state.templateSelectedId;
      const inputId = control.dataset.templateInputId || "";
      const kind = control.dataset.templateInputKind || "";
      const required = control.dataset.templateInputRequired === "true";
      if (!templateId || !inputId) return;
      let value = control.value || "";
      if (kind === "columns") {
        value = Array.from(control.selectedOptions || []).map((option) => option.value).filter(Boolean);
      } else if (kind === "parameter" && value !== "") {
        const numericValue = Number(value);
        value = Number.isFinite(numericValue) ? numericValue : value;
      }
      const nextValues = { ...templateInputValues(templateId) };
      if (Array.isArray(value) && !value.length && !required) {
        nextValues[inputId] = [];
      } else if ((Array.isArray(value) && !value.length) || value === "") {
        delete nextValues[inputId];
      } else {
        nextValues[inputId] = value;
      }
      state.templateInputValues[templateId] = nextValues;
      prepareTemplate(templateId, { silent: true }).then((preparation) => {
        if (preparation?.status === "ready") {
          state.activeAnalysisPlan = preparation.plan;
          state.activeTemplateContext = templateExecutionContext(templateId, preparation);
          const input = $("rsw-chat-input");
          if (input) input.value = promptTemplateText(templateId);
        }
        renderAll();
      }).catch((error) => {
        handleUiError(error, "模板输入重新校验失败");
      });
    });

    $("rsw-plan-inputs")?.addEventListener("click", (event) => {
      if (!(event.target instanceof Element)) return;
      const resetButton = event.target.closest("[data-template-input-reset]");
      if (!resetButton) return;
      const templateId = state.pendingTemplateId || state.templateSelectedId;
      const inputId = resetButton.dataset.templateInputReset || "";
      if (!templateId || !inputId) return;
      const nextValues = { ...templateInputValues(templateId) };
      delete nextValues[inputId];
      state.templateInputValues[templateId] = nextValues;
      prepareTemplate(templateId, { silent: true }).then(() => renderAll()).catch((error) => {
        handleUiError(error, "模板输入恢复失败");
      });
    });

    document.addEventListener("rsw:tool-audit", (event) => {
      handleResearchToolAudit(event.detail || {});
    });
  }

  async function init() {
    bindEvents();
    bindWorkbenchPages();
    syncWorkbenchHash();
    renderAll();
    try {
      await loadLocalSession();
      await ensureSession();
      state.history = readStoredHistory();
      await loadCurrentConversation().catch(() => {});
      await loadReadiness();
      await loadDatasets();
      loadAnalysisTemplates()
        .then(() => scheduleTemplateLibraryRender())
        .catch(() => {});
      await loadPersistedResultState().catch(() => {});
      const reattached = await reattachRunningAnalysis().catch(() => false);
      if (!reattached) {
        const outcome = resultTurnOutcome(activeTurn());
        if (outcome) showAnalysisOutcome(outcome);
        else setRunState("空闲", "就绪");
      }
      renderAll();
    } catch (error) {
      setRunState("错误", "初始化失败", "error");
      toast(error.message, "error");
    }
  }

  if (typeof process !== "undefined" && process.env?.NODE_ENV === "test" && globalThis.__RSW_TEST_HARNESS__) {
    Object.assign(globalThis.__RSW_TEST_HARNESS__, {
      state,
      resultCompletionPresentation,
      resultTurnOutcome,
      resultViewState,
      applyResultState,
      loadPersistedResultState,
      renderAll,
      init,
      continuationPrompt,
      continuationTemplateContext,
      continueCurrentAnalysis,
      continueAnalysisFromButton,
      prepareTemplate,
      renderPreparationQuestions,
      userFacingResolvedInput,
      handleResearchToolAudit,
      createStreamingMessageRenderer,
      reattachRunningAnalysis,
      notifyResultsReady,
      showAnalysisOutcome,
      appendResultCompletionCard,
      getDesktopImportBridge,
      requestDesktopImportSelection,
      loadModelSharingPolicy,
      saveModelSharingPolicy,
      renderModelSharingControls,
      setModelSharingStatus,
      openAiConfigDialog,
      addPendingChatImages,
      uploadPendingChatImages,
      streamChat,
      ensureSession,
      loadLocalSession,
      bindEvents,
      renderRunControls,
      resetModelSharingForSession,
    });
  }

  document.addEventListener("DOMContentLoaded", init);
})();
