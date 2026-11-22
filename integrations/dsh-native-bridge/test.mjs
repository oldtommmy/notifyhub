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
  controller.dispose();
  console.log('native bridge controller: ok');
} finally { fs.rmSync(root, { recursive: true, force: true }); }
