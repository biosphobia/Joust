import random
from pathlib import Path

from joust.audio.loader import Playlist, find_music_files


def test_playlist_rotates_to_a_different_song(tmp_path: Path):
    for name in ("a.mp3", "b.ogg", "c.wav", "notes.txt"):
        (tmp_path / name).write_bytes(b"x")
    pl = Playlist(tmp_path, rng=random.Random(1))
    assert [f.name for f in find_music_files(tmp_path)] == ["a.mp3", "b.ogg", "c.wav"]
    picks = [pl.next() for _ in range(30)]
    assert all(p is not None for p in picks)
    assert all(a != b for a, b in zip(picks, picks[1:]))
    assert {p.name for p in picks} == {"a.mp3", "b.ogg", "c.wav"}


def test_playlist_single_song_and_empty(tmp_path: Path):
    assert Playlist(tmp_path).next() is None
    (tmp_path / "only.flac").write_bytes(b"x")
    pl = Playlist(tmp_path)
    assert pl.next().name == "only.flac"
    assert pl.next().name == "only.flac"
    (tmp_path / "new.flac").write_bytes(b"x")  # added while running
    assert pl.next().name == "new.flac"


def test_playlist_accepts_single_file(tmp_path: Path):
    f = tmp_path / "song.wav"
    f.write_bytes(b"x")
    assert Playlist(f).next() == f
