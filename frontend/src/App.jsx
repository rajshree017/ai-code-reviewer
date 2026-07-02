import React, { useState, useEffect } from 'react'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

const SEVERITY_META = {
  critical: { emoji: '🔴', label: 'Critical' },
  major: { emoji: '🟠', label: 'Major' },
  minor: { emoji: '🟡', label: 'Minor' },
}

const VERDICT_META = {
  approve: { label: 'Approve', className: 'verdict-approve' },
  request_changes: { label: 'Request Changes', className: 'verdict-request-changes' },
  comment: { label: 'Comment', className: 'verdict-comment' },
}

export default function App() {
  const [sessionId, setSessionId] = useState(null)
  const [repos, setRepos] = useState([])
  const [selectedRepo, setSelectedRepo] = useState(null)
  const [files, setFiles] = useState([])
  const [currentPath, setCurrentPath] = useState('')
  const [pulls, setPulls] = useState([])
  const [mode, setMode] = useState('files') // 'files' | 'pulls'
  const [review, setReview] = useState(null)
  const [reviewTarget, setReviewTarget] = useState(null)
  const [reviewKind, setReviewKind] = useState(null) // 'file' | 'pr'
  const [activePR, setActivePR] = useState(null)
  const [streamingText, setStreamingText] = useState('')
  const [loading, setLoading] = useState(false)
  const [posting, setPosting] = useState(false)
  const [postResult, setPostResult] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const sid = params.get('session_id')
    if (sid) {
      setSessionId(sid)
      window.history.replaceState({}, '', window.location.pathname)
    }
  }, [])

  useEffect(() => {
    if (sessionId) fetchRepos()
  }, [sessionId])

  const fetchRepos = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${API_URL}/repos?session_id=${sessionId}`)
      if (!res.ok) throw new Error('Failed to load repos')
      setRepos(await res.json())
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const openRepo = async (repo) => {
    setSelectedRepo(repo)
    setCurrentPath('')
    setReview(null)
    setPostResult(null)
    await loadFiles(repo, '')
  }

  const loadFiles = async (repo, path) => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(
        `${API_URL}/repos/${repo.full_name}/files?session_id=${sessionId}&path=${encodeURIComponent(path)}`
      )
      if (!res.ok) throw new Error('Failed to load files')
      setFiles(await res.json())
      setCurrentPath(path)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const loadPulls = async (repo) => {
    setLoading(true)
    setError(null)
    setMode('pulls')
    try {
      const res = await fetch(`${API_URL}/repos/${repo.full_name}/pulls?session_id=${sessionId}`)
      if (!res.ok) throw new Error('Failed to load pull requests')
      setPulls(await res.json())
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const handleFileClick = (item) => {
    if (item.type === 'dir') {
      loadFiles(selectedRepo, item.path)
    } else {
      reviewFile(item.path)
    }
  }

  const streamThenFetch = async (streamUrl, finalUrl, body, target, kind) => {
    setLoading(true)
    setError(null)
    setReview(null)
    setPostResult(null)
    setStreamingText('')
    setReviewTarget(target)
    setReviewKind(kind)

    try {
      // 1. Stream raw text live for the "typing" effect
      const streamRes = await fetch(streamUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (streamRes.ok && streamRes.body) {
        const reader = streamRes.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n\n')
          buffer = lines.pop()
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue
            const event = JSON.parse(line.slice(6))
            if (event.type === 'token') {
              setStreamingText((prev) => prev + event.text)
            } else if (event.type === 'error') {
              throw new Error(event.message)
            }
          }
        }
      }

      // 2. Fetch the final structured (parsed) result for severity badges + verdict
      const finalRes = await fetch(finalUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!finalRes.ok) throw new Error('Review failed')
      const data = await finalRes.json()
      setReview(data.review)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const reviewFile = (path) => {
    const [owner, repo] = selectedRepo.full_name.split('/')
    const body = { session_id: sessionId, owner, repo, path }
    streamThenFetch(`${API_URL}/review/file/stream`, `${API_URL}/review/file`, body, path, 'file')
  }

  const reviewPR = (prNumber) => {
    const [owner, repo] = selectedRepo.full_name.split('/')
    const body = { session_id: sessionId, owner, repo, pr_number: prNumber }
    setActivePR(prNumber)
    streamThenFetch(`${API_URL}/review/pr/stream`, `${API_URL}/review/pr`, body, `PR #${prNumber}`, 'pr')
  }

  const postToGithub = async () => {
    if (!activePR) return
    setPosting(true)
    setError(null)
    const [owner, repo] = selectedRepo.full_name.split('/')
    try {
      const res = await fetch(`${API_URL}/review/pr/post-to-github`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, owner, repo, pr_number: activePR }),
      })
      if (!res.ok) throw new Error('Failed to post review to GitHub')
      const data = await res.json()
      setPostResult(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setPosting(false)
    }
  }

  const goUpPath = () => {
    const parts = currentPath.split('/').filter(Boolean)
    parts.pop()
    loadFiles(selectedRepo, parts.join('/'))
  }

  if (!sessionId) {
    return (
      <div className="login-screen">
        <div className="login-card">
          <div className="logo">{'>_'} Codespect</div>
          <p className="login-tagline">AI-powered code review for your GitHub repos.</p>
          <a href={`${API_URL}/auth/login`} className="btn-github">Connect with GitHub</a>
        </div>
      </div>
    )
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="logo">{'>_'} Codespect</div>

        {!selectedRepo && (
          <div className="repo-list">
            <div className="sidebar-heading">Repositories</div>
            {loading && <div className="loading-text">Loading…</div>}
            {repos.map((r) => (
              <button key={r.full_name} className="repo-item" onClick={() => openRepo(r)}>
                {r.full_name}
                {r.private && <span className="badge-private">private</span>}
              </button>
            ))}
          </div>
        )}

        {selectedRepo && (
          <div className="repo-detail">
            <button className="back-btn" onClick={() => { setSelectedRepo(null); setMode('files'); setReview(null) }}>
              ← All repos
            </button>
            <div className="sidebar-heading">{selectedRepo.full_name}</div>

            <div className="mode-tabs">
              <button className={`tab ${mode === 'files' ? 'active' : ''}`} onClick={() => { setMode('files'); loadFiles(selectedRepo, '') }}>
                Files
              </button>
              <button className={`tab ${mode === 'pulls' ? 'active' : ''}`} onClick={() => loadPulls(selectedRepo)}>
                Pull Requests
              </button>
            </div>

            {mode === 'files' && (
              <div className="file-list">
                {currentPath && <button className="file-item dir" onClick={goUpPath}>.. (up)</button>}
                {files.map((item) => (
                  <button key={item.path} className={`file-item ${item.type}`} onClick={() => handleFileClick(item)}>
                    {item.type === 'dir' ? '📁' : '📄'} {item.name}
                  </button>
                ))}
              </div>
            )}

            {mode === 'pulls' && (
              <div className="pr-list">
                {pulls.length === 0 && <div className="loading-text">No open PRs</div>}
                {pulls.map((pr) => (
                  <button key={pr.number} className="pr-item" onClick={() => reviewPR(pr.number)}>
                    #{pr.number} {pr.title}
                    <span className="pr-author">by {pr.user}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </aside>

      <main className="main-panel">
        {error && <div className="error-banner">{error}</div>}

        {!review && !loading && !streamingText && (
          <div className="empty-main">
            <div className="empty-icon">{'{ }'}</div>
            <p>Select a file or pull request to review.</p>
          </div>
        )}

        {loading && !review && (
          <div className="streaming-output">
            <div className="review-header">
              <span className="spinner-inline" /> Reviewing {reviewTarget}…
            </div>
            <pre className="streaming-text">{streamingText}<span className="cursor-blink">▍</span></pre>
          </div>
        )}

        {review && (
          <div className="review-output">
            <div className="review-header-row">
              <div className="review-header">Review: {reviewTarget}</div>
              {VERDICT_META[review.verdict] && (
                <span className={`verdict-badge ${VERDICT_META[review.verdict].className}`}>
                  {VERDICT_META[review.verdict].label}
                </span>
              )}
            </div>

            <p className="review-summary">{review.summary}</p>

            {review.issues && review.issues.length > 0 && (
              <div className="issues-list">
                {review.issues.map((issue, i) => (
                  <div key={i} className={`issue-card severity-${issue.severity}`}>
                    <div className="issue-title">
                      <span className="issue-severity-tag">
                        {SEVERITY_META[issue.severity]?.emoji} {SEVERITY_META[issue.severity]?.label}
                      </span>
                      {issue.title}
                      {issue.file && (
                        <span className="issue-location">
                          {issue.file}{issue.line ? `:${issue.line}` : ''}
                        </span>
                      )}
                    </div>
                    <p className="issue-description">{issue.description}</p>
                    {issue.suggestion && (
                      <p className="issue-suggestion"><strong>Fix:</strong> {issue.suggestion}</p>
                    )}
                  </div>
                ))}
              </div>
            )}

            {review.issues && review.issues.length === 0 && (
              <div className="no-issues">✅ No issues found — looks good!</div>
            )}

            {reviewKind === 'pr' && (
              <div className="post-to-github-section">
                <button className="btn-post-github" onClick={postToGithub} disabled={posting}>
                  {posting ? 'Posting…' : '🚀 Post review to GitHub PR'}
                </button>
                <p className="post-hint">
                  Posts inline comments on the flagged lines + a pass/fail status check on the PR.
                </p>
                {postResult && (
                  <div className="post-result">
                    {postResult.github_review_posted ? '✅' : '⚠️'} Review {postResult.github_review_posted ? 'posted' : 'failed to post'} ·{' '}
                    {postResult.github_status_posted ? '✅' : '⚠️'} Status check {postResult.github_status_posted ? 'posted' : 'failed to post'}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  )
}
