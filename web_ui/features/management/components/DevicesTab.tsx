'use client';

import { statusLabel } from '@/lib/labels';

import * as React from 'react';
import { RefreshCw, Eye } from 'lucide-react';
import { DataTable, DataTableColumnDef } from '@/components/data-table/DataTable';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/Sheet';
import { JsonDisplay } from '@/components/ui/JsonDisplay';
import { ConfirmationDialog } from './dialogs/ConfirmationDialog';
import { reloadDevices, getDeviceReport } from '../api/devices';
import type { Device, DeviceReport } from '@/lib/types/management';
import { useOrchestratorConnected } from '@/contexts/OrchestratorStatusContext';

interface DevicesTabProps {
  initialDevices: Device[];
}

export function DevicesTab({ initialDevices }: DevicesTabProps) {
  const { isConnected } = useOrchestratorConnected();
  const [selectedRows, setSelectedRows] = React.useState<Device[]>([]);
  const [reloadDialogOpen, setReloadDialogOpen] = React.useState(false);
  const [selectedDevice, setSelectedDevice] = React.useState<Device | null>(null);
  const [detailPanelOpen, setDetailPanelOpen] = React.useState(false);
  const [deviceReport, setDeviceReport] = React.useState<DeviceReport | null>(null);
  const [loadingReport, setLoadingReport] = React.useState(false);

  // Get unique labs for filter options
  const uniqueLabs = Array.from(new Set(initialDevices.map((d) => d.lab_name))).sort();

  const columns: DataTableColumnDef<Device>[] = [
    {
      accessorKey: 'name',
      header: '设备名称',
      cell: ({ row }) => <div className="font-medium">{row.getValue('name')}</div>,
    },
    {
      accessorKey: 'lab_name',
      header: '实验室',
      cell: ({ row }) => <div className="text-gray-700 dark:text-gray-300">{row.getValue('lab_name')}</div>,
      enableColumnFilter: true,
      filterType: 'multiselect',
      filterOptions: uniqueLabs,
      filterFn: 'arrIncludesSome',
    },
    {
      accessorKey: 'type',
      header: '类型',
      cell: ({ row }) => <div className="text-gray-600 dark:text-gray-400">{row.getValue('type')}</div>,
    },
    {
      accessorKey: 'computer',
      header: '计算机',
      cell: ({ row }) => <div className="text-gray-600 dark:text-gray-400">{row.getValue('computer')}</div>,
    },
    {
      accessorKey: 'status',
      header: '状态',
      cell: ({ row }) => {
        const status = row.getValue('status') as string;
        return <Badge variant={status === 'ACTIVE' ? 'success' : 'default'}>{statusLabel(status)}</Badge>;
      },
      enableColumnFilter: true,
      filterType: 'multiselect',
      filterOptions: ['ACTIVE', 'INACTIVE'],
      filterFn: 'arrIncludesSome',
    },
    {
      id: 'actions',
      header: '操作',
      cell: ({ row }) => {
        const device = row.original;
        return (
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={!isConnected}
              onClick={(e) => {
                e.stopPropagation();
                handleViewDevice(device);
              }}
            >
              <Eye className="h-4 w-4 mr-1" />
              查看
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!isConnected}
              onClick={(e) => {
                e.stopPropagation();
                handleReloadSingle(device);
              }}
            >
              <RefreshCw className="h-4 w-4 mr-1" />
              重新加载
            </Button>
          </div>
        );
      },
    },
  ];

  const handleViewDevice = async (device: Device) => {
    setSelectedDevice(device);
    setDeviceReport(null);
    setDetailPanelOpen(true);
    setLoadingReport(true);

    try {
      const report = await getDeviceReport(device.lab_name, device.name);
      setDeviceReport(report);
    } catch (error) {
      console.error('获取设备报告失败：', error);
      setDeviceReport({ error: '加载设备报告失败' });
    } finally {
      setLoadingReport(false);
    }
  };

  const handleReloadSingle = (device: Device) => {
    setSelectedRows([device]);
    setReloadDialogOpen(true);
  };

  const handleReloadSelected = () => {
    setReloadDialogOpen(true);
  };

  const handleReloadConfirm = async () => {
    // Group devices by lab
    const devicesByLab = selectedRows.reduce(
      (acc, device) => {
        if (!acc[device.lab_name]) {
          acc[device.lab_name] = [];
        }
        acc[device.lab_name].push(device.name);
        return acc;
      },
      {} as Record<string, string[]>
    );

    // Reload devices for each lab
    const reloadPromises = Object.entries(devicesByLab).map(([labName, deviceNames]) =>
      reloadDevices(labName, deviceNames)
    );

    const results = await Promise.all(reloadPromises);
    const failedResults = results.filter((r) => !r.success);

    if (failedResults.length > 0) {
      throw new Error(failedResults.map((r) => r.error).join(', '));
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
        data={initialDevices}
        searchPlaceholder="搜索设备…"
        enableRowSelection
        onSelectionChange={setSelectedRows}
        bulkActions={bulkActions}
      />

      <ConfirmationDialog
        open={reloadDialogOpen}
        onOpenChange={setReloadDialogOpen}
        title="重新加载设备"
        description="确定要重新加载这些设备吗？设备插件将刷新并重新初始化。"
        confirmLabel="重新加载"
        variant="default"
        items={selectedRows.map((row) => `${row.lab_name}.${row.name}`)}
        onConfirm={handleReloadConfirm}
      />

      {/* Device Detail Panel */}
      <Sheet open={detailPanelOpen} onOpenChange={setDetailPanelOpen}>
        <SheetContent side="right" className="overflow-y-auto w-[600px]">
          {selectedDevice && (
            <>
              <SheetHeader>
                <SheetTitle>{selectedDevice.name}</SheetTitle>
                <SheetDescription>所在实验室的设备： {selectedDevice.lab_name}</SheetDescription>
              </SheetHeader>

              <div className="mt-6 space-y-6">
                {/* Basic Info */}
                <div className="space-y-3">
                  <div>
                    <div className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">
                      类型
                    </div>
                    <div className="mt-1 text-sm text-gray-900 dark:text-gray-100">{selectedDevice.type}</div>
                  </div>

                  <div>
                    <div className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">
                      计算机
                    </div>
                    <div className="mt-1 text-sm text-gray-900 dark:text-gray-100">{selectedDevice.computer}</div>
                  </div>

                  <div>
                    <div className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">
                      状态
                    </div>
                    <div className="mt-1">
                      <Badge variant={selectedDevice.status === 'ACTIVE' ? 'success' : 'default'}>
                        {statusLabel(selectedDevice.status)}
                      </Badge>
                    </div>
                  </div>
                </div>

                {/* Metadata */}
                {selectedDevice.meta && Object.keys(selectedDevice.meta).length > 0 && (
                  <div className="border-t border-gray-200 dark:border-slate-700 pt-4">
                    <div className="text-sm font-medium text-gray-900 dark:text-gray-100 mb-3">元数据</div>
                    <JsonDisplay data={selectedDevice.meta} />
                  </div>
                )}

                {/* Device Report */}
                <div className="border-t border-gray-200 dark:border-slate-700 pt-4">
                  <div className="text-sm font-medium text-gray-900 dark:text-gray-100 mb-3">设备报告</div>
                  {loadingReport ? (
                    <div className="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
                      <RefreshCw className="h-4 w-4 animate-spin" />
                      正在加载报告…
                    </div>
                  ) : deviceReport ? (
                    <JsonDisplay data={deviceReport} />
                  ) : (
                    <div className="text-sm text-gray-500 dark:text-gray-400 italic">暂无报告</div>
                  )}
                </div>
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}
