from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_sidebar_clear_calls_backend_before_resetting_visible_messages():
    source = (ROOT / "static" / "js" / "research_workbench.js").read_text(encoding="utf-8")

    assert "async function clearConversation()" in source
    assert "/clear`" in source
    assert source.count("clearConversation().catch") == 1


def test_chat_request_carries_the_selected_dataset_id():
    source = (ROOT / "static" / "js" / "research_workbench.js").read_text(encoding="utf-8")

    assert "dataset_id: state.activeDataset?.dataset_id" in source


def test_template_preparations_are_cleared_across_dataset_boundaries():
    source = (ROOT / "static" / "js" / "research_workbench.js").read_text(encoding="utf-8")

    assert "templatePreparations: {}" in source
    assert "templateInputValues: {}" in source
    assert source.count("state.templatePreparations = {};") >= 3
    assert source.count("state.templateInputValues = {};") >= 3


def test_template_ui_combines_readiness_states_with_editable_typed_inputs():
    template = (ROOT / "templates" / "research_workbench.html").read_text(encoding="utf-8")
    source = (ROOT / "static" / "js" / "research_workbench.js").read_text(encoding="utf-8")

    assert "上传数据后会自动完成基础数据理解，无需先运行“数据质量与变量字典”" in template
    assert "可直接使用" in source
    assert "需确认关键项" in source
    assert "当前数据不适用" in source
    assert 'preparation.status === "needs_preparation"' in source
    assert 'id="rsw-preparation-plan"' in template
    assert "function preparationPlanMarkdown(plan)" in source
    assert 'data-upload-prepared="directory"' in template
    assert "async function prepareTemplate(templateId" in source
    assert "function renderPreparationQuestions(item, preparation)" in source
    assert "分析输入（可按研究问题修改）" in source
    assert "preparation?.input_controls" in source
    assert "data-template-input-reset" in source
    assert "/prepare`" in source


def test_template_ui_never_renders_dataset_uuid_as_a_scientific_object_label():
    source = (ROOT / "static" / "js" / "research_workbench.js").read_text(encoding="utf-8")

    assert "function userFacingResolvedInput" in source
    assert "当前上传数据" in source
    assert "compatibility_guidance" in source
    assert "data-template-alternative" in source
    assert "state.templateSelectedId = alternativeId" in source
    assert 'preparation.status === "not_applicable"' in source
    assert "处理后数据仍不适用" in source
    assert "矩阵科学语义若来自用户确认，它是待验证声明" in source
    assert "data-template-input-id" in source
    assert "data-template-binding-role" not in source
    assert "missingTemplateRoles" not in source
    assert source.index("${renderPreparationQuestions(item, preparation)}") < source.index(
        '<div class="rsw-template-detail-top-actions">'
    )


def test_template_chat_uses_the_backend_frozen_plan_identifier():
    source = (ROOT / "static" / "js" / "research_workbench.js").read_text(encoding="utf-8")

    assert "plan_id: preparation?.plan?.plan_id" in source
    assert "state.activeAnalysisPlan = preparation.plan" in source
    assert "if (!resolved.length && group && group !== \"待识别\")" in source


def test_dependency_gate_requires_confirmation_and_resumes_the_frozen_plan():
    template = (ROOT / "templates" / "research_workbench.html").read_text(encoding="utf-8")
    source = (ROOT / "static" / "js" / "research_workbench.js").read_text(encoding="utf-8")

    assert 'id="rsw-dependency-modal"' in template
    assert 'data-dependency-action="install"' in template
    assert 'data-dependency-action="recheck"' in template
    assert "if (dependencyGateNeedsAction(preparation.dependency_gate))" in source
    assert "dependencyGateNeedsAction(payload?.dependency_gate)" in source
    assert "/research/dependencies/${encodeURIComponent(gate.ticket_id)}/${action}`" in source
    assert 'plan_id: String(gate.plan_id || "")' in source
    assert "从被依赖门暂停的步骤继续" in source


def test_modal_keyboard_focus_is_trapped_and_restored():
    source = (ROOT / "static" / "js" / "research_workbench.js").read_text(encoding="utf-8")

    assert "function trapModalFocus(event)" in source
    assert "modalReturnFocus?.focus()" in source


def test_mobile_sidebar_has_an_explicit_collapse_control():
    template = (ROOT / "templates" / "research_workbench.html").read_text(encoding="utf-8")
    source = (ROOT / "static" / "js" / "research_workbench.js").read_text(encoding="utf-8")

    assert 'id="rsw-mobile-menu"' in template
    assert 'classList.toggle("is-mobile-open")' in source


def test_workbench_uses_same_origin_local_transport_with_csrf():
    source = (ROOT / "static" / "js" / "research_workbench.js").read_text(encoding="utf-8")

    assert "async function apiFetch(url, options = {})" in source
    assert 'headers.set("X-CSRF-Token", token)' in source
    assert "await apiFetch(`/api/session/${encodeURIComponent(state.sid)}/chat`" in source
    assert 'requestJson("/api/local/session")' in source
    assert "initializeEngineTransport" not in source
    assert "engineToken" not in source


def test_current_workspace_id_is_restored_by_the_local_backend():
    source = (ROOT / "static" / "js" / "research_workbench.js").read_text(encoding="utf-8")

    assert "state.sid = payload.current_session_id" in source
    assert "/resume`" in source
    assert 'localStorage.getItem("rsw.sessionId")' not in source


def test_workbench_configures_the_fixed_deepseek_api():
    template = (ROOT / "templates" / "research_workbench.html").read_text(encoding="utf-8")
    source = (ROOT / "static" / "js" / "research_workbench.js").read_text(encoding="utf-8")

    assert 'id="rsw-ai-key-input"' in template
    assert "<h3>DeepSeek V4.1 Flash</h3>" in template
    assert 'id="rsw-attach-image"' in template
    assert "附加图片" in template
    assert "/api/models/set-builtin" in source
    assert "/api/models/clear-builtin" in source
    assert 'provider: "deepseek"' in source
    assert "/chat/images" in source
    assert "function addPendingChatImages" in source
    assert "模型推理、数据处理和 R 统计分析均在本机完成" not in template


def test_invalid_deepseek_key_error_opens_the_configuration_dialog():
    source = (ROOT / "static" / "js" / "research_workbench.js").read_text(encoding="utf-8")

    assert "aiKeyInvalid: false" in source
    assert 'streamError.code = event.code || ""' in source
    assert 'error?.code === "invalid_api_key"' in source
    assert "state.aiKeyInvalid = true" in source
    assert "本机保存的 DeepSeek API Key 已被服务端判定无效" in source
    assert "openAiConfigDialog();" in source
    assert '"configure-ai"' in source


def test_figure_previews_only_use_browser_renderable_image_artifacts():
    source = (ROOT / "static" / "js" / "research_workbench.js").read_text(encoding="utf-8")

    assert "function isBrowserPreviewableFigure(artifact)" in source
    assert source.count("isBrowserPreviewableFigure(artifact)") >= 3


def test_completed_r_step_updates_the_running_phase_copy():
    source = (ROOT / "static" / "js" / "research_workbench.js").read_text(encoding="utf-8")

    assert 'setAnalysisPhase("AI 正在校验结果并决定下一步")' in source
    assert "formatAnalysisElapsed(analysisStartedAt)" in source


def test_internal_quality_flags_are_not_rendered_to_end_users():
    template = (ROOT / "templates" / "research_workbench.html").read_text(encoding="utf-8")
    source = (ROOT / "static" / "js" / "research_workbench.js").read_text(encoding="utf-8")

    assert 'data-turn-filter="warning"' not in template
    assert 'id="rsw-quality-summary"' not in template
    assert "rsw-quality-panel" not in source
    assert "turn.quality_flags" not in source
    assert "summary.quality_flag_count" not in source
    assert "质量与限制" not in source
    assert "质量提醒" not in template
    assert 'renderTemplateList("质量提示"' not in source
    assert "production_ready" not in source
    assert "item?.quality" not in source


def test_catalog_v3_is_grouped_by_family_and_searches_declared_metadata():
    template = (ROOT / "templates" / "research_workbench.html").read_text(encoding="utf-8")
    source = (ROOT / "static" / "js" / "research_workbench.js").read_text(encoding="utf-8")

    assert 'id="rsw-template-categories"' in template
    assert 'id="rsw-template-cards"' in template
    assert 'id="rsw-template-detail"' in template
    assert "function groupTemplatesByFamily(templates)" in source
    assert "item.title," in source
    assert "item.scenario," in source
    assert "item.family," in source
    assert "item.search_terms" in source
    assert "item.package_names" in source
    assert "item.aliases" in source
    assert 'item?.visibility !== "internal"' in source


def test_workbench_shows_analysis_plan_and_distinguishes_cancel_from_failure():
    template = (ROOT / "templates" / "research_workbench.html").read_text(encoding="utf-8")
    source = (ROOT / "static" / "js" / "research_workbench.js").read_text(encoding="utf-8")

    for node_id in (
        "rsw-analysis-plan",
        "rsw-plan-family",
        "rsw-plan-bindings",
        "rsw-plan-packages",
        "rsw-plan-outputs",
        "rsw-stop-run",
    ):
        assert f'id="{node_id}"' in template
    assert "async function stopActiveAnalysis()" in source
    assert "/stop`" in source
    assert '"正在终止"' in source
    assert '"分析已停止"' in source
    assert 'error?.code === "analysis_canceled"' in source
    assert "这是用户取消，不是分析失败" in source
    assert "state.inspecting = true" in source
    assert "state.streaming || state.inspecting" in source
    assert 'error.code = payload.code || ""' in source


def test_assistant_uses_one_sidebar_control_surface_and_a_compact_plan_notice():
    template = (ROOT / "templates" / "research_workbench.html").read_text(encoding="utf-8")
    source = (ROOT / "static" / "js" / "research_workbench.js").read_text(encoding="utf-8")

    assert 'id="rsw-side-results"' in template
    assert 'id="rsw-side-clear"' in template
    assert 'id="rsw-open-results"' not in template
    assert 'id="rsw-clear-session"' not in template
    assert 'id="rsw-plan-summary"' in template
    assert 'class="rsw-analysis-plan-details"' not in template
    assert "ResearchMarkdown.render" in source


def test_markdown_renderer_is_loaded_before_the_workbench_controller():
    template = (ROOT / "templates" / "research_workbench.html").read_text(encoding="utf-8")

    renderer = "js/research_markdown.js"
    controller = "js/research_workbench.js"
    assert renderer in template
    assert template.index(renderer) < template.index(controller)


def test_desktop_import_supports_native_files_directories_and_browser_fallback():
    template = (ROOT / "templates" / "research_workbench.html").read_text(encoding="utf-8")
    source = (ROOT / "static" / "js" / "research_workbench.js").read_text(encoding="utf-8")

    assert "qrc:///qtwebchannel/qwebchannel.js" in template
    assert 'id="rsw-native-files"' in template
    assert 'id="rsw-native-directory"' in template
    assert "function getDesktopImportBridge()" in source
    assert 'kind === "directory" ? "selectDirectory" : "selectFiles"' in source
    assert "/research/import-local`" in source
    assert '$("rsw-file-input")?.click()' in source


def test_system_settings_show_runtime_and_storage_without_rendering_secrets():
    template = (ROOT / "templates" / "research_workbench.html").read_text(encoding="utf-8")
    source = (ROOT / "static" / "js" / "research_workbench.js").read_text(encoding="utf-8")

    for node_id in (
        "rsw-settings-app-version",
        "rsw-settings-r-version",
        "rsw-settings-platform",
        "rsw-settings-python-qt",
        "rsw-settings-r-resources",
        "rsw-settings-package-count",
        "rsw-settings-runtime-digest",
        "rsw-settings-workspace-usage",
    ):
        assert f'id="{node_id}"' in template
    assert 'requestJson("/api/system/runtime")' in source
    assert "/research/storage`" in source
    assert 'id="rsw-ai-key-input" type="password"' in template
    assert "<dt>开发者</dt><dd>{{ app_developer }}</dd>" in template
    assert "license_key" not in source


def test_browser_csv_export_neutralizes_formula_cells_without_changing_numbers():
    source = (ROOT / "static" / "js" / "research_workbench.js").read_text(encoding="utf-8")

    assert "function safeSpreadsheetCell(value)" in source
    assert "function csvQuote(value)" in source
    assert "columns.map(csvQuote)" in source
    assert "const numeric = /^[+-]?" in source
    assert "formulaLike" in source


def test_artifact_preview_copy_matches_the_bounded_backend_contract():
    source = (ROOT / "static" / "js" / "research_workbench.js").read_text(encoding="utf-8")

    assert "payload.total_rows" in source
    assert "payload.total_columns" in source
    assert "最多显示 100 行、50 列" in source
    assert "最多显示 500 行" not in source


def test_continue_analysis_button_submits_a_followup_instead_of_only_focusing_composer():
    source = (ROOT / "static" / "js" / "research_workbench.js").read_text(encoding="utf-8")

    assert 'data-continue-analysis="${escapeHtml(continuationTurnId)}"' in source
    assert "function continuationPrompt(turnId = \"\")" in source
    assert "function continuationTemplateContext(turnId = \"\")" in source
    assert "function tryStartChatRequest()" in source
    assert "async function continueCurrentAnalysis(turnId = \"\", sendMessage = streamChat)" in source
    assert "async function continueAnalysisFromButton(button, sendMessage = streamChat)" in source
    assert "state.chatStarting = true;" in source
    assert "async function streamChat(message, { lockAcquired = false, imageIds = null, attachPending = true } = {})" in source
    assert "const outgoingText = String(message || \"\").trim();" in source
    assert "if (!outgoingText && !pending.length && !reusedIds.length) return;" in source
    assert "message: outgoingText," in source
    assert "if (!lockAcquired && !tryStartChatRequest()) return;" in source
    assert "state.continuationContexts[state.analysisTurnId]" in source
    assert "state.activeTemplateContext = continuationTemplateContext(turnId);" in source
    assert "await sendMessage(continuationPrompt(turnId), { lockAcquired: true, attachPending: false });" in source
    assert 'const continueButton = event.target.closest("[data-continue-analysis]");' in source
    assert "continueAnalysisFromButton(continueButton)" in source
    assert "appendResultCompletionCard(target, state.analysisTurnId);" in source


def test_model_artifacts_are_preserved_through_the_complete_frontend_pipeline():
    source = (ROOT / "static" / "js" / "research_workbench.js").read_text(encoding="utf-8")
    backend = (ROOT / "r_runtime" / "backends" / "local_rscript.py").read_text(encoding="utf-8")

    assert "(payload.models?.length || 0)" in source
    assert "models: stampArtifactsWithTurn(result.models || []" in source
    assert "models: mergeArtifactList(existing.models || [], incoming.models || [])" in source
    assert 'const models = (result.models || []).map' in source
    assert '"models": []' in backend
    assert 'for group in ("tables", "figures", "texts", "models", "artifacts")' in backend


def test_workbench_uses_page_jump_phase_timeline_and_one_click_template_run():
    template = (ROOT / "templates" / "research_workbench.html").read_text(encoding="utf-8")
    source = (ROOT / "static" / "js" / "research_workbench.js").read_text(encoding="utf-8")

    assert 'id="rsw-split-handle"' not in template
    assert "function isSplitWorkbench()" not in source
    assert "function bindSplitHandle()" not in source
    assert 'id="rsw-phase-timeline"' in template
    assert 'id="rsw-run-plan-btn"' in template
    assert 'id="rsw-plan-inputs"' in template
    assert "已生成产物，可查看结果与待完成项" in template
    assert "function bindWorkbenchPages()" in source
    assert 'next = state.activeView === "results" ? "#results" : "#assistant"' in source
    assert "function renderPhaseTimeline()" in source
    assert "按此计划运行" in source
    assert "function activateResultTab" not in source
    assert "function renderEvidence" not in source
    assert '(event.metaKey || event.ctrlKey) && event.key === "Enter"' in source


def test_page_load_reattaches_to_running_conversation_analysis():
    source = (ROOT / "static" / "js" / "research_workbench.js").read_text(encoding="utf-8")

    assert "async function reattachRunningAnalysis()" in source
    # init() restores canonical terminal state only when no run reattached.
    assert "reattachRunningAnalysis().catch" in source
    assert "if (!reattached) {" in source
    assert "if (outcome) showAnalysisOutcome(outcome);" in source
    # The consumer tails the server-side relay, not the cancelled turn.
    assert "/jobs/${encodeURIComponent(runningJob.id)}/stream?after=0`" in source
    assert 'job.type === "conversation_analysis"' in source
