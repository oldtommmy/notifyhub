import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { NativeController, ownedTurns, turnKey } from './lib/controller.js';

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'native-bridge-'));
let id = 0;
const handlers = new Map();
const outputs = [];
const events = [];
const session = { id: 'original', title: '已有会话', header: { cwd: '/original/project' }, get seq() { return events.length; }, eventAt: i => events[i] };
const inputs = [];
const agent = { session, status: 'running', followup: input => inputs.push(input) };
const ctx = { agents: { get: key => key === session.id ? agent : undefined, list: () => [agent] }, sessions: { flush: async () => {} }, on: (name, fn) => { handlers.set(name, fn); return () => handlers.delete(name); }, logger: { warn() {} } };
const options = { statePath: path.join(root, 'state.json'), createUserMessage: value => ({ ...value, id: `input-${++id}` }), output: value => outputs.push(value) };
const input = (message_id, kind, payload) => ({ message_id, chat_id: 'chat', kind, payload });
try {
  let controller = new NativeController(ctx, options);
  await controller.receive(input('list', 'attach_list'));
  await controller.receive(input('attach', 'attach', '1'));
  assert.equal(controller.data.bindings.chat, 'original');
  await controller.receive(input('phone-1', 'task', ['dsh', '这次手机的问题']));
  assert.equal(inputs.length, 1);
  assert.equal(inputs[0].content[0].text, '这次手机的问题');
  assert.equal(agent.session, session); // no subprocess/new session/cwd substitution
  // The desktop's previous turn must not be returned as the phone's answer.
  controller.onEvent(session, { type: 'turn/end', data: { turn: 1, reason: { kind: 'completed' } } });
  assert(!outputs.some(value => value.key === 'result'));
  await controller.receive(input('phone-1', 'task', ['dsh', '重复投递']));
  await controller.receive(input('phone-2', 'task', ['dsh', '并发消息']));
  assert.equal(inputs.length, 1);
  handlers.get('agent/inbox/claimed')({ agent, message: inputs[0], turn: 2 });
  assert(ownedTurns.has(turnKey('original', 2)));
  events.push({ type: 'turn/start', data: { turn: 1 } }, { type: 'assistant/message', data: { message: { content: [{ type: 'text', text: '旧回答' }] } } }, { type: 'turn/start', data: { turn: 2 } }, { type: 'assistant/message', data: { message: { content: [{ type: 'reasoning', text: '隐藏推理' }, { type: 'text', text: '新的回答' }] } } });
  controller.onEvent(session, { type: 'turn/end', data: { turn: 2, reason: { kind: 'completed' } } });
  const result = outputs.find(value => value.key === 'result');
  assert.equal(result.request_id, 'phone-1');
  assert.equal(result.text, '完成\n新的回答');
  assert.equal(result.message_id, 'phone-1');
  controller.dispose();
  controller = new NativeController(ctx, options);
  await controller.receive(input('phone-1', 'task', ['dsh', '重启后重投']));
  assert.equal(inputs.length, 1);
  await controller.receive({ type: 'delivery', request_id: 'phone-1', key: 'result', status: 'unknown' });
  assert.equal(controller.data.requests['phone-1'].delivery, 'unknown');
  // A turn with no text must not reuse an old answer.
  await controller.receive(input('phone-3', 'task', ['dsh', 'next']));
  handlers.get('agent/inbox/claimed')({ agent, message: inputs[1], turn: 3 });
  events.push({ type: 'turn/start', data: { turn: 3 } });
  controller.onEvent(session, { type: 'turn/end', data: { turn: 3, reason: { kind: 'error', error: { message: '模型失败' } } } });
  assert(outputs.at(-1).text.includes('模型失败'));
  assert(!outputs.at(-1).text.includes('新的回答'));
  // Natural-language session picker and numeric choice need no slash commands.
  await controller.receive(input('ui-list', 'task', ['dsh', '会话']));
  assert.equal(outputs.at(-1).view.type, 'sessions');
  assert.equal(outputs.at(-1).view.sessions[0].title, '已有会话');
  await controller.receive(input('ui-pick', 'task', ['dsh', '1']));
  assert.equal(outputs.at(-1).view.type, 'bound');
  assert.equal(inputs.length, 2); // a picker choice must never become a model prompt
  assert(!controller.cache.has('chat')); // consume selection mode after choosing
  await controller.receive(input('expired-list', 'task', ['dsh', '会话']));
  controller.cache.get('chat').expiresAt = 0;
  await controller.receive(input('expired-choice', 'task', ['dsh', '1']));
  assert(outputs.at(-1).text.includes('已过期'));
  assert.equal(inputs.length, 2);
  // Crash between ledger write and followup must never auto-execute on restart.
  agent.followup = () => { throw new Error('crash boundary'); };
  await controller.receive(input('phone-4', 'task', ['dsh', 'uncertain']));
  controller.dispose();
  controller = new NativeController(ctx, options);
  await controller.receive(input('phone-4', 'task', ['dsh', 'redelivery']));
  assert.equal(inputs.length, 2);

  // Read the last completed answer even when the next desktop turn is still running.
  events.push({ type: 'turn/end', data: { turn: 3 }, time: 11000 },
    { type: 'turn/start', data: { turn: 4 } },
    { type: 'assistant/message', data: { message: { content: [{ type: 'text', text: '最新已保存的回答' }] } } },
    { type: 'turn/end', data: { turn: 4 }, time: 12000 },
    { type: 'turn/start', data: { turn: 5 } },
    { type: 'assistant/message', data: { message: { content: [{ type: 'text', text: '未结束的运行' }] } } });
  await controller.receive(input('latest', 'latest_answer'));
  assert.equal(outputs.at(-1).view.text, '最新已保存的回答');
  assert.equal(outputs.at(-1).view.activity_at, 12);
  agent.inbox = { nextTurn: [{ id: 'desktop-prompt' }], nextStep: [] };
  await controller.receive(input('running-with-queue', 'status'));
  assert.equal(outputs.at(-1).view.session.status, 'running');
  assert.match(outputs.at(-1).view.detail, /1 条消息/);

  // The pending question is bound to this exact live session and tool call.
  const question = { id: 'pick', question: '选择一种运行方式', options: [{ label: '甲' }, { label: '乙' }] };
  let active = [{ callId: 'ask-1', state: 'open', questions: [question] }];
  let closedObservations = 0;
  ctx.sessionQuery = {
    observeSession: async requested => {
      assert.equal(requested, session.id);
      return { projections: { values: { userQuestions: { active } } },
        [Symbol.dispose]() { closedObservations++; } };
    },
  };
  const answers = [];
  const userQuestions = { answer: (owner, callId, answer) => {
    assert.equal(owner, agent);
    answers.push({ callId, answer });
    return true;
  } };
  ctx.get = name => name === 'userQuestions' ? userQuestions : undefined;
  const phone = (messageId, kind, payload) => ({ ...input(messageId, kind, payload), chat_type: 'p2p' });
  await controller.receive(phone('status', 'status'));
  assert.equal(outputs.at(-1).view.session.status, 'waiting_input');
  assert.equal(outputs.at(-1).view.question_ref, undefined);
  active[0].state = 'continued';
  await controller.receive(phone('pending', 'pending_input'));
  const ref = outputs.at(-1).view.question_ref;
  assert.match(ref, /^[a-f0-9]{8}$/);
  await controller.receive(phone('wrong-session', 'answer_input', { session: 'other', ref, label: '乙' }));
  assert.equal(answers.length, 0);
  const choose = phone('choose', 'answer_input', { session: session.id, ref, label: '乙' });
  await controller.receive(choose);
  await controller.receive(choose);
  assert.deepEqual(answers, [{ callId: 'ask-1', answer: { answers: [{ id: 'pick', selected: ['乙'] }] } }]);
  active = [{ callId: 'another', state: 'continued', questions: [question, {
    id: 'note', question: '补充说明',
  }] }];
  await controller.receive(phone('pending-batch', 'pending_input'));
  await controller.receive(phone('choose-text', 'answer_input', `${outputs.at(-1).view.question_ref} 1=1\n2=自定义回答`));
  assert.deepEqual(answers.at(-1), { callId: 'another', answer: { answers: [
    { id: 'pick', selected: ['甲'] }, { id: 'note', selected: [], custom: '自定义回答' },
  ] } });
  active[0].questions[0] = { ...question, intent: { kind: 'approval' } };
  await controller.receive(phone('desktop-approval', 'pending_input'));
  assert.equal(outputs.at(-1).view.question_ref, undefined);
  assert(closedObservations > 0);
  assert.equal(inputs.length, 2);

  controller.dispose();
  console.log('native bridge controller: ok');
} finally { fs.rmSync(root, { recursive: true, force: true }); }
