import fs from 'node:fs';
import path from 'node:path';

// Shared with the ordinary notifier, without importing either plugin into the other.
export const ownedTurns = globalThis[Symbol.for('notifyhub.nativeBridge.turns')] ??= new Set();
export const turnKey = (session, turn) => `${session}:${turn}`;

export class NativeController {
  constructor(ctx, { statePath, createUserMessage, output }) {
    this.ctx = ctx;
    this.statePath = statePath;
    this.createUserMessage = createUserMessage;
    this.output = output;
    this.pending = new Map();
    this.cache = new Map();
    this.data = { bindings: {}, requests: {} };
    try { this.data = JSON.parse(fs.readFileSync(statePath, 'utf8')); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    for (const request of Object.values(this.data.requests)) {
      if (request.inputId && ['submitted', 'unknown'].includes(request.state)) this.pending.set(request.inputId, request);
      if (request.turn !== undefined) ownedTurns.add(turnKey(request.session, request.turn));
    }
    this.disposers = [
      ctx.on('agent/inbox/claimed', ({ agent, message, turn }) => {
        const request = this.pending.get(message.id);
        if (!request || request.session !== agent.session.id) return;
        request.turn = turn;
        ownedTurns.add(turnKey(request.session, turn));
        this.save();
      }),
      ctx.on('session/event', (session, event) => this.onEvent(session, event)),
    ];
  }
  save() {
    fs.mkdirSync(path.dirname(this.statePath), { recursive: true, mode: 0o700 });
    const temp = `${this.statePath}.tmp`;
    fs.writeFileSync(temp, JSON.stringify(this.data), { mode: 0o600 });
    fs.renameSync(temp, this.statePath);
  }
  dispose() { for (const stop of this.disposers) stop?.(); }
  reply(input, text, key = 'reply') {
    this.output({ type: 'reply', message_id: input.message_id, request_id: input.message_id, key, text });
  }
  async receive(input) {
    if (input.type === 'delivery') {
      const request = this.data.requests[input.request_id];
      if (request && input.key === 'result') {
        request.delivery = input.status;
        this.save();
      }
      return;
    }
    if (!input.message_id || !input.chat_id) return;
    const old = this.data.requests[input.message_id];
    if (old) {
      this.reply(input, `这条消息已登记，状态：${old.state}。不会重复执行。`, 'duplicate');
      return;
    }
    const live = () => this.ctx.agents.list().filter(a => !a.session.header?.parentSession && !(a.session.header?.delegationDepth > 0));
    if (input.kind === 'attach_list') {
      const rows = live();
      this.cache.set(input.chat_id, rows.map(a => a.session.id));
      this.reply(input, rows.length ? rows.map((a, i) => `${i + 1}. ${a.session.title || a.session.id}\n${a.session.header?.cwd || ''} · ${a.status}`).join('\n') + '\n/attach 序号 绑定原会话' : '当前宿主没有已加载的主会话。先在 dsh 打开一个会话。');
      return;
    }
    if (input.kind === 'attach') {
      const argument = String(input.payload || '').trim().replace(/^[<`]+|[>`]+$/g, '');
      const id = /^\d+$/.test(argument) ? this.cache.get(input.chat_id)?.[Number(argument) - 1] : argument;
      if (!live().some(a => a.session.id === id)) { this.reply(input, '没有找到已加载的主会话。先发 /sessions，再用 /attach 序号。'); return; }
      this.data.bindings[input.chat_id] = id;
      this.save();
      this.reply(input, `已绑定原会话 ${id}。接下来的消息会写回这个会话。`);
      return;
    }
    if (input.kind === 'status') {
      const id = this.data.bindings[input.chat_id];
      const agent = id && this.ctx.agents.get(id);
      this.reply(input, `会话：${id || '未绑定'}\n状态：${agent?.status || '未在当前宿主加载'}\n权限与模型沿用原会话；需要审批时请在 dsh 处理。`);
      return;
    }
    if (input.kind !== 'task') {
      this.reply(input, '原生续聊：/sessions 列出已加载会话，/attach 序号 绑定，/status 查看状态，然后直接发消息。当前版本不支持新建、话题及 handoff。');
      return;
    }
    const [kind, prompt] = input.payload || [];
    if (kind !== 'dsh') { this.reply(input, '此入口只支持 dsh。'); return; }
    const sessionId = this.data.bindings[input.chat_id];
    const agent = sessionId && this.ctx.agents.get(sessionId);
    if (!agent) { this.reply(input, '原会话尚未在当前 dsh 宿主加载，请先打开会话并发 /sessions。'); return; }
    if ([...this.pending.values()].some(r => r.session === sessionId)) {
      this.reply(input, '该会话还有一条手机消息未完成，请等结果返回。'); return;
    }
    const message = this.createUserMessage({ content: [{ type: 'text', text: prompt }], source: { kind: 'user' } });
    const request = { message_id: input.message_id, session: sessionId, inputId: message.id, state: 'submitted', created_at: Date.now() };
    this.data.requests[input.message_id] = request;
    // Save the identity BEFORE handing input to dsh. A crash here is reported as
    // uncertain, never silently replayed (there is no cross-store transaction).
    this.pending.set(message.id, request);
    this.save();
    try {
      agent.followup(message);
      await this.ctx.sessions.flush(agent.session);
      this.reply(input, agent.status === 'running' ? '已交给原会话，正在执行或等待当前轮结束。需要审批时请在 dsh 处理。' : '已交给原会话。', 'accepted');
    } catch (error) {
      if (request.state !== 'finished') request.state = 'unknown';
      this.save();
      this.reply(input, '提交结果不明确，请检查 dsh 原会话；为避免重复执行，本消息不会自动重试。', 'uncertain');
      this.ctx.logger?.warn(`notifyhub native submission uncertain: ${error.message}`);
    }
  }
  onEvent(session, event) {
    if (event.type !== 'turn/end') return;
    const request = [...this.pending.values()].find(r => r.session === session.id && r.turn === event.data?.turn);
    if (!request) return;
    // Read only this turn, never fall back to an earlier assistant answer.
    let text = '';
    let foundStart = false;
    for (let seq = session.seq - 1; seq >= 0; seq--) {
      const item = session.eventAt(seq);
      if (item?.type === 'turn/start' && item.data.turn === request.turn) { foundStart = true; break; }
      if (!text && item?.type === 'assistant/message') text = (item.data.message?.content || []).filter(p => p.type === 'text').map(p => p.text).join('');
    }
    if (!foundStart) {
      request.state = 'unknown';
      this.save();
      this.reply(request, '无法定位本轮的起始记录，请查看 dsh 原会话。', 'uncertain');
      return;
    }
    const reason = event.data.reason || {};
    request.state = 'finished';
    request.result = text || reason.error?.message || `本轮结束：${reason.kind || 'unknown'}（没有文本回答）`;
    this.pending.delete(request.inputId);
    this.save();
    this.reply(request, `${reason.kind === 'completed' ? '完成' : '运行结束：' + (reason.kind || 'unknown')}\n${request.result}`, 'result');
    while (ownedTurns.size > 5000) ownedTurns.delete(ownedTurns.values().next().value);
  }
}
