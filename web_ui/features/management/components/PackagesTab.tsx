'use client';

import * as React from 'react';
import { Download, Upload } from 'lucide-react';
import { DataTable, DataTableColumnDef } from '@/components/data-table/DataTable';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { ConfirmationDialog } from './dialogs/ConfirmationDialog';
import { loadPackages, unloadPackages } from '../api/packages';
import type { PackageInfo } from '@/lib/types/management';
import { useOrchestratorConnected } from '@/contexts/OrchestratorStatusContext';

interface PackagesTabProps {
  initialPackages: PackageInfo[];
  // Loading/unloading packages is superuser-only; others see a read-only table.
  canManage: boolean;
}

export function PackagesTab({ initialPackages, canManage }: PackagesTabProps) {
  const { isConnected } = useOrchestratorConnected();
  const [selectedRows, setSelectedRows] = React.useState<PackageInfo[]>([]);
  const [actionDialogOpen, setActionDialogOpen] = React.useState(false);
  const [currentAction, setCurrentAction] = React.useState<'load' | 'unload' | null>(null);

  const columns: DataTableColumnDef<PackageInfo>[] = [
    {
      accessorKey: 'name',
      header: '软件包名称',
      cell: ({ row }) => <div className="font-medium">{row.getValue('name')}</div>,
    },
    {
      accessorKey: 'active',
      header: '状态',
      cell: ({ row }) => {
        const active = row.getValue('active') as boolean;
        return <Badge variant={active ? 'success' : 'default'}>{active ? '已加载' : '已卸载'}</Badge>;
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
        const pkg = row.original;
        return (
          <div className="flex items-center gap-2">
            {!pkg.active && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleActionSingle('load', pkg.name)}
                disabled={!isConnected}
              >
                <Download className="h-4 w-4 mr-1" />
                加载
              </Button>
            )}
            {pkg.active && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleActionSingle('unload', pkg.name)}
                disabled={!isConnected}
              >
                <Upload className="h-4 w-4 mr-1" />
                卸载
              </Button>
            )}
          </div>
        );
      },
    },
  ];

  // Hide the per-row actions for users who cannot manage packages (row selection is also disabled below).
  const visibleColumns = canManage ? columns : columns.filter((column) => column.id !== 'actions');

  const handleActionSingle = (action: 'load' | 'unload', packageName: string) => {
    const pkg = initialPackages.find((p) => p.name === packageName);
    if (pkg) {
      setSelectedRows([pkg]);
      setCurrentAction(action);
      setActionDialogOpen(true);
    }
  };

  const handleActionSelected = (action: 'load' | 'unload') => {
    setCurrentAction(action);
    setActionDialogOpen(true);
  };

  const handleActionConfirm = async () => {
    const packageNames = selectedRows.map((row) => row.name);
    let result;

    switch (currentAction) {
      case 'load':
        result = await loadPackages(packageNames);
        break;
      case 'unload':
        result = await unloadPackages(packageNames);
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
          title: '加载软件包',
          description: '确定要加载这些软件包吗？加载后即可使用其中的条目。',
          confirmLabel: '加载',
          variant: 'default' as const,
        };
      case 'unload':
        return {
          title: '卸载软件包',
          description: '确定要卸载这些软件包吗？卸载后其中的条目将不可用。包含已加载实验室或协议的软件包无法卸载。',
          confirmLabel: '卸载',
          variant: 'destructive' as const,
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

  const getValidSelectedRows = () => {
    if (!currentAction) return selectedRows;

    return selectedRows.filter((row) => {
      if (currentAction === 'load') return !row.active;
      if (currentAction === 'unload') return row.active;
      return false;
    });
  };

  const validSelectedRows = getValidSelectedRows();

  const bulkActions = selectedRows.length > 0 && (
    <>
      {selectedRows.some((row) => !row.active) && (
        <Button
          variant="primary"
          size="sm"
          onClick={() => handleActionSelected('load')}
          disabled={!selectedRows.some((row) => !row.active) || !isConnected}
        >
          <Download className="h-4 w-4 mr-1" />
          加载所选项
        </Button>
      )}
      {selectedRows.some((row) => row.active) && (
        <Button
          variant="outline"
          size="sm"
          onClick={() => handleActionSelected('unload')}
          disabled={!selectedRows.some((row) => row.active) || !isConnected}
        >
          <Upload className="h-4 w-4 mr-1" />
          卸载所选项
        </Button>
      )}
    </>
  );

  const dialogConfig = getDialogConfig();

  return (
    <div className="space-y-4">
      <DataTable
        columns={visibleColumns}
        data={initialPackages}
        searchPlaceholder="搜索软件包…"
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
