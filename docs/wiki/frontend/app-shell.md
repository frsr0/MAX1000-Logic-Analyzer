# App Shell

**File:** `frontend/src/layout/AppShell.tsx`

## Purpose

The application chrome: sidebar navigation, top status bar, toast notifications, and global keyboard shortcuts. Wraps all page content.

## Structure

```
┌──────────────────────────────────────────────┐
│  Sidebar (nav)   │  Top Bar                   │
│  ┌─────┐         │  [device badge] [status]   │
│  │ CAP │ Capture │                             │
│  │ SES │ Sessions│  ┌─────────────────────┐   │
│  │ DEV │ Device  │  │                     │   │
│  │ GEN │ Gen.    │  │   <CurrentPage />   │   │
│  │ MIL │ MIL     │  │                     │   │
│  │ DIA │ Diag.   │  │                     │   │
│  │ SET │ Settings │  └─────────────────────┘   │
│  └─────┘         │                             │
│                  │  Status Bar                 │
│                  │  [capture state] [ws status] │
└──────────────────────────────────────────────┘
```

## Navigation Items

| ID | Icon | Label | Page Component |
|---|---|---|---|
| `capture` | CAP | Capture | `CapturePage` |
| `sessions` | SES | Sessions | `SessionsPage` |
| `device` | DEV | Device | `DevicePage` |
| `generator` | GEN | Generator | `GeneratorPage` |
| `mil` | MIL | MIL | `MachineInLoopPage` |
| `diagnostics` | DIA | Diagnostics | `DiagnosticsPage` |
| `settings` | SET | Settings | `SettingsPage` |

## Top Bar

Displays:
- Device badge: device name or "No device" / " (mock)" suffix
- Sample clock: formatted as `XX.X MHz sample clock`
- Control mode indicator: read-only vs control

## Status Bar

Shows:
- WebSocket connection status (connected/disconnected)
- Capture state (idle/armed/busy/done/error)
- Toast notifications (auto-dismiss after 5s)

## Global Keyboard Shortcuts

| Key | Action |
|---|---|
| `Space` | Start/stop capture (toggle) |
| `Ctrl+S` | Save active session as JSON export |

## Toast System

```typescript
interface Toast {
  id: number;
  level: 'info' | 'warning' | 'error' | 'success';
  message: string;
}
```

Auto-dismiss after 5s, click to dismiss. Stacked in top-right.

## State Dependencies

From `useApp()`: `page`, `setPage`, `status`, `wsConnected`, `toasts`, `dismissToast`, `controlMode`.
