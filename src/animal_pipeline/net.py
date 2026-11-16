from __future__ import annotations

import urllib.parse
import urllib.request


def check_url(url: str, hosts: set[str], name: str = "URL") -> None:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in hosts or
            parsed.port not in (None, 443) or parsed.username or parsed.password or
            parsed.fragment):
        raise ValueError(f"unsafe {name}")


class RestrictedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, hosts: set[str], name: str):
        self.hosts = hosts
        self.name = name

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl, self.hosts, self.name)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def bounded_open(url: str, hosts: set[str], *, name: str = "URL", timeout: int = 30):
    check_url(url, hosts, name)
    opener = urllib.request.build_opener(RestrictedRedirect(hosts, name))
    response = opener.open(urllib.request.Request(
        url, headers={"User-Agent": "NaminalHub/0.1 rights-research"}), timeout=timeout)
    check_url(response.geturl(), hosts, name)
    return response


def bounded_request(url: str, hosts: set[str], *, name: str = "URL",
                    data: bytes | None = None, headers: dict | None = None,
                    timeout: int = 30, method: str | None = None):
    check_url(url, hosts, name)
    opener = urllib.request.build_opener(RestrictedRedirect(hosts, name))
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={"User-Agent": "NaminalHub/0.1 rights-research", **(headers or {})})
    response = opener.open(request, timeout=timeout)
    check_url(response.geturl(), hosts, name)
    return response
