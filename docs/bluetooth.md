# Bluetooth

> **Linux only.** This section talks to BlueZ over D-Bus. On Windows and
> macOS it does not appear, and nothing else in the panel is affected.

A section in Settings under **SYSTEM**, beside Wi-Fi, and a state button in the
quick panel.

## How it talks to the adapter

`jeepney`, which speaks D-Bus in pure Python — nothing to compile, and no system
bindings that have to match the interpreter. BlueZ already exposes everything
worth asking:

| | |
|---|---|
| `Adapter1.Powered` | on and off |
| `Adapter1.StartDiscovery` | look for what is in range |
| `Device1.Paired` / `Trusted` | seen before, or new |
| `Device1.Connect` / `Disconnect` | use it |
| `Battery1.Percentage` | what is left in it |

So the work here is asking properly rather than reimplementing any of it.

`jeepney` is **optional**. Without it — or without BlueZ, or without an adapter
— the section says which of those is missing rather than failing. Those are
different jobs: "install a Python package" and "start the Bluetooth service"
send somebody in different directions, and `missing()` tells them apart.

## Reading the object tree

One `GetManagedObjects` call returns adapters and devices together, and
`parse_devices()` turns it into a list. Split from the call so it can be tested
against a captured tree with no adapter present.

Devices sort **connected first, then paired-before, then by signal**. A list that
opens on the headphones already in use answers the common question without being
read.

A device reporting no battery is `-1`, not `0` — a headset at zero percent and a
mouse that has no battery service are not the same thing. One with no name falls
back to its address rather than showing a blank row.

## Connecting

`Pair` first, but only for a device that has not been paired before; one that has
connects straight away. A newly paired device is set **trusted**, so it
reconnects on its own next time — which is what pairing headphones to a wall
panel means by it.

## Discovery is not left running

Scanning stops on a timer, and on leaving the section. Discovery keeps the radio
busy and drains anything battery-powered in range, long after nobody is looking
at the screen.

## In the quick panel

A state button beside Wi-Fi showing the connected device and its charge, which is
the reason to glance at it. Pressing it opens this section. When Bluetooth is
unavailable it says so and explains what is missing instead.

## Nothing waits on the bus from the UI thread

Asking "is there an adapter" is a round trip to the system bus, and if BlueZ is
not running D-Bus tries to **start** it — which waits far longer than anybody
will believe the application is still alive for.

So:

* The answer is **cached**. Neither part of it changes while the app runs: a
  Python package does not appear, and an adapter is not plugged into a wall
  panel. `known()` says whether it has been worked out yet, so a caller on the
  UI thread can skip rather than block.
* The button and the section paint as "asking" and fill in from a worker, rather
  than reading anything while they are built. Both are constructed during
  startup, where a slow answer holds up the whole build.
* The socket has a timeout. Without one, a bus that accepts a connection but
  never finishes the handshake blocks its caller for good.
* One `GetManagedObjects` per refresh. `powered()`, `devices()` and
  `connected_device()` each ask separately, and the object tree already holds
  all three — `snapshot()` returns them together.

## Ruling it out

```
HA_NO_BLUETOOTH=1 python app.py
```

Every call answers immediately without touching the bus. A suspicion that cannot
be tested is not worth much on hardware you have to walk to.
