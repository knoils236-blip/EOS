import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

registerHooks({
  load(url, context, nextLoad) {
    if (url === 'eos:test-auth') {
      return {
        format: 'commonjs',
        source: 'module.exports = { getAccessToken: async () => null };',
        shortCircuit: true,
      };
    }
    const result = nextLoad(url, url.endsWith('.ts') ? { ...context, format: 'module' } : context);
    if (!url.endsWith('.ts') || url.includes('/node_modules/')) return result;
    const source = ts.transpileModule(result.source.toString(), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    return { ...result, source, format: 'commonjs' };
  },
  resolve(specifier, context, nextResolve) {
    if (specifier === '@/lib/auth/session') return { url: 'eos:test-auth', shortCircuit: true };
    if (specifier.startsWith('@/')) {
      return nextResolve(fileURLToPath(new URL(`../${specifier.slice(2)}.ts`, import.meta.url)), context);
    }
    return nextResolve(specifier, context);
  },
});

process.env.ORCHESTRATOR_API_URL = 'http://orchestrator.test/api';
const { OrchestratorApiError, ORCHESTRATOR_BASE_URL } = await import('../lib/api/orchestrator.ts');
const { getOptimizerDefaults, getOptimizerDefaultsResult } = await import('../features/campaigns/api/optimizer.ts');

const defaults = {
  optimizer_type: 'BeaconOptimizer',
  is_beacon: true,
  param_schema: [],
  inputs: [],
  outputs: [],
  constraints: [],
  params: {},
};

function jsonResponse(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

async function withFetch(fetchImpl, callback) {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = fetchImpl;
  try {
    await callback();
  } finally {
    globalThis.fetch = originalFetch;
  }
}

test('optimizer loading distinguishes missing configuration from API and network failures', async () => {
  const protocol = 'matterix/full #1?';
  await withFetch(
    async (url, init) => {
      assert.equal(url, `${ORCHESTRATOR_BASE_URL}/campaigns/optimizer/defaults/${encodeURIComponent(protocol)}`);
      assert.equal(init.method, 'GET');
      return jsonResponse(200, defaults);
    },
    async () => {
      assert.deepEqual(await getOptimizerDefaults(protocol), defaults);
      assert.deepEqual(await getOptimizerDefaultsResult(protocol), { success: true, defaults });
    }
  );

  await withFetch(
    async () => jsonResponse(404, { error: 'No optimizer found' }),
    async () => {
      assert.equal(await getOptimizerDefaults('without_optimizer'), null);
      assert.deepEqual(await getOptimizerDefaultsResult('without_optimizer'), { success: true, defaults: null });
    }
  );

  await withFetch(
    async () => jsonResponse(401, { error: 'Authentication required' }),
    async () => {
      await assert.rejects(getOptimizerDefaults('matterix_full_station'), (error) => {
        assert.ok(error instanceof OrchestratorApiError);
        assert.equal(error.status, 401);
        assert.equal(error.message, 'Authentication required');
        return true;
      });
      assert.deepEqual(await getOptimizerDefaultsResult('matterix_full_station'), {
        success: false,
        error: 'Authentication required（HTTP 401）',
      });
    }
  );

  await withFetch(
    async () => jsonResponse(500, { error: '\u001b[31mOptimizer exploded\u001b[0m' }),
    async () => {
      await assert.rejects(getOptimizerDefaults('matterix_full_station'), (error) => {
        assert.ok(error instanceof OrchestratorApiError);
        assert.equal(error.status, 500);
        assert.equal(error.message, 'Optimizer exploded');
        return true;
      });
      assert.deepEqual(await getOptimizerDefaultsResult('matterix_full_station'), {
        success: false,
        error: 'Optimizer exploded（HTTP 500）',
      });
    }
  );

  await withFetch(
    async () => {
      throw new TypeError('fetch failed');
    },
    async () => {
      await assert.rejects(getOptimizerDefaults('matterix_full_station'), /fetch failed/);
      assert.deepEqual(await getOptimizerDefaultsResult('matterix_full_station'), {
        success: false,
        error: 'fetch failed',
      });
    }
  );
});
