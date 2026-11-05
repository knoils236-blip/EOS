import { browseFiles } from '@/features/files/api/files';
import { FileBrowser } from '@/features/files/components/FileBrowser';

export const dynamic = 'force-dynamic';

export default async function FilesPage() {
  const initialData = await browseFiles('');

  return (
    <div className="container mx-auto p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">文件</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">浏览和管理对象存储中的文件</p>
      </div>
      <FileBrowser initialData={initialData} />
    </div>
  );
}
