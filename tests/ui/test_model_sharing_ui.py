"""Exercise the actual session-sharing UI JavaScript with an isolated Node DOM.

Every fetch is intercepted locally. No model, user configuration, or live session
is touched; DOM fields and event handlers come from the production workbench.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
WORKBENCH = ROOT / "static/js/research_workbench.js"
TEMPLATE = ROOT / "templates/research_workbench.html"


def _node(checks: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.fail("Node.js is required to verify model-sharing UI interactions")
    setup = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
process.env.NODE_ENV = 'test';
const html = fs.readFileSync(TEMPLATE_PATH, 'utf8');
class TestElement {
  constructor(id) {
    this.id = id; this.value = ''; this.checked = false; this.disabled = false;
    this.hidden = false; this.textContent = ''; this.innerHTML = ''; this.dataset = {};
    this.className = ''; this.events = {}; this.style = {}; this.clicked = 0;
    this.classList = {add() {}, remove() {}, toggle() {}};
  }
  addEventListener(type, callback) { (this.events[type] ||= []).push(callback); }
  async dispatch(type, overrides = {}) {
    const event = {target: this, preventDefault() { this.prevented = true; }, ...overrides};
    for (const callback of this.events[type] || []) await callback(event);
    return event;
  }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  setAttribute() {}
  removeAttribute() {}
  focus() { document.activeElement = this; }
  click() { this.clicked += 1; }
  closest(selector) {
    if (selector === '[data-model-sharing-column]' && this.dataset.modelSharingColumn) return this;
    return null;
  }
}
const elements = new Map();
const ids = [...html.matchAll(/id="(rsw-model-sharing-[^"]+)"/g)].map(match => match[1]);
ids.push('rsw-ai-modal', 'rsw-ai-key-input', 'rsw-ai-key-current', 'rsw-ai-key-status',
         'rsw-toast', 'rsw-chat-image-chips', 'rsw-attach-image', 'rsw-chat-image-input');
for (const id of ids) elements.set(id, new TestElement(id));
for (const id of ['rsw-model-sharing-form', 'rsw-model-sharing-result-mode',
                  'rsw-model-sharing-sensitive-columns', 'rsw-model-sharing-save']) {
  assert.ok(html.includes(`id="${id}"`), `Missing production UI control ${id}`);
}
const el = id => elements.get(`rsw-model-sharing-${id}`);
global.Element = TestElement; global.HTMLElement = TestElement;
global.document = {
  body: {classList: {add() {}, remove() {}, toggle() {}}}, activeElement: null,
  getElementById: id => elements.get(id) || null,
  querySelector() { return null; }, querySelectorAll() { return []; },
  addEventListener() {},
};
global.window = global; window.addEventListener = () => {};
global.localStorage = {getItem() { return null; }, setItem() {}};
global.sessionStorage = {getItem() { return 'test-csrf'; }, setItem() {}, removeItem() {}};
// Only the callbacks' effects matter; prevent toast/focus timers from delaying tests.
global.setTimeout = () => 1; global.clearTimeout = () => {};
global.URL.createObjectURL = () => 'blob:isolated-test';
global.URL.revokeObjectURL = () => {};
global.FormData = class { constructor() { this.fields = []; } append(...args) { this.fields.push(args); } };
const requests = [];
const policies = new Map();
const defaults = () => ({version: 1, result_mode: 'summary_only', sensitive_columns: [],
                        send_result_images: false, send_attached_images: true});
const response = (payload, status = 200) => ({ok: status < 400, status, json: async () => payload});
let routeOverride = null;
global.fetch = async (url, options = {}) => {
  const request = {url, method: options.method || 'GET', options};
  requests.push(request);
  if (routeOverride) {
    const handled = await routeOverride(request);
    if (handled !== undefined) return handled;
  }
  const match = String(url).match(/^\/api\/session\/([^/]+)\/model-sharing$/);
  if (match) {
    const sid = decodeURIComponent(match[1]);
    if (request.method === 'POST') policies.set(sid, JSON.parse(options.body));
    return response({ok: true, policy: policies.get(sid) || defaults()});
  }
  if (/\/resume$/.test(url)) return response({ok: true});
  if (/\/readiness$/.test(url)) return response({llm: {configured: true}});
  if (/\/templates$/.test(url)) return response({templates: []});
  if (/\/chat\/images$/.test(url)) return response({images: [{id: 'synthetic-image-id'}]});
  return response({});
};
global.__RSW_TEST_HARNESS__ = {};
vm.runInThisContext(fs.readFileSync(WORKBENCH_PATH, 'utf8'));
const hooks = global.__RSW_TEST_HARNESS__;
const settle = async () => { for (let index = 0; index < 12; index++) await Promise.resolve(); };
"""
    script = (
        f"const TEMPLATE_PATH={json.dumps(str(TEMPLATE))};\n"
        f"const WORKBENCH_PATH={json.dumps(str(WORKBENCH))};\n"
        + setup
        + "\n(async () => {\n"
        + checks
        + "\n})().catch(error => { console.error(error); process.exitCode = 1; });\n"
    )
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_open_loads_and_form_saves_policy_independently_of_api_key():
    _node(r"""
hooks.state.sid = 'isolated session';
hooks.bindEvents();
await hooks.openAiConfigDialog();
assert.strictEqual(elements.get('rsw-ai-modal').hidden, false);
assert.strictEqual(el('result-mode').value, 'summary_only');
assert.strictEqual(el('send-result-images').checked, false);
assert.strictEqual(el('send-attached-images').checked, true);
assert.strictEqual(el('save').disabled, false);
el('result-mode').value = 'deidentified';
await el('result-mode').dispatch('change');
el('sensitive-columns').value = 'patient_id\n姓名\npatient_id\n income '; // spaces are part of the exact name
await el('sensitive-columns').dispatch('input');
el('send-result-images').checked = true;
await el('send-result-images').dispatch('change');
el('send-attached-images').checked = false;
await el('send-attached-images').dispatch('change');
assert.match(el('description').textContent, /不是完全匿名/);
assert.match(el('description').textContent, /尚未保存/);
await el('form').dispatch('submit');
await settle();
const posted = requests.find(req => req.method === 'POST' && req.url.endsWith('/model-sharing'));
assert.ok(posted, 'The actual form handler must POST the policy');
assert.strictEqual(posted.url, '/api/session/isolated%20session/model-sharing');
assert.strictEqual(posted.options.headers.get('X-CSRF-Token'), 'test-csrf');
assert.deepStrictEqual(JSON.parse(posted.options.body), {
  version: 1, result_mode: 'deidentified', sensitive_columns: ['patient_id', '姓名', ' income '],
  send_result_images: true, send_attached_images: false,
});
assert.strictEqual(hooks.state.modelSharingDirty, false);
assert.strictEqual(hooks.state.modelSharingPolicy.send_attached_images, false);
assert.match(el('status').textContent, /已保存/);
assert.ok(!requests.some(req => req.url.startsWith('/api/models/')), 'Saving privacy must not touch an API Key');
""")


@pytest.mark.parametrize("system", ["Windows", "macOS"])
def test_settings_report_host_dependencies_and_refresh_unloadable_qt(system):
    _node("const expectedSystem = " + json.dumps(system) + ";\n" + r"""
for (const id of ['rsw-settings-platform', 'rsw-settings-python-qt',
                  'rsw-settings-external-readiness', 'rsw-settings-app-version', 'rsw-refresh-runtime',
                  'rsw-settings-package-count', 'rsw-settings-r-version']) {
  elements.set(id, new TestElement(id));
}
const payload = {app: {version: 'v1.0'}, dependencies: {
  platform: {system: expectedSystem, version: 'test-release', architecture: 'test-arch'},
  python_version: '3.12.9', qt_version: '6.11.1',
  states: [{label: 'Python', status: 'ready'}, {label: 'PySide6 / Qt WebEngine', status: 'ready'}],
}, r: {installed_packages: 2320, r_version: '4.6.0', external_readiness: {states: [
  {dependency_id: 'java', label: 'Java', status: 'missing'},
  {dependency_id: 'python_ml', label: 'Python 与机器学习后端', status: 'missing'},
]}}};
routeOverride = async request => request.url === '/api/system/runtime' ? response(payload) : undefined;
hooks.state.sid = 'runtime-session';
hooks.bindEvents();
await hooks.openAiConfigDialog();
await settle();
const text = id => elements.get(id).textContent;
assert.strictEqual(text('rsw-settings-app-version'), 'v1.0');
assert.strictEqual(text('rsw-settings-platform'), `${expectedSystem} · test-release · test-arch`);
assert.strictEqual(text('rsw-settings-python-qt'), 'Python 3.12.9 · Qt 6.11.1');
assert.strictEqual(text('rsw-settings-r-version'), '4.6.0');
assert.strictEqual(text('rsw-settings-package-count'), '2,320 个已安装包');
assert.match(text('rsw-settings-external-readiness'), /运行依赖已就绪/);
assert.ok(!text('rsw-settings-external-readiness').includes('机器学习后端'));
payload.dependencies.states[1] = {label: 'PySide6 / Qt WebEngine', status: 'unavailable', detail: '已安装但无法加载'};
await elements.get('rsw-refresh-runtime').dispatch('click');
await settle();
assert.match(text('rsw-settings-external-readiness'), /待处理：PySide6 \/ Qt WebEngine（已安装但无法加载）/);
assert.ok(requests.filter(request => request.url === '/api/system/runtime').length >= 2);
""")


def test_failed_save_keeps_draft_and_effective_policy_with_explicit_error():
    _node(r"""
hooks.state.sid = 'A'; hooks.bindEvents();
await hooks.loadModelSharingPolicy();
el('result-mode').value = 'deidentified';
await el('result-mode').dispatch('change');
el('sensitive-columns').value = 'patient_id';
await el('sensitive-columns').dispatch('input');
el('send-result-images').checked = true;
routeOverride = req => req.method === 'POST' && req.url.endsWith('/model-sharing')
  ? response({error: 'analysis_running'}, 409) : undefined;
await el('form').dispatch('submit'); await settle();
assert.strictEqual(el('result-mode').value, 'deidentified');
assert.strictEqual(el('sensitive-columns').value, 'patient_id');
assert.strictEqual(el('send-result-images').checked, true);
assert.strictEqual(hooks.state.modelSharingPolicy.result_mode, 'summary_only');
assert.strictEqual(hooks.state.modelSharingPolicy.send_result_images, false);
assert.strictEqual(hooks.state.modelSharingDirty, true);
assert.strictEqual(el('save').disabled, false);
assert.match(el('status').textContent, /保存失败.*analysis_running.*尚未生效/);
assert.ok(el('status').className.includes('is-error'));
routeOverride = null;
await hooks.openAiConfigDialog();
assert.strictEqual(el('sensitive-columns').value, 'patient_id', 'Reopening must retain an unsaved failed draft');
assert.match(el('status').textContent, /尚未保存/);
assert.strictEqual(await hooks.saveModelSharingPolicy(), true);
""")


@pytest.mark.parametrize("busy_flag", ["streaming", "inspecting", "chatStarting", "canceling"])
def test_running_analysis_disables_controls_and_never_posts_changes(busy_flag):
    _node(f"""
hooks.state.sid = 'A'; hooks.bindEvents();
await hooks.loadModelSharingPolicy();
requests.length = 0;
hooks.state[{json.dumps(busy_flag)}] = true;
hooks.renderRunControls();
for (const id of ['result-mode', 'sensitive-columns', 'send-result-images', 'send-attached-images', 'save']) {{
  assert.strictEqual(el(id).disabled, true, id);
}}
assert.match(el('lock').textContent, /运行中不能修改/);
await el('form').dispatch('submit'); await settle();
assert.strictEqual(requests.length, 0);
assert.match(el('status').textContent, /运行中不能修改/);
hooks.state[{json.dumps(busy_flag)}] = false;
hooks.renderRunControls();
assert.strictEqual(el('save').disabled, false);
""")


def test_session_restore_and_resume_refresh_policy_and_get_failure_does_not_create_session():
    _node(r"""
hooks.state.sid = 'A'; await hooks.loadModelSharingPolicy();
policies.set('A', {...defaults(), send_attached_images: false});
await hooks.ensureSession();
assert.strictEqual(hooks.state.modelSharingPolicy.send_attached_images, false);
assert.ok(requests.some(req => req.url === '/api/session/A/resume'));
policies.set('B', {...defaults(), result_mode: 'deidentified', sensitive_columns: ['subject']});
routeOverride = req => req.url === '/api/local/session'
  ? response({current_session_id: 'B', csrf_token: 'synthetic-token'}) : undefined;
await hooks.loadLocalSession();
assert.strictEqual(hooks.state.sid, 'B');
assert.strictEqual(hooks.state.modelSharingSessionId, 'B');
assert.strictEqual(el('result-mode').value, 'deidentified');
assert.strictEqual(el('sensitive-columns').value, 'subject');
routeOverride = req => req.url === '/api/session/B/model-sharing'
  ? response({error: 'offline'}, 503) : undefined;
requests.length = 0;
await assert.rejects(hooks.ensureSession(), /offline/);
assert.ok(!requests.some(req => req.url === '/api/session/new'), 'Policy failure must not silently create a new session');
assert.strictEqual(hooks.state.sid, 'B');
assert.strictEqual(el('save').disabled, true);
assert.match(el('status').textContent, /读取失败/);
""")


def test_new_session_policy_replaces_old_policy_after_failed_resume():
    _node(r"""
hooks.state.sid = 'old';
policies.set('old', {...defaults(), sensitive_columns: ['old_private'], send_result_images: true});
await hooks.loadModelSharingPolicy();
routeOverride = req => {
  if (req.url === '/api/session/old/resume') return response({error: 'session_not_found'}, 404);
  if (req.url === '/api/session/new') return response({session_id: 'new'});
};
await hooks.ensureSession();
assert.strictEqual(hooks.state.sid, 'new');
assert.strictEqual(hooks.state.modelSharingSessionId, 'new');
assert.strictEqual(el('sensitive-columns').value, '');
assert.strictEqual(el('send-result-images').checked, false);
assert.ok(requests.some(req => req.url === '/api/session/new/model-sharing'));
""")


def test_stale_load_and_save_responses_cannot_overwrite_current_session():
    _node(r"""
let releaseOldGet;
hooks.state.sid = 'old';
routeOverride = req => req.url === '/api/session/old/model-sharing' && req.method === 'GET'
  ? new Promise(resolve => {releaseOldGet = resolve;}) : undefined;
const oldGet = hooks.loadModelSharingPolicy(); await settle();
hooks.state.sid = 'new'; hooks.resetModelSharingForSession();
policies.set('new', {...defaults(), sensitive_columns: ['new_private']});
await hooks.loadModelSharingPolicy();
releaseOldGet(response({ok: true, policy: {...defaults(), sensitive_columns: ['old_private']}}));
await oldGet;
assert.strictEqual(el('sensitive-columns').value, 'new_private');
assert.strictEqual(hooks.state.modelSharingSessionId, 'new');
let releaseOldPost;
routeOverride = req => req.url === '/api/session/new/model-sharing' && req.method === 'POST'
  ? new Promise(resolve => {releaseOldPost = resolve;}) : undefined;
el('result-mode').value = 'deidentified';
const oldPost = hooks.saveModelSharingPolicy(); await settle();
assert.strictEqual(el('save').disabled, true);
hooks.state.sid = 'next'; hooks.resetModelSharingForSession();
await hooks.loadModelSharingPolicy();
releaseOldPost(response({ok: true, policy: {...defaults(), result_mode: 'deidentified'}}));
assert.strictEqual(await oldPost, false);
assert.strictEqual(hooks.state.modelSharingSessionId, 'next');
assert.strictEqual(el('result-mode').value, 'summary_only');
assert.strictEqual(el('save').disabled, false);
""")


def test_disabled_attachment_policy_blocks_picker_add_upload_and_actual_chat_retry():
    _node(r"""
hooks.state.sid = 'A'; hooks.bindEvents();
policies.set('A', {...defaults(), send_attached_images: false});
await hooks.loadModelSharingPolicy();
await elements.get('rsw-attach-image').dispatch('click');
assert.strictEqual(elements.get('rsw-chat-image-input').clicked, 0);
assert.match(elements.get('rsw-toast').textContent, /关闭用户附件发送/);
assert.strictEqual(hooks.addPendingChatImages([{name: 'synthetic.png', type: 'image/png', size: 20}]), false);
assert.strictEqual(hooks.state.pendingChatImages.length, 0);
hooks.state.pendingChatImages = [{file: {}, name: 'synthetic.png', url: 'blob:synthetic'}];
await assert.rejects(hooks.uploadPendingChatImages(), /关闭用户附件发送/);
requests.length = 0;
await hooks.streamChat('explain', {imageIds: ['synthetic-prior-image'], attachPending: false});
assert.ok(requests.some(req => req.url.endsWith('/model-sharing')), 'Chat must refresh the effective session policy');
assert.ok(!requests.some(req => /\/chat(?:\/images)?$/.test(req.url)), 'No upload or chat may follow a rejected attachment');
assert.match(hooks.state.status.detail, /关闭用户附件发送/);
assert.strictEqual(hooks.state.pendingChatImages.length, 1, 'Rejected send preserves the user attachment');
assert.strictEqual(hooks.state.chatStarting, false);
""")


def test_allowed_attachment_upload_and_last_minute_server_policy_change():
    _node(r"""
hooks.state.sid = 'A'; await hooks.loadModelSharingPolicy();
hooks.addPendingChatImages([{name: 'synthetic.png', type: 'image/png', size: 20}]);
assert.strictEqual(hooks.state.pendingChatImages.length, 1);
assert.deepStrictEqual(await hooks.uploadPendingChatImages(), ['synthetic-image-id']);
const upload = requests.find(req => req.url.endsWith('/chat/images'));
assert.strictEqual(upload.options.body.fields[0][2], 'synthetic.png');
policies.set('A', {...defaults(), send_attached_images: false});
requests.length = 0;
await hooks.streamChat('explain');
assert.strictEqual(hooks.state.modelSharingPolicy.send_attached_images, false);
assert.ok(!requests.some(req => /\/chat(?:\/images)?$/.test(req.url)));
assert.match(elements.get('rsw-toast').textContent, /关闭用户附件发送/);
""")


def test_inspected_field_button_adds_exact_column_without_duplicates_or_html_execution():
    _node(r"""
hooks.state.sid = 'A'; hooks.bindEvents(); await hooks.loadModelSharingPolicy();
hooks.state.activeSummary = {variables: [{name: 'patient_id'}, {name: '<private&column>'}]};
hooks.renderModelSharingControls();
assert.ok(el('column-options').innerHTML.includes('&lt;private&amp;column&gt;'));
const button = new TestElement('synthetic-field-button');
button.dataset.modelSharingColumn = '<private&column>';
await el('column-options').dispatch('click', {target: button});
await el('column-options').dispatch('click', {target: button});
assert.strictEqual(el('sensitive-columns').value, '<private&column>');
assert.strictEqual(hooks.state.modelSharingDirty, true);
await hooks.saveModelSharingPolicy();
assert.deepStrictEqual(hooks.state.modelSharingPolicy.sensitive_columns, ['<private&column>']);
hooks.state.streaming = true; hooks.renderModelSharingControls();
button.dataset.modelSharingColumn = 'must_not_add';
await el('column-options').dispatch('click', {target: button});
assert.strictEqual(el('sensitive-columns').value, '<private&column>');
""")
