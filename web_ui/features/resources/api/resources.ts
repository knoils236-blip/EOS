'use server';

import { revalidatePath } from 'next/cache';
import { orchestratorPost } from '@/lib/api/orchestrator';
import { DEFAULT_PAGE_SIZE, type TableQueryOptions } from '@/lib/types/table';
import { getAllResources, type PaginatedResult, type ResourceRow } from '@/lib/db/queries';
import type { ActionResult } from '@/lib/types/management';
import { requireRole } from '@/lib/auth/authz';

export async function getResources(options: TableQueryOptions = {}): Promise<PaginatedResult<ResourceRow>> {
  await requireRole('VIEWER');
  return await getAllResources({ limit: DEFAULT_PAGE_SIZE, offset: 0, ...options });
}

export async function resetResources(resourceNames: string[]): Promise<ActionResult> {
  try {
    await requireRole('SUBMITTER');
    await orchestratorPost('/resources/reset', { resource_names: resourceNames });
    revalidatePath('/resources');
    return { success: true };
  } catch (error) {
    return {
      success: false,
      error: error instanceof Error ? error.message : '重置资源失败',
    };
  }
}

export async function adjustConsumable(resourceName: string, amount: number, eventId: string): Promise<ActionResult> {
  try {
    await requireRole('SUBMITTER');
    await orchestratorPost('/resources/inventory-events', {
      resource_name: resourceName,
      delta: amount,
      event_id: eventId,
      source: 'manual',
      note: '手动补充',
    });
    revalidatePath('/resources');
    return { success: true };
  } catch (error) {
    return {
      success: false,
      error: error instanceof Error ? error.message : '补充耗材失败',
    };
  }
}
