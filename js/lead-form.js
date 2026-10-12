/*
 * Отправка заявок в Roistat (webhook) вместо серверов Tilda.
 *
 * Валидацию, маску телефона и окно «Спасибо» по-прежнему делает штатный
 * js/tilda-forms-1.0.min.js — здесь подменена только сама отправка
 * (window.tildaForm.send). Формат запроса: help-ru.roistat.com/integrations/Webhook/
 *
 * ВАЖНО:
 *  - cloud.roistat.com не отдаёт CORS-заголовки, поэтому запрос уходит как
 *    "простой" (text/plain, mode: no-cors): тело — JSON, ответ браузеру не виден.
 *    Если заявки в Roistat не появляются — выбросить прямую отправку и слать через
 *    прокси (Cloudflare Worker), поменяв только ENDPOINT.
 *  - key виден в исходниках сайта: любой может прислать фейковую заявку.
 *    От простых ботов защищает honeypot-поле; перевыпустить ключ можно в Roistat.
 */
(function () {
  var ENDPOINT = 'https://cloud.roistat.com/integration/webhook?key=e0bfa9940a136364e414138e40c61e48';
  var DEFAULT_TITLE = 'Заявка с сайта';
  var ERROR_TEXT = 'Не удалось отправить заявку. Проверьте интернет-соединение и повторите попытку.';

  function el(node) {
    if (window.t_forms__getElement) return window.t_forms__getElement(node);
    return node && node.jquery ? node[0] : node;
  }

  // Номер визита Roistat: счётчик ставит cookie roistat_visit (только на боевом домене)
  function roistatVisit() {
    if (window.roistat && window.roistat.visit) return String(window.roistat.visit);
    var m = document.cookie.match(/(?:^|;\s*)roistat_visit=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : null;
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

    var visit = roistatVisit();
    var payload = {
      title: value(form, 'tildaspec-formname') || DEFAULT_TITLE,
      name: value(form, 'name'),
      phone: normalizePhone(value(form, 'phone')),
      comment: 'Страница: ' + location.href,
      fields: { site: location.hostname, page: location.href }
    };
    if (visit) payload.roistat_visit = visit;

    window.fetch(ENDPOINT, {
      method: 'POST',
      mode: 'no-cors',
      keepalive: true,
      headers: { 'Content-Type': 'text/plain;charset=UTF-8' },
      body: JSON.stringify(payload)
    }).then(
      function () { onSuccess(form, btn); },
      function () { onError(form, btn); }
    );
    return false;
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
})();
