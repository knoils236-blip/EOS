import { DeviceInspector } from '@/features/device-inspector/components/DeviceInspector';
import { getDevices } from '@/features/management/api/devices';

export const dynamic = 'force-dynamic';

export const metadata = {
  title: '设备检查器 - EOS',
  description: '实时检查与控制 EOS 设备',
};

export default async function DeviceInspectorPage() {
  // Fetch devices
  const devices = await getDevices();

  return (
    <div className="h-full flex flex-col">
      <div className="px-6 pt-6 pb-4">
        <div className="max-w-screen-xl mx-auto">
          <h1 className="text-3xl font-bold text-gray-900 dark:text-white">设备检查器</h1>
          <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">实时监控设备状态并调用函数</p>
        </div>
      </div>

      <div className="flex-1 min-h-0 px-6 pb-6">
        <div className="max-w-screen-xl mx-auto h-full">
          <DeviceInspector initialDevices={devices} />
        </div>
      </div>
    </div>
  );
}
