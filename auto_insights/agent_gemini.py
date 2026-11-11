"""Same agent, backed by Google Gemini (has a free tier: aistudio.google.com).

It reuses the tools, system prompt and dispatch of the Claude agent, so the
two providers run the identical analysis loop; only the API calls differ.
"""
from __future__ import annotations

import json
import os
from typing import Callable

from google import genai
from google.genai import types

from .agent import MAX_TURNS, SYSTEM_PROMPT, TOOLS, _dispatch, _short, narrative_from, user_prompt
from .data_tools import AnalysisSession
from .report import Narrative

DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")


def _to_json_safe(output: dict) -> dict:
    text = json.dumps(output, default=str)
    if len(text) > 20000:
        return {"result_truncated_json": text[:20000]}
    return json.loads(text)


class GeminiInsightsAgent:
    def __init__(self, session: AnalysisSession, client: genai.Client | None = None,
                 model: str = DEFAULT_MODEL, api_key: str | None = None,
                 log: Callable[[str], None] = print):
        self.session = session
        self.client = client or genai.Client(api_key=api_key or os.getenv("GEMINI_API_KEY"))
        self.model = model
        self.log = log
        self.tool_calls: list[dict] = []
        self.config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t["name"], description=t["description"],
                                          parameters_json_schema=t["input_schema"])
                for t in TOOLS])],
            # We run the tools ourselves, exactly like the Claude loop.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

    def run(self, goal: str = "", context: str = "") -> Narrative:
        contents: list[types.Content] = [types.Content(
            role="user", parts=[types.Part(text=user_prompt(self.session, goal, context))])]

        for turn in range(1, MAX_TURNS + 1):
            response = self.client.models.generate_content(
                model=self.model, contents=contents, config=self.config)
            if not response.candidates or response.candidates[0].content is None:
                raise RuntimeError("Gemini returned no answer (the request may have been blocked).")
            # Keep the model's full turn (including thought signatures) in the history.
            contents.append(response.candidates[0].content)

            calls = response.function_calls or []
            if not calls:
                contents.append(types.Content(role="user", parts=[
                    types.Part(text="Please finish by calling write_report.")]))
                continue

            parts = []
            for call in calls:
                args = dict(call.args or {})
                self.tool_calls.append({"tool": call.name, "input": args})
                self.log(f"  [turn {turn}] {call.name}({_short(args)})")
                if call.name == "write_report":
                    return narrative_from(
                        args, f"{self.model} (agent, {len(self.tool_calls)} tool calls)")
                try:
                    result = {"result": _to_json_safe(_dispatch(self.session, call.name, args))}
                except Exception as exc:  # send the error back so the model can correct itself
                    result = {"error": str(exc)}
                parts.append(types.Part(function_response=types.FunctionResponse(
                    id=call.id, name=call.name, response=result)))
            contents.append(types.Content(role="user", parts=parts))

        raise RuntimeError(f"Agent did not finish within {MAX_TURNS} turns.")
