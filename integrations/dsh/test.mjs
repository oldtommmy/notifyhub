import { apply } from "./lib/index.js";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function waitForJson(file, predicate = () => true, timeout = 3000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    try {
      const data = JSON.parse(await fs.readFile(file, "utf8"));
      if (predicate(data)) return data;
    } catch {
      /* file may not exist yet, or may be partially written */
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`timed out waiting for ${file}`);
}

const directory = await fs.mkdtemp(path.join(os.tmpdir(), "dsh-notifyhub-"));
// Keep test logging out of the real ~/.local/state/notifyhub/dsh-plugin.log.
process.env.NOTIFYHUB_DSH_LOG = path.join(directory, "dsh-plugin.log");

const handlers = new Map();
const ctx = {
  on(event, handler) {
    handlers.set(event, handler);
  },
};

try {
  // Capture each payload into <directory>/<session_id>.json so separate turns
  // do not overwrite each other.
  const capture =
    "const fs=require('fs');const path=require('path');" +
    "const payload=JSON.parse(fs.readFileSync(0,'utf8'));" +
    "fs.writeFileSync(path.join(process.argv[1], `${payload.session_id}.json`), JSON.stringify(payload));";
  apply(ctx, { command: [process.execPath, "-e", capture, directory] });
  const handle = handlers.get("session/event");
  assert(typeof handle === "function", "session/event handler was not registered");

  const session = {
    id: "session-1",
    header: { cwd: "/tmp/demo" },
    deriveMessages: () => [
      { role: "user", source: { kind: "runtime-context" }, content: "<system-reminder>current directory</system-reminder>" },
      { role: "user", source: { kind: "user" }, content: "帮我跑测试" },
      {
        role: "assistant",
        source: { kind: "model" },
        content: [
          { type: "reasoning", text: "hidden chain of thought" },
          { type: "text", text: "**完成**" },
        ],
      },
      { role: "user", source: { kind: "time-context" }, content: "Time sampled while preparing turn 1" },
      { role: "user", source: { kind: "user" }, content: "再检查一下" },
      {
        role: "assistant",
        source: { kind: "model" },
        content: [
          { type: "reasoning", text: "more hidden thought" },
          { type: "text", text: "已检查" },
        ],
      },
      { role: "user", source: { kind: "time-context" }, content: "Time sampled after the answer" },
    ],
  };

  handle(session, {
    type: "session/title",
    data: { title: "测试会话", messageSeqs: [9], source: { kind: "fallback" } },
    seq: 0,
    time: 0,
  });
  handle(session, {
    type: "request/header",
    data: { header: { config: { provider: "deepseek", model: "deepseek-chat" } } },
    seq: 1,
    time: 1,
  });
  handle(session, { type: "turn/end", data: { turn: 1, reason: { kind: "completed" } }, seq: 2, time: 2 });

  const payload = await waitForJson(path.join(directory, "session-1.json"));
  assert(payload.session_id === "session-1", "session id mismatch");
  assert(payload.title === "测试会话", "title should come from session/title");
  assert(payload.cwd === "/tmp/demo", "cwd mismatch");
  assert(payload.recent_prompt === "再检查一下", "recent prompt should skip synthetic user messages");
  assert(payload.last_assistant_message === "已检查", "last message should be the text block");
  assert(!payload.last_assistant_message.includes("hidden"), "reasoning must not reach the body");
  assert(payload.model === "deepseek-chat", "model should come from request/header.header.config");
  assert(payload.status === "completed", "status mismatch");
  assert(payload.origin === "main", "origin mismatch");

  handle(
    {
      id: "session-2",
      header: { cwd: "/tmp/demo", parentSession: "session-1" },
      deriveMessages: () => [],
    },
    { type: "turn/end", data: { turn: 1, reason: { kind: "completed" } }, seq: 3, time: 3 },
  );

  // The plugin must forward subagent turns too; notifyhub's Python adapter is the
  // single place that filters them.
  const subagent = await waitForJson(path.join(directory, "session-2.json"));
  assert(subagent.session_id === "session-2", "subagent session id mismatch");
  assert(subagent.origin === "subagent", "subagent payload should keep origin=subagent");
  assert(subagent.status === "completed", "subagent status mismatch");

  // No session/title: the title falls back to the first real user message, not
  // the runtime-context message.
  const fallbackSession = {
    id: "session-3",
    header: { cwd: "/tmp/fallback" },
    deriveMessages: () => [
      { role: "user", source: { kind: "runtime-context" }, content: "<system-reminder>context</system-reminder>" },
      { role: "user", source: { kind: "user" }, content: "真实提问" },
      { role: "assistant", source: { kind: "model" }, content: [{ type: "text", text: "回答" }] },
    ],
  };
  handle(fallbackSession, { type: "turn/end", data: { turn: 1, reason: { kind: "completed" } }, seq: 4, time: 4 });

  const fallback = await waitForJson(path.join(directory, "session-3.json"));
  assert(fallback.title === "真实提问", "title fallback should skip runtime context");
  assert(fallback.recent_prompt === "真实提问", "recent prompt fallback mismatch");
  assert(fallback.last_assistant_message === "回答", "fallback last message mismatch");

  // model/selection is the fallback model source when no request/header is seen.
  handle(
    { id: "session-4", header: { cwd: "/tmp/selection" }, deriveMessages: () => [] },
    { type: "model/selection", data: { provider: "qwen", model: "deepseek-v4.1-flash-1017" }, seq: 5, time: 5 },
  );
  handle(
    { id: "session-4", header: { cwd: "/tmp/selection" }, deriveMessages: () => [] },
    { type: "turn/end", data: { turn: 1, reason: { kind: "completed" } }, seq: 6, time: 6 },
  );

  const selection = await waitForJson(path.join(directory, "session-4.json"));
  assert(selection.model === "deepseek-v4.1-flash-1017", "model/selection mismatch");
  assert(selection.title === "selection", "cwd basename fallback mismatch");

  // npm layout: lib/ and scripts/notifyhub.py live beside each other.
  const pkgDir = path.join(directory, "pkg");
  await fs.mkdir(path.join(pkgDir, "lib"), { recursive: true });
  await fs.mkdir(path.join(pkgDir, "scripts"), { recursive: true });
  await fs.writeFile(path.join(pkgDir, "package.json"), JSON.stringify({ type: "module" }));
  await fs.copyFile(new URL("./lib/index.js", import.meta.url), path.join(pkgDir, "lib", "index.js"));
  await fs.writeFile(path.join(pkgDir, "scripts", "notifyhub.py"), "");
  const resolverLog = path.join(directory, "resolver.log");
  process.env.NOTIFYHUB_DSH_LOG = resolverLog;
  const npmModule = await import(pathToFileURL(path.join(pkgDir, "lib", "index.js")).href);
  npmModule.apply({ on() {} }, {});
  const loaded = await fs.readFile(resolverLog, "utf8");
  assert(
    loaded.includes(path.join(pkgDir, "scripts", "notifyhub.py")),
    "npm layout launcher was not found",
  );

  console.log("dsh-notifyhub: ok");
} finally {
  await fs.rm(directory, { recursive: true, force: true });
}
