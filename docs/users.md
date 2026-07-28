# Users

Who is allowed to talk to this panel, and how a device is let in.

---



The API has no shared password. A device asks for access, somebody standing at
the panel allows or denies it, and the token that comes back belongs to that
device alone.

That matters for three reasons a single copied secret could not manage: a
device can be revoked without affecting the others, an endpoint can tell who is
calling, and anything that learns one token has not learned the way in for
everything else.

### From a browser

A browser that reaches a page it is not allowed to see is **redirected** to
`/access/wait` rather than refused. That page asks for access on the visitor's
behalf, waits for somebody at the panel, and then sends them on to where they
were going with the token attached.

Told apart by the `Accept` header: a script wants a 401 it can read, and a
person looking at a blank page with some JSON on it wants to be told what to do
about it.

### The flow

```
POST /access/request?name=My%20phone   ->  {"token": "...", "state": "pending"}
GET  /access/state?token=...           ->  {"state": "pending|approved|denied"}
```

A dialog appears on the panel with the device's name and address. Approve it
and the device's next request works; deny it and the polling device is told so
rather than left to time out.

Requests are asked about **one at a time**. Two devices asking together would
otherwise stack two dialogs, and the second would be answered blind.

Approval is polled off the client tick rather than pushed from the request,
because the request arrives on a Flask worker thread and a dialog cannot be
built there.

### Naming

A device announces itself as something like "Firefox on Linux" — which says
what it is and nothing about whose it is. Approving one therefore asks a second
question:

* **Name them** opens the keyboard on the panel, for when whoever is standing
  there knows.
* **Let them decide** marks the user `awaiting_name` and the device is sent to
  a naming page the next time it polls, before it goes anywhere else.

Approval and naming are separate steps on purpose: cancelling the name must not
be able to undo the approval.

A user still choosing a name is left out of `names()`. Offering a placeholder
as an owner is how a household ends up with three events belonging to "Browser
on Linux".

Anyone can be renamed later under **Settings → Users**, which also marks who is
still choosing.

### Choosing an owner

Anywhere something needs an owner — the event editor, the calendar's phone
form, the subscriptions page, the add-a-calendar dialog — the choice is a
picker over `USERS.names()` rather than a text field.

That is not tidiness. A free text field meant "Chris", "chris" and "Chris "
were three different owners as far as the store was concerned, each holding
some of the same events, and nothing in the UI showed why.

`GET /users?token=...` returns the same list, for anything building its own.

### Using it

`client.USERS` is a `UserRegistry`.

| Call | Does |
|---|---|
| `get(token)` | The `User`, or `None`. |
| `is_approved(token)` | Whether that token is allowed. |
| `touch(token)` | Record that a device was seen, and return it. |
| `all_users()` | Every approved device, by name. |
| `state_of(token)` | `approved`, `pending`, `denied` or `unknown`. |
| `approve(token, name="")` | Let a waiting request in. |
| `deny(token)` / `revoke(token)` | Refuse one, or remove an approved one. |
| `rename(token, name)` | Give a device a better name. |
| `waiting()` | Undecided requests, oldest first. |
| `subscribe(fn)` / `unsubscribe(fn)` | Told when the list changes. |

### Identifying the caller

`auth()` records the matched user on the request, so an endpoint does not have
to look it up again:

```python
from flask import request

def my_endpoint(**params):
    user = request.environ.get("ha.user")
    name = user.name if user else "someone"
    return {"request": "Success", "hello": name}, 200
```

The Calendar plugin uses this to attribute an imported event to the device that
sent it. Treat it as *who*, not as permission — every approved device can do
everything the API exposes.

### Where it lives

`users.json` in the user data directory, written `0600` because it holds
tokens. Not in settings: it is state built up by approving things, not
configuration anyone edits, and an update unpacking over the app tree must not
take it.

**Settings → Users** lists approved devices with their address and last-seen
time, and revokes them one at a time. It is generated live from the registry —
there is no settings path behind it.
