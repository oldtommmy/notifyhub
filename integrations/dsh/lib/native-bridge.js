import { randomUUID } from 'node:crypto';
import { spawn } from 'node:child_process';
import readline from 'node:readline';
import os from 'node:os';
import path from 'node:path';
import { NativeController } from './native-controller.js';

export const name = 'notifyhub-native-bridge';
export const inject = ['agents', 'sessions', 'sessionQuery', 'sessionController'];

export async function apply(ctx, config = {}) {
  if (config.enabled !== true) return;
  if (!Array.isArray(config.command) || !config.command.length) throw new Error('native bridge requires a transport command array');
  // UserMessage is a structural wire value. Match the host's identified,
  // immutable user-message shape without loading a second dsh SDK instance.
  const createUserMessage = ({ content, source }) => Object.freeze({
    id: randomUUID(), role: 'user', source: Object.freeze(source),
    content: Object.freeze(content.map(part => Object.freeze(part))),
  });
  const child = spawn(config.command[0], config.command.slice(1), { stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
  let controller;
  let chain = Promise.resolve();
  const send = value => {
    if (!child.stdin.destroyed) child.stdin.write(JSON.stringify(value) + '\n');
  };
  child.stdin.on('error', error => ctx.logger.warn(`native bridge pipe: ${error.message}`));
  child.stderr.on('data', chunk => ctx.logger.info(String(chunk).trim()));
  child.on('error', error => ctx.logger.warn(`native bridge transport: ${error.message}`));
  child.on('exit', code => {
    controller?.dispose();
    ctx.logger.warn(`native bridge transport stopped (${code}); reload plugin to reconnect`);
  });
  const reader = readline.createInterface({ input: child.stdout });
  reader.on('line', line => {
    if (!line.startsWith('NHBRIDGE:')) return;
    chain = chain.then(async () => {
      const input = JSON.parse(line.slice('NHBRIDGE:'.length));
      if (input.type === 'ready') {
        if (controller) throw new Error('transport sent ready twice');
        controller = new NativeController(ctx, {
          statePath: config.state_path || path.join(os.homedir(), '.local/state/notifyhub/native-bridge.json'),
          createUserMessage, output: send,
        });
        send({ type: 'ready' });
      } else if (controller) await controller.receive(input);
    }).catch(error => ctx.logger.warn(`native bridge: ${error.message}`));
  });
  ctx.on('dispose', () => {
    reader.close();
    controller?.dispose();
    child.stdin.end();
    child.kill();
  });
}
