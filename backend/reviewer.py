"""
Review logic: sends code (or a PR diff) to Claude and asks for a STRUCTURED review
(JSON with individual issues + severities + an overall verdict), so the backend can:
- Post inline comments on exact lines in a GitHub PR
- Set a pass/fail GitHub commit status check (like a real CI bot)
- Stream the raw review text to the frontend as it's generated
"""
import json
import os
from typing import Generator, Optional

import anthropic

CLAUDE_MODEL = "claude-sonnet-4-6"

# We ask Claude to return JSON so the backend can programmatically act on the
# review (inline PR comments, pass/fail status) instead of just showing prose.
JSON_INSTRUCTIONS = """
Respond with ONLY a JSON object (no markdown fences, no preamble) matching this shape:
{
  "summary": "2-3 sentence overall summary",
  "verdict": "approve" | "request_changes" | "comment",
  "issues": [
    {
      "severity": "critical" | "major" | "minor",
      "title": "short title",
      "description": "what's wrong and why it matters",
      "suggestion": "how to fix it",
      "file": "path/to/file (omit or empty string if not applicable)",
      "line": null or integer (the new-file line number this applies to, if identifiable from the diff; null if not applicable)
    }
  ]
}
If there are no issues, return an empty "issues" array and a positive summary. Never invent line numbers you can't infer from the input.
"""

FILE_REVIEW_SYSTEM_PROMPT = f"""You are an experienced senior software engineer doing a code review.
Review the given file for:
1. Bugs or logic errors
2. Security vulnerabilities
3. Performance issues
4. Readability / maintainability
5. Best-practice violations for the language used
{JSON_INSTRUCTIONS}"""

PR_REVIEW_SYSTEM_PROMPT = f"""You are an experienced senior software engineer reviewing a GitHub pull request diff.
Review the diff for:
1. Bugs or logic errors introduced
2. Security vulnerabilities
3. Performance issues
4. Readability / maintainability
5. Whether the change does what it likely intends to do
{JSON_INSTRUCTIONS}
For "line", use the line number in the NEW version of the file (the "+" side of the diff hunk), since that's what GitHub needs to anchor inline comments."""


def _get_client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY environment variable not set. "
            "Add it to your .env file or hosting platform's environment settings."
        )
    return anthropic.Anthropic(api_key=api_key)


def _parse_review_json(raw_text: str) -> dict:
    """Parse Claude's JSON response defensively (strip stray markdown fences if present)."""
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Fall back to a safe shape so the API never crashes the caller
        return {
            "summary": raw_text[:500],
            "verdict": "comment",
            "issues": [],
            "_parse_error": True,
        }


def _truncate(content: str, max_chars: int = 60000) -> tuple:
    truncated = content[:max_chars]
    note = "\n\n[NOTE: input was truncated for review due to length]" if len(content) > max_chars else ""
    return truncated, note


def review_code(code_content: str, filename: str = "") -> dict:
    """Review a single file's full content. Returns structured dict: summary, verdict, issues[]."""
    client = _get_client()
    truncated, note = _truncate(code_content)
    user_message = f"File: {filename}\n\n```\n{truncated}\n```{note}"

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=3000,
        system=FILE_REVIEW_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    raw_text = "".join(block.text for block in response.content if block.type == "text")
    return _parse_review_json(raw_text)


def review_diff(diff_content: str, pr_number: int = 0) -> dict:
    """Review a PR's unified diff. Returns structured dict: summary, verdict, issues[]."""
    client = _get_client()
    truncated, note = _truncate(diff_content)
    user_message = f"Pull Request #{pr_number} diff:\n\n```diff\n{truncated}\n```{note}"

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=3000,
        system=PR_REVIEW_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    raw_text = "".join(block.text for block in response.content if block.type == "text")
    return _parse_review_json(raw_text)


def stream_review_text(content: str, *, is_diff: bool, pr_number: int = 0, filename: str = "") -> Generator[str, None, None]:
    """
    Streams the raw text of the review as it's generated (for live display in the UI).
    NOTE: since the model is asked for JSON, this streams raw JSON tokens — the frontend
    shows it as a live "typing" effect, then the final structured result arrives separately
    once review_code()/review_diff() (non-streaming) completes the same request server-side.
    """
    client = _get_client()
    truncated, note = _truncate(content)

    if is_diff:
        system_prompt = PR_REVIEW_SYSTEM_PROMPT
        user_message = f"Pull Request #{pr_number} diff:\n\n```diff\n{truncated}\n```{note}"
    else:
        system_prompt = FILE_REVIEW_SYSTEM_PROMPT
        user_message = f"File: {filename}\n\n```\n{truncated}\n```{note}"

    with client.messages.stream(
        model=CLAUDE_MODEL,
        max_tokens=3000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    ) as stream:
        for text_chunk in stream.text_stream:
            yield text_chunk


def severity_counts(issues: list) -> dict:
    counts = {"critical": 0, "major": 0, "minor": 0}
    for issue in issues:
        sev = issue.get("severity", "minor")
        if sev in counts:
            counts[sev] += 1
    return counts


def overall_status(verdict: str, issues: list) -> str:
    """Translate our verdict + issues into a GitHub commit status state: 'success' | 'failure' | 'pending'."""
    counts = severity_counts(issues)
    if verdict == "request_changes" or counts["critical"] > 0:
        return "failure"
    return "success"
