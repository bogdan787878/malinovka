/*
 * Отправка заявок в Roistat вместо серверов Tilda.
 *
 * Валидацию, маску телефона и окно «Спасибо» по-прежнему делает штатный
 * js/tilda-forms-1.0.min.js — здесь подменена только сама отправка
 * (window.tildaForm.send).
 *
 * Как доставляется заявка (deliver):
 *   1. RELAY_URL (если задан) — наш ретранслятор lead-relay/worker.mjs: он сам шлёт в Roistat с повторами,
 *      прячет ключ вебхука и не режется блокировщиками рекламы (домен не из списков трекеров);
 *   2. напрямую в Roistat (ROISTAT_URL): форма application/x-www-form-urlencoded, как раньше слала Tilda,
 *      режим no-cors (у cloud.roistat.com нет CORS: ответ не виден, ошибкой считается только сбой сети).
 *   До 3 раундов с паузами и общим таймаутом. Если не вышло — заявка сохраняется в localStorage и
 *   дошлётся при следующем заходе на сайт / когда появится сеть; посетитель видит сообщение с телефоном.
 *
 * ПОЧЕМУ ТАК: cloud.roistat.com режут блокировщики рекламы, защита браузера и антивирусы (тот же домен, что у счётчика
 * и подмены номера). У таких посетителей прямая отправка падает всегда — без ретранслятора эти заявки теряются.
 *
 * ВАЖНО: ключ ROISTAT_URL виден в исходниках — любой может прислать фейковую заявку. Когда RELAY_URL заработает,
 * прямой канал можно убрать (ROISTAT_URL = ''), а ключ перевыпустить в Roistat.
 */
(function () {
  var ROISTAT_URL = 'https://cloud.roistat.com/integration/webhook?key=e0bfa9940a136364e414138e40c61e48';
  var RELAY_URL = '';                       // адрес ретранслятора, см. lead-relay/README.md; пусто = только прямая отправка
  var PHONE_TEXT = '+7 (499) 398-15-80';
  var METRIKA_ID = 97980579;

  var DEFAULT_TITLE = 'Заявка с сайта';
  var ERROR_TEXT = 'Не удалось отправить заявку. Повторите попытку или позвоните нам: ' + PHONE_TEXT;

  var ROUNDS = 3;                           // попыток доставки
  var PAUSES = [0, 800, 2000];              // пауза перед раундом, мс
  var REQUEST_TIMEOUT = 7000;               // таймаут одного запроса, мс
  var DEADLINE = 16000;                     // после этого новые раунды не начинаем, мс
  var QUEUE_KEY = 'malinovkaLeadQueue';
  var QUEUE_TTL = 24 * 60 * 60 * 1000;
  var QUEUE_MAX = 5;

  function el(node) {
    if (window.t_forms__getElement) return window.t_forms__getElement(node);
    return node && node.jquery ? node[0] : node;
  }

  // Номер визита Roistat: счётчик ставит cookie roistat_visit (только на боевом домене)
  function roistatVisit() {
    if (window.roistat && window.roistat.visit) return String(window.roistat.visit);
    var m = document.cookie.match(/(?:^|;\s*)roistat_visit=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  function normalizePhone(value) {
    var digits = (value || '').replace(/\D/g, '');
    if (digits.length === 11 && digits.charAt(0) === '8') digits = '7' + digits.slice(1);
    return digits ? '+' + digits : '';
  }

  function value(form, name) {
    var input = form.querySelector('[name="' + name + '"]');
    return input ? input.value.trim() : '';
  }

  function sleep(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  // ---- статистика: видно, сколько заявок дошло / не дошло (Метрика → Параметры визитов → leadForm) ----
  function report(status, extra) {
    try {
      if (window.ym) {
        var p = { status: status };
        for (var k in extra) p[k] = extra[k];
        window.ym(METRIKA_ID, 'params', { leadForm: p });
      }
    } catch (e) { /* аналитика не должна ломать форму */ }
  }

  // ---- очередь неотправленных заявок ----
  function queueLoad() {
    try {
      var all = JSON.parse(localStorage.getItem(QUEUE_KEY) || '[]');
      return all.filter(function (p) { return p && Date.now() - p.ts < QUEUE_TTL; });
    } catch (e) { return []; }
  }

  function queueSave(list) {
    try { localStorage.setItem(QUEUE_KEY, JSON.stringify(list.slice(-QUEUE_MAX))); } catch (e) { /* приватный режим */ }
  }

  function queueAdd(lead) {
    var list = queueLoad().filter(function (p) { return p.phone !== lead.phone; });
    list.push(lead);
    queueSave(list);
  }

  function queueRemovePhone(phone) {
    queueSave(queueLoad().filter(function (p) { return p.phone !== phone; }));
  }

  // ---- заявка и её доставка ----
  function buildLead(form) {
    return {
      ts: Date.now(),
      name: value(form, 'name'),
      phone: normalizePhone(value(form, 'phone')),
      title: value(form, 'tildaspec-formname') || DEFAULT_TITLE,
      comment: 'Страница: ' + location.href,
      formid: form.getAttribute('id') || '',
      roistat_visit: roistatVisit(),
      roistat_url: location.href,
      cookies: document.cookie
    };
  }

  function roistatParams(l) {
    var params = new URLSearchParams();
    params.append('name', l.name);
    params.append('phone', l.phone);
    params.append('title', l.title);
    params.append('comment', l.comment);
    params.append('formid', l.formid);
    params.append('roistat_visit', l.roistat_visit);
    params.append('roistat_fields_roistat', l.roistat_visit);
    params.append('roistat_url', l.roistat_url);
    params.append('COOKIES', l.cookies);
    return params;
  }

  function withTimeout(url, init) {
    var ctl = window.AbortController ? new AbortController() : null;
    var timer = setTimeout(function () { if (ctl) ctl.abort(); }, REQUEST_TIMEOUT);
    if (ctl) init.signal = ctl.signal;
    return window.fetch(url, init).then(
      function (res) { clearTimeout(timer); return res; },
      function (err) { clearTimeout(timer); throw err; }
    );
  }

  // text/plain — «простой» запрос без preflight; ретранслятор разбирает JSON сам
  function viaRelay(lead) {
    return withTimeout(RELAY_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain;charset=UTF-8' },
      body: JSON.stringify(lead)
    }).then(function (res) {
      if (!res.ok) throw new Error('relay ' + res.status);
      return res.json().then(function (j) { if (!j || !j.ok) throw new Error('relay not ok'); });
    });
  }

  function viaRoistat(lead) {
    return withTimeout(ROISTAT_URL, { method: 'POST', mode: 'no-cors', keepalive: true, body: roistatParams(lead) });
  }

  // Resolve {ok, via, attempts}; никогда не reject
  function deliver(lead, rounds) {
    var started = Date.now();
    var total = rounds || ROUNDS;
    var attempt = 0;

    function next() {
      if (attempt >= total || (attempt > 0 && Date.now() - started > DEADLINE)) {
        return Promise.resolve({ ok: false, attempts: attempt });
      }
      var pause = PAUSES[Math.min(attempt, PAUSES.length - 1)];
      attempt++;
      return sleep(pause).then(function () {
        var first = RELAY_URL
          ? viaRelay(lead).then(function () { return 'relay'; })
          : Promise.reject(new Error('relay not configured'));
        return first.catch(function () {
          if (!ROISTAT_URL) throw new Error('no direct channel');
          return viaRoistat(lead).then(function () { return 'roistat'; });
        });
      }).then(
        function (via) { return { ok: true, via: via, attempts: attempt }; },
        next
      );
    }
    return next();
  }

  // ---- ответ Tilda-форме ----
  function finishSending(btn) {
    if (!btn) return;
    btn.classList.remove('t-btn_sending');
    btn.tildaSendingStatus = '0';
  }

  function onSuccess(form, btn) {
    finishSending(btn);
    var id = form.getAttribute('id');
    var eventName = form.getAttribute('data-tilda-event-name') || '/tilda/' + id + '/submitted/';
    try {
      if (window.Tilda && typeof window.Tilda.sendEventToStatistics === 'function') {
        window.Tilda.sendEventToStatistics(eventName, 'Send data from form ' + id, '', 0);
      }
      if (window.dataLayer) window.dataLayer.push({ event: 'submit_' + id });
    } catch (e) {
      console.error('lead-form: statistics', e);
    }
    if (window.t_triggerEvent) window.t_triggerEvent(form, 'tildaform:aftersuccess');
    window.tildaForm.successEnd(form, form.getAttribute('data-success-url'), form.getAttribute('data-success-callback'));
  }

  function onError(form, btn) {
    finishSending(btn);
    var containers = window.t_forms__getErrorContainers ? window.t_forms__getErrorContainers(form, '') : null;
    if (containers) {
      Array.prototype.forEach.call(containers.allError, function (n) {
        n.innerHTML = ERROR_TEXT;
        n.style.display = 'block';
      });
      Array.prototype.forEach.call(containers.errorBoxes, function (n) {
        n.style.display = 'block';
      });
    }
    form.classList.add('js-send-form-error');
    if (window.t_triggerEvent) window.t_triggerEvent(form, 'tildaform:aftererror');
  }

  function send(formNode, btnNode) {
    var form = el(formNode);
    var btn = el(btnNode);

    // honeypot: человек поле не видит, бот заполняет — делаем вид, что всё ушло
    if (value(form, 'website')) {
      onSuccess(form, btn);
      return false;
    }

    var lead = buildLead(form);
    deliver(lead).then(function (res) {
      if (res.ok) {
        queueRemovePhone(lead.phone);
        report('ok', { via: res.via, attempts: res.attempts });
        onSuccess(form, btn);
      } else {
        queueAdd(lead);
        report('fail', { attempts: res.attempts, online: navigator.onLine !== false });
        onError(form, btn);
      }
    });
    return false;
  }

  // Дошлём то, что не ушло раньше (сеть появилась / посетитель вернулся)
  function flushQueue() {
    if (navigator.onLine === false) return;
    queueLoad().forEach(function (lead) {
      deliver(lead, 1).then(function (res) {
        if (res.ok) {
          queueRemovePhone(lead.phone);
          report('ok_from_queue', { via: res.via });
        }
      });
    });
  }

  function addHoneypot() {
    document.querySelectorAll('form.js-form-proccess').forEach(function (form) {
      if (form.querySelector('[name="website"]')) return;
      form.insertAdjacentHTML('beforeend',
        '<div style="position:absolute;left:-5000px;top:auto;width:1px;height:1px;overflow:hidden" aria-hidden="true">' +
        '<input type="text" name="website" tabindex="-1" autocomplete="off"></div>');
    });
  }

  // tilda-forms загружается отдельным скриптом — ждём, пока появится tildaForm
  function install(attempt) {
    if (window.tildaForm && typeof window.tildaForm.send === 'function') {
      window.tildaForm.send = send;
      return;
    }
    if (attempt < 100) setTimeout(function () { install(attempt + 1); }, 100);
  }

  addHoneypot();
  install(0);
  window.addEventListener('online', flushQueue);
  setTimeout(flushQueue, 4000);
})();
