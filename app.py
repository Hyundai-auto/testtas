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
PUSHINPAY_SERVICE_URL = "https://app.pushinpay.com.br/service/pay/A1D84A4C-312D-4A77-A804-4134784C458D"

# Tempo máximo (em segundos) que uma página pré-aquecida pode ficar no cache antes de ser descartada
PAGE_MAX_AGE_SECONDS = 180

class PixRequest(BaseModel):
    payer_name: str
    payer_cpf: str
    payer_phone: str
    payer_email: str = None
    subtotal: str = None

class PreWarmedPage:
    """Armazena uma página pré-aquecida com timestamp de criação."""
    def __init__(self, page, created_at: float):
        self.page = page
        self.created_at = created_at

    def is_expired(self) -> bool:
        import time
        return (time.time() - self.created_at) > PAGE_MAX_AGE_SECONDS

    def is_valid(self) -> bool:
        return not self.page.is_closed() and not self.is_expired()

class BrowserManager:
    """
    Gerencia o Playwright com pool de páginas pré-aquecidas para a PushinPay.
    """
    def __init__(self, pool_size=2):
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

    async def _add_warm_page(self):
        try:
            page = await self._create_ready_page()
            if page:
                import time
                self._warm_pages.append(PreWarmedPage(page, time.time()))
                logger.info("Página PushinPay pré-aquecida adicionada")
        except Exception as e:
            logger.error(f"Erro ao pré-aquecer: {e}")

    async def _create_ready_page(self):
        if not self.context: return None
        page = await self.context.new_page()
        # Bloqueia recursos desnecessários
        async def block_resources(route):
            if route.request.resource_type in ["image", "font", "media"]:
                return await route.abort()
            await route.continue_()
        await page.route("**/*", block_resources)
        await page.goto(PUSHINPAY_SERVICE_URL, wait_until='domcontentloaded', timeout=30000)
        # Aguarda os campos de input estarem prontos
        try:
            await page.wait_for_selector('input[name="name"]', timeout=15000)
        except: pass
        return page

    async def get_ready_page(self):
        async with self._lock:
            while self._warm_pages:
                wp = self._warm_pages.pop(0)
                if wp.is_valid():
                    asyncio.create_task(self._add_warm_page())
                    return wp.page
                else:
                    try:
                        if not wp.page.is_closed(): await wp.page.close()
                    except: pass
        return await self._create_ready_page()

    async def close(self):
        self._running = False
        if self._maintenance_task: self._maintenance_task.cancel()
        for wp in self._warm_pages:
            try: await wp.page.close()
            except: pass
        if self.context: await self.context.close()
        if self.browser: await self.browser.close()
        if self.playwright: await self.playwright.stop()

browser_manager = BrowserManager(pool_size=2)

@app.on_event("startup")
async def startup_event():
    await browser_manager.start()

@app.on_event("shutdown")
async def shutdown_event():
    await browser_manager.close()

async def automate_pushinpay(data: PixRequest):
    page = await browser_manager.get_ready_page()
    if not page: return None, "Erro ao carregar página de pagamento"
    
    try:
        # Preenche os dados
        await page.fill('input[name="name"]', data.payer_name)
        await page.fill('input[name="cpf"]', data.payer_cpf)
        await page.fill('input[name="phone"]', data.payer_phone)
        
        # Clica no botão de gerar PIX (Geralmente o botão principal de submit)
        # Na PushinPay costuma ser um botão que contém "Gerar" ou "Pagar"
        await page.click('button[type="submit"]')
        
        # Aguarda o redirecionamento ou a exibição do QR Code
        # O PushinPay geralmente redireciona para uma URL com o QR Code
        await page.wait_for_load_state('networkidle', timeout=15000)
        
        final_url = page.url
        logger.info(f"Pagamento gerado: {final_url}")
        return final_url, None
    except Exception as e:
        logger.error(f"Erro na automação PushinPay: {e}")
        return None, str(e)
    finally:
        try: await page.close()
        except: pass

@app.post('/proxy/pix')
async def generate_pix(request: PixRequest):
    logger.info(f"Gerando PIX para: {request.payer_name}")
    pix_url, error = await automate_pushinpay(request)
    if pix_url:
        return JSONResponse({'success': True, 'pixUrl': pix_url})
    return JSONResponse({'success': False, 'error': error or 'Erro ao gerar PIX'}, status_code=400)

@app.get('/health')
async def health():
    return {"status": "ok", "pool": len(browser_manager._warm_pages)}

@app.get('/')
async def index():
    return FileResponse(Path(__file__).parent / 'static' / 'index.html')

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
