export type MetricGroupKind = 'objective' | 'ratio' | 'other'

export interface MetricNameGroup {
  kind: MetricGroupKind
  names: string[]
}

const OBJECTIVE_TOKENS = [
  'loss',
  'error',
  'mae',
  'mse',
  'rmse',
  'mape',
  'smape',
  'nll',
  'perplexity',
  'objective',
]

const RATIO_TOKENS = [
  'accuracy',
  'f1',
  'precision',
  'recall',
  'auc',
  'auroc',
  'average_precision',
  'specificity',
  'sensitivity',
  'dice',
  'iou',
  'error_rate',
  'success_rate',
  'hit_rate',
]

function normalizedMetricName(name: string): string {
  return name
    .trim()
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
}

function containsMetricToken(name: string, token: string): boolean {
  return name === token || name.startsWith(`${token}_`) || name.endsWith(`_${token}`) || name.includes(`_${token}_`)
}

export function metricGroupKind(name: string): MetricGroupKind {
  const normalized = normalizedMetricName(name)
  if (RATIO_TOKENS.some(token => containsMetricToken(normalized, token))) return 'ratio'
  if (OBJECTIVE_TOKENS.some(token => containsMetricToken(normalized, token))) return 'objective'
  return 'other'
}

/**
 * Group every numeric Task metric without assuming a model, dataset, or Task.
 * Separate panels prevent incompatible scales from being plotted together.
 */
export function groupMetricNames(names: string[]): MetricNameGroup[] {
  const unique = [...new Set(names)]
  const order: MetricGroupKind[] = ['objective', 'ratio', 'other']
  return order.flatMap(kind => {
    const matching = unique.filter(name => metricGroupKind(name) === kind)
    return matching.length > 0 ? [{ kind, names: matching }] : []
  })
}

export function isRatioMetric(name: string): boolean {
  return metricGroupKind(name) === 'ratio'
}
