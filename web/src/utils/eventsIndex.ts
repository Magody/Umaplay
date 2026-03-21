import type {
  ChoiceEvent, EventsIndex, EventsRoot, RawChoiceEvent, RawEventSet,
  ScenarioSet, SupportSet, TraineeIndex, TraineeSet, SupportsIndex,
  AttrKey,
  Rarity
} from '@/types/events';
import {
  supportImageCandidates, scenarioImageCandidates, traineeImageCandidates
} from './imagePaths';
import { fetchEvents } from '@/services/api';

function toChoiceEvent(ev: RawChoiceEvent): ChoiceEvent {
  return {
    type: ev.type,
    chain_step: ev.chain_step ?? 1,
    name: ev.name,
    options: ev.options || {},
    default_preference: (typeof ev.default_preference === 'number') ? ev.default_preference : null,
  };
}

function normalizeSupportSet(raw: RawEventSet): SupportSet {
  // keep id + all useful metadata for richer UI later
  return {
    kind: 'support',
    id: raw.id,
    name: raw.name,
    attribute: (raw.attribute as AttrKey)!,
    rarity: (raw.rarity as Rarity)!,
    imgCandidates: supportImageCandidates(raw.name, (raw.rarity as Rarity)!, (raw.attribute as AttrKey)!),
    events: (raw.choice_events || []).map(toChoiceEvent),
  } as any;
}

function normalizeScenarioSet(raw: RawEventSet): ScenarioSet {
  return {
    kind: 'scenario',
    id: raw.id,
    name: raw.name,
    imgCandidates: scenarioImageCandidates(raw.name),
    events: (raw.choice_events || []).map(toChoiceEvent),
  } as any;

}

function normalizeTraineeSet(raw: RawEventSet): TraineeSet {
  return {
    kind: 'trainee',
    id: raw.id,
    name: raw.name,
    imgCandidates: traineeImageCandidates(raw.name),
    events: (raw.choice_events || []).map(toChoiceEvent),
  } as any;
}

function buildSupportsIndex(supports: SupportSet[]): SupportsIndex {
  const byAttr: SupportsIndex = new Map<AttrKey, Map<Rarity, SupportSet[]>>();
  for (const s of supports) {
    if (!byAttr.has(s.attribute)) byAttr.set(s.attribute, new Map<Rarity, SupportSet[]>());
    const byRarity = byAttr.get(s.attribute)!;
    if (!byRarity.has(s.rarity)) {
      byRarity.set(s.rarity, []);
    }
    const list = byRarity.get(s.rarity)!;
    list.push(s);
  }
  // sort each list by name for stable UI
  for (const [, byRarity] of byAttr) {
    for (const [, list] of byRarity) {
      list.sort((a, b) => a.name.localeCompare(b.name));
    }
  }
  return byAttr;
}

function buildTraineeIndex(trainees: TraineeSet[]): TraineeIndex {
  let general: TraineeSet | null = null;
  const specific = new Map<string, TraineeSet>();
  for (const t of trainees) {
    if (t.name.toLowerCase() === 'general') general = t;
    else specific.set(t.name, t);
  }
  return { general, specific };
}

export async function loadEventsIndex(): Promise<EventsIndex> {
  const root: EventsRoot = await fetchEvents()

  const supports: SupportSet[] = []
  const scenarios: ScenarioSet[] = []
  const trainees: TraineeSet[]  = []

  for (const row of root) {
    if (row.type === 'support') supports.push(normalizeSupportSet(row))
    else if (row.type === 'scenario') scenarios.push(normalizeScenarioSet(row))
    else if (row.type === 'trainee') trainees.push(normalizeTraineeSet(row))
  }

  scenarios.sort((a, b) => a.name.localeCompare(b.name))
  trainees.sort((a, b) => a.name.localeCompare(b.name))

  return {
    supports: buildSupportsIndex(supports),
    scenarios,
    trainees: buildTraineeIndex(trainees),
  }
}
