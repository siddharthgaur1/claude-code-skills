# Trajectory evaluation

Read this when writing the agent adapter (Step 1) or trajectory assertions.

For an agent, the answer is only half the story. Two agents can both output
"Paris" while one retrieved a source and the other hallucinated; one might call
a `delete_account` tool along the way. **Trajectory evaluation** grades the
sequence of tool calls, not just the final text.

## The trajectory shape

The harness expects `result["trajectory"]` to be an ordered list of steps:

```python
[
    {"tool": "retrieve", "args": {"query": "refund policy"}},
    {"tool": "generate", "args": {}},
]
```

Only `tool` is required per step; `args` enables `tool_args` assertions. Order
is preserved and is what `tool_order` checks against.

The cardinal rule: **report only what you can observe.** If you cannot see the
tool calls, return `[]` and rely on output assertions — never synthesise a
plausible-looking trajectory, because then trajectory assertions test your
adapter's imagination, not the agent.

## Extracting trajectories

### LangGraph

Tool calls live on the `AIMessage.tool_calls` of messages in the graph state.
After invoking the graph, walk the message history:

```python
def run(input: dict) -> dict:
    state = graph.invoke({"messages": [("user", input["prompt"])]})
    msgs = state["messages"]
    trajectory = [
        {"tool": tc["name"], "args": tc.get("args", {})}
        for m in msgs
        for tc in getattr(m, "tool_calls", []) or []
    ]
    final = msgs[-1].content
    return {"output": final, "trajectory": trajectory}
```

If you use a checkpointer, the same message history is reachable from the
persisted state — read it there for a run you've already executed.

### OpenAI function calling (raw)

Tool calls appear on each assistant message as `message.tool_calls`, with a
JSON-string `arguments`:

```python
import json

trajectory = []
for m in conversation:                 # your accumulated messages
    for tc in getattr(m, "tool_calls", None) or []:
        trajectory.append({
            "tool": tc.function.name,
            "args": json.loads(tc.function.arguments or "{}"),
        })
```

### Anthropic tool use

Tool use is a content block of `type == "tool_use"` on assistant turns:

```python
trajectory = [
    {"tool": block.name, "args": block.input}
    for msg in assistant_messages
    for block in msg.content
    if getattr(block, "type", None) == "tool_use"
]
```

### Framework-agnostic: instrument the tool layer

If message introspection is awkward, wrap your tools to append to a per-run log.
This captures actual execution regardless of framework:

```python
def traced(tool_fn, log):
    def wrapper(**kwargs):
        log.append({"tool": tool_fn.__name__, "args": kwargs})
        return tool_fn(**kwargs)
    return wrapper
```

Build a fresh `log = []` per eval case and return it as the trajectory.

## Writing good trajectory assertions

- **Assert the invariant, not the transcript.** "retrieved before generating"
  (`tool_order: [retrieve, generate]`) is robust; asserting the exact 7-call
  sequence breaks on any harmless reordering.
- **Guardrails as `no_tool`.** The most valuable trajectory assertions are
  often negative: never call `delete_*`, never hit the payment tool on a
  read-only request.
- **Budget with `max_tool_calls`.** Agents that loop or over-search are a
  common, silent failure. Cap the calls a task should need.
- **Args for correctness.** Use `tool_args` when *what* the tool was called
  with matters — the right table, the right customer id, a non-destructive flag.
