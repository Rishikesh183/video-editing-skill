"""Scene planning via a headless agent CLI on the creator's own subscription: Claude Code (`claude -p`, Pro/Max)
or OpenAI Codex (`codex exec`, ChatGPT plan). Both get the same system prompt and plan schema."""

import hashlib
import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from . import config, usage
from .models import Job, Plan
from .plan import normalize
from .srt import to_srt


class PlannerError(RuntimeError):
    pass


def system_prompt(style: str = "") -> str:
    """The editor brief plus the creator's standing preferences (planner/lessons.md), which win on conflict."""
    system = (config.PLANNER_DIR / "system.md").read_text(encoding="utf-8")
    lessons = config.PLANNER_DIR / "lessons.md"
    style_file = config.PLANNER_DIR / "styles" / f"{style}.md"
    if style and style_file.is_file():
        system += "\n\nEditing style for this video (follow it unless the creator's preferences say otherwise):\n"
        system += style_file.read_text(encoding="utf-8")
    if lessons.exists():
        system += "\n\n" + lessons.read_text(encoding="utf-8")
    return system


def remember(lesson: str) -> bool:
    """Append a creator correction to the standing preferences (skips exact repeats)."""
    lesson = " ".join(lesson.split())
    path = config.PLANNER_DIR / "lessons.md"
    current = path.read_text(encoding="utf-8") if path.exists() else "# Creator's standing preferences\n"
    if not lesson or lesson.lower() in current.lower():
        return False
    if "## Added from re-plan notes" not in current:
        current = current.rstrip() + "\n\n## Added from re-plan notes\n"
    path.write_text(current.rstrip() + f"\n- {lesson}\n", encoding="utf-8")
    return True


REVISE = """This is a REVISION of the creator's current plan (JSON below). Keep its scenes, templates, timings,
photos and wording unless something is wrong or the notes ask for a change. Fix: wrong or repeated photos
(each side/person gets their own photo), visuals that start before the subject is named or end while it is
still being discussed, text that is too long, and anything the notes mention. Output the FULL revised plan,
including unchanged scenes with their same ids, and update "story" with what you changed and why."""


def build_input(job: Job, library_slots: list[str], notes: str = "", current: Plan | None = None) -> str:
    parts = [f"Video duration: {job.video.duration_us / 1e6:.1f}s.", "Captions:", to_srt(job.captions)]
    if job.script.strip():
        parts += ["Creator's script (Telugu-English):", job.script.strip()]
    if library_slots:
        parts.append("Photos already in the library (reuse these exact slot names): " + ", ".join(sorted(library_slots)))
    if notes.strip():
        parts.append("Creator's notes for this plan: " + notes.strip())
    if current is not None and current.scenes:
        parts += [REVISE, "Current plan:", current.model_dump_json(exclude={"scenes": {"__all__": {"approved"}}})]
    return "\n\n".join(parts)


def run_claude(prompt_input: str, style: str = "") -> dict[str, Any]:
    system = system_prompt(style)
    schema = (config.PLANNER_DIR / "plan.schema.json").read_text(encoding="utf-8")
    cmd = [
        config.claude_executable(), "-p", "Think hard, like a senior cricket video editor, then plan the graphics for this video.",
        "--model", config.PLANNER_MODEL,
        "--tools", "",
        "--system-prompt", system,
        "--json-schema", schema,
        "--output-format", "json",
        "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence",
        "--setting-sources", "",
    ]
    # Run outside the project tree so no CLAUDE.md or project settings are loaded into the call.
    with tempfile.TemporaryDirectory(prefix="hf-plan-") as cwd:
        proc = subprocess.run(cmd, input=prompt_input, capture_output=True, text=True, encoding="utf-8", cwd=cwd, timeout=600)
    if proc.returncode != 0:
        raise PlannerError(f"claude exited {proc.returncode}: {(proc.stderr or proc.stdout)[-800:]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise PlannerError(f"claude returned non-JSON output: {proc.stdout[-400:]}") from e


def _codex_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """OpenAI strict structured output: every property required, no free-form objects.
    The per-template `data` object therefore travels as a JSON string (decoded in `_from_codex`)."""
    def strict(node: Any) -> Any:
        if isinstance(node, dict):
            node = {k: strict(v) for k, v in node.items()}
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"])
                node["additionalProperties"] = False
            return node
        return [strict(v) for v in node] if isinstance(node, list) else node

    out = strict(json.loads(json.dumps(schema)))
    out["properties"]["scenes"]["items"]["properties"]["data"] = {
        "type": "string", "description": "JSON object with the template's fields, e.g. {\"text\": \"...\"}"}
    return out


def _from_codex(payload: dict[str, Any]) -> dict[str, Any]:
    for scene in payload.get("scenes", []):
        raw = scene.get("data")
        if isinstance(raw, str):
            try:
                scene["data"] = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError as e:
                raise PlannerError(f"codex returned unreadable scene data: {raw[:200]}") from e
    return payload


def run_codex(prompt_input: str, style: str = "") -> dict[str, Any]:
    """`codex exec` on the ChatGPT login, read-only sandbox, answer constrained to the plan schema.
    Returns the same shape as run_claude's JSON so the rest of the pipeline is provider-agnostic."""
    system = system_prompt(style)
    schema = _codex_schema(json.loads((config.PLANNER_DIR / "plan.schema.json").read_text(encoding="utf-8")))
    prompt = "\n\n".join([system, "Do not run any commands or read files; answer only with the plan JSON.",
                          "Think hard, like a senior cricket video editor, then plan the graphics for this video.", prompt_input])
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="hf-codex-") as cwd:
        schema_file, answer = Path(cwd) / "schema.json", Path(cwd) / "answer.json"
        schema_file.write_text(json.dumps(schema), encoding="utf-8")
        cmd = [config.tool(config.CODEX_BIN), "exec", "--json", "--skip-git-repo-check", "--sandbox", "read-only",
               "-m", config.CODEX_MODEL, "-c", f'model_reasoning_effort="{config.CODEX_EFFORT}"',
               "--output-schema", str(schema_file), "-o", str(answer), "-"]
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", cwd=cwd, timeout=900)
        events = [json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")]
        failure = next((e for e in events if e.get("type") in ("turn.failed", "error")), None)
        if proc.returncode != 0 or failure or not answer.exists():
            detail = json.dumps(failure)[:600] if failure else (proc.stderr or proc.stdout)[-600:]
            raise PlannerError(f"codex failed: {detail}")
        text = answer.read_text(encoding="utf-8")
    used = next((e.get("usage", {}) for e in reversed(events) if e.get("type") == "turn.completed"), {})
    try:
        payload = _from_codex(json.loads(text))
    except json.JSONDecodeError as e:
        raise PlannerError(f"codex returned non-JSON output: {text[-400:]}") from e
    return {
        "provider": "codex",
        "structured_output": payload,
        "usage": {"input_tokens": used.get("input_tokens", 0) - used.get("cached_input_tokens", 0),
                  "output_tokens": used.get("output_tokens", 0),
                  "output_tokens_details": {"thinking_tokens": used.get("reasoning_output_tokens", 0)},
                  "cache_read_input_tokens": used.get("cached_input_tokens", 0)},
        "modelUsage": {config.CODEX_MODEL: {}},
        "total_cost_usd": 0.0,  # billed to the ChatGPT plan, no per-call price
        "duration_ms": round((time.monotonic() - started) * 1000),
    }


def parse_plan(result: dict[str, Any]) -> Plan:
    if result.get("is_error"):
        raise PlannerError(f"claude reported an error: {str(result.get('result'))[:400]}")
    payload = result.get("structured_output")
    if payload is None:
        try:
            payload = json.loads(result.get("result") or "")
        except json.JSONDecodeError as e:
            raise PlannerError("claude returned no structured plan") from e
    return Plan.model_validate(payload)


def _cache_key(prompt_input: str, provider: str = "claude", style: str = "") -> str:
    model = config.PLANNER_MODEL if provider == "claude" else f"codex:{config.CODEX_MODEL}:{config.CODEX_EFFORT}"
    parts = [system_prompt(style),
             (config.PLANNER_DIR / "plan.schema.json").read_text(encoding="utf-8"), model, prompt_input]
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:32]


def plan_job(job: Job, library_slots: list[str], notes: str = "", fresh: bool = False, provider: str = "",
             revise: bool = False) -> Plan:
    """Plans via the chosen provider, reusing a cached answer for identical inputs unless `fresh` is set.
    With `revise`, the job's current plan is the starting point and only what needs fixing changes."""
    provider = provider if provider in config.PROVIDERS else config.PLANNER_PROVIDER
    prompt_input = build_input(job, library_slots, notes, job.plan if revise else None)
    cache = config.DATA / "plan-cache" / f"{_cache_key(prompt_input, provider, job.style)}.json"
    if cache.exists() and not fresh:
        result = json.loads(cache.read_text(encoding="utf-8"))
        usage.record(job.id, "plan (cached)", {"modelUsage": result.get("modelUsage", {})}, provider)
    else:
        result = run_codex(prompt_input, job.style) if provider == "codex" else run_claude(prompt_input, job.style)
        usage.record(job.id, "plan", result, provider)
        parse_plan(result)  # only cache answers that parse
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(result), encoding="utf-8")
    plan = parse_plan(result)
    plan.scenes = [s for s in plan.scenes if s.template in _known_templates()]  # e.g. a model inventing "face" scenes
    return normalize(plan, job.video.duration_us / 1e6)


def _known_templates() -> set[str]:
    from . import native, render

    return set(render.available_templates()) | native.NATIVE_FULLFRAME | native.NATIVE_OVERLAY


def styles() -> list[str]:
    """Saved editing styles (planner/styles/*.md), by name."""
    folder = config.PLANNER_DIR / "styles"
    return sorted(p.stem for p in folder.glob("*.md")) if folder.is_dir() else []
