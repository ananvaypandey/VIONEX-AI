"""VIONEX AI - local voice chatbot that drives the VIONEX servo robot.

Pipeline (half-duplex):
    mic -> VAD -> faster-whisper -> Ollama -> Piper TTS (+ robot gestures)

Run:
    python main.py                 # voice mode (mic + speakers); robot per config
    python main.py --mock          # no hardware; just log robot commands
    python main.py --serial        # force the real Arduino (see PROTOCOL.md)
    python main.py --list-devices  # show audio devices and exit
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time

import yaml
from colorama import Fore, Style, init as colorama_init

# Windows consoles default to cp1252, which cannot print Hindi (Devanagari).
# Force UTF-8 output so replies in any language print without crashing.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from ai.llm import Brain
from ai.tts import Speaker
from robot.base import Anim, RobotLink

C = Fore
R = Style.RESET_ALL

# Live event fan-out (used by the optional web UI). Safe no-op if unused.
_EMITTERS: list = []


def emit(event: dict) -> None:
    for sink in list(_EMITTERS):
        try:
            sink(event)
        except Exception:
            pass


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def banner() -> None:
    print(
        f"{C.CYAN}"
        "=====================================\n"
        "        VIONEX AI  -  voice chatbot\n"
        "====================================="
        f"{R}"
    )


def handle_turn(
    user_text: str,
    lang: str | None,
    brain: Brain,
    speaker: Speaker,
    robot: RobotLink,
    conv: dict,
    listener=None,
    barge_cfg: dict | None = None,
) -> tuple[float, float, float, object]:
    """Think, speak, and stay interruptible.

    Returns (llm_s, voice_s, gesture_s, interrupted_audio). When the user talks
    over the reply, playback stops and `interrupted_audio` holds their speech so
    the caller can answer it immediately without waiting for a new turn.
    """
    print(f"{C.YELLOW}You:{R} {user_text}")

    emit({"type": "status", "state": "thinking"})
    t0 = time.time()
    reply = brain.think(user_text, lang)
    llm_s = time.time() - t0
    print(f"{C.GREEN}VIONEX:{R} {reply.text}   {C.CYAN}(anim: {reply.anim}){R}")
    emit({"type": "reply", "text": reply.text, "anim": reply.anim})

    # Start the talking gesture the instant the spoken reply begins.
    emit({"type": "status", "state": "speaking"})
    if conv.get("play_talk_while_speaking", True):
        robot.play(Anim.TALK)

    barge_cfg = barge_cfg or {}
    barge_on = bool(barge_cfg.get("enabled", True)) and listener is not None

    interrupted = None
    t1 = time.time()
    if barge_on:
        # Speak on a worker thread so this thread can keep an ear on the mic.
        speaker.reset()
        player = threading.Thread(
            target=speaker.speak, args=(reply.text, lang), daemon=True
        )
        player.start()
        interrupted = listener.monitor(player.is_alive, barge_cfg)
        if interrupted is not None:
            speaker.stop()          # cut the voice off mid-sentence
            player.join(timeout=1.0)
            robot.stop()
        else:
            player.join()
    else:
        speaker.speak(reply.text, lang)
    voice_s = time.time() - t1

    gesture_s = 0.0
    if interrupted is None and reply.anim in Anim.ONE_SHOTS:
        # Sentence finished: stop talking, then play the one-shot gesture once.
        robot.stop()
        robot.play(reply.anim)
        gesture_s = float(conv.get("gesture_wait_s", 3.0))
        time.sleep(gesture_s)
    else:
        robot.stop()

    robot.idle()
    emit({"type": "status", "state": "listening"})
    return llm_s, voice_s, gesture_s, interrupted


def run_voice_mode(
    brain: Brain,
    speaker: Speaker,
    robot: RobotLink,
    conv: dict,
    stt_cfg: dict,
    vad_cfg: dict,
    audio_cfg: dict,
    barge_cfg: dict | None = None,
) -> None:
    from ai.audio import MicStream
    from ai.stt import Transcriber
    from ai.vad import VoiceListener

    transcriber = Transcriber(stt_cfg)
    barge_cfg = barge_cfg or {}

    with MicStream(
        sample_rate=int(audio_cfg.get("sample_rate", 16000)),
        channels=int(audio_cfg.get("channels", 1)),
        frame_ms=int(vad_cfg.get("frame_ms", 20)),
        device=audio_cfg.get("input_device"),
    ) as mic:
        listener = VoiceListener(mic, vad_cfg)
        print(f"{C.CYAN}Listening... (speak now, Ctrl+C to quit){R}")
        emit({"type": "status", "state": "listening"})

        pending = None  # speech captured while VIONEX was talking (barge-in)
        try:
            while True:
                audio = pending if pending is not None else listener.listen_once()
                pending = None

                t0 = time.time()
                transcript = transcriber.transcribe(audio)
                stt_s = time.time() - t0
                if not transcript.text:
                    continue
                print(
                    f"{C.BLUE}[stt]{R} lang={transcript.language} "
                    f"p={transcript.probability:.2f}"
                )
                emit({
                    "type": "you",
                    "text": transcript.text,
                    "lang": transcript.language,
                })
                llm_s, voice_s, gesture_s, interrupted = handle_turn(
                    transcript.text,
                    transcript.language,
                    brain,
                    speaker,
                    robot,
                    conv,
                    listener,
                    barge_cfg,
                )
                print(
                    f"{C.MAGENTA}[timing]{R} stt={stt_s:.2f}s llm={llm_s:.2f}s "
                    f"voice={voice_s:.2f}s gesture={gesture_s:.1f}s"
                )
                emit({
                    "type": "timing",
                    "stt": round(stt_s, 2),
                    "llm": round(llm_s, 2),
                    "voice": round(voice_s, 2),
                    "gesture": round(gesture_s, 1),
                })

                if interrupted is not None:
                    print(f"{C.YELLOW}[barge-in] you interrupted - answering that now{R}")
                    pending = interrupted
                else:
                    print(f"{C.CYAN}Listening...{R}")
        except KeyboardInterrupt:
            print()


def main() -> int:
    parser = argparse.ArgumentParser(description="VIONEX AI voice chatbot")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--serial", action="store_true", help="use the real Arduino")
    parser.add_argument("--mock", action="store_true", help="no hardware; just log robot commands")
    parser.add_argument("--port", default=None, help="serial port, e.g. COM5")
    parser.add_argument("--list-devices", action="store_true")
    args = parser.parse_args()

    colorama_init(autoreset=False)

    if args.list_devices:
        from ai.audio import list_devices

        print(list_devices())
        return 0

    cfg = load_config(args.config)
    # Voice models are resolved relative to the config file's folder.
    cfg.setdefault("tts", {})["base_dir"] = os.path.dirname(os.path.abspath(args.config))

    if args.serial:
        cfg["robot"]["mode"] = "serial"
    if args.mock:
        cfg["robot"]["mode"] = "mock"
    if args.port:
        cfg["robot"]["port"] = args.port

    banner()

    robot = None
    try:
        try:
            robot = RobotLink.from_config(cfg["robot"])
        except Exception as exc:
            # No board connected / port busy -> keep going without hardware.
            print(f"{C.YELLOW}[robot] {exc}{R}")
            print(f"{C.YELLOW}[robot] falling back to mock mode (no hardware){R}")
            cfg["robot"]["mode"] = "mock"
            robot = RobotLink.from_config(cfg["robot"])
        robot.idle()

        brain = Brain(cfg["ollama"], cfg["conversation"].get("max_history_turns", 10))
        speaker = Speaker(cfg["tts"])
        conv = cfg.get("conversation", {})

        print(f"{C.CYAN}[warmup] loading the model into RAM...{R}")
        brain.warmup()

        run_voice_mode(
            brain,
            speaker,
            robot,
            conv,
            cfg["stt"],
            cfg["vad"],
            cfg["audio"],
            cfg.get("barge_in", {}),
        )
    except KeyboardInterrupt:
        print(f"\n{C.CYAN}Bye!{R}")
    except Exception as exc:
        print(f"{C.RED}Error: {exc}{R}")
        return 1
    finally:
        if robot is not None:
            robot.idle()
            robot.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
