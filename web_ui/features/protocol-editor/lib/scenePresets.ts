import type { StaticDeviceAssignment, TaskNode } from '@/lib/types/protocol';

export interface CapabilityParameter {
  type: 'number' | 'boolean' | 'integer' | 'string';
  default: number | boolean | string;
  choices?: Array<string | number>;
  label: string;
  min?: number | null;
  max?: number | null;
  unit?: string;
}

export interface ScenePreset {
  id: string;
  label: string;
  description: string;
  group: string;
  device_slot?: string;
  parameter_specs?: Record<string, CapabilityParameter>;
  task: Omit<TaskNode, 'name' | 'position'>;
}

export interface EditorScene {
  lab_name: string;
  device_name: string;
  scene_id: string;
  label: string;
  loaded: boolean;
  chain_device: boolean;
  presets: ScenePreset[];
}

export interface BoundPreset extends ScenePreset {
  key: string;
  scene: EditorScene;
}

export function bindScenePresets(scenes: EditorScene[]): BoundPreset[] {
  return scenes.flatMap((scene) =>
    scene.presets.map((preset) => ({
      ...preset,
      scene,
      key: `${scene.lab_name}/${scene.device_name}/${preset.id}`,
    }))
  );
}

function resolveDevice(
  task: TaskNode,
  slot: string,
  tasks: TaskNode[],
  visited = new Set<string>()
): StaticDeviceAssignment | undefined {
  if (visited.has(task.name)) return;
  visited.add(task.name);
  const assignment = task.devices?.[slot];
  if (typeof assignment === 'string') {
    const [name, refSlot] = assignment.split('.');
    const referenced = tasks.find((t) => t.name === name);
    return referenced ? resolveDevice(referenced, refSlot, tasks, visited) : undefined;
  }
  if (assignment && 'lab_name' in assignment) return assignment;
}

export function matchesSceneDevice(task: TaskNode, preset: BoundPreset, tasks: TaskNode[]): boolean {
  if (!preset.device_slot) return true;
  const device = resolveDevice(task, preset.device_slot, tasks);
  return device?.lab_name === preset.scene.lab_name && device.name === preset.scene.device_name;
}

export function findTaskPreset(task: TaskNode, presets: BoundPreset[], tasks: TaskNode[]): BoundPreset | undefined {
  return presets.find(
    (preset) =>
      preset.task.type === task.type &&
      preset.task.parameters?.capability_id === task.parameters?.capability_id &&
      (task.type !== 'Matterix End Experiment' ||
        (preset.task.parameters?.expected_phase === 0) === (task.parameters?.expected_phase === 0)) &&
      matchesSceneDevice(task, preset, tasks)
  );
}

/** The generic capability task nests its operation parameters; typed capability tasks keep them flat. */
export function presetParameterValues(task: TaskNode): Record<string, unknown> | undefined {
  return task.type === 'Matterix Capability'
    ? (task.parameters?.parameters as Record<string, unknown> | undefined)
    : task.parameters;
}

export function withPresetParameterValues(task: TaskNode, values: Record<string, unknown>): TaskNode['parameters'] {
  return task.type === 'Matterix Capability' ? { ...task.parameters, parameters: values } : values;
}

export function sceneTaskLabel(task: TaskNode, preset: BoundPreset | undefined): string | undefined {
  if (!preset) return undefined;
  const spec = preset.parameter_specs?.volume_ul;
  const volume = presetParameterValues(task)?.volume_ul ?? spec?.default;
  return spec && typeof volume === 'number' ? `${preset.label} · ${volume} µL` : preset.label;
}

export function createPresetTask(
  preset: BoundPreset,
  name: string,
  position: TaskNode['position'],
  tasks: TaskNode[]
): TaskNode {
  const task: TaskNode = { ...structuredClone(preset.task), name, position, desc: preset.label };
  const slot = preset.device_slot;
  if (!slot) return task;
  task.devices = { [slot]: { lab_name: preset.scene.lab_name, name: preset.scene.device_name } };
  if (preset.scene.chain_device) {
    task.device_holds = { [slot]: !['end_experiment', 'end_custom_experiment', 'release_beaker'].includes(preset.id) };
    const previous = tasks.findLast((t) => t.devices?.[slot] && matchesSceneDevice(t, preset, tasks));
    if (previous) {
      // Steps on one station must stay ordered, including a new experiment after the previous one released it
      task.dependencies = [previous.name];
      if (preset.id !== 'begin_experiment' && previous.type !== 'Matterix End Experiment')
        task.devices[slot] = `${previous.name}.${slot}`;
    }
  }
  return task;
}
