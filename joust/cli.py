"""Command line entry points."""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

from . import __version__
from .audio import create_audio, load_audio_file, pick_music
from .audio import synth
from .game import JoustSession, Settings
from .game.motion import SENSITIVITY_NAMES
from .game.tempo import TempoController
from .psmove import available_backends, enumerate_devices

log = logging.getLogger("joust")

DEFAULT_MUSIC_DIR = Path(__file__).resolve().parent.parent / "music"


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _load_music(audio, path: Path | None, silent: bool) -> None:
    if silent:
        return
    chosen = pick_music(path) if path else None
    if chosen is None and path is None:
        chosen = pick_music(DEFAULT_MUSIC_DIR)
    if chosen is not None:
        try:
            samples, rate = load_audio_file(chosen)
            audio.set_music(samples, rate)
            log.info("music loaded: %s (%.0fs)", chosen.name, len(samples) / rate)
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("could not load %s (%s); using built-in music", chosen, exc)
    log.info("using built-in generated music (drop a Brandenburg Concerto into music/ for the real thing)")
    audio.set_music(synth.generate_baroque_loop(rate=audio.rate), audio.rate)


def cmd_list(args) -> int:
    devices = enumerate_devices(args.backend)
    print(f"backends: {', '.join(available_backends()) or 'none'}")
    if not devices:
        print("no PS Move controllers found")
        return 1
    from .psmove import Controller

    for d in devices:
        line = f"{d.model.value:5} {d.transport:9} {d.label:20} {d.path}"
        if args.probe:
            try:
                c = Controller(d)
                c.start()
                time.sleep(0.5)
                bat = c.battery.label if c.battery else "?"
                acc = c.accel_g()
                accs = f"|a|={sum(v * v for v in acc) ** 0.5:.2f}g" if acc else "no input yet"
                line += f"  battery={bat} {accs} reports={c.reports}"
                c.close()
            except Exception as exc:  # noqa: BLE001
                line += f"  (probe failed: {exc})"
        print(line)
    return 0


def cmd_pair(args) -> int:
    from .psmove import pairing

    addrs = pairing.pair_all(host=args.host, register=not args.no_register)
    if not addrs:
        return 1
    print("paired:", ", ".join(addrs))
    print("Unplug the USB cable and press the PS button; the sphere should light up when connected.")
    return 0


def cmd_play(args) -> int:
    audio = create_audio(enabled=not args.no_audio, device=args.audio_device)
    _load_music(audio, Path(args.music) if args.music else None, args.no_audio)

    settings = Settings(sensitivity=args.sensitivity, teams=args.teams, team_count=args.team_count, min_players=args.min_players)
    tempo = TempoController(slow_speed=args.slow_speed, fast_speed=args.fast_speed)
    session = JoustSession(audio, settings, tempo)

    controllers = []
    sim_bus = None
    auto_players = []
    if args.sim:
        from .sim import AutoPlayer, SimBus, SimController

        sims = [SimController(f"sim-{i + 1}", seed=i) for i in range(args.sim)]
        sim_bus = SimBus(sims)
        sim_bus.start()
        auto_players = [AutoPlayer(s, seed=100 + i) for i, s in enumerate(sims)]
        controllers = sims
        for c in controllers:
            session.add_controller(c)
    else:
        from .psmove import Controller

        known: dict[str, Controller] = {}

        def scan() -> None:
            for d in enumerate_devices(args.backend):
                if not d.bluetooth and not args.allow_usb:
                    continue
                if d.label in known and known[d.label].connected:
                    continue
                try:
                    c = Controller(d)
                    c.start()
                except OSError as exc:
                    log.warning("cannot open %s: %s", d.path, exc)
                    continue
                if d.label in known:
                    session.remove_controller(d.label)
                known[d.label] = c
                session.add_controller(c)
                log.info("connected %s", c)

        scan()
        last_scan = time.monotonic()

    log.info("Joust %s ready. Sensitivity: %s. Pull the trigger to join; hold it 2s to force start.",
             __version__, SENSITIVITY_NAMES[settings.sensitivity])
    log.info("Lobby buttons: MOVE = cycle sensitivity, SQUARE = toggle teams, SELECT = show battery, CIRCLE/X = leave.")

    stop = False

    def _sig(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    period = 1.0 / args.tick_rate
    try:
        while not stop:
            now = time.monotonic()
            if args.sim:
                for ap in auto_players:
                    ap.update(now, session.phase.value)
            else:
                if now - last_scan > 2.0:
                    scan()
                    last_scan = now
            session.tick(now)
            spent = time.monotonic() - now
            if spent < period:
                time.sleep(period - spent)
    finally:
        if sim_bus:
            sim_bus.close()
        for c in controllers if args.sim else list(known.values()):
            try:
                c.set_leds(0, 0, 0)
                c.set_rumble(0)
                if hasattr(c, "close"):
                    c.close()
            except Exception:  # noqa: BLE001
                pass
        audio.close()
    return 0


def cmd_test_audio(args) -> int:
    audio = create_audio(enabled=True, device=args.audio_device)
    _load_music(audio, Path(args.music) if args.music else None, False)
    print("playing music for 12 s: normal, then fast, then slow ...")
    audio.play_music()
    for speed, secs in ((1.0, 4), (1.3, 4), (0.7, 4)):
        audio.set_speed(speed)
        time.sleep(secs)
    audio.stop_music()
    for name in ("sfx_join", "sfx_countdown_beep", "sfx_start", "sfx_explosion", "sfx_game_over", "sfx_victory"):
        print(name)
        audio.play_sfx(getattr(synth, name)(audio.rate))
        time.sleep(1.4)
    audio.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="joust", description="Headless, audio-only Johann Sebastian Joust clone for PS Move controllers.")
    p.add_argument("--version", action="version", version=f"joust {__version__}")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--backend", choices=["hidraw", "hidapi"], default=None, help="HID backend (default: auto)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("play", help="run the game")
    s.add_argument("--music", help="audio file or directory of files (wav/flac/ogg/mp3)")
    s.add_argument("--no-audio", action="store_true", help="run silent (logs only)")
    s.add_argument("--audio-device", default=None, help="sounddevice output device name or index")
    s.add_argument("--sensitivity", type=int, default=2, choices=range(5), help="0=ultra slow .. 4=ultra fast (default 2)")
    s.add_argument("--teams", action="store_true", help="start in team mode")
    s.add_argument("--team-count", type=int, default=2)
    s.add_argument("--min-players", type=int, default=2)
    s.add_argument("--slow-speed", type=float, default=1.0, help="music speed in the slow phase")
    s.add_argument("--fast-speed", type=float, default=1.3, help="music speed in the fast phase")
    s.add_argument("--tick-rate", type=float, default=200.0, help="game loop Hz")
    s.add_argument("--allow-usb", action="store_true", help="also use controllers connected over USB")
    s.add_argument("--sim", type=int, default=0, metavar="N", help="play with N simulated controllers (no hardware)")
    s.set_defaults(func=cmd_play)

    s = sub.add_parser("list", help="list connected controllers")
    s.add_argument("--probe", action="store_true", help="open each controller and read battery / accelerometer")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("pair", help="pair USB-connected controllers with this PC's bluetooth adapter (Linux, run as root)")
    s.add_argument("--host", help="bluetooth adapter address to pair with (default: first adapter)")
    s.add_argument("--no-register", action="store_true", help="only write the host address into the controller")
    s.set_defaults(func=cmd_pair)

    s = sub.add_parser("test-audio", help="play the music at a few speeds plus every sound effect")
    s.add_argument("--music")
    s.add_argument("--audio-device", default=None)
    s.set_defaults(func=cmd_test_audio)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
