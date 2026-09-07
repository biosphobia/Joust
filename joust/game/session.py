"""The round state machine: lobby -> countdown -> playing -> game over.

Everything here is driven by ``tick(now)``; the wall clock is injected so
tests can run a whole round in milliseconds.  Controllers only need the tiny
duck-typed interface in ``ControllerLike``.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional, Protocol

from ..audio import synth
from ..audio.engine import AudioEngine
from ..psmove.protocol import AccelCalibration, Button, InputReport
from . import colors
from .motion import DEFAULT_SENSITIVITY, SENSITIVITY_NAMES, MotionTracker, RestReference, magnitude, thresholds_for
from .tempo import TempoController

log = logging.getLogger(__name__)


class ControllerLike(Protocol):
    serial: str
    connected: bool
    latest: Optional[InputReport]
    calibration: AccelCalibration

    def drain(self) -> list[InputReport]: ...
    def set_leds(self, r: int, g: int, b: int) -> None: ...
    def set_rumble(self, value: int) -> None: ...


class Phase(Enum):
    LOBBY = "lobby"
    COUNTDOWN = "countdown"
    PLAYING = "playing"
    GAME_OVER = "game_over"


class Status(Enum):
    IDLE = "idle"  # connected, not joined
    JOINED = "joined"
    ALIVE = "alive"
    DEAD = "dead"


# Timing constants (seconds)
JOIN_HOLD_TO_FORCE_START = 2.0
AUTO_START_DELAY = 3.0
COUNTDOWN_STEP = 0.75
NO_RUMBLE_AFTER_START = 2.0
WARNING_DURATION = 0.5
WARNING_COOLDOWN = 0.5
DEATH_RUMBLE = 4.0
DEATH_TO_GAME_OVER = 2.0
WINNER_CELEBRATION = 8.0
LOSER_FADE = 4.0
BATTERY_SHOW = 2.0
WARNING_RUMBLE = 230  # ~90%
DEATH_RUMBLE_STRENGTH = 230
FLICKER_PERIOD = 0.09
DODGE_SECONDS = 0.75
DODGE_COLOR = colors.WHITE
DODGE_RUMBLE = 120

BUTTON_NAMES = {
    "move": Button.MOVE,
    "square": Button.SQUARE,
    "triangle": Button.TRIANGLE,
    "circle": Button.CIRCLE,
    "cross": Button.CROSS,
    "select": Button.SELECT,
    "start": Button.START,
}


@dataclass
class Player:
    controller: ControllerLike
    status: Status = Status.IDLE
    color: colors.RGB = colors.DIM_WHITE
    team: int = 0
    tracker: MotionTracker = field(default_factory=MotionTracker)
    rest: RestReference = field(default_factory=RestReference)
    last_sequence: int = -1
    trigger_down_since: Optional[float] = None
    trigger_was_down: bool = False
    buttons_prev: int = 0
    warning_until: float = 0.0
    no_rumble_until: float = 0.0
    death_time: float = 0.0
    show_battery_until: float = 0.0
    last_leds: Optional[colors.RGB] = None
    last_rumble: int = -1
    died_at_change: float = 0.0
    kills_survived: int = 0
    dodge_used: bool = False
    dodge_until: float = 0.0
    dodge_landed: bool = True

    @property
    def serial(self) -> str:
        return self.controller.serial

    def leds(self, rgb: colors.RGB) -> None:
        if rgb != self.last_leds:
            self.controller.set_leds(*rgb)
            self.last_leds = rgb

    def rumble(self, value: int) -> None:
        if value != self.last_rumble:
            self.controller.set_rumble(value)
            self.last_rumble = value

    def just_pressed(self, button: Button) -> bool:
        return bool(self._pressed_edge & button)

    def dodging(self, now: float) -> bool:
        return now < self.dodge_until

    _pressed_edge: int = 0


@dataclass
class Settings:
    sensitivity: int = DEFAULT_SENSITIVITY
    teams: bool = False
    team_count: int = 2
    min_players: int = 2
    dead_led: colors.RGB = colors.DEAD_RED
    auto_start: bool = True
    dodge_button: Button = Button.MOVE
    dodge_seconds: float = DODGE_SECONDS


class JoustSession:
    """Owns the players and runs the round logic.  Call ``tick`` often (>=60 Hz)."""

    def __init__(
        self,
        audio: AudioEngine,
        settings: Optional[Settings] = None,
        tempo: Optional[TempoController] = None,
        on_event: Optional[Callable[[str, dict], None]] = None,
    ):
        self.audio = audio
        self.settings = settings or Settings()
        self.tempo = tempo or TempoController()
        self.on_event = on_event
        self.players: dict[str, Player] = {}
        self.phase = Phase.LOBBY
        self.phase_started = 0.0
        self.round_started = 0.0
        self.countdown_step = 0
        self.auto_start_at: Optional[float] = None
        self.winners: list[str] = []
        self.rounds_played = 0
        self._sfx = {
            "join": synth.sfx_join(audio.rate),
            "leave": synth.sfx_leave(audio.rate),
            "beep": synth.sfx_countdown_beep(audio.rate),
            "start": synth.sfx_start(audio.rate),
            "tick": synth.sfx_menu_tick(audio.rate),
            "explosion": synth.sfx_explosion(audio.rate),
            "game_over": synth.sfx_game_over(audio.rate),
            "victory": synth.sfx_victory(audio.rate),
            "whoosh": synth.sfx_whoosh(audio.rate),
            "denied": synth.sfx_denied(audio.rate),
        }

    # -- plumbing ------------------------------------------------------------

    def emit(self, name: str, **data) -> None:
        log.info("%s %s", name, data if data else "")
        if self.on_event:
            self.on_event(name, data)

    def sfx(self, name: str) -> None:
        self.audio.play_sfx(self._sfx[name])

    def add_controller(self, controller: ControllerLike) -> Player:
        p = self.players.get(controller.serial)
        if p is None:
            p = Player(controller=controller)
            self.players[controller.serial] = p
            self.emit("controller_added", serial=controller.serial)
        return p

    def remove_controller(self, serial: str) -> None:
        p = self.players.pop(serial, None)
        if p is not None:
            self.emit("controller_removed", serial=serial)

    @property
    def joined(self) -> list[Player]:
        return [p for p in self.players.values() if p.status in (Status.JOINED, Status.ALIVE, Status.DEAD)]

    @property
    def alive(self) -> list[Player]:
        return [p for p in self.players.values() if p.status is Status.ALIVE]

    @property
    def dead(self) -> list[Player]:
        return [p for p in self.players.values() if p.status is Status.DEAD]

    # -- per-tick input processing ------------------------------------------------

    def _ingest(self, p: Player, now: float) -> list[float]:
        """Consume every report since the last tick; returns |a| samples in g.

        Draining (instead of peeking at the latest report) keeps the movement
        metric independent of the tick rate, which matters on Windows where
        sleep granularity is coarse.
        """
        reports = p.controller.drain()
        if not reports:
            p._pressed_edge = 0
            return []

        edge = 0
        mags: list[float] = []
        for rep in reports:
            edge |= rep.buttons & ~p.buttons_prev
            p.buttons_prev = rep.buttons
            trigger_down = rep.trigger > 100
            if trigger_down and not p.trigger_was_down:
                p.trigger_down_since = now
            if not trigger_down:
                p.trigger_down_since = None
            p.trigger_was_down = trigger_down
            mag = magnitude(p.controller.calibration.apply(rep.accel))
            if self.phase is Phase.LOBBY:
                p.rest.feed(mag)
            mags.append(mag * p.rest.scale)
        p._pressed_edge = edge
        p.last_sequence = reports[-1].sequence
        return mags

    # -- main entry ---------------------------------------------------------

    def tick(self, now: float) -> None:
        samples: dict[str, list[float]] = {}
        for serial, p in list(self.players.items()):
            samples[serial] = self._ingest(p, now)

        if self.phase is Phase.LOBBY:
            self._tick_lobby(now)
        elif self.phase is Phase.COUNTDOWN:
            self._tick_countdown(now)
        elif self.phase is Phase.PLAYING:
            self._tick_playing(now, samples)
        elif self.phase is Phase.GAME_OVER:
            self._tick_game_over(now)

    # -- lobby ---------------------------------------------------------------

    def _assign_colors(self) -> None:
        joined = self.joined
        if self.settings.teams:
            n = max(2, min(self.settings.team_count, len(colors.TEAM_COLORS)))
            for i, p in enumerate(joined):
                p.team = i % n
                p.color = colors.TEAM_COLORS[p.team]
        else:
            for i, p in enumerate(joined):
                p.team = i
                p.color = colors.PLAYER_COLORS[i % len(colors.PLAYER_COLORS)]

    def _tick_lobby(self, now: float) -> None:
        force_start = False
        for p in self.players.values():
            if not p.controller.connected:
                continue
            if p.status is Status.IDLE:
                if p.trigger_was_down:
                    p.status = Status.JOINED
                    self._assign_colors()
                    self.sfx("join")
                    self.emit("player_joined", serial=p.serial, players=len(self.joined))
                    p.trigger_down_since = None  # do not count the join press as a hold
                    p.rest.locked = True
            elif p.status is Status.JOINED:
                if p.just_pressed(Button.CIRCLE) or p.just_pressed(Button.CROSS):
                    p.status = Status.IDLE
                    p.color = colors.DIM_WHITE
                    self._assign_colors()
                    self.sfx("leave")
                    self.emit("player_left", serial=p.serial, players=len(self.joined))
                elif p.trigger_down_since is not None and now - p.trigger_down_since >= JOIN_HOLD_TO_FORCE_START:
                    force_start = True

            # Lobby menu buttons (anyone can use them)
            if p.just_pressed(Button.MOVE):
                self.settings.sensitivity = (self.settings.sensitivity + 1) % len(SENSITIVITY_NAMES)
                self.audio.play_sfx(synth.sfx_beeps(self.settings.sensitivity + 1, self.audio.rate))
                self.emit("sensitivity", level=self.settings.sensitivity, label=SENSITIVITY_NAMES[self.settings.sensitivity])
            if p.just_pressed(Button.SQUARE):
                self.settings.teams = not self.settings.teams
                self._assign_colors()
                self.audio.play_sfx(synth.sfx_team_mode(self.settings.teams, self.audio.rate))
                self.emit("teams", enabled=self.settings.teams)
            if p.just_pressed(Button.SELECT):
                p.show_battery_until = now + BATTERY_SHOW

        # LEDs
        breath = 0.35 + 0.25 * math.sin(now * 2.0)
        for p in self.players.values():
            if now < p.show_battery_until:
                p.leds(self._battery_color(p))
            elif p.status is Status.JOINED:
                p.leds(p.color)
            else:
                p.leds(colors.scale(colors.WHITE, breath))
            p.rumble(0)

        # Starting conditions
        joined = self.joined
        connected = [p for p in self.players.values() if p.controller.connected]
        enough = len(joined) >= self.settings.min_players
        everyone_in = enough and len(joined) == len(connected)
        if enough and force_start:
            self.emit("force_start")
            self._start_countdown(now)
            return
        if self.settings.auto_start and everyone_in:
            if self.auto_start_at is None:
                self.auto_start_at = now + AUTO_START_DELAY
            elif now >= self.auto_start_at:
                self._start_countdown(now)
                return
        else:
            self.auto_start_at = None

    def _battery_color(self, p: Player) -> colors.RGB:
        rep = p.controller.latest
        state = rep.battery_state if rep else None
        if state is None:
            return colors.DIM_WHITE
        return colors.BATTERY_COLORS.get(state.label, colors.DIM_WHITE)

    # -- countdown ------------------------------------------------------------

    def _start_countdown(self, now: float) -> None:
        for p in self.players.values():
            if p.status is Status.JOINED:
                p.tracker.reset()
        self._assign_colors()
        self.phase = Phase.COUNTDOWN
        self.phase_started = now
        self.countdown_step = -1
        self.auto_start_at = None
        self.emit("countdown", players=[p.serial for p in self.joined], teams=self.settings.teams,
                  sensitivity=SENSITIVITY_NAMES[self.settings.sensitivity])

    def _tick_countdown(self, now: float) -> None:
        step = int((now - self.phase_started) / COUNTDOWN_STEP)
        step_colors = [(80, 0, 0), (70, 100, 0), (0, 70, 0)]
        if step != self.countdown_step:
            self.countdown_step = step
            if step < 3:
                self.sfx("beep")
                for p in self.joined:
                    p.leds(step_colors[step])
            else:
                self._start_round(now)

    def _start_round(self, now: float) -> None:
        self.phase = Phase.PLAYING
        self.phase_started = now
        self.round_started = now
        self.tempo.reset(now)
        self.audio.set_speed(self.tempo.speed)
        for p in self.joined:
            p.status = Status.ALIVE
            p.tracker.reset()
            p.no_rumble_until = now + NO_RUMBLE_AFTER_START
            p.warning_until = 0.0
            p.dodge_used = False
            p.dodge_until = 0.0
            p.dodge_landed = True
            p.leds(p.color)
            p.rumble(0)
        for p in self.players.values():
            if p.status is Status.IDLE:
                p.leds(colors.BLACK)
        self.sfx("start")
        self.audio.play_music()
        self.emit("round_started", players=len(self.alive))

    # -- playing -----------------------------------------------------------------

    def _progress(self) -> float:
        joined = len(self.joined)
        denom = max(1, joined - 2)
        return min(1.0, len(self.dead) / denom)

    def _tick_playing(self, now: float, samples: dict[str, list[float]]) -> None:
        speed = self.tempo.update(now, self._progress())
        self.audio.set_speed(speed)
        th = thresholds_for(self.settings.sensitivity, self.tempo.speed_percent)

        for p in self.players.values():
            if p.status is Status.ALIVE:
                if not p.controller.connected:
                    self._kill(p, now, reason="disconnected")
                    continue
                if p.just_pressed(self.settings.dodge_button):
                    self._dodge(p, now)
                if p.dodging(now):
                    for mag in samples.get(p.serial, ()):
                        p.tracker.update(mag)  # keep the average warm, but nothing can hurt you
                    p.rumble(DODGE_RUMBLE)
                    p.leds(DODGE_COLOR)
                    continue
                if not p.dodge_landed:
                    # the dodge just expired: forget the motion that happened during it
                    p.tracker.reset()
                    p.dodge_landed = True
                    p.warning_until = 0.0
                died = False
                for mag in samples.get(p.serial, ()):
                    change = p.tracker.update(mag)
                    if change > th.death and now > p.no_rumble_until:
                        self._kill(p, now, reason="jostled", change=change, threshold=th.death)
                        died = True
                        break
                    if change > th.warning and now > p.no_rumble_until and now >= p.warning_until + WARNING_COOLDOWN:
                        p.warning_until = now + WARNING_DURATION
                        self.emit("warning", serial=p.serial, change=round(change, 2), threshold=round(th.warning, 2))
                if died:
                    continue
                if now < p.warning_until:
                    p.rumble(WARNING_RUMBLE)
                    flick = int(now / FLICKER_PERIOD) % 2 == 0
                    p.leds(colors.scale(colors.WHITE, 0.4) if flick else p.color)
                else:
                    p.rumble(0)
                    p.leds(p.color)
            elif p.status is Status.DEAD:
                if now - p.death_time < DEATH_RUMBLE:
                    p.rumble(DEATH_RUMBLE_STRENGTH)
                else:
                    p.rumble(0)
                p.leds(self.settings.dead_led)

        self._check_winner(now)

    def _dodge(self, p: Player, now: float) -> None:
        """Ninja dodge: one short burst of invulnerability per round."""
        if p.dodge_used:
            self.sfx("denied")
            self.emit("dodge_denied", serial=p.serial)
            return
        p.dodge_used = True
        p.dodge_until = now + self.settings.dodge_seconds
        p.dodge_landed = False
        p.warning_until = 0.0
        self.sfx("whoosh")
        self.emit("dodge", serial=p.serial, seconds=self.settings.dodge_seconds)

    def _kill(self, p: Player, now: float, reason: str, **extra) -> None:
        p.status = Status.DEAD
        p.death_time = now
        p.died_at_change = extra.get("change", 0.0)
        p.leds(self.settings.dead_led)
        p.rumble(DEATH_RUMBLE_STRENGTH)
        self.sfx("explosion")
        self.emit("player_out", serial=p.serial, reason=reason, alive=len(self.alive), **extra)

    def _check_winner(self, now: float) -> None:
        alive = self.alive
        teams = {p.team for p in alive}
        if len(teams) <= 1:
            self.winners = [p.serial for p in alive]
            self.phase = Phase.GAME_OVER
            self.phase_started = now
            self._game_over_stage = 0
            self.rounds_played += 1
            self.emit("round_over", winners=self.winners, team=(alive[0].team if alive else None))

    # -- game over -------------------------------------------------------------------

    def _tick_game_over(self, now: float) -> None:
        elapsed = now - self.phase_started
        stage = getattr(self, "_game_over_stage", 0)
        if stage == 0 and elapsed >= DEATH_TO_GAME_OVER:
            self.audio.stop_music()
            self.audio.set_speed(1.0)
            self.sfx("game_over")
            self._game_over_stage = 1
        if stage == 1 and elapsed >= DEATH_TO_GAME_OVER + 1.6:
            self.sfx("victory")
            self._game_over_stage = 2

        fade = max(0.1, 1.0 - elapsed / LOSER_FADE)
        for p in self.players.values():
            if p.serial in self.winners:
                p.leds(colors.hsv(elapsed * 0.6, 1.0, 1.0))
                p.rumble(0)
            elif p.status is Status.DEAD:
                if now - p.death_time < DEATH_RUMBLE:
                    p.rumble(DEATH_RUMBLE_STRENGTH)
                else:
                    p.rumble(0)
                p.leds(colors.scale(colors.RED, 0.4 * fade))
            else:
                p.leds(colors.BLACK)

        if elapsed >= DEATH_TO_GAME_OVER + WINNER_CELEBRATION:
            self._back_to_lobby(now)

    def _back_to_lobby(self, now: float) -> None:
        for p in self.players.values():
            p.status = Status.IDLE
            p.color = colors.DIM_WHITE
            p.tracker.reset()
            p.rumble(0)
            p.rest.reset()
        self.winners = []
        self.phase = Phase.LOBBY
        self.phase_started = now
        self.auto_start_at = None
        self.emit("lobby")
