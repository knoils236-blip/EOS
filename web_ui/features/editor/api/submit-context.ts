'use server';

/**
 * Server Action that prepares the editor's "submit" dialogs for the selected task or protocol.
 */

import path from 'path';
import { and, eq } from 'drizzle-orm';
import { db } from '@/lib/db/client';
import { definitions } from '@/lib/db/schema';
import { getLabSpecs, getProtocolSpecs, getTaskSpecs, type LabSpec, type ProtocolSpec } from '@/lib/api/specs';
import { transformTaskSpec } from '@/lib/api/taskSpecTransform';
import type { TaskSpec } from '@/lib/types/protocol';

export interface SubmitContext {
  /** Task or protocol type as the orchestrator knows it, or null if the definition was never synced. */
  type: string | null;
  isLoaded: boolean;
  taskSpecs: Record<string, TaskSpec>;
  labSpecs: Record<string, LabSpec>;
  protocolSpecs: Record<string, ProtocolSpec>;
}

/**
 * Resolve an editor entity (package + folder name) to its type. Task folders are named differently from their
 * types (e.g. "matterix_capability" holds "Matterix Capability"), so the type comes from the synced definition.
 */
export async function getSubmitContext(
  entityType: 'tasks' | 'protocols',
  packageName: string,
  entityName: string
): Promise<SubmitContext> {
  const [rows, rawTaskSpecs, labSpecs, protocolSpecs] = await Promise.all([
    db
      .select()
      .from(definitions)
      .where(
        and(
          eq(definitions.type, entityType === 'tasks' ? 'task' : 'protocol'),
          eq(definitions.name, path.posix.basename(entityName)),
          eq(definitions.packageName, packageName)
        )
      ),
    getTaskSpecs(),
    getLabSpecs(false),
    getProtocolSpecs(true),
  ]);

  const row = rows[0];
  return {
    type: row ? (row.data as { type: string }).type : null,
    isLoaded: row?.isLoaded ?? false,
    taskSpecs: Object.fromEntries(
      Object.entries(rawTaskSpecs).map(([type, spec]) => [type, transformTaskSpec(type, spec)])
    ),
    labSpecs,
    protocolSpecs,
  };
}
