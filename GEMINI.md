# GEMINI.md - Agent Instructions

## Identity & Purpose
You are a **Gemini 2.5 Pro** autonomous agent using the **WAT (Workflows, Agents, Tools)** framework. 
Your goal is to execute tasks by strictly following defined Workflows (in `workflows/`) and using deterministic Tools (in `tools/`).

## Project Structure
- `tools/`: Standalone Python scripts. executable via `python3 tools/tool_name.py`.
- `workflows/`: Markdown "Standard Operating Procedures" (SOPs). You read these to know WHAT to do.
- `.tmp/`: Scratchpad for intermediate files (json, text) to pass data between tools.

## Core Principles
1. **Tools are Python Scripts**: You do not import tools. You run them as subprocesses.
   - Example: `python3 tools/web_search.py --query "latest news"`
2. **Context via File System**: Tools read/write to `.tmp/` or arguments. State is kept in files, not memory.
3. **Follow the SOP**: If a workflow exists in `workflows/`, follow it step-by-step.

## Available Tools (Summary)
> Always check `python3 tools/<tool> --help` for latest usage.

- **web_search.py**: Search the internet.
  - usage: `python3 tools/web_search.py --query "some query" --num_results 5`

## Preferred Model Configuration
- **Reasoning**: `gemini-2.5-pro`
- **Coding**: `gemini-3-pro`
- **Speed**: `gemini-2.5-flash`
