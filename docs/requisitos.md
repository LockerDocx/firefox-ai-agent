# Requisitos para que funcione bien

> 🔧 **Instalación paso a paso en tu sistema concreto** (comandos exactos y comprobaciones): [setup-por-sistema.md](setup-por-sistema.md).

Extraído del propio código (no de suposiciones): `pyproject.toml`, `extension/manifest.json`,
`jev_ultrafast/firefox.py`, `neko.py` y `tools.py`. Se separa lo **imprescindible** de lo
**opcional por función**, y qué ocurre exactamente si falta cada cosa.

---

## 1. Imprescindibles (sin esto no arranca)

| Requisito | Detalle real | Si falta |
| --- | --- | --- |
| **Python 3.11+** | `requires-python = ">=3.11"`; los lanzadores buscan `python3.13/3.12/3.11/3` y usan el más nuevo | `start-host.*` avisa con el comando de tu distro (zypper/apt/dnf/pacman) y no arranca |
| **Firefox 109 o superior** | `strict_min_version: "109.0"`, manifiesto v2 con `sidebar_action` | la barra lateral no aparece |
| **La extensión cargada** | `about:debugging` → *Cargar complemento temporal* → `extension/manifest.json`. Pide `tabs`, `storage` y `<all_urls>` | sin ella el host arranca pero no hay con quién hablar |
| **Un modelo *policy*** | `POLICY_PROVIDER` + `POLICY_MODEL` + su clave. Es el ejecutor que decide cada paso | imprime `Policy model: no policy model configured` y no hay agente |
| **Una clave de proveedor** | `GROQ_API_KEY` y/o `NVIDIA_API_KEY` (Groq es gratis). En tu PC van en `.env`; los secretos del repo **solo** sirven para los workflows de GitHub | el rol falla con `No API key for …` |
| **Mismo equipo (loopback)** | El puente escucha en `ws://127.0.0.1:8767` y el GUI en `127.0.0.1:8766`; además validan `Origin`/`Host` por diseño (anti-DNS-rebinding) | no se puede usar desde otro ordenador — ni hace falta |
| **Salida a internet** | HTTPS a los endpoints de los proveedores (`api.groq.com`, `integrate.api.nvidia.com`, …) | el *check* marca 🔴 con el error del proveedor |

> El **planner** y el **text model** son opcionales: sin planner no hay descomposición en pasos;
> sin text model, el agente no puede *escribir* en campos de formulario (el propio check lo dice).

## 2. Configuración recomendada

- **Nada obligatorio**: la clave que el panel pide es la de **NVIDIA**, y esa sola cubre los tres roles con
  `z-ai/glm-5.3-flash` sin razonamiento (el modelo y el «sin pensar» son la elección del 26/09/2026). Si la que
  tienes es la de **Groq**, también funciona todo sola: planner `groq:openai/gpt-oss-120b`, executor y text
  `openai/gpt-oss-20b`. **Se pega en el sidebar de Firefox** (tarjeta de primer arranque, o botón
  *🔑 API keys*); el host la guarda en `.env` y la usa al instante, sin reiniciar nada. Plantilla para
  escribirla a mano: `.env.example`.
- **Overrides opcionales** en `.env`/panel: `POLICY_*`, `PLANNER_*`, `TEXT_MODEL_*` (`PROVIDER`/`MODEL`/`BASE_URL`/`API_KEY`
  por rol). Lo configurado a mano siempre gana a lo derivado. Referencia completa: `docs/providers.md`.
- **Los más rápidos medidos** (26/09/2026, misma clave de NVIDIA): `z-ai/glm-5.3` sin razonamiento —
  *executor* **1,9 s** de mediana (12 llamadas), *text* **1,4 s** (4), *planner* 25,7 s (9). El modelo que se
  envía, `z-ai/glm-5.3-flash` sin razonamiento, midió **37,4 s**, **56,1 s** y **75,7 s** en esos mismos tres
  papeles: en la capa gratuita el coste es la cola, no el tamaño del modelo. Para cambiar de modelo:
  panel **⚙️ Modelos y parámetros** o `POLICY_MODEL=z-ai/glm-5.3` en el `.env`.
- **Instalación**: `uv sync` (hay `uv.lock`) o `python -m venv .venv && pip install -e .`
- **Arranque (opcional)**: `start-host.*` registra el host en Firefox (manifest nativo por usuario, sin admin) y a partir
  de ahí el navegador lo arranca solo al abrir el sidebar. `jev-register-host --unregister` lo revierte;
  `--status` dice si está registrado y hacia qué ejecutable apunta.
- **Cuotas**: si un modelo agota su límite, la app lo marca 🟡 «limitado ahora mismo» — estado
  propio, no un fallo rojo.

## 3. Opcionales, por función

| Función | Requisito | Si falta |
| --- | --- | --- |
| **Leer PDF/Word/Excel** (`parse_document`) | `pip install -e ".[documents]"` → pypdf, python-docx, openpyxl | la herramienta avisa de que no puede leer ese formato |
| **Motor de decisión local** (Laya) | `laya>=0.3,<1` — **se instala solo** en el primer arranque normal (`JEV_LAYA_AUTO=off` para no instalarlo; nunca descarga nada en CI ni en las pruebas) | se usa el enrutado por palabras clave (o `JEV_LAYA=off` para desactivarlo) |
| **Navegador aislado (Neko)** | **Docker** + daemon accesible; imagen `ghcr.io/m1k1o/neko/chromium:latest` (~2 GB); puertos libres `8088` (WebRTC) y `9223` (CDP); contenedor con `--shm-size 2g` | la app lo dice claro: *«Docker was not found on this machine»*, y sigue funcionando en modo pestaña real |
| **Que el sandbox sea *conducible*** | la imagen debe aceptar `NEKO_BROWSER_ARGS=--remote-debugging-port=9222 --remote-debugging-address=0.0.0.0` | si nunca responde, la sesión se marca **«manual»**: se ve por WebRTC, pero el agente no la conduce |
| **Vía suelta `jev` (GUI con Chrome)** | Chrome/Chromium con depuración remota (`chrome://inspect` → *Allow*); `BH_CHROME_PATH` si está en una ruta rara | la vía **Firefox + extensión no lo necesita**: la página la lee la propia extensión |
| **Portapapeles** | `pbcopy`/`pbpaste` (macOS), PowerShell (Windows), `wl-copy`/`wl-paste` (Wayland) o `xclip` (X11) | error explícito: *«El comando … no está instalado»* |
| **Terminal** | shell del sistema (`shell=True`); límites de proceso solo en POSIX | el permiso `terminal=deny` lo bloquea desde el centro de permisos |
| **Firmar el add-on (AMO)** | no hace falta para probar: complemento temporal | desaparece al reiniciar Firefox (30 s de recarga) |

## 4. Sistemas operativos

Windows, macOS y Linux están contemplados: hay lanzadores `start-host.bat` / `.command` / `.sh`
e `install-laya.*`, y el portapapeles y las teclas modificadoras se eligen por plataforma
(`sys.platform` / `os.name`). Guía paso a paso sin consola: **`EMPEZAR-AQUI.md`**.

## 5. Solo para el proyecto (desarrollo y CI)

- `pytest`, `ruff`, `pillow` (grupo `dev`); **Node 20 solo para `node --check`** en CI —
  la extensión **no tiene compilación**: manifiesto v2 y ficheros planos.
- Secretos `NVIDIA_API_KEY` / `GROQ_API_KEY` en GitHub para los workflows
  *Provider check* y *Real provider test*.

## 6. Comprobación en 30 segundos

```bash
python3 -c "import sys; print(sys.version)"     # 3.11 o superior
.venv/bin/jev-firefox                            # imprime ws://127.0.0.1:8767 y el modelo de policy
.venv/bin/python scripts/check_providers.py      # tabla por rol: 🟢 ok · ms / 🟡 cuota / 🔴 error
```

Y dentro de la barra lateral, el botón **Test setup** hace lo mismo sin consola.
Verde en los tres roles = está todo.
