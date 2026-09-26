# Setup y uso, sistema por sistema — **AI Agent for Firefox** v0.12.2

Guía definitiva: qué haces **una vez** (setup) y qué haces **cada día** (uso), con los comandos exactos de tu sistema.

- **Parte A** — lo común a todos: 5 pasos, primer uso, uso diario. **Léela entera, son 3 minutos.**
- **Parte B** — tu sistema: openSUSE/SUSE · Ubuntu/Debian/Mint · Fedora/RHEL/Rocky · Arch/Manjaro · Windows · macOS.
- **Parte C** — actualizar, mover, desinstalar, y los problemas típicos de cada sistema.

> **Resumen si tienes prisa:** descargar el ZIP → conseguir una clave gratis de NVIDIA (una clave = los tres papeles, con `z-ai/glm-5.3-flash` sin razonamiento) → **un doble clic** (solo la primera vez) → cargar el `.xpi` en `about:debugging` → pegar la clave en el panel lateral. Desde ese momento, **abrir el panel arranca el agente solo**.

---

## Índice

- [A · Lo común: el setup en 5 pasos](#a--lo-común-el-setup-en-5-pasos)
  - [Paso 1 · Descargar y descomprimir](#paso-1--descargar-y-descomprimir)
  - [Paso 2 · La clave gratis](#paso-2--la-clave-gratis)
  - [Paso 3 · El doble clic (una sola vez)](#paso-3--el-doble-clic-una-sola-vez)
  - [Paso 4 · Cargar el add-on en Firefox](#paso-4--cargar-el-add-on-en-firefox)
  - [Paso 5 · Pegar la clave y probarlo](#paso-5--pegar-la-clave-y-probarlo)
  - [Las 4 comprobaciones de que está todo bien](#las-4-comprobaciones-de-que-está-todo-bien)
  - [Uso diario: cómo se usa](#uso-diario-cómo-se-usa)
  - [Dónde queda todo](#dónde-queda-todo)
- [B · Tu sistema](#b--tu-sistema)
  - [1 · openSUSE / SUSE (Leap, Tumbleweed, SLES)](#1--opensuse--suse-leap-tumbleweed-sles)
  - [2 · Ubuntu / Debian / Linux Mint](#2--ubuntu--debian--linux-mint)
  - [3 · Fedora / RHEL / Rocky / AlmaLinux](#3--fedora--rhel--rocky--almalinux)
  - [4 · Arch / Manjaro / EndeavourOS](#4--arch--manjaro--endeavouros)
  - [5 · Windows 10 y 11](#5--windows-10-y-11)
  - [6 · macOS](#6--macos-plataforma-original)
  - [7 · Firefox instalado como snap o Flatpak (Linux)](#7--firefox-instalado-como-snap-o-flatpak-linux)
- [C · Después del setup](#c--después-del-setup)
  - [Actualizar a una versión nueva](#actualizar-a-una-versión-nueva)
  - [Mover la carpeta de sitio](#mover-la-carpeta-de-sitio)
  - [Apagar y salir](#apagar-y-salir)
  - [Desinstalar del todo](#desinstalar-del-todo)
  - [Comprobar desde la terminal](#comprobar-desde-la-terminal)
  - [Problemas, por sistema](#problemas-por-sistema)
  - [Apéndice · Docker para el navegador aislado (opcional)](#apéndice--docker-para-el-navegador-aislado-opcional)

---

# A · Lo común: el setup en 5 pasos

## Paso 1 · Descargar y descomprimir

1. Descarga el proyecto, de una de las dos formas:
   - **Código**: https://github.com/LockerDocx/firefox-ai-agent → botón verde **Code** → **Download ZIP**
   - **Release** (recomendado): https://github.com/LockerDocx/firefox-ai-agent/releases/latest → `firefox-ai-agent-v0.12.2-source.zip`
2. **Descomprímelo** en un sitio que no vayas a mover después (Escritorio, Documentos, o `C:\Jev` en Windows).
   - Windows: clic derecho sobre el ZIP → **Extraer todo…**
   - macOS / Linux: doble clic sobre el ZIP
3. ⚠️ **No lo ejecutes desde dentro del ZIP.** Tiene que estar extraído.
4. ✓ **Comprobación**: dentro de la carpeta ves `start-host.bat`, `start-host.sh`, `start-host.command` y una carpeta `extension`.

> La carpeta que eliges pasa a ser "la carpeta del proyecto". Si la mueves de sitio más adelante, vuelve a hacer el [Paso 3](#paso-3--el-doble-clic-una-sola-vez) una vez.

## Paso 2 · La clave gratis

El agente necesita una clave de IA (gratis). Se consigue en **NVIDIA** y **esa sola clave cubre los tres papeles** con `z-ai/glm-5.3`:

1. Abre **https://build.nvidia.com** → *Login* (vale la cuenta de Google)
2. Tu perfil → **API Keys** → **Generate API Key**
3. Copia el texto que empieza por **`nvapi-...`** (botón de copiar 📋)

> ⚡ **Opcional**: la clave de **Groq** (https://console.groq.com/keys) es más rápida por llamada, pero su plan gratuito son **8 000 tokens por minuto** y el ejecutor los gasta a mitad de misión (error 429). **Con la de NVIDIA ya funciona todo.**

## Paso 3 · El doble clic (una sola vez)

Entra en la carpeta del proyecto y haz **doble clic** en el arranque de tu sistema:

| Sistema | Archivo |
| --- | --- |
| Windows | **`start-host.bat`** |
| macOS | **`start-host.command`** *(la primera vez: clic derecho → **Abrir** → **Abrir**)* |
| Linux (openSUSE, Ubuntu, Fedora, Arch…) | **`start-host.sh`** |

Qué pasa la primera vez: prepara todo él solo (~1 minuto, necesita internet) y deja el agente **registrado en Firefox**. Al final verás:

```
Registering the host with Firefox...
  Done: from now on the sidebar starts Jev by itself - you will not need this window again.
  This one stays open only as a fallback: if the panel says offline, it is to blame.
```

**A partir de aquí ese doble clic ya no hace falta:** cuando abras el panel, Firefox arranca el agente por su cuenta, sin ninguna ventana. La ventana que se ha abierto puedes dejarla abierta o cerrarla: solo es el plan B.

> Si tu sistema no tiene Python 3.11 o superior, la ventana **no se cierra**: te dice qué falta y **el comando exacto para tu sistema**. Lo instalas y repites el doble clic. (Está en la Parte B, sistema por sistema.)

## Paso 4 · Cargar el add-on en Firefox

1. En la barra de direcciones de Firefox escribe **`about:debugging`** y pulsa Enter
2. En el menú izquierdo, **This Firefox** (en español: **Este Firefox**)
3. Botón **Load Temporary Add-on…** (**Cargar complemento temporal…**)
4. Elige **un archivo**, la opción que te sea más cómoda:
   - ⭐ **El `.xpi`**: descarga el `.xpi` (`ai-agent-for-firefox-<versión>.xpi`) de la página **Releases** y selecciónalo tal cual (un solo archivo, lo más simple)
   - **O desde la carpeta**: entra en la carpeta del proyecto → subcarpeta **`extension`** → selecciona **`manifest.json`**
5. ✓ En la lista debe aparecer **"AI Agent for Firefox"**

> ⚠️ **"Temporal" significa que al cerrar Firefox desaparece.** Es normal: aún no está firmado. Cada vez que reinicies Firefox, repite este paso (30 segundos). **No** hace falta repetir el Paso 3.

## Paso 5 · Pegar la clave y probarlo

1. Arriba a la derecha en Firefox, pulsa el **icono del agente** en la barra de herramientas.
   - ¿No lo ves? Pieza de puzzle 🧩 → **AI Agent**, o menú **☰ → Panel lateral → AI Agent**.
2. Se abre el **panel lateral**. Mira el puntito de arriba a la derecha:
   - 🟢 **host online** = conectado ✓
   - 🔴 **offline** = Firefox no pudo arrancar el agente → haz **doble clic en el starter** (Paso 3) **y deja esa ventana abierta**: todo funciona igual. (Pasa sobre todo si moviste la carpeta, o con Firefox *snap*/*Flatpak*.)
3. **Pega la clave** en la tarjeta que te pide ("🔑 One free key starts the agent") y pulsa **Save**. No hay que editar ningún archivo. Debajo verás qué ha quedado configurado, por ejemplo:
   ```
   Ready — planner groq:openai/gpt-oss-120b · policy groq:openai/gpt-oss-20b · text groq:openai/gpt-oss-20b
   ```
4. Pulsa **Test setup**: el panel prueba cada modelo y te da el resultado exacto.
   - 🟢 `Executor · groq:openai/gpt-oss-20b 267 ms` = tu clave funciona ✓
   - 🔴 algo rojo = lee el mensaje: dice exactamente qué falla (clave mal pegada, sin saldo, modelo inexistente…)
   - ⏳ mientras prueba verás `Testing every model connection… N s` **con los segundos corriendo**: es normal. Los roles se prueban uno detrás de otro y un endpoint gratis que estaba dormido puede tardar un minuto. Espera; no hace falta pulsar nada.
   - Si un rol sale rojo con **“Model connection failed … not a rejected key”**: tu clave está bien, es el proveedor que no contestó a tiempo. Pulsa **Test setup** otra vez, o sube la paciencia en `.env` con `JEV_HTTP_TIMEOUT=120` (segundos) y reinicia el starter. **No generes una clave nueva por esto.**
5. Escribe una misión de prueba y pulsa **Run**:
   > Busca el artículo de la Wikipedia sobre la Torre Eiffel y ábrelo.

   Si estás en una pestaña vacía no pasa nada: el agente abre DuckDuckGo y trabaja allí. Si quieres que trabaje en una web concreta, **navega a ella antes** de pulsar Run.
6. 🍿 Mira tu Firefox: aparece el **Plan** con pasos que se van marcando ✓, la página se mueve sola, escribe, hace clic… y los **Steps** van apareciendo en el panel.

---

## Las 4 comprobaciones de que está todo bien

| # | Comprueba | Debe verse |
| --- | --- | --- |
| 1 | Última línea del starter (Paso 3) | `Done: from now on the sidebar starts Jev by itself` |
| 2 | Puntito del panel (Paso 5) | 🟢 **host online** |
| 3 | Botón **Test setup** (Paso 5) | Todas las líneas 🟢 con su tiempo en ms |
| 4 | Misión de prueba (Paso 5) | El Plan se marca ✓ y la respuesta aparece arriba |

Con esas 4 en verde, está perfecto. Lo demás es uso.

---

## Uso diario: cómo se usa

### Lo básico (es siempre igual en todos los sistemas)

1. **Abre el panel**: icono del agente en la barra de herramientas (o ☰ → Panel lateral → AI Agent). El agente arranca solo.
2. **Navega** a la web donde quieras que trabaje (o déjalo: abrirá un buscador).
3. **Escribe la misión** en el cuadro de abajo y pulsa **Run**.
4. Mira el **Plan** (los pasos, con ✓ al completarse) y los **Steps** (qué herramienta usó en cada uno: 🔧, y la respuesta final con 🏁).
5. **Stop** lo detiene cuando quieras: termina la acción en curso y se para.

Ejemplos de misiones que funcionan bien:

- *Busca vuelos de ida de Barcelona a Roma el 20 de junio para 1 adulto y para cuando se vean los resultados*
- *Busca el precio del euro hoy y guárdalo en un archivo llamado euro.txt*
- *Descarga un PDF sobre cambio climático y resúmelo*
- *Crea un script de python que diga hola y ejecútalo*

### El candado 🔐: cuando el agente pide permiso

Si el agente quiere hacer algo "con efectos" (ejecutar un comando, instalar, crear archivos), aparece un aviso amarillo arriba con **el comando exacto** y dos botones: **Approve** / **Deny**.

- Si no contestas en **2 minutos**, se considera **Deny**. Seguridad máxima por defecto.
- Los comandos de solo lectura (`ls`, `git status`) no preguntan.
- Los peligrosos (`sudo`, `rm -rf`…) se bloquean solos, siempre.

### ⚙️ Models & parameters: cambiar de modelo sin tocar ficheros

Clic en la línea **"⚙️ Models & parameters"** para desplegarla:

- Botones rápidos: **Fast**, **Balanced**, **Deep**, **Browser**, **Coding** (ajustes de conjunto).
- Tres desplegables: **Planner**, **Executor**, **Text writer**. Elige otro modelo y **se guarda solo** (se mantiene al reiniciar).
- **Refresh catalogue** recarga la lista del proveedor (la primera vez puede tardar unos segundos).
- **advanced parameters** muestra solo los controles que **ese modelo concreto** acepta: si algo no lo admite, desaparece del panel en vez de enviarse.
- **Profile name…** + **Save** guarda tu combinación con un nombre; **Reset to defaults** vuelve al ajuste recomendado.

### 🔐 Permissions: tú decides hasta dónde llega

Panel **"🔐 Permissions"** con 6 ámbitos × 3 niveles (`allow` / `ask` / `deny`):

| Ámbito | Controla |
| --- | --- |
| **Browser** | misiones en tu pestaña y el paso de navegación |
| **Terminal** | comandos de terminal (`ask` = pregunta siempre) |
| **Files** | escribir archivos en `workspace/` |
| **Network** | buscar en la web y leer páginas |
| **Clipboard** | leer/escribir el portapapeles (por defecto *ask*) |
| **Downloads** | descargar archivos (por defecto *allow*, tope 25 MB) |

Los comandos destructivos y la lectura de secretos siguen bloqueados **aunque pongas `allow`**. Todo cambio y toda aprobación queda registrado en `artifacts/audit.jsonl`.

### 🧪 Browser target: tu pestaña o un navegador aislado

Arriba del panel, **Browser target** con dos botones:

- **My current tab** — lo normal: trabaja en tu pestaña actual.
- **Isolated browser** (Neko, en Docker) — trabaja en un navegador aparte, sin tocar tus cuentas ni cookies. **Start session** arranca (necesita Docker, ver apéndice), **Watch it** lo ves en directo, **Stop session** lo apaga.

### 🔑 API keys: cambiar o añadir claves

Botón **🔑 API keys** → pega la nueva → **Save**. Se aplica **al momento, sin reiniciar nada** (no hay que editar ningún fichero: el panel lo escribe por ti).

---

## Dónde queda todo

Dentro de la carpeta del proyecto:

| Ruta | Qué es |
| --- | --- |
| `.env` | tus claves y ajustes (lo escribe el panel por ti) |
| `workspace/` | los archivos que el agente crea o descarga. **No puede salir de ahí.** |
| `artifacts/audit.jsonl` | registro de aprobaciones y cambios de permisos |
| `artifacts/runs.jsonl` | historial de misiones |
| `artifacts/model-config.json` | los modelos y parámetros que elegiste |
| `.venv/` | el entorno privado de Python (lo crea el starter) |

---

# B · Tu sistema

## 1 · openSUSE / SUSE (Leap, Tumbleweed, SLES)

### Python que necesitas

| Distribución | Comando | Detalle |
| --- | --- | --- |
| **openSUSE Leap 15.6** | `sudo zypper install python312 python312-pip` | ⚠️ el `python3` del sistema es **3.6** (el de YaST) y no sirve: hay que instalar el 3.12 |
| **openSUSE Leap 16** | `sudo zypper install python313 python313-pip` | comprueba cuál ofrece tu repositorio con `zypper search python3` |
| **openSUSE Tumbleweed** | *nada que instalar* | su `python3` ya es 3.13 ✓ |
| **SUSE Linux Enterprise 15 SP6+** | `sudo zypper install python312 python312-pip` | igual que Leap; si tu repositorio no lo tiene, `zypper search python3` te dice qué versión sí |

> Los paquetes de SUSE **no llevan punto**: es `python312`, nunca `python3.12`. (Es el error más común; el comando de arriba es el bueno.)

### Puesta en marcha

1. Abre una terminal **en la carpeta del proyecto**, de la forma que te resulte más fácil:
   - **Dolphin (KDE)**: clic derecho sobre la carpeta → **Abrir terminal**
   - **Nautilus (GNOME)**: clic derecho → **Abrir en terminal** (si no aparece esa opción) → abre **Konsole**/GNOME Terminal desde el menú de aplicaciones y **arrastra la carpeta dentro de la ventana**: escribe la ruta por ti
2. (Si hace falta Python) pega el `sudo zypper install …` de tu fila, introduce tu contraseña y espera.
3. **Doble clic en `start-host.sh`.** Si no arranca, en la terminal:
   ```bash
   chmod +x start-host.sh
   bash start-host.sh
   ```
4. Deja la ventana abierta; sigue con el [Paso 4](#paso-4--cargar-el-add-on-en-firefox) de la Parte A.

### Comprobar que quedó bien registrado

```bash
.venv/bin/jev-register-host --status
```

Debe salir algo así (con tu ruta):

```
registered: True  host: /home/tu-usuario/…/.venv/bin/jev-firefox-native  present: True
```

`registered: True` + `present: True` = Firefox sabe arrancar el agente y el programa existe. Si sale `False`, haz un doble clic en el starter y vuelve a mirar. El manifiesto vive en `~/.mozilla/native-messaging-hosts/jev_ultrafast_host.json`.

### Trampas de este sistema

- **`python3 --version` te dice 3.6** en Leap 15.6 → es normal, **no** está roto nada: instala el 3.12 con zypper y el arranque elige ese solo (busca 3.13 → 3.12 → 3.11 → `python3`, y el 3.6 queda descartado).
- **Doble clic sin efecto** (los gestores de archivos modernos no ejecutan scripts): usa la terminal del paso 3 (`bash start-host.sh`), es exactamente lo mismo.
- **En SLES/Leap de empresa** puede estar activado `SELinux`/AppArmor con perfiles estrictos: si el registro falla, ejecuta el `--status` de arriba y lee el mensaje; el modo respaldo (ventana abierta) funciona igual.

---

## 2 · Ubuntu / Debian / Linux Mint

### Python que necesitas

```bash
sudo apt update && sudo apt install python3 python3-pip python3-venv
```

| Versión | Trae | ¿Vale? |
| --- | --- | --- |
| Ubuntu 24.04 · 24.10 · 25.x | Python 3.12 / 3.13 | ✅ con el comando de arriba |
| Ubuntu 23.04+ · Debian 12 | Python 3.11 / 3.12 | ✅ |
| **Ubuntu 22.04 · Linux Mint 21** | **Python 3.10** | ❌ demasiado antiguo → instala el 3.12 con el PPA: <br> `sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt update && sudo apt install python3.12 python3.12-venv` |
| Debian 13 | Python 3.13 | ✅ |

> El paquete **`python3-venv`** no es opcional en Debian/Ubuntu: sin él, el starter no puede crear su entorno privado.

### Puesta en marcha

1. Terminal en la carpeta del proyecto (Nautilus: clic derecho → **Abrir en terminal**; o `Ctrl+Alt+T` y `cd`).
2. El `sudo apt install …` de arriba.
3. **Doble clic en `start-host.sh`**, o si no arranca:
   ```bash
   chmod +x start-host.sh && bash start-host.sh
   ```
4. Continúa con el [Paso 4](#paso-4--cargar-el-add-on-en-firefox).

### Trampas de este sistema

- **Firefox de Ubuntu es un *snap*** → ver el [apartado 7](#7--firefox-instalado-como-snap-o-flatpak-linux). El modo respaldo (dejar la ventana del starter abierta) funciona siempre.
- **Ubuntu 22.04 / Mint 21 dicen "Python 3.10"**: tenías el comando del PPA arriba; el starter usará `python3.12` solo.

---

## 3 · Fedora / RHEL / Rocky / AlmaLinux

### Python que necesitas

| Distribución | Comando |
| --- | --- |
| **Fedora 40+** | `sudo dnf install python3 python3-pip` (su `python3` ya es 3.12/3.13) |
| **RHEL / Rocky / Alma 9** | ⚠️ su `python3` es **3.9** (no vale) → `sudo dnf install python3.11 python3.11-pip` |
| **RHEL / Rocky / Alma 10** | `sudo dnf install python3 python3-pip` (ya es 3.12+) |

### Puesta en marcha

1. Terminal en la carpeta del proyecto (Dolphin: clic derecho → Abrir terminal · Nautilus: ver el apartado de openSUSE, arriba).
2. El `sudo dnf install …` de tu fila.
3. **Doble clic en `start-host.sh`**, o:
   ```bash
   chmod +x start-host.sh && bash start-host.sh
   ```
4. Continúa con el [Paso 4](#paso-4--cargar-el-add-on-en-firefox).

### Trampas de este sistema

- **RHEL/Rocky 9**: el sistema trae 3.9 y `dnf install python3` no te lo soluciona. Instala **3.11** (paquete `python3.11`) y el arranque lo preferirá solo.

---

## 4 · Arch / Manjaro / EndeavourOS

### Python que necesitas

```bash
sudo pacman -S python        # normalmente ya lo tienes; es 3.13/3.14 y vale
sudo pacman -S python-pip    # opcional
```

### Puesta en marcha

1. Terminal en la carpeta del proyecto.
2. **Doble clic en `start-host.sh`**, o:
   ```bash
   chmod +x start-host.sh && bash start-host.sh
   ```
3. Continúa con el [Paso 4](#paso-4--cargar-el-add-on-en-firefox).

### Trampas de este sistema

- **Arch evoluciona rápido**: si dentro de un año una versión nueva de Python rompiera algo, el CI del proyecto prueba 3.11, 3.12 y 3.13; actualiza el proyecto ([Parte C](#actualizar-a-una-versión-nueva)).

---

## 5 · Windows 10 y 11

### Python que necesitas

1. Descarga el instalador de **https://www.python.org/downloads/** (3.12 o superior).
2. En la **primera pantalla** del instalador, marca la casilla **"Add python.exe to PATH"** ← crítico.
3. Pulsa **Install Now** y espera.

> ⚠️ **No uses el `python` de la Microsoft Store.** Si al escribir `python` en una terminal se abre la Tienda, es el *stub* de Windows: no sirve. Instala el de python.org con la casilla marcada. El `.bat` detecta ese caso y te lo dice con este texto: *"If a Microsoft Store window opened instead, that is the Store stub"*.

### Puesta en marcha

1. Abre la carpeta del proyecto en el Explorador.
2. **Doble clic en `start-host.bat`**.
   - Windows abre una ventana negra de comandos. **No la cierres** la primera vez.
   - La primera vez tarda ~1 minuto (descarga dependencias).
3. **Si Windows muestra "Windows protegió tu PC"** (SmartScreen, porque el archivo viene de internet):
   → **Más información** → **Ejecutar de todas formas**. Es normal en archivos descargados; pasa solo la primera vez.
4. Al final de la ventana verás:
   ```
   Registering the host with Firefox...
     Done: from now on the sidebar starts Jev by itself, no window needed.
   Host starting. KEEP THIS WINDOW OPEN while you use the sidebar.
   ```
5. Sigue con el [Paso 4](#paso-4--cargar-el-add-on-en-firefox). En el punto 4 del Paso 4, la ruta del `.xpi` estará en tu carpeta de **Descargas**.

### Comprobar que quedó bien registrado

En la carpeta del proyecto, escribe `cmd` en la barra de direcciones del Explorador (abre la terminal ahí) y pega:

```
.venv\Scripts\jev-register-host --status
```

Debe salir algo así (con tu ruta):

```
registered: True  host: C:\...\.venv\Scripts\jev-firefox-native.exe  present: True
```

### Detalles de Windows que conviene saber

- **No hace falta ser administrador** y **no hace falta PowerShell**: el registro es para tu usuario (HKEY_CURRENT_USER) y lo hace el `.bat` solo. Si algo te pide permisos de administrador, no es nuestro.
- Si un antivirus o el Firewall pregunta: el agente escucha **solo en `127.0.0.1`** (tu propio PC, nada expuesto a internet). Permitir en redes privadas es suficiente.
- El registro deja dos cosas, ambas borrables con el desinstalador del [final](#desinstalar-del-todo):
  - clave `HKEY_CURRENT_USER\SOFTWARE\Mozilla\NativeMessagingHosts\jev_ultrafast_host`
  - archivo `%APPDATA%\Mozilla\NativeMessagingHosts\jev_ultrafast_host.json`
- **Cierra Firefox antes** de mover la carpeta o desinstalar, para que suelte el agente.

---

## 6 · macOS (plataforma original)

### Python que necesitas

Opción A (recomendada), con Homebrew:

```bash
brew install python@3.12
```

Opción B: instalador de **https://www.python.org/downloads/** (doble clic, Siguiente, Siguiente).

> El Python que trae macOS de fábrica (3.9) **no vale**; hace falta uno de 3.11 o superior.

### Puesta en marcha

1. En el Finder, ve a la carpeta del proyecto.
2. **Clic derecho en `start-host.command` → Abrir → Abrir.** (La primera vez, macOS bloquea el doble clic directo porque el archivo viene de internet: ese "Abrir" del menú contextual es el permiso. Solo la primera vez.)
3. Si macOS pide permiso para que "Terminal" acceda a los archivos de esa carpeta (Escritorio, Documentos o Descargas), **permitir**.
4. Sigue con el [Paso 4](#paso-4--cargar-el-add-on-en-firefox).

### Trampas de este sistema

- Si el doble clic no hace nada: **Terminal** → arrastra la carpeta dentro de la ventana → `./start-host.command`.
- Apple Silicon (M1/M2/M3/M4) y Intel funcionan igual.

---

## 7 · Firefox instalado como snap o Flatpak (Linux)

Si tu Firefox es el **snap** de Ubuntu o un **Flatpak**, el navegador lanza programas locales a través de un permiso del sistema. Dos cosas que conviene saber:

1. **Normalmente funciona**: Firefox pide el permiso la primera vez (o lo tiene concedido). Si el panel dice **🔴 offline**, ese permiso está denegado o no se concedió.
2. **Solución garantizada (modo respaldo)**: doble clic en el starter, **deja la ventana abierta** y todo funciona igual — el panel se conecta por sí solo. No tienes que pelear con permisos.

Si quieres arreglarlo del todo (opcional, avanzado), se concede así:

```bash
# Firefox Flatpak
flatpak permission-set webextensions jev-ultrafast@custom-web-ultrafast org.mozilla.firefox yes
```

Y si usas el Firefox **.deb** oficial de Mozilla (el de mozilla.org), no hay nada de esto: funciona directo.

---

# C · Después del setup

## Actualizar a una versión nueva

1. Descarga el ZIP nuevo (o `git pull` si usas git) y descomprímelo **encima** de tu carpeta.
2. **Un doble clic** en el starter de tu sistema otra vez (reconstruye lo que haga falta) y espera el `Done`.
3. En Firefox: `about:debugging` → quita el add-on antiguo si sigue ahí → **Load Temporary Add-on…** → el `.xpi` nuevo. (30 segundos.)
4. Tus claves, modelos y permisos **se conservan**: viven en `.env` y `artifacts/`.

## Mover la carpeta de sitio

Mueve la carpeta y haz **un doble clic** en el starter: vuelve a registrar la ruta nueva. Nada más.

## Apagar y salir

- **Cerrar Firefox detiene el agente.** No queda nada corriendo.
- La ventana del starter (si la dejaste abierta) se cierra con la X o `Ctrl+C` en la terminal.
- Para que no arranque solo: cierra Firefox, o pon el panel en pausa quitando el add-on temporal.

## Desinstalar del todo

1. **Quitar el registro de Firefox** (desde la carpeta del proyecto):
   - Windows: `.venv\Scripts\jev-register-host --unregister`
   - Linux/macOS: `.venv/bin/jev-register-host --unregister`
2. **Quitar el add-on de Firefox**: `about:debugging` → *Este Firefox* → **AI Agent for Firefox** → **Remove**. (El temporal desaparece igual al cerrar Firefox.)
3. **Borrar la carpeta del proyecto.** Ahí vivían tus claves (`.env`), los registros y los archivos que creó (`workspace/`).

## Comprobar desde la terminal

```bash
# ¿Está registrado el agente para Firefox?
.venv/bin/jev-register-host --status          # Windows: .venv\Scripts\jev-register-host --status

# ¿Con qué Python se preparó?
.venv/bin/python --version
```

## Problemas, por sistema

| Sistema | Síntoma | Causa y solución |
| --- | --- | --- |
| **openSUSE Leap 15.6** | *"Python 3.11 or newer is required"* aunque tienes `python3` | Tu `python3` es 3.6. `sudo zypper install python312 python312-pip` y repite el doble clic |
| **openSUSE (todos)** | `zypper install python3.12` → *not found in package names* | Los nombres no llevan punto: `python312` |
| **Ubuntu 22.04 / Mint 21** | Dice que tu Python es 3.10 | Instala 3.12 con el PPA de deadsnakes ([apartado 2](#2--ubuntu--debian--linux-mint)) |
| **RHEL/Rocky/Alma 9** | Su `python3` es 3.9 | `sudo dnf install python3.11 python3.11-pip` |
| **Cualquier Linux** | Doble clic no hace nada | `chmod +x start-host.sh && bash start-host.sh` |
| **Ubuntu (Firefox snap)** | Panel 🔴 offline | Deja la ventana del starter abierta (o el permiso del [apartado 7](#7--firefox-instalado-como-snap-o-flatpak-linux)) |
| **Windows** | Se abre la Microsoft Store al escribir `python` | Es el *stub*: instala python.org marcando *Add python.exe to PATH* |
| **Windows** | *"Windows protegió tu PC"* al abrir el `.bat` | **Más información → Ejecutar de todas formas** (solo la primera vez) |
| **Windows** | La ventana negra se cierra al instante | Reinstala Python con *Add python.exe to PATH* marcado y repite |
| **macOS** | *"no se puede abrir porque proviene de un desarrollador no identificado"* | Clic derecho en `start-host.command` → **Abrir** → **Abrir** |
| **Todos** | Panel 🔴 offline tras mover la carpeta | Un doble clic en el starter (lo vuelve a registrar) |
| **Todos** | El add-on desapareció al reiniciar Firefox | Es temporal: repite el [Paso 4](#paso-4--cargar-el-add-on-en-firefox) |
| **Todos** | Un modelo en 🔴 | **Test setup** dice el motivo exacto: `401/403` = clave mal pegada (**🔑 API keys** → Save); `404` = ese modelo no existe, elige otro en **⚙️ Models & parameters** |
| **Todos** | El agente no mueve la página | Debe ser una web normal (no `about:...`) y la pestaña estar **visible** (no minimizada) |

## Apéndice · Docker para el navegador aislado (opcional)

Solo lo necesitas si quieres usar **Isolated browser** (el navegador Neko). Sin Docker, el botón te lo dirá y seguirás usando **My current tab** con normalidad.

| Sistema | Instalación |
| --- | --- |
| **openSUSE** | `sudo zypper install docker docker-compose && sudo systemctl enable --now docker && sudo usermod -aG docker $USER` |
| **Ubuntu/Debian/Mint** | `sudo apt install docker.io && sudo systemctl enable --now docker && sudo usermod -aG docker $USER` |
| **Fedora** | `sudo dnf install docker docker-compose && sudo systemctl enable --now docker && sudo usermod -aG docker $USER` |
| **Arch** | `sudo pacman -S docker docker-compose && sudo systemctl enable --now docker && sudo usermod -aG docker $USER` |
| **Windows / macOS** | Instala **Docker Desktop** y ábrelo (la ballena debe estar en marcha) |

Tras `usermod -aG docker`, **cierra la sesión y vuelve a entrar** (o reinicia) para que tu usuario pueda usar Docker sin `sudo`.

Ocupa unos **2 GB** en disco y descarga una imagen de ~630 MB la primera vez. El agente se encarga de arrancar y parar el contenedor.

---

<sub>AI Agent for Firefox v0.12.2 · Verificado en CI sobre contenedores reales de openSUSE Leap 15.6 y Tumbleweed, en Windows (windows-latest) y en Linux con Python 3.11, 3.12 y 3.13. Informe técnico: `informe-suse-linux-windows.md`.</sub>
