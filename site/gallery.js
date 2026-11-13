// Screenshot gallery: opens the screenshots of a page in a full-window viewer.
// Without JavaScript the links simply open the image.
(function () {
  var links = Array.prototype.slice.call(document.querySelectorAll('.shots a'));
  if (!links.length || !window.HTMLDialogElement) return;

  var italian = document.documentElement.lang === 'it';
  var text = italian
    ? { close: 'Chiudi', prev: 'Immagine precedente', next: 'Immagine successiva' }
    : { close: 'Close', prev: 'Previous image', next: 'Next image' };

  var box = document.createElement('dialog');
  box.className = 'lightbox';
  box.innerHTML =
    '<button type="button" class="lb-prev">&#8249;</button>' +
    '<figure><img alt=""><figcaption></figcaption></figure>' +
    '<button type="button" class="lb-next">&#8250;</button>' +
    '<button type="button" class="lb-close">&times;</button>';
  var img = box.querySelector('img');
  var caption = box.querySelector('figcaption');
  var prev = box.querySelector('.lb-prev');
  var next = box.querySelector('.lb-next');
  var close = box.querySelector('.lb-close');
  prev.setAttribute('aria-label', text.prev);
  next.setAttribute('aria-label', text.next);
  close.setAttribute('aria-label', text.close);
  document.body.appendChild(box);

  var current = 0;

  function show(index) {
    current = (index + links.length) % links.length;
    var link = links[current];
    var thumb = link.querySelector('img');
    var note = link.parentNode.querySelector('figcaption');
    img.src = link.href;
    img.alt = thumb ? thumb.alt : '';
    caption.innerHTML = (note ? note.innerHTML + ' ' : '') + '(' + (current + 1) + '/' + links.length + ')';
  }

  links.forEach(function (link, index) {
    link.addEventListener('click', function (event) {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.button) return;
      event.preventDefault();
      show(index);
      box.showModal();
    });
  });

  prev.addEventListener('click', function () { show(current - 1); });
  next.addEventListener('click', function () { show(current + 1); });
  close.addEventListener('click', function () { box.close(); });

  // A click anywhere but on the image, its caption or a button closes the viewer.
  box.addEventListener('click', function (event) {
    if (!event.target.closest('img, figcaption, button')) box.close();
  });

  box.addEventListener('keydown', function (event) {
    if (event.key === 'ArrowLeft') show(current - 1);
    if (event.key === 'ArrowRight') show(current + 1);
  });

  var startX = null;
  box.addEventListener('touchstart', function (event) {
    startX = event.touches.length === 1 ? event.touches[0].clientX : null;
  }, { passive: true });
  box.addEventListener('touchend', function (event) {
    if (startX === null) return;
    var moved = event.changedTouches[0].clientX - startX;
    startX = null;
    if (Math.abs(moved) > 50) show(current + (moved < 0 ? 1 : -1));
  }, { passive: true });
})();
