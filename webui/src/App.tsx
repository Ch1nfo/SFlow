import { Route, Routes, Navigate } from 'react-router-dom'
import Layout from '@/components/layout/Layout'
import SentinelFlowOverviewPage from '@/pages/SentinelFlow/Overview'
import SentinelFlowAlertsPage from '@/pages/SentinelFlow/Alerts'
import SentinelFlowTasksPage from '@/pages/SentinelFlow/Tasks'
import SentinelFlowConversationPage from '@/pages/SentinelFlow/Conversation'
import SentinelFlowSkillsPage from '@/pages/SentinelFlow/Skills'
import SentinelFlowRagPage from '@/pages/SentinelFlow/Rag'
import SentinelFlowAgentsPage from '@/pages/SentinelFlow/Agents'
import SentinelFlowWorkflowsPage from '@/pages/SentinelFlow/Workflows'
import SentinelFlowSettingsPage from '@/pages/SentinelFlow/Settings'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<SentinelFlowOverviewPage />} />
        <Route path="alerts" element={<SentinelFlowAlertsPage />} />
        <Route path="tasks" element={<SentinelFlowTasksPage />} />
        <Route path="conversation" element={<SentinelFlowConversationPage />} />
        <Route path="skills" element={<SentinelFlowSkillsPage />} />
        <Route path="rag" element={<SentinelFlowRagPage />} />
        <Route path="agents" element={<SentinelFlowAgentsPage />} />
        <Route path="workflows" element={<SentinelFlowWorkflowsPage />} />
        <Route path="workflows/new" element={<SentinelFlowWorkflowsPage />} />
        <Route path="workflows/:id" element={<SentinelFlowWorkflowsPage />} />
        <Route path="workflows/:id/edit" element={<SentinelFlowWorkflowsPage />} />
        <Route path="settings" element={<SentinelFlowSettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
