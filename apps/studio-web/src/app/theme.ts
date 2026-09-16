import type { AppTheme } from './types'

export const THEME_CHANGE_EVENT = 'fedops-studio-theme-change'
export const CUSTOM_THEME_SCHEMA_VERSION = 1

const UI_COLOR_KEYS = [
  'background',
  'chrome',
  'surface',
  'surfaceAlt',
  'surfaceRaised',
  'border',
  'borderSubtle',
  'control',
  'input',
  'text',
  'muted',
  'dim',
  'accent',
  'success',
  'warning',
  'error',
  'purple',
  'orange',
  'brand',
  'primaryText',
  'selection',
  'focus',
  'scrollbar',
  'scrollbarHover',
  'shadow',
  'overlay',
] as const

const TERMINAL_COLOR_KEYS = [
  'background',
  'surface',
  'input',
  'border',
  'foreground',
  'muted',
  'dim',
  'accent',
  'prompt',
  'cursor',
  'selection',
] as const

const ANSI_COLOR_KEYS = [
  'black',
  'red',
  'green',
  'yellow',
  'blue',
  'magenta',
  'cyan',
  'white',
  'brightBlack',
  'brightRed',
  'brightGreen',
  'brightYellow',
  'brightBlue',
  'brightMagenta',
  'brightCyan',
  'brightWhite',
] as const

const EDITOR_COLOR_KEYS = [
  'background',
  'foreground',
  'lineHighlight',
  'selection',
  'selectionHighlight',
  'comment',
  'red',
  'orange',
  'yellow',
  'green',
  'cyan',
  'purple',
  'pink',
] as const

type UiColorName = (typeof UI_COLOR_KEYS)[number]
type TerminalColorName = (typeof TERMINAL_COLOR_KEYS)[number]
type AnsiColorName = (typeof ANSI_COLOR_KEYS)[number]
type EditorColorName = (typeof EDITOR_COLOR_KEYS)[number]

export interface CustomThemeV1 {
  schemaVersion: 1
  id: string
  name: string
  appearance: 'dark' | 'light'
  ui: Record<UiColorName, string>
  terminal: Record<TerminalColorName, string> & { ansi: Record<AnsiColorName, string> }
  editor: Record<EditorColorName, string>
}

export interface AppliedTheme {
  selection: Exclude<AppTheme, 'system'>
  appearance: 'dark' | 'light'
  customTheme: CustomThemeV1 | null
  revision: number
}

let appliedTheme: AppliedTheme = {
  selection: 'dark',
  appearance: 'dark',
  customTheme: null,
  revision: 0,
}

const CUSTOM_CSS_VARIABLES = [
  '--studio-bg',
  '--studio-chrome',
  '--studio-surface',
  '--studio-surface-2',
  '--studio-surface-3',
  '--studio-border',
  '--studio-border-subtle',
  '--studio-control-border',
  '--studio-control-bg',
  '--studio-control-text',
  '--studio-input-bg',
  '--studio-text',
  '--studio-muted',
  '--studio-dim',
  '--studio-accent',
  '--studio-accent-dim',
  '--studio-accent-border',
  '--studio-green',
  '--studio-green-dim',
  '--studio-green-border',
  '--studio-yellow',
  '--studio-yellow-dim',
  '--studio-yellow-border',
  '--studio-red',
  '--studio-red-dim',
  '--studio-red-border',
  '--studio-purple',
  '--studio-purple-dim',
  '--studio-purple-border',
  '--studio-brand',
  '--studio-orange',
  '--studio-orange-dim',
  '--studio-orange-border',
  '--studio-primary-bg',
  '--studio-primary-text',
  '--studio-primary-border',
  '--studio-shadow',
  '--studio-overlay',
  '--studio-scrollbar',
  '--studio-scrollbar-hover',
  '--studio-selection-bg',
  '--studio-selection-text',
  '--studio-focus',
  '--terminal-bg',
  '--terminal-surface',
  '--terminal-input-bg',
  '--terminal-border',
  '--terminal-text',
  '--terminal-muted',
  '--terminal-dim',
  '--terminal-accent',
  '--terminal-prompt',
  '--terminal-caret',
  '--terminal-selection',
  ...ANSI_COLOR_KEYS.map(name => `--terminal-ansi-${camelToKebab(name)}`),
]

export function customThemeSelection(id: string): AppTheme {
  return `custom:${id}`
}

export function customThemeId(selection: AppTheme): string | null {
  return selection.startsWith('custom:') ? selection.slice('custom:'.length) : null
}

export function parseCustomThemeJson(source: string): CustomThemeV1 {
  if (new Blob([source]).size > 64 * 1024) {
    throw new Error('Theme JSON must be 64 KB or smaller.')
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(source)
  } catch {
    throw new Error('Theme file is not valid JSON.')
  }
  return normalizeCustomTheme(parsed)
}

export function normalizeCustomTheme(value: unknown): CustomThemeV1 {
  const root = objectValue(value, 'theme')
  if (root.schemaVersion !== CUSTOM_THEME_SCHEMA_VERSION) {
    throw new Error(`schemaVersion must be ${CUSTOM_THEME_SCHEMA_VERSION}.`)
  }

  const id = stringValue(root.id, 'id').trim()
  if (!/^[a-z0-9][a-z0-9-]{1,47}$/.test(id)) {
    throw new Error('id must be 2–48 lowercase letters, numbers, or hyphens.')
  }
  const name = stringValue(root.name, 'name').trim()
  if (!name || name.length > 60) throw new Error('name must be 1–60 characters.')
  if (root.appearance !== 'dark' && root.appearance !== 'light') {
    throw new Error('appearance must be "dark" or "light".')
  }

  const terminal = objectValue(root.terminal, 'terminal')
  return {
    schemaVersion: 1,
    id,
    name,
    appearance: root.appearance,
    ui: colorRecord(root.ui, UI_COLOR_KEYS, 'ui'),
    terminal: {
      ...colorRecord(terminal, TERMINAL_COLOR_KEYS, 'terminal'),
      ansi: colorRecord(terminal.ansi, ANSI_COLOR_KEYS, 'terminal.ansi'),
    },
    editor: colorRecord(root.editor, EDITOR_COLOR_KEYS, 'editor'),
  }
}

export function loadStoredCustomThemes(source: string | null): CustomThemeV1[] {
  if (!source) return []
  try {
    const values: unknown = JSON.parse(source)
    if (!Array.isArray(values)) return []
    const themes = new Map<string, CustomThemeV1>()
    for (const value of values) {
      try {
        const theme = normalizeCustomTheme(value)
        themes.set(theme.id, theme)
      } catch {
        // Ignore a damaged entry without discarding the account's other themes.
      }
    }
    return [...themes.values()]
  } catch {
    return []
  }
}

export function applyThemeSelection(
  selection: Exclude<AppTheme, 'system'>,
  customThemes: CustomThemeV1[],
): AppliedTheme {
  const root = document.documentElement
  const requestedId = customThemeId(selection)
  const customTheme = requestedId ? customThemes.find(theme => theme.id === requestedId) ?? null : null
  const safeSelection: Exclude<AppTheme, 'system'> = customTheme
    ? customThemeSelection(customTheme.id) as `custom:${string}`
    : selection === 'dark'
      ? 'dark'
      : 'light'
  const appearance: 'dark' | 'light' = customTheme?.appearance ?? (safeSelection === 'dark' ? 'dark' : 'light')

  for (const variable of CUSTOM_CSS_VARIABLES) root.style.removeProperty(variable)
  root.classList.toggle('theme-light', appearance === 'light')
  root.dataset.studioTheme = safeSelection
  root.style.colorScheme = appearance

  if (customTheme) applyCustomCssVariables(root.style, customTheme)

  appliedTheme = {
    selection: safeSelection,
    appearance,
    customTheme,
    revision: appliedTheme.revision + 1,
  }
  window.dispatchEvent(new CustomEvent<AppliedTheme>(THEME_CHANGE_EVENT, { detail: appliedTheme }))
  return appliedTheme
}

export function getAppliedTheme(): AppliedTheme {
  return appliedTheme
}

export function createCustomThemeBaseline(): CustomThemeV1 {
  const styles = getComputedStyle(document.documentElement)
  const color = (variable: string) => cssColorToHex(styles.getPropertyValue(variable), variable)

  return normalizeCustomTheme({
    schemaVersion: 1,
    id: 'my-custom-theme',
    name: 'My Custom Theme',
    appearance: appliedTheme.appearance,
    ui: {
      background: color('--studio-bg'),
      chrome: color('--studio-chrome'),
      surface: color('--studio-surface'),
      surfaceAlt: color('--studio-surface-2'),
      surfaceRaised: color('--studio-surface-3'),
      border: color('--studio-border'),
      borderSubtle: color('--studio-border-subtle'),
      control: color('--studio-control-bg'),
      input: color('--studio-input-bg'),
      text: color('--studio-text'),
      muted: color('--studio-muted'),
      dim: color('--studio-dim'),
      accent: color('--studio-accent'),
      success: color('--studio-green'),
      warning: color('--studio-yellow'),
      error: color('--studio-red'),
      purple: color('--studio-purple'),
      orange: color('--studio-orange'),
      brand: color('--studio-brand'),
      primaryText: color('--studio-primary-text'),
      selection: color('--studio-selection-bg'),
      focus: color('--studio-focus'),
      scrollbar: color('--studio-scrollbar'),
      scrollbarHover: color('--studio-scrollbar-hover'),
      shadow: color('--studio-overlay'),
      overlay: color('--studio-overlay'),
    },
    terminal: {
      background: color('--terminal-bg'),
      surface: color('--terminal-surface'),
      input: color('--terminal-input-bg'),
      border: color('--terminal-border'),
      foreground: color('--terminal-text'),
      muted: color('--terminal-muted'),
      dim: color('--terminal-dim'),
      accent: color('--terminal-accent'),
      prompt: color('--terminal-prompt'),
      cursor: color('--terminal-caret'),
      selection: color('--terminal-selection'),
      ansi: Object.fromEntries(
        ANSI_COLOR_KEYS.map(key => [key, color(`--terminal-ansi-${camelToKebab(key)}`)]),
      ),
    },
    editor: {
      background: color('--studio-bg'),
      foreground: color('--studio-text'),
      lineHighlight: color('--studio-surface-2'),
      selection: color('--studio-selection-bg'),
      selectionHighlight: color('--studio-selection-bg'),
      comment: color('--studio-muted'),
      red: color('--studio-red'),
      orange: color('--studio-orange'),
      yellow: color('--studio-yellow'),
      green: color('--studio-green'),
      cyan: color('--studio-accent'),
      purple: color('--studio-purple'),
      pink: color('--studio-brand'),
    },
  })
}

function applyCustomCssVariables(style: CSSStyleDeclaration, theme: CustomThemeV1) {
  const { ui, terminal } = theme
  const set = (name: string, value: string) => style.setProperty(name, value)
  const tint = (color: string, strength: number) =>
    `color-mix(in srgb, ${color} ${strength}%, ${ui.background})`
  const border = (color: string) => `color-mix(in srgb, ${color} 38%, transparent)`

  set('--studio-bg', ui.background)
  set('--studio-chrome', ui.chrome)
  set('--studio-surface', ui.surface)
  set('--studio-surface-2', ui.surfaceAlt)
  set('--studio-surface-3', ui.surfaceRaised)
  set('--studio-border', ui.border)
  set('--studio-border-subtle', ui.borderSubtle)
  set('--studio-control-border', ui.border)
  set('--studio-control-bg', ui.control)
  set('--studio-control-text', ui.text)
  set('--studio-input-bg', ui.input)
  set('--studio-text', ui.text)
  set('--studio-muted', ui.muted)
  set('--studio-dim', ui.dim)
  set('--studio-accent', ui.accent)
  set('--studio-accent-dim', tint(ui.accent, 14))
  set('--studio-accent-border', border(ui.accent))
  set('--studio-green', ui.success)
  set('--studio-green-dim', tint(ui.success, 14))
  set('--studio-green-border', border(ui.success))
  set('--studio-yellow', ui.warning)
  set('--studio-yellow-dim', tint(ui.warning, 14))
  set('--studio-yellow-border', border(ui.warning))
  set('--studio-red', ui.error)
  set('--studio-red-dim', tint(ui.error, 14))
  set('--studio-red-border', border(ui.error))
  set('--studio-purple', ui.purple)
  set('--studio-purple-dim', tint(ui.purple, 14))
  set('--studio-purple-border', border(ui.purple))
  set('--studio-brand', ui.brand)
  set('--studio-orange', ui.orange)
  set('--studio-orange-dim', tint(ui.orange, 14))
  set('--studio-orange-border', border(ui.orange))
  set('--studio-primary-bg', ui.accent)
  set('--studio-primary-text', ui.primaryText)
  set('--studio-primary-border', ui.accent)
  set('--studio-shadow', `0 10px 36px ${ui.shadow}`)
  set('--studio-overlay', ui.overlay)
  set('--studio-scrollbar', ui.scrollbar)
  set('--studio-scrollbar-hover', ui.scrollbarHover)
  set('--studio-selection-bg', ui.selection)
  set('--studio-selection-text', ui.text)
  set('--studio-focus', ui.focus)

  set('--terminal-bg', terminal.background)
  set('--terminal-surface', terminal.surface)
  set('--terminal-input-bg', terminal.input)
  set('--terminal-border', terminal.border)
  set('--terminal-text', terminal.foreground)
  set('--terminal-muted', terminal.muted)
  set('--terminal-dim', terminal.dim)
  set('--terminal-accent', terminal.accent)
  set('--terminal-prompt', terminal.prompt)
  set('--terminal-caret', terminal.cursor)
  set('--terminal-selection', terminal.selection)
  for (const key of ANSI_COLOR_KEYS) {
    set(`--terminal-ansi-${camelToKebab(key)}`, terminal.ansi[key])
  }
}

function objectValue(value: unknown, field: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${field} must be an object.`)
  }
  return value as Record<string, unknown>
}

function stringValue(value: unknown, field: string): string {
  if (typeof value !== 'string') throw new Error(`${field} must be a string.`)
  return value
}

function colorRecord<Key extends string>(
  value: unknown,
  keys: readonly Key[],
  field: string,
): Record<Key, string> {
  const source = objectValue(value, field)
  const result = {} as Record<Key, string>
  for (const key of keys) {
    const color = stringValue(source[key], `${field}.${key}`).toUpperCase()
    if (!/^#[0-9A-F]{6}([0-9A-F]{2})?$/.test(color)) {
      throw new Error(`${field}.${key} must be a #RRGGBB or #RRGGBBAA color.`)
    }
    result[key] = color
  }
  return result
}

function cssColorToHex(value: string, variable: string): string {
  const source = value.trim()
  const hexMatch = source.match(/^#([0-9A-F]{3,4}|[0-9A-F]{6}|[0-9A-F]{8})$/i)
  if (hexMatch) {
    const digits = hexMatch[1].length <= 4
      ? [...hexMatch[1]].map(character => character.repeat(2)).join('')
      : hexMatch[1]
    return `#${digits.toUpperCase()}`
  }
  if (source.toLowerCase() === 'transparent') return '#00000000'

  const functional = source.match(/^rgba?\((.*)\)$/i)
  if (!functional) throw new Error(`${variable} could not be converted to a theme color.`)
  const tokens = functional[1]
    .replace(/,/g, ' ')
    .replace('/', ' / ')
    .trim()
    .split(/\s+/)
  const slash = tokens.indexOf('/')
  const rgbTokens = slash >= 0 ? tokens.slice(0, slash) : tokens.slice(0, 3)
  const alphaToken = slash >= 0 ? tokens[slash + 1] : tokens[3]
  if (rgbTokens.length !== 3) throw new Error(`${variable} could not be converted to a theme color.`)

  const rgb = rgbTokens.map(component => parseCssChannel(component))
  const alpha = alphaToken === undefined ? 1 : parseCssAlpha(alphaToken)
  if (rgb.some(component => !Number.isFinite(component)) || !Number.isFinite(alpha)) {
    throw new Error(`${variable} could not be converted to a theme color.`)
  }
  const hex = rgb.map(component => byteHex(component)).join('')
  return alpha >= 1 ? `#${hex}` : `#${hex}${byteHex(alpha * 255)}`
}

function parseCssChannel(value: string): number {
  const numeric = Number.parseFloat(value)
  return value.endsWith('%') ? numeric * 2.55 : numeric
}

function parseCssAlpha(value: string): number {
  const numeric = Number.parseFloat(value)
  return Math.min(1, Math.max(0, value.endsWith('%') ? numeric / 100 : numeric))
}

function byteHex(value: number): string {
  return Math.min(255, Math.max(0, Math.round(value))).toString(16).padStart(2, '0').toUpperCase()
}

function camelToKebab(value: string): string {
  return value.replace(/[A-Z]/g, letter => `-${letter.toLowerCase()}`)
}
