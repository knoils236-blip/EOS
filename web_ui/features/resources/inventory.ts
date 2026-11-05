export type ResourceSource = 'simulation' | 'hardware' | 'manual';

export interface ConsumableMeta {
  quantity: number;
  unit: string;
  lowStockThreshold: number;
  label: string;
  source: ResourceSource;
  updatedAt: string | null;
}

const RESOURCE_SOURCES: readonly ResourceSource[] = ['simulation', 'hardware', 'manual'];

const SOURCE_LABELS: Record<ResourceSource, string> = {
  simulation: '仿真',
  hardware: '硬件',
  manual: '手动',
};

export function isConsumableResource(meta: Record<string, unknown>): boolean {
  return meta.consumable === true;
}

export function readConsumableMeta(meta: Record<string, unknown>): ConsumableMeta | null {
  if (!isConsumableResource(meta)) return null;

  const { quantity, unit, low_stock_threshold, label, source, updated_at } = meta;
  if (
    !Number.isInteger(quantity) ||
    (quantity as number) < 0 ||
    typeof unit !== 'string' ||
    unit.trim() === '' ||
    !Number.isInteger(low_stock_threshold) ||
    typeof label !== 'string' ||
    label.trim() === '' ||
    !RESOURCE_SOURCES.includes(source as ResourceSource) ||
    (updated_at !== undefined && (typeof updated_at !== 'string' || Number.isNaN(Date.parse(updated_at))))
  ) {
    return null;
  }

  return {
    quantity: quantity as number,
    unit,
    lowStockThreshold: low_stock_threshold as number,
    label,
    source: source as ResourceSource,
    updatedAt: typeof updated_at === 'string' && !Number.isNaN(Date.parse(updated_at)) ? updated_at : null,
  };
}

export function isLowStock(meta: ConsumableMeta): boolean {
  return meta.quantity <= meta.lowStockThreshold;
}

export function formatInventoryQuantity(meta: ConsumableMeta): string {
  return `${meta.quantity} ${meta.unit}`;
}

export function resourceSourceLabel(source: ResourceSource): string {
  return SOURCE_LABELS[source];
}
