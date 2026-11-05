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
    if (specifier.startsWith('.') && context.parentURL?.endsWith('.ts')) {
      const candidate = new URL(`${specifier}.ts`, context.parentURL);
      return nextResolve(fileURLToPath(candidate), context);
    }
    return nextResolve(specifier, context);
  },
});

const {
  assertOptimizerDomainSaveable,
  createBayesianOptimizerDraft,
  isOptimizerCodeWritable,
  loadOptimizerEditorState,
  mergeOptimizerSaveValues,
} = await import('../features/protocol-editor/utils/optimizerEditorState.ts');
const { buildOptimizerConfig, generateOptimizerPython } = await import(
  '../features/protocol-editor/utils/optimizerCodegen.ts'
);

const remoteDefaults = {
  optimizer_type: 'BeaconOptimizer',
  is_beacon: true,
  param_schema: [],
  inputs: [{ type: 'ContinuousInput', key: 'mix.volume', bounds: [1, 10] }],
  outputs: [
    {
      type: 'ContinuousOutput',
      key: 'score.loss',
      objective: { type: 'MinimizeObjective', w: 1 },
    },
  ],
  constraints: [],
  params: {
    p_bayesian: 0.8,
    p_ai: 0.2,
    ai_model: 'openai:mixture-qc',
    ai_retries: 4,
    ai_history_size: 25,
    ai_additional_context: '保留质控上下文',
    num_initial_samples: 3,
    initial_sampling_method: 'SOBOL',
    ai_model_settings: null,
    ai_additional_parameters: ['quality.verdict'],
    acquisition_function: { type: 'qLogNEI' },
    surrogate_specs: null,
  },
};

test('missing optimizer defaults is the neutral unconfigured state', async () => {
  const state = await loadOptimizerEditorState('', 'mixture_mock', async () => ({
    success: true,
    defaults: null,
  }));
  assert.deepEqual(state, { kind: 'unconfigured' });
});

test('serialized request failures remain visible and preserve the backend message', async () => {
  const state = await loadOptimizerEditorState('', 'matterix_full_station', async () => ({
    success: false,
    error: 'orchestrator returned 502: refresh failed',
  }));
  assert.equal(state.kind, 'error');
  assert.match(state.message, /orchestrator returned 502: refresh failed/);
});

test('new optimizer drafts are empty pure-Bayesian domains', () => {
  const draft = createBayesianOptimizerDraft();
  assert.deepEqual(draft.inputs, []);
  assert.deepEqual(draft.outputs, []);
  assert.deepEqual(draft.constraints, []);
  assert.equal(draft.params.p_bayesian, 1);
  assert.equal(draft.params.p_ai, 0);
  assert.equal(draft.params.ai_model, 'openai:your-model');
  assert.deepEqual(draft.params.acquisition_function, { type: 'qLogNEI' });
});

test('saving requires an input and an output domain', () => {
  const input = { type: 'ContinuousInput', key: 'mix.volume', bounds: [1, 10] };
  const output = {
    type: 'ContinuousOutput',
    key: 'score.loss',
    objective: { type: 'MinimizeObjective', w: 1 },
  };
  assert.throws(
    () => assertOptimizerDomainSaveable({ inputs: [], outputs: [output], constraints: [] }),
    /优化输入变量/
  );
  assert.throws(
    () => assertOptimizerDomainSaveable({ inputs: [input], outputs: [], constraints: [] }),
    /优化目标/
  );
  assert.doesNotThrow(() =>
    assertOptimizerDomainSaveable({ inputs: [input], outputs: [output], constraints: [] })
  );
});

test('partial edits preserve saved parameters and keep the strategy mix consistent', () => {
  const domain = {
    inputs: [{ type: 'ContinuousInput', key: 'mix.volume', bounds: [1, 10] }],
    outputs: [
      {
        type: 'ContinuousOutput',
        key: 'score.loss',
        objective: { type: 'MinimizeObjective', w: 1 },
      },
    ],
    constraints: [],
  };
  const merged = mergeOptimizerSaveValues(remoteDefaults, { num_initial_samples: 7, p_bayesian: 0.65 });
  const config = buildOptimizerConfig(merged, domain, [], '');
  assert.equal(config.num_initial_samples, 7);
  assert.equal(config.p_bayesian, 0.65);
  assert.equal(config.p_ai, 0.35);
  assert.equal(config.ai_model, 'openai:mixture-qc');
  assert.deepEqual(config.ai_additional_parameters, ['quality.verdict']);
});

test('import-style alias factories remain read-only even when remote defaults exist', async () => {
  const customFactory = `from matterix_sim.mixture_optimizer import optimizer_definition

eos_create_campaign_optimizer = optimizer_definition
`;
  let remoteCalls = 0;
  const state = await loadOptimizerEditorState(customFactory, 'mixture_mock', async () => {
    remoteCalls += 1;
    return { success: true, defaults: remoteDefaults };
  });
  assert.deepEqual(state, { kind: 'custom' });
  assert.equal(remoteCalls, 0);
  assert.equal(isOptimizerCodeWritable(customFactory), false);
});

test('standard literal custom factories keep their preamble and return class when saved', () => {
  const existingCode = `from eos.optimization.beacon_optimizer import BeaconOptimizer


class QualityBeaconOptimizer(BeaconOptimizer):
    def _quality_gate(self):
        return True


def eos_create_campaign_optimizer():
    constructor_args = {
        "inputs": [],
        "outputs": [],
        "constraints": [],
        "p_bayesian": 1.0,
        "p_ai": 0.0,
    }
    return constructor_args, QualityBeaconOptimizer
`;
  const domain = {
    inputs: [{ type: 'ContinuousInput', key: 'mix.volume', bounds: [1, 10] }],
    outputs: [
      {
        type: 'ContinuousOutput',
        key: 'score.loss',
        objective: { type: 'MinimizeObjective', w: 1 },
      },
    ],
    constraints: [],
  };

  assert.equal(isOptimizerCodeWritable(existingCode), true);
  const config = buildOptimizerConfig(remoteDefaults.params, domain, [], existingCode);
  const generated = generateOptimizerPython(config);
  assert.match(generated, /class QualityBeaconOptimizer\(BeaconOptimizer\):/);
  assert.match(generated, /return constructor_args, QualityBeaconOptimizer/);
});
