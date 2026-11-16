import assert from 'node:assert/strict';
import { test } from 'node:test';
import ts from 'typescript';
import { registerHooks } from 'node:module';

registerHooks({
  load(url, context, nextLoad) {
    const result = nextLoad(url, url.endsWith('.ts') ? { ...context, format: 'module' } : context);
    if (!url.endsWith('.ts') || url.includes('/node_modules/')) return result;
    const source = ts.transpileModule(result.source.toString(), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    return { ...result, source, format: 'commonjs' };
  },
});

const { gateTools } = await import('../lib/mcp/role-gate.ts');

function fakeServer() {
  const tools = {};
  return { tools, registerTool: (name, _config, cb) => (tools[name] = cb) };
}

const allowSub = (sub) => async (authInfo) => authInfo?.extra?.sub === sub;

test('a caller without the required local role never reaches the tool', async () => {
  const server = fakeServer();
  let ran = false;
  gateTools(server, allowSub('viewer'), 'needs viewer');
  server.registerTool('list_tasks', {}, async () => ((ran = true), 'rows'));

  const result = await server.tools.list_tasks({ limit: 5 }, { authInfo: { extra: { sub: 'no-role' } } });
  assert.equal(ran, false);
  assert.equal(result.isError, true);
  assert.match(result.content[0].text, /needs viewer/);
});

test('an allowed caller reaches the tool with its original arguments, with or without an input schema', async () => {
  const server = fakeServer();
  gateTools(server, allowSub('viewer'), 'needs viewer');
  server.registerTool('get_task', {}, async (args, extra) => [args, extra.authInfo.extra.sub]);
  server.registerTool('list_devices', {}, async (extra) => extra.authInfo.extra.sub);

  const extra = { authInfo: { extra: { sub: 'viewer' } } };
  assert.deepEqual(await server.tools.get_task({ task_name: 't' }, extra), [{ task_name: 't' }, 'viewer']);
  assert.equal(await server.tools.list_devices(extra), 'viewer');
});
