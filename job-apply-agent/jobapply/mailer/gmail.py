"""Gmail API client.

Default behaviour is **draft, never send**: the agent creates a real Gmail draft
with the resume attached and stops. You open Gmail, read it, hit send. Switch to
`JOBAPPLY_EMAIL_MODE=send` (or `--send`) once you trust the drafts.

Scopes requested are `gmail.compose` (create/read/update drafts) and
`gmail.send`. Nothing here reads your mailbox.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from .. import settings as settings_mod

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
]


class GmailUnavailable(RuntimeError):
    pass


@dataclass
class SendOutcome:
    draft_id: str | None
    message_id: str | None
    sent: bool


class GmailClient:
    def __init__(self, credentials_path: Path | None = None, token_path: Path | None = None) -> None:
        st = settings_mod.load()
        self.credentials_path = Path(credentials_path or st.env.gmail_credentials)
        self.token_path = Path(token_path or st.env.gmail_token)
        self._service: Any = None

    # ------------------------------------------------------------------ auth

    def configured(self) -> bool:
        return self.credentials_path.exists() or self.token_path.exists()

    def authorize(self, headless: bool = False) -> None:
        """Run the OAuth flow. Called by `jobapply gmail-auth`."""
        from google_auth_oauthlib.flow import InstalledAppFlow

        if not self.credentials_path.exists():
            raise GmailUnavailable(
                f"OAuth client file not found at {self.credentials_path}. "
                "Create a Desktop OAuth client in Google Cloud Console, download the "
                "JSON, and save it there. See docs/gmail.md."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_path), SCOPES)
        creds = flow.run_console() if headless else flow.run_local_server(port=0)
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(creds.to_json(), encoding="utf-8")
        self.token_path.chmod(0o600)
        log.info("gmail token saved to %s", self.token_path)

    @property
    def service(self) -> Any:
        if self._service is not None:
            return self._service
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except ImportError as exc:  # pragma: no cover
            raise GmailUnavailable("google api client libraries are not installed") from exc

        if not self.token_path.exists():
            raise GmailUnavailable(
                f"no Gmail token at {self.token_path}. Run `jobapply gmail-auth` first."
            )
        creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                self.token_path.write_text(creds.to_json(), encoding="utf-8")
            else:
                raise GmailUnavailable("Gmail credentials are invalid; re-run `jobapply gmail-auth`")
        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._service

    # --------------------------------------------------------------- messages

    def build_message(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        attachment: Path | None = None,
        cc: list[str] | None = None,
        sender: str | None = None,
    ) -> EmailMessage:
        msg = EmailMessage()
        msg["To"] = to
        if cc:
            msg["Cc"] = ", ".join(cc)
        if sender:
            msg["From"] = sender
        msg["Subject"] = subject
        msg.set_content(body)

        if attachment:
            attachment = Path(attachment)
            if not attachment.exists():
                raise GmailUnavailable(f"attachment not found: {attachment}")
            ctype, _ = mimetypes.guess_type(attachment.name)
            maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
            msg.add_attachment(
                attachment.read_bytes(),
                maintype=maintype,
                subtype=subtype or "octet-stream",
                filename=attachment.name,
            )
        return msg

    def create_draft(self, msg: EmailMessage) -> str:
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        draft = (
            self.service.users()
            .drafts()
            .create(userId="me", body={"message": {"raw": raw}})
            .execute()
        )
        return draft["id"]

    def send_draft(self, draft_id: str) -> str:
        sent = self.service.users().drafts().send(userId="me", body={"id": draft_id}).execute()
        return sent["id"]

    def deliver(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        attachment: Path | None = None,
        cc: list[str] | None = None,
        send: bool = False,
    ) -> SendOutcome:
        """Create a draft, and send it only when `send` is explicitly True."""
        msg = self.build_message(to=to, subject=subject, body=body, attachment=attachment, cc=cc)
        draft_id = self.create_draft(msg)
        if not send:
            log.info("gmail draft %s created for %s (not sent)", draft_id, to)
            return SendOutcome(draft_id=draft_id, message_id=None, sent=False)
        message_id = self.send_draft(draft_id)
        log.info("gmail draft %s sent to %s", draft_id, to)
        return SendOutcome(draft_id=draft_id, message_id=message_id, sent=True)
