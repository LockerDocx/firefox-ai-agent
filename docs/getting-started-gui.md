# Getting started without a console (GUI-only guide)

> 🇪🇸 **¿Español o primera vez absoluta?** Lee primero **[EMPEZAR-AQUI.md](../EMPEZAR-AQUI.md)** — el manual paso a paso más detallado.

You can use, install, and even re-publish this project **without opening a terminal**. This guide covers download, first run, the Firefox extension, and how to put the project on your own GitHub using only the web interface.

## What you need

- **Firefox** (109 or newer).
- **Python 3.11+** from [python.org/downloads](https://www.python.org/downloads/) — on Windows, tick *"Add python.exe to PATH"* during install. On Linux the starter tells you the exact command if it is missing:

| System | Install Python 3.11+ |
| --- | --- |
| openSUSE / SUSE (Leap, Tumbleweed) | `sudo zypper install python312 python312-pip` |
| Ubuntu / Debian / Mint | `sudo apt update && sudo apt install python3 python3-pip python3-venv` |
| Fedora / RHEL / Rocky | `sudo dnf install python3.12 python3-pip` |
| Arch / Manjaro | `sudo pacman -S python` |
| macOS | `brew install python@3.12` (or python.org) |
| Windows | python.org installer, tick *Add python.exe to PATH* |

openSUSE Leap 15.6's own `python3` is Python 3.6 (the one YaST uses), so on Leap the zypper command above is required; on Tumbleweed the shipped `python3` is already 3.13 and nothing has to be installed. The bar is 3.11 because that is the oldest interpreter current distributions still ship as an option. The starter looks for `python3.13`, `python3.12`, `python3.11`, `python3` in that order and uses the newest one it finds, so the 3.6 on Leap is never picked.
- **One free API key** — an NVIDIA NIM key ([build.nvidia.com](https://build.nvidia.com) → *API Keys*). It is the only one needed: the planner, executor and text helper all run `z-ai/glm-5.3-flash` with thinking off. A Groq key ([console.groq.com](https://console.groq.com)) is optional, answers in a few hundred milliseconds, and is limited to 8 000 tokens/minute on the free tier.

> 📚 **Setup and daily use, system by system** (openSUSE/SUSE, Ubuntu/Debian/Mint, Fedora/RHEL/Rocky, Arch,
> Windows, macOS, snap/Flatpak Firefox) — exact commands, verification and uninstall:
> **[setup-por-sistema.md](setup-por-sistema.md)** *(in Spanish)*.

## 1. Get the code

Two ways, both point-and-click:

- **From the repository page**: the green **Code** button → **Download ZIP** → unzip it wherever you like.
- **From Releases**: ready-made artifacts — the source ZIP and the extension as a single `.xpi` file you can hand to Firefox directly.

## 2. Start the host (double-click)

The agent runs on your machine as a small local process ("the host"). Unzip the project and double-click the starter for your system:

| System | Double-click | What happens |
| --- | --- | --- |
| Windows | `start-host.bat` | First run: creates a private Python environment (about a minute, internet needed), registers the host with Firefox, then starts it. Nothing to type here. |
| macOS | `start-host.command` | Same. If macOS blocks it: right-click → **Open**. |
| Linux | `start-host.sh` | Same (run it from your file manager). |

**First run flow:** the starter creates `.env`, installs what it needs and starts the host. That window
then only reports status — **the setup lives in Firefox**. Keep it open, install the sidebar (step 3)
and paste your one free key in the panel:

```
🔑 One free key starts the agent
NVIDIA NIM · one key runs all three roles (recommended)      get one ↗
[ paste your key… ]                                          [ Save ]
```

A Groq key already in `.env` is not offered and not used: the card names it (*"Also on this machine… Not
used"*) with the reason, so a key that is idle cannot be mistaken for a broken setup.

Pressing **Save** writes `NVIDIA_API_KEY` to `.env`, exports it immediately and re-tests every role, so
the panel goes straight to `Ready — planner … · policy …`. No restart, no file editing; the key is
never sent anywhere except the provider you chose. Paste a second key later (the **🔑 API keys**
button in *Models & parameters*) to upgrade the planner to `z-ai/glm-5.3`. (Prefer editing files?
Copy `.env.example` to `.env` and fill in the lines you want — both paths are equivalent.)

Every option is described in [providers.md](providers.md), and to change what runs, use the **Models & parameters** panel in the sidebar.

## 2b. After the first run you can forget about the starter

That first run registers the host as a **native messaging host** for your user (one JSON manifest in
Firefox's own folder; on Windows the same file plus one `HKEY_CURRENT_USER` value — no administrator
rights, nothing system-wide). From then on:

- opening the **sidebar** starts the host by itself, with no window and no port;
- closing Firefox stops it; nothing stays running in the background;
- the host is only reachable through the browser, and only for this add-on id
  (`allowed_extensions`), which is stronger than a shared secret;
- **moving the project folder** breaks the registration until you run the starter once more (it
  re-registers with the new paths).

Firefox deliberately never lets an extension install local software on its own: that one-time
registration is the browser's security model, and the starter is what does it for you. Sandboxed
builds (Ubuntu's *snap*, Flatpak) route the launch through a system permission: Firefox asks the
first time, and if it is denied — or the portal is missing — the starter window remains the way to
run the agent, with everything else identical.

## 3. Install the extension in Firefox

1. Open Firefox and type `about:debugging` in the address bar.
2. Click **This Firefox** → **Load Temporary Add-on…**
3. Pick `extension/manifest.json` inside the project folder (or the `.xpi` from the Release page — the file picker filters by type; choose *All files* if needed).
4. Open the sidebar with the agent's toolbar button (or menu → View → Sidebar → **AI Agent**).

The dot in the sidebar header turns **green** when it reaches the host. If it stays red, start the host first (step 2).

> The add-on is unsigned, so Firefox treats it as *temporary*: it disappears when Firefox restarts. Load it again after a restart — or sign it via [addons.mozilla.org](https://addons.mozilla.org/developers/) for a permanent install.

## 4. Run your first task

1. Navigate to any normal website in the current tab.
2. Type a goal in the sidebar, e.g. *“Find one-way flights from Zurich to London on September 20, 2026”*.
3. Press **Run** and watch the checklist fill with ✓ as steps complete. **Stop** halts after the current action.

## 5. Put it on your own GitHub (no terminal)

Four point-and-click options, best first:

| Method | How | Keeps history |
| --- | --- | --- |
| **Fork** (simplest) | Click **Fork** at the top of the repository page. You get your own copy instantly. | Yes |
| **Import repository** | Go to [github.com/new/import](https://github.com/new/import), paste the repository URL, choose a name, click **Begin import**. | Yes |
| **Web upload** | Create a new empty repository → **"uploading an existing file"** → drag the *contents* of the unzipped folder into the browser (Chrome/Edge; folder drag works). This project is ~60 files, under GitHub's 100-file-per-upload limit — one drag is enough. | No |
| **GitHub Desktop** | Install [desktop.github.com](https://desktop.github.com), clone the original, then *Repository → Add remote* / push to your new empty repository. GUI app, no terminal. | Yes |

> When uploading manually, **never upload your `.env`** — it holds your API keys. The `.gitignore` already excludes it for git-based methods.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Python 3.11 or newer is required` | The message names your system's command (zypper/apt/dnf/pacman) or the python.org download link. |
| Starter window closes instantly | Open it from a terminal once to read the error, or reinstall Python with *Add to PATH*. |
| Sidebar dot stays red | Opening the sidebar starts the host by itself; if it does not, that Firefox cannot launch local programs (snap/Flatpak builds) or the project folder moved — double-click the starter once, keep that window open. |
| `Model provider returned HTTP 401` | The provider rejected the key: paste a fresh one with **🔑 API keys** in the models panel (no restart needed). |
| Sidebar says *offline* right after Firefox restarts | Temporary add-ons are removed on restart — load it again via `about:debugging`. |
| Agent refuses to act on a page | Privileged pages (`about:*`, add-ons manager) cannot be scripted; use a normal website. |

Next: the full architecture and security model live in [firefox-extension.md](firefox-extension.md).
