import assert from 'node:assert/strict';
import { test } from 'node:test';
import ts from 'typescript';
import { fileURLToPath } from 'node:url';
import { registerHooks } from 'node:module';
import { readFile, mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

registerHooks({
  load(url, context, nextLoad) {
    const result = nextLoad(url, url.endsWith('.ts') ? { ...context, format: 'module' } : context);
    if (!url.endsWith('.ts') || url.includes('/node_modules/')) return result;
    const source = ts.transpileModule(result.source.toString(), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    return { ...result, source, format: 'commonjs' };
  },
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith('@/')) {
      return nextResolve(fileURLToPath(new URL(`../${specifier.slice(2)}.ts`, import.meta.url)), context);
    }
    return nextResolve(specifier, context);
  },
});

const { statusLabel, roleLabel, ASSIGNMENT_MODE_LABELS } = await import('../lib/labels.ts');
const { parseProtocolRunParameters } = await import('../features/campaigns/utils/protocolRunParametersParsing.ts');
const {
  createEntity,
  renameEntity,
  deleteEntity,
  getPackageTree,
  readEntityFiles,
  writeEntityFiles,
  getEntityMtimeOnly,
} = await import('../lib/filesystem/operations.ts');

test('Chinese labels preserve raw status, role and assignment values', () => {
  const raw = { status: 'RUNNING', role: 'LAB_ADMIN', mode: 'dynamic' };
  assert.equal(statusLabel(raw.status), '运行中');
  assert.equal(statusLabel('completed'), '已完成');
  assert.equal(roleLabel(raw.role), '实验室管理员');
  assert.equal(ASSIGNMENT_MODE_LABELS[raw.mode], '动态');
  assert.equal(statusLabel('NEW_BACKEND_STATUS'), 'NEW_BACKEND_STATUS');
  assert.deepEqual(raw, { status: 'RUNNING', role: 'LAB_ADMIN', mode: 'dynamic' });
});

test('login locale covers every message and preserves interpolation placeholders', async () => {
  const base = new URL('../../docker/zitadel/login/overlay/apps/login/locales/', import.meta.url);
  const en = JSON.parse(await readFile(new URL('en.json', base), 'utf8'));
  const zh = JSON.parse(await readFile(new URL('zh.json', base), 'utf8'));
  function compare(a, b) {
    assert.deepEqual(Object.keys(a), Object.keys(b));
    for (const key of Object.keys(a)) {
      if (typeof a[key] === 'object') compare(a[key], b[key]);
      else {
        assert.deepEqual(b[key].match(/\{\w+\}/g), a[key].match(/\{\w+\}/g));
        if (a[key]) assert.match(b[key], /[\u4e00-\u9fff]/);
      }
    }
  }
  compare(en, zh);
});

test('parameter import keeps machine keys and types while reporting Chinese errors', () => {
  assert.deepEqual(parseProtocolRunParameters('mix.temp,mix.enabled\n25,true'), [{ mix: { temp: 25, enabled: true } }]);
  assert.deepEqual(parseProtocolRunParameters('[{"mix":{"temp":25}}]'), [{ mix: { temp: 25 } }]);
  assert.throws(() => parseProtocolRunParameters('[bad'), /JSON 无效/);
  assert.throws(() => parseProtocolRunParameters('mix.temp.extra,mix.enabled\n25,true'), /列标题无效/);
});

test('localized filesystem errors still protect existing entity files', async () => {
  const temp = await mkdtemp(join(tmpdir(), 'eos-zh-entities-'));
  const previous = process.env.USER_DIR;
  process.env.USER_DIR = temp;
  try {
    const entities = join(temp, 'pkg', 'tasks');
    for (const name of ['source', 'target']) {
      await mkdir(join(entities, name), { recursive: true });
      await writeFile(join(entities, name, 'task.py'), `# ${name}`);
    }
    await assert.rejects(createEntity('pkg', 'tasks', 'target'), /条目已存在/);
    await assert.rejects(renameEntity('pkg', 'tasks', 'source', 'target'), /同名条目已存在/);
    assert.equal(await readFile(join(entities, 'source', 'task.py'), 'utf8'), '# source');
    assert.equal(await readFile(join(entities, 'target', 'task.py'), 'utf8'), '# target');
  } finally {
    if (previous === undefined) delete process.env.USER_DIR;
    else process.env.USER_DIR = previous;
    console.log(`清理本测试创建的临时目录：${temp}`);
    await rm(temp, { recursive: true, force: true });
  }
});

test('filesystem operations reject paths outside the requested package and entity directory', async () => {
  const temp = await mkdtemp(join(tmpdir(), 'eos-filesystem-boundary-'));
  const previous = process.env.USER_DIR;
  process.env.USER_DIR = join(temp, 'user');
  try {
    const outside = join(temp, 'outside', 'tasks', 'victim');
    await mkdir(outside, { recursive: true });
    await writeFile(join(outside, 'task.py'), '# must survive');

    await assert.rejects(deleteEntity('../outside', 'tasks', 'victim'), /路径无效/);
    assert.equal(await readFile(join(outside, 'task.py'), 'utf8'), '# must survive');
    await assert.rejects(getPackageTree('../outside'), /路径无效/);

    const invalid = [
      ['../outside', 'tasks', 'victim'],
      ['pkg', '../outside', 'victim'],
      ['pkg', '__proto__', 'victim'],
      ['pkg', 'tasks', '../victim'],
      ['pkg', 'tasks', '.'],
      ['pkg', 'tasks', '/absolute'],
      ['pkg', 'tasks', 'nested\\victim'],
    ];
    for (const args of invalid) {
      await assert.rejects(createEntity(...args), /路径无效/);
      await assert.rejects(readEntityFiles(...args), /路径无效/);
      await assert.rejects(writeEntityFiles(...args, { python: '# invalid' }), /路径无效/);
      await assert.rejects(getEntityMtimeOnly(...args), /路径无效/);
      await assert.rejects(deleteEntity(...args), /路径无效/);
      await assert.rejects(renameEntity(...args, 'renamed'), /路径无效/);
    }

    await createEntity('nested/pkg', 'tasks', 'group/source');
    await assert.rejects(renameEntity('nested/pkg', 'tasks', 'group/source', '../escaped'), /路径无效/);
    assert.match((await readEntityFiles('nested/pkg', 'tasks', 'group/source')).python, /async def source/);
    await createEntity('.', 'tasks', 'root_task');
    assert.match((await readEntityFiles('.', 'tasks', 'root_task')).python, /async def root_task/);
  } finally {
    if (previous === undefined) delete process.env.USER_DIR;
    else process.env.USER_DIR = previous;
    console.log(`清理本测试创建的临时目录：${temp}`);
    await rm(temp, { recursive: true, force: true });
  }
});
