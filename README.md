# Job Bot

Finds **Summer 2027** internships (SWE, UX/UI, embedded), scores how well each one fits your profile, tracks the good ones in **Notion**, and sends a **push notification** for strong matches. Runs for free on a GitHub Actions schedule.

```
SimplifyJobs list (+ optional company watchlist)
  → title filter (config.yaml › roles)
  → job description from the employer's ATS (Workday, Greenhouse, Oracle, Lever, Ashby, iCIMS, …)
  → free pre-score: skill keywords + local embeddings   (ranks the queue, drops obvious misses)
  → Gemini free-tier fit analysis                      (pauses when quota runs out, resumes next run)
  → final score = 85% fit + 15% logistics preference
  → Notion (score ≥ 50)  +  ntfy push (score ≥ 80)
```

## How scoring works

Gemini reads your `profile/profile.yaml` and the job description, then returns a **fit score** based on skills, experience level and role alignment. It also pulls out eligibility (graduation window, degree), work mode, pay, and relocation.

**Final score** = `0.85 × fit + 0.15 × logistics`. The logistics value encodes your preference order:

| Setup | Logistics |
|---|---|
| In person + relocation + paid | 100 |
| Remote + paid | 85 |
| In person + paid | 70 |
| Remote, unpaid | 35 |
| In person, unpaid | 20 |

Hybrid counts as in person, and unknown pay counts as paid ×0.95. If the posting clearly excludes you (wrong graduation window, PhD-only, not a Summer 2027 internship), the score is capped at 40.

| Tier | Score | What happens |
|---|---|---|
| 🔥 Strong | ≥ 80 | Notion + push notification |
| ✅ Good | 65–79 | Notion |
| 🤔 Possible | 50–64 | Notion |
| — | < 50 | Remembered in `data/state.json` only |

All weights and thresholds live in `config.yaml`.

Notes:
- Push notifications only go out when the bot could read the job description; set `notify_without_jd: true` to change that.
- The very first scan sends one digest instead of dozens of pushes.

## Setup

### 1. Local install
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
pytest -q
```

### 2. Review your profile
`profile/profile.yaml` was drafted from your resume, without name or contact info, since it's sent to Gemini and committed to the repo. Check it, and fill in `work_authorization`; it matters for defense and clearance roles.

### 3. Gemini API key (free)
1. Create a key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey) and put it in `.env` as `GEMINI_API_KEY`.
2. Check **AI Studio → Rate limits** for the free RPM/RPD of the model in `config.yaml › llm.model`. Set `llm.rpm` to match, and optionally `llm.daily_budget`.
3. Note that Google may use free-tier prompts to improve its products.

### 4. Phone notifications (ntfy)
1. Install the **ntfy** app (iOS/Android).
2. Generate a hard-to-guess topic, since anyone who knows the topic can read it:
   ```bash
   python -c "import secrets;print('jobbot-'+secrets.token_urlsafe(18))"
   ```
3. Put the topic in `.env` as `NTFY_TOPIC` and subscribe to it in the app.
4. Run `python -m jobbot test-notify`.

### 5. Notion database
1. Create an internal integration at [notion.so/profile/integrations](https://www.notion.so/profile/integrations). Copy its secret into `.env` as `NOTION_TOKEN`.
2. Create a page (e.g. "Job Bot"), open **••• → Connections**, and add your integration.
3. Create the database:
   ```bash
   python -m jobbot init-notion --parent-page "<page URL>"
   ```
4. Put the printed `NOTION_DATA_SOURCE_ID` in `.env`.

Edit **Status** and **Notes** freely; the bot never overwrites them. If you delete a page, the bot won't recreate it.

### 6. Try it locally
```bash
python -m jobbot run --dry-run --limit 10   # scores 10 listings and prints them; saves/sends nothing
python -m jobbot run --limit 10             # real run: state, Notion, notifications
python -m jobbot show                       # tracked listings from saved state
```

### 7. Schedule it on GitHub Actions
1. Push this folder to a **private** GitHub repo.
2. Add the 4 values from `.env` as repository secrets: **Settings → Secrets and variables → Actions**, or with `gh secret set GEMINI_API_KEY` and so on.
3. **Actions → jobbot → Run workflow** starts a run now. After that it runs every 4 hours and commits `data/state.json` back to the repo.

The first backfill of existing listings may take a few runs if the free Gemini quota runs out. Unscored listings stay pending and are retried, best matches first.

## Commands

| Command | What it does |
|---|---|
| `python -m jobbot run [--dry-run] [--limit N] [--no-notify]` | One full pass |
| `python -m jobbot show [--all]` | Print tracked (or all scored) listings and filter stats |
| `python -m jobbot rescore <id>… \| all` | Queue listings for another LLM pass (e.g. after editing your profile) |
| `python -m jobbot init-notion --parent-page <url>` | Create the Notion tracking database |
| `python -m jobbot test-notify` | Send a sample push |

## Tuning

- **Missing roles, or noise getting through?** Edit `roles` regexes in `config.yaml`, then check with `python -m jobbot show`, which lists filter reasons.
- **Dream companies:** enable `sources.ats_watchlist` and add Greenhouse, Lever or Ashby board slugs to catch postings before the aggregator lists them.
- **Too many or too few pushes:** adjust `tiers.notify_threshold`.
