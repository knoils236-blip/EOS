import { getMaterials, getResources } from '@/features/resources/api/resources';
import { MaterialsTable } from '@/features/resources/components/MaterialsTable';
import { ResourcesTable } from '@/features/resources/components/ResourcesTable';

export const dynamic = 'force-dynamic';

export default async function ResourcesPage() {
  const [initialData, materials] = await Promise.all([getResources(), getMaterials()]);

  return (
    <div className="container mx-auto p-6">
      <ResourcesTable initialData={initialData} />
      <MaterialsTable containers={materials.containers} samples={materials.samples} />
    </div>
  );
}
