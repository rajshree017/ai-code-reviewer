"""
AI Code Reviewer - FastAPI Backend
Connects to GitHub via OAuth, lets the user pick a repo + file or PR,
and asks Claude to review the code.

Wow features:
- Structured severity-scored reviews (critical/major/minor) instead of plain text
- Posts reviews back to GitHub as real inline PR comments + a pass/fail commit status check
- Optional webhook: automatically reviews every new/updated PR (real CI-bot behavior)
- Streaming review text via Server-Sent Events
"""
import hashlib
import hmac
import json
import os
import secrets
from typing import Optional, List

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel

from reviewer import review_code, review_diff, stream_review_text, overall_status, severity_counts
from github_client import post_pr_review_with_comments, post_commit_status, get_pr_head_sha

app = FastAPI(title="AI Code Reviewer API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restrict to your frontend URL in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET")
GITHUB_REDIRECT_URI = os.environ.get("GITHUB_REDIRECT_URI", "http://localhost:8000/auth/callback")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173")

# Used to verify that incoming /webhook/github requests really came from GitHub.
# Set this to the same secret you configure in the GitHub webhook settings.
GITHUB_WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET")

# A token used by the webhook flow (since GitHub doesn't carry our OAuth session id).
# Generate a Personal Access Token with 'repo' scope and set it here, OR rely on
# per-user OAuth tokens stored after they log in via the app at least once.
WEBHOOK_GITHUB_TOKEN = os.environ.get("WEBHOOK_GITHUB_TOKEN")

# In-memory token store keyed by a random session id (fine for a single-user demo;
# swap for a real DB/session store if you have multiple users).
SESSIONS = {}


# ---------- GitHub OAuth ----------

@app.get("/auth/login")
def github_login():
    """Redirect the user to GitHub's OAuth consent screen."""
    if not GITHUB_CLIENT_ID:
        raise HTTPException(status_code=500, detail="GITHUB_CLIENT_ID not configured on server")

    state = secrets.token_urlsafe(16)
    SESSIONS[state] = {"pending": True}

    github_auth_url = (
        "https://github.com/login/oauth/authorize"
        f"?client_id={GITHUB_CLIENT_ID}"
        f"&redirect_uri={GITHUB_REDIRECT_URI}"
        "&scope=repo"
        f"&state={state}"
    )
    return RedirectResponse(github_auth_url)


@app.get("/auth/callback")
async def github_callback(code: str, state: str):
    """GitHub redirects here after the user approves access."""
    if state not in SESSIONS:
        raise HTTPException(status_code=400, detail="Invalid or expired state")

    async with httpx.AsyncClient() as client:
        token_res = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": GITHUB_REDIRECT_URI,
            },
        )
        token_data = token_res.json()

    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail=f"GitHub auth failed: {token_data}")

    # Store the token under the session id (the "state" value acts as our session key)
    SESSIONS[state] = {"access_token": access_token, "pending": False}

    # Send the user back to the frontend with the session id in the URL
    return RedirectResponse(f"{FRONTEND_URL}?session_id={state}")


def get_github_token(session_id: str) -> str:
    session = SESSIONS.get(session_id)
    if not session or session.get("pending") or not session.get("access_token"):
        raise HTTPException(status_code=401, detail="Not authenticated. Please log in with GitHub first.")
    return session["access_token"]


# ---------- GitHub Data Endpoints ----------

@app.get("/repos")
async def list_repos(session_id: str = Query(...)):
    """List the authenticated user's repositories."""
    token = get_github_token(session_id)

    async with httpx.AsyncClient() as client:
        res = await client.get(
            "https://api.github.com/user/repos",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            params={"sort": "updated", "per_page": 50},
        )
    if res.status_code != 200:
        raise HTTPException(status_code=res.status_code, detail="Failed to fetch repos from GitHub")

    repos = res.json()
    return [{"full_name": r["full_name"], "private": r["private"], "default_branch": r["default_branch"]} for r in repos]


@app.get("/repos/{owner}/{repo}/files")
async def list_files(owner: str, repo: str, session_id: str = Query(...), path: str = ""):
    """List files/folders in a repo path (browse the repo tree)."""
    token = get_github_token(session_id)

    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/contents/{path}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
    if res.status_code != 200:
        raise HTTPException(status_code=res.status_code, detail="Failed to list repo contents")

    items = res.json()
    if isinstance(items, dict):  # single file was requested, not a folder
        items = [items]

    return [{"name": i["name"], "path": i["path"], "type": i["type"]} for i in items]


@app.get("/repos/{owner}/{repo}/pulls")
async def list_pull_requests(owner: str, repo: str, session_id: str = Query(...)):
    """List open pull requests for a repo."""
    token = get_github_token(session_id)

    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/pulls",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            params={"state": "open", "per_page": 30},
        )
    if res.status_code != 200:
        raise HTTPException(status_code=res.status_code, detail="Failed to fetch pull requests")

    prs = res.json()
    return [{"number": p["number"], "title": p["title"], "user": p["user"]["login"]} for p in prs]


# ---------- Review Endpoints ----------

class FileReviewRequest(BaseModel):
    session_id: str
    owner: str
    repo: str
    path: str


class PRReviewRequest(BaseModel):
    session_id: str
    owner: str
    repo: str
    pr_number: int


@app.post("/review/file")
async def review_file(request: FileReviewRequest):
    """Fetch a single file's content from GitHub and review it with Claude.
    Returns a structured review: {summary, verdict, issues[]}."""
    token = get_github_token(request.session_id)

    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"https://api.github.com/repos/{request.owner}/{request.repo}/contents/{request.path}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.raw+json"},
        )
    if res.status_code != 200:
        raise HTTPException(status_code=res.status_code, detail="Failed to fetch file from GitHub")

    code_content = res.text
    review = review_code(code_content, filename=request.path)
    return {
        "path": request.path,
        "review": review,
        "severity_counts": severity_counts(review.get("issues", [])),
    }


@app.post("/review/pr")
async def review_pull_request(request: PRReviewRequest):
    """Fetch a PR's diff from GitHub and review it with Claude.
    Returns a structured review: {summary, verdict, issues[]}."""
    token = get_github_token(request.session_id)

    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"https://api.github.com/repos/{request.owner}/{request.repo}/pulls/{request.pr_number}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.diff"},
        )
    if res.status_code != 200:
        raise HTTPException(status_code=res.status_code, detail="Failed to fetch PR diff from GitHub")

    diff_content = res.text
    review = review_diff(diff_content, pr_number=request.pr_number)
    return {
        "pr_number": request.pr_number,
        "review": review,
        "severity_counts": severity_counts(review.get("issues", [])),
    }


@app.post("/review/file/stream")
async def review_file_stream(request: FileReviewRequest):
    """Stream a file review's raw text live (SSE) for a 'typing' effect in the UI."""
    token = get_github_token(request.session_id)

    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"https://api.github.com/repos/{request.owner}/{request.repo}/contents/{request.path}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.raw+json"},
        )
    if res.status_code != 200:
        raise HTTPException(status_code=res.status_code, detail="Failed to fetch file from GitHub")

    code_content = res.text

    def event_generator():
        try:
            for chunk in stream_review_text(code_content, is_diff=False, filename=request.path):
                yield f"data: {json.dumps({'type': 'token', 'text': chunk})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/review/pr/stream")
async def review_pr_stream(request: PRReviewRequest):
    """Stream a PR review's raw text live (SSE) for a 'typing' effect in the UI."""
    token = get_github_token(request.session_id)

    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"https://api.github.com/repos/{request.owner}/{request.repo}/pulls/{request.pr_number}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.diff"},
        )
    if res.status_code != 200:
        raise HTTPException(status_code=res.status_code, detail="Failed to fetch PR diff from GitHub")

    diff_content = res.text

    def event_generator():
        try:
            for chunk in stream_review_text(diff_content, is_diff=True, pr_number=request.pr_number):
                yield f"data: {json.dumps({'type': 'token', 'text': chunk})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ---------- Post Review Back to GitHub ----------

class PostReviewRequest(BaseModel):
    session_id: str
    owner: str
    repo: str
    pr_number: int


@app.post("/review/pr/post-to-github")
async def post_review_to_github(request: PostReviewRequest):
    """
    Review a PR, then actually post the result back to GitHub as:
    1. A PR review with inline comments on the flagged lines
    2. A commit status check (pass/fail), just like a real CI bot
    """
    token = get_github_token(request.session_id)

    async with httpx.AsyncClient() as client:
        diff_res = await client.get(
            f"https://api.github.com/repos/{request.owner}/{request.repo}/pulls/{request.pr_number}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.diff"},
        )
    if diff_res.status_code != 200:
        raise HTTPException(status_code=diff_res.status_code, detail="Failed to fetch PR diff from GitHub")

    review = review_diff(diff_res.text, pr_number=request.pr_number)

    commit_sha = await get_pr_head_sha(token, request.owner, request.repo, request.pr_number)
    if not commit_sha:
        raise HTTPException(status_code=400, detail="Could not resolve PR head commit SHA")

    review_status_code, review_response = await post_pr_review_with_comments(
        token, request.owner, request.repo, request.pr_number, commit_sha,
        review.get("summary", ""), review.get("verdict", "comment"), review.get("issues", []),
    )

    status = overall_status(review.get("verdict", "comment"), review.get("issues", []))
    counts = severity_counts(review.get("issues", []))
    status_description = f"{counts['critical']} critical, {counts['major']} major, {counts['minor']} minor issues"

    status_code, status_response = await post_commit_status(
        token, request.owner, request.repo, commit_sha, status, status_description,
    )

    return {
        "review": review,
        "severity_counts": counts,
        "github_review_posted": review_status_code in (200, 201),
        "github_status_posted": status_code in (200, 201),
        "commit_sha": commit_sha,
    }


# ---------- Webhook (automatic review on PR open/update) ----------

def _verify_webhook_signature(payload_body: bytes, signature_header: Optional[str]) -> bool:
    if not GITHUB_WEBHOOK_SECRET:
        return True  # no secret configured: skip verification (fine for local testing only)
    if not signature_header:
        return False
    expected = "sha256=" + hmac.new(GITHUB_WEBHOOK_SECRET.encode(), payload_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


@app.post("/webhook/github")
async def github_webhook(request: Request):
    """
    Configure this URL as a GitHub webhook (Settings > Webhooks) listening for
    'pull_request' events. Whenever a PR is opened or updated, this automatically
    runs a review and posts it back to GitHub — no manual clicking required.
    """
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    if not _verify_webhook_signature(body, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = json.loads(body)
    event = request.headers.get("X-GitHub-Event")

    if event != "pull_request" or payload.get("action") not in ("opened", "synchronize", "reopened"):
        return {"message": "Event ignored (not a relevant PR action)"}

    if not WEBHOOK_GITHUB_TOKEN:
        raise HTTPException(
            status_code=500,
            detail="WEBHOOK_GITHUB_TOKEN not configured on server; cannot auto-review via webhook.",
        )

    owner = payload["repository"]["owner"]["login"]
    repo = payload["repository"]["name"]
    pr_number = payload["pull_request"]["number"]
    commit_sha = payload["pull_request"]["head"]["sha"]

    async with httpx.AsyncClient() as client:
        diff_res = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}",
            headers={"Authorization": f"Bearer {WEBHOOK_GITHUB_TOKEN}", "Accept": "application/vnd.github.diff"},
        )
    if diff_res.status_code != 200:
        raise HTTPException(status_code=diff_res.status_code, detail="Failed to fetch PR diff from GitHub")

    review = review_diff(diff_res.text, pr_number=pr_number)

    await post_pr_review_with_comments(
        WEBHOOK_GITHUB_TOKEN, owner, repo, pr_number, commit_sha,
        review.get("summary", ""), review.get("verdict", "comment"), review.get("issues", []),
    )

    status = overall_status(review.get("verdict", "comment"), review.get("issues", []))
    counts = severity_counts(review.get("issues", []))
    await post_commit_status(
        WEBHOOK_GITHUB_TOKEN, owner, repo, commit_sha, status,
        f"{counts['critical']} critical, {counts['major']} major, {counts['minor']} minor issues",
    )

    return {"message": f"Auto-reviewed PR #{pr_number}", "verdict": review.get("verdict"), "severity_counts": counts}


@app.get("/")
def health_check():
    return {"status": "ok", "message": "AI Code Reviewer backend is running"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
