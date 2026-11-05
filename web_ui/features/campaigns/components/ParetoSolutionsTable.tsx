'use client';

import * as React from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';

interface ParetoSolutionsTableProps {
  solutions: Array<Record<string, unknown>>;
  inputNames: string[];
  outputNames: string[];
}

const ROW_HEIGHT = 32;

export function ParetoSolutionsTable({ solutions, inputNames, outputNames }: ParetoSolutionsTableProps) {
  const scrollRef = React.useRef<HTMLDivElement>(null);

  const bestValues = React.useMemo(() => {
    const bests: Record<string, { value: number; isMin: boolean }> = {};
    outputNames.forEach((name) => {
      const values = solutions.map((s) => s[name] as number).filter((v) => v !== undefined);
      if (values.length === 0) return;
      const minVal = Math.min(...values);
      bests[name] = { value: minVal, isMin: true };
    });
    return bests;
  }, [solutions, outputNames]);

  const allColumns = React.useMemo(() => {
    const cols = new Set<string>();
    solutions.forEach((s) => {
      Object.keys(s).forEach((k) => cols.add(k));
    });
    return Array.from(cols);
  }, [solutions]);

  const inputCols = allColumns.filter((c) => inputNames.includes(c));
  const outputCols = allColumns.filter((c) => outputNames.includes(c));
  const otherCols = allColumns.filter((c) => !inputNames.includes(c) && !outputNames.includes(c));

  // One leading "#" column plus all data columns. Use minmax so each column flexes.
  const gridTemplate = `48px repeat(${inputCols.length + outputCols.length + otherCols.length}, minmax(96px, 1fr))`;

  const virtualizer = useVirtualizer({
    count: solutions.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 8,
  });

  if (solutions.length === 0) {
    return <p className="text-gray-500 dark:text-gray-400 text-center py-8">暂无帕累托解。</p>;
  }

  const headerCellCls = 'py-2 px-3 font-medium text-gray-500 dark:text-gray-400 text-left';
  const bodyCellCls = 'py-2 px-3 font-mono text-xs flex items-center';

  return (
    <div>
      <div className="overflow-x-auto">
        <div className="min-w-full text-sm">
          {/* Group header row: Inputs / Outputs labels */}
          <div
            className="grid border-b border-gray-200 dark:border-slate-700"
            style={{ gridTemplateColumns: gridTemplate }}
          >
            <div className={headerCellCls}>#</div>
            {inputCols.length > 0 && (
              <div
                className="py-2 px-3 font-medium text-gray-400 dark:text-gray-500 text-xs uppercase tracking-wider text-center border-l border-gray-200 dark:border-slate-700"
                style={{ gridColumn: `span ${inputCols.length}` }}
              >
                输入
              </div>
            )}
            {outputCols.length > 0 && (
              <div
                className="py-2 px-3 font-medium text-gray-400 dark:text-gray-500 text-xs uppercase tracking-wider text-center border-l border-gray-200 dark:border-slate-700"
                style={{ gridColumn: `span ${outputCols.length}` }}
              >
                输出
              </div>
            )}
            {otherCols.length > 0 && <div style={{ gridColumn: `span ${otherCols.length}` }} />}
          </div>

          {/* Column-name header row */}
          <div
            className="grid border-b border-gray-200 dark:border-slate-700"
            style={{ gridTemplateColumns: gridTemplate }}
          >
            <div className={headerCellCls}></div>
            {inputCols.map((col, idx) => (
              <div
                key={col}
                className={`${headerCellCls} ${idx === 0 ? 'border-l border-gray-200 dark:border-slate-700' : ''}`}
              >
                {col}
              </div>
            ))}
            {outputCols.map((col, idx) => (
              <div
                key={col}
                className={`${headerCellCls} ${idx === 0 ? 'border-l border-gray-200 dark:border-slate-700' : ''}`}
              >
                {col}
              </div>
            ))}
            {otherCols.map((col) => (
              <div key={col} className={headerCellCls}>
                {col}
              </div>
            ))}
          </div>

          {/* Virtualized rows */}
          <div ref={scrollRef} className="overflow-y-auto" style={{ maxHeight: 480 }}>
            <div style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
              {virtualizer.getVirtualItems().map((virtualRow) => {
                const idx = virtualRow.index;
                const solution = solutions[idx];
                return (
                  <div
                    key={idx}
                    data-index={idx}
                    className="grid border-b border-gray-100 dark:border-slate-800 hover:bg-gray-50 dark:hover:bg-slate-800/50"
                    style={{
                      gridTemplateColumns: gridTemplate,
                      position: 'absolute',
                      top: 0,
                      left: 0,
                      right: 0,
                      height: ROW_HEIGHT,
                      transform: `translateY(${virtualRow.start}px)`,
                    }}
                  >
                    <div className={`${bodyCellCls} text-gray-500 dark:text-gray-400`}>{idx + 1}</div>
                    {inputCols.map((col, colIdx) => (
                      <div
                        key={col}
                        className={`${bodyCellCls} text-gray-900 dark:text-white ${
                          colIdx === 0 ? 'border-l border-gray-200 dark:border-slate-700' : ''
                        }`}
                      >
                        {formatValue(solution[col])}
                      </div>
                    ))}
                    {outputCols.map((col, colIdx) => {
                      const value = solution[col] as number;
                      const best = bestValues[col];
                      const isBest = best && value === best.value;
                      return (
                        <div
                          key={col}
                          className={`${bodyCellCls} ${
                            colIdx === 0 ? 'border-l border-gray-200 dark:border-slate-700' : ''
                          } ${
                            isBest
                              ? 'text-green-600 dark:text-green-400 font-semibold'
                              : 'text-gray-900 dark:text-white'
                          }`}
                        >
                          {formatValue(value)}
                          {isBest && ' *'}
                        </div>
                      );
                    })}
                    {otherCols.map((col) => (
                      <div key={col} className={`${bodyCellCls} text-gray-900 dark:text-white`}>
                        {formatValue(solution[col])}
                      </div>
                    ))}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>

      <p className="text-xs text-gray-500 dark:text-gray-400 mt-2">* 各目标的最优值</p>
    </div>
  );
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '-';
  if (typeof value === 'number') {
    return Number.isInteger(value) ? value.toString() : value.toFixed(4);
  }
  return String(value);
}
