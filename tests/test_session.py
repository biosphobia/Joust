"""Drive whole rounds with simulated controllers and a fake clock."""
import pytest

from joust.audio.engine import NullAudio
from joust.game.session import (
    AUTO_START_DELAY,
    COUNTDOWN_STEP,
    DEATH_TO_GAME_OVER,
    NO_RUMBLE_AFTER_START,
    WINNER_CELEBRATION,
    JoustSession,
    Phase,
    Settings,
    Status,
)
from joust.game.tempo import TempoController
from joust.psmove.protocol import Button
from joust.sim import SimController


class World:
    def __init__(self, n=3, **settings):
        self.audio = NullAudio()
        self.events = []
        import random

        self.session = JoustSession(self.audio, Settings(**settings), TempoController(rng=random.Random(0)),
                                    on_event=lambda name, data: self.events.append((name, data)))
        self.sims = [SimController(f"sim-{i}", seed=i) for i in range(n)]
        for s in self.sims:
            self.session.add_controller(s)
        self.now = 100.0

    def run(self, seconds, hz=100):
        steps = int(seconds * hz)
        for _ in range(steps):
            self.now += 1.0 / hz
            for s in self.sims:
                s.emit_report()
            self.session.tick(self.now)

    def join_all(self):
        for s in self.sims:
            s.trigger(255)
        self.run(0.1)
        for s in self.sims:
            s.trigger(0)
        self.run(0.1)

    def names(self):
        return [e[0] for e in self.events]

    def start_round(self):
        self.join_all()
        assert self.session.phase is Phase.LOBBY
        self.run(AUTO_START_DELAY + 0.1)
        assert self.session.phase is Phase.COUNTDOWN
        self.run(COUNTDOWN_STEP * 3 + 0.1)
        assert self.session.phase is Phase.PLAYING
        self.run(NO_RUMBLE_AFTER_START + 0.1)


def test_join_and_auto_start():
    w = World(3)
    w.run(0.5)
    assert all(p.status is Status.IDLE for p in w.session.players.values())
    w.sims[0].trigger(255)
    w.run(0.1)
    assert w.session.players["sim-0"].status is Status.JOINED
    assert w.session.phase is Phase.LOBBY
    w.run(AUTO_START_DELAY + 1)
    assert w.session.phase is Phase.LOBBY  # only one joined, 2 idle -> no start
    w.sims[1].trigger(255)
    w.sims[2].trigger(255)
    w.run(0.1)
    assert w.session.phase is Phase.LOBBY
    w.run(AUTO_START_DELAY + 0.1)
    assert w.session.phase is Phase.COUNTDOWN
    assert "join" not in w.names()  # events are named player_joined
    assert w.names().count("player_joined") == 3
    colors = {p.color for p in w.session.joined}
    assert len(colors) == 3


def test_force_start_by_holding_trigger():
    w = World(3)
    w.sims[0].trigger(255)
    w.sims[1].trigger(255)
    w.run(0.2)
    for s in w.sims[:2]:
        s.trigger(0)
    w.run(0.2)
    assert w.session.phase is Phase.LOBBY
    w.sims[0].trigger(255)  # hold
    w.run(1.0)
    assert w.session.phase is Phase.LOBBY
    w.run(1.5)
    assert w.session.phase is Phase.COUNTDOWN
    assert "force_start" in w.names()
    assert w.session.players["sim-2"].status is Status.IDLE


def test_full_round_last_one_standing():
    w = World(3)
    w.start_round()
    assert len(w.session.alive) == 3
    assert w.audio.music_playing
    assert w.audio.speed == pytest.approx(1.0)

    # everyone still: nobody dies
    w.run(3.0)
    assert len(w.session.alive) == 3

    # a light nudge -> warning, survives
    w.sims[0].set_motion(1.65)
    w.run(0.3)
    w.sims[0].set_motion(1.0)
    w.run(1.0)
    assert w.session.players["sim-0"].status is Status.ALIVE
    assert "warning" in w.names()

    # a real shove -> out
    w.sims[1].set_motion(3.0)
    w.run(0.3)
    w.sims[1].set_motion(1.0)
    p1 = w.session.players["sim-1"]
    assert p1.status is Status.DEAD
    assert p1.controller.rumble > 0
    assert p1.controller.leds == w.session.settings.dead_led
    assert w.session.phase is Phase.PLAYING

    w.sims[2].set_motion(3.0)
    w.run(0.3)
    w.sims[2].set_motion(1.0)
    assert w.session.phase is Phase.GAME_OVER
    assert w.session.winners == ["sim-0"]
    assert w.audio.music_playing
    w.run(DEATH_TO_GAME_OVER + 0.1)
    assert not w.audio.music_playing
    w.run(WINNER_CELEBRATION + 0.5)
    assert w.session.phase is Phase.LOBBY
    assert all(p.status is Status.IDLE for p in w.session.players.values())
    assert w.session.rounds_played == 1
    out = [d for n, d in w.events if n == "player_out"]
    assert [o["serial"] for o in out] == ["sim-1", "sim-2"]


def test_death_threshold_follows_music_speed():
    w = World(2)
    w.start_round()
    # Force the fast phase and check a medium shove (2.2g) survives at fast
    # speed but dies at slow speed for the default (medium) sensitivity.
    w.session.tempo.fast = True
    w.session.tempo._phase_started = w.now - 10
    w.session.tempo._next_change = w.now + 1000
    w.run(0.5)
    assert w.session.tempo.speed_percent == pytest.approx(1.0)
    w.sims[0].set_motion(2.2)
    w.run(0.5)
    w.sims[0].set_motion(1.0)
    w.run(1.0)
    assert w.session.players["sim-0"].status is Status.ALIVE

    w.session.tempo.fast = False
    w.session.tempo._phase_started = w.now - 10
    w.session.tempo._next_change = w.now + 1000
    w.run(0.5)
    assert w.session.tempo.speed_percent == pytest.approx(0.0)
    w.sims[0].set_motion(2.2)
    w.run(0.5)
    assert w.session.players["sim-0"].status is Status.DEAD


def test_grace_period_after_start():
    w = World(2)
    w.join_all()
    w.run(AUTO_START_DELAY + COUNTDOWN_STEP * 3 + 0.3)
    assert w.session.phase is Phase.PLAYING
    w.sims[0].set_motion(4.0)  # violent during grace
    w.run(NO_RUMBLE_AFTER_START - 0.5)
    assert w.session.players["sim-0"].status is Status.ALIVE
    w.run(1.0)
    assert w.session.players["sim-0"].status is Status.DEAD


def test_team_mode_win():
    w = World(4, teams=True)
    w.start_round()
    teams = {p.serial: p.team for p in w.session.joined}
    assert sorted(teams.values()) == [0, 0, 1, 1]
    team0 = [s for s, t in teams.items() if t == 0]
    for s in w.sims:
        if s.serial in team0:
            s.set_motion(3.5)
    w.run(0.4)
    assert w.session.phase is Phase.GAME_OVER
    assert set(w.session.winners) == {s for s, t in teams.items() if t == 1}


def test_lobby_menu_buttons():
    w = World(2)
    w.run(0.2)
    before = w.session.settings.sensitivity
    w.sims[0].press(Button.MOVE)
    w.run(0.05)
    w.sims[0].release(Button.MOVE)
    w.run(0.05)
    assert w.session.settings.sensitivity == (before + 1) % 5
    w.sims[0].press(Button.SQUARE)
    w.run(0.05)
    w.sims[0].release(Button.SQUARE)
    w.run(0.05)
    assert w.session.settings.teams is True
    # leaving the lobby
    w.sims[0].trigger(255)
    w.run(0.1)
    w.sims[0].trigger(0)
    w.run(0.1)
    assert w.session.players["sim-0"].status is Status.JOINED
    w.sims[0].press(Button.CIRCLE)
    w.run(0.05)
    assert w.session.players["sim-0"].status is Status.IDLE
    assert "player_left" in w.names()


def test_disconnect_counts_as_out():
    w = World(3)
    w.start_round()
    w.sims[1].connected = False
    w.run(0.1)
    out = [d for n, d in w.events if n == "player_out"]
    assert out and out[0]["reason"] == "disconnected"
    assert len(w.session.alive) == 2
