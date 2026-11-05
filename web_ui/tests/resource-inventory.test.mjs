import assert from 'node:assert/strict';
import { test } from 'node:test';
import ts from 'typescript';
import { registerHooks } from 'node:module';

registerHooks({
  load(url, context, nextLoad) {
    const result = nextLoad(url, url.endsWith('.ts') ? { ...context, format: 'module' } : context);
    if (!url.endsWith('.ts') || url.includes('/node_modules/')) return result;
    const source = ts.transpileModule(result.source.toString(), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    return { ...result, source, format: 'commonjs' };
  },
});

const { formatInventoryQuantity, isConsumableResource, isLowStock, readConsumableMeta, resourceSourceLabel } =
  await import('../features/resources/inventory.ts');

const validMeta = {
  consumable: true,
  quantity: 3,
  unit: '盒',
  low_stock_threshold: 3,
  label: '移液枪头',
  source: 'manual',
  updated_at: '2026-11-03T10:00:00Z',
};

test('consumable metadata is parsed for inventory display', () => {
  const parsed = readConsumableMeta(validMeta);

  assert.equal(isConsumableResource(validMeta), true);
  assert.deepEqual(parsed, {
    quantity: 3,
    unit: '盒',
    lowStockThreshold: 3,
    label: '移液枪头',
    source: 'manual',
    updatedAt: '2026-11-03T10:00:00Z',
  });
  assert.equal(formatInventoryQuantity(parsed), '3 盒');
  assert.equal(isLowStock(parsed), true);
  assert.equal(resourceSourceLabel(parsed.source), '手动');
});

test('non-consumables have no inventory state', () => {
  assert.equal(isConsumableResource({ consumable: false }), false);
  assert.equal(readConsumableMeta({ quantity: 1 }), null);
});

test('initial configured stock displays before its first usage event', () => {
  const { updated_at, ...initialMeta } = validMeta;
  const parsed = readConsumableMeta(initialMeta);
  assert.equal(parsed.quantity, 3);
  assert.equal(parsed.updatedAt, null);
});

test('invalid consumable metadata is rejected instead of silently recovered', () => {
  assert.equal(readConsumableMeta({ ...validMeta, quantity: -1 }), null);
  assert.equal(readConsumableMeta({ ...validMeta, low_stock_threshold: 1.5 }), null);
  assert.equal(readConsumableMeta({ ...validMeta, source: 'device' }), null);
  assert.equal(readConsumableMeta({ ...validMeta, updated_at: 'not-a-time' }), null);
});
