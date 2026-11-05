'use client';

import * as React from 'react';
import { Copy } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { ErrorBox } from '@/components/ui/ErrorBox';
import { Input } from '@/components/ui/Input';
import { Label } from '@/components/ui/Label';
import { Modal } from '@/components/ui/Modal';
import { createApiToken } from '../../api/tokens';

interface CreateApiTokenDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CreateApiTokenDialog({ open, onOpenChange }: CreateApiTokenDialogProps) {
  const [label, setLabel] = React.useState('');
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [token, setToken] = React.useState<string | null>(null);

  const reset = () => {
    setLabel('');
    setError(null);
    setToken(null);
  };

  const handleClose = () => {
    if (isSubmitting) return;
    reset();
    onOpenChange(false);
  };

  const handleSubmit = async () => {
    setIsSubmitting(true);
    setError(null);
    const result = await createApiToken(label);
    setIsSubmitting(false);
    if (result.success) {
      setToken(result.token ?? null);
    } else {
      setError(result.error ?? '创建令牌失败');
    }
  };

  return (
    <Modal
      open={open}
      onOpenChange={(next) => !next && handleClose()}
      title={token ? 'API 令牌已创建' : '新建 API 令牌'}
      closeDisabled={isSubmitting}
    >
      {token ? (
        <div className="space-y-4">
          <p className="text-sm text-gray-600 dark:text-gray-300">
            请立即复制此令牌，关闭后将不再显示。发送请求时使用{' '}
            <code className="font-mono text-xs">Authorization: Bearer &lt;token&gt;</code>.
          </p>
          <div className="flex items-center gap-2 p-3 rounded-md bg-gray-100 dark:bg-slate-800 font-mono text-sm">
            <span className="flex-1 break-all">{token}</span>
            <button
              onClick={() => navigator.clipboard.writeText(token)}
              className="text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
              aria-label="复制令牌"
            >
              <Copy className="w-4 h-4" />
            </button>
          </div>
          <div className="flex justify-end">
            <Button onClick={handleClose}>完成</Button>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <p className="text-sm text-gray-600 dark:text-gray-300">令牌使用您当前的访问权限，可随时在个人资料中撤销。</p>
          <div className="space-y-1.5">
            <Label htmlFor="token-label">标签</Label>
            <Input
              id="token-label"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="例如：笔记本脚本"
              disabled={isSubmitting}
            />
          </div>
          {error && <ErrorBox error={error} />}
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={handleClose} disabled={isSubmitting}>
              取消
            </Button>
            <Button onClick={handleSubmit} disabled={isSubmitting}>
              {isSubmitting ? '正在创建…' : '创建令牌'}
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
