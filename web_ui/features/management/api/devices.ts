'use server';

/**
 * Server Actions for Device Management
 */

import { revalidatePath } from 'next/cache';
import { orchestratorGet, orchestratorPost } from '@/lib/api/orchestrator';
import { requireRole } from '@/lib/auth/authz';
import { db } from '@/lib/db/client';
import { devices } from '@/lib/db/schema';
import type { Device, DeviceReport, ActionResult } from '@/lib/types/management';

/**
 * Get all devices from the database
 */
export async function getDevices(): Promise<Device[]> {
  try {
    const results = await db.select().from(devices);

    return results.map((row) => ({
      name: row.name,
      lab_name: row.labName,
      type: row.type,
      computer: row.computer,
      status: row.status as 'ACTIVE' | 'INACTIVE',
      meta: (row.meta as Record<string, unknown>) || {},
    }));
  } catch (error) {
    console.error('获取设备失败：', error);
    throw new Error('获取数据库中的设备失败');
  }
}

/**
 * Get a report for a specific device
 */
export async function getDeviceReport(labName: string, deviceName: string): Promise<DeviceReport> {
  try {
    const response = (await orchestratorGet(`/labs/${labName}/device/${deviceName}/report`)) as DeviceReport;
    return response;
  } catch (error) {
    console.error('获取设备报告失败：', error);
    throw new Error('获取设备报告失败');
  }
}

/**
 * Reload specific devices in a lab
 */
export async function reloadDevices(labName: string, deviceNames: string[]): Promise<ActionResult> {
  try {
    await requireRole('LAB_ADMIN');
    await orchestratorPost(`/labs/${labName}/devices/reload`, {
      device_names: deviceNames,
    });

    // Revalidate the management page to show updated status
    revalidatePath('/management');

    return {
      success: true,
    };
  } catch (error) {
    console.error('重新加载设备失败：', error);
    return {
      success: false,
      error: error instanceof Error ? error.message : '重新加载设备失败',
    };
  }
}
