#!/usr/bin/env py
"""Live and file transcription with local faster-whisper (CPU, offline).

Sources
    live loopback   system audio (meeting output) via WASAPI loopback
    live mic        the default microphone
    file            an existing recording (wav/mp3/m4a/... via PyAV)

No cloud, no API keys: speech recognition runs locally on CPU (int8).
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from math import gcd
from pathlib import Path

import numpy as np

TARGET_RATE = 16_000
DEFAULT_MODEL = "small"
TRANSCRIPTS_DIR = Path("transcripts")
BACKENDS = ("soundcard", "pyaudio")


# --------------------------------------------------------------------------- #
# console helpers
# --------------------------------------------------------------------------- #

def setup_console() -> None:
    """Windows consoles default to cp1252 and mangle accents."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def eprint(*parts: object) -> None:
    print(*parts, file=sys.stderr, flush=True)


def stamp(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def source_tag(device: "Device") -> str:
    return "loopback" if device.loopback else "mic"


# --------------------------------------------------------------------------- #
# devices
# --------------------------------------------------------------------------- #

@dataclass
class Device:
    backend: str
    handle: str          # opaque selector understood by its own backend
    name: str
    samplerate: int
    channels: int
    loopback: bool = False
    default: bool = False

    def describe(self) -> str:
        kind = "loopback" if self.loopback else "mic"
        mark = "*" if self.default else " "
        return f"{mark} [{self.backend}] {kind:<8} {self.name}  ({self.samplerate} Hz, {self.channels} ch)"


def enumerate_backend(backend: str) -> list[Device]:
    return _enumerate_soundcard() if backend == "soundcard" else _enumerate_pyaudio()


def _enumerate_soundcard() -> list[Device]:
    import soundcard as sc

    try:
        default_loopback = sc.get_microphone(id=str(sc.default_speaker().name), include_loopback=True)
        default_loopback_id = str(default_loopback.id)
    except Exception:
        default_loopback_id = ""
    try:
        default_mic_id = str(sc.default_microphone().id)
    except Exception:
        default_mic_id = ""

    devices: list[Device] = []
    for mic in sc.all_microphones(include_loopback=True):
        handle = str(mic.id)
        devices.append(
            Device(
                backend="soundcard",
                handle=handle,
                name=mic.name,
                # soundcard does not expose the endpoint mix rate; WASAPI shared mode is 48 kHz.
                samplerate=48_000,
                channels=2,
                loopback=bool(mic.isloopback),
                default=handle in (default_loopback_id, default_mic_id),
            )
        )
    return devices


def _enumerate_pyaudio() -> list[Device]:
    import pyaudiowpatch as pa

    api = pa.PyAudio()
    try:
        wasapi = api.get_host_api_info_by_type(pa.paWASAPI)
        try:
            default_loopback = api.get_default_wasapi_loopback()["index"]
        except Exception:
            default_loopback = -1
        default_mic = wasapi["defaultInputDevice"]

        devices: list[Device] = []
        for index in range(api.get_device_count()):
            info = api.get_device_info_by_index(index)
            # WASAPI only: it is the sole host API that can expose a loopback capture.
            if info["hostApi"] != wasapi["index"] or info["maxInputChannels"] <= 0:
                continue
            loopback = bool(info.get("isLoopbackDevice", False))
            devices.append(
                Device(
                    backend="pyaudio",
                    handle=str(index),
                    name=info["name"],
                    samplerate=int(info["defaultSampleRate"]),
                    channels=min(2, int(info["maxInputChannels"])),
                    loopback=loopback,
                    default=index == (default_loopback if loopback else default_mic),
                )
            )
        return devices
    finally:
        api.terminate()


def pick_device(devices: list[Device], source: str, selector: str | None) -> Device:
    """Resolve --device against the enumerated list; an explicit handle beats --source."""
    if selector:
        wanted = selector.strip().lower()
        for attribute in (lambda d: d.handle.lower(), lambda d: d.name.lower()):
            for device in devices:
                if attribute(device) == wanted:
                    return device
        matches = [d for d in devices if wanted in d.name.lower()]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise LookupError(f"no device matches {selector!r} (run --list)")
        listing = "\n".join(f"  - {m.handle}  {m.name}" for m in matches)
        raise LookupError(f"{selector!r} is ambiguous, use the handle instead:\n{listing}")

    candidates = [d for d in devices if d.loopback == (source == "loopback")]
    if not candidates:
        raise LookupError(f"no {source} capture device available (run --list)")
    preferred = [d for d in candidates if d.default]
    return (preferred or candidates)[0]


# --------------------------------------------------------------------------- #
# recorders
# --------------------------------------------------------------------------- #

@dataclass
class SoundcardRecorder:
    device: Device
    samplerate: int
    channels: int
    _rec: object = None

    def open(self) -> "SoundcardRecorder":
        import soundcard as sc

        mic = sc.get_microphone(id=self.device.handle, include_loopback=True)
        self._rec = mic.recorder(samplerate=self.samplerate, channels=self.channels, blocksize=1024)
        self._rec.__enter__()
        return self

    def read(self, frames: int) -> np.ndarray:
        block = self._rec.record(numframes=frames)
        return to_mono(block)

    def close(self) -> None:
        if self._rec is not None:
            try:
                self._rec.__exit__(None, None, None)
            except Exception:
                pass
            self._rec = None


@dataclass
class PyAudioRecorder:
    device: Device
    samplerate: int
    channels: int
    _api: object = None
    _stream: object = None

    def open(self) -> "PyAudioRecorder":
        import pyaudiowpatch as pa

        self._api = pa.PyAudio()
        self._stream = self._api.open(
            format=pa.paFloat32,
            channels=self.channels,
            rate=self.samplerate,
            input=True,
            input_device_index=int(self.device.handle),
            frames_per_buffer=1024,
        )
        return self

    def read(self, frames: int) -> np.ndarray:
        raw = self._stream.read(frames, exception_on_overflow=False)
        block = np.frombuffer(raw, dtype=np.float32).reshape(-1, self.channels)
        return to_mono(block)

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._api is not None:
            try:
                self._api.terminate()
            except Exception:
                pass
            self._api = None


def to_mono(block: np.ndarray) -> np.ndarray:
    block = np.asarray(block, dtype=np.float32)
    if block.ndim == 2 and block.shape[1] > 1:
        block = block.mean(axis=1, dtype=np.float32)
    return np.ascontiguousarray(block.reshape(-1), dtype=np.float32)


def open_recorder(
    source: str, selector: str | None, backend: str, rate: int | None, channels: int | None
) -> tuple[object, Device]:
    """Open a capture stream, trying every allowed backend and reporting every failure."""
    failures: list[str] = []
    for candidate in (BACKENDS if backend == "auto" else (backend,)):
        try:
            devices = enumerate_backend(candidate)
            device = pick_device(devices, source, selector)
            recorder = (
                SoundcardRecorder(device, rate or device.samplerate, channels or device.channels)
                if candidate == "soundcard"
                else PyAudioRecorder(device, rate or device.samplerate, channels or device.channels)
            ).open()
            return recorder, device
        except Exception as exc:  # noqa: BLE001 - every backend failure is reported to the user
            failures.append(f"  {candidate}: {type(exc).__name__}: {exc}")
    raise RuntimeError("no capture device could be opened:\n" + "\n".join(failures))


# --------------------------------------------------------------------------- #
# audio -> text
# --------------------------------------------------------------------------- #

def resample(audio: np.ndarray, samplerate: int) -> np.ndarray:
    """Convert to the 16 kHz mono float32 that faster-whisper requires."""
    audio = np.ascontiguousarray(audio, dtype=np.float32)
    if samplerate == TARGET_RATE:
        return audio
    try:
        from scipy.signal import resample_poly

        step = gcd(int(samplerate), TARGET_RATE)
        return resample_poly(audio, TARGET_RATE // step, int(samplerate) // step).astype(np.float32)
    except ImportError:
        count = max(1, int(round(len(audio) * TARGET_RATE / samplerate)))
        source_x = np.linspace(0.0, 1.0, len(audio), endpoint=False)
        target_x = np.linspace(0.0, 1.0, count, endpoint=False)
        return np.interp(target_x, source_x, audio).astype(np.float32)


def load_model(name: str):
    from faster_whisper import WhisperModel

    eprint(f"loading model {name!r} (cpu/int8)...")
    started = time.monotonic()
    model = WhisperModel(name, device="cpu", compute_type="int8")
    eprint(f"model ready in {time.monotonic() - started:.1f}s")
    return model


def language_arg(lang: str) -> str | None:
    return None if lang.lower() in ("auto", "") else lang


def transcribe_array(model, audio: np.ndarray, samplerate: int, lang: str) -> str:
    """One short utterance; beam_size=1 keeps live latency low."""
    segments, _ = model.transcribe(
        resample(audio, samplerate),
        language=language_arg(lang),
        beam_size=1,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    return " ".join(segment.text.strip() for segment in segments).strip()


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #

class TranscriptWriter:
    def __init__(self, path: Path, title: str, meta: list[tuple[str, str]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._file = path.open("w", encoding="utf-8", newline="\n")
        self._file.write(f"# {title}\n\n")
        for key, value in meta:
            self._file.write(f"- **{key}**: {value}\n")
        self._file.write("\n---\n\n")
        self._file.flush()

    def add(self, text: str, seconds: float) -> None:
        text = " ".join(text.split())
        if not text:
            return
        line = f"- **[{stamp(seconds)}]** {text}"
        self._file.write(line + "\n")
        self._file.flush()
        print(line, flush=True)

    def close(self) -> None:
        self._file.close()


def default_output(tag: str, suffix: str = "") -> Path:
    when = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return TRANSCRIPTS_DIR / f"{when}_{tag}{suffix}.md"


# --------------------------------------------------------------------------- #
# modes
# --------------------------------------------------------------------------- #

def run_list(backend: str) -> int:
    total = 0
    for candidate in (BACKENDS if backend == "auto" else (backend,)):
        try:
            devices = enumerate_backend(candidate)
        except Exception as exc:  # noqa: BLE001
            print(f"[{candidate}] unavailable: {type(exc).__name__}: {exc}")
            continue
        if not devices:
            print(f"[{candidate}] no input devices")
            continue
        print(f"[{candidate}]")
        for device in devices:
            print("  " + device.describe())
            print(f"      --device {device.handle}")
        total += len(devices)
    print(f"\n{total} capture device(s). '*' marks the system default.")
    return 0 if total else 1


def run_check(backend: str) -> int:
    """Open every input and read half a second, reporting the real OS error."""
    ok = failed = 0
    for candidate in (BACKENDS if backend == "auto" else (backend,)):
        try:
            devices = enumerate_backend(candidate)
        except Exception as exc:  # noqa: BLE001
            print(f"[{candidate}] enumeration failed: {type(exc).__name__}: {exc}")
            continue
        for device in devices:
            label = f"[{candidate}/{source_tag(device)}] {device.name}"
            recorder = None
            try:
                recorder = (
                    SoundcardRecorder(device, device.samplerate, device.channels)
                    if candidate == "soundcard"
                    else PyAudioRecorder(device, device.samplerate, device.channels)
                ).open()
                audio = recorder.read(min(device.samplerate // 2, 8192))
                peak = float(np.abs(audio).max()) if len(audio) else 0.0
                print(f"OK   {label}  peak={peak:.4f}")
                ok += 1
            except Exception as exc:  # noqa: BLE001
                print(f"FAIL {label}  {type(exc).__name__}: {exc}")
                failed += 1
            finally:
                if recorder is not None:
                    recorder.close()
    print(f"\n{ok} working, {failed} failing.")
    return 0 if ok else 1


def run_live(args: argparse.Namespace, model) -> int:
    recorder, device = open_recorder(args.source, args.device, args.backend, args.rate, args.channels)
    samplerate, channels = recorder.samplerate, recorder.channels
    output = Path(args.out) if args.out else default_output(args.source)

    writer = TranscriptWriter(
        output,
        f"Transcript en vivo — {dt.datetime.now():%Y-%m-%d %H:%M}",
        [
            ("Fuente", f"{source_tag(device)} · {device.name}"),
            ("Backend", f"{device.backend} · {samplerate} Hz · {channels} ch"),
            ("Modelo", f"{args.model} · {args.lang}"),
        ],
    )
    print(f"source : {source_tag(device)} · {device.name} ({samplerate} Hz, {channels} ch)")
    print(f"output : {output}")
    print("listening — Ctrl+C to stop\n")

    chunk = max(160, int(samplerate * 0.1))
    min_peak = max(args.threshold * 2, 0.002)
    started = time.monotonic()
    buffer: list[np.ndarray] = []
    buffered = silence = 0.0
    utterances = 0

    try:
        while True:
            block = recorder.read(chunk)
            if not len(block):
                continue
            duration = len(block) / samplerate
            buffer.append(block)
            buffered += duration
            silence = silence + duration if float(np.sqrt(np.mean(block**2))) < args.threshold else 0.0

            cut = (buffered >= args.min_utt and silence >= args.silence) or buffered >= args.max_utt
            if not cut:
                continue

            audio = np.concatenate(buffer) if buffer else np.empty(0, dtype=np.float32)
            buffer, buffered, silence = [], 0.0, 0.0
            if len(audio) == 0 or float(np.abs(audio).max()) < min_peak:
                continue

            utterances += 1
            try:
                text = transcribe_array(model, audio, samplerate, args.lang)
            except Exception as exc:  # noqa: BLE001 - keep capturing on a single bad block
                eprint(f"  transcription failed: {type(exc).__name__}: {exc}")
                continue
            if text:
                writer.add(text, time.monotonic() - started)
    except KeyboardInterrupt:
        eprint("\nstopped")
    finally:
        recorder.close()
        writer.close()

    print(f"\n{utterances} utterance(s) → {output}")
    return 0


def run_file(args: argparse.Namespace, model, path: Path) -> int:
    if not path.is_file():
        eprint(f"no such file: {path}")
        return 2
    output = Path(args.out) if args.out else default_output(path.stem, "_" + path.suffix.lstrip("."))
    writer = TranscriptWriter(
        output,
        f"Transcript — {path.name}",
        [("Archivo", str(path)), ("Modelo", f"{args.model} · {args.lang}")],
    )
    print(f"file   : {path}")
    print(f"output : {output}\n")

    segments, info = model.transcribe(
        str(path),
        language=language_arg(args.lang),
        beam_size=5,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    for segment in segments:
        writer.add(segment.text, segment.start)
    writer.close()
    print(f"\nduration {stamp(info.duration)} → {output}")
    return 0


def run_demo(args: argparse.Namespace, model) -> int:
    """Smoke-test the speech engine without any capture device (Windows SAPI voice)."""
    if os.name != "nt":
        eprint("--demo needs Windows SAPI")
        return 2
    phrase = "Esta es una prueba del transcriptor local. La reunion comienza a las tres de la tarde."
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "demo.wav"
        script = (
            "Add-Type -AssemblyName System.Speech;"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            "try { $s.SelectVoice('Microsoft Sabina Desktop') } catch {};"
            f"$s.SetOutputToWaveFile('{wav}');"
            f"$s.Speak('{phrase}');"
            "$s.Dispose()"
        )
        eprint("generating demo audio with SAPI...")
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            check=True,
            capture_output=True,
        )
        args.out = args.out or str(TRANSCRIPTS_DIR / "demo.md")
        return run_file(args, model, wav)


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="transcribe.py",
        description="Live and file transcription with local faster-whisper (CPU, offline).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  py transcribe.py --list                 # what can be captured here\n"
            "  py transcribe.py --check                # open each input, show the real error\n"
            "  py transcribe.py --demo                 # smoke-test the speech engine\n"
            "  py transcribe.py                        # live loopback -> transcripts/*.md\n"
            "  py transcribe.py --source mic           # live microphone\n"
            "  py transcribe.py --file reunion.m4a     # transcribe a recording\n"
        ),
    )
    parser.add_argument("--list", action="store_true", help="list capture devices and exit")
    parser.add_argument("--check", action="store_true", help="open every input and report failures")
    parser.add_argument("--demo", action="store_true", help="synthesize Spanish audio and transcribe it")
    parser.add_argument("--file", metavar="PATH", help="transcribe an existing audio file")
    parser.add_argument("--source", choices=("loopback", "mic"), default="loopback", help="live capture source")
    parser.add_argument("--device", help="device handle or name fragment from --list")
    parser.add_argument("--backend", choices=("auto",) + BACKENDS, default="auto", help="capture library")
    parser.add_argument("--rate", type=int, help="override capture sample rate")
    parser.add_argument("--channels", type=int, help="override capture channel count")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"whisper model (default {DEFAULT_MODEL})")
    parser.add_argument("--lang", default="es", help="language code, or 'auto' (default es)")
    parser.add_argument("--out", help="markdown output path")
    parser.add_argument("--silence", type=float, default=0.6, help="seconds of silence that close an utterance")
    parser.add_argument("--threshold", type=float, default=0.006, help="RMS gate below which audio counts as silence")
    parser.add_argument("--min-utt", type=float, default=0.8, help="minimum utterance length in seconds")
    parser.add_argument("--max-utt", type=float, default=25.0, help="force a cut after this many seconds")
    return parser


def main(argv: list[str] | None = None) -> int:
    setup_console()
    args = build_parser().parse_args(argv)

    if args.list:
        return run_list(args.backend)
    if args.check:
        return run_check(args.backend)

    model = load_model(args.model)
    try:
        if args.demo:
            return run_demo(args, model)
        if args.file:
            return run_file(args, model, Path(args.file))
        return run_live(args, model)
    except (RuntimeError, LookupError) as exc:
        eprint(f"error: {exc}")
        eprint("\nhint: run --check to see exactly which capture device fails and why.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
