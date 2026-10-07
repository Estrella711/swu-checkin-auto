"""Log request outcomes without URLs containing credentials or response bodies."""

import time
from urllib.parse import urlsplit

import requests


SAFE_PATHS = {
    "/cas/oauth/login/SWU_CAS2_FEDERAL",
    "/am/UI/Login",
    "/am/validate.code",
    "/cas/oauth/callback/SWU_CAS2_FEDERAL",
    "/gateway/fighter-middle/api/integrate/uaap/cas/exchange-token",
    "/gateway/fighter-middle/api/auth/user",
    "/gateway/fighter-baida/api/xsqjxj/listSelfLeaveData",
    "/gateway/fighter-baida/api/cqlc/getDormitory",
    "/gateway/fighter-baida/api/cqtj/getTransitionByToday",
    "/gateway/fighter-baida/api/form-instance/save",
}


def safe_endpoint(url):
    parsed = urlsplit(url)
    path = "/" + parsed.path.lstrip("/")
    return f"{parsed.hostname}{path if path in SAFE_PATHS else '/[path omitted]'}"


original_request = requests.sessions.Session.request
original_json = requests.models.Response.json


def diagnosed_request(session, method, url, *args, **kwargs):
    started = time.monotonic()
    endpoint = safe_endpoint(url)
    try:
        response = original_request(session, method, url, *args, **kwargs)
    except requests.exceptions.RequestException as error:
        print(f"[HTTP] {method} {endpoint}: {type(error).__name__}, {time.monotonic() - started:.1f}s", flush=True)
        raise
    print(f"[HTTP] {method} {endpoint}: HTTP {response.status_code}, {time.monotonic() - started:.1f}s", flush=True)
    return response


def diagnosed_json(response, *args, **kwargs):
    try:
        return original_json(response, *args, **kwargs)
    except ValueError as error:
        print(f"[JSON] {safe_endpoint(response.url)}: HTTP {response.status_code}, {type(error).__name__}", flush=True)
        raise


requests.sessions.Session.request = diagnosed_request
requests.models.Response.json = diagnosed_json
