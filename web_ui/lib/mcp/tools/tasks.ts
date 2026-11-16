import { z } from 'zod/v3';
import type { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { getAllTasks, getTaskAttempts, getTaskByName, getTasksByProtocolRun } from '@/lib/db/queries';
import { orchestratorPost } from '@/lib/api/orchestrator';
import { submissionMessage } from './sdl';
import { formatDate, formatDuration, formatPagination, textResult, errorResult } from '../helpers/format';

export function registerTaskTools(server: McpServer) {
  server.registerTool(
    'list_tasks',
    {
      title: 'List Tasks',
      description: 'List recent tasks with pagination. Returns name, type, status, protocol run, and timing info.',
      inputSchema: {
        limit: z.number().int().min(1).max(200).default(20).describe('Max rows to return'),
        offset: z.number().int().min(0).default(0).describe('Number of rows to skip'),
      },
    },
    async ({ limit, offset }) => {
      const result = await getAllTasks({ limit, offset });
      const lines = result.data.map((t) => {
        const dur = formatDuration(t.startTime, t.endTime);
        return `• ${t.name} [${t.status}] type=${t.type} exp=${t.protocolRunName ?? 'standalone'} dur=${dur}`;
      });
      const header = formatPagination(result.total, result.limit, result.offset);
      return textResult(`${header}\n\n${lines.join('\n')}`);
    }
  );

  server.registerTool(
    'get_task',
    {
      title: 'Get Task',
      description: 'Get full details for a specific task including inputs, outputs, devices, and timing.',
      inputSchema: {
        task_name: z.string().describe('Task name'),
        protocol_run_name: z.string().describe('ProtocolRun name the task belongs to'),
      },
    },
    async ({ task_name, protocol_run_name }) => {
      const task = await getTaskByName(task_name, protocol_run_name);
      if (!task) return errorResult(`Task "${task_name}" not found in protocol run "${protocol_run_name}"`);

      const lines = [
        `Task: ${task.name}`,
        `Type: ${task.type}`,
        `Status: ${task.status}`,
        `Priority: ${task.priority}`,
        `Protocol Run: ${task.protocolRunName ?? 'standalone'}`,
        `Created: ${formatDate(task.createdAt)}`,
        `Started: ${formatDate(task.startTime)}`,
        `Ended: ${formatDate(task.endTime)}`,
        `Duration: ${formatDuration(task.startTime, task.endTime)}`,
        `Devices: ${JSON.stringify(task.devices)}`,
      ];
      if (task.inputParameters) lines.push(`Input Parameters: ${JSON.stringify(task.inputParameters, null, 2)}`);
      if (task.outputParameters) lines.push(`Output Parameters: ${JSON.stringify(task.outputParameters, null, 2)}`);
      if (task.inputResources) lines.push(`Input Resources: ${JSON.stringify(task.inputResources)}`);
      if (task.outputResources) lines.push(`Output Resources: ${JSON.stringify(task.outputResources)}`);
      if (task.outputFileNames?.length) lines.push(`Output Files: ${task.outputFileNames.join(', ')}`);
      for (const a of await getTaskAttempts(protocol_run_name, task_name)) {
        const recovery = a.needsManualRecovery ? ' NEEDS MANUAL RECOVERY (not relaunched automatically)' : '';
        lines.push(
          `Attempt ${a.attemptNumber}: ${a.outcome} stop_confirmed=${a.stopConfirmed} device_available=${a.deviceAvailable}${recovery}${a.error ? ` error=${a.error}` : ''}`
        );
      }

      return textResult(lines.join('\n'));
    }
  );

  server.registerTool(
    'get_tasks_by_protocol_run',
    {
      title: 'Get Tasks by ProtocolRun',
      description: 'Get all tasks belonging to a specific protocol run.',
      inputSchema: {
        protocol_run_name: z.string().describe('ProtocolRun name'),
      },
    },
    async ({ protocol_run_name }) => {
      const tasks = await getTasksByProtocolRun(protocol_run_name);
      if (tasks.length === 0) return textResult(`No tasks found for protocol run "${protocol_run_name}"`);

      const lines = tasks.map((t) => {
        const dur = formatDuration(t.startTime, t.endTime);
        return `• ${t.name} [${t.status}] type=${t.type} dur=${dur}`;
      });
      return textResult(`${tasks.length} task(s) in protocol run "${protocol_run_name}":\n\n${lines.join('\n')}`);
    }
  );

  server.registerTool(
    'submit_task',
    {
      title: 'Submit Task',
      description:
        'Submit a standalone task. It starts at once unless a lab of its devices requires approval; then it waits for a lab admin in the web UI (see list_approval_requests).',
      inputSchema: {
        name: z.string().describe('Unique task name'),
        type: z.string().describe('Task type (e.g. "Noop", "GoToAbsolutePosition")'),
        devices: z
          .record(
            z.object({
              lab_name: z.string(),
              name: z.string().describe('Device name in the lab'),
              hold: z.boolean().optional().describe('Keep the device allocated after the task'),
            })
          )
          .describe('Device assignments: { device_role: { lab_name, name, hold? } }'),
        parameters: z.record(z.unknown()).optional().describe('Task input parameters'),
        protocol_run_name: z.string().optional().describe('Parent protocol run name'),
        reason: z.string().optional().describe('Why this task is worth running; shown to the approver'),
      },
    },
    async ({ name, type, devices, parameters, protocol_run_name, reason }) => {
      try {
        const body: Record<string, unknown> = { name, type, devices };
        if (parameters) body.input_parameters = parameters;
        if (protocol_run_name) body.protocol_run_name = protocol_run_name;
        const result = await orchestratorPost('/approvals/tasks', { submission: body, reason });
        return textResult(submissionMessage('Task', result));
      } catch (e) {
        return errorResult(`Failed to submit task: ${e instanceof Error ? e.message : String(e)}`);
      }
    }
  );

  server.registerTool(
    'cancel_task',
    {
      title: 'Cancel Task',
      description: 'Cancel a running task. Give protocol_run_name for a task inside a protocol run.',
      inputSchema: {
        task_name: z.string().describe('Name of the task to cancel'),
        protocol_run_name: z.string().optional().describe('ProtocolRun the task belongs to; omit for standalone tasks'),
      },
    },
    async ({ task_name, protocol_run_name }) => {
      try {
        const query = protocol_run_name ? `?protocol_run_name=${encodeURIComponent(protocol_run_name)}` : '';
        await orchestratorPost(`/tasks/${encodeURIComponent(task_name)}/cancel${query}`);
        return textResult(`Cancellation requested for task "${task_name}".`);
      } catch (e) {
        return errorResult(`Failed to cancel task: ${e instanceof Error ? e.message : String(e)}`);
      }
    }
  );
}
