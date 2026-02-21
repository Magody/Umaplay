import {
  Box, Collapse, Divider, FormControlLabel, MenuItem, Select, Slider, Stack, Switch, TextField, Typography,
} from '@mui/material'
import { useEffect, useMemo, useState, type SyntheticEvent } from 'react'
import FieldRow from '@/components/common/FieldRow'
import { useConfigStore } from '@/store/configStore'

export default function AdvancedSettings() {
  const { config, setGeneral } = useConfigStore()
  const [open, setOpen] = useState(false)
  const a = config.general.advanced

  const [autoRest, setAutoRest] = useState(a.autoRestMinimum)
  const [skillInterval, setSkillInterval] = useState(a.skillCheckInterval)
  const [skillDelta, setSkillDelta] = useState(a.skillPtsDelta)
  const [undertrain, setUndertrain] = useState(a.undertrainThreshold)
  const [topStats, setTopStats] = useState(a.topStatsFocus)
  const [externalUrl, setExternalUrl] = useState(a.externalProcessorUrl)

  // Career Loop state
  const [careerLoopEnabled, setCareerLoopEnabled] = useState(a.careerLoop?.enabled ?? true)
  const [maxCareers, setMaxCareers] = useState(a.careerLoop?.maxCareers ?? 5)
  const [preferredSupport, setPreferredSupport] = useState(a.careerLoop?.preferredSupport ?? 'Riko Kashimoto')
  const [preferredLevel, setPreferredLevel] = useState(a.careerLoop?.preferredLevel ?? 50)
  const [maxRefresh, setMaxRefresh] = useState(a.careerLoop?.maxRefresh ?? 5)
  const [refreshWait, setRefreshWait] = useState(a.careerLoop?.refreshWait ?? 5.0)
  const [errorThreshold, setErrorThreshold] = useState(a.careerLoop?.errorThreshold ?? 5)

  const autoRestMarks = useMemo(() => {
    const marks = [{ value: 1 }]
    for (let v = 5; v <= 70; v += 5) {
      marks.push({ value: v })
    }
    return marks
  }, [])

  const toNumber = (val: number | number[]) => (Array.isArray(val) ? val[0] : val)

  useEffect(() => {
    setAutoRest(a.autoRestMinimum)
    setSkillInterval(a.skillCheckInterval)
    setSkillDelta(a.skillPtsDelta)
    setUndertrain(a.undertrainThreshold)
    setTopStats(a.topStatsFocus)
    setExternalUrl(a.externalProcessorUrl)
    setCareerLoopEnabled(a.careerLoop?.enabled ?? true)
    setMaxCareers(a.careerLoop?.maxCareers ?? 5)
    setPreferredSupport(a.careerLoop?.preferredSupport ?? 'Riko Kashimoto')
    setPreferredLevel(a.careerLoop?.preferredLevel ?? 50)
    setMaxRefresh(a.careerLoop?.maxRefresh ?? 5)
    setRefreshWait(a.careerLoop?.refreshWait ?? 5.0)
    setErrorThreshold(a.careerLoop?.errorThreshold ?? 5)
  }, [
    a.autoRestMinimum,
    a.skillCheckInterval,
    a.skillPtsDelta,
    a.undertrainThreshold,
    a.topStatsFocus,
    a.externalProcessorUrl,
    a.careerLoop,
  ])

  const commitAdvanced = <K extends keyof typeof a>(key: K, value: (typeof a)[K]) => {
    setGeneral({
      advanced: {
        ...a,
        [key]: value,
      },
    })
  }
  const sliderSx = {
    width: '100%',
    mt: '-2px',
    mb: '-4px',
    py: 0.5,
    '.MuiSlider-thumb': {
      width: 12,
      height: 12,
      boxShadow: 'none',
    },
    '.MuiSlider-thumb::before': {
      boxShadow: 'none',
    },
    '.MuiSlider-rail': {
      height: 4,
      borderRadius: 2,
    },
    '.MuiSlider-track': {
      height: 4,
      borderRadius: 2,
    },
  } as const
  const renderSliderControl = ({
    id,
    value,
    min,
    max,
    step,
    marks,
    format,
    onChange,
    onCommit,
  }: {
    id: string
    value: number
    min: number
    max: number
    step: number
    marks?: typeof autoRestMarks
    format?: (v: number) => string
    onChange: (event: Event | SyntheticEvent<Element, Event>, value: number | number[]) => void
    onCommit: (event: Event | SyntheticEvent<Element, Event>, value: number | number[]) => void
  }) => {
    const percent = max === min ? 0 : ((value - min) / (max - min)) * 100
    const formatted = format ? format(value) : `${value}`
    return (
      <Box sx={controlWrapSx} data-advanced-slider={id}>
        <Box sx={{ position: 'relative', width: '100%' }}>
          <Typography
            component="span"
            sx={{
              position: 'absolute',
              top: -14,
              left: `${Math.min(100, Math.max(0, percent))}%`,
              transform: 'translateX(-50%)',
              fontWeight: 800,
              fontSize: 16,
              lineHeight: 1,
              color: '#1976d2',
            }}
            data-advanced-slider-label
          >
            {formatted}
          </Typography>
          <Slider
            value={value}
            onChange={onChange}
            onChangeCommitted={onCommit}
            min={min}
            max={max}
            step={step}
            marks={marks}
            sx={sliderSx}
          />
        </Box>
      </Box>
    )
  }
  const controlWrapSx = (theme: any) => ({
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 1,
    px: 1.5,
    pt: 3,
    pb: 1.75,
    border: `1px solid ${theme.palette.divider}`,
    borderRadius: 1.5,
    backgroundColor:
      theme.palette.mode === 'dark'
        ? theme.palette.background.default
        : theme.palette.grey[50],
    boxShadow: theme.palette.mode === 'dark'
      ? 'inset 0 0 0 1px rgba(255,255,255,0.05)'
      : '0 1px 2px rgba(15, 23, 42, 0.08)',
    maxWidth: 240,
    width: '100%',
  })

  useEffect(() => {
    if (typeof window === 'undefined') return
    const logLabelOffsets = () => {
      document.querySelectorAll<HTMLElement>('[data-advanced-slider]').forEach((container) => {
        const id = container.getAttribute('data-advanced-slider') ?? 'unknown'
        const thumb = container.querySelector<HTMLElement>('.MuiSlider-thumb')
        const label = container.querySelector<HTMLElement>('[data-advanced-slider-label]')
        const sliderRoot = container.querySelector<HTMLElement>('.MuiSlider-root')
        if (!thumb || !label) return
        const thumbRect = thumb.getBoundingClientRect()
        const labelRect = label.getBoundingClientRect()
        const topGap = Math.round(thumbRect.top - labelRect.bottom)
        const paddingTop = sliderRoot ? window.getComputedStyle(sliderRoot).paddingTop : null
        console.debug('[AdvancedSettings] slider spacing', {
          id,
          topGap,
          paddingTop,
        })
      })
    }
    logLabelOffsets()
  }, [])

  return (
    <Stack spacing={1.5}>
      <Typography
        variant="subtitle1"
        sx={{ cursor: 'pointer', userSelect: 'none' }}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? '▼' : '▶'} Advanced settings
      </Typography>

      <Collapse in={open}>
        <Divider sx={{ mb: 2 }} />

        <FieldRow
          label="Hotkey"
          control={
            <Select
              size="small"
              value={a.hotkey}
              onChange={(e) => setGeneral({ advanced: { ...a, hotkey: e.target.value as any } })}
            >
              {['F1', 'F2', 'F3', 'F4'].map((k) => (
                <MenuItem key={k} value={k}>{k}</MenuItem>
              ))}
            </Select>
          }
          info="Keyboard key to toggle the agent on/off."
        />

        <FieldRow
          label="Debug mode"
          control={
            <FormControlLabel
              control={
                <Switch
                  checked={a.debugMode}
                  onChange={(e) => setGeneral({ advanced: { ...a, debugMode: e.target.checked } })}
                />
              }
              label={a.debugMode ? 'Enabled' : 'Disabled'}
            />
          }
          info="Enable debug mode for verbose logging and extra overlays."
        />

        <FieldRow
          label="Use external processor"
          control={
            <FormControlLabel
              control={
                <Switch
                  checked={a.useExternalProcessor}
                  onChange={(e) =>
                    setGeneral({ advanced: { ...a, useExternalProcessor: e.target.checked } })
                  }
                />
              }
              label={a.useExternalProcessor ? 'Enabled' : 'Disabled'}
            />
          }
          info="Send OCR/YOLO processing to a remote server."
        />

        <FieldRow
          label="External processor URL"
          control={
            <TextField
              size="small"
              fullWidth
              value={externalUrl}
              onChange={(e) => setExternalUrl(e.target.value)}
              onBlur={() => {
                if (externalUrl !== a.externalProcessorUrl) {
                  setGeneral({ advanced: { ...a, externalProcessorUrl: externalUrl } })
                }
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  const next = externalUrl.trim()
                  if (next && next !== a.externalProcessorUrl) {
                    setGeneral({ advanced: { ...a, externalProcessorUrl: next } })
                  }
                }
              }}
              disabled={!a.useExternalProcessor}
              placeholder="http://127.0.0.1:8001"
            />
          }
          info="Base URL for the remote inference server."
        />

        <FieldRow
          label="Auto rest minimum"
          info="Below this energy% the bot will rest automatically."
          control={renderSliderControl({
            id: 'autoRestMinimum',
            value: autoRest,
            min: 1,
            max: 70,
            step: 1,
            marks: autoRestMarks,
            format: (v) => `${v}%`,
            onChange: (_, v) => {
              const raw = toNumber(v)
              const next = raw <= 1 ? 1 : Math.max(5, Math.min(70, Math.round(raw / 5) * 5))
              setAutoRest(next)
            },
            onCommit: (_, v) => {
              const raw = toNumber(v)
              const next = raw <= 1 ? 1 : Math.max(5, Math.min(70, Math.round(raw / 5) * 5))
              setAutoRest(next)
              commitAdvanced('autoRestMinimum', next)
            },
          })}
          sx={{ mt: 2 }}
        />

        <FieldRow
          label="Skills: check interval"
          info="How often to reopen the Skills shop during races — 1 checks every turn, 3 checks every third turn."
          control={renderSliderControl({
            id: 'skillCheckInterval',
            value: skillInterval,
            min: 1,
            max: 5,
            step: 1,
            onChange: (_, v) => {
              const next = Math.max(1, Math.min(5, Math.round(toNumber(v))))
              setSkillInterval(next)
            },
            onCommit: (_, v) => {
              const next = Math.max(1, Math.min(5, Math.round(toNumber(v))))
              setSkillInterval(next)
              commitAdvanced('skillCheckInterval', next)
            },
          })}
          sx={{ mt: 2 }}
        />

        <FieldRow
          label="Skills: points delta"
          info="Reopen the Skills shop only after earning at least this many points (e.g. 60 waits until you gain 60 more points)."
          control={renderSliderControl({
            id: 'skillPtsDelta',
            value: skillDelta,
            min: 60,
            max: 200,
            step: 10,
            onChange: (_, v) => {
              const raw = toNumber(v)
              const next = Math.max(60, Math.min(200, Math.round(raw / 10) * 10))
              setSkillDelta(next)
            },
            onCommit: (_, v) => {
              const raw = toNumber(v)
              const next = Math.max(60, Math.min(200, Math.round(raw / 10) * 10))
              setSkillDelta(next)
              commitAdvanced('skillPtsDelta', next)
            },
          })}
          sx={{ mt: 2 }}
        />

        <FieldRow
          label="Undertrain threshold"
          info="Lower values make detection stricter, higher values more forgiving. The system looks at how much each stat contributes to your total stats and compares it with how much it should contribute based on the ideal balance. Think of it as checking if any stat is falling behind or getting too far ahead. For example, if your total stats add up to 1640 and SPD is 375, that means SPD makes up about 23% of your total. But according to the ideal setup (your stat caps), SPD should represent let's say 32% — which would be around 525 points. That means SPD is about 9% lower than where it should be, so it’s undertrained. However, if your undertraining threshold is 10%, the bot won’t force SPD training yet because the difference isn’t big enough. On the other hand, PWR is let's say 512, which is 31% of the total, while its ideal ratio in this hypothetical scenario is let's say 25%, so it’s 7% higher than expected — meaning PWR is overtrained and should be paused until the others catch up."
          control={renderSliderControl({
            id: 'undertrainThreshold',
            value: undertrain,
            min: 1,
            max: 20,
            step: 1,
            format: (v) => `${v}%`,
            onChange: (_, v) => {
              const next = Math.max(1, Math.min(20, Math.round(toNumber(v))))
              setUndertrain(next)
            },
            onCommit: (_, v) => {
              const next = Math.max(1, Math.min(20, Math.round(toNumber(v))))
              setUndertrain(next)
              commitAdvanced('undertrainThreshold', next)
            },
          })}
          sx={{ mt: 2 }}
        />

        <FieldRow
          label="Top stats focus"
          info="Used to re-prioritize stats when training and also if bot finds a low SV value in priority stat compared with maximum SV value in another tile. If difference is not more than 0.75 we skip the 'best' option and get the second best option only if it is in these first top stats"
          control={renderSliderControl({
            id: 'topStatsFocus',
            value: topStats,
            min: 1,
            max: 5,
            step: 1,
            onChange: (_, v) => {
              const next = Math.max(1, Math.min(5, Math.round(toNumber(v))))
              setTopStats(next)
            },
            onCommit: (_, v) => {
              const next = Math.max(1, Math.min(5, Math.round(toNumber(v))))
              setTopStats(next)
              commitAdvanced('topStatsFocus', next)
            },
          })}
          sx={{ mt: 2 }}
        />

        <Divider sx={{ my: 3 }} />
        <Typography variant="h6" sx={{ mb: 2 }}>Career Loop Automation</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Automated career farming for unattended gameplay. Press F1 to start/stop career loop mode.
        </Typography>

        <FieldRow
          label="Enable Career Loop"
          control={
            <FormControlLabel
              control={
                <Switch
                  checked={careerLoopEnabled}
                  onChange={(e) => {
                    const enabled = e.target.checked
                    setCareerLoopEnabled(enabled)
                    setGeneral({
                      advanced: {
                        ...a,
                        careerLoop: {
                          ...(a.careerLoop || {}),
                          enabled,
                          maxCareers: maxCareers || null,
                          preferredSupport,
                          preferredLevel,
                          maxRefresh,
                          refreshWait,
                          errorThreshold,
                        },
                      },
                    })
                  }}
                />
              }
              label={careerLoopEnabled ? 'Enabled' : 'Disabled'}
            />
          }
          info="Enable automated career loop for farming multiple careers."
        />

        <FieldRow
          label="Max Careers"
          control={
            <TextField
              size="small"
              type="number"
              value={maxCareers || ''}
              onChange={(e) => {
                const val = e.target.value === '' ? 0 : parseInt(e.target.value, 10)
                setMaxCareers(val)
              }}
              onBlur={() => {
                setGeneral({
                  advanced: {
                    ...a,
                    careerLoop: {
                      ...(a.careerLoop || {}),
                      enabled: careerLoopEnabled,
                      maxCareers: maxCareers || 0,
                      preferredSupport,
                      preferredLevel,
                      maxRefresh,
                      refreshWait,
                      errorThreshold,
                    },
                  },
                })
              }}
              inputProps={{ min: 0, max: 999 }}
              placeholder="0 = infinite"
              sx={{ width: 120 }}
            />
          }
          info="Number of careers to complete (0 or empty = infinite loop)."
        />

        <FieldRow
          label="Preferred Support"
          control={
            <TextField
              size="small"
              fullWidth
              value={preferredSupport}
              onChange={(e) => setPreferredSupport(e.target.value)}
              onBlur={() => {
                setGeneral({
                  advanced: {
                    ...a,
                    careerLoop: {
                      ...(a.careerLoop || {}),
                      enabled: careerLoopEnabled,
                      maxCareers: maxCareers || null,
                      preferredSupport,
                      preferredLevel,
                      maxRefresh,
                      refreshWait,
                      errorThreshold,
                    },
                  },
                })
              }}
              placeholder="Riko Kashimoto"
            />
          }
          info="Name of the preferred support card to select."
        />

        <FieldRow
          label="Preferred Level"
          control={renderSliderControl({
            id: 'preferredLevel',
            value: preferredLevel,
            min: 1,
            max: 100,
            step: 1,
            onChange: (_, v) => {
              const next = Math.max(1, Math.min(100, Math.round(toNumber(v))))
              setPreferredLevel(next)
            },
            onCommit: (_, v) => {
              const next = Math.max(1, Math.min(100, Math.round(toNumber(v))))
              setPreferredLevel(next)
              setGeneral({
                advanced: {
                  ...a,
                  careerLoop: {
                    ...(a.careerLoop || {}),
                    enabled: careerLoopEnabled,
                    maxCareers: maxCareers || null,
                    preferredSupport,
                    preferredLevel: next,
                    maxRefresh,
                    refreshWait,
                    errorThreshold,
                  },
                },
              })
            },
          })}
          info="Preferred support card level (1-100)."
          sx={{ mt: 2 }}
        />

        <FieldRow
          label="Max Support Refresh"
          control={renderSliderControl({
            id: 'maxRefresh',
            value: maxRefresh,
            min: 0,
            max: 20,
            step: 1,
            onChange: (_, v) => {
              const next = Math.max(0, Math.min(20, Math.round(toNumber(v))))
              setMaxRefresh(next)
            },
            onCommit: (_, v) => {
              const next = Math.max(0, Math.min(20, Math.round(toNumber(v))))
              setMaxRefresh(next)
              setGeneral({
                advanced: {
                  ...a,
                  careerLoop: {
                    ...(a.careerLoop || {}),
                    enabled: careerLoopEnabled,
                    maxCareers: maxCareers || null,
                    preferredSupport,
                    preferredLevel,
                    maxRefresh: next,
                    refreshWait,
                    errorThreshold,
                  },
                },
              })
            },
          })}
          info="Maximum times to refresh the support list (0-20)."
          sx={{ mt: 2 }}
        />

        <FieldRow
          label="Refresh Wait (seconds)"
          control={
            <TextField
              size="small"
              type="number"
              value={refreshWait}
              onChange={(e) => {
                const val = parseFloat(e.target.value)
                if (!isNaN(val)) setRefreshWait(val)
              }}
              onBlur={() => {
                const clamped = Math.max(1.0, Math.min(30.0, refreshWait))
                setRefreshWait(clamped)
                setGeneral({
                  advanced: {
                    ...a,
                    careerLoop: {
                      ...(a.careerLoop || {}),
                      enabled: careerLoopEnabled,
                      maxCareers: maxCareers || null,
                      preferredSupport,
                      preferredLevel,
                      maxRefresh,
                      refreshWait: clamped,
                      errorThreshold,
                    },
                  },
                })
              }}
              inputProps={{ min: 1.0, max: 30.0, step: 0.5 }}
              sx={{ width: 120 }}
            />
          }
          info="Wait time between support list refreshes (1.0-30.0 seconds)."
        />

        <FieldRow
          label="Error Threshold"
          control={renderSliderControl({
            id: 'errorThreshold',
            value: errorThreshold,
            min: 1,
            max: 20,
            step: 1,
            onChange: (_, v) => {
              const next = Math.max(1, Math.min(20, Math.round(toNumber(v))))
              setErrorThreshold(next)
            },
            onCommit: (_, v) => {
              const next = Math.max(1, Math.min(20, Math.round(toNumber(v))))
              setErrorThreshold(next)
              setGeneral({
                advanced: {
                  ...a,
                  careerLoop: {
                    ...(a.careerLoop || {}),
                    enabled: careerLoopEnabled,
                    maxCareers: maxCareers || null,
                    preferredSupport,
                    preferredLevel,
                    maxRefresh,
                    refreshWait,
                    errorThreshold: next,
                  },
                },
              })
            },
          })}
          info="Max consecutive errors before stopping the loop (1-20)."
          sx={{ mt: 2 }}
        />
      </Collapse>
    </Stack>
  )
}
