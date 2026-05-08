import os
import shutil
import signal
import subprocess
from pathlib import Path


class AudioPlayer:
    """Simple track player for Raspberry Pi using mpg123 and ALSA output."""

    def __init__(self, audio_dir="audio", volume=25):
        self.D = None
        self.audio_dir = Path(audio_dir)
        self._proc = None
        self._volume = 15
        self._mpg123 = shutil.which("mpg123")
        self.set_volume(volume)

    def _resolve_track_file(self, track_id):
        if not self.audio_dir.exists():
            return None

        track_num = int(track_id)
        candidates = [
            f"{track_num}.mp3",
            f"{track_num:02d}.mp3",
            f"{track_num:03d}.mp3",
            f"track_{track_num}.mp3",
            f"{track_num}.wav",
            f"{track_num:02d}.wav",
            f"{track_num:03d}.wav",
            f"track_{track_num}.wav",
        ]
        for name in candidates:
            candidate = self.audio_dir / name
            if candidate.exists():
                return candidate

        stem_prefixes = {str(track_num), f"{track_num:02d}", f"{track_num:03d}", f"track_{track_num}"}
        for file_path in sorted(self.audio_dir.iterdir()):
            if file_path.suffix.lower() not in (".mp3", ".wav"):
                continue
            stem = file_path.stem.lower()
            if any(stem.startswith(prefix) for prefix in stem_prefixes):
                return file_path
        return None

    def _set_system_volume(self):
        percent = int(round((self._volume / 30.0) * 100))
        for control in ("PCM", "Master"):
            try:
                subprocess.run(
                    ["amixer", "sset", control, f"{percent}%"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
            except Exception:
                pass

    def _spawn(self, track_path, skip_frames=0):
        if not self._mpg123:
            raise RuntimeError("mpg123 is not installed. Install it before running this app.")

        gain = int(500 + (self._volume / 30.0) * 3000)
        cmd = [self._mpg123, "-q", "-f", str(gain)]
        if skip_frames > 0:
            cmd += ["-k", str(skip_frames)]
        cmd.append(str(track_path))
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def play_track(self, track_id=1, start_ms=0):
        self.stop()
        track = self._resolve_track_file(track_id)
        if not track:
            print(f"Audio track {track_id} not found in {self.audio_dir.resolve()}")
            return
        self._set_system_volume()
        # mpg123 seeks by MPEG frames; assumes 44100 Hz / 1152 samples per frame (~38.28 fps)
        skip_frames = int(start_ms * 44100 // 1152000) if start_ms > 0 else 0
        self._spawn(track, skip_frames=skip_frames)

    def stop(self):
        if not self._proc:
            return
        if self._proc.poll() is None:
            try:
                os.killpg(self._proc.pid, signal.SIGTERM)
            except Exception:
                self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    os.killpg(self._proc.pid, signal.SIGKILL)
                except Exception:
                    self._proc.kill()
        self._proc = None

    def pause(self):
        if self._proc and self._proc.poll() is None:
            try:
                os.killpg(self._proc.pid, signal.SIGSTOP)
            except Exception:
                pass

    def resume(self):
        if self._proc and self._proc.poll() is None:
            try:
                os.killpg(self._proc.pid, signal.SIGCONT)
            except Exception:
                pass

    def set_volume(self, level):
        self._volume = max(0, min(30, int(level)))
        self._set_system_volume()

    def volume_up(self, step=1):
        self.set_volume(self._volume + int(step))

    def volume_down(self, step=1):
        self.set_volume(self._volume - int(step))

    def mute(self):
        self.set_volume(0)

    def unmute(self):
        if self._volume == 0:
            self.set_volume(15)

    @property
    def volume(self):
        return self._volume
