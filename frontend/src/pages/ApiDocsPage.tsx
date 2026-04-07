import * as Tabs from '@radix-ui/react-tabs'
import { FileCode2, Globe2 } from 'lucide-react'
import { useEffect, useState } from 'react'

import { fetchOpenApiSpec } from '../lib/api'

export function ApiDocsPage() {
  const [spec, setSpec] = useState<Record<string, unknown> | null>(null)

  useEffect(() => {
    fetchOpenApiSpec().then(setSpec).catch(() => setSpec(null))
  }, [])

  return (
    <div className="page-stack">

      <section className="panel-surface grid min-h-[760px] gap-4 p-5">
        <Tabs.Root defaultValue="swagger" className="grid min-h-0 grid-rows-[auto,1fr] gap-4">
          <div className="toolbar-strip">
            <Tabs.List className="flex gap-2">
              <Tabs.Trigger value="swagger" className="tab-trigger">
                <Globe2 className="h-4 w-4" />
                Swagger UI
              </Tabs.Trigger>
              <Tabs.Trigger value="openapi" className="tab-trigger">
                <FileCode2 className="h-4 w-4" />
                OpenAPI JSON
              </Tabs.Trigger>
            </Tabs.List>
            <div className="text-sm text-[var(--text-secondary)]">同域挂载，无需单独前端生产服务器</div>
          </div>

          <Tabs.Content value="swagger" className="min-h-0">
            <div className="subtle-surface h-[680px] overflow-hidden p-2">
              <iframe title="Swagger UI" src="/docs" className="h-full w-full rounded-[18px] border border-[var(--line)] bg-white" />
            </div>
          </Tabs.Content>

          <Tabs.Content value="openapi" className="min-h-0">
            <pre className="subtle-surface h-[680px] overflow-auto px-4 py-4 font-mono text-xs leading-6 text-[var(--text-secondary)]">
              {spec ? JSON.stringify(spec, null, 2) : 'Loading /openapi.json ...'}
            </pre>
          </Tabs.Content>
        </Tabs.Root>
      </section>
    </div>
  )
}

