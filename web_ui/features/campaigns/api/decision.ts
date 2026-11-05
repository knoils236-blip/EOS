'use server';

import { orchestratorGet, orchestratorPost } from '@/lib/api/orchestrator';
import type { DecisionSnapshot, DecisionReview } from '../types/decision';

export async function getCampaignDecision(name: string): Promise<{ data?: DecisionSnapshot; error?: string }> {
  try {
    return { data: (await orchestratorGet(`/campaigns/${encodeURIComponent(name)}/decision`)) as DecisionSnapshot };
  } catch (error) {
    return { error: error instanceof Error ? error.message : '获取实验决策数据失败' };
  }
}

export async function reviewCampaignDecision(
  name: string,
  question: string
): Promise<{ data?: DecisionReview; error?: string }> {
  try {
    return {
      data: (await orchestratorPost(`/campaigns/${encodeURIComponent(name)}/decision/review`, {
        question,
      })) as DecisionReview,
    };
  } catch (error) {
    return { error: error instanceof Error ? error.message : 'AI 分析失败' };
  }
}
