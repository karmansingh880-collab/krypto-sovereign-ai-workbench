# Krypto

Sovereign AI Workbench

## Team Project

This repository contains the development work for the Krypto project.

## Layout

```
backend/app/main.py      FastAPI app (API + serves the UI at /ui)
backend/app/routes/      HTTP routes: /tasks (run the agent), /system/status
backend/app/agent/       LangGraph agent loop (plan -> act -> observe -> reflect); direct.py answers questions and
                         writes+runs code; vision_context.py looks at pictures
backend/app/router/      Model router (models.yaml)
backend/app/tools/       OCR, vision, corrosion calc, docgen, code sandbox (+ code_runner.py: Docker or isolated process)
backend/app/rag/         Qdrant knowledge base (ingest + search)
backend/app/outputs/     Word / PDF / image results (intent rules, renderers, charts, exports)
backend/app/live.py      Live progress of a run (what the AI is doing now / next), streamed over WebSocket
backend/app/netguard.py  Strict offline mode (blocks any connection to another computer)
backend/app/network.py   Online / offline (egress) status, Ethernet / Wi-Fi, address for a second computer
backend/app/sovereignty.py  Proof: the OS's own list of connections, audit log, self-test
backend/app/quality.py   Checks the facts in an answer against your document
backend/app/auth/        Accounts and sessions
backend/tests/           Unit tests (no services needed)
web/                     React + TypeScript app (source). `npm run build` writes it to frontend/
frontend/                The BUILT app the backend serves at /ui  (frontend/legacy = the previous UI)
run_demo.ps1             One-command demo launcher (-Offline, -Lan, -Seal, -OllamaHost)
scripts/seal_network.ps1 Windows Firewall rules that block the internet for every program Krypto uses (admin)
```

## Run the demo (Windows)

Prerequisites: Docker, Ollama (running), Python 3.12.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r backend\requirements.txt
copy backend\.env.example backend\.env
ollama pull llama3.2:3b
ollama pull nomic-embed-text
.\run_demo.ps1
```

Then open http://localhost:8010/ui/ and click **Run agent** (the bundled sample
inspection report is pre-selected). `run_demo.ps1` starts MongoDB and Qdrant in
Docker, loads the demo SOP into the knowledge base, and serves the API + UI.

### Models

By default `run_demo.ps1` runs in **demo mode** (`KRYPTO_DEMO_MODEL=llama3.2:3b`):
every LLM call uses that one small model so the agent runs on a laptop without a
GPU. In this mode the agent also runs the SOP lookup and the approval note in
code from the verified OCR/calculation results when the small model can't (these
steps are labelled `[SYSTEM]` in the trace). With a capable machine, pull the
models in `backend/app/router/models.yaml` and run `.\run_demo.ps1 -DemoModel ""`
to use the locked models with none of these assists.

Two small **specialist** models are used, when installed, for the kinds of task they suit (the router still decides by
task type; in demo mode the specialist stands in for the large model the design names):

```powershell
ollama pull qwen2.5-coder:1.5b   # code tasks (design calls for qwen2.5-coder:7b)
ollama pull moondream            # looking at pictures (design calls for qwen2.5vl:7b)
```

Override with `KRYPTO_DEMO_CODER_MODEL` / `KRYPTO_DEMO_VISION_MODEL` (`none` switches a specialist off).

## Accounts

The app requires sign-in (the `/ui/#/login` page). Create an account, or use the built-in
**demo account** to skip sign-up: click *Continue with the demo account*
(`demo@krypto.local` / `demo1234`). Runs and files are private to each account;
runs made before accounts existed belong to the demo account.

- Passwords are hashed with scrypt; sessions are random tokens stored hashed in MongoDB and
  sent as an HttpOnly, SameSite=Lax cookie. Failed logins are throttled (5 per 5 minutes).
- Settings (in `backend/.env`): `DEMO_ACCOUNT_ENABLED=false` removes the demo account,
  `DEMO_EMAIL` / `DEMO_PASSWORD` change it, `SESSION_DAYS`, and `COOKIE_SECURE=true` when serving over HTTPS.

## Files and images as results

Ask in the instruction, or tick **Also create** under the goal:

| You ask for | You get |
|---|---|
| "create a Word file / docx of this summary" | a `.docx` written from the document |
| "make a PDF of ..." | a `.pdf` |
| "give me the graph image" / "show me all the images" | the figures **extracted from the attached PDF** (header logos skipped), shown as a gallery |
| "create a bar chart of ..." | a chart drawn from the data. Tables in inspection reports are read exactly, without the model |
| an image request with no numbers to plot | an infographic of the key points |

For the corrosion workflow, ticking **PDF** adds the approval note as a PDF and **Image** adds a
corrosion chart; without them the workflow is unchanged.

Limits: images are extracted from the PDF/Word file or drawn as charts/infographics. Photo-realistic AI image
generation is **not** included (it needs a diffusion model, too heavy for a CPU-only laptop). Quality of
written text depends on the model in use; the small demo model summarizes plainly and can miss detail.

## Documents you can attach

PDF, Word (`.docx`), text (`.txt`, `.md`, `.csv`) and images (PNG, JPG, WebP, BMP, TIFF). Up to **5 files** per task,
**100 MB each** (250 MB in total). Old `.doc` files are refused with a hint to save as `.docx`.

| Input | How it is handled |
|---|---|
| Long PDF / document (hundreds of pages) | A question is answered from the best-matching passages (keyword shortlist, then embedding search). A *summary* takes notes on up to 6 evenly spread sections first, so the whole document counts (the run says how many sections it covered; expect several minutes on a CPU) |
| Several files | Read together; the answer can use all of them |
| Scanned PDF | OCR on the first 40 pages (each page takes seconds on a CPU) |
| Very large image | Shrunk to at most 3000 px before OCR |
| Word document | Text and tables are read; embedded pictures can be extracted with "give me all the images" |

Extracting many images (more than 4) also offers them as one `all-images.zip`.

## Web app (React)

The UI lives in `web/` (React 19, TypeScript, Vite, React Router with hash routes so the backend can serve it as
plain static files). Features: sign-in/sign-up + demo account, drag-and-drop uploads with progress, per-type suggestions,
run history with search/filters/grouping, result view with answer / corrosion table / downloads / image gallery with
a full-size viewer / step trace, light and dark themes, and a responsive layout.

```powershell
cd web
npm install
npm run dev        # http://localhost:5173/ui/  (proxies the API on :8010; sign in works via the proxy)
npm run build      # type-checks, then writes the app into ../frontend (what the backend serves)
```

After `npm run build` the backend serves the new build immediately (no restart; refresh with Ctrl+F5 if the
browser cached the old page). The previous UI is still available at `/ui/legacy/`.

## Tests

```powershell
.venv\Scripts\python -m unittest backend.tests.test_units backend.tests.test_live_features backend.tests.test_direct_and_proof -v   # 161 unit tests, no services needed
cd web; npm run typecheck                                        # TypeScript
```

## Live activity (WebSocket)

While a task runs, the panel on the **right** shows what the AI is doing: the step in progress (with a timer and, while
the model reads your text, an estimate of how long that takes on this computer), the steps still to come (the next one is
marked), and the answer **as it is being written**. Finished runs show how long each step took.

- `GET /ws/tasks/{id}` streams the run (only its owner can watch); `GET /ws/system` streams the network state.
- If a WebSocket cannot connect, the page keeps working: it refreshes itself every few seconds instead.
- Reading long documents shows progress ("Section 3 of 6").

## What next? (after every answer)

Under each finished answer: **Download as Word / PDF / Image** (instant, no model is run) and a **follow-up** box that asks
more about the *same files*, remembering the earlier question and answer. `POST /tasks/{id}/export` and
`POST /tasks/{id}/followup` do the work; follow-ups keep working even if the original run is deleted.

## Works without internet, and shows the state

Everything runs on this computer (AI models, OCR, database, search). The **network badge** in the top bar shows whether the
computer is online (internet reachability and latency, Wi-Fi name and signal, browser state) and says so when it is offline.

Run with `.\run_demo.ps1 -Offline` for **strict offline mode**: any attempt to reach another computer is refused and
recorded (the badge shows the count and the addresses). Only this computer stays reachable. The state (internet, Wi-Fi,
**Ethernet cable**, and the address of this computer on the network) is shown live in the top bar.

## Speed and answer quality

Prompt reading is the slow part on a CPU (about 28 tokens per second here; thread/batch settings do not change it), so the
work is arranged to avoid it:

| What | Effect |
|---|---|
| Corrosion workflow computed in code from the OCR text (no model planning) | about 150-210 s -> 1-3 s |
| Text recognized once per file is remembered (`~/.krypto_cache`) | re-reading the same image: 50 s -> instant |
| Model kept loaded and warmed at start-up; identical document prefix | a follow-up about the same document: 8 s instead of 60-75 s |
| Short answers by default (longer when you ask for a list, a summary or detail) | less time writing |
| Stray glyphs / blank lines removed from the text sent to the model | fewer tokens |

Checks on every answer: facts in it (numbers, codes, quoted text) are looked up in your document, and any that are **not
found** are flagged ("double-check these"); the passages the answer used can be expanded under it.

## Questions and code (no document needed)

Type any question, or a coding request, with **No file** selected (a request that plainly has nothing to do with the sample
report, such as "simple code for python", ignores the pre-selected sample automatically):

- **A question** is answered directly and streamed as it is written (about 8-12 s here).
- **A code request** is written by the coder model, then **run in the sandbox**. If it fails, the model sees the real error
  and fixes it (up to twice; the last try goes to the larger model). The answer shows the code, its real output and a badge:
  *Verified* (ran and its own checks passed), *Ran, but the model's own tests were wrong* (they are removed and the run is
  repeated; stated plainly), or *Not verified*. Small models often write wrong expected values in their own `assert`s, so this
  matters.
- The sandbox is a Docker container with the network switched off, 1 CPU, 512 MB and a 30 s limit. If Docker is not
  available (a second computer), the code runs in an isolated local process instead (sockets disabled, temp folder, 20 s
  limit) and the answer says which one ran. Code in other languages is shown and marked *not run*.

## Automatic model selection

Every routed step records what the router chose and why. The result page shows an **Automatic model selection** card (task
type, the model the design calls for, the model that ran here, the reason) and a chip on each trace step;
`GET /system/models` lists the whole table. Task types shown: general reasoning (`qwen3:14b` by design), code
(`qwen2.5-coder:7b`), images (`qwen2.5vl:7b`). Demo mode swaps in smaller models and says so.

## Pictures and scans

Text in an image or scan is read with OCR (PaddleOCR, on this computer). When the words alone cannot answer ("describe this
chart", an image with no text), the local vision model looks at the picture and its description is added to what the answering
model reads. "What text is written in the image?" returns the OCR text exactly (a small model copying it can drop a digit).
Without a vision model installed the answer says so and gives plain facts (size, colours) instead of guessing.

## Two computers over ethernet

One computer runs everything (models, database, search); the other only needs a **web browser**.

1. Join the two computers with an ethernet cable (or a switch). If there is no router, give them fixed addresses, e.g.
   `192.168.50.1` and `192.168.50.2`, mask `255.255.255.0` (Settings > Network > Ethernet > IP assignment > Manual).
2. On the computer that runs Krypto: `.\run_demo.ps1 -Lan -Offline` (or `-Seal`, below). It prints the address for the other
   computer, e.g. `http://192.168.50.1:8010/ui/`, and adds a firewall rule for that port (subnet only) when run as
   administrator; otherwise it prints the one command to run once in an admin PowerShell.
3. On the other computer open that address and sign in. Live progress, files and downloads all work through the cable.
4. Optional split: keep the web app on one computer and the AI models on the other:
   `.\run_demo.ps1 -OllamaHost http://192.168.50.2:11434` (start Ollama there with `OLLAMA_HOST=0.0.0.0`).

`-Lan` allows connections to **private** addresses only (192.168.x.x, 10.x.x.x, 172.16-31.x.x, 169.254.x.x). With `-Offline`
the internet stays blocked and every connection to another computer on the network is listed. The top bar shows whether the
Ethernet cable is connected, and the address to open on the other computer.

## Sovereignty proof

Open the **shield** in the top bar (or `#/proof`). It shows evidence, not a statement:

- The **operating system's own list** of open connections for Krypto, the AI model server, MongoDB and the search relay
  (read with `psutil`; `netstat -ano` shows the same table), each classified as this computer / local network / internet and
  as incoming (a browser) or outgoing. A sampler records every destination ever seen, and the audit log file
  (`~/.krypto_output/network_audit.log`) keeps them.
- A **self-test** that makes Krypto try to reach the internet three ways (public address, public DNS, a website name); with
  strict offline mode all three are refused.
- The number of refused attempts (0 during normal use), whether any cloud AI client library is installed (none), and the
  model table.

**Third-party helpers.** Programs that sit next to Krypto have habits of their own: the **Ollama tray app** checks
ollama.com for updates every hour, and **Docker Desktop** sends usage statistics and checks for updates. The proof page lists
them separately ("helper") instead of hiding them. To make the whole machine sealed:

```powershell
.\run_demo.ps1 -Seal -Lan        # = -Offline; stops the Ollama tray app and runs `ollama serve` headless (no updater)
.\scripts\seal_network.ps1       # ADMIN shell: Windows Firewall rules that block the internet for every program above
.\scripts\seal_network.ps1 -Undo # remove them again
```

## Checklist for the expected solution

| Requirement | Where to see it |
|---|---|
| Local deployment, small open-weight models | `.\run_demo.ps1`; llama3.2:3b + qwen2.5-coder:1.5b + moondream + nomic-embed-text |
| Model auto-selection across task types | Ask a question, then "simple code for python": result page > *Automatic model selection* |
| Agentic task end to end (scan -> findings -> Word approval note) | Sample report + *Run agent* (1-3 s); `Download` the `.docx` |
| Coding task run and verified in a sandbox | "write a python function to check if a number is prime": *Verified* badge + real output |
| Multimodal: image / scanned document | Upload a picture or scan; ask "describe this picture" or a question about its text |
| No external calls, shown by logs / network monitor | Network badge (top bar), **Sovereignty proof** page, `~/.krypto_output/network_audit.log` |
