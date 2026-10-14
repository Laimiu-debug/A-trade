export type TableColumnDefinition = { key: string; title: string; width: number; minWidth?: number }
export type TableColumnPreference = { key: string; title: string; width: number; visible: boolean }

export function clampColumnWidth(value: number, definition: TableColumnDefinition): number {
  return Math.round(Math.max(definition.minWidth ?? 72, Math.min(600, Number.isFinite(value) ? value : definition.width)))
}

export function normalizeTablePreferences(raw: unknown, definitions: TableColumnDefinition[]): TableColumnPreference[] {
  const available = new Map(definitions.map(column => [column.key, column]))
  const result: TableColumnPreference[] = []
  const rows = Array.isArray(raw) ? raw : []
  for (const row of [...rows, ...definitions]) {
    if (!row || typeof row !== 'object' || !('key' in row)) continue
    const definition = available.get(String(row.key))
    if (!definition) continue
    available.delete(definition.key)
    const title = 'title' in row && typeof row.title === 'string' ? row.title.trim().slice(0, 40) : ''
    result.push({
      key: definition.key,
      title: title || definition.title,
      width: clampColumnWidth('width' in row && typeof row.width === 'number' ? row.width : definition.width, definition),
      visible: !('visible' in row) || row.visible !== false,
    })
  }
  if (result.length && !result.some(column => column.visible)) result[0].visible = true
  return result
}

export function moveTableColumn(rows: TableColumnPreference[], key: string, offset: number): TableColumnPreference[] {
  const index = rows.findIndex(column => column.key === key)
  const target = index + offset
  if (index < 0 || target < 0 || target >= rows.length) return rows
  const next = [...rows]
  const [column] = next.splice(index, 1)
  next.splice(target, 0, column)
  return next
}
