"""Submit supported newsletter forms with the server-assigned relay only.

JavaScript-only forms, CAPTCHA and additional consent steps require manual
action. A successful POST means submitted, never a confirmed subscription.
"""
import re
from urllib.parse import urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from .retrieval import SourceError, public_url


class FormSignup:
    supports_submission_guard = True

    def __init__(self, fetcher, browser=None):
        self.fetcher = fetcher
        self.browser = browser

    def subscribe(self, *, url, email, before_submit=None):
        if not re.fullmatch(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9.-]+", email) or email.endswith(".invalid"):
            raise SourceError("A working account relay is required for newsletter signup")
        page = self.fetcher.fetch(url)
        soup = BeautifulSoup(page.body, "html.parser")
        # 1440's current Next server action requires a reCAPTCHA token; it
        # cannot be completed by a plain form POST or our isolated browser.
        if urlsplit(page.url).hostname in ("join1440.com", "www.join1440.com"):
            return {"status": "manual_action", "detail": "1440 requires human verification on its website. Use Finish signup on publisher website, then paste your copied newsletter address. Incoming mail will connect the subscription."}
        if soup.select("script[src*=recaptcha], script[src*=hcaptcha], script[src*=turnstile], iframe[src*=recaptcha]"):
            return {"status": "manual_action", "detail": "The publisher requires a human verification step. Finish signup on its website using your account's newsletter address."}
        forms = []
        for form in soup.find_all("form", limit=20):
            fields = [node for node in form.find_all("input", attrs={"name": True})
                      if not node.has_attr("disabled") and
                      (node.get("type", "").lower() == "email" or re.search(r"(?:^|[_.\[])(?:email|email_address|emailaddress)(?:$|\])", node["name"], re.I))]
            label = form.get_text(" ", strip=True) + " " + str(form.get("action", ""))
            if len(fields) == 1 and re.search(r"subscrib|newsletter|join|sign.?up", label, re.I):
                forms.append((form, fields[0]))
        if len(forms) != 1:
            if self.browser:
                options = {"before_submit": before_submit} if before_submit is not None else {}
                return self.browser.subscribe(url=page.url, email=email, **options)
            return {"status": "manual_action", "detail": "No single supported newsletter form found; use the account relay to subscribe manually"}
        form, email_field = forms[0]
        if form.get("method", "get").lower() != "post" or form.get("enctype", "application/x-www-form-urlencoded").lower() != "application/x-www-form-urlencoded":
            return {"status": "manual_action", "detail": "This signup form needs manual submission using the account relay"}
        if form.select("[class*=captcha], [id*=captcha], [name*=captcha], iframe") or soup.select("script[src*=recaptcha], script[src*=hcaptcha], script[src*=turnstile]"):
            return {"status": "manual_action", "detail": "Complete the publisher's CAPTCHA manually using the account relay"}
        action = public_url(urljoin(page.url, form.get("action", "") or page.url))
        if urlsplit(action).scheme != "https" or re.search(r"unsubscribe|delete|login|signin", action, re.I):
            return {"status": "manual_action", "detail": "This form is not a supported secure newsletter signup"}
        values = []
        for field in form.find_all(["input", "select", "textarea"], limit=100):
            if field.has_attr("disabled") or not field.get("name"):
                continue
            kind = field.get("type", "text").lower()
            if field is email_field:
                values.append((field["name"], email))  # Always overwrite any publisher-prefilled email.
            elif field.has_attr("required") or kind in ("password", "file"):
                return {"status": "manual_action", "detail": "The publisher requires additional information or consent; finish signup manually with the account relay"}
            elif kind == "hidden":
                if "email" in field["name"].lower():
                    return {"status": "manual_action", "detail": "This form has ambiguous recipient fields; use the account relay manually"}
                values.append((field["name"], str(field.get("value", ""))[:8000]))
            elif kind not in ("submit", "button", "reset", "checkbox", "radio"):
                values.append((field["name"], ""))
        body = urlencode(values).encode()
        if len(body) > 32000:
            return {"status": "manual_action", "detail": "Signup form exceeds the local submission limit"}
        if before_submit is not None and not before_submit():
            return {"status": "manual_action", "detail": "Signup cancelled because the account or subscription is no longer active"}
        result = self.fetcher.submit_form(action, body)
        text = BeautifulSoup(result.body, "html.parser").get_text(" ", strip=True)
        if re.search(r"captcha|invalid email|something went wrong|try again|an error occurred", text, re.I):
            return {"status": "manual_action", "detail": "Publisher requested additional action; check signup manually with the account relay"}
        if re.search(r"confirm.*(?:email|subscription)|check your (?:email|inbox)|verification email", text, re.I):
            return {"status": "confirmation_required", "detail": "Signup submitted using your account relay; confirmation email must be completed manually in Receiving"}
        return {"status": "submitted", "detail": "Signup form submitted using your account relay; subscription confirmation has not been verified"}
