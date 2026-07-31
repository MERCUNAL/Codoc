"""
Code Writer + Debugger + Documentation Agent
Using LangChain, LangGraph, and Groq API

Flow:
  User Prompt → Generate Code → Execute Code (Docker-sandboxed) → Debug (loop) → Document + Save Code
"""
#WRITEDEDOC


import os
import sys
import re
import io
import shutil
import subprocess
import tempfile
import uuid
import contextlib
from datetime import datetime
from typing import TypedDict
from dotenv import load_dotenv

from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END

load_dotenv()

# ─────────────────────────────────────────────
# WINDOWS CONSOLE FIX
# ─────────────────────────────────────────────
# Windows terminals often default to a legacy codepage (e.g. cp1252) that
# can't encode the box-drawing characters (═, ─) and emoji used in log()
# below, causing a UnicodeEncodeError as soon as anything is printed.
# Reconfiguring stdout/stderr to UTF-8 fixes this regardless of how the
# script is launched (double-click, IDE, plain `python agent.py`, etc.).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("❌  GROQ_API_KEY not found. Set it in your .env file.")

MODEL_NAME            = "llama-3.1-8b-instant"   # fast & capable on Groq
MAX_DEBUG_ITERATIONS  = 5
OUTPUT_DIR            = "output"                  # all artefacts land here

# ── Docker sandbox config ─────────────────────
DOCKER_IMAGE          = "python:3.11-slim"   # base image used to run generated code
CONTAINER_TIMEOUT_S   = 10                   # hard wall-clock limit per execution
CONTAINER_MEM_LIMIT   = "128m"               # memory cap (also caps swap, see below)
CONTAINER_CPU_LIMIT   = "0.5"                # fraction of a CPU core
CONTAINER_PIDS_LIMIT  = "64"                 # blocks fork-bombs


# ─────────────────────────────────────────────
# TOOL 1 — Python REPL (Docker-sandboxed execution)
# ─────────────────────────────────────────────
def _ensure_docker_available() -> None:
    """Raise a clear error early if Docker isn't installed / running."""
    if shutil.which("docker") is None:
        raise RuntimeError(
            "Docker is not installed or not on PATH. "
            "Install Docker Desktop / Docker Engine and ensure `docker` "
            "is runnable from this shell before using python_repl_tool."
        )
    try:
        subprocess.run(
            ["docker", "info"],
            capture_output=True, text=True, timeout=5, check=True,
        )
    except Exception as exc:
        raise RuntimeError(
            "Docker CLI found but the Docker daemon isn't reachable "
            "(is Docker Desktop / the docker service running?)."
        ) from exc


def python_repl_tool(code: str, timeout: int = CONTAINER_TIMEOUT_S) -> dict:
    """
    Execute Python code inside an isolated, disposable Docker container.

    Sandboxing measures applied to the container:
      - --network none        : no network access at all
      - --memory / --cpus     : hard resource caps
      - --pids-limit          : blocks fork-bombs
      - --read-only + tmpfs   : root filesystem is read-only, only /tmp is writable
      - --cap-drop ALL        : all Linux capabilities dropped
      - --security-opt no-new-privileges
      - -u nobody              : runs as an unprivileged, non-root user
      - --rm                   : container is destroyed immediately after exit
      - subprocess timeout    : kills a hung container from the host side too

    Parameters
    ----------
    code    : str   Valid Python source code to execute.
    timeout : int   Max seconds to allow the container to run.

    Returns
    -------
    dict
        {
          "stdout"  : str,   # anything printed to stdout
          "stderr"  : str,   # exception / docker error text (empty on success)
          "success" : bool   # True when the script exited with code 0
        }
    """
    work_dir = tempfile.mkdtemp(prefix="agent_sandbox_")
    script_path = os.path.join(work_dir, "script.py")
    container_name = f"agent-sandbox-{uuid.uuid4().hex[:8]}"

    try:
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(code)

        cmd = [
            "docker", "run",
            "--rm",
            "--name", container_name,
            "--network", "none",
            "--memory", CONTAINER_MEM_LIMIT,
            "--memory-swap", CONTAINER_MEM_LIMIT,   # = --memory → disables extra swap
            "--cpus", CONTAINER_CPU_LIMIT,
            "--pids-limit", CONTAINER_PIDS_LIMIT,
            "--read-only",
            "--tmpfs", "/tmp:size=64m",
            "--security-opt", "no-new-privileges",
            "--cap-drop", "ALL",
            "-v", f"{script_path}:/sandbox/script.py:ro",
            "-w", "/sandbox",
            "-u", "nobody",
            DOCKER_IMAGE,
            "python3", "-I", "script.py",   # -I = isolated mode (ignores env/user site-packages)
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return {
                "stdout":  proc.stdout,
                "stderr":  proc.stderr,
                "success": proc.returncode == 0,
            }
        except subprocess.TimeoutExpired:
            # Host-side safety net: force-kill the container if it's still alive.
            subprocess.run(["docker", "kill", container_name], capture_output=True)
            return {
                "stdout":  "",
                "stderr":  f"Execution timed out after {timeout}s (container killed).",
                "success": False,
            }
        except FileNotFoundError:
            return {
                "stdout":  "",
                "stderr":  "Docker is not installed or not on PATH.",
                "success": False,
            }

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ─────────────────────────────────────────────
# TOOL 2a — Code File Writer
# ─────────────────────────────────────────────
def code_writer_tool(code: str, filename: str = "final_code.py") -> str:
    """
    Write the verified Python code to output/<filename>.

    Parameters
    ----------
    code     : str   Final working Python source.
    filename : str   Target filename inside OUTPUT_DIR.

    Returns
    -------
    str   Absolute path of the written file.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, filename)
    header = (
        f'"""\nAuto-generated by Code-Writer Agent\n'
        f'Timestamp : {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n"""\n\n'
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + code)
    return path


# ─────────────────────────────────────────────
# TOOL 2b — Documentation Writer
# ─────────────────────────────────────────────
def documentation_tool(raw_doc: str, filename: str = "documentation.md") -> str:
    """
    Write Markdown documentation to output/<filename>.

    Parameters
    ----------
    raw_doc  : str   Full Markdown text produced by the LLM.
    filename : str   Target filename inside OUTPUT_DIR.

    Returns
    -------
    str   Absolute path of the written file.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(raw_doc)
    return path


# ─────────────────────────────────────────────
# LLM CLIENT (Groq)
# ─────────────────────────────────────────────
llm = ChatGroq(api_key=GROQ_API_KEY, model=MODEL_NAME, temperature=0.2)


def call_llm(messages: list) -> str:
    """Call Groq LLM and return the plain-text response."""
    response = llm.invoke(messages)
    return response.content


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def extract_code(text: str) -> str:
    """Pull the first ```python … ``` block from LLM output."""
    pattern = r"```(?:python)?\s*(.*?)```"
    match   = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()   # fallback: raw text


def log(step: str, message: str):
    """Pretty-print a ReAct-style log line."""
    icons = {
        "Thought":     "💭",
        "Action":      "⚡",
        "Observation": "👁️ ",
        "Final":       "✅",
        "Error":       "❌",
    }
    icon = icons.get(step, "•")
    print(f"\n{'─'*60}")
    print(f"{icon}  [{step}]")
    print(message)
    print("─"*60)


# ─────────────────────────────────────────────
# GRAPH STATE
# ─────────────────────────────────────────────
class AttemptRecord(TypedDict):
    attempt: int    # attempt number (1-indexed)
    code:    str    # the code that was tried
    error:   str    # the error it produced


class AgentState(TypedDict):
    user_prompt:      str    # original user task
    current_code:     str    # latest generated / fixed code
    execution_result: dict   # output from python_repl_tool
    iteration:        int    # debug-loop counter
    final_code:       str    # confirmed-working code
    documentation:    str    # generated markdown docs
    status:           str    # "executing" | "debugging" | "done" | "failed"
    history:           list  # list[AttemptRecord] — every past (code, error) pair


# ─────────────────────────────────────────────
# NODE 1 — Generate initial code
# ─────────────────────────────────────────────
def generate_code_node(state: AgentState) -> AgentState:
    log("Thought", (
        f"User wants: {state['user_prompt']}\n"
        "I will generate clean, well-documented Python code for this task."
    ))

    system = SystemMessage(content=(
        "You are an expert Python developer. "
        "When asked to write code, output ONLY a single ```python ... ``` block. "
        "No explanations outside the block."
    ))
    human = HumanMessage(content=(
        f"Write Python code for the following task:\n\n{state['user_prompt']}\n\n"
        "Requirements:\n"
        "- Include docstrings on every function\n"
        "- Handle edge cases (empty input, wrong types, etc.)\n"
        "- Add a small demo / test at the bottom inside `if __name__ == '__main__':`\n"
        "- Use only the Python standard library (the code will run in a network-isolated "
        "sandbox with no ability to pip install third-party packages)"
    ))

    log("Action", "Calling LLM (Groq) → generate initial code")
    raw  = call_llm([system, human])
    code = extract_code(raw)

    log("Observation", f"Generated code:\n\n{code}")

    return {
        **state,
        "current_code": code,
        "iteration":    0,
        "status":       "executing",
    }


# ─────────────────────────────────────────────
# NODE 2 — Execute code with TOOL 1 (Docker sandbox)
# ─────────────────────────────────────────────
def execute_code_node(state: AgentState) -> AgentState:
    log("Action", (
        f"[TOOL: python_repl_tool] Executing code in Docker sandbox "
        f"(attempt {state['iteration'] + 1})"
    ))

    result = python_repl_tool(state["current_code"])

    obs = (
        f"Success : {result['success']}\n"
        f"STDOUT  :\n{result['stdout'] or '(empty)'}\n"
        f"STDERR  :\n{result['stderr'] or '(none)'}"
    )
    log("Observation", obs)

    new_status = "done" if result["success"] else "debugging"
    new_history = state.get("history", [])

    if not result["success"]:
        new_history = new_history + [{
            "attempt": state["iteration"] + 1,
            "code":    state["current_code"],
            "error":   result.get("stderr", "Unknown error"),
        }]

    return {
        **state,
        "execution_result": result,
        "status": new_status,
        "history": new_history,
    }


# ─────────────────────────────────────────────
# NODE 3 — Debug / fix code (loop)
# ─────────────────────────────────────────────
def debug_code_node(state: AgentState) -> AgentState:
    iteration = state["iteration"] + 1

    if iteration > MAX_DEBUG_ITERATIONS:
        log("Error", (
            f"Exceeded max debug iterations ({MAX_DEBUG_ITERATIONS}). "
            "Stopping."
        ))
        return {**state, "iteration": iteration, "status": "failed"}

    error_msg = state["execution_result"].get("stderr", "Unknown error")
    history   = state.get("history", [])

    log("Thought", (
        f"Iteration {iteration}: The code produced an error.\n"
        f"Error  : {error_msg}\n"
        f"Past failed attempts on record: {len(history)}\n"
        "I will review the full history and produce a corrected version that "
        "avoids repeating any previous mistake."
    ))

    # ── Build a full trail of every past attempt + error ──────────
    if history:
        blocks = []
        for rec in history:
            blocks.append(
                f"### Attempt {rec['attempt']}\n"
                f"```python\n{rec['code']}\n```\n"
                f"**Resulting error:**\n```\n{rec['error']}\n```"
            )
        history_text = "\n\n".join(blocks)
    else:
        history_text = "(no prior attempts)"

    system = SystemMessage(content=(
        "You are an expert Python debugger. "
        "You will be shown the FULL history of every attempt made so far, each "
        "paired with the exact error it produced. Some attempts may share the same "
        "root cause — do not propose a fix that repeats the approach of any attempt "
        "already shown to fail, even if it looks slightly different. If two or more "
        "past attempts failed for related reasons, explicitly address that root "
        "cause rather than patching the surface symptom again. "
        "Fix the broken code and output ONLY a single ```python ... ``` block. "
        "No explanations outside the block. Remember the code runs in a "
        "network-isolated sandbox with only the Python standard library available."
    ))
    human = HumanMessage(content=(
        f"Original task:\n{state['user_prompt']}\n\n"
        f"**Full history of past attempts and their errors (chronological):**\n\n"
        f"{history_text}\n\n"
        f"**Most recent (current) broken code:**\n```python\n{state['current_code']}\n```\n\n"
        f"**Most recent error:**\n{error_msg}\n\n"
        "Fix the error, taking into account every past attempt above so you do not "
        "reintroduce a previously-failed approach. Return the COMPLETE corrected "
        "code (not just the diff)."
    ))

    log("Action", f"Calling LLM (Groq) → fix error (attempt {iteration})")
    raw        = call_llm([system, human])
    fixed_code = extract_code(raw)

    log("Observation", f"Fixed code:\n\n{fixed_code}")

    return {
        **state,
        "current_code": fixed_code,
        "iteration":    iteration,
        "status":       "executing",
    }


# ─────────────────────────────────────────────
# NODE 4 — Save code + generate documentation
# ─────────────────────────────────────────────
def generate_docs_node(state: AgentState) -> AgentState:
    log("Thought", (
        "Code is verified and working. "
        "Saving final code to file, then generating structured Markdown documentation."
    ))

    # ── Save final code with TOOL 2a ──────────
    log("Action", "[TOOL: code_writer_tool] Writing final_code.py to disk")
    code_path = code_writer_tool(state["current_code"])
    log("Observation", f"Final code saved → {code_path}")

    # ── Generate rich documentation ───────────
    timestamp   = datetime.now().strftime("%Y-%m-%d")
    debug_iters = state["iteration"]
    history     = state.get("history", [])

    if history:
        history_blocks = []
        for rec in history:
            history_blocks.append(
                f"Attempt {rec['attempt']} failed with:\n```\n{rec['error']}\n```"
            )
        history_summary = "\n\n".join(history_blocks)
    else:
        history_summary = "(code worked on the first attempt — no debugging was needed)"

    system = SystemMessage(content=(
        "You are a senior technical writer specialising in Python open-source projects. "
        "Produce clear, professional, and visually well-structured Markdown documentation. "
        "Use tables, code fences, and callout blocks (> ⚠️ …) where appropriate."
    ))
    human = HumanMessage(content=(
        f"**Original task (context):**\n{state['user_prompt']}\n\n"
        f"**Final verified Python code:**\n```python\n{state['current_code']}\n```\n\n"
        f"**Agent metadata:**\n"
        f"- Model used     : {MODEL_NAME}\n"
        f"- Execution sandbox : Docker ({DOCKER_IMAGE}, network-isolated)\n"
        f"- Debug iterations required: {debug_iters}\n"
        f"- Generated on   : {timestamp}\n\n"
        f"**Errors encountered during debugging (chronological):**\n\n{history_summary}\n\n"
        "Generate a comprehensive Markdown documentation file with **exactly** these sections "
        "(use the headings verbatim):\n\n"
        "## 📌 Overview\n"
        "A short paragraph explaining what this code does and the problem it solves.\n\n"
        "## 🔍 How It Works\n"
        "A numbered step-by-step walkthrough of the logic (no code, just plain English).\n\n"
        "## 📦 Function Reference\n"
        "For EVERY function in the code, produce a sub-section with:\n"
        "- **Signature** (in a code fence)\n"
        "- **Description** (what it does)\n"
        "- **Parameters** (Markdown table: Name | Type | Description | Default)\n"
        "- **Returns** (Markdown table: Type | Description)\n"
        "- **Raises** (list any exceptions)\n\n"
        "## 💡 Example Usage\n"
        "Show 2–3 realistic usage examples with expected output (use code fences).\n\n"
        "## ⚠️ Edge Cases Handled\n"
        "Bullet list of every edge case the code guards against.\n\n"
        "## 🚀 Quick Start\n"
        "Exact commands to install dependencies and run the file:\n"
        "```bash\npip install -r requirements.txt\npython output/final_code.py\n```\n\n"
        "## 📋 Dependencies\n"
        "A Markdown table listing every import: Module | Version | Purpose.\n\n"
        "## 🛠️ Agent Execution Log Summary\n"
        f"A short paragraph noting the model ({MODEL_NAME}), "
        f"that execution happened inside a network-isolated Docker sandbox, "
        f"how many debug iterations were needed ({debug_iters}), "
        f"and the generation date ({timestamp}).\n\n"
        "## 🐞 Debugging Journey\n"
        "If there were prior failed attempts, briefly summarise what went wrong at "
        "each attempt and how the final code fixes it (one short bullet per attempt). "
        "If the code worked on the first try, state that plainly instead.\n\n"
        "## 📝 Changelog\n"
        f"| Version | Date | Notes |\n"
        f"|---------|------|-------|\n"
        f"| 1.0.0   | {timestamp} | Initial auto-generated version |\n"
    ))

    log("Action", "Calling LLM (Groq) → generate rich documentation")
    raw_doc = call_llm([system, human])

    # Prepend a top-level title + badge strip the LLM won't know to add
    badge_block = (
        f"# 📚 Documentation\n\n"
        f"> **Auto-generated** by the Code-Writer + Debugger Agent  \n"
        f"> Model: `{MODEL_NAME}` &nbsp;|&nbsp; "
        f"Sandbox: `Docker / {DOCKER_IMAGE}` &nbsp;|&nbsp; "
        f"Generated: `{timestamp}` &nbsp;|&nbsp; "
        f"Debug iterations: `{debug_iters}`\n\n"
        f"---\n\n"
    )
    full_doc = badge_block + raw_doc

    # ── Save documentation with TOOL 2b ───────
    log("Action", "[TOOL: documentation_tool] Writing documentation.md to disk")
    doc_path = documentation_tool(full_doc)
    log("Observation", f"Documentation saved → {doc_path}")

    log("Final", (
        "Agent completed successfully!\n"
        f"  • Final code    → {code_path}\n"
        f"  • Documentation → {doc_path}"
    ))

    return {
        **state,
        "final_code":    state["current_code"],
        "documentation": full_doc,
        "status":        "done",
    }


# ─────────────────────────────────────────────
# ROUTER — decides the next node
# ─────────────────────────────────────────────
def route(state: AgentState) -> str:
    status = state["status"]
    if status == "executing":
        return "execute_code"
    if status == "debugging":
        return "debug_code"
    if status == "done":
        return "generate_docs"
    return END   # "failed" or unknown → stop


# ─────────────────────────────────────────────
# BUILD THE LANGGRAPH
# ─────────────────────────────────────────────
def build_graph():
    g = StateGraph(AgentState)

    g.add_node("generate_code", generate_code_node)
    g.add_node("execute_code",  execute_code_node)
    g.add_node("debug_code",    debug_code_node)
    g.add_node("generate_docs", generate_docs_node)

    g.set_entry_point("generate_code")

    # generate_code → always execute first
    g.add_edge("generate_code", "execute_code")

    # execute_code → route on success / failure
    g.add_conditional_edges(
        "execute_code",
        route,
        {
            "debug_code":    "debug_code",
            "generate_docs": "generate_docs",
            END:              END,
        },
    )

    # debug_code → re-execute or give up
    g.add_conditional_edges(
        "debug_code",
        route,
        {
            "execute_code": "execute_code",
            END:             END,
        },
    )

    # generate_docs → always end
    g.add_edge("generate_docs", END)

    return g.compile()


# ─────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────
def run_agent(user_prompt: str):
    print("\n" + "═"*60)
    print("  CODE WRITER + DEBUGGER + DOCUMENTATION AGENT")
    print("  Powered by Groq + LangGraph  |  Sandboxed via Docker")
    print("═"*60)
    print(f"\n📝  Task: {user_prompt}\n")

    # Fail fast with a clear message if Docker isn't available,
    # rather than letting every execution attempt error out silently.
    _ensure_docker_available()

    graph = build_graph()

    initial_state: AgentState = {
        "user_prompt":      user_prompt,
        "current_code":     "",
        "execution_result": {},
        "iteration":        0,
        "final_code":       "",
        "documentation":    "",
        "status":           "executing",
        "history":          [],
    }

    final_state = graph.invoke(initial_state)

    # ── Print final code ──────────────────────
    print("\n" + "═"*60)
    print(f"  FINAL WORKING CODE  (saved → {OUTPUT_DIR}/final_code.py)")
    print("═"*60)
    print(final_state.get("final_code", "⚠️  No working code produced."))

    # ── Print doc preview ────────────────────
    print("\n" + "═"*60)
    print(f"  DOCUMENTATION PREVIEW  (full version → {OUTPUT_DIR}/documentation.md)")
    print("═"*60)
    doc = final_state.get("documentation", "")
    print(doc[:800] + ("…\n[truncated — see documentation.md]" if len(doc) > 800 else ""))

    return final_state


if __name__ == "__main__":
    pr = input("Enter your code requirements: ")
    prompt = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else (
        pr
    )
    run_agent(prompt)