# Joust

A headless, audio-only clone of *Johann Sebastian Joust* for PlayStation Move
controllers, written in Python. No screen, no graphics: the game lives in the
music, the glowing spheres and the rumble in your hand.

Works with both PS Move models: **CECH-ZCM1** (PS3 era) and **CECH-ZCM2**
(the PS4 / PSVR one). Linux is the primary target and needs no native
dependencies beyond an audio output; the `hidapi` backend also runs on
Windows and macOS once the controllers are paired there.

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
* Last player (or last team) standing wins. The winner's sphere cycles
  through the rainbow, then everyone goes back to the lobby.

## Install

```bash
sudo apt install libportaudio2 libsndfile1     # Debian/Ubuntu audio libs
pip install -e .
sudo cp udev/99-psmove.rules /etc/udev/rules.d/ && sudo udevadm control --reload && sudo udevadm trigger
```

Optionally drop music files into `music/` (wav, flac, ogg, mp3). The
original uses Bach's Brandenburg Concertos; public-domain recordings exist
on musopen.org. Without any file the game plays a built-in, procedurally
generated harpsichord loop so it works out of the box.

## Pair the controllers (once per controller)

```bash
sudo joust pair          # with the controller plugged in over USB
```

This writes your PC's Bluetooth address into the controller and registers
the controller with BlueZ. Unplug the cable, press the PS button, and the
sphere lights up when connected. `joust list --probe` shows what the PC
sees, including battery level.

Bluetooth tip: one adapter handles about 6 or 7 controllers. A class 1 USB
dongle has better range and lower latency than most built-in adapters.

## Play

```bash
joust play                       # default sensitivity, free-for-all
joust play --teams               # start in team mode
joust play --sensitivity 3       # 0 = ultra slow ... 4 = ultra fast
joust play --music ~/bach/       # a file, or a directory to pick from
joust play --sim 4               # no hardware: four simulated players
joust test-audio                 # hear the music at three speeds + all effects
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
joust/audio/     variable-speed mixer (sounddevice), synthesized effects, file loading
joust/game/      motion metric, tempo state machine, round logic
joust/sim.py     simulated controllers
joust/cli.py     `joust play | list | pair | test-audio`
tests/           pytest suite (runs without hardware or audio)
```

Run the tests with `pytest`.

## Credits

Johann Sebastian Joust is by Die Gute Fabrik. The controller protocol comes
from the moveonpc wiki and the psmoveapi project; the threshold tables and
tempo pacing follow JoustMania, which spent years tuning them at conventions.
