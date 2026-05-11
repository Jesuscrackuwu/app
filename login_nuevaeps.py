"""
=============================================================================
  Nueva EPS - Bot de Consulta de Estado de Afiliación
  Versión    : 3.2.0  (nodriver + Webshare Proxy)
  Descripción: Usa nodriver con Proxy Residencial de Webshare
               para evadir el bloqueo de IP de Nueva EPS.
=============================================================================
"""

import asyncio
import json
import os
import random
import shutil
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

import nodriver as uc
from dotenv import load_dotenv, set_key, unset_key

# ---------------------------------------------------------------------------
# CONFIGURACIÓN DE PROXY (Webshare)
# ---------------------------------------------------------------------------
PROXY_HOST = "45.38.107.97"
PROXY_PORT = "6014"
PROXY_USER = "szpjaaxb"
PROXY_PASS = "w0wjxg9iv3sh"

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
DEFAULT_TIMEOUT = 90
IPS_LABEL       = "SUBSIDIADO-IPSI WAYUU TALATSHI"
SUCURSAL_LABEL  = "SUBSIDIADO-IPSI WAYUU TALATSHI"

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
        self._proxy_ext_dir = BASE_DIR / "proxy_auth_extension"

    # -----------------------------------------------------------------------
    # MÉTODO PRINCIPAL
    # -----------------------------------------------------------------------

    async def ejecutar(self, tipo_doc: str, num_doc: str) -> dict:
        datos: dict = {}
        try:
            await self._iniciar_navegador()
            await self._cargar_cookies()

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
            if self.api_mode: raise
            sys.exit(1)
        finally:
            if self._browser:
                self._browser.stop()
                print("\n🔒 Navegador cerrado.")
            # Limpiar extensión de proxy
            if self._proxy_ext_dir.exists():
                shutil.rmtree(self._proxy_ext_dir)
        return datos

    # -----------------------------------------------------------------------
    # PROXY AUTH HELPER (Chrome Extension)
    # -----------------------------------------------------------------------

    def _crear_extension_proxy(self):
        """Crea una extensión de Chrome al vuelo para autenticar el proxy."""
        if self._proxy_ext_dir.exists():
            shutil.rmtree(self._proxy_ext_dir)
        self._proxy_ext_dir.mkdir()

        manifest_json = """
        {
            "version": "1.0.0",
            "manifest_version": 2,
            "name": "Chrome Proxy",
            "permissions": [
                "proxy",
                "tabs",
                "unlimitedStorage",
                "storage",
                "<all_urls>",
                "webRequest",
                "webRequestBlocking"
            ],
            "background": {
                "scripts": ["background.js"]
            },
            "minimum_chrome_version":"22.0.0"
        }
        """

        background_js = f"""
        var config = {{
                mode: "fixed_servers",
                rules: {{
                  singleProxy: {{
                    scheme: "http",
                    host: "{PROXY_HOST}",
                    port: parseInt({PROXY_PORT})
                  }},
                  bypassList: ["localhost"]
                }}
              }};

        chrome.proxy.settings.set({{value: config, scope: "regular"}}, function() {{}});

        chrome.webRequest.onAuthRequired.addListener(
            function(details) {{
                return {{
                    authCredentials: {{
                        username: "{PROXY_USER}",
                        password: "{PROXY_PASS}"
                    }}
                }};
            }},
            {{urls: ["<all_urls>"]}},
            ["blocking"]
        );
        """
        (self._proxy_ext_dir / "manifest.json").write_text(manifest_json)
        (self._proxy_ext_dir / "background.js").write_text(background_js)
        return str(self._proxy_ext_dir)

    # -----------------------------------------------------------------------
    # NAVEGADOR
    # -----------------------------------------------------------------------

    async def _iniciar_navegador(self):
        print("\n🚀 Iniciando navegador (nodriver + Webshare Proxy)...")
        
        # Buscar Chrome
        for ruta in ("/usr/bin/google-chrome", "/usr/bin/chromium-browser", CHROME_PATH):
            if Path(ruta).exists():
                chrome_path = ruta
                break
        else: chrome_path = None

        print(f"   🌐 Chrome   : {chrome_path or 'auto-detect'}")
        print(f"   🔒 Proxy IP : {PROXY_HOST}:{PROXY_PORT}")

        # Crear extensión de autenticación
        ext_path = self._crear_extension_proxy()

        config = uc.Config()
        config.headless = self.headless
        config.sandbox  = False
        if chrome_path:
            config.browser_executable_path = str(chrome_path)

        # Cargar extensión
        config.add_argument(f"--load-extension={ext_path}")
        
        for arg in [
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--window-size=1366,768",
            "--no-first-run",
            "--no-default-browser-check",
        ]:
            config.add_argument(arg)

        self._browser = await uc.start(config)
        print("   ✅ Navegador iniciado.")

    # -----------------------------------------------------------------------
    # COOKIES, HELPERS Y OTROS (Sin cambios significativos)
    # -----------------------------------------------------------------------

    async def _cargar_cookies(self):
        if SESSION_FILE.exists():
            try:
                with open(SESSION_FILE, "r") as f:
                    cookies = json.load(f)
                await self._browser.cookies.set_all(cookies)
                print(f"📂 Cookies cargadas desde {SESSION_FILE.name}")
            except Exception as e: print(f"   ⚠️  Cookies error: {e}")

    async def _guardar_cookies(self):
        try:
            cookies = await self._browser.cookies.get_all()
            cookies_data = [{k: getattr(c, k, None) for k in ("name", "value", "domain", "path", "secure", "httpOnly", "expires")} for c in cookies]
            with open(SESSION_FILE, "w") as f: json.dump(cookies_data, f, indent=2)
            print(f"   💾 Cookies guardadas.")
        except Exception as e: print(f"   ⚠️  Cookies error: {e}")

    def _invalidar_sesion(self):
        if SESSION_FILE.exists(): SESSION_FILE.unlink()
        for var in ("NUEVAEPS_CONSULTA_URL", "NUEVAEPS_URL_GUARDADA_EN"):
            unset_key(str(ENV_PATH), var)
            os.environ.pop(var, None)

    async def _delay(self, min_ms=400, max_ms=1000):
        await asyncio.sleep(random.uniform(min_ms, max_ms) / 1000)

    async def _esperar_elemento(self, selector: str, timeout: float = None) -> object:
        t = timeout or DEFAULT_TIMEOUT
        elem = await self._tab.select(selector, timeout=t)
        if elem is None: raise TimeoutError(f"No encontrado: '{selector}'")
        return elem

    async def _js(self, script: str): return await self._tab.evaluate(script)

    async def _js_frame(self, script: str):
        try:
            for frame in (self._tab.frames or []):
                try:
                    res = await frame.evaluate(script)
                    if res: return res
                except: continue
        except: pass
        return await self._tab.evaluate(script)

    async def _seleccionar_opcion(self, selector: str, value: str):
        sel_escaped = selector.replace("\\", "\\\\").replace("'", "\\'")
        await self._js_frame(f"""
            (() => {{
                const s = document.querySelector('{sel_escaped}')
                       || (Array.from(document.querySelectorAll('iframe')).map(f => {{ try {{ return f.contentDocument.querySelector('{sel_escaped}'); }} catch(e){{}} return null; }}).find(Boolean));
                if (!s) return false;
                s.value = '{value}';
                ['change','input'].forEach(ev => s.dispatchEvent(new Event(ev, {{bubbles:true}})));
                return true;
            }})()
        """)

    async def _llenar_input(self, selector: str, text: str):
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
    # VERIFICAR SESIÓN Y LOGIN
    # -----------------------------------------------------------------------

    async def _esperar_cf_resuelto(self, timeout: int = 90) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            titulo = await self._tab.evaluate("document.title")
            if "restringido" not in titulo.lower() and "just a moment" not in titulo.lower(): return True
            print(f"   ⏳ CF activo... título: '{titulo}'")
            await asyncio.sleep(3)
        return False

    async def _verificar_sesion_activa(self) -> bool:
        print(f"\n🌐 Navegando al portal: {PORTAL_URL}")
        self._tab = await self._browser.get(PORTAL_URL)
        await asyncio.sleep(5)
        titulo = await self._tab.evaluate("document.title")
        print(f"   📌 Título: '{titulo}'")

        if "restringido" in titulo.lower() or "just a moment" in titulo.lower():
            print("   ☁️  Esperando resolución de Cloudflare...")
            if not await self._esperar_cf_resuelto():
                raise RuntimeError("Cloudflare no se resolvió con el proxy residencial.")
            titulo = await self._tab.evaluate("document.title")
            print(f"   ✅ CF resuelto. Título: '{titulo}'")

        try:
            await self._tab.select("#tabServicios", timeout=5)
            print("   ✅ Sesión activa.")
            return True
        except: return False

    async def _realizar_login(self):
        print("\n🔐 Iniciando login...")
        await self._esperar_elemento("#loginForm\\:tipoId")
        await self._seleccionar_opcion("#loginForm\\:tipoId", "3")
        await self._llenar_input("#loginForm\\:id", self.usuario)
        await self._llenar_input("#loginForm\\:clave", self.clave)
        btn = await self._esperar_elemento("#loginForm\\:loginButton")
        await btn.click()
        await asyncio.sleep(5)

    async def _click_tab_servicios(self):
        await self._click_selector("#tabServicios")
        await asyncio.sleep(3)

    async def _click_menu_ips(self):
        elem = await self._tab.find("IPS")
        await elem.click()
        await asyncio.sleep(3)

    async def _seleccionar_ips_y_sucursal(self):
        await self._js(f"""
            (() => {{
                const s = document.querySelector("select[name$=':ips']");
                if (!s) return;
                for (const o of s.options) {{ if (o.text.trim() === '{IPS_LABEL}') {{ s.value = o.value; s.dispatchEvent(new Event('change', {{bubbles:true}})); break; }} }}
            }})()
        """)
        await asyncio.sleep(3)
        await self._js(f"""
            (() => {{
                const s = document.querySelector("select[name$=':sucIps']");
                if (!s) return;
                for (const o of s.options) {{ if (o.text.trim() === '{SUCURSAL_LABEL}') {{ s.value = o.value; s.dispatchEvent(new Event('change', {{bubbles:true}})); break; }} }}
            }})()
        """)
        await asyncio.sleep(2)
        await self._click_selector("input[src*='btnAceptar'][type='image']")
        await asyncio.sleep(4)

    async def _click_autorizaciones(self):
        elem = await self._tab.select("div[onclick*='option1161']")
        await elem.click()
        await asyncio.sleep(3)

    async def _click_estado_afiliacion(self):
        elem = await self._tab.find("Estado Afiliación")
        await elem.click()
        await asyncio.sleep(5)

    async def _consultar_afiliado(self, tipo_doc: str, num_doc: str) -> dict:
        valor = TIPO_DOC_MAP.get(tipo_doc.upper())
        SEL_TIPO = "select[name$=':solTipdoc']"
        SEL_NUM  = "input[name$=':itNumdoc']"
        SEL_BTN  = "input[name$=':cbQAfil'][type='image']"

        print(f"\n🔍 Consultando: {tipo_doc} {num_doc}")
        
        # Función para interactuar dentro de iframes
        async def run_in_ctx(script):
            found_in_main = await self._tab.evaluate(f"!!document.querySelector(\"{SEL_TIPO}\")")
            if found_in_main: return await self._tab.evaluate(script)
            return await self._tab.evaluate(f"""
                (() => {{
                    for (const f of document.querySelectorAll('iframe')) {{
                        try {{ if (f.contentDocument.querySelector("{SEL_TIPO}")) return (function() {{ {script} }}).call(f.contentDocument); }} catch(e) {{}}
                    }}
                }})()
            """)

        await run_in_ctx(f"this.querySelector('{SEL_TIPO}').value = '{valor}'; this.querySelector('{SEL_TIPO}').dispatchEvent(new Event('change', {{bubbles:true}}));")
        await self._delay(500)
        await run_in_ctx(f"this.querySelector('{SEL_NUM}').value = '{num_doc}';")
        await run_in_ctx(f"this.querySelector('{SEL_BTN}').click();")
        await asyncio.sleep(6)

        campos = ["Tipo Identificación", "Identificación", "Nombre Usuario", "Estado Afiliación", "IPS Primaria"]
        datos = {"_meta": {"timestamp": datetime.now().isoformat()}}
        for c in campos:
            val = await self._tab.evaluate(f"""
                (() => {{
                    function b(d) {{
                        for (const l of d.querySelectorAll('label.tituloCampo')) {{
                            if (l.textContent.includes('{c}')) return l.closest('tr').querySelector('textarea, input').value;
                        }}
                    }}
                    let r = b(document); if (r) return r;
                    for (const f of document.querySelectorAll('iframe')) {{ try {{ r = b(f.contentDocument); if (r) return r; }} catch(e) {{}} }}
                    return '';
                }})()
            """)
            datos[c] = val.strip()
        return datos

    def _imprimir_resultado(self, datos: dict):
        print("\n" + "="*50)
        for k, v in datos.items():
            if k != "_meta": print(f"  {k:<20}: {v}")
        print("="*50)

    def _guardar_resultado_json(self, datos: dict):
        archivo = RESULTS_DIR / f"afiliado_{int(time.time())}.json"
        with open(archivo, "w", encoding="utf-8") as f: json.dump(datos, f, indent=4)
        print(f"\n💾 Guardado en: {archivo}")

async def main():
    usuario = os.getenv("NUEVAEPS_USUARIO")
    clave = os.getenv("NUEVAEPS_CLAVE")
    print(f"🔑 Operador: {usuario}")
    
    # Datos de ejemplo o solicitar
    tipo, num = "CC", "27024479"
    
    bot = NuevaEPSBot(usuario=usuario, clave=clave, headless=True)
    await bot.ejecutar(tipo_doc=tipo, num_doc=num)

if __name__ == "__main__":
    uc.loop().run_until_complete(main())
