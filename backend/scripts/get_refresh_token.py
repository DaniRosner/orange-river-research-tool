"""
One-time setup script: authorizes this app against a Dropbox account and
prints a long-lived refresh token to paste into .env as
DROPBOX_REFRESH_TOKEN. Only needs to be run once (or again if the token is
ever revoked/rotated). Requires DROPBOX_APP_KEY and DROPBOX_APP_SECRET to
already be set in .env.
"""

import os

from dotenv import load_dotenv
from dropbox import DropboxOAuth2FlowNoRedirect

load_dotenv("../.env")

app_key = os.environ["DROPBOX_APP_KEY"]
app_secret = os.environ["DROPBOX_APP_SECRET"]

flow = DropboxOAuth2FlowNoRedirect(app_key, app_secret, token_access_type="offline")

authorize_url = flow.start()
print("1. Go to this URL and click Allow:")
print(authorize_url)
print()

auth_code = input("2. Paste the authorization code shown by Dropbox here: ").strip()

result = flow.finish(auth_code)
print()
print("Refresh token (paste this into .env as DROPBOX_REFRESH_TOKEN):")
print(result.refresh_token)
