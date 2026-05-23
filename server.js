const express = require('express');
const puppeteer = require('puppeteer');
const cors = require('cors');

const app = express();
const PORT = process.env.PORT || 3000;

app.use(cors());
app.use(express.json());
app.use(express.static('public'));

// Gerenciamento de browser para economizar memória no Render
let browserInstance = null;

async function getBrowser() {
    if (!browserInstance || !browserInstance.connected) {
        browserInstance = await puppeteer.launch({
            headless: "new",
            args: [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-accelerated-2d-canvas',
                '--no-first-run',
                '--no-zygote',
                '--single-process',
                '--disable-gpu'
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
        
        // Otimização extrema: não carregar nada visual
        await page.setRequestInterception(true);
        page.on('request', (request) => {
            const type = request.resourceType();
            if (['image', 'stylesheet', 'font', 'media', 'other'].includes(type)) {
                request.abort();
            } else {
                request.continue();
            }
        });

        await page.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36');

        console.log("Emulando checkout em background...");
        await page.goto(checkoutUrl, { waitUntil: 'networkidle2', timeout: 30000 });

        // Interação silenciosa
        try {
            await page.waitForSelector('input[type="checkbox"]', { timeout: 5000 });
            await page.click('input[type="checkbox"]');
        } catch (e) {}

        await page.evaluate(() => {
            const btn = Array.from(document.querySelectorAll('button')).find(b => b.innerText.includes('PAGAMENTO') || b.innerText.includes('CONFIRMAR'));
            if (btn) btn.click();
        });

        // Captura do código gerado
        await page.waitForFunction(() => document.body.innerText.includes('000201'), { timeout: 20000 });
        
        const pixData = await page.evaluate(() => {
            const code = document.body.innerText.match(/000201[a-zA-Z0-9]+/);
            return { pix_code: code ? code[0] : null };
        });

        if (pixData.pix_code) {
            console.log("PIX extraído com sucesso!");
            res.json(pixData);
        } else {
            res.status(400).json({ error: 'Falha na emulação' });
        }

    } catch (error) {
        console.error("Erro na emulação:", error.message);
        res.status(500).json({ error: 'Tente novamente em instantes.' });
    } finally {
        if (page) await page.close();
    }
});

app.listen(PORT, () => console.log(`Servidor ativo na porta ${PORT}`));
