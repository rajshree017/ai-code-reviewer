"""
GitHub posting helpers: turn a structured review (summary, verdict, issues[])
into real actions on GitHub — inline PR review comments and a commit status
check, the same way a real CI bot (like a linter or CodeQL) would.
"""
from typing import Optional

import httpx


async def post_pr_review_with_comments(
    token: str,
    owner: str,
    repo: str,
    pr_number: int,
    commit_sha: str,
    summary: str,
    verdict: str,
    issues: list,
):
    """
    Submit a single GitHub PR review containing a top-level summary plus
    inline comments for each issue that has a resolvable file+line.
    GitHub event mapping: approve -> APPROVE, request_changes -> REQUEST_CHANGES, comment -> COMMENT
    """
    event_map = {
        "approve": "APPROVE",
        "request_changes": "REQUEST_CHANGES",
        "comment": "COMMENT",
    }
    github_event = event_map.get(verdict, "COMMENT")

    severity_emoji = {"critical": "🔴", "major": "🟠", "minor": "🟡"}

    inline_comments = []
    general_notes = []  # issues without a resolvable file/line go into the summary body instead

    for issue in issues:
        file_path = issue.get("file")
        line = issue.get("line")
        emoji = severity_emoji.get(issue.get("severity", "minor"), "⚪")
        body = (
            f"{emoji} **{issue.get('severity', 'minor').upper()}: {issue.get('title', 'Issue')}**\n\n"
            f"{issue.get('description', '')}\n\n"
            f"**Suggestion:** {issue.get('suggestion', '')}"
        )

        if file_path and line:
            inline_comments.append({"path": file_path, "line": line, "body": body})
        else:
            general_notes.append(body)

    body_text = f"### 🤖 AI Review Summary\n\n{summary}\n"
    if general_notes:
        body_text += "\n---\n\n**Additional notes (no specific line):**\n\n" + "\n\n".join(general_notes)

    payload = {
        "commit_id": commit_sha,
        "body": body_text,
        "event": github_event,
        "comments": inline_comments,
    }

    async with httpx.AsyncClient() as client:
        res = await client.post(
            f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            json=payload,
        )

    # If inline comments reference lines GitHub can't anchor (outside the diff context),
    # GitHub returns 422. Retry once with just the summary, no inline comments, so the
    # user still gets a review instead of a hard failure.
    if res.status_code == 422 and inline_comments:
        fallback_payload = {"commit_id": commit_sha, "body": body_text, "event": github_event}
        async with httpx.AsyncClient() as client:
            res = await client.post(
                f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                json=fallback_payload,
            )

    return res.status_code, res.json() if res.content else {}


async def post_commit_status(
    token: str,
    owner: str,
    repo: str,
    commit_sha: str,
    state: str,
    description: str,
    context: str = "ai-code-reviewer",
):
    """
    Post a commit status check (the small ✅/❌ dot you see next to commits/PRs).
    state must be one of: 'error', 'failure', 'pending', 'success'
    """
    async with httpx.AsyncClient() as client:
        res = await client.post(
            f"https://api.github.com/repos/{owner}/{repo}/statuses/{commit_sha}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            json={"state": state, "description": description[:140], "context": context},
        )
    return res.status_code, res.json() if res.content else {}


async def get_pr_head_sha(token: str, owner: str, repo: str, pr_number: int) -> Optional[str]:
    """Look up the latest commit SHA on a PR's head branch (needed to post reviews/statuses)."""
    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
    if res.status_code != 200:
        return None
    return res.json().get("head", {}).get("sha")
