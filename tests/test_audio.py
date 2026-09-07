import numpy as np

from joust.audio import synth
from joust.audio.engine import Mixer


def test_sfx_shapes():
    for fn in (synth.sfx_join, synth.sfx_explosion, synth.sfx_start, synth.sfx_victory, synth.sfx_game_over):
        s = fn(48000)
        assert s.ndim == 2 and s.shape[1] == 2
        assert s.dtype == np.float32
        assert np.max(np.abs(s)) <= 1.0
        assert np.max(np.abs(s)) > 0.05


def test_generated_music_loop():
    s = synth.generate_baroque_loop(bpm=200, passes=1, rate=8000)
    assert s.shape[1] == 2
    assert 8 < s.shape[0] / 8000 < 12  # 8 bars of 4 beats at 200 bpm = 9.6 s
    assert np.max(np.abs(s)) <= 0.71


def test_note_freq():
    assert abs(synth.note_freq("A4") - 440.0) < 1e-6
    assert abs(synth.note_freq("C#4") - 277.18) < 0.01
    assert abs(synth.note_freq("Bb3") - 233.08) < 0.01


def test_mixer_speed_changes_consumption():
    rate = 8000
    m = Mixer(rate)
    music = np.zeros((rate * 2, 2), dtype=np.float32)
    music[:, 0] = np.linspace(0, 1, rate * 2)
    m.set_music(music, rate)
    m.play_music()
    m.set_speed(1.0)
    for _ in range(20):
        m.render(256)
    pos_normal = m._pos
    m.set_speed(2.0)
    for _ in range(40):  # let the glide settle
        m.render(256)
    start = m._pos
    m.render(256)
    assert m._pos - start > 256 * 1.9
    assert pos_normal > 0


def test_mixer_sfx_and_silence():
    m = Mixer(8000)
    assert not np.any(m.render(64))
    m.play_sfx(np.ones((100, 2), dtype=np.float32) * 0.5)
    out = m.render(64)
    assert np.allclose(out, 0.5)
    out = m.render(64)
    assert np.allclose(out[:36], 0.5) and not np.any(out[36:])
    assert not np.any(m.render(64))


def test_whoosh_and_denied():
    w = synth.sfx_whoosh(48000)
    assert w.shape == (int(0.45 * 48000), 2)
    assert 0.5 < np.max(np.abs(w)) <= 0.8
    # energy peaks in the middle of the sweep
    thirds = np.array_split(np.abs(w[:, 0]), 3)
    assert thirds[1].mean() > thirds[0].mean() and thirds[1].mean() > thirds[2].mean()
    d = synth.sfx_denied(48000)
    assert d.shape[1] == 2 and np.max(np.abs(d)) > 0.1
