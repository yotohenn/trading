# Insider & Congress Trade Monitor — hosted website

A free, self-updating website that tracks **insider cluster buys (SEC Form 4)**, **congressional stock disclosures (STOCK Act)**, and **big-fund portfolio moves (13F)** — with a research-based conviction score on every signal, entity leaderboards (dollar and % changes), and profiles with photos.

It runs entirely on **GitHub's free tier**:
- **GitHub Pages** serves the dashboard at a public URL.
- **GitHub Actions** runs `fetch_data.py` once a day in the cloud, commits a fresh `data.js`, and Pages redeploys automatically.

No servers, no API keys, no monthly cost. It keeps running with **Claude completely out of the loop** — cancel Claude anytime and the site still updates itself every day.

---

## One-time setup (about 5 minutes)

### 1. Create an empty repo
On github.com: **New repository** → name it e.g. `trade-monitor` → **Public** → do **not** add a README/gitignore (this folder already has them) → **Create repository**.

### 2. Push these files
Open **Git Bash** in this folder (right-click inside the `trade-monitor-site` folder → "Open Git Bash here") and run, replacing `YOURNAME`:

```bash
git init
git add .
git commit -m "Initial trade monitor site"
git branch -M main
git remote add origin https://github.com/YOURNAME/trade-monitor.git
git push -u origin main
```

(If you prefer no command line: on the repo page click **Add file → Upload files**, then drag **everything inside** this folder — including the `.github` folder — into the browser and commit. Make sure the `.github/workflows/refresh.yml` path is preserved.)

### 3. Turn on Pages
Repo → **Settings → Pages** → under **Build and deployment**, Source = **Deploy from a branch**, Branch = **main**, folder = **/ (root)** → **Save**. After a minute your site is live at:

```
https://YOURNAME.github.io/trade-monitor/
```

It will show clearly-labeled **sample data** until the first real data run.

### 4. Run the first real data pull
Repo → **Actions** tab → **Refresh trade data** → **Run workflow**. First run takes 10–25 minutes (it politely rate-limits against SEC servers). When it finishes it commits `data.js`; refresh your site URL and you'll see live data. After that it runs **automatically every day** — you never touch it again.

---

## What each file does

| File | Purpose |
|---|---|
| `index.html` | The dashboard (Signals, Leaderboards, Profiles, How-scoring-works) |
| `fetch_data.py` | Pulls the free public data and writes `data.js` — Python standard library only, no installs |
| `config.json` | Your watchlists and settings — edit funds (by CIK), politicians, thresholds |
| `data.js` | The data the page reads (sample until the first Action run) |
| `.github/workflows/refresh.yml` | The daily cloud job |
| `.gitignore`, `.nojekyll` | Housekeeping so Pages deploys cleanly |

## Editing your watchlist

Edit `config.json` and push the change (or edit it directly on GitHub and commit). Add a fund by its SEC **CIK** number (look it up at sec.gov/cgi-bin/browse-edgar). Add a politician with their name-match strings and committees. Change `cluster_window_days`, `min_purchase_dollars`, etc. to tune sensitivity. The next daily run picks it up.

## Data sources and honest lags

| Source | What | Lag |
|---|---|---|
| SEC EDGAR daily indexes + Form 4 | Corporate insider buys/sells — exact shares & prices | 2 business days (freshest signal anywhere) |
| Senate/House Stock Watcher (community mirrors of official STOCK Act filings) | Congress member/spouse trades, in dollar ranges | 30–45 days; the dashboard warns if the mirror itself goes stale |
| SEC EDGAR 13F | Institutional portfolios (watchlist funds) — full dollar values | up to 45 days after quarter-end |
| unitedstates.io | Congressional photos (public domain) + member lookup | — |

## The conviction score (what the meter means)

Every factor and its points show on each signal card — nothing is hidden. It rewards what published research actually found predictive: **true open-market purchases only** (grants/ESPP/option-exercises filtered out), **cluster buying** (2 and especially 3+ insiders in the window — historically ~2x the abnormal return of solo buys), **C-suite buyers**, **dollar size**, **stake increases**, **first-time opportunistic buyers**, plus corroboration when **a member of Congress bought the same ticker within 45 days** (extra if their committee oversees that sector) or **a watchlist fund added a matching position**. It penalizes staleness, heavier same-window selling, and pre-scheduled 10b5-1 buys.

**It is signal strength, not a probability of profit.** Post-2012 studies find members of Congress in aggregate do *not* beat the market; insider cluster buying is the better-documented edge (~3.8% vs ~2% abnormal return over 21 trading days, averaged across many events — not a promise about any one stock). Treat signals as research leads, not trade triggers. Informational only — not investment, legal, or tax advice.

## Legality

Everything here reads **mandatory public disclosures** published by the U.S. government precisely so the public can see them (the point of the STOCK Act and Section 16). This is the same data Capitol Trades, Quiver Quantitative, and Unusual Whales build businesses on. The fetch identifies itself politely to SEC servers and respects their rate limits; nothing is scraped from private or paywalled sources.

## Optional: also run it locally

The sibling `trade-monitor` folder on your Desktop is the local version — double-click `refresh.bat` to run on your PC without GitHub. The hosted site and the local copy are independent; use either or both.

## Ideas for later (Claude can add these)

Email/text alerts when a score crosses a threshold; options-leg parsing for congressional LEAPS; Form 144 (advance notice of insider sales) and 13D activist-stake feeds; a custom domain on the Pages site.
