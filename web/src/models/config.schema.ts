import { z } from 'zod'
import type { AppConfig, GeneralConfig, Preset, StatKey } from './types'

type ScenarioKey = GeneralConfig['activeScenario']

// Lightweight re-declare to avoid circulars in schema:
const eventDefaults = { support: 1, trainee: 1, scenario: 1 }
const defaultRewardPriority = ['skill_pts', 'stats', 'hints'] as const
export const defaultEventSetup = () => ({
  supports: [null, null, null, null, null, null],
  scenario: null,
  trainee: null,
  prefs: { overrides: {}, patterns: [], defaults: eventDefaults, rewardPriority: [...defaultRewardPriority] },
})

const scenarioPresetDefaults: Record<ScenarioKey, { weakTurnSv: number; racePrecheckSv: number }> = {
  ura: {
    weakTurnSv: 1.0,
    racePrecheckSv: 2.5,
  },
  unity_cup: {
    weakTurnSv: 1.75,
    racePrecheckSv: 3.5,
  },
}

export const STAT_KEYS: StatKey[] = ['SPD', 'STA', 'PWR', 'GUTS', 'WIT']

const UNITY_CUP_DEFAULT_BURST_ALLOWED_STATS: StatKey[] = ['SPD', 'STA', 'PWR', 'WIT']

const unityCupOpponentValue = z.number().int().min(1).max(3)

const unityCupMultiplierSchema = z.object({
  white: z.number().min(0).max(10).default(1),
  whiteCombo: z.number().min(0).max(10).default(1),
  blueCombo: z.number().min(0).max(10).default(1),
})

export const unityCupAdvancedSchema = z.object({
  burstAllowedStats: z
    .array(z.enum(STAT_KEYS))
    .min(0)
    .max(STAT_KEYS.length)
    .default([...UNITY_CUP_DEFAULT_BURST_ALLOWED_STATS]),
  scores: z
    .object({
      rainbowCombo: z.number().min(0).max(10).default(0.5),
      whiteSpiritFill: z.number().min(0).max(10).default(0.4),
      whiteSpiritExploded: z.number().min(0).max(10).default(0.13),
      whiteComboPerFill: z.number().min(0).max(10).default(0.25),
      blueSpiritEach: z.number().min(0).max(10).default(0.5),
      blueComboPerExtraFill: z.number().min(0).max(10).default(0.25),
    })
    .default({
      rainbowCombo: 0.5,
      whiteSpiritFill: 0.4,
      whiteSpiritExploded: 0.13,
      whiteComboPerFill: 0.25,
      blueSpiritEach: 0.5,
      blueComboPerExtraFill: 0.25,
    }),
  multipliers: z
    .object({
      juniorClassic: unityCupMultiplierSchema.default({ white: 1.5, whiteCombo: 1.5, blueCombo: 1.5 }),
      senior: unityCupMultiplierSchema.default({ white: 1, whiteCombo: 1, blueCombo: 1 }),
    })
    .default({
      juniorClassic: { white: 1.5, whiteCombo: 1.5, blueCombo: 1.5 },
      senior: { white: 1, whiteCombo: 1, blueCombo: 1 },
    }),
  opponentSelection: z
    .object({
      race1: unityCupOpponentValue.default(2),
      race2: unityCupOpponentValue.default(1),
      race3: unityCupOpponentValue.default(1),
      race4: unityCupOpponentValue.default(1),
      defaultUnknown: unityCupOpponentValue.default(1),
    })
    .default({
      race1: 2,
      race2: 1,
      race3: 1,
      race4: 1,
      defaultUnknown: 1,
    }),
})

const UNITY_CUP_ADVANCED_DEFAULTS = {
  burstAllowedStats: [...UNITY_CUP_DEFAULT_BURST_ALLOWED_STATS],
  scores: {
    rainbowCombo: 0.5,
    whiteSpiritFill: 0.4,
    whiteSpiritExploded: 0.13,
    whiteComboPerFill: 0.25,
    blueSpiritEach: 0.5,
    blueComboPerExtraFill: 0.25,
  },
  multipliers: {
    juniorClassic: { white: 1.5, whiteCombo: 1.5, blueCombo: 1.5 },
    senior: { white: 1, whiteCombo: 1, blueCombo: 1 },
  },
  opponentSelection: { race1: 2, race2: 1, race3: 1, race4: 1, defaultUnknown: 1 },
} as const

export const defaultUnityCupAdvanced = () => ({
  burstAllowedStats: [...UNITY_CUP_ADVANCED_DEFAULTS.burstAllowedStats],
  scores: { ...UNITY_CUP_ADVANCED_DEFAULTS.scores },
  multipliers: {
    juniorClassic: { ...UNITY_CUP_ADVANCED_DEFAULTS.multipliers.juniorClassic },
    senior: { ...UNITY_CUP_ADVANCED_DEFAULTS.multipliers.senior },
  },
  opponentSelection: { ...UNITY_CUP_ADVANCED_DEFAULTS.opponentSelection },
})

export const generalSchema = z.object({
  mode: z.enum(['steam', 'scrcpy', 'bluestack', 'adb']).default('steam'),
  windowTitle: z.string().default('Umamusume'),
  useAdb: z.boolean().default(false),
  adbDevice: z.string().default('localhost:5555'),
  fastMode: z.boolean().default(false),
  tryAgainOnFailedGoal: z.boolean().default(true),
  maxFailure: z.number().int().min(0).max(99).default(20),
  acceptConsecutiveRace: z.boolean().default(true),
  activeScenario: z.enum(['ura', 'unity_cup']).default('ura'),
  scenarioConfirmed: z.boolean().default(false),
  advanced: z.object({
    hotkey: z.enum(['F1', 'F2', 'F3', 'F4']).default('F2'),
    debugMode: z.boolean().default(true),
    useExternalProcessor: z.boolean().default(false),
    externalProcessorUrl: z.string().url().default('http://127.0.0.1:8001'),
    autoRestMinimum: z.number().int().min(0).max(100).default(26),
    undertrainThreshold: z.number().min(0).max(100).default(6),
    topStatsFocus: z.number().int().min(1).max(5).default(3),
    skillCheckInterval: z.number().int().min(1).max(12).default(3),
    skillPtsDelta: z.number().int().min(0).max(1000).default(60),
    careerLoop: z.object({
      enabled: z.boolean().default(true),
      maxCareers: z.number().nullable().default(5),
      preferredSupport: z.string().default('Riko Kashimoto'),
      preferredLevel: z.number().int().min(1).max(100).default(50),
      maxRefresh: z.number().int().min(0).max(20).default(5),
      refreshWait: z.number().min(1).max(30).default(5),
      errorThreshold: z.number().int().min(1).max(20).default(5),
    }).optional(),
  }).default({
    hotkey: 'F2',
    debugMode: true,
    useExternalProcessor: false,
    externalProcessorUrl: 'http://127.0.0.1:8001',
    autoRestMinimum: 18,
    undertrainThreshold: 6,
    topStatsFocus: 3,
    skillCheckInterval: 3,
    skillPtsDelta: 60,
  }),
}).default({
  mode: 'steam',
  windowTitle: 'Umamusume',
  useAdb: false,
  adbDevice: 'localhost:5555',
  fastMode: false,
  tryAgainOnFailedGoal: true,
  maxFailure: 20,
  acceptConsecutiveRace: true,
  activeScenario: 'ura',
  scenarioConfirmed: false,
  advanced: {
    hotkey: 'F2',
    debugMode: true,
    useExternalProcessor: false,
    externalProcessorUrl: 'http://127.0.0.1:8001',
    autoRestMinimum: 18,
    undertrainThreshold: 6,
    topStatsFocus: 3,
    skillCheckInterval: 3,
    skillPtsDelta: 60,
  },
})

export const presetSchema = z.object({
  id: z.string(),
  name: z.string(),
  group: z
    .string()
    .max(80)
    .optional()
    .nullable()
    .default(null),
  priorityStats: z.array(z.enum(STAT_KEYS)).min(5).max(5),
  targetStats: z.record(z.enum(STAT_KEYS), z.number().int().min(0)),
  minimalMood: z.enum(['AWFUL', 'BAD', 'NORMAL', 'GOOD', 'GREAT']),
  juniorStyle: z.enum(['end', 'late', 'pace', 'front']).nullable(),
  skillsToBuy: z.array(z.string()),
  skillPtsCheck: z.number().int().min(0).default(600),
  plannedRaces: z.record(z.string(), z.string()),
  plannedRacesTentative: z.record(z.string(), z.boolean()).default({}),
  raceIfNoGoodValue: z.boolean().default(false),
  prioritizeHint: z.boolean().default(false),
  weakTurnSv: z.number().min(0).max(10).default(1.0),
  racePrecheckSv: z.number().min(0).max(10).default(2.5),
  lobbyPrecheckEnable: z.boolean().default(false),
  juniorMinimalMood: z.enum(['AWFUL', 'BAD', 'NORMAL', 'GOOD', 'GREAT']).nullable().default(null),
  goalRaceForceTurns: z.number().int().min(0).max(12).default(5),
  unityCupAdvanced: unityCupAdvancedSchema.optional().default(() => defaultUnityCupAdvanced()),
  // Make optional on input, but always present on output via default()
  event_setup: (() => {
    const rarity = z.enum(['SSR', 'SR', 'R'])
    const attr = z.enum(['SPD', 'STA', 'PWR', 'GUTS', 'WIT', 'PAL'])
    const supportPriority = z.object({
      enabled: z.boolean().default(true),
      scoreBlueGreen: z.number().min(0).max(10).default(0.75),
      scoreOrangeMax: z.number().min(0).max(10).default(0.5),
      skillsRequiredForPriority: z.array(z.string()).default([]),
      recheckAfterHint: z.boolean().default(false),
    }).default({
      enabled: true,
      scoreBlueGreen: 0.75,
      scoreOrangeMax: 0.5,
      skillsRequiredForPriority: [],
      recheckAfterHint: false,
    })

    const selectedSupport = z.object({
      slot: z.number(),
      name: z.string(),
      rarity,
      attribute: attr,
      rewardPriority: z.array(z.enum(['skill_pts', 'stats', 'hints'])).default(['skill_pts', 'stats', 'hints']).optional(),
      priority: supportPriority.optional(),
      avoidEnergyOverflow: z.boolean().default(true).optional(),
    })
    const selectedScenario = z.object({
      name: z.string(),
      avoidEnergyOverflow: z.boolean().default(true).optional(),
      rewardPriority: z.array(z.enum(['skill_pts', 'stats', 'hints'])).default(['skill_pts', 'stats', 'hints']).optional(),
    }).nullable()
    const selectedTrainee = z.object({
      name: z.string(),
      avoidEnergyOverflow: z.boolean().default(true).optional(),
      rewardPriority: z.array(z.enum(['skill_pts', 'stats', 'hints'])).default(['skill_pts', 'stats', 'hints']).optional(),
    }).nullable()

    const defaults = { support: 1, trainee: 1, scenario: 1 }
    const eventPrefs = z.object({
      // IMPORTANT: string keys! (EventKey)
      overrides: z.record(z.string(), z.number()).default({}),
      patterns: z.array(z.object({ pattern: z.string(), pick: z.number() })).default([]),
      defaults: z.object({
        support: z.number().default(1),
        trainee: z.number().default(1),
        scenario: z.number().default(1),
      }).default(defaults),
      rewardPriority: z
        .array(z.enum(['skill_pts', 'stats', 'hints']))
        .default(['skill_pts', 'stats', 'hints']),
    })

    const eventSetup = z.object({
      supports: z.array(selectedSupport.nullable()).length(6).default([null, null, null, null, null, null]),
      scenario: selectedScenario.default(null),
      trainee: selectedTrainee.default(null),
      prefs: eventPrefs.default({
        overrides: {},
        patterns: [],
        defaults: { support: 1, trainee: 1, scenario: 1 },
        rewardPriority: ['skill_pts', 'stats', 'hints'],
      }),
    })

    return eventSetup
  })().default({
    supports: [null, null, null, null, null, null],
    scenario: null,
    trainee: null,
    prefs: {
      overrides: {},
      patterns: [],
      defaults: { support: 1, trainee: 1, scenario: 1 },
      rewardPriority: ['skill_pts', 'stats', 'hints'],
    },
  }),
})

const scenarioConfigSchema = z.object({
  presets: z.array(presetSchema),
  activePresetId: z.string().optional(),
})

export const appConfigSchema = z.object({
  version: z.number().int(),
  general: generalSchema,
  scenarios: z
    .record(z.string(), scenarioConfigSchema)
    .default({
      ura: { presets: [], activePresetId: undefined },
      unity_cup: { presets: [], activePresetId: undefined },
    }),
})

export const defaultGeneral: GeneralConfig = generalSchema.parse({})
export const defaultPreset = (id: string, name: string, scenario: ScenarioKey = 'ura'): Preset => {
  const scenarioDefaults = scenarioPresetDefaults[scenario] ?? scenarioPresetDefaults.ura

  const preset = {
    id,
    name,
    group: null,
    priorityStats: ['SPD', 'STA', 'WIT', 'PWR', 'GUTS'],
    raceIfNoGoodValue: false,
    prioritizeHint: false,
    skillPtsCheck: 600,
    targetStats: {
      SPD: 1150,
      STA: 900,
      PWR: 700,
      GUTS: 250,
      WIT: 300,
    },
    minimalMood: 'NORMAL',
    juniorStyle: null,
    skillsToBuy: [],
    plannedRaces: {},
    weakTurnSv: scenarioDefaults.weakTurnSv,
    racePrecheckSv: scenarioDefaults.racePrecheckSv,
    lobbyPrecheckEnable: false,
    juniorMinimalMood: null,
    goalRaceForceTurns: 5,
    event_setup: defaultEventSetup(),
  } as Preset & { unityCupAdvanced?: ReturnType<typeof defaultUnityCupAdvanced> }

  if (scenario === 'unity_cup') {
    preset.unityCupAdvanced = defaultUnityCupAdvanced()
  }

  return preset
}

export const defaultAppConfig = (): AppConfig => ({
  version: 1,
  general: defaultGeneral,
  scenarios: {
    ura: {
      presets: [defaultPreset(crypto.randomUUID(), 'Preset 1')],
      activePresetId: undefined,
    },
    unity_cup: {
      presets: [defaultPreset(crypto.randomUUID(), 'Preset 1', 'unity_cup')],
      activePresetId: undefined,
    },
  },
})
