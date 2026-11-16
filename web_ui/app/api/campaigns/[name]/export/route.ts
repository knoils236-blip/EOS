import { orchestratorGet, OrchestratorApiError } from '@/lib/api/orchestrator';
import { requireRoleResponse } from '@/lib/auth/authz';

/** Download a campaign's complete record (every round, not the decision view's recent window). */
export async function GET(_request: Request, { params }: { params: Promise<{ name: string }> }) {
  const denied = await requireRoleResponse('VIEWER');
  if (denied) return denied;
  const { name } = await params;
  try {
    const exported = await orchestratorGet(`/campaigns/${encodeURIComponent(name)}/export`);
    return new Response(JSON.stringify(exported, null, 2), {
      headers: {
        'Content-Type': 'application/json',
        'Content-Disposition': `attachment; filename*=UTF-8''${encodeURIComponent(name)}-export.json`,
      },
    });
  } catch (error) {
    if (error instanceof OrchestratorApiError) return Response.json({ error: error.message }, { status: error.status });
    throw error;
  }
}
