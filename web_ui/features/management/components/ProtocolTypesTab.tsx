'use client';

import * as React from 'react';
import { Download, Upload, RefreshCw } from 'lucide-react';
import { DataTable, DataTableColumnDef } from '@/components/data-table/DataTable';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { ConfirmationDialog } from './dialogs/ConfirmationDialog';
import { loadProtocolTypes, unloadProtocolTypes, reloadProtocolTypes } from '../api/protocolTypes';
import type { ProtocolType } from '@/lib/types/management';
import { useOrchestratorConnected } from '@/contexts/OrchestratorStatusContext';
import { packageColumn } from './columns';

interface ProtocolTypesTabProps {
  initialProtocolTypes: ProtocolType[];
}

export function ProtocolTypesTab({ initialProtocolTypes }: ProtocolTypesTabProps) {
  const { isConnected } = useOrchestratorConnected();
  const [selectedRows, setSelectedRows] = React.useState<ProtocolType[]>([]);
  const [actionDialogOpen, setActionDialogOpen] = React.useState(false);
  const [currentAction, setCurrentAction] = React.useState<'load' | 'unload' | 'reload' | null>(null);

  const columns: DataTableColumnDef<ProtocolType>[] = [
    {
      accessorKey: 'name',
      header: '协议',
      cell: ({ row }) => <div className="font-medium">{row.getValue('name')}</div>,
    },
    packageColumn<ProtocolType>(initialProtocolTypes),
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
        const protocolType = row.original;
        return (
          <div className="flex items-center gap-2">
            {!protocolType.loaded && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleActionSingle('load', protocolType.name)}
                disabled={!isConnected}
              >
                <Download className="h-4 w-4 mr-1" />
                加载
              </Button>
            )}
            {protocolType.loaded && (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => handleActionSingle('unload', protocolType.name)}
                  disabled={!isConnected}
                >
                  <Upload className="h-4 w-4 mr-1" />
                  卸载
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => handleActionSingle('reload', protocolType.name)}
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

  const handleActionSingle = (action: 'load' | 'unload' | 'reload', protocolTypeName: string) => {
    const protocolType = initialProtocolTypes.find((e) => e.name === protocolTypeName);
    if (protocolType) {
      setSelectedRows([protocolType]);
      setCurrentAction(action);
      setActionDialogOpen(true);
    }
  };

  const handleActionSelected = (action: 'load' | 'unload' | 'reload') => {
    setCurrentAction(action);
    setActionDialogOpen(true);
  };

  const handleActionConfirm = async () => {
    const protocolTypeNames = selectedRows.map((row) => row.name);
    let result;

    switch (currentAction) {
      case 'load':
        result = await loadProtocolTypes(protocolTypeNames);
        break;
      case 'unload':
        result = await unloadProtocolTypes(protocolTypeNames);
        break;
      case 'reload':
        result = await reloadProtocolTypes(protocolTypeNames);
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
          title: '加载协议',
          description: '确定要加载这些协议吗？加载后即可提交协议运行。',
          confirmLabel: '加载',
          variant: 'default' as const,
        };
      case 'unload':
        return {
          title: '卸载协议',
          description: '确定要卸载这些协议吗？使用这些类型的活动协议运行不受影响。',
          confirmLabel: '卸载',
          variant: 'destructive' as const,
        };
      case 'reload':
        return {
          title: '重新加载协议',
          description: '确定要重新加载这些协议吗？这将刷新协议配置和实验计划优化器。',
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
        columns={columns}
        data={initialProtocolTypes}
        searchPlaceholder="搜索协议…"
        enableRowSelection
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
