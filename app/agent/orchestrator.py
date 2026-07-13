"""Agent orchestration: intent -> role-scoped tool selection -> retrieval ->
validation -> prompt packing -> LLM -> audited response.

This is the runtime half of the 'RAG + Tool Pipeline' diagram page.
"""
import json

from ..audit import record
from ..config import DEFAULT_ROLE, ROLE_TOOLS
from ..models import ChatResponse, Evidence
from ..rag.index import SopIndex
from ..tools import registry
from .llm import LLMClient
from .prompts import ANSWER_TEMPLATE

SCHEDULE_WORDS = {"trip", "trips", "route", "schedule", "depart", "departure", "delay", "delayed", "cancel", "cancelled", "next", "arrival", "late"}
FLEET_WORDS = {"vehicle", "vehicles", "bus", "driver", "drivers", "available", "availability", "replacement", "cover", "standby", "breakdown", "maintenance"}
SOP_WORDS = {"sop", "procedure", "policy", "approved", "steps", "step", "allowed", "rule", "rules", "should", "how", "what"}


class Orchestrator:
    def __init__(self, sop_index: SopIndex):
        self.llm = LLMClient()
        self.tools = {
            "schedule_lookup": registry.schedule_lookup,
            "fleet_status": registry.fleet_status,
            "sop_search": registry.make_sop_search(sop_index),
        }

    def classify_intent(self, question: str) -> list[str]:
        words = set(question.lower().split())
        intents = []
        if words & SCHEDULE_WORDS:
            intents.append("schedule_lookup")
        if words & FLEET_WORDS:
            intents.append("fleet_status")
        if words & SOP_WORDS or not intents:
            intents.append("sop_search")
        return intents

    def handle(
        self,
        question: str,
        role: str | None,
        images: list[tuple[str, bytes]],
        notes: list[str],
    ) -> ChatResponse:
        role = role if role in ROLE_TOOLS else DEFAULT_ROLE
        allowed = ROLE_TOOLS[role]
        intents = self.classify_intent(question)

        evidence: list[Evidence] = []
        for tool_name in intents:
            if tool_name not in allowed:
                notes.append(f"Tool '{tool_name}' is not permitted for role '{role}'; skipped.")
                continue
            evidence.append(self.tools[tool_name](question, role))

        stale = [e.source for e in evidence if not e.fresh]
        escalated = not evidence or bool(stale)
        if stale:
            notes.append(f"Evidence from {', '.join(stale)} exceeded the freshness limit.")
        if not evidence:
            notes.append("No permitted tool matched this question; escalate to a human operator.")

        packed = ANSWER_TEMPLATE.format(
            role=role,
            question=question,
            evidence=json.dumps(
                [e.model_dump(mode="json") for e in evidence], indent=2, ensure_ascii=False
            ),
        )
        if notes:
            packed += "\n\nOperational notes (mention these to the user):\n- " + "\n- ".join(notes)

        answer = self.llm.answer(packed, images)

        response = ChatResponse(
            answer=answer,
            intent=intents,
            role=role,
            evidence=evidence,
            model=self.llm.model,
            escalated=escalated,
            notes=notes,
        )
        record(
            {
                "event": "chat_turn",
                "role": role,
                "question": question,
                "intents": intents,
                "tools_used": [e.source for e in evidence],
                "stale_sources": stale,
                "image_count": len(images),
                "model": self.llm.model,
                "escalated": escalated,
                "answer_preview": answer[:200],
            }
        )
        return response
