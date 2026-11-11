'use client';

import * as React from 'react';
import dynamic from 'next/dynamic';
import { statusLabel } from '@/lib/labels';
import { getMatterixProtocolExecutionDetail } from '../api/protocolRunDetails';
import { getProtocolRuns } from '../api/protocolRuns';
import type { MatterixProtocolExecutionDetail } from '../types/matterixExecution';
import { shouldPollMatterixExecution } from '../utils/matterixExecution';

const StationTwinView = dynamic(() => import('./StationTwinView'), { ssr: false });

/** The twin page: the station model alone, holding the state of the newest protocol run (live while it runs). */
export function StationTwinClient() {
  const [detail, setDetail] = React.useState<MatterixProtocolExecutionDetail | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      let next: MatterixProtocolExecutionDetail | null = null;
      try {
        const newest = (await getProtocolRuns({ limit: 1 })).data[0];
        if (newest) next = await getMatterixProtocolExecutionDetail(newest.name);
      } catch {
        // the newest run has no Matterix record (another device) or the orchestrator is down: show the rest pose
      }
      if (cancelled) return;
      setDetail(next);
      timer = setTimeout(load, next && shouldPollMatterixExecution(next) ? 1000 : 5000);
    };
    void load();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  return (
    <div className="relative h-full">
      <StationTwinView detail={detail} replay={false} />
      <div className="pointer-events-none absolute left-6 top-4 text-slate-800">
        <h1 className="text-2xl font-bold">数字孪生</h1>
        <p className="mt-1 text-xs text-slate-600">
          {detail
            ? `当前状态来自协议运行 ${detail.protocol_run_name}（${statusLabel(detail.status)}）`
            : '暂无移液站运行数据，显示初始姿态'}
          。拖动旋转，滚轮缩放，右键平移。
        </p>
      </div>
    </div>
  );
}
