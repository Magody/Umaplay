const VALID_SUPPORT_RARITIES = new Set(['SSR', 'SR', 'R'])
const VALID_SUPPORT_ATTRIBUTES = new Set(['SPD', 'STA', 'PWR', 'GUTS', 'WIT', 'PAL'])

const LEGACY_SUPPORT_NAME_ALIASES: Record<string, string> = {
  'Aoi Kiryuuin': 'Aoi Kiryuin',
}

const normalizeSpacing = (value: string): string => value.trim().replace(/\s+/g, ' ')

const applyLegacyAlias = (name: string): string => {
  const normalized = normalizeSpacing(name)
  for (const [legacy, canonical] of Object.entries(LEGACY_SUPPORT_NAME_ALIASES)) {
    if (normalized === legacy || normalized.startsWith(`${legacy} `)) {
      return `${canonical}${normalized.slice(legacy.length)}`
    }
  }
  return normalized
}

export function canonicalizeSupportName(name: string, rarity: unknown, attribute: unknown): string {
  const normalized = applyLegacyAlias(name)
  const attr = typeof attribute === 'string' ? attribute.trim().toUpperCase() : ''
  const rar = typeof rarity === 'string' ? rarity.trim().toUpperCase() : ''

  if (!normalized || !VALID_SUPPORT_ATTRIBUTES.has(attr) || !VALID_SUPPORT_RARITIES.has(rar)) {
    return normalized
  }

  const suffix = `${attr} ${rar}`
  if (normalized.endsWith(suffix) || normalized.endsWith(`${suffix}(Duplicate)`)) {
    return normalized
  }

  return `${normalized} ${suffix}`
}

type SupportLike = {
  name: string
  rarity?: unknown
  attribute?: unknown
}

type PatternLike = {
  pattern: string
}

export function canonicalizeSupportSelection<T extends SupportLike | null | undefined>(support: T): T {
  if (!support || typeof support !== 'object' || typeof support.name !== 'string') {
    return support
  }

  return {
    ...support,
    name: canonicalizeSupportName(support.name, support.rarity, support.attribute),
  } as T
}

export function canonicalizeSupportEventKey(key: string): string {
  const match = key.match(/^support\/([^/]+)\/([^/]+)\/([^/]+)\/([\s\S]+)$/)
  if (!match) {
    return key
  }

  const [, name, attribute, rarity, rest] = match
  const canonicalName = canonicalizeSupportName(name, rarity, attribute)
  return `support/${canonicalName}/${attribute}/${rarity}/${rest}`
}

const canonicalizePattern = <T extends PatternLike | null | undefined>(entry: T): T => {
  if (!entry || typeof entry !== 'object' || typeof entry.pattern !== 'string') {
    return entry
  }

  return {
    ...entry,
    pattern: canonicalizeSupportEventKey(entry.pattern),
  } as T
}

export function canonicalizeEventSetupSupports<T extends { supports?: unknown } | null | undefined>(setup: T): T {
  if (!setup || typeof setup !== 'object') {
    return setup
  }

  const supports = (setup as { supports?: unknown }).supports
  const nextSupports = Array.isArray(supports)
    ? supports.map((entry) => canonicalizeSupportSelection(entry as SupportLike | null | undefined))
    : supports

  const prefs = (setup as { prefs?: unknown }).prefs
  const nextPrefs =
    prefs && typeof prefs === 'object'
      ? {
          ...(prefs as Record<string, unknown>),
          overrides:
            (prefs as { overrides?: unknown }).overrides &&
            typeof (prefs as { overrides?: unknown }).overrides === 'object'
              ? Object.fromEntries(
                  Object.entries((prefs as { overrides: Record<string, unknown> }).overrides).map(([key, value]) => [
                    canonicalizeSupportEventKey(key),
                    value,
                  ])
                )
              : (prefs as { overrides?: unknown }).overrides,
          patterns: Array.isArray((prefs as { patterns?: unknown }).patterns)
            ? (prefs as { patterns: unknown[] }).patterns.map((entry) => canonicalizePattern(entry as PatternLike | null | undefined))
            : (prefs as { patterns?: unknown }).patterns,
        }
      : prefs

  return {
    ...(setup as Record<string, unknown>),
    supports: nextSupports,
    prefs: nextPrefs,
  } as unknown as T
}
