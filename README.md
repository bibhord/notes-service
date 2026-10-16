# Notes Service

A REST backend for a note-taking service shared among small teams. Django 5 and
Django REST Framework, SQLite by default, Postgres when you want it.

A note belongs to one person. They can keep it private or share it with one
team they belong to. That is the whole model.

---

## Running it

```bash
make install
make seed      # optional: demo users, a team, some notes
make run       # http://127.0.0.1:8000
make test
```

Or with Postgres:

```bash
docker compose up --build
```

`make seed` prints API tokens for three users. `ada` and `grace` share a team,
`mallory` is on her own — useful for seeing what the isolation rules actually do.

Nothing needs a `.env`. Every setting has a working default; `.env.example`
lists the ones that matter in production.

---

## API

All endpoints except register and token require `Authorization: Token <key>`.

| Method | Path | What it does |
|---|---|---|
| POST | `/api/auth/register/` | Create an account, returns a token |
| POST | `/api/auth/token/` | Exchange username + password for a token |
| GET | `/api/me/` | The current user |
| GET, POST | `/api/teams/` | Your teams / create one (you become admin) |
| GET, PATCH, DELETE | `/api/teams/{slug}/` | One team (writes are admin-only) |
| GET, POST | `/api/teams/{slug}/members/` | Members / add one (admin-only) |
| DELETE | `/api/teams/{slug}/members/{username}/` | Remove a member (admin-only) |
| GET, POST | `/api/notes/` | Notes you can see / create one |
| GET, PATCH, DELETE | `/api/notes/{id}/` | One note (DELETE is a soft delete) |
| GET | `/api/notes/trash/` | Notes you deleted |
| POST | `/api/notes/{id}/restore/` | Undelete |

Listing notes supports `?q=`, `?team=`, `?author=`, `?visibility=`,
`?updated_after=`, `?ordering=`, `?page=` and `?page_size=`.

```bash
curl -H "Authorization: Token $TOKEN" \
  "http://127.0.0.1:8000/api/notes/?q=runbook&team=platform"
```

---

## The three things I spent the most time on

### 1. Authorization lives in the queryset, not in the view

Every read path starts from `Note.objects.alive().visible_to(request.user)`.
`visible_to()` is one method, in one file, and it is the only code that decides
what a user may read.

I have shipped multi-tenant analytics where competing businesses sit in the
same warehouse, and the thing I learned there is that per-view permission
checks fail open. Somebody adds an endpoint six months later, forgets the
check, and the bug is invisible because the response looks perfectly normal —
just with too many rows in it. If the base queryset is already scoped, the same
mistake returns an empty list instead of someone else's data.

It also sets the failure mode. A note you cannot see returns **404, not 403**,
because a 403 confirms the note exists. The tests assert this directly, along
with a case proving that `?q=` cannot be used as an oracle to probe for notes
you cannot read.

Writes are a separate question and are handled by `CanEditNote`. By the time
a request reaches it the caller can already see the note, so the only thing
left to decide is whether they may change it.

### 2. How sharing is modelled

A note has a `visibility` of `private` or `team`, plus a nullable FK to one
team. Not a join table of per-note grants.

The alternative — an ACL row per note per grantee — is strictly more powerful,
and I did not pick it. For "a note is mine, or it's my team's," an ACL table
means a join on every list query and a second write path to keep in sync, to
express two states. The simpler schema makes the common query one index scan.

The cost is that this cannot express "share this one note with Grace," or
"share with two teams." If that requirement shows up, the migration is a
`NoteShare` table and a wider `visible_to()`, and the rest of the code does
not move. I would rather make that change when somebody asks for it than carry
the join now.

Two fields that must agree is a real risk, so it is enforced in the database,
not just in the serializer:

```python
models.CheckConstraint(
    condition=(
        Q(visibility="team", team__isnull=False)
        | Q(visibility="private", team__isnull=True)
    ),
    name="team_visibility_requires_team",
)
```

A future bulk update or a management command cannot create a note that claims
to be shared with nobody. Validation in the serializer catches the API path;
the constraint catches every other path there will ever be.

### 3. Delete does not delete

`DELETE /api/notes/{id}/` sets `deleted_at`. The row stays. `/notes/trash/`
lists what you deleted and `/notes/{id}/restore/` puts it back.

Notes are the product. Losing one to a mis-click is a much worse outcome than
carrying dead rows in a table, and the storage cost of text is not a real cost.

What I deliberately did **not** do is make the default manager hide deleted
rows. That trick is convenient right up to the first time you need to find a
deleted record and cannot work out why your query returns nothing. Instead
`Note.objects` returns everything and callers say `.alive()` or `.deleted()`.
Slightly more typing, no surprises.

---

## What I would change, add, or stop doing with more time

**Change: search.** `?q=` is `ILIKE %term%` over title and body. It is correct,
it works the same on SQLite and Postgres, and it will fall over at scale
because it cannot use an index. The next step is a Postgres `GIN` index with
`SearchVector`, or `pg_trgm` if substring matching matters more than stemming.
I kept LIKE because the point of this exercise was the sharing and
authorization model, and a full-text setup would have been the most code for
the least insight.

**Add: note history.** Shared editing with no revision log means two people on
one note can silently overwrite each other. The fix is a `NoteRevision` row per
save and optimistic concurrency via an `updated_at` precondition on PATCH. This
is the gap I am least comfortable leaving.

**Add: an OpenAPI schema.** `drf-spectacular` would generate it from what is
already here. I left it out rather than add a dependency for a take-home, but
on a real service a hand-written endpoint table like the one above goes stale
within a month.

**Add: rate limiting and an audit log.** DRF throttling on the auth endpoints
at minimum. For a shared workspace, "who deleted this and when" is a question
people ask out loud.

**Change: token auth.** Tokens here never expire, which is fine for a
take-home and wrong for production. The reason I chose DRF tokens over JWT is
revocation — deleting a row kills a session immediately, which for a shared
workspace matters more than the statelessness JWT buys. A real version gets
expiry and rotation.

**Stop: running migrations in the container entrypoint.** `docker compose up`
does it so that a clean checkout is one command. In a real deployment that is
a release step, not something every replica races to do on boot.

---

## Scope I left out on purpose

No tags or folders, no attachments, no real-time collaboration, no invite flow
for users who do not have accounts yet (you add people by existing username).
Each of those is a reasonable next feature; none of them would have told you
anything more about how I think about the part that is actually hard here,
which is who can see what.

---

## Tests

```bash
make test
```

44 tests. The ones worth reading are in `notes/tests/test_permissions.py` —
they are written from the attacker's side: given a note I should not have,
what does every verb return?

- Private notes stay invisible to everyone but the author, across list, read, edit, delete and search
- Team members can read and edit shared notes, but only the author or a team admin can delete one
- Removing someone from a team revokes their access on the next request
- You cannot post a note into a team you are not on, or move your own note into one
- Visibility and team must agree, in both directions
- A team cannot be left without an admin

The rest cover registration, token auth, CRUD, validation, soft delete and
restore, search, filtering and pagination.
