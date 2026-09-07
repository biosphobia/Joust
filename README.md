# Joust

A headless, audio-only clone of *Johann Sebastian Joust* for PlayStation Move
controllers, written in Python. No screen, no graphics: the game lives in the
music, the glowing spheres and the rumble in your hand.

Works with both PS Move models: **CECH-ZCM1** (PS3 era) and **CECH-ZCM2**
(the PS4 / PSVR one). Runs on Windows, Linux and macOS: Linux talks to the
controllers through hidraw with no extra packages, the other platforms go
through the `hidapi` package, which pip installs automatically.

## How the game works

* 2 to 8 players each hold a Move. Pull the trigger to join; every sphere
  lights up in its own colour.
* When the round starts the music plays. **Your only job is to keep your
  controller still.** Move it too fast and it explodes: a bang from the
  speakers, the sphere turns red, the controller rumbles, you are out.
* The music alternates between a *slow* phase and a *fast* phase.
  When it is slow the controllers are extremely sensitive, so everyone
  creeps. When it speeds up the threshold loosens for a few seconds and
  players can lunge at each other to knock rivals' controllers.
* A small jolt short of the limit gives a warning: a burst of rumble and a
  flickering sphere.
* Every player has **one ninja dodge per round**. Press the big Move button
  and for three quarters of a second you cannot be knocked out: the sphere
  flashes white, the controller purrs and a whoosh plays. Use it to survive
  a shove or to make a reckless lunge. Press it again later and a low
  double-blip tells you it is spent.
* Last player (or last team) standing wins. The winner's sphere cycles
  through the rainbow, then everyone goes back to the lobby.

## Install

Python 3.10 or newer.

```bash
pip install -e .
```

On Windows and macOS that is everything: the audio libraries and hidapi
ship inside the wheels. On Debian/Ubuntu also install the system audio
libraries and the udev rule so you can run without root:

```bash
sudo apt install libportaudio2 libsndfile1
sudo cp udev/99-psmove.rules /etc/udev/rules.d/ && sudo udevadm control --reload && sudo udevadm trigger
```

Drop songs into `songs/` (wav, flac, ogg, mp3). The game picks one at
random and switches to a different random song after every round. The
original uses Bach's Brandenburg Concertos; public-domain recordings exist
on musopen.org. Without any file the game plays a built-in, procedurally
generated harpsichord loop so it works out of the box.

## Pair the controllers (once per controller)

Plug the controller in over USB, then:

```bash
sudo joust pair                       # Linux
joust pair --host aa:bb:cc:dd:ee:ff   # Windows / macOS (your adapter's address)
```

This writes your PC's Bluetooth address into the controller. On Linux it
also registers the controller with BlueZ, so after unplugging you just
press the PS button and the sphere lights up when connected.

On Windows and macOS, unplug, press the PS button and accept "Motion
Controller" in the system Bluetooth settings when it appears. That works
for the ZCM2 (PS4) controller, which is the model this project targets
first. The older ZCM1 does not do standard pairing on Windows; use
psmoveapi's `psmove pair` tool for it once, then this game will see it.

`joust list --probe` shows what the PC sees, including battery level.

### Troubleshooting on Windows

Windows lists every Move three times (the HID path contains `&col01#`,
`&col02#` or `&col03#`). Only `col01` delivers input; the game picks it
automatically. If you still see a hidapi "read error":

* Run `joust list --raw`. It prints each raw entry and tries a short read
  on every one, which shows which collection works and whether another
  program has the controller open.
* Close anything else that talks to Move controllers (Steam, PSMoveService,
  DS4Windows) and reconnect the controller.
* Make sure you are connected over Bluetooth, not USB; the PS3-era ZCM1
  sends no sensor data over USB.

Bluetooth tip: one adapter handles about 6 or 7 controllers. A class 1 USB
dongle has better range and lower latency than most built-in adapters.

## Play

```bash
joust play                       # default sensitivity, free-for-all
joust play --teams               # start in team mode
joust play --sensitivity 3       # 0 = ultra slow ... 4 = ultra fast
joust play --songs ~/bach/       # a different songs folder (or one file)
joust play --dodge-button square # move the ninja dodge to another button
joust play --dodge-seconds 1.0   # longer invulnerability
joust play --sim 4               # no hardware: four simulated players
joust test-audio                 # hear a song at three speeds + all effects
```

Controllers keep connecting while the game runs; there is no need to
restart when someone joins late.

### Buttons

| Where | Button | Action |
|-------|--------|--------|
| Lobby | Trigger | Join the next round |
| Lobby | Hold trigger 2 s | Force the round to start now |
| Lobby | Circle / X | Leave the round |
| Lobby | Move | Cycle sensitivity (you hear 1 to 5 beeps) |
| Lobby | Square | Toggle team mode (two tones = on) |
| Lobby | Select | Show battery level as a colour for 2 s |
| Playing | Move | Ninja dodge: 0.75 s invulnerable, once per round |

The round auto-starts three seconds after every connected controller has
joined (minimum two players).

Battery colours: green 100%, turquoise 80%, blue 60%, yellow 40%, red 20%
or less, dim white while charging.

## Tuning

Movement is scored as the smoothed magnitude of the accelerometer vector in
g (1.0 when held still). The kill threshold depends on the sensitivity
setting and on where the music currently sits between slow and fast:

| Sensitivity | Slow: warn / out | Fast: warn / out |
|-------------|------------------|------------------|
| 0 ultra slow | 1.2 / 1.3 g | 1.4 / 1.6 g |
| 1 slow       | 1.3 / 1.5 g | 1.6 / 1.8 g |
| 2 medium     | 1.6 / 1.8 g | 1.9 / 2.8 g |
| 3 fast       | 2.0 / 2.5 g | 2.7 / 3.2 g |
| 4 ultra fast | 2.5 / 3.2 g | 2.8 / 3.5 g |

Music speed glides between `--slow-speed` (1.0) and `--fast-speed` (1.3)
over 1.5 s. Slow phases last 10 to 23 s and fast phases 4 to 8 s at the
start of a round, tightening to 8 to 12 s and 6 to 10 s as players drop.

## Layout

```
joust/psmove/    HID protocol, hidraw + hidapi backends, controller I/O, pairing
joust/audio/     variable-speed mixer (sounddevice), synthesized effects, song playlist
joust/game/      motion metric, tempo state machine, round logic
joust/sim.py     simulated controllers
joust/cli.py     `joust play | list | pair | test-audio`, song rotation
songs/           put your music here
tests/           pytest suite (runs without hardware or audio)
```

Run the tests with `pytest`.

## Credits

Johann Sebastian Joust is by Die Gute Fabrik. The controller protocol comes
from the moveonpc wiki and the psmoveapi project; the threshold tables and
tempo pacing follow JoustMania, which spent years tuning them at conventions.
