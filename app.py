import os
import asyncio
import logging
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from playwright.async_api import async_playwright
from typing import Optional, Dict

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI()

# Configuração de CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# CONFIGURAÇÃO PUSHINPAY
# ============================================================
PUSHINPAY_URLS = {
    "default": "https://app.pushinpay.com.br/service/pay/A1D84A4C-312D-4A77-A804-4134784C458D"
}

PAGE_MAX_AGE_SECONDS = 180

class PixRequest(BaseModel):
    payer_name: str
    payer_cpf: str
    payer_phone: str
    payer_email: str = None
    subtotal: str = None

class PreWarmedPage:
    def __init__(self, page, created_at: float, url: str):
        self.page = page
        self.created_at = created_at
        self.url = url

    def is_expired(self) -> bool:
        import time
        return (time.time() - self.created_at) > PAGE_MAX_AGE_SECONDS

    def is_valid(self) -> bool:
        return not self.page.is_closed() and not self.is_expired()

class BrowserManager:
    def __init__(self, pool_size=1): # Reduzido para 1 para economizar memória no Render
        self.playwright = None
        self.browser = None
        self.context = None
        self.pool_size = pool_size
        self._running = False
        self._starting = False
        self._lock = asyncio.Lock()
        self._warm_pages: list[PreWarmedPage] = []
        self._maintenance_task = None

    async def start(self):
        if self._starting: return
        self._starting = True
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-gpu', '--single-process']
            )
            self.context = await self.browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
            )
            self._running = True
            logger.info("BrowserManager iniciado")
            self._maintenance_task = asyncio.create_task(self._pool_maintenance_loop())
            asyncio.create_task(self._initial_warmup())
        except Exception as e:
            logger.error(f"Erro ao iniciar BrowserManager: {e}")
        finally:
            self._starting = False

    async def _initial_warmup(self):
        await asyncio.sleep(2)
        for _ in range(self.pool_size):
            await self._add_warm_page()

    async def _pool_maintenance_loop(self):
        while self._running:
            try:
                await asyncio.sleep(30)
                await self._cleanup_expired_pages()
                await self._replenish_pool()
            except asyncio.CancelledError: break
            except Exception as e:
                logger.error(f"Erro na manutenção: {e}")
                await asyncio.sleep(5)

    async def _cleanup_expired_pages(self):
        valid_pages = []
        for wp in self._warm_pages:
            if wp.is_valid():
                valid_pages.append(wp)
            else:
                try:
                    if not wp.page.is_closed(): await wp.page.close()
                except: pass
        self._warm_pages = valid_pages

    async def _replenish_pool(self):
        while len(self._warm_pages) < self.pool_size:
            await self._add_warm_page()

    async def _add_warm_page(self, url=None):
        target_url = url or PUSHINPAY_URLS["default"]
        try:
            page = await self._create_ready_page(target_url)
            if page:
                import time
                self._warm_pages.append(PreWarmedPage(page, time.time(), target_url))
                logger.info(f"Página pré-aquecida adicionada: {target_url}")
        except Exception as e:
            logger.error(f"Erro ao pré-aquecer: {e}")

    async def _create_ready_page(self, url):
        if not self.context: return None
        page = await self.context.new_page()
        async def block_resources(route):
            if route.request.resource_type in ["image", "font", "media"]:
                return await route.abort()
            await route.continue_()
        await page.route("**/*", block_resources)
        await page.goto(url, wait_until='domcontentloaded', timeout=30000)
        return page

    async def get_ready_page(self, url=None):
        target_url = url or PUSHINPAY_URLS["default"]
        async with self._lock:
            for i, wp in enumerate(self._warm_pages):
                if wp.url == target_url and wp.is_valid():
                    self._warm_pages.pop(i)
                    asyncio.create_task(self._add_warm_page(target_url))
                    return wp.page
        return await self._create_ready_page(target_url)

    async def close(self):
        self._running = False
        if self._maintenance_task: self._maintenance_task.cancel()
        for wp in self._warm_pages:
            try: await wp.page.close()
            except: pass
        if self.context: await self.context.close()
        if self.browser: await self.browser.close()
        if self.playwright: await self.playwright.stop()

browser_manager = BrowserManager(pool_size=1)

@app.on_event("startup")
async def startup_event():
    await browser_manager.start()

@app.on_event("shutdown")
async def shutdown_event():
    await browser_manager.close()

async def automate_pushinpay(data: PixRequest):
    url = PUSHINPAY_URLS.get(data.subtotal, PUSHINPAY_URLS["default"])
    page = await browser_manager.get_ready_page(url)
    if not page: return None, "Erro ao carregar página de pagamento"
    
    try:
        checkbox = await page.query_selector('input[type="checkbox"]')
        if checkbox: await checkbox.check()
        
        confirm_btn = await page.query_selector('button:has-text("Confirmar Pagamento")')
        if confirm_btn: await confirm_btn.click()
        
        await page.wait_for_load_state('networkidle', timeout=15000)
        return page.url, None
    except Exception as e:
        logger.error(f"Erro na automação: {e}")
        return None, str(e)
    finally:
        try: await page.close()
        except: pass

@app.post('/proxy/pix')
async def generate_pix(request: PixRequest):
    logger.info(f"Requisição PIX: {request.payer_name}")
    pix_url, error = await automate_pushinpay(request)
    if pix_url:
        return JSONResponse({'success': True, 'pixUrl': pix_url})
    return JSONResponse({'success': False, 'error': error or 'Erro ao gerar PIX'}, status_code=400)

@app.get('/health')
async def health():
    return {"status": "ok", "pool": len(browser_manager._warm_pages)}

# --- CONFIGURAÇÃO DE ARQUIVOS ESTÁTICOS ---
# Importante: a rota '/' deve vir ANTES de montar o diretório static se você quiser servir o index.html na raiz
BASE_DIR = Path(__file__).parent

@app.get("/")
async def read_index():
    index_path = BASE_DIR / "static" / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return JSONResponse({"error": "index.html not found"}, status_code=404)

# Monta o diretório static para servir CSS, JS, etc.
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
