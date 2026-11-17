import { spawn } from "node:child_process";
import { appendFileSync, existsSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import os from "node:os";

export const name = "dsh-notifyhub";

const here = path.dirname(fileURLToPath(import.meta.url));
const maxSeen = 500;

function log(message) {
  try {
    const file = process.env.NOTIFYHUB_DSH_LOG || path.join(os.homedir(), ".local", "state", "notifyhub", "dsh-plugin.log");
    mkdirSync(path.dirname(file), { recursive: true });
    appendFileSync(file, `${new Date().toISOString()} ${message}\n`);
  } catch {
    /* logging is best effort */
  }
}

function pythonCommand() {
  if (process.env.NOTIFYHUB_PYTHON) return [process.env.NOTIFYHUB_PYTHON];
  if (process.env.PYTHON) return [process.env.PYTHON];
  return process.platform === "win32" ? ["python"] : ["python3"];
}

function resolveCommand(config) {
  if (Array.isArray(config?.command) && config.command.length > 0) {
    return config.command;
  }
  const root = path.resolve(here, "../../..");
  const candidates = [
    path.join(root, "scripts", "notifyhub.py"),
    path.join(root, "notifyhub.py"),
  ];
  const launcher = candidates.find((candidate) => existsSync(candidate));
  if (launcher) {
    return [...pythonCommand(), launcher, "hook", "dsh"];
  }
  return ["notifyhub", "hook", "dsh"];
}

function messageText(message) {
  const content = message?.content;
  if (typeof content === "string") return content.trim();
  if (Array.isArray(content)) {
    return content
      .map((part) => {
        if (typeof part === "string") return part;
        if (!part || typeof part !== "object") return "";
        // Only user-visible text. Reasoning, tool calls, and images must never
        // reach the notification body.
        if (part.type !== "text" && part.type !== "input_text" && part.type !== "output_text") {
          return "";
        }
        return part.text ?? part.content ?? "";
      })
      .filter(Boolean)
      .join("\n")
      .trim();
  }
  return "";
}

function messagesOf(session) {
  try {
    const messages = typeof session?.deriveMessages === "function" ? session.deriveMessages() : [];
    return Array.isArray(messages) ? messages : [];
  } catch {
    return [];
  }
}

function sessionTitle(session) {
  try {
    return typeof session?.title === "string" ? session.title.trim() : "";
  } catch {
    return "";
  }
}

function isRealUserMessage(message) {
  const role = String(message?.role || "").toLowerCase();
  if (role !== "user" && role !== "human") return false;
  const source = message?.source;
  if (source && typeof source === "object" && typeof source.kind === "string" && source.kind !== "user") {
    return false;
  }
  return true;
}

function firstUserText(messages) {
  for (const message of messages) {
    if (!isRealUserMessage(message)) continue;
    const text = messageText(message);
    if (text) return text;
  }
  return "";
}

function lastUserText(messages) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (!isRealUserMessage(message)) continue;
    const text = messageText(message);
    if (text) return text;
  }
  return "";
}

function lastAssistantText(messages) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    const role = String(message?.role || "").toLowerCase();
    if (role !== "assistant" && role !== "ai") continue;
    const text = messageText(message);
    if (text) return text;
  }
  return "";
}

function modelText(value) {
  if (typeof value === "string") return value;
  if (value && typeof value === "object") {
    return String(value.model || value.slug || value.id || "");
  }
  return "";
}

function modelFrom(data) {
  const config = data?.header?.config;
  return (
    modelText(config?.model) ||
    modelText(config) ||
    modelText(data?.model) ||
    modelText(data?.modelId) ||
    modelText(data?.modelSlug) ||
    modelText(data?.request?.model) ||
    ""
  );
}

export function apply(ctx, config = {}) {
  if (config.enabled === false) return;

  const command = resolveCommand(config);
  const seen = new Set();
  const models = new Map();
  const titles = new Map();
  log(`loaded; command=${command.join(" ")}`);

  function send(payload) {
    log(`send session=${payload.session_id} status=${payload.status} origin=${payload.origin}`);
    try {
      const child = spawn(command[0], command.slice(1), {
        stdio: ["pipe", "ignore", "ignore"],
        detached: true,
        windowsHide: true,
      });
      child.on("error", (error) => {
        log(`spawn failed: ${error?.message || error}`);
        console.warn("[dsh-notifyhub] notify failed:", error?.message || error);
      });
      child.stdin.on("error", () => {});
      child.stdin.end(JSON.stringify(payload));
      child.unref();
    } catch (error) {
      log(`notify failed: ${error?.message || error}`);
      console.warn("[dsh-notifyhub] notify failed:", error?.message || error);
    }
  }

  ctx.on("session/event", (session, event) => {
    if (!session || !event) return;

    if (event.type === "request/header" || event.type === "model/selection") {
      const model = modelFrom(event.data);
      if (model) models.set(session.id, model);
      return;
    }

    if (event.type === "session/title") {
      const title = String(event.data?.title || "").trim();
      if (title) titles.set(session.id, title);
      return;
    }

    if (event.type !== "turn/end") return;
    const status = event.data?.reason?.kind || event.data?.kind || "completed";
    log(`turn/end session=${session.id} status=${status}`);

    const header = session.header || {};
    const origin =
      header.parentSession || (header.delegationDepth || 0) > 0 ? "subagent" : "main";

    const key = `${session.id}:${event.seq ?? event.time ?? ""}`;
    if (seen.has(key)) return;
    seen.add(key);
    if (seen.size > maxSeen) {
      seen.delete(seen.values().next().value);
    }

    const messages = messagesOf(session);
    const cwd = header.cwd || "";
    const title =
      titles.get(session.id) ||
      sessionTitle(session) ||
      firstUserText(messages) ||
      path.basename(cwd) ||
      String(session.id || "");

    send({
      hook_event_name: "SessionComplete",
      source: "dsh",
      session_id: String(session.id || ""),
      cwd,
      title,
      model: models.get(session.id) || "",
      recent_prompt: lastUserText(messages),
      last_assistant_message: lastAssistantText(messages),
      status,
      completed_at: event.time || 0,
      seq: event.seq ?? 0,
      origin,
    });
  });
}
