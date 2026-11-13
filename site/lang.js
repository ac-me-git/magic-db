// Language choice. A visitor whose browser is set to Italian lands on the Italian
// pages; once someone picks a language with the flag, that choice wins. It is kept
// in the browser's local storage: no cookie, and nothing is sent anywhere.
(function () {
  var KEY = 'amtg-lang';
  var here = document.documentElement.lang;
  var root = new URL('.', document.currentScript.src).href;
  var saved = null;
  try { saved = localStorage.getItem(KEY); } catch (e) { /* storage switched off */ }

  var wanted = saved || (/^it\b/i.test(navigator.language || '') ? 'it' : 'en');
  if (here === 'en' && wanted === 'it' && location.href.indexOf(root) === 0) {
    location.replace(root + 'it/' + location.href.slice(root.length));
    return;
  }

  document.addEventListener('click', function (event) {
    var link = event.target.closest && event.target.closest('a.lang');
    if (!link) return;
    try { localStorage.setItem(KEY, link.hreflang); } catch (e) { /* ignore */ }
  });
})();
