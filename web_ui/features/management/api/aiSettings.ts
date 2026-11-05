'use server';

import { requireSuperuser } from '@/lib/auth/authz';
import { orchestratorGet, orchestratorPut } from '@/lib/api/orchestrator';
import type { ActionResult } from '@/lib/types/management';

export type AIProtocol = 'openai' | 'anthropic';

export interface AISettings {
  protocol: AIProtocol;
  base_url: string;
  model: string;
  timeout_s: number;
  key_configured: boolean;
  configured: boolean;
}

export interface SaveAISettingsInput {
  protocol: AIProtocol;
  base_url: string;
  model: string;
  api_key: string;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

export async function getAiSettings(): Promise<ActionResult<AISettings>> {
  try {
    await requireSuperuser();
    return { success: true, data: (await orchestratorGet('/ai-settings/')) as AISettings };
  } catch (error) {
    return { success: false, error: errorMessage(error, '获取 AI 设置失败') };
  }
}

export async function saveAiSettings(input: SaveAISettingsInput): Promise<ActionResult<AISettings>> {
  try {
    await requireSuperuser();
    return {
      success: true,
      data: (await orchestratorPut('/ai-settings/', input)) as AISettings,
    };
  } catch (error) {
    return { success: false, error: errorMessage(error, '保存 AI 设置失败') };
  }
}
