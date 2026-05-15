import { useEffect } from 'react'
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
  useEffect(() => {
    const activeClass = 'sentinelflow-scrollbar-active'
    const activeElements = new Set<Element>()
    const timers = new WeakMap<Element, ReturnType<typeof window.setTimeout>>()

    function resolveScrollElement(target: EventTarget | null): Element {
      if (target instanceof Document || target === document || target === window) {
        return document.documentElement
      }
      return target instanceof Element ? target : document.documentElement
    }

    function markScrollActive(event: Event) {
      const element = resolveScrollElement(event.target)
      element.classList.add(activeClass)
      activeElements.add(element)

      const existingTimer = timers.get(element)
      if (existingTimer) {
        window.clearTimeout(existingTimer)
      }

      const timer = window.setTimeout(() => {
        element.classList.remove(activeClass)
        activeElements.delete(element)
        timers.delete(element)
      }, 3000)
      timers.set(element, timer)
    }

    document.addEventListener('scroll', markScrollActive, true)
    window.addEventListener('scroll', markScrollActive, true)

    return () => {
      document.removeEventListener('scroll', markScrollActive, true)
      window.removeEventListener('scroll', markScrollActive, true)
      activeElements.forEach((element) => {
        const timer = timers.get(element)
        if (timer) {
          window.clearTimeout(timer)
        }
        element.classList.remove(activeClass)
      })
    }
  }, [])

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
