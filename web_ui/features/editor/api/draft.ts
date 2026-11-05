'use server';

/**
 * Server Action for AI-drafted protocols.
 */

import { orchestratorPost } from '@/lib/api/orchestrator';
import { requireRole } from '@/lib/auth/authz';

export interface ProtocolDraft {
  yaml: string;
  notes: string;
  valid: boolean;
  errors: Array<{ task: string | null; message: string }>;
  attempts: number;
}

/**
 * Ask the orchestrator's AI drafter for a protocol, validated against the given labs. Failures come back as a
 * value: production builds hide the message of an error thrown from a server action.
 */
export async function draftProtocol(
  goal: string,
  labs: string[],
  protocolType: string
): Promise<{ data?: ProtocolDraft; error?: string }> {
  try {
    await requireRole('EDITOR');
    return {
      data: (await orchestratorPost('/protocols/draft', { goal, labs, protocol_type: protocolType })) as ProtocolDraft,
    };
  } catch (error) {
    return { error: error instanceof Error ? error.message : '生成草稿失败' };
  }
}
