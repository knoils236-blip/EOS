'use client';

import * as React from 'react';
import { Loader2, CheckCircle, XCircle } from 'lucide-react';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/Sheet';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Label } from '@/components/ui/Label';
import { Textarea } from '@/components/ui/Textarea';
import { JsonDisplay } from '@/components/ui/JsonDisplay';
import { Separator } from '@/components/ui/Separator';
import { Badge } from '@/components/ui/Badge';
import { ErrorBox } from '@/components/ui/ErrorBox';
import { toast } from '@/lib/utils/toast';
import { callDeviceFunction } from '../api/inspector';
import { useDeviceInspectorStore } from '@/lib/stores/deviceInspectorStore';
import type {
  SelectedDevice,
  DeviceFunction,
  ParsedParameter,
  ParameterUIType,
  FunctionCall,
} from '@/lib/types/device-inspector';
import { useOrchestratorConnected } from '@/contexts/OrchestratorStatusContext';

interface FunctionCallDialogProps {
  device: SelectedDevice;
  functionName: string;
  functionDef: DeviceFunction;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function FunctionCallDialog({ device, functionName, functionDef, open, onOpenChange }: FunctionCallDialogProps) {
  const { isConnected } = useOrchestratorConnected();
  const [parameterValues, setParameterValues] = React.useState<Record<string, unknown>>({});
  const [isCalling, setIsCalling] = React.useState(false);
  const [result, setResult] = React.useState<unknown>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [callStartTime, setCallStartTime] = React.useState<number | null>(null);

  const { addFunctionCall } = useDeviceInspectorStore();

  // Parse parameters to determine UI types
  const parsedParameters: ParsedParameter[] = React.useMemo(() => {
    return functionDef.parameters.map((param) => {
      const typeStr = param.type.toLowerCase();
      let uiType: ParameterUIType = 'string';

      if (typeStr.includes('int') || typeStr.includes('float') || typeStr === 'number') {
        uiType = 'number';
      } else if (typeStr.includes('bool')) {
        uiType = 'boolean';
      } else if (
        typeStr.includes('dict') ||
        typeStr.includes('list') ||
        typeStr.includes('{') ||
        typeStr.includes('[')
      ) {
        uiType = 'json';
      }

      return {
        ...param,
        uiType,
      };
    });
  }, [functionDef.parameters]);

  // Initialize default values
  React.useEffect(() => {
    const defaults: Record<string, unknown> = {};
    parsedParameters.forEach((param) => {
      if (param.default !== null && param.default !== 'None') {
        defaults[param.name] = param.default;
      } else if (param.uiType === 'boolean') {
        defaults[param.name] = false;
      } else if (param.uiType === 'number') {
        defaults[param.name] = 0;
      } else if (param.uiType === 'json') {
        defaults[param.name] = '{}';
      } else {
        defaults[param.name] = '';
      }
    });
    setParameterValues(defaults);
  }, [parsedParameters]);

  const handleParameterChange = (name: string, value: unknown) => {
    setParameterValues((prev) => ({
      ...prev,
      [name]: value,
    }));
  };

  const handleCall = async () => {
    setIsCalling(true);
    setResult(null);
    setError(null);
    setCallStartTime(Date.now());

    try {
      // Build parameters object
      const params: Record<string, unknown> = {};

      for (const param of parsedParameters) {
        const value = parameterValues[param.name];

        // Skip optional parameters with no value
        if (!param.required && (value === '' || value === null || value === undefined)) {
          continue;
        }

        // Parse JSON parameters
        if (param.uiType === 'json') {
          try {
            params[param.name] = JSON.parse(value as string);
          } catch {
            throw new Error(`参数的 JSON 无效：“${param.name}"`);
          }
        } else if (param.uiType === 'boolean') {
          params[param.name] = value === true || value === 'true';
        } else if (param.uiType === 'number') {
          const num = Number(value);
          if (isNaN(num)) {
            throw new Error(`参数的数值无效：“${param.name}"`);
          }
          params[param.name] = num;
        } else {
          params[param.name] = value;
        }
      }

      // Call the function
      const response = await callDeviceFunction(device.labName, device.deviceName, functionName, params);

      const duration = callStartTime ? Date.now() - callStartTime : undefined;

      if (response.success) {
        setResult(response.result);
        toast.success(`函数 ${functionName} 调用成功`);

        // Add to history
        const call: FunctionCall = {
          id: `${Date.now()}-${Math.random()}`,
          timestamp: Date.now(),
          functionName,
          parameters: params,
          result: response,
          duration,
        };
        addFunctionCall(call);
      } else {
        setError(response.error || '未知错误');
        toast.error(response.error || '函数调用失败');
      }
    } catch (e) {
      const errorMsg = e instanceof Error ? e.message : '调用函数失败';
      setError(errorMsg);
      toast.error(errorMsg);
    } finally {
      setIsCalling(false);
    }
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="sm:max-w-2xl overflow-y-auto">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            调用函数： <code className="text-blue-600 dark:text-yellow-400">{functionName}</code>
          </SheetTitle>
          <SheetDescription>{functionDef.docstring || '暂无说明'}</SheetDescription>
        </SheetHeader>

        <div className="mt-6 space-y-6">
          {/* Parameters */}
          {parsedParameters.length > 0 && (
            <div className="space-y-4">
              <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">参数</h3>

              {parsedParameters.map((param) => (
                <div key={param.name} className="space-y-2">
                  <Label htmlFor={param.name} className="flex items-center gap-2">
                    {param.name}
                    {!param.required && (
                      <Badge variant="default" className="text-xs">
                        可选
                      </Badge>
                    )}
                    <span className="text-xs text-gray-500 dark:text-gray-400 font-normal">({param.type})</span>
                  </Label>

                  {param.uiType === 'boolean' ? (
                    <div className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        id={param.name}
                        checked={parameterValues[param.name] === true}
                        onChange={(e) => handleParameterChange(param.name, e.target.checked)}
                        className="rounded border-gray-300 text-blue-600 focus:ring-blue-500 dark:text-yellow-400 dark:focus:ring-yellow-400"
                      />
                      <span className="text-sm text-gray-600 dark:text-gray-400">
                        {parameterValues[param.name] ? 'true' : 'false'}
                      </span>
                    </div>
                  ) : param.uiType === 'json' ? (
                    <Textarea
                      id={param.name}
                      value={(parameterValues[param.name] as string) || ''}
                      onChange={(e) => handleParameterChange(param.name, e.target.value)}
                      placeholder="输入 JSON…"
                      className="font-mono text-sm"
                      rows={4}
                    />
                  ) : (
                    <Input
                      id={param.name}
                      type={param.uiType === 'number' ? 'number' : 'text'}
                      value={(parameterValues[param.name] as string | number) || ''}
                      onChange={(e) =>
                        handleParameterChange(param.name, param.uiType === 'number' ? e.target.value : e.target.value)
                      }
                      placeholder={param.default ? `默认值：${param.default}` : ''}
                    />
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Call button */}
          <Button onClick={handleCall} disabled={isCalling || !isConnected} className="w-full" variant="primary">
            {isCalling ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                正在调用…
              </>
            ) : (
              '调用函数'
            )}
          </Button>

          {/* Result */}
          {(result !== null || error) && (
            <>
              <Separator />

              <div className="space-y-2">
                <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-2">
                  {error ? (
                    <>
                      <XCircle className="h-5 w-5 text-red-500" />
                      错误
                    </>
                  ) : (
                    <>
                      <CheckCircle className="h-5 w-5 text-green-500" />
                      结果
                    </>
                  )}
                </h3>

                {error ? (
                  <ErrorBox error={error} />
                ) : (
                  <div className="bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-lg p-4">
                    {typeof result === 'object' && result !== null ? (
                      <JsonDisplay data={result} />
                    ) : (
                      <pre className="text-sm text-green-800 dark:text-green-200">{String(result)}</pre>
                    )}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
