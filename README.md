# WAT (Web Agent Toolkit) - Gemini Edition

Welcome to the **WAT** (Workflows, Agents, Tools) template. This is a lightweight, file-based framework for building autonomous agents with Gemini.

## 🚀 Structure

The framework is simple:
- **Agent (`GEMINI.md`)**: The "Brain" instructions.
- **Workflows (`workflows/`)**: Markdown SOPs describing *what* to do.
- **Tools (`tools/`)**: Standalone Python scripts for *how* to do it.

### Roles
1. **You (The User)**: Create tasks and approve critical actions.
2. **Gemini (The Agent)**: Reads `workflows/`, plans, and executes `tools/`.
3. **Tools**: Dumb, deterministic scripts that do exactly one thing.

---

## 🧩 Core Concepts

1. **Read the Workflow**: The agent reads a `.md` file in `workflows/`.
2. **Execute Tools**: The agent runs `python3 tools/tool_name.py`.
3. **Save State**: The agent writes intermediate data to `.tmp/`.

---

## 🏆 Best Practices

1. **Tools are Scripts**: Tools should be executable from the CLI.
2. **Files over Memory**: Don't rely on context window for massive data; save it to files.
3. **Human-readable Workflows**: Write workflows in English (Markdown), not code.

---

## 🤖 Model Recommendations

| Need | Recommended Model | Rationale |
| :--- | :--- | :--- |
| **Complex Reasoning** | `gemini-2.5-pro` | Future-proofed deep reasoning and context handling. |
| **Speed/Volume** | `gemini-2.5-flash` | Optimized for sub-second responses at scale. |
| **Code Generation** | `gemini-3-pro` | Specialized high-accuracy logic and syntax generation. |

---

## 🛠️ Usage

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt # (if you add any)
   ```

2. **Run a Tool Manually**:
   ```bash
   python3 tools/web_search.py --query "simulated search"
   ```

3. **Run a Workflow**:
   - Open `workflows/sample_research.md`.
   - Paste the content into your Gemini chat window.
   - Let the agent execute the steps.
