import fedopsLogo from '../assets/fedops-logo.png'

export default function FedOpsLogo({ size = 32 }: { size?: number }) {
  const iconSize = Math.round(size * 0.72)

  return (
    <span
      aria-hidden="true"
      style={{
        display: 'grid',
        placeItems: 'center',
        width: size,
        height: size,
        flexShrink: 0,
        boxSizing: 'border-box',
        overflow: 'hidden',
        background: '#ffffff',
        border: '1px solid rgba(24, 24, 24, 0.14)',
        borderRadius: Math.round(size * 0.27),
        boxShadow: '0 1px 2px rgba(24, 24, 24, 0.08)',
      }}
    >
      <img
        src={fedopsLogo}
        alt=""
        width={iconSize}
        height={iconSize}
        style={{ display: 'block', width: iconSize, height: iconSize, objectFit: 'contain' }}
      />
    </span>
  )
}
