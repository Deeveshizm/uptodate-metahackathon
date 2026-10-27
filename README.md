# UpToDate prototype

Status: `~local generation only`. The tested backend, SQLite database, Ollama text inference, and ComfyUI fallback-image generation run locally. Public-source retrieval still uses the internet. Supabase is schema preparation only, not the active runtime. See [file-by-file change notes](docs/local-generation-changes.md).

Local working prototype with a native SwiftUI client and Python backend. This initial snapshot contains work completed before the hackathon, developed by Deeveshizm and Ujwal Jain with coding-agent assistance. Instagram supplies familiar feed conventions; the visual identity is provisional.

## Run

From the repository root:

```sh
make api
```

The API runs at <http://127.0.0.1:8000>, with interactive documentation at <http://127.0.0.1:8000/docs>. On a fresh checkout, install Python 3.11 or newer and create the environment before running `make api`:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Open `native/UpToDate.xcodeproj` in Xcode, select the **UpToDate** scheme, choose an iPhone simulator, and run. Start a local account, then choose **Load sample edition**. Alternatively, run `make app` in another terminal for a native Mac app. The Mac app uses the same SwiftUI source and API.

For a populated simulator preview, add `--demo` to the Xcode scheme's launch arguments. This creates a local account if necessary and loads the seeded sample edition.

The sample edition in `backend/demo.py` and `samples/newsletter.eml` both condense one real Muse-generated roundup ("Your Weekly Roundup — October 18": four East Bay/SF coffee openings and the early-season Tahoe snow picture). Source links are the ones that edition cited; the sender and signup URL are placeholders on `muse.example`. Posts are labeled as a seeded sample and are not independently verified.

## Try the complete flow

1. Explore the sample edition. Open a card, bookmark it, mark it read, and inspect Saved.
2. Open **Manage** to add a newsletter URL or **Import newsletter .eml**, choosing `samples/newsletter.eml` or an email exported from your mail client. EML importing remains offline and does not follow links.
3. Posts appear automatically in the private **Home** feed. Imported excerpts carry an **Original analysis pending** tag; generated posts with unfinished visuals carry **Image pending**. No manual approval is required for private reading.
4. The finite edition date uses your account's timezone. Source filters are under **Today's edition**. Muting in Manage hides a source without unsubscribing at the publisher.
5. Private posts share only an original-source URL. The backend retains a separate explicit human-review/public-permission endpoint for a local text-only public preview; automatic feed placement never grants public permission.
6. Sharing lives in the post view, alongside bookmarking and read state. The feed card itself carries only bookmarking.

The bottom tabs are Home, Saved, Manage, and Profile, following the latest collaborator UI. Manage contains newsletter links, EML import, source visibility and job progress/retries. Profile retains the masked placeholder alias and account deletion. Deletion removes that account's database records and revokes its share links; the client also clears its cached data and session.

## Implemented

- Native iPhone/Mac client with source filtering, expandable captions, bookmarks, read state, external source links, and sharing.
- Local account sessions with high-entropy tokens stored in Keychain on devices/Mac; unsigned simulator builds use a protected file inside the simulator app sandbox. The server stores token hashes.
- Exactly one placeholder alias per account. `.invalid` makes its non-deliverable status explicit.
- SQLite persistence, MIME/HTML extraction, script removal, per-account duplicate detection, and a durable queue with restart recovery.
- Original-source evidence, automatic private-feed placement, pending-work tags, separate public permission gates, escaped share pages, and signup CTAs.
- Cached feed/library for offline reading. Writes require the backend. Retry refreshes the cache.
- Job status and extraction failures visible in Manage. Jobs discard the temporary extraction payload after processing; retained post excerpts remain until account deletion.

## Prototype boundaries

This is a local prototype, not a deployable beta. Account creation is a development endpoint, not Sign in with Apple. Do not expose the API to the internet. It binds to loopback and accepts only local hostnames. Keep one backend process; startup recovery assumes a single process.

The alias cannot receive email. A mail domain/provider, signature-validated webhook, and opt-in confirmation flow still need to be connected. Importing an `.eml` is an explicit local action, not authenticated publisher delivery.

EML imports remain **offline and extractive**, available in Manage, and are clearly tagged as not yet analyzed from the original. The source-link engine retrieves public HTML/text/PDF originals and uses **local Ollama** for drafting plus a separate verification call. Successful text goes directly to the private feed, even if its image needs work; the post carries a pending tag. Invalid model output is not published. Machine checks do not guarantee accuracy. Public permission remains a separate human assertion. Seeded sample posts retain code-drawn covers and unverified-sample labels.

Share URLs work only on this Mac and its simulator. Public hosting, Universal Links, App Store identity, actual newsletter signup destinations, production queues/Postgres, quotas, and production privacy/security operations remain future integration work. There are no likes, friends, creator posting, or built-in chatbot.

## Verify

```sh
make test
make build-ios
make smoke
```

`make smoke` requires the API to be running. It creates an isolated temporary test account, exercises the live import/review/feed/share flow, and deletes that test account afterward.

## Files

- `backend/app.py`: account-scoped API, queue worker, review/publication, sharing.
- `backend/pipeline.py`: offline MIME/HTML extraction and evidence drafting.
- `backend/store.py`: SQLite schema and transactional connections.
- `native/Sources/FeedComponents.swift`: feed card layout, cover illustrations, the profile glyph, and central styling constants.
- `native/Sources/UpToDateApp.swift`: app navigation, reading, review, and profile screens.
- `backend/demo.py`: the seeded sample edition summarized from the Muse roundup.
- `native/Sources/AppModel.swift`: API integration and local cache.
- `tests/test_workflow.py`: workflow, isolation, deletion, URL/markup handling, queue recovery.

Data lives in ignored `data/prototype.sqlite3`. Client snapshots live in the app's Application Support directory. No API keys or paid services are required.

## Local source-to-post engine

The default text model is `llama3:8b`. Install [Ollama](https://ollama.com/download) separately, then start its service in another terminal if it is not already running:

```sh
make model
```

On your own machine, install the model with `ollama pull llama3:8b` after starting Ollama. Do not run model-management commands against another person's server. Ollama and ComfyUI packages and model weights are not Python API dependencies and are not included in `requirements.txt`.

Start/restart the API with `make api` (or `make api-comfy` for local fallback images). Adding a newsletter now automatically discovers and processes public content in the backend:

- `POST /v1/newsletters` saves a publication URL and requests signup using the account's existing single alias. The backend also recognizes a submitted public article/issue, an advertised RSS 2.0/Atom feed, or an explicit latest-issue link. It selects at most one public issue per add operation (newest dated item among the first 20 feed entries, otherwise feed order), then creates a durable generation job automatically. Recognized roundups follow up to three original editorial links; ordinary articles produce one draft. No user-facing generation choice is needed.
- Signup and content processing are independent. The placeholder `.invalid` alias reports `needs_inbox`, but accessible public content can still generate drafts. A deliverable alias requires a configured signup-agent adapter (`needs_agent` otherwise). No signup is attempted against a placeholder address, and submission never implies email confirmation succeeded.
- The newsletter job result includes `content_discovery` and `generation_job_id`. No accessible issue gives `waiting_for_content`; a broken/blocked feed gives `discovery_failed`. A discovered issue gets its own retryable generation job. Completed text appears automatically in the private feed; pending images are tagged and processing failures appear in Manage. A signup landing page is not treated as article content.
- `POST /v1/generation` remains a backend/developer diagnostic endpoint for public article, paper HTML/PDF, or newsletter edition URLs; it does not need to be exposed in Add newsletter. Explicit Article/Digest modes remain available there.

The latest GitHub Manage-tab layout is integrated with the backend: Add newsletter calls `POST /v1/newsletters`, has no Generate post/source-type choices, polls processing jobs, and displays authenticated source images and pending tags in Home. The newsletter name is the display author; `original_url` and evidence refer to the original paper/article. The true source author is retained separately in `analysis.provenance.content_author`.

Discovery runs once on add; there is no recurring feed poller or live inbound-mail generation worker yet. An existing duplicate add does not crawl again. The engine never silently subscribes through a different service. The Muse adapter awaits identification of the intended Muse service/runtime. No live receiving domain, inbound confirmation handler, or concrete Muse signup integration is configured yet. EML import remains available and extractive.

The coffee sample is `samples/newsletter.eml`; its Muse signup URL is a placeholder, not a public newsletter archive. A real coffee-related source already cited there is [The Infatuation's SF openings guide](https://www.theinfatuation.com/san-francisco/guides/new-san-francisco-restaurant-openings). To exercise the automatic Add newsletter path on that source, without a signup submission:

```sh
.venv/bin/python scripts/smoke_api.py --newsletter https://www.theinfatuation.com/san-francisco/guides/new-san-francisco-restaurant-openings --output data/coffee-generation-test.json
```

To test AlphaSignal's advertised public feed through the same automatic path, including a ready, retrievable image (run `make comfy` and `make api-comfy` first):

```sh
.venv/bin/python scripts/smoke_api.py --newsletter https://newsletter.alphasignal.ai/ --output data/alphasignal-final-test.json --require-image
```

This uses a temporary account, saves text/image test artifacts locally, and deletes that account's server records afterward. It does not insert posts into your simulator account.

The pipeline is: bounded retrieval → linked original paper/article → extraction → selection of 1–3 substantive passages → typed draft → evidence/number checks → separate model verification → source-first image selection → automatic private feed, with pending tags as needed. If an explicitly linked original cannot be read, the job reports that failure rather than silently substituting newsletter text. The writer receives only the selected original passages and can cite only their IDs. The server attaches exact source text. Repairs receive the rejected draft and field verdicts. Missing problem/method/result/impact fields remain null. One text repair attempt is allowed. Model verification is not a guarantee of factual correctness.

`POST /v1/posts/{id}/regenerate` regenerates an existing private source-engine post from its original source, retaining the post ID and bookmarks. Replacement is transactional and requires valid text and a ready image; on failure the previous post is preserved. Existing public posts cannot be silently regenerated. This is useful for updating earlier newsletter-derived test posts without mislabeling their old evidence as original research.

Retrieval validates resolved addresses and pins connections to a validated public IP while retaining HTTPS hostname verification. Redirects are checked again, HTTP downgrade is blocked, and robots.txt restrictions are honored. Downloads are limited to 6 MB; extracted evidence is limited to 18,000 characters / 50 spans; PDFs examine at most 25 pages. JavaScript-only pages, paywalls, encrypted/scanned PDFs, and sources that block automated retrieval need a different public original or manual import. Nothing bypasses these restrictions. Source HTML and PDFs are untrusted data and are never given tool privileges.

Source evidence and job state are stored per account. Existing databases receive additive migrations. Deleting an account deletes its source evidence too. Failed source jobs retain their request for explicit retry; successful jobs discard their temporary request payload. Source revisions already represented by a nonrejected post are deduplicated per account. The API accepts `refresh: true` to re-fetch a changed page.

To choose another installed model, set `OLLAMA_MODEL` before starting the backend, for example `OLLAMA_MODEL=your-installed-model make api`. `OLLAMA_URL` defaults to `http://127.0.0.1:11434`. The adapter uses Ollama's documented [JSON schema output format](https://docs.ollama.com/capabilities/structured-outputs), includes the output schema in the prompt, and validates the result server-side. It does not use a paid cloud inference API.

### Collaborator's Ollama over Tailscale

Optionally use an explicitly authorized Ollama server on your Tailscale network. Its availability depends on the peer being awake and connected. Configure the peer address yourself and select an already-installed model; the application does not download or remove models.

```sh
make comfy        # local image backend, separate terminal
TAILNET_OLLAMA_HOST=your-authorized-peer-ip make api-tailnet
```

The target uses your `TAILNET_OLLAMA_HOST` on port 11434 and defaults to `OLLAMA_MODEL=llama3.1:8b`. Set `TAILNET_OLLAMA_MODEL` for another already-installed model. The adapter requires the exact configured literal Tailscale IPv4 address; there is no blanket private-network allowlist. The public article fetcher still rejects Tailscale/private addresses.

This adapter uses the native `/api/tags` and `/api/chat` endpoints at the server root, **not** the OpenAI-compatible `/v1` base. It needs no dummy API key. It sends no `/pull`, `/delete`, or model-management calls, and does not override remote model residency with `keep_alive`. Selected source text and prompts are sent to the collaborator's machine over Tailscale; ComfyUI and app data stay local. Keep Tailscale access restricted; the Ollama service has no separate authentication.

There is no silent fallback to a different server/model if the peer sleeps. Jobs fail with a Tailscale/awake diagnostic and retain their retry request. Ask the collaborator to wake/connect the MacBook, then retry the failed generation job.

For manual debugging, `scripts.generate_source --trace data/your-trace.json` saves intermediate evidence selection, drafts, and verification locally. Traces include source text; keep them private and outside Git. `python -m scripts.check_verifier` tests supported paraphrases and deliberately false location/health/number/negation claims against the configured model. These live checks are separate from the mocked unit tests.

Test the real installed model without the UI:

```sh
make engine-test
.venv/bin/python -m scripts.generate_source https://ollama.com/blog/structured-outputs --output data/public-source-test.json
```

The first command uses an original local fixture, explicitly labeled in the output. The second fetches the specified public source. Generated JSON artifacts in `data/` are local and ignored by Git. Unit/integration tests use fake retrieval and model adapters for deterministic failures; these CLI checks run actual local inference.

New modules: `backend/retrieval.py` (public transport), `backend/documents.py` (HTML/PDF evidence), `backend/generation.py` (Ollama adapter and checks), `backend/source_engine.py` (durable orchestration). API additions: `GET /v1/engine`, `POST /v1/newsletters`, `POST /v1/generation`, and `POST /v1/jobs/{id}/retry`.

## Supabase schema preparation (not the active runtime yet)

The preparatory migration is `supabase/migrations/20261018000100_initial_backend.sql`, aligned with the existing Manage-tab UI. Configure your own Supabase project if you later implement the hosted adapters.

This is a schema foundation, **not a completed database migration**: the running FastAPI app still uses SQLite and local prototype tokens. No SQLite data or sessions have been uploaded. Hosted deployment, Supabase Auth validation, a Postgres store/worker adapter, Storage upload/deletion, and existing-account migration still need implementation and hosted integration testing. Creating tables alone does not switch the app to Supabase or fix local-model grounding failures.

The new UI continues to consume these contracts:

| UI / engine need | Supabase representation | API compatibility requirement |
| --- | --- | --- |
| Profile and masked receiving alias | Auth identity + private `profiles`, nullable unique alias, timezone | Return alias as a string (empty until configured), never a fabricated deliverable address; masking belongs in UI |
| Newsletter visibility and signup | Owner-scoped `sources`, `muted`, signup/feed URLs and truthful subscription state | Serialize boolean `muted` as `0`/`1` for the existing Swift model |
| Manage / review / processing | Owner-scoped `jobs` and `posts`, status/stage/errors/results | Keep existing `id`, `subject`, `status`, `error`; new generation fields are additive |
| Finite daily edition | Approved `posts.published_date`, profile timezone, source visibility | API keeps `date`, `posts`, `total`, `unread`, `finite`; not an infinite public feed |
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

Unknown licensing does **not** mean the image is absent and does not trigger generation. A relevant, decodable source image is retained in the private draft with source/image URLs, caption, author credit when available, and detected license information. Unknown or unrecognized rights are flagged with `requires_rights_review` / `rights_status = needs_review`; third-party credits do not inherit a blanket page license. Image decoding checks dimensions and format and strips metadata. A usable source visual stops both the fallback prompt planner and ComfyUI from being called. Private review and explicit public-permission attestation remain separate; attribution is not an automatic permission grant. Public share previews remain text-only.

For PDFs, the prototype renders an entire source page containing a figure caption, independently of whether license metadata is available. This preserves vector diagrams and text labels and avoids guessing an unsafe crop. It is labeled as a source-page preview rather than pretending to be an isolated figure. The app supports tap-to-zoom. Precise figure cropping and vision-based selection remain future improvements. Rendering follows [PyMuPDF's page rendering API](https://pymupdf.readthedocs.io/en/latest/recipes-images.html).

If no usable source image exists, Ollama reads the **verified post content** (hook, summary, problem, approach, finding, impact, limitations and supporting evidence), not raw newsletter instructions. It chooses a concrete subject, a wordless process illustration when a reported approach exists, or a conceptual metaphor otherwise. It describes a short scene of recognizable objects; missing research steps must not be invented. The scene, rather than a technical headline and article prose, becomes the diffusion prompt. Scene validation rejects unsupported process mode, digits, chart/diagram/label terminology, and descriptions over 40 words. An invalid scene gets one repair attempt with its rejected description and feedback; continued failure leaves the image pending. Generated art is explicitly labeled; a metaphor is not a depiction of the actual research or its results. Accurate labeled scientific flowcharts are not produced by this fallback; use the original licensed figure for those. Visual relevance still requires human review; the installed text-only Llama model cannot validate image content.

The fallback adapter supports a **local ComfyUI** workflow. The development setup uses the official ComfyUI backend in the sibling `../ComfyUI` directory, with its own `.venv`, and ByteDance's `sdxl_lightning_4step.safetensors` checkpoint in `ComfyUI/models/checkpoints`. These large local dependencies are not committed or automatically downloaded by the app. Installation follows the [official manual setup](https://docs.comfy.org/installation/manual_install). The checkpoint is approximately 6.94 GB, under its [OpenRAIL++ license](https://huggingface.co/ByteDance/SDXL-Lightning/blob/main/LICENSE.md); review its terms before distribution or deployment.

From the project directory, run these in separate terminals (Ollama must also be running):

```sh
make comfy
make api-comfy
```

`make comfy` binds to localhost port 8188, disables custom nodes and paid API nodes, and uses offline mode. It does not open a browser or touch the native app. Override `COMFYUI_DIR` if installed elsewhere. `make api-comfy` enables the bundled API workflow and memory-saving settings. It unloads Ollama after each text call, serializes app text/image inference, and asks ComfyUI to release its models after successful image generation. This trades reload time for lower memory pressure on a 16 GB Mac. Run one backend process; this lock does not coordinate other apps or manual ComfyUI jobs.

The bundled `backend/workflows/sdxl-lightning-api.json` follows the model author's [four-step Euler / sgm_uniform configuration](https://huggingface.co/ByteDance/SDXL-Lightning), with one 1024×1024 image and tiled VAE decoding. It is a testing baseline, not a guarantee of scientific accuracy or visual quality. The model can ignore exclusions and invent labels or diagrams; generated illustrations are never evidence. Inspect results before relying on or distributing them. The fixed seed makes comparisons easier; the verified content and style brief supply the positive prompt.

To test just fallback generation on an existing machine-checked draft (this CLI deliberately skips source-image selection):

```sh
LOCAL_MODEL_MEMORY_SAVING=1 COMFYUI_TIMEOUT=600 COMFYUI_WORKFLOW=backend/workflows/sdxl-lightning-api.json .venv/bin/python -m scripts.generate_visual data/local-model-test.json
```

The test writes `data/comfyui-test.png` and its prompt metadata locally; neither is committed.

Until ComfyUI and its checkpoint are available, the post remains readable with `visual.status = needs_image_generator`; no generated image is falsely claimed. Alternatively, reuse an API-format workflow exported from your own ComfyUI, and place `{{POST_PROMPT}}` in the positive prompt text. Restart the backend with:

```sh
COMFYUI_WORKFLOW=/absolute/path/to/your-api-workflow.json make api
```

`COMFYUI_URL` defaults to `http://127.0.0.1:8188` and must be loopback. `COMFYUI_TIMEOUT` defaults to 180 seconds (600 with `make api-comfy`, maximum 1800). The adapter submits the workflow, polls history, and downloads the final image using ComfyUI's [official API example](https://github.com/Comfy-Org/ComfyUI/blob/master/script_examples/websockets_api_example.py). It does not download checkpoints or install custom nodes. The configured workflow controls any downstream behavior. If a job times out, check ComfyUI's queue before retrying: a timeout does not cancel inference already running there.

After setup, use **Retry source visual** in the post, or `POST /v1/posts/{id}/image/retry`. A ready source image is retained and never replaced with generated art by that action. Image bytes are stored per post, delivered through an authenticated endpoint, cached privately by the native client, and deleted with the account. Public share previews remain text-only.
