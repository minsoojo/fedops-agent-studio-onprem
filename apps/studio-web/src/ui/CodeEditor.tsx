import Editor, { loader } from '@monaco-editor/react'
import { useEffect, useState } from 'react'
import * as monaco from 'monaco-editor'
import editorWorker from 'monaco-editor/editor/editor.worker?worker'
import cssWorker from 'monaco-editor/language/css/css.worker?worker'
import htmlWorker from 'monaco-editor/language/html/html.worker?worker'
import jsonWorker from 'monaco-editor/language/json/json.worker?worker'
import tsWorker from 'monaco-editor/language/typescript/ts.worker?worker'

import { getAppliedTheme, THEME_CHANGE_EVENT } from '../app/theme'
import { useI18n } from '../app/i18n'
import { C } from './UIKit'

type MonacoWorkerEnvironment = {
  getWorker: (_moduleId: string, label: string) => Worker
}

const workerScope = self as typeof self & { MonacoEnvironment?: MonacoWorkerEnvironment }
workerScope.MonacoEnvironment = {
  getWorker(_moduleId, label) {
    if (label === 'json') return new jsonWorker()
    if (label === 'css' || label === 'scss' || label === 'less') return new cssWorker()
    if (label === 'html' || label === 'handlebars' || label === 'razor') return new htmlWorker()
    if (label === 'typescript' || label === 'javascript') return new tsWorker()
    return new editorWorker()
  },
}
loader.config({ monaco })

export function CodeEditor({
  path,
  language,
  value,
  fontSize,
  readOnly = false,
  onChange,
}: {
  path: string
  language: string
  value: string
  fontSize: number
  readOnly?: boolean
  onChange: (value: string) => void
}) {
  const { t } = useI18n()
  const [theme, setTheme] = useState(editorTheme)

  useEffect(() => {
    const updateTheme = () => setTheme(editorTheme())
    window.addEventListener(THEME_CHANGE_EVENT, updateTheme)
    return () => window.removeEventListener(THEME_CHANGE_EVENT, updateTheme)
  }, [])

  return (
    <Editor
      path={`file:///${path}`}
      language={monacoLanguage(language)}
      value={value}
      theme={theme}
      onChange={next => onChange(next ?? '')}
      loading={<div style={{ color: C.dim, padding: 16 }}>{t('Loading the code editor…')}</div>}
      options={{
        automaticLayout: true,
        bracketPairColorization: { enabled: true },
        cursorBlinking: 'smooth',
        detectIndentation: true,
        folding: true,
        fontFamily: 'JetBrains Mono, monospace',
        fontLigatures: true,
        fontSize,
        formatOnPaste: true,
        formatOnType: true,
        glyphMargin: true,
        lineHeight: Math.round(fontSize * 1.6),
        lineNumbers: 'on',
        minimap: { enabled: true, maxColumn: 80, showSlider: 'mouseover' },
        padding: { top: 10, bottom: 10 },
        readOnly,
        readOnlyMessage: { value: t('This is FedOps-managed implementation. You can inspect and copy it, but only Owner-editable files can be changed.') },
        renderWhitespace: 'selection',
        scrollBeyondLastLine: false,
        smoothScrolling: true,
        stickyScroll: { enabled: true },
        tabSize: language === 'json' ? 2 : 4,
        trimAutoWhitespace: true,
        wordWrap: 'off',
      }}
    />
  )
}

function editorTheme(): string {
  const applied = getAppliedTheme()
  const custom = applied.customTheme
  if (!custom) return applied.appearance === 'light' ? 'vs' : 'vs-dark'

  const name = `fedops-custom-${custom.id}-${applied.revision}`
  const color = (value: string) => value.slice(1)
  monaco.editor.defineTheme(name, {
    base: custom.appearance === 'light' ? 'vs' : 'vs-dark',
    inherit: true,
    colors: {
      'editor.background': custom.editor.background,
      'editor.foreground': custom.editor.foreground,
      'editor.lineHighlightBackground': custom.editor.lineHighlight,
      'editor.selectionBackground': custom.editor.selection,
      'editor.inactiveSelectionBackground': custom.editor.selectionHighlight,
      'editor.selectionHighlightBackground': custom.editor.selectionHighlight,
      'editorCursor.foreground': custom.editor.pink,
      'editorLineNumber.foreground': custom.editor.comment,
      'editorLineNumber.activeForeground': custom.editor.foreground,
      'editorWhitespace.foreground': custom.editor.lineHighlight,
      'editorIndentGuide.background1': custom.editor.lineHighlight,
      'editorIndentGuide.activeBackground1': custom.editor.comment,
    },
    rules: [
      { token: 'comment', foreground: color(custom.editor.comment), fontStyle: 'italic' },
      { token: 'keyword', foreground: color(custom.editor.pink) },
      { token: 'storage', foreground: color(custom.editor.pink) },
      { token: 'tag', foreground: color(custom.editor.pink) },
      { token: 'string', foreground: color(custom.editor.yellow) },
      { token: 'number', foreground: color(custom.editor.orange) },
      { token: 'constant', foreground: color(custom.editor.orange) },
      { token: 'type', foreground: color(custom.editor.cyan) },
      { token: 'class', foreground: color(custom.editor.cyan) },
      { token: 'function', foreground: color(custom.editor.green) },
      { token: 'variable', foreground: color(custom.editor.purple) },
      { token: 'parameter', foreground: color(custom.editor.orange), fontStyle: 'italic' },
      { token: 'invalid', foreground: color(custom.editor.red) },
    ],
  })
  return name
}

function monacoLanguage(language: string): string {
  return ({ shell: 'shell', text: 'plaintext' } as Record<string, string>)[language] ?? language
}
