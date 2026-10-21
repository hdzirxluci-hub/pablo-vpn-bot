(() => {
  'use strict';

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const STORE = 'pablo-vpn-telegram-preview-v2';
  const TG = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  const TG_SESSION = Boolean(TG && TG.initData);
  const TELEGRAM_MODE = TG_SESSION || new URLSearchParams(location.search).has('telegram');
  const AUTH_HEADER = TG_SESSION ? `tma ${TG.initData}` : 'tma signed-demo-init-data';

  const panels = [
    { id: 'pg-ams-1', type: 'PasarGuard', name: 'PasarGuard · Amsterdam', host: 'pg-ams.demo.local', latency: 24, load: 41, status: 'online' },
    { id: 'xui-de-1', type: '3x-ui', name: '3x-ui · Frankfurt', host: 'xui-fra.demo.local', latency: 31, load: 57, status: 'online' },
    { id: 'mrz-fin-1', type: 'Marzban', name: 'Marzban · Helsinki', host: 'mrz-hel.demo.local', latency: 39, load: 33, status: 'online' }
  ];

  let plans = [
    { id: 'start15', mark: 'S', name: 'شروع', subtitle: 'برای مصرف سبک و تست', volume: 15, days: 15, price: 89000, category: 'short', panelId: 'pg-ams-1', featured: false, tag: 'اقتصادی' },
    { id: 'smart40', mark: 'P', name: 'هوشمند', subtitle: 'انتخاب محبوب کاربران', volume: 40, days: 30, price: 179000, category: 'monthly', panelId: 'pg-ams-1', featured: true, tag: 'پرفروش' },
    { id: 'pro80', mark: 'PRO', name: 'حرفه‌ای', subtitle: 'برای استفاده روزانه سنگین', volume: 80, days: 30, price: 289000, category: 'monthly', panelId: 'xui-de-1', featured: false, tag: 'سریع' },
    { id: 'max150', mark: 'M', name: 'مکس', subtitle: 'دو ماه بدون نگرانی', volume: 150, days: 60, price: 499000, category: 'heavy', panelId: 'mrz-fin-1', featured: false, tag: 'پرحجم' },
    { id: 'ultra300', mark: 'U', name: 'اولترا', subtitle: 'برای چند دستگاه و مصرف بالا', volume: 300, days: 90, price: 799000, category: 'heavy', panelId: 'xui-de-1', featured: false, tag: 'ویژه' }
  ];

  const initialState = {
    enabledPlans: plans.map(plan => plan.id),
    orders: [],
    services: [
      {
        id: 'svc-demo-001',
        planId: 'smart40',
        name: 'سرویس اصلی من',
        username: 'ali_8421',
        panelId: 'pg-ams-1',
        panel: 'PasarGuard',
        used: 18.6,
        total: 50,
        days: 18,
        status: 'active',
        sub: 'https://demo.pablovpn.local/sub/ali-x7m2',
        createdAt: '۱۴۰۵/۰۷/۲۲'
      }
    ],
    tickets: [],
    trial: { enabled: true, volumeMb: 100, durationHours: 24, maxClaims: 1, usedClaims: 0, remainingClaims: 1, eligible: true },
    user: { firstName: 'علی', lastName: 'محمدی', username: 'ali_demo', isOwner: true }
  };

  let state = loadState();
  let selectedPlanId = null;
  let receiptReady = false;
  let activeFilter = 'all';
  let ownerInitialized = false;

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function loadState() {
    if (TELEGRAM_MODE) return clone(initialState);
    try {
      const raw = localStorage.getItem(STORE);
      if (!raw) return clone(initialState);
      const parsed = JSON.parse(raw);
      if (!parsed || !Array.isArray(parsed.orders) || !Array.isArray(parsed.services)) return clone(initialState);
      parsed.trial = parsed.trial || clone(initialState.trial);
      parsed.user = parsed.user || clone(initialState.user);
      return parsed;
    } catch { return clone(initialState); }
  }

  function saveState() { if (!TELEGRAM_MODE) localStorage.setItem(STORE, JSON.stringify(state)); }

  async function api(path, options = {}) {
    const response = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json', 'Authorization': AUTH_HEADER, ...(options.headers || {}) } });
    let payload = null;
    try { payload = await response.json(); } catch { payload = null; }
    if (!response.ok) throw new Error((payload && payload.detail) || `خطای سرور (${response.status})`);
    return payload;
  }

  function haptic(kind = 'light') { if (TG_SESSION && TG.HapticFeedback) TG.HapticFeedback.impactOccurred(kind); }

  function fa(value) {
    return new Intl.NumberFormat('fa-IR', { maximumFractionDigits: 1 }).format(value);
  }

  function money(value) { return new Intl.NumberFormat('fa-IR').format(value); }

  function quotaText(service, used = false) {
    const mb = used ? service.usedMb : service.totalMb;
    if (Number.isFinite(mb) && mb < 1024) return `${fa(mb)} MB`;
    return `${fa(used ? service.used : service.total)} GB`;
  }
  function remainingText(service) {
    if (service.hoursRemaining && service.hoursRemaining < 48) return `${fa(service.hoursRemaining)} ساعت`;
    return `${fa(service.days)} روز`;
  }

  function esc(value) {
    return String(value).replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
  }

  function panelFor(id) {
    return panels.find(panel => panel.id === id);
  }

  function planFor(id) {
    return plans.find(plan => plan.id === id);
  }

  function enabledPlans() {
    return plans.filter(plan => state.enabledPlans.includes(plan.id));
  }

  function toast(message, error = false) {
    const el = document.createElement('div');
    el.className = `toast${error ? ' error' : ''}`;
    el.innerHTML = `<i></i><span>${esc(message)}</span>`;
    $('#toastStack').append(el);
    setTimeout(() => el.remove(), 3000);
  }

  async function copyText(text, message = 'کپی شد') {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const input = document.createElement('textarea');
      input.value = text;
      document.body.append(input);
      input.select();
      document.execCommand('copy');
      input.remove();
    }
    toast(message);
  }

  function planCard(plan) {
    return `
      <article class="plan-card ${plan.featured ? 'featured' : ''}">
        ${plan.featured ? '<span class="plan-ribbon">پیشنهاد PABLO</span>' : ''}
        <div class="plan-top">
          <span class="plan-mark">${esc(plan.mark)}</span>
          <div><h3>${esc(plan.name)}</h3><p>${esc(plan.subtitle)}</p></div>
        </div>
        <div class="plan-meta">
          <div><span>حجم</span><b>${fa(plan.volume)} گیگ</b></div>
          <div><span>اعتبار</span><b>${fa(plan.days)} روز</b></div>
          <div><span>نوع</span><b>${esc(plan.tag)}</b></div>
        </div>
        <div class="plan-footer">
          <div class="price"><b>${money(plan.price)}</b><small>تومان</small></div>
          <button class="buy-mini" data-plan="${plan.id}" type="button">انتخاب پلن</button>
        </div>
      </article>`;
  }

  function renderPlans() {
    const active = enabledPlans();
    const featured = active.filter(plan => plan.featured || ['pro80'].includes(plan.id));
    $('#featuredPlans').innerHTML = featured.map(planCard).join('') || '<div class="empty-card"><b>پلنی فعال نیست</b></div>';
    const filtered = activeFilter === 'all' ? active : active.filter(plan => plan.category === activeFilter);
    $('#plansList').innerHTML = filtered.map(planCard).join('') || '<div class="empty-card"><b>در این دسته پلنی نیست</b><small>یک دستهٔ دیگر را انتخاب کن.</small></div>';
  }

  function homeServiceMarkup(service) {
    const percent = service.total ? Math.min(100, Math.round(service.used / service.total * 100)) : 0;
    return `
      <div class="home-service">
        <div class="service-title-row">
          <span class="live-icon"><svg><use href="#i-signal"></use></svg></span>
          <div><b>${esc(service.name)}</b><small>${esc(service.username)} · ${esc(service.panel)}</small></div>
          <span class="live-pill"><i></i>فعال</span>
        </div>
        <div class="usage-head"><span>مصرف این دوره</span><strong>${quotaText(service, true)} از ${quotaText(service)}</strong></div>
        <div class="meter"><i style="--progress:${percent}%"></i></div>
        <div class="service-bottom"><span>${remainingText(service)} تا پایان سرویس</span><button data-service="${service.id}" type="button">جزئیات و اتصال</button></div>
      </div>`;
  }

  function renderTrial() {
    const trial = state.trial; const container = $('#trialBanner');
    if (!container || !trial || !trial.enabled) { if (container) container.innerHTML = ''; return; }
    container.innerHTML = trial.eligible
      ? `<div class="trial-card"><span class="trial-icon"><svg><use href="#i-gift"></use></svg></span><div class="trial-copy"><b>تست رایگان PABLO VPN</b><small>${fa(trial.volumeMb)} مگابایت برای ${fa(trial.durationHours)} ساعت · بدون پرداخت</small></div><button data-claim-trial type="button">فعال‌سازی</button></div>`
      : `<div class="trial-card used"><span class="trial-icon"><svg><use href="#i-check"></use></svg></span><div class="trial-copy"><b>سهمیه تست رایگان استفاده شده</b><small>سرویس آزمایشی را در «سرویس‌های من» ببین.</small></div></div>`;
  }

  function renderHome() {
    const pending = state.orders.find(order => ['awaiting_receipt', 'pending'].includes(order.status));
    const activeService = state.services.find(service => service.status === 'active');
    const parts = [];
    if (pending) {
      const plan = planFor(pending.planId);
      parts.push(`<div class="pending-home"><span><svg><use href="#i-clock"></use></svg></span><div><b>سفارش ${esc(plan.name)} در حال بررسی است</b><small>پس از تأیید Owner، سرویس خودکار ساخته می‌شود.</small></div><button data-go="services" type="button">پیگیری</button></div>`);
    }
    if (activeService) parts.push(homeServiceMarkup(activeService));
    $('#homeStatus').innerHTML = parts.join('');
  }

  function pendingCard(order) {
    const plan = planFor(order.planId);
    return `
      <article class="pending-card">
        <div class="pending-card-head">
          <span class="pending-icon"><svg><use href="#i-clock"></use></svg></span>
          <div><b>در انتظار تأیید پرداخت</b><small>پلن ${esc(plan.name)} · ${money(plan.price)} تومان</small></div>
          <span class="order-code">#${order.id}</span>
        </div>
        <div class="timeline"><span class="done">ثبت</span><i></i><span class="current">بررسی Owner</span><i></i><span>ساخت سرویس</span></div>
      </article>`;
  }

  function serviceCard(service) {
    const percent = service.total ? Math.min(100, Math.round(service.used / service.total * 100)) : 0;
    return `
      <article class="service-card">
        <div class="service-card-top">
          <span class="server-badge">VPN</span>
          <div><b>${esc(service.name)}</b><small>${esc(service.username)} · ${esc(service.panel)}</small></div>
          <span class="state-pill"><i></i>فعال</span>
        </div>
        <div class="service-numbers">
          <div><span>حجم مصرف‌شده</span><b>${quotaText(service, true)} از ${quotaText(service)}</b></div>
          <div><span>اعتبار باقی‌مانده</span><b>${remainingText(service)}</b></div>
        </div>
        <div class="service-progress"><div class="meter"><i style="--progress:${percent}%"></i></div></div>
        <div class="service-actions">
          <button data-service="${service.id}" type="button">لینک و راهنمای اتصال</button>
          <button data-renew="${service.id}" type="button">تمدید</button>
        </div>
      </article>`;
  }

  function renderServices() {
    const pending = state.orders.filter(order => ['awaiting_receipt', 'pending'].includes(order.status));
    $('#pendingOrders').innerHTML = pending.map(pendingCard).join('');
    $('#servicesList').innerHTML = state.services.length ? state.services.map(serviceCard).join('') : '<div class="empty-card"><svg><use href="#i-signal"></use></svg><b>هنوز سرویسی نداری</b><small>از بخش پلن‌ها اولین سرویس را بخر.</small></div>';
    $('#serviceBadge').textContent = state.services.length;
    $('#serviceBadge').hidden = state.services.length === 0;
  }

  function renderProfile() {
    const approved = state.orders.filter(order => order.status === 'approved').length;
    const pending = state.orders.filter(order => order.status === 'pending').length;
    $('#profileStats').innerHTML = `
      <div><b>${fa(state.services.length)}</b><span>سرویس فعال</span></div>
      <div><b>${fa(state.orders.length)}</b><span>کل سفارش‌ها</span></div>
      <div><b>${fa(approved)}</b><span>خرید تأییدشده</span></div>`;
    $('#orderSummary').textContent = pending ? `${fa(pending)} سفارش در انتظار بررسی` : (state.orders.length ? `${fa(state.orders.length)} سفارش ثبت‌شده` : 'بدون سفارش جدید');
  }

  function renderOwnerBadges() {
    const pending = state.orders.filter(order => order.status === 'pending').length;
    $('#pendingBadge').textContent = fa(pending);
    $('#pendingBadge').hidden = pending === 0;
  }

  function renderAll() {
    renderPlans();
    renderTrial();
    renderHome();
    renderServices();
    renderProfile();
    renderOwnerBadges();
  }

  function go(page) {
    $$('.page').forEach(section => section.classList.toggle('active', section.dataset.page === page));
    $$('.bottom-nav [data-go]').forEach(button => button.classList.toggle('active', button.dataset.go === page));
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function openLayer(element) {
    closeLayers();
    $('#sheetOverlay').classList.add('show');
    element.classList.add('show');
    document.body.style.overflow = 'hidden';
  }

  function closeLayers() {
    $('#sheetOverlay').classList.remove('show');
    $$('.bottom-sheet,.center-modal').forEach(element => element.classList.remove('show'));
    document.body.style.overflow = '';
  }

  function openPlan(planId) {
    const plan = planFor(planId);
    if (!plan || !state.enabledPlans.includes(plan.id)) return;
    selectedPlanId = plan.id;
    $('#planDetail').innerHTML = `
      <div class="sheet-plan-head">
        <div class="sheet-plan-mark">${esc(plan.mark)}</div>
        <h2>پلن ${esc(plan.name)}</h2>
        <p>${esc(plan.subtitle)}</p>
      </div>
      <div class="sheet-plan-price"><b>${money(plan.price)}</b><span>تومان</span></div>
      <div class="detail-grid">
        <div><svg><use href="#i-download"></use></svg><b>${fa(plan.volume)} GB</b><span>حجم کامل</span></div>
        <div><svg><use href="#i-clock"></use></svg><b>${fa(plan.days)} روز</b><span>اعتبار سرویس</span></div>
        <div><svg><use href="#i-globe"></use></svg><b>چند دستگاه</b><span>اتصال هم‌زمان</span></div>
      </div>
      <ul class="feature-list">
        <li><i><svg><use href="#i-check"></use></svg></i>تحویل خودکار پس از تأیید Owner</li>
        <li><i><svg><use href="#i-check"></use></svg></i>لینک اشتراک و QR اختصاصی</li>
        <li><i><svg><use href="#i-check"></use></svg></i>پشتیبانی و تمدید مستقیم داخل ربات</li>
      </ul>
      <button class="primary-wide" data-start-payment="${plan.id}" type="button">ادامه و ثبت پرداخت</button>`;
    openLayer($('#planSheet'));
  }

  function openPayment(planId) {
    const plan = planFor(planId); if (!plan) return;
    selectedPlanId = plan.id; receiptReady = TELEGRAM_MODE;
    $('#paymentSummary').innerHTML = `<b>پلن ${esc(plan.name)} · ${fa(plan.volume)} گیگ · ${fa(plan.days)} روز</b><span>${money(plan.price)} <small>تومان</small></span>`;
    $('#receiptTitle').textContent = TELEGRAM_MODE ? 'ارسال رسید داخل ربات' : 'انتخاب رسید نمونه';
    $('#receiptHint').textContent = TELEGRAM_MODE ? 'سفارش را ثبت کن؛ سپس تصویر رسید را برای ربات بفرست' : 'در Preview یک رسید نمونه انتخاب می‌شود';
    $('#chooseReceipt').classList.toggle('ready', TELEGRAM_MODE);
    $('#chooseReceipt span').innerHTML = TELEGRAM_MODE ? '<svg><use href="#i-send"></use></svg>' : '<svg><use href="#i-upload"></use></svg>';
    $('#chooseReceipt').disabled = TELEGRAM_MODE; $('#submitReceipt').disabled = !TELEGRAM_MODE;
    $('#submitReceipt').textContent = TELEGRAM_MODE ? 'ثبت سفارش و برگشت به ربات' : 'ثبت سفارش آزمایشی';
    openLayer($('#paymentSheet'));
  }

  function chooseDemoReceipt() {
    receiptReady = true;
    $('#receiptTitle').textContent = 'receipt-demo.jpg';
    $('#receiptHint').textContent = 'رسید نمونه آمادهٔ ثبت است';
    $('#chooseReceipt').classList.add('ready');
    $('#chooseReceipt span').innerHTML = '<svg><use href="#i-check"></use></svg>';
    $('#submitReceipt').disabled = false;
    toast('رسید نمونه انتخاب شد');
  }

  async function submitOrder() {
    if (!receiptReady || !selectedPlanId) return;
    const plan = planFor(selectedPlanId); if (!plan) return;
    $('#submitReceipt').disabled = true;
    try {
      if (TELEGRAM_MODE) {
        const order = await api('/api/orders', { method: 'POST', body: JSON.stringify({ plan_id: plan.id }) });
        state.orders = [order, ...state.orders.filter(item => item.id !== order.id)]; haptic('medium');
        if (TG_SESSION && TG.sendData) TG.sendData(JSON.stringify({ type: 'order_created', orderId: order.id }));
        if (TG_SESSION && TG.showPopup) TG.showPopup({ title: 'سفارش ثبت شد', message: `سفارش #${order.id} ثبت شد. حالا تصویر رسید را برای ربات ارسال کن.`, buttons: [{ type: 'ok' }] }, () => TG.close());
        else { closeLayers(); renderAll(); go('services'); toast('سفارش ثبت شد؛ رسید را برای ربات بفرست'); }
      } else {
        const id = 1042 + state.orders.length;
        state.orders.unshift({ id, planId: plan.id, status: 'pending', receipt: 'receipt-demo.jpg', amount: plan.price, createdAt: new Date().toISOString() });
        saveState(); closeLayers(); renderAll(); go('services'); toast('سفارش Preview ثبت شد؛ منتظر تأیید Owner است');
      }
    } catch (error) { toast(error.message || 'ثبت سفارش انجام نشد', true); $('#submitReceipt').disabled = false; }
  }

  async function claimFreeTrial(button) {
    if (!state.trial || !state.trial.eligible) return;
    button.disabled = true; button.textContent = 'در حال ساخت…';
    try {
      if (TELEGRAM_MODE) {
        const result = await api('/api/trial', { method: 'POST' }); state.services.unshift(result.service); state.trial = result.policy; haptic('medium');
      } else {
        const trial = state.trial;
        state.services.unshift({ id: `trial-${Date.now()}`, planId: 'free-trial', name: 'تست رایگان', username: `trial_${Math.random().toString(36).slice(2,7)}`, panelId: 'pg-ams-1', panel: 'PasarGuard', used: 0, usedMb: 0, total: trial.volumeMb / 1024, totalMb: trial.volumeMb, days: Math.max(1, Math.ceil(trial.durationHours/24)), hoursRemaining: trial.durationHours, status: 'active', isTrial: true, sub: `https://demo.pablovpn.local/sub/trial-${Math.random().toString(36).slice(2,12)}`, createdAt: new Date().toISOString() });
        trial.usedClaims += 1; trial.remainingClaims = Math.max(0, trial.maxClaims - trial.usedClaims); trial.eligible = trial.remainingClaims > 0; saveState();
      }
      renderAll(); go('services'); toast('تست رایگان آماده شد');
    } catch (error) { toast(error.message || 'ساخت تست رایگان انجام نشد', true); button.disabled = false; button.textContent = 'فعال‌سازی'; }
  }

  function qrMarkup(seed) {
    const size = 25;
    let hash = 2166136261;
    for (const char of seed) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619);
    let cells = '<rect width="250" height="250" rx="5" fill="#fff"/>';
    const finder = (x, y) => {
      cells += `<rect x="${x * 10}" y="${y * 10}" width="70" height="70" fill="#07101b"/><rect x="${(x + 1) * 10}" y="${(y + 1) * 10}" width="50" height="50" fill="#fff"/><rect x="${(x + 2) * 10}" y="${(y + 2) * 10}" width="30" height="30" fill="#07101b"/>`;
    };
    finder(1, 1); finder(17, 1); finder(1, 17);
    for (let y = 1; y < size - 1; y++) {
      for (let x = 1; x < size - 1; x++) {
        if ((x < 8 && y < 8) || (x > 16 && y < 8) || (x < 8 && y > 16)) continue;
        hash ^= hash << 13; hash ^= hash >>> 17; hash ^= hash << 5;
        if ((hash >>> 0) % 3 === 0) cells += `<rect x="${x * 10}" y="${y * 10}" width="10" height="10" rx="1" fill="#07101b"/>`;
      }
    }
    return `<svg viewBox="0 0 250 250" aria-label="QR نمایشی">${cells}</svg>`;
  }

  function openService(serviceId) {
    const service = state.services.find(item => item.id === serviceId);
    if (!service) return;
    const percent = service.total ? Math.min(100, Math.round(service.used / service.total * 100)) : 0;
    $('#serviceDetail').innerHTML = `
      <div class="service-detail-head">
        <span class="server-badge">VPN</span>
        <h2>${esc(service.name)}</h2>
        <p>${esc(service.username)} · ${esc(service.panel)}${TELEGRAM_MODE ? '' : ' · PREVIEW'}</p>
      </div>
      <div class="detail-grid">
        <div><svg><use href="#i-download"></use></svg><b>${quotaText(service, true)} / ${quotaText(service)}</b><span>مصرف</span></div>
        <div><svg><use href="#i-clock"></use></svg><b>${remainingText(service)}</b><span>باقی‌مانده</span></div>
        <div><svg><use href="#i-signal"></use></svg><b>${fa(percent)}٪</b><span>مصرف دوره</span></div>
      </div>
      <div class="subscription-box">
        <label>${TELEGRAM_MODE ? 'لینک اشتراک سرویس' : 'لینک اشتراک Preview'}</label>
        <div class="subscription-link">${esc(service.sub)}</div>
        <div class="subscription-actions">
          <button data-copy-sub="${service.id}" type="button">کپی لینک اشتراک</button>
          <button data-copy-config="${service.id}" type="button">کپی کانفیگ</button>
        </div>
      </div>
      <div class="qr-demo">${qrMarkup(service.sub)}</div>
      <div class="qr-caption">${TELEGRAM_MODE ? 'برای اتصال، این QR را در برنامهٔ VPN اسکن کن' : 'QR در حالت Preview نمایشی است'}</div>
      <div class="detail-action-row">
        <button class="renew" data-renew="${service.id}" type="button">تمدید سرویس</button>
        <button data-help="راهنمای اتصال" type="button">آموزش اتصال</button>
      </div>`;
    openLayer($('#serviceSheet'));
  }

  function openHelp(type) {
    const content = {
      'راهنمای اتصال': {
        icon: 'i-phone',
        title: 'آموزش اتصال',
        intro: 'بعد از دریافت لینک اشتراک، این سه مرحله را انجام بده.',
        steps: ['یکی از برنامه‌های پیشنهادی را نصب کن.', 'لینک اشتراک را کپی و داخل برنامه Import کن.', 'پروفایل PABLO را انتخاب و اتصال را روشن کن.']
      },
      'رفع مشکل اتصال': {
        icon: 'i-tools',
        title: 'رفع مشکل اتصال',
        intro: 'در نسخهٔ واقعی، ربات وضعیت سرویس و پنل را بررسی می‌کند.',
        steps: ['اینترنت عادی دستگاه را یک‌بار بررسی کن.', 'لینک اشتراک را به‌روزرسانی کن.', 'اگر مشکل باقی بود، برای پشتیبانی تیکت بساز.']
      },
      'سؤالات پرتکرار': {
        icon: 'i-help',
        title: 'سؤالات پرتکرار',
        intro: 'اطلاعات این بخش فعلاً نمونه هستند.',
        steps: ['تحویل سرویس بعد از تأیید Owner انجام می‌شود.', 'حجم و زمان دقیقاً مطابق پلن انتخابی هستند.', 'تمدید از بخش سرویس‌های من انجام می‌شود.']
      }
    }[type] || {
      icon: 'i-info', title: type, intro: 'این بخش در نسخهٔ نمایشی آماده شده است.',
      steps: ['اطلاعات فعلی آزمایشی است.', 'هیچ عملیات واقعی روی پنل انجام نمی‌شود.', 'پس از تأیید، اتصال واقعی اضافه خواهد شد.']
    };
    $('#helpContent').innerHTML = `
      <div class="help-head"><span><svg><use href="#${content.icon}"></use></svg></span><h2>${esc(content.title)}</h2><p>${esc(content.intro)}</p></div>
      <div class="help-steps">${content.steps.map((step, index) => `<div class="help-step"><span>${fa(index + 1)}</span><div><b>${esc(step)}</b><small>راهنمای نمایشی PABLO VPN</small></div></div>`).join('')}</div>`;
    openLayer($('#helpModal'));
  }

  function showProfileInfo(type) {
    if (type === 'orders') {
      const rows = state.orders.length ? state.orders.map(order => {
        const plan = planFor(order.planId);
        const status = order.status === 'pending' ? 'در انتظار بررسی' : order.status === 'approved' ? 'تأیید و ساخته شد' : 'رد شده';
        return `${plan.name} — سفارش #${order.id} — ${status}`;
      }) : ['هنوز سفارش جدیدی ثبت نشده است.'];
      $('#helpContent').innerHTML = `<div class="help-head"><span><svg><use href="#i-receipt"></use></svg></span><h2>سفارش‌های من</h2><p>سوابق سفارش‌های آزمایشی</p></div><div class="help-steps">${rows.map((row, index) => `<div class="help-step"><span>${fa(index + 1)}</span><div><b>${esc(row)}</b><small>اطلاعات دمو</small></div></div>`).join('')}</div>`;
      openLayer($('#helpModal'));
      return;
    }
    if (type === 'invite') {
      openHelp('دعوت از دوستان');
      return;
    }
    openHelp('قوانین و حریم خصوصی');
  }

  function showOwner() {
    $('#customerApp').classList.add('hidden');
    $('#ownerApp').classList.remove('hidden');
    $('#roleSwitch').classList.add('hidden');
    closeLayers();
    if (!ownerInitialized) {
      ownerInitialized = true;
      setTimeout(() => ownerCommand('dashboard', false), 120);
    }
    setTimeout(scrollOwner, 180);
  }

  function showCustomer() {
    $('#ownerApp').classList.add('hidden');
    $('#customerApp').classList.remove('hidden');
    $('#roleSwitch').classList.remove('hidden');
    renderAll();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  const ownerLabels = {
    dashboard: '📊 آمار فروش',
    orders: '🧾 سفارش‌ها',
    panels: '🖥 پنل‌ها',
    plans: '🛍 پلن‌ها',
    customers: '👥 کاربران',
    broadcast: '📣 پیام همگانی'
  };

  function appendUserMessage(text) {
    $('#ownerMessages').insertAdjacentHTML('beforeend', `<div class="message-row user-message"><div class="message-bubble"><p>${esc(text)}</p><time>اکنون ✓✓</time></div></div>`);
  }

  function appendBotMessage(content) {
    $('#ownerMessages').insertAdjacentHTML('beforeend', `<div class="message-row bot-message"><div class="message-avatar">P</div><div class="message-bubble"><b class="sender">PABLO VPN Bot</b>${content}<time>اکنون ✓</time></div></div>`);
  }

  function scrollOwner() {
    window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'smooth' });
  }

  function ownerDashboard() {
    const pending = state.orders.filter(order => order.status === 'pending').length;
    const approved = state.orders.filter(order => order.status === 'approved');
    const revenue = approved.reduce((sum, order) => sum + order.amount, 0) + 2847000;
    return `<p>گزارش لحظه‌ای فروشگاه:</p><div class="bot-card"><div class="bot-card-title">📊 امروز، ۲۹ مهر</div><div class="bot-stat-grid"><div class="bot-stat"><b>${money(revenue)}</b><span>فروش آزمایشی / تومان</span></div><div class="bot-stat"><b>${fa(12 + approved.length)}</b><span>سفارش موفق</span></div><div class="bot-stat"><b>${fa(pending)}</b><span>در انتظار تأیید</span></div><div class="bot-stat"><b>${fa(128 + state.services.length)}</b><span>سرویس فعال</span></div></div><div class="bot-note">✅ هر سه پنل آنلاین هستند.<br>⚠️ این اعداد و عملیات کاملاً نمایشی‌اند.</div><div class="tg-inline"><button data-owner-menu="orders" type="button">مشاهده سفارش‌ها</button><button data-owner-menu="panels" type="button">سلامت پنل‌ها</button></div></div>`;
  }

  function ownerOrders() {
    if (!state.orders.length) {
      return '<p>هنوز سفارش جدیدی ثبت نشده است.</p><div class="bot-card"><div class="bot-note">برای تست، به حالت مشتری برگرد و یک پلن بخر.</div></div>';
    }
    return `<p>آخرین سفارش‌ها:</p><div class="bot-card">${state.orders.map(order => {
      const plan = planFor(order.planId);
      const status = order.status === 'pending' ? '⏳ منتظر تأیید' : order.status === 'approved' ? '✅ تحویل‌شده' : '❌ ردشده';
      return `<div class="order-box"><div class="order-line"><b>#${order.id} · ${esc(plan.name)}</b><span>${status}</span></div><small>علی محمدی · ${money(order.amount)} تومان<br>رسید نمونه: ${esc(order.receipt)}</small>${order.status === 'pending' ? `<div class="tg-inline"><button class="approve" data-approve="${order.id}" type="button">✅ تأیید و ساخت</button><button class="reject" data-reject="${order.id}" type="button">رد سفارش</button></div>` : ''}</div>`;
    }).join('')}</div>`;
  }

  function ownerPanels() {
    return `<p>سلامت پنل‌های متصل:</p><div class="bot-card">${panels.map(panel => `<div class="panel-box"><div class="panel-line"><b>${esc(panel.type)}</b><span class="panel-health">آنلاین</span></div><small>${esc(panel.name)}<br>${panel.latency}ms · بار فعلی ${fa(panel.load)}٪</small><div class="tg-inline"><button class="full" data-panel-test="${panel.id}" type="button">تست اتصال</button></div></div>`).join('')}<div class="tg-inline"><button class="full" data-demo-add-panel type="button">➕ افزودن پنل جدید</button></div></div>`;
  }

  function ownerPlans() {
    return `<p>پلن‌ها و مقصد ثابت هرکدام:</p><div class="bot-card">${plans.map(plan => {
      const panel = panelFor(plan.panelId);
      const enabled = state.enabledPlans.includes(plan.id);
      return `<div class="plan-map"><div class="map-line"><b>${esc(plan.name)} · ${fa(plan.volume)}GB/${fa(plan.days)}روز</b><span>${enabled ? '🟢 فعال' : '⚪ غیرفعال'}</span></div><small>مقصد: ${esc(panel.type)} · ${esc(panel.name)}</small><div class="tg-inline"><button class="full" data-plan-toggle="${plan.id}" type="button">${enabled ? 'غیرفعال‌کردن پلن' : 'فعال‌کردن پلن'}</button></div></div>`;
    }).join('')}</div>`;
  }

  function ownerCustomers() {
    return `<p>نمونهٔ فهرست کاربران:</p><div class="bot-card"><div class="customer-box"><div class="customer-line"><b>علی محمدی</b><span>🟢 ${fa(state.services.length)} سرویس</span></div><small>@ali_demo · ID 842103 · مشتری دمو</small></div><div class="customer-box"><div class="customer-line"><b>سارا احمدی</b><span>🟢 ۱ سرویس</span></div><small>@sara_vip · آخرین خرید امروز</small></div><div class="customer-box"><div class="customer-line"><b>نیما رضایی</b><span>🟡 رو به پایان</span></div><small>@nima_net · ۳ روز تا انقضا</small></div><div class="tg-inline"><button class="full" data-demo-customer-search type="button">🔎 جست‌وجوی کاربر</button></div></div>`;
  }

  function ownerBroadcast() {
    return `<p>ارسال پیام آزمایشی به کاربران:</p><div class="bot-card"><div class="broadcast-box"><textarea id="broadcastText" placeholder="متن پیام همگانی…">🎁 تخفیف ویژهٔ تمدید PABLO VPN تا پایان امشب</textarea><button data-broadcast-send type="button">ارسال آزمایشی به ۱۲۸ کاربر</button></div></div>`;
  }

  function ownerCommand(type, includeUser = true) {
    if (includeUser) appendUserMessage(ownerLabels[type] || type);
    const renderers = { dashboard: ownerDashboard, orders: ownerOrders, panels: ownerPanels, plans: ownerPlans, customers: ownerCustomers, broadcast: ownerBroadcast };
    const renderer = renderers[type] || ownerDashboard;
    setTimeout(() => {
      appendBotMessage(renderer());
      renderOwnerBadges();
      setTimeout(scrollOwner, 50);
    }, includeUser ? 160 : 0);
  }

  function approveOrder(orderId) {
    const order = state.orders.find(item => item.id === Number(orderId));
    if (!order || order.status !== 'pending') return;
    const plan = planFor(order.planId);
    const panel = panelFor(plan.panelId);
    order.status = 'approved';
    order.approvedAt = new Date().toISOString();
    const suffix = Math.random().toString(36).slice(2, 6);
    const service = {
      id: `svc-${order.id}`,
      planId: plan.id,
      name: `پلن ${plan.name}`,
      username: `ali_${order.id}`,
      panelId: panel.id,
      panel: panel.type,
      used: 0,
      total: plan.volume,
      days: plan.days,
      status: 'active',
      sub: `https://demo.pablovpn.local/sub/ali-${suffix}`,
      createdAt: new Intl.DateTimeFormat('fa-IR').format(new Date())
    };
    state.services.unshift(service);
    saveState();
    renderAll();
    appendBotMessage(`<p>✅ سفارش #${order.id} تأیید شد.</p><div class="bot-card"><div class="bot-note">کاربر <b>${esc(service.username)}</b> روی ${esc(panel.type)} ساخته شد.<br>${fa(plan.volume)} گیگ · ${fa(plan.days)} روز<br>لینک اشتراک برای مشتری ارسال شد.</div><div class="tg-inline"><button class="full" data-owner-menu="orders" type="button">بازگشت به سفارش‌ها</button></div></div>`);
    toast('سرویس نمایشی ساخته و تحویل شد');
    setTimeout(scrollOwner, 80);
  }

  function rejectOrder(orderId) {
    const order = state.orders.find(item => item.id === Number(orderId));
    if (!order || order.status !== 'pending') return;
    order.status = 'rejected';
    saveState();
    renderAll();
    appendBotMessage(`<p>❌ سفارش #${order.id} رد شد و پیام اصلاح رسید برای مشتری ارسال شد.</p>`);
    toast('سفارش آزمایشی رد شد', true);
    setTimeout(scrollOwner, 80);
  }

  function togglePlan(planId) {
    if (state.enabledPlans.includes(planId)) {
      state.enabledPlans = state.enabledPlans.filter(id => id !== planId);
      toast('پلن از فروشگاه مخفی شد');
    } else {
      state.enabledPlans.push(planId);
      toast('پلن دوباره فعال شد');
    }
    saveState();
    renderAll();
    ownerCommand('plans', false);
  }

  function resetDemo() {
    state = clone(initialState);
    saveState();
    selectedPlanId = null;
    receiptReady = false;
    activeFilter = 'all';
    $$('#planFilters button').forEach(button => button.classList.toggle('active', button.dataset.filter === 'all'));
    $('#ownerMessages').innerHTML = '';
    ownerInitialized = false;
    renderAll();
    go('home');
    toast('اطلاعات دمو بازنشانی شد');
  }

  document.addEventListener('click', event => {
    const trialButton = event.target.closest('[data-claim-trial]');
    if (trialButton) { claimFreeTrial(trialButton); return; }

    const goButton = event.target.closest('[data-go]');
    if (goButton) {
      closeLayers();
      go(goButton.dataset.go);
      return;
    }

    const planButton = event.target.closest('[data-plan]');
    if (planButton) {
      openPlan(planButton.dataset.plan);
      return;
    }

    const paymentButton = event.target.closest('[data-start-payment]');
    if (paymentButton) {
      closeLayers();
      setTimeout(() => openPayment(paymentButton.dataset.startPayment), 120);
      return;
    }

    const serviceButton = event.target.closest('[data-service]');
    if (serviceButton) {
      openService(serviceButton.dataset.service);
      return;
    }

    const renewButton = event.target.closest('[data-renew]');
    if (renewButton) {
      const service = state.services.find(item => item.id === renewButton.dataset.renew);
      closeLayers();
      setTimeout(() => openPlan(service && service.planId ? service.planId : 'smart40'), 120);
      return;
    }

    const helpButton = event.target.closest('[data-help]');
    if (helpButton) {
      openHelp(helpButton.dataset.help);
      return;
    }

    const profileButton = event.target.closest('[data-profile]');
    if (profileButton) {
      showProfileInfo(profileButton.dataset.profile);
      return;
    }

    const closeButton = event.target.closest('[data-close-sheet]');
    if (closeButton) {
      closeLayers();
      return;
    }

    const copySub = event.target.closest('[data-copy-sub]');
    if (copySub) {
      const service = state.services.find(item => item.id === copySub.dataset.copySub);
      if (service) copyText(service.sub, 'لینک اشتراک کپی شد');
      return;
    }

    const copyConfig = event.target.closest('[data-copy-config]');
    if (copyConfig) {
      const service = state.services.find(item => item.id === copyConfig.dataset.copyConfig);
      if (service) copyText(TELEGRAM_MODE ? service.sub : `vless://DEMO-UUID@demo.pablovpn.local:443?security=tls&type=ws#PABLO-${service.username}`, TELEGRAM_MODE ? 'لینک اتصال کپی شد' : 'کانفیگ Preview کپی شد');
      return;
    }

    const ownerMenu = event.target.closest('[data-owner-menu]');
    if (ownerMenu) {
      ownerCommand(ownerMenu.dataset.ownerMenu);
      return;
    }

    const approve = event.target.closest('[data-approve]');
    if (approve) {
      approveOrder(approve.dataset.approve);
      return;
    }

    const reject = event.target.closest('[data-reject]');
    if (reject) {
      rejectOrder(reject.dataset.reject);
      return;
    }

    const panelTest = event.target.closest('[data-panel-test]');
    if (panelTest) {
      const panel = panelFor(panelTest.dataset.panelTest);
      panelTest.disabled = true;
      panelTest.textContent = 'در حال تست…';
      setTimeout(() => {
        panelTest.disabled = false;
        panelTest.textContent = `✅ متصل · ${panel.latency}ms`;
        toast(`اتصال آزمایشی ${panel.type} سالم است`);
      }, 650);
      return;
    }

    const planToggle = event.target.closest('[data-plan-toggle]');
    if (planToggle) {
      togglePlan(planToggle.dataset.planToggle);
      return;
    }

    if (event.target.closest('[data-demo-add-panel]')) {
      toast('در نسخهٔ واقعی فرم امن افزودن پنل باز می‌شود');
      return;
    }

    if (event.target.closest('[data-demo-customer-search]')) {
      toast('جست‌وجوی آزمایشی کاربران آماده است');
      return;
    }

    if (event.target.closest('[data-broadcast-send]')) {
      const text = ($('#broadcastText') && $('#broadcastText').value.trim()) || '';
      if (!text) return toast('متن پیام خالی است', true);
      appendBotMessage('<p>✅ پیام آزمایشی برای ۱۲۸ کاربر در صف ارسال قرار گرفت.</p>');
      toast('ارسال همگانی شبیه‌سازی شد');
      setTimeout(scrollOwner, 80);
    }
  });

  $('#sheetOverlay').addEventListener('click', closeLayers);
  $('#roleSwitch').addEventListener('click', showOwner);
  $('#backToCustomer').addEventListener('click', showCustomer);
  $('#chooseReceipt').addEventListener('click', chooseDemoReceipt);
  $('#submitReceipt').addEventListener('click', submitOrder);
  $('#copyCard').addEventListener('click', () => copyText('6037999912345678', 'شماره کارت نمایشی کپی شد'));
  $('#giftButton').addEventListener('click', () => { if (state.trial && state.trial.eligible) go('home'); toast(state.trial && state.trial.eligible ? 'تست رایگان آمادهٔ فعال‌سازی است' : 'در حال حاضر هدیهٔ تازه‌ای نداری'); });
  $('#newTicket').addEventListener('click', () => {
    if (TELEGRAM_MODE) {
      const message = 'Mini App را ببند و پیام پشتیبانی را مستقیم برای ربات بفرست؛ پیام به Owner می‌رسد.';
      if (TG_SESSION && TG.showPopup) TG.showPopup({ title: 'پشتیبانی PABLO', message, buttons: [{ type: 'ok' }] }, () => TG.close()); else toast(message);
      return;
    }
    state.tickets.unshift({ id: 301 + state.tickets.length, status: 'open' }); saveState();
    $('#ticketPreview').hidden = false;
    $('#ticketPreview').innerHTML = `<b>تیکت Preview #${301 + state.tickets.length - 1}</b><p>در اجرای واقعی، پیام داخل ربات به Owner فرستاده می‌شود.</p>`;
    toast('گفتگوی پشتیبانی Preview شروع شد');
  });
  $('#resetDemo').addEventListener('click', resetDemo);
  $('#planFilters').addEventListener('click', event => {
    const button = event.target.closest('[data-filter]');
    if (!button) return;
    activeFilter = button.dataset.filter;
    $$('#planFilters button').forEach(item => item.classList.toggle('active', item === button));
    renderPlans();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') closeLayers();
  });

  function applyUserProfile(user) {
    const fullName = [user.firstName, user.lastName].filter(Boolean).join(' ') || 'کاربر PABLO';
    const initials = fullName.split(/\s+/).slice(0, 2).map(part => part[0]).join('').toUpperCase();
    const miniName = $('.profile-mini strong'), miniAvatar = $('.profile-mini .avatar');
    const profileName = $('.profile-card h1'), profileHandle = $('.profile-card p'), profileAvatar = $('.profile-avatar');
    if (miniName) miniName.textContent = fullName;
    if (miniAvatar) miniAvatar.childNodes[0].nodeValue = initials;
    if (profileName) profileName.textContent = fullName;
    if (profileHandle) profileHandle.textContent = user.username ? `@${user.username}` : `Telegram ID ${user.id || ''}`;
    if (profileAvatar) profileAvatar.childNodes[0].nodeValue = initials;
  }

  async function initialize() {
    if (TG_SESSION) { TG.ready(); TG.expand(); if (TG.setHeaderColor) TG.setHeaderColor('#0b0d13'); if (TG.setBackgroundColor) TG.setBackgroundColor('#080a0f'); }
    if (!TELEGRAM_MODE) {
      $('#runtimeLabel').textContent = 'PREVIEW مستقل — داده‌ها آزمایشی‌اند'; $('#paymentModeHint').textContent = 'حالت Preview؛ هیچ پولی جابه‌جا نمی‌شود';
      applyUserProfile(state.user); renderAll(); return;
    }
    $('#runtimeLabel').textContent = 'متصل به @PablloVPN3_bot'; $('#roleSwitch').hidden = true; $('#resetDemo').hidden = true;
    try {
      const data = await api('/api/bootstrap'); plans = data.plans;
      state = { enabledPlans: plans.map(plan => plan.id), orders: data.orders, services: data.services, tickets: [], trial: data.trial, user: data.user };
      applyUserProfile(data.user); renderAll();
    } catch (error) { $('#runtimeLabel').textContent = 'خطا در اتصال امن به ربات'; toast(error.message || 'اطلاعات حساب دریافت نشد', true); applyUserProfile(state.user); renderAll(); }
  }

  initialize();
  if ('serviceWorker' in navigator) window.addEventListener('load', () => navigator.serviceWorker.register('./sw.js').catch(() => {}));
})();
