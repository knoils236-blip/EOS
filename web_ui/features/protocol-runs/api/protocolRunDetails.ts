'use server';

import {
  getProtocolRunByName,
  getTasksByProtocolRun,
  getTaskStatusesByProtocolRun,
  getTaskByName,
  ProtocolRunRow,
  TaskRow,
  TaskStatusRow,
} from '@/lib/db/queries';
import type { ProtocolRun, Task, TaskStatus, TaskDeviceConfig } from '@/lib/types/api';
import { requireRole } from '@/lib/auth/authz';
import { orchestratorGet } from '@/lib/api/orchestrator';
import { parseMatterixExecutionDetail, parseMatterixProtocolExecutionDetail } from '../utils/matterixExecution';
import type { MatterixExecutionDetail, MatterixProtocolExecutionDetail } from '../types/matterixExecution';

export interface TaskStatusInfo {
  name: string;
  type: string;
  status: TaskStatus;
}

function transformDbProtocolRun(exp: ProtocolRunRow): ProtocolRun {
  return {
    name: exp.name,
    type: exp.type,
    owner: exp.owner,
    priority: exp.priority,
    parameters: exp.parameters || {},
    meta: exp.meta ?? null,
    resume: exp.resume,
    status: exp.status as ProtocolRun['status'],
    error_message: exp.errorMessage ?? null,
    created_at: exp.createdAt.toISOString(),
    start_time: exp.startTime?.toISOString() ?? null,
    end_time: exp.endTime?.toISOString() ?? null,
  };
}

function transformDbTask(task: TaskRow): Task {
  return {
    name: task.name,
    protocol_run_name: task.protocolRunName,
    type: task.type,
    devices: task.devices as Record<string, TaskDeviceConfig>,
    input_parameters: task.inputParameters ?? null,
    input_resources: task.inputResources ?? null,
    output_parameters: task.outputParameters ?? null,
    output_resources: task.outputResources ?? null,
    output_file_names: task.outputFileNames ?? null,
    priority: task.priority,
    allocation_timeout: task.allocationTimeout,
    meta: task.meta,
    status: task.status as Task['status'],
    error_message: task.errorMessage ?? null,
    created_at: task.createdAt.toISOString(),
    start_time: task.startTime?.toISOString() ?? null,
    end_time: task.endTime?.toISOString() ?? null,
  };
}

export async function getProtocolRunDetails(protocolRunName: string): Promise<{
  protocolRun: ProtocolRun | null;
  tasks: Task[];
}> {
  await requireRole('VIEWER');
  try {
    const [protocolRunRow, taskRows] = await Promise.all([
      getProtocolRunByName(protocolRunName),
      getTasksByProtocolRun(protocolRunName),
    ]);

    if (!protocolRunRow) {
      return { protocolRun: null, tasks: [] };
    }

    return {
      protocolRun: transformDbProtocolRun(protocolRunRow),
      tasks: taskRows.map(transformDbTask),
    };
  } catch (error) {
    console.error('获取协议运行详情失败：', error);
    throw new Error('获取协议运行详情失败');
  }
}

export async function getProtocolRunTasks(protocolRunName: string): Promise<Task[]> {
  await requireRole('VIEWER');
  try {
    const taskRows = await getTasksByProtocolRun(protocolRunName);
    return taskRows.map(transformDbTask);
  } catch (error) {
    console.error('获取协议运行任务失败：', error);
    throw new Error('获取协议运行任务失败');
  }
}

export async function getTaskStatuses(protocolRunName: string): Promise<TaskStatusInfo[]> {
  await requireRole('VIEWER');
  try {
    const statusRows = await getTaskStatusesByProtocolRun(protocolRunName);
    return statusRows.map((row: TaskStatusRow) => ({
      name: row.name,
      type: row.type,
      status: row.status as TaskStatus,
    }));
  } catch (error) {
    console.error('获取任务状态失败：', error);
    throw new Error('获取任务状态失败');
  }
}

export async function getTaskDetails(taskName: string, protocolRunName: string): Promise<Task | null> {
  await requireRole('VIEWER');
  try {
    const taskRow = await getTaskByName(taskName, protocolRunName);
    if (!taskRow) return null;
    return transformDbTask(taskRow);
  } catch (error) {
    console.error('获取任务详情失败：', error);
    throw new Error('获取任务详情失败');
  }
}

export async function getMatterixExecutionDetail(
  protocolRunName: string,
  taskName: string
): Promise<MatterixExecutionDetail> {
  await requireRole('VIEWER');
  try {
    const response = await orchestratorGet(
      `/matterix/${encodeURIComponent(protocolRunName)}/${encodeURIComponent(taskName)}`
    );
    return parseMatterixExecutionDetail(response);
  } catch (error) {
    console.error('获取 Matterix 执行详情失败：', error);
    throw new Error(error instanceof Error ? error.message : '获取 Matterix 执行详情失败');
  }
}

export async function getMatterixProtocolExecutionDetail(
  protocolRunName: string
): Promise<MatterixProtocolExecutionDetail> {
  await requireRole('VIEWER');
  try {
    const response = await orchestratorGet(`/matterix/${encodeURIComponent(protocolRunName)}`);
    return parseMatterixProtocolExecutionDetail(response);
  } catch (error) {
    console.error('获取 Matterix 协议执行详情失败：', error);
    throw new Error(error instanceof Error ? error.message : '获取 Matterix 协议执行详情失败');
  }
}

export async function getProtocolRunWithTaskStatuses(protocolRunName: string): Promise<{
  protocolRun: ProtocolRun | null;
  taskStatuses: TaskStatusInfo[];
}> {
  await requireRole('VIEWER');
  try {
    const [protocolRunRow, statusRows] = await Promise.all([
      getProtocolRunByName(protocolRunName),
      getTaskStatusesByProtocolRun(protocolRunName),
    ]);

    if (!protocolRunRow) {
      return { protocolRun: null, taskStatuses: [] };
    }

    return {
      protocolRun: transformDbProtocolRun(protocolRunRow),
      taskStatuses: statusRows.map((row: TaskStatusRow) => ({
        name: row.name,
        type: row.type,
        status: row.status as TaskStatus,
      })),
    };
  } catch (error) {
    console.error('获取协议运行及任务状态失败：', error);
    throw new Error('获取协议运行及任务状态失败');
  }
}
