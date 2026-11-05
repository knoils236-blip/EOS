'use client';

import * as React from 'react';
import { RefreshCw } from 'lucide-react';
import { DataTable, DataTableColumnDef } from '@/components/data-table/DataTable';
import { Button } from '@/components/ui/Button';
import { ConfirmationDialog } from './dialogs/ConfirmationDialog';
import { reloadTaskPlugins } from '../api/taskPlugins';
import type { TaskPluginInfo } from '@/lib/types/management';
import { useOrchestratorConnected } from '@/contexts/OrchestratorStatusContext';
import { packageColumn } from './columns';

interface TaskPluginsTabProps {
  initialTaskPlugins: TaskPluginInfo[];
}

export function TaskPluginsTab({ initialTaskPlugins }: TaskPluginsTabProps) {
  const { isConnected } = useOrchestratorConnected();
  const [selectedRows, setSelectedRows] = React.useState<TaskPluginInfo[]>([]);
  const [reloadDialogOpen, setReloadDialogOpen] = React.useState(false);

  const columns: DataTableColumnDef<TaskPluginInfo>[] = [
    {
      accessorKey: 'type',
      header: '任务类型',
      cell: ({ row }) => <div className="font-medium">{row.getValue('type')}</div>,
    },
    packageColumn<TaskPluginInfo>(initialTaskPlugins),
    {
      accessorKey: 'description',
      header: '说明',
      cell: ({ row }) => {
        const description = row.getValue('description') as string | undefined;
        return (
          <div className="text-gray-600 dark:text-gray-400">
            {description || <span className="italic text-gray-400 dark:text-gray-500">暂无说明</span>}
          </div>
        );
      },
    },
    {
      id: 'actions',
      header: '操作',
      cell: ({ row }) => (
        <Button
          variant="outline"
          size="sm"
          onClick={() => handleReloadSingle(row.original.type)}
          disabled={!isConnected}
        >
          <RefreshCw className="h-4 w-4 mr-1" />
          重新加载
        </Button>
      ),
    },
  ];

  const handleReloadSingle = (taskType: string) => {
    const plugin = initialTaskPlugins.find((p) => p.type === taskType);
    if (plugin) {
      setSelectedRows([plugin]);
      setReloadDialogOpen(true);
    }
  };

  const handleReloadSelected = () => {
    setReloadDialogOpen(true);
  };

  const handleReloadConfirm = async () => {
    const taskTypes = selectedRows.map((row) => row.type);
    const result = await reloadTaskPlugins(taskTypes);

    if (!result.success) {
      throw new Error(result.error);
    }
  };

  const bulkActions = selectedRows.length > 0 && (
    <Button variant="primary" size="sm" onClick={handleReloadSelected} disabled={!isConnected}>
      <RefreshCw className="h-4 w-4 mr-1" />
      重新加载所选项
    </Button>
  );

  return (
    <div className="space-y-4">
      <DataTable
        columns={columns}
        data={initialTaskPlugins}
        searchPlaceholder="搜索任务类型…"
        enableRowSelection
        onSelectionChange={setSelectedRows}
        bulkActions={bulkActions}
      />

      <ConfirmationDialog
        open={reloadDialogOpen}
        onOpenChange={setReloadDialogOpen}
        title="重新加载任务"
        description="确定要重新加载这些任务吗？任务代码将刷新。"
        confirmLabel="重新加载"
        variant="default"
        items={selectedRows.map((row) => row.type)}
        onConfirm={handleReloadConfirm}
      />
    </div>
  );
}
