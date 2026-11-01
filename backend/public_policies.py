"""Reviewed public policy pages; publication requires explicit operator settings."""
import html
import os
import re

from fastapi import HTTPException
from fastapi.responses import HTMLResponse

UPDATED='November 1, 2026'

PRIVACY='''<p>UpToDate is a beta newsletter-reading app operated by __OPERATOR__, an independent developer. This notice explains how the current app and its connector handle information.</p>
<h2>Information the service processes</h2>
<p>Account information includes your name, email address, date of birth, account identifiers and time zone. Optional information includes your interests and profile picture. Supabase handles sign-in and password protection; the UpToDate backend does not receive your plaintext password.</p>
<p>The service processes newsletter addresses and subscriptions, emails received at your app alias, imported email files, source links, and content delivered through a connected AI assistant. It stores extracted source passages, generated posts, images, bookmarks, read status, and subscription preferences. Operational records include processing status, errors, credential hashes, and estimated inference usage.</p>
<h2>Why this information is used</h2>
<p>We use this information to provide and protect your account, assign a newsletter alias, perform subscriptions you request, receive content, create and display your private feed, retrieve relevant source information and images, and support the service. Your selected interests help match a curated newsletter catalogue. Public source material may be cached and reused to avoid repeated processing.</p>
<p>Newsletter delivery is designed to use the app's receiving alias rather than forward newsletter messages to your personal inbox. Your sign-in email is used for account verification, recovery and necessary account communications. A direct AI connector delivers editions to your account without email.</p>
<h2>Cloud and AI processing</h2>
<p>Processing happens on a server. Content relevant to a post or an Ask Biggie request may be sent to Meta's Model API for text processing or image generation. The backend and configured providers can process that content to provide the requested feature. Muse's consumer app and other AI apps you choose have their own account settings and privacy terms.</p>
<p>Our service providers include Supabase for authentication, database and private image storage; Google Cloud for hosting and queued work; Cloudflare for receiving email; and Meta for configured AI inference. Resend is used where legacy receiving remains configured. Optional Google Places lookups retrieve venue information and photos. Publisher and source websites receive requests needed to subscribe or retrieve content. Providers process information under their applicable terms and settings.</p>
<p>We do not sell personal information or use it for targeted advertising. We do not train an UpToDate model on your private newsletter content. This does not make promises about processing or retention outside the service providers' applicable terms.</p>
<h2>Device permissions and sharing</h2>
<p>Adding selected ingredients to Reminders requires your permission. Reminders lists and selected items are handled on your device and are not uploaded to the UpToDate backend. A profile photo you choose is uploaded for your account. The current app does not request precise device GPS access; places you include in content may be processed for venue features.</p>
<p>When you choose Share, Open in, maps, a phone call or an external website, the relevant link or selected information is passed to the app or service you choose. Requests and newsletter addresses copied by a button use the system clipboard.</p>
<h2>Retention, disconnecting and deletion</h2>
<p>Account information, saved posts, source references and subscription settings are retained while needed to provide your account and the features you use. Processing payloads are cleared as work finishes; extracted passages and post records can remain for the feed and source checks. Limited operational and security records may be retained while necessary for troubleshooting, abuse prevention or legal obligations.</p>
<p>Disconnecting a connector blocks further access and queued processing authorized through that connection. In the current beta, previously imported posts remain in your private account unless removed or the account is deleted. Stopping a custom newsletter blocks future processing; its recurring task must be cancelled in the AI app that owns the schedule.</p>
<p>You can delete your account in Profile → Manage account. The deletion flow removes account-scoped active records and schedules unreferenced image objects for cleanup. Data may remain temporarily in caches, provider backups, or limited records required for legitimate security or legal purposes. Retention depends on those purposes and provider settings, rather than a promise of immediate erasure of every backup.</p>
<h2>Your choices and contact</h2>
<p>You can edit profile information and interests, control newsletter visibility, unsubscribe or stop processing, revoke device permissions, disconnect an AI connection, and request account deletion. Applicable privacy law may give you additional access, correction, deletion or complaint rights. Contact __CONTACT__ for privacy or support requests; do not post private newsletter content or credentials in public issue reports.</p>
<p>Cloud providers may process information in countries other than yours. We will update this notice when our practices change and identify the updated date on this page.</p>'''

TERMS='''<p>These terms cover the UpToDate beta app and its developer-operated backend and connectors. The service is operated by __OPERATOR__, an independent developer. By using the service, you agree to these terms and acknowledge the <a href="/privacy">Privacy Policy</a>.</p>
<h2>Beta access and accounts</h2>
<p>The app is in beta and may be offered through TestFlight or other testing channels. Features, capacity, availability and supported providers may change. Use an email address you control, provide accurate information, protect your sign-in credentials, and use the service only when you can lawfully agree to these terms. Obtain a parent or guardian's permission where required.</p>
<h2>Newsletters, aliases and AI connections</h2>
<p>You authorize the subscription and processing actions you request. UpToDate assigns a receiving alias so newsletter content can become posts without cluttering your personal inbox. Some publishers require confirmation or other actions from you. Successful receipt or a signup attempt does not guarantee that a publisher has completed your subscription.</p>
<p>Newsletter visibility controls affect the feed. Unsubscribing requests publisher cancellation; stopping a custom newsletter blocks processing in UpToDate. AI assistants own their recurring tasks. Cancelling or changing those schedules may require action in the relevant assistant. Disconnecting an account connection blocks further authorized deliveries.</p>
<h2>Generated content and original sources</h2>
<p>AI summaries, extracted details, answers and illustrations can be incomplete or inaccurate. Consult the linked original source before relying on a post. Source images are preferred when available, and generated illustrations may be used as a fallback. An illustration is not evidence of a real place, product, research result or event.</p>
<p>Publishers and other third parties retain their rights in source material. Links and attribution do not transfer those rights. Provide or connect only content you are authorized to use, and do not use the service to infringe others' rights. You permit us and the necessary service providers to process, store and display submitted content to provide the features you request, as described in the Privacy Policy.</p>
<h2>Acceptable use</h2>
<p>Do not use the service for spam, impersonation, unlawful content or unauthorized access to others' information. Do not share account credentials, bypass access controls, evade publisher restrictions, or interfere with other users or service providers. We may restrict use to protect users, enforce these terms or comply with law.</p>
<h2>Costs and limits</h2>
<p>The current beta does not charge users a subscription fee. Processing limits and provider budgets may pause or prevent work. Separate services you choose, including an AI assistant, may have their own plans or charges. Any future UpToDate paid offering will disclose its applicable prices and terms before purchase.</p>
<h2>Availability and responsibility</h2>
<p>To the extent permitted by applicable law, the beta is provided as available without a promise of uninterrupted access, error-free output or continued availability of every feature. You remain responsible for decisions you make using its content and for external services you choose. These terms do not exclude rights or responsibilities that applicable law does not allow to be excluded.</p>
<h2>Leaving the service and changes</h2>
<p>You can stop using the app, disconnect integrations, unsubscribe and delete your account. Data handling after these actions is described in the Privacy Policy. We may update these terms as the beta develops, publish the revised date, and provide notice of material changes through an appropriate service channel.</p>
<p>For support, privacy requests or concerns about source content, contact __CONTACT__.</p>'''


def page(kind,operator,email,draft=False):
    title='Privacy Policy' if kind=='privacy' else 'Terms of Service'
    content=(PRIVACY if kind=='privacy' else TERMS)
    contact='<a href="mailto:'+html.escape(email,quote=True)+'">'+html.escape(email)+'</a>'
    content=content.replace('__OPERATOR__',html.escape(operator)).replace('__CONTACT__',contact)
    notice='<p class="draft">Draft for review — not published. Confirm the operator, public contact and data-retention choices before publication.</p>' if draft else ''
    return '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'''+title+''' · UpToDate</title><style>:root{color-scheme:light dark}body{font:17px/1.7 system-ui;margin:0 auto;padding:32px 24px 64px;max-width:740px}nav{display:flex;gap:12px;flex-wrap:wrap}a{color:#1686ed}nav a{border:1px solid #8885;border-radius:99px;padding:6px 16px;text-decoration:none}h1{font-size:32px;line-height:1.2}h2{font-size:20px;margin-top:32px}small{opacity:.65}.draft{padding:16px;border-radius:20px;background:#edb22020;border:1px solid #edb22080}p{overflow-wrap:anywhere}</style><nav><a href="/">UpToDate</a><a href="/privacy">Privacy</a><a href="/terms">Terms</a></nav><h1>'''+title+'</h1><small>UpToDate beta · Updated '+UPDATED+'</small>'+notice+content+'</html>'


def install(app):
    def policy(kind):
        if os.environ.get('PUBLIC_POLICIES_ENABLED','0')!='1':raise HTTPException(404,'Not found')
        operator=os.environ.get('PUBLIC_DEVELOPER_NAME','').strip()
        email=os.environ.get('PUBLIC_SUPPORT_EMAIL','').strip()
        if not operator or len(operator)>120 or not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+',email):
            raise HTTPException(503,'Policy contact information is not configured')
        return HTMLResponse(page(kind,operator,email),headers={'Cache-Control':'public,max-age=300',
            'Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",'Referrer-Policy':'no-referrer'})
    @app.api_route('/privacy',methods=['GET','HEAD'],include_in_schema=False)
    def privacy():return policy('privacy')
    @app.api_route('/terms',methods=['GET','HEAD'],include_in_schema=False)
    def terms():return policy('terms')
