from pathlib import Path

try:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request

    GDRIVE_OK = True
except ImportError:
    GDRIVE_OK = False

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
TOKEN_FILE = "token.json"

_service = None


def is_available() -> bool:
    return GDRIVE_OK


def init(client_secret_file: str):
    """
    Initialise Google Drive service using OAuth2.
    First run: opens browser for login and saves token.json.
    Subsequent runs: loads token.json automatically — no browser needed.
    Returns the Drive service object, or None on failure.
    """
    global _service

    if _service:
        return _service

    if not GDRIVE_OK:
        print("  ✗ Google Drive libraries not installed.")
        print(
            "    pip install google-api-python-client google-auth-oauthlib google-auth-httplib2"
        )
        return None

    try:
        creds = None

        # Load saved token if it exists
        if Path(TOKEN_FILE).exists():
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, DRIVE_SCOPES)

        # Refresh or re-authenticate if needed
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    client_secret_file, DRIVE_SCOPES
                )
                print(f"\n  → Opening browser for Google Drive login…")
                print(f"  Token will be saved to {TOKEN_FILE} for future runs.\n")
                creds = flow.run_local_server(port=0)

            # Save token for next run
            with open(TOKEN_FILE, "w") as f:
                f.write(creds.to_json())
            print(f"  ✓ Drive token saved → {TOKEN_FILE}")

        _service = build("drive", "v3", credentials=creds, cache_discovery=False)
        print(f"  ✓ Google Drive connected")
        return _service

    except Exception as e:
        print(f"  ✗ Drive auth failed: {e}")
        return None


def upload(local_path: str, folder_id: str, drive_service) -> str:
    """
    Upload a local file to Google Drive.
    Sets permission to 'anyone with link can view'.
    Returns the shareable Drive link, or empty string on failure.

    Retries up to 3 times with exponential backoff to handle transient
    SSL errors that occur when multiple threads upload concurrently.
    """
    import time as _time

    fname = Path(local_path).name
    max_attempts = 3

    for attempt in range(1, max_attempts + 1):
        try:
            media = MediaFileUpload(
                str(local_path), mimetype="image/png", resumable=False
            )
            meta = {"name": fname, "parents": [folder_id]}

            f = (
                drive_service.files()
                .create(
                    body=meta,
                    media_body=media,
                    fields="id",
                )
                .execute()
            )
            fid = f.get("id")

            drive_service.permissions().create(
                fileId=fid,
                body={"type": "anyone", "role": "reader"},
            ).execute()

            return f"https://drive.google.com/file/d/{fid}/view"

        except Exception as e:
            err = str(e)
            is_ssl = any(
                kw in err.upper()
                for kw in (
                    "SSL",
                    "HANDSHAKE",
                    "DECRYPTION",
                    "CIPHER",
                    "RECORD",
                    "WRONG_VERSION",
                    "INCOMPLETE",
                    "CORRUPT",
                )
            )
            if attempt < max_attempts:
                delay = 2**attempt  # 2s, 4s
                if is_ssl:
                    print(
                        f"  ⚠  Drive SSL error (attempt {attempt}/{max_attempts}) — retrying in {delay}s..."
                    )
                else:
                    print(
                        f"  ⚠  Drive upload failed (attempt {attempt}/{max_attempts}): {err[:60]} — retrying in {delay}s..."
                    )
                _time.sleep(delay)
            else:
                print(
                    f"  ⚠  Drive upload failed after {max_attempts} attempts: {err[:80]}"
                )

    return ""
