type FileTypeIconProps = {
  name: string
  directory?: boolean
  open?: boolean
  size?: number
}

type FileVisual = {
  glyph: string
  label: string
  color: string
}

export default function FileTypeIcon({
  name,
  directory = false,
  open = false,
  size = 18,
}: FileTypeIconProps) {
  if (directory) {
    return (
      <svg
        aria-hidden="true"
        viewBox="0 0 20 20"
        width={size}
        height={size}
        style={{ display: 'block', flexShrink: 0 }}
      >
        <path
          d="M2.25 5.1c0-.8.65-1.45 1.45-1.45h3.15l1.45 1.6h8c.8 0 1.45.65 1.45 1.45v7.45c0 .9-.73 1.62-1.62 1.62H3.87c-.9 0-1.62-.73-1.62-1.62V5.1Z"
          fill={open ? 'var(--studio-accent-dim)' : 'var(--studio-surface-3)'}
          stroke={open ? 'var(--studio-accent)' : 'var(--studio-muted)'}
          strokeWidth="1.35"
          strokeLinejoin="round"
        />
        {open && (
          <path
            d="M3.4 8.1h13.2"
            stroke="var(--studio-accent)"
            strokeWidth="1"
            strokeLinecap="round"
            opacity=".72"
          />
        )}
      </svg>
    )
  }

  const visual = fileVisual(name)
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 20 20"
      width={size}
      height={size}
      style={{ display: 'block', flexShrink: 0 }}
    >
      <path
        d="M4.1 1.9h7.2l4.6 4.6v11.6H4.1V1.9Z"
        fill="var(--studio-surface-2)"
        stroke={visual.color}
        strokeWidth="1.2"
        strokeLinejoin="round"
      />
      <path
        d="M11.3 1.9v4.6h4.6"
        fill="none"
        stroke={visual.color}
        strokeWidth="1.2"
        strokeLinejoin="round"
      />
      {visual.glyph && (
        <text
          x="10"
          y="14.2"
          fill={visual.color}
          fontFamily="JetBrains Mono, ui-monospace, monospace"
          fontSize={visual.glyph.length > 2 ? 4.25 : 5.4}
          fontWeight="700"
          textAnchor="middle"
        >
          {visual.glyph}
        </text>
      )}
    </svg>
  )
}

export function fileTypeLabel(name: string, directory = false): string {
  return directory ? 'Directory' : fileVisual(name).label
}

function fileVisual(name: string): FileVisual {
  const value = name.toLowerCase()
  const extension = value.includes('.') ? value.slice(value.lastIndexOf('.')) : ''

  if (extension === '.py' || extension === '.pyi') return visual('PY', 'Python file', '--studio-yellow')
  if (['.js', '.jsx', '.mjs', '.cjs'].includes(extension)) return visual('JS', 'JavaScript file', '--studio-yellow')
  if (extension === '.ts' || extension === '.tsx') return visual('TS', 'TypeScript file', '--studio-accent')
  if (extension === '.json' || extension === '.jsonl') return visual('{}', 'JSON file', '--studio-orange')
  if (extension === '.yaml' || extension === '.yml') return visual('YML', 'YAML file', '--studio-purple')
  if (extension === '.toml') return visual('TML', 'TOML file', '--studio-purple')
  if (extension === '.md' || extension === '.mdx') return visual('MD', 'Markdown file', '--studio-accent')
  if (['.sh', '.bash', '.zsh', '.fish'].includes(extension)) return visual('>_', 'Shell script', '--studio-green')
  if (extension === '.env' || ['.ini', '.cfg', '.conf', '.properties'].includes(extension)) {
    return visual('CFG', 'Configuration file', '--studio-purple')
  }
  if (['.csv', '.tsv', '.parquet', '.arrow'].includes(extension)) return visual('DATA', 'Data file', '--studio-green')
  if (['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.ico'].includes(extension)) {
    return visual('IMG', 'Image file', '--studio-purple')
  }
  if (extension === '.html' || extension === '.htm') return visual('HTM', 'HTML file', '--studio-orange')
  if (['.css', '.scss', '.sass', '.less'].includes(extension)) return visual('CSS', 'Stylesheet', '--studio-accent')
  if (extension === '.sql' || extension === '.db' || extension === '.sqlite') {
    return visual('SQL', 'Database file', '--studio-green')
  }
  if (extension === '.ipynb') return visual('NB', 'Notebook file', '--studio-orange')
  if (extension === '.xml') return visual('XML', 'XML file', '--studio-orange')
  if (extension === '.lock') return visual('LCK', 'Lock file', '--studio-orange')
  if (['.txt', '.log'].includes(extension)) return visual('TXT', 'Text file', '--studio-muted')
  if (value === 'dockerfile') return visual('DKR', 'Container file', '--studio-accent')
  if (value === 'makefile') return visual('MK', 'Makefile', '--studio-green')
  if (value.startsWith('.')) return visual('CFG', 'Configuration file', '--studio-muted')
  return visual('', 'File', '--studio-dim')
}

function visual(glyph: string, label: string, colorVariable: string): FileVisual {
  return { glyph, label, color: `var(${colorVariable})` }
}
