'use client';

import { useCallback, useState, useEffect, useRef, useMemo } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  type Node,
  type Edge,
  type Connection,
  type NodeTypes,
  type NodeChange,
  addEdge,
  useNodesState,
  useEdgesState,
  useReactFlow,
  BackgroundVariant,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Panel, Group, Separator } from 'react-resizable-panels';
import { useTheme } from 'next-themes';
import { TaskNode } from './TaskNode';
import { Toolbar } from './Toolbar';
import { ContextMenu } from './ContextMenu';
import { NewTaskModal } from './NewTaskModal';
import { TaskPalette, TASK_DRAG_TYPE, CAPABILITY_DRAG_TYPE } from './TaskPalette';
import { getEditorScenes } from '@/features/editor/api/scenes';
import {
  bindScenePresets,
  createPresetTask,
  findTaskPreset,
  sceneTaskLabel,
  type BoundPreset,
  type EditorScene,
} from '../lib/scenePresets';
import { TaskPropertiesPanel } from './TaskPropertiesPanel';
import { ColorPicker } from './ColorPicker';
import { ExportImageDialog } from './ExportImageDialog';
import { ConfirmDialog } from '@/components/dialogs/ConfirmDialog';
import { ToastContainer } from '@/components/ui/Toast';
import { useToast } from '@/components/ui/useToast';
import { OptimizerEditorPanel } from './OptimizerEditorPanel';
import { ValidationErrorsPanel } from './ValidationErrorsPanel';
import { useEditorStore } from '@/lib/stores/editorStore';
import { useProtocolValidation } from '@/hooks/useProtocolValidation';
import type { TaskSpec, TaskNodeData, TaskNode as TaskNodeType } from '@/lib/types/protocol';
import { flattenInputParameters } from '@/lib/utils/paramGroups';
import { EDITOR_LAYOUT, getEdgeColors } from '@/lib/constants/theme';
import { performAutoLayout } from '../utils/autolayout';
import { resolveOverlaps } from '../utils/preventOverlaps';
import { isAncestor } from '../utils/dependencyGraph';
import { extractRunIfRefs, runIfHandleId } from '@/lib/utils/runIf';

const nodeTypes: NodeTypes = { taskNode: TaskNode };
const GRID_SIZE = EDITOR_LAYOUT.gridSize;

// Helper functions extracted outside component for performance
const createEdge = (
  source: string,
  target: string,
  sourceHandle: string,
  targetHandle: string,
  style?: React.CSSProperties,
  extra?: Partial<Edge>
): Edge => ({
  id: `${source}-${target}-${sourceHandle}-${targetHandle}`,
  source,
  target,
  sourceHandle,
  targetHandle,
  type: 'default',
  ...(style && { style }),
  ...extra,
});

const parseTaskRef = (value: unknown): [string, string] | null => {
  if (typeof value === 'string' && value.includes('.')) {
    const [taskId, param] = value.split('.');
    return [taskId, param];
  }
  return null;
};

// File references are 'task.filename.ext'; split on the FIRST dot so the filename keeps its extension.
const parseFileRef = (value: unknown): [string, string] | null => {
  if (typeof value === 'string') {
    const i = value.indexOf('.');
    if (i > 0 && i < value.length - 1) return [value.slice(0, i), value.slice(i + 1)];
  }
  return null;
};

const snapToGrid = (pos: { x: number; y: number }): { x: number; y: number } => ({
  x: Math.round(pos.x / GRID_SIZE) * GRID_SIZE,
  y: Math.round(pos.y / GRID_SIZE) * GRID_SIZE,
});

// Connection type helpers
const isMainDependencyConnection = (sourceHandle: string, targetHandle: string): boolean =>
  sourceHandle.includes('main-output') && targetHandle.includes('main-input');

const isDeviceConnection = (sourceHandle: string, targetHandle: string): boolean =>
  sourceHandle.includes('-output-device-') && targetHandle.includes('-input-device-');

const isResourceConnection = (sourceHandle: string, targetHandle: string): boolean =>
  sourceHandle.includes('-output-resource-') && targetHandle.includes('-input-resource-');

const isParameterConnection = (sourceHandle: string, targetHandle: string): boolean =>
  sourceHandle.includes('-output-parameter-') && targetHandle.includes('-input-parameter-');

const isFileConnection = (sourceHandle: string, targetHandle: string): boolean =>
  sourceHandle.includes('-output-file-') && targetHandle.includes('-input-file-');

export function ProtocolEditor() {
  const { resolvedTheme } = useTheme();
  // Manual validation — triggered by the Validate button
  const { validate } = useProtocolValidation();

  const tasks = useEditorStore((state) => state.tasks);
  const taskTemplates = useEditorStore((state) => state.taskTemplates);
  const labSpecs = useEditorStore((state) => state.labSpecs);
  const labs = useEditorStore((state) => state.labs);
  const setLabs = useEditorStore((state) => state.setLabs);
  const selectedNodeName = useEditorStore((state) => state.selectedNodeName);
  const isPropertiesPanelOpen = useEditorStore((state) => state.isPropertiesPanelOpen);
  const clipboard = useEditorStore((state) => state.clipboard);
  const addTask = useEditorStore((state) => state.addTask);
  const updateTask = useEditorStore((state) => state.updateTask);
  const deleteTask = useEditorStore((state) => state.deleteTask);
  const copyNodes = useEditorStore((state) => state.copyNodes);
  const pasteNodes = useEditorStore((state) => state.pasteNodes);
  const setIsPropertiesPanelOpen = useEditorStore((state) => state.setIsPropertiesPanelOpen);
  const getNextTaskName = useEditorStore((state) => state.getNextTaskName);
  const batchOperation = useEditorStore((state) => state.batchOperation);
  const needsOverlapResolution = useEditorStore((state) => state.needsOverlapResolution);
  const storedViewport = useEditorStore((state) => state.viewport);
  const setStoredViewport = useEditorStore((state) => state.setViewport);

  // Edge colors that adapt to theme - memoized to prevent infinite loops
  const edgeColors = useMemo(() => getEdgeColors(resolvedTheme === 'dark'), [resolvedTheme]);

  const { screenToFlowPosition, getViewport } = useReactFlow();
  const [nodes, setNodes, applyNodesChange] = useNodesState<Node<TaskNodeData>>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [contextMenu, setContextMenu] = useState<{
    position: { x: number; y: number };
    nodeId: string | null;
    edgeId: string | null;
  } | null>(null);
  const [isNewTaskModalOpen, setIsNewTaskModalOpen] = useState(false);
  const [isExportDialogOpen, setIsExportDialogOpen] = useState(false);
  const [newNodePosition, setNewNodePosition] = useState({ x: 0, y: 0 });
  const [colorPickerState, setColorPickerState] = useState<{
    nodeId: string;
    position: { x: number; y: number };
  } | null>(null);
  const [confirmDialog, setConfirmDialog] = useState<{
    title: string;
    message: string;
    onConfirm: () => void;
  } | null>(null);
  const { toasts, showToast, closeToast } = useToast();
  const [selectedNodes, setSelectedNodes] = useState<string[]>([]);
  const mousePositionRef = useRef({ x: 0, y: 0 });
  const [scenes, setScenes] = useState<EditorScene[]>([]);
  const [scenesLoading, setScenesLoading] = useState(true);
  const [scenesError, setScenesError] = useState<string | null>(null);
  const [sceneReload, setSceneReload] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setScenesLoading(true);
    setScenesError(null);
    getEditorScenes()
      .then((data) => {
        if (!cancelled) setScenes(data);
      })
      .catch((error: unknown) => {
        if (!cancelled) setScenesError(error instanceof Error ? error.message : '读取场景能力失败');
      })
      .finally(() => {
        if (!cancelled) setScenesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sceneReload, labSpecs]);
  const scenePresets = useMemo(
    () => bindScenePresets(scenes).filter((p) => taskTemplates.some((t) => t.type === p.task.type)),
    [scenes, taskTemplates]
  );

  // Save viewport to store on unmount so it survives navigation
  const getViewportRef = useRef(getViewport);
  getViewportRef.current = getViewport;
  useEffect(() => {
    return () => {
      setStoredViewport(getViewportRef.current());
    };
  }, [setStoredViewport]);

  // Stable callback refs that don't change
  const onNodeClickRef = useRef((nodeName: string) => {
    useEditorStore.getState().setSelectedNodeName(nodeName);
  });

  const onNodeContextMenuRef = useRef((event: React.MouseEvent, nodeName: string) => {
    event.preventDefault();
    event.stopPropagation();

    setContextMenu({
      position: { x: event.clientX, y: event.clientY },
      nodeId: nodeName,
      edgeId: null,
    });
  });

  const onPortDoubleClickRef = useRef(
    (nodeName: string, kind: 'parameter' | 'device' | 'resource' | 'file', name: string) => {
      const store = useEditorStore.getState();
      store.setSelectedNodeName(nodeName);
      store.setFocusedField({ taskName: nodeName, kind, name });
    }
  );

  // Memoize task lookup map for performance
  const taskMap = useMemo(() => new Map(tasks.map((t) => [t.name, t])), [tasks]);
  const templateMap = useMemo(() => new Map(taskTemplates.map((t) => [t.type, t])), [taskTemplates]);

  // Sync nodes
  useEffect(() => {
    const missingTemplates = new Set<string>();
    const validNodes = tasks.map((task) => {
      const taskSpec = templateMap.get(task.type);
      const isMissingSpec = !taskSpec;
      if (isMissingSpec) {
        missingTemplates.add(task.type);
      }
      return {
        id: task.name,
        type: 'taskNode',
        position: task.position,
        data: {
          taskNode: task,
          displayLabel: sceneTaskLabel(task, findTaskPreset(task, scenePresets, tasks)),
          taskSpec: taskSpec ?? ({ type: task.type, desc: '', device_types: [] } as TaskSpec),
          isMissingSpec,
          onNodeClick: onNodeClickRef.current,
          onNodeContextMenu: onNodeContextMenuRef.current,
          onPortDoubleClick: onPortDoubleClickRef.current,
        },
      };
    });

    // Show toast if any templates are missing
    if (missingTemplates.size > 0) {
      showToast(
        'error',
        '缺少任务定义',
        `数据库中没有以下任务类型（软件包可能尚未加载）：${Array.from(missingTemplates).join(', ')}`
      );
    }

    setNodes(validNodes);

    // Transfer pending overlap resolution request now that nodes are synced.
    // Both setNodes and setState are batched by React 18, so the overlap
    // resolution effect will see the new nodes + the active flag together.
    if (useEditorStore.getState().overlapResolutionRequested) {
      useEditorStore.setState({
        overlapResolutionRequested: false,
        needsOverlapResolution: true,
      });
    }
  }, [tasks, templateMap, scenePresets, setNodes, showToast]);

  // Sync edges - create edges from task dependencies and references
  useEffect(() => {
    const flowEdges: Edge[] = [];

    const addReferenceEdges = (
      task: TaskNodeType,
      assignments: Record<string, unknown> | undefined,
      portType: 'device' | 'resource' | 'parameter',
      color: string
    ) => {
      Object.entries(assignments || {}).forEach(([name, value]) => {
        // One edge per source; fan-in (2+ alternates into one port) renders dense-dashed.
        const isArray = Array.isArray(value);
        const isFanin = isArray && value.length >= 2;
        const refs = (isArray ? value : [value]).map(parseTaskRef);
        refs.forEach((ref) => {
          if (ref && taskMap.has(ref[0])) {
            flowEdges.push(
              createEdge(
                ref[0],
                task.name,
                `${ref[0]}-output-${portType}-${ref[1]}`,
                `${task.name}-input-${portType}-${name}`,
                isFanin ? { stroke: color, strokeDasharray: '2 2' } : { stroke: color }
              )
            );
          }
        });
      });
    };

    tasks.forEach((task) => {
      // Dependency edges
      task.dependencies?.forEach((depName) => {
        flowEdges.push(
          createEdge(depName, task.name, `${depName}-main-output`, `${task.name}-main-input`, {
            strokeWidth: 4,
            stroke: edgeColors.dependency,
          })
        );
      });

      // Device, Resource, and Parameter edges
      addReferenceEdges(task, task.devices, 'device', edgeColors.device);
      addReferenceEdges(task, task.resources, 'resource', edgeColors.resource);
      addReferenceEdges(task, task.parameters, 'parameter', edgeColors.parameter);

      // File edges (single ref; filename keeps its extension)
      Object.entries(task.files || {}).forEach(([name, value]) => {
        const ref = parseFileRef(value);
        if (ref && taskMap.has(ref[0])) {
          flowEdges.push(
            createEdge(ref[0], task.name, `${ref[0]}-output-file-${ref[1]}`, `${task.name}-input-file-${name}`, {
              stroke: edgeColors.file,
            })
          );
        }
      });

      // run_if reference edges (read-only visualization).
      // Skip when the source has no matching output_parameter handle (otherwise React Flow logs an error).
      for (const ref of extractRunIfRefs(task.run_if)) {
        const refTask = taskMap.get(ref.task);
        if (!refTask) continue;
        const refSpec = templateMap.get(refTask.type);
        if (!refSpec?.output_parameters || !(ref.output in refSpec.output_parameters)) continue;
        flowEdges.push(
          createEdge(
            ref.task,
            task.name,
            `${ref.task}-output-parameter-${ref.output}`,
            runIfHandleId(task.name, ref),
            { stroke: edgeColors.runif, strokeDasharray: '4 3', opacity: 0.5 },
            { deletable: false } // read-only visualization; hoverable/selectable but not deletable
          )
        );
      }
    });

    setEdges(flowEdges);
  }, [tasks, taskMap, templateMap, setEdges, edgeColors]);

  // Post-render overlap resolution: after AI updates, wait for React Flow to measure
  // nodes, then resolve any overlaps using actual dimensions
  useEffect(() => {
    if (!needsOverlapResolution || nodes.length < 2) return;

    const allMeasured = nodes.every((n) => n.measured?.width && n.measured?.height);
    if (!allMeasured) return;

    const resolved = resolveOverlaps(nodes);
    useEditorStore.setState({ needsOverlapResolution: false });
    if (!resolved) return;

    setNodes(resolved);
    batchOperation(() => {
      resolved.forEach((node) => {
        updateTask(node.id, { position: node.position });
      });
    });
  }, [nodes, needsOverlapResolution, setNodes, batchOperation, updateTask]);

  // Handle device/resource/parameter port connections
  const handlePortConnection = useCallback(
    (
      connection: Connection,
      sourceHandle: string,
      targetHandle: string,
      field: 'devices' | 'resources' | 'parameters' | 'files',
      portType: string,
      edgeColor: string
    ) => {
      const sourceTask = taskMap.get(connection.source!);
      const targetTask = taskMap.get(connection.target!);
      if (!sourceTask || !targetTask) return;

      const sourceSpec = templateMap.get(sourceTask.type);
      const targetSpec = templateMap.get(targetTask.type);
      if (!sourceSpec || !targetSpec) return;

      // Parse handle IDs to extract parameter names
      const sourceParam = sourceHandle.split(`-output-${portType}-`)[1];
      const targetParam = targetHandle.split(`-input-${portType}-`)[1];

      if (!sourceParam || !targetParam) {
        console.error('无法解析端口名称', { sourceHandle, targetHandle, sourceParam, targetParam });
        return;
      }

      // Determine source and target types based on field. Files have no type, so skip the type check.
      if (field !== 'files') {
        let sourceType: string | undefined;
        let targetType: string | undefined;

        if (field === 'devices') {
          sourceType = sourceSpec.output_devices?.[sourceParam]?.type || sourceSpec.input_devices?.[sourceParam]?.type;
          targetType = targetSpec.input_devices?.[targetParam]?.type;
        } else if (field === 'resources') {
          sourceType =
            sourceSpec.output_resources?.[sourceParam]?.type || sourceSpec.input_resources?.[sourceParam]?.type;
          targetType = targetSpec.input_resources?.[targetParam]?.type;
        } else {
          sourceType = sourceSpec.output_parameters?.[sourceParam]?.type;
          targetType = flattenInputParameters(targetSpec.input_parameters)[targetParam]?.type;
        }

        if (sourceType !== targetType) {
          showToast('error', '连接错误', `无法连接：类型不匹配！源类型：${sourceType}，目标类型：${targetType}`);
          return;
        }
      }

      // Source must be a transitive dependency of target; add a direct one unless that would form a cycle.
      let dependencies = targetTask.dependencies;
      if (!isAncestor(taskMap, connection.target!, connection.source!)) {
        if (connection.source === connection.target || isAncestor(taskMap, connection.source!, connection.target!)) {
          showToast(
            'error',
            '连接错误',
            `无法连接：会产生循环依赖（“${connection.source}”已经依赖“${connection.target}”）。`
          );
          return;
        }
        dependencies = [...(targetTask.dependencies || []), connection.source!];
        setEdges((eds) =>
          addEdge(
            {
              source: connection.source!,
              target: connection.target!,
              sourceHandle: `${connection.source}-main-output`,
              targetHandle: `${connection.target}-main-input`,
              style: { strokeWidth: 4, stroke: edgeColors.dependency },
            },
            eds
          )
        );
        showToast('info', '已自动添加依赖', `“${connection.target}”现在依赖“${connection.source}”。`);
      }

      // For parameters, a second source onto an existing reference builds a fan-in list.
      const newRef = `${connection.source}.${sourceParam}`;
      const fieldValues = (targetTask[field] || {}) as Record<string, unknown>;
      const existing = fieldValues[targetParam];
      let nextValue: string | string[] = newRef;
      if (field === 'parameters' && Array.isArray(existing)) {
        const refs = existing as string[];
        nextValue = refs.includes(newRef) ? refs : [...refs, newRef];
      } else if (field === 'parameters' && typeof existing === 'string' && parseTaskRef(existing)) {
        nextValue = existing === newRef ? existing : [existing, newRef];
      }
      updateTask(connection.target!, {
        dependencies,
        [field]: { ...fieldValues, [targetParam]: nextValue },
      });

      // Add edge with appropriate color
      setEdges((eds) => addEdge({ ...connection, style: { stroke: edgeColor } }, eds));
    },
    [taskMap, templateMap, updateTask, setEdges, showToast, edgeColors]
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      if (!connection.source || !connection.target) return;

      const sourceHandle = connection.sourceHandle || '';
      const targetHandle = connection.targetHandle || '';

      // run_if ports are visualization-only; reject any user-initiated connection to them.
      if (targetHandle.includes('-input-runif-')) return;

      // Main dependency connection
      if (isMainDependencyConnection(sourceHandle, targetHandle)) {
        if (connection.source === connection.target || isAncestor(taskMap, connection.source, connection.target)) {
          showToast(
            'error',
            '连接错误',
            `无法连接：会产生循环依赖（“${connection.source}”已经依赖“${connection.target}”）。`
          );
          return;
        }
        const targetTask = taskMap.get(connection.target);
        if (targetTask && !targetTask.dependencies?.includes(connection.source)) {
          updateTask(connection.target, { dependencies: [...(targetTask.dependencies || []), connection.source] });
        }
        setEdges((eds) => addEdge({ ...connection, style: { strokeWidth: 4, stroke: edgeColors.dependency } }, eds));
        return;
      }

      // Device/Resource/Parameter connections
      if (isDeviceConnection(sourceHandle, targetHandle)) {
        handlePortConnection(connection, sourceHandle, targetHandle, 'devices', 'device', edgeColors.device);
        return;
      }

      if (isResourceConnection(sourceHandle, targetHandle)) {
        handlePortConnection(connection, sourceHandle, targetHandle, 'resources', 'resource', edgeColors.resource);
        return;
      }

      if (isParameterConnection(sourceHandle, targetHandle)) {
        handlePortConnection(connection, sourceHandle, targetHandle, 'parameters', 'parameter', edgeColors.parameter);
        return;
      }

      if (isFileConnection(sourceHandle, targetHandle)) {
        handlePortConnection(connection, sourceHandle, targetHandle, 'files', 'file', edgeColors.file);
        return;
      }

      // Mixed types - not allowed
      if (sourceHandle.includes('output-') && targetHandle.includes('input-')) {
        const getPortType = (handle: string) =>
          handle.includes('-device-')
            ? 'device'
            : handle.includes('-resource-')
              ? 'resource'
              : handle.includes('-file-')
                ? 'file'
                : 'parameter';
        const sourcePortType = getPortType(sourceHandle);
        const targetPortType = getPortType(targetHandle);
        showToast(
          'error',
          '连接错误',
          `无法连接：${sourcePortType}只能连接到 ${sourcePortType}（不能连接到 ${targetPortType}）`
        );
        return;
      }

      setEdges((eds) => addEdge(connection, eds));
    },
    [taskMap, updateTask, setEdges, edgeColors, handlePortConnection, showToast]
  );

  const handleContextMenu = useCallback(
    (event: React.MouseEvent | MouseEvent, item?: Node | Edge) => {
      event.preventDefault();
      const isEdge = item && 'source' in item;

      // If we clicked on a node that's part of a multi-selection, show multi-select menu
      if (!isEdge && item && selectedNodes.length > 1 && selectedNodes.includes(item.id)) {
        setContextMenu({
          position: { x: event.clientX, y: event.clientY },
          nodeId: item.id,
          edgeId: null,
        });
        return;
      }

      // If nodes are selected and we're right-clicking on pane, show node context menu
      if (selectedNodes.length > 0 && !item) {
        setContextMenu({
          position: { x: event.clientX, y: event.clientY },
          nodeId: selectedNodes[0], // Use first selected node as reference
          edgeId: null,
        });
        return;
      }

      // run_if edges are read-only visualization; don't offer edge actions (e.g. delete) on them.
      const isRunIfEdge = isEdge && ((item as Edge).targetHandle || '').includes('-input-runif-');

      setContextMenu({
        position: { x: event.clientX, y: event.clientY },
        nodeId: item && !isEdge ? item.id : null,
        edgeId: isEdge && !isRunIfEdge ? (item as Edge).id : null,
      });
    },
    [selectedNodes]
  );

  const handleNewTask = useCallback(() => {
    setNewNodePosition(contextMenu?.position || { x: 0, y: 0 });
    setIsNewTaskModalOpen(true);
  }, [contextMenu]);

  const handleDeleteEdge = useCallback(() => {
    if (!contextMenu?.edgeId) return;

    const edge = edges.find((e) => e.id === contextMenu.edgeId);
    if (!edge) return;

    const targetHandle = edge.targetHandle || '';
    const targetTask = taskMap.get(edge.target);
    if (!targetTask) return;

    // Delete main dependency
    if (targetHandle.includes('main-input')) {
      updateTask(edge.target, { dependencies: targetTask.dependencies?.filter((d) => d !== edge.source) || [] });
      return;
    }

    // Delete device, resource, parameter, or file
    let targetParam: string;
    let field: 'devices' | 'resources' | 'parameters' | 'files';

    if (targetHandle.includes('-input-device-')) {
      targetParam = targetHandle.split('-input-device-')[1];
      field = 'devices';
    } else if (targetHandle.includes('-input-resource-')) {
      targetParam = targetHandle.split('-input-resource-')[1];
      field = 'resources';
    } else if (targetHandle.includes('-input-parameter-')) {
      targetParam = targetHandle.split('-input-parameter-')[1];
      field = 'parameters';
    } else if (targetHandle.includes('-input-file-')) {
      targetParam = targetHandle.split('-input-file-')[1];
      field = 'files';
    } else {
      return;
    }

    if (!targetParam) return;

    const updated = { ...(targetTask[field] || {}) } as Record<string, unknown>;
    const existing = updated[targetParam];
    if (field === 'parameters' && Array.isArray(existing)) {
      // Drop only the deleted source from the fan-in; collapse to a plain reference at one left.
      const sourceParam = (edge.sourceHandle || '').split('-output-parameter-')[1];
      const remaining = (existing as string[]).filter((r) => r !== `${edge.source}.${sourceParam}`);
      if (remaining.length === 0) delete updated[targetParam];
      else updated[targetParam] = remaining.length === 1 ? remaining[0] : remaining;
    } else {
      delete updated[targetParam];
    }
    updateTask(edge.target, { [field]: updated });
  }, [contextMenu, edges, taskMap, updateTask]);

  const handleSetColor = useCallback(() => {
    if (contextMenu?.nodeId && contextMenu?.position) {
      setColorPickerState({
        nodeId: contextMenu.nodeId,
        position: contextMenu.position,
      });
      setContextMenu(null);
    }
  }, [contextMenu]);

  const addTaskAt = useCallback(
    (template: TaskSpec, screenPosition: { x: number; y: number }) => {
      addTask({
        name: getNextTaskName(),
        type: template.type,
        position: snapToGrid(screenToFlowPosition(screenPosition)),
        devices: {},
        resources: {},
        parameters: {},
        desc: template.desc,
      });
    },
    [addTask, screenToFlowPosition, getNextTaskName]
  );

  const handleSelectTemplate = useCallback(
    (template: TaskSpec) => addTaskAt(template, newNodePosition),
    [addTaskAt, newNodePosition]
  );

  const addPresetAt = useCallback(
    (preset: BoundPreset, screenPosition: { x: number; y: number }) => {
      const state = useEditorStore.getState();
      batchOperation(() => {
        if (!state.labs.includes(preset.scene.lab_name)) setLabs([...state.labs, preset.scene.lab_name]);
        const task = createPresetTask(
          preset,
          getNextTaskName(),
          snapToGrid(screenToFlowPosition(screenPosition)),
          state.tasks
        );
        useEditorStore.setState({ overlapResolutionRequested: true });
        addTask(task);
        state.setSelectedNodeName(task.name);
        setIsPropertiesPanelOpen(true);
      });
    },
    [batchOperation, setLabs, getNextTaskName, screenToFlowPosition, addTask, setIsPropertiesPanelOpen]
  );

  // Task palette: drag a task onto the canvas, or click + to drop it in the middle of the visible canvas
  const canvasRef = useRef<HTMLDivElement>(null);
  const taskUsage = useMemo(() => {
    const counts = new Map<string, number>();
    tasks.forEach((t) => {
      const preset = findTaskPreset(t, scenePresets, tasks);
      if (preset) counts.set(preset.key, (counts.get(preset.key) ?? 0) + 1);
    });
    return counts;
  }, [tasks, scenePresets]);

  const handleCanvasDragOver = useCallback((event: React.DragEvent) => {
    if (!event.dataTransfer.types.includes(TASK_DRAG_TYPE) && !event.dataTransfer.types.includes(CAPABILITY_DRAG_TYPE))
      return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
  }, []);

  const handleCanvasDrop = useCallback(
    (event: React.DragEvent) => {
      const preset = scenePresets.find((p) => p.key === event.dataTransfer.getData(CAPABILITY_DRAG_TYPE));
      if (preset) {
        event.preventDefault();
        addPresetAt(preset, { x: event.clientX, y: event.clientY });
        return;
      }
      const template = templateMap.get(event.dataTransfer.getData(TASK_DRAG_TYPE));
      if (!template) return;
      event.preventDefault();
      addTaskAt(template, { x: event.clientX, y: event.clientY });
    },
    [templateMap, addTaskAt, scenePresets, addPresetAt]
  );

  const handlePaletteAdd = useCallback(
    (preset: BoundPreset) => {
      const rect = canvasRef.current?.getBoundingClientRect();
      if (!rect) return;
      addPresetAt(preset, { x: rect.left + rect.width / 2, y: rect.top + rect.height / 3 });
    },
    [addPresetAt]
  );

  const handleNodesChange = useCallback(
    (changes: NodeChange<Node<TaskNodeData>>[]) => {
      applyNodesChange(changes);

      changes.forEach((change) => {
        if (
          change.type === 'position' &&
          change.position &&
          (change.dragging === false || change.dragging === undefined)
        ) {
          updateTask(change.id, { position: snapToGrid(change.position) });
        }
      });
    },
    [applyNodesChange, updateTask]
  );

  // Track selected nodes from React Flow
  const handleSelectionChange = useCallback(({ nodes }: { nodes: Node[] }) => {
    setSelectedNodes(nodes.map((n) => n.id));
  }, []);

  // Track mouse position for paste
  const handleMouseMove = useCallback((event: React.MouseEvent) => {
    mousePositionRef.current = { x: event.clientX, y: event.clientY };
  }, []);

  // Copy handler
  const handleCopy = useCallback(() => {
    const nodesToCopy = selectedNodes.length > 0 ? selectedNodes : contextMenu?.nodeId ? [contextMenu.nodeId] : [];
    if (nodesToCopy.length === 0) return;

    copyNodes(nodesToCopy);
    showToast('success', '已复制', `已复制 ${nodesToCopy.length} 个任务`);
  }, [selectedNodes, contextMenu, copyNodes, showToast]);

  // Paste handler
  const handlePaste = useCallback(() => {
    if (clipboard.length === 0) return;

    const flowPosition = screenToFlowPosition(mousePositionRef.current);
    const pastedNodeIds = pasteNodes(flowPosition);
    showToast('success', '已粘贴', `已粘贴 ${pastedNodeIds.length} 个任务`);
  }, [clipboard, screenToFlowPosition, pasteNodes, showToast]);

  // Update delete to handle multiple selected nodes
  const handleDelete = useCallback(() => {
    const nodesToDelete = selectedNodes.length > 0 ? selectedNodes : contextMenu?.nodeId ? [contextMenu.nodeId] : [];
    if (nodesToDelete.length === 0) return;

    // Use batchOperation for multiple deletes to group into single undo operation
    if (nodesToDelete.length > 1) {
      batchOperation(() => {
        nodesToDelete.forEach((nodeId) => deleteTask(nodeId));
      });
    } else {
      // Single delete doesn't need batch
      deleteTask(nodesToDelete[0]);
    }
  }, [selectedNodes, contextMenu, deleteTask, batchOperation]);

  // Global context menu handler for multi-select
  useEffect(() => {
    const handleGlobalContextMenu = (event: MouseEvent) => {
      const target = event.target as HTMLElement;
      const isInReactFlow = target.closest('.react-flow');

      if (isInReactFlow && selectedNodes.length > 0) {
        const isNode = target.closest('.react-flow__node');
        const isEdge = target.closest('.react-flow__edge');

        if (!isNode && !isEdge) {
          // Clicking on the selection box or pane - show multi-select menu
          event.preventDefault();
          event.stopPropagation();
          setContextMenu({
            position: { x: event.clientX, y: event.clientY },
            nodeId: selectedNodes[0],
            edgeId: null,
          });
        }
      }
    };

    document.addEventListener('contextmenu', handleGlobalContextMenu);
    return () => document.removeEventListener('contextmenu', handleGlobalContextMenu);
  }, [selectedNodes]);

  // Keyboard shortcuts
  useEffect(() => {
    const undo = useEditorStore.getState().undo;
    const redo = useEditorStore.getState().redo;

    const handleKeyDown = (event: KeyboardEvent) => {
      // Check if user is typing in an input field
      const target = event.target as HTMLElement;
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA') return;

      // Undo: Ctrl+Z (Windows/Linux) or Cmd+Z (Mac)
      if ((event.metaKey || event.ctrlKey) && event.key === 'z' && !event.shiftKey) {
        event.preventDefault();
        undo();
        return;
      }

      // Redo: Ctrl+Shift+Z or Ctrl+Y (Windows/Linux) or Cmd+Shift+Z (Mac)
      if ((event.metaKey || event.ctrlKey) && ((event.shiftKey && event.key === 'z') || event.key === 'y')) {
        event.preventDefault();
        redo();
        return;
      }

      if ((event.metaKey || event.ctrlKey) && event.key === 'c') {
        event.preventDefault();
        handleCopy();
      }

      if ((event.metaKey || event.ctrlKey) && event.key === 'v') {
        event.preventDefault();
        handlePaste();
      }

      // Delete: Delete or Backspace key
      if (event.key === 'Delete' || event.key === 'Backspace') {
        event.preventDefault();
        handleDelete();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleCopy, handlePaste, handleDelete]);

  // Auto Layout Handler
  const handleAutoLayout = useCallback(() => {
    // Show confirmation dialog before applying layout
    setConfirmDialog({
      title: '应用自动布局？',
      message: '这将按从左到右的层级结构自动排列所有节点。',
      onConfirm: () => {
        // Calculate new layout using auto-layout utility
        const { nodes: newNodes, edges: newEdges } = performAutoLayout(nodes, edges);

        // Update React Flow State
        setNodes(newNodes);
        setEdges(newEdges);

        // Use batchOperation to group all position updates into single undo operation
        batchOperation(() => {
          newNodes.forEach((node) => {
            updateTask(node.id, { position: node.position });
          });
        });

        showToast('success', '布局已应用', '节点已按从左到右的顺序自动排列。');
        setConfirmDialog(null);
      },
    });
  }, [nodes, edges, setNodes, setEdges, updateTask, batchOperation, showToast]);

  const selectedTask = useMemo(() => taskMap.get(selectedNodeName || ''), [taskMap, selectedNodeName]);
  const selectedTaskSpec = useMemo(
    () =>
      selectedTask
        ? (templateMap.get(selectedTask.type) ?? ({ type: selectedTask.type, desc: '', device_types: [] } as TaskSpec))
        : null,
    [selectedTask, templateMap]
  );
  const isSelectedTaskMissingSpec = useMemo(
    () => (selectedTask ? !templateMap.has(selectedTask.type) : false),
    [selectedTask, templateMap]
  );

  const isOptimizerPanelOpen = useEditorStore((state) => state.isOptimizerPanelOpen);
  const setSelectedNodeName = useEditorStore((state) => state.setSelectedNodeName);

  const handleSelectTaskFromError = useCallback(
    (taskName: string) => {
      setSelectedNodeName(taskName);
    },
    [setSelectedNodeName]
  );

  // Calculate default sizes based on what's open (must add up to 100%)
  const getCanvasSize = () => {
    const sidePanels = [isOptimizerPanelOpen, isPropertiesPanelOpen && selectedTask].filter(Boolean).length;
    if (sidePanels === 2) return '45%';
    if (isOptimizerPanelOpen) return '70%';
    if (isPropertiesPanelOpen) return '78%';
    return '100%';
  };

  const getTaskPropertiesSize = () => {
    return '22%';
  };

  return (
    <div className="h-full w-full flex flex-col overflow-hidden">
      <Toolbar
        labSpecs={labSpecs}
        onAutoLayout={handleAutoLayout}
        onExportImage={() => setIsExportDialogOpen(true)}
        onValidate={validate}
      />

      <Group orientation="horizontal" className="flex-1 overflow-hidden">
        {/* Main Canvas Panel */}
        <Panel id="canvas-panel" defaultSize={getCanvasSize()} minSize="30%">
          <div className="h-full flex flex-col">
            <div className="flex-1 flex min-h-0">
              <TaskPalette
                scenes={scenes}
                presets={scenePresets}
                selectedLabs={labs}
                usage={taskUsage}
                loading={scenesLoading}
                error={scenesError}
                onRefresh={() => setSceneReload((n) => n + 1)}
                onSelectScene={(labName) => {
                  if (!labs.includes(labName)) setLabs([...labs, labName]);
                }}
                onAdd={handlePaletteAdd}
                onAdvanced={handleNewTask}
              />
              <div ref={canvasRef} className="flex-1 relative bg-gray-50 dark:bg-slate-950">
                <ReactFlow
                  nodes={nodes}
                  edges={edges}
                  onNodesChange={handleNodesChange}
                  onEdgesChange={onEdgesChange}
                  onConnect={onConnect}
                  onSelectionChange={handleSelectionChange}
                  onPaneContextMenu={(e) => handleContextMenu(e)}
                  onNodeContextMenu={(e, node) => handleContextMenu(e, node)}
                  onEdgeContextMenu={(e, edge) => handleContextMenu(e, edge)}
                  onMouseMove={handleMouseMove}
                  onDragOver={handleCanvasDragOver}
                  onDrop={handleCanvasDrop}
                  nodeTypes={nodeTypes}
                  selectionOnDrag
                  snapToGrid
                  snapGrid={[GRID_SIZE, GRID_SIZE]}
                  autoPanOnNodeDrag={false}
                  autoPanOnConnect={false}
                  onlyRenderVisibleElements
                  elevateNodesOnSelect={false}
                  zoomOnDoubleClick={false}
                  minZoom={0.1}
                  defaultViewport={storedViewport}
                  proOptions={{ hideAttribution: true }}
                >
                  <Background variant={BackgroundVariant.Dots} gap={GRID_SIZE} size={1.5} />
                  <Controls />
                </ReactFlow>

                <ContextMenu
                  position={contextMenu?.position || null}
                  nodeId={contextMenu?.nodeId || null}
                  edgeId={contextMenu?.edgeId || null}
                  selectedNodeCount={selectedNodes.length || (contextMenu?.nodeId ? 1 : 0)}
                  hasClipboard={clipboard.length > 0}
                  onNewTask={handleNewTask}
                  onDeleteTask={handleDelete}
                  onDeleteEdge={handleDeleteEdge}
                  onSetColor={handleSetColor}
                  onCopy={handleCopy}
                  onPaste={handlePaste}
                  onClose={() => setContextMenu(null)}
                />

                {colorPickerState && (
                  <ColorPicker
                    currentColor={taskMap.get(colorPickerState.nodeId)?.color}
                    position={colorPickerState.position}
                    onColorSelect={(color) => {
                      updateTask(colorPickerState.nodeId, { color });
                      setColorPickerState(null);
                    }}
                    onClose={() => setColorPickerState(null)}
                  />
                )}

                <NewTaskModal
                  isOpen={isNewTaskModalOpen}
                  onClose={() => setIsNewTaskModalOpen(false)}
                  templates={taskTemplates}
                  onSelectTemplate={handleSelectTemplate}
                />

                {confirmDialog && (
                  <ConfirmDialog
                    isOpen={!!confirmDialog}
                    onClose={() => setConfirmDialog(null)}
                    onConfirm={confirmDialog.onConfirm}
                    title={confirmDialog.title}
                    message={confirmDialog.message}
                  />
                )}

                <ExportImageDialog isOpen={isExportDialogOpen} onClose={() => setIsExportDialogOpen(false)} />

                <ToastContainer toasts={toasts} onClose={closeToast} />
              </div>
            </div>
            <ValidationErrorsPanel onSelectTask={handleSelectTaskFromError} />
          </div>
        </Panel>

        {/* Resize Handle - Only show when task properties panel is open AND a task is selected */}
        {isPropertiesPanelOpen && selectedTask && (
          <>
            <Separator className="w-1 bg-gray-200 dark:bg-gray-700 hover:bg-blue-500 dark:hover:bg-yellow-500 transition-colors" />

            {/* Task Properties Panel */}
            <Panel id="task-properties-panel" defaultSize={getTaskPropertiesSize()} minSize="20%" maxSize="40%">
              <TaskPropertiesPanel
                isOpen={isPropertiesPanelOpen}
                taskNode={selectedTask}
                taskSpec={selectedTaskSpec || null}
                preset={selectedTask ? findTaskPreset(selectedTask, scenePresets, tasks) : undefined}
                isMissingSpec={isSelectedTaskMissingSpec}
                labSpecs={labSpecs}
                selectedLabs={labs}
                onClose={() => setIsPropertiesPanelOpen(false)}
                onUpdate={updateTask}
              />
            </Panel>
          </>
        )}

        {/* Resize Handle - Only show when optimizer panel is open */}
        {isOptimizerPanelOpen && (
          <>
            <Separator className="w-1 bg-gray-200 dark:bg-gray-700 hover:bg-blue-500 dark:hover:bg-yellow-500 transition-colors" />

            {/* Optimizer Panel */}
            <Panel id="optimizer-panel" defaultSize="30%" minSize="20%" maxSize="50%">
              <OptimizerEditorPanel />
            </Panel>
          </>
        )}
      </Group>
    </div>
  );
}
