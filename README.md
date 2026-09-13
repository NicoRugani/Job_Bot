# Job Bot

Finds **Summer 2027** internships (SWE, UX/UI, embedded), scores how well each one fits your profile, tracks the good ones in **Notion**, and sends a **push notification** for strong matches. Runs for free on a GitHub Actions schedule.

```
Simplify + zshah101 + speedyapply lists, plus the Greenhouse/Lever/Ashby boards of every company they mention
  → the same posting found in several places is kept once (matched by the employer's job ID)
  → title filter (config.yaml › roles)
  → job description from the employer's ATS (Workday, Greenhouse, Oracle, Lever, Ashby, iCIMS, …)
  → free pre-score: skill keywords + local embeddings   (ranks the queue, drops obvious misses)
  → Gemini free-tier fit analysis                      (pauses when quota runs out, resumes next run)
  → final score = 85% fit + 15% logistics preference
  → Notion (score ≥ 55)  +  ntfy push (score ≥ 82)
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

**Location:** in-person and hybrid roles outside the US are dropped before scoring. Remote roles stay, unless they're limited to another country ("Remote in Canada"). Jobs in the St. Louis or Bozeman areas get **+8 points** and a ⭐ in the Notion **Area** column. Edit `location` in `config.yaml` to change the areas or bonus.

| Tier | Score | What happens |
|---|---|---|
| 🔥 Strong | ≥ 82 | Notion + push notification |
| ✅ Good | 70–81 | Notion |
| 🤔 Possible | 55–69 | Notion |
| — | < 55 | Remembered in `data/state.json` only |

The scoring instructions in `jobbot/llm/prompt.py` are deliberately strict: clear fits score 82+, and roles missing a core skill land around 68–76. If you edit those instructions, older scores get re-scored automatically, after postings from the last day and before the backlog, without re-sending notifications. Changing tier cutoffs or weights in `config.yaml` updates existing scores on the next run without any Gemini calls.

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

The database comes with these views:

| View | What it shows |
|---|---|
| **All matches** | Everything, best score first |
| **To review** | Status = New, still open |
| **Application board** | Cards grouped by Status; drag a card to change its status |
| **Strong matches** | 🔥 Strong tier, still open |
| **Near home** | ⭐ St. Louis / ⭐ Bozeman |
| **Applied** | Applied, OA, Interview or Offer, newest application first |

Edit **Status**, **Applied on** and **Notes** freely; the bot never overwrites them. If you delete a page, the bot won't recreate it. If a tracked job later falls outside your rules (e.g. you tighten `location`), the bot removes its row, unless you've already set a status, a note or an applied date.

### 6. Try it locally
```bash
python -m jobbot run --dry-run --limit 10   # scores 10 listings and prints them; saves/sends nothing
python -m jobbot run --limit 10             # real run: state, Notion, notifications
python -m jobbot show                       # tracked listings from saved state
```

### 7. Schedule it on GitHub Actions
1. Push this folder to a **private** GitHub repo.
2. Add the 4 values from `.env` as repository secrets: **Settings → Secrets and variables → Actions**, or with `gh secret set GEMINI_API_KEY` and so on.
3. **Actions → jobbot → Run workflow** starts a run now. After that it runs every 3 hours and commits `data/state.json` back to the repo.

The first backfill of existing listings may take a few runs if the free Gemini quota runs out. Unscored listings stay pending and are retried, best matches first.

## Commands

| Command | What it does |
|---|---|
| `python -m jobbot run [--dry-run] [--limit N] [--no-notify]` | One full pass |
| `python -m jobbot show [--all]` | Print tracked (or all scored) listings and filter stats |
| `python -m jobbot rescore <id>… \| all` | Queue listings for another LLM pass (e.g. after editing your profile) |
| `python -m jobbot init-notion --parent-page <url>` | Create the Notion tracking database, with its views |
| `python -m jobbot setup-notion` | Add any missing columns and views to an existing database (safe to rerun) |
| `python -m jobbot test-notify` | Send a sample push |

## Tuning

- **Missing roles, or noise getting through?** Edit `roles` regexes in `config.yaml`, then check with `python -m jobbot show`, which lists filter reasons.
- **Dream companies:** enable `sources.ats_watchlist` and add Greenhouse, Lever or Ashby board slugs to catch postings before the aggregator lists them.
- **"JD read" unchecked for a company?** If its career site embeds Greenhouse (`?gh_jid=` in the URL), add `domain: board-name` under `jd.greenhouse_boards`.
- **Too many or too few pushes:** adjust `tiers.notify_threshold`.
