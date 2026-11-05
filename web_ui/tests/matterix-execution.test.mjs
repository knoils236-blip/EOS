import assert from 'node:assert/strict';
import { test } from 'node:test';
import ts from 'typescript';
import { fileURLToPath } from 'node:url';
import { registerHooks } from 'node:module';

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
    if (specifier.startsWith('@/')) {
      return nextResolve(fileURLToPath(new URL(`../${specifier.slice(2)}.ts`, import.meta.url)), context);
    }
    if (specifier.startsWith('.') && context.parentURL?.endsWith('.ts')) {
      return nextResolve(fileURLToPath(new URL(`${specifier}.ts`, context.parentURL)), context);
    }
    return nextResolve(specifier, context);
  },
});

const {
  artifactDownloadUrl,
  existingMatterixBoolean,
  extractMatterixChartGroups,
  formatMatterixScientific,
  formatMatterixCompactValue,
  matterixAcceptanceChecks,
  matterixAcceptanceRows,
  matterixChartTimeLabel,
  matterixExecutionPassed,
  matterixProtocolTaskRows,
  matterixRecording,
  matterixRecordingStatusLabel,
  matterixResultSummary,
  matterixSnapshotGroups,
  matterixSnapshotMetadata,
  matterixVideoNotice,
  mergeMatterixRecordingMeta,
  mergeMatterixSamples,
  parseMatterixProtocolExecutionDetail,
  protocolHasRealMatterixDevice,
  protocolRunUsesMatterix,
  readBoundedResponseText,
  shouldPollMatterixExecution,
} = await import('../features/protocol-runs/utils/matterixExecution.ts');
const { matterixCapabilityLabel, matterixFieldLabel, matterixTaskLabel } = await import('../lib/labels.ts');

test('bridge terminal values and calibration are readable in Chinese', () => {
  assert.equal(formatMatterixCompactValue('succeeded'), '已完成');
  assert.equal(formatMatterixCompactValue('timed_out'), '已超时');
  assert.equal(formatMatterixCompactValue('verified'), '已验证');
  assert.equal(formatMatterixCompactValue('unknown_field_value'), 'unknown_field_value');
});

test('mixture phase acceptance exposes individual actual and expected checks', () => {
  const checks = matterixAcceptanceChecks({
    passed: false,
    stages: {
      stock: { checks: [{ name: '液量', actual: 100, expected: 100, passed: true }] },
      diluent: { checks: [{ name: 'TIP', actual: false, expected: true, passed: false }] },
    },
  });
  assert.deepEqual(
    checks.map((check) => check.name),
    ['stock · 液量', 'diluent · TIP']
  );
  assert.equal(checks[1].passed, false);
  assert.equal(
    matterixAcceptanceChecks({ acceptance_checks: [{ name: 'door_end', actual: 0.39, passed: true }] })[0].name,
    'door_end'
  );
});

test('acceptance summaries keep flat stage fields and omit history arrays', () => {
  const rows = matterixAcceptanceRows({
    phase_continuous: true,
    semantic_history: [{ huge: 'not rendered' }],
    stages: {
      door: { passed: true, door_start: 0, door_end: 0.39 },
      stock: { passed: true, stock_ul: 74, diluent_ul: 126 },
    },
  });
  assert.deepEqual(
    rows.map((row) => row.key),
    [
      'phase_continuous',
      'door.passed',
      'door.door_start',
      'door.door_end',
      'stock.passed',
      'stock.stock_ul',
      'stock.diluent_ul',
    ]
  );
  assert.equal(matterixFieldLabel('door_end'), '门实际位置');
  assert.equal(matterixFieldLabel('unknown_saved_field'), 'unknown_saved_field');
});

test('mock and synthetic result summaries use saved values without dumping large arrays', () => {
  const summary = matterixResultSummary({
    status: 'COMPLETED',
    source: 'mock',
    sample: {
      sample_id: 'sample-1',
      backend: 'mock',
      stock_ul: 74,
      diluent_ul: 126,
      stock_concentration_mm: 1,
      composition_source: 'mock_material_balance',
      hardware_calibrated: false,
      operations: Array.from({ length: 1000 }, () => ({ huge: true })),
    },
    observation: {
      source: 'synthetic_model',
      backend: 'mock',
      replicates: [0.000123, 0.000124, 0.000123],
      value_mm: 0.000123456,
      uncertainty_mm: 3e-7,
      blank_mm: 0,
      reference_mm: 1,
      seed: 20261102,
      model_version: 'synthetic_concentration_v1',
    },
    quality: {
      eligible: true,
      reasons: [],
      failure_kind: 'none',
      policy_version: 'mixture_qc_v1',
    },
    semantic_history: Array.from({ length: 1000 }, () => ({ huge: true })),
  });
  const rows = summary.sections.flatMap((section) => section.rows);
  assert.equal(summary.syntheticModel, true);
  assert.equal(rows.find((row) => row.key === 'stock_ul').value, '74 µL');
  assert.equal(rows.find((row) => row.key === 'value_mm').value, '1.23456e-4 mM');
  assert.equal(rows.find((row) => row.key === 'eligible').value, '是');
  assert.equal(
    rows.some((row) => row.key.includes('semantic_history') || row.key.includes('operations')),
    false
  );
  assert.equal(formatMatterixScientific(2.5e-9), '2.5e-9');
});

test('score result preserves scientific loss and saved synthetic source', () => {
  const summary = matterixResultSummary({
    target_mm: 0.37,
    measured_mm: 0.369,
    uncertainty_mm: 0.00002,
    loss: 2.5e-9,
    source: 'synthetic_model',
    observation_id: 'obs-1',
  });
  const rows = summary.sections.flatMap((section) => section.rows);
  assert.equal(summary.syntheticModel, true);
  assert.equal(rows.find((row) => row.key === 'loss').value, '2.5e-9 mM²');
  assert.equal(rows.find((row) => row.key === 'source').value, '合成模型（synthetic_model）');
});
function detail(samples, source = 'simulation') {
  return {
    schema_version: 1,
    protocol_run_name: 'run-1',
    task_name: 'task-1',
    scope: 'task',
    status: 'RUNNING',
    source,
    snapshot: { submitted: {}, effective: {} },
    samples,
    latest_sample: samples.at(-1) ?? null,
    acceptance: null,
    videos: [],
    files: [],
    result: null,
    warnings: [],
  };
}

test('samples merge by request id and simulation time, keep latest duplicate, and cap at 5000', () => {
  const current = [
    { request_id: 'r1', sim_time_s: 0, value: 1 },
    { request_id: 'r1', sim_time_s: 1, value: 2 },
  ];
  const incoming = [
    { request_id: 'r1', sim_time_s: 1, value: 3 },
    { request_id: 'r1', sim_time_s: 2, value: 4 },
  ];
  assert.deepEqual(mergeMatterixSamples(current, incoming), [
    { request_id: 'r1', sim_time_s: 0, value: 1 },
    { request_id: 'r1', sim_time_s: 1, value: 3 },
    { request_id: 'r1', sim_time_s: 2, value: 4 },
  ]);

  const many = Array.from({ length: 5002 }, (_, index) => ({
    request_id: 'r2',
    sim_time_s: index,
  }));
  const merged = mergeMatterixSamples([], many);
  assert.equal(merged.length, 5000);
  assert.equal(merged[0].sim_time_s, 2);
  assert.equal(merged.at(-1).sim_time_s, 5001);
});

test('a completed archive fills early history in time order without inventing missing timestamps', () => {
  const merged = mergeMatterixSamples(
    [{ request_id: 'r1', sim_time_s: 10 }],
    [
      { request_id: 'r1', sim_time_s: 1 },
      { request_id: 'r1', sim_time_s: 10 },
    ]
  );
  assert.deepEqual(
    merged.map((point) => point.sim_time_s),
    [1, 10]
  );
  assert.deepEqual(extractMatterixChartGroups(detail([{ semantics: { liquid_volumes_ml: { vial: 1 } } }])), []);
});

test('station curves use actual liquid volumes and only explicitly unit-tagged joints', () => {
  const withUnits = detail([
    {
      request_id: 'r1',
      sim_time_s: 0,
      semantics: {
        liquid_volumes_ml: { target: 0 },
        jointsSI: {
          carriage_x1: { value: 0.1, unit: 'm' },
          rotate_large_cap: { value: Math.PI, unit: 'rad' },
        },
      },
    },
    {
      request_id: 'r1',
      sim_time_s: 1,
      semantics: {
        liquid_volumes_ml: { target: 0.1 },
        jointsSI: {
          carriage_x1: { value: 0.12, unit: 'm' },
          rotate_large_cap: { value: Math.PI / 2, unit: 'rad' },
        },
      },
    },
  ]);
  const groups = extractMatterixChartGroups(withUnits);
  assert.equal(groups.find((group) => group.key === 'liquid-delta').series[0].points.at(-1).y, 100);
  assert.equal(groups.find((group) => group.key === 'joint-linear').series[0].points.at(-1).y, 120);
  assert.equal(groups.find((group) => group.key === 'joint-angular').series[0].points.at(-1).y, 90);

  const withoutUnits = detail([
    {
      request_id: 'r1',
      sim_time_s: 0,
      semantics: {
        liquid_volumes_ml: { target: 0 },
        jointsSI: { carriage_x1: 0.1, rotate_large_cap: 3.14 },
      },
    },
    {
      request_id: 'r1',
      sim_time_s: 1,
      semantics: {
        liquid_volumes_ml: { target: 0.1 },
        jointsSI: { carriage_x1: 0.12, rotate_large_cap: 1.57 },
      },
    },
  ]);
  assert.equal(
    extractMatterixChartGroups(withoutUnits).some((group) => group.key.startsWith('joint-')),
    false
  );
});

test('station semantics jointsSI positions use units.joints metadata from the worker', () => {
  const samples = detail([
    {
      request_id: 'r1',
      sim_time_s: 0,
      semantics: {
        jointsSI: { carriage_x1: 0.1, rotate_large_cap: Math.PI },
        units: { joints: { carriage_x1: 'm', rotate_large_cap: 'rad' } },
      },
    },
    {
      request_id: 'r1',
      sim_time_s: 1,
      semantics: {
        jointsSI: { carriage_x1: 0.12, rotate_large_cap: Math.PI / 2 },
        units: { joints: { carriage_x1: 'm', rotate_large_cap: 'rad' } },
      },
    },
  ]);
  const groups = extractMatterixChartGroups(samples);
  assert.equal(groups.find((group) => group.key === 'joint-linear').series[0].points.at(-1).y, 120);
  assert.equal(groups.find((group) => group.key === 'joint-angular').series[0].points.at(-1).y, 90);
});

test('Franka changed samples retain all seven arm and two finger curves', () => {
  const samples = detail([
    {
      request_id: 'r1',
      sim_time_s: 0,
      beaker: { position_w: [0, 0, 0.1] },
      units: { arm_joint_position: 'rad', gripper_joint_position: 'm' },
      robot: {
        joint_names: [
          'panda_joint1',
          'panda_joint2',
          'panda_joint3',
          'panda_joint4',
          'panda_joint5',
          'panda_joint6',
          'panda_joint7',
          'panda_finger_joint1',
          'panda_finger_joint2',
        ],
        joint_position: [0, 0, 0, 0, 0, 0, 0, 0.01, 0.02],
      },
    },
    {
      request_id: 'r2',
      sim_time_s: 1,
      beaker: { position_w: [0, 0, 0.1] },
      units: { arm_joint_position: 'rad', gripper_joint_position: 'm' },
      robot: {
        joint_names: [
          'panda_joint1',
          'panda_joint2',
          'panda_joint3',
          'panda_joint4',
          'panda_joint5',
          'panda_joint6',
          'panda_joint7',
          'panda_finger_joint1',
          'panda_finger_joint2',
        ],
        joint_position: [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, Math.PI / 2, 0.015, 0.025],
      },
    },
  ]);
  const groups = extractMatterixChartGroups(samples);
  const angular = groups.find((group) => group.key === 'joint-angular');
  const linear = groups.find((group) => group.key === 'joint-linear');
  assert.equal(angular.series.length, 7);
  assert.equal(angular.series.find((series) => series.label === 'panda_joint7').points.at(-1).y, 90);
  assert.deepEqual(Object.fromEntries(linear.series.map((series) => [series.label, series.points.at(-1).y])), {
    panda_finger_joint1: 15,
    panda_finger_joint2: 25,
  });
});

test('all changed liquid and joint series are retained without hard limits', () => {
  const liquidVolumes = Object.fromEntries(Array.from({ length: 12 }, (_, index) => [`bottle_${index}`, index]));
  const joints = Object.fromEntries(
    Array.from({ length: 30 }, (_, index) => [`joint_${index}`, { value: index * 0.001, unit: 'm' }])
  );
  const changedJoints = Object.fromEntries(
    Array.from({ length: 30 }, (_, index) => [`joint_${index}`, { value: (index + 2) * 0.001, unit: 'm' }])
  );
  const groups = extractMatterixChartGroups(
    detail([
      { sim_time_s: 0, semantics: { liquid_volumes_ml: liquidVolumes, jointsSI: joints } },
      {
        sim_time_s: 1,
        semantics: {
          liquid_volumes_ml: Object.fromEntries(
            Object.keys(liquidVolumes).map((key) => [key, liquidVolumes[key] + 0.001])
          ),
          jointsSI: changedJoints,
        },
      },
    ])
  );
  assert.equal(groups.find((group) => group.key === 'liquid-absolute').series.length, 12);
  assert.equal(groups.find((group) => group.key === 'liquid-delta').series.length, 12);
  assert.equal(groups.find((group) => group.key === 'joint-linear').series.length, 30);
});

test('mock never receives mechanism curves', () => {
  const groups = extractMatterixChartGroups(
    detail(
      [
        {
          request_id: 'mock-1',
          sim_time_s: 0,
          robot: { joint_names: ['joint1'], joint_position: [0] },
        },
      ],
      'mock'
    )
  );
  assert.deepEqual(groups, []);
});

test('recording metadata is omitted when untouched and can explicitly clear cloned true values', () => {
  const original = { description: 'keep', matterix: { custom: 'keep' } };
  assert.equal(mergeMatterixRecordingMeta(original, null, null), original);
  assert.equal(
    existingMatterixBoolean({ matterix: { record_video: false, hide_shell: false } }, 'record_video'),
    false
  );
  assert.deepEqual(mergeMatterixRecordingMeta(original, false, false), {
    description: 'keep',
    matterix: { custom: 'keep', record_video: false, hide_shell: false },
  });
});

test('raw previews stop reading after the finite byte limit even when Range is ignored', async () => {
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('a'.repeat(100_000)));
      controller.close();
    },
  });
  const result = await readBoundedResponseText(new Response(body), 64);
  assert.equal(result.text, 'a'.repeat(64));
  assert.equal(result.truncated, true);
});

test('real Matterix controls require a simulation device or mixture Matterix backend', () => {
  const protocol = { type: 'p', desc: '', labs: ['lab'], tasks: [] };
  assert.equal(
    protocolHasRealMatterixDevice(protocol, {
      lab: { name: 'lab', desc: '', devices: { station: { type: 'matterix_workstation', computer: 'c' } } },
    }),
    true
  );
  assert.equal(
    protocolHasRealMatterixDevice(protocol, {
      lab: {
        name: 'lab',
        desc: '',
        devices: {
          mixture: {
            type: 'mixture_workstation',
            computer: 'c',
            init_parameters: { backend: 'matterix' },
          },
        },
      },
    }),
    true
  );
  assert.equal(
    protocolHasRealMatterixDevice(protocol, {
      lab: {
        name: 'lab',
        desc: '',
        devices: {
          mixture: {
            type: 'mixture_workstation',
            computer: 'c',
            init_parameters: { backend: 'mock' },
          },
        },
      },
    }),
    false
  );
});

test('protocol overview detects Matterix from task statuses after its definition is removed', () => {
  assert.equal(
    protocolRunUsesMatterix({
      protocolRunType: 'removed-protocol',
      protocolSpec: null,
      taskStatuses: [{ type: 'Matterix Capability' }],
    }),
    true
  );
  assert.equal(
    protocolRunUsesMatterix({
      protocolRunType: 'mixture_mock',
      protocolSpec: { type: 'mixture_mock', desc: '', labs: ['mixture_mock_lab'], tasks: [] },
      taskStatuses: [{ type: 'Mixture Workstation' }],
    }),
    false
  );
});

test('real Matterix recording controls are shown for static lab devices but not Mock', () => {
  const protocol = {
    type: 'matterix_full_station',
    desc: '',
    labs: ['matterix_pipetting_lab'],
    tasks: [
      {
        name: 'begin',
        type: 'Matterix Begin Experiment',
        devices: {
          matterix: {
            lab_name: 'matterix_pipetting_lab',
            name: 'matterix',
          },
        },
      },
    ],
  };
  assert.equal(
    protocolHasRealMatterixDevice(protocol, {
      matterix_pipetting_lab: {
        name: 'matterix_pipetting_lab',
        desc: '',
        devices: { matterix: { type: 'matterix_workstation', computer: 'eos_computer' } },
      },
    }),
    true
  );
  assert.equal(
    protocolHasRealMatterixDevice(
      { ...protocol, labs: ['mixture_mock_lab'] },
      {
        mixture_mock_lab: {
          name: 'mixture_mock_lab',
          desc: '',
          devices: {
            mixture: {
              type: 'mixture_workstation',
              computer: 'eos_computer',
              init_parameters: { backend: 'mock' },
            },
          },
        },
      }
    ),
    false
  );
});

test('protocol samples merge by task, session, request, scene, and protocol time', () => {
  const merged = mergeMatterixSamples(
    [
      { task_name: 'a', session_id: 's1', request_id: 'r1', protocol_time_s: 1, value: 1 },
      { task_name: 'b', session_id: 's1', request_id: 'r1', protocol_time_s: 1, value: 2 },
    ],
    [
      { task_name: 'a', session_id: 's1', request_id: 'r1', protocol_time_s: 1, value: 3 },
      { task_name: 'a', session_id: 's2', request_id: 'r1', protocol_time_s: 1, value: 4 },
    ]
  );
  assert.deepEqual(
    merged.map((sample) => sample.value),
    [3, 2, 4]
  );
});

test('protocol curves use protocol time and keep scenes and sessions as separate series', () => {
  const protocolSamples = {
    ...detail([]),
    task_name: '__protocol__',
    scope: 'protocol',
    samples: [
      {
        task_name: 'grasp',
        session_id: 's1',
        scene_id: 'scene-a',
        protocol_time_s: 1,
        sim_time_s: 0,
        semantics: {
          liquid_volumes_ml: { target: 0 },
          jointsSI: { carriage_x1: { value: 0.1, unit: 'm' } },
        },
      },
      {
        task_name: 'grasp',
        session_id: 's1',
        scene_id: 'scene-a',
        protocol_time_s: 2,
        sim_time_s: 0,
        semantics: {
          liquid_volumes_ml: { target: 0.1 },
          jointsSI: { carriage_x1: { value: 0.2, unit: 'm' } },
        },
      },
      {
        task_name: 'lift',
        session_id: 's2',
        scene_id: 'scene-b',
        protocol_time_s: 3,
        sim_time_s: 0,
        semantics: {
          liquid_volumes_ml: { target: 0.2 },
          jointsSI: { carriage_x1: { value: 0.05, unit: 'm' } },
        },
      },
      {
        task_name: 'lift',
        session_id: 's2',
        scene_id: 'scene-b',
        protocol_time_s: 4,
        sim_time_s: 0,
        semantics: {
          liquid_volumes_ml: { target: 0.4 },
          jointsSI: { carriage_x1: { value: 0.15, unit: 'm' } },
        },
      },
      {
        task_name: 'missing-time',
        session_id: 's3',
        sim_time_s: 999,
        semantics: {
          liquid_volumes_ml: { target: 9 },
          jointsSI: { carriage_x1: { value: 9, unit: 'm' } },
        },
      },
    ],
  };
  const groups = extractMatterixChartGroups(protocolSamples);
  const liquid = groups.find((group) => group.key === 'liquid-absolute');
  const joint = groups.find((group) => group.key === 'joint-linear');
  assert.equal(matterixChartTimeLabel(protocolSamples), '协议时间（s）');
  assert.equal(liquid.series.length, 2);
  assert.deepEqual(liquid.series.flatMap((series) => series.points.map((point) => point.x)).sort(), [1, 2, 3, 4]);
  assert.equal(joint.series.length, 2);
  assert.equal(
    joint.series.some((series) => series.points.some((point) => point.x === 999)),
    false
  );
});

test('static and numerical-noise telemetry is omitted without falling back to all parameters', () => {
  const groups = extractMatterixChartGroups(
    detail([
      {
        sim_time_s: 0,
        semantics: {
          liquid_volumes_ml: { static: 1, noise: 1, moving: 0 },
          jointsSI: {
            static: { value: 0.1, unit: 'm' },
            noise: { value: 0.1, unit: 'm' },
            moving: { value: 0.1, unit: 'm' },
            angular_noise: { value: 0, unit: 'deg' },
            angular_moving: { value: 0, unit: 'deg' },
          },
        },
      },
      {
        sim_time_s: 1,
        semantics: {
          liquid_volumes_ml: { static: 1, noise: 1.0000005, moving: 0.001 },
          jointsSI: {
            static: { value: 0.1, unit: 'm' },
            noise: { value: 0.1000005, unit: 'm' },
            moving: { value: 0.2, unit: 'm' },
            angular_noise: { value: 0.0005, unit: 'deg' },
            angular_moving: { value: 0.01, unit: 'deg' },
          },
        },
      },
    ])
  );
  assert.deepEqual(
    groups.find((group) => group.key === 'liquid-absolute').series.map((series) => series.label),
    ['moving']
  );
  assert.deepEqual(
    groups.find((group) => group.key === 'liquid-delta').series.map((series) => series.label),
    ['moving']
  );
  assert.deepEqual(
    groups.find((group) => group.key === 'joint-linear').series.map((series) => series.label),
    ['moving']
  );
  assert.deepEqual(
    groups.find((group) => group.key === 'joint-angular').series.map((series) => series.label),
    ['angular_moving']
  );
  assert.deepEqual(
    extractMatterixChartGroups(
      detail([
        {
          sim_time_s: 0,
          semantics: {
            liquid_volumes_ml: { target: 1 },
            jointsSI: { carriage_x1: { value: 0.1, unit: 'm' } },
          },
        },
      ])
    ),
    []
  );
});

test('protocol baselines stay inside each scene/session and legends omit full context ids', () => {
  const firstScene = 'scene-very-long-alpha';
  const firstSession = 'session-very-long-alpha';
  const secondScene = 'scene-very-long-beta';
  const secondSession = 'session-very-long-beta';
  const staticGroups = extractMatterixChartGroups({
    ...detail([
      {
        scene_id: firstScene,
        session_id: firstSession,
        protocol_time_s: 1,
        sim_time_s: 0,
        semantics: { liquid_volumes_ml: { target: 10 } },
      },
      {
        scene_id: firstScene,
        session_id: firstSession,
        protocol_time_s: 2,
        sim_time_s: 0,
        semantics: { liquid_volumes_ml: { target: 10 } },
      },
      {
        scene_id: secondScene,
        session_id: secondSession,
        protocol_time_s: 3,
        sim_time_s: 0,
        semantics: { liquid_volumes_ml: { target: 100 } },
      },
      {
        scene_id: secondScene,
        session_id: secondSession,
        protocol_time_s: 4,
        sim_time_s: 0,
        semantics: { liquid_volumes_ml: { target: 100 } },
      },
    ]),
    scope: 'protocol',
  });
  assert.deepEqual(staticGroups, []);

  const groups = extractMatterixChartGroups({
    ...detail([
      {
        task_name: 'first',
        scene_id: firstScene,
        session_id: firstSession,
        protocol_time_s: 1,
        sim_time_s: 0,
        semantics: { liquid_volumes_ml: { target: 10 } },
      },
      {
        task_name: 'first',
        scene_id: firstScene,
        session_id: firstSession,
        protocol_time_s: 2,
        sim_time_s: 0,
        semantics: { liquid_volumes_ml: { target: 10 } },
      },
      {
        task_name: 'second',
        scene_id: secondScene,
        session_id: secondSession,
        protocol_time_s: 3,
        sim_time_s: 0,
        semantics: { liquid_volumes_ml: { target: 100 } },
      },
      {
        task_name: 'second',
        scene_id: secondScene,
        session_id: secondSession,
        protocol_time_s: 4,
        sim_time_s: 0,
        semantics: { liquid_volumes_ml: { target: 100.1 } },
      },
    ]),
    scope: 'protocol',
  });
  const delta = groups.find((group) => group.key === 'liquid-delta');
  assert.equal(delta.series.length, 1);
  assert.deepEqual(
    delta.series[0].points.map((point) => Number(point.y.toFixed(9))),
    [0, 100]
  );
  assert.doesNotMatch(delta.series[0].label, new RegExp(secondScene));
  assert.doesNotMatch(delta.series[0].label, new RegExp(secondSession));
  assert.match(delta.series[0].label, /场景 sce…beta/);
  assert.match(delta.series[0].label, /会话 ses…beta/);
});

test('historical protocol samples retain unknown scene and session identities', () => {
  const protocol = parseMatterixProtocolExecutionDetail({
    ...detail([]),
    task_name: '__protocol__',
    scope: 'protocol',
    snapshot: { submitted: { parameters: {}, meta: {} }, effective: { parameters: {}, meta: {} } },
    samples: [{ task_name: 't', sim_time_s: 1, protocol_time_s: 1, session_id: null, scene_id: null }],
    result: { protocol_run: {}, tasks: [{ name: 't', type: 'Matterix Capability', status: 'COMPLETED' }] },
  });
  assert.equal(protocol.samples[0].session_id, null);
});

test('protocol curves connect the same physical container across tasks and retain the full timeline', () => {
  const samples = [0, 1, 2].map((time) => ({
    task_name: time < 2 ? 'prepare' : 'transfer',
    session_id: 's',
    scene_id: 'station',
    protocol_time_s: time,
    sim_time_s: time,
    semantics: { liquid_volumes_ml: { target: time < 2 ? 0 : 0.075 } },
  }));
  const groups = extractMatterixChartGroups({ ...detail(samples), scope: 'protocol' });
  const delta = groups.find((group) => group.key === 'liquid-delta');
  assert.equal(delta.series.length, 1);
  assert.equal(delta.series[0].points.at(-1).y, 75);
  assert.deepEqual(
    mergeMatterixSamples([], samples, 2).map((sample) => sample.protocol_time_s),
    [0, 2]
  );
});

test('real backend protocol shape maps snapshots, nested acceptance, and result tasks', () => {
  const protocol = parseMatterixProtocolExecutionDetail({
    ...detail([]),
    task_name: '__protocol__',
    scope: 'protocol',
    status: 'COMPLETED',
    snapshot: {
      submitted: {
        parameters: {
          grasp: {
            capability_id: 'grasp_beaker',
            timeout_s: 60,
            parameters: { volume_ul: 100 },
          },
          lift: {
            capability_id: 'lift_beaker',
            parameters: { height_m: 0.29 },
          },
        },
        meta: { matterix: { record_video: true, hide_shell: false } },
      },
      effective: {
        parameters: {
          grasp: {
            capability_id: 'grasp_beaker',
            timeout_s: 30,
            parameters: { volume_ul: 100 },
          },
          lift: {
            capability_id: 'lift_beaker',
            parameters: { height_m: 0.29 },
          },
        },
        meta: { matterix: { record_video: true, hide_shell: false } },
      },
    },
    acceptance: {
      passed: true,
      stages: {
        grasp: {
          status: 'COMPLETED',
          task_type: 'Matterix Capability',
          acceptance: {
            passed: true,
            checks: [{ name: '抓取', actual: true, expected: true, passed: true }],
          },
        },
        lift: {
          status: 'COMPLETED',
          task_type: 'Matterix Capability',
          acceptance: null,
        },
      },
    },
    result: {
      protocol_run: { name: 'run-1', status: 'COMPLETED' },
      tasks: [
        { name: 'grasp', type: 'Matterix Capability', status: 'COMPLETED' },
        { name: 'lift', type: 'Matterix Capability', status: 'COMPLETED' },
      ],
    },
    recording: { status: 'pending', complete: false },
  });
  assert.deepEqual(
    matterixSnapshotGroups(protocol).map((group) => group.taskName),
    ['grasp', 'lift']
  );
  assert.deepEqual(
    matterixProtocolTaskRows(protocol).map((task) => [task.taskName, task.label, task.status]),
    [
      ['grasp', '抓取烧杯（100 µL）', 'COMPLETED'],
      ['lift', '抬升烧杯', 'COMPLETED'],
    ]
  );
  assert.equal(matterixSnapshotGroups(protocol)[0].submitted.timeout_s, 60);
  assert.equal(matterixSnapshotGroups(protocol)[0].effective.timeout_s, 30);
  assert.deepEqual(matterixSnapshotGroups(protocol)[0].submitted.parameters, { volume_ul: 100 });
  assert.deepEqual(matterixSnapshotMetadata(protocol), {
    submitted: { matterix: { record_video: true, hide_shell: false } },
    effective: { matterix: { record_video: true, hide_shell: false } },
  });
  assert.deepEqual(
    matterixAcceptanceChecks(protocol.acceptance).map((check) => [check.name, check.passed]),
    [['grasp · 抓取', true]]
  );
  assert.equal(matterixExecutionPassed(protocol), false);
  assert.equal(matterixRecordingStatusLabel(matterixRecording(protocol)), '录制待开始');
  assert.equal(matterixVideoNotice(protocol), '完整协议视频尚未归档；请从下载页查看已有任务产物。');
  assert.equal(shouldPollMatterixExecution(protocol), true);
  assert.throws(
    () =>
      parseMatterixProtocolExecutionDetail({
        ...protocol,
        videos: [
          { name: 'protocol-simulation.mp4', key: 'run/protocol-simulation.mp4', kind: 'video' },
          { name: 'task-simulation.mp4', key: 'run/task/task-simulation.mp4', kind: 'video' },
        ],
      }),
    /Matterix 协议执行详情格式无效/
  );
  assert.throws(
    () =>
      parseMatterixProtocolExecutionDetail({
        ...protocol,
        result: {
          ...protocol.result,
          tasks: [{ task_name: 'grasp', task_type: 'Matterix Capability', status: 'COMPLETED' }],
        },
      }),
    /Matterix 协议执行详情格式无效/
  );

  const completed = {
    ...protocol,
    acceptance: {
      passed: true,
      stages: {
        grasp: {
          status: 'COMPLETED',
          task_type: 'Matterix Capability',
          acceptance: { passed: true },
        },
        lift: {
          status: 'COMPLETED',
          task_type: 'Matterix Capability',
          acceptance: { passed: true },
        },
      },
    },
    recording: { status: 'ready', complete: true },
  };
  assert.equal(matterixExecutionPassed(completed), true);
  assert.equal(shouldPollMatterixExecution(completed), false);
  assert.equal(
    matterixExecutionPassed({
      ...completed,
      acceptance: {
        passed: true,
        stages: {
          grasp: {
            status: 'COMPLETED',
            task_type: 'Matterix Capability',
            acceptance: { passed: true },
          },
        },
      },
    }),
    false
  );
});

test('completed protocol keeps polling while recording is pending or recording', () => {
  const base = {
    ...detail([]),
    task_name: '__protocol__',
    scope: 'protocol',
    status: 'COMPLETED',
    snapshot: {
      submitted: { parameters: {}, meta: {} },
      effective: { parameters: {}, meta: {} },
    },
    result: { protocol_run: { status: 'COMPLETED' }, tasks: [] },
  };
  for (const status of ['pending', 'recording']) {
    assert.equal(shouldPollMatterixExecution({ ...base, recording: { status, complete: false } }), true);
  }
  for (const status of ['ready', 'partial', 'failed', 'unavailable']) {
    assert.equal(
      shouldPollMatterixExecution({
        ...base,
        recording: { status, complete: status === 'ready' },
      }),
      false
    );
  }
});

test('recording status also accepts the API compatibility location under result', () => {
  const taskDetail = {
    ...detail([]),
    result: { recording: { status: 'partial', complete: false, error: 'task cancelled' } },
  };
  assert.equal(matterixRecording(taskDetail).status, 'partial');
  assert.equal(matterixRecordingStatusLabel(matterixRecording(taskDetail)), '仅生成部分任务视频');
});

test('labels and authenticated artifact URLs preserve machine identifiers', () => {
  assert.equal(matterixCapabilityLabel('grasp_beaker'), '抓取烧杯');
  assert.equal(matterixCapabilityLabel('release_beaker'), '松开烧杯');
  assert.equal(matterixCapabilityLabel('station_skill_syringe_aspirate'), '针筒吸液');
  assert.equal(matterixTaskLabel('Matterix Capability', { capability_id: 'lift_beaker' }), '抬升烧杯');
  assert.equal(
    artifactDownloadUrl('run with space/task/证据.zip', true),
    '/api/files/download/run%20with%20space/task/%E8%AF%81%E6%8D%AE.zip?inline=1'
  );
});
