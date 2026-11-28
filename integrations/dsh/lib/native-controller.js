import fs from 'node:fs';
import path from 'node:path';
import { questionAnswers, questionRef } from './questions.js';

// Shared with the ordinary notifier, without importing either plugin into the other.
export const ownedTurns = globalThis[Symbol.for('notifyhub.nativeBridge.turns')] ??= new Set();
export const turnKey = (session, turn) => `${session}:${turn}`;

function textOf(event) {
  return (event.data.message?.content || []).filter(part => part.type === 'text').map(part => part.text).join('');
}

function eventTime(event) {
  const time = typeof event?.time === 'number' ? event.time : Date.parse(event?.time);
  return Number.isFinite(time) ? time : 0;
}

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
  metadata(agent) {
    const session = agent.session;
    const selection = agent.model?.current?.();
    return { id: session.id, title: session.title || '未命名会话',
      project: path.basename((session.header?.cwd || '').replace(/\\/g, '/')) || '未指定项目',
      cwd: session.header?.cwd || '', status: agent.status,
      model: typeof selection === 'string' ? selection : selection?.model || '' };
  }
  async inspectSession(id, read) {
    const session = this.ctx.agents.get(id)?.session;
    if (typeof session?.eventAt === 'function') return read(session);
    const query = this.ctx.sessionQuery;
    if (typeof query?.readSession === 'function') {
      const log = await query.readSession(id);
      return read({ id, header: log.session, seq: log.events.length, eventAt: seq => log.events[seq] });
    }
    return query?.inspectSession?.(id, read);
  }
  async catalog() {
    const live = this.ctx.agents.list().filter(a => !a.session.header?.parentSession && !(a.session.header?.delegationDepth > 0));
    const query = this.ctx.sessionQuery;
    if (!query) return live.map(a => ({ ...this.metadata(a), loaded: true }));
    const records = (await query.listSessions()).filter(r => !r.header.parentSession && !(r.header.delegationDepth > 0));
    const rows = await Promise.all(records.map(async record => {
      const header = record.header;
      const agent = this.ctx.agents.get(header.id);
      try {
        const meta = await this.inspectSession(header.id, session => {
          let title = agent?.session.title || '';
          let selection;
          let updatedAt = Number(header.createdAt) || 0;
          for (let seq = session.seq - 1; seq >= 0; seq--) {
            const event = session.eventAt(seq);
            if (!event) continue;
            if (seq === session.seq - 1) {
              const timestamp = typeof event.time === 'number' ? event.time : Date.parse(event.time);
              if (Number.isFinite(timestamp)) updatedAt = timestamp;
            }
            if (!title && event.type === 'session/title') title = event.data?.title || '';
            if (!selection && event.type === 'model/selection') selection = event.data;
          }
          const meta = this.metadata(agent || { session: { id: header.id, title, header }, status: 'historical', model: { current: () => selection } });
          return { ...meta, loaded: !!agent, updatedAt };
        });
        if (meta) return meta;
      } catch (error) {
        this.ctx.logger?.warn(`native session metadata unavailable (${header.id}): ${error.message}`);
      }
      return { ...this.metadata(agent || { session: { id: header.id, header }, status: 'unavailable' }), loaded: !!agent, updatedAt: Number(header.createdAt) || 0 };
    }));
    return rows.sort((a, b) => b.updatedAt - a.updatedAt || a.id.localeCompare(b.id));
  }
  async resolveAgent(id) {
    if (!id) return undefined;
    const live = this.ctx.agents.get(id);
    if (live) return live.session.header?.parentSession || live.session.header?.delegationDepth > 0 ? undefined : live;
    if (!this.ctx.sessionController) return undefined;
    const found = await this.ctx.sessionController.resolveAgent(id);
    if (found.error) throw found.error;
    return found.agent;
  }
  reply(input, text, key = 'reply', view = undefined) {
    this.output({ type: 'reply', message_id: input.message_id, request_id: input.message_id, key, text, view });
  }
  async pendingQuestions(id) {
    let observed;
    try {
      observed = await this.ctx.sessionQuery?.observeSession?.(id);
      return observed?.projections?.values?.userQuestions?.active || [];
    } catch (error) {
      this.ctx.logger?.warn(`native question status unavailable: ${error.message}`);
      return [];
    } finally {
      observed?.[Symbol.dispose]?.();
    }
  }
  async sessionView(input, id) {
    const agent = this.ctx.agents.get(id);
    const session = agent?.session;
    const query = input.kind;
    const snapshot = await this.inspectSession(id, stored => {
      let title = session?.title || '';
      let ended;
      let answer;
      let selection;
      let activity;
      for (let seq = stored.seq - 1; seq >= 0; seq--) {
        const event = stored.eventAt(seq);
        if (!event) continue;
        if (!title && event.type === 'session/title') title = event.data.title;
        if (!selection && event.type === 'model/selection') selection = event.data;
        if (event.type === 'turn/end' || event.type === 'turn/start') activity ??= event;
        if (event.type === 'turn/end') ended = event;
        if (event.type === 'turn/start') ended = undefined;
        if (!answer && ended && event.type === 'assistant/message') {
          const text = textOf(event);
          if (text.trim()) answer = { text, at: eventTime(ended) || eventTime(event) };
        }
        if (title && selection && activity && (query !== 'latest_answer' || answer)) break;
      }
      const recorded = { completed: 'completed', aborted: 'interrupted', error: 'failed' }[activity?.data?.reason?.kind];
      return { answer, activity, meta: this.metadata(agent || {
        session: { id, header: stored.header, title },
        model: { current: () => selection }, status: activity?.type === 'turn/start' ? 'unfinished' : recorded || 'historical',
      }) };
    });
    if (!snapshot && !agent) return this.reply(input, '暂未读取到原会话，请在桌面打开它。');
    const meta = snapshot?.meta || this.metadata(agent);
    if (agent && input.chat_type === 'p2p' && query !== 'latest_answer') {
      const pending = await this.pendingQuestions(id);
      const ordinary = pending.filter(item => item.questions?.length && item.questions.every(question => !question.intent));
      const active = ordinary.find(item => item.state === (query === 'status' ? 'open' : 'continued')) || ordinary[0];
      if (active) {
        const visible = active.questions.map(({ question, multiSelect, options = [] }) => ({
          question, multi_select: multiSelect, options: options.map(({ label, description }) => ({ label, description })),
        }));
        if (active.state === 'open') {
          meta.status = 'waiting_input';
          return this.reply(input, '原会话正在等待回复。', 'reply', {
            type: 'pending_input', session: meta, detail: '桌面正在等待这组问题。可在原会话回答，或稍后刷新查看可在飞书继续回答的问题。',
            questions: visible,
          });
        }
        const questions = this.ctx.get?.('userQuestions');
        if (active.state === 'continued' && typeof questions?.answer === 'function') {
          const ref = questionRef(id, active.callId);
          const samples = visible.map(({ options, multi_select }) =>
            options.length ? (multi_select && options.length > 1 ? '1,2' : '1') : '你的回答');
          const format = samples.length === 1 ? samples[0] : samples.map((sample, index) => `${index + 1}=${sample}`).join('\n');
          if (meta.status === 'idle') meta.status = 'waiting_input';
          return this.reply(input, '原会话有待回复的问题。', 'reply', {
            type: 'pending_input', session: meta, question_ref: ref,
            detail: `可用选项序号或文字回答。例如发送\n回答 ${ref} ${format}`,
            questions: visible,
          });
        }
      }
    }
    let detail = '';
    if (query === 'status') {
      const queued = (agent?.inbox?.nextTurn?.length || 0) + (agent?.inbox?.nextStep?.length || 0);
      if (queued) {
        if (meta.status === 'idle') meta.status = 'queued';
        detail = `原会话有 ${queued} 条消息在队列中。`;
      } else if (!agent && snapshot?.activity) {
        detail = '原会话当前未加载，显示最近的记录。';
      } else if (meta.status === 'idle' && [...this.pending.values()].some(request => request.session === id)) {
        meta.status = 'submitted';
        detail = '有一条手机消息的处理结果待确认。';
      }
    }
    if (query === 'pending_input') detail = '当前未读取到可在飞书回答的结构化问题。普通问题可用「回复 原文」。';
    const view = { type: query, session: meta, detail };
    if (query === 'status') view.activity_at = eventTime(snapshot?.activity) / 1000 || undefined;
    if (query === 'latest_answer' && snapshot?.answer) {
      view.text = snapshot.answer.text;
      view.activity_at = snapshot.answer.at / 1000 || undefined;
    }
    this.reply(input, view.text || detail || '当前 DeepSeek Harness 会话。', 'reply', view);
  }
  async answerQuestion(input, id) {
    const agent = this.ctx.agents.get(id);
    const questions = this.ctx.get?.('userQuestions');
    if (input.chat_type !== 'p2p' || !agent || typeof questions?.answer !== 'function') {
      return this.reply(input, '这个待回复项目请在原桌面会话处理。');
    }
    const action = input.payload;
    const text = typeof action === 'string' ? action.match(/^(\S+)\s+([\s\S]+)$/) : null;
    const ref = typeof action === 'string' ? text?.[1] : action?.ref;
    if (!ref || (typeof action !== 'string' && action?.session !== id)) {
      return this.reply(input, '发送「待回复」查看当前问题和回答格式。');
    }
    const pending = (await this.pendingQuestions(id)).find(item => item.state === 'continued'
      && questionRef(id, item.callId) === ref && item.questions?.length && item.questions.every(question => !question.intent));
    if (!pending) return this.reply(input, '这个问题已失效或会话已切换。请发「待回复」刷新。');
    const option = typeof action !== 'string' && pending.questions.length === 1 && !pending.questions[0].multiSelect
      && pending.questions[0].options?.find(item => item.label === action.label);
    const answer = text ? questionAnswers(pending.questions, text[2])
      : option ? { answers: [{ id: pending.questions[0].id, selected: [option.label] }] } : null;
    if (!answer) return this.reply(input, '回答格式或序号有误。发「待回复」可查看当前问题。');
    const request = { message_id: input.message_id, session: id, state: 'answer_submitting', created_at: Date.now() };
    this.data.requests[input.message_id] = request;
    this.save();
    try {
      const accepted = await questions.answer(agent, pending.callId, answer);
      request.state = accepted ? 'answer_queued' : 'answer_rejected';
      this.save();
      this.reply(input, accepted ? '已将答复交给原会话。' : '问题已被处理。发「待回复」刷新。', 'answer');
    } catch (error) {
      request.state = error.code === 'REPLY_QUEUED' ? 'answer_queued' : 'answer_unknown';
      this.save();
      this.reply(input, request.state === 'answer_queued' ? '这道题已有答复在等待原会话接收。'
        : '答复状态待确认，请在桌面查看。', 'answer');
    }
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
    if (input.kind === 'task') {
      const prompt = String(input.payload?.[1] || '').trim();
      if (['会话', '切换会话', '会话列表'].includes(prompt)) input = { ...input, kind: 'attach_list' };
      else if (prompt === '状态') input = { ...input, kind: 'status' };
      else if (['下一页', '上一页'].includes(prompt)) input = { ...input, kind: 'attach_list', payload: String(Math.max(0, (this.cache.get(input.chat_id)?.page || 0) + (prompt === '下一页' ? 1 : -1))) };
      else if (/^\d+$/.test(prompt)) {
        const choice = this.cache.get(input.chat_id);
        if (choice && Date.now() >= choice.expiresAt) {
          this.cache.delete(input.chat_id);
          this.reply(input, '会话列表已过期，请发「会话」获取新列表。');
          return;
        }
        if (choice) input = { ...input, kind: 'attach', payload: prompt };
      }
    }
    const old = this.data.requests[input.message_id];
    if (old) {
      this.reply(input, `这条消息已登记，状态：${old.state}。不会重复执行。`, 'duplicate');
      return;
    }
    if (input.kind === 'attach_list') {
      let all;
      try { all = await this.catalog(); }
      catch (error) {
        this.ctx.logger?.warn(`native session listing failed: ${error.message}`);
        this.reply(input, '暂时无法读取历史会话，请稍后刷新。当前绑定保持不变。');
        return;
      }
      const pages = Math.max(1, Math.ceil(all.length / 10));
      const page = Math.min(pages - 1, Math.max(0, Number.parseInt(input.payload, 10) || 0));
      const rows = all.slice(page * 10, page * 10 + 10).map(row => ({ ...row, current: row.id === this.data.bindings[input.chat_id] }));
      this.cache.set(input.chat_id, { ids: rows.map(row => row.id), page, expiresAt: Date.now() + 5 * 60_000 });
      this.reply(input, rows.map((row, i) => `${i + 1}. ${row.title}`).join('\n') || '暂无主会话。', 'reply', { type: 'sessions', sessions: rows, page, pages, total: all.length });
      return;
    }
    if (input.kind === 'attach') {
      const argument = String(input.payload || '').trim().replace(/^[<`]+|[>`]+$/g, '');
      const id = /^\d+$/.test(argument) ? this.cache.get(input.chat_id)?.ids[Number(argument) - 1] : argument;
      let agent;
      try { agent = await this.resolveAgent(id); }
      catch (error) {
        this.ctx.logger?.warn(`native session restore failed (${id}): ${error.message}`);
        this.reply(input, '恢复会话失败，当前绑定保持不变。请在桌面检查原会话的目录、权限或占用状态。');
        return;
      }
      if (!agent) { this.reply(input, '没有找到可继续的主会话，请刷新会话列表。'); return; }
      this.cache.delete(input.chat_id);
      this.data.bindings[input.chat_id] = id;
      this.save();
      this.reply(input, `已切换到「${agent.session.title || '未命名会话'}」。直接发消息即可继续。`, 'reply', { type: 'bound', session: this.metadata(agent) });
      return;
    }
    const sessionId = this.data.bindings[input.chat_id];
    if (['status', 'latest_answer', 'pending_input', 'answer_input'].includes(input.kind)) {
      if (!sessionId) return this.reply(input, '请先发「DSH会话」并选择原会话。');
      try {
        if (input.kind === 'answer_input') await this.answerQuestion(input, sessionId);
        else await this.sessionView(input, sessionId);
      } catch (error) {
        this.ctx.logger?.warn(`native session read failed: ${error.message}`);
        this.reply(input, '暂时无法读取原会话。请在桌面打开它后重试。');
      }
      return;
    }
    if (!['task', 'reply_text'].includes(input.kind)) {
      this.reply(input, '发「会话」选择对话，回复序号切换，然后直接发消息继续聊。发「状态」查看当前会话。');
      return;
    }
    const [kind, prompt] = input.payload || [];
    if (kind !== 'dsh') { this.reply(input, '此入口只支持 dsh。'); return; }
    let agent;
    try { agent = await this.resolveAgent(sessionId); }
    catch (error) {
      this.ctx.logger?.warn(`native bound session restore failed: ${error.message}`);
      this.reply(input, '当前会话暂时无法恢复，本条消息未执行。请在桌面检查原会话或切换其他会话。');
      return;
    }
    if (!agent) { this.reply(input, '还没有可用的当前会话。请先在桌面打开对话，再发「会话」选择；选择后请重新发送本条消息。'); return; }
    if ([...this.pending.values()].some(r => r.session === sessionId)) {
      this.reply(input, '该会话还有一条手机消息未完成，请等结果返回。'); return;
    }
    const message = this.createUserMessage({ content: [{ type: 'text', text: prompt }], source: { kind: 'user' } });
    const request = { message_id: input.message_id, session: sessionId, inputId: message.id, state: 'submitted', created_at: Date.now(), metadata: this.metadata(agent) };
    this.data.requests[input.message_id] = request;
    // Save the identity BEFORE handing input to dsh. A crash here is reported as
    // uncertain, never silently replayed (there is no cross-store transaction).
    this.pending.set(message.id, request);
    this.save();
    try {
      agent.followup(message);
      await this.ctx.sessions.flush(agent.session);
      this.reply(input, agent.status === 'running' ? '已交给原会话，正在执行或等待当前轮结束。需要审批时请在 dsh 处理。' : '已交给原会话。', 'accepted', { type: 'accepted', session: request.metadata });
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
      if (!text && item?.type === 'assistant/message') text = textOf(item);
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
    this.reply(request, `${reason.kind === 'completed' ? '完成' : '运行结束：' + (reason.kind || 'unknown')}\n${request.result}`, 'result', { type: 'result', session: { ...(request.metadata || this.metadata({ session, status: 'idle' })), status: reason.kind === 'completed' ? 'completed' : 'failed' }, text: request.result, ok: reason.kind === 'completed', duration: Math.max(0, Math.round((Date.now() - request.created_at) / 1000)), turn: request.turn });
    while (ownedTurns.size > 5000) ownedTurns.delete(ownedTurns.values().next().value);
  }
}
