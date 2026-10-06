// SPDX-FileCopyrightText: 2026 Continuum contributors
// SPDX-License-Identifier: AGPL-3.0-only
/* 壳前端 DOM-stub 冒烟：页面 JS 在最小 DOM 环境 + 真实壳 API 下初始化与渲染。
   由 tests/test_shell_dom_smoke.py 以 node 子进程运行（node 缺失自动跳过）。
   需要 shell 服务运行在 SHELL_URL（默认 http://127.0.0.1:8501）。 */
const fs = require("fs");
const path = require("path");
const REPO = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(REPO, "src", "continuum", "shell", "static", "index.html"), "utf8");
const js = html.match(/<script>([\s\S]*)<\/script>/)[1];
const i18njs = fs.readFileSync(path.join(REPO, "src", "continuum", "shell", "static", "i18n.js"), "utf8");
const BASE = process.env.SHELL_URL || "http://127.0.0.1:8501";

const elements = {};
function makeEl(tag) {
  return { tag, children: [], style: {}, dataset: {}, value: "", textContent: "", innerHTML: "",
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    setAttribute() {}, appendChild(c) { this.children.push(c); },
    insertBefore(c) { this.children.unshift(c); }, addEventListener() {},
    querySelector(s) { if (!elements[s]) elements[s] = makeEl(s); return elements[s]; },
    querySelectorAll() { return []; } };
}
for (const id of [...html.matchAll(/id="([\w-]+)"/g)].map(m => m[1]))
  elements["#" + id] = makeEl("div");
const realFetch = global.fetch;
global.document = {
  querySelector(s) { if (!elements[s]) elements[s] = makeEl(s); return elements[s]; },
  querySelectorAll() { return []; },
  getElementById(s) { return elements["#" + s] || makeEl("div"); },
  createElement(t) { return makeEl(t); },
  documentElement: { lang: "", dir: "", setAttribute() {} },
  title: "",
};
global.localStorage = { getItem() { return null; }, setItem() {} };
global.window = global;
global.fetch = (p, o) => realFetch(BASE + p, o);

const failures = [];
new Function(i18njs + "\n;global.I18N = I18N;")();
try { new Function(js)(); } catch (e) { failures.push("初始化崩溃: " + e.message); }
setTimeout(() => {
  if (global.__errors && global.__errors.length)
    failures.push("运行时错误: " + global.__errors.join(" | "));
  if (!(elements["#stats"] || {}).innerHTML || elements["#stats"].innerHTML.length < 50)
    failures.push("统计卡未渲染");
  if (!(elements["#sess tbody"] || {}).innerHTML.includes("8100开发"))
    failures.push("会话表未渲染出 8100开发（需已导入该会话）");
  const db = (elements["#dbpath"] || {}).textContent || "";
  if (!db.includes(".continuum")) failures.push("db 路径未渲染: " + db);
  if (failures.length) { console.error("DOM-SMOKE FAIL:\n" + failures.join("\n")); process.exit(1); }
  console.log("DOM-SMOKE OK: 初始化/渲染/数据全部通过");
  process.exit(0);
}, 2500);
