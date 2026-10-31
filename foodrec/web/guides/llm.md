# Language model

The supervisor uses a language model for three bounded jobs only: understanding a chat message (choosing an intent from a fixed list), filling typed fields, and rewriting an answer from recorded evidence. **It never ranks recipes and never changes a saved goal.** Without a model the app still works: rules parse messages and templates write answers.

## Providers

| Provider | When to use it | What to set |
|---|---|---|
| `ollama` | Local, free, private. Default. | `model` (a pulled model, e.g. `qwen3.5:2b-q4_K_M`), Ollama URL |
| `openai` | Any OpenAI-compatible API: OpenAI, Groq, Together, OpenRouter, Mistral, vLLM, LM Studio, llama.cpp, Ollama's `/v1` | `base_url`, `model`, `api_key_env` |
| `anthropic` | The Claude API | `model` (e.g. `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`), `api_key_env`, `effort` |

Examples of `base_url`:

- OpenAI: `https://api.openai.com/v1`
- Groq: `https://api.groq.com/openai/v1`
- OpenRouter: `https://openrouter.ai/api/v1`
- LM Studio: `http://localhost:1234/v1`
- Ollama through the OpenAI API: `http://127.0.0.1:11434/v1`

## API keys

The config file never contains a key. `api_key_env` holds the **name** of an environment variable, for example `OPENAI_API_KEY` (empty: the provider's usual name).

**Paste the key into the field under "API key variable" and click Save to .env.** The server adds the line to the `.env` file of the repository (or replaces the old value), keeps all other lines, makes the file readable only by you, and uses the key at once, without a restart. `.env` is ignored by git. **Remove** deletes the line.

You can also edit `.env` by hand (then restart) or export the variable before starting:

```
# .env
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
```

The page shows whether that variable is set, never its value. For Anthropic, the SDK also finds an `ant auth login` profile.

The Anthropic provider needs the SDK: `pip install -e ".[anthropic]"`.

## Settings

- **enabled**: off = rule-based parsing and template answers (useful as a baseline condition).
- **model**: changes apply at once. Use the scenario runner to compare models on the same conversation: `foodrec scenario run scenarios/model_comparison.json --matrix llm.model=qwen3.5:2b-q4_K_M,qwen3:4b`.
- **think**: stream the model's reasoning into the trace by default (slower; marked unverified).
- **timeout_s**: one call; a timeout falls back to rules/templates for that turn.
- **effort** (Anthropic): `low` is usually enough for classification and short rewrites, and cheapest.
- **max_tokens** (hosted APIs): minimum output budget per call. Reasoning models need room to think.
- **fallbacks** (Anthropic): when the model declines a request for safety reasons, the API re-runs it on a fallback model in the same call. If a whole chain declines, the app answers from rules/templates.
- **ctx, seed** (Ollama): context window and sampling seed.

## Embeddings

Semantic search over recipes, method notes and documents uses **embed_provider**: `ollama` (default, `nomic-embed-text` on the CPU), `openai` (the `/embeddings` endpoint of `base_url`, e.g. `text-embedding-3-small`) or `none` (lexical hashing only). Anthropic has no embedding endpoint: combine it with `ollama` or `none`. Changing embeddings needs a restart (the index is rebuilt).

## What changes in the results

The model never ranks, so the **recommended lists do not change** with the model. What changes: how well chat messages are understood (which skills are planned), whether answers are model-written (and pass the evidence check) or template-written, and response time. The export records the model of every run.
