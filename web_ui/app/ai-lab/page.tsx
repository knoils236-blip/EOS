import { getApprovals, getNotebook } from '@/features/ai-lab/api/aiLab';
import { AiLabClient } from '@/features/ai-lab/components/AiLabClient';

export const dynamic = 'force-dynamic';

export default async function AiLabPage() {
  const [approvals, notebook] = await Promise.all([getApprovals(), getNotebook()]);

  return (
    <div className="container mx-auto px-6 py-8">
      <div className="mb-6">
        <h1 className="text-3xl font-bold text-gray-900 dark:text-white">AI 协作</h1>
        <p className="text-gray-600 dark:text-gray-400 mt-1">
          审批 AI 提交到真实实验室的运行，查看它记录的假设、设计理由和决策
        </p>
      </div>
      <AiLabClient approvals={approvals} notebook={notebook} />
    </div>
  );
}
