#!/usr/bin/env bash
# exit on error
set -o errexit

npm install
# Garante que o Chromium seja baixado durante o build para não falhar no runtime
npx puppeteer install
