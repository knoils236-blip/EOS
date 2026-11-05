'use server';

import { orchestratorGet } from '@/lib/api/orchestrator';
import { requireRole } from '@/lib/auth/authz';
import type { EditorScene } from '@/features/protocol-editor/lib/scenePresets';

export async function getEditorScenes(): Promise<EditorScene[]> {
  await requireRole('VIEWER');
  return (await orchestratorGet('/labs/editor-scenes')) as EditorScene[];
}
