const express = require('express');
const puppeteer = require('puppeteer');
const cors = require('cors');

const app = express();
const PORT = process.env.PORT || 3000;

app.use(cors());
app.use(express.json());
app.use(express.static('public'));

let browserInstance = null;

async function getBrowser() {
    if (!browserInstance || !browserInstance.connected) {
        browserInstance = await puppeteer.launch({
            headless: "new",
            executablePath: '/usr/bin/google-chrome-stable', // Caminho padrão na imagem do Puppeteer
            args: [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--no-zygote',
                '--single-process'
            ]
        });
    }
    return browserInstance;
}

app.post('/api/generate-pix', async (req, res) => {
    const { name, email, cpf } = req.body;
    const checkoutUrl = 'https://app.pushinpay.com.br/service/pay/A1D84A4C-312D-4A77-A804-4134784C458D';

    let page;
    try {
        const browser = await getBrowser();
        page = await browser.newPage();
        
        await page.setRequestInterception(true);
        page.on('request', (req) => {
            if (['image', 'stylesheet', 'font'].includes(req.resourceType())) {
                req.abort();
            } else {
                req.continue();
            }
        });

        await page.goto(checkoutUrl, { waitUntil: 'networkidle2', timeout: 30000 });

        try {
            await page.waitForSelector('input[type="checkbox"]', { timeout: 5000 });
            await page.click('input[type="checkbox"]');
        } catch (e) {}

        await page.evaluate(() => {
            const btn = Array.from(document.querySelectorAll('button')).find(b => b.innerText.includes('PAGAMENTO') || b.innerText.includes('CONFIRMAR'));
            if (btn) btn.click();
        });

        await page.waitForFunction(() => document.body.innerText.includes('000201'), { timeout: 20000 });
        const pixCode = await page.evaluate(() => {
            const m = document.body.innerText.match(/000201[a-zA-Z0-9]+/);
            return m ? m[0] : null;
        });

        if (pixCode) {
            res.json({ pix_code: pixCode });
        } else {
            res.status(400).json({ error: 'Falha ao extrair PIX' });
        }

    } catch (error) {
        console.error(error);
        res.status(500).json({ error: 'Erro interno' });
    } finally {
        if (page) await page.close();
    }
});

app.listen(PORT, () => console.log(`Servidor Docker rodando na porta ${PORT}`));
