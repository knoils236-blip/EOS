const STATUS_LABELS: Record<string, string> = {
  CREATED: '已创建',
  RUNNING: '运行中',
  COMPLETED: '已完成',
  FAILED: '失败',
  CANCELLED: '已取消',
  SUSPENDED: '已暂停',
  SKIPPED: '已跳过',
  ACTIVE: '已启用',
  INACTIVE: '已停用',
  INITIAL: '待激活',
  LOCKED: '已锁定',
  DELETED: '已删除',
  UNKNOWN: '未知',
};

export function statusLabel(status: string): string {
  return STATUS_LABELS[status.toUpperCase()] ?? status;
}

const ROLE_LABELS: Record<string, string> = {
  SUPERUSER: '超级用户',
  LAB_ADMIN: '实验室管理员',
  EDITOR: '编辑者',
  SUBMITTER: '提交者',
  VIEWER: '查看者',
};

export function roleLabel(role: string): string {
  return ROLE_LABELS[role.toUpperCase()] ?? role;
}

export const ASSIGNMENT_MODE_LABELS = { static: '静态', dynamic: '动态', reference: '引用' };
export const ENTITY_TYPE_LABELS = { devices: '设备', tasks: '任务', labs: '实验室', protocols: '协议' };

export const MATTERIX_CAPABILITY_LABELS: Record<string, string> = {
  station_skill_pick: '夹取对象',
  station_skill_place: '放回对象',
  station_skill_decap: '打开容器盖',
  station_skill_recap: '关闭容器盖',
  station_skill_mount: '装载耗材',
  station_skill_aspirate: '吸液',
  station_skill_dispense: '吐液',
  station_skill_discard: '弃置工具耗材',
  station_transfer_liquid: '转移液体',
  grasp_beaker: '抓取烧杯',
  lift_beaker: '抬升烧杯',
  lower_beaker: '放下烧杯',
  release_beaker: '松开烧杯',
  pickup_beaker: '抓取并抬升烧杯',
  return_beaker: '放回并松开烧杯',
  wait: '推进仿真时间',
  station_take_drop_tip_big_bottle: 'TIP 与大瓶取放',
  station_small_vial_decap_recap: '小瓶取放与开盖',
  station_pipette_xiaoping_to_zhongping1: '小瓶向中瓶移液',
  station_pipette_zhongping1: '中瓶区自吸自吐',
  station_filter_take_syringe: '过滤机构取针筒',
  station_big_bottle_decap_recap: '大瓶取放与开盖',
  station_full_station_open_door: '打开前门',
  station_full_station_prepare_vial: '准备小瓶',
  station_full_station_transfer_small: '小瓶向中瓶转移',
  station_full_station_open_bottle: '打开大瓶',
  station_full_station_transfer_big: '大瓶定量转移',
  station_full_station_syringe_transfer: '注射器过滤转移',
  station_full_station_close_door: '关闭前门',
  station_skill_mount_tip: '装载 TIP',
  station_skill_eject_tip: '卸载 TIP',
  station_skill_aspirate_from: '从指定区吸液',
  station_skill_dispense_to: '向指定区吐液',
  station_skill_small_vial_pick: '夹取 2 mL 小瓶',
  station_skill_small_vial_place: '放回 2 mL 小瓶',
  station_skill_small_vial_decap: '打开 2 mL 小瓶盖',
  station_skill_small_vial_recap: '关闭 2 mL 小瓶盖',
  station_skill_small_cap_home: '小瓶盖头回静息位',
  station_skill_big_bottle_pick: '夹取 500 mL 大瓶',
  station_skill_big_bottle_place: '放回 500 mL 大瓶',
  station_skill_big_bottle_decap: '打开 500 mL 大瓶盖',
  station_skill_big_bottle_recap: '关闭 500 mL 大瓶盖',
  station_skill_syringe_pick: '夹取过滤针筒',
  station_skill_filter_head_mount: '安装过滤头',
  station_skill_syringe_aspirate: '针筒吸液',
  station_skill_syringe_dispense: '针筒吐液',
  station_skill_syringe_discard: '弃置针筒和过滤头',
  station_skill_wait: '推进仿真时间',
  station_mixture_stock: '加入母液',
  station_mixture_diluent: '加入稀释液',
  station_mixture_close: '配液结束与关门',
};

export function matterixCapabilityLabel(capabilityId: string, parameters: Record<string, unknown> = {}): string {
  const label = MATTERIX_CAPABILITY_LABELS[capabilityId] ?? capabilityId;
  const nested = parameters.parameters;
  const volume =
    parameters.volume_ul ??
    (nested && typeof nested === 'object' && !Array.isArray(nested)
      ? (nested as Record<string, unknown>).volume_ul
      : undefined);
  return typeof volume === 'number' && Number.isFinite(volume) ? `${label}（${volume} µL）` : label;
}

export function matterixTaskLabel(type: string, parameters: Record<string, unknown> = {}): string {
  if (type === 'Matterix Capability' || type === 'Matterix Volume Operation') {
    return typeof parameters.capability_id === 'string'
      ? matterixCapabilityLabel(parameters.capability_id, parameters)
      : type;
  }
  if (type === 'Matterix Begin Experiment') return '实验初始化';
  if (type === 'Matterix End Experiment') {
    if (parameters.expected_phase === 0) return '结束自定义实验';
    return typeof parameters.expected_phase === 'number'
      ? `结束检查（${parameters.expected_phase} 阶段）`
      : '实验结束检查';
  }
  if (type === 'Prepare Mixture') return '制备配液';
  if (type === 'Measure Mixture') return '虚拟浓度测量';
  if (type === 'Check Mixture Quality') return '测量质量检查';
  if (type === 'Score Mixture') return '目标浓度评分';
  return type;
}

export function matterixAssetLabel(assetId: string): string {
  if (assetId === 'TransferBottle10ml_617') return '母液瓶（TransferBottle10ml_617）';
  if (assetId === 'DesignC_BottleBody50ml_231') return '中瓶（DesignC_BottleBody50ml_231）';
  if (assetId === 'EightPositionReactor_662') return '反应位（EightPositionReactor_662）';
  if (assetId === 'DesignD_DesignC_BottleBody500ml_238') {
    return '大瓶（DesignD_DesignC_BottleBody500ml_238）';
  }
  if (assetId === '_1mlTipBox_222_Tip_85') return 'TIP 1（_1mlTipBox_222_Tip_85）';
  if (assetId === '_1mlTipBox_222_Tip_73') return 'TIP 2（_1mlTipBox_222_Tip_73）';
  if (assetId.startsWith('SyringeComponent')) return `注射器（${assetId}）`;
  if (assetId.startsWith('DesignD_DesignC_ChromatographyVial')) return `样品瓶（${assetId}）`;
  return assetId;
}

export const MATTERIX_FIELD_LABELS: Record<string, string> = {
  status: '状态',
  source: '来源',
  scene: '场景',
  scene_id: '场景',
  capability: '能力',
  capability_id: '能力 ID',
  elapsed_sim_s: '仿真耗时',
  elapsed_wall_s: '实际耗时',
  sim_time_s: '仿真耗时',
  wall_time_s: '实际耗时',
  duration_s: '持续时间',
  steps: '步数',
  step_count: '步数',
  sample_count: '样本数',
  config_version: '配置版本',
  request_id: '请求编号',
  error: '错误',
  passed: '通过',
  workflow_name: '工作流',
  state_machine_success: '状态机完成',
  reset_acceptance: '初态验收',
  sample: '制备样品',
  observation: '测量观测',
  quality: '测量质量',
  score: '目标评分',
  stock: '母液阶段',
  diluent: '稀释液阶段',
  measurement: '测量阶段',
  sample_id: '样本编号',
  observation_id: '观测编号',
  backend: '执行后端',
  requested_stock_ul: '请求母液体积',
  stock_ul: '母液实际体积',
  diluent_ul: '稀释液实际体积',
  stock_concentration_mm: '母液浓度',
  composition_source: '组成来源',
  hardware_calibrated: '硬件校准',
  fixture_id: '夹具标识',
  container_id: '容器 ID',
  model_version: '模型版本',
  processing_version: '处理版本',
  replicates: '重复读数',
  blank_mm: '空白读数',
  reference_mm: '参考样读数',
  value_mm: '测量浓度',
  uncertainty_mm: '不确定度',
  seed: '随机种子',
  eligible: '质量合格',
  reasons: '不合格原因',
  failure_kind: '失效类型',
  policy_version: '质控策略版本',
  target_mm: '目标浓度',
  measured_mm: '测量浓度',
  loss: '目标损失',
  continuous_phase: '连续阶段',
  phase_continuous: '连续阶段',
  tip_unused: 'TIP 未使用',
  fresh_tip: 'TIP 状态',
  source_volume_available: '源液量充足',
  source_reserve: '母液余量',
  reactor_initial_volume: '反应器初始液量',
  empty_target: '目标瓶初始为空',
  diluent_open: '稀释液瓶已开盖',
  diluent_recapped: '稀释液瓶已关盖',
  big_bottle_open: '大瓶已开盖',
  big_bottle_final_closed: '大瓶最终关闭',
  door_start: '门初始位置',
  door_end: '门实际位置',
  door_target: '门目标位置',
  door_travel: '门实际行程',
  door_at_target: '门到位',
  door_moved: '门实际运动',
  door_closed: '门已关闭',
  big_cap_grasp: '大瓶盖抓取',
  big_cap_release: '大瓶盖释放',
  big_cap_detached: '大瓶盖旋开',
  big_cap_engaged: '大瓶盖旋合',
  big_cap_held_open: '大瓶保持开盖',
  filter_head_installed: '过滤头已安装',
  tools_released: '工具已释放',
  tool_grips_empty: '最终工具夹持为空',
  rejected_operations: '拒绝操作数',
  spill_volume: '溢液量',
  air_volume: '空气量',
  container_set_unchanged: '容器集合不变',
  baseline_z_m: '原支撑面高度（m）',
  calibrated_max_lift_m: '已验证抬升上限（m）',
  calibration_status: '标定状态',
  current_requested_height_m: '本步开始时目标高度（m）',
  expected_height_m: '本步目标高度（m）',
  requested_delta_m: '本步请求升降量（m）',
  actual_height_m: '实际离台高度（m）',
  minimum_height_m: '过程最低高度（m）',
  maximum_height_m: '过程最高高度（m）',
  measured_delta_m: '实际升降量（m）',
  height_error_m: '高度误差（m）',
  height_tolerance_m: '到位容差（m）',
  horizontal_drift_m: '水平偏移（m）',
  horizontal_drift_tolerance_m: '水平偏移容差（m）',
  grasp_offset_drift_m: '抓取相对位置偏移（m）',
  grasp_offset_tolerance_m: '抓取偏移容差（m）',
  gripper_drift_m: '夹爪间距变化（m）',
  gripper_drift_tolerance_m: '夹爪间距变化容差（m）',
  gripper_spacing_m: '夹爪间距（m）',
  maximum_open_gripper_spacing_m: '过程最大夹爪间距（m）',
  gripper_close_delta_m: '夹爪闭合量（m）',
  gripper_close_delta_tolerance_m: '夹爪闭合判定阈值（m）',
  tilt_rad: '烧杯倾斜角（rad）',
  tilt_tolerance_rad: '倾斜容差（rad）',
  linear_speed_mps: '烧杯移动速度（m/s）',
  angular_speed_radps: '烧杯转动速度（rad/s）',
  position_drift_m: '保持期间位置漂移（m）',
  position_drift_tolerance_m: '位置漂移容差（m）',
  orientation_drift_rad: '保持期间姿态漂移（rad）',
  orientation_drift_tolerance_rad: '姿态漂移容差（rad）',
  current_stable_s: '连续稳定时间（s）',
  support_stable_s: '支撑稳定时间（s）',
  required_hold_s: '要求稳定时间（s）',
  supported: '已回到支撑面',
  release_ready: '满足松开条件',
  released: '已松开',
  hard_failure: '过程约束违反原因',
};

export function matterixFieldLabel(field: string): string {
  const normalized = field.toLowerCase();
  return (
    MATTERIX_FIELD_LABELS[normalized] ?? MATTERIX_FIELD_LABELS[normalized.split('.').at(-1) ?? normalized] ?? field
  );
}
