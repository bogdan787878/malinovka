/*
 * Интерактивный генплан: подсветка, тултип, статусы участков.
 * Данные (статусы) — в js/plots-data.js (window.PLOTS).
 * Разметка — <div id="tooltip"> и <svg id="genplan"> в index.html.
 * Стили — css/map.css.
 */
(function () {
  var RESERVED = 'забронирован';

  var svg = document.getElementById('genplan');
  var tooltip = document.getElementById('tooltip');
  var tooltipText = document.getElementById('tooltipText');
  if (!svg || !tooltip || !tooltipText || !window.PLOTS) return;

  // Номер участка = первый class у <path>
  function plotOf(path) {
    return window.PLOTS[path.classList[0]];
  }

  function isFree(plot) {
    return !!plot && plot.status !== RESERVED;
  }

  // Мобильная версия карты показывает кадр меньшего размера
  function applySvgLayout() {
    if (window.matchMedia('(max-width: 480px)').matches) {
      svg.setAttribute('width', '938');
      svg.setAttribute('height', '480');
      svg.setAttribute('viewBox', '100 0 1226 832');
    } else {
      svg.setAttribute('width', '1626');
      svg.setAttribute('height', '832');
      svg.setAttribute('viewBox', '0 0 1626 832');
    }
  }

  function showTooltip(e) {
    e.stopPropagation();
    var plot = plotOf(e.currentTarget);
    if (!isFree(plot)) return;

    tooltipText.innerHTML = plot.name;
    tooltip.style.left = e.clientX + 10 + 'px';
    tooltip.style.top = e.clientY + 5 + 'px';
    tooltip.style.display = 'block';

    // не даём тултипу вылезти за правый край экрана
    if (e.clientX + 10 + tooltip.offsetWidth > window.innerWidth) {
      tooltip.style.left = e.clientX - tooltip.offsetWidth - 10 + 'px';
    }
  }

  svg.querySelectorAll('path').forEach(function (path) {
    // проданный/забронированный участок: не кликается, без подсветки и курсора-«руки»
    // (css/map.css: .reserved { pointer-events: none }), клик уходит на подложку и закрывает тултип
    if (!isFree(plotOf(path))) {
      path.classList.add('reserved');
      return;
    }
    path.style.cursor = 'pointer';
    path.addEventListener('click', showTooltip);
  });

  document.addEventListener('click', function () {
    tooltip.style.display = 'none';
  });

  applySvgLayout();
  window.addEventListener('resize', applySvgLayout);
})();
