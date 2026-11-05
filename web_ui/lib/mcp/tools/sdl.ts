import { z } from 'zod/v3';
import type { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { orchestratorGet, orchestratorPost } from '@/lib/api/orchestrator';
import { textResult, errorResult } from '../helpers/format';

/**
 * Tools for an AI agent that runs the self-driving-lab loop: read what the labs can do, design and save a
 * protocol, run it, then read the evidence and record its reasoning in the lab notebook.
 */

const NOTEBOOK_KINDS = ['hypothesis', 'design', 'observation', 'interpretation', 'decision', 'note'] as const;

export function formatErrors(errors: Array<{ task: string | null; message: string }>): string {
  return errors.map((e) => `- ${e.task ? `[${e.task}] ` : ''}${e.message}`).join('\n');
}

export function submissionMessage(what: string, result: unknown): string {
  const r = result as { status?: string; approval_id?: number; real_labs?: string[] };
  if (r.status === 'pending_approval') {
    return `${what} is waiting for human approval (request #${r.approval_id}) because it uses labs that are not simulated: ${r.real_labs?.join(', ')}. Check it later with list_approval_requests.`;
  }
  return `${what} submitted.\n${JSON.stringify(result, null, 2)}`;
}

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function registerSdlTools(server: McpServer) {
  server.registerTool(
    'get_lab_capabilities',
    {
      title: 'Get Lab Capabilities',
      description:
        'What each lab can physically do: its scene operations (ID, meaning, acceptance criteria, ready-to-use task template, parameter ranges) and scene notes (initial state, required order, single-use consumables). Read this before designing a protocol; build tasks from the templates.',
      inputSchema: {
        labs: z.array(z.string()).optional().describe('Lab names to include (default: all labs with scenes)'),
      },
    },
    async ({ labs }) => {
      try {
        const scenes = (await orchestratorGet('/labs/editor-scenes')) as Array<Record<string, unknown>>;
        const selected = labs?.length ? scenes.filter((s) => labs.includes(s.lab_name as string)) : scenes;
        if (selected.length === 0) return textResult('No scene capabilities are registered for these labs.');
        return textResult(JSON.stringify(selected, null, 2));
      } catch (e) {
        return errorResult(`Failed to read lab capabilities: ${message(e)}`);
      }
    }
  );

  server.registerTool(
    'save_protocol',
    {
      title: 'Save Protocol',
      description:
        "Validate a protocol (specs plus each device's order checks), write it as protocols/<type>/protocol.yml in a package, and load it when its labs are loaded so it can be submitted. Fix all reported errors and call again. Never overwrites unless overwrite is true.",
      inputSchema: {
        package: z.string().describe('Package to save into, e.g. matterix_sim'),
        yaml: z.string().describe('Complete protocol YAML; its type becomes the folder name (lowercase snake_case)'),
        overwrite: z.boolean().optional().describe('Replace an existing protocol of the same type in this package'),
      },
    },
    async ({ package: packageName, yaml, overwrite }) => {
      try {
        const result = (await orchestratorPost('/protocols/save', {
          package: packageName,
          protocol_yaml: yaml,
          overwrite: overwrite ?? false,
        })) as {
          saved: boolean;
          loaded: boolean;
          protocol_type?: string;
          errors: Array<{ task: string | null; message: string }>;
        };
        if (!result.saved) return errorResult(`Not saved, validation failed:\n${formatErrors(result.errors)}`);
        return textResult(
          result.loaded
            ? `Saved and loaded protocol "${result.protocol_type}"; it can be submitted now.`
            : `Saved protocol "${result.protocol_type}". Its labs are not loaded yet, so a lab admin must load it before it can run.`
        );
      } catch (e) {
        return errorResult(`Failed to save protocol: ${message(e)}`);
      }
    }
  );

  server.registerTool(
    'get_campaign_decision',
    {
      title: 'Get Campaign Decision Evidence',
      description:
        'Decision evidence of a campaign: rounds with inputs, objectives, eligibility and data source (mock, simulation or measurement), best values per source, and stop reasons. Use it to judge progress and decide the next experiment; never compare best values across sources.',
      inputSchema: {
        campaign: z.string().describe('Campaign name'),
      },
    },
    async ({ campaign }) => {
      try {
        const decision = await orchestratorGet(`/campaigns/${encodeURIComponent(campaign)}/decision`);
        return textResult(JSON.stringify(decision, null, 2));
      } catch (e) {
        return errorResult(`Failed to read decision evidence: ${message(e)}`);
      }
    }
  );

  server.registerTool(
    'add_notebook_entry',
    {
      title: 'Add Lab Notebook Entry',
      description:
        'Record reasoning in the lab notebook so people and later sessions can follow it: a hypothesis before experimenting, the design rationale of a protocol or campaign, an observation, an interpretation of results, or a decision about what to do next. Link it to a campaign or protocol run when it is about one.',
      inputSchema: {
        kind: z.enum(NOTEBOOK_KINDS),
        content: z.string().min(1).describe('The entry; separate observed facts from inferences'),
        campaign_name: z.string().optional(),
        protocol_run_name: z.string().optional(),
      },
    },
    async (entry) => {
      try {
        const saved = (await orchestratorPost('/notebook/', entry)) as { id: number };
        return textResult(`Notebook entry #${saved.id} recorded.`);
      } catch (e) {
        return errorResult(`Failed to record notebook entry: ${message(e)}`);
      }
    }
  );

  server.registerTool(
    'list_notebook_entries',
    {
      title: 'List Lab Notebook Entries',
      description:
        'Read the lab notebook, oldest first: hypotheses, designs and decisions recorded earlier. Read it before planning to continue prior work.',
      inputSchema: {
        campaign_name: z.string().optional(),
        protocol_run_name: z.string().optional(),
        kind: z.enum(NOTEBOOK_KINDS).optional(),
        limit: z.number().int().min(1).max(200).optional().describe('Newest entries to return (default 50)'),
      },
    },
    async (filters) => {
      try {
        const query = new URLSearchParams(
          Object.entries(filters).flatMap(([key, value]) => (value === undefined ? [] : [[key, String(value)]]))
        );
        const entries = await orchestratorGet(`/notebook/?${query}`);
        return textResult(JSON.stringify(entries, null, 2));
      } catch (e) {
        return errorResult(`Failed to read the notebook: ${message(e)}`);
      }
    }
  );

  server.registerTool(
    'list_approval_requests',
    {
      title: 'List Approval Requests',
      description:
        'Submissions to labs that are not simulated wait for a person to approve them in the web UI. List them to see whether yours was approved (and started), rejected with a note, or is still pending.',
      inputSchema: {
        status: z.enum(['pending', 'approved', 'rejected']).optional(),
      },
    },
    async ({ status }) => {
      try {
        const requests = await orchestratorGet(`/approvals/${status ? `?status=${status}` : ''}`);
        return textResult(JSON.stringify(requests, null, 2));
      } catch (e) {
        return errorResult(`Failed to read approval requests: ${message(e)}`);
      }
    }
  );
}
