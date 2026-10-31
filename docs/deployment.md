# Running and deploying FoodRec

FoodRec is one codebase with three modes (`server.mode` in the config file):

| Mode | Use | Who | Config page |
|---|---|---|---|
| `local` (default) | **your own copy**: clone it and run it on your computer | everyone signs in: the administrator account or participant accounts | administrator only |
| `demo` | a public showcase (e.g. on a cPouta VM) | no sign-in; visitors switch between a **User** and an **Admin** view | admin view only, read-only |
| `open` | development and automated tests | no sign-in at all | anyone whose role allows it |

The demo is not a different version: it is the same code started with `deploy/demo/demo.config.json`.

---

## 1. Your own copy (the normal way)

```bash
git clone https://github.com/<owner>/agentic_recom_sys.git
cd agentic_recom_sys
python -m venv .venv && source .venv/bin/activate
pip install -e .

foodrec admin password        # REQUIRED once: the administrator's password (stored as a hash)
foodrec                       # opens http://127.0.0.1:8000 and shows the sign-in page
```

Sign in as `admin` with that password. The administrator:

* uses the app like any user, and
* opens **Config** from the account menu (top right), or with `foodrec config open` from the terminal.

On the Config page (Users & study) the administrator creates participant accounts, for example 40 users
with 50 % "full" and 50 % "baseline". Participants sign in with those usernames and passwords, get the
role of their account, and never see Config. Everything on the Config page is stored in
`foodrec.config.json`; secrets (API keys) go to `.env`. Both stay on your computer.

Instead of `foodrec admin password` you can write the password into `foodrec.config.json` once:

```json
"access": {"admin": {"username": "admin", "password": "choose-a-password"}}
```

The app replaces it with `"password_hash"` when it starts. Until a password is set, `foodrec` refuses to
start and says how to set it.

Useful commands: `foodrec status`, `foodrec stop`, `foodrec config open`, `foodrec study list`,
`foodrec export --out study.zip`. The app restarts itself when the config file or its own code changes.

To make your copy reachable from other computers (a lab server), set `server.host` to `0.0.0.0`, put
HTTPS in front (Caddy, as below) and keep `server.mode` at `local`, so everyone signs in.

---

## 2. The public demo on a cPouta VM

The demo shows every page a user and an administrator can see. Visitors cannot change anything that
affects others: the Config page is read-only, there is no sign-in, each browser gets its own visitor id,
and the data is wiped every night.

### Create the VM (cPouta web interface)

1. **Instance**: Ubuntu 24.04, flavour `standard.medium` or larger (`standard.large` if the language model
   runs on the VM). Add your SSH key.
2. **Security group**: allow TCP 22 **from your institution's address range only**, and TCP 80 and 443 from
   everywhere. Do not open 8000.
3. **Floating IP**: associate one, and point a DNS name at it (needed for the HTTPS certificate).

### Install

```bash
ssh ubuntu@<floating-ip>
sudo apt update && sudo apt install -y python3-venv git caddy
sudo useradd --system --create-home --home-dir /opt/foodrec foodrec
sudo -u foodrec git clone https://github.com/<owner>/agentic_recom_sys.git /opt/foodrec
cd /opt/foodrec
sudo -u foodrec git checkout v0.5.0                    # a release tag: the demo runs a tested version
sudo -u foodrec python3 -m venv venv
sudo -u foodrec venv/bin/pip install -e .
```

Optional, for the assistant's language model and semantic search (otherwise the demo uses rules and
templates, which works fine):

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3.5:2b-q4_K_M && ollama pull nomic-embed-text
```

### Start the services

```bash
sudo cp deploy/demo/foodrec-demo.service deploy/demo/foodrec-demo-reset.service \
        deploy/demo/foodrec-demo-reset.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now foodrec-demo foodrec-demo-reset.timer

sudo cp deploy/demo/Caddyfile /etc/caddy/Caddyfile     # first replace the DNS name in it
sudo systemctl reload caddy
```

Open `https://<your-dns-name>`. A banner says it is a demo; the account menu (top right) switches between
**User view** and **Admin view**, and the admin view shows **Config** (read-only).

### Update the demo

```bash
cd /opt/foodrec
sudo -u foodrec git fetch --tags && sudo -u foodrec git checkout v0.6.0
sudo -u foodrec venv/bin/pip install -e .
sudo systemctl restart foodrec-demo
```

`deploy/demo/demo.config.json` sets `auto_reload` to `false`, so the demo only changes when you restart it.

### What the demo protects

| Risk | How the demo handles it |
|---|---|
| visitors changing settings for everyone | the Config page is read-only; every saving endpoint answers 403 |
| API keys | not shown and cannot be set; use a local model (or none) |
| visitors reading each other's data | each browser has a random visitor id; requests for another id are refused; admin lists and exports only show the visitor's own data |
| abuse | at most 20 new visitors per hour per address and 30 assistant messages per hour per visitor |
| data piling up | wiped every night by `foodrec-demo-reset.timer` |
| eavesdropping | HTTPS through Caddy; FoodRec itself only listens on 127.0.0.1 |
