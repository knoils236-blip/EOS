// Mirrors the backend identifier rule in eos/configuration/utils.py.
export const IDENTIFIER_PATTERN = /^[a-zA-Z0-9_-]+(?: [a-zA-Z0-9_-]+)*$/;

export const IDENTIFIER_ERROR_MESSAGE = '允许使用英文字母、数字、下划线、连字符和单个空格。';

export function isValidIdentifier(value: string): boolean {
  return IDENTIFIER_PATTERN.test(value);
}
