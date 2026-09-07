const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { Script } = require("node:vm");
const { JSDOM } = require("jsdom");

const staticDir = path.resolve(__dirname, "../../app/static");
const tick = () => new Promise((resolve) => setImmediate(resolve));
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const question = (id) => ({ id, type: "", max_score: 10, regions: [
  { page_no: 1, rect: [0.1, 0.1, 0.8, 0.3] },
], refs: [], note: "", language: "ja", answer_format: "essay", answer_key: "" });
const template = () => ({ template_id: "tpl", title: "テスト", pages: [
  { page_no: 1 }, { page_no: 2 },
], questions: [question("Q1"), question("Q3")] });
const session = (status = "ready") => ({ session_id: "s", template_id: "tpl", status,
  progress: "", warnings: [], failed_pages: [], crops: [],
  transcriptions: [{ question_id: "Q1", transcription: "original", is_blank: false }],
});

async function mount(page, overrides = {}) {
  const html = fs.readFileSync(path.join(staticDir, page), "utf8");
  const dom = new JSDOM(html, { url: `http://localhost/static/${page}?template=tpl&session=s`, runScripts: "outside-only" });
  const w = dom.window;
  const calls = [], timers = [], images = [];
  const routes = {
    "/api/templates/tpl": template(), "/api/templates/tpl/refs": [],
    "/api/sessions/s": session(), "/api/templates": [], "/api/sessions": [],
    "/api/stats": { question_count: 0 },
    "/api/config": { grading_ready: true, ocr_supports_words: true }, ...overrides,
  };
  w.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    if (!(url in routes)) throw new Error(`Unexpected request: ${url}`);
    const data = typeof routes[url] === "function" ? await routes[url](options) : routes[url];
    return { ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => data };
  };
  w.setTimeout = (callback, delay) => { const timer = { callback, delay }; timers.push(timer); return timer; };
  w.clearTimeout = (timer) => { const index = timers.indexOf(timer); if (index >= 0) timers.splice(index, 1); };
  w.confirm = () => true;
  w.CSS = { escape: (s) => s };
  w.HTMLCanvasElement.prototype.getContext = () => new Proxy({}, {
    get: (_, key) => key === "measureText" ? () => ({ width: 20 }) : () => {},
  });
  w.Image = class { constructor() { this.width = 600; this.height = 800; images.push(this); } };
  new Script(fs.readFileSync(path.join(staticDir, "app.js"), "utf8")).runInContext(dom.getInternalVMContext());
  for (const [, source] of html.matchAll(/<script>([\s\S]*?)<\/script>/g)) new Script(source).runInContext(dom.getInternalVMContext());
  await tick();
  return { dom, w, doc: w.document, routes, calls, timers, images };
}

test("API validation and connection errors are readable", async (t) => {
  const ui = await mount("review.html"); t.after(() => ui.dom.window.close());
  ui.w.fetch = async () => ({ ok: false, status: 422, json: async () => ({ detail: [
    { loc: ["body", "questions", 0, "id"], msg: "設問IDを確認してください" },
  ] }) });
  await assert.rejects(ui.w.api("/bad"), (e) => e.status === 422 && e.message.includes("questions → 0 → id") && !e.message.includes("[object Object]"));
  ui.w.fetch = async () => { throw new TypeError("Failed to fetch"); };
  await assert.rejects(ui.w.api("/offline"), /サーバーに接続できません/);
});

test("processing locks transcriptions; failed grading allows retry", async (t) => {
  const ui = await mount("review.html", { "/api/sessions/s": session("processing") });
  t.after(() => ui.dom.window.close());
  assert.equal(ui.doc.querySelector("textarea").disabled, true);
  assert.equal(ui.doc.getElementById("grade-btn").disabled, true);
  ui.routes["/api/sessions/s"] = session("failed");
  await ui.w.refresh();
  assert.equal(ui.doc.querySelector("textarea").disabled, false);
  assert.equal(ui.doc.getElementById("grade-btn").disabled, false);
  assert.equal(ui.doc.getElementById("grade-btn").textContent, "採点を再試行");
});

test("failed save preserves edits and leaving the page warns about them", async (t) => {
  const pending = deferred();
  const ui = await mount("review.html", { "/api/sessions/s/transcriptions": () => pending.promise });
  t.after(() => ui.dom.window.close());
  const input = ui.doc.querySelector("textarea");
  input.value = "corrected";
  input.dispatchEvent(new ui.w.Event("input", { bubbles: true }));
  ui.doc.getElementById("save-btn").click();
  assert.equal(input.disabled, true);
  assert.equal(ui.doc.getElementById("grade-btn").disabled, true);
  pending.reject(new Error("offline"));
  await tick();
  assert.equal(input.value, "corrected");
  assert.equal(input.disabled, false);
  const leaving = new ui.w.Event("beforeunload", { cancelable: true });
  ui.w.dispatchEvent(leaving);
  assert.equal(leaving.defaultPrevented, true);
});

test("grade saves first, prevents duplicate requests, and locks inputs immediately", async (t) => {
  const pending = deferred();
  const ui = await mount("review.html", {
    "/api/sessions/s/transcriptions": () => pending.promise,
    "/api/sessions/s/grade": { status: "grading" },
  });
  t.after(() => ui.dom.window.close());
  const button = ui.doc.getElementById("grade-btn");
  button.click(); button.click();
  assert.equal(ui.doc.querySelector("textarea").disabled, true);
  assert.equal(ui.calls.filter((c) => c.url.endsWith("/grade")).length, 0);
  pending.resolve(session()); await tick();
  assert.equal(ui.calls.filter((c) => c.url.endsWith("/transcriptions")).length, 1);
  assert.equal(ui.calls.filter((c) => c.url.endsWith("/grade")).length, 1);
  assert.equal(ui.doc.querySelector("textarea").disabled, true);
  assert.equal(button.disabled, true);
  assert.equal(ui.timers.length, 1);
});

test("blank selection preserves text while disabling its editor", async (t) => {
  const ui = await mount("review.html"); t.after(() => ui.dom.window.close());
  const blank = ui.doc.querySelector("input[data-blank]");
  blank.click();
  assert.equal(ui.doc.querySelector("textarea").disabled, true);
  assert.equal(ui.w.collectTranscriptions().items[0].transcription, "original");
  blank.click();
  assert.equal(ui.doc.querySelector("textarea").disabled, false);
});

test("saving restored text shows the existing result again", async (t) => {
  const ui = await mount("review.html", {
    "/api/sessions/s": session("graded"),
    "/api/sessions/s/transcriptions": session("graded"),
    "/api/sessions/s/result": { total_score: 10, total_max_score: 10, questions: [] },
  });
  t.after(() => ui.dom.window.close());
  const input = ui.doc.querySelector("textarea");
  input.dispatchEvent(new ui.w.Event("input", { bubbles: true }));
  assert.equal(ui.doc.getElementById("result-panel").style.display, "none");
  ui.doc.getElementById("save-btn").click(); await tick();
  assert.equal(ui.doc.getElementById("result-panel").style.display, "block");
  assert.equal(ui.w.eval("dirty"), false);
});

test("polling waits for each request and recovers after a network failure", async (t) => {
  const ui = await mount("review.html", { "/api/sessions/s": session("processing") });
  t.after(() => ui.dom.window.close());
  const pending = deferred();
  ui.routes["/api/sessions/s"] = () => pending.promise;
  const active = ui.timers.shift().callback();
  assert.equal(ui.timers.length, 0);
  pending.reject(new Error("offline")); await active;
  assert.equal(ui.timers.length, 1);
  assert.equal(ui.timers[0].delay, 4000);
  ui.routes["/api/sessions/s"] = session();
  await ui.timers.shift().callback();
  assert.equal(ui.timers.length, 0);
  assert.equal(ui.doc.querySelector("textarea").disabled, false);
});

test("editor assigns a free question ID and clears stale region selection", async (t) => {
  const ui = await mount("editor.html"); t.after(() => ui.dom.window.close());
  ui.w.eval("selectedRegion = {questionIndex: 1, regionIndex: 0}");
  ui.doc.getElementById("add-question").click();
  assert.deepEqual(Array.from(ui.doc.querySelectorAll(".question-item input[type=text]")).filter((_, i) => i % 2 === 0).map((input) => input.value), ["Q1", "Q3", "Q2"]);
  assert.equal(ui.w.eval("selectedRegion"), null);
  assert.equal(ui.doc.activeElement.value, "Q2");
});

test("editor reordering cannot leave Delete pointing to a different question", async (t) => {
  const ui = await mount("editor.html"); t.after(() => ui.dom.window.close());
  ui.w.eval("selectedRegion = {questionIndex: 0, regionIndex: 0}");
  ui.doc.querySelector('button[aria-label="Q1 を下へ"]').click();
  ui.w.dispatchEvent(new ui.w.KeyboardEvent("keydown", { key: "Delete" }));
  assert.equal(ui.w.eval("template.questions[0].id"), "Q3");
  assert.equal(ui.w.eval("template.questions[0].regions.length"), 1);
  assert.equal(ui.w.eval("selectedRegion"), null);
});

test("older image loads cannot replace the currently selected page", async (t) => {
  const ui = await mount("editor.html"); t.after(() => ui.dom.window.close());
  ui.doc.querySelectorAll("#page-tabs button")[1].click();
  ui.images[1].onload();
  ui.images[0].onload();
  assert.equal(ui.w.eval("currentPage"), 2);
  assert.match(ui.w.eval("pageImage.src"), /page\/2.png/);
});

test("editing during a template save keeps newer changes and the unsaved flag", async (t) => {
  const ui = await mount("editor.html"); t.after(() => ui.dom.window.close());
  const pending = deferred();
  ui.routes["/api/templates/tpl"] = () => pending.promise;
  ui.doc.getElementById("save-btn").click();
  const input = ui.doc.querySelector(".question-item input");
  input.value = "Q-new";
  input.dispatchEvent(new ui.w.Event("input", { bubbles: true }));
  pending.resolve(template()); await tick();
  assert.equal(ui.w.eval("template.questions[0].id"), "Q-new");
  assert.equal(ui.w.eval("dirty"), true);
  assert.match(ui.doc.getElementById("notice").textContent, /もう一度保存/);
});

test("home disables scanning incomplete templates and recovers failed sections", async (t) => {
  const ui = await mount("index.html", {
    "/api/templates": [{ template_id: "tpl", title: "未設定", question_count: 2, scan_ready: false }],
    "/api/sessions": () => { throw new Error("offline"); },
  });
  t.after(() => ui.dom.window.close());
  assert.equal(ui.doc.querySelector("#scan-form button").disabled, true);
  assert.match(ui.doc.getElementById("template-rows").textContent, /未設定/);
  assert.match(ui.doc.getElementById("session-rows").textContent, /再試行/);
  ui.routes["/api/sessions"] = [];
  await ui.w.refresh();
  assert.match(ui.doc.getElementById("session-rows").textContent, /まだセッションがありません/);
  assert.equal(ui.doc.getElementById("notice").textContent, "");
});

const registrationGroup = (name) => ({ title: name, sheet: `${name}_sheet.pdf`, references: [`${name}_answers.pdf`] });
const registrationFiles = (ui, name) => ["sheet", "answers"].map((role) => new ui.w.File(["PDF"], `${name}_${role}.pdf`, { type: "application/pdf" }));

test("registration previews matches and automatically names the sheet", async (t) => {
  const ui = await mount("index.html", { "/api/templates/registration-preview": [registrationGroup("exam")] });
  t.after(() => ui.dom.window.close());
  await ui.w.selectRegistrationFile(registrationFiles(ui, "exam"));
  assert.equal(ui.doc.getElementById("tpl-title").value, "exam");
  assert.equal(ui.doc.getElementById("tpl-id").value, "");
  assert.equal(ui.doc.getElementById("create-btn").disabled, false);
  assert.match(ui.doc.getElementById("registration-preview").textContent, /exam_answers.pdf/);
  const call = ui.calls.find((call) => call.url.endsWith("registration-preview"));
  assert.deepEqual(JSON.parse(call.options.body).filenames, ["exam_sheet.pdf", "exam_answers.pdf"]);
});

test("stale naming previews cannot replace a newer file selection", async (t) => {
  const pending = deferred();
  const ui = await mount("index.html", { "/api/templates/registration-preview": () => pending.promise });
  t.after(() => ui.dom.window.close());
  const first = ui.w.selectRegistrationFile(registrationFiles(ui, "first"));
  ui.routes["/api/templates/registration-preview"] = [registrationGroup("second")];
  await ui.w.selectRegistrationFile(registrationFiles(ui, "second"));
  pending.resolve([registrationGroup("first")]); await first;
  assert.equal(ui.doc.getElementById("tpl-title").value, "second");
  assert.doesNotMatch(ui.doc.getElementById("registration-preview").textContent, /first/);
});

test("registration failure preserves files and prevents double submission", async (t) => {
  const pending = deferred();
  const ui = await mount("index.html", {
    "/api/templates/registration-preview": [registrationGroup("exam")],
    "/api/templates/register-files": () => pending.promise,
  });
  t.after(() => ui.dom.window.close());
  await ui.w.selectRegistrationFile(registrationFiles(ui, "exam"));
  ui.doc.getElementById("create-btn").click();
  ui.doc.getElementById("create-form").dispatchEvent(new ui.w.Event("submit", { cancelable: true }));
  assert.equal(ui.doc.getElementById("create-fields").disabled, true);
  assert.equal(ui.calls.filter((call) => call.url.endsWith("register-files")).length, 1);
  pending.reject(new Error("offline")); await tick();
  assert.equal(ui.doc.getElementById("create-fields").disabled, false);
  assert.equal(ui.doc.getElementById("create-btn").disabled, false);
  assert.equal(ui.w.eval("registrationFiles.length"), 2);
  assert.match(ui.doc.getElementById("create-notice").textContent, /保持/);
});

test("partial registration retries only the failed sheet and its references", async (t) => {
  const ui = await mount("index.html", {
    "/api/templates/registration-preview": [registrationGroup("first"), registrationGroup("second")],
    "/api/templates/register-files": {
      templates: [{ sheet: "first_sheet.pdf", template: { template_id: "first", title: "first" } }],
      failures: [{ sheet: "second_sheet.pdf", message: "failed" }],
    },
  });
  t.after(() => ui.dom.window.close());
  await ui.w.selectRegistrationFile([...registrationFiles(ui, "first"), ...registrationFiles(ui, "second")]);
  assert.equal(ui.doc.getElementById("tpl-title").disabled, true);
  ui.routes["/api/templates/registration-preview"] = [registrationGroup("second")];
  ui.doc.getElementById("create-btn").click(); await tick();
  assert.deepEqual(Array.from(ui.w.eval("registrationFiles"), (file) => file.name), ["second_sheet.pdf", "second_answers.pdf"]);
  assert.equal(ui.doc.querySelectorAll("#registration-results a").length, 1);
  assert.match(ui.doc.getElementById("create-notice").textContent, /失敗した用紙だけ/);
});

test("new questions automatically select paired answer references", async (t) => {
  const data = template();
  data.default_refs = ["refs/exam_answers.pdf", "refs/exam_rubric.md"];
  const ui = await mount("editor.html", {
    "/api/templates/tpl": data,
    "/api/templates/tpl/refs": data.default_refs.map((path) => ({ path, name: path.slice(5), is_dir: false })),
  });
  t.after(() => ui.dom.window.close());
  ui.doc.getElementById("add-question").click();
  const checks = Array.from(ui.doc.querySelectorAll(".question-item")).at(-1).querySelectorAll("input[type=checkbox]");
  assert.deepEqual(Array.from(ui.w.eval("template.questions.at(-1).refs")), data.default_refs);
  assert.equal(checks.length, 2);
  assert.equal(Array.from(checks).every((checkbox) => checkbox.checked), true);
});
