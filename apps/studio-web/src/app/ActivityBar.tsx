import { useState } from 'react'
import type { Screen } from './types'
import { C } from '../ui/UIKit'
import { useI18n } from './i18n'

// ─── Nav items ────────────────────────────────────────────────────────────────

const ITEMS: { id: Screen; label: string; icon: string }[] = [
  { id: 'home',      label: 'Home',              icon: '⌂' },
  { id: 'workspace', label: 'Workspace',         icon: '⊟' },
  { id: 'registry',  label: 'Registry',          icon: '◎' },
  { id: 'federate',  label: 'Federated Learning', icon: '⬡' },
  { id: 'agent',     label: 'Agent Builder',      icon: '◈' },
  { id: 'agents',    label: 'Agents',             icon: '▷' },
]

const BOTTOM_ITEMS: { id: Screen; label: string; icon: string }[] = [
  { id: 'settings', label: 'Settings', icon: '⚙' },
]

interface Props {
  screen: Screen
  setScreen: (s: Screen) => void
}

export default function ActivityBar({ screen, setScreen }: Props) {
  const { t } = useI18n()
  const [collapsed, setCollapsed] = useState(false)
  const W = collapsed ? 48 : 182

  return (
    <div style={{
      width: W, background: C.chrome, borderRight: `1px solid ${C.border}`,
      display: 'flex', flexDirection: 'column', flexShrink: 0, overflow: 'hidden',
      transition: 'width 0.18s ease', userSelect: 'none',
    }}>
      {/* Collapse / expand button */}
      <div style={{ display: 'flex', justifyContent: collapsed ? 'center' : 'flex-end', padding: '6px 6px 2px' }}>
        <button
          onClick={() => setCollapsed(c => !c)}
          title={t(collapsed ? 'Expand sidebar' : 'Collapse sidebar')}
          style={{
            background: 'none', borderTop: 'none', borderRight: 'none', borderBottom: 'none', borderLeft: 'none',
            color: C.dim, cursor: 'pointer', fontSize: 14, padding: '2px 4px', lineHeight: 1,
            borderRadius: 2,
          }}
          onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.color = C.muted }}
          onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.color = C.dim }}
        >
          {collapsed ? '›' : '‹'}
        </button>
      </div>

      {/* Main nav items */}
      <div style={{ flex: 1, paddingTop: 4 }}>
        {ITEMS.map(item => (
          <NavItem key={item.id} item={{ ...item, label: t(item.label) }} active={screen === item.id} collapsed={collapsed} onClick={() => setScreen(item.id)} />
        ))}
      </div>

      {/* Bottom items */}
      <div style={{ paddingBottom: 8 }}>
        {BOTTOM_ITEMS.map(item => (
          <NavItem key={item.id} item={{ ...item, label: t(item.label) }} active={screen === item.id} collapsed={collapsed} onClick={() => setScreen(item.id)} />
        ))}
      </div>
    </div>
  )
}

function NavItem({ item, active, collapsed, onClick }: {
  item: { id: Screen; label: string; icon: string }; active: boolean; collapsed: boolean; onClick: () => void
}) {
  const [hovered, setHovered] = useState(false)
  const showTooltip = collapsed && hovered

  return (
    <div style={{ position: 'relative' }}>
      <button
        onClick={onClick}
        title={item.label}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        style={{
          display: 'flex', alignItems: 'center', gap: collapsed ? 0 : 9,
          width: '100%', padding: collapsed ? '8px 0' : '7px 12px',
          justifyContent: collapsed ? 'center' : 'flex-start',
          background: active ? C.surface2 : hovered ? C.surface : 'transparent',
          borderTop: 'none', borderRight: 'none', borderBottom: 'none',
          borderLeft: active ? `2px solid ${C.accent}` : '2px solid transparent',
          cursor: 'pointer', transition: 'background 0.1s',
        }}
      >
        <span style={{ fontSize: 15, color: active ? C.accent : hovered ? C.muted : C.dim, transition: 'color 0.1s', flexShrink: 0 }}>
          {item.icon}
        </span>
        {!collapsed && (
          <span style={{ fontFamily: 'Inter, sans-serif', fontSize: 13, color: active ? C.text : C.muted, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {item.label}
          </span>
        )}
      </button>

      {/* Tooltip when collapsed */}
      {showTooltip && (
        <div style={{
          position: 'absolute', left: 46, top: '50%', transform: 'translateY(-50%)',
          background: C.surface3, border: `1px solid ${C.border}`, borderRadius: 4,
          padding: '4px 10px', whiteSpace: 'nowrap', zIndex: 1000, pointerEvents: 'none',
          fontFamily: 'Inter, sans-serif', fontSize: 13, color: C.text,
          boxShadow: C.shadow,
        }}>
          {item.label}
        </div>
      )}
    </div>
  )
}
