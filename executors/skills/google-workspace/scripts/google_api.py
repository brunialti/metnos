#!/usr/bin/env python3
"""Google Workspace API CLI for Metnos.

Uses the Google Workspace CLI (`gws`) when available, but preserves the
existing Metnos-facing JSON contract and falls back to the Python client
libraries if `gws` is not installed.

Usage:
  python google_api.py gmail search "is:unread" [--max 10]
  python google_api.py gmail get MESSAGE_ID
  python google_api.py gmail send --to user@example.com --subject "Hi" --body "Hello"
  python google_api.py gmail reply MESSAGE_ID --body "Thanks"
  python google_api.py calendar list [--from DATE] [--to DATE] [--calendar primary]
  python google_api.py calendar create --summary "Meeting" --start DATETIME --end DATETIME
  python google_api.py drive search "budget report" [--max 10]
  python google_api.py contacts list [--max 20]
  python google_api.py sheets get SHEET_ID RANGE
  python google_api.py sheets update SHEET_ID RANGE --values '[[...]]'
  python google_api.py sheets append SHEET_ID RANGE --values '[[...]]'
  python google_api.py docs get DOC_ID
"""

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path

# Ensure sibling modules (_skill_home) are importable when run standalone.
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from _skill_home import get_skill_home
from _scopes import SCOPES  # SoT unica (guard: test_google_scopes_sot)

try:
    # Presente quando lo script gira come backend Metnos. Il budget arriva dal
    # manifest firmato attraverso il runtime, anche su executor remoti.
    from executor_workers import assigned_workers, map_ordered
except ImportError:  # uso standalone della skill: fallback fail-closed seriale
    def assigned_workers(*, item_count=None):
        return 1

    def map_ordered(fn, items, *, deadline_s=None):
        del deadline_s
        return [(index, fn(item)) for index, item in enumerate(items)], []

METNOS_SKILL_HOME = get_skill_home()
TOKEN_PATH = METNOS_SKILL_HOME / "google_token.json"
CLIENT_SECRET_PATH = METNOS_SKILL_HOME / "google_client_secret.json"


def _normalize_authorized_user_payload(payload: dict) -> dict:
    normalized = dict(payload)
    if not normalized.get("type"):
        normalized["type"] = "authorized_user"
    return normalized


def _ensure_authenticated():
    if not TOKEN_PATH.exists():
        print("Not authenticated. Run the setup script first:", file=sys.stderr)
        print(f"  python {Path(__file__).parent / 'setup.py'}", file=sys.stderr)
        sys.exit(1)


def _stored_token_scopes() -> list[str]:
    try:
        data = json.loads(TOKEN_PATH.read_text())
    except Exception:
        return list(SCOPES)
    scopes = data.get("scopes")
    if isinstance(scopes, list) and scopes:
        return scopes
    return list(SCOPES)


def _gws_binary() -> str | None:
    override = os.getenv("METNOS_GWS_BIN")
    if override:
        return override
    return shutil.which("gws")


def _gws_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE"] = str(TOKEN_PATH)
    return env


def _run_gws(parts: list[str], *, params: dict | None = None, body: dict | None = None):
    binary = _gws_binary()
    if not binary:
        raise RuntimeError("gws not installed")

    _ensure_authenticated()

    cmd = [binary, *parts]
    if params is not None:
        cmd.extend(["--params", json.dumps(params)])
    if body is not None:
        cmd.extend(["--json", json.dumps(body)])

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=_gws_env(),
    )
    if result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip() or "Unknown gws error"
        print(err, file=sys.stderr)
        sys.exit(result.returncode or 1)

    stdout = result.stdout.strip()
    if not stdout:
        return {}

    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        print("ERROR: Unexpected non-JSON output from gws:", file=sys.stderr)
        print(stdout, file=sys.stderr)
        sys.exit(1)


def _headers_dict(msg: dict) -> dict[str, str]:
    return {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}


def _extract_message_body(msg: dict) -> str:
    body = ""
    payload = msg.get("payload", {})
    if payload.get("body", {}).get("data"):
        body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
    elif payload.get("parts"):
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
                break
        if not body:
            for part in payload["parts"]:
                if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
                    body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
                    break
    return body


def _extract_doc_text(doc: dict) -> str:
    text_parts = []
    for element in doc.get("body", {}).get("content", []):
        paragraph = element.get("paragraph", {})
        for pe in paragraph.get("elements", []):
            text_run = pe.get("textRun", {})
            if text_run.get("content"):
                text_parts.append(text_run["content"])
    return "".join(text_parts)


def _datetime_with_timezone(value: str) -> str:
    if not value:
        return value
    if "T" not in value:
        return value
    if value.endswith("Z"):
        return value
    tail = value[10:]
    if "+" in tail or "-" in tail:
        return value
    return value + "Z"


def get_credentials():
    """Load and refresh credentials from token file."""
    _ensure_authenticated()

    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), _stored_token_scopes())
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_PATH.write_text(
            json.dumps(
                _normalize_authorized_user_payload(json.loads(creds.to_json())),
                indent=2,
            )
        )
    if not creds.valid:
        print("Token is invalid. Re-run setup.", file=sys.stderr)
        sys.exit(1)
    return creds


def build_service(api, version):
    from googleapiclient.discovery import build

    return build(api, version, credentials=get_credentials())


# =========================================================================
# Gmail
# =========================================================================


def gmail_search(args):
    if _gws_binary():
        results = _run_gws(
            ["gmail", "users", "messages", "list"],
            params={"userId": "me", "q": args.query, "maxResults": args.max},
        )
        messages = results.get("messages", [])
        output = []
        for msg_meta in messages:
            msg = _run_gws(
                ["gmail", "users", "messages", "get"],
                params={
                    "userId": "me",
                    "id": msg_meta["id"],
                    "format": "metadata",
                    "metadataHeaders": ["From", "To", "Subject", "Date"],
                },
            )
            headers = _headers_dict(msg)
            output.append(
                {
                    "id": msg["id"],
                    "threadId": msg["threadId"],
                    "from": headers.get("From", ""),
                    "to": headers.get("To", ""),
                    "subject": headers.get("Subject", ""),
                    "date": headers.get("Date", ""),
                    "snippet": msg.get("snippet", ""),
                    "labels": msg.get("labelIds", []),
                }
            )
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return

    service = build_service("gmail", "v1")
    results = service.users().messages().list(
        userId="me", q=args.query, maxResults=args.max
    ).execute()
    messages = results.get("messages", [])
    if not messages:
        print("No messages found.")
        return

    output = []
    for msg_meta in messages:
        msg = service.users().messages().get(
            userId="me", id=msg_meta["id"], format="metadata",
            metadataHeaders=["From", "To", "Subject", "Date"],
        ).execute()
        headers = _headers_dict(msg)
        output.append({
            "id": msg["id"],
            "threadId": msg["threadId"],
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
            "snippet": msg.get("snippet", ""),
            "labels": msg.get("labelIds", []),
        })
    print(json.dumps(output, indent=2, ensure_ascii=False))



def gmail_get(args):
    if _gws_binary():
        msg = _run_gws(
            ["gmail", "users", "messages", "get"],
            params={"userId": "me", "id": args.message_id, "format": "full"},
        )
        headers = _headers_dict(msg)
        result = {
            "id": msg["id"],
            "threadId": msg["threadId"],
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
            "labels": msg.get("labelIds", []),
            "body": _extract_message_body(msg),
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    service = build_service("gmail", "v1")
    msg = service.users().messages().get(
        userId="me", id=args.message_id, format="full"
    ).execute()

    headers = _headers_dict(msg)
    result = {
        "id": msg["id"],
        "threadId": msg["threadId"],
        "from": headers.get("From", ""),
        "to": headers.get("To", ""),
        "subject": headers.get("Subject", ""),
        "date": headers.get("Date", ""),
        "labels": msg.get("labelIds", []),
        "body": _extract_message_body(msg),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))



def gmail_send(args):
    if _gws_binary():
        message = MIMEText(args.body, "html" if args.html else "plain")
        message["to"] = args.to
        message["subject"] = args.subject
        if args.cc:
            message["cc"] = args.cc
        if args.from_header:
            message["from"] = args.from_header

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        body = {"raw": raw}
        if args.thread_id:
            body["threadId"] = args.thread_id

        result = _run_gws(
            ["gmail", "users", "messages", "send"],
            params={"userId": "me"},
            body=body,
        )
        print(json.dumps({"status": "sent", "id": result["id"], "threadId": result.get("threadId", "")}, indent=2))
        return

    service = build_service("gmail", "v1")
    message = MIMEText(args.body, "html" if args.html else "plain")
    message["to"] = args.to
    message["subject"] = args.subject
    if args.cc:
        message["cc"] = args.cc
    if args.from_header:
        message["from"] = args.from_header

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    body = {"raw": raw}

    if args.thread_id:
        body["threadId"] = args.thread_id

    result = service.users().messages().send(userId="me", body=body).execute()
    print(json.dumps({"status": "sent", "id": result["id"], "threadId": result.get("threadId", "")}, indent=2))



def gmail_reply(args):
    if _gws_binary():
        original = _run_gws(
            ["gmail", "users", "messages", "get"],
            params={
                "userId": "me",
                "id": args.message_id,
                "format": "metadata",
                "metadataHeaders": ["From", "Subject", "Message-ID"],
            },
        )
        headers = _headers_dict(original)

        subject = headers.get("Subject", "")
        if not subject.startswith("Re:"):
            subject = f"Re: {subject}"

        message = MIMEText(args.body)
        message["to"] = headers.get("From", "")
        message["subject"] = subject
        if args.from_header:
            message["from"] = args.from_header
        if headers.get("Message-ID"):
            message["In-Reply-To"] = headers["Message-ID"]
            message["References"] = headers["Message-ID"]

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        result = _run_gws(
            ["gmail", "users", "messages", "send"],
            params={"userId": "me"},
            body={"raw": raw, "threadId": original["threadId"]},
        )
        print(json.dumps({"status": "sent", "id": result["id"], "threadId": result.get("threadId", "")}, indent=2))
        return

    service = build_service("gmail", "v1")
    original = service.users().messages().get(
        userId="me", id=args.message_id, format="metadata",
        metadataHeaders=["From", "Subject", "Message-ID"],
    ).execute()
    headers = _headers_dict(original)

    subject = headers.get("Subject", "")
    if not subject.startswith("Re:"):
        subject = f"Re: {subject}"

    message = MIMEText(args.body)
    message["to"] = headers.get("From", "")
    message["subject"] = subject
    if args.from_header:
        message["from"] = args.from_header
    if headers.get("Message-ID"):
        message["In-Reply-To"] = headers["Message-ID"]
        message["References"] = headers["Message-ID"]

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    body = {"raw": raw, "threadId": original["threadId"]}

    result = service.users().messages().send(userId="me", body=body).execute()
    print(json.dumps({"status": "sent", "id": result["id"], "threadId": result.get("threadId", "")}, indent=2))



def gmail_labels(args):
    if _gws_binary():
        results = _run_gws(["gmail", "users", "labels", "list"], params={"userId": "me"})
        labels = [{"id": l["id"], "name": l["name"], "type": l.get("type", "")} for l in results.get("labels", [])]
        print(json.dumps(labels, indent=2))
        return

    service = build_service("gmail", "v1")
    results = service.users().labels().list(userId="me").execute()
    labels = [{"id": l["id"], "name": l["name"], "type": l.get("type", "")} for l in results.get("labels", [])]
    print(json.dumps(labels, indent=2))



def gmail_modify(args):
    body = {}
    if args.add_labels:
        body["addLabelIds"] = args.add_labels.split(",")
    if args.remove_labels:
        body["removeLabelIds"] = args.remove_labels.split(",")

    if _gws_binary():
        result = _run_gws(
            ["gmail", "users", "messages", "modify"],
            params={"userId": "me", "id": args.message_id},
            body=body,
        )
        print(json.dumps({"id": result["id"], "labels": result.get("labelIds", [])}, indent=2))
        return

    service = build_service("gmail", "v1")
    result = service.users().messages().modify(userId="me", id=args.message_id, body=body).execute()
    print(json.dumps({"id": result["id"], "labels": result.get("labelIds", [])}, indent=2))


# =========================================================================
# Calendar
# =========================================================================


def calendar_list(args):
    now = datetime.now(timezone.utc)
    time_min = _datetime_with_timezone(args.start or now.isoformat())
    time_max = _datetime_with_timezone(args.end or (now + timedelta(days=7)).isoformat())

    # Calendar IDs: "all" → enumerate user calendarList, else single id.
    cal_arg = (args.calendar or "primary").strip()
    if cal_arg.lower() == "all":
        cal_ids = []
        if _gws_binary():
            cal_list = _run_gws(["calendar", "calendarList", "list"], params={})
            # Filter: solo calendari di cui l'utente è OWNER (no writer
            # condivisi, no reader, no freeBusyReader).
            for c in cal_list.get("items", []):
                cid = c.get("id")
                if (cid and not c.get("deleted") and not c.get("hidden")
                    and c.get("accessRole") == "owner"):
                    cal_ids.append(cid)
        if not cal_ids:
            cal_ids = ["primary"]
    else:
        cal_ids = [cal_arg]

    if _gws_binary():
        events = []

        def _list_gws_calendar(cid):
            return cid, _run_gws(
                ["calendar", "events", "list"],
                params={
                    "calendarId": cid,
                    "timeMin": time_min,
                    "timeMax": time_max,
                    "maxResults": args.max,
                    "singleEvents": True,
                    "orderBy": "startTime",
                },
            )

        listed, _skipped = map_ordered(_list_gws_calendar, cal_ids)
        for _index, (cid, results) in listed:
            for e in results.get("items", []):
                events.append({
                    "id": e["id"],
                    "summary": e.get("summary", "(no title)"),
                    "start": e.get("start", {}).get("dateTime", e.get("start", {}).get("date", "")),
                    "end": e.get("end", {}).get("dateTime", e.get("end", {}).get("date", "")),
                    "location": e.get("location", ""),
                    "description": e.get("description", ""),
                    "status": e.get("status", ""),
                    "htmlLink": e.get("htmlLink", ""),
                    "_calendar_id": cid,
                })
        # Sort cross-calendar by start time
        events.sort(key=lambda x: x.get("start") or "")
        print(json.dumps(events, indent=2, ensure_ascii=False))
        return

    # Fallback senza gws binary: usa googleapiclient direct. Expand "all" qui.
    service = build_service("calendar", "v3")
    if cal_arg.lower() == "all":
        cal_list_res = service.calendarList().list().execute()
        # Filter: solo OWNER (no writer condivisi, no reader).
        direct_cal_ids = [
            c["id"] for c in cal_list_res.get("items", [])
            if (c.get("id") and not c.get("deleted") and not c.get("hidden")
                and c.get("accessRole") == "owner")
        ] or ["primary"]
    else:
        direct_cal_ids = [args.calendar]
    events = []

    def _list_direct_calendar(cid):
        # googleapiclient service/httplib2 non e' thread-safe: ogni worker
        # parallelo possiede il proprio service. Il ramo seriale riusa quello
        # già costruito per preservare costo e comportamento storici.
        worker_service = (
            service if assigned_workers(item_count=len(direct_cal_ids)) == 1
            else build_service("calendar", "v3")
        )
        result = worker_service.events().list(
            calendarId=cid, timeMin=time_min, timeMax=time_max,
            maxResults=args.max, singleEvents=True, orderBy="startTime",
        ).execute()
        return cid, result

    listed, _skipped = map_ordered(_list_direct_calendar, direct_cal_ids)
    for _index, (cid, results) in listed:
        for e in results.get("items", []):
            events.append({
                "id": e["id"],
                "summary": e.get("summary", "(no title)"),
                "start": e.get("start", {}).get("dateTime", e.get("start", {}).get("date", "")),
                "end": e.get("end", {}).get("dateTime", e.get("end", {}).get("date", "")),
                "location": e.get("location", ""),
                "description": e.get("description", ""),
                "status": e.get("status", ""),
                "htmlLink": e.get("htmlLink", ""),
                "_calendar_id": cid,
            })
    events.sort(key=lambda x: x.get("start") or "")
    print(json.dumps(events, indent=2, ensure_ascii=False))



def calendar_create(args):
    # All-day events: start/end are date strings (YYYY-MM-DD) without 'T'.
    # Google Calendar API requires `{"date": ...}` for all-day, `{"dateTime": ...}`
    # for timed events. Detection via 'T' presence in the string.
    def _time_field(s):
        if isinstance(s, str) and "T" not in s and len(s) == 10:
            return {"date": s}
        return {"dateTime": s}
    event = {
        "summary": args.summary,
        "start": _time_field(args.start),
        "end": _time_field(args.end),
    }
    if args.location:
        event["location"] = args.location
    if args.description:
        event["description"] = args.description
    if args.attendees:
        event["attendees"] = [{"email": e.strip()} for e in args.attendees.split(",") if e.strip()]

    if _gws_binary():
        result = _run_gws(
            ["calendar", "events", "insert"],
            params={"calendarId": args.calendar},
            body=event,
        )
        print(json.dumps({
            "status": "created",
            "id": result["id"],
            "summary": result.get("summary", ""),
            "htmlLink": result.get("htmlLink", ""),
        }, indent=2))
        return

    service = build_service("calendar", "v3")
    result = service.events().insert(calendarId=args.calendar, body=event).execute()
    print(json.dumps({
        "status": "created",
        "id": result["id"],
        "summary": result.get("summary", ""),
        "htmlLink": result.get("htmlLink", ""),
    }, indent=2))



def calendar_delete(args):
    if _gws_binary():
        _run_gws(["calendar", "events", "delete"], params={"calendarId": args.calendar, "eventId": args.event_id})
        print(json.dumps({"status": "deleted", "eventId": args.event_id}))
        return

    service = build_service("calendar", "v3")
    service.events().delete(calendarId=args.calendar, eventId=args.event_id).execute()
    print(json.dumps({"status": "deleted", "eventId": args.event_id}))


def calendar_new(args):
    """Crea un CALENDARIO-contenitore (non un evento): calendars().insert."""
    service = build_service("calendar", "v3")
    body = {"summary": args.summary}
    if getattr(args, "description", ""):
        body["description"] = args.description
    if getattr(args, "timezone", ""):
        body["timeZone"] = args.timezone
    created = service.calendars().insert(body=body).execute()
    print(json.dumps({"status": "created", "calendarId": created.get("id"),
                      "summary": created.get("summary")}))


def calendar_list_cals(args):
    """Elenca i CALENDARI dell'utente (container): calendarList.list."""
    service = build_service("calendar", "v3")
    res = service.calendarList().list().execute()
    cals = [{"id": c.get("id"), "summary": c.get("summary"),
             "primary": bool(c.get("primary", False)),
             "accessRole": c.get("accessRole")}
            for c in res.get("items", [])]
    print(json.dumps({"calendars": cals, "count": len(cals)}))


def calendar_delete_cal(args):
    """Cancella un CALENDARIO-contenitore: calendars().delete."""
    service = build_service("calendar", "v3")
    service.calendars().delete(calendarId=args.calendar_id).execute()
    print(json.dumps({"status": "deleted", "calendarId": args.calendar_id}))


def calendar_update(args):
    """Patch update di un event esistente. Solo i field passati vengono
    modificati (PATCH semantics, NOT PUT). Field opzionali: summary,
    start, end, location, description, attendees."""
    def _time_field(s):
        if isinstance(s, str) and "T" not in s and len(s) == 10:
            return {"date": s}
        return {"dateTime": s}
    patch = {}
    if args.summary:
        patch["summary"] = args.summary
    if args.start:
        patch["start"] = _time_field(args.start)
    if args.end:
        patch["end"] = _time_field(args.end)
    if args.location is not None:
        patch["location"] = args.location
    if args.description is not None:
        patch["description"] = args.description
    if args.attendees:
        patch["attendees"] = [{"email": e.strip()}
                               for e in args.attendees.split(",") if e.strip()]
    if not patch:
        print(json.dumps({"status": "noop", "reason": "no fields to update"}))
        return

    if _gws_binary():
        result = _run_gws(
            ["calendar", "events", "patch"],
            params={"calendarId": args.calendar, "eventId": args.event_id},
            body=patch,
        )
        print(json.dumps({
            "status": "updated",
            "id": result["id"],
            "summary": result.get("summary", ""),
            "htmlLink": result.get("htmlLink", ""),
            "updated_fields": sorted(patch.keys()),
        }, indent=2))
        return

    service = build_service("calendar", "v3")
    result = service.events().patch(
        calendarId=args.calendar, eventId=args.event_id, body=patch,
    ).execute()
    print(json.dumps({
        "status": "updated",
        "id": result["id"],
        "summary": result.get("summary", ""),
        "htmlLink": result.get("htmlLink", ""),
        "updated_fields": sorted(patch.keys()),
    }, indent=2))


# =========================================================================
# Drive
# =========================================================================


def drive_search(args):
    # Escludi SEMPRE i file nel cestino dalla ricerca non-raw: un file trashed
    # non deve piu' comparire (evita omonimi-fantasma dopo delete → falsa
    # ambiguita'). Il path raw resta sotto controllo del chiamante.
    query = (args.query if args.raw_query
             else f"fullText contains '{args.query}' and trashed = false")
    if _gws_binary():
        results = _run_gws(
            ["drive", "files", "list"],
            params={
                "q": query,
                "pageSize": args.max,
                "fields": "files(id, name, mimeType, modifiedTime, webViewLink)",
            },
        )
        print(json.dumps(results.get("files", []), indent=2, ensure_ascii=False))
        return

    service = build_service("drive", "v3")
    results = service.files().list(
        q=query, pageSize=args.max, fields="files(id, name, mimeType, modifiedTime, webViewLink)",
    ).execute()
    files = results.get("files", [])
    print(json.dumps(files, indent=2, ensure_ascii=False))


def drive_get(args):
    """Get metadata for a single Drive file by ID."""
    fields = "id, name, mimeType, modifiedTime, size, webViewLink, parents, owners(emailAddress)"
    if _gws_binary():
        result = _run_gws(
            ["drive", "files", "get"],
            params={"fileId": args.file_id, "fields": fields},
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    service = build_service("drive", "v3")
    result = service.files().get(fileId=args.file_id, fields=fields).execute()
    print(json.dumps(result, indent=2, ensure_ascii=False))


def drive_upload(args):
    """Upload a local file to Drive. Falls through to Python client even when gws
    is installed, because gws doesn't do multipart uploads."""
    import mimetypes
    from googleapiclient.http import MediaFileUpload

    local_path = Path(args.path).expanduser()
    if not local_path.exists():
        print(f"ERROR: file not found: {local_path}", file=sys.stderr)
        sys.exit(1)

    mime = args.mime_type or mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"
    metadata = {"name": args.name or local_path.name}
    if args.parent:
        metadata["parents"] = [args.parent]

    service = build_service("drive", "v3")
    media = MediaFileUpload(str(local_path), mimetype=mime, resumable=True)
    result = service.files().create(
        body=metadata,
        media_body=media,
        fields="id, name, mimeType, webViewLink",
    ).execute()
    print(json.dumps({
        "status": "uploaded",
        "id": result["id"],
        "name": result.get("name", ""),
        "mimeType": result.get("mimeType", ""),
        "webViewLink": result.get("webViewLink", ""),
    }, indent=2, ensure_ascii=False))


def drive_download(args):
    """Download a Drive file to a local path. Google-native files (Docs/Sheets/Slides)
    must be exported; binary files are downloaded as-is."""
    import io
    from googleapiclient.http import MediaIoBaseDownload

    service = build_service("drive", "v3")

    # Look up the file to decide download vs export.
    meta = service.files().get(fileId=args.file_id, fields="id, name, mimeType").execute()
    mime = meta.get("mimeType", "")
    name = meta.get("name", args.file_id)

    # Map Google-native MIME types to a sensible export default.
    native_export_map = {
        "application/vnd.google-apps.document": ("application/pdf", ".pdf"),
        "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv"),
        "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
        "application/vnd.google-apps.drawing": ("image/png", ".png"),
    }

    out_path = Path(args.output).expanduser() if args.output else Path.cwd() / name

    if mime in native_export_map:
        export_mime = args.export_mime or native_export_map[mime][0]
        default_ext = native_export_map[mime][1]
        if not args.output and not out_path.suffix:
            out_path = out_path.with_suffix(default_ext)
        request = service.files().export_media(fileId=args.file_id, mimeType=export_mime)
    else:
        request = service.files().get_media(fileId=args.file_id)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fh = io.FileIO(str(out_path), "wb")
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    fh.close()

    print(json.dumps({
        "status": "downloaded",
        "id": args.file_id,
        "name": name,
        "path": str(out_path),
        "mimeType": mime,
    }, indent=2, ensure_ascii=False))


def drive_read(args):
    """Legge il CONTENUTO di un file Drive INLINE (per il backend files.read()).
    Google-native → export testo (Doc→text/plain, Sheet→csv, Slides→text/plain);
    binari → get_media. Output JSON con `content` (testo) o null se binario."""
    import io
    from googleapiclient.http import MediaIoBaseDownload

    service = build_service("drive", "v3")
    meta = service.files().get(fileId=args.file_id, fields="id, name, mimeType").execute()
    mime = meta.get("mimeType", "")
    name = meta.get("name", args.file_id)
    native_text_map = {
        "application/vnd.google-apps.document": "text/plain",
        "application/vnd.google-apps.spreadsheet": "text/csv",
        "application/vnd.google-apps.presentation": "text/plain",
    }
    if mime in native_text_map:
        export_mime = args.export_mime or native_text_map[mime]
        request = service.files().export_media(fileId=args.file_id, mimeType=export_mime)
    else:
        request = service.files().get_media(fileId=args.file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    raw = fh.getvalue()
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        content = None  # binario: nessun testo inline
    print(json.dumps({
        "id": args.file_id, "name": name, "mimeType": mime,
        "content": content, "bytes": len(raw),
    }, ensure_ascii=False))


def drive_create_folder(args):
    body = {
        "name": args.name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if args.parent:
        body["parents"] = [args.parent]

    if _gws_binary():
        result = _run_gws(
            ["drive", "files", "create"],
            params={"fields": "id, name, webViewLink"},
            body=body,
        )
        print(json.dumps({
            "status": "created",
            "id": result["id"],
            "name": result.get("name", ""),
            "webViewLink": result.get("webViewLink", ""),
        }, indent=2, ensure_ascii=False))
        return

    service = build_service("drive", "v3")
    result = service.files().create(body=body, fields="id, name, webViewLink").execute()
    print(json.dumps({
        "status": "created",
        "id": result["id"],
        "name": result.get("name", ""),
        "webViewLink": result.get("webViewLink", ""),
    }, indent=2, ensure_ascii=False))


def drive_share(args):
    permission = {
        "type": args.type,
        "role": args.role,
    }
    if args.type in ("user", "group"):
        if not args.email:
            print("ERROR: --email is required for type=user or type=group", file=sys.stderr)
            sys.exit(1)
        permission["emailAddress"] = args.email
    elif args.type == "domain":
        if not args.domain:
            print("ERROR: --domain is required for type=domain", file=sys.stderr)
            sys.exit(1)
        permission["domain"] = args.domain

    if _gws_binary():
        result = _run_gws(
            ["drive", "permissions", "create"],
            params={
                "fileId": args.file_id,
                "sendNotificationEmail": args.notify,
            },
            body=permission,
        )
        print(json.dumps({
            "status": "shared",
            "permissionId": result.get("id", ""),
            "fileId": args.file_id,
            "role": permission["role"],
            "type": permission["type"],
        }, indent=2, ensure_ascii=False))
        return

    service = build_service("drive", "v3")
    result = service.permissions().create(
        fileId=args.file_id,
        body=permission,
        sendNotificationEmail=args.notify,
        fields="id",
    ).execute()
    print(json.dumps({
        "status": "shared",
        "permissionId": result.get("id", ""),
        "fileId": args.file_id,
        "role": permission["role"],
        "type": permission["type"],
    }, indent=2, ensure_ascii=False))


def drive_delete(args):
    """Trash or permanently delete a Drive file. Defaults to trash (reversible)."""
    if args.permanent:
        if _gws_binary():
            _run_gws(["drive", "files", "delete"], params={"fileId": args.file_id})
            print(json.dumps({"status": "deleted", "fileId": args.file_id, "permanent": True}))
            return
        service = build_service("drive", "v3")
        service.files().delete(fileId=args.file_id).execute()
        print(json.dumps({"status": "deleted", "fileId": args.file_id, "permanent": True}))
        return

    # Trash (reversible). Use files.update with trashed=True.
    body = {"trashed": True}
    if _gws_binary():
        _run_gws(
            ["drive", "files", "update"],
            params={"fileId": args.file_id},
            body=body,
        )
        print(json.dumps({"status": "trashed", "fileId": args.file_id, "permanent": False}))
        return

    service = build_service("drive", "v3")
    service.files().update(fileId=args.file_id, body=body).execute()
    print(json.dumps({"status": "trashed", "fileId": args.file_id, "permanent": False}))


# =========================================================================
# Contacts
# =========================================================================


def contacts_list(args):
    if _gws_binary():
        results = _run_gws(
            ["people", "people", "connections", "list"],
            params={
                "resourceName": "people/me",
                "pageSize": args.max,
                "personFields": "names,emailAddresses,phoneNumbers",
            },
        )
        contacts = []
        for person in results.get("connections", []):
            names = person.get("names", [{}])
            emails = person.get("emailAddresses", [])
            phones = person.get("phoneNumbers", [])
            contacts.append({
                "name": names[0].get("displayName", "") if names else "",
                "emails": [e.get("value", "") for e in emails],
                "phones": [p.get("value", "") for p in phones],
            })
        print(json.dumps(contacts, indent=2, ensure_ascii=False))
        return

    service = build_service("people", "v1")
    results = service.people().connections().list(
        resourceName="people/me",
        pageSize=args.max,
        personFields="names,emailAddresses,phoneNumbers",
    ).execute()
    contacts = []
    for person in results.get("connections", []):
        names = person.get("names", [{}])
        emails = person.get("emailAddresses", [])
        phones = person.get("phoneNumbers", [])
        contacts.append({
            "name": names[0].get("displayName", "") if names else "",
            "emails": [e.get("value", "") for e in emails],
            "phones": [p.get("value", "") for p in phones],
        })
    print(json.dumps(contacts, indent=2, ensure_ascii=False))


# =========================================================================
# Sheets
# =========================================================================


def sheets_get(args):
    if _gws_binary():
        result = _run_gws(
            ["sheets", "spreadsheets", "values", "get"],
            params={"spreadsheetId": args.sheet_id, "range": args.range},
        )
        print(json.dumps(result.get("values", []), indent=2, ensure_ascii=False))
        return

    service = build_service("sheets", "v4")
    result = service.spreadsheets().values().get(
        spreadsheetId=args.sheet_id, range=args.range,
    ).execute()
    print(json.dumps(result.get("values", []), indent=2, ensure_ascii=False))



def sheets_update(args):
    values = json.loads(args.values)
    body = {"values": values}

    if _gws_binary():
        result = _run_gws(
            ["sheets", "spreadsheets", "values", "update"],
            params={
                "spreadsheetId": args.sheet_id,
                "range": args.range,
                "valueInputOption": "USER_ENTERED",
            },
            body=body,
        )
        print(json.dumps({"updatedCells": result.get("updatedCells", 0), "updatedRange": result.get("updatedRange", "")}, indent=2))
        return

    service = build_service("sheets", "v4")
    result = service.spreadsheets().values().update(
        spreadsheetId=args.sheet_id, range=args.range,
        valueInputOption="USER_ENTERED", body=body,
    ).execute()
    print(json.dumps({"updatedCells": result.get("updatedCells", 0), "updatedRange": result.get("updatedRange", "")}, indent=2))



def sheets_append(args):
    values = json.loads(args.values)
    body = {"values": values}

    if _gws_binary():
        result = _run_gws(
            ["sheets", "spreadsheets", "values", "append"],
            params={
                "spreadsheetId": args.sheet_id,
                "range": args.range,
                "valueInputOption": "USER_ENTERED",
                "insertDataOption": "INSERT_ROWS",
            },
            body=body,
        )
        print(json.dumps({"updatedCells": result.get("updates", {}).get("updatedCells", 0)}, indent=2))
        return

    service = build_service("sheets", "v4")
    result = service.spreadsheets().values().append(
        spreadsheetId=args.sheet_id, range=args.range,
        valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS", body=body,
    ).execute()
    print(json.dumps({"updatedCells": result.get("updates", {}).get("updatedCells", 0)}, indent=2))


def sheets_create(args):
    """Create a new spreadsheet. Returns the new spreadsheet ID and URL."""
    body = {"properties": {"title": args.title}}
    if args.sheet_name:
        body["sheets"] = [{"properties": {"title": args.sheet_name}}]

    if _gws_binary():
        result = _run_gws(["sheets", "spreadsheets", "create"], body=body)
        print(json.dumps({
            "status": "created",
            "spreadsheetId": result.get("spreadsheetId", ""),
            "title": result.get("properties", {}).get("title", ""),
            "spreadsheetUrl": result.get("spreadsheetUrl", ""),
        }, indent=2, ensure_ascii=False))
        return

    service = build_service("sheets", "v4")
    result = service.spreadsheets().create(
        body=body, fields="spreadsheetId,properties,spreadsheetUrl",
    ).execute()
    print(json.dumps({
        "status": "created",
        "spreadsheetId": result.get("spreadsheetId", ""),
        "title": result.get("properties", {}).get("title", ""),
        "spreadsheetUrl": result.get("spreadsheetUrl", ""),
    }, indent=2, ensure_ascii=False))


# =========================================================================
# Docs
# =========================================================================


def docs_get(args):
    if _gws_binary():
        doc = _run_gws(["docs", "documents", "get"], params={"documentId": args.doc_id})
        result = {
            "title": doc.get("title", ""),
            "documentId": doc.get("documentId", ""),
            "body": _extract_doc_text(doc),
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    service = build_service("docs", "v1")
    doc = service.documents().get(documentId=args.doc_id).execute()
    result = {
        "title": doc.get("title", ""),
        "documentId": doc.get("documentId", ""),
        "body": _extract_doc_text(doc),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))


def docs_create(args):
    """Create a new Doc. Optionally seed it with initial body text."""
    body = {"title": args.title}

    if _gws_binary():
        doc = _run_gws(["docs", "documents", "create"], body=body)
    else:
        service = build_service("docs", "v1")
        doc = service.documents().create(body=body).execute()

    doc_id = doc.get("documentId", "")

    if args.body and doc_id:
        _docs_insert_text(doc_id, args.body, index=1)

    print(json.dumps({
        "status": "created",
        "documentId": doc_id,
        "title": doc.get("title", ""),
        "url": f"https://docs.google.com/document/d/{doc_id}/edit" if doc_id else "",
    }, indent=2, ensure_ascii=False))


def vision_web_detect(args):
    """Google Cloud Vision Web Detection: reverse image search.

    Input: --image-path (file locale) OR --image-url (URL pubblico).
    Output JSON: {
      "best_guess_labels": [str],
      "full_matching_images": [{url}],     # exact match
      "partial_matching_images": [{url}],  # crop/edit
      "visually_similar_images": [{url}],
      "pages_with_matching_images": [{url, page_title, full_matching_images}],
      "web_entities": [{description, score}],
    }
    Cap max-results applicato a ciascuna lista.
    """
    import base64
    # Costruisci il request body Cloud Vision images:annotate
    if args.image_path:
        with open(args.image_path, "rb") as fh:
            content = base64.b64encode(fh.read()).decode("ascii")
        image_field = {"content": content}
    elif args.image_url:
        image_field = {"source": {"imageUri": args.image_url}}
    else:
        print(json.dumps({"error": "must pass --image-path or --image-url"}))
        sys.exit(2)

    body = {
        "requests": [{
            "image": image_field,
            "features": [{"type": "WEB_DETECTION",
                          "maxResults": args.max_results}],
        }],
    }

    if _gws_binary():
        result = _run_gws(["vision", "images", "annotate"], body=body)
    else:
        service = build_service("vision", "v1")
        result = service.images().annotate(body=body).execute()

    response = (result.get("responses") or [{}])[0]
    web = response.get("webDetection") or {}
    cap = args.max_results

    def _trim(lst):
        return (lst or [])[:cap]

    out = {
        "best_guess_labels": [
            b.get("label", "") for b in _trim(web.get("bestGuessLabels"))
        ],
        "full_matching_images": [
            {"url": m.get("url", "")} for m in _trim(web.get("fullMatchingImages"))
        ],
        "partial_matching_images": [
            {"url": m.get("url", "")} for m in _trim(web.get("partialMatchingImages"))
        ],
        "visually_similar_images": [
            {"url": m.get("url", "")} for m in _trim(web.get("visuallySimilarImages"))
        ],
        "pages_with_matching_images": [
            {
                "url": p.get("url", ""),
                "page_title": p.get("pageTitle", ""),
                "full_matching_images": [
                    m.get("url", "") for m in _trim(p.get("fullMatchingImages"))
                ],
                "partial_matching_images": [
                    m.get("url", "") for m in _trim(p.get("partialMatchingImages"))
                ],
            }
            for p in _trim(web.get("pagesWithMatchingImages"))
        ],
        "web_entities": [
            {"description": e.get("description", ""),
             "score": e.get("score", 0.0)}
            for e in _trim(web.get("webEntities"))
        ],
    }
    print(json.dumps(out, ensure_ascii=False))


def docs_append(args):
    """Append text to the end of an existing Doc."""
    if _gws_binary():
        doc = _run_gws(["docs", "documents", "get"], params={"documentId": args.doc_id})
    else:
        service = build_service("docs", "v1")
        doc = service.documents().get(documentId=args.doc_id).execute()

    # The end-of-body index is one less than the segment endIndex of the body
    # (trailing newline is always at length-1). Docs indexes are 1-based; use
    # endIndex - 1 to insert before the final newline.
    content = doc.get("body", {}).get("content", [])
    end_index = 1
    for element in content:
        ei = element.get("endIndex")
        if isinstance(ei, int) and ei > end_index:
            end_index = ei
    insert_index = max(end_index - 1, 1)

    text = args.text if args.text.endswith("\n") else args.text + "\n"
    _docs_insert_text(args.doc_id, text, index=insert_index)

    print(json.dumps({
        "status": "appended",
        "documentId": args.doc_id,
        "inserted_at": insert_index,
        "characters": len(text),
    }, indent=2, ensure_ascii=False))


def docs_delete_range(args):
    """Delete a content range [start, end) from a Doc (undo of append)."""
    start = int(args.start)
    end = int(args.end)
    requests = [{
        "deleteContentRange": {
            "range": {
                "segmentId": "",
                "startIndex": start,
                "endIndex": end,
            }
        }
    }]
    if _gws_binary():
        _run_gws(
            ["docs", "documents", "batchUpdate"],
            params={"documentId": args.doc_id},
            body={"requests": requests},
        )
    else:
        service = build_service("docs", "v1")
        service.documents().batchUpdate(
            documentId=args.doc_id, body={"requests": requests}).execute()
    print(json.dumps({
        "status": "range_deleted",
        "documentId": args.doc_id,
        "removed_range": [start, end],
    }, indent=2, ensure_ascii=False))


def _docs_insert_text(doc_id: str, text: str, index: int) -> None:
    """Send a batchUpdate with a single insertText request."""
    requests = [{
        "insertText": {
            "location": {"index": index},
            "text": text,
        }
    }]
    if _gws_binary():
        _run_gws(
            ["docs", "documents", "batchUpdate"],
            params={"documentId": doc_id},
            body={"requests": requests},
        )
        return

    service = build_service("docs", "v1")
    service.documents().batchUpdate(documentId=doc_id, body={"requests": requests}).execute()


# =========================================================================
# Photos  (Google Photos Library API — SOLO dati creati dall'app, post 31/3/2025)
# =========================================================================
# La Photos Library API NON e' nel discovery di googleapiclient e l'upload e' un
# POST di bytes grezzi: usiamo AuthorizedSession (auth header + refresh auto)
# sugli endpoint REST v1. Scope: photoslibrary.appendonly (upload) +
# photoslibrary.readonly.appcreateddata (lettura del solo creato-da-Metnos).
# Delete di mediaItems: IMPOSSIBILE via API (l'upload NON e' reversibile).

_PHOTOS_API_BASE = "https://photoslibrary.googleapis.com/v1"


def _photos_session():
    from google.auth.transport.requests import AuthorizedSession
    return AuthorizedSession(get_credentials())


def _photos_fail(action: str, resp) -> None:
    """Errore onesto su STDERR (lo classifica `_google_api_runner`): includo lo
    status HTTP e il messaggio API, cosi' un 403 'insufficient scopes' →
    auth_required → re-consent (§3.1), non un fallimento opaco."""
    try:
        detail = resp.json().get("error", {}).get("message", resp.text)
    except Exception:
        detail = resp.text
    print(f"ERROR {action}: HTTP {resp.status_code} {detail}", file=sys.stderr)
    sys.exit(1)


def photos_upload(args):
    """Upload di UN file locale: POST bytes → uploadToken → mediaItems:batchCreate.
    Opzionale `--album-id` per aggiungere all'album. baseUrl mai persistito."""
    import mimetypes
    local_path = Path(args.path).expanduser()
    if not local_path.exists():
        print(f"ERROR: file not found: {local_path}", file=sys.stderr)
        sys.exit(1)
    mime = args.mime_type or mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"
    session = _photos_session()
    with open(local_path, "rb") as fh:
        raw = fh.read()
    up = session.post(
        f"{_PHOTOS_API_BASE}/uploads",
        data=raw,
        headers={
            "Content-Type": "application/octet-stream",
            "X-Goog-Upload-Content-Type": mime,
            "X-Goog-Upload-Protocol": "raw",
        },
    )
    if up.status_code >= 400:
        _photos_fail("upload-bytes", up)
    upload_token = up.text.strip()
    if not upload_token:
        print("ERROR upload-bytes: empty upload token", file=sys.stderr)
        sys.exit(1)
    body = {"newMediaItems": [{
        "simpleMediaItem": {"uploadToken": upload_token,
                            "fileName": local_path.name},
    }]}
    if args.album_id:
        body["albumId"] = args.album_id
    bc = session.post(f"{_PHOTOS_API_BASE}/mediaItems:batchCreate", json=body)
    if bc.status_code >= 400:
        _photos_fail("batchCreate", bc)
    results = bc.json().get("newMediaItemResults", [])
    r0 = results[0] if results else {}
    media_item = r0.get("mediaItem", {})
    status_msg = (r0.get("status") or {}).get("message", "")
    mid = media_item.get("id", "")
    out = {
        "status": "uploaded" if mid else "failed",
        "media_item_id": mid,
        "filename": media_item.get("filename", local_path.name),
        "album_id": args.album_id or "",
        "status_message": status_msg,
    }
    print(json.dumps(out, ensure_ascii=False))
    if not mid:
        # Fallimento per-item (batchCreate 200 ma senza mediaItem): §2.8 onesto.
        print(f"ERROR batchCreate item: {status_msg or 'no mediaItem returned'}",
              file=sys.stderr)
        sys.exit(1)


def photos_upload_bytes(args):
    """SOLA fase bytes: POST /uploads → uploadToken. NON crea il mediaItem —
    il batchCreate lo fa il BACKEND a chunk di 50 (spec §3.2/§3.3) via
    `photos batch-create`. Il token scade in 24h: usarlo subito."""
    import mimetypes
    local_path = Path(args.path).expanduser()
    if not local_path.exists():
        print(f"ERROR: file not found: {local_path}", file=sys.stderr)
        sys.exit(1)
    mime = args.mime_type or mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"
    session = _photos_session()
    with open(local_path, "rb") as fh:
        raw = fh.read()
    up = session.post(
        f"{_PHOTOS_API_BASE}/uploads",
        data=raw,
        headers={
            "Content-Type": "application/octet-stream",
            "X-Goog-Upload-Content-Type": mime,
            "X-Goog-Upload-Protocol": "raw",
        },
    )
    if up.status_code >= 400:
        _photos_fail("upload-bytes", up)
    token = up.text.strip()
    if not token:
        print("ERROR upload-bytes: empty upload token", file=sys.stderr)
        sys.exit(1)
    print(json.dumps({"uploadToken": token, "fileName": local_path.name},
                     ensure_ascii=False))


def photos_batch_create(args):
    """mediaItems:batchCreate su una LISTA di uploadToken (max 50 = contratto
    API). `--items` = JSON `[{uploadToken, fileName}, ...]`. Output: results
    allineati per indice, ok per-item onesto (§2.8)."""
    try:
        items = json.loads(args.items)
    except json.JSONDecodeError as ex:
        print(f"ERROR batch-create: --items invalid JSON: {ex}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(items, list) or not items:
        print("ERROR batch-create: --items must be a non-empty JSON list",
              file=sys.stderr)
        sys.exit(1)
    if len(items) > 50:
        print("ERROR batch-create: max 50 items per call (API contract)",
              file=sys.stderr)
        sys.exit(1)
    body = {"newMediaItems": [
        {"simpleMediaItem": {"uploadToken": str(it.get("uploadToken", "")),
                             "fileName": str(it.get("fileName", ""))}}
        for it in items
    ]}
    if args.album_id:
        body["albumId"] = args.album_id
    session = _photos_session()
    bc = session.post(f"{_PHOTOS_API_BASE}/mediaItems:batchCreate", json=body)
    if bc.status_code >= 400:
        _photos_fail("batchCreate", bc)
    api_results = bc.json().get("newMediaItemResults", [])
    out = []
    for i, it in enumerate(items):
        r = api_results[i] if i < len(api_results) else {}
        media = r.get("mediaItem", {}) or {}
        out.append({
            "ok": bool(media.get("id")),
            "media_item_id": media.get("id", ""),
            "filename": media.get("filename") or str(it.get("fileName", "")),
            "status_message": (r.get("status") or {}).get("message", ""),
        })
    print(json.dumps({"results": out}, ensure_ascii=False))


def photos_album_create(args):
    """Crea un album app-created. POST /albums → {id, title, productUrl}."""
    session = _photos_session()
    resp = session.post(f"{_PHOTOS_API_BASE}/albums",
                        json={"album": {"title": args.title}})
    if resp.status_code >= 400:
        _photos_fail("album-create", resp)
    alb = resp.json()
    print(json.dumps({
        "id": alb.get("id", ""),
        "title": alb.get("title", args.title),
        "productUrl": alb.get("productUrl", ""),
    }, ensure_ascii=False))


def photos_album_list(args):
    """Elenca gli album app-created (paginato). GET /albums?pageSize=50.
    LIMITE API: solo album creati dall'app — NON la lista completa dell'utente
    (post 31/3/2025). La lista integrale arriva da Takeout (spec §4.3-bis)."""
    session = _photos_session()
    albums = []
    page_token = ""
    while True:
        # Bool proto3 in query string: "true" MINUSCOLO — requests serializza
        # il bool Python come "True" e l'API risponde 400 INVALID_ARGUMENT.
        params = {"pageSize": 50, "excludeNonAppCreatedData": "true"}
        if page_token:
            params["pageToken"] = page_token
        resp = session.get(f"{_PHOTOS_API_BASE}/albums", params=params)
        if resp.status_code >= 400:
            _photos_fail("album-list", resp)
        data = resp.json()
        for a in data.get("albums", []):
            albums.append({
                "id": a.get("id", ""),
                "title": a.get("title", ""),
                "items_count": int(a.get("mediaItemsCount", 0) or 0),
                "url": a.get("productUrl", ""),
            })
        page_token = data.get("nextPageToken", "")
        if not page_token:
            break
    print(json.dumps(albums, ensure_ascii=False))


def photos_search(args):
    """Cerca fra i mediaItems app-created (UNA pagina per chiamata; il backend
    itera con --page-token). `--album-id` e `--year` sono MUTUAMENTE ESCLUSIVI
    per contratto API (albumId non ammette filters): l'album ha precedenza.
    SENZA filtri usa `mediaItems.list` (GET): contratto esplicito «tutti gli
    item app-created», niente dipendenza dal comportamento di search-vuota."""
    session = _photos_session()
    if args.album_id or args.year:
        body = {"pageSize": int(args.max)}
        if args.page_token:
            body["pageToken"] = args.page_token
        if args.album_id:
            body["albumId"] = args.album_id
        else:
            y = int(args.year)
            body["filters"] = {"dateFilter": {"ranges": [{
                "startDate": {"year": y, "month": 1, "day": 1},
                "endDate": {"year": y, "month": 12, "day": 31},
            }]}}
        resp = session.post(f"{_PHOTOS_API_BASE}/mediaItems:search", json=body)
    else:
        params = {"pageSize": int(args.max)}
        if args.page_token:
            params["pageToken"] = args.page_token
        resp = session.get(f"{_PHOTOS_API_BASE}/mediaItems", params=params)
    if resp.status_code >= 400:
        _photos_fail("search", resp)
    data = resp.json()
    items = []
    for m in data.get("mediaItems", []):
        meta = m.get("mediaMetadata", {})
        items.append({
            "id": m.get("id", ""),
            "filename": m.get("filename", ""),
            "mime": m.get("mimeType", ""),
            "created_at": meta.get("creationTime", ""),
            "width": int(meta.get("width", 0) or 0),
            "height": int(meta.get("height", 0) or 0),
        })
    print(json.dumps({"items": items,
                      "nextPageToken": data.get("nextPageToken", "")},
                     ensure_ascii=False))


def photos_download(args):
    """Scarica l'ORIGINALE di un mediaItem: GET /mediaItems/{id} → baseUrl,
    poi GET baseUrl+'=d'. baseUrl scade in 60 min → get+download nella stessa
    call (mai persistito)."""
    session = _photos_session()
    meta_resp = session.get(f"{_PHOTOS_API_BASE}/mediaItems/{args.media_item_id}")
    if meta_resp.status_code >= 400:
        _photos_fail("download-meta", meta_resp)
    meta = meta_resp.json()
    base_url = meta.get("baseUrl", "")
    if not base_url:
        print("ERROR download: no baseUrl for media item", file=sys.stderr)
        sys.exit(1)
    filename = meta.get("filename", args.media_item_id)
    # `--output` puo' essere un FILE (con estensione) o una DIRECTORY: il
    # filename originale non e' noto prima del meta, quindi il backend passa una
    # dir e qui componiamo <dir>/<filename>. Estensione presente + non-dir = file.
    out_arg = Path(args.output).expanduser() if args.output else Path.cwd()
    if out_arg.is_dir() or not out_arg.suffix:
        out_path = out_arg / filename
    else:
        out_path = out_arg
    out_path.parent.mkdir(parents=True, exist_ok=True)
    dl = session.get(base_url + "=d")
    if dl.status_code >= 400:
        _photos_fail("download-bytes", dl)
    out_path.write_bytes(dl.content)
    print(json.dumps({
        "status": "downloaded",
        "id": args.media_item_id,
        "filename": filename,
        "path": str(out_path),
        "bytes": len(dl.content),
        "mimeType": meta.get("mimeType", ""),
    }, ensure_ascii=False))


# --- Picker API (P3): l'UTENTE seleziona nella UI Google, l'app scarica ----
# Base separata dalla Library API. Scope photospicker.mediaitems.readonly.
# Flusso: sessions.create → pickerUri (LINK per l'utente) → sessions.get
# finche' mediaItemsSet → mediaItems?sessionId= (pageSize COSTANTE) →
# download baseUrl+'=d' (autenticato) → sessions.delete (cleanup).

_PICKER_API_BASE = "https://photospicker.googleapis.com/v1"


def photos_picker_create(args):
    session = _photos_session()
    resp = session.post(f"{_PICKER_API_BASE}/sessions", json={})
    if resp.status_code >= 400:
        _photos_fail("picker-create", resp)
    d = resp.json()
    print(json.dumps({
        "session_id": d.get("id", ""),
        "picker_uri": d.get("pickerUri", ""),
        "media_items_set": bool(d.get("mediaItemsSet")),
    }, ensure_ascii=False))


def photos_picker_get(args):
    session = _photos_session()
    resp = session.get(f"{_PICKER_API_BASE}/sessions/{args.session_id}")
    if resp.status_code >= 400:
        _photos_fail("picker-get", resp)
    d = resp.json()
    print(json.dumps({
        "session_id": d.get("id", args.session_id),
        "picker_uri": d.get("pickerUri", ""),
        "media_items_set": bool(d.get("mediaItemsSet")),
    }, ensure_ascii=False))


def photos_picker_download(args):
    """Scarica TUTTI gli item selezionati nella sessione picker in --output.
    Paginazione a pageSize COSTANTE (contratto API). A fine download la
    sessione viene chiusa (delete best-effort: gli item restano scaricati)."""
    session = _photos_session()
    out_dir = Path(args.output).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    page_token = ""
    max_total = int(args.max) if args.max else 0
    while True:
        params = {"sessionId": args.session_id, "pageSize": 100}
        if page_token:
            params["pageToken"] = page_token
        resp = session.get(f"{_PICKER_API_BASE}/mediaItems", params=params)
        if resp.status_code >= 400:
            _photos_fail("picker-items", resp)
        d = resp.json()
        for it in d.get("mediaItems", []):
            mf = it.get("mediaFile") or {}
            base_url = mf.get("baseUrl", "")
            filename = mf.get("filename") or f"{it.get('id','item')}.bin"
            row = {"id": it.get("id", ""), "filename": filename,
                   "mime": mf.get("mimeType", "")}
            if not base_url:
                row.update({"ok": False, "error": "no baseUrl"})
                results.append(row)
                continue
            dl = session.get(base_url + "=d")
            if dl.status_code >= 400:
                row.update({"ok": False,
                            "error": f"HTTP {dl.status_code}"})
                results.append(row)
                continue
            out_path = out_dir / filename
            n = 1
            while out_path.exists():   # niente overwrite silenzioso
                out_path = out_dir / f"{Path(filename).stem}-{n}{Path(filename).suffix}"
                n += 1
            out_path.write_bytes(dl.content)
            row.update({"ok": True, "path": str(out_path),
                        "bytes": len(dl.content)})
            results.append(row)
            if max_total and sum(1 for r in results if r.get("ok")) >= max_total:
                page_token = ""
                break
        else:
            page_token = d.get("nextPageToken", "")
        if not page_token:
            break
    try:  # cleanup best-effort: la sessione non serve piu'
        session.delete(f"{_PICKER_API_BASE}/sessions/{args.session_id}")
    except Exception:
        pass
    print(json.dumps({"results": results}, ensure_ascii=False))


# =========================================================================
# CLI parser
# =========================================================================


def main():
    parser = argparse.ArgumentParser(description="Google Workspace API for Metnos")
    sub = parser.add_subparsers(dest="service", required=True)

    # --- Gmail ---
    gmail = sub.add_parser("gmail")
    gmail_sub = gmail.add_subparsers(dest="action", required=True)

    p = gmail_sub.add_parser("search")
    p.add_argument("query", help="Gmail search query (e.g. 'is:unread')")
    p.add_argument("--max", type=int, default=10)
    p.set_defaults(func=gmail_search)

    p = gmail_sub.add_parser("get")
    p.add_argument("message_id")
    p.set_defaults(func=gmail_get)

    p = gmail_sub.add_parser("send")
    p.add_argument("--to", required=True)
    p.add_argument("--subject", required=True)
    p.add_argument("--body", required=True)
    p.add_argument("--cc", default="")
    p.add_argument("--from", dest="from_header", default="", help="Custom From header (e.g. '\"Agent Name\" <user@example.com>')")
    p.add_argument("--html", action="store_true", help="Send body as HTML")
    p.add_argument("--thread-id", default="", help="Thread ID for threading")
    p.set_defaults(func=gmail_send)

    p = gmail_sub.add_parser("reply")
    p.add_argument("message_id", help="Message ID to reply to")
    p.add_argument("--body", required=True)
    p.add_argument("--from", dest="from_header", default="", help="Custom From header (e.g. '\"Agent Name\" <user@example.com>')")
    p.set_defaults(func=gmail_reply)

    p = gmail_sub.add_parser("labels")
    p.set_defaults(func=gmail_labels)

    p = gmail_sub.add_parser("modify")
    p.add_argument("message_id")
    p.add_argument("--add-labels", default="", help="Comma-separated label IDs to add")
    p.add_argument("--remove-labels", default="", help="Comma-separated label IDs to remove")
    p.set_defaults(func=gmail_modify)

    # --- Calendar ---
    cal = sub.add_parser("calendar")
    cal_sub = cal.add_subparsers(dest="action", required=True)

    p = cal_sub.add_parser("list")
    p.add_argument("--start", default="", help="Start time (ISO 8601)")
    p.add_argument("--end", default="", help="End time (ISO 8601)")
    p.add_argument("--max", type=int, default=25)
    p.add_argument("--calendar", default="primary")
    p.set_defaults(func=calendar_list)

    p = cal_sub.add_parser("create")
    p.add_argument("--summary", required=True)
    p.add_argument("--start", required=True, help="Start (ISO 8601 with timezone)")
    p.add_argument("--end", required=True, help="End (ISO 8601 with timezone)")
    p.add_argument("--location", default="")
    p.add_argument("--description", default="")
    p.add_argument("--attendees", default="", help="Comma-separated email addresses")
    p.add_argument("--calendar", default="primary")
    p.set_defaults(func=calendar_create)

    p = cal_sub.add_parser("delete")
    p.add_argument("event_id")
    p.add_argument("--calendar", default="primary")
    p.set_defaults(func=calendar_delete)

    p = cal_sub.add_parser("new-calendar")
    p.add_argument("--summary", required=True)
    p.add_argument("--description", default="")
    p.add_argument("--timezone", default="")
    p.set_defaults(func=calendar_new)

    p = cal_sub.add_parser("list-calendars")
    p.set_defaults(func=calendar_list_cals)

    p = cal_sub.add_parser("delete-calendar")
    p.add_argument("calendar_id")
    p.set_defaults(func=calendar_delete_cal)

    p = cal_sub.add_parser("update")
    p.add_argument("event_id")
    p.add_argument("--summary", default=None)
    p.add_argument("--start", default=None,
                    help="Start (ISO 8601 with timezone or YYYY-MM-DD all-day)")
    p.add_argument("--end", default=None,
                    help="End (ISO 8601 with timezone or YYYY-MM-DD all-day)")
    p.add_argument("--location", default=None)
    p.add_argument("--description", default=None)
    p.add_argument("--attendees", default="",
                    help="Comma-separated email addresses")
    p.add_argument("--calendar", default="primary")
    p.set_defaults(func=calendar_update)

    # --- Drive ---
    drv = sub.add_parser("drive")
    drv_sub = drv.add_subparsers(dest="action", required=True)

    p = drv_sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--max", type=int, default=10)
    p.add_argument("--raw-query", action="store_true", help="Use query as raw Drive API query")
    p.set_defaults(func=drive_search)

    p = drv_sub.add_parser("get")
    p.add_argument("file_id")
    p.set_defaults(func=drive_get)

    p = drv_sub.add_parser("upload")
    p.add_argument("path", help="Local file path to upload")
    p.add_argument("--name", default="", help="Override file name in Drive (defaults to local filename)")
    p.add_argument("--parent", default="", help="Parent folder ID")
    p.add_argument("--mime-type", default="", help="Override MIME type (auto-detected if omitted)")
    p.set_defaults(func=drive_upload)

    p = drv_sub.add_parser("download")
    p.add_argument("file_id")
    p.add_argument("--output", default="", help="Local output path (defaults to ./<name> in cwd)")
    p.add_argument("--export-mime", default="", help="Export MIME for Google-native files (overrides defaults: pdf for Docs/Slides, csv for Sheets, png for Drawings)")
    p.set_defaults(func=drive_download)

    p = drv_sub.add_parser("read")
    p.add_argument("file_id")
    p.add_argument("--export-mime", default="", help="Override export MIME for Google-native (default: text/plain per Doc, csv per Sheet)")
    p.set_defaults(func=drive_read)

    p = drv_sub.add_parser("create-folder")
    p.add_argument("name")
    p.add_argument("--parent", default="", help="Parent folder ID (defaults to root)")
    p.set_defaults(func=drive_create_folder)

    p = drv_sub.add_parser("share")
    p.add_argument("file_id")
    p.add_argument("--role", default="reader", choices=["reader", "commenter", "writer", "fileOrganizer", "organizer", "owner"])
    p.add_argument("--type", default="user", choices=["user", "group", "domain", "anyone"])
    p.add_argument("--email", default="", help="Email address (required for type=user or type=group)")
    p.add_argument("--domain", default="", help="Domain (required for type=domain)")
    p.add_argument("--notify", action="store_true", help="Send notification email")
    p.set_defaults(func=drive_share)

    p = drv_sub.add_parser("delete")
    p.add_argument("file_id")
    p.add_argument("--permanent", action="store_true", help="Permanently delete (default is trash, which is reversible)")
    p.set_defaults(func=drive_delete)

    # --- Contacts ---
    con = sub.add_parser("contacts")
    con_sub = con.add_subparsers(dest="action", required=True)

    p = con_sub.add_parser("list")
    p.add_argument("--max", type=int, default=50)
    p.set_defaults(func=contacts_list)

    # --- Sheets ---
    sh = sub.add_parser("sheets")
    sh_sub = sh.add_subparsers(dest="action", required=True)

    p = sh_sub.add_parser("get")
    p.add_argument("sheet_id")
    p.add_argument("range")
    p.set_defaults(func=sheets_get)

    p = sh_sub.add_parser("update")
    p.add_argument("sheet_id")
    p.add_argument("range")
    p.add_argument("--values", required=True, help="JSON array of arrays")
    p.set_defaults(func=sheets_update)

    p = sh_sub.add_parser("append")
    p.add_argument("sheet_id")
    p.add_argument("range")
    p.add_argument("--values", required=True, help="JSON array of arrays")
    p.set_defaults(func=sheets_append)

    p = sh_sub.add_parser("create")
    p.add_argument("--title", required=True, help="Spreadsheet title")
    p.add_argument("--sheet-name", default="", help="Name of the first tab (defaults to 'Sheet1')")
    p.set_defaults(func=sheets_create)

    # --- Docs ---
    docs = sub.add_parser("docs")
    docs_sub = docs.add_subparsers(dest="action", required=True)

    p = docs_sub.add_parser("get")
    p.add_argument("doc_id")
    p.set_defaults(func=docs_get)

    p = docs_sub.add_parser("create")
    p.add_argument("--title", required=True, help="Document title")
    p.add_argument("--body", default="", help="Initial body text (optional)")
    p.set_defaults(func=docs_create)

    p = docs_sub.add_parser("append")
    p.add_argument("doc_id")
    p.add_argument("--text", required=True, help="Text to append to the end of the document")
    p.set_defaults(func=docs_append)

    p = docs_sub.add_parser("delete-range")
    p.add_argument("doc_id")
    p.add_argument("--start", type=int, required=True, help="Range start index (inclusive)")
    p.add_argument("--end", type=int, required=True, help="Range end index (exclusive)")
    p.set_defaults(func=docs_delete_range)

    # --- Vision ---
    vis = sub.add_parser("vision")
    vis_sub = vis.add_subparsers(dest="action", required=True)

    p = vis_sub.add_parser("web_detect")
    p.add_argument("--image-path", default="", help="Local image path (file)")
    p.add_argument("--image-url", default="",
                    help="Public image URL (alternative to --image-path)")
    p.add_argument("--max-results", type=int, default=20,
                    help="Cap web entities/pages returned (default 20)")
    p.set_defaults(func=vision_web_detect)

    # --- Photos (Library API, app-created only) ---
    ph = sub.add_parser("photos")
    ph_sub = ph.add_subparsers(dest="action", required=True)

    p = ph_sub.add_parser("upload")
    p.add_argument("path", help="Local image/video file to upload")
    p.add_argument("--album-id", default="", help="Album id to add the item to")
    p.add_argument("--mime-type", default="", help="Override MIME (auto-detected if omitted)")
    p.set_defaults(func=photos_upload)

    p = ph_sub.add_parser("upload-bytes")
    p.add_argument("path", help="Local file: uploads bytes only, prints uploadToken")
    p.add_argument("--mime-type", default="", help="Override MIME (auto-detected if omitted)")
    p.set_defaults(func=photos_upload_bytes)

    p = ph_sub.add_parser("batch-create")
    p.add_argument("--items", required=True,
                   help='JSON list [{"uploadToken": ..., "fileName": ...}] (max 50)')
    p.add_argument("--album-id", default="", help="Album id to add the items to")
    p.set_defaults(func=photos_batch_create)

    p = ph_sub.add_parser("album-create")
    p.add_argument("title", help="Album title")
    p.set_defaults(func=photos_album_create)

    p = ph_sub.add_parser("album-list")
    p.set_defaults(func=photos_album_list)

    p = ph_sub.add_parser("search")
    p.add_argument("--album-id", default="", help="Restrict to an album (mutually exclusive with --year)")
    p.add_argument("--year", default="", help="Filter by year YYYY (ignored if --album-id given)")
    p.add_argument("--page-token", default="", help="Continuation token from a previous page")
    p.add_argument("--max", type=int, default=100, help="Page size (max items per call)")
    p.set_defaults(func=photos_search)

    p = ph_sub.add_parser("download")
    p.add_argument("media_item_id")
    p.add_argument("--output", default="", help="Local output path (defaults to ./<filename>)")
    p.set_defaults(func=photos_download)

    p = ph_sub.add_parser("picker-create")
    p.set_defaults(func=photos_picker_create)

    p = ph_sub.add_parser("picker-get")
    p.add_argument("session_id")
    p.set_defaults(func=photos_picker_get)

    p = ph_sub.add_parser("picker-download")
    p.add_argument("session_id")
    p.add_argument("--output", required=True, help="Local destination directory")
    p.add_argument("--max", default="", help="Cap on downloaded items (empty = all)")
    p.set_defaults(func=photos_picker_download)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
