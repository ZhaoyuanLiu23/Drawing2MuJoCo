// Compile the real JSX with Next's already-installed compiler; no new test dependency.
const { before, test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const swc = require("next/dist/build/swc");
const cache = new Map();
function load(relative) {
  const filename = path.resolve(__dirname, "..", relative);
  if (cache.has(filename)) return cache.get(filename).exports;
  const compiled = swc.transformSync(fs.readFileSync(filename, "utf8"), {
    filename, jsc: { parser: { syntax: "ecmascript", jsx: true },
      transform: { react: { runtime: "automatic" } }, target: "es2020" }, module: { type: "commonjs" },
  });
  const mod = new Module(filename);
  cache.set(filename, mod);
  mod.paths = Module._nodeModulePaths(path.dirname(filename));
  const normalRequire = Module.createRequire(filename);
  mod.require = (name) => name.startsWith(".") ? load(path.resolve(path.dirname(filename), name + ".js")) : normalRequire(name);
  mod._compile(compiled.code, filename);
  return mod.exports;
}
let Preview, Results, simulateJob;
before(async () => {
  await swc.loadBindings();
  Preview = load("components/simulation-preview.js").default;
  Results = load("components/simulation-results.js").default;
  simulateJob = load("lib/api.js").simulateJob;
});
const job = "a".repeat(32), run = "b".repeat(32);
const url = `/api/jobs/${job}/simulation/${run}/simulation.mp4`;
const base = { job_id: job, run_id: run, status: "failed", visual_task_success: false, video_available: false, video_url: null };
const render = (result) => renderToStaticMarkup(React.createElement(Preview, { result }));

test("no video degrades visibly without marking the failed task successful", () => {
  const html = render(base);
  assert.match(html, /Video unavailable/);
  assert.match(html, /任务失败/);
  assert.doesNotMatch(html, /<video|任务成功/);
});
test("failed task may play its recording while keeping FAILED and the original error", () => {
  const result = { ...base, video_available: true, video_url: url, error: { code: "GRASP_FAILED", message: "failure evidence" } };
  assert.match(render(result), /<video/);
  assert.match(render(result), /FAILED/);
  const results = renderToStaticMarkup(React.createElement(Results, { result }));
  assert.match(results, /GRASP_FAILED/);
  assert.doesNotMatch(results, /任务成功/);
});
test("successful recording has native playback controls, autoplay and a download", () => {
  const html = render({ ...base, status: "succeeded", visual_task_success: true, video_available: true, video_url: url });
  for (const attribute of ["controls", "autoPlay", "muted", "playsInline"]) assert.match(html, new RegExp(attribute, "i"));
  assert.match(html, /保存视频/);
  assert.match(html, /任务成功/);
});
test("running never plays an old recording", () => {
  const html = render({ ...base, status: "running", video_available: true, video_url: url });
  assert.match(html, /正在运行仿真并录制/);
  assert.doesNotMatch(html, /<video/);
});
test("API keeps failure and video availability independent", async (context) => {
  context.mock.method(globalThis, "fetch", async () => ({ ok: false, json: async () => ({ ...base, video_available: true, video_url: url }) }));
  assert.equal((await simulateJob(job, {})).status, "failed");
});
test("API refuses a video from any other path", async (context) => {
  context.mock.method(globalThis, "fetch", async () => ({ ok: false, json: async () => ({ ...base, video_available: true, video_url: "file:///elsewhere.mp4" }) }));
  await assert.rejects(simulateJob(job, {}), /视频地址无效/);
});
test("API with no video clears a stale video URL", async (context) => {
  context.mock.method(globalThis, "fetch", async () => ({ ok: false, json: async () => ({ ...base, video_url: url }) }));
  assert.equal((await simulateJob(job, {})).video_url, null);
});
