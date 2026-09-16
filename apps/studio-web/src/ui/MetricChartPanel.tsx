import { useEffect, useMemo, useState } from "react"
import { useI18n } from "../app/i18n"
import { C } from "./UIKit"

export interface MetricChartPoint {
  x: number
  y: number
}

export interface MetricChartSeries {
  id: string
  label: string
  color: string
  points: MetricChartPoint[]
}

interface Props {
  title: string
  description?: string
  xLabel: string
  series: MetricChartSeries[]
  percent?: boolean
  emptyMessage?: string
  defaultActiveSeriesIds?: string[]
}

interface HoveredPoint {
  series: MetricChartSeries
  point: MetricChartPoint
  x: number
  y: number
}

const WIDTH = 720
const HEIGHT = 260
const PLOT = { left: 62, right: 18, top: 18, bottom: 42 }
const COLORS = { grid: C.borderSubtle, axis: C.border, label: C.dim }

export default function MetricChartPanel({
  title,
  description,
  xLabel,
  series,
  percent = false,
  emptyMessage,
  defaultActiveSeriesIds,
}: Props) {
  const { t } = useI18n()
  const [expanded, setExpanded] = useState(false)
  const usableSeries = useMemo(
    () =>
      series
        .map((item) => ({
          ...item,
          points: item.points.filter(
            (point) => Number.isFinite(point.x) && Number.isFinite(point.y),
          ),
        }))
        .filter((item) => item.points.length > 0),
    [series],
  )
  const seriesKey = usableSeries.map((item) => item.id).join("\u0000")
  const defaultKey = (defaultActiveSeriesIds ?? []).join("\u0000")
  const preferredIds = useMemo(
    () => selectDefaultSeriesIds(usableSeries, defaultActiveSeriesIds),
    [seriesKey, defaultKey],
  )
  const [activeIds, setActiveIds] = useState<string[]>(preferredIds)
  const availableIds = useMemo(
    () => new Set(usableSeries.map((item) => item.id)),
    [seriesKey],
  )
  const retainedActiveIds = activeIds.filter((id) => availableIds.has(id))
  const effectiveActiveIds =
    retainedActiveIds.length > 0 ? retainedActiveIds : preferredIds
  const activeIdSet = new Set(effectiveActiveIds)
  const visibleSeries = usableSeries.filter((item) => activeIdSet.has(item.id))
  const hasTrend = visibleSeries.some((item) => item.points.length > 1)
  const latest = usableSeries.map((item) => ({
    ...item,
    value: item.points[item.points.length - 1]?.y,
  }))

  useEffect(() => {
    setActiveIds((current) => {
      const retained = current.filter((id) => availableIds.has(id))
      return retained.length > 0 ? retained : preferredIds
    })
  }, [seriesKey, defaultKey])

  const toggleSeries = (id: string) => {
    setActiveIds((current) => {
      const retained = current.filter((item) => availableIds.has(item))
      const source = retained.length > 0 ? retained : preferredIds
      if (source.includes(id)) {
        return source.length === 1
          ? source
          : source.filter((item) => item !== id)
      }
      return [...source, id]
    })
  }

  useEffect(() => {
    if (!expanded) return undefined
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExpanded(false)
    }
    window.addEventListener("keydown", closeOnEscape)
    return () => window.removeEventListener("keydown", closeOnEscape)
  }, [expanded])

  return (
    <>
      <section
        style={{
          minWidth: 0,
          overflow: "hidden",
          border: `1px solid ${C.border}`,
          borderRadius: C.radius,
          background: C.surface,
          boxShadow: C.shadow,
        }}
      >
        <ChartHeader
          title={title}
          description={description}
          latest={latest}
          activeIds={activeIdSet}
          onToggleSeries={toggleSeries}
          percent={percent}
          hasData={usableSeries.length > 0}
          onExport={() => exportCsv(title, xLabel, visibleSeries)}
          onExpand={hasTrend ? () => setExpanded(true) : undefined}
        />
        {visibleSeries.length > 0 && hasTrend ? (
          <ChartCanvas
            series={visibleSeries}
            xLabel={xLabel}
            percent={percent}
          />
        ) : visibleSeries.length > 0 ? (
          <SingleObservation series={visibleSeries} xLabel={xLabel} percent={percent} />
        ) : (
          <div
            style={{
              height: 260,
              display: "grid",
              placeItems: "center",
              padding: 20,
              color: C.muted,
              background: C.surface2,
              fontSize: 12,
              textAlign: "center",
            }}
          >
            {emptyMessage ?? t("No metric data is available for this run.")}
          </div>
        )}
      </section>

      {expanded && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={`${title} ${t("expanded chart")}`}
          onMouseDown={() => setExpanded(false)}
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 1000,
            display: "grid",
            placeItems: "center",
            padding: 28,
            background: C.overlay,
          }}
        >
          <section
            onMouseDown={(event) => event.stopPropagation()}
            style={{
              width: "min(1180px, 94vw)",
              maxHeight: "90vh",
              overflow: "auto",
              border: `1px solid ${C.border}`,
              borderRadius: C.radius,
              background: C.surface,
              boxShadow: C.shadow,
            }}
          >
            <ChartHeader
              title={title}
              description={description}
              latest={latest}
              activeIds={activeIdSet}
              onToggleSeries={toggleSeries}
              percent={percent}
              hasData
              onExport={() => exportCsv(title, xLabel, visibleSeries)}
              onClose={() => setExpanded(false)}
            />
            {hasTrend ? (
              <ChartCanvas
                series={visibleSeries}
                xLabel={xLabel}
                percent={percent}
                expanded
              />
            ) : (
              <SingleObservation series={visibleSeries} xLabel={xLabel} percent={percent} />
            )}
          </section>
        </div>
      )}
    </>
  )
}

function SingleObservation({
  series,
  xLabel,
  percent,
}: {
  series: MetricChartSeries[]
  xLabel: string
  percent: boolean
}) {
  const { t } = useI18n()
  const point = series[0]?.points[0]
  return (
    <div
      style={{
        minHeight: 132,
        display: "grid",
        placeItems: "center",
        padding: 18,
        borderTop: `1px solid ${C.borderSubtle}`,
        background: C.surface2,
        textAlign: "center",
      }}
    >
      <div>
        <div style={{ display: "flex", justifyContent: "center", gap: 6, flexWrap: "wrap" }}>
          {series.map((item) => (
            <span
              key={item.id}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "5px 8px",
                border: `1px solid ${C.border}`,
                borderRadius: C.pillRadius,
                background: C.surface,
                color: C.text,
                fontSize: 10.5,
              }}
            >
              <span style={{ width: 7, height: 7, borderRadius: 99, background: item.color }} />
              {item.label} <strong style={{ fontFamily: "JetBrains Mono, monospace" }}>{formatValue(item.points[0].y, percent)}</strong>
            </span>
          ))}
        </div>
        <div style={{ marginTop: 10, color: C.muted, fontSize: 11 }}>
          {t("Only one metric observation was recorded. A trend requires at least two points.")}
        </div>
        {point && <div style={{ marginTop: 5, color: C.dim, fontFamily: "JetBrains Mono, monospace", fontSize: 9.5 }}>{xLabel} {formatAxis(point.x)}</div>}
      </div>
    </div>
  )
}

function ChartHeader({
  title,
  description,
  latest,
  activeIds,
  onToggleSeries,
  percent,
  hasData,
  onExport,
  onExpand,
  onClose,
}: {
  title: string
  description?: string
  latest: Array<MetricChartSeries & { value: number }>
  activeIds: Set<string>
  onToggleSeries: (id: string) => void
  percent: boolean
  hasData: boolean
  onExport: () => void
  onExpand?: () => void
  onClose?: () => void
}) {
  const { t } = useI18n()
  return (
    <header
      style={{
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "space-between",
        gap: 16,
        minHeight: 64,
        padding: "12px 14px 10px",
        borderBottom: `1px solid ${C.borderSubtle}`,
      }}
    >
      <div style={{ minWidth: 0 }}>
        <h3
          style={{
            margin: 0,
            color: C.text,
            fontFamily: "Inter, sans-serif",
            fontSize: 13,
            fontWeight: 650,
          }}
        >
          {title}
        </h3>
        {description && (
          <p
            style={{
              margin: "4px 0 0",
              color: C.muted,
              fontSize: 10.5,
              lineHeight: 1.4,
            }}
          >
            {description}
          </p>
        )}
        {latest.length > 0 && (
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "5px 12px",
              marginTop: 8,
            }}
          >
            {latest.map((item) => (
              <button
                type="button"
                key={item.id}
                aria-pressed={activeIds.has(item.id)}
                title={t(
                  activeIds.has(item.id) ? "Hide metric" : "Show metric",
                )}
                onClick={() => onToggleSeries(item.id)}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 5,
                  padding: "3px 6px",
                  border: `1px solid ${
                    activeIds.has(item.id) ? item.color : C.borderSubtle
                  }`,
                  borderRadius: C.pillRadius,
                  background: activeIds.has(item.id)
                    ? C.surface2
                    : "transparent",
                  color: activeIds.has(item.id) ? C.text : C.dim,
                  fontSize: 10,
                  cursor: "pointer",
                  opacity: activeIds.has(item.id) ? 1 : 0.64,
                }}
              >
                <span
                  style={{
                    width: 7,
                    height: 7,
                    borderRadius: 99,
                    background: activeIds.has(item.id) ? item.color : C.dim,
                  }}
                />
                <span>{item.label}</span>
                <strong
                  style={{
                    color: C.text,
                    fontFamily: "JetBrains Mono, monospace",
                    fontWeight: 600,
                  }}
                >
                  {formatValue(item.value, percent)}
                </strong>
              </button>
            ))}
          </div>
        )}
      </div>
      <div style={{ display: "flex", gap: 5, flexShrink: 0 }}>
        {hasData && (
          <ChartAction title={t("Export CSV")} onClick={onExport}>
            ⇩ <span>{t("CSV")}</span>
          </ChartAction>
        )}
        {hasData && onExpand && (
          <ChartAction title={t("Expand chart")} onClick={onExpand}>
            ↗
          </ChartAction>
        )}
        {onClose && (
          <ChartAction title={t("Close expanded chart")} onClick={onClose}>
            ×
          </ChartAction>
        )}
      </div>
    </header>
  )
}

function selectDefaultSeriesIds(
  series: MetricChartSeries[],
  requested?: string[],
): string[] {
  const available = new Set(series.map((item) => item.id))
  const requestedAvailable = (requested ?? []).filter((id) => available.has(id))
  if (requestedAvailable.length > 0) return requestedAvailable
  const preferredOrder = [
    "validation_loss",
    "accuracy",
    "primary_metric",
    "training_loss",
  ]
  const preferred = preferredOrder.find((id) => available.has(id))
  return preferred ? [preferred] : series[0] ? [series[0].id] : []
}

function ChartAction({
  title,
  onClick,
  children,
}: {
  title: string
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      onClick={onClick}
      style={{
        minWidth: 29,
        minHeight: 27,
        padding: "3px 7px",
        border: `1px solid ${C.controlBorder}`,
        borderRadius: C.radius,
        background: C.controlBg,
        color: C.controlText,
        cursor: "pointer",
        fontFamily: "Inter, sans-serif",
        fontSize: 10,
      }}
    >
      {children}
    </button>
  )
}

function ChartCanvas({
  series,
  xLabel,
  percent,
  expanded = false,
}: {
  series: MetricChartSeries[]
  xLabel: string
  percent: boolean
  expanded?: boolean
}) {
  const [hovered, setHovered] = useState<HoveredPoint | null>(null)
  const allPoints = series.flatMap((item) => item.points)
  const xValues = allPoints.map((point) => point.x)
  const yValues = allPoints.map((point) => point.y)
  const xRange = paddedRange(Math.min(...xValues), Math.max(...xValues), false)
  const yRange = paddedRange(
    Math.min(...yValues),
    Math.max(...yValues),
    percent,
  )
  const xTicks = ticks(xRange.min, xRange.max, 6)
  const yTicks = ticks(yRange.min, yRange.max, 5)
  const plotWidth = WIDTH - PLOT.left - PLOT.right
  const plotHeight = HEIGHT - PLOT.top - PLOT.bottom
  const px = (value: number) =>
    PLOT.left +
    ((value - xRange.min) / Math.max(xRange.max - xRange.min, Number.EPSILON)) *
      plotWidth
  const py = (value: number) =>
    PLOT.top +
    plotHeight -
    ((value - yRange.min) / Math.max(yRange.max - yRange.min, Number.EPSILON)) *
      plotHeight

  return (
    <div
      style={{
        position: "relative",
        minHeight: expanded ? 520 : 260,
        padding: expanded ? "12px 18px 20px" : "7px 8px 10px",
        background: C.surface,
      }}
    >
      <svg
        role="img"
        aria-label={`${xLabel} metric chart`}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        preserveAspectRatio="xMidYMid meet"
        onMouseLeave={() => setHovered(null)}
        style={{
          display: "block",
          width: "100%",
          height: expanded ? 520 : 260,
          overflow: "visible",
        }}
      >
        {yTicks.map((value) => {
          const y = py(value)
          return (
            <g key={`y-${value}`}>
              <line
                x1={PLOT.left}
                y1={y}
                x2={WIDTH - PLOT.right}
                y2={y}
                stroke={COLORS.grid}
                strokeWidth="1"
                vectorEffect="non-scaling-stroke"
              />
              <text
                x={PLOT.left - 9}
                y={y + 3.5}
                textAnchor="end"
                fill={COLORS.label}
                fontFamily="JetBrains Mono, monospace"
                fontSize="9"
              >
                {formatValue(value, percent, true)}
              </text>
            </g>
          )
        })}
        {xTicks.map((value) => {
          const x = px(value)
          return (
            <g key={`x-${value}`}>
              <line
                x1={x}
                y1={PLOT.top}
                x2={x}
                y2={HEIGHT - PLOT.bottom}
                stroke={COLORS.grid}
                strokeWidth="1"
                strokeDasharray="2 4"
                vectorEffect="non-scaling-stroke"
              />
              <text
                x={x}
                y={HEIGHT - PLOT.bottom + 17}
                textAnchor="middle"
                fill={COLORS.label}
                fontFamily="JetBrains Mono, monospace"
                fontSize="9"
              >
                {formatAxis(value)}
              </text>
            </g>
          )
        })}
        <line
          x1={PLOT.left}
          y1={HEIGHT - PLOT.bottom}
          x2={WIDTH - PLOT.right}
          y2={HEIGHT - PLOT.bottom}
          stroke={COLORS.axis}
          strokeWidth="1"
          vectorEffect="non-scaling-stroke"
        />
        <text
          x={PLOT.left + plotWidth / 2}
          y={HEIGHT - 6}
          textAnchor="middle"
          fill={COLORS.label}
          fontFamily="Inter, sans-serif"
          fontSize="9.5"
        >
          {xLabel}
        </text>

        {series.map((item) => {
          const rendered = item.points.map((point) => ({
            point,
            x: px(point.x),
            y: py(point.y),
          }))
          return (
            <g key={item.id}>
              {rendered.length > 1 && (
                <path
                  d={linePath(rendered)}
                  fill="none"
                  stroke={item.color}
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  vectorEffect="non-scaling-stroke"
                />
              )}
              {rendered.map((entry, index) => (
                <g key={`${item.id}-${entry.point.x}-${index}`}>
                  <circle
                    cx={entry.x}
                    cy={entry.y}
                    r={rendered.length === 1 ? 3.2 : 2.2}
                    fill={item.color}
                    vectorEffect="non-scaling-stroke"
                  />
                  <circle
                    cx={entry.x}
                    cy={entry.y}
                    r="10"
                    fill="transparent"
                    style={{ cursor: "crosshair" }}
                    onMouseEnter={() =>
                      setHovered({
                        series: item,
                        point: entry.point,
                        x: entry.x,
                        y: entry.y,
                      })
                    }
                  />
                </g>
              ))}
            </g>
          )
        })}

        {hovered && (
          <ChartTooltip hovered={hovered} xLabel={xLabel} percent={percent} />
        )}
      </svg>
    </div>
  )
}

function ChartTooltip({
  hovered,
  xLabel,
  percent,
}: {
  hovered: HoveredPoint
  xLabel: string
  percent: boolean
}) {
  const boxWidth = 190
  const boxHeight = 50
  const x = Math.min(
    Math.max(hovered.x + 10, PLOT.left),
    WIDTH - PLOT.right - boxWidth,
  )
  const y = Math.min(
    Math.max(hovered.y - boxHeight - 9, PLOT.top),
    HEIGHT - PLOT.bottom - boxHeight,
  )
  return (
    <g pointerEvents="none">
      <line
        x1={hovered.x}
        y1={PLOT.top}
        x2={hovered.x}
        y2={HEIGHT - PLOT.bottom}
        stroke={C.muted}
        strokeWidth="1"
        strokeDasharray="3 3"
        opacity="0.55"
        vectorEffect="non-scaling-stroke"
      />
      <circle
        cx={hovered.x}
        cy={hovered.y}
        r="4"
        fill={C.surface}
        stroke={hovered.series.color}
        strokeWidth="2"
        vectorEffect="non-scaling-stroke"
      />
      <rect
        x={x}
        y={y}
        width={boxWidth}
        height={boxHeight}
        rx="4"
        fill={C.surface3}
        stroke={C.border}
        strokeWidth="1"
        vectorEffect="non-scaling-stroke"
      />
      <circle cx={x + 11} cy={y + 14} r="3.5" fill={hovered.series.color} />
      <text
        x={x + 20}
        y={y + 17}
        fill={C.text}
        fontFamily="Inter, sans-serif"
        fontSize="10.5"
        fontWeight="600"
      >
        {hovered.series.label}
      </text>
      <text
        x={x + 10}
        y={y + 37}
        fill={C.muted}
        fontFamily="JetBrains Mono, monospace"
        fontSize="9.5"
      >{`${xLabel} ${formatAxis(hovered.point.x)}  ·  ${formatValue(hovered.point.y, percent)}`}</text>
    </g>
  )
}

function paddedRange(rawMin: number, rawMax: number, percent: boolean) {
  const basePadding =
    rawMax === rawMin
      ? Math.max(Math.abs(rawMax) * 0.08, percent ? 0.01 : 0.001)
      : (rawMax - rawMin) * 0.08
  const min =
    percent && rawMin >= 0 && rawMax <= 1
      ? Math.max(0, rawMin - basePadding)
      : rawMin - basePadding
  const max =
    percent && rawMin >= 0 && rawMax <= 1
      ? Math.min(1, rawMax + basePadding)
      : rawMax + basePadding
  return min === max ? { min: min - 1, max: max + 1 } : { min, max }
}

function ticks(min: number, max: number, count: number) {
  return Array.from(
    { length: count },
    (_, index) => min + ((max - min) * index) / (count - 1),
  )
}

function linePath(points: Array<{ x: number; y: number }>) {
  if (points.length === 0) return ""
  if (points.length === 1) return `M ${points[0].x} ${points[0].y}`
  return points.slice(1).reduce(
    (path, point) => `${path} L ${point.x} ${point.y}`,
    `M ${points[0].x} ${points[0].y}`,
  )
}

function formatValue(value: number, percent: boolean, compact = false) {
  if (!Number.isFinite(value)) return "—"
  if (percent && value >= 0 && value <= 1)
    return `${(value * 100).toFixed(compact ? 0 : 2)}%`
  const absolute = Math.abs(value)
  if (absolute >= 1000)
    return Intl.NumberFormat("en", {
      notation: "compact",
      maximumFractionDigits: 1,
    }).format(value)
  if (compact) return value.toFixed(absolute < 0.01 ? 3 : absolute < 1 ? 2 : 1)
  return value.toFixed(absolute < 0.01 ? 6 : 4)
}

function formatAxis(value: number) {
  if (!Number.isFinite(value)) return "—"
  if (Number.isInteger(value)) return String(value)
  return value.toFixed(Math.abs(value) < 10 ? 1 : 0)
}

function exportCsv(title: string, xLabel: string, series: MetricChartSeries[]) {
  const xValues = [
    ...new Set(series.flatMap((item) => item.points.map((point) => point.x))),
  ].sort((a, b) => a - b)
  const rows = [
    [xLabel, ...series.map((item) => item.label)],
    ...xValues.map((x) => [
      String(x),
      ...series.map((item) =>
        String(item.points.find((point) => point.x === x)?.y ?? ""),
      ),
    ]),
  ]
  const csv = rows.map((row) => row.map(csvCell).join(",")).join("\n")
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = `${title.toLowerCase().replace(/[^a-z0-9]+/g, "-") || "metrics"}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

function csvCell(value: string) {
  return /[",\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value
}
