import { apply } from "./lib/index.js";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

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
const handlers = new Map();
const ctx = {
  on(event, handler) {
    handlers.set(event, handler);
  },
};

try {
  // Capture each payload into <directory>/<session_id>.json so the main turn and
  // the subagent turn do not overwrite each other.
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
      { role: "user", content: "帮我跑测试" },
      { role: "assistant", content: "**完成**" },
      { role: "user", content: "再检查一下" },
      { role: "assistant", content: "已检查" },
    ],
  };

  handle(session, { type: "request/header", data: { model: { model: "deepseek-chat" } }, seq: 1, time: 1 });
  handle(session, { type: "turn/end", data: { turn: 1, reason: { kind: "completed" } }, seq: 2, time: 2 });

  const payload = await waitForJson(path.join(directory, "session-1.json"));
  assert(payload.session_id === "session-1", "session id mismatch");
  assert(payload.cwd === "/tmp/demo", "cwd mismatch");
  assert(payload.title === "帮我跑测试", "title mismatch");
  assert(payload.recent_prompt === "再检查一下", "recent prompt mismatch");
  assert(payload.last_assistant_message === "已检查", "last message mismatch");
  assert(payload.model === "deepseek-chat", "model mismatch");
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

  console.log("dsh-notifyhub: ok");
} finally {
  await fs.rm(directory, { recursive: true, force: true });
}
