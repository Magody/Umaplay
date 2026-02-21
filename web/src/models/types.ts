import type { EventSetup } from "@/types/events"

export type Mode = 'steam' | 'scrcpy' | 'bluestack' | 'adb'
export type Hotkey = 'F1' | 'F2' | 'F3' | 'F4'

export type StatKey = 'SPD' | 'STA' | 'PWR' | 'GUTS' | 'WIT'
export type MoodName = 'AWFUL' | 'BAD' | 'NORMAL' | 'GOOD' | 'GREAT'

export interface UnityCupMultiplierSet {
  white: number
  whiteCombo: number
  blueCombo: number
}

export interface UnityCupAdvancedScores {
  rainbowCombo: number
  whiteSpiritFill: number
  whiteSpiritExploded: number
  whiteComboPerFill: number
  blueSpiritEach: number
  blueComboPerExtraFill: number
}

export interface UnityCupAdvancedSettings {
  burstAllowedStats: StatKey[]
  scores: UnityCupAdvancedScores
  multipliers: {
    juniorClassic: UnityCupMultiplierSet
    senior: UnityCupMultiplierSet
  }
  opponentSelection: {
    race1: number
    race2: number
    race3: number
    race4: number
    defaultUnknown: number
  }
}

export interface GeneralConfig {
  mode: Mode
  windowTitle: string
  useAdb?: boolean
  adbDevice?: string
  fastMode: boolean
  tryAgainOnFailedGoal: boolean
  maxFailure: number
  acceptConsecutiveRace: boolean
  activeScenario: 'ura' | 'unity_cup'
  scenarioConfirmed: boolean
  advanced: {
    hotkey: Hotkey
    debugMode: boolean
    useExternalProcessor: boolean
    externalProcessorUrl: string
    autoRestMinimum: number
    undertrainThreshold: number // Percentage threshold for undertraining stats (0-100)
    topStatsFocus: number // Number of top stats to focus on (1-5)
    // Skills optimization (Raceday auto-buy gating)
    skillCheckInterval: number // Check skills every N turns (1 = every turn)
    skillPtsDelta: number // Only check if points increased by at least this amount
    // Career Loop automation settings
    careerLoop?: {
      enabled: boolean
      maxCareers: number | null // null or 0 = infinite
      preferredSupport: string
      preferredLevel: number // 1-100
      maxRefresh: number // 0-20
      refreshWait: number // 1.0-30.0 seconds
      errorThreshold: number // 1-20
    }
  }
}

export interface Preset {
  id: string
  name: string
  group?: string | null
  priorityStats: StatKey[]
  targetStats: Record<StatKey, number>
  minimalMood: MoodName
  juniorStyle: 'end' | 'late' | 'pace' | 'front' | null
  skillsToBuy: string[]
  skillPtsCheck: number
  event_setup?: EventSetup
  plannedRaces: Record<string, string> // dateKey -> raceName (Y{year}-{MM}-{half})
  plannedRacesTentative?: Record<string, boolean>
  raceIfNoGoodValue?: boolean // Whether to race even if no good training options are available
  prioritizeHint?: boolean // Moved from general to per-preset
  weakTurnSv?: number
  racePrecheckSv?: number
  lobbyPrecheckEnable?: boolean
  juniorMinimalMood?: MoodName | null
  goalRaceForceTurns?: number
  unityCupAdvanced?: UnityCupAdvancedSettings
}

export interface ScenarioConfig {
  presets: Preset[]
  activePresetId?: string
}

export interface AppConfig {
  version: number
  general: GeneralConfig
  scenarios: Record<'ura' | 'unity_cup' | string, ScenarioConfig>
}
