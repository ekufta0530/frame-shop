"""The public privacy policy page, served at /privacy (linked from the login page and the Google consent screen)."""

PRIVACY_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Privacy Policy - Frame Shop Performance</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 16px/1.6 system-ui, sans-serif; max-width: 40rem; margin: 3rem auto; padding: 0 1rem; }
  h1 { font-size: 1.6rem; } h2 { font-size: 1.1rem; margin-top: 2rem; }
  .muted { opacity: .7; }
</style>
</head>
<body>
<h1>Privacy Policy</h1>
<p class="muted">Frame Shop Performance &middot; Last updated October 14, 2026</p>

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
about you, email <a href="mailto:eric.kufta@gmail.com">eric.kufta@gmail.com</a>.</p>

<h2>Changes</h2>
<p>If this policy changes, the date above will be updated.</p>
</body>
</html>
"""
