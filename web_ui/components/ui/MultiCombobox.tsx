'use client';

import * as React from 'react';
import * as Popover from '@radix-ui/react-popover';
import { ChevronDown, Search, X } from 'lucide-react';
import { cn } from '@/lib/utils/cn';

export interface MultiComboboxOption {
  value: string;
  label: string;
}

export interface MultiComboboxProps {
  options: MultiComboboxOption[];
  value: string[];
  onChange: (value: string[]) => void;
  placeholder?: string;
  emptyText?: string;
  searchPlaceholder?: string;
  className?: string;
  disabled?: boolean;
}

export function MultiCombobox({
  options,
  value,
  onChange,
  placeholder = '选择…',
  emptyText = '未找到结果',
  searchPlaceholder = '搜索…',
  className,
  disabled = false,
}: MultiComboboxProps) {
  const [open, setOpen] = React.useState(false);
  const [searchQuery, setSearchQuery] = React.useState('');
  const searchInputRef = React.useRef<HTMLInputElement>(null);

  const filteredOptions = React.useMemo(() => {
    if (!searchQuery) return options;
    const query = searchQuery.toLowerCase();
    return options.filter((opt) => opt.label.toLowerCase().includes(query));
  }, [options, searchQuery]);

  const selectedSet = React.useMemo(() => new Set(value), [value]);

  const selectedLabels = React.useMemo(
    () => value.map((v) => options.find((o) => o.value === v)).filter(Boolean) as MultiComboboxOption[],
    [value, options]
  );

  React.useEffect(() => {
    if (open) setTimeout(() => searchInputRef.current?.focus(), 0);
    else setSearchQuery('');
  }, [open]);

  const toggle = (optionValue: string) => {
    onChange(selectedSet.has(optionValue) ? value.filter((v) => v !== optionValue) : [...value, optionValue]);
  };

  const remove = (optionValue: string) => {
    onChange(value.filter((v) => v !== optionValue));
  };

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <button
          type="button"
          disabled={disabled}
          className={cn(
            'flex min-h-[2.5rem] w-full items-center justify-between rounded-md border border-gray-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-3 py-1.5 text-sm text-gray-900 dark:text-gray-100',
            'focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:cursor-not-allowed disabled:opacity-50',
            className
          )}
        >
          <div className="flex flex-wrap gap-1 flex-1 min-w-0">
            {selectedLabels.length === 0 ? (
              <span className="text-gray-400 dark:text-gray-500">{placeholder}</span>
            ) : (
              selectedLabels.map((opt) => (
                <span
                  key={opt.value}
                  className="inline-flex items-center gap-0.5 rounded bg-gray-100 dark:bg-slate-700 text-gray-800 dark:text-slate-200 px-1.5 py-0.5 text-xs font-medium"
                >
                  {opt.label}
                  <span
                    role="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      e.preventDefault();
                      remove(opt.value);
                    }}
                    className="ml-0.5 rounded-full hover:bg-gray-200 dark:hover:bg-slate-600 p-0.5 cursor-pointer"
                  >
                    <X className="h-2.5 w-2.5" />
                  </span>
                </span>
              ))
            )}
          </div>
          <ChevronDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
        </button>
      </Popover.Trigger>

      <Popover.Portal>
        <Popover.Content
          className="z-50 w-[var(--radix-popover-trigger-width)] rounded-md border border-gray-200 dark:border-slate-700 bg-white dark:bg-slate-800 shadow-md"
          align="start"
          sideOffset={4}
          onWheel={(e) => e.stopPropagation()}
        >
          <div className="flex items-center border-b border-gray-200 dark:border-slate-700 px-3 py-2">
            <Search className="mr-2 h-4 w-4 shrink-0 opacity-50" />
            <input
              ref={searchInputRef}
              type="text"
              placeholder={searchPlaceholder}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') setOpen(false);
              }}
              className="w-full text-sm outline-none placeholder:text-gray-400 dark:placeholder:text-gray-500 bg-transparent text-gray-900 dark:text-gray-100"
            />
          </div>

          <div className="max-h-[300px] overflow-y-auto p-1" onWheel={(e) => e.stopPropagation()}>
            {filteredOptions.length === 0 ? (
              <div className="py-6 text-center text-sm text-gray-500 dark:text-gray-400">{emptyText}</div>
            ) : (
              filteredOptions.map((option) => (
                <label
                  key={option.value}
                  className="flex items-center gap-2 cursor-pointer rounded-sm px-2 py-1.5 text-sm hover:bg-gray-100 dark:hover:bg-slate-700"
                >
                  <input
                    type="checkbox"
                    checked={selectedSet.has(option.value)}
                    onChange={() => toggle(option.value)}
                    className="rounded border-gray-300 dark:border-slate-600 text-blue-600 dark:text-blue-500 focus:ring-blue-500 dark:bg-slate-700"
                  />
                  <span className="text-gray-900 dark:text-gray-100">{option.label}</span>
                </label>
              ))
            )}
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
