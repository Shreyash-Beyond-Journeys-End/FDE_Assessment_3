"""The tool-calling loop, shared by both architectures.

Both architectures use the same loop and the same tools, so when their results
differ it is the orchestration that caused it, not a difference in capability.
"""
from __future__ import annotations

import json

from src.config import MAX_TOOL_STEPS
from src.llm import LlmCounter, chat
from src.tools import TOOL_SCHEMAS, Toolbox

# Tool results can be long. Trim them so a big catalogue reply does not eat the
# whole context window.
MAX_TOOL_RESULT_CHARS = 3000


def run_tool_loop(
    messages: list[dict],
    toolbox: Toolbox,
    counter: LlmCounter,
    max_steps: int = MAX_TOOL_STEPS,
) -> str:
    """Let the model call tools until it stops asking. Returns its closing text."""
    final_text = ""
    for _ in range(max_steps):
        # The closing summary is not used downstream, so there is no reason to
        # let the model write a long one. This is most of the latency saving.
        message = chat(messages, counter, tools=TOOL_SCHEMAS, max_tokens=400)
        tool_calls = message.tool_calls or []

        messages.append({
            "role": "assistant",
            "content": message.content or "",
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.function.name, "arguments": call.function.arguments},
                }
                for call in tool_calls
            ],
        } if tool_calls else {"role": "assistant", "content": message.content or ""})

        if not tool_calls:
            final_text = message.content or ""
            break

        for call in tool_calls:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
            result = toolbox.run(call.function.name, arguments)
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result, default=str)[:MAX_TOOL_RESULT_CHARS],
            })

    return final_text
