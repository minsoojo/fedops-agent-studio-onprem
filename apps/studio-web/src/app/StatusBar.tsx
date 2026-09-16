import { useStudio } from './StudioProvider'
import type { ActiveTask } from './types'
import type { HardwareGpuDevice } from '../api/system'
import { C } from '../ui/UIKit'
import { useI18n } from './i18n'

function Item({ label, value, color, title, grow = false, maxWidth }: {
  label: string
  value: string
  color: string
  title?: string
  grow?: boolean
  maxWidth?: number
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 5, minWidth: grow ? 120 : 0, maxWidth, flex: grow ? '1 1 260px' : '0 1 auto', paddingLeft: 9, paddingRight: 9, borderRight: `1px solid ${C.borderSubtle}`, height: '100%', overflow: 'hidden' }}>
      <span style={{ flexShrink: 0, fontFamily: 'JetBrains Mono, monospace', fontSize: 11, color: C.dim }}>{label}:</span>
      <span title={title ?? value} style={{ minWidth: 0, overflow: 'hidden', fontFamily: 'JetBrains Mono, monospace', fontSize: 11, color, fontWeight: 500, textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{value}</span>
    </div>
  )
}

interface Props { activeTask: ActiveTask | null }

export default function StatusBar({ activeTask }: Props) {
  const studio = useStudio()
  const { t } = useI18n()
  const hardware = studio.bootstrap?.hardware
  const workspace = studio.bootstrap?.workspace.displayRoot ?? t('Workspace path unavailable')
  const identity = studio.bootstrap?.session.handle
    ? `@${studio.bootstrap.session.handle}`
    : studio.bootstrap?.session.displayName ?? studio.bootstrap?.session.username ?? t('FedOps account')
  const cpu = hardware
    ? `${hardware.cpu.model} · ${hardware.cpu.logicalCores}c`
    : t('Detecting…')
  const gpu = hardware ? t(gpuLabel(hardware.gpu.devices)) : t('Detecting…')
  const system = hardware
    ? `${t(hardware.source === 'host' ? 'Host' : 'Runtime')} · ${hardware.platform.system} ${hardware.platform.architecture}`
    : t('Detecting…')
  return (
    <div style={{
      height: 26, background: C.chrome, borderTop: `1px solid ${C.border}`,
      display: 'flex', alignItems: 'center', paddingLeft: 0, paddingRight: 8,
      flexShrink: 0, overflow: 'hidden',
    }}>
      {activeTask && (
        <>
          <Item label={t('Task')} value={activeTask.title} color={C.purple} maxWidth={190} />
          <Item label={t('Role')} value={t(activeTask.role ?? 'viewer')} color={activeTask.role === 'owner' ? C.yellow : C.accent} maxWidth={100} />
        </>
      )}
      <Item label={t('Location')} value={workspace} title={workspace} color={C.text} grow />
      <Item label={t('System')} value={system} title={hardware ? `${hardware.platform.system} ${hardware.platform.release} · ${hardware.platform.architecture} · source=${hardware.source}` : system} color={C.muted} />
      <Item label="CPU" value={cpu} color={C.accent} />
      <Item label="GPU" value={gpu} color={hardware?.gpu.detected ? C.yellow : C.dim} />
      <Item label={t('Memory')} value={hardware ? t(formatCapacity(hardware.memory.totalBytes)) : t('Detecting…')} color={C.green} />
      <Item label="FedOps" value={`${t('Connected')} · ${identity}`} color={C.green} />
    </div>
  )
}

function gpuLabel(devices: HardwareGpuDevice[]) {
  const first = devices[0]
  if (!first) return 'Not detected'
  const details = [
    first.computeUnits ? `${first.computeUnits}c` : null,
    first.memoryBytes ? formatCapacity(first.memoryBytes) : null,
    devices.length > 1 ? `+${devices.length - 1}` : null,
  ].filter(Boolean)
  return details.length ? `${first.name} · ${details.join(' · ')}` : first.name
}

function formatCapacity(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return 'Unknown'
  const gibibytes = bytes / 1024**3
  if (gibibytes >= 1) {
    return `${Number.isInteger(gibibytes) ? gibibytes : gibibytes.toFixed(1)} GiB`
  }
  return `${Math.round(bytes / 1024**2)} MiB`
}
