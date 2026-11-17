import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

export const name = "dsh-notifyhub";

const here = path.dirname(fileURLToPath(import.meta.url));
const maxSeen = 500;

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
    return [process.env.NOTIFYHUB_PYTHON || "python3", launcher, "hook", "dsh"];
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
        return part?.text ?? part?.content ?? "";
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

function firstUserText(messages) {
  for (const message of messages) {
    const role = String(message?.role || "").toLowerCase();
    if (role !== "user" && role !== "human") continue;
    const text = messageText(message);
    if (text) return text;
  }
  return "";
}

function lastUserText(messages) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    const role = String(message?.role || "").toLowerCase();
    if (role !== "user" && role !== "human") continue;
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
  return (
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

  function send(payload) {
    try {
      const child = spawn(command[0], command.slice(1), {
        stdio: ["pipe", "ignore", "ignore"],
        detached: true,
      });
      child.on("error", (error) => {
        console.warn("[dsh-notifyhub] notify failed:", error?.message || error);
      });
      child.stdin.on("error", () => {});
      child.stdin.end(JSON.stringify(payload));
      child.unref();
    } catch (error) {
      console.warn("[dsh-notifyhub] notify failed:", error?.message || error);
    }
  }

  ctx.on("session/event", (session, event) => {
    if (!session || !event) return;

    if (event.type === "request/header") {
      const model = modelFrom(event.data);
      if (model) models.set(session.id, model);
      return;
    }

    if (event.type !== "turn/end") return;

    const header = session.header || {};
    const origin =
      header.parentSession || (header.delegationDepth || 0) > 0 ? "subagent" : "main";
    if (origin === "subagent" && config.includeSubagents !== true) return;

    const key = `${session.id}:${event.seq ?? event.time ?? ""}`;
    if (seen.has(key)) return;
    seen.add(key);
    if (seen.size > maxSeen) {
      seen.delete(seen.values().next().value);
    }

    const messages = messagesOf(session);
    const cwd = header.cwd || "";
    const title = firstUserText(messages) || path.basename(cwd) || String(session.id || "");

    send({
      hook_event_name: "SessionComplete",
      source: "dsh",
      session_id: String(session.id || ""),
      cwd,
      title,
      model: models.get(session.id) || "",
      recent_prompt: lastUserText(messages),
      last_assistant_message: lastAssistantText(messages),
      status: event.data?.kind || "completed",
      completed_at: event.time || 0,
      seq: event.seq ?? 0,
      origin,
    });
  });
}
