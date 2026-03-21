import { create } from 'zustand'
import { appConfigSchema, defaultAppConfig, defaultGeneral, defaultPreset, defaultEventSetup, defaultUnityCupAdvanced, unityCupAdvancedSchema } from '@/models/config.schema'
import type { AppConfig, GeneralConfig, Preset, ScenarioConfig, UnityCupAdvancedSettings } from '@/models/types'
import { canonicalizeEventSetupSupports } from '@/utils/supportNames'

const LS_KEY = 'uma:config:v1'

type State = {
  config: AppConfig
  uiTheme: 'dark' | 'light'
  uiGeneralCollapsed: boolean
  uiSelectedPresetId?: string
  uiScenarioKey: 'ura' | 'unity_cup'
}

type Actions = {
  setGeneral: (patch: Partial<GeneralConfig>) => void
  setScenario: (key: 'ura' | 'unity_cup') => void
  setActivePresetId: (id: string | undefined) => void
  setSelectedPresetId: (id: string | undefined) => void
  getScenarioBranch: (key?: string) => { key: 'ura' | 'unity_cup'; branch: ScenarioConfig }
  getActivePreset: () => { scenario: 'ura' | 'unity_cup'; id: string | undefined; preset: Preset | undefined }
  getSelectedPreset: () => { scenario: 'ura' | 'unity_cup'; id: string | undefined; preset: Preset | undefined }
  commitSelectedPreset: () => void
  addPreset: () => void
  copyPreset: (id: string) => void
  deletePreset: (id: string) => void
  renamePreset: (id: string, name: string) => void
  setPresetGroup: (id: string, group: string | null) => void
  renamePresetGroup: (oldName: string, newName: string) => void
  deletePresetGroup: (name: string) => void
  reorderPresets: (order: string[]) => void
  patchPreset: <K extends keyof Preset>(id: string, key: K, value: Preset[K]) => void
  replaceConfig: (cfg: AppConfig) => void
  saveLocal: () => void
  loadLocal: () => void
  exportJson: () => void
  importJson: (raw: unknown) => { ok: boolean; error?: string }
  setUiTheme: (mode: 'dark' | 'light') => void
  setGeneralCollapsed: (v: boolean) => void
}

const newId = () => (crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2))

const normalizeScenario = (value: unknown): 'ura' | 'unity_cup' =>
  value === 'unity_cup' ? 'unity_cup' : 'ura'

function normalizePreset(raw: any, general: Partial<GeneralConfig> | undefined, scenario: 'ura' | 'unity_cup'): Preset {
  const base = defaultPreset(raw?.id ?? newId(), raw?.name ?? 'Preset', scenario)
  const fallbackSkillPts = Number((general as any)?.skillPtsCheck ?? 600)
  const skillPts = Number.isFinite(Number(raw?.skillPtsCheck))
    ? Math.max(0, Number(raw?.skillPtsCheck))
    : Math.max(0, fallbackSkillPts)

  let unityCupAdvanced: UnityCupAdvancedSettings | undefined
  if (scenario === 'unity_cup') {
    try {
      const parsed = unityCupAdvancedSchema.parse(raw?.unityCupAdvanced ?? base.unityCupAdvanced ?? defaultUnityCupAdvanced())
      unityCupAdvanced = parsed
    } catch {
      unityCupAdvanced = defaultUnityCupAdvanced()
    }
  }

  return {
    ...base,
    ...raw,
    id: raw?.id ?? base.id,
    name: raw?.name ?? base.name,
    skillPtsCheck: skillPts,
    prioritizeHint:
      typeof raw?.prioritizeHint === 'boolean'
        ? raw.prioritizeHint
        : !!(general as any)?.prioritizeHint,
    event_setup: canonicalizeEventSetupSupports(raw?.event_setup ?? base.event_setup) ?? base.event_setup,
    unityCupAdvanced: unityCupAdvanced ?? base.unityCupAdvanced,
  }
}

const ensureScenarioMap = (scenarios?: Record<string, ScenarioConfig>): Record<string, ScenarioConfig> => {
  const map: Record<string, ScenarioConfig> = { ...(scenarios ?? {}) }
  if (!map.ura) map.ura = { presets: [], activePresetId: undefined }
  if (!map.unity_cup) map.unity_cup = { presets: [], activePresetId: undefined }
  return map
}

const resolveScenario = (config: AppConfig, scenario?: string) => {
  const key = normalizeScenario(scenario ?? config.general?.activeScenario)
  const map = ensureScenarioMap(config.scenarios)
  const branch = map[key] ?? { presets: [], activePresetId: undefined }
  map[key] = branch
  return { key, branch, map }
}

const migrateConfig = (cfg: AppConfig): AppConfig => {
  console.log('[MIGRATE] Starting migration with config:', cfg)
  
  const baseScenarios = ensureScenarioMap(defaultAppConfig().scenarios)
  const incomingScenarios = ensureScenarioMap((cfg as any).scenarios)
  const scenarios = ensureScenarioMap({ ...baseScenarios, ...incomingScenarios })

  const legacyPresets = (cfg as any).presets
  const legacyActive = (cfg as any).activePresetId
  
  console.log('[MIGRATE] Legacy presets found:', legacyPresets?.length ?? 0)
  console.log('[MIGRATE] Legacy activePresetId:', legacyActive)
  
  if (Array.isArray(legacyPresets)) {
    const normalized = legacyPresets.map((p: any) => normalizePreset(p, cfg.general, 'ura'))
    scenarios.ura = {
      presets: normalized,
      activePresetId:
        (typeof legacyActive === 'string' ? legacyActive : undefined) ?? normalized[0]?.id,
    }
    console.log('[MIGRATE] Migrated', normalized.length, 'presets to scenarios.ura')
  }

  for (const key of Object.keys(scenarios)) {
    const branch = scenarios[key] ?? { presets: [], activePresetId: undefined }
    const normalizedPresets = Array.isArray(branch.presets)
      ? branch.presets.map((p: any) => normalizePreset(p, cfg.general, key as 'ura' | 'unity_cup'))
      : []
    const activeId = branch.activePresetId && normalizedPresets.find((p) => p.id === branch.activePresetId)
      ? branch.activePresetId
      : normalizedPresets[0]?.id
    scenarios[key] = {
      presets: normalizedPresets,
      activePresetId: activeId,
    }
  }

  const activeScenario = normalizeScenario(cfg?.general?.activeScenario)
  const result = {
    version: cfg.version ?? 1,
    general: {
      ...defaultGeneral,
      ...cfg.general,
      activeScenario,
    },
    scenarios,
  }
  
  console.log('[MIGRATE] Migration complete. Result:', result)
  console.log('[MIGRATE] URA presets count:', result.scenarios.ura?.presets?.length ?? 0)
  console.log('[MIGRATE] Unity Cup presets count:', result.scenarios.unity_cup?.presets?.length ?? 0)
  
  return result
}

export const useConfigStore = create<State & Actions>((set, get) => ({
  config: defaultAppConfig(),
  uiTheme: 'light',
  uiGeneralCollapsed: false,
  uiSelectedPresetId: undefined,
  uiScenarioKey: 'ura',

  // ---- general
  setGeneral: (patch) =>
    set((s) => ({
      config: {
        ...s.config,
        general: { ...s.config.general, ...patch },
      },
    })),

  // ---- presets
  setScenario: (key) =>
    set((s) => {
      const scenarioKey = normalizeScenario(key)
      const { key: resolvedKey, branch, map } = resolveScenario(s.config, scenarioKey)
      return {
        config: {
          ...s.config,
          general: { ...s.config.general, activeScenario: resolvedKey, scenarioConfirmed: true },
          scenarios: map,
        },
        uiScenarioKey: resolvedKey,
        uiSelectedPresetId: branch.activePresetId ?? branch.presets[0]?.id,
      }
    }),

  setActivePresetId: (id) =>
    set((s) => {
      const { key, branch, map } = resolveScenario(s.config, s.uiScenarioKey)
      return {
        config: {
          ...s.config,
          scenarios: { ...map, [key]: { ...branch, activePresetId: id } },
        },
      }
    }),

  setSelectedPresetId: (id) => set({ uiSelectedPresetId: id }),

  getScenarioBranch: (targetKey) => {
    const { config } = get()
    const { key, branch } = resolveScenario(config, targetKey)
    return { key, branch }
  },

  getActivePreset: () => {
    const { config } = get()
    const { key, branch } = resolveScenario(config)
    const activeId = branch.activePresetId ?? branch.presets[0]?.id
    return {
      scenario: key,
      id: activeId,
      preset: activeId ? branch.presets.find((p) => p.id === activeId) : undefined,
    }
  },

  getSelectedPreset: () => {
    const { config, uiSelectedPresetId, uiScenarioKey } = get()
    const { key, branch } = resolveScenario(config, uiScenarioKey)
    const selectedId = uiSelectedPresetId ?? branch.activePresetId ?? branch.presets[0]?.id
    return {
      scenario: key,
      id: selectedId,
      preset: branch.presets.find((p) => p.id === selectedId),
    }
  },

  commitSelectedPreset: () =>
    set((s) => {
      const { key, branch, map } = resolveScenario(s.config, s.uiScenarioKey)
      const selectedId = s.uiSelectedPresetId ?? branch.activePresetId ?? branch.presets[0]?.id
      if (!selectedId) return {}
      return {
        config: {
          ...s.config,
          scenarios: { ...map, [key]: { ...branch, activePresetId: selectedId } },
        },
        uiSelectedPresetId: selectedId,
      }
    }),

  addPreset: () =>
    set((s) => {
      const { key, branch, map } = resolveScenario(s.config, s.uiScenarioKey)
      const newPresetId = newId()
      const newPresetName = `Preset ${(branch.presets?.length ?? 0) + 1}`
      const preset = defaultPreset(newPresetId, newPresetName, key)
      preset.event_setup = defaultEventSetup()
      console.log('[addPreset] Created new preset:', { id: preset.id, name: preset.name, scenario: key, skillsToBuy: preset.skillsToBuy, plannedRaces: preset.plannedRaces })
      const presets = [...branch.presets, preset]
      const activeId = branch.activePresetId ?? presets[0]?.id ?? preset.id
      return {
        config: {
          ...s.config,
          scenarios: { ...map, [key]: { ...branch, presets, activePresetId: activeId } },
        },
        uiSelectedPresetId: newPresetId,
      }
    }),

  copyPreset: (id) =>
    set((s) => {
      const { key, branch, map } = resolveScenario(s.config, s.uiScenarioKey)
      const src = branch.presets.find((p) => p.id === id)
      if (!src) return {}
      const clone: Preset = {
        ...src,
        id: newId(),
        name: src.name + ' (copy)',
        event_setup: JSON.parse(JSON.stringify(src.event_setup ?? defaultEventSetup())),
        unityCupAdvanced: src.unityCupAdvanced
          ? JSON.parse(JSON.stringify(src.unityCupAdvanced))
          : src.unityCupAdvanced,
      }
      const presets = [...branch.presets, clone]
      return {
        config: {
          ...s.config,
          scenarios: { ...map, [key]: { ...branch, presets, activePresetId: clone.id } },
        },
        uiSelectedPresetId: clone.id,
      }
    }),

  deletePreset: (id) =>
    set((s) => {
      const { key, branch, map } = resolveScenario(s.config, s.uiScenarioKey)
      const left = branch.presets.filter((p) => p.id !== id)
      const presets = left.length
        ? left
        : [{ ...defaultPreset(newId(), 'Preset 1', key), event_setup: defaultEventSetup() }]
      const nextSelected = presets[presets.length - 1]?.id
      const nextActive = presets.find((p) => p.id === branch.activePresetId)
        ? branch.activePresetId
        : nextSelected
      return {
        config: {
          ...s.config,
          scenarios: { ...map, [key]: { ...branch, presets, activePresetId: nextActive } },
        },
        uiSelectedPresetId: nextSelected,
      }
    }),

  renamePreset: (id, name) =>
    set((s) => {
      const { key, branch, map } = resolveScenario(s.config, s.uiScenarioKey)
      return {
        config: {
          ...s.config,
          scenarios: {
            ...map,
            [key]: {
              ...branch,
              presets: branch.presets.map((p) => (p.id === id ? { ...p, name } : p)),
            },
          },
        },
      }
    }),

  setPresetGroup: (id, group) =>
    set((s) => {
      const { key, branch, map } = resolveScenario(s.config, s.uiScenarioKey)
      return {
        config: {
          ...s.config,
          scenarios: {
            ...map,
            [key]: {
              ...branch,
              presets: branch.presets.map((p) => (p.id === id ? { ...p, group } : p)),
            },
          },
        },
      }
    }),

  renamePresetGroup: (oldName, newName) =>
    set((s) => {
      if (!oldName || oldName === newName) return {}
      const { key, branch, map } = resolveScenario(s.config, s.uiScenarioKey)
      return {
        config: {
          ...s.config,
          scenarios: {
            ...map,
            [key]: {
              ...branch,
              presets: branch.presets.map((p) =>
                (p as any).group === oldName ? { ...p, group: newName } : p,
              ),
            },
          },
        },
      }
    }),

  deletePresetGroup: (name) =>
    set((s) => {
      if (!name) return {}
      const { key, branch, map } = resolveScenario(s.config, s.uiScenarioKey)
      return {
        config: {
          ...s.config,
          scenarios: {
            ...map,
            [key]: {
              ...branch,
              presets: branch.presets.map((p) =>
                (p as any).group === name ? { ...p, group: null } : p,
              ),
            },
          },
        },
      }
    }),

  reorderPresets: (order) =>
    set((s) => {
      const { key, branch, map } = resolveScenario(s.config, s.uiScenarioKey)
      const idToPreset = new Map<string, Preset>()
      for (const p of branch.presets) {
        idToPreset.set(p.id, p)
      }
      const next: Preset[] = []
      for (const id of order) {
        const p = idToPreset.get(id)
        if (p) {
          next.push(p)
          idToPreset.delete(id)
        }
      }
      for (const p of branch.presets) {
        if (idToPreset.has(p.id)) {
          next.push(p)
          idToPreset.delete(p.id)
        }
      }
      return {
        config: {
          ...s.config,
          scenarios: {
            ...map,
            [key]: {
              ...branch,
              presets: next,
            },
          },
        },
      }
    }),

  patchPreset: (id, keyPatch, value) =>
    set((s) => {
      const { key, branch, map } = resolveScenario(s.config, s.uiScenarioKey)
      const normalizedValue = keyPatch === 'event_setup'
        ? (canonicalizeEventSetupSupports(value as Preset['event_setup']) as Preset[typeof keyPatch])
        : value
      return {
        config: {
          ...s.config,
          scenarios: {
            ...map,
            [key]: {
              ...branch,
              presets: branch.presets.map((p) => (p.id === id ? { ...p, [keyPatch]: normalizedValue } : p)),
            },
          },
        },
      }
    }),

  // ---- io
  replaceConfig: (cfg) => set(() => {
    // Normalize scenarios map & migrate legacy structures
    const normalized = migrateConfig(cfg)
    return {
      config: normalized,
      uiSelectedPresetId: normalized.scenarios[normalized.general.activeScenario]?.activePresetId,
      uiScenarioKey: normalized.general.activeScenario,
    }
  }),

  saveLocal: () => {
    const snapshot = mirrorGeneralFromPreset(get().config)
    
    // Safety check: don't save if all scenarios are empty (likely a bug/data loss)
    const uraPresetsCount = snapshot.scenarios?.ura?.presets?.length ?? 0
    const unityCupPresetsCount = snapshot.scenarios?.unity_cup?.presets?.length ?? 0
    const totalPresets = uraPresetsCount + unityCupPresetsCount
    
    if (totalPresets === 0) {
      const existing = localStorage.getItem(LS_KEY)
      if (existing) {
        const parsed = JSON.parse(existing)
        const existingCount = (parsed.presets?.length ?? 0) + 
                              (parsed.scenarios?.ura?.presets?.length ?? 0) + 
                              (parsed.scenarios?.unity_cup?.presets?.length ?? 0)
        if (existingCount > 0) {
          console.warn('[SAVE] BLOCKED: Attempted to overwrite', existingCount, 'presets with empty config. Keeping existing data.')
          return
        }
      }
    }
    
    console.log('[SAVE] Saving to localStorage:', totalPresets, 'total presets')
    localStorage.setItem(LS_KEY, JSON.stringify(snapshot))
  },

  loadLocal: () => {
    const raw = localStorage.getItem(LS_KEY)
    console.log('[LOAD] localStorage key:', LS_KEY)
    console.log('[LOAD] Raw localStorage data length:', raw?.length ?? 0)
    if (!raw) {
      console.log('[LOAD] No localStorage data found')
      return
    }
    try {
      const parsed = JSON.parse(raw)
      console.log('[LOAD] Parsed localStorage:', parsed)
      console.log('[LOAD] Has legacy presets?', Array.isArray(parsed.presets))
      console.log('[LOAD] Legacy presets count:', parsed.presets?.length ?? 0)
      
      // Migrate first, then validate - this allows legacy structures to pass through
      const normalized = migrateConfig(parsed as any)
      
      console.log('[LOAD] After migration, validating with schema...')
      // Now validate the migrated structure
      const safe = appConfigSchema.parse(normalized)
      
      console.log('[LOAD] Validation passed, setting state...')
      set({
        config: safe,
        uiSelectedPresetId: safe.scenarios[safe.general.activeScenario]?.activePresetId,
        uiScenarioKey: safe.general.activeScenario,
      })
      console.log('[LOAD] State updated successfully')
    } catch (err) {
      console.error('[LOAD] Failed to load config from localStorage:', err)
      // ignore
    }
  },

  exportJson: () => {
    const snapshot = mirrorGeneralFromPreset(get().config)
    const blob = new Blob([JSON.stringify(snapshot, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'config.json'
    a.click()
    URL.revokeObjectURL(url)
  },

  importJson: (raw) => {
    try {
      const safe = appConfigSchema.parse(raw)
      const normalized = migrateConfig(safe)
      set({
        config: normalized,
        uiSelectedPresetId: normalized.scenarios[normalized.general.activeScenario]?.activePresetId,
        uiScenarioKey: normalized.general.activeScenario,
      })
      return { ok: true }
    } catch (e: any) {
      return { ok: false, error: String(e?.message || e) }
    }
  },

  // ---- theme
  setUiTheme: (mode) => set({ uiTheme: mode }),
  setGeneralCollapsed: (v: boolean) => set({ uiGeneralCollapsed: v }),
}))

function mirrorGeneralFromPreset(config: AppConfig): AppConfig {
  const { key, branch, map } = resolveScenario(config, config.general.activeScenario)
  const activeId = branch.activePresetId ?? branch.presets[0]?.id
  const activePreset = branch.presets.find((p) => p.id === activeId)

  const general = { ...config.general } as Record<string, any>
  general.prioritizeHint = activePreset ? !!activePreset.prioritizeHint : !!general.prioritizeHint
  general.skillPtsCheck = activePreset?.skillPtsCheck ?? general.skillPtsCheck ?? 600

  return {
    ...config,
    general: general as GeneralConfig,
    scenarios: { ...map, [key]: { ...branch, activePresetId: activeId } },
  }
}
