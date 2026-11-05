'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { KeyRound, Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { ConfirmationDialog } from '@/features/management/components/dialogs/ConfirmationDialog';
import { CreateApiTokenDialog } from './dialogs/CreateApiTokenDialog';
import { revokeApiToken, type ApiTokenRow } from '../api/tokens';

interface ApiTokensSectionProps {
  tokens: ApiTokenRow[];
}

export function ApiTokensSection({ tokens }: ApiTokensSectionProps) {
  const router = useRouter();
  const [createOpen, setCreateOpen] = React.useState(false);
  const [revokeTarget, setRevokeTarget] = React.useState<ApiTokenRow | null>(null);

  return (
    <section className="rounded-lg border border-gray-200 bg-white dark:border-slate-700 dark:bg-slate-900">
      <div className="flex items-center justify-between gap-2 border-b border-gray-200 px-6 py-4 dark:border-slate-700">
        <div className="flex items-center gap-2">
          <KeyRound className="h-4 w-4 text-gray-400 dark:text-gray-500" />
          <h2 className="text-sm font-semibold text-gray-900 dark:text-white">API 令牌</h2>
        </div>
        <Button size="sm" onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" />
          新建令牌
        </Button>
      </div>

      <div className="px-6 py-5">
        {tokens.length === 0 ? (
          <p className="text-sm text-gray-400 dark:text-gray-500">暂无 API 令牌。</p>
        ) : (
          <ul className="divide-y divide-gray-100 dark:divide-slate-800">
            {tokens.map((token) => (
              <li key={token.id} className="flex items-center justify-between gap-4 py-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-gray-900 dark:text-gray-100">
                    {token.label || '未命名令牌'}
                  </p>
                  <p className="text-xs text-gray-400 dark:text-gray-500">
                    创建于 {new Date(token.created_at).toLocaleString('zh-CN')}
                  </p>
                </div>
                <Button variant="outline" size="sm" onClick={() => setRevokeTarget(token)}>
                  <Trash2 className="h-4 w-4" />
                  撤销
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <CreateApiTokenDialog
        open={createOpen}
        onOpenChange={(open) => {
          setCreateOpen(open);
          if (!open) router.refresh();
        }}
      />
      <ConfirmationDialog
        open={revokeTarget !== null}
        onOpenChange={(open) => !open && setRevokeTarget(null)}
        title="撤销 API 令牌"
        description="此操作将永久禁用该令牌，使用它的脚本将无法继续访问。"
        confirmLabel="撤销"
        variant="destructive"
        items={revokeTarget ? [revokeTarget.label || '未命名令牌'] : []}
        onConfirm={async () => {
          if (revokeTarget) {
            const result = await revokeApiToken(revokeTarget.id);
            if (!result.success) throw new Error(result.error);
          }
          router.refresh();
        }}
      />
    </section>
  );
}
