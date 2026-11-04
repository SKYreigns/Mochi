import { useEffect } from 'react'
import { HashRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Background } from '@/components/Background'
import { AppShell } from '@/components/AppShell'
import { ApprovalDialog } from '@/components/ApprovalDialog'
import { Toaster } from '@/components/Toaster'
import { useAppStore } from '@/stores/app'
import { startRealtime, stopRealtime } from '@/lib/realtime'
import { HomeScreen } from '@/screens/Home'
import { ChatScreen } from '@/screens/Chat'
import { LiveActivityScreen } from '@/screens/LiveActivity'
import { TasksScreen } from '@/screens/Tasks'
import { ComputerScreen } from '@/screens/Computer'
import { FilesScreen } from '@/screens/Files'
import { WorkflowsScreen } from '@/screens/Workflows'
import { MemoryScreen } from '@/screens/Memory'
import { ScheduleScreen } from '@/screens/Schedule'
import { SettingsScreen } from '@/screens/Settings'

export default function App() {
  const initialize = useAppStore((state) => state.initialize)

  useEffect(() => {
    void initialize()
    startRealtime()
    return () => stopRealtime()
  }, [initialize])

  return (
    <HashRouter>
      <Background />
      <AppShell>
        <Routes>
          <Route path="/" element={<HomeScreen />} />
          <Route path="/chat" element={<ChatScreen />} />
          <Route path="/chat/:conversationId" element={<ChatScreen />} />
          <Route path="/activity" element={<LiveActivityScreen />} />
          <Route path="/tasks" element={<TasksScreen />} />
          <Route path="/computer" element={<ComputerScreen />} />
          <Route path="/files" element={<FilesScreen />} />
          <Route path="/workflows" element={<WorkflowsScreen />} />
          <Route path="/memory" element={<MemoryScreen />} />
          <Route path="/schedule" element={<ScheduleScreen />} />
          <Route path="/settings" element={<SettingsScreen />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AppShell>
      <ApprovalDialog />
      <Toaster />
    </HashRouter>
  )
}
