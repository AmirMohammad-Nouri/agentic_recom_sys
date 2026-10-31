# The config file

Everything on this page is stored in one JSON file (default `foodrec.config.json` in the repository; another file with `foodrec --config study.json serve`). The page and the file are equivalent: anything set here can be set in the file and the other way round, and a running app picks up edits to the file by itself.

Sections: `llm`, `dataset`, `services`, `server`, `ranking`, `supervisor`, `criteria`, `interactions`, `access`, `study`.

- `null` means "not set here": the environment variable or the built-in default applies (precedence: default < environment / .env < file).
- Secrets never go in the file: `NEO4J_PASSWORD`, `FOODREC_ADMIN_TOKEN` and API keys stay in the environment (`llm.api_key_env` names the variable).
- **Check** validates without saving; invalid files are never applied.

Command line: `foodrec config show` (values and where they come from), `foodrec config check`, `foodrec config init` (a fresh file with all defaults), `foodrec config open`.
