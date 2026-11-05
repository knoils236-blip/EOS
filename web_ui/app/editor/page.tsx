import { EditorClient } from '@/features/editor/components/EditorClient';
import { getTaskSpecs, getLabSpecs } from '@/lib/api/specs';
import { transformTaskSpec } from '@/lib/api/taskSpecTransform';
import { scanPackages } from '@/lib/filesystem/operations';
import type { EntityType } from '@/lib/types/filesystem';

export const dynamic = 'force-dynamic';

export const metadata = {
  title: '编辑器',
  description: '编辑 EOS 软件包、协议、设备、任务和实验室',
};

interface EditorPageProps {
  searchParams: Promise<{
    pkg?: string;
    type?: string;
    name?: string;
    mode?: string;
  }>;
}

export default async function EditorPage({ searchParams }: EditorPageProps) {
  // Await searchParams
  const params = await searchParams;

  // Fetch packages and specs in parallel
  const [packages, taskSpecs, labSpecs] = await Promise.all([scanPackages(), getTaskSpecs(), getLabSpecs()]);

  // Transform task specs for protocol editor (shared with /api/specs)
  const taskSpecsArray = Object.entries(taskSpecs).map(([type, spec]) => transformTaskSpec(type, spec));

  // Prepare initial selection from URL params
  const initialSelection =
    params.pkg && params.type && params.name
      ? {
          packageName: params.pkg,
          entityType: params.type as EntityType,
          entityName: params.name,
          editorMode: (params.mode as 'code' | 'visual') || undefined,
        }
      : undefined;

  return (
    <EditorClient
      initialPackages={packages}
      taskSpecs={taskSpecsArray}
      labSpecs={labSpecs}
      initialSelection={initialSelection}
    />
  );
}
