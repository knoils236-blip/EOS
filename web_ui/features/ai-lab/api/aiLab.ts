'use server';

import { revalidatePath } from 'next/cache';
import { orchestratorGet, orchestratorPost } from '@/lib/api/orchestrator';
import { requireRole } from '@/lib/auth/authz';

export interface ApprovalRequest {
  id: number;
  kind: 'protocol_run' | 'campaign';
  name: string;
  labs: string[];
  payload: Record<string, unknown>;
  reason: string | null;
  status: 'pending' | 'approved' | 'rejected';
  requested_by: string;
  decided_by: string | null;
  note: string | null;
  created_at: string;
  decided_at: string | null;
}

export interface NotebookEntry {
  id: number;
  kind: string;
  content: string;
  campaign_name: string | null;
  protocol_run_name: string | null;
  author: string;
  created_at: string;
}

export async function getApprovals(): Promise<ApprovalRequest[]> {
  return (await orchestratorGet('/approvals/')) as ApprovalRequest[];
}

export async function getNotebook(): Promise<NotebookEntry[]> {
  return (await orchestratorGet('/notebook/?limit=100')) as NotebookEntry[];
}

export async function decideApproval(
  id: number,
  decision: 'approve' | 'reject',
  note: string
): Promise<{ error?: string }> {
  try {
    await requireRole('SUBMITTER');
    await orchestratorPost(`/approvals/${id}/${decision}`, { note: note || null });
    revalidatePath('/ai-lab');
    return {};
  } catch (error) {
    return { error: error instanceof Error ? error.message : '操作失败' };
  }
}
