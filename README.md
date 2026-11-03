# UpToDate prototype

## Private Muse delivery

The backend includes an account-restricted private API connector at `/connectors/muse`.
It is disabled by default. Enable it only with `PRIVATE_MUSE_CONNECTOR_ENABLED=1`,
`PRIVATE_MUSE_USER_ID` set to a verified Supabase account UUID, and
`PRIVATE_MUSE_EMAIL` set to that account's current email in backend settings.
For multiple approved testers, use `PRIVATE_MUSE_ACCOUNTS` as a JSON object mapping
verified account UUIDs to their current emails. An invalid allowlist disables access;
this is a private test setting and must never be bundled into the native app.
Apply `supabase/migrations/20261101000100_private_muse_connector.sql` before deploying
against Postgres. Keep these settings and all credentials out of Git.

In Profile → Connections → Muse, start setup to copy a connection prompt and open
Muse. The prompt first reuses an existing connector and verifies it with the
read-only `listCustomNewsletters` tool. If setup is needed, use the OpenAPI
specification at `/connectors/muse/openapi.json` and enter the account key only in
Muse's secure credential form. Creating a key shows a pending state; a successful
authenticated connector call confirms the connection in UpToDate. The credential
expires in 30 days; replacing or disconnecting it invalidates the previous key.
The pending raw key stays in account-bound memory and its clipboard copy expires
after two minutes. This private test does not implement public OAuth or refresh.

Each edition supplies a stable newsletter key, a unique edition ID and one or more
stories. Identical retries reuse the original job; conflicting content is rejected.
Original-source retrieval, verification, source-image selection and image fallback
reuse the existing engine. Deliveries appear under Custom in Manage, where Stop
processing blocks new editions. The connector can check that preference before
researching a scheduled edition. This does not cancel Muse's own recurring task;
cancel that schedule in Muse. Direct backend delivery needs no app update; the
native Profile → Connections → Muse controls require the updated app build.

Create newsletter has one row of actions: Create Directly copies the form's
request for the verified existing connector; the circular Share action copies
an email-delivery request containing the account's relay alias and opens Muse.
Copy copies that same email prompt. Direct requests contain no email address or
credential and never rotate the connector key. Muse confirms the schedule.
Opening the installed Muse app uses its universal link; automatic chat-field
prefill is not assumed, so the user pastes the copied prompt.

## Run

Open `native/UpToDate.xcodeproj` in Xcode, select the **UpToDate** scheme and an iPhone simulator, and run. Sign in with a confirmed Supabase account. The default API is <https://uptodate-api-spx7xuvzfq-uk.a.run.app>; no backend secrets are bundled with the app. `make build-ios` uses the same endpoint. `make app` builds the macOS app against it.

For an isolated local backend, first install the dependencies below, export your ignored local settings when needed, then run:

```sh
make api
```

The API runs at <http://127.0.0.1:8000>, with interactive documentation at <http://127.0.0.1:8000/docs>. Load your ignored `.env.local` into the shell first when using hosted inference or Supabase Auth. The Python environment and dependencies are already installed on this Mac. On a fresh checkout:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Build a client for that local backend with `make build-ios API_URL=http://127.0.0.1:8000`, or pass `UPTODATE_API_URL=http://127.0.0.1:8000` to `xcodebuild`. The hosted endpoint remains the project default. Seeded samples and `--demo` are for disposable local accounts, not the hosted account flow.

For a populated simulator preview, add `--demo` to the Xcode scheme's launch arguments. This creates a local account if necessary and loads the seeded sample edition.

The sample edition in `backend/demo.py` and `samples/newsletter.eml` both condense one real Muse-generated roundup ("Your Weekly Roundup — October 18": four East Bay/SF coffee openings and the early-season Tahoe snow picture). Source links are the ones that edition cited; the sender and signup URL are placeholders on `muse.example`. Posts are labeled as a seeded sample and are not independently verified.

## Try the complete flow

1. Explore the sample edition. Open a card, bookmark it, mark it read, and inspect Saved.
2. Open **Manage** to add a newsletter URL or **Import newsletter .eml**, choosing `samples/newsletter.eml` or an email exported from your mail client. EML importing remains offline and does not follow links.
3. Posts appear automatically in the private **Home** feed. Imported excerpts carry an **Original analysis pending** tag; generated posts with unfinished visuals carry **Image pending**. No manual approval is required for private reading.
4. The finite edition date uses your account's timezone. Source filters are under **Today's edition**. Muting in Manage hides a source without unsubscribing at the publisher.
5. Share post explicitly publishes an unlisted snapshot of the selected completed post, including its images, original links and paper abstract when present. Anyone with that link can read it without an account. Stop sharing revokes it; deleting the account also removes it. Raw email, account details, evidence and Biggie conversations are excluded. Open in AI apps continues sharing only the main source link. Legacy clients retain their source-only behavior.
6. Post prose follows the source: recipes discuss the dish, supported flavors/texture, technique and timing; papers discuss their actual contribution. Detail headings are optional source-chosen sections with checked citations. No generic limitations block is inserted. Grocery reminders contain only the selected ingredient item.
7. Source visuals form a swipeable gallery in Home and the expanded post. The first image sets one stable gallery frame, with tall images cropped at 4:5; pinch and pan reveal the original while held. Image credits stay available. Papers expose an expanded **Abstract** section containing the paper's original abstract.
8. A small icon-only pill at the lower left, opposite Bookmark, adapts to available source metadata: ingredients → choose items and a Reminders list; venues → Maps search; other stories/papers → Ask AI. Accessible labels retain the action name. Without reliable ingredients or venue metadata, the action stays Ask AI.
9. Ask AI offers a source-bounded Spark conversation in the app and an iOS sharing sheet carrying only the main source link. Chats and unfinished drafts stay in account/post-bound memory during the app session and survive feed refreshes or closing/reopening the chat. Signing out cancels requests and clears conversations. Requests use the configured backend provider and spend ledger.

The bottom tabs are Home, Saved, Manage, and Profile, following the latest collaborator UI. Home and Manage share a newsletter website form with explicit keyboard focus, URL input, a Paste button and inline validation. Bare website domains gain `https://`; an email address is rejected before an API request. The server supplies the account alias separately. Manage also contains EML import, source visibility and job progress/retries. Profile displays the full newsletter address and a copy action, plus account deletion. Deletion removes that account's database records and revokes its share links; the client also clears its cached data and session.

## Implemented

- Native iPhone/Mac client with source filtering, expandable captions, bookmarks, read state, external source links, and sharing.
- Local account sessions with high-entropy tokens stored in Keychain on devices/Mac; unsigned simulator builds use a protected file inside the simulator app sandbox. The server stores token hashes.
- One stable receiving alias per verified account when Resend is configured. Local development accounts use an explicit non-deliverable `.invalid` placeholder.
- Hosted Postgres persistence and private Supabase Storage, plus optional local SQLite; MIME/HTML extraction, script removal and per-account duplicate detection.
- Transactional job-delivery outbox, named authenticated Cloud Tasks, separate private Cloud Run workers and scheduled receiving. Duplicate deliveries do not rerun active/completed work; interrupted paid work requires an explicit retry.
- Original-source evidence, automatic private-feed placement, pending-work tags, separate public permission gates, escaped share pages, and signup CTAs.
- Cached feed/library for offline reading. Writes require the backend. Retry refreshes the cache.
- Job status and extraction failures visible in Manage. Jobs discard the temporary extraction payload after processing; retained post excerpts remain until account deletion.

## Deployment and prototype boundaries

Shared pages use `/p/{token}` with narrowly scoped image routes and a bottom beta/app CTA. Apply `supabase/migrations/20261101000300_post_share_pages.sql` before deploying. Set `PUBLIC_SITE_ORIGIN` to the public HTTPS origin and optionally `APP_INSTALL_URL` to a real distribution link; otherwise the CTA opens the product homepage. The website Worker proxies only product assets and shared pages, with no share caching. Google photo/details attribution is fetched live; Google photo binaries are not persisted in share snapshots.

Hosted deployments require `UPTODATE_ENV=cloud`, `AUTH_MODE=supabase` and `DATABASE_URL`. The server uses the non-exposed `uptodate_runtime` Postgres schema; existing Auth and profile tables are unchanged. All runtime tables deny anonymous/authenticated direct access, and the API verifies account ownership. Images remain in the private `post-images` bucket and are returned through owner-checked API routes. Server credentials live in version-pinned Secret Manager entries.

Cloud Run has separate API and private worker services, zero minimum instances and two maximum instances each. Cloud Tasks dispatches at most two concurrent jobs. A five-minute Cloud Scheduler job recovers delivery, checks receiving and collects unused media. Model reservations default to $5/job, $5/account/day and $10/project/day in the deployment script; these are inference limits, not GCP billing caps. A separate $5/month project budget sends billing alerts and does not stop services.

Install deployment tools with `.venv/bin/python -m pip install -r requirements-cloud.txt`. `deploy/gcp/deploy.py` provides `prepare`, `build` and `deploy` commands with explicit project/region options. Build contexts allow only backend code, Docker configuration and dependencies; environment files, local content, native artifacts and tests are excluded. `scripts/migrate_cloud_data.py` defaults to a dry run; an explicit `--apply --snapshot ...` copies an immutable SQLite backup, preserving identifiers and verifying every copied field/image. It never alters Auth/profile records.

Local mode still defaults to SQLite and `AUTH_MODE=local`; `AUTH_MODE=supabase` can use verified identities with local content. Local listeners bind to loopback, accept only local hostnames, and retain single-process restart recovery. A saved `DATABASE_URL` alone does not switch local execution to cloud mode.

The Resend adapter routes provider-reported recipients through a private per-account alias registry. Hosted receiving runs through scheduled/authenticated workers, so it does not depend on a Mac staying online. App-owned Cloudflare receiving and automatic publisher subscription confirmation remain follow-up work. Importing an `.eml` remains an explicit extractive action, not authenticated publisher delivery. Supabase's default Auth sender is restricted; general-public registration needs appropriate email-delivery configuration.

EML imports remain **offline and extractive**, available in Manage, and are clearly tagged as not yet analyzed from the original. The source-link engine retrieves public HTML/text/PDF originals and uses **Muse Wonder 1.0 in Standard mode** for drafting and a separate verification call by default. Set `GENERATION_PROVIDER=ollama` for explicit local testing. Successful text goes directly to the private feed, even if its image needs work; the post carries a pending tag. Invalid model output is not published. Machine checks do not guarantee accuracy. Public permission remains a separate human assertion. Seeded sample posts retain code-drawn covers and unverified-sample labels.

The backend is hosted, but Universal Links, App Store identity, broader publisher-signup compatibility, app-owned-domain receiving and operational hardening remain follow-up work. The private source-bounded Ask AI service is implemented; there are no likes, friends or creator posting.

## Verify

```sh
make test
make test-auth-native
make test-newsletter-native
make test-backend-native
make test-cloud
make build-ios
make smoke
```

`make test` and both native contract targets use temporary data and mocked services. Browser tests require `make browser-install`. For schema and RLS checks, install PostgreSQL and run `.venv/bin/python scripts/check_supabase_schema.py`; this starts and removes a disposable local cluster without accessing Supabase.

`make smoke` requires a separate API running in `AUTH_MODE=local`. It creates a temporary development account, exercises the import/review/feed/share flow, and deletes that account afterward. It is not compatible with a Supabase-authenticated server.

## Files

- `backend/app.py`: account-scoped API, queue worker, review/publication, sharing.
- `backend/pipeline.py`: offline MIME/HTML extraction and evidence drafting.
- `backend/store.py`: SQLite schema and transactional connections.
- `native/Sources/FeedComponents.swift`: feed card layout, cover illustrations, the profile glyph, and central styling constants.
- `native/Sources/UpToDateApp.swift`: app navigation, reading, review, and profile screens.
- `backend/demo.py`: the seeded sample edition summarized from the Muse roundup.
- `native/Sources/AppModel.swift`: API integration and local cache.
- `tests/test_workflow.py`: workflow, isolation, deletion, URL/markup handling, queue recovery.

Data lives in ignored `data/prototype.sqlite3` unless `NEWSLETTER_DB` is configured. Client snapshots live in the app's Application Support directory. Default text inference requires a backend-only Meta API key and consumes API credits. Offline EML import/sample data and explicit Ollama mode remain available without a paid inference API.

## Email/password accounts: optional Supabase configuration

The native client sends passwords directly to Supabase over HTTPS using its Auth REST endpoints. The Python backend never handles passwords or refresh tokens. It validates access tokens against `/auth/v1/user`, requires a confirmed email/non-anonymous identity, and uses the caller's token to read/update their RLS-protected profile. The client receives only the project URL and publishable key from `/v1/auth/config`; secret/admin keys stay on the backend. Missing hosted configuration fails startup rather than falling back to development accounts.

Auth and profiles live in Supabase when enabled; posts, bookmarks, jobs and images still live in local SQLite, keyed by the stable Supabase UUID. Returning sessions reach the same local account data. This does not provide cross-server content synchronization. Existing prototype accounts are preserved separately; they are not silently attached to an email address or migrated. Inference selection is independent of Auth: Spark is the default, and explicit Ollama mode remains available.

Setup requires project-owner access:

1. Inspect the hosted project's existing schema and policies before applying any SQL. For a fresh compatible project, apply the versioned migrations in `supabase/migrations` in order, including `20261027000100_profile_display_name.sql`. The repository does not automatically apply migrations to a hosted project.
2. Enable email/password authentication and email confirmation. Set the minimum password length to 12 in Supabase, so the service enforces the same rule as the app. Password hashing is handled by Supabase Auth, not implemented here.
3. For the initial two-person test, use Supabase's built-in sender with email addresses already in the project's organization team. No domain purchase or custom SMTP is needed for that limited test. The built-in sender currently permits two emails per hour; plan confirmation and recovery tests accordingly. General sign-up requires a suitable sending setup such as custom SMTP. Do not turn off email confirmation to bypass delivery restrictions.
4. Keep Supabase's default confirmation and recovery templates. New free projects using the default sender cannot customize them ([Supabase announcement](https://github.com/orgs/supabase/discussions/46599)). In **Authentication → URL Configuration → Redirect URLs**, allow exactly `local.uptodate.prototype://auth/callback`. No custom domain is required. The native app uses PKCE challenges for signup/recovery and exchanges the callback's one-time code with a random verifier retained on the initiating device. Unrequested, malformed and token-bearing callbacks are rejected. Signup resend and recovery respect server rate limits.
5. Create the ignored configuration file:

```sh
umask 077
touch .env.local
chmod 600 .env.local
```

Edit it locally with `AUTH_MODE=supabase`, `SUPABASE_URL` set to the project's HTTPS URL, and `SUPABASE_PUBLISHABLE_KEY` set to its **publishable** key (or legacy anon key). For account deletion, also configure `SUPABASE_SECRET_KEY` with a backend-only secret/service-role key. Never put that key in the iOS app, Git, chat, logs, or a screenshot. Without it, deletion reports an unavailable service instead of claiming success. Environment files are intentionally excluded from Git; each collaborator creates their own local settings.

Load that file into the shell, then start the backend:

```sh
set -a
source .env.local
set +a
make api
```

Run the iOS app normally, without `--demo`. It offers sign-up, email-link confirmation, password sign-in, email-link recovery, profile name/timezone editing, sign-out and deletion. Open email links on the same device that requested them. For the simulator, copy the email link into the simulator's Safari; clicking it in the Mac's browser will not route into the simulator. After a recovery link returns, the app displays a new-password form. Pending verifiers and recovery sessions survive app restarts in separate secure-store entries. Access/refresh credentials are saved in Keychain on signed devices/Mac. The existing unsigned-simulator fallback is a protected sandbox file, not a production Keychain substitute. The app rotates refresh credentials, retries one unauthorized request after refresh, and clears cached account data on sign-out. Transient account-service failures do not automatically delete the session. Hosted sign-out does not promise immediate invalidation of every previously issued access token.

**Protection boundaries:** Supabase documents encryption at rest and TLS for its hosted services; RLS and backend ownership checks still provide access control. This is not end-to-end encryption. The server must read source content for generation. Local SQLite is not encrypted by this application; local disk protection such as FileVault must be configured separately. Mac caches and unsigned simulator files do not acquire hosted encryption simply because Auth uses Supabase. Hosted builds use HTTPS for the API; explicit local-development builds can use loopback HTTP. Never describe this prototype as fully encrypted or production-ready until these remaining storage/deployment boundaries are addressed.

Verification: `make test` uses isolated mocked Auth, never live credentials. `make test-auth-native` compiles/runs the native Auth client's HTTPS-request, PKCE challenge/exchange, callback rejection, session, refresh and recovery contracts using URLProtocol mocks with no real network calls. `scripts/check_supabase_schema.py` validates all migrations and owner-profile policies in a disposable local PostgreSQL cluster. Separate live acceptance checks on October 27 used the existing Supabase development project, not a hosted sandbox: signup confirmation and password recovery succeeded, and the user confirmed sign-in with the new password. Two disposable users exercised profile/feed isolation, password updates, and account deletion with local content cleanup; the main account was preserved. Signed-device Keychain behavior remains unverified. These live checks are not part of `make test`.

References: [password authentication](https://supabase.com/docs/guides/auth/passwords), [password storage](https://supabase.com/docs/guides/auth/password-security), [SMTP restrictions](https://supabase.com/docs/guides/auth/auth-smtp), and [hosted encryption](https://supabase.com/security).

## Permanent relay address assignment: implemented, not activated

`20261027000200_relay_aliases.sql` adds operator-only receiving-domain settings and a caller-bound `ensure_relay_alias()` RPC. After activation, the backend calls it when a verified user opens their account. It assigns one random, stable address per account without deriving it from the main email or identity. Addresses remain `provisioning` until an incoming-delivery test is verified; only active aliases are passed to newsletter agents. Deleted-account addresses retain an ownerless reservation so they are not reused. No forwarding to the sign-in email is implemented.

The receiving domain can be provider-managed or app-owned. Permanent receiving is planned around an app-owned domain with Cloudflare Email Routing/Workers. The Resend receiver below currently uses a provider-managed domain for verified accounts. No Cloudflare deployment or permanent hosted alias has been activated. Address assignment alone is not proof of incoming delivery.

The migration has passed local ownership, uniqueness, readiness and tombstone checks, and the iOS build succeeds. Apply the additional migration to the hosted project only after reviewing it, configure the chosen receiving domain, and enable `RELAY_ALIASES_ENABLED=1` in the local backend only after migration. Permanent alias activation remains disabled; the Resend receiver below works independently. Do not rerun the initial schema setup.

### Multiuser receiving on the Resend-managed domain

`backend/receiving.py` assigns a stable random alias to every verified account on a configured Resend-managed domain. Configure backend-only `RESEND_RECEIVING_DOMAIN` and `RESEND_API_KEY` (Full access). `RELAY_TEST_DOMAIN` remains a legacy domain-setting fallback; `RELAY_TEST_USER_ID` is ignored and no longer restricts receiving. Keep `RELAY_ALIASES_ENABLED=0` while using this runtime registry: the permanent public-profile alias mechanism is separate. Keys stay in ignored local settings or cloud Secret Manager, never in the app or Git. The adapter itself makes bounded HTTPS GET requests only; it does not send or forward mail.
Start the API with the local settings exported:

```sh
set -a
source .env.local
set +a
make api
```

1. Sign in with a verified account. Profile shows its stable random alias. A configured Resend catch-all enables receiving immediately, without a separate delivery probe per user.
2. Local receiving polls every 30 seconds; the hosted worker checks on the maintenance schedule. Provider-returned top-level recipient metadata selects one registered owner. Ambiguous multi-recipient messages, unknown addresses, MIME-header recipient hints, and forwarded-for hints cannot select another account. No mail is forwarded to the sign-in email.
3. Paste a newsletter signup link into empty Home or Manage. The backend submits a supported form using the active alias belonging to the authenticated account. Previously saved links waiting for an inbox resume after the account alias becomes active.
4. Supported static forms use HTTPS POST and one unambiguous email field. With `NEWSLETTER_SIGNUP_MODE=browser` and `make browser-install`, unsupported static forms fall back to isolated Chromium: the JavaScript page renders, its visible email field is filled with the server-assigned alias, and its signup control is clicked once. A fresh context has no personal browser cookies. All requests use bounded, DNS-pinned public connections; service workers, WebSockets, downloads and account-administration requests are blocked. This adds no inference/token cost. CAPTCHA, additional required information and consent stay `manual_action`. A successful submission is `submitted`, never proof of a confirmed subscription. Confirmation emails are recorded and can be opened manually in Resend's Receiving dashboard.
5. Incoming newsletters with eligible public article links queue durable `received_email` jobs. They use the source engine, configured Spark/Ollama drafting and verifier, original-source resolution and source-first images. Link labels and surrounding context are retained; eligible stories run in durable batches of four instead of silently stopping at the first three links. Imports retain at most 50 eligible links and report when that safety limit is reached. Plain probes and account-action emails create no posts. An edition without usable public links does not yet generate from email-only content.
6. Completed private posts appear automatically in Home. The app checks for newly arriving jobs even when no generation was previously running. Provider-ID and normalized-content deduplication prevent redelivery from rerunning the same edition. Failed source jobs retain retry data; Manage shows subscription status without raw processing jobs.

Unknown email senders first appear as a newsletter suggestion. Choose **Yes, add newsletter**, give it a name, and the latest edition plus future matching editions enter the pipeline. Personal forwarding addresses also use a normalized subject topic so unrelated forwarded emails do not become the same newsletter. Inline email content can be the original source; street addresses and map links drive the places action, and linked photos are tried before image generation. Private email originals are readable only by their owner and cannot be shared publicly.

Complete short emails (25–160 words after removing greetings and delivery boilerplate) keep their source wording instead of making another drafting call. They are labeled **Original newsletter content**, distinct from machine-checked AI summaries. This preserves the source's content; it does not independently verify its claims. Longer content uses source-aware narratives and optional sections, without generic email/author narration or a fixed research-paper template for recipes.

For recommendations of named places—including restaurants, cafés and other venues—the visual pipeline tries source photos, linked venue websites, then live discovery through Meta's search-grounding API. The lookup sends only the venue names and stated addresses, with one bounded tool call, no stored Responses conversation and no automatic retry. Returned pages must match the place name and street address before their images qualify. Lazy-loaded images are supported, and duplicate size variants are removed. Discovered page URLs are cached for seven days to avoid repeating paid searches; empty results are cached briefly. No venue names, city tourism sites or photo URLs are hard-coded. Search uses the existing inference spending ledger and incurs token/query charges described in [Meta's pricing](https://dev.meta.ai/docs/pricing-rate-limits). A context-specific, labeled Muse Image illustration remains the fallback when usable real photos cannot be retrieved.

For publishers requiring human verification, including 1440, **Finish signup on publisher website** opens an in-app browser and provides a relay-address copy control. Complete the publisher's verification yourself. Opening or closing the browser never marks signup successful; publisher mail updates the receiving state.

Home shows unread posts across dates, newest publication date first, with bounded pages loaded as you scroll. **Mark read** removes a post from Home and keeps it under **Read** in the hamburger menu. Opening a post alone does not mark it read. Newsletter toggles still control Home visibility; Read preserves already-read posts even when a source is hidden. The feed API supports `history`, `read_state`, `limit` and an owner-scoped `before` cursor; explicit daily queries remain available.

Subscription progress and public content discovery are separate: a public post is not proof of signup. Manage shows queued/submitted/confirmation/receiving states and provides an explicit alias-bound signup retry. Submitted registrations are not silently resubmitted after a restart. Publisher-domain matching joins incoming editions to the existing newsletter source when unambiguous, so receipt/generation status appears on the source the user added. The Resend receiver resolves registered account aliases from authenticated provider recipient metadata. App-owned Cloudflare envelope routing remains a separate integration.

Manage also provides a separate **Unsubscribe from newsletter** action with confirmation. The **Show in daily feed** toggle only changes visibility. Unsubscribe stops new jobs for the source, retains existing posts and uses authenticated publisher mail's `List-Unsubscribe` instructions or an unambiguous HTTPS footer link labeled Unsubscribe. RFC 8058 one-click requests use HTTPS POST; ordinary header/footer unsubscribe links open the publisher page for the user to finish. Footer links never authorize an automatic one-click POST. Completing a publisher page manually does not automatically confirm the app's pending status; the AlphaSignal acceptance test below was reconciled by the operator after observing publisher confirmation. Instructions are stored only in the private local action table, never exposed on source/feed responses or sent to the model. Without instructions, the app reports publisher unsubscribe pending rather than success; when a verified one-click instruction arrives, the pending request can finish. This first version requires Resend's server-computed DKIM or DMARC pass before accepting those mail actions.

Authenticated `GET /v1/relay` reports only the caller's receipt counts and readiness; `POST /v1/relay/sync` queues a provider check. Legacy `/v1/relay/test` routes remain compatible aliases. Regression checks cover distinct account aliases, alias-only signup, private candidate/post access, redelivery, revocation during fetch and concurrent provisioning on PostgreSQL. A real user-sent delivery test was received on October 27 and activated the assigned alias; the plain test created no post. Adding a newsletter link also produced a real `ollama:llama3:8b` post, machine-checked and visible in private Home with a ready original-source image. That generation came from public-page discovery and initially did not submit signup. After the JavaScript adapter and repeated-response-header fix were tested, the live AlphaSignal signup returned `submitted`. Its welcome message arrived at the assigned alias from `mail.alphasignal.ai` with server-computed SPF, DKIM and DMARC passing, and was joined to the saved source as `email_received`. The welcome message contained no authenticated unsubscribe instructions and was intentionally not made into a post. A subsequent user-sent email containing an arXiv link queued a received-email job, generated a machine-checked local-model post, and displayed the original paper's SVG figure as PNG without calling image generation. On October 27, a later authenticated AlphaSignal edition supplied an HTTPS footer unsubscribe link but no List-Unsubscribe header. The backend returned the manual publisher action; the isolated browser clicked Unsubscribe and the publisher displayed “NOTE: YOU ARE ALREADY UNSUBSCRIBED.” No feedback survey was submitted. The operator recorded the verified result in local subscription state. The existing post and feed visibility were preserved. Regression coverage verifies that later editions are ignored after an unsubscribe request; long-term delivery cessation has not yet been observed. Password recovery and disposable-account deletion passed separately as described above.

Simulator input note for Xcode 27: the Home field has passed a direct simulator typing test. On this installation, legacy `simctl pbcopy` and `pbsync` returned success while retaining old text. `xcrun devicectl device pasteboard transfer to --device <simulator-uuid> --data-policy all-resolved` successfully transferred the Mac clipboard; readback through `devicectl device pasteboard paste` matched. Use the current CoreDevice pasteboard API for that local workaround. A physical iPhone's own keyboard/clipboard has not yet been tested. `make test-newsletter-native` checks website normalization and rejection of pasted email addresses without a network request.

The receiver scans at most 300 recent provider messages per poll and bounds responses to 2 MB. This supports small multiuser tests rather than sustained high-volume ingestion; a webhook or durable cursor is needed before expanding the receiving volume. Resend's header-derived `received_for` is never used to assign ownership. Existing provider aliases remain reserved after account deletion and are never reassigned. Cloudflare receiving will be added alongside the current provider so existing subscriptions can retain their delivery address. Local SQLite is not application-encrypted.

## Source-to-post engine

Muse Wonder 1.0 is the default. Keep these settings in ignored `.env.local`:

```sh
GENERATION_PROVIDER=meta
META_MODEL=muse-wonder-1.0
META_REASONING_EFFORT=low
# Also save MODEL_API_KEY locally; never commit or send it to the native client.
```

Load the settings before starting the API (`set -a; source .env.local; set +a`). The adapter uses the fixed HTTPS Meta endpoint, Standard mode, strict JSON schemas and a maximum of 5,000 output tokens per call, including reasoning. It does not silently switch providers, use Contributor mode, call search/tools or automatically retry failed HTTP requests. `GET /v1/engine` reports the selected provider/model and readiness without exposing its key.

Venue photos use `VENUE_PHOTO_PROVIDER=source_first`: keep a usable newsletter photo, then try the official website, press/reference pages and Commons/Openverse, followed by public-page discovery and Google/Facebook fallbacks. Muse Image runs only when real-photo retrieval is exhausted. Branch matching and filters reject branding, maps, stock/decorative images and promotional social-feed posts. This mode does not require an explicit website permission marker to try a photo; source URLs and credits are retained. Google Places photos remain live with attribution; other saved post images use the existing private image cache. Set `GOOGLE_PLACES_PHOTOS_ENABLED=1` with a backend-only Google Places key to enable that fallback. The stricter `licensed` mode remains available for testing.

For explicit local inference, start `make model` in another terminal and use `make api-local`. This selects the existing `llama3:8b`; no model downloads are performed automatically.

Start/restart the API with `make api`. Adding a newsletter now automatically discovers and processes public content in the backend:

- `POST /v1/newsletters` saves a publication URL and requests signup using the account's existing single alias. The backend also recognizes a submitted public article/issue, an advertised RSS 2.0/Atom feed, or an explicit latest-issue link. It selects at most one public issue per add operation (newest dated item among the first 20 feed entries, otherwise feed order), then creates a durable generation job automatically. Recognized roundups process separate linked stories in batches of four; remaining stories stay queued. Ordinary articles produce one draft. No user-facing generation choice is needed.
- Signup and content processing are independent. The placeholder `.invalid` alias reports `needs_inbox`, but accessible public content can still generate drafts. A deliverable alias requires a configured signup-agent adapter (`needs_agent` otherwise). No signup is attempted against a placeholder address, and submission never implies email confirmation succeeded.
- The newsletter job result includes `content_discovery` and `generation_job_id`. No accessible issue gives `waiting_for_content`; a broken/blocked feed gives `discovery_failed`. A discovered issue gets its own retryable generation job. Completed text appears automatically in the private feed; pending images are tagged and processing failures appear in Manage. A signup landing page is not treated as article content.
- `POST /v1/generation` remains a backend/developer diagnostic endpoint for public article, paper HTML/PDF, or newsletter edition URLs; it does not need to be exposed in Add newsletter. Explicit Article/Digest modes remain available there.

The latest GitHub Manage-tab layout is integrated with the backend: Add newsletter calls `POST /v1/newsletters`, has no Generate post/source-type choices, polls processing jobs, and displays authenticated source images and pending tags in Home. The newsletter name is the display author; `original_url` and evidence refer to the original paper/article. The true source author is retained separately in `analysis.provenance.content_author`.

Discovery runs once on add; there is no recurring public-feed poller. The Resend receiver polls incoming mail locally or through scheduled hosted workers. An existing duplicate add does not crawl again; pending signups that need a receiving address can resume after the account alias becomes active. The engine never substitutes the main email for an alias. Supported HTML forms can use the deterministic signup adapter; the Muse adapter awaits identification of the intended service/runtime. Inbound confirmation remains manual. EML import remains available and extractive.

The coffee sample is `samples/newsletter.eml`; its Muse signup URL is a placeholder, not a public newsletter archive. A real coffee-related source already cited there is [The Infatuation's SF openings guide](https://www.theinfatuation.com/san-francisco/guides/new-san-francisco-restaurant-openings). To exercise the automatic Add newsletter path on that source, without a signup submission:

```sh
.venv/bin/python scripts/smoke_api.py --newsletter https://www.theinfatuation.com/san-francisco/guides/new-san-francisco-restaurant-openings --output data/coffee-generation-test.json
```

To test AlphaSignal's advertised public feed through the same automatic path, including a ready, retrievable image (run `make api` with the Meta credentials loaded first):

```sh
.venv/bin/python scripts/smoke_api.py --newsletter https://newsletter.alphasignal.ai/ --output data/alphasignal-final-test.json --require-image
```

This uses a temporary account, saves text/image test artifacts locally, and deletes that account's server records afterward. It does not insert posts into your simulator account.

The pipeline is: bounded retrieval → linked original paper/article → section-balanced extraction → deterministic evidence packet → typed short draft → evidence/number/voice/length checks → compact, separate model verification → source-first gallery → private feed. The normal text path uses two model calls; one targeted repair is allowed. Evidence is supplied once to the verifier, with claim-to-span references. Source figures and original abstracts require no model-generated replacement. Text publishes without waiting for a generated fallback image; that work runs as a separate durable job. If an explicitly linked original cannot be read, the job reports that failure rather than silently substituting newsletter text. The writer receives at most eight selected original passages within a 10,000-character budget and can cite only their IDs. Its visible post is capped at 200 words, with a 120–180-word editorial target. Headline/hook/body are distinct, and first-person authorial voice is rejected. The server attaches exact source text. Repairs receive the rejected draft and field verdicts. Missing problem/method/result/impact fields remain null. One text repair attempt is allowed. Model verification is not a guarantee of factual correctness.

`POST /v1/posts/{id}/regenerate` regenerates an existing private source-engine post from its original source, retaining the post ID and bookmarks. Replacement is transactional and requires valid text and a ready image; on failure the previous post is preserved. Existing public posts cannot be silently regenerated. This is useful for updating earlier newsletter-derived test posts without mislabeling their old evidence as original research.

Retrieval validates resolved addresses and pins connections to a validated public IP while retaining HTTPS hostname verification. Redirects are checked again, HTTP downgrade is blocked, and robots.txt restrictions are honored. Downloads are limited to 6 MB; extracted evidence is limited to 18,000 characters / 50 spans, sampled across relevant sections from at most 160,000 characters; PDFs examine at most 25 pages. Later results and limitations are considered before the introduction consumes the budget. JavaScript-only pages, paywalls, encrypted/scanned PDFs, and sources that block automated retrieval need a different public original or manual import. Nothing bypasses these restrictions. Source HTML and PDFs are untrusted data and are never given tool privileges.

Source evidence and job state are stored per account. Generated artifacts from verified, anonymous, query-free public sources can be reused across accounts using a source-revision/model/prompt key and an atomic claim. Tracking URLs are shared only after a clean URL has been fetched and verified to return the same revision; signed/unknown-query/private sources remain tenant-scoped. Each account retains its own private post association, bookmarks, read state and newsletter provenance. A story discovered through multiple newsletters appears once in that account and remains visible while any associated source is unmuted. The local schema keeps compatibility snapshots in private post rows; a fully normalized hosted store remains deployment work. Existing databases receive additive migrations. Deleting an account deletes its source evidence too. Failed source jobs retain their request for explicit retry; successful jobs discard their temporary request payload. Source revisions already represented by a nonrejected post are deduplicated per account. The API accepts `refresh: true` to re-fetch a changed page.

In explicit local mode, choose another installed model with `GENERATION_PROVIDER=ollama OLLAMA_MODEL=your-installed-model make api`. `OLLAMA_URL` defaults to `http://127.0.0.1:11434`. The adapter uses Ollama's documented [JSON schema output format](https://docs.ollama.com/capabilities/structured-outputs), includes the output schema in the prompt, and validates the result server-side. That explicit Ollama mode does not call Meta. The default Meta mode does.

### Usage limits, source details and validation

The application reserves a conservative maximum before each paid call, then reconciles the reservation using reported input/output/cached/reasoning usage. Defaults are **$5 per job, $5 per account per UTC day, and $10 per backend database per UTC day**. Override with `GENERATION_JOB_BUDGET_USD`, `GENERATION_USER_DAILY_BUDGET_USD` and `GENERATION_DAILY_BUDGET_USD`. Unknown timeout outcomes retain their reservations; rejected authentication/rate-limit requests release them. Account deletion anonymizes retained spend totals rather than resetting the shared budget. These limits cover this backend's ledger, not unrelated tools or other databases using the same Meta account. Direct CLI diagnostics consume credits separately.

Google Places requests are counted separately in `resource_usage`, per API SKU and calendar month in US Pacific time. Photo requests and Enterprise detail/search SKUs each have 1,000 monthly free requests; Pro detail/search SKUs have 5,000, and photo-metadata-only details are unlimited. Requests within those allowances reserve **$0**, so they remain available even when a dollar cap is exhausted. Only estimated billable overage enters the paid-spend total. Counters are shared across accounts and worker replicas and survive account deletion. Explicit rejections count conservatively as attempts, with their monetary reservation released; uncertain outcomes retain reservations. These are usage estimates rather than a provider invoice.

Apply `supabase/migrations/20261102000100_resource_usage.sql` before deploying. `.venv/bin/python -m scripts.usage_report` prints the current counters without credentials or newsletter content. Counters cover this backend, not unrelated applications using the same Google billing account. Known external usage can be included with `RESOURCE_FREE_USAGE_OFFSETS`, for example `{"google_places":{"2026-11":{"place_photos":100}}}`. Offsets apply only to the named month. Pricing/allowances follow [Google’s pricing list](https://developers.google.com/maps/billing-and-pricing/pricing).

`analysis.usage` records all text calls, tokens, reasoning and estimated inference cost. Cached attachments report zero new text-inference cost. Failed semantic checks retain usage with the failed item. Partial digests preserve completed posts and keep failed stories retryable. Startup recovers model jobs in the worker rather than blocking the API. This remains a single-process SQLite prototype; distributed workers require shared Postgres, leases and durable dispatch before deployment.

The API returns `paper` (original abstract/title/authors/DOI/revision), an ordered `visuals` collection and `content_action` metadata. Gallery images use account-authorized `/v1/posts/{post_id}/images/{asset_id}` routes. Explicit structured recipe ingredients and venue addresses can populate action metadata without inference. Native abstract/gallery presentation, Reminders/Maps actions and the grounded Ask AI service are follow-up features; returning metadata does not mean those native interactions are implemented.

Run isolated, mocked regression checks with `make test`. `.venv/bin/python -m scripts.smoke_spark_pipeline` performs a **real, charged Spark test** against a public paper in two disposable local accounts, verifies cache reuse and image access, and deletes the local test accounts. It does not mutate hosted Supabase accounts or your existing feed. Private artifacts live under ignored `data/spark-pipeline-acceptance/`.

References: [Meta models](https://dev.meta.ai/docs/models), [Standard pricing](https://dev.meta.ai/docs/pricing-rate-limits), [structured output](https://dev.meta.ai/docs/structured-output).

### Collaborator's Ollama over Tailscale

The collaborator-authorized test host is `100.121.255.126:11434` (`macbook-pro-3`). Tailscale connectivity and the model list were verified; availability depends on that MacBook being awake and connected. Installed models at that check: `llama3.1:8b`, `gemma3:latest`, `deepseek-r1:1.5b`. Nothing downloads or removes models.

```sh
make api-tailnet  # local FastAPI + collaborator's llama3.1:8b text inference
```

The target sets `OLLAMA_URL=http://100.121.255.126:11434`, `OLLAMA_TRUSTED_HOST=100.121.255.126`, and `OLLAMA_MODEL=llama3.1:8b`. Override `TAILNET_OLLAMA_HOST` / `TAILNET_OLLAMA_MODEL` only for an explicitly authorized peer and an already-installed model. The adapter requires the exact configured literal Tailscale IPv4 address on port 11434; there is no blanket private-network allowlist. The public article fetcher still rejects Tailscale/private addresses.

This adapter uses the native `/api/tags` and `/api/chat` endpoints at the server root, **not** the OpenAI-compatible `/v1` base. It needs no dummy API key. It sends no `/pull`, `/delete`, or model-management calls, and does not override remote model residency with `keep_alive`. Selected source text and prompts are sent to the collaborator's machine over Tailscale; image fallback uses the configured backend Meta key. Keep Tailscale access restricted; the Ollama service has no separate authentication.

There is no silent fallback to a different server/model if the peer sleeps. Jobs fail with a Tailscale/awake diagnostic and retain their retry request. Ask the collaborator to wake/connect the MacBook, then retry the failed generation job.

For manual debugging, `scripts.generate_source --trace data/your-trace.json` saves intermediate evidence selection, drafts, and verification locally. Traces include source text; keep them private and outside Git. `python -m scripts.check_verifier` tests supported paraphrases and deliberately false location/health/number/negation claims against the configured model. These live checks are separate from the mocked unit tests.

Test the real installed model without the UI:

```sh
make engine-test
.venv/bin/python -m scripts.generate_source https://ollama.com/blog/structured-outputs --output data/public-source-test.json
```

The first command uses an original local fixture, explicitly labeled in the output. The second fetches the specified public source. Generated JSON artifacts in `data/` are local and ignored by Git. Unit/integration tests use fake retrieval and model adapters for deterministic failures; these CLI checks run actual local inference.

New modules: `backend/retrieval.py` (public transport), `backend/documents.py` (HTML/PDF evidence), `backend/generation.py` (Meta/Ollama adapters and checks), `backend/editorial.py` (compact drafting/verifier), `backend/artifacts.py` (shared generation claims), `backend/budgets.py` (spend reservations), `backend/source_engine.py` (durable orchestration). API additions: `GET /v1/engine`, `POST /v1/newsletters`, `POST /v1/generation`, and `POST /v1/jobs/{id}/retry`.

## Supabase content schema preparation (SQLite remains the content runtime)

Target project: `uptodate`, project reference `vfflespysusisxdbynap`. The migration is `supabase/migrations/20261018000100_initial_backend.sql`. It was reviewed against the collaborator's `feat/home-card-trim` commit `0667085ca7509d9bfe71913069ee625b15cad912`, now merged into GitHub `main` at `609f21491939ef43188ee7c1efa172083e32ce6b`. Inspecting/fetching that code did not overwrite local UI or backend edits.

This is a content-schema foundation, **not a completed content migration**: FastAPI still uses SQLite for feeds/jobs/media. Optional Supabase Auth validation is implemented as described above, pending configuration and live acceptance testing. No SQLite data or sessions have been uploaded. Hosted deployment, a Postgres store/worker adapter, Storage upload/deletion, and existing-account migration still need implementation and hosted integration testing. Creating tables alone does not switch the content runtime or fix local-model grounding failures.

The new UI continues to consume these contracts:

| UI / engine need | Supabase representation | API compatibility requirement |
| --- | --- | --- |
| Profile and full receiving alias | Auth identity + private `profiles`, nullable unique alias, timezone | Return alias as a string (empty until configured), never a fabricated deliverable address; Profile displays the full address with a copy action |
| Newsletter visibility and signup | Owner-scoped `sources`, `muted`, signup/feed URLs and truthful subscription state | Serialize boolean `muted` as `0`/`1` for the existing Swift model |
| Manage / review / processing | Owner-scoped `jobs` and `posts`, status/stage/errors/results | Keep existing `id`, `subject`, `status`, `error`; new generation fields are additive |
| Private unread feed and read archive | Approved `posts.published_date`, profile timezone, source visibility and explicit reads | API keeps `date`, `posts`, `total`, `unread`, `finite`, adding bounded `next_cursor` pagination |
| Saved posts and reading state | Owner-scoped `bookmarks` and `reads` referencing approved posts | Keep per-post `bookmarked`/`read` booleans; counts are derived, not separately stored |
| Grounded content and source-first visuals | Private `documents`, post evidence/analysis/visual JSON, `post_assets` metadata | Keep title/hook/finding/limitations/original URL/evidence; expose image/provenance fields additively |
| Sharing | Backend-only hashed `shares` tokens and explicit public-permission timestamps | Private shares use the original URL; public previews require backend authorization, not public table access |

The collaborator's external-AI feature removal is preserved; no chat/prompt-history tables are introduced. Link-add, job-polling, real-image bindings and pending tags are now integrated with their layout.

All ten app tables have RLS. Clients read only their own account data; only the backend writes jobs, originals, generated content, review decisions, aliases, and media metadata. Limited client writes cover timezone, source visibility/signup URL, and approved-post saved/read state. Composite foreign keys prevent cross-account relationships even in backend writes. Raw source documents, job payloads, share hashes, and the cleanup queue are not exposed to clients. This follows Supabase's [grant and RLS model](https://supabase.com/docs/guides/database/postgres/row-level-security).

Images use the private `post-images` bucket, PNG only, up to 6 MB. A readable object must belong to the signed-in account's folder **and** have registered asset metadata. Backend-only uploads prevent clients from inventing source provenance. Asset replacement/deletion queues the old object for deletion through the Storage API; a cleanup worker and failure/retry handling must be implemented before claiming physical image deletion is complete. Removing metadata immediately denies client reads but does not itself delete storage bytes. Avoid issuing long-lived signed URLs that outlive account deletion. Supabase documents [Storage access policies](https://supabase.com/docs/guides/storage/security/access-control) and [account deletion caveats](https://supabase.com/docs/guides/auth/managing-user-data).

Validate locally using installed PostgreSQL binaries:

```sh
.venv/bin/python scripts/check_supabase_schema.py
```

This creates a disposable PostgreSQL cluster with minimal local Auth/Storage test doubles, applies the migration, and checks ownership, restricted writes, private media, approval gates, and cascading deletion. It does not connect to the hosted project or validate the real Auth/Storage services. `supabase/tests/local_bootstrap.sql` is **local-test-only**, never a hosted migration. The test cluster is stopped and removed afterward.

Before hosted application: authorize Supabase access, inspect this exact project's existing schema/bucket/policies, and apply the versioned migration transactionally. Do not overwrite existing tables or loosen other Storage policies. Backend secret keys/database passwords stay outside Git and the native app. Use Supabase's documented [backend connection settings](https://supabase.com/docs/guides/database/connecting-to-postgres); project dashboard URLs are not database credentials.

## Source-first visuals

The engine prefers the **main source's** visuals: an original paper/article explicitly linked by a secondary story, then the story's own relevant image, then generated artwork only if no usable source image is found. Original discovery is bounded to two explicit primary references, supports arXiv HTML with PDF fallback, and does not recursively crawl paper references. The same public-fetch/robots/size protections apply to original documents and images. It inspects article images and captions and excludes logos/tracking images. Relevance ranking uses caption/post overlap and gives methodology/architecture overviews preference. It is a caption-based heuristic, not a vision model's understanding of the image. A human must check the result.

Unknown licensing does **not** mean the image is absent and does not trigger generation. A relevant, decodable source image is retained in the private draft with source/image URLs, caption, author credit when available, and detected license information. Unknown or unrecognized rights are flagged with `requires_rights_review` / `rights_status = needs_review`; third-party credits do not inherit a blanket page license. Image decoding checks dimensions and format and strips metadata. A usable source visual skips Muse Image fallback entirely. Private review and explicit public-permission attestation remain separate; attribution is not an automatic permission grant. Explicit shared-post snapshots can include these images; attribution does not grant republication rights. Legacy previews remain text-only.

For PDFs, the prototype renders an entire source page containing a figure caption, independently of whether license metadata is available. This preserves vector diagrams and text labels and avoids guessing an unsafe crop. It is labeled as a source-page preview rather than pretending to be an isolated figure. The app supports tap-to-zoom. Precise figure cropping and vision-based selection remain future improvements. Rendering follows [PyMuPDF's page rendering API](https://pymupdf.readthedocs.io/en/latest/recipes-images.html).

If no usable source image exists, **Muse Image (`muse-image-1.0`)** receives the verified narrative and source-aware visual brief through the existing backend Meta key. It needs no separate text-model planning call. The brief adapts to recipes, venues and research; research flows use only source-supported methodology. Generated art is labeled and must not be treated as evidence from the source.

The backend requests one image, reserves $0.01 in the job/user/project spending ledger, and defaults to high refinement with web/image search enabled and shell access disabled. There are no automatic request retries. A definite rejection releases the reservation; uncertain timeout billing remains reserved. Original source photos always take precedence. Captionless article photos are eligible based on article placement, dimensions and aspect ratio, while ads, avatars, logos and separators are filtered.

To test just fallback generation on an existing machine-checked draft (this CLI deliberately skips source-image selection), load the Meta credentials into the backend environment, then run:

```sh
.venv/bin/python -m scripts.generate_visual data/local-model-test.json
```

The test writes `data/muse-image-test.png` and its prompt metadata locally; neither is committed. When Muse Image is unavailable, the post remains readable with `visual.status = needs_image_generator`; no generated image is falsely claimed. Failed image attempts have an explicit retry.

After setup, use **Retry source visual** in the post, or `POST /v1/posts/{id}/image/retry`. A ready source image is retained and never replaced with generated art by that action. Ordinary source and generated image bytes are stored per post, delivered through an authenticated endpoint, cached privately by the native client, and deleted with the account. Google Places photos remain live: their bytes and expiring photo names are excluded from the permanent phone/server cache. Concurrent requests for the same account/post/image share one active download; completed Google downloads are not retained by that request group. A compact Google Maps credit card below the gallery links to the contributor and original photo without placing contributor avatars over the image. A failed image can be retried in place. Explicit shared-post snapshots can include these images; attribution does not grant republication rights. Legacy previews remain text-only.
