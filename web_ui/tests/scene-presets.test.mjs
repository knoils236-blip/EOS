import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import { registerHooks } from 'node:module';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';
import yaml from 'js-yaml';
import { bindScenePresets, createPresetTask, findTaskPreset, sceneTaskLabel } from '../features/protocol-editor/lib/scenePresets.ts';

registerHooks({
  load(url, context, nextLoad) {
    const result = nextLoad(url, url.endsWith('.ts') ? { ...context, format: 'module' } : context);
    if (!url.endsWith('.ts') || url.includes('/node_modules/')) return result;
    const source = ts.transpileModule(result.source.toString(), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    return { ...result, source, format: 'commonjs' };
  },
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith('@/'))
      return nextResolve(fileURLToPath(new URL(`../${specifier.slice(2)}.ts`, import.meta.url)), context);
    return nextResolve(specifier, context);
  },
});

const { useEditorStore } = await import('../lib/stores/editorStore.ts');
const { serializeCurrentProtocol } = await import('../lib/utils/protocolSerializer.ts');
const { extractHoldsFromRawTasks } = await import('../lib/utils/editor-utils.ts');
const { matterixTaskLabel } = await import('../lib/labels.ts');

const catalog = JSON.parse(
  await readFile(new URL('../../integrations/matterix/bridge/capabilities.json', import.meta.url), 'utf8')
);
const station = {
  lab_name: 'station_lab',
  device_name: 'station',
  scene_id: 'pipetting_realtime',
  label: '移液站',
  loaded: false,
  chain_device: true,
  presets: [
    {
      id: 'begin_experiment',
      label: '实验初始化',
      task: { type: 'Matterix Begin Experiment' },
      device_slot: 'matterix',
    },
    ...catalog
      .filter((c) => c.scene_id === 'pipetting_realtime')
      .map((c) => ({
        id: c.id,
        label: c.label,
        device_slot: 'matterix',
        task: {
          type: 'Matterix Capability',
          parameters: {
            capability_id: c.id,
            parameters: Object.fromEntries(Object.entries(c.parameters).map(([k, v]) => [k, v.default])),
          },
        },
      })),
    {
      id: 'end_experiment',
      label: '结束检查',
      task: { type: 'Matterix End Experiment', parameters: { expected_phase: 7 } },
      device_slot: 'matterix',
    },
  ],
};
const presets = bindScenePresets([station]);
const preset = (id) => presets.find((p) => p.id === id);

test('existing phase volume remains editable, saved and reflected in the node label', () => {
  const phase = preset('station_full_station_transfer_small');
  const editable = { ...phase, parameter_specs: { volume_ul: catalog.find(c => c.id === phase.id).parameters.volume_ul } };
  assert.ok(editable.parameter_specs.volume_ul);
  const task = createPresetTask(editable, 'transfer', {x:0,y:0}, []);
  task.parameters.parameters.volume_ul = 75;
  assert.match(sceneTaskLabel(task, editable), /75 µL/);
  assert.match(matterixTaskLabel(task.type, task.parameters), /75 µL/);
  assert.equal(JSON.parse(JSON.stringify(task)).parameters.parameters.volume_ul, 75);
});

test('repeated Franka motion keeps one device until the independent release step', () => {
  const beakerPresets = bindScenePresets([{
    ...station, lab_name: 'beaker_lab', scene_id: 'franka_beaker',
    presets: catalog.filter((item) => ['grasp_beaker', 'lift_beaker', 'lower_beaker', 'release_beaker'].includes(item.id))
      .map((item) => ({ id: item.id, label: item.label, device_slot: 'matterix', task: {
        type: 'Matterix Capability', parameters: { capability_id: item.id, parameters: {} },
      }})),
  }]);
  const tasks = [];
  for (const id of ['grasp_beaker', 'lift_beaker', 'lift_beaker', 'lower_beaker', 'lower_beaker', 'release_beaker']) {
    tasks.push(createPresetTask(beakerPresets.find((item) => item.id === id), `b${tasks.length}`, { x: 0, y: 0 }, tasks));
  }
  assert.equal(tasks[4].devices.matterix, 'b3.matterix');
  assert.deepEqual(tasks[4].dependencies, ['b3']);
  assert.equal(tasks[4].device_holds.matterix, true);
  assert.equal(tasks[5].device_holds.matterix, false);
});

test('custom lifecycle finish and seven-phase finish remain distinct on reload', () => {
  const withCustom = bindScenePresets([{...station, presets: [{
    id: 'end_custom_experiment', label: '结束自定义实验', device_slot: 'matterix',
    task: { type: 'Matterix End Experiment', parameters: { expected_phase: 0 } },
  }, ...station.presets]}]);
  for (const [id, phase] of [['end_custom_experiment', 0], ['end_experiment', 7]]) {
    const task = createPresetTask(withCustom.find((item) => item.id === id), 'finish', {x:0,y:0}, []);
    assert.equal(task.parameters.expected_phase, phase);
    assert.equal(task.device_holds.matterix, false);
    assert.equal(findTaskPreset(task, withCustom, [task]).id, id);
  }
});

test('operations with the same EOS task type keep distinct labels, IDs, and defaults', () => {
  const open = createPresetTask(preset('station_full_station_open_door'), 'open', { x: 5, y: 10 }, []);
  const transfer = createPresetTask(preset('station_mixture_stock'), 'dose', { x: 10, y: 20 }, []);
  assert.equal(open.type, transfer.type);
  assert.equal(open.parameters.capability_id, 'station_full_station_open_door');
  assert.equal(transfer.parameters.parameters.volume_ul, 100);
  assert.equal(findTaskPreset(open, presets, [open]).label, '开门');
  transfer.parameters.parameters.volume_ul = 74;
  assert.equal(preset('station_mixture_stock').task.parameters.parameters.volume_ul, 100);
});

test('station insertion preserves device ownership through begin, operations and end', () => {
  const tasks = [];
  for (const id of [
    'begin_experiment',
    'station_full_station_open_door',
    'station_full_station_prepare_vial',
    'end_experiment',
  ]) {
    tasks.push(createPresetTask(preset(id), `step${tasks.length}`, { x: 0, y: 0 }, tasks));
  }
  assert.deepEqual(tasks[0].devices.matterix, { lab_name: 'station_lab', name: 'station' });
  assert.equal(tasks[1].devices.matterix, 'step0.matterix');
  assert.equal(tasks[2].devices.matterix, 'step1.matterix');
  assert.deepEqual(tasks[2].dependencies, ['step1']);
  assert.equal(tasks[2].device_holds.matterix, true);
  assert.equal(tasks[3].device_holds.matterix, false);
  assert.equal(findTaskPreset(tasks[2], presets, tasks).id, 'station_full_station_prepare_vial');
  const restored = JSON.parse(JSON.stringify(tasks));
  assert.equal(findTaskPreset(restored[1], presets, restored).label, '开门');
  // A second experiment on the same station starts after the first released it
  const again = createPresetTask(preset('begin_experiment'), 'again', { x: 0, y: 0 }, tasks);
  assert.deepEqual(again.dependencies, ['step3']);
  assert.deepEqual(again.devices.matterix, { lab_name: 'station_lab', name: 'station' });
});

test('scene changes do not chain different workstations or rewrite previous tasks', () => {
  const oldTask = {
    name: 'other',
    type: 'Matterix Capability',
    devices: { matterix: { lab_name: 'other_lab', name: 'station' } },
  };
  const original = JSON.stringify(oldTask);
  const task = createPresetTask(preset('station_full_station_open_door'), 'new', { x: 0, y: 0 }, [oldTask]);
  assert.deepEqual(task.devices.matterix, { lab_name: 'station_lab', name: 'station' });
  assert.equal(task.dependencies, undefined);
  assert.equal(JSON.stringify(oldTask), original);
  assert.equal(
    findTaskPreset({ ...task, parameters: { capability_id: 'not_registered' } }, presets, [task]),
    undefined
  );
});

test('cyclic device references cannot hang discovery or inherit a different device', () => {
  const a = { name: 'a', type: 'Matterix Capability', devices: { matterix: 'b.matterix' } };
  const b = { name: 'b', type: 'Matterix Capability', devices: { matterix: 'a.matterix' } };
  const task = createPresetTask(preset('station_full_station_open_door'), 'new', { x: 0, y: 0 }, [a, b]);
  assert.deepEqual(task.devices.matterix, { lab_name: 'station_lab', name: 'station' });
});

test('actual editor serialization preserves scene identity, nested parameters and device holds on reopen', () => {
  const tasks = [];
  for (const id of ['begin_experiment', 'station_full_station_open_door', 'station_mixture_stock', 'end_experiment']) {
    tasks.push(createPresetTask(preset(id), `step${tasks.length}`, { x: tasks.length * 200, y: 0 }, tasks));
  }
  tasks[2].parameters.parameters.volume_ul = 74;
  useEditorStore.setState({
    protocolType: 'editor_acceptance',
    protocolDesc: '场景能力往返保存',
    labs: ['station_lab'],
    tasks,
    taskTemplates: [...new Set(tasks.map((t) => t.type))].map((type) => ({
      type,
      desc: '',
      device_types: ['matterix_workstation'],
      input_devices: { matterix: { type: 'matterix_workstation' } },
      input_parameters:
        type === 'Matterix Capability'
          ? { capability_id: { type: 'str' }, parameters: { type: 'dict', value: {} } }
          : type === 'Matterix End Experiment'
            ? { expected_phase: { type: 'int', value: 7 } }
            : {},
    })),
  });
  const saved = serializeCurrentProtocol();
  const parsed = yaml.load(saved.yaml);
  assert.deepEqual(parsed.labs, ['station_lab']);
  assert.equal(parsed.tasks[2].parameters.parameters.volume_ul, 74);
  assert.deepEqual(parsed.tasks[2].devices.matterix, { ref: 'step1.matterix', hold: true });
  assert.equal(parsed.tasks[0].devices.matterix.hold, true);
  const restored = extractHoldsFromRawTasks(parsed.tasks);
  assert.equal(restored[2].device_holds.matterix, true);
  assert.equal(findTaskPreset(restored[1], presets, restored).label, '开门');
  assert.deepEqual(JSON.parse(saved.layoutJson).step2, { x: 400, y: 0 });
});

test('deleting a task removes it from the dependencies of the remaining tasks', () => {
  useEditorStore.setState({
    tasks: [
      { name: 'door', type: 'T', position: { x: 0, y: 0 } },
      { name: 'pick', type: 'T', position: { x: 0, y: 0 }, dependencies: ['door', 'begin'] },
    ],
  });
  useEditorStore.getState().deleteTask('door');
  assert.deepEqual(useEditorStore.getState().tasks.map((t) => [t.name, t.dependencies]), [['pick', ['begin']]]);
});
