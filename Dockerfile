# Usar uma imagem oficial do Node.js que já venha com ferramentas de build
FROM ghcr.io/puppeteer/puppeteer:21.6.0

# Definir o diretório de trabalho
WORKDIR /usr/src/app

# Copiar package.json e instalar dependências
COPY package*.json ./
RUN npm install

# Copiar o restante dos arquivos
COPY . .

# Expor a porta que o app vai rodar
EXPOSE 3000

# Comando para iniciar o servidor
CMD [ "node", "server.js" ]
