import { orchestratorPost } from '@/lib/api/orchestrator';
import type { ActionResult } from '@/lib/types/api';

interface AdmissionResponse {
  status: 'submitted' | 'pending_approval';
  approval_id?: number;
  approval_labs?: string[];
}

/**
 * Submit through the EOS admission endpoint: it starts at once, or is held for a lab admin when a lab's approval
 * policy requires it. A held submission is reported as not started so the dialog stays open and explains why.
 */
export async function submitThroughAdmission(
  kind: 'protocol-runs' | 'campaigns' | 'tasks',
  submission: unknown
): Promise<ActionResult> {
  const result = (await orchestratorPost(`/approvals/${kind}`, { submission })) as AdmissionResponse;
  if (result.status === 'pending_approval') {
    return {
      success: false,
      error: `已提交审批申请 #${result.approval_id}：实验室 ${result.approval_labs?.join('、')} 要求实验室管理员在“AI 实验室”页面批准后才会执行，请勿重复提交。`,
    };
  }
  return { success: true };
}
