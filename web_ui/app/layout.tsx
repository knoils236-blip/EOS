import type { Metadata } from 'next';
import { Geist, Geist_Mono } from 'next/font/google';
import './globals.css';
import { ClientLayout } from '@/components/layout/ClientLayout';
import { UserProvider, type CurrentUser } from '@/contexts/UserContext';
import { getSessionUser } from '@/lib/auth/session';
import { rolesFor, isSuperuser } from '@/lib/auth/authz';
import { env } from '@/lib/env';

async function loadCurrentUser(): Promise<CurrentUser | null> {
  const user = await getSessionUser();
  if (!user) return null;
  const roles = await rolesFor(user.sub);
  return { ...user, superuser: isSuperuser(roles), roles, authEnabled: env.AUTH_ENABLED };
}

const geistSans = Geist({
  variable: '--font-geist-sans',
  subsets: ['latin'],
});

const geistMono = Geist_Mono({
  variable: '--font-geist-mono',
  subsets: ['latin'],
});

export const metadata: Metadata = {
  title: {
    default: 'EOS - 实验编排系统',
    template: '%s - EOS',
  },
  description: '用于机器人与自动化的可视化流程编辑器和实验编排系统',
  keywords: ['protocol', 'orchestration', 'robotics', 'automation', 'workflow', 'editor'],
  authors: [{ name: 'EOS Team' }],
  creator: 'EOS Team',
  openGraph: {
    type: 'website',
    locale: 'zh_CN',
    siteName: 'EOS',
    title: 'EOS - 实验编排系统',
    description: '用于机器人与自动化的可视化流程编辑器和实验编排系统',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'EOS - 实验编排系统',
    description: '用于机器人与自动化的可视化流程编辑器和实验编排系统',
  },
  robots: {
    index: true,
    follow: true,
  },
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const user = await loadCurrentUser();

  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body className={`${geistSans.variable} ${geistMono.variable} antialiased`}>
        <UserProvider user={user}>
          <ClientLayout>{children}</ClientLayout>
        </UserProvider>
      </body>
    </html>
  );
}
