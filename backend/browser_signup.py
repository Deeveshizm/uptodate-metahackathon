"""Isolated JavaScript signup using the account relay, with no model calls.

All HTTP requests use DNS-pinned public connections. Personal browser profiles,
files, WebSockets, service workers and local-network requests are unavailable.
Publisher tokens/cookies are discarded with the fresh browser context.
"""
import http.client
import json
import re
import threading
import time
import zlib
import os
import selectors
import signal
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit, urlunsplit

from .retrieval import MAX_BYTES, PinnedHTTP, PinnedHTTPS, Resource, SourceError, public_url, resolve_public

BROWSER_LOCK = threading.Lock()


class BrowserTransport:
    def request(self, url, method, headers, body):
        parts = urlsplit(public_url(url))
        if method not in ("GET", "HEAD", "OPTIONS", "POST") or (method == "POST" and parts.scheme != "https"):
            raise SourceError("Unsupported signup request")
        if body and len(body) > 64_000:
            raise SourceError("Signup request exceeds the size limit")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        addresses = resolve_public(parts.hostname, port)
        connection = (PinnedHTTPS if parts.scheme == "https" else PinnedHTTP)(parts.hostname, port, addresses)
        connection.timeout = 8
        path = urlunsplit(("", "", parts.path or "/", parts.query, ""))
        forwarded = {k: v for k, v in headers.items() if k.lower() not in
                     ("host", "content-length", "accept-encoding", "connection", "proxy-authorization")}
        forwarded["accept-encoding"] = "identity"
        try:
            connection.request(method, path, body=body, headers=forwarded)
            response = connection.getresponse()
            data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise SourceError("Signup response exceeds the size limit")
            encoding = response.getheader("Content-Encoding", "identity").lower()
            if encoding in ("gzip", "deflate"):
                decoder = zlib.decompressobj(16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS)
                data = decoder.decompress(data, MAX_BYTES + 1)
                if len(data) > MAX_BYTES or decoder.unconsumed_tail:
                    raise SourceError("Signup response exceeds the decompressed size limit")
            elif encoding not in ("", "identity"):
                raise SourceError("Unsupported signup response encoding")
            output = {}
            for key, value in response.getheaders():
                key = key.lower()
                if key in ("content-length", "content-encoding", "transfer-encoding", "connection"):
                    continue
                # Chromium's response interception cannot accept embedded
                # newlines in ordinary repeated headers. Only Set-Cookie uses
                # Playwright's documented newline-separated representation.
                separator = "\n" if key == "set-cookie" else ", "
                output[key] = output[key] + separator + value if key in output else value
            return Resource(url, response.getheader("Content-Type", ""), data, response.status), output
        except (OSError, http.client.HTTPException, zlib.error):
            raise SourceError("The signup site could not be reached securely") from None
        finally:
            connection.close()


def carries_alias(body, email):
    if not body:
        return False
    try:
        value = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        try:
            return any(email in values for values in parse_qs(body.decode()).values())
        except UnicodeDecodeError:
            return False
    def contains(value):
        if isinstance(value, dict):
            return any(contains(item) for item in value.values())
        if isinstance(value, list):
            return any(contains(item) for item in value)
        return value == email
    return contains(value)


def signup_response(resource):
    try:
        value = json.loads(resource.body)
    except (ValueError, UnicodeDecodeError):
        value = None
    if resource.status == 409:
        return {"status": "submitted", "detail": "The publisher reports this alias is already registered; awaiting incoming email", "adapter": "javascript"}
    if 200 <= resource.status < 400 and not (isinstance(value, dict) and (value.get("success") is False or value.get("error"))):
        return {"status": "submitted", "detail": "Signup submitted using your account alias; awaiting confirmation or the first newsletter email", "adapter": "javascript"}
    return {"status": "manual_action", "detail": "The publisher did not accept the signup automatically; check confirmation or CAPTCHA using your account alias", "adapter": "javascript"}


class BrowserSignup:
    def __init__(self, transport=None, timeout=45):
        self.isolated = transport is None
        self.transport, self.timeout = transport or BrowserTransport(), timeout
        self.last_diagnostic = None

    def subscribe(self, *, url, email, before_submit=None):
        if not re.fullmatch(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9.-]+", email) or email.endswith(".invalid"):
            raise SourceError("A working account relay is required for newsletter signup")
        if urlsplit(public_url(url)).scheme != "https":
            raise SourceError("JavaScript newsletter signup requires HTTPS")
        if not BROWSER_LOCK.acquire(timeout=2):
            return self.manual("Another signup is running; retry this subscription shortly")
        try:
            return self._supervised(url, email, before_submit) if self.isolated else self._subscribe(url, email, before_submit)
        finally:
            BROWSER_LOCK.release()

    @staticmethod
    def manual(detail):
        return {"status": "manual_action", "detail": detail, "adapter": "javascript"}

    def _supervised(self, url, email, before_submit):
        """A stuck browser or intercepted network call cannot hold the worker."""
        process = subprocess.Popen([sys.executable, "-m", "backend.browser_signup", "--worker"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   text=True, start_new_session=True)
        try:
            process.stdin.write(json.dumps({"url": url, "email": email, "timeout": self.timeout}) + "\n"); process.stdin.flush()
            deadline = time.monotonic() + self.timeout + 10
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while time.monotonic() < deadline:
                    if not selector.select(max(0, deadline - time.monotonic())):
                        break
                    line = process.stdout.readline()
                    if not line:
                        break
                    event = json.loads(line)
                    if event.get("event") == "before_submit":
                        allowed = before_submit is None or before_submit()
                        process.stdin.write(json.dumps({"allowed": bool(allowed)}) + "\n"); process.stdin.flush()
                    elif event.get("event") == "result":
                        self.last_diagnostic = event.get("diagnostic")
                        return event["outcome"]
        except (OSError, ValueError):
            pass
        finally:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait()
            process.stdin.close(); process.stdout.close()
        return self.manual("Signup timed out. Check incoming mail before retrying; the publisher may have received it. You can also finish signup on the publisher website using your newsletter address.")

    def _subscribe(self, url, email, before_submit):
        try:
            from playwright.sync_api import Error, sync_playwright
        except ImportError:
            raise SourceError("JavaScript signup needs Playwright and its Chromium runtime installed") from None
        state = {"armed": False, "attempted": False, "result": None, "requests": 0, "bytes": 0, "phase": "browser_setup"}
        deadline = time.monotonic() + self.timeout

        def route_request(route):
            request = route.request
            parts = urlsplit(request.url)
            body = request.post_data_buffer
            if state["armed"] and request.method == "GET" and any(email in values for values in parse_qs(parts.query).values()):
                state["result"] = self.manual("This form would put your newsletter address in a URL. Use the publisher's secure signup instead.")
                route.abort()
                return
            if state["armed"] and request.method == "POST" and carries_alias(body, email) and before_submit is not None and not before_submit():
                state["result"] = state["result"] or self.manual("Signup cancelled because the account or subscription is no longer active")
                route.abort()
                return
            candidate = (state["armed"] and request.method == "POST" and carries_alias(body, email)
                         and (re.search(r"subscrib|newsletter|sign.?up|register", parts.path, re.I) or request.is_navigation_request()))
            # Do not proceed into social login or account administration after
            # newsletter signup. Only the publisher's signup submission is needed.
            if (time.monotonic() > deadline or state["requests"] >= 100 or state["bytes"] >= 25_000_000
                    or parts.scheme not in ("https", "http")
                    or request.resource_type in ("image", "media", "font")
                    or re.search(r"(?:^|\.)(?:posthog|doubleclick|googletagmanager|google-analytics|facebook|hotjar)\.", parts.hostname or "")
                    or re.search(r"/auth/(?:signin|callback)|/user/(?:settings|delete)|/logout", parts.path, re.I)):
                route.abort()
                return
            if candidate:
                if state["attempted"]:
                    route.abort()
                    return
                state["attempted"] = True
            state["requests"] += 1
            try:
                forwarded = request.headers
                if "cookie" not in forwarded:
                    cookies = request.frame.page.context.cookies(request.url)
                    if cookies:
                        forwarded["cookie"] = "; ".join(cookie["name"] + "=" + cookie["value"] for cookie in cookies)
                resource, headers = self.transport.request(request.url, request.method, forwarded, body)
                state["bytes"] += len(resource.body)
                if candidate:
                    state["result"] = signup_response(resource)
                route.fulfill(status=resource.status, headers=headers, body=resource.body)
            except SourceError:
                route.abort()

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    context = browser.new_context(service_workers="block", accept_downloads=False)
                    context.route("**/*", route_request)
                    context.route_web_socket("**/*", lambda socket: socket.close())
                    page = context.new_page()
                    page.on("dialog", lambda dialog: dialog.dismiss())
                    page.set_default_timeout(5000)
                    page.set_default_navigation_timeout(self.timeout * 1000)
                    state["phase"] = "loading_page"
                    page.goto(url, wait_until="domcontentloaded")
                    state["phase"] = "finding_email_field"
                    inputs = page.locator('input[type="email"]:visible, input[name="email" i]:visible')
                    inputs.first.wait_for(state="visible", timeout=10000)
                    # Give hydration a bounded opportunity to attach its handlers.
                    page.wait_for_timeout(800)
                    if page.get_by_text(re.compile(r"verify (?:that )?you are human|complete the captcha", re.I)).count():
                        return self.manual("The publisher requires CAPTCHA; complete signup manually using your account alias")
                    field = inputs.first
                    form = field.locator("xpath=ancestor::form[1]")
                    scope = form if form.count() else page
                    # Hydrated forms often omit method while JavaScript sends
                    # HTTPS POST. Block an actual alias-bearing GET in routing.
                    required = scope.locator('input[required]:visible, select[required]:visible, textarea[required]:visible')
                    for item in required.all():
                        if item.get_attribute("type") != "email" and item.get_attribute("name") != "email":
                            return self.manual("The publisher requires additional information or consent; complete signup manually with the alias")
                    captcha = scope.locator('iframe[src*="recaptcha"]:visible, iframe[src*="hcaptcha"]:visible, [class*="turnstile"]:visible')
                    if captcha.count():
                        return self.manual("The publisher requires CAPTCHA; complete signup manually using your account alias")
                    state["phase"] = "filling_alias"
                    field.fill(email)
                    if field.input_value() != email:
                        return self.manual("The signup field did not retain the account alias; nothing was submitted")
                    buttons = scope.get_by_role("button", name=re.compile(r"^(?:join|subscribe|sign.?up|get (?:the |my )?newsletter)(?:\b|$)", re.I))
                    visible = [button for button in buttons.all() if button.is_visible() and button.is_enabled()]
                    if not visible:
                        return self.manual("No supported newsletter signup action was found after JavaScript loaded")
                    state["armed"] = True
                    state["phase"] = "submitting_signup"
                    visible[0].click(timeout=5000, no_wait_after=True)
                    while not state["result"] and time.monotonic() < deadline:
                        page.wait_for_timeout(200)
                    return state["result"] or self.manual("Signup could not be confirmed; check incoming mail before retrying")
                finally:
                    browser.close()
        except Error:
            return state["result"] or self.manual("JavaScript signup could not finish; check browser setup, confirmation, or CAPTCHA before retrying")
        finally:
            self.last_diagnostic = {key: state[key] for key in ("phase", "attempted", "requests")}


if __name__ == "__main__":
    job = json.loads(sys.stdin.readline())
    worker = BrowserSignup(transport=BrowserTransport(), timeout=job["timeout"])
    def approved():
        print(json.dumps({"event": "before_submit"}), flush=True)
        return json.loads(sys.stdin.readline()).get("allowed") is True
    outcome = worker.subscribe(url=job["url"], email=job["email"], before_submit=approved)
    print(json.dumps({"event": "result", "outcome": outcome, "diagnostic": worker.last_diagnostic}), flush=True)
