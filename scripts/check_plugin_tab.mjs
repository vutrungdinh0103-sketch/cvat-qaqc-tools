#!/usr/bin/env node
// Kiểm tra end-to-end tab QA/QC trong CVAT UI bằng Chrome headless.
//
// Mục đích: phát hiện lớp lỗi "plugin đã build nhưng tab không hiện" - thứ mà test
// Python (offline) KHÔNG bắt được. Xem V31 trong docs/verified-behaviors.md: quên
// dispatch(actionCreators.addUIComponent(...)) thì tab không xuất hiện và KHÔNG có
// cảnh báo nào trong console.
//
// Yêu cầu:
//   - CVAT đang chạy image UI có plugin (docs/plugin-install.md)
//   - QA service đang chạy: python -m qaqc serve --rules rules/level1_v1.yaml
//   - Chrome/Edge trên máy + module `puppeteer-core` (không tải thêm browser):
//       npm install puppeteer-core
//     Script tự tìm module này, hoặc chỉ định --puppeteer <đường-dẫn-tới-puppeteer-core>
//     hay biến môi trường PUPPETEER_CORE.
//
// Cách dùng:
//   node scripts/check_plugin_tab.mjs --task 9 --user dinhvt --password ***
//   node scripts/check_plugin_tab.mjs --task 9 --session <sessionid> --run
//   node scripts/check_plugin_tab.mjs --task 9 --user dinhvt --password *** --screenshot tab.png
//
// Lấy sessionid (không cần mật khẩu; chạy trên máy có CVAT self-hosted):
//   docker exec cvat_server python manage.py shell -c "from django.test import Client;
//   from django.contrib.auth import get_user_model; c=Client();
//   c.force_login(get_user_model().objects.get(username='dinhvt'));
//   print('SESSIONID=' + c.cookies['sessionid'].value)"
//
// Exit code: 0 = tab hiển thị và đọc được dữ liệu; 1 = lỗi (có gợi ý cách sửa).

import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const CHROME_CANDIDATES = [
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
];

const TAB_PANE_SELECTOR = '.ant-tabs-tabpane-active, .ant-tabs-content-active';
const TAB_SELECTOR = '.cvat-quality-control-page-tabs .ant-tabs-tab';

function parseArgs(argv) {
    const parsed = {};
    for (let i = 0; i < argv.length; i += 1) {
        const token = argv[i];
        if (!token.startsWith('--')) continue;
        const key = token.slice(2);
        const next = argv[i + 1];
        if (next === undefined || next.startsWith('--')) {
            parsed[key] = true;
        } else {
            parsed[key] = next;
            i += 1;
        }
    }
    return parsed;
}

function loadPuppeteer(explicit) {
    const require = createRequire(import.meta.url);
    const candidates = [
        explicit,
        process.env.PUPPETEER_CORE,
        'puppeteer-core',
        path.join(process.cwd(), 'node_modules', 'puppeteer-core'),
        path.join(process.env.TEMP || '/tmp', 'qaqc-ui-check', 'node_modules', 'puppeteer-core'),
    ].filter(Boolean);

    const errors = [];
    for (const candidate of candidates) {
        try {
            return require(candidate);
        } catch (error) {
            errors.push(`${candidate}: ${error.message}`);
        }
    }
    throw new Error(
        'Không nạp được puppeteer-core. Cài bằng `npm install puppeteer-core` rồi truyền\n' +
        '--puppeteer <đường-dẫn-tới-puppeteer-core> (hoặc đặt biến môi trường PUPPETEER_CORE).\n' +
        errors.join('\n'),
    );
}

function findChrome(explicit) {
    const candidates = [explicit, process.env.CHROME_PATH, ...CHROME_CANDIDATES].filter(Boolean);
    const found = candidates.find((candidate) => fs.existsSync(candidate));
    if (!found) {
        throw new Error('Không tìm thấy Chrome/Edge. Truyền đường dẫn bằng --chrome "C:\\...\\chrome.exe".');
    }
    return found;
}

const args = parseArgs(process.argv.slice(2));
if (!args.task) {
    console.error('Thiếu --task <id>. Ví dụ: node scripts/check_plugin_tab.mjs --task 9 --user dinhvt --password ***');
    process.exit(2);
}
if (!args.session && !(args.user && args.password)) {
    console.error('Cần --session <sessionid> hoặc --user/--password (xem phần đầu file để lấy sessionid).');
    process.exit(2);
}

const baseUrl = String(args.url || 'http://localhost:8080').replace(/\/$/, '');
const taskId = Number(args.task);
const targetUrl = `${baseUrl}/tasks/${taskId}/quality-control`;
const timeout = Number(args.timeout || 60000);

const puppeteer = loadPuppeteer(args.puppeteer === true ? undefined : args.puppeteer);
const result = { url: targetUrl, taskId, tabs: [], serviceRequests: [], pageErrors: [], ok: false };

const browser = await puppeteer.launch({
    executablePath: findChrome(args.chrome === true ? undefined : args.chrome),
    headless: true,
    defaultViewport: { width: 1600, height: 1000 },
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
});

try {
    const page = await browser.newPage();
    page.on('pageerror', (error) => result.pageErrors.push(error.message));
    page.on('response', (response) => {
        const url = response.url();
        if (url.includes('8081') || url.includes('/qaqc/')) {
            result.serviceRequests.push(`${response.request().method()} ${response.status()} ${url}`);
        }
    });

    if (args.session) {
        await page.setCookie({
            name: 'sessionid',
            value: String(args.session),
            domain: new URL(baseUrl).hostname,
            path: '/',
            httpOnly: true,
        });
    } else {
        await page.goto(`${baseUrl}/auth/login`, { waitUntil: 'domcontentloaded', timeout });
        result.loginStatus = await page.evaluate(async ({ url, username, password }) => {
            const response = await fetch(`${url}/api/auth/login`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, password }),
            });
            return response.status;
        }, { url: baseUrl, username: String(args.user), password: String(args.password) });
        if (result.loginStatus !== 200) {
            throw new Error(`Đăng nhập thất bại (HTTP ${result.loginStatus}). Kiểm tra --user/--password.`);
        }
    }

    const pageResponse = await page.goto(targetUrl, { waitUntil: 'networkidle2', timeout });
    result.httpStatus = pageResponse ? pageResponse.status() : null;
    await page.waitForSelector('.cvat-quality-control-page-tabs', { timeout });
    result.tabs = await page.$$eval(TAB_SELECTOR, (elements) => elements.map((element) => element.innerText.trim()));

    if (!result.tabs.some((label) => label.includes('QA/QC'))) {
        throw new Error(
            `Không thấy tab QA/QC (tab hiện có: ${JSON.stringify(result.tabs)}). Kiểm tra: ` +
            '(1) cvat_ui có dùng image đã build plugin? ' +
            '`docker exec cvat_ui grep -c qualityControlPage.tabs.items /usr/share/nginx/html/assets/plugin_1.*.min.js`; ' +
            '(2) plugin có dispatch(actionCreators.addUIComponent(...)) không (V31)?; ' +
            '(3) đã hard-refresh (Ctrl+Shift+R) chưa?',
        );
    }

    await page.evaluate((selector) => {
        const tabs = [...document.querySelectorAll(selector)];
        const target = tabs.find((tab) => tab.innerText.includes('QA/QC'));
        if (target) target.querySelector('.ant-tabs-tab-btn').click();
    }, TAB_SELECTOR);
    await page.waitForFunction(
        (selector) => {
            const pane = document.querySelector(selector);
            return !!pane && pane.innerText.trim().length > 0;
        },
        { timeout },
        TAB_PANE_SELECTOR,
    );
    await new Promise((resolve) => setTimeout(resolve, 1500));
    result.panelText = await page.evaluate(
        (selector) => document.querySelector(selector).innerText.replace(/\s+\n/g, '\n').trim(),
        TAB_PANE_SELECTOR,
    );

    if (result.panelText.includes('Không lấy được kết quả QA/QC')) {
        throw new Error(
            `Tab hiển thị nhưng không đọc được dữ liệu (QA service chưa chạy?). ` +
            `Kiểm tra: curl http://127.0.0.1:8081/health\n${result.panelText.slice(0, 400)}`,
        );
    }

    const summaryLine = result.panelText.split('\n').find((line) => /^\d+\s+lỗi/.test(line)) || '';
    result.summary = summaryLine;
    result.issuesFound = Number((summaryLine.match(/^(\d+)\s+lỗi/) || [])[1] ?? NaN);
    if (!summaryLine) {
        throw new Error(
            'Tab QA/QC hiển thị nhưng không có dòng tổng kết "<n> lỗi / ..." ' +
            `(component có render lỗi?):\n${result.panelText.slice(0, 400)}`,
        );
    }

    if (args.run) {
        const runResponsePromise = page.waitForResponse(
            (response) => response.url().includes('/run') && response.request().method() === 'POST',
            { timeout },
        );
        await page.evaluate(() => {
            const button = [...document.querySelectorAll('button')]
                .find((element) => element.innerText.includes('Chạy lại QA/QC'));
            if (button) button.click();
        });
        const runResponse = await runResponsePromise;
        result.runStatus = runResponse.status();
        if (result.runStatus !== 200) {
            throw new Error(`Bấm "Chạy lại QA/QC" nhưng QA service trả HTTP ${result.runStatus}.`);
        }
    }

    if (args.screenshot && args.screenshot !== true) {
        await page.screenshot({ path: String(args.screenshot), fullPage: true });
        result.screenshot = String(args.screenshot);
    }

    result.ok = true;
} catch (error) {
    result.error = error instanceof Error ? error.message : String(error);
} finally {
    await browser.close();
}

console.log(JSON.stringify(result, null, 2));
if (args.json && args.json !== true) {
    fs.writeFileSync(String(args.json), JSON.stringify(result, null, 2), 'utf8');
}
process.exit(result.ok ? 0 : 1);
