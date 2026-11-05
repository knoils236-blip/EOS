'use client';

import * as React from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { Eraser, ChevronDown, ChevronRight } from 'lucide-react';
import { BaseSubmitDialog } from '@/components/dialogs/BaseSubmitDialog';
import { Input } from '@/components/ui/Input';
import { Label } from '@/components/ui/Label';
import { Textarea } from '@/components/ui/Textarea';
import { Button } from '@/components/ui/Button';
import { Combobox } from '@/components/ui/Combobox';
import { ParameterSearchInput } from '@/components/ui/ParameterSearchInput';
import { DescriptionTooltip } from '@/components/ui/DescriptionTooltip';
import { submitProtocolRun } from '../api/protocolRuns';
import { ProtocolRunParameterField } from './ProtocolRunParameterField';
import {
  hasNonEmptyObject,
  convertParameters,
  extractParameterValues,
  filterNonDefaultParameters,
} from '@/lib/utils/protocolHelpers';
import { iterateInputParameters } from '@/lib/utils/paramGroups';
import { IDENTIFIER_PATTERN, IDENTIFIER_ERROR_MESSAGE } from '@/lib/utils/identifier';
import type { ProtocolRunDefinition, ProtocolRun } from '@/lib/types/api';
import type { TaskSpec, ParameterSpec, ParameterValue } from '@/lib/types/protocol';
import type { ProtocolSpec, LabSpec } from '@/lib/api/specs';
import {
  existingMatterixBoolean,
  mergeMatterixRecordingMeta,
  protocolHasRealMatterixDevice,
} from '../utils/matterixExecution';

const protocolRunFormSchema = z.object({
  name: z.string().min(1, '请输入协议运行名称').regex(IDENTIFIER_PATTERN, IDENTIFIER_ERROR_MESSAGE),
  type: z.string().min(1, '请选择协议运行类型'),
  owner: z.string().min(1, '请输入负责人'),
  priority: z.number().min(0),
  meta: z.string().optional(),
  resume: z.boolean(),
});

type ProtocolRunFormValues = z.infer<typeof protocolRunFormSchema>;

interface SubmitProtocolRunDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSuccess?: (protocolRunName: string) => void;
  protocolSpecs: Record<string, ProtocolSpec>;
  taskSpecs: Record<string, TaskSpec>;
  labSpecs: Record<string, LabSpec>;
  initialProtocolRun?: ProtocolRun | null;
  /** Protocol type to preselect when the dialog opens without a run to clone. */
  initialType?: string;
  generateCloneName: (name: string) => Promise<string>;
}

export function SubmitProtocolRunDialog({
  open,
  onOpenChange,
  onSuccess,
  protocolSpecs,
  taskSpecs,
  labSpecs,
  initialProtocolRun,
  initialType,
  generateCloneName,
}: SubmitProtocolRunDialogProps) {
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [selectedProtocolSpec, setSelectedProtocolSpec] = React.useState<ProtocolSpec | null>(null);

  // Visual editor state - task parameters structure: { task_name: { param_name: ParameterValue } }
  const [taskParameters, setTaskParameters] = React.useState<Record<string, Record<string, ParameterValue>>>({});

  // Track which task sections are expanded (default: all expanded)
  const [expandedTasks, setExpandedTasks] = React.useState<Set<string>>(new Set());
  const [inputSearch, setInputSearch] = React.useState('');
  const [recordVideoChoice, setRecordVideoChoice] = React.useState<boolean | null>(null);
  const [hideShellChoice, setHideShellChoice] = React.useState<boolean | null>(null);

  // Track if we've already populated from initialProtocolRun to prevent re-population
  const populatedFromExpRef = React.useRef<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors },
    reset,
    watch,
    setValue,
  } = useForm<ProtocolRunFormValues>({
    resolver: zodResolver(protocolRunFormSchema),
    defaultValues: {
      name: '',
      type: '',
      owner: '',
      priority: 0,
      meta: '',
      resume: false,
    },
  });

  const protocolType = watch('type');
  const showsMatterixRecording = React.useMemo(
    () => protocolHasRealMatterixDevice(selectedProtocolSpec, labSpecs),
    [selectedProtocolSpec, labSpecs]
  );
  const existingRecordVideo = existingMatterixBoolean(initialProtocolRun?.meta, 'record_video');
  const existingHideShell = existingMatterixBoolean(initialProtocolRun?.meta, 'hide_shell');
  const recordVideo = recordVideoChoice ?? existingRecordVideo === true;
  const hideShell = recordVideo ? (hideShellChoice ?? existingHideShell === true) : false;

  // Create protocol type options from specs
  const protocolTypeOptions = React.useMemo(
    () =>
      Object.entries(protocolSpecs).map(([type, spec]) => ({
        value: type,
        label: type,
        description: spec.desc,
      })),
    [protocolSpecs]
  );

  // Initialize from clone - cloned parameters ARE overrides
  React.useEffect(() => {
    if (initialProtocolRun && populatedFromExpRef.current !== initialProtocolRun.name) {
      populatedFromExpRef.current = initialProtocolRun.name;

      generateCloneName(initialProtocolRun.name).then((name) => setValue('name', name));
      setValue('type', initialProtocolRun.type);
      setValue('owner', initialProtocolRun.owner);
      setValue('priority', initialProtocolRun.priority ?? 0);
      setValue('resume', initialProtocolRun.resume || false);
      setValue(
        'meta',
        hasNonEmptyObject(initialProtocolRun.meta) ? JSON.stringify(initialProtocolRun.meta, null, 2) : ''
      );
      setRecordVideoChoice(null);
      setHideShellChoice(null);

      if (initialProtocolRun.parameters) {
        // Get the protocol spec for filtering
        const expSpec = protocolSpecs[initialProtocolRun.type];

        // Filter out parameters that match spec defaults to avoid false "override" indicators
        const filteredParams = expSpec
          ? filterNonDefaultParameters(initialProtocolRun.parameters, expSpec)
          : initialProtocolRun.parameters;

        const convertedParams = convertParameters(filteredParams);
        setTaskParameters(convertedParams);
        setExpandedTasks(new Set(Object.keys(convertedParams)));
      } else {
        setTaskParameters({});
        setExpandedTasks(new Set());
      }
    }
  }, [initialProtocolRun, protocolSpecs, generateCloneName, setValue]);

  React.useEffect(() => {
    if (open && initialType && !initialProtocolRun) setValue('type', initialType);
    if (open && !initialProtocolRun) {
      setRecordVideoChoice(null);
      setHideShellChoice(null);
    }
  }, [open, initialType, initialProtocolRun, setValue]);

  React.useEffect(() => {
    if (!showsMatterixRecording) {
      setRecordVideoChoice(null);
      setHideShellChoice(null);
    }
  }, [showsMatterixRecording]);

  // Load protocol spec when type changes
  React.useEffect(() => {
    if (protocolType && protocolSpecs[protocolType]) {
      const spec = protocolSpecs[protocolType];
      setSelectedProtocolSpec(spec);

      // Expand all tasks by default (but don't initialize taskParameters - it only holds overrides)
      setExpandedTasks(new Set(spec.tasks.map((t) => t.name)));
    } else {
      setSelectedProtocolSpec(null);
    }
  }, [protocolType, protocolSpecs]);

  const toggleTaskExpansion = (taskName: string) => {
    setExpandedTasks((prev) => {
      const next = new Set(prev);
      if (next.has(taskName)) {
        next.delete(taskName);
      } else {
        next.add(taskName);
      }
      return next;
    });
  };

  const updateTaskParameter = (taskName: string, paramName: string, value: ParameterValue) => {
    setTaskParameters((prev) => ({
      ...prev,
      [taskName]: {
        ...(prev[taskName] || {}),
        [paramName]: value,
      },
    }));
  };

  // Remove an override, reverting to spec default
  const clearTaskParameter = (taskName: string, paramName: string) => {
    setTaskParameters((prev) => {
      const taskParams = prev[taskName];
      if (!taskParams) return prev;

      const { [paramName]: _removed, ...rest } = taskParams;
      void _removed; // Destructuring to remove the key
      if (Object.keys(rest).length === 0) {
        const { [taskName]: _removedTask, ...taskRest } = prev;
        void _removedTask; // Destructuring to remove the key
        return taskRest;
      }
      return { ...prev, [taskName]: rest };
    });
  };

  const handleClear = () => {
    reset({
      name: '',
      type: '',
      owner: '',
      priority: 0,
      meta: '',
      resume: false,
    });

    // Clear all overrides (taskParameters only holds overrides)
    setTaskParameters({});

    // Keep tasks expanded
    if (selectedProtocolSpec) {
      setExpandedTasks(new Set(selectedProtocolSpec.tasks.map((t) => t.name)));
    } else {
      setExpandedTasks(new Set());
    }

    setError(null);
    populatedFromExpRef.current = null; // Reset so we can clone again if needed
    setRecordVideoChoice(null);
    setHideShellChoice(null);
  };

  const onSubmit = async (data: ProtocolRunFormValues) => {
    setIsSubmitting(true);
    setError(null);

    try {
      // Create task type map from protocol spec
      const taskTypeMap: Record<string, string> = {};
      if (selectedProtocolSpec) {
        selectedProtocolSpec.tasks.forEach((task) => {
          taskTypeMap[task.name] = task.type;
        });
      }

      const parameters = extractParameterValues(taskParameters, taskTypeMap, taskSpecs);
      const parsedMeta = data.meta ? JSON.parse(data.meta) : null;
      const meta = showsMatterixRecording ? mergeMatterixRecordingMeta(parsedMeta, recordVideo, hideShell) : parsedMeta;

      const protocolRunDefinition: ProtocolRunDefinition = {
        name: data.name,
        type: data.type,
        owner: data.owner,
        priority: data.priority,
        parameters: Object.keys(parameters).length > 0 ? parameters : {},
        meta,
        resume: data.resume,
      };

      const result = await submitProtocolRun(protocolRunDefinition);

      if (result.success) {
        onOpenChange(false);
        onSuccess?.(data.name);
      } else {
        setError(result.error || '提交协议运行失败');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '某个字段的输入无效');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <BaseSubmitDialog
      open={open}
      onOpenChange={onOpenChange}
      title="提交协议运行"
      submitLabel="提交协议运行"
      isSubmitting={isSubmitting}
      error={error}
      onSubmit={handleSubmit(onSubmit)}
      headerActions={
        <Button variant="outline" size="sm" onClick={handleClear} className="gap-2">
          <Eraser className="w-4 h-4" />
          清空
        </Button>
      }
    >
      {/* Basic Fields */}
      <div className="space-y-2">
        <Label htmlFor="name">协议运行名称 *</Label>
        <Input id="name" {...register('name')} error={errors.name?.message} placeholder="my_protocol_run" />
      </div>

      <div className="space-y-2">
        <Label htmlFor="type">协议 *</Label>
        <Combobox
          options={protocolTypeOptions}
          value={protocolType}
          onChange={(value) => setValue('type', value, { shouldValidate: true })}
          placeholder="选择协议"
          searchPlaceholder="搜索协议…"
          emptyText="未找到协议"
        />
        {errors.type && <p className="text-sm text-red-600">{errors.type.message}</p>}
      </div>

      <div className="space-y-2">
        <Label htmlFor="owner">负责人 *</Label>
        <Input id="owner" {...register('owner')} error={errors.owner?.message} placeholder="user1" />
      </div>

      <div className="space-y-2">
        <Label htmlFor="priority">优先级</Label>
        <Input
          id="priority"
          type="number"
          {...register('priority', { valueAsNumber: true })}
          error={errors.priority?.message}
        />
      </div>

      <div className="flex items-center gap-2">
        <input
          id="resume"
          type="checkbox"
          {...register('resume')}
          className="h-4 w-4 rounded border-gray-300 dark:border-slate-600 dark:bg-slate-800 text-blue-600 focus:ring-blue-600"
        />
        <Label htmlFor="resume" className="cursor-pointer">
          若已存在则恢复运行
        </Label>
      </div>

      {showsMatterixRecording && (
        <div className="space-y-3 rounded-md border border-gray-200 bg-gray-50 p-3 dark:border-slate-700 dark:bg-slate-800/50">
          <div>
            <div className="text-sm font-medium text-gray-900 dark:text-white">Matterix 执行设置</div>
            <p className="text-xs text-gray-500 dark:text-gray-400">这些设置覆盖本次协议中各任务原有的录制配置。</p>
          </div>
          <label className="flex items-center gap-2 text-sm text-gray-800 dark:text-gray-200">
            <input
              type="checkbox"
              checked={recordVideo}
              onChange={(event) => {
                const checked = event.target.checked;
                setRecordVideoChoice(checked);
                if (!checked) setHideShellChoice(false);
              }}
              className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-600 dark:border-slate-600 dark:bg-slate-800"
            />
            录制整个协议视频
          </label>
          <label className="flex items-center gap-2 text-sm text-gray-800 dark:text-gray-200">
            <input
              type="checkbox"
              checked={hideShell}
              disabled={!recordVideo}
              onChange={(event) => {
                setHideShellChoice(event.target.checked);
              }}
              className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-600 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-600 dark:bg-slate-800"
            />
            隐藏移液站外壳
          </label>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            开启后会录制所有真实 Matterix 任务，并按执行顺序合成为一个完整协议视频；不包含任务间调度等待的空白时间。
          </p>
        </div>
      )}

      {/* Task Parameters Section */}
      {selectedProtocolSpec && selectedProtocolSpec.tasks.length > 0 && (
        <div className="space-y-4 pt-2 border-t border-gray-200 dark:border-slate-700">
          <div className="space-y-2">
            <Label className="text-base">任务参数</Label>
            <ParameterSearchInput value={inputSearch} onChange={setInputSearch} />
          </div>

          {selectedProtocolSpec.tasks.map((taskConfig) => {
            const taskSpec = taskSpecs[taskConfig.type];
            if (!taskSpec || !taskSpec.input_parameters || Object.keys(taskSpec.input_parameters).length === 0) {
              return null;
            }

            const searchLower = inputSearch.toLowerCase();
            const taskNameMatches =
              !searchLower ||
              taskConfig.name.toLowerCase().includes(searchLower) ||
              taskConfig.type.toLowerCase().includes(searchLower);

            const matchesLeaf = (paramName: string, spec: ParameterSpec) =>
              !searchLower ||
              taskNameMatches ||
              paramName.toLowerCase().includes(searchLower) ||
              (spec.desc && spec.desc.toLowerCase().includes(searchLower)) ||
              spec.type.toLowerCase().includes(searchLower);

            type VisibleItem =
              | { kind: 'param'; name: string; spec: ParameterSpec }
              | { kind: 'group'; name: string; params: Array<{ name: string; spec: ParameterSpec }> };

            const visible: VisibleItem[] = [];
            for (const item of iterateInputParameters(taskSpec.input_parameters)) {
              if (item.kind === 'param') {
                if (matchesLeaf(item.name, item.spec)) {
                  visible.push({ kind: 'param', name: item.name, spec: item.spec });
                }
                continue;
              }
              const groupNameMatches = !searchLower || item.name.toLowerCase().includes(searchLower);
              const keptLeaves: Array<{ name: string; spec: ParameterSpec }> = [];
              for (const [leafName, leafSpec] of Object.entries(item.params)) {
                if (groupNameMatches || matchesLeaf(leafName, leafSpec)) {
                  keptLeaves.push({ name: leafName, spec: leafSpec });
                }
              }
              if (keptLeaves.length > 0) {
                visible.push({ kind: 'group', name: item.name, params: keptLeaves });
              }
            }

            if (searchLower && !taskNameMatches && visible.length === 0) return null;

            const isExpanded = searchLower ? true : expandedTasks.has(taskConfig.name);

            const renderLeaf = (paramName: string, paramSpec: ParameterSpec) => (
              <ProtocolRunParameterField
                key={paramName}
                paramName={paramName}
                paramSpec={paramSpec}
                value={taskParameters[taskConfig.name]?.[paramName]}
                specDefault={taskConfig.parameters?.[paramName]}
                taskSpecDefault={paramSpec.value}
                onChange={(value) => updateTaskParameter(taskConfig.name, paramName, value)}
                onClear={() => clearTaskParameter(taskConfig.name, paramName)}
              />
            );

            return (
              <div
                key={taskConfig.name}
                className="border border-gray-200 dark:border-slate-700 rounded-md bg-gray-50 dark:bg-slate-800/50"
              >
                {/* Collapsible Header */}
                <div className="flex items-center pr-3 hover:bg-gray-100 dark:hover:bg-slate-700/50 transition-colors rounded-t-md">
                  <button
                    type="button"
                    tabIndex={-1}
                    aria-expanded={isExpanded}
                    onClick={() => toggleTaskExpansion(taskConfig.name)}
                    className="flex-1 min-w-0 px-3 py-2.5 flex items-center gap-2 text-left rounded-t-md"
                  >
                    {isExpanded ? (
                      <ChevronDown className="h-4 w-4 text-gray-500 dark:text-gray-400" />
                    ) : (
                      <ChevronRight className="h-4 w-4 text-gray-500 dark:text-gray-400" />
                    )}
                    <span>
                      <span className="block text-sm font-medium text-gray-900 dark:text-white">{taskConfig.name}</span>
                      <span className="block text-xs text-gray-500 dark:text-gray-400">类型： {taskConfig.type}</span>
                    </span>
                  </button>
                  {taskConfig.desc && <DescriptionTooltip description={taskConfig.desc} />}
                </div>

                {/* Collapsible Content */}
                {isExpanded && (
                  <div className="px-3 pt-3 pb-3 space-y-4 border-t border-gray-200 dark:border-slate-700">
                    {visible.map((item) => {
                      if (item.kind === 'param') return renderLeaf(item.name, item.spec);
                      return (
                        <div key={`group-${item.name}`} className="space-y-2 pt-1">
                          <div className="flex items-center gap-2">
                            <span className="text-xs font-bold uppercase tracking-wide text-black dark:text-white whitespace-nowrap">
                              {item.name}
                            </span>
                            <div className="flex-1 h-px bg-gray-200 dark:bg-slate-700" />
                          </div>
                          <div className="space-y-4">{item.params.map(({ name, spec }) => renderLeaf(name, spec))}</div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Metadata Section */}
      <div className="space-y-2 border-t border-gray-200 dark:border-slate-700 pt-4">
        <Label htmlFor="meta">元数据（JSON）</Label>
        <Textarea
          id="meta"
          {...register('meta')}
          error={errors.meta?.message}
          placeholder='{"description": "Test protocol run"}'
        />
      </div>
    </BaseSubmitDialog>
  );
}
