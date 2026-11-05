import { redirect } from 'next/navigation';
import { env } from '@/lib/env';

const ERROR_MESSAGES: Record<string, string> = {
  OAuthCallbackError: '登录已取消或失败，请重试。',
  AccessDenied: '访问被拒绝，请联系管理员。',
  Configuration: '身份验证配置有误，请联系管理员。',
};

export const metadata = { title: '登录' };

function startUrl(callbackUrl?: string): string {
  return callbackUrl ? `/signin/start?callbackUrl=${encodeURIComponent(callbackUrl)}` : '/signin/start';
}

export default async function SignInPage({
  searchParams,
}: {
  searchParams: Promise<{ callbackUrl?: string; error?: string }>;
}) {
  if (!env.AUTH_ENABLED) redirect('/');
  const { callbackUrl, error } = await searchParams;
  // Normal entry goes straight to the provider; only a sign-in error renders a page.
  if (!error) redirect(startUrl(callbackUrl));

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-slate-950">
      <div className="w-full max-w-sm p-8 bg-white dark:bg-slate-900 rounded-lg border border-gray-200 dark:border-slate-700 shadow-sm text-center">
        <p className="mb-4 text-sm text-red-600 dark:text-red-400">{ERROR_MESSAGES[error] ?? '登录失败，请重试。'}</p>
        <a
          href={startUrl(callbackUrl)}
          className="inline-flex w-full items-center justify-center px-4 py-2.5 rounded-md bg-blue-600 hover:bg-blue-700 text-white font-medium transition-colors"
        >
          重试
        </a>
      </div>
    </div>
  );
}
