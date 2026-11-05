import { DataTableColumnDef } from '@/components/data-table/DataTable';

export function packageColumn<T extends { package: string }>(rows: T[]): DataTableColumnDef<T> {
  const options = Array.from(new Set(rows.map((r) => r.package).filter(Boolean))).sort();
  return {
    accessorKey: 'package',
    header: '软件包',
    cell: ({ row }) => <div className="text-gray-700 dark:text-gray-300">{row.getValue('package')}</div>,
    enableColumnFilter: true,
    filterType: 'multiselect',
    filterOptions: options,
    filterFn: 'arrIncludesSome',
  };
}
