'use client';

import { roleLabel, statusLabel } from '@/lib/labels';

import * as React from 'react';
import { ShieldCheck, Trash2, UserCog, UserPlus, UserX, UserCheck } from 'lucide-react';
import { DataTable, DataTableColumnDef } from '@/components/data-table/DataTable';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { ConfirmationDialog } from './dialogs/ConfirmationDialog';
import { CreateUserDialog } from './dialogs/CreateUserDialog';
import { UserRolesDialog } from './dialogs/UserRolesDialog';
import { deleteUser, setUserActive, type ManagedUser } from '../api/users';

interface UsersTabProps {
  initialUsers: ManagedUser[];
  labNames: string[];
}

export function UsersTab({ initialUsers, labNames }: UsersTabProps) {
  const [createOpen, setCreateOpen] = React.useState(false);
  const [rolesUser, setRolesUser] = React.useState<ManagedUser | null>(null);
  const [deleteTarget, setDeleteTarget] = React.useState<ManagedUser | null>(null);

  // Keep the roles dialog in sync after revalidation refreshes the user list
  React.useEffect(() => {
    if (rolesUser) setRolesUser(initialUsers.find((u) => u.userId === rolesUser.userId) ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialUsers]);

  const columns: DataTableColumnDef<ManagedUser>[] = [
    {
      accessorKey: 'username',
      header: '用户名',
      cell: ({ row }) => <div className="font-medium">{row.getValue('username')}</div>,
    },
    {
      accessorKey: 'displayName',
      header: '名称',
    },
    {
      accessorKey: 'email',
      header: '邮箱',
    },
    {
      accessorKey: 'state',
      header: '状态',
      cell: ({ row }) => {
        const state = row.getValue('state') as string;
        return <Badge variant={state === 'ACTIVE' ? 'success' : 'default'}>{statusLabel(state)}</Badge>;
      },
    },
    {
      id: 'roles',
      header: '角色',
      cell: ({ row }) => {
        const user = row.original;
        return (
          <div className="flex flex-wrap gap-1">
            {user.superuser && (
              <Badge variant="warning">
                <ShieldCheck className="w-3 h-3 mr-1" />
                超级用户
              </Badge>
            )}
            {user.roles.map((role) => (
              <Badge key={role.id} variant="info">
                {roleLabel(role.role)}
                {role.labName ? `: ${role.labName}` : ''}
              </Badge>
            ))}
            {!user.superuser && user.roles.length === 0 && (
              <span className="text-xs text-gray-400 dark:text-gray-500">无访问权限</span>
            )}
          </div>
        );
      },
    },
    {
      id: 'actions',
      header: '操作',
      cell: ({ row }) => {
        const user = row.original;
        const active = user.state === 'ACTIVE';
        return (
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => setRolesUser(user)}>
              <UserCog className="h-4 w-4 mr-1" />
              角色
            </Button>
            <Button variant="outline" size="sm" onClick={() => setUserActive(user.userId, !active)}>
              {active ? <UserX className="h-4 w-4 mr-1" /> : <UserCheck className="h-4 w-4 mr-1" />}
              {active ? '停用' : '重新启用'}
            </Button>
            <Button variant="outline" size="sm" onClick={() => setDeleteTarget(user)}>
              <Trash2 className="h-4 w-4 mr-1" />
              删除
            </Button>
          </div>
        );
      },
    },
  ];

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button variant="primary" size="sm" onClick={() => setCreateOpen(true)}>
          <UserPlus className="h-4 w-4 mr-1" />
          创建用户
        </Button>
      </div>

      <DataTable columns={columns} data={initialUsers} searchPlaceholder="搜索用户…" />

      <CreateUserDialog open={createOpen} onOpenChange={setCreateOpen} labNames={labNames} />

      <UserRolesDialog user={rolesUser} onOpenChange={() => setRolesUser(null)} labNames={labNames} />

      <ConfirmationDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title="删除用户"
        description="确定要删除吗？这将永久删除 Zitadel 中的账户。"
        confirmLabel="删除"
        variant="destructive"
        items={deleteTarget ? [deleteTarget.username] : []}
        onConfirm={async () => {
          if (!deleteTarget) return;
          const result = await deleteUser(deleteTarget.userId);
          if (!result.success) throw new Error(result.error);
        }}
      />
    </div>
  );
}
