import { useEffect, useState } from 'react'
import Modal from './Modal'

const SHORTCUTS: { keys: string; description: string }[] = [
  { keys: '?', description: 'Show this shortcuts panel' },
  { keys: 'Esc', description: 'Close a dialog or popup' },
  { keys: '/', description: 'Focus the reply box (on a run page)' },
]

/** Global "?" listener + cheat-sheet modal, mounted once in AppShell so it
 * works from any page. Ignores "?" while the user is typing in a field --
 * otherwise typing a literal "?" anywhere would pop this open. */
export default function ShortcutsHelp() {
  const [open, setOpen] = useState(false)

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null
      const typing = target && ['INPUT', 'TEXTAREA'].includes(target.tagName)
      if (e.key === '?' && !typing) {
        e.preventDefault()
        setOpen(true)
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [])

  if (!open) return null

  return (
    <Modal title="Keyboard shortcuts" onClose={() => setOpen(false)}>
      <div className="shortcuts-list">
        {SHORTCUTS.map((s) => (
          <div className="shortcuts-row" key={s.keys}>
            <kbd>{s.keys}</kbd>
            <span>{s.description}</span>
          </div>
        ))}
      </div>
    </Modal>
  )
}
