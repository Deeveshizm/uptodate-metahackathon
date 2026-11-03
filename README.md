# uptodate

**Stay up to date with what you love, without the email clutter.**

Newsletters are a great idea. Choose something you care about, and useful pockets of information arrive at regular intervals. They can help you keep up with a hobby, follow a research field, discover places, or find something new to cook.

But the experience often ends in an overflowing inbox. The content we chose to enjoy sits beside work messages, receipts, and notifications. Reading something interesting starts to feel like another email to deal with.

I built **UpToDate** to give that content a home of its own: an app that turns newsletters into digestible posts, while keeping the subscriptions out of your main inbox.

[Website](https://up2-date.app) · [Privacy](https://up2-date.app/privacy) · [Terms](https://up2-date.app/terms)

## 1. Create your account and get a newsletter address

When you create an account, UpToDate assigns you a private email alias for newsletter subscriptions. Your main email remains your account address; the alias receives the content you want to read in the app.

Newsletter emails go to UpToDate's receiving pipeline rather than being forwarded into your personal inbox. A welcome guide introduces the app, and you can choose interests to get starter newsletter suggestions: AI & tech, research, cooking, coffee & dining, business, and travel.

Accounts support email verification, password recovery, profile editing, a profile picture, and account deletion.

## 2. Subscribe from inside the app

Paste a newsletter link into Home or Manage. UpToDate uses your account's alias for supported signup flows.

Publishers sometimes need a human step: a CAPTCHA, a JavaScript form, an email confirmation, or a verification code. You can finish those steps through the publisher's page from the app, using your copied alias. Available confirmation links and codes are surfaced in UpToDate so you can complete the subscription.

You can also import an existing `.eml` file from Manage.

## 3. Read the content as posts

Incoming editions are processed by a backend powered by Muse and supporting extraction, retrieval, and verification functions. The result is a personal feed of short posts that helps you keep up with your interests without working through an inbox.

The posts aim to stay faithful to the source. Substantial stories in a roundup can become separate posts, while related material—such as a paper, its repository, and its project page—stays together. Links take you back to the original article or paper, not just the newsletter homepage.

Read today's posts, scroll into older editions, or open a post for more detail. Bookmark something for later, mark it read to move it into your read history, or share the post as a web preview.

### Real source images come first

Posts can include swipeable image galleries with pinch-to-zoom. UpToDate first looks for usable newsletter or original-source images, then tries the official venue website, press/reference pages, Commons/Openverse, and additional public-page or Google/Facebook fallbacks. A labeled Muse Image illustration is the final fallback.

Venue matching uses the name and address. Image filters reject branding, maps, generic stock/decorative images, and promotional social-feed posts. Saved post images use private storage and the app's cache; Google Places photos retain live retrieval and attribution.

## 4. Use actions that fit the content

A recipe, a research paper, and a coffee recommendation should not all behave like the same kind of post. UpToDate adapts the reading view and available actions to the information in the source.

| What you're reading | What you can do |
| --- | --- |
| A research paper | Read its original abstract when retrievable, view source figures, and open the paper, PDF, repository, or supporting project links. |
| A recipe | Read the dish description and source-provided cooking steps, then add ingredient names straight to your grocery list in Apple Reminders. |
| A restaurant, café, or other place recommendation | See available ratings and hours, open the location in a maps app, visit the website, or call the supplied number. |
| News, a blog, or an article | Read the main points, follow the original source, and explore the subject further. |

Buttons appear when the corresponding information is available. Missing cooking instructions, ratings, or other details should not be filled with invented facts.

## 5. Ask Biggie—or take the source to your preferred AI

If you want a better understanding, **Biggie** is right there with the post. Ask a question in the chat, and it uses the available post and source context to explain it. It can make bounded web searches when more information is needed and display returned source citations.

If you want to go much deeper, **Open in** shares the main source link through the iOS share sheet so you can continue with your preferred AI app. Sharing the post itself creates a post preview; Open in shares the original source.

## 6. Create a newsletter around your own interests

My favorite feature is that you do not have to find an existing newsletter that happens to match what you want. You can describe your own and ask Muse to create it.

Choose a newsletter type, give it a name, describe the content and your preferences, and choose how often you want it. UpToDate prepares a request suited to that content:

- **Recommendations:** one recommendation per scheduled edition, with useful venue details and real images when available.
- **Research papers:** the paper, a useful explanation, and selected PDF, code, project, or data links.
- **Cooking:** recipes that fit your preferences, with ingredients and source-provided instructions.
- **Blogs & articles:** relevant reads, useful summaries, original links, and source images.
- **Other:** topics and kinds of posts you choose.

There are two delivery options:

**Email delivery:** share or copy the prepared request into Muse. It includes your UpToDate alias, so Muse can send scheduled editions to the app without filling your main inbox. When a new custom newsletter arrives, UpToDate asks whether you want to add it and what you want to call it.

**Direct delivery:** connect Muse from Profile, then use **Create Directly with Muse**. The request uses your existing private connector to deliver editions straight to your account and feed, without email. You confirm the newsletter task and schedule in Muse.

A public connector is pending review and approval. Direct delivery is currently limited to approved private-connector beta accounts.

## 7. Keep your feed yours

Manage separates subscribed newsletters, custom newsletters, and unsubscribed sources. A visibility toggle controls what appears in the feed; it is separate from unsubscribing.

Swipe actions let you unsubscribe from a publisher, stop processing a custom newsletter, or remove an unsubscribed entry. Publisher flows that require a website step remain available through the app. Profile also gives you access to bookmarks, read history, account settings, and light/dark appearance controls.

## Efficient generation

The backend is designed to avoid paying for the same work repeatedly:

- Message and story fingerprints prevent repeat processing of the same delivery.
- Exact content hashes and database-backed generation caches identify reusable results. Compatible public-source artifacts can be reused across feeds; private content remains account-scoped.
- Atomic generation claims coordinate jobs so the same artifact is not generated concurrently.
- Grouping related links avoids treating a paper and its repository as two independent stories.
- Complete source content can be preserved, and real images are preferred over generating replacements.
- Bounded model requests and spending limits control generation and retrieval work.

**Bloom-filter pre-checks are a planned optimization, not part of the current implementation.** The current token savings come from exact hash/database checks, reusable generation artifacts, and avoiding unnecessary model calls.

Text generation, Biggie, and model-assisted retrieval use **Muse Wonder 1.0** by default. Fallback illustrations use **Muse Image**. `.eml` imports retain their extractive import flow.

## Run the app

```sh
git clone https://github.com/Deeveshizm/uptodate-metahackathon.git
cd uptodate-metahackathon
open native/UpToDate.xcodeproj
```

Select the **UpToDate** scheme in Xcode, choose an iPhone simulator, and run. Physical-device builds require signing with an Apple Developer team. TestFlight access is by invitation during the beta.

**UpToDate already supports multiple users and is deployed on Google Cloud Platform (GCP).** Each user has their own account, newsletter alias, subscriptions, and personal feed. The iOS app connects to the hosted backend, so testers do not need environment files or backend API keys.

## Shared demo account

If you just want to try the beta app without creating an account, sign in with the shared demo account below. You can also create your own account to test the full signup experience and keep your subscriptions and feed separate.

- **Email:** `deevesh.jain99@gmail.com`
- **Password:** `G00dP@ssW0rd`

This account is shared publicly. Use it only for testing; its feed and account settings can be changed by other testers. Create your own account for personal subscriptions and content.

## Built with

SwiftUI · Python/FastAPI · Supabase Auth, PostgreSQL, and Storage · Google Cloud Run and Cloud Tasks · Cloudflare Email Routing and an Email Worker · Meta Model API

The receiving layer also retains legacy Resend support. Backend development can use Ollama for explicit local text-inference testing.

| Folder | Purpose |
| --- | --- |
| `native/` | iOS app, assets, and Xcode project. |
| `backend/` | API, ingestion, extraction, generation, chat, images, and sharing. |
| `supabase/migrations/` | Database schema and migrations. |
| `deploy/gcp/` | Hosted backend deployment. |
| `deploy/cloudflare/` | Email receiver Worker. |
| `scripts/` | Development and maintenance utilities. |

Backend deployments read configuration from ignored `.env.local` settings and keep runtime secrets in Google Cloud Secret Manager. Keep environment files and service credentials out of Git and the iOS app.
