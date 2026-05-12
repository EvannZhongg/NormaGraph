import { useEffect, useRef } from 'react'


type MathJaxRuntime = {
  startup?: {
    promise?: Promise<unknown>
  }
  typesetPromise?: (elements?: HTMLElement[]) => Promise<unknown>
  typesetClear?: (elements?: HTMLElement[]) => void
}

declare global {
  interface Window {
    MathJax?: MathJaxRuntime & Record<string, unknown>
  }
}

const MATHJAX_SCRIPT_ID = 'mathjax-tex-chtml'
const MATHJAX_SRC = 'https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js'

let mathJaxLoadPromise: Promise<MathJaxRuntime> | null = null
let mathJaxTypesetQueue: Promise<unknown> = Promise.resolve()

function containsMathSyntax(text: string) {
  return text.includes('\\(') || text.includes('\\[')
}

function loadMathJax() {
  if (typeof window === 'undefined' || typeof document === 'undefined') {
    return Promise.reject(new Error('MathJax is only available in the browser.'))
  }
  if (window.MathJax?.typesetPromise) {
    return Promise.resolve(window.MathJax)
  }
  if (mathJaxLoadPromise) {
    return mathJaxLoadPromise
  }

  mathJaxLoadPromise = new Promise<MathJaxRuntime>((resolve, reject) => {
    const existingScript = document.getElementById(MATHJAX_SCRIPT_ID) as HTMLScriptElement | null
    if (existingScript) {
      existingScript.addEventListener('load', () => resolve(window.MathJax ?? {}), { once: true })
      existingScript.addEventListener('error', () => reject(new Error('Failed to load MathJax.')), { once: true })
      return
    }

    window.MathJax = {
      ...(window.MathJax ?? {}),
      startup: {
        ...((window.MathJax?.startup as Record<string, unknown> | undefined) ?? {}),
        typeset: false,
      },
      tex: {
        inlineMath: [['\\(', '\\)']],
        displayMath: [['\\[', '\\]']],
      },
      options: {
        skipHtmlTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code'],
      },
    }

    const script = document.createElement('script')
    script.id = MATHJAX_SCRIPT_ID
    script.async = true
    script.src = MATHJAX_SRC
    script.addEventListener(
      'load',
      () => {
        const startupPromise = window.MathJax?.startup?.promise
        if (startupPromise) {
          void startupPromise.then(() => resolve(window.MathJax ?? {})).catch(reject)
          return
        }
        resolve(window.MathJax ?? {})
      },
      { once: true },
    )
    script.addEventListener('error', () => reject(new Error('Failed to load MathJax.')), { once: true })
    document.head.appendChild(script)
  })

  return mathJaxLoadPromise
}

function queueMathTypeset(element: HTMLElement) {
  mathJaxTypesetQueue = mathJaxTypesetQueue
    .then(async () => {
      const mathJax = await loadMathJax()
      mathJax.typesetClear?.([element])
      await mathJax.typesetPromise?.([element])
    })
    .catch(() => undefined)

  return mathJaxTypesetQueue
}

type MathTextProps = {
  text: string
  className?: string
}

export function MathText({ text, className }: MathTextProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const mergedClassName = className ? `math-text ${className}` : 'math-text'

  useEffect(() => {
    const element = containerRef.current
    if (!element) {
      return
    }

    element.textContent = text
    if (!containsMathSyntax(text)) {
      return
    }

    let cancelled = false
    void queueMathTypeset(element).then(() => {
      if (cancelled || !containerRef.current) {
        return
      }
    })

    return () => {
      cancelled = true
      if (containerRef.current) {
        window.MathJax?.typesetClear?.([containerRef.current])
      }
    }
  }, [text])

  return <div ref={containerRef} className={mergedClassName} />
}
