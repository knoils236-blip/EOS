'use server';

/**
 * Server Actions for Device Inspector
 */

import { revalidatePath } from 'next/cache';
import { orchestratorGet, orchestratorPost } from '@/lib/api/orchestrator';
import { requireRole } from '@/lib/auth/authz';
import type { DeviceIntrospection, FunctionCallResult } from '@/lib/types/device-inspector';
import type { DeviceReport, ActionResult } from '@/lib/types/management';

/**
 * Get device introspection (available functions and their metadata)
 */
export async function getDeviceIntrospection(labName: string, deviceName: string): Promise<DeviceIntrospection> {
  try {
    const response = (await orchestratorPost(
      `/rpc/${labName}/${deviceName}/get_available_functions`,
      {}
    )) as DeviceIntrospection;
    return response;
  } catch (error) {
    console.error('获取设备接口信息失败：', error);
    throw new Error('获取设备接口信息失败');
  }
}

/**
 * Call a device function via RPC
 */
export async function callDeviceFunction(
  labName: string,
  deviceName: string,
  functionName: string,
  parameters: Record<string, unknown>
): Promise<FunctionCallResult> {
  try {
    await requireRole('LAB_ADMIN');
    const result = await orchestratorPost(`/rpc/${labName}/${deviceName}/${functionName}`, parameters);

    return {
      success: true,
      result,
    };
  } catch (error) {
    console.error(`Failed to call device function ${functionName}:`, error);
    return {
      success: false,
      error: error instanceof Error ? error.message : '调用设备函数失败',
    };
  }
}

/**
 * Get device state/report
 */
export async function getDeviceState(labName: string, deviceName: string): Promise<DeviceReport> {
  try {
    const response = (await orchestratorGet(`/labs/${labName}/device/${deviceName}/report`)) as DeviceReport;
    return response;
  } catch (error) {
    console.error('获取设备状态失败：', error);
    throw new Error('获取设备状态失败');
  }
}

/**
 * Reload a specific device
 */
export async function reloadDevice(labName: string, deviceName: string): Promise<ActionResult> {
  try {
    await requireRole('LAB_ADMIN');
    await orchestratorPost(`/labs/${labName}/devices/reload`, {
      device_names: [deviceName],
    });

    // Revalidate the device inspector page to show updated status
    revalidatePath('/devices/inspect');

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

/**
 * Get device status
 */
export async function getDeviceStatus(
  labName: string,
  deviceName: string
): Promise<{ name: string; lab_name: string; status: string }> {
  try {
    const result = (await orchestratorPost(`/rpc/${labName}/${deviceName}/get_status`, {})) as {
      name: string;
      lab_name: string;
      status: { _name_: string; _value_: string };
    };

    // The status comes as an enum object, extract the value
    const status =
      typeof result.status === 'object' && '_value_' in result.status ? result.status._value_ : String(result.status);

    return {
      name: result.name,
      lab_name: result.lab_name,
      status,
    };
  } catch (error) {
    console.error('获取设备状态失败：', error);
    throw new Error('获取设备状态失败');
  }
}
