# Original-source posts and Manage UI integration

`~local generation only`

## Runtime status

The tested setup runs FastAPI, SQLite, Ollama (`llama3:8b`), and ComfyUI on the development Mac. It is not a hosted backend or production deployment. Retrieving public newsletters, articles, and papers still requires internet access. Models, databases, simulator sessions, and generated test artifacts are not committed.

An optional, explicitly configured Tailscale adapter can use the collaborator's Ollama Mac instead. It is not the default used for the latest simulator test. It permits inference/model listing only, with no model downloads, deletion, or remote residency changes. Supabase migrations are prepared and locally tested, but the application has not switched to hosted Supabase Auth, Postgres, or Storage. Live email receiving, aliases, and automated newsletter signup remain unfinished.

## Behavior changes

- Adding a newsletter automatically discovers public content and queues generation; users do not select a separate Generate post action.
- Content is generated from the linked original paper/article when identified. Evidence and the content URL point to that original, while the newsletter name (for example, AlphaSignal or Muse) remains the displayed attribution. Actual research authors are retained separately.
- Images are selected from the original source first, then the article/newsletter. ComfyUI is called only when no relevant, usable source image is available. Attribution, image/source URLs, and detected license information are retained. Unclear rights are flagged separately and do not themselves trigger generation or grant public permission.
- Posts appear automatically in the private Home feed. Pending image work and imported excerpts needing original-source analysis have visible tags. Failed text generation is reported in Manage rather than presented as verified content.
- The latest GitHub UI is integrated: Home, Saved, Manage, and Profile. Manage supports newsletter links, EML import, source visibility, processing status, and retries. The collaborator's external-AI feature removal and masked alias are preserved.
- EML import remains available and offline/extractive. It does not silently claim that newsletter excerpts were analyzed from the original source.
- Regeneration preserves a private post's ID and bookmarks, and replaces its content only after the new original-source result and image are ready. Failure retains the previous post. Public posts cannot be silently regenerated.

## Files changed

| Files | Purpose |
| --- | --- |
| `backend/app.py` | Account-scoped newsletter/generation/regeneration/image APIs, automatic private feed, pending tags, display attribution, and separate public-permission checks. |
| `backend/discovery.py` | Public RSS/Atom and latest-issue discovery; avoids drafting from signup pages. |
| `backend/retrieval.py` | Bounded, DNS-pinned public retrieval with redirect, address, robots, and size checks. |
| `backend/documents.py` | HTML/PDF extraction, stable evidence spans, original-source links, publication names, author and license metadata. |
| `backend/generation.py` | Local Ollama transport, constrained evidence selection/drafting, citation and numerical checks, separate verification, and one repair attempt. |
| `backend/source_engine.py` | Durable source jobs, original-content generation, deduplication, source-first visuals, atomic regeneration, and private feed placement. |
| `backend/visuals.py` | Original-source image selection, image validation/attribution, independent rights flags, and local ComfyUI fallback planning. Selection is caption-based and is not a guarantee of visual relevance. |
| `backend/workflows/sdxl-lightning-api.json` | Four-step SDXL-Lightning ComfyUI workflow with tiled decoding. Model weights are installed separately and are not committed. |
| `backend/store.py` | Additive SQLite fields and storage for jobs, source evidence, analysis, and private image assets. |
| `backend/demo.py` | Uses Muse as the sample newsletter's display name. |
| `native/Sources/AppModel.swift` | Backend bindings, job polling, private image loading/cache, and Manage-oriented status messages. |
| `native/Sources/Models.swift` | Additive decoding for source status, jobs, analysis, visuals, and pending tags. |
| `native/Sources/FeedComponents.swift` | Real source images, honest machine-checked/import labels, and pending-work tags within the collaborator's feed layout. |
| `native/Sources/PostVisualView.swift` | Authenticated image display, source credit, and tap-to-zoom. |
| `native/Sources/UpToDateApp.swift` | Integrates the latest Manage-tab UI with automatic posts, newsletter links, EML import, processing/retries, and original-source post details. |
| `native/Info.plist`, `native/UpToDate.xcodeproj/project.pbxproj` | Local backend networking and native build/source integration. |
| `Makefile`, `pyproject.toml`, `requirements.txt` | Local service/build/test commands and extraction/image dependencies. |
| `scripts/generate_source.py`, `scripts/check_verifier.py` | Real-model diagnostics and verifier calibration, with optional private local traces. |
| `scripts/generate_visual.py` | Explicit fallback-image testing on a previously machine-checked draft. |
| `scripts/smoke_api.py` | Temporary-account live API tests, including automatic feed placement and optional source/image checks. Deletes only its own test account. |
| `scripts/test_simulator_newsletter.py` | Existing simulator-account verification, image retry, and original-source regeneration without account deletion. |
| `scripts/check_supabase_schema.py` | Disposable local Postgres schema/RLS validation; never applies the hosted project. |
| `supabase/migrations/20261018000100_initial_backend.sql` | Prepared account-scoped schema, RLS, private media metadata, cleanup queue, and private/public publication separation. Not the active runtime. |
| `supabase/tests/local_bootstrap.sql`, `supabase/tests/schema_contract.sql` | Local Auth/Storage test doubles and ownership, permission, publication, media, and deletion contracts. |
| `tests/test_source_engine.py`, `tests/test_newsletter_discovery.py`, `tests/test_ollama_transport.py`, `tests/test_visuals.py`, `tests/test_workflow.py` | Regression coverage for original-source evidence, automatic tagged feeds, safe regeneration, privacy, source-first images, transport, and preserved import behavior. |
| `samples/source.html` | Clearly identified original fixture for deterministic generation testing. |
| `.gitignore`, `README.md`, `docs/local-generation-changes.md` | Local-artifact exclusions, setup/runtime boundaries, and change documentation. |

## Validation

- Backend regression suite: 114 tests passed.
- iOS simulator build succeeded; the updated iPhone 17 app shows Manage and the AlphaSignal-derived post in Home.
- Real AlphaSignal test retrieved the original ScoringBench paper, generated and checked text against its evidence, reused its source image, and retained the result in the existing simulator account's private feed.
- Local PostgreSQL schema/RLS contracts passed. No hosted Supabase database was accessed.

Machine checks and source-image heuristics are prototype safeguards, not guarantees of accuracy or reuse permission. Generated fallback images can still be unsuitable; a failed image planner leaves a visible pending tag rather than blocking private reading or pretending an image exists.
