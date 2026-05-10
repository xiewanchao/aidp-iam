#!/usr/bin/env python3
import html
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


HOST = os.environ.get("CAS_CLIENT_HOST", "127.0.0.1")
PORT = int(os.environ.get("CAS_CLIENT_PORT", "18080"))
KEYCLOAK_BASE_URL = os.environ.get("KEYCLOAK_BASE_URL", "http://localhost:30080").rstrip("/")
REALM = os.environ.get("REALM", "aidp")
SERVICE_URL = os.environ.get("SERVICE_URL", f"http://localhost:{PORT}/cas/callback")

CAS_BASE_URL = f"{KEYCLOAK_BASE_URL}/realms/{REALM}/protocol/cas"
CAS_LOGIN_URL = f"{CAS_BASE_URL}/login"
CAS_VALIDATE_URL = f"{CAS_BASE_URL}/serviceValidate"
CAS_LOGOUT_URL = f"{CAS_BASE_URL}/logout"

CAS_NS = {"cas": "http://www.yale.edu/tp/cas"}


def url_with_query(base, params):
    return f"{base}?{urllib.parse.urlencode(params)}"


def validate_ticket(ticket):
    url = url_with_query(CAS_VALIDATE_URL, {"service": SERVICE_URL, "ticket": ticket})
    request = urllib.request.Request(url, headers={"User-Agent": "aidp-cas-client-demo/1.0"})
    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read().decode("utf-8", errors="replace")
    return url, body


def parse_cas_response(xml_text):
    root = ET.fromstring(xml_text)
    success = root.find("cas:authenticationSuccess", CAS_NS)
    if success is None:
        failure = root.find("cas:authenticationFailure", CAS_NS)
        return {
            "ok": False,
            "user": "",
            "attributes": {},
            "failure": (failure.text or "").strip() if failure is not None else "Unknown CAS failure",
        }

    user_node = success.find("cas:user", CAS_NS)
    attrs_node = success.find("cas:attributes", CAS_NS)
    attrs = {}
    if attrs_node is not None:
        for child in list(attrs_node):
            name = child.tag.split("}", 1)[-1]
            attrs.setdefault(name, []).append(child.text or "")

    return {
        "ok": True,
        "user": user_node.text if user_node is not None else "",
        "attributes": attrs,
        "failure": "",
    }


def page(title, body, status=200):
    markup = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: #17202a;
      background: #f6f8fb;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; min-height: 100vh; }}
    main {{ width: min(980px, calc(100vw - 32px)); margin: 40px auto; }}
    header {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 22px; }}
    h1 {{ margin: 0; font-size: 28px; font-weight: 700; letter-spacing: 0; }}
    h2 {{ margin: 28px 0 10px; font-size: 16px; }}
    .panel {{ background: #ffffff; border: 1px solid #d9e0ea; border-radius: 8px; padding: 20px; }}
    .status {{ display: inline-flex; align-items: center; gap: 8px; border-radius: 999px; border: 1px solid #c7d2e2; padding: 6px 10px; font-size: 13px; background: #ffffff; }}
    .dot {{ width: 8px; height: 8px; border-radius: 50%; background: #238636; }}
    dl {{ display: grid; grid-template-columns: 190px 1fr; gap: 10px 16px; margin: 0; }}
    dt {{ color: #5b6777; }}
    dd {{ margin: 0; overflow-wrap: anywhere; }}
    code, pre {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; }}
    code {{ font-size: 13px; }}
    pre {{ margin: 0; overflow: auto; white-space: pre-wrap; background: #101820; color: #edf2f7; border-radius: 8px; padding: 16px; line-height: 1.5; }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 18px; }}
    a.button {{ display: inline-flex; align-items: center; justify-content: center; min-height: 38px; padding: 0 14px; border-radius: 6px; background: #14532d; color: #ffffff; text-decoration: none; font-weight: 650; }}
    a.secondary {{ background: #ffffff; color: #17202a; border: 1px solid #c7d2e2; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ text-align: left; border-bottom: 1px solid #e5eaf1; padding: 10px 8px; vertical-align: top; }}
    th {{ color: #5b6777; font-weight: 650; }}
    .error {{ border-color: #f2c2c2; background: #fff8f8; }}
    @media (max-width: 720px) {{
      main {{ margin-top: 24px; }}
      header {{ align-items: flex-start; flex-direction: column; }}
      dl {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main>{body}</main>
</body>
</html>"""
    return status, "text/html; charset=utf-8", markup.encode("utf-8")


class CasClientHandler(BaseHTTPRequestHandler):
    server_version = "CasClientDemo/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), fmt % args))

    def send_payload(self, status, content_type, data):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self.home()
        elif parsed.path == "/login":
            self.redirect(url_with_query(CAS_LOGIN_URL, {"service": SERVICE_URL}))
        elif parsed.path == "/cas/callback":
            self.callback(parsed)
        elif parsed.path == "/logout":
            self.redirect(url_with_query(CAS_LOGOUT_URL, {"service": f"http://localhost:{PORT}/"}))
        elif parsed.path == "/health":
            self.send_payload(200, "text/plain; charset=utf-8", b"ok\n")
        else:
            self.send_payload(*page("Not found", "<h1>Not found</h1>", 404))

    def home(self):
        body = f"""
<header>
  <div>
    <h1>CAS Client Demo</h1>
  </div>
  <span class="status"><span class="dot"></span>Ready</span>
</header>
<section class="panel">
  <dl>
    <dt>CAS login URL</dt><dd><code>{html.escape(CAS_LOGIN_URL)}</code></dd>
    <dt>CAS validate URL</dt><dd><code>{html.escape(CAS_VALIDATE_URL)}</code></dd>
    <dt>Service URL</dt><dd><code>{html.escape(SERVICE_URL)}</code></dd>
    <dt>Realm</dt><dd><code>{html.escape(REALM)}</code></dd>
  </dl>
  <div class="actions">
    <a class="button" href="/login">Login with Keycloak CAS</a>
    <a class="button secondary" href="/logout">Logout</a>
  </div>
</section>
<h2>Expected Keycloak Client</h2>
<section class="panel">
  <dl>
    <dt>Client type</dt><dd><code>CAS</code></dd>
    <dt>Valid redirect URI</dt><dd><code>{html.escape(SERVICE_URL)}</code></dd>
  </dl>
</section>
"""
        self.send_payload(*page("CAS Client Demo", body))

    def callback(self, parsed):
        query = urllib.parse.parse_qs(parsed.query)
        ticket = query.get("ticket", [""])[0]
        if not ticket:
            body = """
<header><h1>CAS Callback</h1></header>
<section class="panel error">No CAS ticket was provided.</section>
<div class="actions"><a class="button secondary" href="/">Back</a></div>
"""
            self.send_payload(*page("CAS Callback", body, 400))
            return

        try:
            validate_url, xml_text = validate_ticket(ticket)
            parsed_response = parse_cas_response(xml_text)
            status = 200 if parsed_response["ok"] else 401
            attrs = parsed_response["attributes"]
            if attrs:
                rows = "".join(
                    f"<tr><td><code>{html.escape(name)}</code></td><td><code>{html.escape(', '.join(values))}</code></td></tr>"
                    for name, values in attrs.items()
                )
            else:
                rows = '<tr><td colspan="2">No attributes returned.</td></tr>'

            outcome = (
                f"<span class=\"status\"><span class=\"dot\"></span>Authenticated as <strong>{html.escape(parsed_response['user'])}</strong></span>"
                if parsed_response["ok"]
                else f"<span class=\"status\">Authentication failed: {html.escape(parsed_response['failure'])}</span>"
            )
            body = f"""
<header>
  <h1>CAS Callback</h1>
  {outcome}
</header>
<section class="panel">
  <dl>
    <dt>Ticket</dt><dd><code>{html.escape(ticket)}</code></dd>
    <dt>Validation request</dt><dd><code>{html.escape(validate_url)}</code></dd>
  </dl>
</section>
<h2>CAS Attributes</h2>
<section class="panel">
  <table><thead><tr><th>Name</th><th>Value</th></tr></thead><tbody>{rows}</tbody></table>
</section>
<h2>Raw CAS XML</h2>
<pre>{html.escape(xml_text)}</pre>
<div class="actions"><a class="button secondary" href="/">Back</a><a class="button secondary" href="/logout">Logout</a></div>
"""
            self.send_payload(*page("CAS Callback", body, status))
        except (urllib.error.URLError, ET.ParseError, TimeoutError) as exc:
            body = f"""
<header><h1>CAS Callback</h1></header>
<section class="panel error">
  <p>CAS validation failed.</p>
  <pre>{html.escape(str(exc))}</pre>
</section>
<div class="actions"><a class="button secondary" href="/">Back</a></div>
"""
            self.send_payload(*page("CAS Callback", body, 502))


def main():
    server = ThreadingHTTPServer((HOST, PORT), CasClientHandler)
    print(f"CAS Client Demo: http://localhost:{PORT}", flush=True)
    print(f"Service URL: {SERVICE_URL}", flush=True)
    print(f"Keycloak CAS: {CAS_BASE_URL}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
