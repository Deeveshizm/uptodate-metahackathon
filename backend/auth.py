"""Supabase owns passwords and sessions; the API trusts only verified identity.

No password or refresh token passes through this backend. The native client uses
Supabase's HTTPS Auth endpoints directly. This module checks the current user
with Auth and reads their RLS-protected profile using the SAME bearer token.
"""
import base64
import json
import os
import uuid
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException


class SupabaseAuth:
    def __init__(self, url, publishable_key, secret_key="", transport=None, relay_aliases=False):
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.port not in (None, 443) or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
            raise ValueError("SUPABASE_URL must be an HTTPS project origin")
        if not publishable_key or publishable_key.startswith("sb_secret_"):
            raise ValueError("Use a Supabase publishable key, never a secret key, for client configuration")
        if not publishable_key.startswith("sb_publishable_"):
            try:
                payload = publishable_key.split(".")[1]
                role = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["role"]
            except (IndexError, KeyError, ValueError, TypeError):
                raise ValueError("Use a Supabase publishable key or legacy anon key") from None
            if role != "anon":
                raise ValueError("The client key must have the anon role")
        self.url, self.key, self.secret = url.rstrip("/"), publishable_key, secret_key
        self.transport = transport
        self.relay_aliases = relay_aliases

    @classmethod
    def from_environment(cls):
        mode = os.environ.get("AUTH_MODE", "local")
        if mode not in ("local", "supabase"):
            raise ValueError("AUTH_MODE must be local or supabase")
        if mode == "local":
            return None
        return cls(os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_PUBLISHABLE_KEY", ""),
                   os.environ.get("SUPABASE_SECRET_KEY", ""),
                   relay_aliases=os.environ.get("RELAY_ALIASES_ENABLED", "0") == "1")

    def configuration(self):
        return {"mode": "supabase", "url": self.url, "publishable_key": self.key}

    def request(self, method, path, token, *, admin=False, **kwargs):
        key = self.secret if admin else self.key
        with httpx.Client(timeout=15, trust_env=False, transport=self.transport) as client:
            try:
                result = client.request(method, self.url + path,
                                        headers={"apikey": key, "Authorization": "Bearer " + token}, **kwargs)
            except httpx.HTTPError:
                raise HTTPException(503, "Account service is temporarily unavailable; try again") from None
        if result.status_code in (401, 403):
            if admin:
                raise HTTPException(503, "Server account administration is unavailable; your account was not deleted")
            raise HTTPException(401, "Sign in again to continue")
        if not result.is_success:
            raise HTTPException(503, "Account service or profile setup is unavailable; try again later")
        return result

    def identity(self, token):
        try:
            user = self.request("GET", "/auth/v1/user", token).json()
            user_id = str(uuid.UUID(user["id"]))
        except (KeyError, ValueError, TypeError):
            raise HTTPException(401, "Invalid account identity") from None
        if user.get("is_anonymous") or not user.get("email_confirmed_at"):
            raise HTTPException(403, "Verify your email address before continuing")
        profiles = self.request("GET", "/rest/v1/profiles", token,
                                params={"id": "eq." + user_id,
                                        "select": "id,alias,alias_status,timezone,display_name"}).json()
        if not isinstance(profiles, list) or len(profiles) != 1 or profiles[0].get("id") != user_id:
            raise HTTPException(503, "Your account profile is not ready; check the Supabase migrations")
        return {**profiles[0], "id": user_id, "email": user.get("email", "")}

    def delete(self, user_id):
        if not self.secret:
            raise HTTPException(503, "Account deletion needs server-side Supabase administration configuration")
        self.request("DELETE", "/auth/v1/admin/users/" + str(uuid.UUID(user_id)), self.secret, admin=True)

    def update_profile(self, token, user_id, fields):
        self.request("PATCH", "/rest/v1/profiles", token,
                     params={"id": "eq." + user_id}, json=fields)

    def ensure_relay(self, token, profile):
        if not self.relay_aliases:
            return profile
        rows = self.request("POST", "/rest/v1/rpc/ensure_relay_alias", token, json={}).json()
        if not isinstance(rows, list) or len(rows) != 1 or rows[0].get("id") != profile["id"]:
            raise HTTPException(503, "Newsletter address setup is unavailable; try again later")
        return {**rows[0], "email": profile.get("email", "")}
