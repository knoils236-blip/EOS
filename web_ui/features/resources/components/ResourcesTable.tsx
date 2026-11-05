'use client';

import * as React from 'react';
import { Plus, RotateCcw, X } from 'lucide-react';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import { DataTable, DataTableColumnDef } from '@/components/data-table/DataTable';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { ErrorBox } from '@/components/ui/ErrorBox';
import { Input } from '@/components/ui/Input';
import { JsonDisplay } from '@/components/ui/JsonDisplay';
import { RefreshControl } from '@/components/ui/RefreshControl';
import { ConfirmDialog } from '@/components/dialogs/ConfirmDialog';
import { useServerTable } from '@/hooks/useServerTable';
import { useOrchestratorConnected } from '@/contexts/OrchestratorStatusContext';
import { useUser } from '@/contexts/UserContext';
import { adjustConsumable, getResources, resetResources } from '../api/resources';
import {
  formatInventoryQuantity,
  isConsumableResource,
  isLowStock,
  readConsumableMeta,
  resourceSourceLabel,
} from '../inventory';
import type { PaginatedResult, ResourceRow } from '@/lib/db/queries';

interface ResourcesTableProps {
  initialData: PaginatedResult<ResourceRow>;
}

export function ResourcesTable({ initialData }: ResourcesTableProps) {
  const { isConnected: _isConnected } = useOrchestratorConnected();
  const { canSubmit } = useUser();
  const [mounted, setMounted] = React.useState(false);
  React.useEffect(() => setMounted(true), []);
  const isConnected = mounted ? _isConnected : true;

  const [pollingInterval, setPollingInterval] = React.useState(5000);
  const [isResetting, setIsResetting] = React.useState(false);
  const [isAdjusting, setIsAdjusting] = React.useState(false);
  const [selectedResource, setSelectedResource] = React.useState<ResourceRow | null>(null);
  const [detailPanelOpen, setDetailPanelOpen] = React.useState(false);
  const [selectedRows, setSelectedRows] = React.useState<ResourceRow[]>([]);
  const [confirmDialog, setConfirmDialog] = React.useState<{
    title: string;
    message: string;
    resourceNames: string[];
  } | null>(null);
  const [resetError, setResetError] = React.useState<string | null>(null);
  const [resetNotice, setResetNotice] = React.useState<string | null>(null);
  const [adjustAmount, setAdjustAmount] = React.useState('1');
  const [adjustEventId, setAdjustEventId] = React.useState<string | null>(null);
  const [adjustError, setAdjustError] = React.useState<string | null>(null);

  const serverTable = useServerTable({
    fetchFn: getResources,
    initialData,
  });

  React.useEffect(() => {
    if (pollingInterval === 0) return;
    const intervalId = setInterval(() => {
      serverTable.refresh();
    }, pollingInterval);
    return () => clearInterval(intervalId);
  }, [pollingInterval, serverTable.refresh]); // eslint-disable-line react-hooks/exhaustive-deps

  // Keep detail panel in sync with refreshed data
  React.useEffect(() => {
    if (selectedResource) {
      const updated = serverTable.data.find((r) => r.name === selectedResource.name);
      if (updated) setSelectedResource(updated);
    }
  }, [serverTable.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleReset = React.useCallback(
    async (resourceNames: string[]) => {
      setIsResetting(true);
      try {
        const result = await resetResources(resourceNames);
        if (!result.success) {
          setResetError(result.error ?? '重置资源失败');
          return;
        }
        await serverTable.refresh();
      } finally {
        setIsResetting(false);
      }
    },
    [serverTable]
  );

  const openResourceDetails = (resource: ResourceRow) => {
    setSelectedResource(resource);
    setAdjustAmount('1');
    setAdjustEventId(isConsumableResource(resource.meta) ? crypto.randomUUID() : null);
    setAdjustError(null);
    setDetailPanelOpen(true);
  };

  const requestReset = (title: string, resources: ResourceRow[]) => {
    const resetResources = resources.filter((resource) => !isConsumableResource(resource.meta));
    const excludedCount = resources.length - resetResources.length;

    if (excludedCount > 0) {
      setResetNotice(`已跳过 ${excludedCount} 个耗材；耗材请通过详情补充库存。`);
    }
    if (resetResources.length === 0) return;

    setConfirmDialog({
      title,
      message: `确定将 ${resetResources.length} 个非耗材资源的元数据恢复为默认值吗？`,
      resourceNames: resetResources.map((resource) => resource.name),
    });
  };

  const handleAdjustConsumable = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedResource || !isConsumableResource(selectedResource.meta)) return;

    const amount = Number(adjustAmount);
    if (!Number.isInteger(amount) || amount < 1) {
      setAdjustError('补充数量必须是大于 0 的整数');
      return;
    }

    const eventId = adjustEventId ?? crypto.randomUUID();
    if (!adjustEventId) setAdjustEventId(eventId);

    setIsAdjusting(true);
    setAdjustError(null);
    try {
      const result = await adjustConsumable(selectedResource.name, amount, eventId);
      if (!result.success) {
        setAdjustError(result.error ?? '补充耗材失败');
      } else {
        setAdjustAmount('1');
        setAdjustEventId(crypto.randomUUID());
      }
      await serverTable.refresh();
    } catch (error) {
      setAdjustError(error instanceof Error ? error.message : '补充耗材失败');
    } finally {
      setIsAdjusting(false);
    }
  };

  const labs = React.useMemo(
    () => [...new Set(serverTable.data.map((r) => r.lab).filter(Boolean))].sort() as string[],
    [serverTable.data]
  );

  const columns: DataTableColumnDef<ResourceRow>[] = [
    {
      accessorKey: 'name',
      header: '名称',
      cell: ({ row }) => <div className="font-medium">{row.getValue('name')}</div>,
      enableColumnFilter: true,
      filterType: 'text',
    },
    {
      id: 'human_name',
      header: '显示名称',
      cell: ({ row }) => {
        const label = row.original.meta.label;
        return typeof label === 'string' && label.trim() ? label : <span className="text-gray-400">—</span>;
      },
      enableSorting: false,
    },
    {
      accessorKey: 'type',
      header: '类型',
      enableColumnFilter: true,
      filterType: 'text',
    },
    {
      accessorKey: 'lab',
      header: '实验室',
      cell: ({ row }) => {
        const lab = row.getValue('lab') as string | null;
        return lab ?? <span className="text-gray-400">—</span>;
      },
      enableColumnFilter: true,
      filterType: 'text',
    },
    {
      id: 'quantity',
      header: '余量',
      cell: ({ row }) => {
        const meta = readConsumableMeta(row.original.meta);
        return meta ? formatInventoryQuantity(meta) : <span className="text-gray-400">—</span>;
      },
      enableSorting: false,
    },
    {
      id: 'low_stock',
      header: '低库存状态',
      cell: ({ row }) => {
        const meta = readConsumableMeta(row.original.meta);
        if (!meta) return <span className="text-gray-400">—</span>;
        return isLowStock(meta) ? <Badge variant="warning">库存低</Badge> : <Badge variant="success">正常</Badge>;
      },
      enableSorting: false,
    },
    {
      id: 'source',
      header: '数据来源',
      cell: ({ row }) => {
        const meta = readConsumableMeta(row.original.meta);
        return meta ? resourceSourceLabel(meta.source) : <span className="text-gray-400">—</span>;
      },
      enableSorting: false,
    },
    {
      id: 'meta',
      header: '元数据',
      cell: ({ row }) => {
        const text = JSON.stringify(row.original.meta);
        return (
          <code
            className="text-xs bg-gray-100 dark:bg-slate-800 px-2 py-1 rounded text-gray-700 dark:text-gray-300 block max-w-[300px] truncate"
            title={text}
          >
            {text}
          </code>
        );
      },
      enableSorting: false,
    },
    {
      id: 'actions',
      header: '',
      cell: ({ row }) => {
        if (isConsumableResource(row.original.meta)) {
          return (
            <Button
              variant="ghost"
              size="sm"
              disabled={isAdjusting || isResetting}
              onClick={(event) => {
                event.stopPropagation();
                openResourceDetails(row.original);
              }}
            >
              <Plus className="w-3.5 h-3.5" />
              补充
            </Button>
          );
        }

        return (
          <Button
            variant="ghost"
            size="sm"
            disabled={!isConnected || isResetting || !canSubmit}
            onClick={(event) => {
              event.stopPropagation();
              requestReset(`重置 ${row.original.name}`, [row.original]);
            }}
          >
            <RotateCcw className="w-3.5 h-3.5" />
            重置
          </Button>
        );
      },
      enableSorting: false,
    },
  ];

  const bulkActions = (
    <Button
      variant="outline"
      size="sm"
      disabled={!isConnected || isResetting || !canSubmit || selectedRows.length === 0}
      onClick={() => requestReset(`重置 ${selectedRows.length} 个资源`, selectedRows)}
    >
      <RotateCcw className="w-3.5 h-3.5" />
      重置所选项
    </Button>
  );

  const toolbarActions = (
    <div className="flex items-center gap-2">
      {labs.length > 1 ? (
        <DropdownMenu.Root>
          <DropdownMenu.Trigger asChild>
            <Button variant="outline" size="sm" disabled={!isConnected || isResetting || !canSubmit}>
              重置实验室
            </Button>
          </DropdownMenu.Trigger>
          <DropdownMenu.Portal>
            <DropdownMenu.Content
              className="min-w-[160px] bg-white dark:bg-slate-800 rounded-md shadow-lg border border-gray-200 dark:border-slate-700 p-1 z-50"
              sideOffset={4}
            >
              {labs.map((lab) => (
                <DropdownMenu.Item
                  key={lab}
                  className="text-sm px-3 py-2 rounded cursor-pointer hover:bg-gray-100 dark:hover:bg-slate-700 text-gray-900 dark:text-gray-100 outline-none"
                  onSelect={() => {
                    const labResources = serverTable.data.filter((r) => r.lab === lab);
                    requestReset(`重置实验室 ${lab} 的资源`, labResources);
                  }}
                >
                  {lab}
                </DropdownMenu.Item>
              ))}
            </DropdownMenu.Content>
          </DropdownMenu.Portal>
        </DropdownMenu.Root>
      ) : labs.length === 1 ? (
        <Button
          variant="outline"
          size="sm"
          disabled={!isConnected || isResetting || !canSubmit}
          onClick={() => {
            const labResources = serverTable.data.filter((r) => r.lab === labs[0]);
            requestReset(`重置实验室 ${labs[0]} 的资源`, labResources);
          }}
        >
          重置实验室
        </Button>
      ) : null}

      <Button
        variant="primary"
        size="sm"
        disabled={!isConnected || isResetting || !canSubmit || serverTable.data.length === 0}
        onClick={() => requestReset('重置所有资源', serverTable.data)}
      >
        全部重置
      </Button>

      <RefreshControl
        pollingInterval={pollingInterval}
        onIntervalChange={setPollingInterval}
        onRefresh={serverTable.refresh}
        isRefreshing={serverTable.isLoading}
      />
    </div>
  );

  const selectedIsConsumable = selectedResource ? isConsumableResource(selectedResource.meta) : false;
  const selectedConsumableMeta = selectedResource ? readConsumableMeta(selectedResource.meta) : null;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-3xl font-bold text-gray-900 dark:text-white">资源</h1>
        <p className="text-gray-600 dark:text-gray-400 mt-1">查看和管理资源库存</p>
      </div>

      {resetNotice && (
        <div
          role="status"
          className="flex items-start justify-between gap-3 rounded-md border border-yellow-200 bg-yellow-50 px-3 py-2 text-sm text-yellow-900 dark:border-yellow-900 dark:bg-yellow-900/20 dark:text-yellow-200"
        >
          <span>{resetNotice}</span>
          <button
            type="button"
            className="rounded p-0.5 hover:bg-yellow-100 dark:hover:bg-yellow-900/40"
            onClick={() => setResetNotice(null)}
          >
            <X className="h-4 w-4" />
            <span className="sr-only">关闭提示</span>
          </button>
        </div>
      )}

      <div className="flex gap-4">
        <div className={`space-y-4 ${detailPanelOpen ? 'flex-1' : 'w-full'}`}>
          <DataTable
            columns={columns}
            data={serverTable.data}
            searchPlaceholder="搜索资源…"
            onRowClick={mounted ? openResourceDetails : undefined}
            enableRowSelection={mounted}
            onSelectionChange={setSelectedRows}
            bulkActions={bulkActions}
            toolbarActions={toolbarActions}
            manualPagination
            totalRows={serverTable.totalRows}
            pageIndex={serverTable.pageIndex}
            pageSize={serverTable.pageSize}
            onPaginationChange={serverTable.onPaginationChange}
            onSortingChange={serverTable.onSortingChange}
            onColumnFiltersChange={serverTable.onColumnFiltersChange}
            onGlobalFilterChange={serverTable.onGlobalFilterChange}
          />
        </div>

        {detailPanelOpen && selectedResource && (
          <div className="w-96 border-l border-gray-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-6 sticky top-4 max-h-[calc(100vh-2rem)] overflow-y-auto self-start">
            <div className="flex items-start justify-between mb-6">
              <div>
                <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
                  {selectedConsumableMeta?.label ?? selectedResource.name}
                </h2>
                <p className="text-sm text-gray-500 dark:text-gray-400">
                  {selectedConsumableMeta ? selectedResource.name : '资源详情'}
                </p>
              </div>
              <button
                onClick={() => setDetailPanelOpen(false)}
                className="rounded-sm opacity-70 ring-offset-white dark:ring-offset-slate-900 transition-opacity hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-blue-600 focus:ring-offset-2 text-gray-900 dark:text-gray-400"
              >
                <X className="h-4 w-4" />
                <span className="sr-only">关闭</span>
              </button>
            </div>

            <div className="space-y-6">
              <div className="space-y-3">
                <div>
                  <div className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">
                    类型
                  </div>
                  <div className="mt-1 text-sm text-gray-900 dark:text-gray-100">{selectedResource.type}</div>
                </div>

                <div>
                  <div className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">
                    实验室
                  </div>
                  <div className="mt-1 text-sm text-gray-900 dark:text-gray-100">{selectedResource.lab ?? '—'}</div>
                </div>

                {selectedConsumableMeta && (
                  <>
                    <div>
                      <div className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">
                        余量
                      </div>
                      <div className="mt-1 text-sm text-gray-900 dark:text-gray-100">
                        {formatInventoryQuantity(selectedConsumableMeta)}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">
                        低库存阈值
                      </div>
                      <div className="mt-1 text-sm text-gray-900 dark:text-gray-100">
                        {selectedConsumableMeta.lowStockThreshold} {selectedConsumableMeta.unit}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">
                        数据来源
                      </div>
                      <div className="mt-1 text-sm text-gray-900 dark:text-gray-100">
                        {resourceSourceLabel(selectedConsumableMeta.source)}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">
                        更新时间
                      </div>
                      <div className="mt-1 text-sm text-gray-900 dark:text-gray-100">
                        {selectedConsumableMeta.updatedAt
                          ? new Date(selectedConsumableMeta.updatedAt).toLocaleString('zh-CN')
                          : '初始库存'}
                      </div>
                    </div>
                  </>
                )}
              </div>

              {Object.keys(selectedResource.meta).length > 0 && (
                <div className="border-t border-gray-200 dark:border-slate-700 pt-4">
                  <JsonDisplay data={selectedResource.meta} label="元数据" />
                </div>
              )}

              {selectedIsConsumable ? (
                <div className="border-t border-gray-200 dark:border-slate-700 pt-4">
                  {selectedConsumableMeta ? (
                    <form className="space-y-3" onSubmit={handleAdjustConsumable}>
                      <div>
                        <label
                          htmlFor="consumable-adjust-amount"
                          className="text-sm font-medium text-gray-900 dark:text-white"
                        >
                          补充数量
                        </label>
                        <Input
                          id="consumable-adjust-amount"
                          className="mt-1"
                          type="number"
                          min={1}
                          step={1}
                          inputMode="numeric"
                          value={adjustAmount}
                          disabled={!isConnected || isAdjusting || !canSubmit}
                          onChange={(event) => setAdjustAmount(event.target.value)}
                        />
                        <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                          单位为“{selectedConsumableMeta.unit}”，请输入整数。
                        </p>
                      </div>
                      <Button
                        type="submit"
                        variant="primary"
                        size="sm"
                        className="w-full"
                        disabled={!isConnected || isAdjusting || !canSubmit}
                      >
                        <Plus className="w-3.5 h-3.5" />
                        {isAdjusting ? '正在补充…' : '补充'}
                      </Button>
                    </form>
                  ) : (
                    <ErrorBox error="耗材库存元数据不完整，无法补充或重置；请检查 quantity、unit、low_stock_threshold、label 和 source。" />
                  )}
                  {adjustError && <ErrorBox error={adjustError} className="mt-3" />}
                </div>
              ) : (
                <div className="border-t border-gray-200 dark:border-slate-700 pt-4">
                  <Button
                    variant="outline"
                    size="sm"
                    className="w-full"
                    disabled={!isConnected || isResetting || !canSubmit}
                    onClick={() => requestReset(`重置 ${selectedResource.name}`, [selectedResource])}
                  >
                    <RotateCcw className="w-3.5 h-3.5" />
                    恢复默认值
                  </Button>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      <ConfirmDialog
        isOpen={confirmDialog !== null}
        onClose={() => setConfirmDialog(null)}
        onConfirm={() => {
          if (confirmDialog) handleReset(confirmDialog.resourceNames);
        }}
        title={confirmDialog?.title ?? ''}
        message={confirmDialog?.message ?? ''}
        confirmText="重置"
        variant="warning"
      />

      <ConfirmDialog
        isOpen={resetError !== null}
        onClose={() => setResetError(null)}
        onConfirm={() => setResetError(null)}
        title="重置失败"
        message={resetError ?? ''}
        confirmText="确定"
        variant="danger"
      />
    </div>
  );
}
