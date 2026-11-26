const $ = (id) => document.getElementById(id);
const all = (selector) => [...document.querySelectorAll(selector)];
const token = document.querySelector('meta[name="notifyhub-token"]').content;
const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");
const state = {
  screen: "channels", channel: "feishu_app", agent: "overview", config: null,
  drafts: new Map(), status: null, bridgeDirty: false, saving: false,
  quotaLoaded: false, update: null, gatewayNote: "",
};
const names = { feishu_app: "应用机器人", feishu: "群机器人", imessage: "iMessage" };
const agents = {
  codex: { label: "Codex", hint: "安装后在 Codex 中打开 /hooks 并信任 NotifyHub。" },
  dsh: { label: "DeepSeek Harness", hint: "由 Skill 安装本地适配器。安装后重启所选的 dsh。" },
  antigravity: { label: "Antigravity", hint: "安装后在 Antigravity 设置中确认 NotifyHub Stop hook 已启用。" },
  claude: { label: "Claude Code", hint: "安装后重启 Claude Code，再用 /hooks 核对。" },
  commandcode: { label: "Command Code", hint: "安装后重启 Command Code 并核对 hook。" },
};
const recipientExamples = { open_id: "ou_…", union_id: "on_…", chat_id: "oc_…", email: "you@company.com", user_id: "user_id" };
let receiverTimer;
let receiverRevision = 0;
let toastTimer;

async function api(path, method = "GET", data) {
  const headers = { "X-Notifyhub-Token": token };
  if (data !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch("/api/" + path, { method, headers, body: data === undefined ? undefined : JSON.stringify(data) });
  const result = await response.json();
  if (!response.ok || result.ok === false) throw new Error(result.error || "请求失败（HTTP " + response.status + "）");
  return result;
}

function toast(message, error = false) {
  clearTimeout(toastTimer);
  $("toast").textContent = message;
  $("toast").classList.toggle("error", error);
  $("toast").hidden = false;
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 4000);
}

function action(id, handler) {
  $(id).addEventListener("click", async () => {
    const button = $(id);
    button.disabled = true;
    try {
      await handler();
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
      renderFooter();
      if (state.status) renderGateway();
    }
  });
}

function tabset(id, select) {
  const tabs = [...$(id).querySelectorAll('[role="tab"]')];
  function activate(value, focus = false) {
    const current = tabs.find((tab) => tab.dataset.tab === value) || tabs[0];
    for (const tab of tabs) {
      tab.setAttribute("aria-selected", String(tab === current));
      tab.tabIndex = tab === current ? 0 : -1;
    }
    if (focus) current.focus();
    select(current.dataset.tab, current);
  }
  for (const [index, tab] of tabs.entries()) {
    tab.addEventListener("click", () => activate(tab.dataset.tab));
    tab.addEventListener("keydown", (event) => {
      const indices = {
        ArrowLeft: (index - 1 + tabs.length) % tabs.length,
        ArrowRight: (index + 1) % tabs.length, Home: 0, End: tabs.length - 1,
      };
      if (!(event.key in indices)) return;
      event.preventDefault();
      activate(tabs[indices[event.key]].dataset.tab, true);
    });
  }
  return activate;
}

function configValue(path) {
  return path.split(".").reduce((value, key) => value?.[key], state.config);
}

function fieldValue(path) {
  return state.drafts.has(path) ? state.drafts.get(path) : configValue(path);
}

function setControl(input, value) {
  if (input.type === "checkbox") input.checked = value === true;
  else input.value = value ?? "";
}

function controlValue(input) {
  if (input.type === "checkbox") return input.checked;
  if (input.type === "number") return input.value === "" ? null : input.valueAsNumber;
  return input.value.trim();
}

function edit(path, value) {
  if (JSON.stringify(value) === JSON.stringify(configValue(path))) state.drafts.delete(path);
  else state.drafts.set(path, value);
  renderFooter();
}

function renderConfig() {
  for (const input of all("[data-config]")) setControl(input, fieldValue(input.dataset.config));
  $("receiveId").placeholder = recipientExamples[$("receiveType").value] || "ou_…";
  if ($("receiveId").value && !state.drafts.has("channels.feishu_app.receive_id")) {
    $("receiverStatus").textContent = "接收人已保存。";
  }
  renderAgent();
}

function renderAgent() {
  if (state.agent === "overview") return;
  for (const node of all("[data-agent-only]")) node.hidden = node.dataset.agentOnly !== state.agent;
  for (const input of all("[data-agent-setting]")) {
    const value = fieldValue(state.agent + "." + input.dataset.agentSetting);
    setControl(input, value);
  }
  const events = fieldValue("codex.events") || ["Stop"];
  for (const input of all("[data-event]")) input.checked = events.includes(input.dataset.event);
  if (!state.status) return;
  const installed = state.status.agents[state.agent];
  const dsh = state.agent === "dsh";
  const isInstalled = dsh ? installed.profiles[$("dshProfile").value] : installed.installed;
  badge($("agentStatus"), isInstalled ? "已安装" : "未安装", isInstalled ? "on" : "");
  $("agentMaintenance").hidden = dsh || !isInstalled;
  $("dshCliHelp").hidden = !dsh || installed.cli_found;
  $("agentHint").textContent = dsh && !installed.cli_found ? "请先安装 dsh CLI，或确认桌面版自带的 CLI 可用。" : agents[state.agent].hint;
}

function agentIdentity(name) {
  const identity = document.createElement("span");
  identity.className = "agent-identity";
  const icon = new Image(22, 22);
  icon.className = "agent-icon";
  icon.src = "/assets/agents/" + name + ".png";
  icon.alt = "";
  identity.append(icon, agents[name].label);
  return identity;
}

function renderAgentOverview() {
  const rows = Object.entries(agents).map(([name, { label }]) => {
    const local = state.status.agents[name];
    const line = document.createElement("tr");
    const agent = document.createElement("th");
    agent.scope = "row";
    agent.append(agentIdentity(name));
    line.append(agent);

    const connection = line.insertCell();
    const installation = document.createElement("span");
    badge(installation, local.installed ? "已安装" : "未安装", local.installed ? "on" : "");
    connection.append(installation);
    if (name === "dsh" && local.installed) {
      const profiles = Object.entries({ desktop: "桌面版", web: "Web" })
        .filter(([profile]) => local.profiles[profile]).map(([, title]) => title);
      const detail = document.createElement("span");
      detail.className = "agent-profile";
      detail.textContent = profiles.join(" · ");
      connection.append(detail);
    }

    const notifications = document.createElement("span");
    const enabled = configValue(name + ".enabled") === true;
    badge(notifications, enabled ? "已开启" : "已关闭", enabled ? "on" : "");
    line.insertCell().append(notifications);

    const settings = document.createElement("button");
    settings.className = "btn ghost small";
    settings.type = "button";
    settings.dataset.selectAgent = name;
    settings.textContent = "设置";
    settings.setAttribute("aria-label", "设置 " + label);
    line.insertCell().append(settings);
    return line;
  });
  $("agentOverviewRows").replaceChildren(...rows);
}

function renderFooter() {
  const bridge = state.screen === "continuation";
  const overview = state.screen === "agents" && state.agent === "overview";
  const pending = bridge ? state.bridgeDirty : state.drafts.size > 0;
  for (const input of all('#agentAdvanced input[type="number"]')) {
    input.disabled = state.screen !== "agents" || overview;
  }
  const footer = $("formFooter");
  footer.hidden = state.screen === "runtime" || (overview && !pending);
  const saveArea = overview ? $("agentOverview") : $(state.screen).querySelector("[data-save-area]");
  if (!footer.hidden && saveArea && footer.parentElement !== saveArea) saveArea.append(footer);
  $("dirtyNote").textContent = pending ? "有未保存的更改" : "";
  $("saveBtn").textContent = bridge ? "保存续聊设置" : "保存通知设置";
  $("saveBtn").disabled = !state.config || state.saving || !pending;
}

function patchFrom(entries) {
  const result = {};
  for (const [path, value] of entries) {
    const keys = path.split(".");
    const field = keys.pop();
    let object = result;
    for (const key of keys) object = object[key] ||= {};
    object[field] = value;
  }
  return result;
}

async function saveConfig() {
  if (!state.config || state.saving) return false;
  if (!state.drafts.size) return true;
  state.saving = true;
  renderFooter();
  try {
    const sent = new Map(state.drafts);
    const saved = await api("config", "POST", { config: patchFrom(sent) });
    state.config = saved.config;
    for (const [path, value] of sent) if (state.drafts.get(path) === value) state.drafts.delete(path);
    renderConfig();
    await loadStatus();
    return true;
  } finally {
    state.saving = false;
    renderFooter();
  }
}

function badge(node, label, kind = "") {
  node.textContent = label;
  node.className = kind ? "badge " + kind : "badge";
}

function row(term, value, className = "") {
  const block = document.createElement("div");
  const label = document.createElement("dt");
  const text = document.createElement("dd");
  label.textContent = term;
  text.textContent = value;
  text.className = className;
  block.append(label, text);
  return block;
}

async function loadStatus() {
  state.status = await api("status");
  const { channels, outbox, continuation } = state.status;
  $("channelStatus").replaceChildren(...Object.entries(names).map(([name, label]) => {
    const channel = channels[name];
    const isStaged = !channel.enabled && channel.credentials_ready;
    const value = channel.configured ? "已配置" : channel.enabled ? "缺少配置" : isStaged ? "已保存" : "未启用";
    return row(label, value, "badge" + (channel.configured ? " on" : channel.enabled ? " warn" : ""));
  }));
  $("outboxCounts").replaceChildren(row("积压", outbox.count), row("可重试", outbox.due), row("已停止重试", outbox.dead));
  $("outboxNote").textContent = outbox.enabled ? "" : "自动补发已关闭。";
  $("runtimePaths").replaceChildren(
    ...[
      ["通知配置", state.status.config_path],
      ["网关配置", continuation.configured ? continuation.config_path : ""],
      ["通知日志", state.status.log_path], ["去重记录", state.status.state_path],
    ].filter(([, path]) => path).map(([label, path]) => {
      const item = row(label, "");
      const code = document.createElement("code");
      code.textContent = path;
      item.lastElementChild.append(code);
      return item;
    }),
  );
  if (!$("dshProfile").dataset.selected) {
    const dsh = state.status.agents.dsh;
    $("dshProfile").value = dsh.desktop_app || dsh.profiles.desktop ? "desktop" : "web";
  }
  renderAgentOverview();
  renderAgent();
  renderGateway();
}

function renderGateway() {
  const mode = state.status.continuation;
  if (!state.bridgeDirty) {
    const values = {
      enabled: mode.enabled, allow_open_ids: mode.allow_open_ids.join(", "),
      card_actions: mode.card_actions, use_notification_bot: false,
    };
    for (const agent of ["dsh", "codex", "antigravity"]) values[agent + "_enabled"] = mode.selected_engines.includes(agent);
    for (const input of all("[data-bridge]")) setControl(input, values[input.dataset.bridge]);
  }
  $("bridgeSwitch").hidden = mode.legacy;
  $("agyChoice").hidden = mode.legacy;
  $("syncBot").hidden = mode.legacy || !mode.gateway_configured;
  $("gatewayActions").hidden = mode.legacy;
  $("gatewayStart").hidden = !mode.service_supported || !mode.enabled;
  $("gatewayStart").disabled = state.bridgeDirty || state.gatewayBusy;
  $("gatewayStart").textContent = mode.gateway_running ? "重载网关" : "启动网关";
  $("gatewayStop").hidden = !mode.gateway_running;
  $("gatewayStop").disabled = state.gatewayBusy;
  $("dshAdapter").hidden = !mode.gateway_supported || !mode.gateway_configured || !mode.selected_engines.includes("dsh");
  $("agyAdapter").hidden = !mode.gateway_supported || !mode.gateway_configured || !mode.selected_engines.includes("antigravity");
  $("dshAdapter").disabled = state.bridgeDirty;
  $("agyAdapter").disabled = state.bridgeDirty;
  $("agyAdapter").textContent = mode.antigravity_prepared ? "更新 Antigravity 适配器" : "配置 Antigravity 适配器";
  $("gatewayOptions").hidden = $("syncBot").hidden && $("dshAdapter").hidden && $("agyAdapter").hidden;
  const label = mode.legacy ? (mode.enabled ? "由 dsh 托管" : "dsh 托管 · 未启用") :
    mode.gateway_running ? "网关运行中" : mode.configured ? "网关已配置" : "独立网关";
  badge($("bridgeStatus"), label, mode.gateway_running ? "on" : "");
  const launchHint = mode.service_supported ? "保存后启动或重载网关，使白名单与按钮设置生效。"
    : mode.gateway_supported ? "使用终端中的 gateway 命令启动独立网关。" : "独立续聊网关运行在 macOS 和 Linux。";
  $("gatewayNote").textContent = state.gatewayNote || launchHint;
}

async function saveBridge() {
  if (!state.bridgeDirty || state.saving) return;
  if (state.drafts.size) {
    toast("请先在「通知渠道」或「Agent 接入」保存通知设置。", true);
    return;
  }
  state.saving = true;
  renderFooter();
  const inputs = all("[data-bridge]");
  for (const input of inputs) input.disabled = true;
  try {
    const options = Object.fromEntries(inputs.map((input) => [input.dataset.bridge, controlValue(input)]));
    options.allow_open_ids = options.allow_open_ids.split(/[,，\s]+/).filter(Boolean);
    if (state.status.continuation.legacy) {
      const result = await api("hooks/install-dsh", "POST", { ...options, mode: "native", profile: "desktop" });
      toast(result.message || "已保存续聊设置，请重启 dsh。");
    } else {
      await api("continuation", "POST", options);
      state.gatewayNote = "已保存。启动或重载网关后生效。";
      toast("已保存续聊设置。");
    }
    state.bridgeDirty = false;
    await loadStatus();
  } finally {
    for (const input of inputs) input.disabled = false;
    state.saving = false;
    renderFooter();
  }
}

async function discoverReceiver(automatic = false) {
  if ($("appSecret").value.trim() === "" || $("appId").value.trim() === "") {
    if (!automatic) toast("请先填写 App ID 和 App Secret。", true);
    return;
  }
  const revision = ++receiverRevision;
  const button = $("discoverBtn");
  const output = $("receiverStatus");
  button.disabled = true;
  output.textContent = "正在查找接收人…";
  try {
    const data = await api("feishu/receivers", "POST", {
      app_id: $("appId").value.trim(), app_secret: $("appSecret").value.trim(),
      base_url: configValue("channels.feishu_app.base_url"),
    });
    if (revision !== receiverRevision) return;
    const candidate = (data.candidates || []).find((item) => item.type === $("receiveType").value && item.value);
    if (!candidate) {
      output.textContent = "没找到所选类型的接收人。请核对飞书权限，或填写已核实的接收人。";
      return;
    }
    $("receiveId").value = candidate.value;
    edit("channels.feishu_app.receive_id", candidate.value);
    output.textContent = "已找到 " + (candidate.label || candidate.type) + "。保存设置后生效。";
  } catch (error) {
    if (revision === receiverRevision) output.textContent = error.message;
  } finally {
    if (revision === receiverRevision) button.disabled = false;
  }
}

function queueReceiverLookup() {
  receiverRevision++;
  clearTimeout(receiverTimer);
  $("discoverBtn").disabled = false;
  if ($("receiveId").value.trim() || $("receiveType").value !== "open_id") {
    return;
  }
  receiverTimer = setTimeout(() => discoverReceiver(true), 650);
}

async function loadQuota(refresh = false) {
  state.quotaLoaded = true;
  $("quotaLine").textContent = "正在读取…";
  try {
    const data = await api("quota" + (refresh ? "?refresh=1" : ""));
    $("quotaLine").textContent = data.line + (data.stale ? "（缓存）" : "");
  } catch {
    $("quotaLine").textContent = "暂时无法读取额度。";
  }
}

async function loadUpdate(refresh = false) {
  const data = await api("update" + (refresh ? "?refresh=1" : ""));
  state.update = data;
  $("newVersion").hidden = !data.available;
  $("newVersion").textContent = data.latest + " 可用";
  $("releaseNotes").href = data.release_url;
  $("applyUpdate").hidden = !data.available || !data.can_apply;
  $("versionState").textContent = "当前 " + data.current + (data.available ? "，可更新到 " + data.latest : "");
  const hint = data.can_apply ? "更新保留旧版备份。安装后重新打开 GUI。"
    : data.checkout ? "当前从源码运行。通过 Git 更新此检出目录。" : "请从 NotifyHub Skill 安装目录打开 GUI，安装更新。";
  $("updateHint").textContent = data.offline ? "暂时无法连接 GitHub。" : data.available ? hint : "当前没有可安装的新版 Skill。";
}

function theme(value) {
  const dark = value === "dark" || (value === "auto" && systemTheme.matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  const logo = dark ? "/assets/logo.png" : "/assets/logo-light.png";
  if ($("brandMark").getAttribute("src") !== logo) {
    $("brandMark").src = logo;
    $("favicon").href = logo;
  }
  $("themeSelect").value = value;
}

function bind() {
  const showScreen = tabset("screens", (name) => {
    state.screen = name;
    for (const panel of all(".panel")) panel.hidden = panel.id !== name;
    history.replaceState(null, "", "#" + name);
    if (name === "agents" && state.agent === "codex" && !state.quotaLoaded) loadQuota();
    renderFooter();
  });
  tabset("channelTabs", (name) => {
    state.channel = name;
    for (const channel of Object.keys(names)) $(channel).hidden = channel !== name;
  })("feishu_app");
  for (const name of Object.keys(agents)) {
    const tab = document.createElement("button");
    tab.id = "agent-" + name;
    tab.type = "button";
    tab.dataset.tab = name;
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-controls", "agentFields");
    tab.append(agentIdentity(name));
    $("agentTabs").append(tab);
  }
  for (const option of all("[data-bridge-agent]")) option.prepend(agentIdentity(option.dataset.bridgeAgent));
  const showAgent = tabset("agentTabs", (name, tab) => {
    if (state.agent !== name) {
      $("agentAdvanced").open = false;
      $("agentMaintenance").open = false;
    }
    state.agent = name;
    const overview = name === "overview";
    $("agentOverview").hidden = !overview;
    $("agentFields").hidden = overview;
    if (!overview) $("agentFields").setAttribute("aria-labelledby", tab.id);
    if (state.config) renderAgent();
    if (state.screen === "agents" && name === "codex" && !state.quotaLoaded) loadQuota();
    renderFooter();
  });
  showAgent("overview");
  $("agentOverview").addEventListener("click", (event) => {
    const settings = event.target.closest("[data-select-agent]");
    if (settings) showAgent(settings.dataset.selectAgent, true);
  });
  showScreen(location.hash.slice(1) || "channels");
  $("newVersion").addEventListener("click", () => showScreen("runtime", true));

  for (const input of all("[data-config]")) input.addEventListener("input", () => edit(input.dataset.config, controlValue(input)));
  for (const input of all("[data-agent-setting]")) input.addEventListener("input", () => edit(state.agent + "." + input.dataset.agentSetting, controlValue(input)));
  for (const input of all("[data-event]")) input.addEventListener("input", () => {
    edit("codex.events", all("[data-event]:checked").map((option) => option.dataset.event));
  });
  for (const input of all("[data-bridge]")) input.addEventListener("input", () => {
    state.bridgeDirty = true;
    renderFooter();
    if (state.status) renderGateway();
  });
  $("dshProfile").addEventListener("change", () => { $("dshProfile").dataset.selected = "1"; renderAgent(); });
  $("appId").addEventListener("input", queueReceiverLookup);
  $("appSecret").addEventListener("input", queueReceiverLookup);
  $("receiveId").addEventListener("input", () => {
    receiverRevision++;
    clearTimeout(receiverTimer);
    $("discoverBtn").disabled = false;
    $("receiverStatus").textContent = "";
  });
  $("receiveType").addEventListener("change", () => {
    $("receiveId").placeholder = recipientExamples[$("receiveType").value] || "ou_…";
    queueReceiverLookup();
  });
  $("settings").addEventListener("invalid", (event) => {
    const group = event.target.closest("details");
    if (group) group.open = true;
  }, true);
  $("settings").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      if (state.screen === "continuation") await saveBridge();
      else if (await saveConfig()) toast("已保存通知设置。");
    } catch (error) {
      toast(error.message, true);
    }
  });
  $("discoverBtn").addEventListener("click", () => discoverReceiver());
  action("testBtn", async () => {
    const channel = state.channel;
    if (await saveConfig()) {
      await api("test", "POST", { channel });
      toast("测试通知已发送。");
    }
  });
  action("installAgent", async () => {
    const target = state.agent;
    const profile = $("dshProfile").value;
    if (!(await saveConfig())) return;
    const isDsh = target === "dsh";
    await api(isDsh ? "hooks/install-dsh" : "hooks/install", "POST", { target, profile });
    toast(isDsh ? "接入已安装。请重启所选的 dsh。" : "接入已安装。");
    await loadStatus();
  });
  action("uninstallAgent", async () => {
    const target = state.agent;
    if (!window.confirm("卸载此 Agent 的 NotifyHub 接入？")) return;
    await api("hooks/uninstall", "POST", { target });
    toast("接入已卸载。");
    await loadStatus();
  });
  for (const [id, stop] of [["gatewayStart", false], ["gatewayStop", true]]) {
    action(id, async () => {
      if (stop && !window.confirm("停止独立续聊网关？")) return;
      state.gatewayBusy = true;
      try {
        await api("continuation/gateway", "POST", { stop });
        state.gatewayNote = stop ? "网关已停止。" : "网关已启动。发「帮助」验证飞书长连接。";
        await loadStatus();
        toast(state.gatewayNote);
      } finally {
        state.gatewayBusy = false;
      }
    });
  }
  for (const [id, engine] of [["dshAdapter", "dsh"], ["agyAdapter", "antigravity"]]) {
    action(id, async () => {
      const data = await api("continuation/" + engine, "POST", {});
      toast(data.message || "适配器已配置。请重新打开 Antigravity。");
      await loadStatus();
    });
  }
  action("quotaRefresh", () => loadQuota(true));
  action("statusRefresh", loadStatus);
  action("checkUpdate", () => loadUpdate(true));
  action("applyUpdate", async () => {
    if (state.drafts.size || state.bridgeDirty) {
      toast("请先保存设置，再安装更新。", true);
      return;
    }
    if (!window.confirm("安装 " + state.update.latest + " 并备份当前 Skill？")) return;
    const result = await api("update", "POST", { version: state.update.latest });
    $("versionState").textContent = "已安装 " + result.version;
    $("applyUpdate").hidden = true;
    $("newVersion").hidden = true;
    $("updateHint").textContent = "请重新打开 GUI，并重载正在使用的接入。旧版备份在 " + result.backup;
    toast("更新已安装。");
  });

  const themeSelect = $("themeSelect");
  const themeKey = "notifyhub-appearance";
  const preference = localStorage.getItem(themeKey);
  const previous = localStorage.getItem("notifyhub-theme");
  // Earlier versions saved "auto" even when the user had never chosen a theme.
  const defaultTheme = previous === "light" ? "light" : "dark";
  theme(["dark", "light", "auto"].includes(preference) ? preference : defaultTheme);
  themeSelect.addEventListener("change", () => {
    localStorage.setItem(themeKey, themeSelect.value);
    theme(themeSelect.value);
  });
  systemTheme.addEventListener("change", () => {
    if (themeSelect.value === "auto") theme("auto");
  });
  window.addEventListener("beforeunload", (event) => {
    if (!state.drafts.size && !state.bridgeDirty) return;
    event.preventDefault();
    event.returnValue = "";
  });
}

async function boot() {
  bind();
  loadUpdate().catch(() => { $("versionState").textContent = "暂时无法检查版本。"; });
  try {
    state.config = (await api("config")).config;
    renderConfig();
    renderFooter();
    await loadStatus();
    queueReceiverLookup();
  } catch (error) {
    const loading = $("agentOverviewRows").querySelector('[role="status"]');
    if (loading) loading.textContent = "暂时无法读取接入状态。";
    toast(error.message, true);
  }
}

boot();
