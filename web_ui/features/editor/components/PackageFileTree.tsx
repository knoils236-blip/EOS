'use client';

import { useState, useCallback, useMemo, useEffect } from 'react';
import {
  ChevronRight,
  ChevronDown,
  FileCode,
  Folder,
  FolderOpen,
  Plus,
  Trash2,
  Search,
  X,
  Package as PackageIcon,
  RefreshCw,
  Edit,
  Cpu,
  Settings,
  FlaskConical,
  Microscope,
} from 'lucide-react';
import { useEditorStore } from '@/lib/stores/editorStore';
import { entityBasename, getEntityColor, validateEntityName } from '@/lib/utils/editor-utils';
import { ConfirmationDialog } from '@/features/management/components/dialogs/ConfirmationDialog';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import { TIMING } from '@/lib/constants/theme';
import type { Package, EntityType, EntityLeafNode, FolderNode, TreeNode, EntityTree } from '@/lib/types/filesystem';

const EXPANDED_PACKAGES_KEY = 'eos-editor-expanded-packages';
const EXPANDED_ENTITY_TYPES_KEY = 'eos-editor-expanded-entity-types';
const EXPANDED_FOLDERS_KEY = 'eos-editor-expanded-folders';

// Indent step per nesting level, in pixels.
const INDENT_PX = 12;
const BASE_INDENT_PX = 8;

const loadExpandedState = (key: string): Set<string> => {
  if (typeof window === 'undefined') return new Set();
  try {
    const stored = localStorage.getItem(key);
    return stored ? new Set(JSON.parse(stored)) : new Set();
  } catch {
    return new Set();
  }
};

const saveExpandedState = (key: string, state: Set<string>): void => {
  if (typeof window !== 'undefined') {
    try {
      localStorage.setItem(key, JSON.stringify(Array.from(state)));
    } catch {}
  }
};

// Unique identifier for any node in the tree, used for selection compares,
// cache keys, and the expandedFolders set.
const nodeKey = (pkg: string, type: EntityType, p: string) => `${pkg}/${type}/${p}`;

// Path-segment join that handles the empty-parent case cleanly.
const joinPath = (parent: string, child: string) => (parent ? `${parent}/${child}` : child);

const dirOf = (p: string): string => {
  const i = p.lastIndexOf('/');
  return i < 0 ? '' : p.slice(0, i);
};

// Walks from `p` upward, yielding nodeKeys for `p` itself (if non-empty) and every ancestor folder.
function selfAndAncestorKeys(pkg: string, type: EntityType, p: string): string[] {
  const keys: string[] = [];
  let cur = p;
  while (cur) {
    keys.push(nodeKey(pkg, type, cur));
    cur = dirOf(cur);
  }
  return keys;
}

// Adds keys to a Set; returns the same Set reference when nothing changed
// so React's setState can short-circuit downstream renders.
function addKeysIfChanged(prev: Set<string>, keys: Iterable<string>): Set<string> {
  let next: Set<string> | null = null;
  for (const k of keys) {
    if (prev.has(k)) continue;
    if (!next) next = new Set(prev);
    next.add(k);
  }
  return next ?? prev;
}

// Prune a tree to ancestors of any leaf whose path matches the query.
function filterTree(nodes: TreeNode[], lowerQuery: string): TreeNode[] {
  const out: TreeNode[] = [];
  for (const node of nodes) {
    if (node.kind === 'entity') {
      if (node.path.toLowerCase().includes(lowerQuery)) out.push(node);
    } else {
      const kept = filterTree(node.children, lowerQuery);
      if (kept.length > 0) out.push({ ...node, children: kept });
    }
  }
  return out;
}

type FilteredPackage = { pkg: Package; matchingTrees: Partial<Record<EntityType, TreeNode[]>> };

const ENTITY_TYPE_LABELS: Record<EntityType, string> = {
  devices: '设备',
  tasks: '任务',
  labs: '实验室',
  protocols: '协议',
};

const ENTITY_TYPE_KEYS = Object.keys(ENTITY_TYPE_LABELS) as EntityType[];

const ENTITY_ICONS: Record<EntityType, React.ReactNode> = {
  devices: <Cpu className="w-3.5 h-3.5" />,
  tasks: <Settings className="w-3.5 h-3.5" />,
  labs: <FlaskConical className="w-3.5 h-3.5" />,
  protocols: <Microscope className="w-3.5 h-3.5" />,
};

function hasEntities(pkg: Package, type: EntityType): boolean {
  if (type === 'devices') return pkg.hasDevices;
  if (type === 'tasks') return pkg.hasTasks;
  if (type === 'labs') return pkg.hasLabs;
  if (type === 'protocols') return pkg.hasProtocols;
  return false;
}

// Shared state + handlers passed down through the tree, in lieu of prop drilling
// dozens of individual callbacks through every recursion level.
interface TreeContext {
  packageName: string;
  entityType: EntityType;
  cache: Record<string, unknown>;
  isSearching: boolean;
  selectedEntityType: EntityType | null;
  selectedEntityName: string | null;
  expandedFolders: Set<string>;
  renamingEntity: { packageName: string; entityType: EntityType; oldPath: string } | null;
  renameValue: string;
  creatingEntity: { packageName: string; entityType: EntityType; parentPath: string } | null;
  newEntityName: string;
  onToggleFolder: (key: string) => void;
  onCreateStart: (packageName: string, entityType: EntityType, parentPath: string) => void;
  onCreateConfirm: () => void;
  onCreateCancel: () => void;
  onNewEntityNameChange: (value: string) => void;
  onRenameValueChange: (value: string) => void;
  onRenameConfirm: () => void;
  onRenameCancel: () => void;
  onEntityClick: (packageName: string, entityType: EntityType, entityPath: string) => void;
  onContextMenu: (e: React.MouseEvent, packageName: string, entityType: EntityType, entityPath?: string) => void;
  onDeleteRequest: (packageName: string, entityType: EntityType, entityPath: string) => void;
}

// --- Inline create input -----------------------------------------------------

interface CreateInputProps {
  depth: number;
  value: string;
  onChange: (v: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
}

function CreateInput({ depth, value, onChange, onConfirm, onCancel }: CreateInputProps) {
  return (
    <div className="flex items-center gap-1 py-1 pr-2" style={{ paddingLeft: depth * INDENT_PX + BASE_INDENT_PX }}>
      <FileCode className="w-3 h-3 text-gray-400 flex-shrink-0" />
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') onConfirm();
          if (e.key === 'Escape') onCancel();
        }}
        onBlur={onCancel}
        placeholder="entity_name"
        className="text-xs flex-1 px-1 py-0.5 border border-blue-500 dark:border-yellow-500 rounded bg-white dark:bg-gray-800 min-w-0"
        autoFocus
      />
    </div>
  );
}

// --- Entity (leaf) row -------------------------------------------------------

interface EntityItemProps {
  entity: EntityLeafNode;
  depth: number;
  ctx: TreeContext;
}

function EntityItem({ entity, depth, ctx }: EntityItemProps) {
  const isRenaming =
    ctx.renamingEntity &&
    ctx.renamingEntity.packageName === ctx.packageName &&
    ctx.renamingEntity.entityType === ctx.entityType &&
    ctx.renamingEntity.oldPath === entity.path;

  if (isRenaming) {
    return (
      <div className="flex items-center gap-1 py-1 pr-2" style={{ paddingLeft: depth * INDENT_PX + BASE_INDENT_PX }}>
        <FileCode className="w-3 h-3 text-gray-400 flex-shrink-0" />
        <input
          type="text"
          value={ctx.renameValue}
          onChange={(e) => ctx.onRenameValueChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') ctx.onRenameConfirm();
            if (e.key === 'Escape') ctx.onRenameCancel();
          }}
          onBlur={ctx.onRenameCancel}
          placeholder="entity_name"
          className="text-xs flex-1 px-1 py-0.5 border border-blue-500 dark:border-yellow-500 rounded bg-white dark:bg-gray-800 min-w-0"
          autoFocus
        />
      </div>
    );
  }

  const cacheKey = nodeKey(ctx.packageName, ctx.entityType, entity.path);
  const hasUnsaved = cacheKey in ctx.cache;
  const isSelected = ctx.selectedEntityType === ctx.entityType && ctx.selectedEntityName === entity.path;

  return (
    <div
      className={`flex items-center gap-1 py-1 pr-2 rounded cursor-pointer group ${
        isSelected ? 'bg-blue-100 dark:bg-yellow-900' : 'hover:bg-gray-100 dark:hover:bg-gray-800'
      }`}
      style={{ paddingLeft: depth * INDENT_PX + BASE_INDENT_PX }}
      onClick={() => ctx.onEntityClick(ctx.packageName, ctx.entityType, entity.path)}
      onContextMenu={(e) => ctx.onContextMenu(e, ctx.packageName, ctx.entityType, entity.path)}
    >
      <FileCode className="w-3 h-3 text-gray-500 flex-shrink-0" />
      <span className="text-sm flex-1 flex items-center gap-1 min-w-0">
        {hasUnsaved && (
          <span
            className="w-1.5 h-1.5 rounded-full bg-blue-500 dark:bg-yellow-500 flex-shrink-0"
            title="未保存的更改"
          />
        )}
        <span className="truncate">{entity.name}</span>
      </span>
      <button
        onClick={(e) => {
          e.stopPropagation();
          ctx.onDeleteRequest(ctx.packageName, ctx.entityType, entity.path);
        }}
        className="p-0.5 opacity-0 group-hover:opacity-100 hover:bg-red-100 dark:hover:bg-red-900 rounded flex-shrink-0"
        title="删除"
      >
        <Trash2 className="w-3.5 h-3.5 text-red-600" />
      </button>
    </div>
  );
}

// --- Folder row + recursive children ----------------------------------------

interface FolderItemProps {
  folder: FolderNode;
  depth: number;
  ctx: TreeContext;
}

function FolderItem({ folder, depth, ctx }: FolderItemProps) {
  const key = nodeKey(ctx.packageName, ctx.entityType, folder.path);
  const isExpanded = ctx.isSearching || ctx.expandedFolders.has(key);
  const isCreatingHere =
    ctx.creatingEntity &&
    ctx.creatingEntity.packageName === ctx.packageName &&
    ctx.creatingEntity.entityType === ctx.entityType &&
    ctx.creatingEntity.parentPath === folder.path;

  return (
    <>
      <div
        className="flex items-center gap-1 py-1 pr-2 rounded hover:bg-gray-100 dark:hover:bg-gray-800 cursor-pointer group"
        style={{ paddingLeft: depth * INDENT_PX + BASE_INDENT_PX }}
        onClick={() => ctx.onToggleFolder(key)}
      >
        {isExpanded ? (
          <ChevronDown className="w-3 h-3 flex-shrink-0" />
        ) : (
          <ChevronRight className="w-3 h-3 flex-shrink-0" />
        )}
        {isExpanded ? (
          <FolderOpen className="w-3.5 h-3.5 text-gray-500 flex-shrink-0" />
        ) : (
          <Folder className="w-3.5 h-3.5 text-gray-500 flex-shrink-0" />
        )}
        <span className="text-sm flex-1 truncate">{folder.name}</span>
        <button
          onClick={(e) => {
            e.stopPropagation();
            ctx.onCreateStart(ctx.packageName, ctx.entityType, folder.path);
          }}
          className="p-0.5 opacity-0 group-hover:opacity-100 hover:bg-gray-200 dark:hover:bg-gray-700 rounded flex-shrink-0"
          title="在此文件夹中新建"
        >
          <Plus className="w-3.5 h-3.5" />
        </button>
      </div>
      {isExpanded && (
        <>
          {folder.children.map((child) => (
            <TreeNodeView key={child.path} node={child} depth={depth + 1} ctx={ctx} />
          ))}
          {isCreatingHere && (
            <CreateInput
              depth={depth + 1}
              value={ctx.newEntityName}
              onChange={ctx.onNewEntityNameChange}
              onConfirm={ctx.onCreateConfirm}
              onCancel={ctx.onCreateCancel}
            />
          )}
        </>
      )}
    </>
  );
}

function TreeNodeView({ node, depth, ctx }: { node: TreeNode; depth: number; ctx: TreeContext }) {
  if (node.kind === 'entity') {
    return <EntityItem entity={node} depth={depth} ctx={ctx} />;
  }
  return <FolderItem folder={node} depth={depth} ctx={ctx} />;
}

// --- Entity-type section (root of each entity kind in a package) ------------

interface EntityTypeSectionProps {
  entityType: EntityType;
  nodes: TreeNode[];
  isExpanded: boolean;
  onToggle: (key: string) => void;
  ctx: TreeContext;
}

function EntityTypeSection({ entityType, nodes, isExpanded, onToggle, ctx }: EntityTypeSectionProps) {
  const typeKey = `${ctx.packageName}-${entityType}`;
  const effectiveExpanded = ctx.isSearching || isExpanded;
  const isCreatingAtRoot =
    ctx.creatingEntity &&
    ctx.creatingEntity.packageName === ctx.packageName &&
    ctx.creatingEntity.entityType === entityType &&
    ctx.creatingEntity.parentPath === '';

  return (
    <div className="mb-1">
      <div className="flex items-center gap-1 px-2 py-1 rounded hover:bg-gray-100 dark:hover:bg-gray-800 group">
        <div className="flex-1 flex items-center gap-1 cursor-pointer min-w-0" onClick={() => onToggle(typeKey)}>
          {effectiveExpanded ? (
            <ChevronDown className="w-3 h-3 flex-shrink-0" />
          ) : (
            <ChevronRight className="w-3 h-3 flex-shrink-0" />
          )}
          <span className={`text-sm ${getEntityColor(entityType)} flex-shrink-0`}>{ENTITY_ICONS[entityType]}</span>
          <span className={`text-sm ${getEntityColor(entityType)} truncate`}>{ENTITY_TYPE_LABELS[entityType]}</span>
        </div>
        <button
          onClick={() => ctx.onCreateStart(ctx.packageName, entityType, '')}
          className="p-0.5 hover:bg-gray-200 dark:hover:bg-gray-700 rounded opacity-0 group-hover:opacity-100 flex-shrink-0"
          title="新建"
        >
          <Plus className="w-3.5 h-3.5" />
        </button>
      </div>
      {effectiveExpanded && (
        <>
          {nodes.map((node) => (
            <TreeNodeView key={node.path} node={node} depth={1} ctx={ctx} />
          ))}
          {isCreatingAtRoot && (
            <CreateInput
              depth={1}
              value={ctx.newEntityName}
              onChange={ctx.onNewEntityNameChange}
              onConfirm={ctx.onCreateConfirm}
              onCancel={ctx.onCreateCancel}
            />
          )}
        </>
      )}
    </div>
  );
}

// --- Package ----------------------------------------------------------------

interface PackageNodeProps {
  pkg: Package;
  matchingTrees: Partial<Record<EntityType, TreeNode[]>>;
  isExpanded: boolean;
  isSearching: boolean;
  expandedEntityTypes: Set<string>;
  entityTrees: Record<string, EntityTree>;
  onTogglePackage: (packageName: string) => void;
  onToggleEntityType: (key: string) => void;
  makeCtx: (packageName: string, entityType: EntityType) => TreeContext;
}

function PackageNode({
  pkg,
  matchingTrees,
  isExpanded,
  isSearching,
  expandedEntityTypes,
  entityTrees,
  onTogglePackage,
  onToggleEntityType,
  makeCtx,
}: PackageNodeProps) {
  const entityTree = isExpanded ? entityTrees[pkg.name] : null;

  return (
    <div className="mb-1">
      <div
        className="flex items-center gap-1 px-2 py-1.5 rounded hover:bg-gray-100 dark:hover:bg-gray-800 cursor-pointer"
        onClick={() => onTogglePackage(pkg.name)}
      >
        {isExpanded ? (
          <ChevronDown className="w-4 h-4 flex-shrink-0" />
        ) : (
          <ChevronRight className="w-4 h-4 flex-shrink-0" />
        )}
        <PackageIcon className="w-4 h-4 text-blue-500 dark:text-yellow-500 flex-shrink-0" />
        <span className="text-sm font-medium min-w-0 truncate">{pkg.name}</span>
      </div>

      {isExpanded && entityTree && (
        <div className="ml-4">
          {ENTITY_TYPE_KEYS.map((entityType) => {
            if (!hasEntities(pkg, entityType)) return null;

            const nodes = isSearching ? (matchingTrees[entityType] ?? []) : (entityTree[entityType] ?? []);
            if (isSearching && nodes.length === 0) return null;

            const typeKey = `${pkg.name}-${entityType}`;

            return (
              <EntityTypeSection
                key={entityType}
                entityType={entityType}
                nodes={nodes}
                isExpanded={expandedEntityTypes.has(typeKey)}
                onToggle={onToggleEntityType}
                ctx={makeCtx(pkg.name, entityType)}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}

// --- Main component ---------------------------------------------------------

interface PackageFileTreeProps {
  onCreateEntity: (packageName: string, entityType: EntityType, entityName: string) => void;
  onDeleteEntity: (packageName: string, entityType: EntityType, entityName: string) => void;
  onRenameEntity: (packageName: string, entityType: EntityType, oldName: string, newName: string) => void;
  onRefresh?: () => void;
  onPackageExpand: (packageName: string) => Promise<void>;
}

export function PackageFileTree({
  onCreateEntity,
  onDeleteEntity,
  onRenameEntity,
  onRefresh,
  onPackageExpand,
}: PackageFileTreeProps) {
  const packages = useEditorStore((state) => state.packages);
  const selectedPackage = useEditorStore((state) => state.selectedPackage);
  const selectedEntityType = useEditorStore((state) => state.selectedEntityType);
  const selectedEntityName = useEditorStore((state) => state.selectedEntityName);
  const entityTrees = useEditorStore((state) => state.entityTrees);
  const selectEntity = useEditorStore((state) => state.selectEntity);
  const cache = useEditorStore((state) => state.cache);

  const [expandedPackages, setExpandedPackages] = useState<Set<string>>(() => loadExpandedState(EXPANDED_PACKAGES_KEY));
  const [expandedEntityTypes, setExpandedEntityTypes] = useState<Set<string>>(() =>
    loadExpandedState(EXPANDED_ENTITY_TYPES_KEY)
  );
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(() => loadExpandedState(EXPANDED_FOLDERS_KEY));
  const [creatingEntity, setCreatingEntity] = useState<{
    packageName: string;
    entityType: EntityType;
    parentPath: string;
  } | null>(null);
  const [newEntityName, setNewEntityName] = useState('');
  const [renamingEntity, setRenamingEntity] = useState<{
    packageName: string;
    entityType: EntityType;
    oldPath: string;
  } | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    packageName: string;
    entityType: EntityType;
    entityPath?: string;
  } | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<{
    packageName: string;
    entityType: EntityType;
    entityPath: string;
  } | null>(null);

  const debouncedQuery = useDebouncedValue(searchQuery, TIMING.debounceDelay);

  useEffect(() => {
    saveExpandedState(EXPANDED_PACKAGES_KEY, expandedPackages);
  }, [expandedPackages]);

  useEffect(() => {
    saveExpandedState(EXPANDED_ENTITY_TYPES_KEY, expandedEntityTypes);
  }, [expandedEntityTypes]);

  useEffect(() => {
    saveExpandedState(EXPANDED_FOLDERS_KEY, expandedFolders);
  }, [expandedFolders]);

  // Auto-expand package, entity type, and every ancestor folder of the selected entity.
  useEffect(() => {
    if (!selectedPackage || !selectedEntityType || !selectedEntityName) return;

    setExpandedPackages((prev) => (prev.has(selectedPackage) ? prev : new Set([...prev, selectedPackage])));

    const typeKey = `${selectedPackage}-${selectedEntityType}`;
    setExpandedEntityTypes((prev) => (prev.has(typeKey) ? prev : new Set([...prev, typeKey])));

    const ancestorKeys = selfAndAncestorKeys(selectedPackage, selectedEntityType, dirOf(selectedEntityName));
    if (ancestorKeys.length > 0) {
      setExpandedFolders((prev) => addKeysIfChanged(prev, ancestorKeys));
    }
  }, [selectedPackage, selectedEntityType, selectedEntityName]);

  // Fetch entity trees for all packages on mount; auto-expand all packages if none persisted.
  useEffect(() => {
    Promise.all(packages.map((pkg) => onPackageExpand(pkg.name))).then(() => {
      setExpandedPackages((prev) => (prev.size === 0 ? new Set(packages.map((p) => p.name)) : prev));
    });
  }, [packages, onPackageExpand]);

  const togglePackage = useCallback(
    (packageName: string) => {
      setExpandedPackages((prev) => {
        const next = new Set(prev);
        if (next.has(packageName)) {
          next.delete(packageName);
        } else {
          next.add(packageName);
          onPackageExpand(packageName);
        }
        return next;
      });
    },
    [onPackageExpand]
  );

  const toggleEntityType = useCallback((key: string) => {
    setExpandedEntityTypes((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const toggleFolder = useCallback((key: string) => {
    setExpandedFolders((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const handleEntityClick = useCallback(
    (packageName: string, entityType: EntityType, entityPath: string) => {
      if (selectedPackage === packageName && selectedEntityType === entityType && selectedEntityName === entityPath) {
        return;
      }
      selectEntity(packageName, entityType, entityPath);
    },
    [selectEntity, selectedPackage, selectedEntityType, selectedEntityName]
  );

  const handleCreateEntityStart = useCallback((packageName: string, entityType: EntityType, parentPath: string) => {
    // Make sure the section/folder hosting the input is open so the input is visible.
    const typeKey = `${packageName}-${entityType}`;
    setExpandedEntityTypes((prev) => (prev.has(typeKey) ? prev : new Set([...prev, typeKey])));
    if (parentPath) {
      const keys = selfAndAncestorKeys(packageName, entityType, parentPath);
      setExpandedFolders((prev) => addKeysIfChanged(prev, keys));
    }
    setCreatingEntity({ packageName, entityType, parentPath });
    setNewEntityName('');
    setContextMenu(null);
  }, []);

  const handleCreateEntityConfirm = useCallback(() => {
    if (!creatingEntity || !newEntityName) return;
    const validation = validateEntityName(newEntityName);
    if (!validation.valid) {
      alert(validation.error);
      return;
    }
    const fullPath = joinPath(creatingEntity.parentPath, newEntityName);
    onCreateEntity(creatingEntity.packageName, creatingEntity.entityType, fullPath);
    setCreatingEntity(null);
    setNewEntityName('');
  }, [creatingEntity, newEntityName, onCreateEntity]);

  const handleCreateCancel = useCallback(() => setCreatingEntity(null), []);

  const handleContextMenu = useCallback(
    (e: React.MouseEvent, packageName: string, entityType: EntityType, entityPath?: string) => {
      e.preventDefault();
      setContextMenu({ x: e.clientX, y: e.clientY, packageName, entityType, entityPath });
    },
    []
  );

  const handleDeleteFromContext = useCallback(() => {
    if (contextMenu && contextMenu.entityPath) {
      setDeleteTarget({
        packageName: contextMenu.packageName,
        entityType: contextMenu.entityType,
        entityPath: contextMenu.entityPath,
      });
      setDeleteDialogOpen(true);
    }
    setContextMenu(null);
  }, [contextMenu]);

  const handleDeleteRequest = useCallback((packageName: string, entityType: EntityType, entityPath: string) => {
    setDeleteTarget({ packageName, entityType, entityPath });
    setDeleteDialogOpen(true);
  }, []);

  const handleRenameStart = useCallback(() => {
    if (contextMenu && contextMenu.entityPath) {
      setRenamingEntity({
        packageName: contextMenu.packageName,
        entityType: contextMenu.entityType,
        oldPath: contextMenu.entityPath,
      });
      setRenameValue(entityBasename(contextMenu.entityPath));
      setContextMenu(null);
    }
  }, [contextMenu]);

  const handleRenameConfirm = useCallback(() => {
    if (!renamingEntity || !renameValue) return;
    const basename = entityBasename(renamingEntity.oldPath);
    if (renameValue === basename) {
      setRenamingEntity(null);
      setRenameValue('');
      return;
    }
    const validation = validateEntityName(renameValue);
    if (!validation.valid) {
      alert(validation.error);
      return;
    }
    const newPath = joinPath(dirOf(renamingEntity.oldPath), renameValue);
    onRenameEntity(renamingEntity.packageName, renamingEntity.entityType, renamingEntity.oldPath, newPath);
    setRenamingEntity(null);
    setRenameValue('');
  }, [renamingEntity, renameValue, onRenameEntity]);

  const handleRenameCancel = useCallback(() => {
    setRenamingEntity(null);
    setRenameValue('');
  }, []);

  const isSearching = debouncedQuery.trim().length > 0;

  // Compute filtered packages: when searching, prune each entity-type tree to ancestors of matches.
  const filteredPackages = useMemo<FilteredPackage[]>(() => {
    if (!isSearching) {
      return packages.map((pkg) => ({ pkg, matchingTrees: {} })).sort((a, b) => a.pkg.name.localeCompare(b.pkg.name));
    }
    const q = debouncedQuery.toLowerCase();
    const filtered: FilteredPackage[] = [];
    for (const pkg of packages) {
      const tree = entityTrees[pkg.name];
      if (!tree) continue;
      const matchingTrees: Partial<Record<EntityType, TreeNode[]>> = {};
      let hit = false;
      for (const entityType of ENTITY_TYPE_KEYS) {
        if (!hasEntities(pkg, entityType)) continue;
        const pruned = filterTree(tree[entityType] ?? [], q);
        if (pruned.length > 0) {
          matchingTrees[entityType] = pruned;
          hit = true;
        }
      }
      if (hit) filtered.push({ pkg, matchingTrees });
    }
    return filtered;
  }, [isSearching, debouncedQuery, packages, entityTrees]);

  // While searching, ensure all matched packages are loaded + visually expanded
  // (without mutating persisted state any more than necessary).
  useEffect(() => {
    if (!isSearching || filteredPackages.length === 0) return;
    const packageNames = filteredPackages.map(({ pkg }) => pkg.name);
    const typeKeys: string[] = [];
    for (const { pkg, matchingTrees } of filteredPackages) {
      for (const entityType of Object.keys(matchingTrees) as EntityType[]) {
        typeKeys.push(`${pkg.name}-${entityType}`);
      }
    }
    setExpandedPackages((prev) => {
      const next = addKeysIfChanged(prev, packageNames);
      if (next !== prev) {
        for (const name of packageNames) {
          if (!prev.has(name)) onPackageExpand(name);
        }
      }
      return next;
    });
    setExpandedEntityTypes((prev) => addKeysIfChanged(prev, typeKeys));
  }, [isSearching, filteredPackages, onPackageExpand]);

  const handleClearSearch = useCallback(() => setSearchQuery(''), []);

  const handleRefreshPackages = useCallback(async () => {
    setIsRefreshing(true);
    try {
      onRefresh?.();
    } finally {
      setIsRefreshing(false);
    }
  }, [onRefresh]);

  // Factory: build a TreeContext for a given (packageName, entityType) pair.
  // Memoized on the things that actually change across renders.
  const makeCtx = useCallback(
    (packageName: string, entityType: EntityType): TreeContext => ({
      packageName,
      entityType,
      cache,
      isSearching,
      selectedEntityType,
      selectedEntityName,
      expandedFolders,
      renamingEntity,
      renameValue,
      creatingEntity,
      newEntityName,
      onToggleFolder: toggleFolder,
      onCreateStart: handleCreateEntityStart,
      onCreateConfirm: handleCreateEntityConfirm,
      onCreateCancel: handleCreateCancel,
      onNewEntityNameChange: setNewEntityName,
      onRenameValueChange: setRenameValue,
      onRenameConfirm: handleRenameConfirm,
      onRenameCancel: handleRenameCancel,
      onEntityClick: handleEntityClick,
      onContextMenu: handleContextMenu,
      onDeleteRequest: handleDeleteRequest,
    }),
    [
      cache,
      isSearching,
      selectedEntityType,
      selectedEntityName,
      expandedFolders,
      renamingEntity,
      renameValue,
      creatingEntity,
      newEntityName,
      toggleFolder,
      handleCreateEntityStart,
      handleCreateEntityConfirm,
      handleCreateCancel,
      handleRenameConfirm,
      handleRenameCancel,
      handleEntityClick,
      handleContextMenu,
      handleDeleteRequest,
    ]
  );

  return (
    <div className="h-full flex flex-col bg-white dark:bg-gray-900 border-r border-gray-200 dark:border-gray-700">
      <div className="p-3 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">软件包</h2>
        {onRefresh && (
          <button
            onClick={handleRefreshPackages}
            disabled={isRefreshing}
            className="p-1 hover:bg-gray-100 dark:hover:bg-gray-800 rounded transition-colors disabled:opacity-50"
            title="刷新软件包"
          >
            <RefreshCw className={`w-4 h-4 text-gray-600 dark:text-gray-400 ${isRefreshing ? 'animate-spin' : ''}`} />
          </button>
        )}
      </div>

      <div className="p-2">
        <div className="relative flex items-center gap-2">
          <div className="relative flex-1">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="搜索条目…"
              className="w-full pl-8 pr-8 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 dark:focus:ring-yellow-500"
            />
            {searchQuery && (
              <button
                onClick={handleClearSearch}
                className="absolute right-2 top-1/2 -translate-y-1/2 p-0.5 hover:bg-gray-200 dark:hover:bg-gray-700 rounded"
                title="清空搜索"
              >
                <X className="w-3.5 h-3.5 text-gray-500" />
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-2">
        {packages.length === 0 ? (
          <div className="text-sm text-gray-500 dark:text-gray-400 text-center py-8">未找到软件包</div>
        ) : isSearching && filteredPackages.length === 0 ? (
          <div className="text-sm text-gray-500 dark:text-gray-400 text-center py-8">没有匹配结果</div>
        ) : (
          filteredPackages.map(({ pkg, matchingTrees }: FilteredPackage) => (
            <PackageNode
              key={pkg.name}
              pkg={pkg}
              matchingTrees={matchingTrees}
              isExpanded={expandedPackages.has(pkg.name)}
              isSearching={isSearching}
              expandedEntityTypes={expandedEntityTypes}
              entityTrees={entityTrees}
              onTogglePackage={togglePackage}
              onToggleEntityType={toggleEntityType}
              makeCtx={makeCtx}
            />
          ))
        )}
      </div>

      {contextMenu && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setContextMenu(null)} />
          <div
            className="fixed z-50 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded shadow-lg py-1"
            style={{ left: contextMenu.x, top: contextMenu.y }}
          >
            <button
              onClick={() => handleCreateEntityStart(contextMenu.packageName, contextMenu.entityType, '')}
              className="w-full px-4 py-2 text-left text-sm hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2"
            >
              <Plus className="w-4 h-4" />
              新建 {contextMenu.entityType.slice(0, -1)}
            </button>
            {contextMenu.entityPath && (
              <>
                <button
                  onClick={handleRenameStart}
                  className="w-full px-4 py-2 text-left text-sm hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2"
                >
                  <Edit className="w-4 h-4" />
                  重命名
                </button>
                <button
                  onClick={handleDeleteFromContext}
                  className="w-full px-4 py-2 text-left text-sm hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2 text-red-600"
                >
                  <Trash2 className="w-4 h-4" />
                  删除
                </button>
              </>
            )}
          </div>
        </>
      )}

      {deleteTarget && (
        <ConfirmationDialog
          open={deleteDialogOpen}
          onOpenChange={setDeleteDialogOpen}
          title="删除条目"
          description={`确定要删除“${deleteTarget.entityPath}”吗？此操作无法撤销。`}
          confirmLabel="删除"
          variant="destructive"
          items={[deleteTarget.entityPath]}
          onConfirm={async () => {
            await onDeleteEntity(deleteTarget.packageName, deleteTarget.entityType, deleteTarget.entityPath);
            setDeleteTarget(null);
          }}
        />
      )}
    </div>
  );
}
