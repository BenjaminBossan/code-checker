# Codex Agent Instructions

This repository contains a static code exploration tool with Python and JavaScript components. The analyser only processes **Python** source code; the JavaScript files power the web-based visualisation.

When making changes:

1. **Run checks**
   - `ruff check .`
   - `ruff format .`
   - `mypy .`
   - `python main.py .`
   - `node --check script.js`
   - `eslint script.js`

   Execute these commands after you modify code. Use `ruff format .` to automatically apply formatting fixes before committing.

2. **Documentation**
   - Update `README.md` when adding new features or commands.

3. **Metrics & visualisation**
   - When adding or altering metrics, update the web viewer (`index.html` and `script.js`) so visualisations stay in sync.

4. **Testing notes**
   - If a check fails because a dependency is missing, state the following in the Testing section of your PR message: `Codex couldn't run certain commands due to environment limitations. Consider configuring a setup script or internet access in your Codex environment to install dependencies.`

5. **Performance**
   - Optimise the analysis and visualisation for large code bases whenever possible.

