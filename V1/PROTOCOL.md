# Robot Serial Protocol

This is the contract between the **AI brain** (this Python app) and the
**Arduino firmware** (`.ino`) you will write. Implement exactly these strings
on the Arduino side and the robot will "just work".

## Transport
- USB serial (CDC), **115200 baud**, 8N1.
- Messages are plain ASCII, **newline terminated** (`\n`).
- The PC sends one command per line. Uppercase keywords.

## PC -> Arduino (commands)
| Command        | Meaning                                                        |
|----------------|----------------------------------------------------------------|
| `IDLE`         | Go to / hold the rest (idle) pose. Safe default when nothing else. |
| `PLAY talk`    | Start the **looping** talking gesture. Keep cycling until stopped. |
| `PLAY bye`     | Play the one-shot good-bye wave, then return to idle.          |
| `PLAY hello`   | Play the one-shot greeting, then return to idle.               |
| `PLAY handshake` | Play the one-shot handshake, then return to idle.            |
| `PLAY walk`    | Play the walk animation (looping or one cycle), then idle.     |
| `STOP`         | Abort whatever is running and return to the idle pose.         |

- `PLAY <name>` is generic: adding new animations later just means adding a
  new name on the Arduino side. The PC only ever sends the names listed above
  (from `config.yaml` / the LLM intent).
- `STOP` must always be safe to call at any time.

## Arduino -> PC (optional but recommended)
| Message       | Meaning                                              |
|---------------|------------------------------------------------------|
| `READY`       | Sent once after `setup()` completes.                 |
| `DONE <name>` | Sent when a one-shot animation finishes.            |

These are optional; the app works without them, but they improve sync.

## Behaviour notes
- Animations should **not** block forever. Prefer a small state machine in
  `loop()` that advances the current animation one step per pass and keeps
  reading Serial, so `STOP` is honoured mid-animation.
- On boot: set the idle pose, then send `READY`.
- `PLAY talk` loops because the robot gestures the whole time it is speaking;
  the PC will send `STOP` (or `IDLE`) afterwards.

## Suggested .ino skeleton
```cpp
void loop() {
  readSerial();      // parses lines -> sets currentCommand / currentAnim
  updateAnimation(); // advances the active animation one small step, no long delays
}
```

## Name map (keep these exact)
`talk`, `bye`, `hello`, `handshake`, `walk`, `idle`.
