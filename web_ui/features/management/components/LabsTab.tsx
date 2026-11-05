'use client';

import * as React from 'react';
import { Download, Upload, RefreshCw } from 'lucide-react';
import { DataTable, DataTableColumnDef } from '@/components/data-table/DataTable';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { ConfirmationDialog } from './dialogs/ConfirmationDialog';
import { loadLabs, unloadLabs, reloadLabs } from '../api/labs';
import type { Lab } from '@/lib/types/management';
import { useOrchestratorConnected } from '@/contexts/OrchestratorStatusContext';
import { packageColumn } from './columns';

interface LabsTabProps {
  initialLabs: Lab[];
  // Loading/unloading labs is superuser-only; others see a read-only table.
  canManage: boolean;
}

export function LabsTab({ initialLabs, canManage }: LabsTabProps) {
  const { isConnected } = useOrchestratorConnected();
  const [selectedRows, setSelectedRows] = React.useState<Lab[]>([]);
  const [actionDialogOpen, setActionDialogOpen] = React.useState(false);
  const [currentAction, setCurrentAction] = React.useState<'load' | 'unload' | 'reload' | null>(null);

  const columns: DataTableColumnDef<Lab>[] = [
    {
      accessorKey: 'name',
      header: '实验室名称',
      cell: ({ row }) => <div className="font-medium">{row.getValue('name')}</div>,
    },
    packageColumn<Lab>(initialLabs),
    {
      accessorKey: 'loaded',
      header: '状态',
      cell: ({ row }) => {
        const loaded = row.getValue('loaded') as boolean;
        return <Badge variant={loaded ? 'success' : 'default'}>{loaded ? '已加载' : '已卸载'}</Badge>;
      },
      enableColumnFilter: true,
      filterType: 'multiselect',
      filterOptions: ['true', 'false'],
      filterFn: (row, columnId, filterValue: string[]) => {
        const value = String(row.getValue(columnId));
        return filterValue.includes(value);
      },
    },
    {
      id: 'actions',
      header: '操作',
      cell: ({ row }) => {
        const lab = row.original;
        return (
          <div className="flex items-center gap-2">
            {!lab.loaded && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleActionSingle('load', lab.name)}
                disabled={!isConnected}
              >
                <Download className="h-4 w-4 mr-1" />
                加载
              </Button>
            )}
            {lab.loaded && (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => handleActionSingle('unload', lab.name)}
                  disabled={!isConnected}
                >
                  <Upload className="h-4 w-4 mr-1" />
                  卸载
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => handleActionSingle('reload', lab.name)}
                  disabled={!isConnected}
                >
                  <RefreshCw className="h-4 w-4 mr-1" />
                  重新加载
                </Button>
              </>
            )}
          </div>
        );
      },
    },
  ];

  // Hide the per-row actions for users who cannot manage labs (row selection is also disabled below).
  const visibleColumns = canManage ? columns : columns.filter((column) => column.id !== 'actions');

  const handleActionSingle = (action: 'load' | 'unload' | 'reload', labName: string) => {
    const lab = initialLabs.find((l) => l.name === labName);
    if (lab) {
      setSelectedRows([lab]);
      setCurrentAction(action);
      setActionDialogOpen(true);
    }
  };

  const handleActionSelected = (action: 'load' | 'unload' | 'reload') => {
    setCurrentAction(action);
    setActionDialogOpen(true);
  };

  const handleActionConfirm = async () => {
    const labNames = selectedRows.map((row) => row.name);
    let result;

    switch (currentAction) {
      case 'load':
        result = await loadLabs(labNames);
        break;
      case 'unload':
        result = await unloadLabs(labNames);
        break;
      case 'reload':
        result = await reloadLabs(labNames);
        break;
      default:
        return;
    }

    if (!result.success) {
      throw new Error(result.error);
    }
  };

  const getDialogConfig = () => {
    switch (currentAction) {
      case 'load':
        return {
          title: '加载实验室',
          description: '确定要加载这些实验室吗？这将初始化所有实验室设备和资源。',
          confirmLabel: '加载',
          variant: 'default' as const,
        };
      case 'unload':
        return {
          title: '卸载实验室',
          description: '确定要卸载这些实验室吗？这将关闭所有实验室设备并释放资源。',
          confirmLabel: '卸载',
          variant: 'destructive' as const,
        };
      case 'reload':
        return {
          title: '重新加载实验室',
          description: '确定要重新加载这些实验室吗？这将重启所有实验室设备并刷新配置。',
          confirmLabel: '重新加载',
          variant: 'default' as const,
        };
      default:
        return {
          title: '',
          description: '',
          confirmLabel: '确认',
          variant: 'default' as const,
        };
    }
  };

  // Filter selected rows based on current action validity
  const getValidSelectedRows = () => {
    if (!currentAction) return selectedRows;

    return selectedRows.filter((row) => {
      if (currentAction === 'load') return !row.loaded;
      if (currentAction === 'unload' || currentAction === 'reload') return row.loaded;
      return false;
    });
  };

  const validSelectedRows = getValidSelectedRows();

  const bulkActions = selectedRows.length > 0 && (
    <>
      {selectedRows.some((row) => !row.loaded) && (
        <Button
          variant="primary"
          size="sm"
          onClick={() => handleActionSelected('load')}
          disabled={!selectedRows.some((row) => !row.loaded) || !isConnected}
        >
          <Download className="h-4 w-4 mr-1" />
          加载所选项
        </Button>
      )}
      {selectedRows.some((row) => row.loaded) && (
        <>
          <Button
            variant="outline"
            size="sm"
            onClick={() => handleActionSelected('unload')}
            disabled={!selectedRows.some((row) => row.loaded) || !isConnected}
          >
            <Upload className="h-4 w-4 mr-1" />
            卸载所选项
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => handleActionSelected('reload')}
            disabled={!selectedRows.some((row) => row.loaded) || !isConnected}
          >
            <RefreshCw className="h-4 w-4 mr-1" />
            重新加载所选项
          </Button>
        </>
      )}
    </>
  );

  const dialogConfig = getDialogConfig();

  return (
    <div className="space-y-4">
      <DataTable
        columns={visibleColumns}
        data={initialLabs}
        searchPlaceholder="搜索实验室…"
        enableRowSelection={canManage}
        onSelectionChange={setSelectedRows}
        bulkActions={bulkActions}
      />

      <ConfirmationDialog
        open={actionDialogOpen}
        onOpenChange={setActionDialogOpen}
        title={dialogConfig.title}
        description={dialogConfig.description}
        confirmLabel={dialogConfig.confirmLabel}
        variant={dialogConfig.variant}
        items={validSelectedRows.map((row) => row.name)}
        onConfirm={handleActionConfirm}
      />
    </div>
  );
}
