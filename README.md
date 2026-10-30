# UpToDate prototype

Status: **Hosted prototype on Google Cloud Run with Supabase Auth, private Postgres runtime data and private image storage.** Muse Spark 1.3 supplies text inference, automatic real-photo discovery and web-grounded Ask Biggie; Muse Image is the fallback when source images are unavailable. Cloudflare Email Routing delivers app-owned private aliases to the backend without forwarding newsletters to personal inboxes. Migrated accounts retain their old Resend routes for existing subscriptions. General-public account confirmation-email delivery still needs appropriate Supabase sending configuration.

Working prototype with a native SwiftUI client and Python backend. The initial snapshot contains work completed before the hackathon, developed by Deeveshizm and Ujwal Jain with coding-agent assistance. Subsequent commits contain the later development work. Instagram supplies familiar feed conventions; the visual identity is provisional.

## Run the app

Open `native/UpToDate.xcodeproj` in Xcode, select **UpToDate**, choose an iPhone simulator and run. Native builds default to <https://uptodate-api-spx7xuvzfq-uk.a.run.app>. Sign in with a confirmed Supabase account. Using the hosted build requires no local backend, model process, or environment file. `make app` builds the macOS client against the same endpoint.

For isolated local development, use Python 3.11 or newer:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
make api
```

The local backend runs at `http://127.0.0.1:8000`; interactive API documentation is at `/docs`. Build a client for it with `make build-ios API_URL=http://127.0.0.1:8000`, or pass `UPTODATE_API_URL=http://127.0.0.1:8000` to `xcodebuild`. `make app API_URL=http://127.0.0.1:8000` selects the local backend for macOS. Physical iPhone acceptance and App Store distribution remain separate release work.

For an offline sample preview, use a local-backend build and add `--demo` to the Xcode scheme's launch arguments. This creates a local development account and loads the labeled sample edition without model inference. Importing `samples/newsletter.eml` also works without a model. The sample edition is a Muse-generated coffee roundup with example sender details, not independently verified content.

## Hosted deployment

The API runs on Cloud Run; a separate private Cloud Run worker processes authenticated Cloud Tasks. A transactional Postgres outbox records jobs before dispatch, named tasks make delivery idempotent, and worker claims prevent duplicate delivery from repeating active/completed work. Interrupted paid inference requires an explicit retry. A five-minute Cloud Scheduler job checks receiving, retries queued delivery and cleans unused media. Neither service requires a Mac to stay online.

Hosted execution requires `UPTODATE_ENV=cloud`, `AUTH_MODE=supabase` and `DATABASE_URL`. Content lives in the non-exposed `uptodate_runtime` schema, with direct access denied to anonymous/authenticated app roles. Images are stored in the private Supabase `post-images` bucket and returned through owner-checked API routes. Server credentials are version-pinned Secret Manager entries; only the public API origin is bundled with the app.

Install deployment dependencies with `.venv/bin/python -m pip install -r requirements-cloud.txt`. `deploy/gcp/deploy.py` provides `prepare`, `build` and `deploy` commands, with explicit project/region arguments. Runtime-only build contexts exclude environment files, local user data, native artifacts and tests. `scripts/migrate_cloud_data.py` defaults to a dry run; explicit `--apply --snapshot ...` imports an immutable SQLite backup while preserving identifiers and verifying copied fields and image bytes. Existing Supabase Auth/profile records are unchanged.

The deployment script uses zero minimum instances and two maximum instances per service, with two concurrent queued jobs. Its default inference limits are $0.25/job, $0.50/account/day and $2/project/day. Configure the corresponding budget variables to change them. These limits do not cap GCP infrastructure billing. Google Cloud budget alerts also notify rather than stop services.

## Configure inference

Keep credentials in an ignored `.env.local` file on the backend machine. Do not include them in the iOS target or commit them. The default hosted configuration uses:

```sh
GENERATION_PROVIDER=meta
MODEL_API_KEY=YOUR_META_API_KEY
META_MODEL=muse-spark-1.3
META_REASONING_EFFORT=low
IMAGE_GENERATION_PROVIDER=meta
```

Load those settings before starting the backend:

```sh
set -a
source .env.local
set +a
make api
```

Meta API calls consume credits. Text generation, photo lookup and fallback illustrations share the job/account/project spend ledger. Local defaults are $0.50 per job, $5 per account per UTC day and $10 per project per UTC day; the cloud deployment applies the lower defaults described above. Configure `GENERATION_JOB_BUDGET_USD`, `GENERATION_USER_DAILY_BUDGET_USD` and `GENERATION_DAILY_BUDGET_USD` to change them. Reservations and estimated charges are not a provider-enforced billing cap. See [Meta API pricing](https://dev.meta.ai/docs/pricing-rate-limits).

Optional local inference remains available through Ollama and ComfyUI. Run `make comfy` after installing ComfyUI and its image checkpoint in the sibling `../ComfyUI` directory, then run `make api-local`. `COMFYUI_DIR` can point to another installation. The bundled workflow is `backend/workflows/sdxl-lightning-api.json`; model weights and Python environments are not included. `make api-tailnet TAILNET_OLLAMA_HOST=YOUR_AUTHORIZED_PEER` uses an explicitly authorized Tailscale Ollama host. No collaborator address is embedded in this export.

## Accounts and receiving email

The default `AUTH_MODE=local` is for isolated local previews. Local aliases end in `.invalid` and cannot receive mail.

For real accounts, configure `AUTH_MODE=supabase`, `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` and the backend-only `SUPABASE_SECRET_KEY`. Apply the SQL files in `supabase/migrations` in filename order. Configure Supabase confirmation and password-recovery links to reopen `local.uptodate.prototype://auth/callback`. Password hashing and Auth sessions are handled by Supabase; device/Mac credentials use Keychain, with an app-sandbox fallback for unsigned simulator builds. This is standard account protection, not end-to-end newsletter encryption.

For app-owned receiving, configure `CLOUDFLARE_RECEIVING_ENABLED=1`, `CLOUDFLARE_RECEIVING_DOMAIN` and a random `CLOUDFLARE_INGEST_SECRET` of at least 32 characters. Deploy `deploy/cloudflare/email-worker.mjs` using `deploy/cloudflare/deploy.py --backend-origin YOUR_CLOUD_RUN_HTTPS_ORIGIN --apply`. The deployment script reads `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` from ignored local settings; these deployment credentials are not needed by the app or backend at runtime. Configure the domain's Email Routing DNS records and a catch-all **Send to a Worker → uptodate-email-receiver** rule. `deploy/cloudflare/configure_routing.py` can inspect the routing configuration before applying changes. Keep `RELAY_ALIASES_ENABLED=0` while using the runtime alias registry; the permanent public-profile alias mechanism is separate.

Each verified account receives its own stable, random alias. New addresses use a 15-character local part (`n_` plus 13 random base32 characters); existing Cloudflare addresses remain unchanged. A configured catch-all makes an alias active without a separate delivery-test email. Newsletter signup uses only the authenticated account's active alias; incoming mail is never forwarded to the sign-in email. Account deletion removes its primary and legacy receiving routes. Only anonymous address hashes remain reserved to prevent reassignment.

For existing Resend subscriptions, keep `RESEND_RECEIVING_DOMAIN` and the backend-only `RESEND_API_KEY` configured; receiving API access requires Full access. `scripts/migrate_relay_aliases.py` defaults to a dry run. With `--apply`, it assigns a Cloudflare primary while retaining the old Resend address as an account-owned legacy route. It verifies that sources, posts, bookmarks, read history, shares and jobs are unchanged in the migration transaction. Existing subscriptions can continue delivering to their old address; new signups use the new primary. No account deletion is required. `RELAY_TEST_USER_ID` no longer restricts receiving.

The receiving worker maps authenticated provider recipient metadata to the private alias registry. Unknown or ambiguous multi-recipient messages are not assigned to a guessed owner, and MIME headers or forwarded-for hints cannot select an account. Inbox suggestions, receipt counts, subscriptions, generation jobs and posts remain account-scoped. `GET /v1/relay` returns the caller's receiving status; `POST /v1/relay/sync` queues a check. The legacy `/v1/relay/test` routes remain compatible aliases.

Cloudflare sends a signed envelope to `/webhooks/cloudflare/email`; the signature binds the recipient, timestamp and raw-message digest. The backend validates ownership, durably queues messages up to 2 MB and wakes the private worker. Raw MIME is removed after a processing receipt is recorded; this is not a retained email archive. DKIM verification checks publisher-aligned signatures, including signatures after a delivery provider's signature. The optional legacy Resend poller scans at most 300 recent provider messages, so sustained higher-volume legacy receiving needs a webhook or durable cursor. Incoming mail and credentials are never committed. Supabase's default Auth sender is restricted; general-public registration still needs appropriate email-delivery configuration.

## What works

- **Source-aware posts:** original articles and papers are preferred to newsletter summaries. Longer stories use cited drafting and a separate accuracy checker, with one repair attempt. Recipes use natural cooking guidance and useful details instead of a research-paper template. Model checks are not a guarantee of factual correctness.
- **Preserved short emails:** complete 25–160-word editions retain source wording after greeting/delivery cleanup, without another drafting call. The app labels them **Original newsletter content**; their claims are not independently verified.
- **Email newsletter opt-in:** unfamiliar senders appear as suggestions. Choose **Yes, add newsletter**, name the stream, and import its latest edition plus future matches. Personal forwarding addresses also use a normalized subject topic to separate unrelated emails.
- **Source-first galleries:** original source photos/figures are tried first. Publisher branding declared in page structure or structured data is excluded from future image selection, including opaque or resized logo URLs. Named place recommendations can check linked websites, then discover photo pages through a bounded live Meta search. Only the place name and stated address are sent to that lookup. Returned pages must match the venue identity; lazy images are supported and duplicate size variants are removed. Discovered pages are cached, and no venue or photo URL is hard-coded. If retrieval fails, a labeled Muse Image illustration uses the post's content as context, with web/image search enabled and shell access disabled. Existing post images are not automatically regenerated by this update.
- **Minimal post controls:** icon-only contextual actions sit opposite Bookmark in Home, with larger controls and consistent spacing. Expanded posts put Mark read, Share, source/context actions and Bookmark in one horizontally scrollable row. Galleries use square 1:1 frames; photos fill the frame while research figures fit without cropping. Accessible labels retain the action names. Image captions and credits are available when an image is enlarged. The provisional app icon and lowercase system-font wordmark are easy to replace.
- **Native navigation and appearance:** iOS uses the system tab controller for selection, glass rendering and safe-area placement. The tab bar stays expanded while scrolling. Tabs retain their navigation state, and tapping Home returns to the top of the feed even from an open post. A chosen profile photo appears in the Profile tab. Profile includes a saved light/dark toggle and an option to return to system appearance. Biggie has a circular avatar and a slimmer chat composer. Mac uses native SwiftUI tabs.
- **Contextual actions:** recipes expose selected ingredients for a Reminders grocery list; place recommendations open Maps; other content opens Ask AI. The action is available in Home and the expanded post. Reminders contain ingredient names only.
- **Paper details:** the paper's actual abstract appears under **Abstract** in the expanded post. Private inline email originals are owner-only and cannot be publicly shared.
- **Unread Home and Read archive:** Home shows newer unread posts first, including older dates, in bounded pages. Explicitly choosing **Mark read** moves a post to **Read** in the Profile library. Opening a post alone does not mark it read. Saved posts remain available separately.
- **Newsletter management:** adding a link uses the account's relay address. Supported forms can submit automatically; JavaScript forms use the optional browser adapter. CAPTCHA-protected publishers require the user to finish in the in-app browser. Opening a website does not prove signup succeeded. Install Chromium with `make browser-install` and set `NEWSLETTER_SIGNUP_MODE=browser` to enable the adapter.
- **Confirmation and setup:** authenticated newsletter confirmation emails produce an in-app prompt and a Manage action. Only a user tap opens the private confirmation link; confirmation is never completed automatically by the backend. Pending links expire after seven days. The user can mark confirmation complete, or a subsequent authenticated welcome/edition can update the state. Finish setup copies the account alias before opening the publisher browser; Safari controls keyboard suggestions. Retry and Finish setup are capsule buttons, and the first-newsletter form has a circular Subscribe button.
- **Separate unsubscribe:** green visibility toggles only affect Home. Confirmed subscriptions offer Unsubscribe, which stops processing and uses available publisher instructions; manual confirmation stays pending until completed. Unfinished signups instead offer swipe-to-delete, which removes them from Manage, cancels queued signup work and stops processing matching mail. Resubscription reuses the source instead of creating duplicate toggles. Named scheduled-email streams can stop/resume processing; stop the sender's task separately to stop delivery.
- **Ask Biggie:** a post-aware conversation uses the configured backend provider. Meta Responses requests enable bounded web search and show a Sources menu when the provider returns URL citations. Search is available, but the model may answer without using it. Search/query and token charges share the spend ledger. Large-request heuristics suggest another AI app before making an inference call and highlight Open in. External handoff sends only the main source URL, or the original email text for an owner-authorized email source, without a typed question or chat transcript. The iOS sharing sheet shows compatible installed share extensions; iOS controls availability and ordering, and direct handoff to every AI app is not guaranteed.

## Data and export scope

Hosted content and media live in private Supabase services. Local databases, backups, incoming mail, media, caches and test artifacts remain under ignored `data/`. `.env` variants, local plans and new validation cases are excluded from this runtime export. Only README documentation is updated here; historical files already committed to this repository remain in its history.

The backend/native runtime and deployment files are synchronized with the current private checkout. Export-specific changes are this README and a Makefile without a personal Tailscale address or targets for omitted private checks. The release candidate passed 368 backend tests, Cloudflare Worker checks, native contracts, and iOS/macOS builds. Both remaining Resend accounts were migrated with content preservation checks and active legacy routes verified. A hosted disposable-account check verified a short Cloudflare alias, signed backend delivery, removal of pending setup, account/alias deletion and rejection of later delivery to that deleted alias. This check used a preconfirmed account, sent no external email and made no inference calls; it did not test fresh public signup confirmation. No paid model or publisher-signup test runs automatically when someone clones this repository.
