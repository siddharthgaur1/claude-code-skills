"""A trivial reference agent so the harness runs with zero external deps.

It fakes a two-step tool-using agent: it "searches" then "answers". Replace
this with a real entrypoint into your LangGraph / function-calling agent.
The only contract that matters is the return shape.
"""

from __future__ import annotations


def run(input: dict) -> dict:
    prompt = input.get("prompt", "")
    trajectory = [{"tool": "search_web", "args": {"q": prompt}}]

    # Pretend the agent decided it needed to read a page for factual queries.
    if any(w in prompt.lower() for w in ("capital", "who", "when", "population")):
        trajectory.append({"tool": "read_page", "args": {"url": "https://example.com"}})

    if "capital of france" in prompt.lower():
        answer = "The capital of France is Paris."
    elif "2 + 2" in prompt or "2+2" in prompt:
        answer = "4"
        trajectory = []  # arithmetic shouldn't call tools
    else:
        answer = f"Here is a response about: {prompt}"

    return {"output": answer, "trajectory": trajectory, "tokens": 128}
