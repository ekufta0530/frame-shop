"""The public privacy policy (/privacy) and terms of service (/terms): linked from the login page and
the Google consent screen. Plain HTML pages served by the API, outside the SPA."""

_CONTACT = "eric.kufta@gmail.com"
_UPDATED = "October 14, 2026"


def _page(title, body):
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} - Frame Shop Performance</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 16px/1.6 system-ui, sans-serif; max-width: 40rem; margin: 3rem auto; padding: 0 1rem; }}
  h1 {{ font-size: 1.6rem; }} h2 {{ font-size: 1.1rem; margin-top: 2rem; }}
  .muted {{ opacity: .7; }}
</style>
</head>
<body>
<h1>{title}</h1>
<p class="muted">Frame Shop Performance &middot; Last updated {_UPDATED}</p>
{body}
<p class="muted" style="margin-top:2rem"><a href="/privacy">Privacy Policy</a> &middot; <a href="/terms">Terms of Service</a></p>
</body>
</html>
"""


PRIVACY_HTML = _page("Privacy Policy", f"""
<p>Frame Shop Performance is a private reporting site for a small group of picture-framing businesses.
Only people who have been given access can sign in.</p>

<h2>What we collect</h2>
<p>When you sign in with Google, we receive your Google account's email address and name. We use them only to
check that you have been given access and to show your name on the site. We do not receive your Google
password or access any other Google data (such as your mail, contacts or files).</p>
<p>We set one cookie to keep you signed in. It is not used for tracking or advertising.</p>

<h2>What the site shows</h2>
<p>The site displays sales and customer figures from the businesses' own records to people who have been given
access. That information is used only for the businesses' own reporting.</p>

<h2>Sharing</h2>
<p>We do not sell or share your information with anyone. The site runs on Google Cloud.</p>

<h2>Keeping and deleting your information</h2>
<p>Your email address stays on the access list until you are removed. To be removed, or to ask what we hold
about you, email <a href="mailto:{_CONTACT}">{_CONTACT}</a>.</p>

<h2>Changes</h2>
<p>If this policy changes, the date above will be updated.</p>
""")

TERMS_HTML = _page("Terms of Service", f"""
<p>By signing in to Frame Shop Performance you agree to these terms.</p>

<h2>Access</h2>
<p>The site is private and by invitation only. Your access is personal to you: keep your Google account secure
and do not let anyone else use it to sign in.</p>

<h2>Acceptable use</h2>
<p>Use the site only for the businesses' own reporting. Do not share what it shows with people outside those
businesses, copy it in bulk with automated tools, or try to get around the sign-in or access limits.</p>

<h2>The information shown</h2>
<p>The figures come from the businesses' own records and are provided as they are. We try to keep them accurate
and up to date, but we do not guarantee that they are complete or error-free, so check anything important
against the original records before relying on it.</p>

<h2>No warranty</h2>
<p>The site is provided "as is", without warranties of any kind, and may be unavailable or change at any time.
To the extent the law allows, we are not liable for any loss arising from using it.</p>

<h2>Ending access</h2>
<p>We may suspend or remove anyone's access at any time. You can ask to be removed by emailing
<a href="mailto:{_CONTACT}">{_CONTACT}</a>.</p>

<h2>Changes</h2>
<p>We may update these terms. If we do, the date above will change, and continuing to use the site means you
accept the update.</p>
""")
