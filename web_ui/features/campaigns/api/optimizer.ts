'use server';

import { OrchestratorApiError, orchestratorGet, orchestratorPost, orchestratorPut } from '@/lib/api/orchestrator';
import type { OptimizerDefaults, OptimizerInfo, ActionResult } from '@/lib/types/api';

/**
 * Get default optimizer parameters for a protocol.
 * Returns null if no optimizer is configured.
 */
export async function getOptimizerDefaults(protocolType: string): Promise<OptimizerDefaults | null> {
  try {
    const result = await orchestratorGet(`/campaigns/optimizer/defaults/${encodeURIComponent(protocolType)}`);
    return result as OptimizerDefaults;
  } catch (error) {
    if (error instanceof OrchestratorApiError && error.status === 404) return null;
    throw error;
  }
}

export type OptimizerDefaultsResult =
  | { success: true; defaults: OptimizerDefaults | null }
  | { success: false; error: string };

/**
 * Server-action-safe optimizer defaults loader.
 * Non-404 errors are converted to a small serializable result for the client.
 */
export async function getOptimizerDefaultsResult(protocolType: string): Promise<OptimizerDefaultsResult> {
  try {
    return { success: true, defaults: await getOptimizerDefaults(protocolType) };
  } catch (error) {
    const status = error instanceof OrchestratorApiError ? `（HTTP ${error.status}）` : '';
    const message = error instanceof Error ? error.message : '读取优化器配置失败。';
    return { success: false, error: `${message}${status}` };
  }
}

/**
 * Get current optimizer info for a running campaign.
 * Returns null if campaign is not running or has no optimizer.
 */
export async function getOptimizerInfo(campaignName: string): Promise<OptimizerInfo | null> {
  try {
    const result = (await orchestratorGet(`/campaigns/${campaignName}/optimizer/info`)) as Record<string, unknown>;
    if (result.status === 'initializing') return null;
    return result as unknown as OptimizerInfo;
  } catch {
    return null;
  }
}

/**
 * Update runtime-safe optimizer parameters for a running campaign.
 */
export async function updateOptimizerParams(
  campaignName: string,
  params: {
    p_bayesian?: number;
    ai_history_size?: number;
    ai_additional_context?: string;
  }
): Promise<ActionResult> {
  try {
    await orchestratorPut(`/campaigns/${campaignName}/optimizer/params`, params);
    return { success: true };
  } catch (error) {
    return {
      success: false,
      error: error instanceof Error ? error.message : '更新优化器参数失败',
    };
  }
}

/**
 * Add an expert insight to a campaign's optimizer.
 */
export async function addOptimizerInsight(campaignName: string, insight: string): Promise<ActionResult> {
  try {
    await orchestratorPost(`/campaigns/${campaignName}/optimizer/insight`, { insight });
    return { success: true };
  } catch (error) {
    return {
      success: false,
      error: error instanceof Error ? error.message : '添加建议失败',
    };
  }
}
