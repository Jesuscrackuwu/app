"""
=============================================================================
  Nueva EPS - Bot de Consulta de Estado de Afiliación
  Versión    : 3.1.0  (nodriver + WARP Proxy)
  Descripción: Usa nodriver con Cloudflare WARP como proxy SOCKS5
               para que la IP del VPS parezca una IP residencial de CF.

  Flujo: [WARP check] → Login → Servicios → IPS → Autorizaciones → Estado

  Requisitos:
    pip install nodriver python-dotenv
    warp-cli register && warp-cli set-mode proxy && warp-cli connect
=============================================================================
"""

import asyncio
import json
import os
import random
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import nodriver as uc
from dotenv import load_dotenv, set_key, unset_key

# ---------------------------------------------------------------------------
# Rutas y configuración
# ---------------------------------------------------------------------------
BASE_DIR     = Path(__file__).parent
ENV_PATH     = BASE_DIR / ".env"
SESSION_FILE = BASE_DIR / "session.json"
RESULTS_DIR  = BASE_DIR / "resultados"
RESULTS_DIR.mkdir(exist_ok=True)

load_dotenv(dotenv_path=ENV_PATH)

PORTAL_URL      = "https://portal.nuevaeps.com.co/Portal/home.jspx"
DEFAULT_TIMEOUT = 60
IPS_LABEL       = "SUBSIDIADO-IPSI WAYUU TALATSHI"
SUCURSAL_LABEL  = "SUBSIDIADO-IPSI WAYUU TALATSHI"

# Proxy WARP — escucha en modo SOCKS5 por defecto en 127.0.0.1:40000
WARP_PROXY      = os.getenv("WARP_PROXY", "socks5://127.0.0.1:40000")
WARP_PORT       = 40000

# Ruta al Chrome (prioridad: google-chrome del sistema → FlareSolverr)
CHROME_PATH = os.getenv("CHROME_PATH", "/opt/flaresolverr/chrome/chrome")

TIPO_DOC_MAP = {
    "AS": "9", "CC": "3", "CD": "10", "CE": "1",
    "CN": "11", "ME": "7", "NT": "4",  "NU": "8",
    "PE": "13", "PS": "6", "PT": "15", "RC": "5",
    "SC": "12", "TI": "2",
}

# ===========================================================================
# Bot principal
# ===========================================================================

class NuevaEPSBot:
    def __init__(self, usuario: str, clave: str, headless: bool = True, api_mode: bool = False):
        self.usuario  = usuario
        self.clave    = clave
        self.headless = headless
        self.api_mode = api_mode
        self._browser = None
        self._tab     = None

    # -----------------------------------------------------------------------
    # MÉTODO PRINCIPAL
    # -----------------------------------------------------------------------

    async def ejecutar(self, tipo_doc: str, num_doc: str) -> dict:
        datos: dict = {}
        try:
            # 1. Verificar WARP antes de arrancar el navegador
            self._verificar_warp()

            await self._iniciar_navegador()
            await self._cargar_cookies()

            # Verificar si la sesión ya está activa
            sesion_ok = await self._verificar_sesion_activa()

            if not sesion_ok:
                await self._realizar_login()
                await self._guardar_cookies()
                await self._click_tab_servicios()
                await self._click_menu_ips()
                await self._seleccionar_ips_y_sucursal()
                await self._click_autorizaciones()
                await self._click_estado_afiliacion()
            else:
                print("♻️  Sesión activa, omitiendo login.")

            datos = await self._consultar_afiliado(tipo_doc, num_doc)

            if not self.api_mode:
                self._imprimir_resultado(datos)
                self._guardar_resultado_json(datos)

        except Exception as e:
            print(f"\n❌ Error: {type(e).__name__}: {e}")
            self._invalidar_sesion()
            if self.api_mode:
                raise
            sys.exit(1)
        finally:
            if self._browser:
                self._browser.stop()
                print("\n🔒 Navegador cerrado.")
        return datos

    # -----------------------------------------------------------------------
    # WARP — VERIFICACIÓN Y ARRANQUE
    # -----------------------------------------------------------------------

    def _verificar_warp(self) -> None:
        """
        Comprueba que el proxy SOCKS5 de WARP esté activo antes de abrir
        el navegador. Falla rápido con mensaje claro si no está.
        """
        print("\n🔍 Verificando Cloudflare WARP...")

        # 1. ¿El puerto SOCKS5 responde?
        try:
            sock = socket.create_connection(("127.0.0.1", WARP_PORT), timeout=3)
            sock.close()
            print(f"   ✅ Puerto {WARP_PORT} abierto (WARP SOCKS5 activo).")
        except (ConnectionRefusedError, OSError):
            raise RuntimeError(
                f"El proxy WARP no está escuchando en el puerto {WARP_PORT}.\n"
                "  Ejecuta estos comandos en el VPS y vuelve a intentar:\n"
                "    warp-cli set-mode proxy\n"
                "    warp-cli connect\n"
                "    warp-cli status"
            )

        # 2. ¿WARP CLI dice que está conectado?
        try:
            resultado = subprocess.run(
                ["warp-cli", "status"],
                capture_output=True, text=True, timeout=5
            )
            salida = resultado.stdout + resultado.stderr
            if "Connected" in salida:
                print(f"   ✅ warp-cli status: Connected")
            else:
                print(f"   ⚠️  warp-cli status: {salida.strip()[:80]}")
        except FileNotFoundError:
            print("   ⚠️  warp-cli no encontrado (continuando de todas formas).")
        except Exception as e:
            print(f"   ⚠️  No se pudo verificar warp-cli: {e}")

    async def _iniciar_navegador(self):
        print("\n🚀 Iniciando navegador (nodriver + WARP proxy)...")

        # Priorizar google-chrome del sistema, luego FlareSolverr
        for ruta in ("/usr/bin/google-chrome", "/usr/bin/chromium-browser", CHROME_PATH):
            if Path(ruta).exists():
                chrome_path = ruta
                break
        else:
            chrome_path = None

        print(f"   🌐 Chrome   : {chrome_path or 'auto-detect'}")
        print(f"   🔒 Proxy    : {WARP_PROXY}")
        print(f"   👻 Headless : {self.headless}")

        config = uc.Config()
        config.headless = self.headless
        config.sandbox  = False            # obligatorio como root
        if chrome_path:
            config.browser_executable_path = str(chrome_path)

        for arg in [
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--window-size=1366,768",
            "--no-first-run",
            "--no-default-browser-check",
            f"--proxy-server={WARP_PROXY}",
        ]:
            config.add_argument(arg)

        self._browser = await uc.start(config)
        print("   ✅ Navegador iniciado.")

    async def _verificar_ip_warp(self, tab) -> None:
        """
        Navega a un endpoint que devuelve la IP y la muestra.
        Útil para confirmar que el tráfico sale por WARP.
        """
        try:
            print("   🌐 Verificando IP de salida vía WARP...")
            # Usar ipify que devuelve solo el texto de la IP
            ip_tab = await self._browser.get("https://api.ipify.org")
            await asyncio.sleep(2)
            ip = await ip_tab.evaluate("document.body.innerText.trim()")
            print(f"   🟢 IP de salida: {ip}")
        except Exception as e:
            print(f"   ⚠️  No se pudo verificar IP: {e}")

    # -----------------------------------------------------------------------
    # COOKIES DE SESIÓN
    # -----------------------------------------------------------------------

    async def _cargar_cookies(self):
        if SESSION_FILE.exists():
            try:
                with open(SESSION_FILE, "r") as f:
                    cookies = json.load(f)
                await self._browser.cookies.set_all(cookies)
                print(f"📂 Cookies cargadas desde {SESSION_FILE.name}")
            except Exception as e:
                print(f"   ⚠️  No se pudieron cargar cookies: {e}")

    async def _guardar_cookies(self):
        try:
            cookies = await self._browser.cookies.get_all()
            # Serializar a dicts simples
            cookies_data = [
                {k: getattr(c, k, None) for k in
                 ("name", "value", "domain", "path", "secure", "httpOnly", "expires")}
                for c in cookies
            ]
            with open(SESSION_FILE, "w") as f:
                json.dump(cookies_data, f, indent=2)
            print(f"   💾 Cookies guardadas en {SESSION_FILE.name}")
        except Exception as e:
            print(f"   ⚠️  No se pudieron guardar cookies: {e}")

    def _invalidar_sesion(self):
        if SESSION_FILE.exists():
            SESSION_FILE.unlink()
            print(f"   🗑️  {SESSION_FILE.name} eliminado.")
        for var in ("NUEVAEPS_CONSULTA_URL", "NUEVAEPS_URL_GUARDADA_EN"):
            unset_key(str(ENV_PATH), var)
            os.environ.pop(var, None)

    # -----------------------------------------------------------------------
    # HELPERS: esperar / interactuar
    # -----------------------------------------------------------------------

    async def _delay(self, min_ms=400, max_ms=1000):
        await asyncio.sleep(random.uniform(min_ms, max_ms) / 1000)

    async def _esperar_elemento(self, selector: str, timeout: float = None) -> object:
        """Espera y retorna el elemento. Lanza excepción si no aparece o es None."""
        t = timeout or DEFAULT_TIMEOUT
        elem = await self._tab.select(selector, timeout=t)
        if elem is None:
            raise TimeoutError(f"Elemento no encontrado: '{selector}'")
        return elem

    async def _js(self, script: str):
        """Ejecuta JS en el tab o iframe activo."""
        return await self._tab.evaluate(script)

    async def _js_frame(self, script: str):
        """
        Ejecuta JS intentando en todos los iframes, luego en el tab principal.
        Útil para portales ICEfaces que cargan módulos en <iframe>.
        """
        # Intentar en iframes
        try:
            frames = self._tab.frames
            for frame in (frames or []):
                try:
                    result = await frame.evaluate(script)
                    if result:
                        return result
                except Exception:
                    continue
        except Exception:
            pass
        # Fallback: tab principal
        return await self._tab.evaluate(script)

    async def _seleccionar_opcion(self, selector: str, value: str):
        """Selecciona una opción en un <select> usando JS + dispara eventos ICEfaces."""
        sel_escaped = selector.replace("\\", "\\\\").replace("'", "\\'")
        await self._js_frame(f"""
            (() => {{
                const s = document.querySelector('{sel_escaped}')
                       || (Array.from(document.querySelectorAll('iframe'))
                           .map(f => {{ try {{ return f.contentDocument.querySelector('{sel_escaped}'); }} catch(e){{}} return null; }})
                           .find(Boolean));
                if (!s) return false;
                s.value = '{value}';
                ['change','input'].forEach(ev =>
                    s.dispatchEvent(new Event(ev, {{bubbles:true}})));
                return true;
            }})()
        """)

    async def _llenar_input(self, selector: str, text: str):
        """Llena un input carácter a carácter para simular escritura humana."""
        elem = await self._esperar_elemento(selector)
        await elem.click()
        await self._delay(100, 300)
        for char in text:
            await elem.send_keys(char)
            await asyncio.sleep(random.uniform(0.05, 0.15))

    async def _click_selector(self, selector: str):
        elem = await self._esperar_elemento(selector)
        await self._delay(200, 500)
        await elem.click()

    # -----------------------------------------------------------------------
    # VERIFICAR SESIÓN
    # -----------------------------------------------------------------------

    async def _esperar_cf_resuelto(self, timeout: int = 90) -> bool:
        """
        Espera hasta `timeout` segundos a que Cloudflare libere la página.
        nodriver debería resolver el challenge automáticamente al navegar.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            titulo = await self._tab.evaluate("document.title")
            if "restringido" not in titulo.lower() and "just a moment" not in titulo.lower():
                return True
            elapsed = int(timeout - (deadline - time.monotonic()))
            print(f"   ⏳ CF activo ({elapsed}s)... título: '{titulo}'")
            await asyncio.sleep(3)
        return False

    async def _verificar_sesion_activa(self) -> bool:
        """
        Navega al portal y verifica si la sesión ICEfaces está activa.
        Primero verifica la IP de salida vía WARP, luego navega al portal.
        Lanza RuntimeError si Cloudflare no se resuelve.
        """
        # Verificar IP de salida antes de tocar el portal
        await self._verificar_ip_warp(None)

        print(f"\n🌐 Navegando al portal: {PORTAL_URL}")
        self._tab = await self._browser.get(PORTAL_URL)
        await asyncio.sleep(4)

        titulo = await self._tab.evaluate("document.title")
        print(f"   📌 Título inicial: '{titulo}'")

        if "restringido" in titulo.lower() or "just a moment" in titulo.lower():
            print("   ☁️  Cloudflare detectado — esperando resolución (máx 90s)...")
            cf_ok = await self._esperar_cf_resuelto(timeout=90)
            if not cf_ok:
                raise RuntimeError(
                    "Cloudflare sigue bloqueando a pesar de WARP.\n"
                    "  Verifica que WARP esté en modo proxy:\n"
                    "    warp-cli set-mode proxy\n"
                    "    warp-cli connect\n"
                    "  Y que el puerto 40000 esté abierto:\n"
                    "    ss -tulpn | grep 40000"
                )
            titulo = await self._tab.evaluate("document.title")
            print(f"   ✅ CF resuelto. Título: '{titulo}'")

        # Verificar si hay tab de Servicios (sesión autenticada)
        try:
            await self._tab.select("#tabServicios", timeout=5)
            print("   ✅ Sesión ICEfaces activa.")
            return True
        except Exception:
            print("   ℹ️  Sin sesión activa — se procederá con login.")
            return False


    # -----------------------------------------------------------------------
    # LOGIN
    # -----------------------------------------------------------------------

    async def _realizar_login(self):
        print("\n🔐 Iniciando login...")

        # Esperar formulario de login
        await self._esperar_elemento("#loginForm\\:tipoId")
        await self._delay(500, 1000)

        # Seleccionar tipo CC
        print("   ▸ Tipo de identificación: CC")
        await self._seleccionar_opcion("#loginForm\\:tipoId", "3")
        await self._delay(400, 800)

        # Usuario
        print("   ▸ Ingresando usuario...")
        await self._llenar_input("#loginForm\\:id", self.usuario)
        await self._delay(400, 800)

        # Contraseña
        print("   ▸ Ingresando contraseña...")
        await self._llenar_input("#loginForm\\:clave", self.clave)
        await self._delay(500, 1000)

        # Botón Ingresar
        print("   ▸ Clic en Ingresar...")
        btn = await self._esperar_elemento("#loginForm\\:loginButton")
        await btn.click()
        await asyncio.sleep(4)
        print("   ✅ Login enviado.")

    # -----------------------------------------------------------------------
    # NAVEGACIÓN POST-LOGIN
    # -----------------------------------------------------------------------

    async def _click_tab_servicios(self):
        print("\n🗂️  Abriendo 'Servicios'...")
        await self._click_selector("#tabServicios")
        await asyncio.sleep(3)
        print("   ✅ Servicios abierto.")

    async def _click_menu_ips(self):
        print("🏥 Abriendo menú 'IPS'...")
        elem = await self._tab.find("IPS")
        await elem.click()
        await asyncio.sleep(3)
        print("   ✅ Menú IPS abierto.")

    async def _seleccionar_ips_y_sucursal(self):
        print(f"🏨 Seleccionando IPS: {IPS_LABEL}")
        await self._seleccionar_opcion("select[name$=':ips']", "")
        # Seleccionar por label
        await self._js(f"""
            (() => {{
                const s = document.querySelector("select[name$=':ips']");
                if (!s) return;
                for (const o of s.options) {{
                    if (o.text.trim() === '{IPS_LABEL}') {{
                        s.value = o.value;
                        ['change','input'].forEach(ev =>
                            s.dispatchEvent(new Event(ev, {{bubbles:true}})));
                        break;
                    }}
                }}
            }})()
        """)
        await asyncio.sleep(3)

        print(f"🏨 Seleccionando sucursal: {SUCURSAL_LABEL}")
        await self._js(f"""
            (() => {{
                const s = document.querySelector("select[name$=':sucIps']");
                if (!s) return;
                for (const o of s.options) {{
                    if (o.text.trim() === '{SUCURSAL_LABEL}') {{
                        s.value = o.value;
                        ['change','input'].forEach(ev =>
                            s.dispatchEvent(new Event(ev, {{bubbles:true}})));
                        break;
                    }}
                }}
            }})()
        """)
        await asyncio.sleep(2)

        print("   ▸ Clic en Aceptar IPS...")
        await self._click_selector("input[src*='btnAceptar'][type='image']")
        await asyncio.sleep(4)
        print("   ✅ IPS confirmada.")

    async def _click_autorizaciones(self):
        print("📋 Abriendo 'Autorizaciones'...")
        elem = await self._tab.select("div[onclick*='option1161']")
        await elem.click()
        await asyncio.sleep(3)
        print("   ✅ Autorizaciones abierto.")

    async def _click_estado_afiliacion(self):
        print("📌 Abriendo 'Estado Afiliación'...")
        elem = await self._tab.find("Estado Afiliación")
        await elem.click()
        await asyncio.sleep(4)
        print("   ✅ Formulario de consulta cargado.")

    # -----------------------------------------------------------------------
    # CONSULTA DEL AFILIADO
    # -----------------------------------------------------------------------

    async def _consultar_afiliado(self, tipo_doc: str, num_doc: str) -> dict:
        valor = TIPO_DOC_MAP.get(tipo_doc.upper())
        if not valor:
            raise ValueError(f"Tipo de documento '{tipo_doc}' inválido.")

        print(f"\n🔍 Consultando → Tipo: {tipo_doc} | N°: {num_doc}")

        # Buscar formulario en página principal o en iframes
        SEL_TIPO = "select[name$=':solTipdoc']"
        SEL_NUM  = "input[name$=':itNumdoc']"
        SEL_BTN  = "input[name$=':cbQAfil'][type='image']"

        print("   ⏳ Esperando formulario...")
        deadline = time.monotonic() + DEFAULT_TIMEOUT
        ctx_frame = None
        while time.monotonic() < deadline:
            # Buscar en tab principal
            found = await self._tab.evaluate(
                f"!!document.querySelector(\"{SEL_TIPO}\")"
            )
            if found:
                ctx_frame = None  # usar tab principal
                break
            # Buscar en iframes
            found_in_frame = await self._tab.evaluate(f"""
                Array.from(document.querySelectorAll('iframe')).some(f => {{
                    try {{ return !!f.contentDocument.querySelector("{SEL_TIPO}"); }}
                    catch(e) {{ return false; }}
                }})
            """)
            if found_in_frame:
                ctx_frame = "iframe"
                break
            await asyncio.sleep(0.5)

        if ctx_frame is None and not found:
            raise TimeoutError("Formulario de consulta no encontrado.")

        # Helper para ejecutar JS en el contexto correcto
        def js_ctx(script):
            if ctx_frame == "iframe":
                # Envolver para ejecutar en el primer iframe que tenga el elemento
                return self._tab.evaluate(f"""
                    (() => {{
                        for (const f of document.querySelectorAll('iframe')) {{
                            try {{
                                if (f.contentDocument.querySelector("{SEL_TIPO}")) {{
                                    return (function() {{ {script} }}).call(f.contentDocument);
                                }}
                            }} catch(e) {{}}
                        }}
                        return null;
                    }})()
                """)
            return self._tab.evaluate(script)

        # 1) Tipo de documento
        print(f"   ▸ Tipo: {tipo_doc}")
        await js_ctx(f"""
            const s = this.querySelector("{SEL_TIPO}") || document.querySelector("{SEL_TIPO}");
            s.value = '{valor}';
            ['change','input'].forEach(ev => s.dispatchEvent(new Event(ev, {{bubbles:true}})));
        """)
        await self._delay(400, 800)

        # 2) Número de documento
        print(f"   ▸ Número: {num_doc}")
        await js_ctx(f"""
            const inp = this.querySelector("{SEL_NUM}") || document.querySelector("{SEL_NUM}");
            inp.value = '{num_doc}';
            ['input','change'].forEach(ev => inp.dispatchEvent(new Event(ev, {{bubbles:true}})));
        """)
        await self._delay(400, 800)

        # 3) Clic en Aceptar
        print("   ▸ Enviando consulta...")
        await js_ctx(f"""
            const btn = this.querySelector("{SEL_BTN}") || document.querySelector("{SEL_BTN}");
            btn.click();
        """)
        await asyncio.sleep(5)

        # 4) Esperar resultados
        print("   ⏳ Esperando datos del servidor...")
        deadline = time.monotonic() + DEFAULT_TIMEOUT
        while time.monotonic() < deadline:
            found = await self._tab.evaluate("""
                (() => {
                    const labels = document.querySelectorAll('label.tituloCampo');
                    for (const l of labels)
                        if (l.textContent.includes('Nombre Usuario')) return true;
                    // También buscar en iframes
                    for (const f of document.querySelectorAll('iframe')) {
                        try {
                            const ls = f.contentDocument.querySelectorAll('label.tituloCampo');
                            for (const l of ls)
                                if (l.textContent.includes('Nombre Usuario')) return true;
                        } catch(e) {}
                    }
                    return false;
                })()
            """)
            if found:
                break
            await asyncio.sleep(1)

        print("   ✅ Datos recibidos. Extrayendo...")

        # 5) Extraer campos por label
        campos = [
            "Fecha/Hora Consulta", "Tipo Identificación", "Identificación",
            "Nombre Usuario", "Estado Afiliación", "Fecha Nacimiento",
            "Edad", "Sexo", "Dirección Residencia", "Departamento",
            "Municipio", "Teléfono", "Tipo Afiliado", "Categoría Afiliado",
            "Semanas Cotizadas", "IPS Primaria",
        ]

        datos = {
            "_meta": {
                "tipo_doc_consultado": tipo_doc.upper(),
                "num_doc_consultado":  num_doc,
                "timestamp":           datetime.now().isoformat(),
            }
        }

        for campo in campos:
            valor_campo = await self._tab.evaluate(f"""
                (() => {{
                    function buscar(doc) {{
                        const labels = doc.querySelectorAll('label.tituloCampo');
                        for (const lbl of labels) {{
                            if (lbl.textContent.trim().replace(/:$/, '') === '{campo}'.replace(/:$/, '').trim()) {{
                                const row = lbl.closest('tr');
                                if (!row) continue;
                                const ta = row.querySelector('textarea[readonly]');
                                if (ta) return ta.value || ta.textContent || '';
                                const inp = row.querySelector('input[readonly]');
                                if (inp) return inp.value || '';
                            }}
                        }}
                        return null;
                    }}
                    let r = buscar(document);
                    if (r !== null) return r;
                    for (const f of document.querySelectorAll('iframe')) {{
                        try {{
                            r = buscar(f.contentDocument);
                            if (r !== null) return r;
                        }} catch(e) {{}}
                    }}
                    return '';
                }})()
            """)
            datos[campo] = (valor_campo or "").strip()

        return datos

    # -----------------------------------------------------------------------
    # SALIDA
    # -----------------------------------------------------------------------

    def _imprimir_resultado(self, datos: dict):
        meta = datos.get("_meta", {})
        print("\n" + "═" * 62)
        print("  🏥  NUEVA EPS — ESTADO DE AFILIACIÓN")
        print("═" * 62)
        print(f"  Consulta: {meta.get('tipo_doc_consultado','')} {meta.get('num_doc_consultado','')}")
        print(f"  Fecha   : {meta.get('timestamp','')[:19].replace('T',' ')}")
        print("─" * 62)
        for label, valor in datos.items():
            if label == "_meta":
                continue
            if label == "Estado Afiliación":
                color = "\033[92m" if valor.upper() == "ACTIVO" else "\033[91m"
                valor = f"{color}{valor}\033[0m"
            elif not valor:
                valor = "—"
            print(f"  {label:<26} : {valor}")
        print("═" * 62)

    def _guardar_resultado_json(self, datos: dict):
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        archivo = RESULTS_DIR / f"afiliado_{ts}.json"
        with open(archivo, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=4)
        print(f"\n💾 Resultado guardado en: {archivo}")


# ===========================================================================
# CLI
# ===========================================================================

def solicitar_datos_afiliado() -> tuple:
    opciones = ", ".join(TIPO_DOC_MAP.keys())
    print(f"\n{'─'*50}\n  DATOS DEL AFILIADO A CONSULTAR\n{'─'*50}")
    print(f"  Tipos disponibles: {opciones}")
    while True:
        tipo = input("  ▸ Tipo de documento (ej: CC, TI, PT): ").strip().upper()
        if tipo in TIPO_DOC_MAP:
            break
        print(f"  ⚠️  Tipo '{tipo}' no válido.")
    while True:
        num = input("  ▸ Número de documento: ").strip()
        if num.isdigit() and len(num) >= 4:
            break
        print("  ⚠️  Número inválido.")
    print(f"{'─'*50}\n")
    return tipo, num


def cargar_credenciales() -> tuple:
    usuario = os.getenv("NUEVAEPS_USUARIO")
    clave   = os.getenv("NUEVAEPS_CLAVE")
    if not usuario or not clave:
        print("❌ Faltan NUEVAEPS_USUARIO o NUEVAEPS_CLAVE en .env")
        sys.exit(1)
    return usuario, clave


async def main():
    print("═" * 62)
    print("  🏥  NUEVA EPS — Bot de Consulta de Estado de Afiliación")
    print("  v3.0.0  |  nodriver + Chrome real (bypass CF nativo)")
    print("═" * 62)

    usuario, clave = cargar_credenciales()
    print(f"\n🔑 Operador: {'*' * max(0, len(usuario)-4)}{usuario[-4:]}")

    tipo_doc, num_doc = solicitar_datos_afiliado()

    bot = NuevaEPSBot(usuario=usuario, clave=clave, headless=True)
    await bot.ejecutar(tipo_doc=tipo_doc, num_doc=num_doc)


if __name__ == "__main__":
    uc.loop().run_until_complete(main())
