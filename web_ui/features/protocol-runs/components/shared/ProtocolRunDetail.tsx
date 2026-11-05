import { statusLabel } from '@/lib/labels';
import { Badge, getStatusBadgeVariant } from '@/components/ui/Badge';
import type { ProtocolRun } from '@/lib/types/api';
import { DetailField } from './DetailField';
import { TimelineSection } from './TimelineEntry';
import { ConditionalJsonSection } from './ConditionalJsonSection';
import { ErrorBox } from '@/components/ui/ErrorBox';
import { SECTION_DIVIDER } from '../../styles';

interface ProtocolRunDetailProps {
  protocolRun: ProtocolRun;
}

export function ProtocolRunDetail({ protocolRun }: ProtocolRunDetailProps) {
  const timelineEntries = [
    { label: '创建时间', timestamp: protocolRun.created_at ?? null },
    { label: '开始时间', timestamp: protocolRun.start_time ?? null },
    { label: '结束时间', timestamp: protocolRun.end_time ?? null },
  ];

  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <DetailField label="类型" value={protocolRun.type} />
        <DetailField
          label="状态"
          value={<Badge variant={getStatusBadgeVariant(protocolRun.status)}>{statusLabel(protocolRun.status)}</Badge>}
        />
        {protocolRun.status === 'FAILED' && protocolRun.error_message && <ErrorBox error={protocolRun.error_message} />}
        <DetailField label="负责人" value={protocolRun.owner} />
        <DetailField label="优先级" value={protocolRun.priority} />
        {protocolRun.campaign && <DetailField label="实验计划" value={protocolRun.campaign} />}
      </div>

      <div className={SECTION_DIVIDER}>
        <div className="text-sm font-medium text-gray-900 dark:text-white mb-2">时间线</div>
        <TimelineSection entries={timelineEntries} />
      </div>

      <ConditionalJsonSection data={protocolRun.parameters} label="参数" />
      <ConditionalJsonSection
        data={(protocolRun as { input_resources?: Record<string, unknown> }).input_resources}
        label="输入资源"
      />
      <ConditionalJsonSection
        data={(protocolRun as { output_parameters?: Record<string, unknown> }).output_parameters}
        label="输出参数"
      />
      <ConditionalJsonSection
        data={(protocolRun as { output_resources?: Record<string, unknown> }).output_resources}
        label="输出资源"
      />
      <ConditionalJsonSection data={protocolRun.meta} label="元数据" />
    </div>
  );
}
