# OutbreakRadar

Community health early-warning platform (Problem H3). An awareness tool, not a diagnosis tool.

Residents check whether illness reports in their ward are normal or rising, report symptoms using their current location, and read advisories. Health workers add verified reports. Health authorities get explained alerts, act on them, moderate accounts and publish advisories.

## Run

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

Open http://127.0.0.1:8000. The database (`outbreakradar.db`) is created on first start.

**First start:** an authority account is created and its password is printed once in the terminal. To choose your own:

```bash
export OR_AUTHORITY_EMAIL=you@example.org
export OR_AUTHORITY_PASSWORD='a-long-password'
```

There are no built-in demo accounts. Older databases that contain `@outbreakradar.demo` accounts have them removed on start.

## Pages

- `/` landing page
- `/login.html` sign in and create account
- `/app.html` community map. Guests can view it and use **Show my area**. Signed-in users can report symptoms. The authority also gets Moderation, Advisory and Coverage.

## Using your current location

- **Show my area** (map page, everyone): finds your ward and shows its status. Your position is not stored.
- **Use my current location** (Report tab): fills in your ward automatically. If you are outside the covered wards, choose your ward from the list.
- Browsers only share location on `https://` or `localhost`. Deploy behind HTTPS.
- Wards are six placeholders around one point. The authority sets that point under **Coverage** (use device location or type coordinates). Replace with real ward data before real use.

## Presentation mode (for judges only)

The outbreak simulator is **off by default** and is not shown in the app. To show it in a demo:

```bash
OR_DEMO=1 uvicorn main:app
```

Then sign in as the authority and open the **Simulator** tab: Next stage (Normal, Watch, Rising, Critical for Ward 5), Test spam accounts (15 reports from 2 accounts: no alert fires and the main account is flagged), Reset simulation. Simulated reports are labelled and use the same detection code as real ones.

## How misuse is handled

1. **Accounts required to report.** Passwords are hashed (PBKDF2), sessions are stored as hashes and expire after 12 hours, and 5 failed sign-ins lock an email for 10 minutes.
2. **Rate limit and duplicates.** Citizens: 5 reports per hour, and the same symptom in the same ward within an hour is rejected. Verified health workers visit many households, so they get 60 reports per hour and can repeat a symptom for a ward (only double taps within 30 seconds are rejected). Unverified health workers are treated as citizens.
3. **Verification.** Health-worker accounts count as citizens until the authority verifies them.
4. **Independent-evidence check.** If most reports for a ward and symptom come from one citizen account, no alert fires. That account is flagged, its trust score drops by 25, and the event is written to the audit log. A verified health worker's reports count as separate household visits, but one worker counts as at most 10 independent sources, and workers are never auto-flagged for filing many reports. The authority can still mark any report false.
5. **Authority moderation.** Officers can mark a report false (trust -15), verify, suspend, reinstate or ban an account. A trust score below 40 suspends an account automatically.
6. **Audit log.** Automatic flags and every officer action are recorded.

## Files

- `main.py`: API, detection, score, accounts, moderation, simulator.
- `static/`: `index.html` (landing), `login.html`, `app.html` (map and dashboard), `style.css`, `common.js`.

## Limits to state honestly

- One verified health worker can, on their own, push a ward over the alert threshold. This is a deliberate trade-off for field work. Every alert still says the pattern needs verification, and the authority can reject reports or suspend the account.
- Score weights and thresholds are our own design choice, not a validated epidemiological model.
- The 30-day baseline history is synthetic and wards are placeholders. Real use needs real ward boundaries and historical counts.
- Sign-in lockout counters live in memory and reset on restart. There is no email or phone verification yet.
- Tokens are kept in browser storage. Use HTTPS in any real deployment.
- General health tips on the landing page are not medical advice.

## AI assistance

Much of this code was generated with an AI chat assistant (Claude) and then edited by the team. Add here what each team member wrote or changed themselves.