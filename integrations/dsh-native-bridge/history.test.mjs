import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { NativeController } from './lib/controller.js';

const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'native-history-'));
const records = Array.from({ length: 12 }, (_, index) => ({ header: { id: `main-${index}`, cwd: `/original/project-${index}`, createdAt: 100 - index }, live: index === 0 }));
records.push({ header: { id: 'subagent', parentSession: 'main-0', createdAt: 10000 } });
const loaded = new Map();
const makeAgent = header => ({ session: { id: header.id, header, title: `标题 ${header.id}` }, status: 'idle', followup() { throw new Error('Selection must not run a model'); } });
loaded.set('main-0', makeAgent(records[0].header));
const frames = [];
const resumed = [];
let failResume = false;
const ctx = {
  agents: { list: () => [...loaded.values()], get: id => loaded.get(id) },
  sessions: { flush: async () => {} },
  sessionQuery: {
    listSessions: async () => records,
    inspectSession: async (id, read) => {
      const record = records.find(r => r.header.id === id);
      const events = [{ type: 'session/title', data: { title: `标题 ${id}` }, time: Number(id.split('-')[1]) * 1000 }, { type: 'model/selection', data: { model: 'original-model' }, time: Number(id.split('-')[1]) * 1000 }];
      return read({ header: record.header, seq: events.length, eventAt: seq => events[seq] });
    },
  },
  sessionController: { resolveAgent: async id => {
    resumed.push(id);
    if (failResume) return { error: new Error('writer-held') };
    const record = records.find(r => r.header.id === id && !r.header.parentSession);
    if (!record) return { error: new Error('not-found') };
    const agent = makeAgent(record.header);
    loaded.set(id, agent);
    return { agent };
  } },
  on: () => () => {}, logger: { warn() {} },
};
const input = (message_id, kind, payload) => ({ message_id, chat_id: 'chat', kind, payload });
try {
  const controller = new NativeController(ctx, { statePath: path.join(directory, 'ledger.json'), createUserMessage() { throw new Error('No task should run'); }, output: frame => frames.push(frame) });
  await controller.receive(input('list', 'attach_list'));
  const view = frames.at(-1).view;
  assert.equal(view.total, 12); // subagent excluded
  assert.equal(view.pages, 2);
  assert.equal(view.sessions[0].id, 'main-11'); // latest activity, not creation time
  assert.equal(view.sessions[0].loaded, false);
  assert.equal(view.sessions[0].model, 'original-model');
  assert.equal(view.sessions[0].cwd, '/original/project-11');
  await controller.receive(input('select', 'task', ['dsh', '1']));
  assert.deepEqual(resumed, ['main-11']);
  assert.equal(controller.data.bindings.chat, 'main-11');
  assert.equal(loaded.get('main-11').session.header.cwd, '/original/project-11');
  await controller.receive(input('list2', 'attach_list', '1'));
  assert.equal(frames.at(-1).view.sessions.length, 2);
  assert.equal(frames.at(-1).view.page, 1);
  assert.equal(frames.at(-1).view.sessions[0].id, 'main-1');
  failResume = true;
  await controller.receive(input('failed-select', 'task', ['dsh', '1']));
  assert.equal(controller.data.bindings.chat, 'main-11');
  assert(frames.at(-1).text.includes('恢复会话失败'));
  await controller.receive(input('reuse', 'attach', 'main-11'));
  assert.equal(resumed.filter(id => id === 'main-11').length, 1); // reuse live agent
  controller.dispose();
  console.log('native historical sessions: ok');
} finally { fs.rmSync(directory, { recursive: true, force: true }); }
