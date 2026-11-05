import { ParameterSpec } from '../types/protocol';

export interface ValidationResult {
  valid: boolean;
  error?: string;
}

/**
 * Validates a numeric parameter value against its spec
 */
export function validateNumeric(value: unknown, spec: ParameterSpec): ValidationResult {
  if (value === null || value === undefined || value === '') {
    return { valid: false, error: '此值为必填项' };
  }

  const numValue = typeof value === 'string' ? parseFloat(value) : Number(value);

  if (isNaN(numValue)) {
    return { valid: false, error: '请输入有效数值' };
  }

  // Check min/max bounds (only for single numbers, not arrays)
  if (typeof spec.min === 'number' && numValue < spec.min) {
    return { valid: false, error: `值不得小于 ${spec.min}` };
  }

  if (typeof spec.max === 'number' && numValue > spec.max) {
    return { valid: false, error: `值不得大于 ${spec.max}` };
  }

  // For int type, check if it's an integer
  if (spec.type === 'int' && !Number.isInteger(numValue)) {
    return { valid: false, error: '请输入整数' };
  }

  return { valid: true };
}

/**
 * Validates a string parameter value against its spec
 */
export function validateString(value: unknown, _spec: ParameterSpec): ValidationResult {
  if (value === null || value === undefined) {
    return { valid: false, error: '此值为必填项' };
  }

  const strValue = String(value);

  if (!strValue.trim()) {
    return { valid: false, error: '值不能为空或仅包含空白字符' };
  }

  return { valid: true };
}

/**
 * Validates a boolean parameter value
 */
export function validateBoolean(value: unknown, _spec: ParameterSpec): ValidationResult {
  if (value === null || value === undefined) {
    return { valid: false, error: '此值为必填项' };
  }

  if (typeof value !== 'boolean') {
    return { valid: false, error: '值必须为 true 或 false' };
  }

  return { valid: true };
}

/**
 * Validates a choice parameter value against its spec
 */
export function validateChoice(value: unknown, spec: ParameterSpec): ValidationResult {
  if (value === null || value === undefined || value === '') {
    return { valid: false, error: '此值为必填项' };
  }

  if (!spec.choices || spec.choices.length === 0) {
    return { valid: false, error: '此参数尚未定义可选值' };
  }

  const strValue = String(value);

  if (!spec.choices.includes(strValue)) {
    return {
      valid: false,
      error: `值必须为以下选项之一：${spec.choices.join(', ')}`,
    };
  }

  return { valid: true };
}

/**
 * Validates a list parameter value against its spec
 */
export function validateList(value: unknown, spec: ParameterSpec): ValidationResult {
  if (value === null || value === undefined || value === '') {
    return { valid: false, error: '此值为必填项' };
  }

  let arrayValue: unknown[];

  // Parse JSON if it's a string
  if (typeof value === 'string') {
    try {
      arrayValue = JSON.parse(value);
    } catch {
      return { valid: false, error: '请输入有效的 JSON 数组' };
    }
  } else {
    arrayValue = value as unknown[];
  }

  if (!Array.isArray(arrayValue)) {
    return { valid: false, error: '值必须为数组' };
  }

  // Check length constraint
  if (spec.length !== undefined && arrayValue.length !== spec.length) {
    return {
      valid: false,
      error: `数组必须包含 ${spec.length} 个元素`,
    };
  }

  // Validate element types
  if (spec.element_type) {
    for (let i = 0; i < arrayValue.length; i++) {
      const element = arrayValue[i];
      const elementValidation = validateListElement(element, spec.element_type, i, spec);

      if (!elementValidation.valid) {
        return elementValidation;
      }
    }
  }

  return { valid: true };
}

/**
 * Validates a single element of a list
 */
function validateListElement(
  element: unknown,
  elementType: string,
  index: number,
  spec: ParameterSpec
): ValidationResult {
  const normalizedType = elementType.toLowerCase();

  // Check element type
  switch (normalizedType) {
    case 'int':
    case 'integer':
      if (!Number.isInteger(element)) {
        return {
          valid: false,
          error: `元素 ${index} 必须为整数`,
        };
      }
      break;

    case 'float':
    case 'number':
    case 'double':
      if (typeof element !== 'number' || isNaN(element)) {
        return {
          valid: false,
          error: `元素 ${index} 必须为数值`,
        };
      }
      break;

    case 'str':
    case 'string':
      if (typeof element !== 'string') {
        return {
          valid: false,
          error: `元素 ${index} 必须为字符串`,
        };
      }
      break;

    case 'bool':
    case 'boolean':
      if (typeof element !== 'boolean') {
        return {
          valid: false,
          error: `元素 ${index} 必须为布尔值`,
        };
      }
      break;

    default:
      // Unknown element type, skip validation
      break;
  }

  // Check per-element bounds for numeric types
  if (
    (normalizedType === 'int' ||
      normalizedType === 'integer' ||
      normalizedType === 'float' ||
      normalizedType === 'number' ||
      normalizedType === 'double') &&
    typeof element === 'number'
  ) {
    // Check min bounds (array of per-element minimums)
    if (Array.isArray(spec.min) && spec.min[index] !== undefined) {
      if (element < spec.min[index]) {
        return {
          valid: false,
          error: `元素 ${index} 不得小于 ${spec.min[index]}`,
        };
      }
    }

    // Check max bounds (array of per-element maximums)
    if (Array.isArray(spec.max) && spec.max[index] !== undefined) {
      if (element > spec.max[index]) {
        return {
          valid: false,
          error: `元素 ${index} 不得大于 ${spec.max[index]}`,
        };
      }
    }
  }

  return { valid: true };
}

/**
 * Validates a dict parameter value
 */
export function validateDict(value: unknown, _spec: ParameterSpec): ValidationResult {
  if (value === null || value === undefined || value === '') {
    return { valid: false, error: '此值为必填项' };
  }

  let objValue: unknown;

  // Parse JSON if it's a string
  if (typeof value === 'string') {
    try {
      objValue = JSON.parse(value);
    } catch {
      return { valid: false, error: '请输入有效的 JSON 对象' };
    }
  } else {
    objValue = value;
  }

  if (typeof objValue !== 'object' || objValue === null || Array.isArray(objValue)) {
    return { valid: false, error: '值必须为对象' };
  }

  return { valid: true };
}

/**
 * Main validation function that routes to the appropriate validator
 */
export function validateParameter(value: unknown, spec: ParameterSpec): ValidationResult {
  const normalizedType = spec.type.toLowerCase();

  switch (normalizedType) {
    case 'int':
    case 'integer':
    case 'float':
    case 'number':
    case 'double':
      return validateNumeric(value, spec);

    case 'str':
    case 'string':
      return validateString(value, spec);

    case 'bool':
    case 'boolean':
      return validateBoolean(value, spec);

    case 'choice':
      return validateChoice(value, spec);

    case 'list':
      return validateList(value, spec);

    case 'dict':
    case 'dictionary':
      return validateDict(value, spec);

    default:
      // Unknown type, skip validation
      return { valid: true };
  }
}
