"""Intent classification: decide which governed tools a question needs.

Primary path: a small LLM call (temperature 0, strict JSON) that also sees the
user's recent questions so follow-ups like "and what about route 12?" route
correctly. Fallback path: keyword matching — used when no API key is set or
the call fails, which keeps the pipeline and tests fully offline-capable.
"""
import json

from ..config import OPENAI_API_KEY, OPENAI_CHAT_MODEL

TOOL_DESCRIPTIONS = {
    "schedule_lookup": "live trips, routes, departures, arrivals, delays, cancellations, schedule status",
    "fleet_status": "vehicles, buses, maintenance state, drivers, rosters, availability, replacements, standby cover",
    "sop_search": "standard operating procedures, policies, approved steps, rules, what is allowed, how to handle situations",
}

CLASSIFIER_SYSTEM = """You route transportation-operations questions to data tools.

Tools:
- schedule_lookup: """ + TOOL_DESCRIPTIONS["schedule_lookup"] + """
- fleet_status: """ + TOOL_DESCRIPTIONS["fleet_status"] + """
- sop_search: """ + TOOL_DESCRIPTIONS["sop_search"] + """

Reply with JSON only: {"tools": ["..."]}.
Pick every tool whose data is needed to answer well (often more than one).
Include sop_search whenever procedure or policy guidance would help.
Never return an empty list."""


class IntentClassifier:
    def __init__(self) -> None:
        self._client = None
        if OPENAI_API_KEY:
            from openai import OpenAI

            self._client = OpenAI()

    @property
    def mode(self) -> str:
        return "llm" if self._client else "keyword"

    def classify(self, question: str, history: list[dict] | None = None) -> tuple[list[str], str]:
        """Returns (tool_names, mode_used)."""
        if self._client is not None:
            try:
                tools = self._llm_classify(question, history or [])
                if tools:
                    return tools, "llm"
            except Exception:
                pass  # degrade to keywords rather than failing the request
        return keyword_classify(question), "keyword"

    def _llm_classify(self, question: str, history: list[dict]) -> list[str]:
        recent = [m["content"][:200] for m in history if m.get("role") == "user"][-2:]
        context = (
            "Earlier user questions (context for follow-ups):\n" + "\n".join(recent) + "\n\n"
            if recent
            else ""
        )
        resp = self._client.chat.completions.create(
            model=OPENAI_CHAT_MODEL,
            messages=[
                {"role": "system", "content": CLASSIFIER_SYSTEM},
                {"role": "user", "content": f"{context}Question: {question}\n\nWhich tools are needed?"},
            ],
            temperature=0,
            max_tokens=60,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        tools = [t for t in data.get("tools", []) if t in TOOL_DESCRIPTIONS]
        return list(dict.fromkeys(tools))


SCHEDULE_WORDS = {"trip", "trips", "route", "schedule", "depart", "departure", "delay", "delayed", "cancel", "cancelled", "next", "arrival", "late"}
FLEET_WORDS = {"vehicle", "vehicles", "bus", "driver", "drivers", "available", "availability", "replacement", "cover", "standby", "breakdown", "maintenance"}
SOP_WORDS = {"sop", "procedure", "policy", "approved", "steps", "step", "allowed", "rule", "rules", "should", "how", "what"}


def keyword_classify(question: str) -> list[str]:
    words = set(question.lower().split())
    intents = []
    if words & SCHEDULE_WORDS:
        intents.append("schedule_lookup")
    if words & FLEET_WORDS:
        intents.append("fleet_status")
    if words & SOP_WORDS or not intents:
        intents.append("sop_search")
    return intents
