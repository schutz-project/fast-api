// Real browser checks against a running Nginx + portal. Never print credentials.
// Install Playwright separately; TEST_PASSWORD must be supplied via the environment.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
(async () => {
  assert(process.env.TEST_PASSWORD, 'TEST_PASSWORD is required');
  const browser = await chromium.launch({headless:true, ...(process.env.CHROMIUM_EXECUTABLE ? {executablePath:process.env.CHROMIUM_EXECUTABLE, args:['--no-sandbox','--disable-gpu','--disable-dev-shm-usage']} : {})});
  const page = await browser.newPage({viewport:{width:1360,height:900}});
  const errors = [], checks = [];
  page.on('pageerror', error => errors.push(error.message));
  const screenshot = async name => {
    if (process.env.SCREENSHOT_DIR) {
      fs.mkdirSync(process.env.SCREENSHOT_DIR, {recursive:true});
      await page.screenshot({path:path.join(process.env.SCREENSHOT_DIR, name+'.png'), fullPage:true});
    }
  };
  const visible = id => page.locator('#'+id).waitFor({state:'visible'});
  const login = async () => {
    await page.waitForFunction(() => !document.getElementById('login-button').disabled);
    await page.locator('#username').fill('test_user');
    await page.locator('#password').fill(process.env.TEST_PASSWORD);
    await page.locator('#login-button').click();
    await visible('portal-panel');
  };
  const search = async name => { await page.locator('#name').fill(name); await page.locator('#search-button').click(); };
  const employee = () => page.getByRole('cell', {name:'emp002@portal.test', exact:true});
  const logoutConfirm = async () => {
    await page.locator('#logout-button').click();
    await page.locator('#dialog-actions').getByRole('button', {name:'로그아웃', exact:true}).click();
  };
  const dismiss = async () => { await page.locator('#dialog-actions').getByRole('button', {name:'로그인 화면으로 이동'}).click(); };
  try {
    await page.goto(process.env.PORTAL_BASE_URL || 'http://localhost:8080');
    await visible('login-panel');
    await page.waitForFunction(() => !document.getElementById('login-button').disabled);
    await screenshot('01-login');
    await page.locator('#login-button').click();
    await page.getByText('아이디를 입력해 주세요.', {exact:true}).waitFor();
    assert.equal(await page.locator('#username').getAttribute('aria-invalid'), 'true');
    await page.locator('#username').fill('test_user');
    await page.locator('#password').fill('incorrect-password');
    await page.locator('#login-button').click();
    await page.getByText('아이디 또는 비밀번호를 확인해 주세요.', {exact:true}).waitFor();
    assert.equal(await page.locator('#password').inputValue(), '');
    checks.push('login validation and uniform authentication failure');
    // Rotated/missing CSRF must refresh context and require a user resubmission.
    await page.route('**/api/auth/login', route => route.fulfill({status:403, contentType:'application/json', body:JSON.stringify({error_code:'CSRF_INVALID', message:'요청 정보를 새로 확인해 주세요.'})}), {times:1});
    await page.locator('#password').fill(process.env.TEST_PASSWORD); await page.locator('#login-button').click();
    await page.getByText('로그인 정보를 새로 확인했습니다. 다시 입력해 주세요.', {exact:true}).waitFor();
    assert.equal(await page.locator('#password').inputValue(), '');
    await page.locator('#remember-id').check();
    await login(); await visible('home-view'); await screenshot('02-home');
    assert.equal(await page.evaluate(() => localStorage.getItem('schutz.remembered-id')), 'test_user');
    checks.push('CSRF recovery, home navigation and optional ID preference');
    await page.locator('#open-search').click(); await visible('search-view');
    await page.getByText('검색할 직원 이름을 입력해 주세요.', {exact:true}).waitFor();
    await search(' 이서연 '); await employee().waitFor(); await screenshot('03-search');
    await search('이서'); await page.getByText('일치하는 직원이 없습니다.', {exact:true}).waitFor();
    await search(' '); await page.getByText('직원 이름을 입력해 주세요.', {exact:true}).waitFor();
    await search('이서연'.normalize('NFD')); await employee().waitFor();
    checks.push('exact/zero-result/blank/NFC search');
    await page.route('**/api/employees?name=*', async route => {
      if (new URL(route.request().url()).searchParams.get('name') === '느린검색') {
        await new Promise(resolve => setTimeout(resolve, 350));
        try { await route.fulfill({contentType:'application/json', body:'{"items":[],"count":0}'}); } catch {}
      } else await route.continue();
    });
    await search('느린검색'); await search('이서연'); await employee().waitFor();
    await page.waitForTimeout(500); assert.equal(await employee().count(), 1);
    await page.unroute('**/api/employees?name=*');
    checks.push('late search response ignored');
    // HTML proxy errors must be rendered as a safe message, never inserted into the DOM.
    await page.route('**/api/employees?name=*', route => route.fulfill({status:502, contentType:'text/html', body:'<h1>private-proxy-html</h1>'}), {times:1});
    await search('이서연'); await visible('retry');
    assert.equal(await employee().count(), 0);
    assert(!(await page.locator('body').textContent()).includes('private-proxy-html'));
    assert.equal(await page.locator('#name').inputValue(), '이서연');
    await page.locator('#retry').click(); await employee().waitFor();
    // Result strings must stay text even when they contain markup.
    await page.route('**/api/employees?name=*', route => route.fulfill({contentType:'application/json', body:JSON.stringify({count:1, items:[{employee_id:2, name:'<img src=x onerror=alert(1)>', department:'개발팀', position:'매니저', email:'emp002@portal.test', extension:null}]})}), {times:1});
    await search('이서연'); await page.getByRole('cell', {name:'<img src=x onerror=alert(1)>', exact:true}).waitFor();
    assert.equal(await page.locator('#results img').count(), 0);
    assert.equal(await page.getByRole('cell', {name:'-', exact:true}).count(), 1);
    checks.push('proxy HTML failure, retry, text rendering and null extension');
    await search('이서연'); await employee().waitFor();
    await page.locator('#logout-button').click();
    await page.locator('#dialog-actions').getByRole('button', {name:'취소'}).click();
    assert.equal(await employee().count(), 1);
    // Failed logout must preserve authenticated state and report no success.
    await page.route('**/api/auth/logout', route => route.fulfill({status:503, contentType:'application/json', body:'{"error_code":"DATABASE_UNAVAILABLE"}'}), {times:1});
    await logoutConfirm(); await page.getByText('일시적으로 로그아웃할 수 없습니다. 잠시 후 다시 시도해 주세요.', {exact:true}).waitFor();
    assert(await page.locator('#portal-panel').isVisible());
    assert.equal(await page.locator('#portal-dialog').isVisible(), false);
    await logoutConfirm(); await page.getByRole('heading', {name:'안전하게 로그아웃되었습니다.'}).waitFor();
    assert.equal(await page.locator('#results').textContent(), '');
    await screenshot('04-logout'); await dismiss();
    checks.push('logout confirmation/cancel, database failure and success');
    // Commit succeeds but the POST response body is lost/corrupt: context read recovers state.
    let loginPosts = 0;
    await page.route('**/api/auth/login', async route => {
      loginPosts++;
      const response = await route.fetch();
      await route.fulfill({response, contentType:'application/json', body:'{'});
    }, {times:1});
    await login(); assert.equal(loginPosts, 1);
    checks.push('login response loss resolved without POST retry');
    await page.locator('#search-nav').click(); await search('이서연'); await employee().waitFor();
    await page.evaluate(async () => {
      const response = await fetch('/api/auth/context'); const data = await response.json();
      await fetch('/api/auth/logout', {method:'POST', headers:{'X-CSRF-Token':data.csrf_token}});
    });
    await search('이서연'); await page.getByRole('heading', {name:'로그인이 만료되었습니다.'}).waitFor();
    assert.equal(await page.locator('#results').textContent(), '');
    assert.equal(await page.locator('#name').inputValue(), '');
    await dismiss(); await visible('login-panel');
    checks.push('revoked session clears private state and displays expiry dialog');
    await login(); await page.locator('#search-nav').click(); await search('이서연'); await employee().waitFor();
    let logoutPosts = 0;
    await page.route('**/api/auth/logout', async route => {
      logoutPosts++; const response = await route.fetch();
      await route.fulfill({response, contentType:'application/json', body:'{'});
    }, {times:1});
    await logoutConfirm(); await page.getByRole('heading', {name:'로그아웃 상태를 확인했습니다.'}).waitFor();
    assert.equal(logoutPosts, 1); await dismiss();
    checks.push('logout response loss resolved without POST retry');
    await page.setViewportSize({width:390,height:844}); await screenshot('05-mobile-login');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await login(); await screenshot('06-mobile-home');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.locator('#search-nav').click(); await search('이서연'); await employee().waitFor(); await screenshot('07-mobile-search');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    checks.push('390px responsive login/home/search with contained table scrolling');
    await logoutConfirm(); await dismiss();
    await page.route('**/api/auth/context', route => route.abort('failed'), {times:1});
    await page.reload(); await visible('retry'); assert.equal(await page.locator('#login-button').isDisabled(), true);
    await page.locator('#retry').click(); await page.waitForFunction(() => !document.getElementById('login-button').disabled);
    checks.push('context connection failure blocks submission and retries safely');
    assert.deepEqual(errors, []);
    console.log('PASS browser: '+checks.length+' groups\n'+checks.map(check => '  '+check).join('\n'));
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exit(1); });
