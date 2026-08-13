import { useState, useMemo } from 'react'
import {
  LayoutDashboardIcon, ScaleIcon, FilesIcon, NetworkIcon, SearchIcon,
  GavelIcon, BoxesIcon, SparklesIcon, Code2Icon, RocketIcon,
  BellIcon, SunIcon, MoonIcon, PanelLeftIcon, LayersIcon, PencilRulerIcon, UsersIcon, ShapesIcon,
  UserCogIcon, WandSparklesIcon,
  FolderGitIcon, LogOutIcon
} from 'lucide-react'
import { useSettingsStore } from '@/stores/settings'
import { useAuthStore } from '@/stores/state'
import { navigationService } from '@/services/navigation'
import { setUiMode } from '@/lib/uiMode'
import WorkspaceSelector from '@/components/WorkspaceSelector'
import './next.css'

import Dashboard from '@/features/next/pages/Dashboard'
import Decisions from '@/features/next/pages/Decisions'
import AdminUsers from '@/features/next/pages/AdminUsers'
import GraphQualityNext from '@/features/next/pages/GraphQualityNext'
import OntologyNext from '@/features/next/pages/OntologyNext'
import RulesNext from '@/features/next/pages/RulesNext'
import Studio from '@/features/next/pages/Studio'
import Wizard from '@/features/next/pages/Wizard'
import DocumentsNext from '@/features/next/pages/DocumentsNext'
import ChunkInspector from '@/features/next/pages/ChunkInspector'
import WeaveBoard from '@/features/next/pages/WeaveBoard'
import { DiagramsPage } from '@/features/next/pages/Diagrams'
import GraphViewer from '@/features/GraphViewer'
import RetrievalTesting from '@/features/RetrievalTesting'
import GetStarted from '@/features/GetStarted'
import ApiSite from '@/features/ApiSite'
import Features from '@/features/next/pages/Features'
import Learnings from '@/features/next/pages/Learnings'
import Projects from '@/features/next/pages/Projects'

type ViewId =
  | 'weave' | 'features' | 'learnings' | 'projects'
  | 'dashboard' | 'decisions' | 'documents' | 'graph' | 'retrieval' | 'chunks'
  | 'rules' | 'ontology' | 'quality' | 'studio' | 'wizard' | 'diagrams'
  | 'getstarted' | 'api'
  | 'users'

type NavItem = {
  id: ViewId
  label: string
  icon: React.ComponentType<{ className?: string }>
  group: string
  flush?: boolean            // full-bleed feature (no padding, no page scroll)
  badge?: { text: string; warn?: boolean }
}

// Weave first (CR-001).
//
// The product was **item 13 of 16** in its own navigation, under a group called
// "Team", while the first twelve entries spoke the vocabulary of the engine it
// was forked from. That is not a build failure — nothing in the BLOG, RFC, DRP
// or work plan ever asked for Weave to be primary, so no review could have
// caught it. This is the requirement arriving late, not a defect being fixed.
//
// **Nothing is deleted.** Every engine surface keeps its label, its route and
// its `ViewId`; they move into `Knowledge` and `Governance` below the Weave
// group. Demoting a screen in a menu is reversible and needs no decision;
// deleting one is a separate call with its own `D-NN` (CR-001 §3).
const NAV: NavItem[] = [
  { id: 'weave', label: 'Work', icon: UsersIcon, group: 'Weave' },
  { id: 'features', label: 'Features', icon: SparklesIcon, group: 'Weave' },
  { id: 'learnings', label: 'Learnings', icon: ScaleIcon, group: 'Weave' },
  { id: 'projects', label: 'Projects', icon: FolderGitIcon, group: 'Weave' },
  { id: 'wizard', label: 'Team vocabulary', icon: WandSparklesIcon, group: 'Weave' },

  { id: 'ontology', label: 'Ontology', icon: BoxesIcon, group: 'Governance' },
  { id: 'rules', label: 'Rules', icon: GavelIcon, group: 'Governance' },
  { id: 'studio', label: 'History', icon: PencilRulerIcon, group: 'Governance' },
  { id: 'users', label: 'Users', icon: UserCogIcon, group: 'Governance' },

  { id: 'documents', label: 'Documents', icon: FilesIcon, group: 'Knowledge', flush: true },
  { id: 'graph', label: 'Knowledge Graph', icon: NetworkIcon, group: 'Knowledge', flush: true },
  { id: 'retrieval', label: 'Retrieval', icon: SearchIcon, group: 'Knowledge', flush: true },
  { id: 'chunks', label: 'Chunks', icon: LayersIcon, group: 'Knowledge' },
  { id: 'diagrams', label: 'Diagrams', icon: ShapesIcon, group: 'Knowledge', flush: true },
  { id: 'quality', label: 'Graph Quality', icon: SparklesIcon, group: 'Knowledge' },

  { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboardIcon, group: 'Setup' },
  { id: 'decisions', label: 'Decisions', icon: ScaleIcon, group: 'Setup' },
  { id: 'getstarted', label: 'Get Started', icon: RocketIcon, group: 'Setup' },
  { id: 'api', label: 'API', icon: Code2Icon, group: 'Setup' }
]

function ThemeToggle() {
  const theme = useSettingsStore.use.theme()
  const setTheme = useSettingsStore.use.setTheme()
  const isDark =
    theme === 'dark' ||
    (theme === 'system' && typeof window !== 'undefined' &&
      window.matchMedia('(prefers-color-scheme: dark)').matches)
  return (
    <button
      className="iconbtn"
      title={isDark ? 'Switch to light' : 'Switch to dark'}
      onClick={() => setTheme(isDark ? 'light' : 'dark')}
    >
      {isDark ? <SunIcon className="" /> : <MoonIcon className="" />}
    </button>
  )
}

// Embedded classic features gate their data-loading on the settings-store
// currentTab (e.g. DocumentManager only fetches when currentTab === 'documents').
// Mirror the shell's view into currentTab so those features load correctly.
const TAB_OF: Partial<Record<ViewId, string>> = {
  documents: 'documents', graph: 'knowledge-graph', retrieval: 'retrieval',
  rules: 'rules', ontology: 'ontology', getstarted: 'get-started', api: 'api'
}

export default function AppShell() {
  // The landing view answers a Weave question, not a document one (M7 gate).
  const [view, setView] = useState<ViewId>('weave')
  const [search, setSearch] = useState('')
  const workspace = useSettingsStore.use.workspace()
  const setCurrentTab = useSettingsStore.use.setCurrentTab()

  // Who is signed in, and how to stop being them (U11 · U12 · U13).
  //
  // `role` comes from the **token**, which is what the server enforces against
  // (D5) — not from the user record. Those can differ, and when they do it is
  // the whole of U1: a role changed in Admin ▸ Users is saved and not yet in
  // force. Showing the token's role makes that visible rather than baffling.
  const { username, role, isAuthenticated, logout } = useAuthStore()

  const initials = (username ?? '?')
    .split(/[\s._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]!.toUpperCase())
    .join('') || '?'

  const signOut = () => {
    // Clear the session, then hand over to the same navigation the classic
    // header used — one logout, not a second implementation of it (R10).
    logout()
    navigationService.navigateToLogin()
  }

  const go = (v: ViewId) => {
    setView(v)
    const tab = TAB_OF[v]
    if (tab) setCurrentTab(tab as never)
  }

  const groups = useMemo(() => {
    const order = ['Weave', 'Governance', 'Knowledge', 'Setup']
    return order.map((g) => ({ group: g, items: NAV.filter((n) => n.group === g) }))
  }, [])

  const active = NAV.find((n) => n.id === view)!

  const content = () => {
    switch (view) {
    case 'dashboard': return <Dashboard onNavigate={go} />
    case 'decisions': return <Decisions />
    case 'documents': return <DocumentsNext />
    case 'graph': return <GraphViewer />
    case 'retrieval': return <RetrievalTesting />
    case 'chunks': return <ChunkInspector />
    case 'rules': return <RulesNext />
    case 'ontology': return <OntologyNext />
    case 'quality': return <GraphQualityNext />
    case 'studio': return <Studio />
    case 'wizard': return <Wizard />
    case 'diagrams': return <DiagramsPage />
    case 'weave': return <WeaveBoard />
    case 'features': return <Features />
    case 'learnings': return <Learnings />
    case 'projects': return <Projects />
    case 'users': return <AdminUsers />
    case 'getstarted': return <GetStarted />
    case 'api': return <ApiSite />
    default: return null
    }
  }

  return (
    <div className="cgnext">
      <div className="app">
        {/* sidebar */}
        <aside className="side">
          <div className="brand">
            <svg className="mark" viewBox="0 0 32 32" fill="none" aria-hidden="true">
              <circle cx="8" cy="9" r="3.4" fill="var(--accent)" />
              <circle cx="24" cy="7" r="3" fill="var(--comm)" />
              <circle cx="23" cy="24" r="3.4" fill="var(--good)" />
              <circle cx="9" cy="23" r="2.6" fill="var(--warn)" />
              <path d="M8 9L24 7M24 7L23 24M23 24L9 23M9 23L8 9M8 9L23 24" stroke="var(--line2)" strokeWidth="1.4" />
            </svg>
            <div className="name">Weave<small>Governed knowledge</small></div>
          </div>
          <nav className="nav">
            {groups.map(({ group, items }) => (
              <div key={group}>
                <div className="grp">{group}</div>
                {items.map((n) => {
                  const Icon = n.icon
                  return (
                    <button
                      key={n.id}
                      className={'navitem' + (view === n.id ? ' active' : '')}
                      onClick={() => go(n.id)}
                    >
                      <Icon className="" />
                      {n.label}
                      {n.badge && (
                        <span className={'badge' + (n.badge.warn ? ' warn' : '')}>{n.badge.text}</span>
                      )}
                    </button>
                  )
                })}
              </div>
            ))}
          </nav>
          {/* The session block (U11 · U12 · U13, one change).
              `AppShell` is the whole app in `next` mode — `SiteHeader` is never
              rendered — and it owned the only logout and the only display of who
              you are. So this shell showed a hardcoded `CG` avatar (the parent's
              initials, U13) beside the word "Weave", and offered no way to sign
              out at all.
              That is not three cosmetic bugs. Without logout a token cannot be
              re-minted, so a user who changes their own role in Admin ▸ Users
              saves it, keeps the old role in force, and has no way to pick up the
              new one — the deadlock in U1. Identity belonged here from the
              start. */}
          <div className="foot">
            <div className="avatar" title={username ?? 'not signed in'}>
              {initials}
            </div>
            <div className="who" style={{ flex: 1, minWidth: 0 }}>
              {username ?? 'Not signed in'}
              <small>
                {role ? `${role} · ` : ''}Workspace · {workspace}
              </small>
            </div>
            <button className="iconbtn" title="Back to classic UI" onClick={() => setUiMode('classic')}>
              <PanelLeftIcon className="" />
            </button>
            {isAuthenticated && (
              <button className="iconbtn" title={`Sign out (${username ?? ''})`} onClick={signOut}>
                <LogOutIcon className="" />
              </button>
            )}
          </div>
        </aside>

        {/* main */}
        <div className="cgmain">
          <div className="topbar">
            <div className="wswrap"><WorkspaceSelector /></div>
            <form
              className="search"
              onSubmit={(e) => { e.preventDefault(); if (search.trim()) go('retrieval') }}
            >
              <SearchIcon className="" />
              <input
                className="searchinput"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search — press Enter to open Retrieval…"
              />
            </form>
            <div className="top-actions">
              <button className="iconbtn" title="Notifications"><BellIcon className="" /></button>
              <ThemeToggle />
            </div>
          </div>

          <div className={'content' + (active.flush ? ' flush' : '')}>
            {content()}
          </div>
        </div>
      </div>
    </div>
  )
}
