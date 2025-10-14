# sioyek-ai

Python helper that streams highlighted passages from sioyek to OpenAI, shows the reply in a PyQt window, stores the conversation in sioyek's databases, and marks the source text with a dedicated highlight colour.

## Setup

1. Ensure [uv](https://github.com/astral-sh/uv) is installed (already used in this repo).
2. Create/refresh the virtual environment and install dependencies:
   ```bash
   UV_CACHE_DIR="$PWD/.uv-cache" uv sync
   ```
3. Copy `.env` to fill in your OpenAI credentials (the file is created with empty defaults):
   ```bash
   echo "OPENAI_API_KEY=sk-..." >> .env
   # optional overrides:
   # OPENAI_MODEL=gpt-4o-mini
   ```

> **Heads-up:** the command now uses PyQt5 to show results, so it needs a graphical session (X11/Wayland) when you trigger `_ask_ai`.

You can activate the environment with `source .venv/bin/activate` if you prefer.

## Sioyek integration

Add a new command to your `prefs_user.config` (adjust the module path if you install it differently). Including `%{command_text}` prompts for a custom question each time, and passing the selection/DB placeholders lets us highlight the text and store the chat history:

```text
new_command _ask_ai /home/manshar/projects/sioyek-ai/.venv/bin/python /home/manshar/projects/sioyek-ai/sioyek_ai/ask_ai.py "%{sioyek_path}" "%{selected_text}" "%{file_path}" "%{command_text}" "%{selection_begin_document}" "%{selection_end_document}" "%{local_database}" "%{shared_database}"
```

Bind it to a key in `keys_user.config`, for example:

```text
_ask_ai <C-a>
```

To reopen conversations by clicking a purple highlight, add another command and assign it to `control_click_command`:

```text
new_command _show_ai_history /home/manshar/projects/sioyek-ai/.venv/bin/python /home/manshar/projects/sioyek-ai/sioyek_ai/show_history.py "%{sioyek_path}" "%{file_path}" "%{mouse_pos_document}" "%{local_database}" "%{shared_database}"
shift_click_command _show_ai_history
```

When you select text and trigger the command, the script:

- loads `.env` (via `python-dotenv`) and reads `OPENAI_API_KEY` plus optional tuning vars,
- drops a purple (type `v`) highlight on the selection and flags it in `shared.db` (`highlights.is_ai = 1`) so you can spot AI-assisted passages later,
- gathers nearby context from the page and basic metadata (file name, PDF title, detected abstract if any) and sends that alongside the selected text,
- inserts a new row into `shared.db` (`ai_sessions` + `ai_messages`) tied to the highlight and document hash,
- opens a PyQt window immediately (even while the model is thinking) so you can see the document path, highlighted text, question, and the answer streaming in live,
- shows previous sessions for the current document in a history sidebar; selecting one loads the saved conversation, and
- appends detailed logs—including stdout/stderr—to `test.txt` in this directory.

Trigger `_ask_ai` without typing a question to simply open the history window (no API call) with the current selection preloaded.

If no text is selected or the API call fails, the status bar and `test.txt` include the relevant error.

## Local testing

Run the module directly (using `echo` as a harmless stand-in for `sioyek_path`). Provide some dummy coordinates and point to your actual sioyek databases:

```bash
.venv/bin/python -m sioyek_ai.ask_ai \
  $(which echo) \
  "Example selection" \
  "/path/to/doc.pdf" \
  "Why is this important?" \
  "0 100 200" \
  "0 150 220" \
  "$HOME/.local/share/Sioyek/local.db" \
  "$HOME/.local/share/Sioyek/shared.db"
```

With an empty `OPENAI_API_KEY`, the command reports the missing key; once you add a valid key, it will reach the API. The question argument is optional—omit it or leave it blank to rely only on the highlighted text.
