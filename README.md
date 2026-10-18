# Hyderabad 22K gold Telegram alerts

Checks only **Hyderabad, 22K gold, INR per 1 gram** on
[GoodReturns](https://www.goodreturns.in/gold-rates/hyderabad.html).
Runs about every 15 minutes through GitHub Actions. No third-party Python packages.

## Alert rules

The limit defaults to `13000` in `gold_alert.py` (`THRESHOLD_INR`). Change that line,
or set repository variable `GOLD_THRESHOLD_INR` to override it. Use INR per gram,
such as `13000`, not a price for 10 grams. Decimal arithmetic avoids float errors.

* Price **strictly below** the limit: send on the first observation, then whenever
  the observed price changes while below the limit (including increases).
* Same qualifying price on consecutive runs: no repeat message, including across days.
* Price equal to or above the limit: no message; remember it so a later return below
  the limit sends again, even if it returns to a previously alerted price.
* Changing the limit, bot token or chat starts a new alert context.
* A fetch, parse or Telegram failure fails the workflow and preserves previous state.

Example with a limit of ₹13,000: `13100 → 13000 → 12900 → 12900 → 12850 → 13100 → 12850`
sends at the first `12900`, at `12850`, and again at the final `12850`.
There are no daily reminders and no alerts for above-limit changes.

## Set up on GitHub

1. Create a Telegram bot using the official [@BotFather](https://t.me/BotFather).
   Save its token privately. Open your bot's chat and send `/start`.
2. Obtain your chat ID from Telegram's
   [getUpdates](https://core.telegram.org/bots/api#getupdates) response after sending
   a message: use `result[].message.chat.id`, not your username or bot ID.
   For a group, add the bot and send a command addressed to it; group IDs are negative.
   `getUpdates` is unavailable while a webhook is configured on that bot.
   To inspect updates locally without putting a token in a browser URL or shell history:

   ```python
   # Run in a local Python session. Do not paste the output publicly.
   import getpass, json, urllib.request
   token = getpass.getpass("Bot token: ")
   with urllib.request.urlopen("https://api.telegram.org/bot" + token + "/getUpdates", timeout=30) as r:
       print(json.load(r))
   ```

3. Create a GitHub repository (private is recommended). Copy **the contents** of this
   folder into its root, including `.github/workflows/gold-alert.yml`. Commit them to
   the repository's default branch. Do not place the files inside an extra subfolder.
4. Under **Settings → Secrets and variables → Actions → Secrets**, add repository secrets:

   | Name | Value |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | Token from BotFather |
   | `TELEGRAM_CHAT_ID` | Your private or group chat ID |

5. Under **Variables**, optionally add `GOLD_THRESHOLD_INR`, e.g. `13000`.
   Alternatively edit the Python constant and commit it. An existing variable takes priority.
6. Enable Actions if needed. The workflow requests `contents: write` so it can create
   and update a dedicated `gold-alert-state` branch using the automatic `GITHUB_TOKEN`.
   Your organization settings and branch rules must allow this. No personal access token
   is required. Do not apply rules requiring PRs or signed commits to this state branch.
7. Open **Actions → Hyderabad 22K gold alert → Run workflow**. Inspect the run logs.
   A successful run creates the state branch after the first successful observation.
   If the current price is at or above your limit, a successful run correctly sends nothing.
   To exercise delivery, temporarily set a limit higher than the displayed price and run
   manually; then restore your actual limit. This test sends a real Telegram message.

The workflow fetches the default branch, serializes manual and scheduled runs, and
stores state with a normal fast-forward Git push. It never relies on an expiring cache.
Do not delete or edit the state branch during normal operation. A repository-wide
ruleset that prevents this branch's creation/update must be adjusted before enabling alerts.
Enable GitHub Actions failure notifications in your account to notice blocked fetches.

## Local use and verification

Requires Python 3.12 or newer. From the project folder:

```sh
python -m unittest discover -s tests -v
python gold_alert.py --dry-run
python gold_alert.py --html tests/fixtures/current-layout.html --dry-run
```

The first dry run fetches the actual site; the second uses the documented synthetic
fixture. Both print the extracted price and threshold; neither sends nor saves state.
For a real local run, set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in your process
environment, optionally set `GOLD_THRESHOLD_INR`, then run `python gold_alert.py`.
Local state is stored in `state.json` in the current folder. Local runs and Actions
do not share state. Run only one local process and avoid running local sending alongside
Actions if you want a single stream of alerts. `.env` files are not automatically loaded.

## What was verified, and the live-access limitation

On 18 October 2026, the public page as returned by the web reader showed a table headed
`Today Gold Price Per Gram in Hyderabad (INR)`, columns `Gram | 24K | 22K | 18K`, and
the one-gram row `1 | ₹14,766 | ₹13,535 | ₹11,074`.
The extractor targets the Hyderabad page heading, this exact current-price table,
the **22K column by label**, and the **1-gram row**. It rejects missing or ambiguous
matches. It does not read the global ticker, narrative text, history or 10-gram prices.

**Direct automated requests from the development machine received a Cloudflare block.**
Consequently, successful raw-HTML Python extraction against the live site was not
established here. The included HTML fixture is reconstructed from the web reader's
table, not a saved raw response. A GitHub runner may also be blocked, and the real DOM
may differ from that reconstruction. The first manual Actions run is the required
live acceptance check. If HTTP 403/429 or a parser error occurs, no alert is sent and
no state is overwritten. Inspect an authorized raw page response and adapt the parser
if the layout changes; if access is denied, request access from the publisher. This
implementation does not bypass Cloudflare or silently use another price source.

Tests cover purity and weight selection, column order, wrong-city/blocked/malformed
pages, strict threshold boundaries, persisted duplicate suppression, changes below
the limit, re-entry below the limit, configuration changes, corrupt state and send failure.
All 13 tests passed locally. The Git integration test also verified first-time branch
creation, restoring and updating state, an unchanged-state no-op, preservation of the
main branch/index, and failure on an inaccessible remote. No real Telegram message
was sent during development; delivery requires your bot credentials and manual setup run.

## Scheduling and delivery limits

The cron runs at minutes 7, 22, 37 and 52 of each hour (UTC), corresponding to
minutes 7, 22, 37 and 52 past each IST hour, in a shifted order. It polls the site;
it cannot run precisely when the publisher updates a price, and it can miss changes
between polls. [GitHub schedules](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
can be delayed or dropped under load, run only from the default branch, and are
automatically disabled in public repositories after 60 days without repository activity.
Private repositories are subject to the account's Actions usage allowance.

State is saved only after Telegram confirms `sendMessage`. There is a small unavoidable
duplicate window: Telegram may deliver a message but the response can time out, or a
runner/state push can fail after delivery. A later run can then resend. Telegram's
[sendMessage API](https://core.telegram.org/bots/api#sendmessage) provides no idempotency
key, so this is duplicate suppression during successful runs, not exactly-once delivery.
The bot does not automatically retry an uncertain send. Review a failed run and your
chat before manually rerunning it. After a state-push failure, repair branch permissions
before rerunning. Corrupt or inaccessible state causes failure rather than resetting itself.

To intentionally reset alerts, disable the workflow first, wait for active runs to finish,
then delete only the dedicated `gold-alert-state` branch and re-enable the workflow.
The next below-limit observation will alert again. Do not reset merely to fix a network error.

GoodReturns describes these as indicative prices excluding GST, TCS and other levies;
actual jeweller prices and making charges can differ. Observe the publisher's access terms.
