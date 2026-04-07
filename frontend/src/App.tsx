import * as Dialog from '@radix-ui/react-dialog'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import * as Tooltip from '@radix-ui/react-tooltip'
import { BookText, BotMessageSquare, Languages, MoonStar, Network, SunMedium, TerminalSquare, Zap } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Navigate, NavLink, Route, Routes, useLocation } from 'react-router-dom'

import { ApiDocsPage } from './pages/ApiDocsPage'
import { DocumentsPage } from './pages/DocumentsPage'
import { KnowledgeGraphPage } from './pages/KnowledgeGraphPage'
import { RetrievalPage } from './pages/RetrievalPage'
import { t } from './lib/translations'
import { useAppStore } from './store/app-store'

const navItems = [
  { to: '/documents', labelKey: 'documents', icon: BookText },
  { to: '/knowledge-graph', labelKey: 'knowledgeGraph', icon: Network },
  { to: '/retrieval', labelKey: 'retrieval', icon: BotMessageSquare },
  { to: '/api', labelKey: 'api', icon: TerminalSquare },
] as const

export default function App() {
  const location = useLocation()
  const theme = useAppStore((state) => state.theme)
  const setTheme = useAppStore((state) => state.setTheme)
  const locale = useAppStore((state) => state.locale)
  const token = useAppStore((state) => state.token)
  const setToken = useAppStore((state) => state.setToken)
  const setLocale = useAppStore((state) => state.setLocale)
  const [tokenDialogOpen, setTokenDialogOpen] = useState(false)
  const [tokenDraft, setTokenDraft] = useState(token)
  const isKnowledgeGraphRoute = location.pathname.startsWith('/knowledge-graph')

  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])

  useEffect(() => {
    if (tokenDialogOpen) {
      setTokenDraft(token)
    }
  }, [token, tokenDialogOpen])

  return (
    <Tooltip.Provider delayDuration={120}>
      <div className="app-shell">
        <header className="shell-header">
          <div className="header-brand">
            <div className="brand-badge">
              <Zap className="h-4 w-4" />
            </div>
            <p className="brand-title">NormaGraph</p>
          </div>

          <nav className="header-nav">
            {navItems.map((item) => (
              <Tooltip.Root key={item.to}>
                <Tooltip.Trigger asChild>
                  <NavLink to={item.to} className={({ isActive }) => `header-nav-link ${isActive ? 'is-active' : ''}`}>
                    <span className="header-nav-link-content">
                      <item.icon className="h-4 w-4 shrink-0" />
                      <span>{t(locale, item.labelKey)}</span>
                    </span>
                  </NavLink>
                </Tooltip.Trigger>
                <Tooltip.Portal>
                  <Tooltip.Content side="bottom" sideOffset={10} className="tooltip-content">
                    {t(locale, item.labelKey)}
                  </Tooltip.Content>
                </Tooltip.Portal>
              </Tooltip.Root>
            ))}
          </nav>

          <div className="header-actions">
            <span className="header-meta hidden xl:inline-flex">single-service /webui</span>

            <DropdownMenu.Root>
              <DropdownMenu.Trigger className="surface-icon-button" aria-label={t(locale, 'theme')}>
                {theme === 'dark' ? <MoonStar className="h-4 w-4" /> : <SunMedium className="h-4 w-4" />}
              </DropdownMenu.Trigger>
              <DropdownMenu.Portal>
                <DropdownMenu.Content sideOffset={8} className="menu-surface">
                  <DropdownMenu.Item onSelect={() => setTheme('light')} className="menu-item">Light</DropdownMenu.Item>
                  <DropdownMenu.Item onSelect={() => setTheme('dark')} className="menu-item">Dark</DropdownMenu.Item>
                </DropdownMenu.Content>
              </DropdownMenu.Portal>
            </DropdownMenu.Root>

            <DropdownMenu.Root>
              <DropdownMenu.Trigger className="surface-button compact-button">
                <Languages className="h-4 w-4" />
                <span>{locale}</span>
              </DropdownMenu.Trigger>
              <DropdownMenu.Portal>
                <DropdownMenu.Content sideOffset={8} className="menu-surface">
                  <DropdownMenu.Item onSelect={() => setLocale('zh-CN')} className="menu-item">zh-CN</DropdownMenu.Item>
                  <DropdownMenu.Item onSelect={() => setLocale('en-US')} className="menu-item">en-US</DropdownMenu.Item>
                </DropdownMenu.Content>
              </DropdownMenu.Portal>
            </DropdownMenu.Root>

            <Dialog.Root open={tokenDialogOpen} onOpenChange={setTokenDialogOpen}>
              <Dialog.Trigger className="surface-button accent-button compact-button">{t(locale, 'token')}</Dialog.Trigger>
              <Dialog.Portal>
                <Dialog.Overlay className="fixed inset-0 z-40 bg-slate-950/14 backdrop-blur-sm" />
                <Dialog.Content className="dialog-surface fixed left-1/2 top-1/2 z-50 w-[min(520px,92vw)] -translate-x-1/2 -translate-y-1/2 p-6">
                  <Dialog.Title className="text-lg font-semibold text-[var(--text-primary)]">API Token</Dialog.Title>
                  <Dialog.Description className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">
                    Axios client 会自动将这里保存的 token 注入到 Authorization Header。
                  </Dialog.Description>
                  <textarea
                    value={tokenDraft}
                    onChange={(event) => setTokenDraft(event.target.value)}
                    rows={6}
                    className="control-textarea mt-4"
                  />
                  <div className="mt-5 flex justify-end gap-2">
                    <button type="button" onClick={() => setTokenDialogOpen(false)} className="surface-button compact-button">
                      取消
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setToken(tokenDraft.trim())
                        setTokenDialogOpen(false)
                      }}
                      className="surface-button primary-button compact-button"
                    >
                      保存
                    </button>
                  </div>
                </Dialog.Content>
              </Dialog.Portal>
            </Dialog.Root>
          </div>
        </header>

        <main className={isKnowledgeGraphRoute ? 'content-main is-graph' : 'content-main'}>
          <Routes>
            <Route path="/" element={<Navigate to="/documents" replace />} />
            <Route path="/documents" element={<DocumentsPage />} />
            <Route path="/knowledge-graph" element={<KnowledgeGraphPage />} />
            <Route path="/retrieval" element={<RetrievalPage />} />
            <Route path="/api" element={<ApiDocsPage />} />
          </Routes>
        </main>
      </div>
    </Tooltip.Provider>
  )
}



