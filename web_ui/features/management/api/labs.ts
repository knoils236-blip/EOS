'use server';

/**
 * Server Actions for Lab Management
 */

import { revalidatePath } from 'next/cache';
import { orchestratorPost, orchestratorGet } from '@/lib/api/orchestrator';
import { requireSuperuser } from '@/lib/auth/authz';
import { db } from '@/lib/db/client';
import { definitions } from '@/lib/db/schema';
import { eq } from 'drizzle-orm';
import type { Lab, ActionResult } from '@/lib/types/management';

/**
 * Get all labs with their loaded status from the orchestrator API (single source of truth)
 * and package names from the database.
 */
export async function getLabs(): Promise<Lab[]> {
  try {
    const [response, defs] = await Promise.all([
      orchestratorGet('/labs/') as Promise<Record<string, boolean>>,
      db
        .select({ name: definitions.name, packageName: definitions.packageName })
        .from(definitions)
        .where(eq(definitions.type, 'lab')),
    ]);
    const packageByName = new Map(defs.map((d) => [d.name, d.packageName]));
    return Object.entries(response).map(([name, loaded]) => ({
      name,
      loaded,
      package: packageByName.get(name) ?? '',
    }));
  } catch (error) {
    console.error('获取实验室失败：', error);
    throw new Error('获取实验室失败');
  }
}

/**
 * Load labs
 */
export async function loadLabs(labTypes: string[]): Promise<ActionResult> {
  try {
    await requireSuperuser();
    await orchestratorPost('/labs/load', {
      lab_types: labTypes,
    });

    revalidatePath('/management');
    return { success: true };
  } catch (error) {
    console.error('加载实验室失败：', error);
    revalidatePath('/management');
    return {
      success: false,
      error: error instanceof Error ? error.message : '加载实验室失败',
    };
  }
}

/**
 * Unload labs
 */
export async function unloadLabs(labTypes: string[]): Promise<ActionResult> {
  try {
    await requireSuperuser();
    await orchestratorPost('/labs/unload', {
      lab_types: labTypes,
    });

    revalidatePath('/management');
    return { success: true };
  } catch (error) {
    console.error('卸载实验室失败：', error);
    revalidatePath('/management');
    return {
      success: false,
      error: error instanceof Error ? error.message : '卸载实验室失败',
    };
  }
}

/**
 * Reload labs
 */
export async function reloadLabs(labTypes: string[]): Promise<ActionResult> {
  try {
    await requireSuperuser();
    await orchestratorPost('/labs/reload', {
      lab_types: labTypes,
    });

    revalidatePath('/management');
    return { success: true };
  } catch (error) {
    console.error('重新加载实验室失败：', error);
    revalidatePath('/management');
    return {
      success: false,
      error: error instanceof Error ? error.message : '重新加载实验室失败',
    };
  }
}
