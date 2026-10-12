/* Аналитика: работает только на боевом домене, чтобы локальная разработка не накручивала статистику. */
(function () {
  var prod = ['malinovka-park2.ru', 'www.malinovka-park2.ru'];
  if (prod.indexOf(location.hostname) === -1) return;

  window.dataLayer = window.dataLayer || [];

  /* Roistat */
(function(w, d, s, h, id) {
    w.roistatProjectId = id; w.roistatHost = h;
    var p = d.location.protocol == "https:" ? "https://" : "http://";
    var u = /^.*roistat_visit=[^;]+(.*)?$/.test(d.cookie) ? "/dist/module.js" : "/api/site/1.0/"+id+"/init?referrer="+encodeURIComponent(d.location.href);
    var js = d.createElement(s); js.charset="UTF-8"; js.async = 1; js.src = p+h+u; var js2 = d.getElementsByTagName(s)[0]; js2.parentNode.insertBefore(js, js2);
})(window, document, 'script', 'cloud.roistat.com', 'c595e32592ef5dd6979b04942a0991ca');

  /* Яндекс.Метрика */
(function(m,e,t,r,i,k,a){m[i]=m[i]||function(){(m[i].a=m[i].a||[]).push(arguments)};
   m[i].l=1*new Date();
   for (var j = 0; j < document.scripts.length; j++) {if (document.scripts[j].src === r) { return; }}
   k=e.createElement(t),a=e.getElementsByTagName(t)[0],k.async=1,k.src=r,a.parentNode.insertBefore(k,a)})
   (window, document, "script", "https://mc.yandex.ru/metrika/tag.js", "ym");

   ym(97980579, "init", {
        clickmap:true,
        trackLinks:true,
        accurateTrackBounce:true,
        webvisor:true,
        ecommerce:"dataLayer"
   });

  /* Roistat: подмешивает roistat_url / roistat_fields_roistat во все формы */
var RoiTildaTimer = setInterval(function() {
if (!!window.roistat && !!window.roistat.visit) {
clearInterval(RoiTildaTimer);
var roiCount=0;
var roiFieldsObj = {};
document.querySelectorAll('form').forEach(el => {
roiFieldsObj['roiformu' + roiCount] = document.createElement('input');
roiFieldsObj['roiformu' + roiCount].type = 'hidden';
roiFieldsObj['roiformu' + roiCount].name = 'roistat_url';
roiFieldsObj['roiformu' + roiCount].value = document.location.href;
el.append(roiFieldsObj['roiformu' + roiCount]);
roiFieldsObj['roiformv' + roiCount] = document.createElement('input');
roiFieldsObj['roiformv' + roiCount].type = 'hidden';
roiFieldsObj['roiformv' + roiCount].name = 'roistat_fields_roistat';
roiFieldsObj['roiformv' + roiCount].value = window.roistat.visit;
el.append(roiFieldsObj['roiformv' + roiCount]);
roiCount ++;
});
}
},200);
})();
