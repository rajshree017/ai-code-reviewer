# Codespect — AI Code Reviewer

Connect your GitHub repos and get AI-powered code reviews using Claude API.

## Key Features
- GitHub OAuth login (connect your real repos)
- Review any file or open Pull Request with one click
- Severity-scored issues: Critical, Major, Minor
- Post inline comments directly to GitHub PRs
- Webhook support: auto-review every new PR (like a real CI bot)
- Live streaming review output

## Tech Stack
- Backend: Python, FastAPI, Groq API
- Frontend: React, Vite
- Auth: GitHub OAuth
- Hosting: Render.com

## Local Setup

### Backend
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn main:app --reload --port 8000

### Frontend
cd frontend
npm install
npm run dev

Open: http://localhost:5173

## GitHub OAuth Setup
1. Go to https://github.com/settings/developers
2. New OAuth App
3. Homepage URL: http://localhost:5173
4. Callback URL: http://localhost:8000/auth/callback
5. Copy Client ID and Secret to .env file
