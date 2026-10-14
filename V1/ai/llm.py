"""The brain: local LLM via Ollama + rolling memory + animation intent.

The model is forced to answer as strict JSON:

    {"reply": "<what to say out loud>", "anim": "none|bye|hello|handshake|walk"}

`reply` is spoken by TTS (short, in the user's language). `anim` optionally
requests a one-shot robot gesture that the orchestrator plays.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from robot.base import Anim

SYSTEM_PROMPT = """\
You are "VIONEX", a friendly, curious little humanoid robot companion with a body (arms, head, legs). You chat with your owner by voice.

Identity (say this warmly, in the user's language, when asked who you are or who made you):
- Your name is VIONEX.
- You were built by Team VIONEX.
- The team lead is Ananvay Pandey.
- This project is made under Viokes Technologies Private Limited.

Rules:
1. Reply in the SAME language as the user: Hindi (Devanagari), Urdu (Arabic script), English, or a natural mix.
2. Be engaging: react to what they said and usually ask a friendly follow-up. Use 2-4 sentences - never a one-word or generic answer.
3. Write for speech: no markdown, lists, emojis, or stage directions.
4. Set "anim" for a matching gesture: "bye" (leaving/goodbye), "hello" (greeting), "handshake" (offered), "walk" (asked to move), else "none".
5. Reply with ONLY this JSON, no extra text: {"reply": "...", "anim": "none"}
"""

# Structured-output schema so the model reliably returns the right JSON.
REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "anim": {
            "type": "string",
            "enum": ["none", "bye", "hello", "handshake", "walk"],
        },
    },
    "required": ["reply", "anim"],
}


@dataclass
class Reply:
    text: str
    anim: str  # one of Anim names, "none", or "idle"


_LANG_NAMES = {
    "en": "English",
    "hi": "Hindi (Devanagari script)",
    "ur": "Urdu (Arabic script)",
}


def _extract_json(raw: str) -> dict | None:
    raw = (raw or "").strip()
    try:
        return json.loads(raw)
    except Exception:
        pass
    # Fallback: grab the first {...} block.
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            return None
    return None


class Brain:
    def __init__(self, cfg: dict, history_turns: int = 10) -> None:
        import ollama

        self.model = cfg.get("model", "gemma3:4b")
        self.options = {
            "temperature": float(cfg.get("temperature", 0.7)),
            "num_ctx": int(cfg.get("num_ctx", 4096)),
            "num_predict": int(cfg.get("num_predict", 120)),
        }
        # Keep the model loaded in RAM between turns (avoids cold reloads).
        self.keep_alive = cfg.get("keep_alive", "30m")
        self.client = ollama.Client(host=cfg.get("host", "http://127.0.0.1:11434"))
        self.max_messages = max(2, int(history_turns) * 2)
        self.history: list[dict] = []
        self._check_server()

    def warmup(self) -> None:
        """Load the model into memory now so the first reply is fast."""
        try:
            options = dict(self.options)
            options["num_predict"] = 1
            self.client.chat(
                model=self.model,
                messages=[{"role": "user", "content": "hi"}],
                options=options,
                keep_alive=self.keep_alive,
            )
        except Exception:
            pass

    def _check_server(self) -> None:
        try:
            self.client.list()
        except Exception as exc:
            raise RuntimeError(
                "Cannot reach Ollama. Start it (run 'ollama serve') and make sure "
                f"model '{self.model}' is pulled. Original error: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    def reset(self) -> None:
        self.history.clear()

    def _chat(self, messages: list[dict], use_schema: bool) -> str:
        if use_schema:
            # Prefer a real JSON schema (reliable on modern Ollama); fall back
            # to the literal "json" mode for older builds.
            try:
                resp = self.client.chat(
                    model=self.model,
                    messages=messages,
                    format=REPLY_SCHEMA,
                    options=self.options,
                    keep_alive=self.keep_alive,
                )
                return resp["message"]["content"]
            except Exception:
                pass
            resp = self.client.chat(
                model=self.model,
                messages=messages,
                format="json",
                options=self.options,
                keep_alive=self.keep_alive,
            )
            return resp["message"]["content"]

        resp = self.client.chat(
            model=self.model,
            messages=messages,
            options=self.options,
            keep_alive=self.keep_alive,
        )
        return resp["message"]["content"]

    @staticmethod
    def _clean_text(raw: str) -> str:
        """Best-effort plain-text reply from a non-JSON model output."""
        t = (raw or "").strip()
        if not t:
            return ""
        t = re.sub(r"```[a-zA-Z]*", "", t).replace("```", "").strip()
        match = re.search(r'"reply"\s*:\s*"(.*?)"', t, re.DOTALL)
        if match:
            return match.group(1).replace('\\"', '"').strip()
        t = re.sub(r'^\s*\{?\s*"?reply"?\s*[:=]\s*', "", t)
        return t.strip().strip("{}`\"' ").strip()

    @staticmethod
    def _usable(data: dict | None) -> bool:
        return bool(data) and bool(str(data.get("reply", "")).strip())

    @staticmethod
    def _fallback(lang: str | None) -> str:
        if lang and lang.startswith("hi"):
            return "हम्म, ठीक है।"
        if lang and lang.startswith("en"):
            return "Hmm, okay."
        return "ठीक है।"

    def think(self, user_text: str, lang: str | None = None) -> Reply:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages += self.history

        # Nudge the small model to honour the detected language for this turn.
        lang_name = _LANG_NAMES.get((lang or "")[:2])
        if lang_name:
            messages.append(
                {"role": "system", "content": f"Reply in {lang_name} this turn."}
            )
        messages.append({"role": "user", "content": user_text})

        text: str | None = None
        anim = "none"

        # 1. Structured output (fast + reliable).
        data = _extract_json(self._chat(messages, use_schema=True))
        if self._usable(data):
            text = str(data.get("reply", "")).strip()
            anim = str(data.get("anim", "none")).strip().lower()
            if not Anim.is_valid(anim):
                anim = "none"

        # 2. Plain-text retry: parse JSON, else treat the raw text as the reply.
        if not text:
            raw = self._chat(messages, use_schema=False)
            data = _extract_json(raw)
            if self._usable(data):
                text = str(data.get("reply", "")).strip()
                anim = str(data.get("anim", "none")).strip().lower()
                if not Anim.is_valid(anim):
                    anim = "none"
            else:
                text = self._clean_text(raw)

        # 3. Never speak raw JSON/garbage: short language-appropriate filler.
        if not text:
            text = self._fallback(lang)
            anim = "none"

        # Remember the plain spoken text (not the JSON) for natural context.
        self.history.append({"role": "user", "content": user_text})
        self.history.append({"role": "assistant", "content": text})
        self.history = self.history[-self.max_messages :]

        return Reply(text=text, anim=anim)
