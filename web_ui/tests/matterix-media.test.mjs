import assert from 'node:assert/strict';
import { test } from 'node:test';
import ts from 'typescript';
import { fileURLToPath } from 'node:url';
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
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith('@/')) {
      return nextResolve(fileURLToPath(new URL(`../${specifier.slice(2)}.ts`, import.meta.url)), context);
    }
    return nextResolve(specifier, context);
  },
});

const { assertSafeS3Key, downloadResponseShape, normalizeByteRange, resolveInlineContentType } = await import(
  '../lib/s3/client.ts'
);

test('downloads accept relative S3 keys and reject filesystem-like traversal', () => {
  assert.doesNotThrow(() => assertSafeS3Key('run/task/matterix-evidence.zip'));
  for (const key of ['', '/absolute/key', '../secret', 'run/../secret', 'run//secret', 'run\\secret', 'run/\0']) {
    assert.throws(() => assertSafeS3Key(key), /S3 对象键无效/);
  }
});

test('video playback forwards one valid byte range and rejects multipart ranges', () => {
  assert.equal(normalizeByteRange(null), undefined);
  assert.equal(normalizeByteRange('bytes=0-1023'), 'bytes=0-1023');
  assert.equal(normalizeByteRange('bytes=1024-'), 'bytes=1024-');
  assert.equal(normalizeByteRange('bytes=-1024'), 'bytes=-1024');
  for (const range of ['items=0-1', 'bytes=0-1,4-5', 'bytes=bad']) {
    assert.throws(() => normalizeByteRange(range), /单段 HTTP Range/);
  }
});

test('inline video API response exposes the exact partial-content contract', () => {
  assert.deepEqual(
    downloadResponseShape({
      fileName: '仿真.mp4',
      size: 1024,
      contentType: 'video/mp4',
      contentRange: 'bytes 0-1023/1048576',
      acceptRanges: 'bytes',
      inline: true,
    }),
    {
      status: 206,
      headers: {
        'Content-Type': 'video/mp4',
        'Content-Length': '1024',
        'Content-Disposition': `inline; filename*=UTF-8''${encodeURIComponent('仿真.mp4')}`,
        'Accept-Ranges': 'bytes',
        'X-Content-Type-Options': 'nosniff',
        'Content-Range': 'bytes 0-1023/1048576',
      },
    }
  );
  assert.equal(
    downloadResponseShape({
      fileName: 'matterix-evidence.zip',
      size: 2048,
      contentType: 'application/zip',
      contentRange: null,
      acceptRanges: 'bytes',
      inline: false,
    }).status,
    200
  );
});

test('inline delivery permits video and plain JSON but rejects HTML and SVG', () => {
  assert.equal(resolveInlineContentType('application/octet-stream', 'simulation.mp4'), 'video/mp4');
  assert.equal(resolveInlineContentType('binary/octet-stream', 'simulation.mp4'), 'video/mp4');
  assert.equal(resolveInlineContentType('video/mp4', 'simulation.mp4'), 'video/mp4');
  assert.equal(resolveInlineContentType('application/json', 'latest.json'), 'application/json');
  assert.equal(
    resolveInlineContentType('application/octet-stream', 'samples.jsonl'),
    'text/plain; charset=utf-8'
  );
  assert.equal(resolveInlineContentType('text/html', 'evidence.html'), null);
  assert.equal(resolveInlineContentType('image/svg+xml', 'evidence.svg'), null);
});
