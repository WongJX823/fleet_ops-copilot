"""Agent orchestration: intent -> role-scoped tool selection -> retrieval ->
validation -> prompt packing -> LLM -> audited response.

This is the runtime half of the 'RAG + Tool Pipeline' diagram page.
"""
import json

from ..audit import record
from ..config import CONFIDENCE_ESCALATION_THRESHOLD, DEFAULT_ROLE, ROLE_TOOLS
from ..models import ChatResponse, Evidence
from ..rag.index import SopIndex
from ..tools import registry
from . import confidence as confidence_scoring
from .intent import IntentClassifier
from .llm import LLMClient
from .prompts import ANSWER_TEMPLATE


class Orchestrator:
    def __init__(self, sop_index: SopIndex):
        self.llm = LLMClient()
        self.classifier = IntentClassifier()
        self.tools = {
            "schedule_lookup": registry.schedule_lookup,
            "fleet_status": registry.fleet_status,
            "sop_search": registry.make_sop_search(sop_index),
        }

    def handle(
        self,
        question: str,
        role: str | None,
        images: list[tuple[str, bytes]],
        notes: list[str],
        history: list[dict] | None = None,
    ) -> ChatResponse:
        role = role if role in ROLE_TOOLS else DEFAULT_ROLE
        allowed = ROLE_TOOLS[role]
        intents, intent_mode = self.classifier.classify(question, history)

        evidence: list[Evidence] = []
        for tool_name in intents:
            if tool_name not in allowed:
                notes.append(f"Tool '{tool_name}' is not permitted for role '{role}'; skipped.")
                continue
            evidence.append(self.tools[tool_name](question, role))

        stale = [e.source for e in evidence if not e.fresh]
        score = confidence_scoring.score(evidence, intents, allowed, intent_mode)
        escalated = score < CONFIDENCE_ESCALATION_THRESHOLD
        if stale:
            notes.append(f"Evidence from {', '.join(stale)} exceeded the freshness limit.")
        if not evidence:
            notes.append("No permitted tool matched this question; escalate to a human operator.")
        elif escalated:
            notes.append(
                f"Confidence {score:.2f} is below the escalation threshold "
                f"({CONFIDENCE_ESCALATION_THRESHOLD}); recommend human review."
            )

        packed = ANSWER_TEMPLATE.format(
            role=role,
            question=question,
            evidence=json.dumps(
                [e.model_dump(mode="json") for e in evidence], indent=2, ensure_ascii=False
            ),
        )
        if notes:
            packed += "\n\nOperational notes (mention these to the user):\n- " + "\n- ".join(notes)

        answer = self.llm.answer(packed, images, history)

        response = ChatResponse(
            answer=answer,
            intent=intents,
            role=role,
            evidence=evidence,
            model=self.llm.model,
            confidence=score,
            escalated=escalated,
            notes=notes,
        )
        record(
            {
                "event": "chat_turn",
                "role": role,
                "question": question,
                "intents": intents,
                "intent_mode": intent_mode,
                "tools_used": [e.source for e in evidence],
                "stale_sources": stale,
                "image_count": len(images),
                "model": self.llm.model,
                "confidence": score,
                "escalated": escalated,
                "answer_preview": answer[:200],
            }
        )
        return response
