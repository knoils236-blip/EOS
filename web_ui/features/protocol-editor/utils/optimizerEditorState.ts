import type { DomainValue } from '@/features/campaigns/components/DomainEditor';
import type { OptimizerDefaultsResult } from '@/features/campaigns/api/optimizer';
import type { OptimizerDefaults } from '@/lib/types/api';
import { isStandardOptimizerPy, parseOptimizerPython } from './optimizerCodegen';

export type OptimizerEditorState =
  | { kind: 'loading' }
  | { kind: 'unconfigured' }
  | { kind: 'custom' }
  | { kind: 'error'; message: string }
  | {
      kind: 'ready';
      source: 'local' | 'remote' | 'draft';
      defaults: OptimizerDefaults;
    };

export function createBayesianOptimizerDraft(): OptimizerDefaults {
  return {
    optimizer_type: 'BeaconOptimizer',
    is_beacon: true,
    param_schema: [],
    inputs: [],
    outputs: [],
    constraints: [],
    params: {
      p_bayesian: 1,
      p_ai: 0,
      ai_model: 'openai:your-model',
      ai_retries: 3,
      ai_history_size: 50,
      ai_additional_context: null,
      num_initial_samples: 5,
      initial_sampling_method: 'SOBOL',
      ai_model_settings: null,
      ai_additional_parameters: null,
      acquisition_function: { type: 'qLogNEI' },
      surrogate_specs: null,
    },
  };
}

export function isOptimizerCodeWritable(code: string): boolean {
  return !code.trim() || isStandardOptimizerPy(code);
}

export function mergeOptimizerSaveValues(
  defaults: OptimizerDefaults,
  values: Record<string, unknown>
): Record<string, unknown> {
  const merged = { ...defaults.params, ...values };
  const pBayesian = merged.p_bayesian as number;
  return { ...merged, p_bayesian: pBayesian, p_ai: 1 - pBayesian };
}

export function assertOptimizerDomainSaveable(domain: DomainValue): void {
  if (domain.inputs.length === 0) throw new Error('请至少添加一个优化输入变量。');
  if (domain.outputs.length === 0) throw new Error('请至少添加一个优化目标。');
  if (domain.inputs.some((input) => !input.key.trim())) throw new Error('优化输入变量必须填写键名。');
  if (domain.outputs.some((output) => !output.key.trim())) throw new Error('优化目标必须填写键名。');
}

export async function loadOptimizerEditorState(
  pythonContent: string,
  protocolType: string | null,
  loadDefaults: (protocolType: string) => Promise<OptimizerDefaultsResult>
): Promise<Exclude<OptimizerEditorState, { kind: 'loading' }>> {
  if (pythonContent.trim()) {
    if (isOptimizerCodeWritable(pythonContent)) {
      const parsed = parseOptimizerPython(pythonContent);
      if (parsed) return { kind: 'ready', source: 'local', defaults: parsed };
    }
    return { kind: 'custom' };
  }

  if (!protocolType) return { kind: 'unconfigured' };

  try {
    const result = await loadDefaults(protocolType);
    if (!result.success) return { kind: 'error', message: result.error || '无法获取优化器默认值，请重试。' };
    return result.defaults
      ? { kind: 'ready', source: 'remote', defaults: result.defaults }
      : { kind: 'unconfigured' };
  } catch (error) {
    const detail = error instanceof Error ? error.message.trim() : '';
    return {
      kind: 'error',
      message: detail ? `无法获取优化器默认值：${detail}` : '无法获取优化器默认值，请重试。',
    };
  }
}
