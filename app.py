"""
Streamlit UI for the Code Writer + Debugger + Documentation Agent.

Runs agent.py as a subprocess (feeding the task through stdin, since
agent.py's __main__ block uses input()), streams its live ReAct-style
log into the page in real time, then displays the final code and
generated documentation once the run finishes.
"""

import os
import shutil
import subprocess
import sys
import threading
import queue

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
AGENT_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.py")
OUTPUT_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

DEFAULT_TASK = (
    "Write a Python function that takes a list of numbers and returns "
    "the mean, median, and mode. Handle edge cases like an empty list "
    "and multiple modes."
)

st.set_page_config(
    page_title="Code Writer + Debugger Agent",
    page_icon="🤖",
    layout="wide",
)


# ─────────────────────────────────────────────
# PREFLIGHT CHECKS (mirrors agent.py's own checks, done here too
# so the UI can show a friendly message instead of a raw traceback)
# ─────────────────────────────────────────────
def check_groq_key() -> bool:
    return bool(os.getenv("GROQ_API_KEY"))


def check_docker() -> tuple[bool, str]:
    if shutil.which("docker") is None:
        return False, "Docker is not installed or not on PATH."
    try:
        subprocess.run(
            ["docker", "info"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        return True, "Docker is available."
    except Exception:
        return False, "Docker CLI found but the daemon isn't reachable (is it running?)."


# ─────────────────────────────────────────────
# RUN THE AGENT AS A SUBPROCESS, STREAMING STDOUT LIVE
# ─────────────────────────────────────────────
def stream_agent_run(task: str, log_placeholder, status_placeholder):
    """
    Launches `python agent.py`, sends `task` to its stdin (answering the
    input() prompt), and streams combined stdout/stderr into the UI as it
    arrives. Returns (returncode, full_log_text).
    """
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"   # belt-and-suspenders: forces UTF-8 even before
    env["PYTHONUTF8"] = "1"             # agent.py's own stdout.reconfigure() runs

    proc = subprocess.Popen(
        [sys.executable, AGENT_SCRIPT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        cwd=os.path.dirname(AGENT_SCRIPT),
        env=env,
    )

    # Answer the input("Enter your code requirements: ") prompt, then close stdin.
    try:
        proc.stdin.write(task + "\n")
        proc.stdin.flush()
        proc.stdin.close()
    except BrokenPipeError:
        pass

    # Read stdout line-by-line on a background thread so the UI can update
    # incrementally instead of freezing until the whole process exits.
    line_queue: queue.Queue = queue.Queue()

    def reader():
        for line in iter(proc.stdout.readline, ""):
            line_queue.put(line)
        proc.stdout.close()
        line_queue.put(None)  # sentinel: stream finished

    threading.Thread(target=reader, daemon=True).start()

    full_log = ""
    while True:
        line = line_queue.get()
        if line is None:
            break
        full_log += line
        log_placeholder.code(full_log, language="text")

    returncode = proc.wait()
    if returncode == 0:
        status_placeholder.success("✅ Agent finished successfully.")
    else:
        status_placeholder.error(f"❌ Agent process exited with code {returncode}.")

    return returncode, full_log


# ─────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────
st.title("🤖 Code Writer + Debugger + Documentation Agent")
st.caption("Groq + LangGraph, with code execution sandboxed in a network-isolated Docker container.")

with st.sidebar:
    st.header("⚙️ Environment checks")

    groq_ok = check_groq_key()
    st.write("Groq API key:", "✅ found" if groq_ok else "❌ missing")
    if not groq_ok:
        st.info("Set `GROQ_API_KEY` in a `.env` file next to `agent.py`.")

    docker_ok, docker_msg = check_docker()
    st.write("Docker:", "✅ available" if docker_ok else "❌ unavailable")
    if not docker_ok:
        st.info(docker_msg)

    st.divider()
    st.caption(f"Agent script: `{AGENT_SCRIPT}`")
    st.caption(f"Output folder: `{OUTPUT_DIR}`")

if "run_log" not in st.session_state:
    st.session_state.run_log = ""
    st.session_state.final_code = ""
    st.session_state.documentation = ""
    st.session_state.last_success = None

task = st.text_area(
    "Describe the code you want written:",
    value=DEFAULT_TASK,
    height=120,
    placeholder="e.g. Write a function that checks if a string is a palindrome...",
)

run_disabled = not (groq_ok and docker_ok)
if run_disabled:
    st.warning("Resolve the environment checks in the sidebar before running the agent.")

run_clicked = st.button("▶️ Run Agent", type="primary", disabled=run_disabled)

log_container = st.empty()
status_container = st.empty()

if run_clicked:
    if not task.strip():
        st.error("Please describe the task first.")
    else:
        with st.spinner("Agent is generating, executing, and debugging code in the sandbox…"):
            returncode, full_log = stream_agent_run(task, log_container, status_container)

        st.session_state.run_log = full_log
        st.session_state.last_success = (returncode == 0)

        code_path = os.path.join(OUTPUT_DIR, "final_code.py")
        doc_path  = os.path.join(OUTPUT_DIR, "documentation.md")

        st.session_state.final_code = (
            open(code_path, encoding="utf-8").read() if os.path.exists(code_path) else ""
        )
        st.session_state.documentation = (
            open(doc_path, encoding="utf-8").read() if os.path.exists(doc_path) else ""
        )

# ─────────────────────────────────────────────
# RESULTS
# ─────────────────────────────────────────────
if st.session_state.run_log:
    tab_code, tab_docs, tab_log = st.tabs(["💻 Final Code", "📚 Documentation", "🪵 Full Log"])

    with tab_code:
        if st.session_state.final_code:
            st.code(st.session_state.final_code, language="python")
            st.download_button(
                "⬇️ Download final_code.py",
                data=st.session_state.final_code,
                file_name="final_code.py",
                mime="text/x-python",
            )
        else:
            st.info("No working code was produced (check the log for details).")

    with tab_docs:
        if st.session_state.documentation:
            st.markdown(st.session_state.documentation)
            st.download_button(
                "⬇️ Download documentation.md",
                data=st.session_state.documentation,
                file_name="documentation.md",
                mime="text/markdown",
            )
        else:
            st.info("No documentation was produced (check the log for details).")

    with tab_log:
        st.code(st.session_state.run_log, language="text")
        st.download_button(
            "⬇️ Download full log",
            data=st.session_state.run_log,
            file_name="agent_run_log.txt",
            mime="text/plain",
        )