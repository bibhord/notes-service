"""Optimistic concurrency for note writes.

A note carries an integer version, surfaced as an HTTP ETag. A client
that wants to edit safely sends back the version it last read in
`If-Match`, and the write only lands if nothing has changed underneath.

Two notes on the choices here:

* An integer, not `updated_at`. Timestamps have precision limits, clocks
  can move backwards, and two writes in the same microsecond compare
  equal. A counter has none of those problems.

* Shared notes *require* the precondition; private ones do not. Making
  every client do a read before every write is a real cost, and it buys
  nothing on a note only one person can reach. The moment a note is
  shared with a team, concurrent editors become possible and a blind
  write is no longer safe -- so that is where the requirement starts.

* The version travels in the header, never in the body. It describes the
  request rather than the note, and mixing it into the payload invites a
  client to treat it as a writable field. `If-Match` is the header HTTP
  already has for this, so there is no reason to invent one.
"""

from rest_framework import status
from rest_framework.exceptions import APIException

from .models import Note


class PreconditionRequired(APIException):
    status_code = status.HTTP_428_PRECONDITION_REQUIRED
    default_detail = (
        "This note is shared with a team, so an If-Match header carrying the "
        "version you last read is required. GET the note and use the ETag "
        "it returns. A version in the request body is not accepted."
    )
    default_code = "precondition_required"


def etag_for(note: Note) -> str:
    return f'"{note.version}"'


def parse_if_match(raw: str | None) -> int | None:
    """Pull a version out of an If-Match header.

    Accepts `"7"`, `W/"7"` and a bare `7`. Returns None for a missing
    header or `*`, which means "any current version" and so imposes no
    constraint. A malformed value returns None rather than raising: it is
    the caller's job to decide whether a missing precondition is fatal.
    """
    if not raw:
        return None
    value = raw.strip()
    if value == "*":
        return None
    if value.startswith("W/"):
        value = value[2:]
    value = value.strip('"')
    try:
        return int(value)
    except ValueError:
        return None


def expected_version_for(request, note: Note) -> int | None:
    """Work out which version this write must match, if any.

    The If-Match header is the only source. Raises 428 when a shared note
    is written without one.
    """
    expected = parse_if_match(request.headers.get("If-Match"))

    if expected is None and note.visibility == Note.Visibility.TEAM:
        raise PreconditionRequired()

    return expected
