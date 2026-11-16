import type { Materials } from '../api/resources';

/** Where each container is, what it holds, and moves that are planned but not yet confirmed. */
export function MaterialsTable({ containers, samples }: Materials) {
  if (containers.length === 0) return null;
  return (
    <section aria-labelledby="materials-heading" className="mt-8">
      <h2 id="materials-heading" className="mb-3 text-lg font-semibold text-gray-900 dark:text-white">
        容器与样品
      </h2>
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-gray-500">
          <tr>
            <th className="py-2 pr-4">容器</th>
            <th className="py-2 pr-4">位置</th>
            <th className="py-2 pr-4">待确认移动</th>
            <th className="py-2">样品</th>
          </tr>
        </thead>
        <tbody>
          {containers.map((c) => (
            <tr key={c.id} className="border-t border-gray-200 dark:border-slate-700">
              <td className="py-2 pr-4 font-medium">
                {c.id} <span className="text-xs text-gray-500">{c.type}</span>
              </td>
              <td className="py-2 pr-4">
                {c.positionKnown ? (
                  (c.position ?? '—')
                ) : (
                  <span className="text-amber-700 dark:text-amber-400">
                    位置待核对（最后确认：{c.position ?? '无'}），依赖其位置的操作已暂停
                  </span>
                )}
              </td>
              <td className="py-2 pr-4">{c.reservedPosition ? `→ ${c.reservedPosition}` : '—'}</td>
              <td className="py-2">
                {samples
                  .filter((s) => s.containerId === c.id && !s.removed)
                  .map((s) => (
                    <p key={s.id}>
                      {s.id}
                      {s.volumeUl !== null && ` · ${s.volumeUl} uL`}
                      {s.parentId && <span className="text-xs text-gray-500"> · 分装自 {s.parentId}</span>}
                    </p>
                  ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
