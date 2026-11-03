# uptodate

**Your newsletters, without the inbox.**

UpToDate turns newsletters, blogs, articles, research papers, and custom AI digests into a personal reading feed. Each account gets its own newsletter email address, so subscriptions go to the app instead of cluttering your main inbox.

Read the key points, explore the original source, and act on useful information from one place.

[Website](https://up2-date.app) · [Privacy](https://up2-date.app/privacy) · [Terms](https://up2-date.app/terms)

## What you can do

### Build a clutter-free feed

- Get a private newsletter alias for your account. Incoming editions are processed for your feed rather than forwarded to your personal email.
- Add a newsletter by its signup link, finish any publisher verification, or import an existing `.eml` file.
- Discover starter newsletters through optional interests: AI & tech, research, cooking, coffee & dining, business, and travel.
- Read short, source-aware posts with the original article or paper linked. Substantial stories in a roundup can become separate posts; related paper, repository, and project links are grouped together.
- Browse today's posts and continue into older editions. Bookmark posts for later, or mark them read to move them out of the unread feed and into your read history.

### Go beyond a summary

| Content | Available features |
| --- | --- |
| Research papers | Original abstract when retrievable, source-based explanations, paper figures, and supporting paper/repository links. |
| Recipes | Dish descriptions, ingredient lists, source-provided steps, and an action to add ingredient names to Apple Reminders. |
| Places and recommendations | Venue descriptions and available ratings/hours, with actions for maps, websites, and phone calls. |
| Articles and blogs | Concise takeaways, original-source links, relevant images, and follow-up questions with Biggie. |

Posts can include swipeable image galleries and pinch-to-zoom images. Expanded posts provide the longer reading view and additional source actions.

### Ask Biggie

Biggie answers questions in the context of the post and its available source material. It can use bounded web search when more information is needed and displays source citations when returned.

For a larger task, use **Open in** to share the main source link through the iOS share sheet and continue in another app. Sharing a post itself creates a web preview of the post.

### Create your own newsletter with Muse

Describe what you want to follow, choose a schedule, and prepare a newsletter request for Muse. Templates cover:

- Recommendations, including useful venue details when relevant.
- Research papers, with optional repository and supporting links.
- Cooking, with ingredients and source-provided instructions.
- Blogs and articles.
- Other topics of your choice.

The email flow includes your UpToDate alias in the request. Muse confirms and runs the schedule; UpToDate receives the editions and processes them into posts.

A **private Muse connector beta** also supports direct delivery to approved test accounts without email. Its connection controls live in Profile. Public connector availability is still pending.

### Manage your account and subscriptions

- Email/password accounts, email verification, password recovery, and account deletion.
- A welcome guide, editable profile, profile pictures, and light/dark appearance controls.
- Separate sections for subscribed newsletters, custom newsletters, and unsubscribed sources.
- Feed visibility toggles that are separate from unsubscribing.
- Publisher unsubscribe actions, stop-processing controls for custom newsletters, and removal of unsubscribed entries.
- In-app access to available publisher confirmation links and verification codes.

## How posts and images are prepared

The backend receives an edition, identifies its account and publication, extracts the content, and groups related links into stories. It retrieves original sources where available and uses content hashes and generation caches to avoid unnecessary repeat work. Source-grounded drafting and verification prepare the reading view and contextual actions.

**Muse Wonder 1.0** is the default text model for generation, Biggie, and model-assisted retrieval. **Muse Image** provides fallback illustrations.

Photo retrieval prefers existing source material:

1. A usable image supplied by the newsletter or original source.
2. The official venue website.
3. Press/reference pages, Wikimedia Commons, and Openverse.
4. Additional public-page discovery and Google/Facebook fallbacks.
5. An explicitly labeled AI illustration when no usable real image is retrieved.

Venue matching checks the supplied name and address. Filters reject logos, maps, generic stock/decorative images, and promotional social-feed posts. The current `source_first` mode does not require an explicit website permission marker before trying an image; source URLs and available credits are retained.

Saved post images use private storage and the app's image cache. Google Places photos use live retrieval with attribution. `.eml` imports retain their extractive import flow.

## Run the iOS app

```sh
git clone https://github.com/Deeveshizm/uptodate-metahackathon.git
cd uptodate-metahackathon
open native/UpToDate.xcodeproj
```

In Xcode, select the **UpToDate** scheme, choose an iPhone simulator, and run. Physical-device builds require signing with an Apple Developer team. TestFlight access is by invitation during the beta.

**The iOS app connects to the hosted backend. Testers do not need an environment file or backend API keys.**

## Backend development

The deployed app uses:

| Component | Technology |
| --- | --- |
| iOS client | SwiftUI and native iOS integrations |
| API and processing engine | Python / FastAPI |
| Text and fallback image inference | Meta Model API: Muse Wonder and Muse Image |
| Accounts, database, and private assets | Supabase Auth, PostgreSQL, and Storage |
| Hosting and background jobs | Google Cloud Run, Cloud Tasks, and scheduled maintenance |
| Newsletter email reception | Cloudflare Email Routing and an Email Worker; legacy Resend receiving support |

For local backend development, create a Python environment and install the requirements:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The API binds to `127.0.0.1:8000` with `make api`. Configure backend settings in your environment before starting it. For explicit local text inference, run Ollama with an installed model and use `make api-local`; `OLLAMA_MODEL` selects the model. Local backend development is optional for running the hosted iOS client.

Server configuration includes `MODEL_API_KEY` for Meta inference, Supabase authentication/database/storage settings, and receiving-provider configuration. Google Places fallback is optional and uses a backend-only key with `GOOGLE_PLACES_PHOTOS_ENABLED=1`. The deployment script reads ignored `.env.local` settings and stores runtime secrets in Google Cloud Secret Manager.

**Keep environment files and service credentials out of Git and the iOS app.**

### Repository layout

- `native/` — SwiftUI app, assets, and Xcode project.
- `backend/` — API, email ingestion, source extraction, generation, chat, images, and sharing.
- `supabase/migrations/` — database schema and migrations.
- `deploy/gcp/` — backend container deployment and cloud configuration.
- `deploy/cloudflare/` — email receiver Worker.
- `scripts/` — development and maintenance utilities.

## Beta scope

Publisher signup and unsubscribe flows vary. Some require a browser step, CAPTCHA, email link, or verification code. UpToDate surfaces those actions rather than assuming a subscription succeeded. Direct Muse delivery remains restricted to the connector beta's approved test accounts.
