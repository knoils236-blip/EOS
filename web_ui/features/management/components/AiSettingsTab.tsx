'use client';

import * as React from 'react';
import { CheckCircle2, KeyRound, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { ErrorBox } from '@/components/ui/ErrorBox';
import { Input } from '@/components/ui/Input';
import { Label } from '@/components/ui/Label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { getAiSettings, saveAiSettings, type AIProtocol } from '../api/aiSettings';

export function AiSettingsTab() {
  const [protocol, setProtocol] = React.useState<AIProtocol>('openai');
  const [baseUrl, setBaseUrl] = React.useState('');
  const [model, setModel] = React.useState('');
  const [apiKey, setApiKey] = React.useState('');
  const [keyConfigured, setKeyConfigured] = React.useState(false);
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [saved, setSaved] = React.useState(false);

  React.useEffect(() => {
    let active = true;
    getAiSettings().then((result) => {
      if (!active) return;
      if (result.success && result.data) {
        setProtocol(result.data.protocol);
        setBaseUrl(result.data.base_url);
        setModel(result.data.model);
        setKeyConfigured(result.data.key_configured);
      } else {
        setError(result.error ?? '获取 AI 设置失败');
      }
      setLoading(false);
    });
    return () => {
      active = false;
    };
  }, []);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setSaved(false);
    if (!keyConfigured && !apiKey.trim()) {
      setError('首次保存 AI 设置时必须提供 API Key');
      return;
    }

    setSaving(true);
    const result = await saveAiSettings({
      protocol,
      base_url: baseUrl,
      model,
      api_key: apiKey,
    });
    setSaving(false);
    if (!result.success || !result.data) {
      setError(result.error ?? '保存 AI 设置失败');
      return;
    }
    setKeyConfigured(result.data.key_configured);
    setApiKey('');
    setSaved(true);
  };

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
        <Loader2 className="h-4 w-4 animate-spin" />
        正在读取 AI 设置…
      </div>
    );
  }

  return (
    <div className="max-w-2xl rounded-lg border border-gray-200 bg-white p-6 dark:border-slate-700 dark:bg-slate-900">
      <div className="mb-5">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-white">AI 服务</h2>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          此设置用于决策分析、协议起草和 Beacon 的默认模型。保存到服务器后优先于 EOS_AI_* 环境变量。
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-5">
        <div className="space-y-1.5">
          <Label htmlFor="ai-protocol">协议</Label>
          <Select value={protocol} onValueChange={(value) => setProtocol(value as AIProtocol)} disabled={saving}>
            <SelectTrigger id="ai-protocol">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="openai">OpenAI Chat Completions</SelectItem>
              <SelectItem value="anthropic">Anthropic Messages</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="ai-base-url">Base URL</Label>
          <Input
            id="ai-base-url"
            type="url"
            value={baseUrl}
            onChange={(event) => setBaseUrl(event.target.value)}
            placeholder={protocol === 'openai' ? 'https://your-service.example/v1' : 'https://api.anthropic.com'}
            maxLength={2048}
            required
            disabled={saving}
          />
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {protocol === 'anthropic'
              ? 'Anthropic 填根地址 https://api.anthropic.com，不要加 /v1；SDK 会追加 /v1/messages。'
              : '填写 OpenAI 兼容服务的 API 基地址，不要包含 /chat/completions。'}
          </p>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="ai-model">模型</Label>
          <Input
            id="ai-model"
            value={model}
            onChange={(event) => setModel(event.target.value)}
            placeholder={protocol === 'openai' ? 'gpt-model-name' : 'claude-model-name'}
            maxLength={256}
            required
            disabled={saving}
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="ai-api-key">API Key</Label>
          <Input
            id="ai-api-key"
            type="password"
            autoComplete="new-password"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder={keyConfigured ? '已配置；留空以保留现有密钥' : '请输入 API Key'}
            maxLength={8192}
            disabled={saving}
          />
          <p className="flex items-center gap-1.5 text-xs text-gray-500 dark:text-gray-400">
            <KeyRound className="h-3.5 w-3.5" />
            {keyConfigured ? '服务端已保存密钥，接口不会返回密钥内容。' : '首次保存时必须填写 API Key。'}
          </p>
        </div>

        {error && <ErrorBox error={error} />}
        {saved && (
          <p className="flex items-center gap-1.5 rounded-md border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-700 dark:border-green-800 dark:bg-green-900/30 dark:text-green-300">
            <CheckCircle2 className="h-4 w-4 flex-shrink-0" />
            AI 设置已保存。新的决策分析、协议起草和新建 Beacon 计划将使用此配置。
          </p>
        )}

        <Button type="submit" variant="primary" disabled={saving}>
          {saving && <Loader2 className="h-4 w-4 animate-spin" />}
          {saving ? '正在保存…' : '保存 AI 设置'}
        </Button>
      </form>
    </div>
  );
}
