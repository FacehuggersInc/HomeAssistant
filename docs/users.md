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
