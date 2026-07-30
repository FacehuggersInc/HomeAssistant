# Wi-Fi

A section in Settings under **SYSTEM**, beside Users and Plugins. It sits there
rather than with the file-generated sections because it behaves like them: the
list changes while you are looking at it, and joining a network happens
immediately rather than on a Save button.

## What it needs

`nmcli` for everything, `iwgetid`/`iw` for reading only.

**NetworkManager is required to join a network, not merely preferred.** It is
what stores the credential and brings the link back after a reboot — joining
through anything else works until the next restart and then quietly stops. Where
it is missing the section says so, and hides the Scan button rather than
offering one that cannot work.

Throughput needs nothing: the byte counters come straight from
`/sys/class/net/<iface>/statistics`.

## Reading nmcli

The **terse** output is parsed, not the aligned output. Terse is the only one
that escapes anything, and network names containing colons exist — a plain
`split(":")` mangles them. `_split_terse()` handles `\:` and `\\`, which is why
this needs a parser rather than a one-liner.

The scan shows **one entry per name**. A network with two radios appears once
per access point, and a list with the same name five times is not a list of
networks. The strongest radio wins for signal, except that "currently joined"
is never lost to a stronger radio that is not.

Failure messages are nmcli's own, with one substitution: *"Secrets were
required"* becomes something a person can act on. A wrong password and a network
out of range fail differently and it matters which.

## Throughput

Sampled once a second while the section is on screen.

The counters are **cumulative since boot**, so the first sample is not a rate —
reporting it would show the whole uptime's traffic as this second's. A dash
shows until there are two samples.

A difference that comes out negative reads as zero rather than as a spike: a
counter wrapping would otherwise report four gigabytes in one second.

Rates are shown in **bits**, since that is how links are sold, and the label is
monospaced so the numbers do not jump sideways as digits change width.

## Costs

Both timers stop on `hideEvent`. A wall panel is on its home page nearly all the
time, and neither a network list nor a byte counter is worth a subprocess a
second when nobody is reading it.

Every nmcli call runs on a worker and applies through `call_on_ui`. A scan takes
seconds — long enough to freeze the page, and a frozen panel looks broken rather
than busy. The byte counters are the exception and stay on the UI thread, being
a file read.

## From the quick panel

The System side shows the current network as a button. Pressing it closes the
panel and opens Settings **at this section**:

```python
client.goto("#settings", data={"section": "wifi"}, override=True)
```

Any page can be opened at a section this way. The name is checked against the
nav buttons that were actually built rather than trusted — a section can be
missing because its plugin failed to load, and a stale link should land
somewhere real instead of on a blank page.

The quick row shows the name and signal only. The up/down figures stay in
Settings, where there is room for them and someone is looking.

## Joining

Tap a network. A password is asked for only when one is needed — a saved profile
already holds the credential and an open network has none — and the prompt masks
what is typed.

Joining is not confirmed first: anyone at the panel can tap and type.
**Forgetting** is, since it throws away a credential that would otherwise have
to be entered again.
