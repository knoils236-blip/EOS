'use server';

/**
 * Server Actions for ProtocolRun Type Management
 */

import { revalidatePath } from 'next/cache';
import { orchestratorPost } from '@/lib/api/orchestrator';
import { requireRole } from '@/lib/auth/authz';
import { db } from '@/lib/db/client';
import { definitions } from '@/lib/db/schema';
import { eq } from 'drizzle-orm';
import type { ProtocolType, ActionResult } from '@/lib/types/management';

/**
 * Get all protocol types with their loaded status from the database
 */
export async function getProtocolTypes(): Promise<ProtocolType[]> {
  try {
    const results = await db
      .select({
        name: definitions.name,
        loaded: definitions.isLoaded,
        packageName: definitions.packageName,
      })
      .from(definitions)
      .where(eq(definitions.type, 'protocol'));

    return results.map((row) => ({
      name: row.name,
      loaded: row.loaded,
      package: row.packageName,
    }));
  } catch (error) {
    console.error('获取协议类型失败：', error);
    throw new Error('获取数据库中的协议类型失败');
  }
}

/**
 * Load protocol types
 */
export async function loadProtocolTypes(protocolTypes: string[]): Promise<ActionResult> {
  try {
    await requireRole('LAB_ADMIN');
    await orchestratorPost('/protocols/load', {
      protocol_types: protocolTypes,
    });

    // Revalidate the management page to show updated status
    revalidatePath('/management');

    return {
      success: true,
    };
  } catch (error) {
    console.error('加载协议类型失败：', error);
    return {
      success: false,
      error: error instanceof Error ? error.message : '加载协议类型失败',
    };
  }
}

/**
 * Unload protocol types
 */
export async function unloadProtocolTypes(protocolTypes: string[]): Promise<ActionResult> {
  try {
    await requireRole('LAB_ADMIN');
    await orchestratorPost('/protocols/unload', {
      protocol_types: protocolTypes,
    });

    // Revalidate the management page to show updated status
    revalidatePath('/management');

    return {
      success: true,
    };
  } catch (error) {
    console.error('卸载协议类型失败：', error);
    return {
      success: false,
      error: error instanceof Error ? error.message : '卸载协议类型失败',
    };
  }
}

/**
 * Reload protocol types
 */
export async function reloadProtocolTypes(protocolTypes: string[]): Promise<ActionResult> {
  try {
    await requireRole('LAB_ADMIN');
    await orchestratorPost('/protocols/reload', {
      protocol_types: protocolTypes,
    });

    // Revalidate the management page to show updated status
    revalidatePath('/management');

    return {
      success: true,
    };
  } catch (error) {
    console.error('重新加载协议类型失败：', error);
    return {
      success: false,
      error: error instanceof Error ? error.message : '重新加载协议类型失败',
    };
  }
}
