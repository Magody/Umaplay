import { canonicalizeSupportName } from '@/utils/supportNames'

export const PLACEHOLDER = `/placeholder_card.png`; // add a neutral image; UI will fallback to this on final error.

export const supportTypeIcons: Record<string, string> = {
  SPD: '/icons/support_card_type_spd.png',
  STA: '/icons/support_card_type_sta.png',
  PWR: '/icons/support_card_type_pwr.png',
  GUTS: '/icons/support_card_type_guts.png',
  WIT: '/icons/support_card_type_wit.png',
  PAL: '/icons/support_card_type_friend.png',
  None: '/icons/support_card_type_wit.png', // fallback
}

export function supportImageCandidates(name: string, rarity: any, attr: any) {
  const base = `/events/support`
  const NAME = canonicalizeSupportName(name, rarity, attr)
  const ATTR = (attr || 'None').toUpperCase()
  const RAR  = rarity || 'None'
  return [
    `${base}/${NAME}_${ATTR}_${RAR}.png`,
  ]
}

export function scenarioImageCandidates(name: string) {
  const base = `/events/scenario`
  return [
    `${base}/${name}.png`,
  ]
}

export function traineeImageCandidates(name?: string) {
  const base = `/events/trainee`
  return [
    `${base}/${name}_profile.png`,
  ]
}

