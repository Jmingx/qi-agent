import { useEffect, useRef, useState, type FormEvent } from 'react'

type LoginPageProps = {
  error?: string | null
  loading: boolean
  localHint: boolean
  onSubmit: (token: string) => void
}

export function LoginPage({ error, loading, localHint, onSubmit }: LoginPageProps) {
  const [token, setToken] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  function handleSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    onSubmit(token)
  }

  return (
    <div className="login-shell">
      <div className="login-orb login-orb-a" />
      <div className="login-orb login-orb-b" />
      <main className="login-card">
        <div className="login-brand">
          <div className="logo">qi</div>
          <div>
            <h1>qi-agent Web</h1>
            <p>先校验 web token，再进入对话界面。</p>
          </div>
        </div>
        <form className="login-form" onSubmit={handleSubmit}>
          <label className="login-label" htmlFor="qi-web-token">
            Web token
          </label>
          <input
            id="qi-web-token"
            ref={inputRef}
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder={localHint ? '本地回环可留空' : '输入 ~/.qi-agent/web_token 中的 token'}
            autoComplete="off"
            spellCheck={false}
          />
          <button type="submit" className="login-submit" disabled={loading}>
            {loading ? '连接中...' : '进入'}
          </button>
        </form>
        {error && <p className="login-error">{error}</p>}
        <div className="login-hints">
          <span>HTTP 静态页公开</span>
          <span>WS 和 RPC 受 token 保护</span>
          <span>{localHint ? '127.0.0.1 可直接进入' : '非回环必须带 token'}</span>
        </div>
      </main>
    </div>
  )
}
