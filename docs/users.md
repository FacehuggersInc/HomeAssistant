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

## The panel's own identity

Every route wants a device token. The panel is not a device, and it calls its
own routes constantly - an action tile asking `/dashboard/state`, a skill
posting to `/say`.

So it holds one of its own, made fresh each run and kept only in memory. It is
approved by definition, named "This panel", and does not appear among the
users: it is not somebody's device, it cannot be revoked, and it has no place
on a page about who has access.

The alternative was borrowing an approved device's token, which is worse than
it sounds. `touch()` would mark that person as active whenever the panel
called itself, `/say` would announce their name as the sender, revoking them
would silently break every action tile - and a fresh install with nobody
approved would have no access to its own routes at all.

## From a browser

A browser that reaches a page it is not allowed to see is **redirected** to
`/access/wait` rather than refused. That page asks for access on the visitor's
behalf, waits for somebody at the panel, and then sends them on to where they
were going with the token attached.

Told apart by the `Accept` header: a script wants a 401 it can read, and a
person looking at a blank page with some JSON on it wants to be told what to do
about it.

## Being remembered

An accepted token is stored in a `ha_device_token` cookie, and read back as a
third source alongside the query string and the `X-Client-Token` header.

Without it an approved device is asked to pair again every time somebody types
the bare address: a browser sends no `X-Client-Token`, and `<ip>:port` on its
own carries no query string, so there is nothing left identifying the device.

It is set **only after the token has been checked**, so a rejected one is never
stored, and only when the value actually changes, so an ordinary page load
carries no `Set-Cookie` it does not need. `HttpOnly`, and `SameSite=Lax` rather
than `Strict` - a device following a link from a message app is still the same
device, and `Strict` would drop the cookie and start the whole dance again.

A token in the URL still wins over the cookie, and the cookie is replaced with
whichever one worked. Revoking a device on the panel takes effect on its next
request regardless of what it is holding.

## Two holds, not one

Approving a device sets **both** `awaiting_name` and `awaiting_decision`.

`awaiting_name` means nobody has given the device a name yet.
`awaiting_decision` means nobody has answered the *second* question - name them
here, or let them name themselves. Without it a device would be told "you're
in" while that question is still on screen, walk off to `/access/name`, and
name itself out from under whoever is mid-way through naming it.

While the decision is open the device is held on the waiting page:
`/access/state` reports `deciding`, `auth()` sends it back to wait rather than
to naming, and `/access/name` refuses it outright in case it arrives by a stale
link. `needs_name()` is False throughout, which is what the wait page checks.

Answering releases it: **Name them** clears both through `rename()`, **Let them
decide** clears only the decision and sends the device off to name itself.
Typing nothing at the panel falls through to the second.

## Coming back with a token that is no longer good

A browser arriving with a stale, revoked or unknown token is sent to
`/access/wait` like any other - but **the refused token is not carried with
it**, and neither is the one already in the address it was heading for.

That matters more than it sounds. `next` is built from the path the browser
arrived with, and if the refused token were left on it the wait page would
append the new one to give `?token=OLD&token=NEW`. The first value wins in
every parser there is, so the browser would go back holding exactly the token
that had just been refused, be refused again, and bounce between the two
forever.

Both strip `token` and `id` out of a target before adding the real one - in the
redirect and in the wait page's own JavaScript, which builds the same URL
client-side. Every other query parameter survives, so a device sent away from a
form comes back to the same form.

## The flow

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

## Naming

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

## Choosing an owner

Anywhere something needs an owner — the event editor, the calendar's phone
form, the subscriptions page, the add-a-calendar dialog — the choice is a
picker over `USERS.names()` rather than a text field.

That is not tidiness. A free text field meant "Chris", "chris" and "Chris "
were three different owners as far as the store was concerned, each holding
some of the same events, and nothing in the UI showed why.

`GET /users?token=...` returns the same list, for anything building its own.

## Using it

`client.USERS` is a `UserRegistry`.

| Call                                | Does                                          |
|-------------------------------------|-----------------------------------------------|
| `get(token)`                        | The `User`, or `None`.                        |
| `is_approved(token)`                | Whether that token is allowed.                |
| `touch(token)`                      | Record that a device was seen, and return it. |
| `all_users()`                       | Every approved device, by name.               |
| `state_of(token)`                   | `approved`, `pending`, `denied` or `unknown`. |
| `approve(token, name="")`           | Let a waiting request in.                     |
| `deny(token)` / `revoke(token)`     | Refuse one, or remove an approved one.        |
| `rename(token, name)`               | Give a device a better name.                  |
| `waiting()`                         | Undecided requests, oldest first.             |
| `subscribe(fn)` / `unsubscribe(fn)` | Told when the list changes.                   |

## Identifying the caller

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

## Permissions

Approval is the door. A permission is what a device may touch once it is
inside, and the two are separate because they answer different questions -
"is this phone allowed here" and "may this phone put code on the machine" are
not the same decision and should not be made by the same yes.

| Permission | What it allows                           |
|------------|------------------------------------------|
| `plugins`  | Upload, load, unload and reload plugins. |

They live as a **set of names** on the user rather than a flag each. Adding
one is a string in `PERMISSIONS` and a checkbox, rather than a new column in
the saved file that every existing install has to be migrated for. A name not
in `PERMISSION_KEYS` is dropped when the file is read, so a permission removed
from the app and later reused for something else cannot come back attached to
somebody.

**Nobody holds any by default.** A device is approved and holds nothing until
it is granted something, whenever it was approved. A permission that arrives
switched on for everybody is not a permission.

`USERS.may(token, name)` re-checks approval rather than reading the flag off a
user object. A revoked device keeps its token, and a permission read without
asking whether that device is still let in is a door that stays open after the
lock is changed. The panel's own token holds everything: it is the thing
granting permissions, not a device with them.

Granted on the panel - **Settings → Users**, the menu on a device's card - and
never over the API. Turning one on asks for confirmation; turning it off does
not, because taking a capability away is not a thing to be talked out of. What
a device holds is shown on its card, since a capability visible only after
opening a menu is one nobody audits.

## Reading the token in a route

`auth()` looks in three places - the query string, the `X-Client-Token`
header, and the cookie - because a device arrives by all three: a link carries
the token, a script sends the header, and a browser that has been here before
sends the cookie and nothing else.

**A route that reads only the first two passes `auth()` and then behaves as
though nobody is there.** The cookie satisfies the check, so the request is
allowed; the route's own `token` is empty, so every permission test on it
fails and every link it renders carries `token=`. On a phone - which is the
only place these pages are ever opened - that is the whole of the failure, and
it looks exactly like the permission not having been granted.

Use `_token()`. It is the same three places, in the same order.

## Where it lives

`users.json` in the user data directory, written `0600` because it holds
tokens. Not in settings: it is state built up by approving things, not
configuration anyone edits, and an update unpacking over the app tree must not
take it.

**Settings → Users** lists approved devices with their address and last-seen
time, and revokes them one at a time. It is generated live from the registry —
there is no settings path behind it.
