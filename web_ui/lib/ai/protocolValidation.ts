import * as yaml from 'js-yaml';

export function validateYaml(yamlString: string): { valid: boolean; error?: string; parsed?: Record<string, unknown> } {
  try {
    const parsed = yaml.load(yamlString);
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      return { valid: false, error: 'YAML 必须为映射（对象），不能是标量或数组' };
    }
    return { valid: true, parsed: parsed as Record<string, unknown> };
  } catch (error: unknown) {
    const errorMessage = error instanceof Error ? error.message : '未知错误';
    return { valid: false, error: errorMessage };
  }
}
