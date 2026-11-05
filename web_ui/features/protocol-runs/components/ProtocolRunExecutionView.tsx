'use client';

import { statusLabel } from '@/lib/labels';

import * as React from 'react';
import { ReactFlowProvider } from '@xyflow/react';
import { ArrowLeft, X, List, GitBranch, LayoutDashboard } from 'lucide-react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Button } from '@/components/ui/Button';
import { RefreshControl } from '@/components/ui/RefreshControl';
import { Badge, getStatusBadgeVariant } from '@/components/ui/Badge';
import { ProtocolRunFlowCanvas } from './ProtocolRunFlowCanvas';
import { TaskDetailPanel } from './TaskDetailPanel';
import { TaskDetailPanelSkeleton } from './TaskDetailPanelSkeleton';
import { TaskListPanel } from './TaskListPanel';
import { ProtocolMatterixExecutionView } from './ProtocolMatterixExecutionView';
import { getProtocolRunWithTaskStatuses, getTaskDetails, type TaskStatusInfo } from '../api/protocolRunDetails';
import { cancelProtocolRun } from '../api/protocolRuns';
import { ConfirmDialog } from '@/components/dialogs/ConfirmDialog';
import type { ProtocolRun, Task, TaskDeviceConfig } from '@/lib/types/api';
import type { ProtocolSpec } from '@/lib/api/specs';
import { useOrchestratorConnected } from '@/contexts/OrchestratorStatusContext';
import { protocolRunUsesMatterix } from '../utils/matterixExecution';

const PROTOCOL_RUN_POLLING_INTERVALS = [
  { label: '关闭', value: 0 },
  { label: '1 秒', value: 1000 },
  { label: '5 秒', value: 5000 },
  { label: '10 秒', value: 10000 },
  { label: '30 秒', value: 30000 },
  { label: '1 分钟', value: 60000 },
];

interface ProtocolRunExecutionViewProps {
  protocolRun: ProtocolRun;
  initialTaskStatuses: TaskStatusInfo[];
  protocolSpec?: ProtocolSpec;
}

export function ProtocolRunExecutionView({
  protocolRun,
  initialTaskStatuses,
  protocolSpec,
}: ProtocolRunExecutionViewProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { isConnected } = useOrchestratorConnected();
  const [taskStatuses, setTaskStatuses] = React.useState<TaskStatusInfo[]>(initialTaskStatuses);
  const [selectedTaskName, setSelectedTaskName] = React.useState<string | null>(null);
  const [currentProtocolRun, setCurrentProtocolRun] = React.useState<ProtocolRun>(protocolRun);
  const isActiveStatus = (status: ProtocolRun['status']) => status === 'RUNNING' || status === 'CREATED';
  const [pollingInterval, setPollingInterval] = React.useState(isActiveStatus(protocolRun.status) ? 5000 : 0);
  const [isRefreshing, setIsRefreshing] = React.useState(false);
  const [taskDetailsCache, setTaskDetailsCache] = React.useState<Record<string, Task>>({});
  const taskDetailsCacheRef = React.useRef(taskDetailsCache);
  taskDetailsCacheRef.current = taskDetailsCache;
  const cacheInsertionOrderRef = React.useRef<string[]>([]);

  const TASK_DETAILS_CACHE_MAX = 100;
  const insertTaskDetails = React.useCallback((name: string, task: Task) => {
    setTaskDetailsCache((prev) => {
      const next = { ...prev, [name]: task };
      const order = cacheInsertionOrderRef.current;
      if (!(name in prev)) {
        order.push(name);
        while (order.length > TASK_DETAILS_CACHE_MAX) {
          const oldest = order.shift();
          if (oldest && oldest !== name) delete next[oldest];
        }
      }
      return next;
    });
  }, []);
  const [isLoadingTaskDetails, setIsLoadingTaskDetails] = React.useState(false);
  const [isCancelling, setIsCancelling] = React.useState(false);
  const [showCancelDialog, setShowCancelDialog] = React.useState(false);
  const [showTaskList, setShowTaskList] = React.useState(!protocolSpec);
  const usesMatterix = React.useMemo(
    () =>
      protocolRunUsesMatterix({
        protocolRunType: currentProtocolRun.type,
        protocolSpec,
        taskStatuses,
      }),
    [currentProtocolRun.type, protocolSpec, taskStatuses]
  );
  const [mainView, setMainView] = React.useState<'overview' | 'canvas'>(usesMatterix ? 'overview' : 'canvas');

  const selectedTaskDetails = selectedTaskName ? taskDetailsCache[selectedTaskName] : null;
  const canCancel = currentProtocolRun.status === 'RUNNING' || currentProtocolRun.status === 'CREATED';

  React.useEffect(() => {
    if (usesMatterix) setMainView('overview');
  }, [usesMatterix]);

  const handleRefresh = React.useCallback(async () => {
    setIsRefreshing(true);
    try {
      const { protocolRun: freshProtocolRun, taskStatuses: freshStatuses } = await getProtocolRunWithTaskStatuses(
        protocolRun.name
      );
      if (freshProtocolRun) {
        setCurrentProtocolRun(freshProtocolRun);

        // Invalidate cached task details when their status changes
        setTaskDetailsCache((prev) => {
          const updated = { ...prev };
          const order = cacheInsertionOrderRef.current;
          for (const fresh of freshStatuses) {
            const old = taskStatuses.find((t) => t.name === fresh.name);
            if (old && old.status !== fresh.status && fresh.name in updated) {
              delete updated[fresh.name];
              const idx = order.indexOf(fresh.name);
              if (idx !== -1) order.splice(idx, 1);
            }
          }
          return updated;
        });

        setTaskStatuses(freshStatuses);

        // Disable polling once the protocol run reaches a terminal state
        if (!isActiveStatus(freshProtocolRun.status) && pollingInterval !== 0) {
          setPollingInterval(0);
        }
      }
    } catch (error) {
      console.error('刷新协议运行失败：', error);
    } finally {
      setIsRefreshing(false);
    }
  }, [protocolRun.name, pollingInterval, taskStatuses]);

  React.useEffect(() => {
    if (pollingInterval === 0) return;
    const intervalId = setInterval(handleRefresh, pollingInterval);
    return () => clearInterval(intervalId);
  }, [pollingInterval, handleRefresh]);

  const handleTaskSelect = React.useCallback(
    async (taskName: string | null) => {
      setSelectedTaskName(taskName);
      if (!taskName || taskDetailsCacheRef.current[taskName]) return;

      setIsLoadingTaskDetails(true);
      try {
        const taskDetails = await getTaskDetails(taskName, protocolRun.name);
        if (taskDetails) {
          insertTaskDetails(taskName, taskDetails);
        } else {
          // Task not yet in DB, build a minimal Task from spec data
          const specTask = protocolSpec?.tasks.find((t) => t.name === taskName);
          if (specTask) {
            const statusInfo = taskStatuses.find((t) => t.name === taskName);
            const fallbackTask: Task = {
              name: specTask.name,
              type: specTask.type,
              protocol_run_name: protocolRun.name,
              status: statusInfo?.status || 'CREATED',
              created_at: protocolRun.created_at,
              devices: specTask.devices as Record<string, TaskDeviceConfig> | undefined,
              input_parameters: (specTask.parameters as Record<string, unknown>) ?? null,
            };
            insertTaskDetails(taskName, fallbackTask);
          }
        }
      } catch (error) {
        console.error('获取任务详情失败：', error);
      } finally {
        setIsLoadingTaskDetails(false);
      }
    },
    [protocolRun.name, protocolRun.created_at, protocolSpec?.tasks, taskStatuses, insertTaskDetails]
  );

  // Auto-refetch selected task details when its cache entry is invalidated
  React.useEffect(() => {
    if (selectedTaskName && !taskDetailsCache[selectedTaskName]) {
      handleTaskSelect(selectedTaskName);
    }
  }, [selectedTaskName, taskDetailsCache, handleTaskSelect]);

  const handleClosePanel = React.useCallback(() => {
    setSelectedTaskName(null);
  }, []);

  const handleCloseTaskList = React.useCallback(() => {
    setShowTaskList(false);
    setSelectedTaskName(null);
  }, []);

  const handleBack = React.useCallback(() => {
    const from = searchParams.get('from');
    const campaignName = searchParams.get('campaign');

    if (from === 'campaign' && campaignName) {
      router.push(`/campaigns/${encodeURIComponent(campaignName)}`);
    } else {
      router.push('/protocol-runs');
    }
  }, [router, searchParams]);

  const handleCancelProtocolRun = async () => {
    setIsCancelling(true);
    try {
      const result = await cancelProtocolRun(currentProtocolRun.name);
      if (!result.success) {
        alert(`取消协议运行失败：${result.error}`);
      } else {
        handleRefresh();
      }
    } finally {
      setIsCancelling(false);
    }
  };

  return (
    <div className="flex flex-col h-screen">
      {/* Header */}
      <div className="border-b border-gray-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-4 py-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Button variant="ghost" size="sm" onClick={handleBack} className="gap-2">
              <ArrowLeft className="h-4 w-4" />
              返回
            </Button>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-lg font-bold text-gray-900 dark:text-white">{currentProtocolRun.name}</h1>
                <Badge variant={getStatusBadgeVariant(currentProtocolRun.status)}>
                  {statusLabel(currentProtocolRun.status)}
                </Badge>
              </div>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                {currentProtocolRun.type} • {currentProtocolRun.owner}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {usesMatterix && (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setMainView('overview')}
                  className={`gap-2 ${mainView === 'overview' ? 'bg-gray-100 dark:bg-slate-700' : ''}`}
                >
                  <LayoutDashboard className="h-4 w-4" />
                  协议总览
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setMainView('canvas')}
                  className={`gap-2 ${mainView === 'canvas' ? 'bg-gray-100 dark:bg-slate-700' : ''}`}
                >
                  <GitBranch className="h-4 w-4" />
                  流程图
                </Button>
              </>
            )}

            {/* Task list toggle */}
            <Button
              variant="outline"
              size="sm"
              onClick={() => setShowTaskList((prev) => !prev)}
              className={`gap-2 ${showTaskList ? 'bg-gray-100 dark:bg-slate-700' : ''}`}
            >
              <List className="h-4 w-4" />
              任务
            </Button>

            {/* Cancel button */}
            {canCancel && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setShowCancelDialog(true)}
                disabled={isCancelling || !isConnected}
                className="gap-2 text-red-600 hover:text-red-700 hover:bg-red-50 dark:text-red-400 dark:hover:text-red-300 dark:hover:bg-red-950"
              >
                <X className="h-4 w-4" />
                {isCancelling ? '正在取消…' : '取消'}
              </Button>
            )}

            <RefreshControl
              pollingInterval={pollingInterval}
              onIntervalChange={setPollingInterval}
              onRefresh={handleRefresh}
              isRefreshing={isRefreshing}
              disabled={!isActiveStatus(currentProtocolRun.status)}
              intervals={PROTOCOL_RUN_POLLING_INTERVALS}
            />
          </div>
        </div>
      </div>

      {/* Main content: Flow canvas + Task detail panel + Logs */}
      <div className="flex-1 flex overflow-hidden">
        <div className="flex-1 min-w-0">
          {usesMatterix && mainView === 'overview' ? (
            <ProtocolMatterixExecutionView
              protocolRunName={currentProtocolRun.name}
              protocolRunStatus={currentProtocolRun.status}
              onTaskSelect={handleTaskSelect}
            />
          ) : protocolSpec ? (
            <ReactFlowProvider>
              <ProtocolRunFlowCanvas
                protocolTasks={protocolSpec.tasks}
                taskStatuses={taskStatuses}
                onTaskSelect={handleTaskSelect}
                selectedTaskName={selectedTaskName}
                onShowTaskList={() => setShowTaskList(true)}
              />
            </ReactFlowProvider>
          ) : (
            <p className="p-6 text-sm text-gray-500 dark:text-gray-400">
              此协议定义已移除。请从任务列表查看本次运行保存的参数、观测、验收和文件。
            </p>
          )}
        </div>
        {showTaskList && (
          <TaskListPanel
            taskStatuses={taskStatuses}
            selectedTaskName={selectedTaskName}
            onTaskSelect={handleTaskSelect}
            onClose={handleCloseTaskList}
          />
        )}
        {selectedTaskName &&
          (isLoadingTaskDetails && !selectedTaskDetails ? (
            <TaskDetailPanelSkeleton taskName={selectedTaskName} onClose={handleClosePanel} />
          ) : selectedTaskDetails ? (
            <TaskDetailPanel task={selectedTaskDetails} onClose={handleClosePanel} />
          ) : null)}
      </div>

      <ConfirmDialog
        isOpen={showCancelDialog}
        onClose={() => setShowCancelDialog(false)}
        onConfirm={handleCancelProtocolRun}
        title="取消协议运行"
        message={`确定要取消协议运行“${currentProtocolRun.name}”吗？这将停止所有正在运行的任务。`}
        confirmText="取消协议运行"
        cancelText="继续运行"
        variant="danger"
      />
    </div>
  );
}
