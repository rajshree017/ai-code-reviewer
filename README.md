# Codespect — AI Code Reviewer

GitHub se OAuth ke through connect karo, koi bhi repo browse karo, aur kisi file ya
open pull request ko ek click mein Claude se review karwao.

## ✨ Key Features (jo recruiter ko impress karenge)

1. **Severity-scored structured reviews** — Claude plain text nahi, JSON return karta hai
   (critical/major/minor issues + verdict), jisse UI mein proper issue cards dikhte hain.
2. **Real GitHub automation** — "Post to GitHub" button click karne se review seedha PR mein
   inline comments ke roop mein post hota hai, plus ek pass/fail commit status check
   (jaisa CI bots karte hain).
3. **Webhook support** — Optional setup se bot automatically har naye/updated PR ko review
   kar sakta hai, bina kisi manual click ke (real production bot jaisa behavior).
4. **Streaming output** — Review live "type" hota hua dikhta hai, static loading spinner nahi.

## Folder Structure

```
ai-code-reviewer/
├── backend/          FastAPI server (Python)
│   ├── main.py          API routes + GitHub OAuth flow + webhook
│   ├── reviewer.py      Claude review logic (structured JSON + streaming)
│   ├── github_client.py posts inline comments + status checks back to GitHub
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
├── frontend/          React app (Vite)
│   └── src/
└── render.yaml         deploy config for Render.com
```

## Step 1 — GitHub OAuth App banao (Zaroori, sabse pehle)

GitHub OAuth ke liye GitHub khud tumhe ek app register karne ko kahega — yeh main nahi kar
sakta kyunki yeh tumhare GitHub account se login karke karna padta hai. Steps:

1. GitHub par login karo, jaao: **https://github.com/settings/developers**
2. **"OAuth Apps"** tab → **"New OAuth App"** click karo
3. Form fill karo:
   - **Application name:** `Codespect` (ya kuch bhi)
   - **Homepage URL:** `http://localhost:5173` (local testing ke liye; baad mein production URL se update kar dena)
   - **Authorization callback URL:** `http://localhost:8000/auth/callback`
4. **"Register application"** click karo
5. Tumhe ek **Client ID** milega seedha. **"Generate a new client secret"** click karke
   **Client Secret** bhi le lo (yeh sirf ek baar dikhta hai, copy kar lena)

Yeh dono values `.env` file mein daalni hain (neeche step 2 mein).

> **Production deploy karte waqt:** Jab backend host ho jaaye (Render se URL milega), wapas
> isi GitHub OAuth App settings mein jaake **Authorization callback URL** ko update karna
> hoga production URL se, jaise: `https://ai-code-reviewer-backend.onrender.com/auth/callback`
> — warna login fail hoga.

## Step 2 — Local Setup

### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

`.env` file kholo aur fill karo:
```
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxx
GITHUB_CLIENT_ID=<step 1 se mila Client ID>
GITHUB_CLIENT_SECRET=<step 1 se mila Client Secret>
GITHUB_REDIRECT_URI=http://localhost:8000/auth/callback
FRONTEND_URL=http://localhost:5173
```

Backend chalu karo:
```bash
uvicorn main:app --reload --port 8000
```

### Frontend

Naye terminal mein:
```bash
cd frontend
npm install
npm run dev
```

Browser mein kholo: `http://localhost:5173` → "Connect with GitHub" pe click karo.

## Step 3 — Hosting (Production)

### Backend → Render.com

1. Repo ko GitHub par push karo (neeche steps).
2. [render.com](https://render.com) → "New +" → "Blueprint" → apna repo connect karo.
3. Render `render.yaml` detect karega. Yeh environment variables daalne ko kahega:
   - `ANTHROPIC_API_KEY`
   - `GITHUB_CLIENT_ID`
   - `GITHUB_CLIENT_SECRET`
   - `GITHUB_REDIRECT_URI` → `https://<tumhara-backend-url>.onrender.com/auth/callback`
   - `FRONTEND_URL` → tumhara deployed frontend ka URL
   - (Optional, webhook ke liye) `GITHUB_WEBHOOK_SECRET`, `WEBHOOK_GITHUB_TOKEN`
4. Deploy hone do, backend URL note kar lo.
5. **GitHub OAuth App settings** mein wapas jaake Authorization callback URL update karo
   (Step 1 ka "production deploy" note dekho).

### Frontend → Render.com (blueprint isse bhi deploy kar dega)

`VITE_API_URL` environment variable set karo = tumhara backend ka production URL.

## Step 4 — GitHub par push karna

```bash
cd ai-code-reviewer
git init
git add .
git commit -m "Initial commit: AI Code Reviewer"
git branch -M main
git remote add origin https://github.com/<tumhara-username>/ai-code-reviewer.git
git push -u origin main
```

## Step 5 (Optional) — Webhook se automatic reviews enable karna

Yeh feature har naye ya update hue PR ko **automatically** review kar deta hai —
bina manually "review" click kiye. Yeh dikhane ke liye best feature hai ki tumhara
tool ek real CI bot jaisa kaam karta hai.

1. `.env` mein 2 naye variables fill karo:
   - `GITHUB_WEBHOOK_SECRET` → koi bhi random string khud bana lo (jaise password)
   - `WEBHOOK_GITHUB_TOKEN` → GitHub Personal Access Token banao:
     **https://github.com/settings/tokens** → "Generate new token (classic)" →
     `repo` scope check karo → generate → copy karo

2. Repo settings mein jaao (jis repo ko auto-review karwana hai):
   `https://github.com/<owner>/<repo>/settings/hooks` → **"Add webhook"**
   - **Payload URL:** `https://<tumhara-backend-url>/webhook/github`
   - **Content type:** `application/json`
   - **Secret:** wahi string jo `.env` mein `GITHUB_WEBHOOK_SECRET` mein daala
   - **Which events:** "Let me select individual events" → sirf **Pull requests** check karo
   - **"Add webhook"** click karo

3. Ab jab koi naya PR khulega ya update hoga, backend automatically:
   - Diff fetch karega
   - Claude se review karwayega
   - PR mein inline comments post karega
   - Commit status check (✅/❌) laga dega

## Important Notes

- **Session storage in-memory hai:** Abhi tokens RAM mein store hote hain (single-user demo
  ke liye theek hai). Backend restart hone par sabko phir se login karna padega. Multi-user
  production app ke liye isse Redis ya database mein store karna chahiye.
- **OAuth scope:** Backend `repo` scope use karta hai (private repos bhi padh sake) —
  agar sirf public repos chahiye to `main.py` mein scope ko `public_repo` kar do.
- **Free tier cold start:** Render free tier inactive hone par sleep ho jaata hai, pehli
  request mein ~30-50 second lag sakta hai.
- **CORS:** Abhi `allow_origins=["*"]` hai. Production mein apne frontend URL tak restrict
  karna recommended hai.
