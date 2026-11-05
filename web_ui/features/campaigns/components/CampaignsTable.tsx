'use client';

import { statusLabel } from '@/lib/labels';

import * as React from 'react';
import { MoreHorizontal, X, Copy, Activity } from 'lucide-react';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import { useRouter } from 'next/navigation';
import { DataTable, DataTableColumnDef } from '@/components/data-table/DataTable';
import { Button } from '@/components/ui/Button';
import { RefreshControl } from '@/components/ui/RefreshControl';
import { Badge, getStatusBadgeVariant } from '@/components/ui/Badge';
import { ErrorBox } from '@/components/ui/ErrorBox';
import type { Campaign } from '@/lib/types/api';
import type { PaginatedResult } from '@/lib/db/queries';
import type { TaskSpec } from '@/lib/types/protocol';
import type { ProtocolSpec } from '@/lib/api/specs';
import { cancelCampaign, getCampaigns } from '@/features/campaigns/api/campaigns';
import { generateCloneNameForEntity } from '@/lib/utils/naming.server';
import { useServerTable } from '@/hooks/useServerTable';
import { ConfirmDialog } from '@/components/dialogs/ConfirmDialog';
import { SubmitCampaignDialog } from './SubmitCampaignDialog';
import { useOrchestratorConnected } from '@/contexts/OrchestratorStatusContext';
import { useUser } from '@/contexts/UserContext';
import { ConditionalJsonSection, DetailField, TimelineSection } from '@/features/protocol-runs/components/shared';
import { SECTION_DIVIDER } from '@/features/protocol-runs/styles';

const SECTION_TITLE = 'text-sm font-medium text-gray-900 dark:text-white';

const CAMPAIGN_COLUMN_ID_MAP: Record<string, string> = {
  protocol: 'protocol',
  created_at: 'createdAt',
};

interface CampaignsTableProps {
  initialData: PaginatedResult<Campaign>;
  protocolSpecs: Record<string, ProtocolSpec>;
  taskSpecs: Record<string, TaskSpec>;
}

export function CampaignsTable({ initialData, protocolSpecs, taskSpecs }: CampaignsTableProps) {
  const router = useRouter();
  const { isConnected } = useOrchestratorConnected();
  const { canSubmit } = useUser();
  const [pollingInterval, setPollingInterval] = React.useState(5000);
  const [submitDialogOpen, setSubmitDialogOpen] = React.useState(false);
  const [cancellingCampaign, setCancellingCampaign] = React.useState<string | null>(null);
  const [selectedCampaign, setSelectedCampaign] = React.useState<Campaign | null>(null);
  const [detailPanelOpen, setDetailPanelOpen] = React.useState(false);
  const [campaignToClone, setCampaignToClone] = React.useState<Campaign | null>(null);
  const [campaignToCancel, setCampaignToCancel] = React.useState<string | null>(null);

  const serverTable = useServerTable({
    fetchFn: getCampaigns,
    initialData,
    columnIdMap: CAMPAIGN_COLUMN_ID_MAP,
  });

  // Auto-polling effect
  React.useEffect(() => {
    if (pollingInterval === 0) return;
    const intervalId = setInterval(() => {
      serverTable.refresh();
    }, pollingInterval);
    return () => clearInterval(intervalId);
  }, [pollingInterval, serverTable.refresh]); // eslint-disable-line react-hooks/exhaustive-deps

  // Update selected campaign from refreshed data
  React.useEffect(() => {
    if (selectedCampaign) {
      const updated = serverTable.data.find((c) => c.name === selectedCampaign.name);
      if (updated) setSelectedCampaign(updated);
    }
  }, [serverTable.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleViewExecution = (campaignName: string) => {
    router.push(`/campaigns/${encodeURIComponent(campaignName)}`);
  };

  const handleCancelCampaign = async () => {
    if (!campaignToCancel) return;
    setCancellingCampaign(campaignToCancel);
    try {
      const result = await cancelCampaign(campaignToCancel);
      if (!result.success) {
        alert(`取消实验计划失败：${result.error}`);
      }
    } finally {
      setCancellingCampaign(null);
      setCampaignToCancel(null);
    }
  };

  const handleRowClick = (campaign: Campaign) => {
    setSelectedCampaign(campaign);
    setDetailPanelOpen(true);
  };

  const handleCloneCampaign = (campaign: Campaign) => {
    setCampaignToClone(campaign);
    setSubmitDialogOpen(true);
  };

  const handleCloseSubmitDialog = (open: boolean) => {
    setSubmitDialogOpen(open);
    if (!open) setCampaignToClone(null);
  };

  const columns: DataTableColumnDef<Campaign>[] = [
    {
      accessorKey: 'name',
      header: '名称',
      cell: ({ row }) => <div className="font-medium">{row.getValue('name')}</div>,
      enableColumnFilter: true,
      filterType: 'text',
    },
    {
      accessorKey: 'protocol',
      header: '协议',
      enableColumnFilter: true,
      filterType: 'text',
    },
    {
      accessorKey: 'owner',
      header: '负责人',
      enableColumnFilter: true,
      filterType: 'text',
    },
    {
      accessorKey: 'status',
      header: '状态',
      cell: ({ row }) => {
        const status = row.getValue('status') as string;
        return <Badge variant={getStatusBadgeVariant(status)}>{statusLabel(status)}</Badge>;
      },
      enableColumnFilter: true,
      filterType: 'multiselect',
      filterOptions: ['CREATED', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED'],
      filterFn: 'arrIncludesSome',
    },
    {
      accessorKey: 'protocol_runs_completed',
      header: '进度',
      cell: ({ row }) => {
        const completed = row.getValue('protocol_runs_completed') as number;
        const max = row.original.max_protocol_runs;
        return (
          <div className="text-sm">
            {completed} / {max === 0 ? '∞' : max}
          </div>
        );
      },
    },
    {
      accessorKey: 'optimize',
      header: '优化',
      cell: ({ row }) => {
        const optimize = row.getValue('optimize') as boolean;
        return <Badge variant={optimize ? 'info' : 'default'}>{optimize ? '是' : '否'}</Badge>;
      },
    },
    {
      accessorKey: 'created_at',
      header: '创建时间',
      cell: ({ row }) => {
        const date = new Date(row.getValue('created_at'));
        return date.toLocaleString('zh-CN');
      },
    },
    {
      id: 'actions',
      header: '操作',
      cell: ({ row }) => {
        const campaign = row.original;
        const canCancel = campaign.status === 'RUNNING' || campaign.status === 'CREATED';

        return (
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="sm"
              className="h-8 w-8 p-0"
              onClick={() => handleViewExecution(campaign.name)}
              title="查看执行过程"
            >
              <Activity className="h-4 w-4" />
            </Button>
            <DropdownMenu.Root>
              <DropdownMenu.Trigger asChild>
                <Button variant="ghost" size="sm" className="h-8 w-8 p-0">
                  <span className="sr-only">打开菜单</span>
                  <MoreHorizontal className="h-4 w-4" />
                </Button>
              </DropdownMenu.Trigger>
              <DropdownMenu.Portal>
                <DropdownMenu.Content className="w-48 rounded-md border border-gray-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-1 shadow-md z-50">
                  <DropdownMenu.Item
                    className="relative flex cursor-pointer select-none items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-none transition-colors hover:bg-gray-100 dark:hover:bg-slate-700 focus:bg-gray-100 dark:focus:bg-slate-700 dark:text-gray-300"
                    onClick={() => handleCloneCampaign(campaign)}
                  >
                    <Copy className="h-4 w-4" />
                    <span>克隆</span>
                  </DropdownMenu.Item>
                  {canCancel && (
                    <DropdownMenu.Item
                      className="relative flex cursor-pointer select-none items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-none transition-colors hover:bg-gray-100 dark:hover:bg-slate-700 focus:bg-gray-100 dark:focus:bg-slate-700 dark:text-gray-300"
                      onClick={() => setCampaignToCancel(campaign.name)}
                      disabled={cancellingCampaign === campaign.name || !isConnected || !canSubmit}
                    >
                      <X className="h-4 w-4" />
                      <span>{cancellingCampaign === campaign.name ? '正在取消…' : '取消实验计划'}</span>
                    </DropdownMenu.Item>
                  )}
                </DropdownMenu.Content>
              </DropdownMenu.Portal>
            </DropdownMenu.Root>
          </div>
        );
      },
    },
  ];

  const toolbarActions = (
    <RefreshControl
      pollingInterval={pollingInterval}
      onIntervalChange={setPollingInterval}
      onRefresh={serverTable.refresh}
      isRefreshing={serverTable.isLoading}
    />
  );

  return (
    <div className="flex gap-4">
      <div className={`space-y-4 ${detailPanelOpen ? 'flex-1 min-w-0' : 'w-full'}`}>
        <div className="flex justify-between items-center">
          <div>
            <h1 className="text-3xl font-bold text-gray-900 dark:text-white">实验计划</h1>
            <p className="text-gray-600 dark:text-gray-400 mt-1">查看和管理实验计划</p>
          </div>
          <Button
            variant="primary"
            disabled={!isConnected || !canSubmit}
            title={!isConnected ? '编排器离线' : undefined}
            onClick={() => {
              setCampaignToClone(null);
              setSubmitDialogOpen(true);
            }}
          >
            提交实验计划
          </Button>
        </div>

        <DataTable
          columns={columns}
          data={serverTable.data}
          searchPlaceholder="搜索实验计划…"
          onRowClick={handleRowClick}
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

        <SubmitCampaignDialog
          open={submitDialogOpen}
          onOpenChange={handleCloseSubmitDialog}
          onSuccess={() => setTimeout(() => serverTable.refresh(), 500)}
          protocolSpecs={protocolSpecs}
          taskSpecs={taskSpecs}
          initialCampaign={campaignToClone}
          generateCloneName={(name) => generateCloneNameForEntity('campaigns', name)}
        />
      </div>

      {detailPanelOpen && selectedCampaign && (
        <div className="w-96 border-l border-gray-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-6 sticky top-4 max-h-[calc(100vh-2rem)] overflow-y-auto self-start">
          <div className="flex items-start justify-between mb-6 gap-2">
            <div className="min-w-0">
              <h2 className="text-lg font-semibold text-gray-900 dark:text-white break-words">
                {selectedCampaign.name}
              </h2>
              <p className="text-sm text-gray-500 dark:text-gray-400">实验计划详情</p>
            </div>
            <button
              onClick={() => setDetailPanelOpen(false)}
              className="flex-shrink-0 rounded-sm opacity-70 ring-offset-white dark:ring-offset-slate-900 transition-opacity hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-blue-600 focus:ring-offset-2 text-gray-900 dark:text-gray-400"
            >
              <X className="h-4 w-4" />
              <span className="sr-only">关闭</span>
            </button>
          </div>

          <div className="space-y-6">
            <div className="space-y-3">
              <DetailField label="协议" value={selectedCampaign.protocol} />
              <DetailField
                label="状态"
                value={
                  <Badge variant={getStatusBadgeVariant(selectedCampaign.status)}>
                    {statusLabel(selectedCampaign.status)}
                  </Badge>
                }
              />
              {selectedCampaign.status === 'FAILED' && selectedCampaign.error_message && (
                <ErrorBox error={selectedCampaign.error_message} />
              )}
              <DetailField label="负责人" value={selectedCampaign.owner} />
              <DetailField label="优先级" value={selectedCampaign.priority} />
            </div>

            <div className={SECTION_DIVIDER}>
              <div className={`${SECTION_TITLE} mb-3`}>进度</div>
              <div className="space-y-2">
                <DetailField inline label="已完成" value={selectedCampaign.protocol_runs_completed} />
                <DetailField
                  inline
                  label="最大协议运行次数"
                  value={selectedCampaign.max_protocol_runs === 0 ? '∞' : selectedCampaign.max_protocol_runs}
                />
                <DetailField inline label="最大并发数" value={selectedCampaign.max_concurrent_protocol_runs} />
              </div>
            </div>

            <div className={SECTION_DIVIDER}>
              <div className={`${SECTION_TITLE} mb-3`}>优化</div>
              <div className="space-y-2">
                <DetailField
                  inline
                  label="已启用"
                  value={
                    <Badge variant={selectedCampaign.optimize ? 'info' : 'default'}>
                      {selectedCampaign.optimize ? '是' : '否'}
                    </Badge>
                  }
                />
                {selectedCampaign.optimize && (
                  <DetailField
                    inline
                    label="优化器 IP"
                    value={<span className="font-mono text-xs break-all">{selectedCampaign.optimizer_ip}</span>}
                  />
                )}
              </div>
            </div>

            <div className={SECTION_DIVIDER}>
              <div className={`${SECTION_TITLE} mb-2`}>时间线</div>
              <TimelineSection
                entries={[
                  { label: '创建时间', timestamp: selectedCampaign.created_at ?? null },
                  { label: '开始时间', timestamp: selectedCampaign.start_time ?? null },
                  { label: '结束时间', timestamp: selectedCampaign.end_time ?? null },
                ]}
              />
            </div>

            <ConditionalJsonSection data={selectedCampaign.global_parameters} label="全局参数" />
            <ConditionalJsonSection data={selectedCampaign.protocol_run_parameters} label="协议运行参数" />
            <ConditionalJsonSection data={selectedCampaign.pareto_solutions} label="帕累托解" />
            <ConditionalJsonSection data={selectedCampaign.meta} label="元数据" />
          </div>
        </div>
      )}

      <ConfirmDialog
        isOpen={campaignToCancel !== null}
        onClose={() => setCampaignToCancel(null)}
        onConfirm={handleCancelCampaign}
        title="取消实验计划"
        message={`确定要取消实验计划“${campaignToCancel}”吗？这将停止所有正在运行的协议。`}
        confirmText="取消实验计划"
        cancelText="继续运行"
        variant="danger"
      />
    </div>
  );
}
