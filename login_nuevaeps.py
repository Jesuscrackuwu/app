"""
=============================================================================
  Nueva EPS - Bot de Consulta de Estado de Afiliación
  Versión    : 3.3.0  (nodriver + Proxy Rotation)
=============================================================================
"""

import asyncio
import json
import os
import random
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import nodriver as uc
from dotenv import load_dotenv, set_key, unset_key

# ---------------------------------------------------------------------------
# LISTA DE PROXIES (Webshare)
# ---------------------------------------------------------------------------
PROXIES = [
    {"host": "31.59.20.176", "port": "6754"},
    {"host": "31.56.127.193", "port": "7684"},
    {"host": "45.38.107.97", "port": "6014"},
    {"host": "107.172.163.27", "port": "6543"},
    {"host": "198.23.243.226", "port": "6361"},
    {"host": "216.10.27.159", "port": "6837"},
    {"host": "142.111.67.146", "port": "5611"},
    {"host": "191.96.254.138", "port": "6185"},
    {"host": "31.58.9.4", "port": "6077"},
    {"host": "23.229.19.94", "port": "8689"},
]
PROXY_USER = "szpjaaxb"
PROXY_PASS = "w0wjxg9iv3sh"

# ---------------------------------------------------------------------------
# Configuración General
# ---------------------------------------------------------------------------
BASE_DIR     = Path(__file__).parent
ENV_PATH     = BASE_DIR / ".env"
SESSION_FILE = BASE_DIR / "session.json"
RESULTS_DIR  = BASE_DIR / "resultados"
RESULTS_DIR.mkdir(exist_ok=True)

load_dotenv(dotenv_path=ENV_PATH)

PORTAL_URL      = "https://portal.nuevaeps.com.co/Portal/home.jspx"
DEFAULT_TIMEOUT = 45
IPS_LABEL       = "SUBSIDIADO-IPSI WAYUU TALATSHI"
SUCURSAL_LABEL  = "SUBSIDIADO-IPSI WAYUU TALATSHI"
CHROME_PATH     = os.getenv("CHROME_PATH", "/usr/bin/google-chrome")

TIPO_DOC_MAP = {"AS": "9", "CC": "3", "CD": "10", "CE": "1", "CN": "11", "ME": "7", "NT": "4", "NU": "8", "PE": "13", "PS": "6", "PT": "15", "RC": "5", "SC": "12", "TI": "2"}

class NuevaEPSBot:
    def __init__(self, usuario: str, clave: str, headless: bool = True):
        self.usuario  = usuario
        self.clave    = clave
        self.headless = headless
        self._browser = None
        self._tab     = None
        self._current_proxy = None
        self._proxy_ext_dir = BASE_DIR / "proxy_auth_extension"

    async def ejecutar(self, tipo_doc: str, num_doc: str) -> dict:
        # Mezclamos los proxies para no usar siempre el mismo al inicio
        random.shuffle(PROXIES)
        
        for i, proxy in enumerate(PROXIES):
            self._current_proxy = proxy
            print(f"\n🔄 Intento {i+1}/{len(PROXIES)} usando Proxy: {proxy['host']}")
            
            try:
                await self._iniciar_navegador()
                await self._cargar_cookies()

                # Verificar si la IP es aceptada por Cloudflare
                sesion_ok = await self._verificar_sesion_activa()
                
                # Si llegamos aquí sin excepción de "Restringido", procedemos
                if not sesion_ok:
                    await self._realizar_login()
                    await self._guardar_cookies()
                    await self._click_tab_servicios()
                    await self._click_menu_ips()
                    await self._seleccionar_ips_y_sucursal()
                    await self._click_autorizaciones()
                    await self._click_estado_afiliacion()
                
                # Consulta final
                datos = await self._consultar_afiliado(tipo_doc, num_doc)
                self._imprimir_resultado(datos)
                self._guardar_resultado_json(datos)
                return datos

            except RuntimeError as re:
                print(f"   ❌ Proxy {proxy['host']} rechazado: {re}")
                if self._browser: self._browser.stop()
                continue # Probar con el siguiente proxy
            except Exception as e:
                print(f"   ⚠️  Error inesperado: {e}")
                if self._browser: self._browser.stop()
                continue
            finally:
                if self._proxy_ext_dir.exists(): shutil.rmtree(self._proxy_ext_dir)

        print("\n🚫 Se agotaron todos los proxies y ninguno pudo saltar el bloqueo.")
        return {}

    def _crear_extension_proxy(self):
        if self._proxy_ext_dir.exists(): shutil.rmtree(self._proxy_ext_dir)
        self._proxy_ext_dir.mkdir()
        
        manifest = {
            "version": "1.0.0", "manifest_version": 2, "name": "Chrome Proxy",
            "permissions": ["proxy", "tabs", "unlimitedStorage", "storage", "<all_urls>", "webRequest", "webRequestBlocking"],
            "background": {"scripts": ["background.js"]}
        }
        
        background = f"""
        var config = {{ mode: "fixed_servers", rules: {{ singleProxy: {{ scheme: "http", host: "{self._current_proxy['host']}", port: parseInt({self._current_proxy['port']}) }}, bypassList: ["localhost"] }} }};
        chrome.proxy.settings.set({{value: config, scope: "regular"}}, function() {{}});
        chrome.webRequest.onAuthRequired.addListener(function(details) {{ return {{ authCredentials: {{ username: "{PROXY_USER}", password: "{PROXY_PASS}" }} }}; }}, {{urls: ["<all_urls>"]}}, ["blocking"]);
        """
        (self._proxy_ext_dir / "manifest.json").write_text(json.dumps(manifest))
        (self._proxy_ext_dir / "background.js").write_text(background)
        return str(self._proxy_ext_dir)

    async def _iniciar_navegador(self):
        ext_path = self._crear_extension_proxy()
        config = uc.Config()
        config.headless = self.headless
        config.sandbox = False
        if Path(CHROME_PATH).exists(): config.browser_executable_path = CHROME_PATH
        config.add_argument(f"--load-extension={ext_path}")
        # Desactivar detección de automatización básica
        config.add_argument("--disable-blink-features=AutomationControlled")
        self._browser = await uc.start(config)

    async def _verificar_sesion_activa(self) -> bool:
        print(f"   🌐 Navegando al portal...")
        self._tab = await self._browser.get(PORTAL_URL)
        await asyncio.sleep(6)
        
        titulo = await self._tab.evaluate("document.title")
        if "restringido" in titulo.lower():
            raise RuntimeError("IP Bloqueada (Restringido)")
        
        if "just a moment" in titulo.lower():
            print("   ☁️  Esperando a Cloudflare (Turnstile)...")
            for _ in range(15):
                await asyncio.sleep(3)
                titulo = await self._tab.evaluate("document.title")
                if "restringido" in titulo.lower(): raise RuntimeError("IP Bloqueada tras challenge")
                if "just a moment" not in titulo.lower(): break
            else: raise RuntimeError("Timeout esperando Cloudflare")

        print(f"   ✅ Portal cargado: '{titulo}'")
        try:
            await self._tab.select("#tabServicios", timeout=4)
            return True
        except: return False

    # --- Los demás métodos (login, navegación, consulta) se mantienen igual ---
    async def _cargar_cookies(self):
        if SESSION_FILE.exists():
            try:
                with open(SESSION_FILE, "r") as f: cookies = json.load(f)
                await self._browser.cookies.set_all(cookies)
            except: pass

    async def _guardar_cookies(self):
        try:
            cookies = await self._browser.cookies.get_all()
            data = [{k: getattr(c, k, None) for k in ("name", "value", "domain", "path", "secure", "httpOnly", "expires")} for c in cookies]
            with open(SESSION_FILE, "w") as f: json.dump(data, f, indent=2)
        except: pass

    async def _esperar_elemento(self, selector: str, timeout: float = 30):
        elem = await self._tab.select(selector, timeout=timeout)
        if not elem: raise TimeoutError(f"No encontrado: {selector}")
        return elem

    async def _llenar_input(self, selector: str, text: str):
        elem = await self._esperar_elemento(selector)
        await elem.click()
        for char in text:
            await elem.send_keys(char)
            await asyncio.sleep(random.uniform(0.05, 0.1))

    async def _realizar_login(self):
        print("   🔐 Login...")
        await self._esperar_elemento("#loginForm\\:tipoId")
        # Usar JS para seleccionar para evitar problemas de click
        await self._tab.evaluate(f"document.querySelector('#loginForm\\\\:tipoId').value = '3'")
        await self._llenar_input("#loginForm\\:id", self.usuario)
        await self._llenar_input("#loginForm\\:clave", self.clave)
        btn = await self._esperar_elemento("#loginForm\\:loginButton")
        await btn.click()
        await asyncio.sleep(6)

    async def _click_tab_servicios(self):
        btn = await self._esperar_elemento("#tabServicios")
        await btn.click()
        await asyncio.sleep(3)

    async def _click_menu_ips(self):
        elem = await self._tab.find("IPS")
        await elem.click()
        await asyncio.sleep(3)

    async def _seleccionar_ips_y_sucursal(self):
        print(f"   🏨 Seleccionando IPS...")
        await self._tab.evaluate(f"""
            (() => {{
                const s1 = document.querySelector("select[name$=':ips']");
                for(let o of s1.options) {{ if(o.text.includes('{IPS_LABEL}')) {{ s1.value = o.value; break; }} }}
                s1.dispatchEvent(new Event('change', {{bubbles:true}}));
            }})()
        """)
        await asyncio.sleep(3)
        await self._tab.evaluate(f"""
            (() => {{
                const s2 = document.querySelector("select[name$=':sucIps']");
                for(let o of s2.options) {{ if(o.text.includes('{SUCURSAL_LABEL}')) {{ s2.value = o.value; break; }} }}
                s2.dispatchEvent(new Event('change', {{bubbles:true}}));
            }})()
        """)
        await asyncio.sleep(2)
        btn = await self._esperar_elemento("input[src*='btnAceptar'][type='image']")
        await btn.click()
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
        valor = TIPO_DOC_MAP.get(tipo_doc.upper(), "3")
        print(f"\n🔍 Consultando: {tipo_doc} {num_doc}")
        
        # JS para llenar en el frame correcto
        script = f"""
            (() => {{
                const doc = document.querySelector('iframe')?.contentDocument || document;
                const s = doc.querySelector("select[name$=':solTipdoc']");
                const i = doc.querySelector("input[name$=':itNumdoc']");
                const b = doc.querySelector("input[name$=':cbQAfil']");
                if(s && i && b) {{
                    s.value = '{valor}';
                    i.value = '{num_doc}';
                    b.click();
                    return true;
                }}
                return false;
            }})()
        """
        await self._tab.evaluate(script)
        await asyncio.sleep(7)

        # Extracción básica
        res = await self._tab.evaluate("""
            (() => {
                const d = document.querySelector('iframe')?.contentDocument || document;
                const obj = {};
                d.querySelectorAll('label.tituloCampo').forEach(l => {
                    const val = l.closest('tr').querySelector('textarea, input')?.value;
                    if(val) obj[l.textContent.trim().replace(':','')] = val.strip();
                });
                return obj;
            })()
        """)
        return res

    def _imprimir_resultado(self, datos: dict):
        print("\n" + "="*50 + "\n  RESULTADO DE CONSULTA\n" + "="*50)
        for k,v in datos.items(): print(f"  {k:<20}: {v}")
        print("="*50)

    def _guardar_resultado_json(self, datos: dict):
        path = RESULTS_DIR / f"afiliado_{int(time.time())}.json"
        with open(path, "w", encoding="utf-8") as f: json.dump(datos, f, indent=4)
        print(f"💾 Guardado en: {path}")

async def main():
    u, c = os.getenv("NUEVAEPS_USUARIO"), os.getenv("NUEVAEPS_CLAVE")
    bot = NuevaEPSBot(usuario=u, clave=c, headless=True)
    await bot.ejecutar(tipo_doc="CC", num_doc="27024479")

if __name__ == "__main__":
    uc.loop().run_until_complete(main())
