import { apply } from "./lib/index.js";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function waitForJson(file, timeout = 3000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    try {
      return JSON.parse(await fs.readFile(file, "utf8"));
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
  }
  throw new Error(`timed out waiting for ${file}`);
}

const directory = await fs.mkdtemp(path.join(os.tmpdir(), "dsh-notifyhub-"));
const output = path.join(directory, "payload.json");
const handlers = new Map();
const ctx = {
  on(event, handler) {
    handlers.set(event, handler);
  },
};

try {
  const capture = "require('fs').writeFileSync(process.argv[1], require('fs').readFileSync(0))";
  apply(ctx, { command: [process.execPath, "-e", capture, output] });
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
  handle(session, { type: "turn/end", data: { kind: "completed" }, seq: 2, time: 2 });

  const payload = await waitForJson(output);
  assert(payload.session_id === "session-1", "session id mismatch");
  assert(payload.cwd === "/tmp/demo", "cwd mismatch");
  assert(payload.title === "帮我跑测试", "title mismatch");
  assert(payload.recent_prompt === "再检查一下", "recent prompt mismatch");
  assert(payload.last_assistant_message === "已检查", "last message mismatch");
  assert(payload.model === "deepseek-chat", "model mismatch");
  assert(payload.status === "completed", "status mismatch");
  assert(payload.origin === "main", "origin mismatch");

  const before = await fs.readFile(output, "utf8");
  handle(
    {
      id: "session-2",
      header: { cwd: "/tmp/demo", parentSession: "session-1" },
      deriveMessages: () => [],
    },
    { type: "turn/end", data: { kind: "completed" }, seq: 3, time: 3 },
  );
  await new Promise((resolve) => setTimeout(resolve, 300));
  const after = await fs.readFile(output, "utf8");
  assert(before === after, "subagent session should be skipped by default");

  console.log("dsh-notifyhub: ok");
} finally {
  await fs.rm(directory, { recursive: true, force: true });
}
