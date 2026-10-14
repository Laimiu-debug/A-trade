import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode, ThHTMLAttributes } from 'react'
import { App, Button, Input, InputNumber, Modal, Space, Switch, Table } from 'antd'
import type { TableProps } from 'antd'
import type { ColumnType } from 'antd/es/table'
import { clampColumnWidth, moveTableColumn, normalizeTablePreferences } from '@/shared/utils/tablePreferences'
import type { TableColumnDefinition, TableColumnPreference } from '@/shared/utils/tablePreferences'

const CHANGE_EVENT = 'atrade-table-preferences-changed'
const DATE_KEYS = new Set(['signal_date', 'entry_date', 'exit_date'])
type ResizeHeaderProps = ThHTMLAttributes<HTMLTableCellElement> & {
  resizeWidth?: number
  resizeTitle?: string
  onColumnResize?: (width: number, finish: boolean) => void
}

function ResizeHeader({ resizeWidth, resizeTitle, onColumnResize, children, ...props }: ResizeHeaderProps) {
  const drag = useRef<{ start: number; width: number } | null>(null)
  return (
    <th {...props}>
      {children}
      {onColumnResize && resizeWidth ? (
        <span
          className="research-column-resize"
          role="separator"
          aria-orientation="vertical"
          aria-label={`调整${resizeTitle}列宽`}
          aria-valuenow={resizeWidth}
          tabIndex={0}
          title="左右拖动调整列宽，也可使用左右方向键"
          onClick={event => event.stopPropagation()}
          onPointerDown={event => {
            if (event.button !== 0) return
            event.preventDefault()
            event.stopPropagation()
            drag.current = { start: event.clientX, width: resizeWidth }
            event.currentTarget.setPointerCapture(event.pointerId)
          }}
          onPointerMove={event => {
            if (drag.current) onColumnResize(drag.current.width + event.clientX - drag.current.start, false)
          }}
          onPointerUp={event => {
            if (!drag.current) return
            onColumnResize(drag.current.width + event.clientX - drag.current.start, true)
            drag.current = null
            event.currentTarget.releasePointerCapture(event.pointerId)
          }}
          onPointerCancel={() => {
            if (drag.current) onColumnResize(drag.current.width, true)
            drag.current = null
          }}
          onKeyDown={event => {
            if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return
            event.preventDefault()
            onColumnResize(resizeWidth + (event.key === 'ArrowRight' ? 10 : -10), true)
          }}
        />
      ) : null}
    </th>
  )
}

type Props<T extends object> = TableProps<T> & { settingsKey: string; toolbarExtra?: ReactNode }

export function EditableDetailTable<T extends object>({ columns = [], settingsKey, toolbarExtra, scroll, ...props }: Props<T>) {
  const { message } = App.useApp()
  const definitions = useMemo<TableColumnDefinition[]>(() => (columns as ColumnType<T>[]).map((column, index) => {
    const key = String(column.key ?? column.dataIndex ?? index)
    return { key, title: typeof column.title === 'string' ? column.title : key, width: Number(column.width) || 110, minWidth: DATE_KEYS.has(key) ? 116 : 72 }
  }), [columns])
  const storageKey = `atrade.table-columns.v1.${settingsKey}`
  const read = () => {
    try { return normalizeTablePreferences(JSON.parse(localStorage.getItem(storageKey) || 'null'), definitions) }
    catch { return normalizeTablePreferences(null, definitions) }
  }
  const [preferences, setPreferences] = useState<TableColumnPreference[]>(read)
  const [draft, setDraft] = useState<TableColumnPreference[] | null>(null)
  useEffect(() => {
    const sync = () => setPreferences(read())
    const onStorage = (event: StorageEvent) => { if (event.key === storageKey) sync() }
    const onChange = (event: Event) => { if ((event as CustomEvent).detail === storageKey) sync() }
    window.addEventListener('storage', onStorage)
    window.addEventListener(CHANGE_EVENT, onChange)
    return () => { window.removeEventListener('storage', onStorage); window.removeEventListener(CHANGE_EVENT, onChange) }
    // The column definitions and storage key remain stable for each table.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageKey, definitions])
  const commit = (next: TableColumnPreference[]) => {
    const normalized = normalizeTablePreferences(next, definitions)
    setPreferences(normalized)
    try {
      localStorage.setItem(storageKey, JSON.stringify(normalized))
      window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: storageKey }))
    } catch { message.warning('表头已应用，但本地存储不可用，关闭页面后可能无法保留。') }
  }
  const visible = preferences.filter(column => column.visible)
  const renderedColumns: ColumnType<T>[] = visible.map(preference => {
    const index = definitions.findIndex(column => column.key === preference.key)
    const original = columns[index] as ColumnType<T>
    const definition = definitions[index]
    return {
      ...original,
      key: preference.key,
      title: preference.title,
      width: preference.width,
      className: [original.className, DATE_KEYS.has(preference.key) ? 'research-date-cell' : ''].filter(Boolean).join(' '),
      onHeaderCell: row => ({
        ...original.onHeaderCell?.(row),
        resizeWidth: preference.width,
        resizeTitle: preference.title,
        onColumnResize: (width: number, finish: boolean) => {
          const next = preferences.map(column => column.key === preference.key ? { ...column, width: clampColumnWidth(width, definition) } : column)
          if (finish) commit(next)
          else setPreferences(next)
        },
      }),
    }
  })
  const updateDraft = (key: string, patch: Partial<TableColumnPreference>) => setDraft(current => {
    if (!current || (patch.visible === false && current.filter(column => column.visible).length === 1)) return current
    return current.map(column => column.key === key ? { ...column, ...patch } : column)
  })
  return (
    <div data-research="trade-table">
      <div className="research-table-toolbar">
        <span className="research-inline-note">表头右侧可拖动调整列宽</span>
        <Space size={8}>
          <Button size="small" onClick={() => setDraft(preferences.map(column => ({ ...column })))}>表头设置</Button>
          {toolbarExtra}
        </Space>
      </div>
      <Table<T>
        {...props}
        columns={renderedColumns}
        tableLayout="fixed"
        components={{ header: { cell: ResizeHeader } }}
        scroll={{ ...scroll, x: visible.reduce((sum, column) => sum + column.width, 0) }}
      />
      <Modal
        title="交易明细表头设置"
        open={draft !== null}
        width={680}
        onCancel={() => setDraft(null)}
        onOk={() => { if (draft) commit(draft); setDraft(null) }}
        okText="保存设置"
        cancelText="取消"
        className="research-column-settings"
      >
        <p className="research-inline-note">可修改列名、显示与隐藏、顺序和宽度。设置会在本机保留，策略回测与收益平原共用。</p>
        <div className="research-column-settings-scroll">
          {draft?.map((column, index) => (
            <div className="research-column-settings-row" key={column.key} data-column-key={column.key}>
              <Switch
                size="small"
                aria-label={`显示${column.title}`}
                checked={column.visible}
                disabled={column.visible && draft.filter(item => item.visible).length === 1}
                onChange={visible => updateDraft(column.key, { visible })}
              />
              <Input size="small" aria-label={`${definitions.find(item => item.key === column.key)?.title}列名`} value={column.title} maxLength={40} onChange={event => updateDraft(column.key, { title: event.target.value })} />
              <InputNumber size="small" aria-label={`${column.title}列宽`} value={column.width} min={definitions.find(item => item.key === column.key)?.minWidth ?? 72} max={600} addonAfter="px" onChange={width => { if (width !== null) updateDraft(column.key, { width }) }} />
              <Space size={4}>
                <Button size="small" aria-label={`上移${column.title}`} disabled={index === 0} onClick={() => setDraft(current => current && moveTableColumn(current, column.key, -1))}>↑</Button>
                <Button size="small" aria-label={`下移${column.title}`} disabled={index === draft.length - 1} onClick={() => setDraft(current => current && moveTableColumn(current, column.key, 1))}>↓</Button>
              </Space>
            </div>
          ))}
        </div>
        <Button size="small" onClick={() => setDraft(normalizeTablePreferences(null, definitions))}>恢复默认表头</Button>
      </Modal>
    </div>
  )
}
