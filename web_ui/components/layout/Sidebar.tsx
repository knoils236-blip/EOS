'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  Bot,
  ChevronLeft,
  ChevronRight,
  SquarePen,
  ListChecks,
  FlaskConical,
  Target,
  FolderOpen,
  Microscope,
  GanttChart,
  Rotate3d,
  Box,
  Settings,
  Sun,
  Moon,
  Wifi,
  WifiOff,
  Terminal,
  CircleUser,
  LogOut,
} from 'lucide-react';
import * as Separator from '@radix-ui/react-separator';
import * as Tooltip from '@radix-ui/react-tooltip';
import { useTheme } from 'next-themes';
import { useOrchestratorConnected } from '@/contexts/OrchestratorStatusContext';
import { useLogPanel } from '@/contexts/LogPanelContext';
import { useUser } from '@/contexts/UserContext';
import { signOutUser } from '@/features/auth/api/actions';

export function Sidebar() {
  const [isExpanded, setIsExpanded] = useState(false);
  const pathname = usePathname();
  const { theme, setTheme, resolvedTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  const { isConnected, isChecking, checkNow } = useOrchestratorConnected();
  const { showLogs, toggleLogs } = useLogPanel();
  const user = useUser();

  // Avoid hydration mismatch
  useEffect(() => {
    setMounted(true);
  }, []);

  const currentTheme = theme === 'system' ? resolvedTheme : theme;

  const toggleTheme = () => {
    setTheme(currentTheme === 'dark' ? 'light' : 'dark');
  };

  const menuItems = [
    {
      href: '/editor',
      icon: SquarePen,
      label: '编辑器',
    },
    {
      href: '/tasks',
      icon: ListChecks,
      label: '任务',
    },
    {
      href: '/protocol-runs',
      icon: FlaskConical,
      label: '协议运行',
    },
    {
      href: '/campaigns',
      icon: Target,
      label: '实验计划',
    },
    {
      href: '/resources',
      icon: Box,
      label: '资源',
    },
    {
      href: '/files',
      icon: FolderOpen,
      label: '文件',
    },
    {
      href: '/devices/inspect',
      icon: Microscope,
      label: '设备检查器',
    },
    {
      href: '/simulator',
      icon: GanttChart,
      label: '模拟器',
    },
    {
      href: '/digital-twin',
      icon: Rotate3d,
      label: '数字孪生',
    },
    {
      href: '/ai-lab',
      icon: Bot,
      label: 'AI 协作',
    },
    ...(user.isAdmin
      ? [
          {
            href: '/management',
            icon: Settings,
            label: '系统管理',
          },
        ]
      : []),
    ...(user.authEnabled
      ? [
          {
            href: '/profile',
            icon: CircleUser,
            label: '个人资料',
          },
        ]
      : []),
  ];

  return (
    <div
      className={`h-screen bg-white dark:bg-slate-900 border-r border-gray-200 dark:border-slate-700 shadow-sm transition-all duration-300 ease-in-out flex flex-col overflow-hidden ${
        isExpanded ? 'w-52' : 'w-16'
      }`}
      style={{ isolation: 'isolate' }}
    >
      {/* Header */}
      <div className="p-4 flex items-center justify-center">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">EOS</h1>
      </div>

      {/* Toggle Button */}
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="mx-2 mb-4 p-2 hover:bg-gray-100 dark:hover:bg-slate-800 rounded-md transition-colors duration-200 flex items-center justify-center"
        aria-label={isExpanded ? '收起侧边栏' : '展开侧边栏'}
      >
        {isExpanded ? (
          <ChevronLeft className="w-5 h-5 text-gray-600 dark:text-gray-400" />
        ) : (
          <ChevronRight className="w-5 h-5 text-gray-600 dark:text-gray-400" />
        )}
      </button>

      <Separator.Root className="bg-gray-200 dark:bg-slate-700 h-px mx-2 mb-4" />

      {/* Menu Items and Theme Toggle */}
      <Tooltip.Provider delayDuration={300}>
        <nav className="flex-1 px-2">
          {menuItems.map((item) => {
            const Icon = item.icon;
            const isActive = pathname === item.href;

            const linkContent = (
              <Link
                key={item.href}
                href={item.href}
                className={`w-full p-3 mb-1 rounded-md transition-all duration-200 flex items-center gap-3 ${
                  isActive
                    ? 'bg-blue-50 text-blue-600 dark:bg-slate-800 dark:text-yellow-400'
                    : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-slate-800'
                }`}
              >
                <Icon className="w-5 h-5 flex-shrink-0" />
                <span
                  className={`transition-opacity duration-300 whitespace-nowrap ${
                    isExpanded ? 'opacity-100' : 'opacity-0 w-0'
                  }`}
                >
                  {item.label}
                </span>
              </Link>
            );

            if (!isExpanded) {
              return (
                <Tooltip.Root key={item.href} disableHoverableContent>
                  <Tooltip.Trigger asChild>{linkContent}</Tooltip.Trigger>
                  <Tooltip.Portal>
                    <Tooltip.Content
                      side="right"
                      className="bg-gray-900 dark:bg-slate-700 text-white px-3 py-2 rounded-md text-sm shadow-lg z-50 pointer-events-none"
                      sideOffset={20}
                      collisionPadding={10}
                    >
                      {item.label}
                      <Tooltip.Arrow className="fill-gray-900 dark:fill-slate-700" />
                    </Tooltip.Content>
                  </Tooltip.Portal>
                </Tooltip.Root>
              );
            }

            return linkContent;
          })}
        </nav>

        {/* Logs Toggle */}
        <div className="px-2 mt-auto">
          {!isExpanded ? (
            <Tooltip.Root disableHoverableContent>
              <Tooltip.Trigger asChild>
                <button
                  onClick={toggleLogs}
                  className={`w-full p-3 mb-1 rounded-md transition-all duration-200 flex items-center justify-center ${
                    showLogs
                      ? 'bg-blue-50 text-blue-600 dark:bg-slate-800 dark:text-yellow-400'
                      : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-slate-800'
                  }`}
                  aria-label="切换日志面板"
                >
                  <Terminal className="w-5 h-5 flex-shrink-0" />
                </button>
              </Tooltip.Trigger>
              <Tooltip.Portal>
                <Tooltip.Content
                  side="right"
                  className="bg-gray-900 dark:bg-slate-700 text-white px-3 py-2 rounded-md text-sm shadow-lg z-50 pointer-events-none"
                  sideOffset={20}
                  collisionPadding={10}
                >
                  日志
                  <Tooltip.Arrow className="fill-gray-900 dark:fill-slate-700" />
                </Tooltip.Content>
              </Tooltip.Portal>
            </Tooltip.Root>
          ) : (
            <button
              onClick={toggleLogs}
              className={`w-full p-3 mb-1 rounded-md transition-all duration-200 flex items-center gap-3 ${
                showLogs
                  ? 'bg-blue-50 text-blue-600 dark:bg-slate-800 dark:text-yellow-400'
                  : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-slate-800'
              }`}
              aria-label="切换日志面板"
            >
              <Terminal className="w-5 h-5 flex-shrink-0" />
              <span
                className={`transition-opacity duration-300 whitespace-nowrap ${
                  isExpanded ? 'opacity-100' : 'opacity-0 w-0'
                }`}
              >
                日志
              </span>
            </button>
          )}
        </div>

        <Separator.Root className="bg-gray-200 dark:bg-slate-700 h-px mx-2 mt-4 mb-4" />

        {mounted && (
          <div className="px-2 pb-4 space-y-1">
            {/* Orchestrator Status Indicator */}
            {!isExpanded ? (
              <Tooltip.Root disableHoverableContent>
                <Tooltip.Trigger asChild>
                  <button
                    onClick={checkNow}
                    disabled={isChecking}
                    className="w-full p-3 rounded-md flex items-center justify-center cursor-pointer hover:bg-gray-100 dark:hover:bg-slate-800 transition-colors"
                    aria-label={isConnected ? '编排器在线，点击重新检查' : '编排器离线，点击重新检查'}
                  >
                    {isConnected ? (
                      <Wifi className={`w-5 h-5 text-green-500 ${isChecking ? 'animate-pulse' : ''}`} />
                    ) : (
                      <WifiOff className={`w-5 h-5 text-red-500 ${isChecking ? 'animate-pulse' : ''}`} />
                    )}
                  </button>
                </Tooltip.Trigger>
                <Tooltip.Portal>
                  <Tooltip.Content
                    side="right"
                    className="bg-gray-900 dark:bg-slate-700 text-white px-3 py-2 rounded-md text-sm shadow-lg z-50 pointer-events-none"
                    sideOffset={20}
                    collisionPadding={10}
                  >
                    {isConnected ? '编排器在线，点击重新检查' : '编排器离线，点击重新检查'}
                    <Tooltip.Arrow className="fill-gray-900 dark:fill-slate-700" />
                  </Tooltip.Content>
                </Tooltip.Portal>
              </Tooltip.Root>
            ) : (
              <button
                onClick={checkNow}
                disabled={isChecking}
                className="w-full p-3 rounded-md flex items-center gap-3 cursor-pointer hover:bg-gray-100 dark:hover:bg-slate-800 transition-colors"
                aria-label={isConnected ? '编排器在线，点击重新检查' : '编排器离线，点击重新检查'}
              >
                {isConnected ? (
                  <Wifi className={`w-5 h-5 flex-shrink-0 text-green-500 ${isChecking ? 'animate-pulse' : ''}`} />
                ) : (
                  <WifiOff className={`w-5 h-5 flex-shrink-0 text-red-500 ${isChecking ? 'animate-pulse' : ''}`} />
                )}
                <span
                  className={`transition-opacity duration-300 whitespace-nowrap ${
                    isConnected ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'
                  }`}
                >
                  {isConnected ? '在线' : '离线'}
                </span>
              </button>
            )}

            {/* Theme Toggle */}
            {!isExpanded ? (
              <Tooltip.Root disableHoverableContent>
                <Tooltip.Trigger asChild>
                  <button
                    onClick={toggleTheme}
                    className="w-full p-3 rounded-md transition-all duration-200 flex items-center justify-center hover:bg-gray-100 dark:hover:bg-slate-800"
                    aria-label={currentTheme === 'light' ? '切换到深色模式' : '切换到浅色模式'}
                  >
                    {currentTheme === 'light' ? (
                      <Moon className="w-5 h-5 text-gray-600 dark:text-gray-400" />
                    ) : (
                      <Sun className="w-5 h-5 text-gray-600 dark:text-gray-400" />
                    )}
                  </button>
                </Tooltip.Trigger>
                <Tooltip.Portal>
                  <Tooltip.Content
                    side="right"
                    className="bg-gray-900 dark:bg-slate-700 text-white px-3 py-2 rounded-md text-sm shadow-lg z-50 pointer-events-none"
                    sideOffset={20}
                    collisionPadding={10}
                  >
                    {currentTheme === 'light' ? '深色模式' : '浅色模式'}
                    <Tooltip.Arrow className="fill-gray-900 dark:fill-slate-700" />
                  </Tooltip.Content>
                </Tooltip.Portal>
              </Tooltip.Root>
            ) : (
              <button
                onClick={toggleTheme}
                className="w-full p-3 rounded-md transition-all duration-200 flex items-center gap-3 hover:bg-gray-100 dark:hover:bg-slate-800 text-gray-700 dark:text-gray-300"
                aria-label={currentTheme === 'light' ? '切换到深色模式' : '切换到浅色模式'}
              >
                {currentTheme === 'light' ? (
                  <Moon className="w-5 h-5 flex-shrink-0" />
                ) : (
                  <Sun className="w-5 h-5 flex-shrink-0" />
                )}
                <span className="transition-opacity duration-300 whitespace-nowrap">
                  {currentTheme === 'light' ? '深色模式' : '浅色模式'}
                </span>
              </button>
            )}

            {/* Sign Out */}
            {user.authEnabled &&
              (!isExpanded ? (
                <Tooltip.Root disableHoverableContent>
                  <Tooltip.Trigger asChild>
                    <button
                      onClick={() => signOutUser()}
                      className="w-full p-3 rounded-md transition-all duration-200 flex items-center justify-center hover:bg-gray-100 dark:hover:bg-slate-800"
                      aria-label="退出登录"
                    >
                      <LogOut className="w-5 h-5 text-gray-600 dark:text-gray-400" />
                    </button>
                  </Tooltip.Trigger>
                  <Tooltip.Portal>
                    <Tooltip.Content
                      side="right"
                      className="bg-gray-900 dark:bg-slate-700 text-white px-3 py-2 rounded-md text-sm shadow-lg z-50 pointer-events-none"
                      sideOffset={20}
                      collisionPadding={10}
                    >
                      退出登录{user.name ? ` (${user.name})` : ''}
                      <Tooltip.Arrow className="fill-gray-900 dark:fill-slate-700" />
                    </Tooltip.Content>
                  </Tooltip.Portal>
                </Tooltip.Root>
              ) : (
                <button
                  onClick={() => signOutUser()}
                  className="w-full p-3 rounded-md transition-all duration-200 flex items-center gap-3 hover:bg-gray-100 dark:hover:bg-slate-800 text-gray-700 dark:text-gray-300"
                  aria-label="退出登录"
                >
                  <LogOut className="w-5 h-5 flex-shrink-0" />
                  <span className="transition-opacity duration-300 whitespace-nowrap">退出登录</span>
                </button>
              ))}
          </div>
        )}
      </Tooltip.Provider>
    </div>
  );
}
