/* eslint-disable react-refresh/only-export-components */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert } from 'antd'
import type { AlertProps } from 'antd'

const STORAGE_PREFIX = 'tdx-dismissible-alert-v1.'
const RESET_EVENT_NAME = 'tdx-dismissible-alert-reset'

function readDismissed(storageKey: string, persist: boolean): boolean {
  if (!persist || typeof window === 'undefined') return false
  try {
    return window.localStorage.getItem(storageKey) === '1'
  } catch {
    return false
  }
}

function writeDismissed(storageKey: string, persist: boolean): void {
  if (!persist || typeof window === 'undefined') return
  try {
    window.localStorage.setItem(storageKey, '1')
  } catch {
    // ignore local storage failures
  }
}

function broadcastReset() {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent(RESET_EVENT_NAME))
}

export function resetAllDismissedAlerts(): number {
  if (typeof window === 'undefined') return 0
  let removedCount = 0
  try {
    for (let index = window.localStorage.length - 1; index >= 0; index -= 1) {
      const key = window.localStorage.key(index)
      if (!key || !key.startsWith(STORAGE_PREFIX)) continue
      window.localStorage.removeItem(key)
      removedCount += 1
    }
  } catch {
    return 0
  }
  broadcastReset()
  return removedCount
}

export type DismissibleAlertProps = AlertProps & {
  dismissKey: string
  persist?: boolean
}

export function DismissibleAlert(props: DismissibleAlertProps) {
  const { dismissKey, persist = true, closable = true, onClose, ...restProps } = props
  const storageKey = useMemo(() => `${STORAGE_PREFIX}${dismissKey}`, [dismissKey])
  const [visible, setVisible] = useState(() => !readDismissed(storageKey, persist))

  useEffect(() => {
    setVisible(!readDismissed(storageKey, persist))
  }, [persist, storageKey])

  useEffect(() => {
    if (typeof window === 'undefined') return undefined
    const resetHandler = () => {
      setVisible(!readDismissed(storageKey, persist))
    }
    window.addEventListener(RESET_EVENT_NAME, resetHandler)
    return () => {
      window.removeEventListener(RESET_EVENT_NAME, resetHandler)
    }
  }, [persist, storageKey])

  const handleClose: NonNullable<AlertProps['onClose']> = useCallback((event) => {
    writeDismissed(storageKey, persist)
    setVisible(false)
    onClose?.(event)
  }, [onClose, persist, storageKey])

  if (!visible) return null
  return <Alert {...restProps} closable={closable} onClose={handleClose} />
}
